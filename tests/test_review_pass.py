"""
Unit tests for notebooklm_pipeline/review_pass.py — the whole-report sonnet
review pass. The Claude CLI call is mocked throughout (same pattern as
tests/test_notebooklm_orchestrator.py) so nothing here shells out for real.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from notebooklm_pipeline.review_pass import (
    _try_parse_json_object,
    apply_deletions,
    append_findings,
    build_review_prompt,
    run_review_pass,
)


class TestTryParseJsonObject:
    def test_direct_json(self):
        assert _try_parse_json_object('{"deletions": [], "findings": []}') == {
            "deletions": [], "findings": []
        }

    def test_fenced_json(self):
        text = '```json\n{"deletions": ["x"], "findings": []}\n```'
        assert _try_parse_json_object(text) == {"deletions": ["x"], "findings": []}

    def test_embedded_in_prose(self):
        text = 'Here is the result:\n{"deletions": [], "findings": ["a contradiction"]}\nThanks.'
        assert _try_parse_json_object(text) == {"deletions": [], "findings": ["a contradiction"]}

    def test_invalid_json_returns_none(self):
        assert _try_parse_json_object("not json at all") is None

    def test_json_array_not_object_returns_none(self):
        assert _try_parse_json_object("[1, 2, 3]") is None


class TestBuildReviewPrompt:
    def test_includes_official_identity(self):
        prompt = build_review_prompt(
            {"11_Qualifications.md": "content"},
            {"ep_firm": "Envicon Engineering", "assessor_name": "Shivansh Dutta", "reviewer_name": "Jason Dutta"},
        )
        assert "Envicon Engineering" in prompt
        assert "Shivansh Dutta" in prompt
        assert "Jason Dutta" in prompt

    def test_includes_every_section_file(self):
        sections = {"01_Introduction.md": "Intro text.", "05_Records_Review.md": "Records text."}
        prompt = build_review_prompt(sections, {})
        assert "01_Introduction.md" in prompt
        assert "Intro text." in prompt
        assert "05_Records_Review.md" in prompt
        assert "Records text." in prompt

    def test_missing_dashboard_fields_show_not_stated(self):
        prompt = build_review_prompt({}, {})
        assert "(not stated)" in prompt


class TestApplyDeletions:
    def test_removes_exact_match_and_rewrites_file(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        (project / "Report_Sections" / "11_Qualifications.md").write_text(
            "Prepared by Ravi Engineering & Land Surveying, P.C. Also prepared by Envicon.",
            encoding="utf-8",
        )
        sections = {"11_Qualifications.md": "Prepared by Ravi Engineering & Land Surveying, P.C. Also prepared by Envicon."}

        applied = apply_deletions(project, sections, ["Prepared by Ravi Engineering & Land Surveying, P.C. "])

        assert applied == 1
        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Ravi Engineering" not in content
        assert "Also prepared by Envicon." in content

    def test_non_matching_deletion_is_silently_skipped(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        (project / "Report_Sections" / "01_Introduction.md").write_text("Real content.", encoding="utf-8")
        sections = {"01_Introduction.md": "Real content."}

        applied = apply_deletions(project, sections, ["Text that never appears anywhere"])

        assert applied == 0
        assert (project / "Report_Sections" / "01_Introduction.md").read_text(encoding="utf-8") == "Real content."

    def test_unchanged_files_are_not_rewritten(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        path = project / "Report_Sections" / "01_Introduction.md"
        path.write_text("Untouched.", encoding="utf-8")
        mtime_before = path.stat().st_mtime_ns

        apply_deletions(project, {"01_Introduction.md": "Untouched."}, [])

        assert path.stat().st_mtime_ns == mtime_before

    def test_non_string_deletion_entries_are_ignored(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        (project / "Report_Sections" / "01_Introduction.md").write_text("Text.", encoding="utf-8")
        applied = apply_deletions(project, {"01_Introduction.md": "Text."}, [123, None, ""])
        assert applied == 0


class TestAppendFindings:
    def test_appends_to_existing_questions_file(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        (project / "Questions_For_User.md").write_text(
            "# Questions For User\n\n- Dashboard field 'city' not found.\n", encoding="utf-8"
        )
        append_findings(project, ["Section 3.8 says questionnaire completed; Section 8.3 calls it a data gap."])

        content = (project / "Questions_For_User.md").read_text(encoding="utf-8")
        assert "Dashboard field 'city' not found." in content  # original preserved
        assert "## Automated consistency review" in content
        assert "Section 3.8 says questionnaire completed" in content

    def test_creates_file_if_absent(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        append_findings(project, ["A contradiction."])
        content = (project / "Questions_For_User.md").read_text(encoding="utf-8")
        assert "A contradiction." in content

    def test_no_op_for_empty_findings(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        append_findings(project, [])
        assert not (project / "Questions_For_User.md").exists()

    def test_filters_non_string_entries(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        append_findings(project, [None, "", "  ", "Real finding."])
        content = (project / "Questions_For_User.md").read_text(encoding="utf-8")
        assert content.count("- ") == 1
        assert "Real finding." in content


class TestRunReviewPass:
    def _project_with_sections(self, tmp_path: Path) -> Path:
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        (project / "Report_Sections" / "11_Qualifications.md").write_text(
            "Prepared by Ravi Engineering & Land Surveying, P.C.", encoding="utf-8"
        )
        return project

    def test_no_sections_is_a_no_op(self, tmp_path: Path):
        project = tmp_path / "EmptyProject"
        project.mkdir()
        with patch("notebooklm_pipeline.review_pass.run_claude") as mock_run:
            run_review_pass(project, {})
        mock_run.assert_not_called()

    def test_applies_deletions_and_findings_on_success(self, tmp_path: Path):
        project = self._project_with_sections(tmp_path)
        response = CompletedProcess(
            args=[], returncode=0,
            stdout='{"deletions": ["Prepared by Ravi Engineering & Land Surveying, P.C."], '
                   '"findings": ["Authorship mismatch in 11.0."]}',
            stderr="",
        )
        with patch("notebooklm_pipeline.review_pass.run_claude", return_value=response):
            run_review_pass(project, {"ep_firm": "Envicon Engineering"})

        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Ravi Engineering" not in content
        questions = (project / "Questions_For_User.md").read_text(encoding="utf-8")
        assert "Authorship mismatch in 11.0." in questions

    def test_failed_cli_call_is_a_no_op(self, tmp_path: Path):
        project = self._project_with_sections(tmp_path)
        response = CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch("notebooklm_pipeline.review_pass.run_claude", return_value=response):
            run_review_pass(project, {})

        # Section untouched, no Questions_For_User.md created.
        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Ravi Engineering" in content
        assert not (project / "Questions_For_User.md").exists()

    def test_malformed_json_response_is_a_no_op(self, tmp_path: Path):
        project = self._project_with_sections(tmp_path)
        response = CompletedProcess(args=[], returncode=0, stdout="not valid json", stderr="")
        with patch("notebooklm_pipeline.review_pass.run_claude", return_value=response):
            run_review_pass(project, {})
        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Ravi Engineering" in content

    def test_wrong_shape_json_response_is_a_no_op(self, tmp_path: Path):
        project = self._project_with_sections(tmp_path)
        response = CompletedProcess(args=[], returncode=0, stdout='{"deletions": "not a list", "findings": []}', stderr="")
        with patch("notebooklm_pipeline.review_pass.run_claude", return_value=response):
            run_review_pass(project, {})
        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Ravi Engineering" in content

    def test_empty_deletions_and_findings_leaves_everything_untouched(self, tmp_path: Path):
        project = self._project_with_sections(tmp_path)
        response = CompletedProcess(args=[], returncode=0, stdout='{"deletions": [], "findings": []}', stderr="")
        with patch("notebooklm_pipeline.review_pass.run_claude", return_value=response):
            run_review_pass(project, {})
        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Ravi Engineering" in content
        assert not (project / "Questions_For_User.md").exists()
