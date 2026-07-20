"""
scripts/report_constants.py — Phase 1 ESA Report Generator

Small, static data used by scripts/export_docx.py to fill the Envicon Phase I
ESA report template: the marker for content the pipeline genuinely cannot
know (site-visit-only observations, etc.), the placeholder -> project-
metadata field map, and (Phase B) the Acronyms list, EDR database-to-list
mapping, and table header signatures used to locate each template table.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# PE-completion marker
# ---------------------------------------------------------------------------

# Used anywhere the pipeline has no data source for a required field (e.g.
# site-visit-only interior/exterior checklist observations, adjacent-property
# ownership, previous-reports history). Deliberately NOT the template's own
# `{{...}}` placeholder syntax, so a completed export can be scanned for zero
# remaining literal "{{" as a simple, unambiguous "nothing was left unfilled
# silently" check, while PE_MARKER occurrences remain visibly distinct as
# "known gap, needs a human" rather than "bug."
PE_MARKER = "» PE TO COMPLETE"


def pe_marker(description: str = "") -> str:
    """Build a PE-completion marker, optionally with a short description of
    what's missing (e.g. pe_marker("site-visit observation"))."""
    return f"{PE_MARKER}: {description}" if description else PE_MARKER


# ---------------------------------------------------------------------------
# Placeholder -> project-metadata field map
# ---------------------------------------------------------------------------

# Maps the template's `{{Placeholder Name}}` tokens (normalized: stripped +
# casefolded, matching docx_helpers.replace_placeholders' lookup key) to the
# 00_Project_Dashboard.md frontmatter field that supplies the value. Fields
# not yet present in the dashboard schema (see Phase C: scripts/init_project.py
# extension) simply won't resolve and will be left as PE_MARKER by the
# exporter — never guessed, never silently blank.
PLACEHOLDER_FIELD_MAP: dict[str, str] = {
    "property address": "site_address",
    "city": "city",
    "county": "county",
    "state": "state",
    "zip": "zip",
    "client name": "client_name",
    "client address": "client_address",
    "report date": "report_draft_date",
    "project number": "project_no",
    "assessor name": "assessor_name",
    "reviewer name": "reviewer_name",
    "title": "title",
    "last name": "last_name",
}


# ---------------------------------------------------------------------------
# EDR radius tables (template section 5.3) — Federal + State/Tribal/Local
# ---------------------------------------------------------------------------

# Both tables share this exact header row; find_table_by_header_and_first_row
# disambiguates them by their first data row's List name (see below).
EDR_TABLE_HEADER = (
    "List",
    "Search radius",
    "On Subject Property",
    "Listings in radius",
    "REC relative to Subject Property",
)

FEDERAL_LIST_FIRST_ROW = "NPL"
STATE_LIST_FIRST_ROW = "SHWS"

# Maps agents/researcher.py's `database_source` frontmatter value (its own
# coarse keyword-based classification — see researcher.py's db_keywords list)
# to the specific ASTM Table 1 list name used as a row in the template's
# Federal/State radius tables. This is a best-effort correspondence, not a
# precise one: the Researcher's classification is coarser than the
# template's list breakdown (e.g. it cannot distinguish RCRA TSD from RCRA
# CORRACTS beyond an explicit "CORRACTS" keyword match). Database sources
# with no clean mapping (CERCLIS, FINDS, TRIS, PADS, ASTK, generic NYSDEC)
# are intentionally left out — their hit notes remain fully visible in
# EDR_Database_Hits/ / Manual_Review/ for the PE, they just aren't rolled
# into one specific list-count cell, since guessing which ASTM list they'd
# belong to would be fabricating data, not reporting it.
DATABASE_TO_LIST: dict[str, str] = {
    "NPL": "NPL",
    "SEMS": "SEMS",
    "CORRACTS": "RCRA CORRACTS",
    "RCRA": "RCRA TSD / generators",
    "ERNS": "ERNS",
    "LUST": "LTANKS",
    "SPILLS": "NYSDEC Spills",
    "STATE_SPILL": "NYSDEC Spills",
    "VCP": "VCP / Brownfields",
    "BROWNFIELD": "VCP / Brownfields",
}
