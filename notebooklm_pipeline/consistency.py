"""
notebooklm_pipeline/consistency.py — cheap, deterministic (no LLM, no API
cost, no failure mode to guard against) checks that always run, complementing
notebooklm_pipeline/review_pass.py's sonnet-based whole-report review. These
catch the exact contradiction patterns found in the 631 Northland review:

  - Section 3.0 (User Provided Information) describes a completed
    environmental questionnaire while Section 8.0 (Findings/Data Gaps)
    states no completed questionnaire was provided — both can't be true.
  - The Executive Summary / Section 8.0 (Conclusions) state "no CRECs or
    HRECs were identified" while Section 5.0 (Records Review) actually
    classifies a listing as CREC/HREC.
  - Section 11.0 (Qualifications) names a preparer/firm that doesn't match
    the project dashboard's own ep_firm — a coarse denylist-style tripwire,
    NOT a replacement for question_bank.py's identity firewall (prevention)
    or review_pass.py's sonnet-based catch (nuanced detection), just a free,
    always-on backstop alongside them.

These are pattern-matching heuristics, not proof of a real contradiction —
every finding is phrased as "possible contradiction... confirm which is
correct," never asserted as fact, consistent with the rule that this
pipeline only ever flags for a human, never resolves judgment calls itself.

Public interface:
    check_consistency(sections, dashboard) -> list[str]
    run_consistency_checks(project_path, dashboard) -> None
"""

from __future__ import annotations

import re
from pathlib import Path

from notebooklm_pipeline.review_pass import append_findings, load_sections

_QUESTIONNAIRE_COMPLETED_RE = re.compile(
    r"questionnaire.{0,200}(completed|responses? (were|was) provided|respondent)",
    re.IGNORECASE | re.DOTALL,
)
# Guards the pattern above against its own most common false positive: a
# NEGATED sentence ("No questionnaire was completed...") naively matches
# "questionnaire...completed" too. If this also matches, the text is
# reporting the questionnaire was NOT completed, not that it was.
_QUESTIONNAIRE_NEGATED_RE = re.compile(
    r"no\b[^.]{0,60}questionnaire[^.]{0,60}(completed|provided)"
    r"|questionnaire[^.]{0,60}(was\s+not|wasn't|were\s+not|weren't|is\s+not|isn't)\s+(completed|provided)",
    re.IGNORECASE,
)
_QUESTIONNAIRE_NOT_PROVIDED_RE = re.compile(
    r"no completed environmental questionnaire|questionnaire.{0,80}(not provided|not completed|no response)",
    re.IGNORECASE | re.DOTALL,
)

_CREC_HREC_NONE_RE = re.compile(
    r"no (CRECs?(\s+or\s+HRECs?)?|CRECs?/HRECs?) (was|were) identified", re.IGNORECASE
)
_CREC_HREC_CLASSIFY_RE = re.compile(r"\b(CREC|HREC)\b")

_PREPARED_BY_RE = re.compile(r"prepared (?:and|&)?\s*(?:performed\s+)?by[^.]{0,120}", re.IGNORECASE)


def _section_text(sections: dict[str, str], *name_fragments: str) -> str:
    """Concatenate every section whose filename contains any of
    *name_fragments* (case-insensitive) — sections are keyed by filename
    (e.g. "08_Findings_Opinions_Conclusions.md"), so fragment matching is
    more robust than requiring an exact key."""
    parts = [
        content for filename, content in sections.items()
        if any(frag.lower() in filename.lower() for frag in name_fragments)
    ]
    return "\n".join(parts)


def check_questionnaire_contradiction(sections: dict[str, str]) -> list[str]:
    user_info_text = _section_text(sections, "User_Provided_Information")
    findings_text = _section_text(sections, "Findings_Opinions")

    completed = (
        bool(_QUESTIONNAIRE_COMPLETED_RE.search(user_info_text))
        and not _QUESTIONNAIRE_NEGATED_RE.search(user_info_text)
    )
    not_provided = bool(_QUESTIONNAIRE_NOT_PROVIDED_RE.search(findings_text))
    if completed and not_provided:
        return [
            "Possible contradiction: Section 3.0 (User Provided Information) describes a "
            "completed environmental questionnaire, but Section 8.0 (Findings/Data Gaps) "
            "states no completed questionnaire was provided. Confirm which is correct."
        ]
    return []


def check_crec_hrec_contradiction(sections: dict[str, str]) -> list[str]:
    exec_summary = _section_text(sections, "Executive_Summary")
    findings_section = _section_text(sections, "Findings_Opinions")
    records_review = _section_text(sections, "Records_Review")

    none_identified = bool(_CREC_HREC_NONE_RE.search(exec_summary) or _CREC_HREC_NONE_RE.search(findings_section))
    classified_in_records = bool(_CREC_HREC_CLASSIFY_RE.search(records_review))
    if none_identified and classified_in_records:
        return [
            "Possible contradiction: the Executive Summary or Section 8.0 (Conclusions) "
            "states no CRECs or HRECs were identified, but Section 5.0 (Records Review) "
            "classifies at least one listing as CREC or HREC. Confirm which is correct."
        ]
    return []


def check_authorship_mismatch(sections: dict[str, str], dashboard: dict[str, str]) -> list[str]:
    """Coarse tripwire: every "prepared by <X>" phrase in Section 11.0 should
    mention the dashboard's own firm name. Skipped entirely if the dashboard
    firm isn't set (nothing to compare against — not a finding in itself)."""
    firm = (dashboard.get("ep_firm") or "").strip()
    if not firm or firm.upper() == "TBD":
        return []

    qualifications_text = _section_text(sections, "Qualifications")
    findings = []
    for m in _PREPARED_BY_RE.finditer(qualifications_text):
        phrase = m.group(0)
        if firm.lower() not in phrase.lower():
            findings.append(
                f'Section 11.0 (Qualifications) names a preparer that does not match the '
                f'project dashboard\'s firm ("{firm}"): "...{phrase.strip()}...". Confirm '
                "this wasn't carried over from a prior consultant's material."
            )
    return findings


def check_consistency(sections: dict[str, str], dashboard: dict[str, str]) -> list[str]:
    findings: list[str] = []
    findings += check_questionnaire_contradiction(sections)
    findings += check_crec_hrec_contradiction(sections)
    findings += check_authorship_mismatch(sections, dashboard)
    return findings


def run_consistency_checks(project_path: Path, dashboard: dict[str, str]) -> None:
    """Load Report_Sections/*.md, run all checks, and append any findings to
    Questions_For_User.md under their own heading (reuses
    review_pass.append_findings so both mechanisms write to the same file
    without clobbering each other)."""
    project_path = Path(project_path)
    sections = load_sections(project_path)
    findings = check_consistency(sections, dashboard)
    append_findings(project_path, findings, heading="## Deterministic consistency checks")
