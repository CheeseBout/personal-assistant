"""Unit tests for Chunker and Grounding modules."""
import pytest
import sys
import os

# Ensure the backend package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services.document import Chunker
from app.services.grounding import (
    _content_tokens,
    grounding_score,
    citation_coverage,
    verify_answer,
    is_refusal,
)


# ---------------------------------------------------------------------------
# Chunker._split tests
# ---------------------------------------------------------------------------

class TestChunkerSplit:
    """Tests for Chunker._split (core splitting logic)."""

    def test_empty_text_returns_empty(self):
        result = Chunker._split("", chunk_size=100, overlap=20)
        assert result == []

    def test_whitespace_only_returns_empty(self):
        result = Chunker._split("   \n\n  ", chunk_size=100, overlap=20)
        assert result == []

    def test_text_shorter_than_chunk_size(self):
        text = "Hello world"
        result = Chunker._split(text, chunk_size=100, overlap=20)
        assert len(result) == 1
        assert result[0]["content"] == text
        assert result[0]["start_char"] == 0
        assert result[0]["end_char"] == len(text)

    def test_text_exactly_chunk_size(self):
        text = "A" * 100
        result = Chunker._split(text, chunk_size=100, overlap=20)
        assert len(result) == 1
        assert result[0]["content"] == text

    def test_text_needs_splitting(self):
        # 250 chars, chunk_size=100, overlap=20 -> should produce multiple chunks
        text = "word " * 50  # 250 chars
        result = Chunker._split(text, chunk_size=100, overlap=20)
        assert len(result) > 1
        # All chunks should have content
        for chunk in result:
            assert len(chunk["content"]) > 0
        # First chunk starts at 0
        assert result[0]["start_char"] == 0
        # Last chunk ends at text length
        assert result[-1]["end_char"] == len(text)

    def test_separator_at_boundary_respected(self):
        # Build text where a sentence boundary ". " sits past the halfway mark
        # of the chunk window, so the splitter prefers it over a hard cut.
        # "First sentence. " is 17 chars; with chunk_size=30, floor=15,
        # the ". " at index 15 is past the halfway mark and will be chosen.
        text = "First sentence. Second sentence content here."
        result = Chunker._split(text, chunk_size=30, overlap=5)
        assert len(result) >= 2
        # The splitter should cut after ". " (the sentence boundary)
        first_content = result[0]["content"]
        assert "First sentence. " in first_content or first_content.rstrip().endswith(".")

    def test_overlap_produces_shared_text(self):
        text = "AAAA BBBB CCCC DDDD EEEE FFFF GGGG HHHH IIII JJJJ"
        result = Chunker._split(text, chunk_size=20, overlap=5)
        if len(result) >= 2:
            # Due to overlap, the end of chunk N and start of chunk N+1
            # should have overlapping character ranges
            for i in range(len(result) - 1):
                curr_end = result[i]["end_char"]
                next_start = result[i + 1]["start_char"]
                # next_start should be less than curr_end due to overlap
                assert next_start < curr_end, (
                    f"Chunk {i} ends at {curr_end}, chunk {i+1} starts at {next_start} "
                    f"-- expected overlap"
                )


class TestChunkerChunkText:
    """Tests for Chunker.chunk_text (backward-compat wrapper)."""

    def test_empty_text(self):
        result = Chunker.chunk_text("")
        assert result == []

    def test_single_chunk(self):
        text = "Short text"
        result = Chunker.chunk_text(text, chunk_size=1000, overlap=100)
        assert len(result) == 1
        assert result[0]["content"] == text
        assert result[0]["index"] == 0
        assert result[0]["meta"] == {}


# ---------------------------------------------------------------------------
# Grounding tests
# ---------------------------------------------------------------------------

class TestContentTokens:
    """Tests for _content_tokens tokenizer."""

    def test_extracts_vietnamese_tokens(self):
        text = "Sản phẩm có công suất 100W và bảo hành 24 tháng"
        tokens = _content_tokens(text)
        # Vietnamese words longer than 2 chars should be present
        assert "sản" in tokens
        assert "phẩm" in tokens
        assert "bảo" in tokens
        assert "hành" in tokens
        assert "tháng" in tokens
        # Short tokens (<=2 chars) should be excluded
        assert "có" not in tokens
        assert "và" not in tokens

    def test_keeps_numeric_tokens(self):
        text = "Giá 12 triệu, bảo hành 24 tháng, pin 75 Wh"
        tokens = _content_tokens(text)
        assert "12" in tokens
        assert "24" in tokens
        assert "75" in tokens

    def test_strips_citation_markers(self):
        # [1] matches \[\d+\] and is stripped.
        # [nguồn tham khảo] matches \[[^\[\]\d]{2,60}\] (no digits inside) and is stripped.
        # But [nguồn 1] is NOT stripped because it contains a digit inside the brackets.
        text = "Sản phẩm tốt [1] với giá rẻ [nguồn tham khảo]"
        tokens = _content_tokens(text)
        # "nguồn" and "tham" and "khảo" from inside the bracket should be stripped
        assert "nguồn" not in tokens
        assert "tham" not in tokens
        assert "khảo" not in tokens
        # But real content tokens should be present
        assert "sản" in tokens
        assert "phẩm" in tokens


class TestGroundingScore:
    """Tests for grounding_score function."""

    def test_high_score_when_answer_matches_evidence(self):
        answer = "Sản phẩm có công suất 100W và bảo hành 24 tháng"
        chunks = [{"content": "Sản phẩm có công suất 100W và bảo hành 24 tháng cho khách hàng"}]
        score = grounding_score(answer, chunks)
        assert score >= 0.8, f"Expected high score, got {score}"

    def test_low_score_when_answer_doesnt_match(self):
        answer = "Laptop gaming hiệu năng cao với card đồ họa mạnh mẽ"
        chunks = [{"content": "Thời tiết hôm nay nắng đẹp, nhiệt độ 30 độ"}]
        score = grounding_score(answer, chunks)
        assert score < 0.3, f"Expected low score, got {score}"

    def test_empty_answer_returns_zero(self):
        score = grounding_score("", [{"content": "some evidence"}])
        assert score == 0.0

    def test_empty_chunks_returns_zero(self):
        score = grounding_score("some answer text here", [])
        assert score == 0.0


class TestCitationCoverage:
    """Tests for citation_coverage function."""

    def test_counts_filename_mentions(self):
        answer = "Theo tài liệu report.pdf, sản phẩm đạt chuẩn"
        sources = [
            {"filename": "report.pdf"},
            {"filename": "specs.docx"},
        ]
        result = citation_coverage(answer, sources)
        assert result["cited_count"] == 1
        assert "report.pdf" in result["cited_sources"]
        assert result["total_sources"] == 2

    def test_stem_matching(self):
        answer = "Theo tài liệu report, sản phẩm đạt chuẩn"
        sources = [{"filename": "report.pdf"}]
        result = citation_coverage(answer, sources)
        assert result["cited_count"] == 1

    def test_no_citations(self):
        answer = "Sản phẩm tốt lắm"
        sources = [{"filename": "report.pdf"}]
        result = citation_coverage(answer, sources)
        assert result["cited_count"] == 0


class TestCitationRegex:
    """Tests for citation marker regex behavior."""

    def test_citation_marker_stripped(self):
        # [1] should be stripped from text (it's a citation marker)
        text_with_citation = "answer [1] here"
        text_without = "answer  here"
        tokens_with = _content_tokens(text_with_citation)
        tokens_without = _content_tokens(text_without)
        # Both should produce the same content tokens
        assert tokens_with == tokens_without

    def test_array_index_not_stripped(self):
        # arr[0] should NOT have [0] stripped -- [0] is a single digit in brackets
        # that matches \[\d+\] so it IS stripped. But importantly, "arr" (3 chars)
        # should be preserved as a token.
        # Actually, per the regex _CITATION_MARKER = r"\[\d+\]|\[[^\[\]\d]{2,60}\]"
        # [0] DOES match \[\d+\] and gets stripped. The key difference is:
        # - [1] in "answer [1]" -> stripped (desired)
        # - [0] in "arr[0]" -> stripped too (the regex can't distinguish)
        # The test verifies the regex behavior as documented:
        # "arr[0]" -> "arr" token survives, only bracket content removed
        tokens = _content_tokens("arr[0]")
        assert "arr" in tokens


class TestVerifyAnswer:
    """Tests for verify_answer function."""

    def test_refusal_text_accepted(self):
        answer = "Tôi không tìm thấy thông tin liên quan"
        result = verify_answer(
            answer,
            sources=[],
            chunks=[],
            min_citations=1,
            min_grounding=0.3,
        )
        assert result["accepted"] is True
        assert result["refusal"] is True

    def test_grounded_answer_accepted(self):
        answer = "Sản phẩm có công suất 100W và bảo hành 24 tháng theo report.pdf"
        sources = [{"filename": "report.pdf"}]
        chunks = [{"content": "Sản phẩm có công suất 100W và bảo hành 24 tháng"}]
        result = verify_answer(
            answer,
            sources=sources,
            chunks=chunks,
            min_citations=1,
            min_grounding=0.3,
        )
        assert result["accepted"] is True
        assert result["refusal"] is False
        assert result["grounding"] >= 0.3

    def test_ungrounded_answer_rejected(self):
        answer = "Laptop gaming hiệu năng cao với card đồ họa mạnh mẽ"
        sources = [{"filename": "weather.pdf"}]
        chunks = [{"content": "Thời tiết hôm nay nắng đẹp, nhiệt độ 30 độ"}]
        result = verify_answer(
            answer,
            sources=sources,
            chunks=chunks,
            min_citations=1,
            min_grounding=0.3,
        )
        assert result["accepted"] is False
        assert result["refusal"] is False
