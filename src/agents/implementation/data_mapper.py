"""B1 — Data Mapper.

Sonnet 4.6 reads a verified ReplicationSpec + our catalog of available data
sources, and produces a DataMapping: one FieldMapping per spec field that
needs data, naming which (source, method) feeds it and at what fidelity.
"""

from __future__ import annotations

import json

from src.agents.implementation.catalog import CATALOG, catalog_as_prompt_text
from src.specs import DataMapping, ReplicationSpec
from src.utils.llm import call_claude, load_prompt

MODEL = "claude-sonnet-4-6"
PROMPT_NAME = "data_mapper"


def map_data(
    spec: ReplicationSpec,
    use_cache: bool = True,
) -> DataMapping:
    """Run B1 — produce a DataMapping for the spec."""
    user_content = json.dumps(
        {
            "spec": spec.model_dump(mode="json"),
            "catalog": CATALOG,
        },
        indent=2,
        default=str,
    )
    # Also expose the catalog as narrative text at the top — Opus reads
    # structured JSON fine but the narrative grounds the choices.
    system_prompt = (
        load_prompt(PROMPT_NAME)
        + "\n\n## Available catalog (narrative form)\n\n"
        + catalog_as_prompt_text()
    )
    return call_claude(
        model=MODEL,
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=DataMapping,
        use_cache=use_cache,
    )
