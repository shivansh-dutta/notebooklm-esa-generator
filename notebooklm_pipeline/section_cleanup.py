"""
notebooklm_pipeline/section_cleanup.py — deterministic scaffolding strip for
NotebookLM's raw section answers, before they're written to
Report_Sections/*.md.

The 631 Northland review found that NotebookLM's drafted answers routinely
echo the template's own scaffolding straight into the "finished" prose:
leaked YAML frontmatter, a doubled `> **DRAFT — PE REVIEW REQUIRED**` banner,
`<!-- DRAFT: ... -->` comments that should have been replaced, and stray
`--- BEGIN/END TEMPLATE ---` / `## Answer` / `## Citations` wrapper text. All
of this rides verbatim into the exported DOCX because nothing strips it (see
notebooklm_pipeline/assemble.py and scripts/export_docx.py — confirmed no
cleanup pass exists at either layer).

This module is pure string/regex processing — no LLM call, no NotebookLM
call — so it's fully unit-testable and runs on every section unconditionally.
It complements (does not replace) notebooklm_pipeline/review_pass.py, which
catches subtler issues (carried-over authorship, contradictions) that regex
can't reliably identify.

Public interface:
    clean_section_markdown(text: str) -> str
"""

from __future__ import annotations

import re

from scripts.report_constants import pe_marker

# ---------------------------------------------------------------------------
# Leaked scaffolding patterns
# ---------------------------------------------------------------------------

# A leaked YAML frontmatter block at the very start of the answer (NotebookLM
# occasionally echoes the template's own `---\nsection: ...\n---` header
# instead of starting with the first real heading).
_LEADING_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n.*?\n---\s*\n?", re.DOTALL)

# The DRAFT banner assemble.write_sections already prepends exactly once
# (agents.writer.DRAFT_MARKER). If NotebookLM's answer also opens with its
# own copy, drop the echoed one so the final file has exactly one.
_DRAFT_BANNER_RE = re.compile(
    r"^\s*>\s*\*\*DRAFT\s*[—-]\s*PE REVIEW REQUIRED\*\*\s*\n+", re.IGNORECASE
)

# `<!-- DRAFT: ... -->` comments should have been replaced with prose; a
# surviving one means NotebookLM skipped that instruction. Removing it
# outright (rather than converting to a PE marker) is correct here — DRAFT
# blocks are things the pipeline expects to be answerable from sources, so a
# leftover one is a drafting miss, not a genuine "needs a human" gap; the
# empty space left behind is honest about that miss without inventing text.
_DRAFT_COMMENT_RE = re.compile(r"<!--\s*DRAFT:.*?-->", re.DOTALL)

# `<!-- FILL IN MANUALLY: X -->` is the one comment NotebookLM is correctly
# instructed to preserve verbatim (question_bank._build_question) — but an
# HTML comment is invisible in a rendered DOCX read, and the review flagged
# raw `<!-- ... -->` syntax leaking into the "finished" report. Convert it to
# the same PE_MARKER convention every other genuine gap uses, so it reads
# consistently in the exported document instead of looking like a bug.
_FILL_IN_MANUALLY_RE = re.compile(r"<!--\s*FILL IN MANUALLY:\s*(.*?)\s*-->", re.DOTALL)

# Prompt delimiters that should never appear in an answer — if NotebookLM
# echoes them, they're a leaked artifact of the prompt, not content.
_TEMPLATE_FENCE_RE = re.compile(r"^\s*---\s*(BEGIN|END)\s+TEMPLATE\s*---\s*$", re.IGNORECASE | re.MULTILINE)

# Answer/citations wrapper lines that only ever appear if raw NBLM_Answers-
# style formatting somehow ends up passed to write_sections instead of the
# bare answer string.
_WRAPPER_LINE_RE = re.compile(
    r"^\s*##\s*(Answer|Citations)\s*$|^\s*\(none returned\)\s*$", re.IGNORECASE | re.MULTILINE
)

# Inline citation markers NotebookLM sometimes emits — numeric ([1], [2, 3],
# [12-14]) and bracketed source-name variants ([APX2 photographs], [1986
# Sanborn], [APX5]). Resolved citations (see nblm_client.ask's structured
# citation capture) are handled separately in build_resolved_references();
# this strips whatever is left when no structured citation data exists,
# since an inline bracket with nothing to resolve it against is worse than
# no citation at all — it looks authoritative but verifies nothing.
_NUMERIC_CITATION_RE = re.compile(r"\[\s*\d+(?:\s*[,–—-]\s*\d+)*\s*\]")
_NAMED_CITATION_RE = re.compile(r"\[(?:APX\d+[^\[\]]{0,40}|\d{4}\s+Sanborn|[A-Za-z .]{2,20}\s+(?:photographs|Sanborn|map))\]")


def _strip_leading_frontmatter(text: str) -> str:
    return _LEADING_FRONTMATTER_RE.sub("", text, count=1)


def _strip_draft_banner(text: str) -> str:
    return _DRAFT_BANNER_RE.sub("", text, count=1)


def _strip_draft_comments(text: str) -> str:
    return _DRAFT_COMMENT_RE.sub("", text)


def _convert_fill_in_manually(text: str) -> str:
    return _FILL_IN_MANUALLY_RE.sub(lambda m: pe_marker(m.group(1).strip()), text)


def _strip_template_fences(text: str) -> str:
    return _TEMPLATE_FENCE_RE.sub("", text)


def _strip_wrapper_lines(text: str) -> str:
    return _WRAPPER_LINE_RE.sub("", text)


def _strip_inline_citations(text: str) -> str:
    text = _NUMERIC_CITATION_RE.sub("", text)
    text = _NAMED_CITATION_RE.sub("", text)
    # Citation removal can leave " ." or "  " artifacts (e.g. "clay [4, 5]."
    # -> "clay ."); tidy the most common ones without touching real prose.
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _collapse_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines left behind by removals to at
    most one, and strip trailing whitespace per line."""
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        out.append(line)
    return "\n".join(out)


def resolved_citations_block(structured_citations: list[dict]) -> str | None:
    """
    Build a "**Citations:**" markdown list from AskResult.structured_citations
    (see nblm_client.py), one numbered entry per citation with whatever of
    source/page/snippet were actually extracted — e.g.
    "1. EDR Radius Map Report, p. 42 — "...glacial lake-deposited silty
    clays..."". Returns None if there's nothing usable (empty list, or every
    entry lacks even a source/title) — callers should treat None as "cannot
    resolve" and fall back to stripping inline citation markers instead of
    leaving them dangling with no reference to explain them.

    This directly implements the "resolve to a real reference (source +
    page/paragraph) if the metadata is available; otherwise strip the inline
    markers entirely" rule — resolution is attempted first, stripping is the
    fallback (see clean_section_markdown's default strip_citations=True).
    """
    usable = [c for c in structured_citations if c.get("source")]
    if not usable:
        return None
    lines = ["**Citations:**", ""]
    for i, c in enumerate(usable, start=1):
        parts = [c["source"]]
        if c.get("page"):
            parts.append(f"p. {c['page']}")
        entry = ", ".join(parts)
        if c.get("snippet"):
            snippet = c["snippet"].strip()
            if len(snippet) > 160:
                snippet = snippet[:157].rstrip() + "..."
            entry += f' — "{snippet}"'
        lines.append(f"{i}. {entry}")
    return "\n".join(lines)


def clean_section_markdown(text: str, *, strip_citations: bool = True) -> str:
    """
    Strip leaked template scaffolding from a raw NotebookLM section answer.

    strip_citations=True (default) removes unresolved inline `[N]` / bracketed
    citation markers — pass False when the caller has already substituted
    them with a resolved References mapping (see nblm_client.AskResult and
    the WS4 citation-resolution path) and wants to preserve whatever inline
    markers remain because they DO resolve to something.
    """
    if not text:
        return text
    cleaned = text
    cleaned = _strip_leading_frontmatter(cleaned)
    cleaned = _strip_draft_banner(cleaned)
    cleaned = _strip_template_fences(cleaned)
    cleaned = _strip_wrapper_lines(cleaned)
    cleaned = _strip_draft_comments(cleaned)
    cleaned = _convert_fill_in_manually(cleaned)
    if strip_citations:
        cleaned = _strip_inline_citations(cleaned)
    cleaned = _collapse_blank_lines(cleaned)
    return cleaned.strip() + "\n"
