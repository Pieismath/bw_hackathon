# Where the agents struggle

A running, honest list of failure modes and known weaknesses in the
multi-agent pipeline. Mentor explicitly asked for this. These are not
weaknesses to hide — they are evidence the system is working: surfacing
its own limits via testing rather than glossing them.

For each item: what failed, what surfaced it, and how the system
handles it today.

---

## Extraction layer

### A1 conflates signal definition with execution timing

**What:** Early prompts allowed A1 to attach the same quote ("formed
immediately after the lagged returns are measured") to both
`signal.skip_months` (a signal-definition field) and
`rebalance.execution_lag_days` (a portfolio-formation-to-trade timing
field). These are orthogonal concepts but the paper's prose conflates them.

**Surfaced by:** prompt review during Phase 2 design. Anti-pattern 3 in
`methodology_extractor.md` codifies the rule: "the quote must support the
specific claim, not a related one."

**Today:** A2 catches this via Haiku's `support` check (output `"no"` →
loop back to A1 with feedback). A3 also reviews for "quote attached to
wrong field" as a category.

### A2 hedged toward `"partial"` when the rubric was loose

**What:** First-pass A2 prompt produced 4 `"partial"` and 0 `"yes"` on JT
even when the quote directly stated the field's primary claim. Haiku
treated `"yes"` as requiring exhaustive coverage of every sub-field
(min_price, market_cap, etc.), not just the field's primary claim.

**Surfaced by:** running A2 on JT and observing the distribution.

**Today:** A2 prompt was rewritten to define "primary claim" per field
and to instruct that absent sub-fields are covered by the AmbiguityFlag
list, not by demoting the support check. Re-run gave 3 `"yes"` + 1
`"partial"` (the rebalance quote did genuinely imply rather than state
the rebalance frequency).

### A3 schema forces 3 criticisms — sometimes leads to over-reaching

**What:** The `min_length=max_length=3` constraint on `criticisms` is
intentional ("no looks-good escape") but on a paper where A1 was
genuinely accurate, A3 may stretch to find the third criticism.

**Surfaced by:** schema design review.

**Today:** Acceptable trade-off. The cost of an occasional weak
criticism is much lower than the cost of A3 silently agreeing with A1.
A future tightening: allow severity = `"none"` for the third entry when
genuinely nothing significant remains.

---

## Implementation layer

### B1/B2 cannot isolate NYSE+AMEX from Yahoo data

**What:** Yahoo's symbol metadata has no exchange flag. JT 1993 was
NYSE+AMEX; our universe will silently include NASDAQ throughout, which
shifts the distribution toward smaller, more volatile stocks.

**Surfaced by:** B2's deterministic source-method existence check + LLM
reasonableness check (Haiku flagged it as `"no"` for the universe field).

**Today:** Surfaced in `BacktestResult.data_quality_flags` and in B2's
blocking_issues. The user-facing report names it explicitly; the engine
proceeds.

### B1/B2 triple-flag the same date-gap

**What:** When the spec's date range (e.g. JT 1965-1989) is outside
defeatbeta_yahoo's coverage (1994+), B1 sets fidelity correctly but B2's
deterministic date-coverage check flags `universe`, `signal.inputs`, and
`rebalance.frequency` mappings ALL as blocking — the same underlying
issue surfaced 3 times.

**Surfaced by:** running B1+B2 on JT.

**Today:** The Phase 5 report synthesizer will deduplicate by underlying
root cause. For now the redundancy is logged honestly.

### Capacity estimate is back-of-envelope

**What:** Step 11.3 uses a sqrt-impact heuristic with a single
calibration constant (K). A real Almgren-Chriss-style model would
require per-stock spread / volatility / participation modeling.

**Today:** Documented in the capacity stress test's `notes` field.
Phase 5.5 follow-up.

---

## Diagnosis layer

### D2's mutation space is constrained by spec validators

**What:** `PortfolioSpec` requires `long_bucket != short_bucket`. To
"swap legs" requires changing both fields — but D2 proposes one mutation
at a time. The single-variable mutation `long_bucket=10 → 1` collides
with `short_bucket=1` and ValidationError fires.

**Surfaced by:** D2's first run on JT (before the `signal.direction` fix)
produced 5 experiments with 2 validation rejections.

**Today:** Fixed indirectly: `signal.direction` is now functional, so D2
can flip the sign in a single mutation (`signal.direction: long_high → long_low`)
without triggering validators. The earlier limitation is preserved as
`PortfolioSpec` semantics for clarity.

### `signal.direction` was annotation-only until D2 surfaced it

**What:** The engine's `_compute_past_return` ignored `signal.direction`
and encoded direction implicitly via bucket integers. The field was
well-named and well-typed, so a code review would have missed it.

**Surfaced by:** D2's first experiment on JT mutated `signal.direction:
long_high → long_low` and the engine returned an identical mean_return.
Zero effect.

**Today:** Fixed. `_compute_past_return` now negates the signal when
`direction == "long_low"`. Regression test:
`tests/test_backtest_engine.py::test_past_return_signal_direction_inverts_sign`.

This is the canonical demo example of why an adversarial system that
runs actual computations beats code review for catching subtle bugs.

---

## Engine layer

### `±500%` monthly return clip is a defensive heuristic, not a real liquidity filter

**What:** `data_quality_flags` reports "N=856 observations clipped at
±500% due to suspected data artifacts" on JT. This handles MLMC-style
($500M / share) data corruption but doesn't address the broader Yahoo
universe quality issues (penny stocks, post-IPO/pre-delisting noise).

**Surfaced by:** Phase 1 KF MOM correlation gap on VW runs (before the
$10T market-cap filter was added).

**Today:** Two layers in place:
1. `±500%` per-month return clip in the engine (broad, blunt).
2. `$10T` market-cap cap on `get_market_cap_panel` (narrow, targeted).

A proper dollar-volume liquidity screen at universe-level is the next
fix (DESIGN_NOTES.md TODO).

### Decay profile (daily-resolution within holding periods) is not implemented

**What:** `RebalanceSpec.measure_daily_returns` exists in the schema but
the engine ignores it. Without daily decomposition, signal classification
falls back to lag-sweep half-life — coarser but informative.

**Today:** D3's `signal_type` field is computed from `lag_half_life_days`,
not daily decay. Documented as Phase 5.5 follow-up.

### Engine clip-and-rerun is the only mutation primitive

**What:** D2 proposes single-field mutations. Multi-field coupled
mutations (e.g. "swap long and short buckets atomically") aren't
expressible. For some causes, no single-field mutation fully closes the
gap; D2 picks the largest single-mutation `gap_delta` and labels it
`primary_cause` even when the real cause is coupled.

**Today:** Surfaced via the `primary_cause_kind` field on
`DivergenceDiagnosis` (`"coupled"` is a valid label). D2's narrative
explicitly notes when a swap couldn't be expressed as a single mutation.

---

## Out of scope (will fail loudly if encountered)

- **Non-equity asset classes.** UniverseSpec.asset_class accepts bond /
  fx / commodity / macro literals, but the engine and data store only
  exercise equity paths.
- **Fundamentals signals before 2019.** defeatbeta_yahoo's
  stock_statement coverage starts 2019-05; A1 may extract a 1965-1989
  fundamentals spec but B2 will block it with `low` fidelity.
- **Non-US universes.** The data store's only universe is
  `defeatbeta_all_equities` (US). Region literal accepts global /
  developed / emerging but no path implements them.
- **Daily-rebalanced strategies.** RebalanceSpec.frequency literal accepts
  `daily` but the engine's tranche logic is monthly-indexed. Will raise
  via the existing validator (`holding_period_months` requires
  `frequency == "monthly"`).

---

## Maintenance

When you find a new failure mode, add an entry here with: what failed,
what surfaced it, how the system handles it today. Mentor priority is
*honesty about limits*, not their absence.
