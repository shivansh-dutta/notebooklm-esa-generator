"""
notebooklm_pipeline/site_visit_guidance.py — write Site_Visit_Guidance.md.

This is the forward-looking feature the user asked to bake in now: the raw
package won't always include site photographs (a future real package may
omit them entirely), so instead of only reporting what WAS found, the tool
tells the field engineer what to specifically go verify and photograph on
the next site visit, tied to the EDR listings and historical/map findings
that already were grounded.

The actual checklist content is produced by NotebookLM itself
(question_bank.SITE_VISIT_SYNTHESIS_QUESTION, run in qa_runner.py) — this
module is pure formatting, no additional Claude or NotebookLM call, to keep
token/query cost at the floor per the user's instruction.

Public interface:
    write_site_visit_guidance(project_path, results) -> Path
"""

from __future__ import annotations

from pathlib import Path


def write_site_visit_guidance(project_path: Path, results) -> Path:
    """
    Write <project>/Site_Visit_Guidance.md from a qa_runner.QaResults —
    the site-visit synthesis answer as the main checklist, plus the raw
    site-photo and map summaries as supporting reference underneath.
    """
    project_path = Path(project_path)

    parts = [
        "# Site Visit Guidance",
        "",
        "_Auto-generated from NotebookLM's grounded review of the EDR radius "
        "report, historical maps, and any site photographs. Use this as a "
        "field checklist during the site visit — confirm or deny each item "
        "and note the result for the report's Section 4.0 (Site "
        "Reconnaissance) and Section 5.0 (Records Review)._",
        "",
        "## Checklist",
        "",
        results.site_visit_notes.strip() or "(No checklist generated.)",
        "",
        "## Reference — Historical Maps / Aerials Summary",
        "",
        results.maps.strip() or "(Not available.)",
        "",
        "## Reference — Site Photographs Already On File",
        "",
        results.site_photos.strip() or "(No site photographs were provided in the source package.)",
        "",
    ]

    path = project_path / "Site_Visit_Guidance.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
