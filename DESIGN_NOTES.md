# Design Notes

Non-obvious decisions and bug postmortems. Short entries, dated, stable.

---

## 2026-04-24 — Tranche-activation boundary: strict `>` reclaimed 7/12 months

### Symptom

`run_backtest` with `holding_period_months=1` produced only 120 observations over 2000–2023 instead of the expected ~287. KF-convention (11,1,1) VW correlation with Ken French MOM was 0.19 — inconsistent with the engine's own long-short-consistency and determinism tests passing cleanly.

### Root cause

`src/engine/tranches.py::active_tranches_in` used strict `>` on the lower bound:

```python
lower = month - pd.DateOffset(months=holding_months)
# OLD: keeps tranches with (tr.formation_date > lower) and (tr.formation_date < month)
```

`pandas.DateOffset(months=1)` subtracted from a month-end lands on the **previous month's last day whenever the current month has more days than the previous**:

| t            | t − 1 month   | explanation                          |
| ------------ | ------------- | ------------------------------------ |
| 2000-02-29   | 2000-01-29    | Jan has ≥ 29 days, day preserved     |
| 2000-03-31   | 2000-02-29    | Feb has 29 days < 31, drops to last  |
| 2000-04-30   | 2000-03-30    | Mar has ≥ 30 days, preserved         |
| 2000-05-31   | 2000-04-30    | Apr has 30 < 31, drops               |
| …            | …             | …                                    |

For K=1 at `t = 2000-03-31`: `lower = 2000-02-29`. The tranche formed on 2000-02-29 (which should be the sole active tranche) had `formation_date > lower` evaluate to `False` (equal). It was dropped.

Pattern: 7 months per calendar year hit this boundary (Jan, Mar, May, Jul, Aug, Oct, Dec). Over 24 years, 24 × 5 = 120 months of observations survived — exactly matching the bug's signature.

### Fix

One character:

```python
# NEW:
if (tr.formation_date >= lower) and (tr.formation_date < month)
```

Semantically correct: a tranche formed at month-end M contributes to K measurement months {M+1, …, M+K}. Inclusive lower bound `[t − K, t)` captures this without date-arithmetic edge cases.

### Impact

After fix: 288 tranches built, 287 measurement months populated, KF-convention (11,1,1) EW correlation jumped from ~0.3 (with the gaps) to **0.80** (matching the engine's other validation signals).

### Regression test

`tests/test_backtest_engine.py::test_active_tranches_k1_boundary_includes_prior_month_end` exercises exactly this — tranches formed on each month-end of 2000-H1 with K=1, asserts each one is active exactly in the next month (the Feb 29 → Mar 31 case is the one that fails under the old strict `>`).

### Takeaway

Pandas `DateOffset` arithmetic is not time-reversible in general. Subtracting one month and adding one month back can change the date when crossing month-length boundaries. For bookkeeping that works on month-ends, use inclusive ranges `[lower, upper)` or index-based (positional) offsets on a pre-sorted calendar, never rely on strict date arithmetic for the boundary.

---

## 2026-04-24 — Phase 3+ roadmap: four features we chose and why

### Context

Phase 1+2 gave us a validated engine + a verified extraction pipeline. What's missing for a reviewer to trust the system as a *research tool* is evidence that (a) the strategy would actually be deployable, (b) it holds up under perturbation, (c) we're being honest about where we diverge from the paper, and (d) we can contextualize results without hallucinating. Each feature below maps 1:1 to one of those gaps.

### 1. Tradeability Scorecard

A high-Sharpe backtest on a signal with 800% annualized turnover and $50M capacity is a lab curiosity, not alpha. The scorecard surfaces the four numbers a CIO actually asks about — turnover, capacity, rebalance frequency, gross/net spread — then lets an LLM heuristic turn them into a plain-English verdict.

**Why an LLM on top of the numbers?** The metrics individually don't imply a verdict; the interaction does (high turnover is fine if capacity is also high, etc.). Hard-coding thresholds would bake in our opinions; a prompted heuristic keeps the reasoning legible and tweakable.

**Non-obvious constraint:** the verdict must cite the underlying metrics. Don't let the agent emit a bare `not_tradeable` — it needs to name which number drove the call, or a reviewer can't argue with it.

### 2. Robustness / decay stress test

The clearest sign of a brittle signal is that it dies when you delay execution by a few days — the alpha was entirely in the open-to-close move. Sweeping `signal_lag ∈ {1, 2, 5, 10}` and reporting the decay curve is the single cheapest robustness check; regime slicing (pre-2008, 2008, 2010s, 2020–2022) is the second. Heatmap rendering is important because the pattern matters more than any individual cell — monotonic decay is "fragile but honest", non-monotonic decay suggests a lookahead leak.

**Non-obvious constraint:** don't re-implement the engine. Wrap `run_backtest` with a parameter sweep; the engine should stay "one tested implementation" per the golden rules.

### 3. Paper-vs-replication diff

The single most convincing artifact for a judge. Most replications hide divergence from the paper behind a prose discussion; we can just put the numbers next to each other. Requires a new `PaperReportedMetrics` spec with `SupportingQuote`s for every claim — A2 verifies those quotes exactly like it verifies methodology quotes today.

**Non-obvious constraint:** don't bloat `ReplicationSpec` with reported-metrics fields. Keep it a separate spec so the methodology/extraction contract stays narrow.

### 4. News contextualization (guarded)

Highest hallucination risk in the roadmap, by a wide margin. An LLM asked "what happened to momentum in Q1 2009?" will happily invent plausible-sounding headlines. Ship this only with an A2-style verifier that requires each headline to have a retrievable URL and a publication date inside the drawdown window; anything that fails verification gets dropped silently rather than shown with a warning (warnings get ignored, missing items get noticed).

**Gate it behind an env flag and leave it off by default.** The reputational cost of one fabricated headline slipping through outweighs the marginal UX win on the tab being always-on.

### Takeaway

All four features share a pattern: derive a number from Phase 1+2 artifacts, layer an LLM interpretation on top, verify with a deterministic check. That's the same shape as A1+A2 — extract structured claims, then verify them mechanically. Keep that shape; it's what lets this system be trusted.

---
