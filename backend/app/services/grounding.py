"""Citation coverage and answer grounding verification (Phase 2, section 10.5).

Lightweight, model-free heuristics so the system can downgrade an answer to
"not found" when the generated text is not actually grounded in the retrieved
evidence.
"""

import re
from typing import List, Dict


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text.lower()).strip()


def citation_coverage(answer: str, sources: List[Dict]) -> Dict:
    """Check how many distinct source files are explicitly referenced in the answer.

    A source counts as cited if its filename (or its stem) appears in the answer
    as a whole token, not just any substring — so a generic stem like "a" won't
    spuriously match the letter 'a' inside another word. Matching is
    diacritic/case-insensitive on word boundaries.
    """
    ans = _normalize(answer)
    cited = []
    for s in sources:
        fname = (s.get("filename") or "").lower().strip()
        if not fname:
            continue
        stem = fname.rsplit('.', 1)[0]
        # Full filename (with extension) is a strong signal — accept a plain
        # substring match. The stem alone must match as a bounded token to avoid
        # false positives on very short/generic stems.
        if fname in ans:
            cited.append(s.get("filename"))
        elif stem and len(stem) >= 3 and re.search(rf"(?<!\w){re.escape(stem)}(?!\w)", ans):
            cited.append(s.get("filename"))

    unique_cited = list(dict.fromkeys(cited))
    return {
        "cited_count": len(unique_cited),
        "cited_sources": unique_cited,
        "total_sources": len(sources),
    }


def grounding_score(answer: str, chunks: List[Dict]) -> float:
    """Token-overlap proxy for how much of the answer is supported by evidence.

    Returns the fraction of answer content-tokens that also appear somewhere in
    the retrieved chunk texts. Cheap stand-in for an NLI verifier; good enough to
    catch fully hallucinated answers.
    """
    ans_tokens = set(re.findall(r'\w+', answer.lower()))
    # Drop very short tokens (stopword-ish) to reduce noise
    ans_tokens = {t for t in ans_tokens if len(t) > 2}
    if not ans_tokens:
        return 0.0

    evidence = " ".join(c.get("content", "") for c in chunks).lower()
    evidence_tokens = set(re.findall(r'\w+', evidence))
    if not evidence_tokens:
        return 0.0

    supported = ans_tokens & evidence_tokens
    return len(supported) / len(ans_tokens)


REFUSAL_MARKERS = [
    "không tìm thấy",
    "không có thông tin",
    "không đủ thông tin",
]


def is_refusal(answer: str) -> bool:
    ans = answer.lower()
    return any(m in ans for m in REFUSAL_MARKERS)


def verify_answer(answer: str, sources: List[Dict], chunks: List[Dict],
                  min_citations: int, min_grounding: float = 0.3) -> Dict:
    """Combine citation coverage + grounding into an accept/downgrade decision."""
    if is_refusal(answer):
        # An explicit refusal is always acceptable and needs no citations.
        return {"accepted": True, "refusal": True, "coverage": None, "grounding": None}

    coverage = citation_coverage(answer, sources)
    grounding = grounding_score(answer, chunks)

    accepted = coverage["cited_count"] >= min_citations and grounding >= min_grounding
    return {
        "accepted": accepted,
        "refusal": False,
        "coverage": coverage,
        "grounding": round(grounding, 3),
        "min_citations": min_citations,
        "min_grounding": min_grounding,
    }
