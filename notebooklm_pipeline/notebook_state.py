"""notebooklm_pipeline/notebook_state.py — persist the NotebookLM notebook_id
used for a project, so a later run can resume against the same notebook
(via --notebook-id, see run.py) instead of creating a duplicate and
re-uploading every source. Mirrors source_manifest.py's pattern of
colocating small run-state files with the project's own folder rather than
a separate database.
"""

from __future__ import annotations

from pathlib import Path

_STATE_FILENAME = "notebook_id.txt"


def write_notebook_id(project_path: Path, notebook_id: str) -> None:
    Path(project_path, _STATE_FILENAME).write_text(notebook_id.strip() + "\n")


def read_notebook_id(project_path: Path) -> str | None:
    state_file = Path(project_path, _STATE_FILENAME)
    if not state_file.exists():
        return None
    value = state_file.read_text().strip()
    return value or None
