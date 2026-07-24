"""
notebooklm_pipeline/qa_runner.py — run question_bank.py against a project's
NotebookLM notebook and collect the results.

Every raw answer (plus citations, when the library returns any) is saved to
<project>/NBLM_Answers/<key>.md for audit/traceability — mirroring the main
pipeline's convention of keeping raw extracts alongside processed output
(e.g. EDR hit notes' "## Raw Extract" block). assemble.py consumes the
in-memory QaResults, not these files; the files exist for a human (or the
domain-expert drafter) to check NotebookLM's grounding against.

Public interface:
    QaResults (dataclass)
    run_qa(client, notebook_id, project_path) -> QaResults
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from notebooklm_pipeline import orchestrator, question_bank
from notebooklm_pipeline.nblm_client import AskResult, NblmError, ask
from notebooklm_pipeline.section_cleanup import resolved_citations_block
from scripts.report_constants import pe_marker

logger = logging.getLogger(__name__)


async def _safe_ask(client, notebook_id: str, question: str, results: "QaResults", label: str) -> AskResult:
    """Wrap ask() so one NotebookLM request failing (even after nblm_client's
    own retries) routes to Questions_For_User.md instead of aborting the
    entire run — a single flaky streaming response shouldn't discard an
    hour of ingestion + every other already-answered question."""
    try:
        return await ask(client, notebook_id, question)
    except NblmError as exc:
        logger.error("qa_runner: %s failed, giving up: %s", label, exc)
        results.unresolved.append(f"{label} — NotebookLM request failed: {exc}")
        return AskResult(answer=pe_marker(), citations=[])


@dataclass
class QaResults:
    dashboard: dict[str, str] = field(default_factory=dict)
    # filename (e.g. "05_Records_Review.md") -> drafted markdown answer
    sections: dict[str, str] = field(default_factory=dict)
    # database_source -> list of raw record dicts (schema per question_bank)
    edr_hits: dict[str, list[dict]] = field(default_factory=dict)
    # "aerial" | "sanborn" | "city_directory" -> list of row dicts, feeds
    # scripts/export_docx.populate_historical_tables() (see question_bank.
    # historical_table_questions()).
    historical_tables: dict[str, list[dict]] = field(default_factory=dict)
    site_photos: str = ""
    maps: str = ""
    site_visit_notes: str = ""
    unresolved: list[str] = field(default_factory=list)


def _answers_dir(project_path: Path) -> Path:
    d = Path(project_path) / "NBLM_Answers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_answer(project_path: Path, key: str, result: AskResult) -> None:
    path = _answers_dir(project_path) / f"{key}.md"
    citations_block = (
        "\n".join(f"- {c}" for c in result.citations) if result.citations else "(none returned)"
    )
    path.write_text(
        f"# {key}\n\n## Answer\n\n{result.answer}\n\n## Citations\n\n{citations_block}\n",
        encoding="utf-8",
    )


async def _ask_dashboard(client, notebook_id: str, project_path: Path, results: QaResults) -> None:
    for dq in question_bank.dashboard_questions():
        result = await _safe_ask(client, notebook_id, dq.question, results, f"Dashboard field '{dq.field}'")
        _save_answer(project_path, f"dashboard_{dq.field}", result)
        value = result.answer.strip()
        results.dashboard[dq.field] = value
        if value == pe_marker():
            results.unresolved.append(f"Dashboard field '{dq.field}' not found in sources.")


async def _ask_one_section_part(
    client, notebook_id: str, question: str, section_name: str, label: str, results: QaResults
) -> AskResult:
    """Ask one section question (or extra_questions part), applying the same
    thin-answer follow-up logic as the main question."""
    result = await _safe_ask(client, notebook_id, question, results, label)
    answer, citations, structured = result.answer, result.citations, result.structured_citations

    if orchestrator.is_thin_answer(answer):
        followup = orchestrator.build_followup_question(question, answer, section_name)
        if followup:
            followup_result = await _safe_ask(client, notebook_id, followup, results, f"{label} follow-up")
            # Prefer whichever answer is less thin; a thin follow-up answer
            # still gets used so nothing is silently dropped.
            if not orchestrator.is_thin_answer(followup_result.answer):
                answer, citations, structured = (
                    followup_result.answer, followup_result.citations, followup_result.structured_citations
                )
            elif len(followup_result.answer.strip()) > len(answer.strip()):
                answer, citations, structured = (
                    followup_result.answer, followup_result.citations, followup_result.structured_citations
                )

    return AskResult(answer=answer, citations=citations, structured_citations=structured)


async def _ask_sections(client, notebook_id: str, project_path: Path, results: QaResults) -> None:
    for sq in question_bank.section_questions():
        label = f"Section {sq.section_num} ({sq.section_name})"
        first = await _ask_one_section_part(client, notebook_id, sq.question, sq.section_name, label, results)
        answer, citations = first.answer, list(first.citations)
        structured_citations = list(first.structured_citations)

        # Sections whose template is split (currently only 5.0, to keep any
        # single NotebookLM response small enough for the streaming decoder
        # to parse — see question_bank._split_records_review_template) ask
        # each remaining part separately and concatenate the answers.
        for i, extra_question in enumerate(sq.extra_questions, start=2):
            part_label = f"{label} part {i}"
            part = await _ask_one_section_part(client, notebook_id, extra_question, sq.section_name, part_label, results)
            answer = answer.rstrip() + "\n\n" + part.answer.lstrip()
            citations.extend(part.citations)
            structured_citations.extend(part.structured_citations)

        if orchestrator.is_thin_answer(answer):
            results.unresolved.append(
                f"Section {sq.section_num} ({sq.section_name}) — answer still thin after follow-up; review manually."
            )

        # Resolve inline [N] citation markers to a real "Citations:" list
        # (source + page, when NotebookLM's response actually carried that
        # metadata) rather than leaving unresolved brackets — see
        # section_cleanup.resolved_citations_block. When no structured
        # citation data comes back (the observed case for every section in
        # the 631 Northland run — "(none returned)"), this is a no-op and
        # assemble.write_sections's default clean_section_markdown() call
        # strips the bare [N] markers instead, since an unresolved citation
        # marker is worse than none. Applied after the thin-answer check so
        # a decorative citations list never masks a genuinely thin answer.
        citations_block = resolved_citations_block(structured_citations)
        if citations_block:
            answer = answer.rstrip() + "\n\n" + citations_block + "\n"

        _save_answer(
            project_path, f"section_{sq.filename}",
            AskResult(answer=answer, citations=citations, structured_citations=structured_citations),
        )
        results.sections[sq.filename] = answer


async def _ask_edr(client, notebook_id: str, project_path: Path, results: QaResults) -> None:
    for eq in question_bank.edr_enumeration_questions():
        result = await _safe_ask(
            client, notebook_id, eq.question, results, f"EDR database '{eq.database_source}'"
        )
        _save_answer(project_path, f"edr_{eq.database_source}", result)

        if result.answer == pe_marker():
            # _safe_ask already gave up and logged this to unresolved — don't
            # waste a Claude repair call trying to parse the PE-marker stub.
            records = []
        else:
            records = orchestrator.try_parse_json_array(result.answer)
            if records is None and result.answer.strip():
                records = orchestrator.repair_edr_json(eq.database_source, result.answer)
            if records is None:
                results.unresolved.append(
                    f"EDR database '{eq.database_source}' — answer was not valid JSON even "
                    "after repair; see NBLM_Answers/edr_"
                    f"{eq.database_source}.md for the raw answer."
                )
                records = []

        results.edr_hits[eq.database_source] = records


async def _ask_historical_tables(client, notebook_id: str, project_path: Path, results: QaResults) -> None:
    """Ask for structured (JSON) aerial/Sanborn/city-directory rows so
    scripts/export_docx.populate_historical_tables() has real per-row data
    for template tables §5.2.1/§5.2.2/§5.2.3 — see question_bank.
    historical_table_questions()'s docstring for why these were previously
    left as unresolved {{placeholder}} cells."""
    for hq in question_bank.historical_table_questions():
        result = await _safe_ask(
            client, notebook_id, hq.question, results, f"Historical table '{hq.table_key}'"
        )
        _save_answer(project_path, f"historical_{hq.table_key}", result)

        if result.answer == pe_marker():
            rows = []
        else:
            rows = orchestrator.try_parse_json_array(result.answer)
            if rows is None:
                results.unresolved.append(
                    f"Historical table '{hq.table_key}' — answer was not valid JSON; see "
                    f"NBLM_Answers/historical_{hq.table_key}.md for the raw answer."
                )
                rows = []

        results.historical_tables[hq.table_key] = rows


async def _ask_vision_and_guidance(client, notebook_id: str, project_path: Path, results: QaResults) -> None:
    photo_result = await _safe_ask(
        client, notebook_id, question_bank.SITE_PHOTO_DESCRIPTION_QUESTION, results, "Site photo description"
    )
    _save_answer(project_path, "site_photos", photo_result)
    results.site_photos = photo_result.answer

    map_result = await _safe_ask(
        client, notebook_id, question_bank.MAP_DESCRIPTION_QUESTION, results, "Map description"
    )
    _save_answer(project_path, "maps", map_result)
    results.maps = map_result.answer

    visit_result = await _safe_ask(
        client, notebook_id, question_bank.SITE_VISIT_SYNTHESIS_QUESTION, results, "Site visit guidance synthesis"
    )
    _save_answer(project_path, "site_visit_guidance", visit_result)
    results.site_visit_notes = visit_result.answer


async def run_qa(client, notebook_id: str, project_path: Path) -> QaResults:
    """Run the full question bank against *notebook_id* and return the
    collected QaResults. Every individual answer is also persisted to
    <project>/NBLM_Answers/ regardless of success/failure downstream."""
    project_path = Path(project_path)
    results = QaResults()

    logger.info("qa_runner: asking dashboard questions")
    await _ask_dashboard(client, notebook_id, project_path, results)

    logger.info("qa_runner: asking section questions")
    await _ask_sections(client, notebook_id, project_path, results)

    logger.info("qa_runner: asking EDR enumeration questions")
    await _ask_edr(client, notebook_id, project_path, results)

    logger.info("qa_runner: asking historical table questions")
    await _ask_historical_tables(client, notebook_id, project_path, results)

    logger.info("qa_runner: asking vision + site-visit guidance questions")
    await _ask_vision_and_guidance(client, notebook_id, project_path, results)

    orchestrator.route_unknowns(project_path, results.unresolved)
    return results
