# Starting a new Phase 1 ESA project

This folder is a starting point for a new site. It contains nothing but a
`.claude` folder — that's what tells Claude Code how to run the NotebookLM
ESA pipeline automatically when you open it here. There's nothing in here
for you to read through or configure.

## How to use this

1. Copy this whole folder somewhere and rename it to the site's name (e.g.
   `123 Example St`). If you already have a project folder without this in
   it, just copy the `.claude` folder alone into it — that's the only part
   that matters.
2. Drop the raw source PDF (EDR report, photos, whatever the package is)
   directly into that renamed folder.
3. Open Claude Code in that folder and ask it to set up / run the Phase 1
   ESA pipeline. It will:
   - Find the raw PDF automatically.
   - Download the pipeline tool into a hidden subfolder (you won't see it
     unless you go looking — you never need to touch it).
   - The first time on a given machine, a browser window will pop up asking
     you to sign into Google so it can use NotebookLM — sign in and it'll
     continue on its own.
   - Run the whole thing and tell you where the finished draft ended up.

That's it — no GitHub, no command line, no setup beyond dropping the PDF in
and asking Claude Code to run it.

## If it's your first time on this computer

If Claude Code says it can't find `git` or `uv`, or the NotebookLM sign-in
step doesn't work right, ask it to run a "preflight check" first (or just
say something's not working) — it will check what's missing on this
computer and fix it before trying the report again.

## When the report is done

Once the draft is finished, ask Claude Code to "summarize what's left to
review" — it will pull together everything that still needs a person's
attention (missing fields, things flagged for double-checking, and the
site-visit checklist) into one list, instead of you having to go hunting
through several files.

## What you'll get back

- A finished draft Word document.
- A short list of anything it couldn't find in the raw package and needs a
  person to fill in.
- A checklist of specific things to look for and photograph on the actual
  site visit, based on what's in the report.

This is always a **draft** — it still needs full review by a PE before it
goes out.
