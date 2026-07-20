"""
init_project.py — Phase 1 ESA Report Generator
Creates a new project folder structure, generates the project dashboard,
and copies section templates from TemplateVault/.

Usage:
    python scripts/init_project.py --name "52-96-Falls-St" --address "52-96 Falls Street, Rochester, NY"
"""

import argparse
import shutil
import sys
from pathlib import Path
from datetime import date

# Resolve paths relative to this script's parent (project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_VAULT = PROJECT_ROOT / "TemplateVault"
PROJECTS_DIR = PROJECT_ROOT / "Projects"

SUBFOLDERS = [
    "Raw",
    "EDR_Database_Hits",
    "Manual_Review",
    "Historical_Records",
    "Site_Photos",
    "Source_Documents",
    "Site_Notes",
    "Report_Sections",
    "Export",
]

SECTION_TEMPLATES = [
    "Executive_Summary.md",
    "01_Introduction.md",
    "02_Site_Description.md",
    "03_User_Provided_Information.md",
    "04_Site_Reconnaissance.md",
    "05_Records_Review.md",
    "06_Interviews.md",
    "07_Non_Scope_BER.md",
    "08_Findings_Opinions_Conclusions.md",
    "09_Recommendations.md",
    "10_Deviations.md",
    "11_Qualifications.md",
    "12_References.md",
    "EDR_Hit_Template.md",
]

TRANSCLUSION_SECTIONS = [
    "Executive_Summary",
    "01_Introduction",
    "02_Site_Description",
    "03_User_Provided_Information",
    "04_Site_Reconnaissance",
    "05_Records_Review",
    "06_Interviews",
    "07_Non_Scope_BER",
    "08_Findings_Opinions_Conclusions",
    "09_Recommendations",
    "10_Deviations",
    "11_Qualifications",
    "12_References",
]


def create_folders(project_path: Path) -> None:
    """Create the project subfolder hierarchy."""
    project_path.mkdir(parents=True, exist_ok=False)
    print(f"Created project folder: {project_path}")

    for folder_name in SUBFOLDERS:
        folder = project_path / folder_name
        folder.mkdir()
        print(f"  Created subfolder: {folder_name}/")


def generate_dashboard(project_path: Path, project_name: str, site_address: str) -> None:
    """Generate 00_Project_Dashboard.md with YAML frontmatter and transclusion links."""
    dashboard_path = project_path / "00_Project_Dashboard.md"

    transclusion_lines = "\n".join(
        f"![[Report_Sections/{section}]]" for section in TRANSCLUSION_SECTIONS
    )

    content = f"""---
project_name: "{project_name}"
site_address: "{site_address}"
city: TBD
county: TBD
state: TBD
zip: TBD
client_name: TBD
client_address: TBD
ep_firm: TBD
project_no: TBD
assessment_dates: TBD
report_draft_date: TBD
report_status: In Progress
assessor_name: TBD
reviewer_name: TBD
title: TBD
last_name: TBD
---

# Phase 1 ESA — {project_name}

{transclusion_lines}
"""

    dashboard_path.write_text(content, encoding="utf-8")
    print(f"  Generated: 00_Project_Dashboard.md")


def copy_templates(project_path: Path, *, overwrite: bool = True) -> None:
    """Copy section templates from TemplateVault/ into Report_Sections/.

    overwrite=False skips any destination file that already exists — used by
    ensure_project_scaffold() so re-running against an already-scaffolded
    project never clobbers section content a user (or NotebookLM) has since
    written.
    """
    report_sections = project_path / "Report_Sections"

    if not TEMPLATE_VAULT.exists():
        print(
            f"WARNING: TemplateVault/ not found at {TEMPLATE_VAULT} — "
            "skipping template copy. Add templates manually to Report_Sections/."
        )
        return

    copied = 0
    skipped = 0
    missing = []
    for template_name in SECTION_TEMPLATES:
        src = TEMPLATE_VAULT / template_name
        dst = report_sections / template_name
        if not src.exists():
            missing.append(template_name)
            continue
        if dst.exists() and not overwrite:
            skipped += 1
            continue
        shutil.copy2(src, dst)
        print(f"  Copied template: {template_name}")
        copied += 1

    if missing:
        print(
            f"WARNING: The following templates were not found in TemplateVault/ "
            f"and were NOT copied: {', '.join(missing)}"
        )
    if skipped:
        print(f"  Skipped {skipped} template(s) already present in Report_Sections/")

    print(f"  Templates copied: {copied} / {len(SECTION_TEMPLATES)}")


def init_project(name: str, address: str) -> None:
    """Main entry point: scaffold a new project."""
    project_path = PROJECTS_DIR / name

    if project_path.exists():
        print(
            f"ERROR: Project '{name}' already exists at {project_path}. "
            "Exiting without overwriting."
        )
        sys.exit(1)

    print(f"\nInitializing project: {name}")
    print(f"Site address: {address}")
    print("-" * 60)

    create_folders(project_path)
    generate_dashboard(project_path, name, address)
    copy_templates(project_path)

    print("-" * 60)
    print(f"Project '{name}' initialized successfully.")
    print(f"Location: {project_path}")


def ensure_project_scaffold(project_path: Path, name: str, address: str) -> None:
    """
    Idempotent sibling to init_project() for scaffolding INTO a folder that
    may already exist — e.g. a project folder a teammate created and dropped
    raw source files into before this tool ever ran there — rather than
    requiring a brand-new, not-yet-existing folder like init_project() does.

    Never overwrites anything already present (existing dashboard, existing
    section content, existing subfolders); only fills in what's missing.
    Safe to call on every run, including repeated runs against the same
    project folder.
    """
    project_path = Path(project_path)
    is_new = not project_path.exists()
    project_path.mkdir(parents=True, exist_ok=True)

    for folder_name in SUBFOLDERS:
        (project_path / folder_name).mkdir(exist_ok=True)
    print(f"{'Created' if is_new else 'Using existing'} project folder: {project_path}")

    dashboard_path = project_path / "00_Project_Dashboard.md"
    if dashboard_path.exists():
        print("  00_Project_Dashboard.md already exists — leaving it untouched")
    else:
        generate_dashboard(project_path, name, address)

    copy_templates(project_path, overwrite=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize a new Phase 1 ESA project folder structure."
    )
    parser.add_argument(
        "--name",
        required=True,
        help='Project folder name (e.g., "52-96-Falls-St")',
    )
    parser.add_argument(
        "--address",
        required=True,
        help='Site address (e.g., "52-96 Falls Street, Rochester, NY")',
    )
    args = parser.parse_args()
    init_project(args.name, args.address)


if __name__ == "__main__":
    main()
