"""Tests for notebooklm_pipeline/source_manifest.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from notebooklm_pipeline.source_manifest import (
    build_manifest_markdown,
    present_components,
    write_source_manifest,
)
from scripts.report_constants import KNOWN_COMPONENTS, UNDETECTABLE_INPUTS


def _source(component: str):
    return SimpleNamespace(component=component)


class TestPresentComponents:
    def test_returns_set_of_components(self):
        sources = [_source("edr_radius_report"), _source("maps"), _source("maps")]
        assert present_components(sources) == {"edr_radius_report", "maps"}

    def test_empty_list_returns_empty_set(self):
        assert present_components([]) == set()


class TestBuildManifestMarkdown:
    def test_none_sources_reports_unavailable(self):
        out = build_manifest_markdown(None)
        assert "Not available" in out
        assert "--notebook-id" in out

    def test_all_seven_appendices_present_no_warning(self):
        sources = [_source(key) for key in KNOWN_COMPONENTS]
        out = build_manifest_markdown(sources)
        assert "WARNING" not in out
        for meta in KNOWN_COMPONENTS.values():
            assert meta["label"] in out

    def test_missing_required_edr_report_triggers_warning(self):
        sources = [_source("maps"), _source("site_photographs")]
        out = build_manifest_markdown(sources)
        assert "WARNING" in out
        assert "EDR radius report" in out

    def test_lists_undetectable_inputs_as_advisory(self):
        out = build_manifest_markdown([])
        for label in UNDETECTABLE_INPUTS.values():
            assert label in out

    def test_matches_631_northland_real_appendix_set(self):
        # The actual 631 Northland raw package's 7 appendices (per
        # Source_Documents/_appendix_map.md) — all standard components
        # present, no required-component warning.
        sources = [
            _source("maps"),
            _source("site_photographs"),
            _source("environmental_questionnaire"),
            _source("historic_research"),
            _source("edr_radius_report"),
            _source("foil"),
            _source("qualifications"),
        ]
        out = build_manifest_markdown(sources)
        assert "WARNING" not in out


class TestWriteSourceManifest:
    def test_writes_file_and_content_matches(self, tmp_path: Path):
        project = tmp_path / "TestProject"
        project.mkdir()
        sources = [_source("edr_radius_report")]
        path = write_source_manifest(project, sources)
        assert path == project / "Source_Manifest.md"
        assert path.exists()
        assert path.read_text(encoding="utf-8") == build_manifest_markdown(sources)
