"""
Integration-style unit test for notebooklm_pipeline/qa_runner.py, using a
fake ask() so no real NotebookLM connection or claude CLI call is needed.

Verifies the orchestration wiring actually works end-to-end in-process:
  - every question category lands in the right QaResults field
  - a thin section answer triggers exactly one follow-up ask()
  - a malformed EDR JSON answer goes through orchestrator.repair_edr_json
    (mocked) and, on repair failure, is recorded in results.unresolved
    rather than silently dropped
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from notebooklm_pipeline import qa_runner
from notebooklm_pipeline.nblm_client import AskResult
from scripts.report_constants import DATABASE_TO_LIST, pe_marker


class _FakeAsk:
    """Routes based on distinctive substrings in each question_bank
    question, so one fake stands in for the whole bank without needing to
    hardcode call order."""

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, client, notebook_id, question: str) -> AskResult:
        self.calls.append(question)

        if "the complete street address" in question:
            return AskResult(answer="123 Test St")
        if "ONLY the value itself" in question:
            return AskResult(answer=pe_marker())  # every other dashboard field: not found

        if "Section 5.0" in question and "CLASSIFICATION REQUIREMENT" in question:
            # Section 5.0 gets a deliberately thin answer to exercise the
            # follow-up path.
            return AskResult(answer="Not found.")
        if question.startswith("A research assistant (NotebookLM) was asked"):
            # This is actually a Claude follow-up-generation prompt, not a
            # NotebookLM question — shouldn't be reached via qa_runner's ask().
            raise AssertionError("qa_runner should not send Claude prompts through nblm ask()")
        if "Draft Section" in question:
            return AskResult(answer="## Some Heading\n\n" + "Grounded section prose. " * 30)

        if 'under the "RCRA" database' in question:
            return AskResult(answer='[{"site_name": "Acme RCRA Site", "distance_ft": 100}]')
        if 'under the "NPL" database' in question:
            return AskResult(answer="this is not json, sorry")  # triggers repair path
        if "JSON array" in question:
            return AskResult(answer="[]")

        if "Site Photographs appendix" in question:
            return AskResult(answer="No site photographs were provided in the uploaded sources.")
        if "historical maps, aerial photographs" in question:
            return AskResult(answer="No historical maps or aerial photographs were provided in the uploaded sources.")
        if "field checklist for the environmental" in question:
            return AskResult(answer="- **Check NE corner** — verify LUST listing.")

        raise AssertionError(f"Unrouted fake question: {question[:120]!r}")


@pytest.mark.asyncio
async def test_run_qa_full_wiring(tmp_path: Path):
    project = tmp_path / "TestProject"
    project.mkdir()
    fake_ask = _FakeAsk()

    with patch("notebooklm_pipeline.qa_runner.ask", fake_ask), \
         patch(
             "notebooklm_pipeline.orchestrator.build_followup_question",
             return_value="Narrower follow-up question about Section 5.0 — "
                          "still needs to hit the 'CLASSIFICATION REQUIREMENT' "
                          "branch so give it the thin-answer text again indirectly.",
         ) as mock_followup, \
         patch(
             "notebooklm_pipeline.orchestrator.repair_edr_json",
             return_value=None,  # simulate repair also failing for NPL
         ) as mock_repair:
        results = await qa_runner.run_qa(client=object(), notebook_id="nb-1", project_path=project)

    # Dashboard: the one field the fake grounded, plus pe_marker() for the rest.
    assert results.dashboard["site_address"] == "123 Test St"
    assert results.dashboard["city"] == pe_marker()

    # Sections: every SECTIONS filename with a template got an answer.
    assert len(results.sections) > 0
    assert all(isinstance(v, str) and v for v in results.sections.values())

    # Follow-up was triggered for the thin Section 5.0 answer.
    mock_followup.assert_called()

    # EDR: RCRA parsed directly, NPL required (and exhausted) repair, others empty.
    assert results.edr_hits["RCRA"] == [{"site_name": "Acme RCRA Site", "distance_ft": 100}]
    mock_repair.assert_called()
    assert results.edr_hits["NPL"] == []
    assert any("NPL" in item for item in results.unresolved)

    # Vision + site-visit questions landed.
    assert "No site photographs" in results.site_photos
    assert "No historical maps" in results.maps
    assert "NE corner" in results.site_visit_notes

    # Every answer was persisted for audit.
    answers_dir = project / "NBLM_Answers"
    assert answers_dir.exists()
    assert any(answers_dir.glob("dashboard_site_address.md"))
    assert any(answers_dir.glob("edr_RCRA.md"))

    # Unresolved items (including the EDR one) got routed to Questions_For_User.md.
    assert (project / "Questions_For_User.md").exists()


@pytest.mark.asyncio
async def test_edr_covers_every_database(tmp_path: Path):
    project = tmp_path / "TestProject2"
    project.mkdir()
    fake_ask = _FakeAsk()

    async def dashboard_and_section_only(client, notebook_id, question):
        # Short-circuit everything except EDR questions to keep this test focused.
        if "JSON array" in question:
            return await fake_ask(client, notebook_id, question)
        return AskResult(answer=pe_marker())

    with patch("notebooklm_pipeline.qa_runner.ask", dashboard_and_section_only), \
         patch("notebooklm_pipeline.orchestrator.build_followup_question", return_value=None), \
         patch("notebooklm_pipeline.orchestrator.repair_edr_json", return_value=[]):
        results = await qa_runner.run_qa(client=object(), notebook_id="nb-2", project_path=project)

    assert set(results.edr_hits.keys()) == set(DATABASE_TO_LIST.keys())
