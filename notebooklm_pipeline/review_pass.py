"""
notebooklm_pipeline/review_pass.py — a single whole-report sonnet review pass
over the fully assembled draft, run once per project after assemble.py has
written Report_Sections/*.md (including the deterministic Section 11.0
Qualifications — see assemble.build_qualifications_markdown).

Per the user's explicit design choice this session: ONE call over the whole
report (not per-section — token/session cost), findings-and-deletions ONLY.
It never rewrites, adds, or infers prose — that would violate the governing
rule for this whole effort: the pipeline may delete/flag wrong content or
leave a visible gap marker, but must never generate replacement facts. This
pass catches things the regex-based section_cleanup.py structurally can't:

  - authorship/identity carry-over that survived question_bank.py's firewall
    instructions (e.g. a stray "prepared by Ravi Engineering..." sentence
    deep in an otherwise well-grounded section)
  - cross-section contradictions (a questionnaire described as completed in
    3.8 but called "not provided" in 8.3; "no CRECs/HRECs" in the Executive
    Summary while 5.3 classifies one)
  - residual duplicate/redundant PE-marker stubs that merely restate a fact
    the surrounding narrative in the same section already answered

Deletions are applied by EXACT substring match only — if the model's
returned string doesn't appear verbatim in the section it's meant to come
from, it's silently skipped, never fuzzy-matched. A malformed/failed
response is a no-op (mirrors orchestrator.repair_edr_json's "None means
unusable, not zero" pattern): this pass augments a report that's already
usable without it, and must never block or crash the run.

Public interface:
    run_review_pass(project_path: Path, dashboard: dict[str, str]) -> None
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agents.claude_cli import run_claude

logger = logging.getLogger(__name__)

REVIEW_MODEL = "sonnet"

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json_object(text: str) -> dict | None:
    """Best-effort JSON-object parse, tolerant of a ```json fence or stray
    prose around the object. Returns None if unparseable or not an object."""
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RE.search(stripped)
    if m:
        try:
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def load_sections(project_path: Path) -> dict[str, str]:
    sections_dir = Path(project_path) / "Report_Sections"
    if not sections_dir.exists():
        return {}
    out: dict[str, str] = {}
    for path in sorted(sections_dir.glob("*.md")):
        try:
            out[path.name] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def build_review_prompt(sections: dict[str, str], dashboard: dict[str, str]) -> str:
    firm = dashboard.get("ep_firm") or "(not stated)"
    assessor = dashboard.get("assessor_name") or "(not stated)"
    reviewer = dashboard.get("reviewer_name") or "(not stated)"

    parts = [
        "You are reviewing a fully-drafted Phase 1 Environmental Site Assessment "
        "report for internal consistency and residual drafting artifacts. You are "
        "given every section's final text below. Your ONLY job is to find "
        "problems — never rewrite, add, or infer any factual content.",
        "",
        f'This engagement\'s OFFICIAL preparer identity (from the project '
        f'dashboard, the only source of truth for this): firm="{firm}", '
        f'assessor="{assessor}", reviewer="{reviewer}". Any sentence naming a '
        "DIFFERENT firm or person as having prepared, authored, reviewed, or "
        "signed THIS report — or as having submitted a FOIL/records request on "
        "THIS engagement's behalf — is a carried-over identity from a prior, "
        "different report and must be flagged for deletion.",
        "",
        "Return STRICT JSON, no other text, no markdown code fence, in exactly "
        "this shape:",
        '{"deletions": ["<exact substring to delete>", ...], '
        '"findings": ["<plain-English description of a contradiction, citing '
        'both locations>", ...]}',
        "",
        "deletions: exact substrings, copied VERBATIM from the section text "
        "below (so they can be found and removed by exact string match), for: "
        "(a) leftover template scaffolding (HTML comments, stray markers, "
        "template delimiters) that survived; (b) a sentence naming a preparer/"
        "firm/reviewer/FOIL-submitter identity that does not match the official "
        "identity above; (c) a redundant PE-marker-style stub that merely "
        "restates a fact the surrounding narrative in the SAME section already "
        "answered.",
        "",
        "findings: plain-English notes on genuine cross-section contradictions "
        "a PE must resolve (e.g. one section states a questionnaire was "
        "completed while another calls it a data gap; one section says no "
        "CRECs/HRECs were identified while another classifies one). Do NOT "
        "resolve them yourself — describe the contradiction and name both "
        "sections.",
        "",
        'If nothing needs fixing, return exactly: {"deletions": [], "findings": []}',
        "",
        "--- REPORT SECTIONS ---",
    ]
    for filename, content in sections.items():
        parts.append(f"\n## FILE: {filename}\n{content}")
    return "\n".join(parts)


def apply_deletions(project_path: Path, sections: dict[str, str], deletions: list) -> int:
    """Remove every string in *deletions* that appears verbatim in a
    section's text, rewriting only files that actually changed. Returns the
    total number of (file, deletion) matches applied."""
    sections_dir = Path(project_path) / "Report_Sections"
    applied = 0
    for filename, content in sections.items():
        original = content
        for deletion in deletions:
            if not isinstance(deletion, str) or not deletion:
                continue
            if deletion in content:
                content = content.replace(deletion, "")
                applied += 1
        if content != original:
            (sections_dir / filename).write_text(content, encoding="utf-8")
    return applied


def append_findings(
    project_path: Path, findings: list, heading: str = "## Automated consistency review"
) -> None:
    """Append findings under *heading* in Questions_For_User.md — appends
    rather than overwrites, since orchestrator.route_unknowns() (and
    possibly this function itself, called again under a different heading —
    see notebooklm_pipeline/consistency.py) may already have written to this
    same file earlier in the run."""
    clean_findings = [f for f in findings if isinstance(f, str) and f.strip()]
    if not clean_findings:
        return
    path = Path(project_path) / "Questions_For_User.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Questions For User\n"
    lines = [existing.rstrip(), "", heading, ""]
    lines += [f"- {f}" for f in clean_findings]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("review_pass: appended %d finding(s) under %r to %s", len(clean_findings), heading, path)


def run_review_pass(project_path: Path, dashboard: dict[str, str]) -> None:
    """
    Run the whole-report sonnet review pass. No-ops (logs and returns)
    entirely if there are no sections to review, the Claude CLI call fails,
    or the response isn't valid JSON of the expected shape — this pass
    augments an already-usable report, it never blocks or crashes the run.
    """
    project_path = Path(project_path)
    sections = load_sections(project_path)
    if not sections:
        logger.info("review_pass: no Report_Sections/*.md found — skipping")
        return

    prompt = build_review_prompt(sections, dashboard)
    result = run_claude(prompt, model=REVIEW_MODEL)
    if result.returncode != 0:
        logger.warning("review_pass: sonnet call failed (exit %d) — skipping", result.returncode)
        return

    parsed = _try_parse_json_object(result.stdout)
    if parsed is None:
        logger.warning("review_pass: response was not valid JSON — skipping")
        return

    deletions = parsed.get("deletions")
    findings = parsed.get("findings")
    if not isinstance(deletions, list) or not isinstance(findings, list):
        logger.warning("review_pass: response JSON had the wrong shape — skipping")
        return

    applied = apply_deletions(project_path, sections, deletions)
    if applied:
        logger.info("review_pass: applied %d deletion(s) across Report_Sections/*.md", applied)

    append_findings(project_path, findings)
