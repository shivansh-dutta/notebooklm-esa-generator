"""Tests for scripts/ingest_engineer_answers.py — folding an engineer's
filled-in answers JSON back into Report_Sections/*.md, 00_Project_Dashboard.md,
and Questions_For_User.md, then triggering a DOCX re-export.

scripts.export_docx.run_export_docx is mocked out in every test here — it
needs the real template DOCX and a fully-formed project, which is exactly
what scripts/test_export_docx.py's own docstring says to avoid for a fast,
standalone unit suite. We only assert it gets *called* (once, with the
project path), not what it produces.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.ingest_engineer_answers import apply_answers


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _answers_file(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


class TestApplyAnswersSectionMarker:
    def test_exact_single_match_is_replaced(self, tmp_path: Path):
        _write(
            tmp_path / "Report_Sections" / "05_Records_Review.md",
            "Some text.\n\n» PE TO COMPLETE: lot size\n\nMore text.\n",
        )
        answers = _answers_file(tmp_path, [
            {"id": "sec-001-aaaa", "kind": "section_marker", "file": "05_Records_Review.md",
             "match": "» PE TO COMPLETE: lot size", "answer": "0.42 acres"},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = tmp_path / "Export" / "out.docx"
            report = apply_answers(tmp_path, answers)

        text = (tmp_path / "Report_Sections" / "05_Records_Review.md").read_text(encoding="utf-8")
        assert "0.42 acres" in text
        assert "» PE TO COMPLETE: lot size" not in text
        assert report.filled == 1
        assert report.skipped_unmatched == []
        mock_export.assert_called_once()

    def test_ambiguous_match_count_is_skipped_not_guessed(self, tmp_path: Path):
        # The same marker text appears twice — replacing either would be a
        # guess about which one the engineer meant, so neither is touched.
        _write(
            tmp_path / "Report_Sections" / "01_Introduction.md",
            "» PE TO COMPLETE: repeat\n\n» PE TO COMPLETE: repeat\n",
        )
        answers = _answers_file(tmp_path, [
            {"id": "sec-001-aaaa", "kind": "section_marker", "file": "01_Introduction.md",
             "match": "» PE TO COMPLETE: repeat", "answer": "some answer"},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)

        text = (tmp_path / "Report_Sections" / "01_Introduction.md").read_text(encoding="utf-8")
        assert text.count("» PE TO COMPLETE: repeat") == 2  # untouched
        assert report.filled == 0
        assert len(report.skipped_unmatched) == 1
        assert "found 2x" in report.skipped_unmatched[0]

    def test_missing_file_is_skipped(self, tmp_path: Path):
        (tmp_path / "Report_Sections").mkdir()
        answers = _answers_file(tmp_path, [
            {"id": "sec-001-aaaa", "kind": "section_marker", "file": "nope.md",
             "match": "» PE TO COMPLETE: x", "answer": "y"},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)
        assert report.filled == 0
        assert "file not found" in report.skipped_unmatched[0]

    def test_blank_answer_is_skipped_and_counted(self, tmp_path: Path):
        _write(tmp_path / "Report_Sections" / "01_Introduction.md", "» PE TO COMPLETE: x\n")
        answers = _answers_file(tmp_path, [
            {"id": "sec-001-aaaa", "kind": "section_marker", "file": "01_Introduction.md",
             "match": "» PE TO COMPLETE: x", "answer": "   "},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)
        assert report.filled == 0
        assert report.skipped_empty == 1
        text = (tmp_path / "Report_Sections" / "01_Introduction.md").read_text(encoding="utf-8")
        assert "» PE TO COMPLETE: x" in text  # untouched


class TestApplyAnswersDashboardField:
    def test_updates_existing_field(self, tmp_path: Path):
        _write(
            tmp_path / "00_Project_Dashboard.md",
            '---\nproject_name: "Test"\nclient_name: "» PE TO COMPLETE"\n---\n\n# Dashboard\n',
        )
        answers = _answers_file(tmp_path, [
            {"id": "dash-001-bbbb", "kind": "dashboard_field", "field_name": "client_name",
             "answer": "Acme Realty LLC"},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)

        text = (tmp_path / "00_Project_Dashboard.md").read_text(encoding="utf-8")
        assert 'client_name: "Acme Realty LLC"' in text
        assert report.dashboard_updated == 1

    def test_unknown_field_is_skipped(self, tmp_path: Path):
        _write(tmp_path / "00_Project_Dashboard.md", '---\nproject_name: "Test"\n---\n\n# D\n')
        answers = _answers_file(tmp_path, [
            {"id": "dash-001-bbbb", "kind": "dashboard_field", "field_name": "not_a_real_field",
             "answer": "value"},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)
        assert report.dashboard_updated == 0
        assert "not_a_real_field" in report.skipped_unmatched[0]

    def test_value_with_double_quotes_is_escaped_not_a_regex_replacement_bug(self, tmp_path: Path):
        # A value containing a backslash would corrupt re.sub's replacement
        # string if passed directly rather than via a replacement function.
        _write(tmp_path / "00_Project_Dashboard.md", '---\nclient_name: "TBD"\n---\n\n# D\n')
        answers = _answers_file(tmp_path, [
            {"id": "dash-001-bbbb", "kind": "dashboard_field", "field_name": "client_name",
             "answer": 'Say "hi" \\1 backslash'},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            apply_answers(tmp_path, answers)
        text = (tmp_path / "00_Project_Dashboard.md").read_text(encoding="utf-8")
        assert 'Say \\"hi\\" \\1 backslash' in text


class TestApplyAnswersDecision:
    def test_records_but_does_not_rewrite_prose(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n## Automated consistency review\n\n- CREC/HREC contradiction: X vs Y.\n",
        )
        answers = _answers_file(tmp_path, [
            {"id": "dec-001-cccc", "kind": "decision", "section": "Automated consistency review",
             "prompt": "CREC/HREC contradiction: X vs Y.", "answer": "Confirmed HREC per site visit."},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)

        text = (tmp_path / "Questions_For_User.md").read_text(encoding="utf-8")
        assert "## Engineer resolutions" in text
        assert "Confirmed HREC per site visit." in text
        assert report.decisions_recorded == 1


class TestApplyAnswersReExport:
    def test_export_docx_called_even_with_no_fillable_items(self, tmp_path: Path):
        answers = _answers_file(tmp_path, [])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = tmp_path / "Export" / "out.docx"
            report = apply_answers(tmp_path, answers)
        mock_export.assert_called_once()
        assert report.exported_docx == tmp_path / "Export" / "out.docx"

    def test_unknown_kind_is_skipped_and_reported(self, tmp_path: Path):
        answers = _answers_file(tmp_path, [
            {"id": "x-1", "kind": "something_else", "answer": "y"},
        ])
        with patch("scripts.ingest_engineer_answers.run_export_docx") as mock_export:
            mock_export.return_value = None
            report = apply_answers(tmp_path, answers)
        assert any("unknown gap kind" in item for item in report.skipped_unmatched)
