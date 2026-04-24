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
