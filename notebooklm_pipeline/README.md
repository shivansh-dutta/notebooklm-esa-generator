# notebooklm_pipeline

An isolated, alternative Phase 1 ESA report generator that uses
[NotebookLM](https://notebooklm.google.com) (via the unofficial
[`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) client) as the
research and vision layer, instead of the main pipeline's page-by-page
`claude` CLI vision calls (`agents/scout.py`, `agents/historian.py`,
`agents/researcher.py`).

## Why a separate sub-project

- `notebooklm-py` wraps **undocumented Google endpoints** — it can break
  without notice if Google changes the NotebookLM frontend. Living in its
  own folder means that breakage never touches the main
  Scout/Historian/Researcher/Writer pipeline (`pipeline.py`), which keeps
  working regardless.
- **Design**: NotebookLM does the heavy lifting — it holds the source PDFs
  and answers grounded, cited questions (including *vision* questions about
  site photos and historical maps, via NotebookLM's own vision model). That
  costs nothing against our Claude token budget. Claude is used only as a
  **thin orchestrator** (`orchestrator.py`) — a handful of small `sonnet`
  calls for follow-up questions on thin answers and repairing malformed
  structured output. It never drafts or extracts in bulk.
- It **reuses** (imports, never modifies) the proven parts of the main
  project: `scripts/init_project.py` (scaffolding), `scripts/segment_pdf.py`
  (appendix segmentation), `scripts/export_docx.py` (the tested Envicon
  template-fill exporter), `scripts/report_constants.py`, and the
  `TemplateVault/` heading text. So the final DOCX comes out through the
  exact same, already-verified code path.

## Setup

```bash
uv pip install -e ".[notebooklm]"     # pulls notebooklm-py[browser] (Playwright + Chromium, ~170MB)
notebooklm login                       # one-time interactive browser login
```

For headless/unattended runs later, `notebooklm-py` also supports a
master-token mode that mints fresh session cookies on demand without a
per-run browser:

```bash
notebooklm login --master-token --account you@example.com
```

Free-tier NotebookLM accounts allow 50 sources per notebook — comfortably
enough after `ingest.py` splits a combined PDF package by appendix (and
further chunks any appendix over ~450,000 words, since NotebookLM caps each
source at 500,000 words / 200MB).

## Usage

Run from the **repo root** (so the sibling `agents`/`scripts` packages
import normally, same convention as `pipeline.py`):

```bash
python -m notebooklm_pipeline \
    --project "631 Northland" \
    --raw "Projects/631 Northland/Raw/631NorthlandRaw.pdf" \
    --address "631 Northland Ave, Buffalo, NY"
```

This scaffolds (or reuses) `Projects/<name>/`, uploads the segmented PDF +
LegalVault reference files to a fresh NotebookLM notebook, runs the
standard question bank, assembles the results into
`00_Project_Dashboard.md` / `Report_Sections/*.md` / `EDR_Database_Hits/`
+ `Manual_Review/`, writes `Site_Visit_Guidance.md`, and exports the final
DOCX to `Projects/<name>/Export/`.

Every raw NotebookLM answer (with citations, when returned) is also saved
to `Projects/<name>/NBLM_Answers/` for audit — check these against the
source PDF before treating the draft as usable.

## What's optional vs required

Per the raw source package structure, the **EDR radius report** is the one
component the pipeline depends on for meaningful output; everything else
(site photos, the environmental questionnaire, historical maps) may be
missing, jumbled, or absent in a real future package. Every question in
`question_bank.py` is phrased to self-report "not found" (via
`scripts.report_constants.pe_marker()`) rather than fail or fabricate when
its source material isn't present — this is deliberate, not a gap.

## Deferred (not in this first cut)

- Pulling historical maps/aerials from the web (a later phase).
- Wiring NotebookLM into the main pipeline — it stays fully separate.
- Headless/CI automation via the master-token flow (documented above, not
  yet exercised end-to-end).

## Guardrails

- **Never** run this against `Projects/52-96-Falls-St/` — it's the guarded
  reference/comparison fixture for the main pipeline. Use a throwaway test
  project name.
- Output is a **draft**. Every artifact this tool cannot ground in the
  uploaded sources is marked with `scripts.report_constants.pe_marker()`
  ("» PE TO COMPLETE"), never guessed — but a NotebookLM-grounded draft
  still requires full PE review before issue (NY Education Law Art. 145).
