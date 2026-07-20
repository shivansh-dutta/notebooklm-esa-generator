"""
Agent 3 — Writer
Drafts all 8 Phase 1 ESA report sections using the claude CLI.

Entry point: run_writer(project_path: Path) -> None

Reads:
  - LegalVault/ — 7 required vault files (may be stubs)
  - project_path/EDR_Database_Hits/ — all .md hit notes (excluding _index.md / no_hits.md)
  - project_path/Report_Sections/ — 8 section template copies

Writes:
  - project_path/Report_Sections/<section>.md — overwritten with drafted content
  - project_path/_writer_notes.md — warnings / missing-input log
  - project_path/00_Project_Dashboard.md — report_status updated to PE Review Pending
"""

import logging
from pathlib import Path

from agents.claude_cli import run_claude

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAULT_FILES = [
    "Def_ASTM_E1527-21.md",
    "Law_Federal_AAI_40CFR312.md",
    "Law_Federal_TSCA.md",
    "Law_Federal_NESHAP.md",
    "Law_State_NYSDEC_Part375_SCOs.md",
    "Law_State_NYSDEC_DER-10.md",
    "Law_State_NYSDOH_Vapor.md",
]

# One entry per top-level template section (matches
# Envicon_Phase_I_ESA_Report_TEMPLATE.docx's 12-section ASTM E1527-21
# structure). One `claude` call per entry — stdin-fed (agents/claude_cli.py),
# so growing from 8 to 13 calls does not reintroduce the old Windows
# argv-length problem. The Writer emits subsection-tagged prose (## headings
# matching the template's own heading text); scripts/export_docx.py splits
# on those tags and injects each block after its matching template heading.
SECTIONS = [
    ("ES", "Executive Summary",                                            "Executive_Summary.md"),
    ("1.0", "Introduction",                                                 "01_Introduction.md"),
    ("2.0", "Site Description",                                            "02_Site_Description.md"),
    ("3.0", "User Provided Information",                                   "03_User_Provided_Information.md"),
    ("4.0", "Site Reconnaissance",                                         "04_Site_Reconnaissance.md"),
    ("5.0", "Records Review",                                              "05_Records_Review.md"),
    ("6.0", "Interviews",                                                  "06_Interviews.md"),
    ("7.0", "Non-Scope Considerations and Business Environmental Risks",   "07_Non_Scope_BER.md"),
    ("8.0", "Findings, Opinions, Data Gaps, and Conclusions",              "08_Findings_Opinions_Conclusions.md"),
    ("9.0", "Recommendations",                                             "09_Recommendations.md"),
    ("10.0", "Deviations",                                                 "10_Deviations.md"),
    ("11.0", "Qualifications and Declaration of Environmental Professionals", "11_Qualifications.md"),
    ("12.0", "References",                                                 "12_References.md"),
]

DRAFT_MARKER = "> **DRAFT — PE REVIEW REQUIRED**\n\n"

# Token budget for EDR hits before batching kicks in (Task 6.4)
TOKEN_BUDGET = 80_000
BATCH_SIZE = 10


# ---------------------------------------------------------------------------
# Task 6.2 — Legal vault loader
# ---------------------------------------------------------------------------

def load_legal_vault(vault_dir: Path) -> str:
    """Read all 7 required vault files and return a single concatenated string.

    Each file is preceded by a header line.  If a file is missing, a warning
    is logged but processing continues — the vault may be partially populated.
    """
    parts: list[str] = []
    for filename in VAULT_FILES:
        fpath = vault_dir / filename
        if not fpath.exists():
            logger.warning("Legal vault file missing: %s — skipping", filename)
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            parts.append(f"\n\n=== {filename} ===\n{content}")
        except OSError as exc:
            logger.warning("Could not read vault file %s: %s", filename, exc)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Task 6.3 — EDR hit loader
# ---------------------------------------------------------------------------

def _strip_raw_extract(content: str) -> str:
    """Drop the '## Raw Extract' block and everything after it.

    The raw extract is a verbatim PyMuPDF text dump used only for debugging.
    The Writer only needs the YAML frontmatter and structured fields above it.
    """
    marker = "\n## Raw Extract"
    idx = content.find(marker)
    if idx != -1:
        return content[:idx].rstrip()
    return content


def load_edr_hits(hits_dir: Path) -> list[str]:
    """Return the structured content of every EDR hit note in *hits_dir*.

    Skips _index.md and no_hits.md.  Strips the raw extract block from each
    note so only the YAML frontmatter and structured fields are passed to the
    Writer — the raw dump is a debugging artifact that inflates token count.
    """
    if not hits_dir.exists():
        logger.warning("EDR_Database_Hits directory not found: %s", hits_dir)
        return []

    skip = {"_index.md", "no_hits.md"}
    results: list[str] = []
    for md_file in sorted(hits_dir.glob("*.md")):
        if md_file.name in skip:
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            results.append(_strip_raw_extract(content))
        except OSError as exc:
            logger.warning("Could not read EDR hit file %s: %s", md_file.name, exc)
    return results


def load_historical_records(records_dir: Path) -> list[str]:
    """Read all historical source notes from Historical_Records/.

    Skips _index.md, no_historical_sources.md, and _historian_notes.md.
    Returns an empty list if the directory does not exist or has no notes.
    """
    skip = {"_index.md", "no_historical_sources.md", "_historian_notes.md"}
    if not records_dir.exists():
        logger.warning("Historical_Records directory not found: %s", records_dir)
        return []
    results: list[str] = []
    for md_file in sorted(records_dir.glob("*.md")):
        if md_file.name in skip:
            continue
        try:
            results.append(md_file.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Could not read historical record %s: %s", md_file.name, exc)
    return results


def load_source_doc(source_docs_dir: Path, filename: str) -> str:
    """
    Read a single Source_Documents/ file (e.g. questionnaire.md, foil.md,
    qualifications.md) and return its content, or empty string if missing.
    """
    path = source_docs_dir / filename
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read source doc %s: %s", filename, exc)
        return ""


def load_site_photos(site_photos_dir: Path) -> str:
    """
    Read Site_Photos/photo_observations.md and return its content,
    or empty string if the file does not exist.
    """
    path = site_photos_dir / "photo_observations.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read photo_observations.md: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Task 6.4 — Token budget guard
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token estimate: one token per four characters."""
    return len(text) // 4


def batch_hits(hits: list[str]) -> list[list[str]]:
    """Split hits into batches where each batch stays within TOKEN_BUDGET tokens."""
    total_tokens = sum(estimate_tokens(h) for h in hits)
    if total_tokens <= TOKEN_BUDGET:
        return [hits]

    logger.info(
        "EDR hits total ~%d tokens (budget %d) — splitting into token-bounded batches",
        total_tokens,
        TOKEN_BUDGET,
    )
    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_tokens = 0
    for hit in hits:
        hit_tokens = estimate_tokens(hit)
        if current_batch and current_tokens + hit_tokens > TOKEN_BUDGET:
            batches.append(current_batch)
            current_batch = [hit]
            current_tokens = hit_tokens
        else:
            current_batch.append(hit)
            current_tokens += hit_tokens
    if current_batch:
        batches.append(current_batch)
    return batches


# ---------------------------------------------------------------------------
# Task 6.8 — Stub detection helper
# ---------------------------------------------------------------------------

def _vault_is_all_stubs(vault_content: str) -> bool:
    """Return True when every non-empty line in *vault_content* is either a
    FILL IN comment or blank — meaning the vault has not been populated."""
    for line in vault_content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<!-- FILL IN"):
            return False
    return True


def _log_missing_input(notes_path: Path, section_num: str) -> None:
    """Append a missing-input warning to _writer_notes.md (Task 6.8)."""
    message = (
        f"WARNING: Legal vault is unpopulated. "
        f"Section {section_num} drafted without regulatory citations — DO NOT USE.\n"
    )
    try:
        with notes_path.open("a", encoding="utf-8") as fh:
            fh.write(message)
    except OSError as exc:
        logger.error("Could not write to _writer_notes.md: %s", exc)


def _substitute_missing_input(template_content: str) -> str:
    """Replace every <!-- DRAFT: ... --> comment with a MISSING INPUT marker."""
    import re
    pattern = re.compile(r"<!--\s*DRAFT:.*?-->", re.DOTALL)
    replacement = (
        "<!-- MISSING INPUT: Legal vault not populated — "
        "run after drafter fills vault files -->"
    )
    return pattern.sub(replacement, template_content)


# ---------------------------------------------------------------------------
# Task 6.5 + 6.6 + 6.9 — Per-section drafting
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    return (
        "You are an Environmental Professional drafting a Phase 1 ESA report section. "
        "You must cite specific legal definitions from the provided vault files. "
        "Do not use knowledge outside the provided vault files for legal citations."
    )


def _build_user_prompt(
    section_num: str,
    section_name: str,
    template_content: str,
    vault_context: str,
    hits_content: str,
    project_meta: dict,
    historical_records_block: str = "",
    questionnaire_block: str = "",
    site_photos_block: str = "",
    foil_block: str = "",
    qualifications_block: str = "",
) -> str:
    """Construct the full user-turn prompt for a single section draft."""

    meta_block = "\n".join(f"  {k}: {v}" for k, v in project_meta.items())

    # Section 5.0 (Records Review) gets extra classification and NYSDEC Spill
    # Program instructions (carried over from the former Section 05).
    section_05_instructions = ""
    if section_num == "5.0":
        section_05_instructions = """
CLASSIFICATION REQUIREMENT — SECTION 5.0 ONLY:
For EVERY EDR hit discussed under "Federal records" / "State, tribal, and local records",
you MUST classify it as one of:
  REC (Recognized Environmental Condition)
  CREC (Controlled Recognized Environmental Condition)
  HREC (Historical Recognized Environmental Condition)
  De Minimis Condition

Each classification MUST include an explicit citation to the applicable ASTM E1527-21
section number (e.g., "§3.2.81", "§3.2.17", "§3.2.50", "§3.2.24").

NYSDEC SPILL PROGRAM CROSS-REFERENCE:
For any EDR hit identified as a NYSDEC Spill Program record with status
"Closed - NFAR" or "Closed - No Further Action Required", reference the
DER-10 vault file's closure criteria when determining if HREC or de minimis
classification is appropriate rather than defaulting to REC.

HISTORICAL RECORDS — SECTION 5.0 ONLY:
Use the historical records above to populate the 5.2.x subsections (5.2.1 Aerial
Photographs, 5.2.2 Sanborn Fire Insurance Maps, 5.2.4 USGS Topographic Maps): replace
<!-- DRAFT: ... --> placeholders with actual year-by-year observations drawn from the
historical notes. If no records exist for a particular subsection type, write:
<!-- MISSING INPUT: No [source type] records found — run Historian agent or provide records manually -->
Preserve all <!-- FILL IN MANUALLY: ... --> comments untouched.
"""

    # --- Route source documents to the section that needs them ---
    # §3.0 User-Provided Information ← questionnaire
    # §4.0 Site Reconnaissance       ← site-photo observations
    # §5.0 Records Review            ← EDR hits + historical records + FOIL
    #                                   (5.4 Regulatory Agency File Review)
    # §11.0 Qualifications            ← EP qualifications
    source_doc_block = ""
    if section_num == "3.0" and questionnaire_block.strip():
        source_doc_block = (
            "## Environmental Questionnaire (User-Provided Information)\n"
            + questionnaire_block
        )
    elif section_num == "4.0" and site_photos_block.strip():
        source_doc_block = (
            "## Site Photograph Observations (Historian Agent Output)\n"
            + site_photos_block
        )
    elif section_num == "5.0" and foil_block.strip():
        source_doc_block = (
            "## FOIL Requests & Responses\n"
            + foil_block
        )
    elif section_num == "11.0" and qualifications_block.strip():
        source_doc_block = (
            "## Qualifications of Environmental Professionals\n"
            + qualifications_block
        )

    source_doc_section = ""
    if source_doc_block:
        source_doc_section = f"\n{source_doc_block}\n"
    elif section_num in ("3.0", "4.0", "11.0"):
        # Signal that the source doc is absent so the Writer emits a marker
        label = {
            "3.0": "Environmental Questionnaire",
            "4.0": "Site Photograph Observations",
            "11.0": "EP Qualifications",
        }[section_num]
        source_doc_section = (
            f"\n## {label}\n"
            f"<!-- MISSING INPUT: {label} not found in Source_Documents/ — "
            "run the Historian/Extractor phase or provide the source document manually -->\n"
        )

    prompt = f"""## Project Metadata
{meta_block}

## Legal Vault Context
The following files from the legal vault provide the authoritative regulatory
framework for this report.  ONLY cite legal definitions drawn from these files.

{vault_context}

## EDR Database Hit Notes
{hits_content if hits_content.strip() else "(No EDR hits provided for this section.)"}

## Historical Records (Historian Agent Output)
{historical_records_block if historical_records_block.strip() else "(No historical records available — Historian agent was not run or found no image pages.)"}{source_doc_section}
## Section Template
Below is the template for Section {section_num} — {section_name}.
Replace every `<!-- DRAFT: ... -->` comment with fully drafted professional
prose appropriate for a Phase 1 ESA report.  Do NOT remove or alter
`<!-- FILL IN MANUALLY: ... -->` comments — those are for the PE to complete.
Output only the completed section markdown, with no preamble or explanation.
{section_05_instructions}
--- BEGIN TEMPLATE ---
{template_content}
--- END TEMPLATE ---
"""
    return prompt


def draft_section(
    section_num: str,
    section_name: str,
    template_content: str,
    vault_context: str,
    hits_content: str,
    project_meta: dict,
    historical_records_block: str = "",
    questionnaire_block: str = "",
    site_photos_block: str = "",
    foil_block: str = "",
    qualifications_block: str = "",
) -> str:
    """Call claude CLI and return the drafted section text (Task 6.5/6.6)."""
    full_prompt = (
        f"{_build_system_prompt()}\n\n"
        + _build_user_prompt(
            section_num,
            section_name,
            template_content,
            vault_context,
            hits_content,
            project_meta,
            historical_records_block,
            questionnaire_block,
            site_photos_block,
            foil_block,
            qualifications_block,
        )
    )

    result = run_claude(full_prompt, stream_json=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Writer: claude CLI failed for section {section_num} "
            f"(exit {result.returncode})\n"
            f"stderr: {result.stderr}\n"
            f"stdout: {result.stdout[:2000]}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Task 6.7 — DRAFT marker injection + file write
# ---------------------------------------------------------------------------

def write_section(output_path: Path, content: str) -> None:
    """Prepend the DRAFT marker and write the section file (Task 6.7)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    full_content = DRAFT_MARKER + content
    output_path.write_text(full_content, encoding="utf-8")
    logger.info("Wrote: %s", output_path)


# ---------------------------------------------------------------------------
# Task 6.10 — Dashboard status update
# ---------------------------------------------------------------------------

def update_dashboard_status(dashboard_path: Path) -> None:
    """Replace 'report_status: In Progress' with 'report_status: PE Review Pending'
    in the YAML frontmatter of 00_Project_Dashboard.md (Task 6.10).

    Uses plain string replacement — no regex — as specified.
    """
    if not dashboard_path.exists():
        logger.warning("Dashboard not found at %s — skipping status update", dashboard_path)
        return

    original = dashboard_path.read_text(encoding="utf-8")
    updated = original.replace(
        "report_status: In Progress",
        "report_status: PE Review Pending",
    )
    if updated == original:
        logger.info(
            "Dashboard status was not 'In Progress' — no change made to %s",
            dashboard_path,
        )
    else:
        dashboard_path.write_text(updated, encoding="utf-8")
        logger.info("Dashboard status updated to 'PE Review Pending': %s", dashboard_path)


# ---------------------------------------------------------------------------
# Task 6.1 — Main entry point
# ---------------------------------------------------------------------------

def run_writer(project_path: Path) -> None:
    """Draft all 8 Phase 1 ESA report sections and update the dashboard.

    Parameters
    ----------
    project_path:
        Root of the project folder (e.g. Projects/52-96-Falls-St).
    """
    project_path = Path(project_path)

    # Resolve key directories
    repo_root = Path(__file__).parent.parent
    vault_dir = repo_root / "LegalVault"
    hits_dir = project_path / "EDR_Database_Hits"
    sections_dir = project_path / "Report_Sections"
    notes_path = project_path / "_writer_notes.md"
    dashboard_path = project_path / "00_Project_Dashboard.md"

    # Build minimal project metadata for prompt context
    project_meta = {
        "project_name": project_path.name,
        "project_path": str(project_path),
    }

    # -----------------------------------------------------------------------
    # Task 6.2 — Load legal vault
    # -----------------------------------------------------------------------
    logger.info("Loading legal vault from %s", vault_dir)
    vault_context = load_legal_vault(vault_dir)

    # -----------------------------------------------------------------------
    # Task 6.8 — Check whether vault is all stubs
    # -----------------------------------------------------------------------
    vault_is_stubs = _vault_is_all_stubs(vault_context)
    if vault_is_stubs:
        logger.warning(
            "Legal vault appears to be fully unpopulated (all FILL IN stubs). "
            "Sections will be written with MISSING INPUT markers — DO NOT USE for reports."
        )

    # -----------------------------------------------------------------------
    # Task 6.3 — Load EDR hits
    # -----------------------------------------------------------------------
    logger.info("Loading EDR hits from %s", hits_dir)
    all_hits = load_edr_hits(hits_dir)
    logger.info("Found %d EDR hit file(s)", len(all_hits))

    # Load historical records (Historian agent output)
    records_dir = project_path / "Historical_Records"
    historical_records = load_historical_records(records_dir)
    logger.info("Found %d historical record file(s)", len(historical_records))
    if not historical_records:
        try:
            with notes_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "WARNING: No historical records loaded. "
                    "Section 5.2 DRAFT placeholders will remain unfilled. "
                    "Run the Historian agent first (pipeline.py --phase historian).\n"
                )
        except OSError as exc:
            logger.warning("Could not write to _writer_notes.md: %s", exc)

    # Load Source_Documents/ (Extractor + Historian outputs)
    source_docs_dir = project_path / "Source_Documents"
    questionnaire_content = load_source_doc(source_docs_dir, "questionnaire.md")
    foil_content = load_source_doc(source_docs_dir, "foil.md")
    qualifications_content = load_source_doc(source_docs_dir, "qualifications.md")
    site_photos_content = load_site_photos(project_path / "Site_Photos")
    logger.info(
        "Source docs loaded — questionnaire: %s, foil: %s, qualifications: %s, "
        "site_photos: %s",
        bool(questionnaire_content),
        bool(foil_content),
        bool(qualifications_content),
        bool(site_photos_content),
    )

    # -----------------------------------------------------------------------
    # Task 6.4 — Token budget guard: split into batches if needed
    # -----------------------------------------------------------------------
    hit_batches = batch_hits(all_hits)

    # -----------------------------------------------------------------------
    # Draft each section
    # -----------------------------------------------------------------------
    for section_num, section_name, filename in SECTIONS:
        section_file = sections_dir / filename
        if not section_file.exists():
            logger.warning(
                "Template not found for section %s (%s) — skipping",
                section_num,
                filename,
            )
            continue

        template_content = section_file.read_text(encoding="utf-8")
        logger.info("Drafting Section %s — %s", section_num, section_name)

        # Task 6.8 — stub vault path: write MISSING INPUT markers, no CLI call
        if vault_is_stubs:
            _log_missing_input(notes_path, section_num)
            stubbed_content = _substitute_missing_input(template_content)
            write_section(section_file, stubbed_content)
            continue

        # Normal path — call the CLI, batching EDR hits for Section 5.0 if needed
        if section_num == "5.0" and len(hit_batches) > 1:
            # Process each batch and concatenate the drafted narratives
            combined_parts: list[str] = []
            hist_block = "\n\n---\n\n".join(historical_records) if historical_records else ""
            for batch_idx, batch in enumerate(hit_batches, start=1):
                logger.info(
                    "  Section 5.0 — processing hit batch %d / %d",
                    batch_idx,
                    len(hit_batches),
                )
                hits_content = "\n\n---\n\n".join(batch)
                part = draft_section(
                    section_num,
                    section_name,
                    template_content,
                    vault_context,
                    hits_content,
                    project_meta,
                    historical_records_block=hist_block,
                    questionnaire_block=questionnaire_content,
                    site_photos_block=site_photos_content,
                    foil_block=foil_content,
                    qualifications_block=qualifications_content,
                )
                combined_parts.append(part)
            drafted = "\n\n".join(combined_parts)
        else:
            # Single call for all other sections (or Section 05 within budget)
            hits_content = (
                "\n\n---\n\n".join(hit_batches[0]) if hit_batches else ""
            )
            # Only include EDR hits in Section 5.0 draft; other sections get
            # an empty hits block so the prompt stays focused.
            if section_num != "5.0":
                hits_content = ""
            hist_block = (
                "\n\n---\n\n".join(historical_records)
                if section_num == "5.0" and historical_records
                else ""
            )
            drafted = draft_section(
                section_num,
                section_name,
                template_content,
                vault_context,
                hits_content,
                project_meta,
                historical_records_block=hist_block,
                questionnaire_block=questionnaire_content,
                site_photos_block=site_photos_content,
                foil_block=foil_content,
                qualifications_block=qualifications_content,
            )

        write_section(section_file, drafted)

    # -----------------------------------------------------------------------
    # Task 6.10 — Update dashboard status
    # -----------------------------------------------------------------------
    update_dashboard_status(dashboard_path)

    logger.info("Writer agent complete. All sections written to %s", sections_dir)
