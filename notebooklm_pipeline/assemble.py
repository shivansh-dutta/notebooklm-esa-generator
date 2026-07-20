"""
notebooklm_pipeline/assemble.py — deterministic mapping of qa_runner's
QaResults into the three on-disk artifacts scripts.export_docx.run_export_docx
actually reads (see notebooklm_pipeline/__init__.py's docstring for why only
these three matter): 00_Project_Dashboard.md, Report_Sections/*.md, and
EDR_Database_Hits/ + Manual_Review/*.md.

Nothing in this module calls Claude or NotebookLM — it is pure formatting of
already-collected answers, so it is fully unit-testable without either.

Public interface:
    write_dashboard(project_path, project_name, dashboard_values) -> Path
    write_sections(project_path, sections) -> list[Path]
    write_edr_hits(project_path, edr_hits) -> list[Path]
    assemble(project_path, project_name, results) -> None
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agents.writer import DRAFT_MARKER, SECTIONS
from scripts.report_constants import pe_marker

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
EDR_HIT_TEMPLATE_PATH = REPO_ROOT / "TemplateVault" / "EDR_Hit_Template.md"

# Auto-draft/manual-review cutoff for EDR hits — a hit within this distance
# gets drafted straight into EDR_Database_Hits/, beyond it goes to
# Manual_Review/ (still counted in the radius-table totals, just flagged
# for a human to double-check). This is intentionally its own constant, not
# imported from agents.researcher.RADIUS_FT (0.1 mi / 528 ft) — the two
# pipelines are isolated on purpose, so notebooklm_pipeline's threshold can
# be tuned independently of the main pipeline's.
AUTO_DRAFT_RADIUS_FT = 1320  # 0.25 miles

# Fields dashboard_questions() (question_bank.py) does not ask about, since
# they either aren't findable from source documents (report_status is a
# workflow state, not a fact) or aren't consumed by export_docx's
# placeholder map (ep_firm/assessment_dates only feed the separate PDF
# cover path in scripts/export.py, not the DOCX template).
_DASHBOARD_NON_QUESTION_DEFAULTS = {
    "ep_firm": "TBD",
    "assessment_dates": "TBD",
    "report_status": "PE Review Pending",
}

_SLUG_MAX = 60


def _escape_yaml(value: str) -> str:
    """
    Prepare a value for single-line `key: "value"` frontmatter.

    scripts/export_docx.py's frontmatter parser is deliberately simple (its
    _FIELD_RE captures everything after `key:` to end-of-line, and
    _parse_frontmatter_block only strips one leading/trailing `"` char per
    field — it does NOT unescape backslash-quotes). Backslash-escaping an
    embedded `"` here would therefore round-trip as a literal backslash
    when read back (verified: agents/researcher.py's own _escape_yaml does
    the same backslash-escaping, so any EDR hit site name/address with an
    embedded quote has the same round-trip issue there too). Leaving
    embedded quotes unescaped round-trips correctly instead, since the
    outer strip('"') only ever touches the two outermost characters. The
    only real risk is an embedded newline, which would break the
    single-line `key: value` pattern entirely — guard against that instead.
    """
    return str(value).replace("\n", " ").replace("\r", " ")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", (text or "").lower())
    slug = re.sub(r"[\s\-]+", "_", slug)
    return slug[:_SLUG_MAX].strip("_") or "unknown_site"


def classify_distance(distance_ft) -> str:
    """
    Classify a distance (feet) against AUTO_DRAFT_RADIUS_FT (0.25 mi).
    Returns "within", "beyond", or "unknown" (missing/unparseable).
    """
    if distance_ft in (None, "", "null"):
        return "unknown"
    try:
        return "within" if float(distance_ft) <= AUTO_DRAFT_RADIUS_FT else "beyond"
    except (TypeError, ValueError):
        return "unknown"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def write_dashboard(project_path: Path, project_name: str, dashboard_values: dict[str, str]) -> Path:
    """
    Write 00_Project_Dashboard.md with the same frontmatter schema
    scripts/init_project.py::generate_dashboard produces, so
    scripts/export_docx.py reads it unchanged. Values come from qa_runner's
    NotebookLM answers; question_bank.dashboard_questions() already
    instructs NotebookLM to answer pe_marker() for anything not found, so a
    field that couldn't be grounded ends up with that marker as its literal
    value here — which export_docx's placeholder substitution then prints
    as-is (a real, non-empty string), landing on the same visible "PE TO
    COMPLETE" result as if the field were left entirely unresolved.
    """
    project_path = Path(project_path)
    fields = {**dashboard_values, **{
        k: v for k, v in _DASHBOARD_NON_QUESTION_DEFAULTS.items() if k not in dashboard_values
    }}
    fields.setdefault("project_name", project_name)

    lines = ["---"]
    # Keep a stable, readable field order matching init_project's schema.
    ordered_keys = [
        "project_name", "site_address", "city", "county", "state", "zip",
        "client_name", "client_address", "ep_firm", "project_no",
        "assessment_dates", "report_draft_date", "report_status",
        "assessor_name", "reviewer_name", "title", "last_name",
    ]
    for key in ordered_keys:
        value = fields.get(key, "TBD")
        lines.append(f'{key}: "{_escape_yaml(value)}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# Phase 1 ESA — {project_name}")
    lines.append("")
    lines.append(
        "_Generated by notebooklm_pipeline — a NotebookLM-grounded draft. "
        "Full PE review required before issue._"
    )
    lines.append("")

    dashboard_path = project_path / "00_Project_Dashboard.md"
    dashboard_path.write_text("\n".join(lines), encoding="utf-8")
    return dashboard_path


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def write_sections(project_path: Path, sections: dict[str, str]) -> list[Path]:
    """
    Overwrite each Report_Sections/<filename> (already scaffolded by
    scripts.init_project.copy_templates, matching agents.writer.SECTIONS'
    filename set) with its NotebookLM-drafted answer, DRAFT_MARKER-prefixed
    — the same convention agents/writer.py::write_section uses, so
    scripts/export_docx.py's parse_writer_sections() reads either
    pipeline's output identically.

    A section with no answer keeps its existing (template) content
    untouched rather than being blanked, and is logged — mirrors
    agents.writer.run_writer's "template not found — skipping" behavior for
    the analogous case.
    """
    project_path = Path(project_path)
    sections_dir = project_path / "Report_Sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for _num, _name, filename in SECTIONS:
        answer = sections.get(filename)
        path = sections_dir / filename
        if answer is None:
            logger.warning("assemble: no NotebookLM answer for %s — leaving template as-is", filename)
            continue
        path.write_text(DRAFT_MARKER + answer, encoding="utf-8")
        written.append(path)

    return written


# ---------------------------------------------------------------------------
# EDR hit notes
# ---------------------------------------------------------------------------

def _build_hit_note(fields: dict[str, str]) -> str:
    template = EDR_HIT_TEMPLATE_PATH.read_text(encoding="utf-8") if EDR_HIT_TEMPLATE_PATH.exists() else (
        "---\nsite_name: \"\"\naddress: \"\"\ndatabase_source: \"\"\ndistance_ft: \"\"\n"
        "direction: \"\"\nprogram_id: \"\"\nstatus: \"\"\nnysdec_program: \"\"\n"
        "preliminary_classification: \"\"\n---\n\n# EDR Hit: \n\n## Raw Extract\n\n"
        "## Assessment Notes\n"
    )
    content = template
    for key, value in fields.items():
        content = re.sub(
            rf'^({re.escape(key)}:\s*)("([^"]*)"|(null)|(\d+\.?\d*))',
            lambda m, v=value: f'{m.group(1)}"{_escape_yaml(v)}"',
            content,
            flags=re.MULTILINE,
        )
    content = re.sub(
        r"^# EDR Hit:.*$",
        f"# EDR Hit: {fields.get('site_name') or pe_marker('site name')}",
        content,
        flags=re.MULTILINE,
    )
    raw_note = (
        "_No verbatim raw extract — this record was enumerated from "
        "NotebookLM's structured JSON answer, not a page-text grep. See "
        f"`NBLM_Answers/edr_{fields.get('database_source', 'unknown')}.md` "
        "for the full raw question/answer/citations._"
    )
    content = re.sub(
        r"(## Raw Extract\n+)<!-- .+?-->",
        lambda m: m.group(1) + raw_note,
        content,
        flags=re.DOTALL,
    )
    assessment_note = (
        "See the Section 5.0 (Records Review) narrative for this listing's "
        "grounded REC/CREC/HREC classification and ASTM E1527-21 citation — "
        "classification is drafted once, at the section level, rather than "
        "repeated per hit note, to keep NotebookLM/Claude usage minimal."
    )
    content = re.sub(
        r"(## Assessment Notes\n+)<!-- .+?-->",
        lambda m: m.group(1) + assessment_note,
        content,
        flags=re.DOTALL,
    )
    return content


def _insert_banner_after_frontmatter(content: str, banner: str) -> str:
    """
    Insert *banner* immediately after the closing `---` of the leading YAML
    frontmatter block, rather than before it.

    scripts/export_docx.py's frontmatter parser requires the file to start
    with `---` at position 0 (_FRONTMATTER_RE.match, not .search) — text
    prepended before the frontmatter silently breaks parsing entirely,
    which load_edr_hit_records treats as "no frontmatter" and drops the
    record. (agents/researcher.py's _create_hit_note has this same
    before-frontmatter prepend for its "Manual Review required" banner —
    worth fixing there too, flagged separately; not touched here per this
    session's scope.) Falls back to prepending if no frontmatter block is
    found, rather than raising.
    """
    parts = content.split("---", 2)
    if len(parts) == 3:
        return f"---{parts[1]}---\n\n{banner}\n{parts[2].lstrip(chr(10))}"
    return banner + "\n\n" + content


def write_edr_hits(project_path: Path, edr_hits: dict[str, list[dict]]) -> list[Path]:
    """
    Turn qa_runner's {database_source: [record, ...]} into one hit-note .md
    per record, routed to EDR_Database_Hits/ (within AUTO_DRAFT_RADIUS_FT) or
    Manual_Review/ (beyond / unparseable distance) — same routing contract
    scripts/export_docx.py::load_edr_hit_records already expects (it reads
    both folders; see that function's docstring for why Manual_Review/
    records still count toward the ASTM list radius tables).
    """
    project_path = Path(project_path)
    hits_dir = project_path / "EDR_Database_Hits"
    manual_dir = project_path / "Manual_Review"
    hits_dir.mkdir(parents=True, exist_ok=True)
    manual_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    seen_names: set[str] = set()

    for database_source, records in edr_hits.items():
        for record in records:
            site_name = str(record.get("site_name") or pe_marker("site name")).strip()
            distance_ft = record.get("distance_ft")
            fields = {
                "site_name": site_name,
                "address": str(record.get("address") or pe_marker("address")),
                "database_source": database_source,
                "distance_ft": "" if distance_ft is None else str(distance_ft),
                "direction": str(record.get("direction") or pe_marker("direction")),
                "program_id": str(record.get("program_id") or pe_marker("program ID")),
                "status": str(record.get("status") or pe_marker("regulatory status")),
                "nysdec_program": str(record.get("nysdec_program") or ""),
                "preliminary_classification": str(
                    record.get("preliminary_classification")
                    or pe_marker("see Section 5.0 narrative")
                ),
            }

            bucket = classify_distance(distance_ft)
            target_dir = hits_dir if bucket == "within" else manual_dir

            filename = f"{database_source}_{_slugify(site_name)}.md"
            candidate = filename
            i = 2
            while candidate in seen_names:
                candidate = f"{database_source}_{_slugify(site_name)}_{i}.md"
                i += 1
            seen_names.add(candidate)

            note_content = _build_hit_note(fields)
            if bucket != "within":
                reason = (
                    "distance is beyond the 0.25-mile (1320 ft) auto-draft radius"
                    if bucket == "beyond"
                    else "distance could not be determined from NotebookLM's answer"
                )
                banner = (
                    f"> **Manual Review required:** routed here because {reason}. "
                    f"Parsed distance_ft: {fields['distance_ft'] or '(none)'}. Verify "
                    "manually and move into EDR_Database_Hits/ if it should be "
                    "counted toward the report's radius tables."
                )
                note_content = _insert_banner_after_frontmatter(note_content, banner)

            note_path = target_dir / candidate
            note_path.write_text(note_content, encoding="utf-8")
            written.append(note_path)

    if not written:
        (hits_dir / "no_hits.md").write_text(
            "# EDR Database Hits\n\nNo hits were enumerated by NotebookLM across "
            "any tracked database. Verify against the raw EDR appendix directly "
            "before relying on this — see NBLM_Answers/edr_*.md.\n",
            encoding="utf-8",
        )

    return written


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def assemble(project_path: Path, project_name: str, results) -> None:
    """Write all three export-ready artifacts from a qa_runner.QaResults."""
    project_path = Path(project_path)
    write_dashboard(project_path, project_name, results.dashboard)
    write_sections(project_path, results.sections)
    write_edr_hits(project_path, results.edr_hits)
    logger.info("assemble: wrote dashboard, %d section file(s), EDR hit notes", len(results.sections))
