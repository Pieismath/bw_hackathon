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
    """Run D3 — produces a `RobustnessJudgment` from a scorecard."""
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
    return call_claude(
        model=MODEL,
        system_prompt=load_prompt(PROMPT_NAME),
        user_content=json.dumps(user_blob, indent=2, default=str),
        response_schema=RobustnessJudgment,
        use_cache=use_cache,
    )
