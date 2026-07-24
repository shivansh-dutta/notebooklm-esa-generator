---
name: notebooklm-esa-sync
description: Use when the user wants to update or refresh the NotebookLM ESA generator pipeline and its skills from GitHub — e.g. "update the ESA generator", "pull the latest pipeline code", "sync the drafter with the repo", "is my install of the ESA tool up to date?". Pulls the latest notebooklm-esa-generator commits into the project's nested clone, re-syncs its Python dependencies, and re-copies the current SKILL.md files into the project's .claude/skills/ so the installed skills (including this one) stay current. Never touches project report artifacts and never re-runs the pipeline itself — see notebooklm-esa-intake for that.
---

# NotebookLM ESA Sync

Keeps a project folder's local copy of the `notebooklm-esa-generator` pipeline
(https://github.com/shivansh-dutta/notebooklm-esa-generator) — and the
project's own installed skills — up to date with whatever's on GitHub's
`main` branch. `notebooklm-esa-intake` clones this once per project on first
run, but nothing refreshes it afterward, so a project can silently keep
running old pipeline code and stale skill definitions indefinitely unless
something like this is run occasionally.

This is a **pull-only, read-from-GitHub** operation: it never writes to the
repo, never touches `Report_Sections/`, `Export/`, `Questions_For_User.md`,
or any other project report artifact, and never re-runs the pipeline itself.

## Ground rules

- **Never `git push`.** This skill only ever pulls.
- **Never touch project artifacts** — no report section, dashboard, export,
  or `Questions_For_User.md` file is read or written by this skill.
- **Never re-run the pipeline** (`python -m notebooklm_pipeline`) — that's
  `notebooklm-esa-intake`'s job, on demand, separately.
- If the local clone has uncommitted changes (shouldn't normally happen for a
  pipeline clone nobody edits directly, but check), stop and tell the user
  rather than pulling over them.

## Steps

1. **Locate the clone.** Look for `.notebooklm-esa-generator/` in the current
   working directory (the convention `notebooklm-esa-intake` uses). If it
   isn't there, tell the user this project hasn't been set up with the ESA
   generator yet and point them at `notebooklm-esa-intake` instead of
   improvising a fresh clone here.

2. **Check for local changes**, from inside `.notebooklm-esa-generator/`:
   ```
   git status --porcelain
   ```
   If this prints anything, stop and tell the user the clone has local
   modifications that would be affected by a pull — let them decide how to
   handle it rather than pulling blindly.

3. **Pull the latest commits:**
   ```
   git fetch origin
   git log --oneline HEAD..origin/main
   git pull --ff-only origin main
   ```
   Report the old and new commit hashes and the one-line log of what came in
   (or "already up to date" if nothing did). Use `--ff-only` deliberately —
   if it fails (local history diverged), stop and report that rather than
   forcing a merge or rebase.

4. **Re-sync dependencies**, in case `pyproject.toml`/`uv.lock` changed:
   ```
   uv sync --extra notebooklm --extra dev
   uv run playwright install chromium
   ```
   Both are safe no-ops if nothing changed.

5. **Re-copy the current skills into this project.** From the just-updated
   clone, copy every `skill/<name>/SKILL.md` over the project's own
   `.claude/skills/<name>/SKILL.md` (the project-level install path
   `notebooklm-esa-intake` documents). This keeps `notebooklm-esa-intake`,
   `notebooklm-esa-preflight`, `notebooklm-esa-review`, and this sync skill
   itself current — including this run, if the pulled commit changed sync
   itself, later invocations pick up the new behavior.

6. **Report a clear summary**: whether an update was pulled (and what
   changed, in one line per commit), whether dependencies needed re-syncing,
   and which skill files were refreshed. If everything was already current,
   say so plainly rather than padding out a report.
