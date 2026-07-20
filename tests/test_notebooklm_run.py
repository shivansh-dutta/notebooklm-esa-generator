"""
Unit tests for notebooklm_pipeline/run.py and scripts.init_project.ensure_project_scaffold.

Covers the "in-place" project mode added for the org-wide Skill workflow:
running against an arbitrary external folder that may already exist (and
may already hold the raw PDF a teammate dropped in) rather than a brand-new
folder under this repo's own Projects/. No Claude or NotebookLM calls are
involved in any of these tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from notebooklm_pipeline import run as run_module
from scripts.init_project import SECTION_TEMPLATES, SUBFOLDERS, ensure_project_scaffold


class TestEnsureProjectScaffold:
    def test_creates_folder_and_scaffold_when_absent(self, tmp_path: Path):
        project_path = tmp_path / "123 Example St"
        ensure_project_scaffold(project_path, "123 Example St", "123 Example St, Buffalo, NY")

        assert project_path.exists()
        for folder in SUBFOLDERS:
            assert (project_path / folder).is_dir()
        assert (project_path / "00_Project_Dashboard.md").exists()
        for template in SECTION_TEMPLATES:
            assert (project_path / "Report_Sections" / template).exists()

    def test_works_on_a_folder_that_already_exists_with_raw_files(self, tmp_path: Path):
        # Mirrors the real workflow: a teammate creates <ProjectName>/ and
        # drops the raw PDF in before Claude Code / this tool ever runs.
        project_path = tmp_path / "123 Example St"
        project_path.mkdir()
        (project_path / "RawPackage.pdf").write_bytes(b"%PDF-1.4 fake")

        ensure_project_scaffold(project_path, "123 Example St", "TBD")

        assert (project_path / "RawPackage.pdf").exists()  # untouched
        assert (project_path / "00_Project_Dashboard.md").exists()

    def test_never_overwrites_existing_dashboard(self, tmp_path: Path):
        project_path = tmp_path / "Proj"
        ensure_project_scaffold(project_path, "Proj", "TBD")
        dashboard = project_path / "00_Project_Dashboard.md"
        dashboard.write_text("CUSTOM CONTENT — DO NOT CLOBBER", encoding="utf-8")

        ensure_project_scaffold(project_path, "Proj", "a different address entirely")

        assert dashboard.read_text(encoding="utf-8") == "CUSTOM CONTENT — DO NOT CLOBBER"

    def test_never_overwrites_existing_section_content(self, tmp_path: Path):
        project_path = tmp_path / "Proj"
        ensure_project_scaffold(project_path, "Proj", "TBD")
        section = project_path / "Report_Sections" / SECTION_TEMPLATES[0]
        section.write_text("GROUNDED DRAFT PROSE", encoding="utf-8")

        ensure_project_scaffold(project_path, "Proj", "TBD")  # second call, same args

        assert section.read_text(encoding="utf-8") == "GROUNDED DRAFT PROSE"

    def test_safe_to_call_twice_in_a_row(self, tmp_path: Path):
        project_path = tmp_path / "Proj"
        ensure_project_scaffold(project_path, "Proj", "TBD")
        ensure_project_scaffold(project_path, "Proj", "TBD")  # must not raise
        assert project_path.exists()


class TestProjectLocationArgs:
    """--project and --project-dir are mutually exclusive and exactly one is required."""

    def _run_main_with_args(self, monkeypatch, argv_tail):
        monkeypatch.setattr(sys, "argv", ["notebooklm_pipeline", *argv_tail])
        with pytest.raises(SystemExit):
            run_module.main()

    def test_neither_given_is_a_usage_error(self, monkeypatch):
        self._run_main_with_args(monkeypatch, ["--raw", "some.pdf"])

    def test_both_given_is_a_usage_error(self, monkeypatch):
        self._run_main_with_args(
            monkeypatch,
            ["--project", "Foo", "--project-dir", "C:/Foo", "--raw", "some.pdf"],
        )


class TestEnsureProject:
    """_ensure_project() dispatches to the right init_project function per mode."""

    def _args(self, **overrides):
        base = {"project": None, "project_dir": None, "address": None}
        base.update(overrides)
        return type("Args", (), base)()

    def test_project_dir_mode_uses_folder_basename_as_display_name(self, tmp_path: Path):
        project_path = tmp_path / "My Site Name"
        args = self._args(project_dir=str(project_path))

        resolved_path, display_name = run_module._ensure_project(args)

        assert resolved_path == project_path.resolve()
        assert display_name == "My Site Name"
        assert (project_path / "00_Project_Dashboard.md").exists()

    def test_project_dir_mode_is_idempotent(self, tmp_path: Path):
        project_path = tmp_path / "My Site Name"
        args = self._args(project_dir=str(project_path))

        run_module._ensure_project(args)
        section = project_path / "Report_Sections" / SECTION_TEMPLATES[0]
        section.write_text("EXISTING CONTENT", encoding="utf-8")

        run_module._ensure_project(args)  # second call must not clobber

        assert section.read_text(encoding="utf-8") == "EXISTING CONTENT"
