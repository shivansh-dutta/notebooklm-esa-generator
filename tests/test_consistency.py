"""Tests for notebooklm_pipeline/consistency.py — deterministic (no LLM)
cross-section contradiction checks."""

from __future__ import annotations

from pathlib import Path

from notebooklm_pipeline.consistency import (
    check_authorship_mismatch,
    check_consistency,
    check_crec_hrec_contradiction,
    check_questionnaire_contradiction,
    run_consistency_checks,
)


class TestCheckQuestionnaireContradiction:
    def test_flags_contradiction(self):
        sections = {
            "03_User_Provided_Information.md": (
                "The completed Environmental Questionnaire references Angelo Rhodes II, "
                "dated Feb 22, 2024. The respondent's answers were provided in full."
            ),
            "08_Findings_Opinions_Conclusions.md": "No completed Environmental Questionnaire was provided by the User.",
        }
        findings = check_questionnaire_contradiction(sections)
        assert len(findings) == 1
        assert "Section 3.0" in findings[0] and "Section 8.0" in findings[0]

    def test_no_finding_when_consistent(self):
        sections = {
            "03_User_Provided_Information.md": "No questionnaire was completed by the User.",
            "08_Findings_Opinions_Conclusions.md": "No completed Environmental Questionnaire was provided.",
        }
        assert check_questionnaire_contradiction(sections) == []

    def test_no_finding_when_sections_missing(self):
        assert check_questionnaire_contradiction({}) == []

    def test_negated_phrasing_is_not_a_false_positive(self):
        # Regression: "No questionnaire was completed" naively contains
        # "questionnaire...completed" too — must not be read as a positive.
        sections = {
            "03_User_Provided_Information.md": "No questionnaire was completed by the User for this assessment.",
            "08_Findings_Opinions_Conclusions.md": "No completed Environmental Questionnaire was provided.",
        }
        assert check_questionnaire_contradiction(sections) == []


class TestCheckCrecHrecContradiction:
    def test_flags_contradiction(self):
        sections = {
            "Executive_Summary.md": "No CRECs or HRECs were identified in connection with the Subject Property.",
            "05_Records_Review.md": "The 577 Northland Avenue spill is classified as an HREC per ASTM E1527-21.",
        }
        findings = check_crec_hrec_contradiction(sections)
        assert len(findings) == 1
        assert "CREC" in findings[0]

    def test_no_finding_when_consistent(self):
        sections = {
            "Executive_Summary.md": "No CRECs or HRECs were identified.",
            "05_Records_Review.md": "All listings were classified as de minimis or REC.",
        }
        assert check_crec_hrec_contradiction(sections) == []


class TestCheckAuthorshipMismatch:
    def test_flags_mismatched_firm(self):
        sections = {
            "11_Qualifications.md": (
                "This assessment was conducted and prepared by qualified environmental "
                "professionals of Ravi Engineering & Land Surveying, P.C. (RE&LS)."
            ),
        }
        findings = check_authorship_mismatch(sections, {"ep_firm": "Envicon Engineering"})
        assert len(findings) == 1
        assert "Ravi Engineering" in findings[0]

    def test_no_finding_when_firm_matches(self):
        sections = {
            "11_Qualifications.md": "This assessment was prepared by qualified professionals of Envicon Engineering.",
        }
        assert check_authorship_mismatch(sections, {"ep_firm": "Envicon Engineering"}) == []

    def test_skipped_entirely_when_dashboard_firm_unset(self):
        sections = {"11_Qualifications.md": "Prepared by Some Other Firm LLC."}
        assert check_authorship_mismatch(sections, {}) == []
        assert check_authorship_mismatch(sections, {"ep_firm": "TBD"}) == []


class TestCheckConsistency:
    def test_aggregates_all_checks(self):
        sections = {
            "03_User_Provided_Information.md": "The completed questionnaire responses were provided in full.",
            "08_Findings_Opinions_Conclusions.md": "No completed Environmental Questionnaire was provided.",
            "Executive_Summary.md": "No CRECs or HRECs were identified.",
            "05_Records_Review.md": "This listing is classified as a CREC.",
            "11_Qualifications.md": "Prepared by Ravi Engineering & Land Surveying, P.C.",
        }
        findings = check_consistency(sections, {"ep_firm": "Envicon Engineering"})
        assert len(findings) == 3

    def test_no_sections_no_findings(self):
        assert check_consistency({}, {}) == []


class TestRunConsistencyChecks:
    def test_appends_findings_to_questions_file(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        (project / "Report_Sections" / "Executive_Summary.md").write_text(
            "No CRECs or HRECs were identified.", encoding="utf-8"
        )
        (project / "Report_Sections" / "05_Records_Review.md").write_text(
            "This listing is classified as an HREC.", encoding="utf-8"
        )
        run_consistency_checks(project, {})
        content = (project / "Questions_For_User.md").read_text(encoding="utf-8")
        assert "## Deterministic consistency checks" in content
        assert "CREC" in content

    def test_no_op_when_nothing_found(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        (project / "Report_Sections" / "Executive_Summary.md").write_text("Clean report.", encoding="utf-8")
        run_consistency_checks(project, {})
        assert not (project / "Questions_For_User.md").exists()
