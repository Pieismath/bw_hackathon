"""D2 — Divergence Diagnostician.

Given a gap between a paper's claim and our engine's replicated result,
D2 runs a small set of engine reruns with one spec parameter mutated at
a time, measures what actually happens to the gap, and reports a
structured diagnosis. The LLM proposes mutations and writes the final
diagnosis narrative; the canonical backtest engine produces all numbers.

Strict separation enforced by this module's architecture:
  - `_propose_mutation` — Opus 4.7 call, outputs a MutationProposal.
  - `apply_mutation`     — pure code, deterministic.
  - `run_backtest_cached`— pure code, disk-cached on spec hash + bps.
  - `_summarize_diagnosis` — Opus 4.7 call, outputs DivergenceDiagnosis.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.agents.validation.result_comparator import (
    DEFAULT_TOLERANCE,
    classify,
    compare_claim,
)
from src.data.cache import QueryCache
from src.data.store import PointInTimeDataStore
from src.engine import run_backtest
from src.specs import (
    BacktestResult,
    ClaimComparison,
    DivergenceDiagnosis,
    MutationProposal,
    MutationResult,
    PaperClaim,
    ReplicationSpec,
)
from src.utils.llm import call_claude, load_prompt

MODEL = "claude-opus-4-7"
PROPOSE_PROMPT = "divergence_diagnostician_propose"
SUMMARIZE_PROMPT = "divergence_diagnostician_summarize"

# Early-exit thresholds. Same-sign + magnitude within 2x tolerance is a
# "good-enough" outcome that doesn't warrant burning more experiments.
EARLY_EXIT_GAP_THRESHOLD = 2.0 * DEFAULT_TOLERANCE  # 0.2%/mo
MAX_EXPERIMENTS_DEFAULT = 6

_backtest_cache: QueryCache | None = None


def _get_backtest_cache() -> QueryCache:
    global _backtest_cache
    if _backtest_cache is None:
        _backtest_cache = QueryCache(Path("data/cache/backtest_cache"))
    return _backtest_cache


# ---------------------------------------------------------------------------
# Spec mutation (deterministic)
# ---------------------------------------------------------------------------

def apply_mutation(spec: ReplicationSpec, parameter: str, to_value: str) -> ReplicationSpec:
    """Return a new ReplicationSpec with `parameter` set to `to_value`.

    `parameter` supports dotted paths up to depth 2 (e.g. `"signal.skip_months"`).
    Pydantic's `model_validate` coerces the string to the target type.
    """
    parts = parameter.split(".")
    if not parts or any(not p for p in parts):
        raise ValueError(f"invalid parameter path: {parameter!r}")
    if len(parts) > 2:
        raise ValueError(
            f"parameter path depth > 2 not supported: {parameter!r}"
        )
    dumped = spec.model_dump()
    ref = dumped
    for p in parts[:-1]:
        if p not in ref:
            raise KeyError(f"spec has no field {p!r} (full path: {parameter})")
        ref = ref[p]
    leaf = parts[-1]
    if leaf not in ref:
        raise KeyError(f"spec has no field {leaf!r} (full path: {parameter})")
    ref[leaf] = to_value  # Pydantic will coerce on re-validation
    return ReplicationSpec.model_validate(dumped)


def read_current_value(spec: ReplicationSpec, parameter: str) -> str:
    """Return the current value of `parameter` (dotted path) as a string."""
    parts = parameter.split(".")
    ref: object = spec
    for p in parts:
        if not hasattr(ref, p):
            raise KeyError(f"spec has no attribute {p!r}")
        ref = getattr(ref, p)
    return str(ref)


# ---------------------------------------------------------------------------
# Backtest cache (disk, keyed on spec hash + bps)
# ---------------------------------------------------------------------------

def _cache_key_for_backtest(spec: ReplicationSpec, bps: float) -> str:
    payload = spec.model_dump_json() + f"|bps={bps}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"backtest__{digest}"


def run_backtest_cached(
    spec: ReplicationSpec,
    store: PointInTimeDataStore,
    transaction_cost_bps: float = 0.0,
) -> BacktestResult:
    """Disk-cached wrapper around the engine. Keyed on spec hash + bps."""
    key = _cache_key_for_backtest(spec, transaction_cost_bps)
    cache = _get_backtest_cache()
    return cache.get_or_compute(
        key,
        lambda: run_backtest(spec, store, transaction_cost_bps=transaction_cost_bps),
    )


# ---------------------------------------------------------------------------
# LLM: propose next mutation
# ---------------------------------------------------------------------------

def _propose_mutation(
    spec: ReplicationSpec,
    claim: PaperClaim,
    baseline_result: BacktestResult,
    baseline_comparison: ClaimComparison,
    experiments_so_far: list[MutationResult],
    max_experiments: int,
    use_cache: bool = True,
) -> MutationProposal:
    system_prompt = load_prompt(PROPOSE_PROMPT)
    user_blob = {
        "spec": spec.model_dump(mode="json"),
        "claim": claim.model_dump(mode="json"),
        "baseline_backtest": {
            "mean_return": baseline_result.mean_return,
            "tstat": baseline_result.alpha_tstat,
            "n_periods": baseline_result.n_periods,
            "data_quality_flags": list(baseline_result.data_quality_flags),
            "verdict": baseline_comparison.verdict,
            "absolute_gap": baseline_comparison.absolute_gap,
        },
        "ambiguities": [a.model_dump(mode="json") for a in spec.ambiguities],
        "experiments_so_far": [r.model_dump(mode="json") for r in experiments_so_far],
        "max_experiments": max_experiments,
    }
    return call_claude(
        model=MODEL,
        system_prompt=system_prompt,
        user_content=json.dumps(user_blob, indent=2, default=str),
        response_schema=MutationProposal,
        use_cache=use_cache,
    )


def _summarize_diagnosis(
    claim: PaperClaim,
    baseline_result: BacktestResult,
    baseline_comparison: ClaimComparison,
    mutation_results: list[MutationResult],
    early_exit: bool,
    early_exit_reason: str | None,
    use_cache: bool = True,
) -> DivergenceDiagnosis:
    system_prompt = load_prompt(SUMMARIZE_PROMPT)
    user_blob = {
        "claim": claim.model_dump(mode="json"),
        "baseline": {
            "mean_return": baseline_result.mean_return,
            "tstat": baseline_result.alpha_tstat,
            "verdict": baseline_comparison.verdict,
            "absolute_gap": baseline_comparison.absolute_gap,
        },
        "mutation_results": [r.model_dump(mode="json") for r in mutation_results],
        "early_exit": early_exit,
        "early_exit_reason": early_exit_reason,
    }
    return call_claude(
        model=MODEL,
        system_prompt=system_prompt,
        user_content=json.dumps(user_blob, indent=2, default=str),
        response_schema=DivergenceDiagnosis,
        use_cache=use_cache,
    )


# ---------------------------------------------------------------------------
# Public: diagnose
# ---------------------------------------------------------------------------

def diagnose(
    spec: ReplicationSpec,
    baseline_result: BacktestResult,
    claim: PaperClaim,
    store: PointInTimeDataStore,
    max_experiments: int = MAX_EXPERIMENTS_DEFAULT,
    tolerance: float = DEFAULT_TOLERANCE,
    use_cache: bool = True,
    transaction_cost_bps: float = 0.0,
) -> DivergenceDiagnosis:
    """Run the D2 loop end-to-end: mutate -> rerun -> measure -> repeat -> diagnose."""
    baseline_comparison = compare_claim(
        claim,
        baseline_result.mean_return,
        replicated_tstat=baseline_result.alpha_tstat,
        tolerance=tolerance,
    )

    current_spec = spec
    mutation_results: list[MutationResult] = []
    tested_mutations: set[tuple[str, str]] = set()
    early_exit = False
    early_exit_reason: str | None = None

    for i in range(max_experiments):
        try:
            proposal = _propose_mutation(
                spec=current_spec,
                claim=claim,
                baseline_result=baseline_result,
                baseline_comparison=baseline_comparison,
                experiments_so_far=mutation_results,
                max_experiments=max_experiments,
                use_cache=use_cache,
            )
        except Exception as e:
            early_exit = True
            early_exit_reason = f"proposer failed: {e}"
            break

        mut_key = (proposal.parameter, proposal.to_value)
        if mut_key in tested_mutations:
            early_exit = True
            early_exit_reason = (
                f"proposer repeated mutation {mut_key} — no new directions to explore"
            )
            break
        tested_mutations.add(mut_key)

        # Read current value BEFORE applying.
        try:
            from_value_human = read_current_value(current_spec, proposal.parameter)
        except KeyError as e:
            mutation_results.append(
                MutationResult(
                    proposal=proposal,
                    from_value_human=f"<unknown: {e}>",
                    pre_abs_gap=abs(baseline_comparison.absolute_gap),
                    post_abs_gap=abs(baseline_comparison.absolute_gap),
                    gap_delta=0.0,
                    pre_mean_return=baseline_result.mean_return,
                    post_mean_return=baseline_result.mean_return,
                    pre_tstat=baseline_result.alpha_tstat,
                    post_tstat=baseline_result.alpha_tstat,
                    verdict_before=baseline_comparison.verdict,
                    verdict_after=baseline_comparison.verdict,
                    closed_sign_flip=False,
                    notes=f"invalid parameter path, skipped: {e}",
                )
            )
            continue

        # Apply + re-validate
        try:
            mutated_spec = apply_mutation(
                current_spec, proposal.parameter, proposal.to_value
            )
        except Exception as e:
            mutation_results.append(
                MutationResult(
                    proposal=proposal,
                    from_value_human=from_value_human,
                    pre_abs_gap=abs(baseline_comparison.absolute_gap),
                    post_abs_gap=abs(baseline_comparison.absolute_gap),
                    gap_delta=0.0,
                    pre_mean_return=baseline_result.mean_return,
                    post_mean_return=baseline_result.mean_return,
                    pre_tstat=baseline_result.alpha_tstat,
                    post_tstat=baseline_result.alpha_tstat,
                    verdict_before=baseline_comparison.verdict,
                    verdict_after=baseline_comparison.verdict,
                    closed_sign_flip=False,
                    notes=f"mutation failed to apply: {e}",
                )
            )
            continue

        # Rerun engine (cached)
        try:
            new_result = run_backtest_cached(
                mutated_spec, store, transaction_cost_bps=transaction_cost_bps
            )
        except Exception as e:
            early_exit = True
            early_exit_reason = f"engine failed on mutation {mut_key}: {e}"
            break

        # Classify against baseline claim
        post_comparison = compare_claim(
            claim,
            new_result.mean_return,
            replicated_tstat=new_result.alpha_tstat,
            tolerance=tolerance,
        )

        pre_abs = abs(baseline_comparison.absolute_gap)
        post_abs = abs(post_comparison.absolute_gap)
        mut_result = MutationResult(
            proposal=proposal,
            from_value_human=from_value_human,
            pre_abs_gap=pre_abs,
            post_abs_gap=post_abs,
            gap_delta=pre_abs - post_abs,
            pre_mean_return=baseline_result.mean_return,
            post_mean_return=new_result.mean_return,
            pre_tstat=baseline_result.alpha_tstat,
            post_tstat=new_result.alpha_tstat,
            verdict_before=baseline_comparison.verdict,
            verdict_after=post_comparison.verdict,
            closed_sign_flip=(
                baseline_comparison.verdict == "opposite_sign"
                and post_comparison.verdict != "opposite_sign"
            ),
            notes=(
                f"mutation {proposal.parameter} from {from_value_human!r} "
                f"to {proposal.to_value!r}; verdict {baseline_comparison.verdict} "
                f"-> {post_comparison.verdict}"
            ),
        )
        mutation_results.append(mut_result)

        # Early exit conditions, in priority order:
        # 1. Verdict is match (|gap| <= 1 * tolerance, same sign).
        # 2. Verdict is partial (|gap| <= 2 * tolerance, same sign).
        # 3. Sign-flip closed AND |gap| reduced by >= 50% of baseline. This
        #    fires when one clean mutation identifies the primary cause
        #    even if a meaningful residual remains — no point burning more
        #    experiments on single-variable alternatives when the dominant
        #    cause is already established.
        if post_comparison.verdict == "match":
            early_exit = True
            early_exit_reason = (
                f"gap within tolerance on experiment {i + 1}: "
                f"|gap|={post_abs:.4f}"
            )
            break
        if (
            post_comparison.verdict == "partial"
            and post_abs <= EARLY_EXIT_GAP_THRESHOLD
        ):
            early_exit = True
            early_exit_reason = (
                f"gap closed on experiment {i + 1}: "
                f"verdict partial, |gap|={post_abs:.4f}"
            )
            break
        if (
            mut_result.closed_sign_flip
            and post_abs < 0.5 * pre_abs
        ):
            early_exit = True
            early_exit_reason = (
                f"primary cause identified on experiment {i + 1}: "
                f"sign flip closed, |gap| reduced from {pre_abs:.4f} to "
                f"{post_abs:.4f} ({(1 - post_abs/pre_abs)*100:.0f}% of gap closed)"
            )
            break

    # Final diagnosis narrative
    try:
        return _summarize_diagnosis(
            claim=claim,
            baseline_result=baseline_result,
            baseline_comparison=baseline_comparison,
            mutation_results=mutation_results,
            early_exit=early_exit,
            early_exit_reason=early_exit_reason,
            use_cache=use_cache,
        )
    except Exception as e:
        # Fallback: synthesize a minimal diagnosis without the LLM narrative
        # so the pipeline still produces a structured result.
        return _fallback_diagnosis(
            mutation_results,
            baseline_comparison,
            early_exit,
            early_exit_reason,
            f"summarizer failed: {e}",
        )


def _fallback_diagnosis(
    mutation_results: list[MutationResult],
    baseline_comparison: ClaimComparison,
    early_exit: bool,
    early_exit_reason: str | None,
    note: str,
) -> DivergenceDiagnosis:
    best = max(mutation_results, key=lambda r: r.gap_delta, default=None)
    primary = best.proposal.parameter if best else "no_single_cause_identified"
    tested = tuple(dict.fromkeys(r.proposal.parameter for r in mutation_results))
    ruled_out = tuple(
        r.proposal.parameter for r in mutation_results if r.gap_delta <= 0
    )
    residual = (
        best.post_abs_gap if best else abs(baseline_comparison.absolute_gap)
    )
    # Heuristic cause-kind in fallback: if the best mutation closed >=80% of
    # the gap, call it single_field; otherwise other. LLM summarizer would
    # give a more nuanced label but this keeps the schema satisfied.
    if best and abs(baseline_comparison.absolute_gap) > 0:
        closed_fraction = best.gap_delta / abs(baseline_comparison.absolute_gap)
    else:
        closed_fraction = 0.0
    if closed_fraction >= 0.80:
        kind: "PrimaryCauseKind" = "single_field"  # type: ignore[name-defined]
    else:
        kind = "other"
    summary = (
        f"Fallback heuristic: mutation {primary} closed {closed_fraction*100:.0f}% "
        f"of the original gap."
        if best
        else "Fallback heuristic: no mutation closed the gap."
    )
    return DivergenceDiagnosis(
        primary_cause=primary,
        primary_cause_kind=kind,
        primary_cause_summary=summary[:300],
        primary_cause_evidence=(
            f"fallback: largest gap_delta was {best.gap_delta:.4f} "
            f"from mutation {best.proposal.parameter}={best.proposal.to_value!r}"
            if best
            else "fallback: no experiments succeeded"
        ),
        experiments_run=len(mutation_results),
        mutation_results=tuple(mutation_results),
        alternatives_tested=tested,
        alternatives_ruled_out=ruled_out,
        residual_abs_gap=residual,
        residual_gap_likely_cause=(
            f"{note} (fallback narrative; LLM summarizer unavailable)"
        ),
        confidence="low",
        early_exit=early_exit,
        early_exit_reason=early_exit_reason,
    )
