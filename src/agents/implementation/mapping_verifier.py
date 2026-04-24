"""B2 — Mapping Verifier.

For each FieldMapping in a DataMapping:
  1. Deterministic: source exists in catalog, method exists on source,
     source can cover the spec's sample window (best-effort given what
     the catalog declares).
  2. LLM (Haiku): is the substitution fit for the paper's requirement?

Outputs a VerifiedDataMapping with per-mapping checks, a list of
blocking issues, and an overall fidelity rollup.
"""

from __future__ import annotations

import json
from datetime import date

from src.agents.implementation.catalog import (
    CATALOG,
    method_exists,
    source_exists,
)
from src.specs import (
    DataMapping,
    FieldMapping,
    MappingCheck,
    ReplicationSpec,
    Reasonable,
    VerifiedDataMapping,
)
from src.utils.llm import call_claude, load_prompt

HAIKU_MODEL = "claude-haiku-4-5-20251001"
PROMPT_NAME = "mapping_verifier"


# Coverage each source declares, minimally encoded here so B2's
# deterministic date check has something to chew on. Source of truth is
# CATALOG['coverage']; this mirror is pragmatic until we parse the strings.
_SOURCE_DATE_COVERAGE: dict[str, tuple[date, date]] = {
    "defeatbeta_yahoo": (date(1994, 11, 30), date(2026, 4, 17)),
    "ken_french_csv": (date(1927, 1, 31), date(2026, 2, 28)),
}
# Fundamentals sub-coverage: the shared source carries different start
# dates for different methods. Flagged explicitly for B2 checks.
_METHOD_DATE_COVERAGE: dict[tuple[str, str], tuple[date, date]] = {
    ("defeatbeta_yahoo", "get_fundamentals"): (date(2019, 5, 31), date(2026, 4, 17)),
}


def verify_mapping(
    mapping: DataMapping,
    spec: ReplicationSpec,
    use_llm_check: bool = True,
    use_cache: bool = True,
) -> VerifiedDataMapping:
    checks: list[MappingCheck] = []
    blocking: list[str] = []
    for m in mapping.mappings:
        check = _verify_one(m, spec, use_llm_check=use_llm_check, use_cache=use_cache)
        checks.append(check)
        if check.blocking:
            blocking.append(f"{m.spec_field} → {m.source_name}.{m.method}: {check.notes}")

    overall = _overall_fidelity(checks)
    return VerifiedDataMapping(
        mapping=mapping,
        checks=tuple(checks),
        blocking_issues=tuple(blocking),
        overall_fidelity=overall,
    )


def _verify_one(
    m: FieldMapping,
    spec: ReplicationSpec,
    use_llm_check: bool,
    use_cache: bool,
) -> MappingCheck:
    # 1. Source + method existence
    src_ok = source_exists(m.source_name)
    method_ok = src_ok and method_exists(m.source_name, m.method)

    # 2. Date coverage (best-effort)
    date_ok = True
    date_note = ""
    if src_ok:
        method_cov = _METHOD_DATE_COVERAGE.get((m.source_name, m.method))
        src_cov = _SOURCE_DATE_COVERAGE.get(m.source_name)
        cov = method_cov or src_cov
        if cov is not None:
            cov_start, cov_end = cov
            if spec.start_date < cov_start or spec.end_date > cov_end:
                date_ok = False
                date_note = (
                    f"source covers [{cov_start}, {cov_end}]; "
                    f"spec needs [{spec.start_date}, {spec.end_date}]"
                )

    # 3. Fields present: the catalog doesn't declare individual fields, so
    # we accept at this layer. Future work: introspect method return types.
    fields_ok = method_ok

    # 4. LLM reasonableness
    llm: Reasonable = "yes"
    llm_reason = "no LLM check performed"
    if use_llm_check and src_ok and method_ok:
        llm_result = _haiku_reasonable(m, spec, use_cache=use_cache)
        llm = llm_result["supports"]
        llm_reason = llm_result["reason"]
    elif not src_ok:
        llm = "no"
        llm_reason = f"source {m.source_name!r} not in catalog"
    elif not method_ok:
        llm = "no"
        llm_reason = f"method {m.method!r} not on source {m.source_name!r}"

    blocking_flag = (
        (not src_ok)
        or (not method_ok)
        or (llm == "no")
        or (not date_ok and m.fidelity == "high")
    )

    notes = []
    if date_note:
        notes.append(date_note)
    if llm_reason:
        notes.append(llm_reason)

    return MappingCheck(
        field_mapping=m,
        source_exists=src_ok,
        date_range_ok=date_ok,
        fields_present=fields_ok,
        llm_reasonable=llm,
        llm_reason=llm_reason,
        blocking=blocking_flag,
        notes="; ".join(notes),
    )


def _haiku_reasonable(
    m: FieldMapping,
    spec: ReplicationSpec,
    use_cache: bool,
) -> dict:
    """Call Haiku for a reasonableness judgment. Returns {supports, reason}."""
    # Find the matching source's caveats
    caveats = []
    for s in CATALOG:
        if s["name"] == m.source_name:
            caveats = s["caveats"]
            break
    spec_context = _extract_spec_context(spec, m.spec_field)
    user_content = json.dumps(
        {
            "field_mapping": m.model_dump(mode="json"),
            "spec_context": spec_context,
            "source_caveats": caveats,
        },
        indent=2,
        default=str,
    )
    from src.specs.verification import SupportCheck  # reuse {yes,no,partial} shape

    system_prompt = load_prompt(PROMPT_NAME)
    check = call_claude(
        model=HAIKU_MODEL,
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=SupportCheck,
        use_cache=use_cache,
    )
    return {"supports": check.supports, "reason": check.reason}


def _extract_spec_context(spec: ReplicationSpec, field_path: str) -> dict:
    """Given a dotted field path like 'signal.inputs[0]', return the relevant
    portion of the spec for Haiku to reason about.
    """
    root = field_path.split(".")[0].split("[")[0]
    try:
        obj = getattr(spec, root)
        return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    except AttributeError:
        return spec.model_dump(mode="json")


def _overall_fidelity(checks: list[MappingCheck]) -> "Fidelity":  # type: ignore[name-defined]
    if not checks:
        return "low"
    fidelities = [c.field_mapping.fidelity for c in checks]
    # Any low → low overall; any medium → medium; all high → high.
    if any(f == "low" for f in fidelities):
        return "low"
    if any(f == "medium" for f in fidelities):
        return "medium"
    return "high"
