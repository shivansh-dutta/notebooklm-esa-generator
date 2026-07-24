"""Tests for scripts/engineer_form.py — gap collection from Report_Sections/
markers + Questions_For_User.md, and the generated HTML form."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.engineer_form import Gap, build_form_html, collect_gaps, write_engineer_form


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestCollectSectionMarkerGaps:
    def test_finds_pe_marker_with_description(self, tmp_path: Path):
        _write(
            tmp_path / "Report_Sections" / "05_Records_Review.md",
            "Some drafted text.\n\n"
            "» PE TO COMPLETE: Record any additional sources reviewed, or \"None.\"\n",
        )
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 1
        g = gaps[0]
        assert g.kind == "section_marker"
        assert g.marker_kind == "PE TO COMPLETE"
        assert g.file == "05_Records_Review.md"
        assert g.match == '» PE TO COMPLETE: Record any additional sources reviewed, or "None."'
        assert "Record any additional sources reviewed" in g.prompt

    def test_finds_missing_input_marker(self, tmp_path: Path):
        _write(
            tmp_path / "Report_Sections" / "03_User_Provided_Information.md",
            "» MISSING INPUT: title policy / environmental lien search\n",
        )
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 1
        assert gaps[0].marker_kind == "MISSING INPUT"
        assert gaps[0].file == "03_User_Provided_Information.md"

    def test_bare_marker_with_no_description(self, tmp_path: Path):
        _write(tmp_path / "Report_Sections" / "10_Deviations.md", "» PE TO COMPLETE\n")
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 1
        assert gaps[0].match == "» PE TO COMPLETE"
        assert "no further description" in gaps[0].prompt

    def test_mid_sentence_double_marker_does_not_swallow_trailing_sentence(self, tmp_path: Path):
        # Regression: notebooklm_pipeline.assemble.build_qualifications_markdown's
        # real Section 11.0 output puts two markers in one sentence, bookended
        # by em dashes. Naive "up to next marker or newline" capture would let
        # the SECOND marker's match swallow the real "declare that..." clause
        # that follows it (no more markers, no newline, until end of
        # paragraph) — replacing that whole span on ingestion would delete
        # real report content, not just the gap.
        text = (
            "The undersigned Environmental Professional(s) — "
            "» PE TO COMPLETE: environmental professional name, "
            "» PE TO COMPLETE: EP professional title — "
            "declare that this assessment was conducted in conformance with "
            "the scope and limitations of ASTM E1527-21.\n"
        )
        _write(tmp_path / "Report_Sections" / "11_Qualifications.md", text)
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 2
        assert gaps[0].match == "» PE TO COMPLETE: environmental professional name, "
        assert gaps[1].match == "» PE TO COMPLETE: EP professional title "
        # Neither captured match includes the real trailing sentence.
        for g in gaps:
            assert "declare that" not in g.match

    def test_mid_sentence_markers_bounded_by_markdown_bold_do_not_swallow_text_between(self, tmp_path: Path):
        # Regression: the real 01_Introduction.md wraps each marker in its
        # own bold span — "...conducted on **» PE TO COMPLETE: site
        # reconnaissance date** by **» PE TO COMPLETE: Environmental
        # Professional name**." Stopping only at the next marker/newline/em
        # dash still let the first marker's match swallow "** by **" — the
        # real word "by" and both bold delimiters — between the two markers.
        text = (
            "The site reconnaissance was conducted on "
            "**» PE TO COMPLETE: site reconnaissance date** by "
            "**» PE TO COMPLETE: Environmental Professional name**.\n"
        )
        _write(tmp_path / "Report_Sections" / "01_Introduction.md", text)
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 2
        assert gaps[0].match == "» PE TO COMPLETE: site reconnaissance date"
        assert gaps[1].match == "» PE TO COMPLETE: Environmental Professional name"
        for g in gaps:
            assert "by" not in g.match
            assert "**" not in g.match

    def test_no_report_sections_dir_returns_no_section_gaps(self, tmp_path: Path):
        assert collect_gaps(tmp_path) == []

    def test_multiple_files_grouped_and_ordered(self, tmp_path: Path):
        _write(tmp_path / "Report_Sections" / "02_Site_Description.md", "» PE TO COMPLETE: lot size\n")
        _write(tmp_path / "Report_Sections" / "01_Introduction.md", "» PE TO COMPLETE: intro gap\n")
        gaps = collect_gaps(tmp_path)
        # sorted() over Report_Sections glob means 01_ comes before 02_
        assert [g.file for g in gaps] == ["01_Introduction.md", "02_Site_Description.md"]


class TestParseQuestionsForUser:
    def test_dashboard_field_bullets(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n"
            "- Dashboard field 'client_name' not found in sources.\n"
            "- Dashboard field 'project_no' not found in sources.\n",
        )
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 2
        assert all(g.kind == "dashboard_field" for g in gaps)
        assert {g.field_name for g in gaps} == {"client_name", "project_no"}
        assert "client name" in gaps[0].prompt.lower()

    def test_other_bullets_become_decisions_under_base_section(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n"
            "- Section 2.0 (Site Description) — NotebookLM request failed: some reason.\n",
        )
        gaps = collect_gaps(tmp_path)
        assert len(gaps) == 1
        assert gaps[0].kind == "decision"
        assert gaps[0].section == "Unresolved items"

    def test_heading_sections_become_decision_groups(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n"
            "- Dashboard field 'client_name' not found in sources.\n\n"
            "## Automated consistency review\n\n"
            "- CREC/HREC contradiction: something conflicts with something else.\n",
        )
        gaps = collect_gaps(tmp_path)
        decisions = [g for g in gaps if g.kind == "decision"]
        assert len(decisions) == 1
        assert decisions[0].section == "Automated consistency review"
        assert "CREC/HREC" in decisions[0].prompt

    def test_no_file_returns_no_gaps(self, tmp_path: Path):
        assert collect_gaps(tmp_path) == []


class TestBuildFormHtml:
    def test_is_self_contained_no_external_requests(self, tmp_path: Path):
        gaps = [
            Gap(id="sec-001-aaaa", kind="section_marker", section="05 Records Review",
                prompt="Some prompt", marker_kind="PE TO COMPLETE",
                file="05_Records_Review.md", match="» PE TO COMPLETE: Some prompt"),
        ]
        out = build_form_html(gaps, "Test Project")
        for banned in ("http://", "https://", "<link ", "fonts.googleapis", "cdn."):
            assert banned not in out

    def test_contains_one_textarea_per_fillable_gap(self):
        gaps = [
            Gap(id="sec-001-aaaa", kind="section_marker", section="A", prompt="P1",
                marker_kind="PE TO COMPLETE", file="a.md", match="» PE TO COMPLETE: P1"),
            Gap(id="dash-001-bbbb", kind="dashboard_field", section="Project Dashboard",
                prompt="What is the client name?", field_name="client_name"),
        ]
        out = build_form_html(gaps, "Test Project")
        assert out.count('class="gap-answer"') == 2
        assert 'id="answer-sec-001-aaaa"' in out
        assert 'id="answer-dash-001-bbbb"' in out

    def test_embeds_exact_match_text_for_round_trip(self):
        match_text = '» PE TO COMPLETE: Record any additional sources reviewed, or "None."'
        gaps = [
            Gap(id="sec-001-aaaa", kind="section_marker", section="A", prompt="p",
                marker_kind="PE TO COMPLETE", file="a.md", match=match_text),
        ]
        out = build_form_html(gaps, "Test Project")
        data = json.loads(out.split('<script id="gaps-data" type="application/json">')[1].split("</script>")[0])
        assert data[0]["match"] == match_text

    def test_decisions_rendered_in_distinct_group_not_as_plain_fillable(self):
        gaps = [Gap(id="dec-001-cccc", kind="decision", section="Automated consistency review", prompt="Conflict X")]
        out = build_form_html(gaps, "Test Project")
        assert "gap-group-decide" in out
        assert "Conflict X" in out
        # A plain-language explainer for someone who isn't the report author.
        assert "won't change the report text" in out

    def test_empty_state_when_no_gaps(self):
        out = build_form_html([], "Test Project")
        assert "No open gaps found" in out

    def test_has_submit_button_and_output_panel(self):
        gaps = [
            Gap(id="sec-001-aaaa", kind="section_marker", section="A", prompt="p",
                marker_kind="PE TO COMPLETE", file="a.md", match="» PE TO COMPLETE: p"),
        ]
        out = build_form_html(gaps, "Test Project")
        assert 'id="submit-btn"' in out
        assert 'id="submit-panel"' in out
        assert 'id="submit-output"' in out
        assert 'id="copy-btn"' in out
        assert 'id="download-btn"' in out


class TestHumanizeDecisionPrompts:
    def test_numbered_filename_becomes_section_label(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n## Automated consistency review\n\n"
            "- CREC/HREC contradiction: 05_Records_Review.md says X but 08_Findings_Opinions_Conclusions.md says Y.\n",
        )
        gaps = collect_gaps(tmp_path)
        decision = next(g for g in gaps if g.kind == "decision")
        assert "05_Records_Review.md" not in decision.prompt
        assert "Section 5.0 (Records Review)" in decision.prompt
        assert "Section 8.0 (Findings Opinions Conclusions)" in decision.prompt

    def test_bare_filename_becomes_plain_label(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n## Automated consistency review\n\n"
            "- REC count mismatch: Executive_Summary.md lists 4 RECs but the body lists 6.\n",
        )
        gaps = collect_gaps(tmp_path)
        decision = next(g for g in gaps if g.kind == "decision")
        assert "Executive_Summary.md" not in decision.prompt
        assert "Executive Summary" in decision.prompt

    def test_notebooklm_failure_bullet_is_simplified(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n"
            "- Section 2.0 (Site Description) — NotebookLM request failed: chat.ask failed for question "
            "'Draft Section 2.0': No parseable chunks in streaming chat response (6 lines scanned).\n",
        )
        gaps = collect_gaps(tmp_path)
        decision = next(g for g in gaps if g.kind == "decision")
        assert "chat.ask" not in decision.prompt
        assert "streaming chat response" not in decision.prompt
        assert "Section 2.0 (Site Description) could not be auto-drafted" in decision.prompt

    def test_historical_table_failure_bullet_is_simplified(self, tmp_path: Path):
        _write(
            tmp_path / "Questions_For_User.md",
            "# Questions For User\n\n"
            "- Historical table 'aerial' — answer was not valid JSON; see NBLM_Answers/historical_aerial.md.\n",
        )
        gaps = collect_gaps(tmp_path)
        decision = next(g for g in gaps if g.kind == "decision")
        assert "valid JSON" not in decision.prompt
        assert "aerial historical-records table" in decision.prompt


class TestWriteEngineerForm:
    def test_writes_html_and_json(self, tmp_path: Path):
        _write(tmp_path / "Report_Sections" / "01_Introduction.md", "» PE TO COMPLETE: something\n")
        html_path, json_path = write_engineer_form(tmp_path)
        assert html_path == tmp_path / "Engineer_Form" / "Engineer_Fill_Form.html"
        assert json_path == tmp_path / "Engineer_Form" / "gaps.json"
        assert html_path.exists() and json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["kind"] == "section_marker"

    def test_uses_dashboard_project_name_when_available(self, tmp_path: Path):
        _write(
            tmp_path / "00_Project_Dashboard.md",
            '---\nproject_name: "My Real Project"\n---\n\n# Dashboard\n',
        )
        html_path, _ = write_engineer_form(tmp_path)
        assert "My Real Project" in html_path.read_text(encoding="utf-8")
