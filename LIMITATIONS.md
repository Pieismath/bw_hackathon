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
- **Fundamentals signals before 2019-05.** defeatbeta_yahoo's
  stock_statement coverage starts 2019-05; A1 may extract a 1965-1989
  fundamentals spec but B2 will block it with `low` fidelity. (Phase E)
  The engine implements `fundamental_ratio` natively for the five
  ratios in `src/engine/signals.py::FUNDAMENTAL_RATIO_REGISTRY`, but
  emits a typed `SpecAdaptation(kind='fundamentals_unavailable_in_window')`
  when the spec's window pre-dates 2019-05 — a Bridgewater-style data
  source with longer coverage plugs in by satisfying the
  `FundamentalDataSource` Protocol without any engine changes.
- **Non-US universes.** The data store's only universe is
  `defeatbeta_all_equities` (US). Region literal accepts global /
  developed / emerging but no path implements them.
- **Daily-rebalanced strategies.** RebalanceSpec.frequency literal accepts
  `daily` but the engine's tranche logic is monthly-indexed. Will raise
  via the existing validator (`holding_period_months` requires
  `frequency == "monthly"`).

### Paper-id-keyed overrides retained as documented exceptions (Phase F)

After the Phase B–F dehardcode refactor, two narrow paper-id-keyed
overrides remain in the codebase. Both are config-shaped knobs with
in-code documentation explaining the property of the paper that justifies
the exception; both relate to JT-1993.

- **`extraction_verifier.PAPER_ID_OVERRIDES["jegadeesh_titman_1993"]
  ["universe.min_price"] = 10.0`** — the paper is silent on a price
  filter, but the published replication community (AQR, Asness-style
  follow-ups) consistently uses a $5-$10 floor to filter penny-stock
  noise. $10 matches the modern academic convention. An A1
  `AmbiguityFlag` would force the user to dial it; we accept the
  industry-standard default as an override.

- **`app/main.py::ENGINE_WINDOW_OVERRIDES["jegadeesh_titman_1993"] =
  (date(2007, 1, 1), date(2026, 4, 2))`** — JT's true sample window is
  1965-1989 but the defeatbeta panel only starts 1994-11-30. The
  default OOS window (`data_min → data_max`) would run on the 1995-2026
  panel, which is fine but includes the early-2000s tech-stock noise
  and the 2008-09 momentum crash without much context. The override
  picks 2007-2026 for a cleaner "post-crash momentum revival" demo. The
  spec's `start_date` / `end_date` still carry the paper's true window
  so the verdict strip's "Paper Window 1965-1989 → Replication Window
  2007-2026" tag remains honest.

For new papers, prefer A1's `AmbiguityFlag` defaults (industry-standard
convention as the default; surface as an ambiguity flag) or a typed
`PaperExtractionOverride` (`data/extraction_overrides/*.yaml`, bound to
a verbatim-quote checksum) over adding another paper-id entry here.

### Papers that need `PaperExtractionOverride` entries (Phase F)

Two papers in the active corpus need human-curated overrides before
they extract cleanly post-Phase-F. Until populated, the pipeline will
run but with degraded fidelity:

- **`lo_mackinlay_1988`** — text stream is byte-scrambled across the
  methodology section; pdfplumber and pypdfium2 both fail. A1 emits a
  single quote that A2 cannot verify. Phase F deleted the curated
  `VerificationReport` patch; the override mechanism replaces it but
  requires a human to transcribe a verbatim quote from a non-scrambled
  span (the abstract works) and compute the SHA-256 checksum via
  `src.specs.compute_quote_checksum`. See
  `src/specs/EXTRACTION_OVERRIDES.md` for the YAML schema. Until
  populated, A2's verification report will show 1/1 fail on the
  unverifiable quote and `headline_claim` will be None.

- **`asness_moskowitz_pedersen_2013`** — Table I cells are stored with
  mirrored character order; A1 cannot recover the verbatim US-momentum
  P3-P1 cell. Phase F deleted the hardcoded `_known_headline_claim`
  entry; same override mechanism applies. Until populated,
  `headline_claim` will be whatever A1 extracts (likely the global
  combo Sharpe ratio, which doesn't fit the `MonthlyLongShortReturn`
  variant — A1 will likely emit a high-severity `AmbiguityFlag` and
  fall through to `StatisticalTestClaim` or null).

---

---

## Engine layer (continued)

### Measurement loop is `O(K × N × T)` Python-level

**What:** Per measurement month the engine iterates `K` active tranches
× `N` weighted positions × pandas single-cell lookups. JT (K=6, N≈400)
runs in ~30-60s. DBT (K=36, N≈3000) is ~50× heavier — single backtest
takes ~3-5 min, full battery ~2 hours. This is why the OOS DBT run was
trimmed to four families (costs, liquidity, capacity, data_quality)
plus a D2 cap of 3 experiments.

**Surfaced by:** the OOS DBT pipeline's first attempt — engine baseline
took >10 min, full battery extrapolated to 2+ hours.

**Today:** trimmed-family runs supported via `--families` flag on
`scripts/run_oos_dbt_1985.py`; default set excludes `lag` (uninformative
at monthly granularity) and `subperiod` (11 reruns dominate cost).

**Fix path:** vectorize the measurement loop — stack tranche weights into
a `(K_active × N_symbols)` matrix, multiply by the period's
`(N_symbols × 1)` returns vector, sum. ~50-100× faster on long-K specs.
Estimated 1-2 hours of focused engine refactor. Phase 5+ work.

---

## Extraction layer (continued)

### A1 collapses imprecise paper language to binary buckets

**What:** When a paper describes its sorts as "the winner portfolio" and
"the loser portfolio" without explicitly naming a percentile (decile,
quintile, top-10, etc.), A1 sometimes collapses the construction to
`n_buckets=2, long_bucket=1, short_bucket=2`. DBT 1985 is the canonical
example: A1 extracted binary halves, but the paper actually used the
top-and-bottom decile of a 35-stock-each portfolio — i.e. `n_buckets=10,
long_bucket=10, short_bucket=1`.

This isn't a fabrication (A1's `supporting_quote` does say "winner
portfolio" / "loser portfolio") but it's a coverage gap: the bucket
size is unspecified in the cited sentence and A1 picked the most
literal reading instead of pursuing the precise decile boundary
elsewhere in the paper.

**Surfaced by:** the OOS DBT run on the post-fix engine — A1 emitted
`PortfolioSpec(construction="custom_sort", n_buckets=2, long_bucket=1,
short_bucket=2)` despite DBT explicitly using deciles in Table I and
discussion thereafter.

**Today:** logged. The downstream impact is real — binary buckets put
half the universe in each leg, which dilutes the signal and inflates
turnover-related noise. D2's mutation space could in principle propose
`n_buckets=10`, but the current proposer prompt prioritizes signal /
direction over construction details.

**Fix paths (ranked):**
1. Tighten the A1 prompt with an example: "if the paper says 'winner
   portfolio' and the table elsewhere shows decile sorts, set
   `n_buckets=10` and cite both the prose and the table caption."
2. Add a B2-side check: when `portfolio.n_buckets <= 2`, surface a
   medium-severity ambiguity flag specifically asking whether a finer
   sort is implied.
3. Promote `n_buckets` to a D2 mutation candidate when the paper's
   construction is ambiguous.

---

## Maintenance

When you find a new failure mode, add an entry here with: what failed,
what surfaced it, how the system handles it today. Mentor priority is
*honesty about limits*, not their absence.
