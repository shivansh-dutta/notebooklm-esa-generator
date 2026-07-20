---
name: notebooklm-esa-preflight
description: Use when the user wants to check or set up a computer so it's ready to run the NotebookLM ESA generator (notebooklm-esa-intake), before ever opening a project folder — e.g. "is this computer set up for the ESA generator?", "check dependencies for the ESA generator", "set up this machine for the Phase 1 pipeline". Verifies git, uv, and the Playwright Chromium browser are installed, offers to install whichever are missing, and reports whether NotebookLM is already authenticated on this machine. Does not touch any project folder or run the actual pipeline — see notebooklm-esa-intake for that.
---

# NotebookLM ESA Preflight

A standalone readiness check for a machine that will run the
`notebooklm-esa-generator` pipeline (via the `notebooklm-esa-intake` skill).
Run this once per machine, before anyone ever opens a project folder — or
any time someone reports the intake skill failing partway through setup.

This skill does **not** need a project folder, a raw PDF, or a clone of the
pipeline repo to exist yet. It only checks/installs the underlying tools.

## What "ready" means

1. **`git`** — needed to clone the pipeline repo.
2. **`uv`** — needed to manage the pipeline's Python environment and run it.
3. **Playwright's Chromium browser** — needed for the interactive
   `notebooklm login` browser flow. This is a separate download from the
   `notebooklm-py` Python package itself.
4. **NotebookLM authentication** — not strictly required to finish
   preflight (a person can log in during their first real intake run
   instead), but worth reporting so the user knows what's already done.

## Steps

1. **Check `git`.**
   ```
   git --version
   ```
   If missing: tell the user git isn't installed and point them to
   https://git-scm.com/downloads (Windows: winget install --id Git.Git,
   or the installer from that page; macOS: `xcode-select --install` or
   `brew install git`; Linux: the distro's package manager, e.g.
   `sudo apt install git`). Don't attempt a silent/unattended install of
   something this fundamental without the user's go-ahead — confirm first.

2. **Check `uv`.**
   ```
   uv --version
   ```
   If missing, ask the user for permission, then install with the official
   installer for their OS:
   - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

   After installing, the user may need to open a new terminal (or Claude
   Code may need to be restarted) for `uv` to be on `PATH` — check again
   with `uv --version` and say so plainly if it's still not found.

3. **Check the Playwright Chromium browser.** This requires a clone of the
   pipeline repo to check properly (the browser is installed into a cache
   shared across projects, but `playwright install` is invoked through this
   repo's own `uv run`). If a `.notebooklm-esa-generator/` folder already
   exists nearby, use it; otherwise clone a throwaway copy into a temp
   location just for this check:
   ```
   git clone --depth 1 https://github.com/shivansh-dutta/notebooklm-esa-generator.git <temp-dir>
   cd <temp-dir>
   uv sync --extra notebooklm --extra dev
   uv run playwright install chromium
   ```
   This is idempotent and safe to run even if Chromium is already
   installed (it no-ops quickly). If you cloned a throwaway copy just for
   this check, you can leave it in place (it does no harm) or remove it —
   tell the user either way rather than silently deleting something.

4. **Check NotebookLM auth** (from inside whichever clone you used above):
   ```
   uv run notebooklm doctor
   ```
   Report the result plainly — authenticated or not. Don't force a login
   here; that's the intake skill's job when there's an actual project to
   run. Just inform the user so they know what to expect on their first
   real run.

5. **Report a clear summary**: which of the four checks passed, which
   needed action (and what you did), and whether this machine is now fully
   ready to use `notebooklm-esa-intake` end to end.

## Ground rules

- This skill never touches a project folder, never reads a raw PDF, and
  never runs the actual pipeline (`python -m notebooklm_pipeline`) — that's
  entirely the `notebooklm-esa-intake` skill's job.
- Confirm before installing anything system-wide (git, uv) — these are
  bigger asks than anything the intake skill does on its own, since they
  affect the whole machine, not just one project folder.
