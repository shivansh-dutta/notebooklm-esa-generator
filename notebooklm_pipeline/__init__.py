"""
notebooklm_pipeline — isolated NotebookLM-driven Phase 1 ESA report generator.

Separate from the main pipeline (agents/, scripts/, pipeline.py) on purpose:
it depends on the unofficial, reverse-engineered `notebooklm-py` client
(browser-automation / undocumented-API auth), so if Google changes the
NotebookLM frontend and that library breaks, the main Scout/Historian/
Researcher/Writer pipeline is completely unaffected and keeps working.

Design: NotebookLM does the heavy lifting — it holds the source PDFs and
answers grounded questions (including vision questions about site photos and
maps), which costs nothing against our Claude token budget. Claude is used
only as a thin orchestrator (see orchestrator.py) for follow-up questions on
thin answers, repairing malformed structured output, and light formatting —
never for bulk drafting or bulk extraction.

Reuses (imports, never modifies) proven pieces of the main project:
    scripts.init_project   — project folder scaffolding + dashboard
    scripts.segment_pdf     — appendix segmentation of the raw PDF
    scripts.export_docx     — the tested Envicon template-fill exporter
    scripts.report_constants — DATABASE_TO_LIST etc.
    TemplateVault/*.md       — authoritative ASTM section heading text

Entry point: `python -m notebooklm_pipeline --project <name> --raw <pdf>`
(see notebooklm_pipeline/run.py). Setup/auth instructions are in
notebooklm_pipeline/README.md.
"""

from __future__ import annotations
