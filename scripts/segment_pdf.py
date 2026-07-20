"""
scripts/segment_pdf.py — Phase 1 ESA Report Generator
Segments an all-in-one Phase 1 PDF package into labeled appendices
by detecting divider pages of the form:
    PRIVILEGED AND CONFIDENTIAL / APPENDIX N / <Title>

Entry points:
    segment_appendices(pdf_path: Path) -> list[dict]
    find_component(appendix_map: list[dict], component: str) -> dict | None
    find_components(appendix_map: list[dict], *components: str) -> list[dict]
    write_appendix_map(project_path: Path, appendix_map: list[dict]) -> Path

Can also be run standalone:
    python scripts/segment_pdf.py --project Projects/631-Northland-Ave
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Component normalisation — maps title keywords to canonical component names
# ---------------------------------------------------------------------------

_COMPONENT_PATTERNS = [
    # More specific / longer patterns first to avoid false matches.
    # "EDR Radius Map Report" contains "MAP" — check EDR/RADIUS before bare MAP.
    ("edr_radius_report",            r"EDR|RADIUS"),
    ("site_photographs",             r"SITE\s+PHOTOGRAPH|PHOTO"),
    ("environmental_questionnaire",  r"QUESTIONNAIRE"),
    ("historic_research",            r"HISTORIC"),
    ("foil",                         r"FOIL|FREEDOM\s+OF\s+INFORMATION"),
    ("qualifications",               r"QUALIFICATION"),
    ("maps",                         r"MAP"),  # intentionally last — catches "Maps" appendix
]

# Pages with more text than this are content pages, not dividers
_DIVIDER_MAX_CHARS = 200


def _normalise_component(title: str) -> str:
    """Map a raw appendix title to a canonical component name."""
    up = title.upper()
    for component, pattern in _COMPONENT_PATTERNS:
        if re.search(pattern, up):
            return component
    return "unknown"


# ---------------------------------------------------------------------------
# Core segmentation
# ---------------------------------------------------------------------------

def segment_appendices(pdf_path: Path) -> list[dict]:
    """
    Scan the PDF for appendix divider pages and return an ordered list of
    appendix dicts::

        {
            "appendix_num": int,
            "title":        str,
            "component":    str,   # canonical name (see _COMPONENT_PATTERNS)
            "start_page":   int,   # 1-indexed, inclusive
            "end_page":     int,   # 1-indexed, inclusive
        }

    Divider pages match the text pattern ``APPENDIX N`` and have fewer than
    _DIVIDER_MAX_CHARS characters of extracted text.

    Fallback: if no dividers are found (package lacks this structure), returns
    a single entry covering the whole PDF with component "whole_pdf".  This
    allows all downstream agents to degrade gracefully to whole-PDF behavior
    — no regression for non-appendix packages.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    n_pages = doc.page_count

    dividers: list[tuple[int, int, str]] = []  # (1-indexed page, appendix_num, raw title)
    for n in range(n_pages):
        text = doc[n].get_text()
        stripped = text.strip()
        if len(stripped) > _DIVIDER_MAX_CHARS:
            continue
        m = re.search(r"APPENDIX\s+(\d+)", stripped.upper())
        if m:
            appendix_num = int(m.group(1))
            # Build a clean title from the non-empty lines on this divider page
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = " / ".join(lines)
            dividers.append((n + 1, appendix_num, title))

    doc.close()

    if not dividers:
        return [{
            "appendix_num": 0,
            "title": "Whole PDF (no appendix structure detected)",
            "component": "whole_pdf",
            "start_page": 1,
            "end_page": n_pages,
        }]

    # Ensure chronological order (they should already be, but be defensive)
    dividers.sort(key=lambda x: x[0])

    result: list[dict] = []
    for i, (start_pg, apx_num, title) in enumerate(dividers):
        end_pg = dividers[i + 1][0] - 1 if i + 1 < len(dividers) else n_pages
        result.append({
            "appendix_num": apx_num,
            "title": title,
            "component": _normalise_component(title),
            "start_page": start_pg,
            "end_page": end_pg,
        })

    return result


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def find_component(appendix_map: list[dict], component: str) -> dict | None:
    """Return the first appendix entry whose component matches, or None."""
    for entry in appendix_map:
        if entry["component"] == component:
            return entry
    return None


def find_components(appendix_map: list[dict], *components: str) -> list[dict]:
    """Return all appendix entries matching any of the given component names."""
    return [e for e in appendix_map if e["component"] in components]


# ---------------------------------------------------------------------------
# _appendix_map.md writer
# ---------------------------------------------------------------------------

def write_appendix_map(project_path: Path, appendix_map: list[dict]) -> Path:
    """
    Write a human-readable _appendix_map.md to Source_Documents/ and return
    its path.  The Source_Documents/ folder is created if it does not exist.
    """
    out_dir = project_path / "Source_Documents"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "_appendix_map.md"

    lines = [
        "# Appendix Map",
        "",
        "Auto-generated by `scripts/segment_pdf.py` — do not edit manually.",
        "",
        "| Appendix | Component | Pages | Page Count | Title |",
        "|---|---|---|---|---|",
    ]
    for entry in appendix_map:
        n = entry["end_page"] - entry["start_page"] + 1
        pg_range = f"p{entry['start_page']}–{entry['end_page']}"
        lines.append(
            f"| APX{entry['appendix_num']} "
            f"| {entry['component']} "
            f"| {pg_range} "
            f"| {n} "
            f"| {entry['title']} |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Segment a Phase 1 PDF package into its appendices."
    )
    parser.add_argument(
        "--project", required=True, metavar="PATH",
        help="Path to the project folder (Raw/ must contain exactly one PDF)",
    )
    args = parser.parse_args()

    project_path = Path(args.project)
    if not project_path.is_absolute():
        project_path = Path(__file__).resolve().parent.parent / project_path

    raw_dir = project_path / "Raw"
    pdfs = sorted(raw_dir.glob("*.pdf")) if raw_dir.exists() else []
    if not pdfs:
        print(f"ERROR: No PDF found in {raw_dir}", file=sys.stderr)
        sys.exit(1)
    if len(pdfs) > 1:
        print(f"WARNING: Multiple PDFs — using first: {pdfs[0].name}")
    pdf_path = pdfs[0]

    doc = fitz.open(str(pdf_path))
    total = doc.page_count
    doc.close()
    print(f"Segmenting: {pdf_path.name}  ({total} pages)")

    appendix_map = segment_appendices(pdf_path)
    map_path = write_appendix_map(project_path, appendix_map)
    print(f"Written: {map_path}")
    print()
    for entry in appendix_map:
        n = entry["end_page"] - entry["start_page"] + 1
        print(
            f"  APX{entry['appendix_num']:2d}  "
            f"p{entry['start_page']:4d}–{entry['end_page']:<4d}  "
            f"({n:4d} pg)  [{entry['component']}]  "
            f"{entry['title'][:65]}"
        )


if __name__ == "__main__":
    main()
