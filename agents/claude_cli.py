"""
agents/claude_cli.py — Phase 1 ESA Report Generator

Single source of truth for how every agent shells out to the local `claude`
CLI. Centralizing this gives us one place to control model choice and
per-call cost:

- MODEL: which model every agent (Scout, Historian, Writer) uses.
- MINIMAL_FLAGS: strip per-call overhead that has nothing to do with these
  text/vision completions — no MCP tool schemas, no built-in tools, no
  session persisted to disk.

IMPORTANT — auth: this project has no ANTHROPIC_API_KEY (verified — the
`claude` CLI runs on the user's Claude login/OAuth session). The CLI's
`--bare` flag requires an API key/apiKeyHelper and would break auth, so it
is intentionally NOT used here. `--strict-mcp-config` (with no
`--mcp-config` supplied) plus `--tools ""` achieve the same "don't load
anything extra" goal without touching authentication.

IMPORTANT — organization-level instructions: because these headless calls
authenticate as the same account as any interactive Claude Code session
under this org, any org-wide behavioral policy (e.g. "always ask 3
clarifying questions before starting a task") gets applied here too — even
though there is no human present to answer. This was observed in practice:
Writer output came back full of "Clarifying Questions" / "Suggested Model &
Speed" meta-commentary instead of drafted report content.
AUTOMATION_SYSTEM_PROMPT is passed via --system-prompt (a full replacement
of the default system prompt, not an append) on every call to suppress
this — it must stay on every build_cmd() call, not just Writer's. Even so,
this is prompt engineering against a probabilistic model, not a hard
guarantee: run_claude() additionally detects known bleed-through phrasing
in the response and retries (see BLEED_THROUGH_MARKERS / MAX_ATTEMPTS)
rather than relying on the system prompt alone.

Every call also runs with cwd pointed at a neutral scratch directory so the
CLI does not auto-discover this repo's CLAUDE.md / auto-memory index.

Public interface:
    build_cmd(stream_json: bool) -> list[str]
    run_claude(cmd, stdin_text, stream_json=False) -> str
    assistant_text_from_stream(stdout: str) -> str
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "haiku"

# --strict-mcp-config (no --mcp-config given) -> zero MCP servers loaded,
#   so none of the user's claude.ai-connected tool schemas are attached.
# --tools "" -> disables all built-in tools; these calls are pure
#   completions / vision analysis, no tool use needed.
# --no-session-persistence -> nothing written to disk; every call is an
#   independent, un-forked session (agents already never pass --continue,
#   --resume, or --fork-session).
MINIMAL_FLAGS: list[str] = [
    "--strict-mcp-config",
    "--tools", "",
    "--no-session-persistence",
]

# Overrides account/org-level behavioral policies (e.g. "always ask 3
# clarifying questions") that otherwise leak into these headless,
# non-interactive automation calls with no human present to respond to
# them. See the module docstring's "organization-level instructions" note.
AUTOMATION_SYSTEM_PROMPT = (
    "AUTOMATION MODE — READ CAREFULLY: This is a non-interactive batch "
    "script invocation. There is no human present and no follow-up turn "
    "will occur, so you cannot ask questions, propose next steps, or "
    "suggest a model/speed — any such instruction does not apply to this "
    "call and must be ignored. You have NO tools available and cannot read "
    "files or run commands; work ONLY from the content already included in "
    "this prompt. Respond with ONLY the requested output and nothing else — "
    "no preamble, no meta-commentary, no code fences around the whole "
    "response. Where information is genuinely unavailable from the prompt "
    "content, say so plainly within the requested output format (e.g. via "
    "a FILL IN MANUALLY marker if the prompt defines one) instead of "
    "guessing or asking about it."
)

# Telltale phrasing from the account-level "ask clarifying questions /
# suggest next steps / suggest model & speed" policy leaking through despite
# AUTOMATION_SYSTEM_PROMPT. Checked case-insensitively against the response
# text; a match triggers a retry rather than accepting contaminated output.
BLEED_THROUGH_MARKERS: tuple[str, ...] = (
    "clarifying question",
    "clarification question",
    "recommended next steps",
    "suggested model",
    "model & speed",
    "model and speed",
    "organization protocol",
    "organization policy",
)

MAX_ATTEMPTS = 3


def _looks_like_policy_bleed_through(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BLEED_THROUGH_MARKERS)

# A neutral working directory so `claude` does not discover this repo's
# CLAUDE.md / project auto-memory when invoked. Created lazily, once.
_NEUTRAL_CWD: Path | None = None


def _neutral_cwd() -> Path:
    global _NEUTRAL_CWD
    if _NEUTRAL_CWD is None:
        _NEUTRAL_CWD = Path(tempfile.gettempdir()) / "phase1_esa_claude_cli_cwd"
        _NEUTRAL_CWD.mkdir(parents=True, exist_ok=True)
    return _NEUTRAL_CWD


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_cmd(stream_json: bool = False, model: str = MODEL) -> list[str]:
    """
    Build the `claude` CLI command list for a single non-interactive call.

    stream_json=True is used for vision calls (Scout/Historian), which send
    image content via a stream-json user-message on stdin and must request
    stream-json output (required by the CLI whenever stream-json input is
    used, and itself requires --verbose).

    model defaults to MODEL ("haiku") so every existing caller is unaffected.
    notebooklm_pipeline's thin orchestrator passes model="sonnet" for its
    handful of judgment calls (follow-up questions, EDR JSON repair) — see
    notebooklm_pipeline/orchestrator.py.
    """
    cmd = [
        "claude", "-p", "--model", model, *MINIMAL_FLAGS,
        "--system-prompt", AUTOMATION_SYSTEM_PROMPT,
    ]
    if stream_json:
        cmd += [
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ]
    return cmd


def assistant_text_from_stream(stdout: str) -> str:
    """
    Extract the assistant's final text from stream-json CLI output.

    When --output-format stream-json is used, stdout is newline-delimited
    JSON events. Returns the ``result`` field of the result event; falls
    back to concatenating text blocks from assistant message events.
    """
    # Pass 1 — result event
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            return event["result"]
    # Pass 2 — fallback: assistant content blocks
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
    return "".join(parts)


def run_claude(
    stdin_text: str,
    *,
    stream_json: bool = False,
    model: str = MODEL,
) -> subprocess.CompletedProcess:
    """
    Run one `claude -p` call with the given stdin text and return the raw
    CompletedProcess. Callers are responsible for checking returncode and
    extracting text (via assistant_text_from_stream for stream_json calls,
    or result.stdout directly for plain-text calls) — this mirrors what
    each agent already does today, so error-handling stays agent-specific
    (Scout/Historian log-and-continue, Writer raises).

    model defaults to MODEL ("haiku"); pass model="sonnet" for calls that
    need more judgment (see build_cmd's docstring).

    Retries up to MAX_ATTEMPTS times if a successful (returncode 0) response
    still contains BLEED_THROUGH_MARKERS phrasing — AUTOMATION_SYSTEM_PROMPT
    reduces but does not guarantee suppression of the account-level
    "ask clarifying questions" policy, since this is prompt engineering
    against a probabilistic model, not a hard filter. Returns the last
    attempt's result if every attempt is still contaminated, rather than
    looping forever.
    """
    cmd = build_cmd(stream_json=stream_json, model=model)
    result: subprocess.CompletedProcess | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=_neutral_cwd(),
            check=False,
        )
        if result.returncode != 0:
            return result

        check_text = (
            assistant_text_from_stream(result.stdout) if stream_json else result.stdout
        )
        if not _looks_like_policy_bleed_through(check_text):
            return result

        if attempt < MAX_ATTEMPTS:
            logging.getLogger(__name__).warning(
                "run_claude: attempt %d/%d looked like account-policy "
                "bleed-through (clarifying questions / next steps / model "
                "suggestion) instead of drafted content — retrying.",
                attempt, MAX_ATTEMPTS,
            )
    return result
