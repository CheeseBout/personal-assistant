"""Automated unit tests for Phase 2 RAG components.

Covers the parts that don't require network or heavy model downloads:
chunking/parsing structure, FTS5 keyword index, RRF fusion, deletion verify,
and the grounding/citation verifier. Run: pytest -q
"""

import os
import sys
import tempfile
import importlib

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.services.document import Chunker, DocumentParser
from app.services.keyword_index import KeywordIndex
from app.services.rag import RAGEngine
from app.services import grounding


# ---- Chunking / parsing ---------------------------------------------------

def test_chunk_segments_preserves_metadata():
    segments = [
        {"text": "alpha " * 300, "meta": {"page": 1}},
        {"text": "bravo " * 300, "meta": {"page": 2}},
    ]
    chunks = Chunker.chunk_segments(segments, chunk_size=500, overlap=50)
    assert chunks, "expected chunks"
    assert all("meta" in c for c in chunks)
    pages = {c["meta"].get("page") for c in chunks}
    assert pages == {1, 2}
    # indices are contiguous and unique
    idxs = [c["index"] for c in chunks]
    assert idxs == list(range(len(idxs)))


def test_md_parser_splits_by_heading():
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Title\nintro\n## Section A\nbody a\n## Section B\nbody b\n")
        path = f.name
    try:
        segments = DocumentParser.parse_segments(path)
        headings = [s["meta"].get("heading") for s in segments if s["meta"].get("heading")]
        assert "Section A" in headings and "Section B" in headings
    finally:
        os.unlink(path)


# ---- FTS5 keyword index ---------------------------------------------------

@pytest.fixture
def kw_index():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    idx = KeywordIndex(db_path=tmp.name)
    yield idx
    try:
        os.unlink(tmp.name)
    except (PermissionError, OSError):
        pass  # Windows may briefly hold the sqlite temp file


def test_keyword_index_add_search_delete(kw_index):
    chunks = [
        {"index": 0, "content": "The invoice number is INV-2024-0099 for project Falcon."},
        {"index": 1, "content": "Unrelated content about weather and clouds."},
    ]
    kw_index.add_chunks("doc1", chunks, version=1)
    assert kw_index.count_by_doc_id("doc1") == 2

    hits = kw_index.search("INV-2024-0099", n_results=5)
    assert hits, "exact keyword should be found by FTS5"
    assert hits[0]["metadata"]["doc_id"] == "doc1"

    kw_index.delete_by_doc_id("doc1")
    assert kw_index.count_by_doc_id("doc1") == 0


def test_keyword_search_handles_special_chars(kw_index):
    kw_index.add_chunks("d", [{"index": 0, "content": "alpha beta gamma"}], version=1)
    # Must not raise on punctuation-heavy queries
    assert kw_index.search("alpha: beta- gamma?") is not None


# ---- RRF fusion -----------------------------------------------------------

def test_rrf_fuse_rewards_agreement():
    vector = [{"id": "a", "content": "x", "metadata": {"doc_id": "d"}},
              {"id": "b", "content": "y", "metadata": {"doc_id": "d"}}]
    keyword = [{"id": "b", "content": "y", "metadata": {"doc_id": "d"}},
               {"id": "c", "content": "z", "metadata": {"doc_id": "d"}}]
    fused = RAGEngine._rrf_fuse(vector, keyword, k=60)
    ids = [f["id"] for f in fused]
    # 'b' appears in both lists → should rank first
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}
    assert all("fusion_score" in f for f in fused)


# ---- Grounding / citation verifier ---------------------------------------

def test_citation_coverage_detects_filename():
    sources = [{"filename": "contract.pdf"}, {"filename": "notes.txt"}]
    cov = grounding.citation_coverage("Theo contract.pdf điều khoản 2.1 ...", sources)
    assert cov["cited_count"] == 1
    assert "contract.pdf" in cov["cited_sources"]


def test_grounding_score_high_when_supported():
    chunks = [{"content": "Doanh thu quý 3 đạt 5 tỷ đồng tại chi nhánh Hà Nội."}]
    score = grounding.grounding_score("Doanh thu quý 3 đạt 5 tỷ đồng", chunks)
    assert score > 0.7


def test_grounding_score_low_when_hallucinated():
    chunks = [{"content": "Doanh thu quý 3 đạt 5 tỷ đồng."}]
    score = grounding.grounding_score("Sao Hỏa có hai mặt trăng tên Phobos Deimos", chunks)
    assert score < 0.3


def test_verify_answer_downgrades_ungrounded():
    sources = [{"filename": "a.pdf"}]
    chunks = [{"content": "Báo cáo tài chính năm 2023."}]
    verdict = grounding.verify_answer(
        "Thủ đô nước Pháp là Paris.", sources, chunks, min_citations=1, min_grounding=0.3
    )
    assert verdict["accepted"] is False


def test_verify_answer_accepts_refusal():
    verdict = grounding.verify_answer(
        "Không tìm thấy thông tin phù hợp.", [], [], min_citations=1
    )
    assert verdict["accepted"] is True and verdict["refusal"] is True
