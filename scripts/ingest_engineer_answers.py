"""
scripts/ingest_engineer_answers.py — take the JSON an engineer downloaded
from the Engineer_Fill_Form.html (see scripts/engineer_form.py) and fold
their answers back into the project, then re-export the DOCX.

Governing rule (same as the rest of this pipeline): a gap only ever gets
filled with text a human actually typed, and only on an exact, unambiguous
match — never a guess, never a fuzzy replace, never silent overwrite of real
report content:

  - section_marker  -> the answer replaces the EXACT marker substring
                        (item["match"]) in Report_Sections/<file>. If that
                        substring doesn't appear in the file exactly once
                        (already edited, file missing, marker text changed),
                        it's skipped and reported — never guessed at.
  - dashboard_field  -> the answer overwrites that key's value in
                        00_Project_Dashboard.md's frontmatter.
  - decision         -> the engineer's note is RECORDED (never substituted
                        into report prose) via
                        notebooklm_pipeline.review_pass.append_findings,
                        under its own heading — the PE still makes the final
                        call, this just carries the engineer's input to them.

After applying everything, scripts.export_docx.run_export_docx() rebuilds
the DOCX so the filled-in answers actually show up in the deliverable.

Public interface:
    IngestReport
    apply_answers(project_path, answers_path) -> IngestReport
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from notebooklm_pipeline.review_pass import append_findings
from scripts.export_docx import run_export_docx

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    filled: int = 0
    dashboard_updated: int = 0
    decisions_recorded: int = 0
    skipped_empty: int = 0
    skipped_unmatched: list[str] = field(default_factory=list)
    exported_docx: Path | None = None

    def summary_lines(self) -> list[str]:
        lines = [
            f"Filled {self.filled} section marker(s)",
            f"Updated {self.dashboard_updated} dashboard field(s)",
            f"Recorded {self.decisions_recorded} decision(s) for the PE",
            f"Skipped {self.skipped_empty} blank answer(s)",
        ]
        if self.skipped_unmatched:
            lines.append(f"Could not apply {len(self.skipped_unmatched)} item(s):")
            lines += [f"  - {item}" for item in self.skipped_unmatched]
        if self.exported_docx:
            lines.append(f"Re-exported: {self.exported_docx}")
        return lines


def _update_dashboard_field(project_path: Path, field_name: str, value: str) -> bool:
    """Overwrite `<field_name>: "..."` in 00_Project_Dashboard.md's
    frontmatter. Returns False (no-op) if the file or the key doesn't exist
    — never adds a field that wasn't already part of the dashboard schema."""
    dash_path = Path(project_path) / "00_Project_Dashboard.md"
    if not dash_path.exists():
        return False
    text = dash_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(field_name)}:\s*.*$", re.MULTILINE)
    if not pattern.search(text):
        return False
    escaped_value = value.replace('"', '\\"')
    # A replacement function (not a plain string) sidesteps re.sub treating
    # backslashes/group-refs in the value as regex replacement syntax.
    new_text = pattern.sub(lambda _m: f'{field_name}: "{escaped_value}"', text, count=1)
    dash_path.write_text(new_text, encoding="utf-8")
    return True


def apply_answers(project_path: Path, answers_path: Path) -> IngestReport:
    project_path = Path(project_path).resolve()
    answers_path = Path(answers_path).resolve()

    items = json.loads(answers_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"{answers_path} does not contain a JSON array of answers")

    report = IngestReport()
    sections_dir = project_path / "Report_Sections"
    decisions: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        answer = (item.get("answer") or "").strip()
        if not answer:
            report.skipped_empty += 1
            continue

        kind = item.get("kind")

        if kind == "section_marker":
            filename = item.get("file")
            match = item.get("match")
            if not filename or not match:
                report.skipped_unmatched.append(f"section_marker item missing file/match: {item.get('id')}")
                continue
            section_path = sections_dir / filename
            if not section_path.exists():
                report.skipped_unmatched.append(f"{filename}: file not found")
                continue
            text = section_path.read_text(encoding="utf-8")
            count = text.count(match)
            if count != 1:
                report.skipped_unmatched.append(
                    f"{filename}: marker text not found exactly once (found {count}x) — {match[:60]!r}"
                )
                continue
            section_path.write_text(text.replace(match, answer, 1), encoding="utf-8")
            report.filled += 1

        elif kind == "dashboard_field":
            field_name = item.get("field_name")
            if not field_name:
                report.skipped_unmatched.append(f"dashboard_field item missing field_name: {item.get('id')}")
                continue
            if _update_dashboard_field(project_path, field_name, answer):
                report.dashboard_updated += 1
            else:
                report.skipped_unmatched.append(f"dashboard field '{field_name}' not found in 00_Project_Dashboard.md")

        elif kind == "decision":
            section = item.get("section", "")
            prompt = item.get("prompt", "")
            decisions.append(f"{section}: {prompt} — Engineer's note: {answer}")
            report.decisions_recorded += 1

        else:
            report.skipped_unmatched.append(f"unknown gap kind {kind!r} (id={item.get('id')})")

    if decisions:
        append_findings(project_path, decisions, heading="## Engineer resolutions")

    report.exported_docx = run_export_docx(project_path)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Fold an engineer's filled-in answers back into the project and re-export the DOCX.",
    )
    parser.add_argument("--project-dir", required=True, help="Path to the project folder")
    parser.add_argument("--answers", required=True, help="Path to the engineer's downloaded answers JSON")
    args = parser.parse_args()

    report = apply_answers(Path(args.project_dir), Path(args.answers))
    for line in report.summary_lines():
        print(line)


if __name__ == "__main__":
    main()
