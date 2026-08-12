"""
notebooklm_pipeline/run.py — CLI entry point for the NotebookLM-driven
Phase 1 ESA report generator.

Two project-location modes (exactly one required):

    --project NAME        Project lives under this repo's own Projects/NAME/
                           (scaffolded fresh if it doesn't exist yet).

    --project-dir PATH    Project IS an arbitrary external folder (e.g. one
                           a teammate created and dropped the raw PDF into
                           before this tool ever ran there). Scaffolding is
                           added in-place, idempotently — nothing already in
                           that folder is overwritten. The display name /
                           NotebookLM notebook title is the folder's own
                           basename. This is what the org-wide Skill uses
                           (see skill/notebooklm-esa-intake/SKILL.md).

Usage:

    python -m notebooklm_pipeline --project "631 Northland" \\
        --raw "Projects/631 Northland/Raw/631NorthlandRaw.pdf" \\
        --address "631 Northland Ave, Buffalo, NY"

    python -m notebooklm_pipeline --project-dir "C:\\Sites\\123 Example St" \\
        --raw "C:\\Sites\\123 Example St\\RawPackage.pdf"

One-time setup (see README.md): `notebooklm login`.

If the question-bank phase fails partway (e.g. a flaky NotebookLM response),
re-run with `--notebook-id <id>` (from `notebooklm list`) to reuse the
already-ingested notebook instead of re-uploading every source.

Orchestrates, in order:
    1. scripts.init_project — scaffold the project, one of two ways per the
       mode above (skipped/idempotent if the project already exists, so a
       run can be re-attempted without clobbering earlier output)
    2. nblm_client.open_client() + create_notebook()
    3. ingest.run_ingest()          — segment the raw PDF + LegalVault, upload
       + source_manifest.write_source_manifest() — record which components
         were actually uploaded (present/absent), for "» MISSING INPUT" vs
         "» PE TO COMPLETE" triage in the finished report
    4. qa_runner.run_qa()           — run the question bank
    5. assemble.assemble()          — write dashboard / sections / EDR hits
    6. site_visit_guidance.write_site_visit_guidance()
    7. review_pass.run_review_pass() — one whole-report sonnet pass: deletes
       residual scaffolding/identity carry-over, flags cross-section
       contradictions to Questions_For_User.md (never rewrites prose)
    8. consistency.run_consistency_checks() — free, deterministic (no LLM)
       cross-section contradiction checks, same Questions_For_User.md
    9. scripts.export_docx.run_export_docx() — final Envicon-template DOCX
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from notebooklm_pipeline import assemble, consistency, ingest, notebook_state, qa_runner, review_pass, site_visit_guidance, source_manifest
from notebooklm_pipeline.nblm_client import NblmError, create_notebook, open_client
from scripts import init_project
from scripts.export_docx import run_export_docx

logger = logging.getLogger(__name__)


def _ensure_project(args: argparse.Namespace) -> tuple[Path, str]:
    """Returns (project_path, display_name). display_name is used as both
    the NotebookLM notebook title and the dashboard's project_name."""
    if args.project_dir:
        project_path = Path(args.project_dir).resolve()
        name = project_path.name
        init_project.ensure_project_scaffold(project_path, name, args.address or "TBD")
        return project_path, name

    project_path = init_project.PROJECTS_DIR / args.project
    if project_path.exists():
        logger.info("run: project folder %s already exists — reusing it", project_path)
    else:
        init_project.init_project(args.project, args.address or "TBD")
    return project_path, args.project


async def _run_async(args: argparse.Namespace) -> Path:
    project_path, display_name = _ensure_project(args)
    raw_pdf_path = Path(args.raw).resolve()
    if not raw_pdf_path.exists():
        raise FileNotFoundError(f"Raw PDF not found: {raw_pdf_path}")

    async with open_client() as client:
        if args.notebook_id:
            # Resume against a notebook that's already fully ingested (e.g.
            # the Q&A phase failed last time) — skips create_notebook() and
            # run_ingest() so re-running never re-uploads sources / never
            # creates a duplicate notebook.
            notebook_id = args.notebook_id
            logger.info("run: reusing existing notebook %s (skipping ingest)", notebook_id)
        else:
            notebook = await create_notebook(client, display_name)
            notebook_id = notebook.id

            logger.info("run: ingesting %s", raw_pdf_path.name)
            sources = await ingest.run_ingest(client, notebook_id, project_path, raw_pdf_path)
            logger.info("run: uploaded %d source(s)", len(sources))
            source_manifest.write_source_manifest(project_path, sources)

        # Persist notebook_id before the question bank runs (the likeliest
        # phase to fail) so a caller can resume against this same notebook
        # via --notebook-id without knowing the ID up front.
        notebook_state.write_notebook_id(project_path, notebook_id)

        logger.info("run: running question bank against the notebook")
        results = await qa_runner.run_qa(client, notebook_id, project_path)

    logger.info("run: assembling export artifacts")
    assemble.assemble(project_path, display_name, results)
    site_visit_guidance.write_site_visit_guidance(project_path, results)

    logger.info("run: running whole-report consistency review pass")
    review_pass.run_review_pass(project_path, results.dashboard)
    consistency.run_consistency_checks(project_path, results.dashboard)

    logger.info("run: exporting DOCX")
    output_path = run_export_docx(project_path)
    return output_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="NotebookLM-driven Phase 1 ESA report generator (isolated sub-project).",
    )
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--project", help='Project folder name under this repo\'s Projects/, e.g. "631 Northland"')
    location.add_argument(
        "--project-dir",
        help="Path to an arbitrary external project folder (scaffolded in-place, "
             "idempotently). Use this for a folder that already exists and may "
             "already contain the raw PDF, e.g. from the org-wide Skill.",
    )
    parser.add_argument("--raw", required=True, help="Path to the raw all-in-one Phase 1 PDF package")
    parser.add_argument(
        "--address", default=None,
        help="Site address, used only to seed a brand-new project folder's dashboard "
             "stub — NotebookLM will also try to find the real address itself",
    )
    parser.add_argument(
        "--notebook-id", default=None,
        help="Resume against an already-ingested notebook ID instead of creating a new "
             "notebook and re-uploading sources (use after a run failed partway through "
             "the question bank; find the ID with `notebooklm list`).",
    )
    args = parser.parse_args()

    try:
        output_path = asyncio.run(_run_async(args))
    except NblmError as exc:
        logger.error("NotebookLM error: %s", exc)
        sys.exit(1)

    print(f"\nDone. Draft exported to: {output_path}")
    print("This is a NotebookLM-grounded DRAFT — full PE review required before issue.")


if __name__ == "__main__":
    main()
