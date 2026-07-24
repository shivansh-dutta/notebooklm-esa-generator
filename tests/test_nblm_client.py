"""
Unit tests for notebooklm_pipeline/nblm_client.py's citation handling.

ask() itself needs a real (or mocked) notebooklm-py client, so these tests
target the pure extraction helpers directly — _extract_structured_citation()
and the citations/structured_citations split ask() builds from whatever
client.chat.ask() returns.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from notebooklm_pipeline.nblm_client import _extract_structured_citation, ask


class TestExtractStructuredCitation:
    def test_string_citation_yields_no_structured_data(self):
        assert _extract_structured_citation("just a plain string") == {}

    def test_dict_citation_extracts_known_keys(self):
        c = {"title": "EDR Radius Map Report", "page": 42, "snippet": "clay soils"}
        out = _extract_structured_citation(c)
        assert out == {"source": "EDR Radius Map Report", "page": "42", "snippet": "clay soils"}

    def test_object_citation_extracts_via_attribute_probing(self):
        c = SimpleNamespace(title="Sanborn Map 1986", page_number=7, url="https://example.com/x")
        out = _extract_structured_citation(c)
        assert out["source"] == "Sanborn Map 1986"
        assert out["page"] == "7"
        assert out["url"] == "https://example.com/x"

    def test_object_with_no_recognizable_fields_yields_empty(self):
        c = SimpleNamespace(irrelevant_field="nothing useful")
        assert _extract_structured_citation(c) == {}

    def test_source_attr_priority_order(self):
        # "title" should win over "source" when both are present.
        c = SimpleNamespace(title="Preferred Title", source="Fallback Source")
        out = _extract_structured_citation(c)
        assert out["source"] == "Preferred Title"


class TestAskCapturesStructuredCitations:
    @pytest.mark.asyncio
    async def test_no_citations_returns_empty_structured_list(self):
        client = SimpleNamespace(chat=SimpleNamespace(
            ask=AsyncMock(return_value=SimpleNamespace(answer="Some answer.", citations=[]))
        ))
        result = await ask(client, "nb-1", "a question")
        assert result.answer == "Some answer."
        assert result.citations == []
        assert result.structured_citations == []

    @pytest.mark.asyncio
    async def test_object_citations_populate_both_lists(self):
        citation_obj = SimpleNamespace(title="EDR Radius Map Report", page=12)
        client = SimpleNamespace(chat=SimpleNamespace(
            ask=AsyncMock(return_value=SimpleNamespace(answer="Grounded.", citations=[citation_obj]))
        ))
        result = await ask(client, "nb-1", "a question")
        assert result.citations == ["EDR Radius Map Report"]
        assert result.structured_citations == [{"source": "EDR Radius Map Report", "page": "12"}]

    @pytest.mark.asyncio
    async def test_string_citations_populate_citations_but_not_structured(self):
        client = SimpleNamespace(chat=SimpleNamespace(
            ask=AsyncMock(return_value=SimpleNamespace(answer="Grounded.", citations=["Some Source"]))
        ))
        result = await ask(client, "nb-1", "a question")
        assert result.citations == ["Some Source"]
        assert result.structured_citations == []
