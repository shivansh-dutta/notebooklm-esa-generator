---
name: notebooklm-esa-review
description: Use when the user wants a summary of what still needs manual attention in a finished NotebookLM ESA draft — e.g. "what's left to review in this report", "summarize what the PE needs to check", "is this draft ready for review", or right after notebooklm-esa-intake finishes a run. Scans a project folder's output (Questions_For_User.md, Manual_Review/, the exported DOCX's remaining placeholder markers, Site_Visit_Guidance.md) and produces a single, organized checklist for the reviewing Professional Engineer. Can also generate a shareable HTML fill-in form of the open gaps for a field engineer to complete (published as a claude.ai Artifact link and saved as a local .html anyone can open offline), and fold their returned answers back into the report and DOCX. Never edits the report or makes a judgment call on the reviewer's behalf — filled answers only replace the exact gap they were asked about.
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

## Generating a fill-in form for a field engineer (optional)

If the user wants to hand the open gaps to a field engineer instead of (or
in addition to) reading the summary themselves — e.g. "make this shareable",
"send this to the engineer to fill in", "generate a form for the gaps" —
offer or run this. It never replaces the consolidated summary above; it's an
alternate, fillable presentation of the same gaps (section markers,
dashboard fields, and open contradictions), sourced from the same files.

1. **Generate the form**, from inside `.notebooklm-esa-generator/`:
   ```
   uv run python -m scripts.engineer_form --project-dir "<absolute path to the project folder>"
   ```
   This writes `<ProjectName>/Engineer_Form/Engineer_Fill_Form.html` (the
   shareable form) and `Engineer_Form/gaps.json` (a record of what was
   asked). It never edits `Report_Sections/`, the dashboard, or the DOCX —
   purely a read of the existing gaps into a new form.

2. **Publish the form as a claude.ai Artifact** (use the Artifact tool on
   the generated HTML file) so the user gets a shareable link. Also tell
   them the local file path — the same HTML opens standalone in any browser,
   offline, so it can be emailed to someone without claude.ai access.
   Mention plainly that an Artifact link's reach depends on the workspace's
   own sharing settings; the local `.html` file is the option that reaches
   literally anyone.

3. **When the engineer sends back their filled-in answers** (they click
   "Download answers" in the form, which produces a `*_engineer_answers.json`
   file), apply them:
   ```
   uv run python -m scripts.ingest_engineer_answers --project-dir "<absolute path to the project folder>" --answers "<path to the returned answers json>"
   ```
   This replaces each answered gap's *exact* marker text in
   `Report_Sections/*.md`, updates matching `00_Project_Dashboard.md`
   fields, records the engineer's notes on open contradictions to
   `Questions_For_User.md` under "## Engineer resolutions" (their input is
   never substituted into report prose as if it settled the question — the
   PE still makes that call), and re-exports the DOCX. Report the printed
   summary (filled / updated / recorded / skipped counts) plainly, including
   anything it couldn't apply (e.g. a marker whose text no longer matches
   exactly) so the user knows what still needs manual attention.
