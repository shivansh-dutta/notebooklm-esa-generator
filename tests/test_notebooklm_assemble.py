"""
Unit tests for notebooklm_pipeline/assemble.py.

Covers the deterministic mapping from qa_runner-shaped data into the three
on-disk artifacts scripts/export_docx.py actually reads: dashboard
frontmatter, Report_Sections/*.md, and EDR_Database_Hits/ + Manual_Review/.
No Claude or NotebookLM calls are involved — this module is pure formatting.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.writer import SECTIONS
from notebooklm_pipeline.assemble import (
    AUTO_DRAFT_RADIUS_FT,
    assemble,
    build_qualifications_markdown,
    classify_distance,
    write_dashboard,
    write_edr_hits,
    write_historical_tables,
    write_sections,
)
from scripts.export_docx import load_dashboard_meta, load_edr_hit_records, parse_writer_sections
from scripts.report_constants import PE_MARKER, pe_marker


class TestClassifyDistance:
    def test_within_radius(self):
        assert classify_distance(AUTO_DRAFT_RADIUS_FT - 1) == "within"
        assert classify_distance(str(AUTO_DRAFT_RADIUS_FT)) == "within"

    def test_beyond_radius(self):
        assert classify_distance(AUTO_DRAFT_RADIUS_FT + 1) == "beyond"

    def test_unknown_for_missing_or_bad_values(self):
        assert classify_distance(None) == "unknown"
        assert classify_distance("") == "unknown"
        assert classify_distance("not a number") == "unknown"


class TestWriteDashboard:
    def test_round_trips_through_export_docx_loader(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        values = {
            "site_address": "123 Main St",
            "city": "Buffalo",
            "state": "NY",
        }
        write_dashboard(project, "TestProject", values)

        loaded = load_dashboard_meta(project)
        assert loaded["site_address"] == "123 Main St"
        assert loaded["city"] == "Buffalo"
        assert loaded["state"] == "NY"
        # Non-question defaults are present.
        assert loaded["report_status"] == "PE Review Pending"

    def test_pe_marker_value_survives_as_literal_string(self, tmp_path: Path):
        # question_bank's questions instruct NotebookLM to answer pe_marker()
        # when a field isn't found — assemble must not "helpfully" convert
        # that into TBD or blank; export_docx treats a non-empty, non-"TBD"
        # value as real data and prints it verbatim.
        project = tmp_path / "TestProject"
        project.mkdir()
        write_dashboard(project, "TestProject", {"site_address": pe_marker("not found")})
        loaded = load_dashboard_meta(project)
        assert loaded["site_address"] == pe_marker("not found")

    def test_escapes_embedded_quotes(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_dashboard(project, "TestProject", {"client_name": 'Acme "Best" Corp'})
        loaded = load_dashboard_meta(project)
        assert loaded["client_name"] == 'Acme "Best" Corp'

    def test_identity_fields_default_to_pe_marker_not_notebooklm_values(self, tmp_path: Path):
        # assessor_name/reviewer_name/title/last_name are never in
        # results.dashboard (question_bank no longer asks NotebookLM for
        # them) — write_dashboard must fall back to a PE marker, never a
        # blank or a fabricated identity.
        project = tmp_path / "TestProject"
        project.mkdir()
        write_dashboard(project, "TestProject", {"site_address": "123 Main St"})
        loaded = load_dashboard_meta(project)
        assert PE_MARKER in loaded["assessor_name"]
        assert PE_MARKER in loaded["reviewer_name"]
        assert PE_MARKER in loaded["title"]
        assert PE_MARKER in loaded["last_name"]


class TestBuildQualificationsMarkdown:
    def test_uses_dashboard_firm_and_ep_identity(self):
        dashboard = {
            "ep_firm": "Envicon Engineering",
            "assessor_name": "Shivansh Dutta",
            "title": "Senior Environmental Professional",
            "reviewer_name": "Jason Dutta",
        }
        out = build_qualifications_markdown(dashboard, "TestProject")
        assert "# 11.0 Qualifications and Declaration of Environmental Professionals" in out
        assert "Envicon Engineering" in out
        assert "Shivansh Dutta" in out
        assert "Jason Dutta" in out
        # Never mentions a third-party firm/name — this function only ever
        # reads the dashboard dict it's given.
        assert "Ravi Engineering" not in out
        assert "Reddy" not in out

    def test_missing_fields_become_pe_marker(self):
        out = build_qualifications_markdown({}, "TestProject")
        assert PE_MARKER in out
        assert "Envicon" not in out  # no fabricated firm name


class TestAssembleWritesQualificationsEvenWithoutNotebookLMAnswer:
    def test_11_qualifications_written_from_dashboard_not_results_sections(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        results = SimpleNamespace(
            dashboard={"ep_firm": "Envicon Engineering", "assessor_name": "Shivansh Dutta"},
            sections={},  # NotebookLM never asked for 11.0 — should be empty here
            edr_hits={},
            historical_tables={},
        )
        assemble(project, "TestProject", results)
        content = (project / "Report_Sections" / "11_Qualifications.md").read_text(encoding="utf-8")
        assert "Envicon Engineering" in content
        assert "Shivansh Dutta" in content


class TestWriteSections:
    def test_writes_all_provided_sections_with_draft_marker(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        sections = {filename: f"## {name}\n\nDrafted prose for {name}." for _n, name, filename in SECTIONS}

        write_sections(project, sections)

        for _num, name, filename in SECTIONS:
            content = (project / "Report_Sections" / filename).read_text(encoding="utf-8")
            assert "DRAFT" in content and "PE REVIEW REQUIRED" in content
            assert f"Drafted prose for {name}." in content

    def test_leaves_missing_section_untouched(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        sections_dir = project / "Report_Sections"
        sections_dir.mkdir(parents=True)
        existing_file = sections_dir / SECTIONS[0][2]
        existing_file.write_text("ORIGINAL TEMPLATE CONTENT", encoding="utf-8")

        write_sections(project, {})  # no answers at all

        assert existing_file.read_text(encoding="utf-8") == "ORIGINAL TEMPLATE CONTENT"

    def test_output_is_parseable_by_export_docx(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        (project / "Report_Sections").mkdir(parents=True)
        sections = {
            "01_Introduction.md": "## 1.1 Purpose\n\nGrounded purpose text.\n",
        }
        write_sections(project, sections)
        parsed = parse_writer_sections(project / "Report_Sections")
        assert parsed.get("1.1 purpose") == "Grounded purpose text."


class TestWriteHistoricalTables:
    def test_writes_json_readable_back(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        tables = {
            "aerial": [{"year": "1950", "subject_property": "Vacant", "adjacent_properties": "Industrial"}],
            "sanborn": [],
            "city_directory": [{"year": "1965", "address": "631 Northland Ave", "occupant": "Example Co."}],
        }
        out_path = write_historical_tables(project, tables)
        assert out_path.exists()
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert loaded == tables


class TestWriteEdrHits:
    def _record(self, **overrides):
        base = {
            "site_name": "Acme Facility",
            "address": "1 Acme Way",
            "distance_ft": 100,
            "direction": "NE",
            "program_id": "NYD000000001",
            "status": "Active",
            "nysdec_program": "RCRA Generator",
            "preliminary_classification": "",
        }
        base.update(overrides)
        return base

    def test_within_radius_routes_to_edr_database_hits(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {"RCRA": [self._record(distance_ft=100)]})

        hits = list((project / "EDR_Database_Hits").glob("*.md"))
        manual = list((project / "Manual_Review").glob("*.md"))
        assert len(hits) == 1
        assert len(manual) == 0
        content = hits[0].read_text(encoding="utf-8")
        assert 'database_source: "RCRA"' in content
        assert "Acme Facility" in content

    def test_beyond_radius_routes_to_manual_review(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {"NPL": [self._record(distance_ft=AUTO_DRAFT_RADIUS_FT + 500)]})

        hits = list((project / "EDR_Database_Hits").glob("*.md"))
        manual = list((project / "Manual_Review").glob("*.md"))
        assert len(hits) == 0
        assert len(manual) == 1
        assert "Manual Review required" in manual[0].read_text(encoding="utf-8")

    def test_missing_distance_routes_to_manual_review_as_unknown(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {"LUST": [self._record(distance_ft=None)]})

        manual = list((project / "Manual_Review").glob("*.md"))
        assert len(manual) == 1

    def test_missing_fields_become_pe_marker_not_blank(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {"SPILLS": [self._record(address="", program_id=None)]})

        note = next((project / "EDR_Database_Hits").glob("*.md")).read_text(encoding="utf-8")
        assert PE_MARKER in note

    def test_no_hits_at_all_writes_no_hits_marker(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {"NPL": []})
        assert (project / "EDR_Database_Hits" / "no_hits.md").exists()

    def test_filename_collisions_are_disambiguated(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {
            "RCRA": [self._record(site_name="Same Name", program_id="A"), self._record(site_name="Same Name", program_id="B")],
        })
        hits = list((project / "EDR_Database_Hits").glob("*.md"))
        assert len(hits) == 2

    def test_output_is_readable_by_export_docx_loader(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        write_edr_hits(project, {
            "NPL": [self._record(distance_ft=50)],
            "RCRA": [self._record(distance_ft=AUTO_DRAFT_RADIUS_FT + 100, site_name="Far Away Site")],
        })
        records = load_edr_hit_records(project)
        sources = {r["database_source"] for r in records}
        assert sources == {"NPL", "RCRA"}
        assert len(records) == 2
