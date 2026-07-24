"""
notebooklm_pipeline/question_bank.py — the standard question set asked of a
project's NotebookLM notebook.

Reuses (imports only) the domain framing already established for the main
pipeline's Writer — agents.writer.SECTIONS (the 13 ASTM E1527-21 sections,
matching the Envicon template exactly) and agents.writer.VAULT_FILES (the
legal/regulatory reference files) — so both pipelines produce prose in the
same voice and structure. The difference is *where the grounding comes
from*: here NotebookLM answers directly against the uploaded source PDFs
(and, per ingest.py, the uploaded LegalVault files), not against
pre-extracted hit notes assembled by Researcher/Historian.

Four question groups:
  1. dashboard_questions()        — one question per cover/signature field
  2. section_questions()          — one question per ASTM template section;
                                     the answer must reproduce the template's
                                     own headings and FILL-IN markers, exactly
                                     like agents.writer's contract, so
                                     assemble.py can hand the result straight
                                     to scripts.export_docx.parse_writer_sections
  3. edr_enumeration_questions()  — one question per EDR database, requesting
                                     a strict JSON array — long hit lists are
                                     enumerated, not summarized into prose
  4. site_visit_synthesis_question() — the forward-looking "what to verify/
                                     photograph on-site" checklist question

Every question instructs NotebookLM to use report_constants.pe_marker() for
anything genuinely not findable in the sources, never to guess — the same
"never fabricate" convention scripts/export_docx.py already relies on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agents.writer import SECTIONS, VAULT_FILES
from scripts.report_constants import DATABASE_TO_LIST, pe_marker

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_VAULT = REPO_ROOT / "TemplateVault"
LEGAL_VAULT = REPO_ROOT / "LegalVault"

_NEVER_GUESS = (
    "If a specific fact is not findable anywhere in the uploaded sources, do "
    f'NOT guess or infer it — write exactly "{pe_marker()}: <short description '
    'of what is missing>" in its place and move on. Do not invent addresses, '
    "dates, names, distances, or regulatory statuses."
)

# Identity/authorship firewall — added after the 631 Northland review found
# NotebookLM answering "who prepared this report" / "who submitted this FOIL
# request" style questions by lifting a PRIOR consultant's own appendix
# (their Qualifications-of-Professionals appendix, their FOIL cover letter)
# verbatim, since to NotebookLM that's just another uploaded source with
# equal authority to everything else. THIS report's preparer/reviewer/firm
# identity is supplied by the project dashboard (see assemble.py), never by
# source-document extraction — a prior consultant's name in the raw package
# is background material about the property's history, not a fact about who
# is doing THIS assessment.
_NEVER_CARRY_OVER_IDENTITY = (
    "IMPORTANT — never state or imply who prepared, authored, reviewed, or "
    "signed THIS report; the name of the consulting firm performing THIS "
    "assessment; personnel names, biographies, or professional "
    "qualifications; or who submitted any FOIL/records request on THIS "
    "engagement's behalf — even if a source document names a consultant, "
    "firm, or person in connection with the property (e.g. a prior "
    "assessment's authors, or a previous FOIL requester). Those identities "
    "belong to a different, earlier engagement, not this one. If the "
    "template asks for such a name, leave it as a PE marker instead of "
    "reusing a name found in the sources. You may still report the "
    "substance of what a prior report or FOIL request found/requested — "
    "just not who performed it."
)


# ---------------------------------------------------------------------------
# 1. Dashboard / cover fields
# ---------------------------------------------------------------------------

@dataclass
class DashboardQuestion:
    field: str          # 00_Project_Dashboard.md frontmatter key
    question: str


# NOTE: assessor_name / reviewer_name / title / last_name are deliberately
# NOT asked here (moved to assemble.py's _DASHBOARD_NON_QUESTION_DEFAULTS,
# same treatment as ep_firm). These describe who is performing THIS
# assessment — a fact the uploaded source PDFs cannot truthfully answer,
# since those PDFs are the property records being assessed, not a record of
# who's assessing them. Asking NotebookLM was the exact mechanism that let a
# prior consultant's identity (found in a Qualifications or FOIL appendix)
# leak into "who prepared this report" — see _NEVER_CARRY_OVER_IDENTITY.
DASHBOARD_FIELDS: list[tuple[str, str]] = [
    ("site_address", "the complete street address of the subject property"),
    ("city", "the city the subject property is located in"),
    ("county", "the county the subject property is located in"),
    ("state", "the two-letter state abbreviation the subject property is located in"),
    ("zip", "the ZIP code of the subject property"),
    ("client_name", "the name of the client this Phase 1 ESA was prepared for (the User, per ASTM E1527-21)"),
    ("client_address", "the client's mailing address"),
    ("project_no", "the project or job number assigned to this assessment"),
    ("report_draft_date", "the report date (or site visit / assessment date if no report date is stated)"),
]


def dashboard_questions() -> list[DashboardQuestion]:
    out = []
    for field, description in DASHBOARD_FIELDS:
        q = (
            f"What is {description}? Answer with ONLY the value itself, no "
            f'explanation, no leading label. If not stated anywhere in the '
            f'uploaded sources, answer with exactly "{pe_marker()}".'
        )
        out.append(DashboardQuestion(field=field, question=q))
    return out


# ---------------------------------------------------------------------------
# 2. Section questions — one per ASTM template section
# ---------------------------------------------------------------------------

@dataclass
class SectionQuestion:
    section_num: str
    section_name: str
    filename: str        # matches Report_Sections/<filename>
    question: str
    # Section 5.0's template asks NotebookLM to classify every EDR hit
    # (REC/CREC/HREC/de minimis) across dozens of records in one answer —
    # empirically large enough to break the notebooklm-py streaming decoder
    # regardless of how short the *question* is (see _split_records_review_template).
    # When non-empty, qa_runner asks each of these as separate follow-on
    # requests and appends their answers to the main question's answer.
    extra_questions: list[str] = field(default_factory=list)


_SECTION_5_INSTRUCTIONS = """
CLASSIFICATION REQUIREMENT — SECTION 5.0 ONLY: classify every environmental
database record discussed under "Federal records" / "State, tribal, and
local records" as REC, CREC (Controlled REC), HREC (Historical REC), or De
Minimis, citing the applicable ASTM E1527-21 section number grounded in the
uploaded ASTM/legal sources. For a Spill Program record marked "Closed -
NFAR" or "Closed - No Further Action Required", check the uploaded DER-10
closure criteria before defaulting to REC — a properly closed spill is often
HREC or de minimis.
"""


def _load_template(filename: str) -> str:
    path = TEMPLATE_VAULT / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _build_question(section_num: str, section_name: str, extra: str, template_content: str) -> str:
    return f"""Draft Section {section_num} — {section_name} of a Phase 1 Environmental
Site Assessment (ASTM E1527-21), grounded ONLY in the uploaded source
documents (the raw package appendices and, where relevant, the legal
reference files).

Fill in every `<!-- DRAFT: ... -->` comment with grounded prose. Leave every
`<!-- FILL IN MANUALLY: ... -->` comment untouched — it must appear in your
answer EXACTLY as it appears in the template. Keep every heading line
EXACTLY as it appears in the template (same text, same number of #'s) — do
not renumber, rename, reorder, or omit any heading. Output ONLY the
completed section markdown — no preamble, no explanation, no code fence.

{_NEVER_GUESS}
{_NEVER_CARRY_OVER_IDENTITY}
{extra}
--- BEGIN TEMPLATE ---
{template_content}
--- END TEMPLATE ---
"""


# Section 5.0's template asks NotebookLM to classify every EDR database hit
# (REC/CREC/HREC/de minimis, dozens of records on an EDR-heavy urban site) in
# one response — empirically large enough to break the notebooklm-py
# streaming decoder even when the *question* itself is short. Splitting the
# ask at this heading boundary (Federal records vs. State/tribal/local
# records) roughly halves each individual response's size; the two answers
# are concatenated back into one Report_Sections/05_Records_Review.md by
# qa_runner, and export_docx's heading-based parser doesn't care how many
# NotebookLM calls produced the headings it finds.
# Section 11.0 (Qualifications and Declaration of Environmental
# Professionals) is never asked of NotebookLM at all — see
# _NEVER_CARRY_OVER_IDENTITY above for why asking it is exactly what caused
# the 631 Northland review's authorship bug (NotebookLM answered "who
# prepared this report" from the prior consultant's own Qualifications
# appendix). assemble.py builds this section deterministically from the
# project dashboard's own EP/firm fields instead — see
# assemble.build_qualifications_markdown().
_NOTEBOOKLM_EXCLUDED_SECTIONS = {"11.0"}

_RECORDS_REVIEW_SPLIT_HEADING = "### State, tribal, and local records"


def _split_records_review_template(template_content: str) -> tuple[str, str] | None:
    idx = template_content.find(_RECORDS_REVIEW_SPLIT_HEADING)
    if idx == -1:
        return None
    return template_content[:idx], template_content[idx:]


def section_questions() -> list[SectionQuestion]:
    """
    One question per agents.writer.SECTIONS entry. Each question hands
    NotebookLM the exact TemplateVault template (same file the main
    pipeline's Writer fills) and asks it to fill it the same way Writer
    does: replace `<!-- DRAFT: ... -->` comments with grounded prose,
    preserve `<!-- FILL IN MANUALLY: ... -->` comments verbatim, keep every
    heading line character-for-character (assemble.py / export_docx's
    heading matcher depends on this), output nothing else.
    """
    out = []
    for section_num, section_name, filename in SECTIONS:
        if section_num in _NOTEBOOKLM_EXCLUDED_SECTIONS:
            continue
        template_content = _load_template(filename)
        if not template_content:
            continue
        extra = _SECTION_5_INSTRUCTIONS if section_num == "5.0" else ""

        extra_questions: list[str] = []
        split = _split_records_review_template(template_content) if section_num == "5.0" else None
        if split is not None:
            first_half, second_half = split
            question = _build_question(section_num, section_name, extra, first_half)
            extra_questions = [_build_question(section_num, section_name, extra, second_half)]
        else:
            question = _build_question(section_num, section_name, extra, template_content)

        out.append(SectionQuestion(
            section_num=section_num, section_name=section_name,
            filename=filename, question=question, extra_questions=extra_questions,
        ))
    return out


def legal_vault_source_paths() -> list[Path]:
    """Paths of LegalVault files that exist on disk (some may still be
    stubs — that's fine, ingest.py uploads whatever is present so Section
    5.0's citation grounding has something to work against)."""
    return [LEGAL_VAULT / name for name in VAULT_FILES if (LEGAL_VAULT / name).exists()]


# ---------------------------------------------------------------------------
# 3. EDR enumeration — one question per database, strict JSON
# ---------------------------------------------------------------------------

@dataclass
class EdrQuestion:
    database_source: str   # a DATABASE_TO_LIST key
    question: str


_EDR_JSON_SCHEMA_EXAMPLE = json.dumps(
    [{
        "site_name": "EXAMPLE FACILITY INC",
        "address": "123 Example St, Anytown, NY",
        "distance_ft": 350,
        "direction": "NE",
        "program_id": "NYD000000000",
        "status": "Active",
        "nysdec_program": "RCRA Generator",
        "preliminary_classification": pe_marker("requires review of raw extract"),
    }],
    indent=2,
)


def edr_enumeration_questions() -> list[EdrQuestion]:
    """
    One question per DATABASE_TO_LIST key (report_constants.py), asking
    NotebookLM to enumerate every listing in that specific database found
    in the EDR radius report — as strict JSON, not prose. Splitting by
    database (rather than one giant "list every hit" question) keeps each
    answer short enough that NotebookLM doesn't truncate or summarize a
    long list, since a PE scrutinizes the resulting radius-table counts
    closely (see scripts/export_docx.py::populate_edr_tables).
    """
    out = []
    for db in DATABASE_TO_LIST:
        question = f"""In the uploaded EDR (Environmental Data Resources) radius report, find
every listing under the "{db}" database. For each one, extract: site_name,
address, distance_ft (straight-line distance from the subject property, in
feet — convert from miles if needed), direction (one of N/NE/E/SE/S/SW/W/NW,
or "on-site" if the listing IS the subject property), program_id (the
EDR/regulatory ID number), status (the regulatory status as stated, e.g.
"Active", "Closed - NFAR", "Inactive"), and nysdec_program (the specific
program name if stated, else "").

Respond with ONLY a JSON array, no other text, no markdown code fence. Each
element must have exactly these keys: site_name, address, distance_ft,
direction, program_id, status, nysdec_program, preliminary_classification.
Leave preliminary_classification as an empty string "" — that is filled in
separately, not by you. If a field is not stated for a given listing, use
"{pe_marker()}" as its value (or null for distance_ft) rather than guessing.
If there are NO listings under "{db}" in the uploaded EDR report, respond
with exactly: []

Example shape (values are illustrative only, not real data):
{_EDR_JSON_SCHEMA_EXAMPLE}
"""
        out.append(EdrQuestion(database_source=db, question=question))
    return out


# ---------------------------------------------------------------------------
# 3b. Site photos / historical maps — vision, feeds site_visit_guidance.py
# ---------------------------------------------------------------------------

# These rely on NotebookLM's own vision model reading the uploaded site-photo
# and map appendix pages directly — no separate image-extraction step is
# needed on our side (unlike agents/historian.py's page-render-to-PNG
# approach for Claude vision calls). Both are self-reporting ("if present")
# rather than gated on ingest.py's component detection, since site photos
# in particular may be entirely absent from a real future package (per the
# user) — NotebookLM saying so is simpler than qa_runner tracking which
# components were actually uploaded.

SITE_PHOTO_DESCRIPTION_QUESTION = """If the uploaded sources include a Site Photographs appendix, describe every
individual site photograph: (1) its likely location on or around the subject
property, (2) camera orientation/facing direction if determinable, (3) what
is visible in the photo, and (4) any potential environmental concern
suggested by the photo (staining, drums, tanks, vents, distressed
vegetation, etc.). Number your answer by photograph/page so it can be
cross-referenced later. If no site photographs are present in the uploaded
sources, respond with exactly: "No site photographs were provided in the
uploaded sources."
"""

MAP_DESCRIPTION_QUESTION = """If the uploaded sources include historical maps, aerial photographs, Sanborn
fire insurance maps, or USGS topographic maps, summarize what each shows for
the subject property and surrounding area by year/date: land use, structures
present, and any features suggesting past industrial/commercial/hazardous
use (fuel tanks, rail spurs, industrial buildings, disturbed ground, etc.).
Organize chronologically. If no such maps are present in the uploaded
sources, respond with exactly: "No historical maps or aerial photographs
were provided in the uploaded sources."
"""


# ---------------------------------------------------------------------------
# 3c. Historical tables — aerial photos / Sanborn maps / city directories
# ---------------------------------------------------------------------------

# The 631 Northland review found these three template tables (§5.2.1, §5.2.2,
# §5.2.3) left with unresolved `{{year}}/{{observation}}` placeholder cells
# directly beneath fully-drafted narrative prose, because nothing ever asked
# NotebookLM for this data in a structured, per-row shape — the section
# question only asks for prose (question_bank._build_question), and
# scripts/docx_helpers.markdown_lite_to_blocks deliberately doesn't parse
# markdown tables. These three questions mirror edr_enumeration_questions()'s
# approach (strict JSON, one row per finding) so
# scripts/export_docx.populate_historical_tables() has real data to fill the
# tables with — see that function's docstring for how it locates each table
# (they share an identical header+first-row shape for 5.2.1/5.2.2, so it
# locates by heading proximity, not header signature).


@dataclass
class HistoricalTableQuestion:
    table_key: str   # "aerial", "sanborn", or "city_directory"
    question: str


_AERIAL_SANBORN_JSON_EXAMPLE = json.dumps(
    [{"year": "1986", "subject_property": "Vacant lot", "adjacent_properties": "Industrial buildings to the east"}],
    indent=2,
)
_CITY_DIRECTORY_JSON_EXAMPLE = json.dumps(
    [{"year": "1965", "address": "631 Northland Ave", "occupant": "Example Manufacturing Co."}],
    indent=2,
)


def historical_table_questions() -> list[HistoricalTableQuestion]:
    out = []
    for key, source_label, columns, example in (
        ("aerial", "aerial photographs", ("year", "subject_property", "adjacent_properties"), _AERIAL_SANBORN_JSON_EXAMPLE),
        ("sanborn", "Sanborn fire insurance maps", ("year", "subject_property", "adjacent_properties"), _AERIAL_SANBORN_JSON_EXAMPLE),
        ("city_directory", "city or street directories", ("year", "address", "occupant"), _CITY_DIRECTORY_JSON_EXAMPLE),
    ):
        cols = ", ".join(columns)
        question = f"""If the uploaded sources include {source_label} for or near the subject
property, extract one row per year/edition reviewed with exactly these
keys: {cols}. Respond with ONLY a JSON array, no other text, no markdown
code fence. {_NEVER_GUESS} If no {source_label} are present in the uploaded
sources, respond with exactly: []

Example shape (values are illustrative only, not real data):
{example}
"""
        out.append(HistoricalTableQuestion(table_key=key, question=question))
    return out


# ---------------------------------------------------------------------------
# 4. Site-visit guidance synthesis
# ---------------------------------------------------------------------------

SITE_VISIT_SYNTHESIS_QUESTION = f"""Based on everything in the uploaded sources (the EDR radius report, any
historical maps/aerials, and any site photographs or the environmental
questionnaire if present), produce a field checklist for the environmental
professional's upcoming site visit.

For EACH nearby or on-site environmental database listing, adjoining
property of concern, or historical use of concern that you identified,
write ONE bullet in this exact format:

- **[what to look for]** — [where on/near the site, using direction/distance
  from the subject property] — [why it matters: which listing or historical
  use this confirms or denies] — [what to photograph or document to
  confirm/deny it]

Example (illustrative only, not real data):
- **Former gas station USTs/vent pipes/staining** — northeast adjoining
  parcel, ~150 ft — confirms or denies EDR LUST listing #NYD000000000 —
  photograph the SE corner of the adjoining parcel for vent pipes, fill
  ports, or pavement staining.

Only include items you can actually ground in the uploaded sources — do not
invent concerns. {_NEVER_GUESS} If you find nothing warranting a site-visit
follow-up, say so plainly: "No specific site-visit verification items were
identified from the available sources."
"""
