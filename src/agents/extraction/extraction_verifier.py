"""A2 — Extraction Verifier.

For every SupportingQuote in an A1-produced ReplicationSpec:
  1. Deterministic: call src.pdf.quote_verifier.verify(quote.text, pdf,
     expected_page=quote.page).
  2. LLM (Haiku): ask "does this quote logically support this field's
     claims?" — only if the deterministic verifier found the quote (no
     point asking Haiku about phantom text).
  3. Aggregate into a VerificationReport; classify each check as pass /
     fail based on severity + verifier status + support status.

Orchestration (`extract_and_verify`) wraps A1 + A2 with a retry loop:
  - A high-severity `not_found` or support `no` triggers an A1 retry with
    structured feedback appended to the user content.
  - `verified_wrong_page` is NOT a retry trigger (logged only).
  - `fuzzy_match` with `supports="yes"` is NOT a retry trigger.
  - Max 3 retries; on exhaustion, return the last result with overall
    confidence = low and the full retry history in the report.
"""

from __future__ import annotations

import json
from typing import Iterable

from src.agents.extraction.methodology_extractor import (
    MODEL as A1_MODEL,
    PROMPT_NAME as A1_PROMPT_NAME,
    format_pdf_as_user_content,
)
from src.pdf.parser import ParsedPDF
from src.pdf.quote_verifier import VerificationResult, verify
from src.specs import (
    QuoteVerification,
    ReplicationSpec,
    SupportCheck,
    VerificationReport,
    VerifiedReplicationSpec,
)
from src.specs.methodology import SensitivityPriority
from src.utils.llm import call_claude, load_prompt

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SUPPORT_PROMPT_NAME = "extraction_verifier_support"

# Map each path-rooted SupportingQuote to its severity tier. Severity drives
# retry triggers — a not_found at a `high` path forces a retry; at `medium`
# or `low` it's just logged.
FIELD_SEVERITY: dict[str, SensitivityPriority] = {
    "universe": "medium",
    "signal": "high",
    "portfolio": "high",
    "rebalance": "high",
    # The headline claim's quote is what D2 compares the engine against — a
    # fabricated or wrong-topic quote here would silently corrupt diagnosis.
    "headline_claim": "high",
}


# ---------------------------------------------------------------------------
# Support-check via Haiku
# ---------------------------------------------------------------------------

def haiku_support_check(
    field_path: str,
    field_claims: dict,
    quote_text: str,
    use_cache: bool = True,
) -> SupportCheck:
    """Ask Haiku whether `quote_text` supports `field_claims` at `field_path`."""
    system_prompt = load_prompt(SUPPORT_PROMPT_NAME)
    user_content = json.dumps(
        {
            "field_path": field_path,
            "field_claims": field_claims,
            "quote": quote_text,
        },
        indent=2,
        default=str,
    )
    return call_claude(
        model=HAIKU_MODEL,
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=SupportCheck,
        use_cache=use_cache,
    )


# ---------------------------------------------------------------------------
# Collect quotes from spec
# ---------------------------------------------------------------------------

def _collect_quotes(
    spec: ReplicationSpec,
) -> list[tuple[str, dict, "SupportingQuote", SensitivityPriority]]:
    """Yield (path, field_claims, quote, severity) for every SupportingQuote.

    Field claims exclude the supporting_quote itself so the Haiku check
    sees only the structural claim, not the evidence.
    """
    from src.specs.claims import SupportingQuote  # local import to keep circulars away

    collected: list = []
    for path in ("universe", "signal", "portfolio", "rebalance"):
        obj = getattr(spec, path)
        sq = obj.supporting_quote
        if sq is not None:
            claims = obj.model_dump(exclude={"supporting_quote"}, mode="json")
            collected.append((path, claims, sq, FIELD_SEVERITY[path]))

    if spec.headline_claim is not None:
        hc = spec.headline_claim
        claims = hc.model_dump(exclude={"supporting_quote"}, mode="json")
        collected.append(
            ("headline_claim", claims, hc.supporting_quote, FIELD_SEVERITY["headline_claim"])
        )

    for i, flag in enumerate(spec.ambiguities):
        if flag.paper_evidence is not None:
            path = f"ambiguities[{i}]"
            claims = flag.model_dump(exclude={"paper_evidence"}, mode="json")
            collected.append((path, claims, flag.paper_evidence, flag.sensitivity_priority))
    return collected


# ---------------------------------------------------------------------------
# Per-check classification
# ---------------------------------------------------------------------------

def _classify_check(
    verifier_result: VerificationResult,
    support: SupportCheck | None,
    severity: SensitivityPriority,
) -> tuple[bool, str | None]:
    """Decide whether a single check has failed, per the retry-trigger rules.

    Returns (failed, failure_reason). Fails when:
      - verifier status is not_found or page_out_of_range (hard fail, any severity)
      - support is "no" (any severity — a wrong-topic quote is always wrong)
      - verifier status is fuzzy_wrong_page at high severity (quote misplaced
        AND only fuzzy — worth a retry)
    Does NOT fail on:
      - verified_wrong_page (logged only per spec)
      - fuzzy_match with support yes/partial
    """
    s = verifier_result.status
    if s in ("not_found", "page_out_of_range"):
        return True, f"verifier: {s}"
    if support is not None and support.supports == "no":
        return True, f"support: no ({support.reason})"
    if s == "fuzzy_wrong_page" and severity == "high":
        return True, "verifier: fuzzy_wrong_page at high-severity field"
    return False, None


# ---------------------------------------------------------------------------
# Public: verify_spec (no retries)
# ---------------------------------------------------------------------------

def verify_spec(
    spec: ReplicationSpec,
    pdf: ParsedPDF,
    use_llm_check: bool = True,
    use_cache: bool = True,
) -> VerificationReport:
    """Run the full A2 pipeline on a single spec — no retries.

    `use_llm_check=False` is useful in tests that only want to exercise the
    deterministic path.
    """
    checks: list[QuoteVerification] = []
    for path, field_claims, quote, severity in _collect_quotes(spec):
        vr = verify(quote.text, pdf, expected_page=quote.page)

        support: SupportCheck | None = None
        if use_llm_check and vr.status in (
            "verified",
            "verified_wrong_page",
            "fuzzy_match",
            "fuzzy_wrong_page",
        ):
            support = haiku_support_check(
                field_path=path,
                field_claims=field_claims,
                quote_text=quote.text,
                use_cache=use_cache,
            )

        failed, reason = _classify_check(vr, support, severity)
        checks.append(
            QuoteVerification(
                field_path=path,
                quote=quote,
                severity=severity,
                verification_status=vr.status,
                verified_page=vr.page,
                verification_confidence=vr.confidence,
                support_check=support,
                failed=failed,
                failure_reason=reason,
            )
        )

    n_failed_high = sum(1 for c in checks if c.failed and c.severity == "high")
    n_failed_medium = sum(1 for c in checks if c.failed and c.severity == "medium")
    n_failed_low = sum(1 for c in checks if c.failed and c.severity == "low")
    overall = _overall_confidence(n_failed_high, n_failed_medium, n_failed_low)

    return VerificationReport(
        checks=tuple(checks),
        overall_confidence=overall,
        n_checks=len(checks),
        n_failed_high=n_failed_high,
        n_failed_medium=n_failed_medium,
        n_failed_low=n_failed_low,
    )


def _overall_confidence(
    n_high: int, n_medium: int, n_low: int
) -> "OverallConfidence":  # type: ignore[name-defined]
    if n_high > 0:
        return "low"
    if n_medium > 1:
        return "low"
    if n_medium == 1 or n_low > 2:
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# Retry feedback construction
# ---------------------------------------------------------------------------

def should_retry(report: VerificationReport) -> bool:
    """Retry trigger: any high-severity failure."""
    return report.n_failed_high > 0


def build_retry_feedback(report: VerificationReport) -> str:
    """Turn failed checks into a concrete user-message addendum for A1.

    The feedback NAMES specific quote texts and locations so the model
    can target the fix instead of regenerating blindly.
    """
    lines = [
        "RETRY FEEDBACK FROM VERIFIER (re-extract, fix these specific issues):",
    ]
    for c in report.checks:
        if not c.failed:
            continue
        if c.verification_status in ("not_found", "page_out_of_range"):
            lines.append(
                f"  - {c.field_path}.supporting_quote: FABRICATED — "
                f"quote text {c.quote.text[:100]!r} was {c.verification_status} "
                f"(claimed page {c.quote.page}). Find a DIFFERENT verbatim sentence "
                "that genuinely appears in the PDF, or drop the quote and raise "
                "an AmbiguityFlag instead."
            )
        elif c.support_check and c.support_check.supports == "no":
            lines.append(
                f"  - {c.field_path}.supporting_quote: WRONG TOPIC — quote is "
                f"verbatim but does not support this field's claim. Verifier "
                f"reason: {c.support_check.reason[:200]}. Pick a sentence that "
                "actually grounds the field's claim, or drop the quote and "
                "raise an AmbiguityFlag."
            )
        elif c.verification_status == "fuzzy_wrong_page":
            lines.append(
                f"  - {c.field_path}.supporting_quote: WRONG PAGE + LOW MATCH — "
                f"claimed page {c.quote.page} but text best matches page "
                f"{c.verified_page} at confidence {c.verification_confidence:.2f}. "
                "Re-cite with the correct page AND verbatim text."
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full orchestration: A1 + A2 with retry loop
# ---------------------------------------------------------------------------

def extract_and_verify(
    pdf: ParsedPDF,
    max_retries: int = 3,
    use_cache: bool = True,
    use_llm_check: bool = True,
) -> VerifiedReplicationSpec:
    """Full A1 + A2 orchestration. Up to `max_retries` A1 regenerations on
    high-severity verifier failures."""
    from src.agents.extraction import extract_methodology

    feedback_history: list[str] = []
    spec: ReplicationSpec | None = None
    report: VerificationReport | None = None

    for attempt in range(max_retries + 1):  # attempt 0 = first shot
        if attempt == 0:
            spec = extract_methodology(pdf, use_cache=use_cache)
        else:
            # Reissue A1 with the failure feedback appended. Cache key
            # naturally differs because user_content changes.
            pdf_content = format_pdf_as_user_content(pdf)
            feedback_addendum = "\n\n---\n\n" + "\n\n---\n\n".join(feedback_history)
            system_prompt = load_prompt(A1_PROMPT_NAME)
            spec = call_claude(
                model=A1_MODEL,
                system_prompt=system_prompt,
                user_content=pdf_content + feedback_addendum,
                response_schema=ReplicationSpec,
                use_cache=use_cache,
            )

        report = verify_spec(
            spec, pdf, use_llm_check=use_llm_check, use_cache=use_cache
        )
        if not should_retry(report):
            break
        if attempt >= max_retries:
            break
        feedback_history.append(build_retry_feedback(report))

    assert spec is not None and report is not None
    # Self-consistency check on headline_claim: a non-zero Newey-West t-stat
    # paired with monthly_return == 0.0 is mathematically impossible
    # (t = mean·√N / std ⇒ mean=0 ⇒ t=0). When A1 emits this combination it
    # has hallucinated a placeholder zero for the return field while
    # extracting a real t-stat from elsewhere in the paper — exactly what
    # happened on AQR Streaks. Downgrade to None so D2 skips and the
    # frontend doesn't render "+0.000%/mo" with a +2.67 t-stat next to it.
    if (
        spec.headline_claim is not None
        and spec.headline_claim.monthly_return == 0.0
        and spec.headline_claim.t_stat is not None
        and spec.headline_claim.t_stat != 0.0
    ):
        bad = spec.headline_claim
        note_addendum = (
            f" [headline_claim dropped: A1 emitted monthly_return=0.0 paired "
            f"with t_stat={bad.t_stat} — mathematically impossible. The paper "
            f"reports a real headline number that A1 failed to extract; D2 will "
            f"skip until headline_claim is supplied manually.]"
        )
        spec = spec.model_copy(update={
            "headline_claim": None,
            "notes": (spec.notes + note_addendum)[:].strip(),
        })

    # Record retry metadata on the report
    report_with_meta = report.model_copy(
        update={
            "retry_count": len(feedback_history),
            "retry_feedback_history": tuple(feedback_history),
        }
    )
    return VerifiedReplicationSpec(spec=spec, report=report_with_meta)
