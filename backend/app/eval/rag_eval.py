"""RAG evaluation harness (§22.1).

Builds an isolated temp index from the bundled fixtures, runs each golden
question through the same hybrid retrieval the chat endpoint uses, and reports
the metrics the requirements call for:

  - Retrieval precision@k / recall@k          (always)
  - Refusal accuracy on unanswerable queries  (always)
  - Citation accuracy + answer faithfulness   (only with OPENAI_API_KEY)

Usage:
  python -m app.eval.rag_eval                  # retrieval metrics only
  OPENAI_API_KEY=sk-... python -m app.eval.rag_eval --verbose
  python -m app.eval.rag_eval --json           # machine-readable summary

This is a report-only harness: exit code is always 0 (a degraded score is a
signal, not a CI gate). Wrap with pytest later if you need thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import as a script: `python -m app.eval.rag_eval` from backend/.
from ..core.config import settings
from ..services.document import DocumentParser, Chunker
from ..services.grounding import citation_coverage, grounding_score, verify_answer
from ..services.rag import RAGEngine

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DOCS_DIR = FIXTURES_DIR / "docs"
GOLDEN_PATH = FIXTURES_DIR / "golden.json"


# ---------- Fixture loading ------------------------------------------------


def _load_golden() -> Dict[str, Any]:
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _index_fixtures(engine: RAGEngine) -> List[str]:
    """Parse + chunk + embed every fixture doc. Returns the doc_ids indexed."""
    doc_ids: List[str] = []
    for path in sorted(DOCS_DIR.iterdir()):
        if not path.is_file():
            continue
        doc_id = path.stem  # matches the IDs in golden.json
        segments = DocumentParser.parse_segments(str(path))
        chunks = Chunker.chunk_segments(segments)
        if not chunks:
            continue
        texts = [c["content"] for c in chunks]
        embeddings = engine.embedding_service.embed_texts(texts)
        engine.vector_store.add_documents(doc_id, chunks, embeddings, version=1)
        engine.keyword_index.add_chunks(doc_id, chunks, version=1)
        doc_ids.append(doc_id)
    return doc_ids


# ---------- Retrieval metrics ---------------------------------------------


def _doc_ids_from(results: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for r in results:
        md = r.get("metadata") or {}
        did = md.get("doc_id")
        if did:
            out.append(did)
    return out


def _precision_recall_at_k(
    retrieved_doc_ids: List[str], expected: List[str], k: int
) -> Tuple[float, float]:
    """Precision: fraction of top-k chunks whose doc is expected.

    Recall: fraction of expected docs that appear anywhere in top-k.
    With an empty expected set both are returned as 0 (caller should skip).
    """
    if not expected:
        return 0.0, 0.0
    top_k = retrieved_doc_ids[:k]
    if not top_k:
        return 0.0, 0.0
    expected_set = set(expected)
    hits = sum(1 for d in top_k if d in expected_set)
    precision = hits / len(top_k)
    found = expected_set & set(top_k)
    recall = len(found) / len(expected_set)
    return precision, recall


def _apply_threshold_filter(
    results: List[Dict[str, Any]], threshold: float, min_results: int
) -> List[Dict[str, Any]]:
    """Mirror the threshold + min_results gate from retrieve_and_rerank.

    Returns the filtered list, or [] if it falls under ``min_results`` (i.e.
    the system would refuse to answer).
    """
    kept = []
    for r in results:
        score = r.get("rerank_score", r.get("fusion_score", 0.0))
        if score >= threshold:
            kept.append(r)
    if len(kept) < min_results:
        return []
    return kept


# ---------- Answer evaluation (optional, needs LLM) ------------------------


def _build_context(chunks: List[Dict[str, Any]], doc_id_to_filename: Dict[str, str]) -> Tuple[str, List[Dict[str, Any]]]:
    """Build a context block + sources list the same shape chat.py uses, so
    the grounding verifier sees comparable input.
    """
    parts: List[str] = []
    sources: List[Dict[str, Any]] = []
    for r in chunks:
        meta = r.get("metadata") or {}
        doc_id = meta.get("doc_id") or "unknown"
        filename = doc_id_to_filename.get(doc_id, doc_id)
        chunk_idx = meta.get("chunk_index", "?")
        citation = f"{filename} (chunk {chunk_idx})"
        parts.append(f"[Nguồn: {citation}]\n{r.get('content', '')}")
        sources.append({"filename": filename, "doc_id": doc_id, "chunk_index": chunk_idx, "citation": citation})
    return "\n\n".join(parts), sources


async def _llm_answer(question: str, context: str) -> str:
    """Ask the configured LLM to answer using only ``context``."""
    from ..services.llm import LLMProvider
    from ..services.prompts import build_rag_system_prompt

    provider = LLMProvider(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.DEFAULT_MODEL,
    )
    messages = [
        {"role": "system", "content": build_rag_system_prompt(context)},
        {"role": "user", "content": question},
    ]
    resp = await provider.chat_async(messages=messages, context=None, temperature=0.0)
    return resp.content or ""


# ---------- Main run -------------------------------------------------------


def _evaluate_one(
    engine: RAGEngine,
    q: Dict[str, Any],
    k: int,
    threshold: float,
    min_results: int,
    doc_id_to_filename: Dict[str, str],
    run_llm: bool,
) -> Dict[str, Any]:
    """Evaluate a single golden question end-to-end."""
    raw = engine.hybrid_search(q["question"])
    retrieved_doc_ids = _doc_ids_from(raw)
    expected = q.get("expected_doc_ids", [])
    answerable = bool(q.get("answerable"))

    precision = recall = None
    if answerable and expected:
        precision, recall = _precision_recall_at_k(retrieved_doc_ids, expected, k)

    filtered = _apply_threshold_filter(raw, threshold, min_results)
    refused = len(filtered) == 0

    out: Dict[str, Any] = {
        "id": q["id"],
        "question": q["question"],
        "answerable": answerable,
        "expected_doc_ids": expected,
        "top_doc_ids": retrieved_doc_ids[:k],
        "precision_at_k": precision,
        "recall_at_k": recall,
        "refused": refused,
    }

    if not run_llm:
        return out

    # Even on questions the system would refuse, we let the LLM see the
    # (low-scoring) context so we can check that verify_answer correctly
    # downgrades a hallucinated answer.
    context_chunks = filtered or raw[:k]
    context, sources = _build_context(context_chunks, doc_id_to_filename)
    try:
        answer = asyncio.run(_llm_answer(q["question"], context))
    except Exception as e:
        out["llm_error"] = str(e)
        return out

    chunks_for_verify = [
        {"content": r.get("content", ""), "citation": s["citation"]}
        for r, s in zip(context_chunks, sources)
    ]
    verdict = verify_answer(
        answer=answer,
        sources=sources,
        chunks=chunks_for_verify,
        min_citations=settings.CITATION_COVERAGE_MIN,
    )
    coverage = citation_coverage(answer, sources)
    grounding = grounding_score(answer, chunks_for_verify)

    must_include = [s.lower() for s in q.get("answer_must_include", [])]
    contains_required = all(s in answer.lower() for s in must_include) if must_include else None

    expected_filenames = {doc_id_to_filename[d] for d in expected if d in doc_id_to_filename}
    cited = set(coverage.get("cited_sources") or [])
    cited_correctly = bool(expected_filenames & cited) if expected_filenames else None

    out.update({
        "answer": answer,
        "grounding": round(grounding, 3),
        "cited_sources": list(cited),
        "cited_correctly": cited_correctly,
        "contains_required": contains_required,
        "verdict_accepted": verdict["accepted"],
        "verdict_refusal": verdict["refusal"],
    })
    return out


def _summarize(per_q: List[Dict[str, Any]], run_llm: bool) -> Dict[str, Any]:
    """Aggregate per-question results into the headline metrics."""
    answerable = [r for r in per_q if r["answerable"]]
    unanswerable = [r for r in per_q if not r["answerable"]]

    def _avg(values: List[float]) -> Optional[float]:
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    summary: Dict[str, Any] = {
        "n_total": len(per_q),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "precision_at_k": _avg([r["precision_at_k"] for r in answerable]),
        "recall_at_k": _avg([r["recall_at_k"] for r in answerable]),
        "refusal_unanswerable": (
            f"{sum(1 for r in unanswerable if r['refused'])}/{len(unanswerable)}"
            if unanswerable else "n/a"
        ),
        "false_refusal_answerable": (
            f"{sum(1 for r in answerable if r['refused'])}/{len(answerable)}"
            if answerable else "n/a"
        ),
    }

    if run_llm:
        with_answer = [r for r in per_q if "answer" in r]
        cite_judged = [r for r in with_answer if r.get("cited_correctly") is not None]
        cite_ok = sum(1 for r in cite_judged if r["cited_correctly"])
        ground_vals = [r.get("grounding") for r in with_answer if r.get("grounding") is not None]
        good_ground = sum(1 for v in ground_vals if v >= 0.3)
        speculation = [
            r for r in with_answer
            if not r["answerable"] and not r.get("verdict_refusal")
            and r.get("verdict_accepted")
        ]
        summary["llm"] = {
            "n_answered": len(with_answer),
            "citation_accuracy": f"{cite_ok}/{len(cite_judged)}" if cite_judged else "n/a",
            "avg_grounding": _avg(ground_vals),
            "grounding_pass": f"{good_ground}/{len(ground_vals)}" if ground_vals else "n/a",
            "speculation_on_unanswerable": len(speculation),
        }
    return summary


def _format_report(summary: Dict[str, Any], k: int, per_q: List[Dict[str, Any]], verbose: bool) -> str:
    lines = [
        "",
        f"=== RAG Eval (n={summary['n_total']} questions, k={k}) ===",
        f"  answerable: {summary['n_answerable']}  unanswerable: {summary['n_unanswerable']}",
        "",
        "-- Retrieval --",
        f"  Precision@{k}              : {summary['precision_at_k']}",
        f"  Recall@{k}                 : {summary['recall_at_k']}",
        f"  Refusal on unanswerable    : {summary['refusal_unanswerable']}",
        f"  False refusal (answerable) : {summary['false_refusal_answerable']}",
    ]
    if "llm" in summary:
        L = summary["llm"]
        lines += [
            "",
            "-- Answer (LLM) --",
            f"  Citation accuracy   : {L['citation_accuracy']}",
            f"  Avg grounding score : {L['avg_grounding']}",
            f"  Grounding >= 0.3    : {L['grounding_pass']}",
            f"  Speculation on unanswerable : {L['speculation_on_unanswerable']}",
        ]
    else:
        lines += ["", "-- Answer (LLM) -- [skipped: set OPENAI_API_KEY to enable]"]

    if verbose:
        lines += ["", "-- Per-question --"]
        for r in per_q:
            tag = "ANS" if r["answerable"] else "UNANS"
            p, rec = r["precision_at_k"], r["recall_at_k"]
            refused = "refused" if r["refused"] else "answered"
            extra = ""
            if "answer" in r:
                extra = f"  cited_ok={r.get('cited_correctly')} ground={r.get('grounding')}"
            lines.append(
                f"  [{r['id']}] {tag:<5} p={p} r={rec} {refused} top={r['top_doc_ids']}{extra}"
            )
    lines.append("")
    return "\n".join(lines)


def _run(args: argparse.Namespace) -> int:
    golden = _load_golden()
    questions = golden["questions"]
    k = args.k or golden.get("k", 5)

    api_key_set = bool(settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY"))
    run_llm = api_key_set and not args.no_llm

    threshold = settings.RERANK_THRESHOLD
    min_results = settings.RAG_MIN_RESULTS

    # The eval doesn't initialize the app DB, so SettingsManager's lazy load of
    # the app_settings table would fail and retry on every hybrid_search call,
    # spamming the log. Seed its cache as "loaded but empty" → it falls back to
    # static settings cleanly without touching any DB.
    from ..services.settings_manager import SettingsManager
    _sm = SettingsManager.get_instance()
    _sm._cache = {}
    _sm._loaded = True

    # Build an isolated index in a tempdir so we never touch real data.
    temp_root = Path(tempfile.mkdtemp(prefix="rag_eval_"))
    vector_dir = temp_root / "chroma"
    kw_db = temp_root / "kw.db"
    print(f"[eval] isolated index at: {temp_root}", file=sys.stderr)

    per_q: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
    try:
        engine = RAGEngine(persist_dir=str(vector_dir), keyword_db_path=str(kw_db))
        doc_ids = _index_fixtures(engine)
        if not doc_ids:
            print("[eval] no fixture docs found; aborting.", file=sys.stderr)
            return 0
        doc_id_to_filename = {d: d for d in doc_ids}  # fixtures already use stem == id

        for q in questions:
            per_q.append(_evaluate_one(
                engine, q, k=k, threshold=threshold, min_results=min_results,
                doc_id_to_filename=doc_id_to_filename, run_llm=run_llm,
            ))

        summary = _summarize(per_q, run_llm)

        if args.json:
            print(json.dumps({"summary": summary, "results": per_q}, ensure_ascii=False, indent=2))
        else:
            print(_format_report(summary, k, per_q, args.verbose))
    finally:
        # Windows can hold a lock on the SQLite/Chroma files briefly after
        # close; ignore_errors keeps a clean exit either way.
        shutil.rmtree(temp_root, ignore_errors=True)

    return 0


def main() -> int:
    # Windows consoles default to cp1252, which can't encode the Vietnamese
    # fixture text in --json/--verbose output. Force UTF-8 so the report and
    # JSON dump never crash on non-ASCII.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="RAG eval harness (§22.1)")
    parser.add_argument("--k", type=int, default=None, help="top-k cutoff (default: from golden.json)")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser.add_argument("--verbose", action="store_true", help="print per-question rows")
    parser.add_argument("--no-llm", action="store_true", help="skip the answer-quality metrics even if a key is set")
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
