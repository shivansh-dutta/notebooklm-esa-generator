from notebooklm_pipeline.notebook_state import read_notebook_id, write_notebook_id


def test_write_then_read_round_trips(tmp_path):
    write_notebook_id(tmp_path, "abc-123")
    assert read_notebook_id(tmp_path) == "abc-123"


def test_read_missing_file_returns_none(tmp_path):
    assert read_notebook_id(tmp_path) is None


def test_write_strips_whitespace(tmp_path):
    write_notebook_id(tmp_path, "  abc-123  \n")
    assert read_notebook_id(tmp_path) == "abc-123"
