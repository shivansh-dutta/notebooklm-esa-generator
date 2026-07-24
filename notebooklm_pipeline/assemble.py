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

import json
import logging
import re
from pathlib import Path

from agents.writer import DRAFT_MARKER, SECTIONS
from notebooklm_pipeline.section_cleanup import clean_section_markdown
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

# Fields dashboard_questions() (question_bank.py) does not ask about.
# report_status/assessment_dates aren't findable from source documents or
# consumed by export_docx's placeholder map. assessor_name/reviewer_name/
# title/last_name/ep_firm are deliberately NOT NotebookLM-derived either —
# they describe who is performing THIS assessment, which the uploaded
# source PDFs (records about the property, not about who's assessing it)
# cannot truthfully answer. Asking NotebookLM for these was exactly the
# mechanism that let a prior consultant's identity (named in a Qualifications
# or FOIL appendix) leak into "who prepared this report" during the 631
# Northland review — see question_bank._NEVER_CARRY_OVER_IDENTITY. These
# default to a PE marker so a fresh run doesn't silently fabricate an
# identity; a real engagement should overwrite them in the dashboard file
# once known (e.g. via ensure_project_scaffold not clobbering an existing
# dashboard, so setting them once persists across re-runs).
_DASHBOARD_NON_QUESTION_DEFAULTS = {
    "ep_firm": "TBD",
    "assessment_dates": "TBD",
    "report_status": "PE Review Pending",
    "assessor_name": pe_marker("environmental professional name"),
    "reviewer_name": pe_marker("reviewing professional name"),
    "title": pe_marker("EP professional title"),
    "last_name": pe_marker("EP last name"),
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

def merge_dashboard_fields(dashboard_values: dict[str, str], project_name: str) -> dict[str, str]:
    """
    Merge qa_runner's NotebookLM-answered dashboard fields with the
    non-question defaults (report_status, ep_firm, and the identity fields
    that are never NotebookLM-derived — see _DASHBOARD_NON_QUESTION_DEFAULTS).
    Shared by write_dashboard() and build_qualifications_markdown() so both
    see the same resolved values.
    """
    fields = {**dashboard_values, **{
        k: v for k, v in _DASHBOARD_NON_QUESTION_DEFAULTS.items() if k not in dashboard_values
    }}
    fields.setdefault("project_name", project_name)
    return fields


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
    fields = merge_dashboard_fields(dashboard_values, project_name)

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
# Section 11.0 — Qualifications (built deterministically, never NotebookLM-asked)
# ---------------------------------------------------------------------------

def build_qualifications_markdown(dashboard_values: dict[str, str], project_name: str) -> str:
    """
    Build Section 11.0 (Qualifications and Declaration of Environmental
    Professionals) from the project dashboard's own EP/firm/reviewer fields
    — question_bank.py deliberately never asks NotebookLM this section (see
    question_bank._NOTEBOOKLM_EXCLUDED_SECTIONS / _NEVER_CARRY_OVER_IDENTITY):
    the 631 Northland review found NotebookLM answering "who prepared this
    report" by lifting the PRIOR consultant's own Qualifications appendix
    verbatim (a different firm's name, a different EP's bio), since that
    appendix is just another uploaded source to it. Detailed personnel
    credentials/resumes are left as a PE marker rather than guessed — this
    function only ever states what's known from the dashboard.
    """
    fields = merge_dashboard_fields(dashboard_values, project_name)
    firm = fields.get("ep_firm") or pe_marker("preparing firm name")
    assessor = fields.get("assessor_name") or pe_marker("environmental professional name")
    title = fields.get("title") or pe_marker("EP professional title")
    reviewer = fields.get("reviewer_name") or pe_marker("reviewing professional name")

    lines = [
        "# 11.0 Qualifications and Declaration of Environmental Professionals",
        "",
        f"This Phase I Environmental Site Assessment (ESA) was conducted and prepared by "
        f"qualified environmental professionals of {firm}.",
        "",
        f"The undersigned Environmental Professional(s) — {assessor}, {title} — declare "
        "that this assessment was conducted in conformance with the scope and "
        "limitations of ASTM E1527-21, and that the undersigned meet the definition of "
        "Environmental Professional as set forth in 40 CFR §312.10(b).",
        "",
        f"Reviewed by: {reviewer}.",
        "",
        pe_marker(
            "individual EP credentials/resumes (education, professional licensure, "
            "years of relevant experience)"
        ),
        "",
    ]
    return "\n".join(lines) + "\n"


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

    Every answer is passed through section_cleanup.clean_section_markdown()
    first — strips leaked frontmatter/DRAFT-banner/template-fence scaffolding
    that NotebookLM occasionally echoes verbatim (see that module's
    docstring), so it never reaches the exported DOCX unfiltered.
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
        path.write_text(DRAFT_MARKER + clean_section_markdown(answer), encoding="utf-8")
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
# Historical tables (aerial / Sanborn / city directory) — feeds
# scripts/export_docx.populate_historical_tables()
# ---------------------------------------------------------------------------

def write_historical_tables(project_path: Path, historical_tables: dict[str, list[dict]]) -> Path:
    """
    Write qa_runner's {table_key: [row, ...]} to
    <project>/Historical_Records/historical_tables.json — a single JSON
    file, not per-row markdown, since (unlike EDR hits) there's no
    within-radius/beyond-radius routing decision for these rows and the
    consumer (export_docx.populate_historical_tables) only needs the raw
    rows, not an audit-friendly per-record file.
    """
    project_path = Path(project_path)
    out_dir = project_path / "Historical_Records"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "historical_tables.json"
    out_path.write_text(json.dumps(historical_tables, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def assemble(project_path: Path, project_name: str, results) -> None:
    """Write all three export-ready artifacts from a qa_runner.QaResults."""
    project_path = Path(project_path)
    write_dashboard(project_path, project_name, results.dashboard)

    # 11.0 Qualifications is never in results.sections (question_bank.py
    # excludes it from NotebookLM extraction entirely) — build it here from
    # dashboard fields instead. See build_qualifications_markdown()'s
    # docstring for why.
    sections = dict(results.sections)
    sections["11_Qualifications.md"] = build_qualifications_markdown(results.dashboard, project_name)

    write_sections(project_path, sections)
    write_edr_hits(project_path, results.edr_hits)
    write_historical_tables(project_path, results.historical_tables)
    logger.info("assemble: wrote dashboard, %d section file(s), EDR hit notes", len(sections))
