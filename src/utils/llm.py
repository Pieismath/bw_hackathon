"""Shared Anthropic wrapper.

Every agent call routes through `call_claude`. The wrapper owns:
  - Structured output via Anthropic tool-use (forces JSON matching a
    Pydantic schema; no ad-hoc JSON parsing of model prose).
  - Pydantic schema validation with retry-on-failure (max 3), feeding the
    validation error back to the model as a user message so it can fix
    specific mistakes instead of regenerating blindly.
  - Disk-backed cache keyed on (model, system_prompt, user_content, schema
    name + schema JSON). During development, iterating on downstream agent
    logic doesn't re-spend tokens — and cache invalidates automatically
    whenever the schema or prompt changes.
  - Prompt loader reading src/agents/prompts/*.md so prompts are reviewable
    in diff, not strewn inside Python strings.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from pathlib import Path
from typing import Any, Type, TypeVar

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).parent.parent / "agents" / "prompts"
CACHE_DIR = Path("data/cache/llm_cache")

_client: Anthropic | None = None


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


def _get_client() -> Anthropic:
    """Lazy-init client.

    - Explicit `.env` path avoids load_dotenv's inspect-based stack walk,
      which is fragile across entry points (scripts vs pytest vs heredoc).
    - `override=True` because the macOS shell ships ANTHROPIC_API_KEY=''
      from the Claude desktop bundle, which beats the .env unless we
      override.
    """
    global _client
    if _client is None:
        if _ENV_FILE.exists():
            load_dotenv(dotenv_path=_ENV_FILE, override=True)
        else:
            load_dotenv(override=True)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"ANTHROPIC_API_KEY not set after loading {_ENV_FILE}. "
                "Ensure the .env file has ANTHROPIC_API_KEY=<key>."
            )
        _client = Anthropic(api_key=api_key)
    return _client


def load_prompt(name: str) -> str:
    """Read src/agents/prompts/{name}.md (no .md extension in `name`)."""
    p = PROMPTS_DIR / f"{name}.md"
    if not p.exists():
        raise FileNotFoundError(f"prompt not found: {p}")
    return p.read_text(encoding="utf-8")


def _cache_key(
    model: str,
    system_prompt: str,
    user_content: str,
    schema: Type[BaseModel],
) -> str:
    schema_json = json.dumps(schema.model_json_schema(), sort_keys=True)
    material = "\x1e".join(
        [model, system_prompt, user_content, schema.__name__, schema_json]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"{schema.__name__}__{digest}"


def _cache_load(key: str) -> Any:
    p = CACHE_DIR / f"{key}.pkl"
    if not p.exists():
        return None
    with p.open("rb") as f:
        return pickle.load(f)


def _cache_save(key: str, value: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{key}.pkl"
    tmp = p.with_suffix(".pkl.tmp")
    with tmp.open("wb") as f:
        pickle.dump(value, f)
    tmp.replace(p)


def call_claude(
    model: str,
    system_prompt: str,
    user_content: str,
    response_schema: Type[T],
    max_retries: int = 3,
    max_tokens: int = 8192,
    use_cache: bool = True,
) -> T:
    """Call Claude with forced structured output matching `response_schema`.

    Anthropic's tool-use with `tool_choice={"type":"tool","name":...}`
    constrains the model to emit a tool_use block whose input matches the
    tool's input_schema (= response_schema's JSON schema). We validate the
    tool input through Pydantic; on ValidationError we feed the error
    back to the model and ask it to fix, up to `max_retries` times.
    """
    if use_cache:
        key = _cache_key(model, system_prompt, user_content, response_schema)
        cached = _cache_load(key)
        if cached is not None:
            return response_schema.model_validate(cached)

    client = _get_client()
    tool_name = f"emit_{response_schema.__name__}"
    tool = {
        "name": tool_name,
        "description": f"Emit a valid {response_schema.__name__} object.",
        "input_schema": response_schema.model_json_schema(),
    }

    messages: list[dict] = [{"role": "user", "content": user_content}]
    last_error: Exception | None = None

    for attempt in range(max_retries):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=messages,
        )
        tool_block = next(
            (b for b in response.content if getattr(b, "type", None) == "tool_use"),
            None,
        )
        if tool_block is None:
            # Model violated tool_choice constraint — re-ask with a plain
            # user message (no tool_result needed because no tool_use was
            # emitted).
            last_error = RuntimeError(
                f"no tool_use block in response on attempt {attempt + 1}"
            )
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You did not emit the required tool call. "
                        f"Emit a {tool_name} tool call with a valid input."
                    ),
                }
            )
            continue

        try:
            validated = response_schema.model_validate(tool_block.input)
        except ValidationError as e:
            last_error = e
            # Anthropic requires every assistant-emitted tool_use to be
            # answered by a tool_result block in the very next user
            # message. Appending a plain-string user message here would
            # trigger a 400 ("tool_use ids were found without tool_result
            # blocks").
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "is_error": True,
                            "content": (
                                "Your tool input failed schema validation "
                                f"with the following errors:\n\n{e}\n\n"
                                f"Return a corrected {tool_name} tool call."
                            ),
                        }
                    ],
                }
            )
            continue

        if use_cache:
            _cache_save(key, validated.model_dump())
        return validated

    raise RuntimeError(
        f"Failed to get valid {response_schema.__name__} after "
        f"{max_retries} attempts. Last error: {last_error}"
    )
