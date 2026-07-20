# notebooklm-esa-generator

A Phase 1 Environmental Site Assessment (ESA) report generator that uses
[NotebookLM](https://notebooklm.google.com) (via the unofficial
[`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) client) as the
research and vision layer — grounded question answering over the raw source
PDFs, including *vision* questions about site photos and historical maps via
NotebookLM's own vision model.

This repo is a **standalone extraction** of the `notebooklm_pipeline/`
sub-project originally built inside the main `Phase 1 Report Generator`
project. It's a frozen snapshot: `agents/` and `scripts/` here are vendored
copies of the modules `notebooklm_pipeline` depends on (report constants,
the DOCX exporter, PDF appendix segmentation, project scaffolding), not a
live link back to the original repo. Future improvements made there won't
automatically appear here.

## Design

NotebookLM does the heavy lifting — it holds the source PDFs and answers
grounded, cited questions directly. That costs nothing against any LLM
token budget. Claude is used only as a **thin orchestrator**
(`notebooklm_pipeline/orchestrator.py`) — a handful of small `sonnet` calls
for follow-up questions on thin answers and repairing malformed structured
output. It never drafts or extracts in bulk.

Notable robustness details baked in from real runs against a live,
EDR-heavy urban site:
- `nblm_client.ask()` retries on NotebookLM's occasional truncated/empty
  streaming responses (a known rough edge of the unofficial library).
- Section 5.0 (Records Review) — which has to classify every EDR database
  hit as REC/CREC/HREC/de minimis, potentially dozens of records — is split
  into two separate asks (Federal records vs. State/Tribal/Local records)
  and stitched back together, since one combined response was large enough
  to break the streaming decoder outright, independent of retries.
- Any question that still fails after retries is routed into a project's
  `Questions_For_User.md` with a PE-completion marker rather than aborting
  the whole run.

## Setup

```bash
uv sync --extra notebooklm --extra dev   # creates this repo's own .venv
                                          # pulls notebooklm-py[browser] (Playwright + Chromium, ~170MB)
uv run notebooklm login                  # one-time interactive browser login
```

For headless/unattended runs later, `notebooklm-py` also supports a
master-token mode that mints fresh session cookies on demand without a
per-run browser:

```bash
uv run notebooklm login --master-token --account you@example.com
```

Free-tier NotebookLM accounts allow 50 sources per notebook — comfortably
enough after `ingest.py` splits a combined PDF package by appendix (and
further chunks any appendix over ~450,000 words, since NotebookLM caps each
source at 500,000 words / 200MB).

## Usage

Run from this repo's root:

```bash
uv run python -m notebooklm_pipeline \
    --project "My Test Site" \
    --raw "path/to/RawPhase1Package.pdf" \
    --address "123 Example St, Buffalo, NY"
```

This scaffolds (or reuses) `Projects/<name>/`, uploads the segmented PDF +
LegalVault reference files to a fresh NotebookLM notebook, runs the
standard question bank, assembles the results into
`00_Project_Dashboard.md` / `Report_Sections/*.md` / `EDR_Database_Hits/`
+ `Manual_Review/`, writes `Site_Visit_Guidance.md`, and exports the final
DOCX to `Projects/<name>/Export/`.

If the question-bank phase fails partway (e.g. a flaky NotebookLM
response), re-run with `--notebook-id <id>` (find it with `uv run
notebooklm list`) to reuse the already-ingested notebook instead of
re-uploading every source — avoids duplicate notebooks/uploads.

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
- Headless/CI automation via the master-token flow (documented above, not
  yet exercised end-to-end).

## Guardrails

- Output is a **draft**. Every field this tool cannot ground in the
  uploaded sources is marked with `scripts.report_constants.pe_marker()`
  ("» PE TO COMPLETE"), never guessed — but a NotebookLM-grounded draft
  still requires full PE review before issue (NY Education Law Art. 145).
- This wraps an **unofficial, reverse-engineered** client
  (`notebooklm-py`) talking to undocumented Google endpoints. It can break
  without notice if Google changes the NotebookLM frontend — that's the
  whole reason this lives in its own repo, separate from any other
  pipeline.
