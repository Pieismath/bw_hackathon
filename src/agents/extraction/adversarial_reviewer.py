"""A3 — Adversarial Reviewer.

Opus 4.7 reads the paper + A1's extraction + A2's verification report,
then emits exactly three structured criticisms (missed / oversimplified /
alternative_interpretation). The orchestrator can fold high-severity
criticisms into the spec's `ambiguities` list so downstream diagnosis
(D2) sees them as candidates for mutation-and-rerun.
"""

from __future__ import annotations

import json

from src.agents.extraction.methodology_extractor import format_pdf_as_user_content
from src.pdf.parser import ParsedPDF
from src.specs import (
    AdversarialCritique,
    AmbiguityFlag,
    ReplicationSpec,
    SupportingQuote,
    VerificationReport,
)
from src.utils.llm import call_claude, load_prompt

MODEL = "claude-opus-4-7"
PROMPT_NAME = "adversarial_reviewer"


def review(
    pdf: ParsedPDF,
    spec: ReplicationSpec,
    verification_report: VerificationReport | None = None,
    use_cache: bool = True,
) -> AdversarialCritique:
    """Run A3 on an A1 extraction. Returns exactly 3 criticisms."""
    user_content = _build_user_content(pdf, spec, verification_report)
    system_prompt = load_prompt(PROMPT_NAME)
    return call_claude(
        model=MODEL,
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=AdversarialCritique,
        use_cache=use_cache,
    )


def _build_user_content(
    pdf: ParsedPDF,
    spec: ReplicationSpec,
    verification_report: VerificationReport | None,
) -> str:
    paper_text = format_pdf_as_user_content(pdf)
    extracted_spec = spec.model_dump(mode="json")
    report_summary = (
        _summarize_report(verification_report)
        if verification_report is not None
        else None
    )
    blob = {
        "extracted_spec": extracted_spec,
        "verification_report": report_summary,
    }
    return (
        "paper_text below, followed by JSON with extracted_spec and "
        "verification_report:\n\n"
        + paper_text
        + "\n\n---\n\n"
        + json.dumps(blob, indent=2, default=str)
    )


def _summarize_report(report: VerificationReport) -> dict:
    return {
        "overall_confidence": report.overall_confidence,
        "n_checks": report.n_checks,
        "n_failed_high": report.n_failed_high,
        "n_failed_medium": report.n_failed_medium,
        "checks": [
            {
                "field": c.field_path,
                "status": c.verification_status,
                "supports": c.support_check.supports if c.support_check else None,
                "failed": c.failed,
            }
            for c in report.checks
        ],
    }


def fold_high_severity_into_spec(
    spec: ReplicationSpec,
    critique: AdversarialCritique,
) -> ReplicationSpec:
    """Append high-severity criticisms as new AmbiguityFlag entries on the spec.

    Medium / low criticisms are not folded — they're logged by the caller
    and appear in the final report but don't mutate the spec.
    """
    new_flags: list[AmbiguityFlag] = []
    for c in critique.criticisms:
        if c.severity != "high":
            continue
        new_flags.append(
            AmbiguityFlag(
                parameter=f"a3.{c.category}",
                default_chosen=c.proposed_remediation[:200],
                alternatives=(),
                paper_evidence=c.evidence_quote,
                sensitivity_priority="high",
                reason=(
                    f"[A3 Adversarial Reviewer, category={c.category}] "
                    + c.description[:200]
                ),
            )
        )
    if not new_flags:
        return spec
    combined = spec.ambiguities + tuple(new_flags)
    return spec.model_copy(update={"ambiguities": combined})
