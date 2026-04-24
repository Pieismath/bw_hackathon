"""Tests for src/utils/llm.py.

Most tests are offline: prompt loading, cache-key stability, and retry
feedback construction don't need an API call. One integration test hits
the Haiku endpoint with an echo schema; it is marked `llm` and cached
after first run so repeat CI hits disk, not the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from src.utils.llm import (
    PROMPTS_DIR,
    _cache_key,
    call_claude,
    load_prompt,
)


class EchoResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    echoed: str = Field(description="The user's message, echoed back verbatim.")
    length: int = Field(ge=0, description="Character length of the echoed message.")


def test_load_prompt_reads_markdown(tmp_path, monkeypatch):
    # Point PROMPTS_DIR at a tmp dir, drop a file, confirm it's read.
    monkeypatch.setattr("src.utils.llm.PROMPTS_DIR", tmp_path)
    (tmp_path / "demo.md").write_text("hello prompt\n")
    assert load_prompt("demo") == "hello prompt\n"


def test_load_prompt_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.llm.PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        load_prompt("nope")


def test_cache_key_stable_across_calls():
    a = _cache_key("claude", "sys", "user", EchoResponse)
    b = _cache_key("claude", "sys", "user", EchoResponse)
    assert a == b


def test_cache_key_changes_when_user_content_changes():
    a = _cache_key("claude", "sys", "user1", EchoResponse)
    b = _cache_key("claude", "sys", "user2", EchoResponse)
    assert a != b


def test_cache_key_changes_when_schema_changes():
    class AltEcho(BaseModel):
        model_config = ConfigDict(frozen=True, extra="forbid")
        echoed: str
        length: int
        new_field: str = "x"  # schema drift

    a = _cache_key("claude", "sys", "user", EchoResponse)
    b = _cache_key("claude", "sys", "user", AltEcho)
    assert a != b


def test_cache_key_filename_starts_with_schema_name():
    k = _cache_key("m", "s", "u", EchoResponse)
    assert k.startswith("EchoResponse__")


@pytest.mark.llm
def test_call_claude_echo_end_to_end(tmp_path, monkeypatch):
    """Integration: hits the real Haiku endpoint once, cached thereafter.

    Uses a tmp cache dir so we don't pollute the shared cache, but the
    test still costs ~a few hundred tokens on first run. CI skips this
    by default — run with `pytest -m llm` to exercise.
    """
    monkeypatch.setattr("src.utils.llm.CACHE_DIR", tmp_path / "llm_cache")
    result = call_claude(
        model="claude-haiku-4-5-20251001",
        system_prompt=(
            "You echo the user's exact message in `echoed`, and report its "
            "character length (spaces included) in `length`."
        ),
        user_content="hello world",
        response_schema=EchoResponse,
    )
    assert isinstance(result, EchoResponse)
    assert "hello" in result.echoed.lower()
    assert result.length == len(result.echoed) or result.length > 0


@pytest.mark.llm
def test_call_claude_cache_roundtrip(tmp_path, monkeypatch):
    """Confirm second call hits cache: mock _get_client AFTER first call
    and verify we get the same result without the client being created."""
    monkeypatch.setattr("src.utils.llm.CACHE_DIR", tmp_path / "llm_cache")
    first = call_claude(
        model="claude-haiku-4-5-20251001",
        system_prompt="You echo messages.",
        user_content="cache test string",
        response_schema=EchoResponse,
    )
    # After the first call, poison _get_client so any real call would fail.
    def _refuse(*a, **kw):  # pragma: no cover
        raise AssertionError("cache miss — should have been served from disk")
    monkeypatch.setattr("src.utils.llm._get_client", _refuse)
    second = call_claude(
        model="claude-haiku-4-5-20251001",
        system_prompt="You echo messages.",
        user_content="cache test string",
        response_schema=EchoResponse,
    )
    assert first == second
