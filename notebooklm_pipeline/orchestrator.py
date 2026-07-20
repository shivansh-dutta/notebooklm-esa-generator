"""
notebooklm_pipeline/orchestrator.py — the ONLY Claude usage in this
sub-project, kept deliberately minimal per the user's explicit instruction:
NotebookLM does the research and drafting; Claude is a thin orchestrator,
token cost kept to the floor.

Three narrow jobs, each a single small `claude` CLI call (via the existing
agents.claude_cli.run_claude, reused unmodified apart from its new optional
`model` parameter — see agents/claude_cli.py) on `sonnet` rather than the
main pipeline's `haiku`, since these calls require more judgment than raw
extraction:

  1. is_thin_answer() / build_followup_question() — when a NotebookLM
     section answer looks too short or too full of "not found" markers to
     be a real draft, ask Claude to write ONE targeted follow-up question
     (never re-send the whole template or any source text — just the
     original question + the thin answer, a few hundred tokens at most).
  2. repair_edr_json() — when a NotebookLM EDR-enumeration answer isn't
     valid JSON (stray prose, an unescaped quote, a trailing comma), ask
     Claude to extract/repair it into the exact schema — again, only the
     malformed answer text is sent, never source documents.
  3. route_unknowns() — collect items neither NotebookLM nor the two calls
     above could resolve into Questions_For_User.md, formatted plainly.

Every call here is intentionally tiny (a single answer string in, a single
answer string out) — this module must never become a second drafting path.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agents.claude_cli import run_claude
from scripts.report_constants import PE_MARKER

logger = logging.getLogger(__name__)

ORCHESTRATOR_MODEL = "sonnet"

# Heuristic thresholds for "this NotebookLM answer looks thin" — deliberately
# simple (length + PE_MARKER density) rather than another LLM call, so most
# answers never trigger the (paid) follow-up path at all.
_MIN_ANSWER_CHARS = 120
_MAX_PE_MARKER_RATIO = 0.5  # PE_MARKER occurrences per 200 chars of answer


def is_thin_answer(answer: str) -> bool:
    """True if *answer* looks too short or too marker-heavy to be a usable
    draft — triggers a single targeted follow-up question rather than
    accepting a near-empty section."""
    stripped = answer.strip()
    if len(stripped) < _MIN_ANSWER_CHARS:
        return True
    marker_count = stripped.count(PE_MARKER)
    density = marker_count / max(len(stripped) / 200, 1)
    return density > _MAX_PE_MARKER_RATIO


def build_followup_question(original_question: str, thin_answer: str, section_name: str) -> str | None:
    """
    Ask Claude (sonnet, one small call) to write ONE better-targeted
    follow-up question for NotebookLM, given that the original question's
    answer came back thin. Returns None if the CLI call fails (caller
    should just keep the original thin answer rather than blocking).
    """
    prompt = f"""A research assistant (NotebookLM) was asked a question about a Phase 1
Environmental Site Assessment section ("{section_name}") and gave a thin or
mostly-unfound answer. Write ONE better, more specific follow-up question
that might surface the missing information from the same source documents
(e.g. narrowing scope, asking for a specific fact instead of a whole
section, or asking it to check a specific appendix). Output ONLY the
follow-up question text, nothing else — no preamble, no quotes around it.

Original question:
{original_question[:1500]}

Thin answer received:
{thin_answer[:800]}
"""
    result = run_claude(prompt, model=ORCHESTRATOR_MODEL)
    if result.returncode != 0:
        logger.warning("orchestrator: follow-up generation failed for %s (exit %d)", section_name, result.returncode)
        return None
    followup = result.stdout.strip()
    return followup or None


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def try_parse_json_array(text: str) -> list[dict] | None:
    """Best-effort direct JSON parse, tolerant of a surrounding ```json
    fence or stray prose around the array. Returns None if unparseable."""
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_ARRAY_RE.search(stripped)
    if m:
        try:
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            pass
    return None


def repair_edr_json(database_source: str, raw_answer: str) -> list[dict] | None:
    """
    When try_parse_json_array() fails on a NotebookLM EDR-enumeration
    answer, ask Claude (sonnet, one small call) to repair it into a valid
    JSON array matching the same schema. Only the malformed text is sent —
    never the source PDFs. Returns None (not []) if repair also fails, so
    callers can distinguish "genuinely zero hits" from "unparseable, needs
    human review."
    """
    prompt = f"""The following text was supposed to be a JSON array of environmental
database hit records (keys: site_name, address, distance_ft, direction,
program_id, status, nysdec_program, preliminary_classification) but is not
valid JSON. Extract whatever real records you can find in it and output ONLY
a corrected, valid JSON array with exactly those keys per element (use ""
or null for any field you cannot determine — do not invent data). If the
text describes zero records, output exactly: []. Output ONLY the JSON array,
no other text, no code fence.

Malformed text (database: {database_source}):
{raw_answer[:4000]}
"""
    result = run_claude(prompt, model=ORCHESTRATOR_MODEL)
    if result.returncode != 0:
        logger.warning("orchestrator: JSON repair failed for %s (exit %d)", database_source, result.returncode)
        return None
    return try_parse_json_array(result.stdout)


def route_unknowns(project_path: Path, items: list[str]) -> Path | None:
    """Write every unresolved item to <project>/Questions_For_User.md.
    Returns the path, or None if there was nothing to write."""
    if not items:
        return None
    path = Path(project_path) / "Questions_For_User.md"
    lines = [
        "# Questions For User",
        "",
        "Items NotebookLM (and the follow-up/repair passes) could not "
        "resolve from the uploaded sources. Review before PE sign-off.",
        "",
    ]
    lines += [f"- {item}" for item in items]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("orchestrator: wrote %d unresolved item(s) to %s", len(items), path)
    return path
