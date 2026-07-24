"""
Unit tests for notebooklm_pipeline/question_bank.py — structural checks that
the question bank stays in sync with what assemble.py / export_docx.py
actually need, without needing NotebookLM itself.
"""

from __future__ import annotations

from agents.writer import SECTIONS
from notebooklm_pipeline.question_bank import (
    DASHBOARD_FIELDS,
    dashboard_questions,
    edr_enumeration_questions,
    historical_table_questions,
    legal_vault_source_paths,
    section_questions,
)
from scripts.report_constants import DATABASE_TO_LIST, PLACEHOLDER_FIELD_MAP


class TestDashboardQuestions:
    def test_one_question_per_field(self):
        questions = dashboard_questions()
        assert len(questions) == len(DASHBOARD_FIELDS)

    def test_covers_every_export_docx_placeholder_field(self):
        # Every dashboard field export_docx.PLACEHOLDER_FIELD_MAP needs must
        # be askable, or the DOCX cover/signature will always show PE_MARKER
        # even when NotebookLM could have found the value — EXCEPT the
        # identity fields (assessor/reviewer/title/last_name), which are
        # deliberately never asked of NotebookLM (see question_bank.py's
        # comment above DASHBOARD_FIELDS / _NEVER_CARRY_OVER_IDENTITY): the
        # 631 Northland review found NotebookLM answering "who conducted
        # this assessment" from a prior consultant's own appendix. Those
        # fields come from assemble.py's non-question defaults instead.
        _identity_fields_never_asked = {"assessor_name", "reviewer_name", "title", "last_name"}
        asked_fields = {dq.field for dq in dashboard_questions()}
        needed_fields = set(PLACEHOLDER_FIELD_MAP.values()) - _identity_fields_never_asked
        assert needed_fields <= asked_fields
        assert asked_fields.isdisjoint(_identity_fields_never_asked)


class TestSectionQuestions:
    def test_one_question_per_writer_section_with_a_template(self):
        questions = section_questions()
        # Every entry should correspond to a real SECTIONS filename.
        filenames = {sq.filename for sq in questions}
        assert filenames <= {f for _n, _name, f in SECTIONS}
        assert len(questions) > 0

    def test_records_review_gets_classification_instructions(self):
        questions = section_questions()
        records_review = next(sq for sq in questions if sq.section_num == "5.0")
        assert "REC" in records_review.question and "CREC" in records_review.question

    def test_other_sections_do_not_get_records_review_instructions(self):
        questions = section_questions()
        intro = next(sq for sq in questions if sq.section_num == "1.0")
        assert "CLASSIFICATION REQUIREMENT" not in intro.question

    def test_question_preserves_template_headings_instruction(self):
        questions = section_questions()
        for sq in questions:
            assert "EXACTLY as it appears" in sq.question
            assert "FILL IN MANUALLY" in sq.question

    def test_records_review_is_split_into_extra_questions(self):
        # Section 5.0 has to classify every EDR hit (REC/CREC/HREC) across
        # potentially dozens of records — one giant response is large enough
        # to break the notebooklm-py streaming decoder (observed live), so
        # it's split at the Federal/State-tribal-local boundary.
        questions = section_questions()
        records_review = next(sq for sq in questions if sq.section_num == "5.0")
        assert len(records_review.extra_questions) == 1
        assert "### Federal records" in records_review.question
        assert "### State, tribal, and local records" not in records_review.question
        assert "### State, tribal, and local records" in records_review.extra_questions[0]
        # Classification instructions must apply to both halves.
        assert "CLASSIFICATION REQUIREMENT" in records_review.extra_questions[0]

    def test_other_sections_are_not_split(self):
        questions = section_questions()
        for sq in questions:
            if sq.section_num != "5.0":
                assert sq.extra_questions == []

    def test_qualifications_section_is_excluded(self):
        # 11.0 is never asked of NotebookLM — see
        # question_bank._NOTEBOOKLM_EXCLUDED_SECTIONS /
        # _NEVER_CARRY_OVER_IDENTITY. assemble.build_qualifications_markdown
        # builds it from dashboard fields instead.
        questions = section_questions()
        assert all(sq.section_num != "11.0" for sq in questions)
        assert "11_Qualifications.md" not in {sq.filename for sq in questions}

    def test_every_question_includes_identity_firewall(self):
        questions = section_questions()
        assert questions  # sanity: not accidentally empty
        for sq in questions:
            assert "never state or imply who prepared" in sq.question.lower()
            for extra in sq.extra_questions:
                assert "never state or imply who prepared" in extra.lower()


class TestEdrEnumerationQuestions:
    def test_one_question_per_database_to_list_key(self):
        questions = edr_enumeration_questions()
        assert {eq.database_source for eq in questions} == set(DATABASE_TO_LIST.keys())

    def test_question_requests_json_only(self):
        questions = edr_enumeration_questions()
        for eq in questions:
            assert "JSON array" in eq.question


class TestHistoricalTableQuestions:
    def test_one_question_per_table(self):
        questions = historical_table_questions()
        assert {hq.table_key for hq in questions} == {"aerial", "sanborn", "city_directory"}

    def test_questions_request_json_only(self):
        for hq in historical_table_questions():
            assert "JSON array" in hq.question

    def test_aerial_and_sanborn_use_subject_adjacent_schema(self):
        questions = {hq.table_key: hq.question for hq in historical_table_questions()}
        assert "subject_property" in questions["aerial"]
        assert "adjacent_properties" in questions["aerial"]
        assert "subject_property" in questions["sanborn"]

    def test_city_directory_uses_address_occupant_schema(self):
        questions = {hq.table_key: hq.question for hq in historical_table_questions()}
        assert "address" in questions["city_directory"]
        assert "occupant" in questions["city_directory"]


class TestLegalVaultSourcePaths:
    def test_returns_only_existing_files(self):
        paths = legal_vault_source_paths()
        assert all(p.exists() for p in paths)
