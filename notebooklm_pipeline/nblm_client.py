"""
notebooklm_pipeline/nblm_client.py — thin async wrapper over notebooklm-py.

Isolates every direct dependency on the unofficial `notebooklm-py` library
(https://github.com/teng-lin/notebooklm-py) behind this one module. That
library wraps undocumented Google endpoints and can change without notice —
if its API surface shifts, this is the only file that should need editing.

Auth: this module does NOT perform login itself. Run one of these once,
out-of-band, before using this pipeline (see notebooklm_pipeline/README.md):

    notebooklm login                                  # interactive browser
    notebooklm login --master-token --account you@x.com  # headless-friendly

NotebookLMClient.from_storage() (used below) reads the credentials that
command persists. If that raises, we surface a clear "run `notebooklm
login`" error rather than a raw stack trace.

Public interface (all async — callers use asyncio.run or await from an
async context; see run.py for the single top-level asyncio.run call):
    NblmError
    open_client() -> async context manager yielding a connected client
    create_notebook(client, title) -> notebook (has .id, .title)
    add_source(client, notebook_id, file_path, wait=True) -> source
    ask(client, notebook_id, question) -> AskResult(answer, citations)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class NblmError(RuntimeError):
    """Raised for any notebooklm-py failure, with a human-actionable message."""


@dataclass
class AskResult:
    """Normalized result of a chat.ask() call — the raw library response
    shape isn't fully documented (esp. for citations), so every call site
    here defensively extracts what it can rather than assuming attributes
    exist."""
    answer: str
    citations: list[str] = field(default_factory=list)
    raw: Any = None


def _import_notebooklm():
    try:
        import notebooklm  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dep
        raise NblmError(
            "notebooklm-py is not installed. Install the optional group with:\n"
            '    uv pip install -e ".[notebooklm]"\n'
            "or: pip install 'notebooklm-py[browser]'\n"
            "See notebooklm_pipeline/README.md for full setup."
        ) from exc
    return notebooklm


@asynccontextmanager
async def open_client() -> AsyncIterator[Any]:
    """
    Async context manager yielding a connected NotebookLMClient, using
    whatever credentials `notebooklm login` (or `--master-token`) already
    persisted to local storage.

    Usage:
        async with open_client() as client:
            nb = await create_notebook(client, "631 Northland")
            ...
    """
    notebooklm = _import_notebooklm()
    try:
        async with notebooklm.NotebookLMClient.from_storage() as client:
            yield client
    except NblmError:
        raise
    except Exception as exc:
        raise NblmError(
            "Could not connect to NotebookLM — no valid stored session. "
            "Run `notebooklm login` (or `notebooklm login --master-token "
            "--account you@example.com` for headless use) and try again. "
            f"Underlying error: {exc}"
        ) from exc


async def create_notebook(client: Any, title: str) -> Any:
    """Create a new NotebookLM notebook and return it (has .id, .title)."""
    try:
        notebook = await client.notebooks.create(title)
    except Exception as exc:
        raise NblmError(f"Failed to create notebook '{title}': {exc}") from exc
    logger.info("nblm_client: created notebook %r (id=%s)", title, getattr(notebook, "id", "?"))
    return notebook


async def add_source(client: Any, notebook_id: str, file_path: Path, *, wait: bool = True) -> Any:
    """
    Upload a local file (PDF, etc.) as a source to the given notebook.

    wait=True blocks until NotebookLM finishes processing the source before
    returning — callers should keep this True before asking questions that
    depend on the source, since an unprocessed source won't be queryable.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise NblmError(f"Source file not found: {file_path}")
    try:
        source = await client.sources.add_file(notebook_id, str(file_path), wait=wait)
    except Exception as exc:
        raise NblmError(f"Failed to upload source {file_path.name}: {exc}") from exc
    logger.info("nblm_client: uploaded source %s -> notebook %s", file_path.name, notebook_id)
    return source


async def ask(
    client: Any, notebook_id: str, question: str, *, retries: int = 4, retry_delay: float = 5.0
) -> AskResult:
    """Ask a question of the notebook and return a normalized AskResult.

    NotebookLM's streaming chat response occasionally comes back truncated
    or unparseable (a known rough edge of the unofficial library talking to
    undocumented endpoints) — a bare retry after a short delay usually
    succeeds, so transient failures are retried before raising.
    """
    last_exc: Exception | None = None
    result = None
    for attempt in range(retries + 1):
        try:
            result = await client.chat.ask(notebook_id, question)
            break
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "nblm_client: ask() failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt + 1, retries + 1, exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
    if result is None:
        raise NblmError(f"chat.ask failed for question {question[:80]!r}: {last_exc}") from last_exc

    answer = getattr(result, "answer", None)
    if answer is None:
        # Defensive fallback if the library's response shape differs from
        # what the README documents (e.g. a plain string or dict response).
        answer = result if isinstance(result, str) else str(result)

    citations_raw = getattr(result, "citations", None) or getattr(result, "sources", None) or []
    citations: list[str] = []
    for c in citations_raw:
        if isinstance(c, str):
            citations.append(c)
        else:
            citations.append(
                getattr(c, "title", None) or getattr(c, "source", None) or str(c)
            )

    return AskResult(answer=answer, citations=citations, raw=result)
