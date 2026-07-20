---
name: notebooklm-esa-intake
description: Use when the user wants to set up or run the NotebookLM-driven Phase 1 ESA report generator against a project folder that contains raw source files (an EDR radius report, site photos, historical maps, etc). Triggers on requests like "set up a Phase 1 ESA project here", "run the NotebookLM pipeline in this folder", or when Claude Code is spawned directly inside a folder containing exactly one raw source PDF and no code. Clones/updates the notebooklm-esa-generator pipeline into a nested, hands-off subfolder, installs its dependencies, handles NotebookLM authentication, and runs the pipeline so all output lands in the project folder itself.
---

# NotebookLM ESA Intake

This skill drives the `notebooklm-esa-generator` pipeline
(https://github.com/shivansh-dutta/notebooklm-esa-generator) against whatever
project folder Claude Code is currently running in. The project folder holds
the raw source files and, after this runs, the finished draft — the pipeline
tool itself lives in a hidden nested subfolder so nobody has to look at or
touch its code to use it.

## Ground rules

- **Never read the raw source PDF's content directly** — no `Read` tool call
  on it, no passing its pages or extracted text through any Claude/LLM call.
  Segmentation is handled entirely by the pipeline's own local PyMuPDF code
  (deterministic, zero token cost); the actual grounded question-answering
  over the full document is handled by NotebookLM itself, on Google's
  infrastructure. Your job here is orchestration only: run the CLI commands
  below and relay their output.
- Everything this pipeline cannot ground in the uploaded sources is marked
  "» PE TO COMPLETE" (or routed to `Questions_For_User.md`) rather than
  guessed. The final DOCX is always a **draft** requiring full PE review
  before issue — say so plainly when reporting completion.

## Steps

1. **Identify the raw file.** Look for `*.pdf` files directly in the current
   working directory (not recursively, not inside any subfolder).
   - Exactly one found → use it.
   - Zero found → tell the user you don't see a raw PDF in this folder and
     ask them for the path (they may be in the wrong folder, or the file may
     be named unusually).
   - More than one found → list them and ask the user which one is the raw
     Phase 1 package to use. Don't guess.

2. **Set up the pipeline.** In the current working directory:
   - If `.notebooklm-esa-generator/` does **not** exist:
     ```
     git clone https://github.com/shivansh-dutta/notebooklm-esa-generator.git .notebooklm-esa-generator
     ```
   - If it **already** exists (a prior run in this same folder):
     ```
     cd .notebooklm-esa-generator && git pull
     ```
   Either way, then:
   ```
   cd .notebooklm-esa-generator
   uv sync --extra notebooklm --extra dev
   uv run playwright install chromium
   ```
   The `playwright install chromium` step downloads the actual browser
   binary the login flow needs — `uv sync` alone only installs the Python
   package, not the browser, so don't skip it. It's a no-op (fast) if
   already installed. If `git` or `uv` themselves aren't available at all,
   stop and point the user at the **notebooklm-esa-preflight** skill
   instead — this skill assumes those two are already on the machine.

3. **Check NotebookLM authentication.**
   ```
   uv run notebooklm doctor
   ```
   If it reports not authenticated, run:
   ```
   uv run notebooklm login
   ```
   This opens a browser window. Tell the user plainly: "A browser window
   opened for NotebookLM login — please sign in with your Google account."
   Wait for the login to complete (the command blocks until it detects
   success) before continuing. If a browser can't be opened in this
   environment, tell the user and stop — don't try to fake or skip auth.

4. **Run the pipeline**, from inside `.notebooklm-esa-generator/`, pointing
   `--project-dir` at the *parent* folder (the actual project folder, one
   level up) and `--raw` at the PDF identified in step 1:
   ```
   uv run python -m notebooklm_pipeline --project-dir ".." --raw "<absolute path to the raw pdf>"
   ```
   This can take a long time (ingestion + a full question bank against
   NotebookLM) — run it and wait for it to finish rather than assuming it
   hung. If it fails partway through the question-bank phase, `notebooklm
   list` will show the notebook it already created; re-run with
   `--notebook-id <id>` appended to reuse it instead of re-uploading
   everything from scratch.

5. **Report results.** Once done, tell the user:
   - The final DOCX path (`<ProjectName>/Export/*.docx`).
   - Whether `<ProjectName>/Questions_For_User.md` has any unresolved items,
     and what they are.
   - That `<ProjectName>/Site_Visit_Guidance.md` has a field checklist worth
     reviewing before the site visit.
   - That this is a NotebookLM-grounded **draft** — full PE review is
     required before issue (NY Education Law Art. 145).

## Notes for whoever installs this skill

This file needs to exist somewhere Claude Code checks *before* the pipeline
repo is cloned. Two ways to get it there — pick whichever fits how your team
actually works:

1. **User-level** (works in any folder, tied to one person's machine):
   copy this file to `~/.claude/skills/notebooklm-esa-intake/SKILL.md` on
   each machine that will run this workflow.
2. **Project-level** (travels with the project folder itself, no per-machine
   setup): copy this file to `<ProjectName>/.claude/skills/notebooklm-esa-intake/SKILL.md`
   inside every new project folder before opening Claude Code there. See
   `project-template/` in this repo — copy that whole folder (or just its
   `.claude/` subfolder) to start a new project; it already has this file
   seeded in the right place.

Either way, getting the file onto a real shared location (a shared drive,
MDM push, or a manual copy) is outside what this skill can do for itself.
