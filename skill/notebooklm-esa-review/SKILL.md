---
name: notebooklm-esa-review
description: Use when the user wants a summary of what still needs manual attention in a finished NotebookLM ESA draft — e.g. "what's left to review in this report", "summarize what the PE needs to check", "is this draft ready for review", or right after notebooklm-esa-intake finishes a run. Scans a project folder's output (Questions_For_User.md, Manual_Review/, the exported DOCX's remaining placeholder markers, Site_Visit_Guidance.md) and produces a single, organized checklist for the reviewing Professional Engineer — it never edits the report or makes judgment calls on the reviewer's behalf.
---

# NotebookLM ESA Review Prep

Every draft this pipeline produces still requires full review by a licensed
Professional Engineer before it can be issued (NY Education Law Art. 145).
This skill doesn't do that review — only a human PE can — it just collects
everything the pipeline flagged as unresolved into one clear list, so the
reviewer isn't hunting across five different files to find out what's
actually missing.

Run this in a project folder that has already been through
`notebooklm-esa-intake` (i.e. it has an `Export/*.docx` and the other
artifacts below).

## Ground rules

- **Never decide whether a REC/CREC/HREC classification is correct, never
  rewrite prose, never fill in a PE-TO-COMPLETE field yourself.** Your job
  is purely to locate and summarize what needs a human's attention — not to
  provide it.
- If the project folder doesn't look like it's been through a pipeline run
  yet (no `Export/` folder, or it's empty), say so and stop rather than
  guessing at partial output.

## Steps

1. **Read `Questions_For_User.md`** (project folder root) if it exists.
   These are dashboard fields or report sections NotebookLM could never
   ground in the uploaded sources at all — usually genuine gaps like the
   client name, project number, or a section that failed to draft.

2. **Scan `Manual_Review/*.md`.** Each file here is an EDR database hit
   beyond the pipeline's auto-draft radius — still real, still counted in
   the report's radius tables, but flagged so the PE double-checks its
   classification and relevance personally rather than trusting an
   auto-draft. List the site name, database, and distance for each.

3. **Check the exported DOCX for remaining placeholder markers.** Open
   `Export/*.docx` (there should be exactly one) and search its text for
   the literal string `» PE TO COMPLETE` — every occurrence is a spot the
   PE must fill in by hand before issue (things like signature blocks,
   fixed tables in the template, or fields no automation ever attempts).
   Report a count and, where feasible, which section/heading each one falls
   under (use nearby heading text as context, don't just dump a bare count).

4. **Read `Site_Visit_Guidance.md`** if it exists and confirm it's actually
   populated (not just a stub saying nothing was found) — remind the user
   this is a field checklist to physically verify during the site visit,
   not something to check off from a desk.

5. **Produce one consolidated summary**, organized as:
   - **Must resolve before issue**: Questions_For_User.md items + DOCX
     PE-TO-COMPLETE count/locations.
   - **Needs PE judgment**: Manual_Review hits, with enough detail (site
     name, database, distance) that the PE can decide REC/CREC/HREC/de
     minimis without re-deriving everything from scratch.
   - **For the field visit**: a one-line pointer to Site_Visit_Guidance.md
     if it has real content.
   - A closing line restating this is a NotebookLM-grounded draft requiring
     full PE sign-off — this summary doesn't substitute for that review, it
     only makes it faster to start.
