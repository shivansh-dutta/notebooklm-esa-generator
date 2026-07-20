"""
notebooklm_pipeline/run.py — CLI entry point for the NotebookLM-driven
Phase 1 ESA report generator.

Usage (run from the repo root, so sibling `agents`/`scripts` packages
import normally — same convention as pipeline.py):

    python -m notebooklm_pipeline --project "631 Northland" \\
        --raw "Projects/631 Northland/Raw/631NorthlandRaw.pdf" \\
        --address "631 Northland Ave, Buffalo, NY"

One-time setup (see notebooklm_pipeline/README.md): `notebooklm login`.

If the question-bank phase fails partway (e.g. a flaky NotebookLM response),
re-run with `--notebook-id <id>` (from `notebooklm list`) to reuse the
already-ingested notebook instead of re-uploading every source.

Orchestrates, in order:
    1. scripts.init_project.init_project() — scaffold the project (skipped
       if the project folder already exists, so a run can be re-attempted
       without clobbering earlier NBLM_Answers/ output)
    2. nblm_client.open_client() + create_notebook()
    3. ingest.run_ingest()          — segment the raw PDF + LegalVault, upload
    4. qa_runner.run_qa()           — run the question bank
    5. assemble.assemble()          — write dashboard / sections / EDR hits
    6. site_visit_guidance.write_site_visit_guidance()
    7. scripts.export_docx.run_export_docx() — final Envicon-template DOCX

Never touches pipeline.py or the Scout/Historian/Researcher/Writer agents —
this is a fully separate CLI/entry path (see notebooklm_pipeline/__init__.py
for why isolation matters here).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from notebooklm_pipeline import assemble, ingest, qa_runner, site_visit_guidance
from notebooklm_pipeline.nblm_client import NblmError, create_notebook, open_client
from scripts import init_project
from scripts.export_docx import run_export_docx

logger = logging.getLogger(__name__)


def _ensure_project(name: str, address: str | None) -> Path:
    project_path = init_project.PROJECTS_DIR / name
    if project_path.exists():
        logger.info("run: project folder %s already exists — reusing it", project_path)
        return project_path
    init_project.init_project(name, address or "TBD")
    return project_path


async def _run_async(args: argparse.Namespace) -> Path:
    project_path = _ensure_project(args.project, args.address)
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
            notebook = await create_notebook(client, args.project)
            notebook_id = notebook.id

            logger.info("run: ingesting %s", raw_pdf_path.name)
            sources = await ingest.run_ingest(client, notebook_id, project_path, raw_pdf_path)
            logger.info("run: uploaded %d source(s)", len(sources))

        logger.info("run: running question bank against the notebook")
        results = await qa_runner.run_qa(client, notebook_id, project_path)

    logger.info("run: assembling export artifacts")
    assemble.assemble(project_path, args.project, results)
    site_visit_guidance.write_site_visit_guidance(project_path, results)

    logger.info("run: exporting DOCX")
    output_path = run_export_docx(project_path)
    return output_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="NotebookLM-driven Phase 1 ESA report generator (isolated sub-project).",
    )
    parser.add_argument("--project", required=True, help='Project folder name, e.g. "631 Northland"')
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
