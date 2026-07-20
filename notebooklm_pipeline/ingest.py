"""
notebooklm_pipeline/ingest.py — split the raw Phase 1 PDF package into
NotebookLM sources and upload them.

Reuses scripts.segment_pdf.segment_appendices() (the same appendix-divider
detection the main pipeline uses — see scripts/segment_pdf.py) so both
pipelines agree on what "Appendix 5 = edr_radius_report" etc. means.

Why chunking is needed: NotebookLM caps each source at 500,000 words / 200MB
(verified against current published limits). A combined ~1500-page Phase 1
package can run ~750k words, and even a single appendix (the EDR radius
report is routinely the largest) can exceed the per-source word cap on its
own. This module measures each appendix's word count via PyMuPDF text
extraction and splits any appendix over MAX_WORDS_PER_SOURCE into multiple
page-range chunks, each uploaded as its own source.

Public interface:
    SourceInfo (dataclass)
    slice_pdf_pages(pdf_path, start_page, end_page, out_path) -> Path
    count_words(pdf_path, start_page, end_page) -> int
    plan_chunks(pdf_path, appendix_map) -> list[ChunkPlan]
    run_ingest(client, notebook_id, project_path, raw_pdf_path) -> list[SourceInfo]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from notebooklm_pipeline.nblm_client import add_source
from notebooklm_pipeline.question_bank import legal_vault_source_paths
from scripts.segment_pdf import segment_appendices, write_appendix_map

logger = logging.getLogger(__name__)

# NotebookLM's published per-source cap is 500,000 words. Chunk well under
# that so word-count estimation error (our count is extracted-text word
# count, NotebookLM's may differ slightly) never risks tripping the cap.
MAX_WORDS_PER_SOURCE = 450_000


@dataclass
class ChunkPlan:
    component: str
    appendix_num: int
    part: int          # 1-indexed; >1 only when an appendix was split
    total_parts: int
    start_page: int     # 1-indexed inclusive
    end_page: int        # 1-indexed inclusive
    title: str


@dataclass
class SourceInfo:
    component: str
    appendix_num: int
    part: int
    total_parts: int
    start_page: int
    end_page: int
    pdf_path: Path
    notebook_source: object  # whatever notebooklm-py's add_file returns


def count_words(pdf_path: Path, start_page: int, end_page: int) -> int:
    """Word count of extracted text across pages [start_page, end_page]
    (1-indexed, inclusive). Used only to decide chunk boundaries — an
    approximation of NotebookLM's own word count is fine for that purpose."""
    doc = fitz.open(str(pdf_path))
    try:
        total = 0
        for n in range(start_page - 1, min(end_page, doc.page_count)):
            total += len(doc[n].get_text().split())
        return total
    finally:
        doc.close()


def slice_pdf_pages(pdf_path: Path, start_page: int, end_page: int, out_path: Path) -> Path:
    """Write pages [start_page, end_page] (1-indexed, inclusive) of
    pdf_path to a new standalone PDF at out_path."""
    src = fitz.open(str(pdf_path))
    try:
        out = fitz.open()
        try:
            out.insert_pdf(src, from_page=start_page - 1, to_page=end_page - 1)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out.save(str(out_path))
        finally:
            out.close()
    finally:
        src.close()
    return out_path


def _split_range_by_words(
    pdf_path: Path, start_page: int, end_page: int, max_words: int
) -> list[tuple[int, int]]:
    """
    Greedily split [start_page, end_page] into sub-ranges each under
    max_words, by accumulating per-page word counts. Falls back to one page
    per chunk in the pathological case that a single page alone exceeds
    max_words (never merged smaller — NotebookLM's cap is on the whole
    source, a single oversized page can't be shrunk further here).
    """
    doc = fitz.open(str(pdf_path))
    try:
        page_words = [
            len(doc[n].get_text().split()) for n in range(start_page - 1, end_page)
        ]
    finally:
        doc.close()

    ranges: list[tuple[int, int]] = []
    chunk_start = start_page
    running = 0
    for offset, wc in enumerate(page_words):
        page_num = start_page + offset
        if running + wc > max_words and running > 0:
            ranges.append((chunk_start, page_num - 1))
            chunk_start = page_num
            running = 0
        running += wc
    ranges.append((chunk_start, end_page))
    return ranges


def plan_chunks(pdf_path: Path, appendix_map: list[dict]) -> list[ChunkPlan]:
    """Turn an appendix_map (from segment_appendices) into a flat list of
    ChunkPlans — one per appendix, or several if an appendix exceeds
    MAX_WORDS_PER_SOURCE."""
    plans: list[ChunkPlan] = []
    for entry in appendix_map:
        start, end = entry["start_page"], entry["end_page"]
        words = count_words(pdf_path, start, end)
        if words <= MAX_WORDS_PER_SOURCE:
            sub_ranges = [(start, end)]
        else:
            logger.info(
                "ingest: APX%s (%s) is %d words > %d cap — splitting",
                entry["appendix_num"], entry["component"], words, MAX_WORDS_PER_SOURCE,
            )
            sub_ranges = _split_range_by_words(pdf_path, start, end, MAX_WORDS_PER_SOURCE)

        total_parts = len(sub_ranges)
        for i, (s, e) in enumerate(sub_ranges, start=1):
            plans.append(ChunkPlan(
                component=entry["component"],
                appendix_num=entry["appendix_num"],
                part=i,
                total_parts=total_parts,
                start_page=s,
                end_page=e,
                title=entry["title"],
            ))
    return plans


def _chunk_filename(plan: ChunkPlan) -> str:
    base = f"APX{plan.appendix_num}_{plan.component}"
    if plan.total_parts > 1:
        base += f"_part{plan.part}of{plan.total_parts}"
    return base + ".pdf"


async def run_ingest(client, notebook_id: str, project_path: Path, raw_pdf_path: Path) -> list[SourceInfo]:
    """
    Segment raw_pdf_path by appendix, chunk any oversized appendix, slice
    each chunk to its own PDF under <project>/NBLM_Sources/, upload every
    chunk as a NotebookLM source, and return the resulting SourceInfo list
    (used by question_bank/qa_runner to know which components are present).
    """
    project_path = Path(project_path)
    raw_pdf_path = Path(raw_pdf_path)

    appendix_map = segment_appendices(raw_pdf_path)
    write_appendix_map(project_path, appendix_map)
    logger.info("ingest: segmented %s into %d appendix entries", raw_pdf_path.name, len(appendix_map))

    plans = plan_chunks(raw_pdf_path, appendix_map)
    sources_dir = project_path / "NBLM_Sources"

    results: list[SourceInfo] = []

    # Upload the legal/regulatory reference files too (whichever exist —
    # LegalVault may be partially populated) so section_questions()'s
    # Section 5.0 REC/CREC/HREC citation requirement has something in the
    # notebook to ground against, not just general model knowledge.
    for legal_path in legal_vault_source_paths():
        notebook_source = await add_source(client, notebook_id, legal_path, wait=True)
        results.append(SourceInfo(
            component="legal_reference",
            appendix_num=0,
            part=1,
            total_parts=1,
            start_page=0,
            end_page=0,
            pdf_path=legal_path,
            notebook_source=notebook_source,
        ))
        logger.info("ingest: uploaded legal reference source %s", legal_path.name)

    for plan in plans:
        out_path = sources_dir / _chunk_filename(plan)
        slice_pdf_pages(raw_pdf_path, plan.start_page, plan.end_page, out_path)
        notebook_source = await add_source(client, notebook_id, out_path, wait=True)
        results.append(SourceInfo(
            component=plan.component,
            appendix_num=plan.appendix_num,
            part=plan.part,
            total_parts=plan.total_parts,
            start_page=plan.start_page,
            end_page=plan.end_page,
            pdf_path=out_path,
            notebook_source=notebook_source,
        ))
        logger.info(
            "ingest: uploaded %s (APX%s %s, part %d/%d, p%d-%d)",
            out_path.name, plan.appendix_num, plan.component,
            plan.part, plan.total_parts, plan.start_page, plan.end_page,
        )

    return results
