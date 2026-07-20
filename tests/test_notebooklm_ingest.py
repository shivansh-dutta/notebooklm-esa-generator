"""
Unit tests for notebooklm_pipeline/ingest.py.

Covers the parts that don't require a live NotebookLM connection:
  - count_words / slice_pdf_pages against a synthetic PDF
  - plan_chunks: appendices under the word cap stay as one chunk; an
    appendix over the cap gets split into multiple sub-ranges
No network or notebooklm-py import is exercised here.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from notebooklm_pipeline.ingest import count_words, plan_chunks, slice_pdf_pages


def _make_pdf(tmp_path: Path, page_word_counts: list[int]) -> Path:
    """Build a synthetic PDF with one page per entry in page_word_counts,
    each page containing that many space-separated "word" tokens, written
    as multiple short lines (position doesn't matter — PyMuPDF's get_text()
    extracts from the page's text content stream regardless)."""
    doc = fitz.open()
    for i, wc in enumerate(page_word_counts):
        page = doc.new_page()
        words = [f"w{i}_{j}" for j in range(wc)]
        y = 72
        for k in range(0, len(words), 10):
            page.insert_text((72, y), " ".join(words[k:k + 10]))
            y += 12
    path = tmp_path / "synthetic.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestCountWords:
    def test_counts_words_across_page_range(self, tmp_path: Path):
        pdf_path = _make_pdf(tmp_path, [50, 50, 50])
        # Just page 2 (1-indexed) should be ~50 words, not 150.
        count_all = count_words(pdf_path, 1, 3)
        count_one = count_words(pdf_path, 2, 2)
        assert count_one < count_all
        assert count_one > 0


class TestSlicePdfPages:
    def test_slice_produces_correct_page_count(self, tmp_path: Path):
        pdf_path = _make_pdf(tmp_path, [10, 10, 10, 10, 10])
        out_path = slice_pdf_pages(pdf_path, 2, 4, tmp_path / "sliced.pdf")
        sliced = fitz.open(str(out_path))
        try:
            assert sliced.page_count == 3
        finally:
            sliced.close()


class TestPlanChunks:
    def test_appendix_under_cap_stays_one_chunk(self, tmp_path: Path):
        pdf_path = _make_pdf(tmp_path, [20] * 5)
        appendix_map = [{
            "appendix_num": 1, "title": "Small Appendix", "component": "foil",
            "start_page": 1, "end_page": 5,
        }]
        plans = plan_chunks(pdf_path, appendix_map)
        assert len(plans) == 1
        assert plans[0].total_parts == 1
        assert (plans[0].start_page, plans[0].end_page) == (1, 5)

    def test_oversized_appendix_is_split_into_multiple_chunks(self, tmp_path: Path, monkeypatch):
        # Force a tiny effective cap so a small synthetic PDF still exercises
        # the splitting path without needing to actually generate 450k words.
        import notebooklm_pipeline.ingest as ingest_mod
        monkeypatch.setattr(ingest_mod, "MAX_WORDS_PER_SOURCE", 30)

        pdf_path = _make_pdf(tmp_path, [20, 20, 20, 20])  # ~80 words > 30 cap
        appendix_map = [{
            "appendix_num": 5, "title": "EDR Radius Report", "component": "edr_radius_report",
            "start_page": 1, "end_page": 4,
        }]
        plans = plan_chunks(pdf_path, appendix_map)

        assert len(plans) > 1
        # Chunks are contiguous and together cover the full page range.
        covered = []
        for p in sorted(plans, key=lambda pl: pl.start_page):
            covered.extend(range(p.start_page, p.end_page + 1))
        assert covered == [1, 2, 3, 4]
        assert all(p.total_parts == len(plans) for p in plans)
        assert all(p.component == "edr_radius_report" for p in plans)

    def test_multiple_appendices_each_planned_independently(self, tmp_path: Path):
        pdf_path = _make_pdf(tmp_path, [5] * 6)
        appendix_map = [
            {"appendix_num": 1, "title": "Maps", "component": "maps", "start_page": 1, "end_page": 3},
            {"appendix_num": 2, "title": "FOIL", "component": "foil", "start_page": 4, "end_page": 6},
        ]
        plans = plan_chunks(pdf_path, appendix_map)
        assert len(plans) == 2
        assert {p.component for p in plans} == {"maps", "foil"}
