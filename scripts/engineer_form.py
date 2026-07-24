"""
scripts/engineer_form.py — build a shareable HTML fill-in form from a
project's still-open gaps, for the review skill (notebooklm-esa-review) to
hand to a field engineer.

Three kinds of gap become one form field each:

    section_marker  — a "» PE TO COMPLETE: ..." / "» MISSING INPUT: ..."
                       marker found inline in Report_Sections/*.md. The
                       engineer's answer round-trips back by an EXACT
                       substring replace of the marker text (see
                       ingest_engineer_answers.py) — never a fuzzy match,
                       never a guess.
    dashboard_field  — a "Dashboard field 'X' not found in sources." bullet
                       from Questions_For_User.md (written by
                       notebooklm_pipeline.orchestrator.route_unknowns).
                       Round-trips into 00_Project_Dashboard.md.
    decision         — every other bullet in Questions_For_User.md: section
                       draft failures and the whole-report review's
                       contradiction findings (notebooklm_pipeline.review_pass
                       / consistency.py). These are never resolved
                       automatically — the engineer's note is only ever
                       *recorded* (see ingest_engineer_answers.py), never
                       substituted into report prose.

This module only reads the project folder and writes the form + its JSON
model — it never edits Report_Sections/, the dashboard, or the DOCX. That
happens later, in ingest_engineer_answers.py, once the engineer's answers
come back.

Public interface:
    Gap                          — one fillable/recordable item
    collect_gaps(project_path)   -> list[Gap]
    build_form_html(gaps, project_name) -> str
    write_engineer_form(project_path)   -> tuple[Path, Path]  (html, json)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from scripts.export_docx import load_dashboard_meta
from scripts.report_constants import MISSING_INPUT_MARKER, PE_MARKER

logger = logging.getLogger(__name__)

# Matches either marker, optionally followed by ": <description>". The
# description stops at the next marker, a newline, or an em dash (—) — the
# em dash boundary matters for the one place two markers share a sentence
# (notebooklm_pipeline.assemble.build_qualifications_markdown's Section 11.0:
# "...Professional(s) — » PE TO COMPLETE: environmental professional name,
# » PE TO COMPLETE: EP professional title — declare that..."). Without it,
# the second marker's captured span would swallow the real "declare that…"
# sentence that follows it, and filling that gap would silently delete real
# report content instead of just the gap — stopping at "—" keeps the
# round-trip an exact, content-preserving substitution. Markdown bold (**)
# is a second, real-world case of the same problem: assemble.py's generated
# Section 1.0 Introduction wraps each marker in its own bold span —
# "conducted on **» PE TO COMPLETE: site reconnaissance date** by **» PE TO
# COMPLETE: Environmental Professional name**." — so a description that ran
# up to the next marker/newline/em-dash would still swallow the closing
# "**" and the real "by **" text between the two markers. A char class can
# only exclude single characters, so the two-character "**" boundary uses a
# negative-lookahead stop instead: consume any character not starting one of
# the marker/newline/em-dash/double-asterisk boundaries.
_MARKER_RE = re.compile(
    rf"(?:{re.escape(PE_MARKER)}|{re.escape(MISSING_INPUT_MARKER)})(?::(?:(?!»|—|\*\*|\n).)*)?"
)
_DASHBOARD_FIELD_RE = re.compile(r"^Dashboard field '([\w]+)' not found in sources\.$")

# Questions_For_User.md's "decision" bullets are written for a PE who already
# has the whole project folder open — they reference internal filenames
# ("05_Records_Review.md") and, for a failed NotebookLM call, raw client
# internals ("chat.ask failed", "No parseable chunks in streaming chat
# response"). None of that means anything to an engineer looking at just
# this form, so it's rewritten into plain language before it's ever shown.
_NOTEBOOKLM_FAILURE_RE = re.compile(r"^(Section [\d.]+ \([^)]+\)) — NotebookLM request failed:")
_HISTORICAL_TABLE_FAILURE_RE = re.compile(r"^Historical table '([^']+)' — answer was not valid JSON")
_NUMBERED_FILE_RE = re.compile(r"\b(\d{1,2})_([A-Za-z]+(?:_[A-Za-z]+)*)\.md\b")
_BARE_FILE_RE = re.compile(r"\b([A-Za-z][A-Za-z]*(?:_[A-Za-z]+)*)\.md\b")


def _numbered_file_repl(m: re.Match) -> str:
    number, rest = m.groups()
    return f"Section {int(number)}.0 ({rest.replace('_', ' ')})"


def _bare_file_repl(m: re.Match) -> str:
    return m.group(1).replace("_", " ")


def _humanize_filenames(text: str) -> str:
    """Replace internal 'NN_Section_Name.md' / 'Executive_Summary.md' style
    filenames with the plain section label a report reader would recognize."""
    text = _NUMBERED_FILE_RE.sub(_numbered_file_repl, text)
    return _BARE_FILE_RE.sub(_bare_file_repl, text)


def _humanize_decision_prompt(bullet: str) -> str:
    m = _NOTEBOOKLM_FAILURE_RE.match(bullet)
    if m:
        return f"{m.group(1)} could not be auto-drafted — this section still needs to be written from scratch."
    m = _HISTORICAL_TABLE_FAILURE_RE.match(bullet)
    if m:
        return (
            f"The {m.group(1).replace('_', ' ')} historical-records table couldn't be filled in "
            "automatically — please fill it in from the source documents."
        )
    return _humanize_filenames(bullet)


@dataclass
class Gap:
    id: str
    kind: str  # "section_marker" | "dashboard_field" | "decision"
    section: str  # human-readable grouping label
    prompt: str  # what to show/ask the engineer
    marker_kind: str | None = None  # "PE TO COMPLETE" | "MISSING INPUT" (section_marker only)
    file: str | None = None  # Report_Sections/<file> (section_marker only)
    match: str | None = None  # exact substring to replace (section_marker only)
    field_name: str | None = None  # 00_Project_Dashboard.md key (dashboard_field only)


def _make_id(prefix: str, index: int, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{index:03d}-{digest}"


def _section_label(filename: str) -> str:
    stem = filename[:-3] if filename.endswith(".md") else filename
    return stem.replace("_", " ").strip()


def _collect_section_marker_gaps(project_path: Path) -> list[Gap]:
    sections_dir = Path(project_path) / "Report_Sections"
    if not sections_dir.exists():
        return []
    gaps: list[Gap] = []
    idx = 0
    for md_path in sorted(sections_dir.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _MARKER_RE.finditer(text):
            raw = m.group(0)
            marker_kind = "MISSING INPUT" if raw.startswith(MISSING_INPUT_MARKER) else "PE TO COMPLETE"
            colon_idx = raw.find(":")
            description = raw[colon_idx + 1:].strip() if colon_idx != -1 else ""
            idx += 1
            gaps.append(
                Gap(
                    id=_make_id("sec", idx, md_path.name + raw),
                    kind="section_marker",
                    section=_section_label(md_path.name),
                    prompt=description or f"({marker_kind} — no further description)",
                    marker_kind=marker_kind,
                    file=md_path.name,
                    match=raw,
                )
            )
    return gaps


def _parse_questions_for_user(project_path: Path) -> list[Gap]:
    qfu_path = Path(project_path) / "Questions_For_User.md"
    if not qfu_path.exists():
        return []
    try:
        text = qfu_path.read_text(encoding="utf-8")
    except OSError:
        return []

    gaps: list[Gap] = []
    current_section = "Unresolved items"
    idx = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if not bullet:
            continue

        m = _DASHBOARD_FIELD_RE.match(bullet)
        idx += 1
        if m:
            field_name = m.group(1)
            label = field_name.replace("_", " ")
            gaps.append(
                Gap(
                    id=_make_id("dash", idx, field_name),
                    kind="dashboard_field",
                    section="Project Dashboard",
                    prompt=f"What is the {label}?",
                    field_name=field_name,
                )
            )
            continue

        gaps.append(
            Gap(
                id=_make_id("dec", idx, bullet),
                kind="decision",
                section=current_section,
                prompt=_humanize_decision_prompt(bullet),
            )
        )
    return gaps


def collect_gaps(project_path: Path) -> list[Gap]:
    """Every fillable/recordable gap in *project_path*, in a stable order:
    section markers grouped by report section, then dashboard fields, then
    decisions/contradictions — matching the order an engineer would want to
    work through them (quick fills first, judgment calls last)."""
    return _collect_section_marker_gaps(project_path) + _parse_questions_for_user(project_path)


# ---------------------------------------------------------------------------
# HTML form
# ---------------------------------------------------------------------------

_CHIP_LABEL = {
    "PE TO COMPLETE": "PE TO COMPLETE",
    "MISSING INPUT": "MISSING INPUT",
}


def _card_html(gap: Gap) -> str:
    chip = ""
    if gap.kind == "section_marker":
        chip_class = "chip-warn" if gap.marker_kind == "MISSING INPUT" else "chip-accent"
        chip = f'<span class="chip {chip_class}">{html.escape(gap.marker_kind or "")}</span>'
    elif gap.kind == "dashboard_field":
        chip = '<span class="chip chip-accent">DASHBOARD</span>'

    # Cards are already grouped under an <h2>{section}</h2> heading, and the
    # raw filename (e.g. "05_Records_Review.md") is exactly the kind of
    # internal detail an engineer reading just this form shouldn't need to
    # parse — so the tag only repeats the section label for decision cards,
    # which aren't grouped under their own per-section heading below.
    tag = html.escape(gap.section) if gap.kind == "decision" else ""

    return f"""
    <article class="gap-card" data-id="{html.escape(gap.id)}">
      <div class="gap-meta">
        <span class="gap-tag">{tag}</span>
        {chip}
      </div>
      <p class="gap-prompt">{html.escape(gap.prompt)}</p>
      <textarea class="gap-answer" id="answer-{html.escape(gap.id)}"
        data-id="{html.escape(gap.id)}" rows="3"
        placeholder="Type the answer here…"></textarea>
    </article>"""


def build_form_html(gaps: list[Gap], project_name: str) -> str:
    """One self-contained HTML document — no external requests of any kind
    (fonts, scripts, images), so it works as a claude.ai Artifact and as a
    plain double-click-to-open local file equally. Groups section_marker /
    dashboard_field gaps as fillable cards, and decision gaps in a visually
    distinct "Decisions needed" block (never auto-resolved, only recorded)."""
    fillable = [g for g in gaps if g.kind in ("section_marker", "dashboard_field")]
    decisions = [g for g in gaps if g.kind == "decision"]

    # Group fillable gaps by section, preserving first-seen order.
    sections: dict[str, list[Gap]] = {}
    for g in fillable:
        sections.setdefault(g.section, []).append(g)

    fillable_html = "\n".join(
        f'<section class="gap-group"><h2>{html.escape(section)}</h2>'
        + "".join(_card_html(g) for g in items)
        + "</section>"
        for section, items in sections.items()
    )
    decisions_html = "".join(_card_html(g) for g in decisions)

    total = len(fillable) + len(decisions)
    gaps_json = json.dumps([asdict(g) for g in gaps])
    project_label = html.escape(project_name)

    if total == 0:
        body = '<p class="empty-state">No open gaps found — nothing for the engineer to fill in right now.</p>'
    else:
        decisions_section = (
            '<section class="gap-group gap-group-decide">'
            "<h2>Things that don't add up</h2>"
            "<p class=\"group-intro\">The draft says two different things in two places below. "
            "Read both and add a note on what's actually correct (or what more detail would settle "
            "it) — this goes to the reviewing engineer as a note, it won't change the report text by "
            "itself.</p>"
            + decisions_html
            + "</section>"
            if decisions
            else ""
        )
        body = f"""
    {fillable_html}
    {decisions_section}"""

    return f"""<title>Engineer Fill-In Form — {project_label}</title>
<style>
:root {{
  --ground: #f6f8f8;
  --surface: #ffffff;
  --ink: #1b2a2b;
  --muted: #5c6f70;
  --line: #dde5e4;
  --accent: #1f6f5c;
  --accent-ink: #ffffff;
  --warn: #b5761d;
  --decide: #9a3b34;
  --decide-ground: #fbeeec;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --ground: #10181a;
    --surface: #172224;
    --ink: #e7efee;
    --muted: #9fb3b2;
    --line: #2a3a3b;
    --accent: #4fb99e;
    --accent-ink: #04211b;
    --warn: #e0a640;
    --decide: #e08a82;
    --decide-ground: #2a1a19;
  }}
}}
:root[data-theme="dark"] {{
  --ground: #10181a; --surface: #172224; --ink: #e7efee; --muted: #9fb3b2;
  --line: #2a3a3b; --accent: #4fb99e; --accent-ink: #04211b; --warn: #e0a640;
  --decide: #e08a82; --decide-ground: #2a1a19;
}}
:root[data-theme="light"] {{
  --ground: #f6f8f8; --surface: #ffffff; --ink: #1b2a2b; --muted: #5c6f70;
  --line: #dde5e4; --accent: #1f6f5c; --accent-ink: #ffffff; --warn: #b5761d;
  --decide: #9a3b34; --decide-ground: #fbeeec;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--ground); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.5;
}}
.page {{ max-width: 760px; margin: 0 auto; padding: 0 20px 96px; }}
header.top {{
  position: sticky; top: 0; z-index: 5; background: var(--ground);
  padding: 20px 0 14px; border-bottom: 1px solid var(--line);
  display: flex; flex-direction: column; gap: 4px;
}}
header.top .eyebrow {{
  font-family: ui-monospace, Consolas, "SFMono-Regular", monospace;
  font-size: 0.75rem; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted);
}}
header.top h1 {{ margin: 0; font-size: 1.4rem; text-wrap: balance; }}
#progress {{ font-size: 0.9rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
.gap-group {{ margin-top: 28px; }}
.gap-group h2 {{
  font-size: 1rem; margin: 0 0 10px; color: var(--muted);
  font-weight: 600; letter-spacing: 0.01em;
}}
.gap-group-decide h2 {{ color: var(--decide); }}
.group-intro {{ color: var(--muted); margin: 0 0 14px; max-width: 62ch; }}
.gap-card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; margin-bottom: 10px;
}}
.gap-group-decide .gap-card {{ border-color: var(--decide); background: var(--decide-ground); }}
.gap-meta {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
.gap-tag {{
  font-family: ui-monospace, Consolas, "SFMono-Regular", monospace;
  font-size: 0.78rem; color: var(--muted);
}}
.chip {{
  font-family: ui-monospace, Consolas, "SFMono-Regular", monospace;
  font-size: 0.68rem; letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px;
  color: var(--accent-ink); background: var(--accent);
}}
.chip-warn {{ background: var(--warn); }}
.gap-prompt {{ margin: 0 0 10px; }}
.gap-answer {{
  width: 100%; border: 1px solid var(--line); border-radius: 8px; background: var(--ground);
  color: var(--ink); font: inherit; padding: 10px; resize: vertical;
}}
.gap-answer:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.empty-state {{ color: var(--muted); margin-top: 28px; }}
footer.bottom {{
  position: fixed; left: 0; right: 0; bottom: 0; background: var(--surface);
  border-top: 1px solid var(--line); padding: 12px 20px;
  display: flex; gap: 10px; justify-content: center; flex-wrap: wrap;
}}
button {{
  font: inherit; border: 1px solid var(--accent); background: var(--accent);
  color: var(--accent-ink); padding: 9px 16px; border-radius: 8px; cursor: pointer;
}}
button.secondary {{ background: transparent; color: var(--accent); }}
button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
#copy-status {{ font-size: 0.85rem; color: var(--muted); align-self: center; }}
.submit-panel {{
  margin: 28px 0 20px; padding: 16px; border: 1px solid var(--accent); border-radius: 10px;
  background: var(--surface);
}}
.submit-panel h2 {{ margin: 0 0 8px; font-size: 1.05rem; }}
.submit-panel p {{ color: var(--muted); margin: 0 0 12px; max-width: 62ch; }}
#submit-output {{
  width: 100%; min-height: 160px; border: 1px solid var(--line); border-radius: 8px;
  background: var(--ground); color: var(--ink);
  font-family: ui-monospace, Consolas, "SFMono-Regular", monospace; font-size: 0.8rem;
  padding: 10px; resize: vertical;
}}
.submit-actions {{ display: flex; gap: 10px; align-items: center; margin-top: 10px; }}
@media (prefers-reduced-motion: no-preference) {{
  .gap-card {{ transition: border-color 0.15s ease; }}
}}
</style>
<div class="page">
  <header class="top">
    <span class="eyebrow">Phase 1 ESA — Engineer Fill-In Form</span>
    <h1>{project_label}</h1>
    <span id="progress"></span>
  </header>
  {body}
  <section id="submit-panel" class="submit-panel" hidden>
    <h2>Your answers are ready</h2>
    <p>Copy the text below and paste it into your chat with Claude (something like "here are the
      engineer's answers" followed by a paste) — Claude will apply them to the report and rebuild
      the Word document. There's no separate upload step; pasting this back is the submission.</p>
    <textarea id="submit-output" readonly rows="10"></textarea>
    <div class="submit-actions">
      <button id="copy-btn">Copy to clipboard</button>
      <span id="copy-status"></span>
    </div>
  </section>
</div>
<footer class="bottom">
  <button id="submit-btn">Submit answers</button>
  <button class="secondary" id="download-btn">Download as a file instead</button>
</footer>
<script id="gaps-data" type="application/json">{gaps_json}</script>
<script>
(function () {{
  var PROJECT = {json.dumps(project_name)};
  var STORAGE_KEY = "esa-engineer-form:" + PROJECT;
  var gaps = JSON.parse(document.getElementById("gaps-data").textContent);
  var textareas = Array.prototype.slice.call(document.querySelectorAll(".gap-answer"));

  function safeGetStorage() {{
    try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}"); }}
    catch (e) {{ return {{}}; }}
  }}
  function safeSetStorage(data) {{
    try {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); }}
    catch (e) {{ /* sandboxed / storage unavailable — form still works, just no autosave */ }}
  }}

  var saved = safeGetStorage();
  textareas.forEach(function (ta) {{
    if (saved[ta.dataset.id]) ta.value = saved[ta.dataset.id];
  }});

  function updateProgress() {{
    var answered = textareas.filter(function (ta) {{ return ta.value.trim().length > 0; }}).length;
    document.getElementById("progress").textContent =
      answered + " of " + textareas.length + " answered";
  }}
  function persist() {{
    var data = {{}};
    textareas.forEach(function (ta) {{ data[ta.dataset.id] = ta.value; }});
    safeSetStorage(data);
    updateProgress();
  }}
  textareas.forEach(function (ta) {{ ta.addEventListener("input", persist); }});
  updateProgress();

  function collectAnswers() {{
    // Only gaps the engineer actually answered — an ingest that skips blanks
    // anyway, and a pasted-into-chat payload, both read better without a
    // long tail of empty entries.
    var byId = {{}};
    textareas.forEach(function (ta) {{ byId[ta.dataset.id] = ta.value.trim(); }});
    return gaps
      .map(function (g) {{
        var copy = Object.assign({{}}, g);
        copy.answer = byId[g.id] || "";
        return copy;
      }})
      .filter(function (g) {{ return g.answer.length > 0; }});
  }}

  function scrollToPanel(panel) {{
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    panel.scrollIntoView({{ behavior: reduceMotion ? "auto" : "smooth", block: "start" }});
  }}

  document.getElementById("submit-btn").addEventListener("click", function () {{
    var answers = collectAnswers();
    var panel = document.getElementById("submit-panel");
    var output = document.getElementById("submit-output");
    var status = document.getElementById("copy-status");
    panel.hidden = false;
    if (answers.length === 0) {{
      output.value = "";
      status.textContent = "Nothing answered yet — fill in at least one field above, then submit again.";
    }} else {{
      output.value = JSON.stringify(answers, null, 2);
      status.textContent = "";
    }}
    scrollToPanel(panel);
  }});

  document.getElementById("download-btn").addEventListener("click", function () {{
    var blob = new Blob([JSON.stringify(collectAnswers(), null, 2)], {{ type: "application/json" }});
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = PROJECT.replace(/[^A-Za-z0-9_-]+/g, "_") + "_engineer_answers.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }});

  document.getElementById("copy-btn").addEventListener("click", function () {{
    var output = document.getElementById("submit-output");
    var status = document.getElementById("copy-status");
    if (!output.value) {{
      status.textContent = "Nothing to copy yet — submit first.";
      return;
    }}
    function ok() {{ status.textContent = "Copied — paste it into your chat with Claude."; }}
    function fail() {{
      output.removeAttribute("readonly");
      output.focus();
      output.select();
      status.textContent = "Couldn't copy automatically — the text is selected, press Ctrl/Cmd+C.";
    }}
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(output.value).then(ok, fail);
    }} else {{
      fail();
    }}
  }});
}})();
</script>"""


def write_engineer_form(project_path: Path) -> tuple[Path, Path]:
    """Write <project>/Engineer_Form/Engineer_Fill_Form.html (the shareable
    form) and Engineer_Form/gaps.json (the same gap list, pre-answer — a
    record of what was asked, independent of whatever the engineer later
    sends back). Returns (html_path, json_path)."""
    project_path = Path(project_path).resolve()
    dashboard_meta = load_dashboard_meta(project_path)
    project_name = dashboard_meta.get("project_name") or project_path.name

    gaps = collect_gaps(project_path)
    out_dir = project_path / "Engineer_Form"
    out_dir.mkdir(exist_ok=True)

    html_path = out_dir / "Engineer_Fill_Form.html"
    html_path.write_text(build_form_html(gaps, project_name), encoding="utf-8")

    json_path = out_dir / "gaps.json"
    json_path.write_text(json.dumps([asdict(g) for g in gaps], indent=2), encoding="utf-8")

    logger.info("engineer_form: wrote %d gap(s) to %s and %s", len(gaps), html_path, json_path)
    return html_path, json_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Build a shareable engineer fill-in form from a project's open gaps.",
    )
    parser.add_argument("--project-dir", required=True, help="Path to the project folder")
    args = parser.parse_args()

    html_path, json_path = write_engineer_form(Path(args.project_dir))
    print(f"Form:  {html_path}")
    print(f"Model: {json_path}")


if __name__ == "__main__":
    main()
