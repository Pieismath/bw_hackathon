"""D3 — Robustness Adversary.

Reads a `RobustnessScorecard` plus the original `BacktestResult`, paper
claim, and (optional) D2 diagnosis. Produces a structured
`RobustnessJudgment` containing implementable alpha, signal-type
classification, primary failure modes, and gap-attribution.

Like all agents in this codebase, D3 may NOT invent numbers. Every claim
in its output must reference a specific test row in the input scorecard.
"""

from __future__ import annotations

import json

from src.specs import (
    BacktestResult,
    DivergenceDiagnosis,
    PaperClaim,
    RobustnessJudgment,
    RobustnessScorecard,
)
from src.utils.llm import call_claude, load_prompt

MODEL = "claude-opus-4-7"
PROMPT_NAME = "robustness_adversary"


def judge(
    scorecard: RobustnessScorecard,
    baseline: BacktestResult,
    claim: PaperClaim,
    diagnosis: DivergenceDiagnosis | None = None,
    use_cache: bool = True,
) -> RobustnessJudgment:
    """Run D3 — produces a `RobustnessJudgment` from a scorecard.

    On any LLM failure (529 overload, missing API key, validation error
    after retries) returns a deterministic fallback judgment so the
    pipeline still produces a structured result and the verdict strip
    can render. Mirrors D2's `_fallback_diagnosis`.
    """
    user_blob = {
        "scorecard": scorecard.model_dump(mode="json"),
        "baseline": {
            "mean_return": baseline.mean_return,
            "alpha_tstat": baseline.alpha_tstat,
            "n_periods": baseline.n_periods,
            "data_quality_flags": list(baseline.data_quality_flags),
        },
        "claim": claim.model_dump(mode="json"),
        "diagnosis": (
            diagnosis.model_dump(mode="json") if diagnosis is not None else None
        ),
    }
    try:
        return call_claude(
            model=MODEL,
            system_prompt=load_prompt(PROMPT_NAME),
            user_content=json.dumps(user_blob, indent=2, default=str),
            response_schema=RobustnessJudgment,
            use_cache=use_cache,
        )
    except Exception as e:
        return _fallback_judgment(scorecard, baseline, note=f"D3 LLM call failed: {e}")


def _fallback_judgment(
    scorecard: RobustnessScorecard,
    baseline: BacktestResult,
    note: str,
) -> RobustnessJudgment:
    """Deterministic fallback when the LLM is unavailable.

    Surfaces the scorecard's own counts and fragility signals so the UI
    has something to render. Confidence is pinned to "low" so consumers
    treat the verdict as provisional.
    """
    return RobustnessJudgment(
        surviving_count=scorecard.n_surviving,
        n_tests=scorecard.n_tests,
        fragility_signals=scorecard.fragility_signals,
        implementable_alpha=baseline.mean_return,
        implementable_alpha_basis=(
            "fallback: baseline mean_return used because D3 LLM was unavailable"
        ),
        signal_type="not_evaluated",
        capacity_estimate_usd=scorecard.capacity_estimate_usd,
        primary_failure_modes=scorecard.fragility_signals[:5],
        gap_attribution="unexplained",
        gap_attribution_evidence=(
            f"fallback: gap attribution skipped because D3 LLM was unavailable. {note}"
        )[:600],
        confidence="low",
        summary="D3 LLM call failed; deterministic fallback values used.",
    )
