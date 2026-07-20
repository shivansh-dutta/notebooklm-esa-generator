"""
Unit tests for notebooklm_pipeline/orchestrator.py — the heuristics and
parsing that don't require calling the claude CLI:
  - is_thin_answer
  - try_parse_json_array (direct, fenced, embedded-in-prose, invalid)
  - route_unknowns

build_followup_question / repair_edr_json (which shell out via
agents.claude_cli.run_claude) are covered indirectly through mocking, same
pattern as tests/test_claude_cli.py.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from notebooklm_pipeline.orchestrator import (
    build_followup_question,
    is_thin_answer,
    repair_edr_json,
    route_unknowns,
    try_parse_json_array,
)
from scripts.report_constants import PE_MARKER


class TestIsThinAnswer:
    def test_short_answer_is_thin(self):
        assert is_thin_answer("Not found.")

    def test_substantial_answer_is_not_thin(self):
        assert not is_thin_answer("A" * 500)

    def test_marker_heavy_answer_is_thin(self):
        answer = (PE_MARKER + " ") * 20
        assert is_thin_answer(answer)

    def test_normal_prose_with_one_marker_is_not_thin(self):
        answer = ("This is a substantial drafted paragraph. " * 20) + PE_MARKER
        assert not is_thin_answer(answer)


class TestTryParseJsonArray:
    def test_parses_clean_json(self):
        result = try_parse_json_array('[{"site_name": "Acme"}]')
        assert result == [{"site_name": "Acme"}]

    def test_parses_fenced_json(self):
        result = try_parse_json_array('```json\n[{"site_name": "Acme"}]\n```')
        assert result == [{"site_name": "Acme"}]

    def test_parses_json_embedded_in_prose(self):
        result = try_parse_json_array('Here is the data:\n[{"site_name": "Acme"}]\nHope that helps!')
        assert result == [{"site_name": "Acme"}]

    def test_empty_array_is_valid(self):
        assert try_parse_json_array("[]") == []

    def test_invalid_json_returns_none(self):
        assert try_parse_json_array("not json at all, sorry I couldn't find anything") is None

    def test_non_array_json_returns_none(self):
        assert try_parse_json_array('{"not": "an array"}') is None


class TestBuildFollowupQuestion:
    def test_returns_none_on_cli_failure(self):
        with patch(
            "notebooklm_pipeline.orchestrator.run_claude",
            return_value=CompletedProcess(args=[], returncode=1, stdout="", stderr="boom"),
        ):
            result = build_followup_question("orig q", "thin answer", "5.0 Records Review")
        assert result is None

    def test_returns_stripped_followup_on_success(self):
        with patch(
            "notebooklm_pipeline.orchestrator.run_claude",
            return_value=CompletedProcess(args=[], returncode=0, stdout="  What is the LUST status?  \n", stderr=""),
        ) as mock_run:
            result = build_followup_question("orig q", "thin answer", "5.0 Records Review")
        assert result == "What is the LUST status?"
        # Confirm the sonnet orchestrator model is actually requested.
        assert mock_run.call_args.kwargs.get("model") == "sonnet"


class TestRepairEdrJson:
    def test_repairs_malformed_json_via_claude(self):
        with patch(
            "notebooklm_pipeline.orchestrator.run_claude",
            return_value=CompletedProcess(args=[], returncode=0, stdout='[{"site_name": "Fixed"}]', stderr=""),
        ):
            result = repair_edr_json("RCRA", "some malformed almost-json text")
        assert result == [{"site_name": "Fixed"}]

    def test_returns_none_when_repair_itself_is_unparseable(self):
        with patch(
            "notebooklm_pipeline.orchestrator.run_claude",
            return_value=CompletedProcess(args=[], returncode=0, stdout="still not json", stderr=""),
        ):
            result = repair_edr_json("RCRA", "malformed")
        assert result is None


class TestRouteUnknowns:
    def test_writes_questions_file(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        path = route_unknowns(project, ["Item A", "Item B"])
        assert path == project / "Questions_For_User.md"
        content = path.read_text(encoding="utf-8")
        assert "Item A" in content
        assert "Item B" in content

    def test_no_file_written_when_nothing_unresolved(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        result = route_unknowns(project, [])
        assert result is None
        assert not (project / "Questions_For_User.md").exists()
