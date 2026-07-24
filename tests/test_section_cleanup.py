"""Tests for notebooklm_pipeline/section_cleanup.py."""

from notebooklm_pipeline.section_cleanup import clean_section_markdown, resolved_citations_block
from scripts.report_constants import PE_MARKER


def test_strips_leading_frontmatter():
    text = '---\nsection: "2.0"\ntitle: "Site Description"\n---\n\n# 2.0 Site Description\n\nBody text.\n'
    out = clean_section_markdown(text)
    assert "---" not in out
    assert "section:" not in out
    assert "# 2.0 Site Description" in out
    assert "Body text." in out


def test_collapses_doubled_draft_banner():
    text = "> **DRAFT — PE REVIEW REQUIRED**\n\n# 2.0 Site Description\n\nBody.\n"
    out = clean_section_markdown(text)
    assert "DRAFT" not in out
    assert "# 2.0 Site Description" in out


def test_removes_leftover_draft_comment():
    text = "# 2.1 Location\n\n<!-- DRAFT: Write a formal site location description. -->\n\nActual prose here.\n"
    out = clean_section_markdown(text)
    assert "<!-- DRAFT" not in out
    assert "Actual prose here." in out


def test_converts_fill_in_manually_to_pe_marker():
    text = "# 2.3 Structures\n\n<!-- FILL IN MANUALLY: Current owner name. -->\n\nBuilding description.\n"
    out = clean_section_markdown(text)
    assert "<!--" not in out
    assert PE_MARKER in out
    assert "Current owner name" in out


def test_strips_template_fences():
    text = "--- BEGIN TEMPLATE ---\n# 1.0 Introduction\n\nBody.\n--- END TEMPLATE ---\n"
    out = clean_section_markdown(text)
    assert "BEGIN TEMPLATE" not in out
    assert "END TEMPLATE" not in out
    assert "# 1.0 Introduction" in out


def test_strips_answer_and_citations_wrapper_lines():
    text = "## Answer\n\n# 1.0 Introduction\n\nBody.\n\n## Citations\n\n(none returned)\n"
    out = clean_section_markdown(text)
    assert "## Answer" not in out
    assert "## Citations" not in out
    assert "(none returned)" not in out
    assert "Body." in out


def test_strips_unresolved_numeric_citations_by_default():
    text = "The bedrock is Onondaga Limestone [4, 13, 14].\n"
    out = clean_section_markdown(text)
    assert "[4" not in out
    assert "Onondaga Limestone" in out


def test_strips_named_citation_variants():
    text = 'The building has the painted text "CLEARING NIAGARA" [APX2 photographs].\n'
    out = clean_section_markdown(text)
    assert "[APX2" not in out
    assert "CLEARING NIAGARA" in out


def test_strip_citations_false_preserves_markers():
    text = "Onondaga Limestone [1].\n"
    out = clean_section_markdown(text, strip_citations=False)
    assert "[1]" in out


def test_collapses_excess_blank_lines():
    text = "# Heading\n\n\n\n\nBody.\n"
    out = clean_section_markdown(text)
    assert "\n\n\n" not in out


def test_empty_input_returns_empty():
    assert clean_section_markdown("") == ""


class TestResolvedCitationsBlock:
    def test_empty_list_returns_none(self):
        assert resolved_citations_block([]) is None

    def test_entries_without_source_return_none(self):
        assert resolved_citations_block([{"page": "4"}, {"snippet": "x"}]) is None

    def test_builds_numbered_list_with_source_and_page(self):
        out = resolved_citations_block([
            {"source": "EDR Radius Map Report", "page": "42"},
            {"source": "1986 Sanborn Map"},
        ])
        assert out is not None
        assert "1. EDR Radius Map Report, p. 42" in out
        assert "2. 1986 Sanborn Map" in out

    def test_includes_truncated_snippet(self):
        out = resolved_citations_block([{"source": "Report X", "snippet": "a" * 300}])
        assert out is not None
        assert "..." in out
        assert len(out) < 300 + 50

    def test_skips_entries_without_source_but_keeps_others(self):
        out = resolved_citations_block([{"page": "1"}, {"source": "Real Source"}])
        assert out is not None
        assert "Real Source" in out
        assert out.count("\n") >= 1


def test_full_leaked_section_end_to_end():
    """Mirrors the actual leaked shape seen in the 631 Northland run."""
    text = (
        "---\nsection: \"2.0\"\ntitle: \"Site Description\"\nstatus: template\n---\n\n"
        "> **DRAFT — PE REVIEW REQUIRED**\n\n"
        "# 2.0 Site Description\n\n"
        "## 2.1 Location and Legal Description\n\n"
        "<!-- FILL IN MANUALLY: approximate acreage and formal legal description. -->\n\n"
        "The subject property is located at 631 Northland Avenue [1-3].\n"
    )
    out = clean_section_markdown(text)
    assert "---" not in out
    assert "DRAFT — PE REVIEW" not in out
    assert "<!--" not in out
    assert PE_MARKER in out
    assert "[1-3]" not in out
    assert "631 Northland Avenue" in out
