Paper Replication Machine — Detailed Pipeline
Entry: PDF Upload
A researcher drops a quant finance paper (PDF) into the system via the web UI. The system immediately kicks off the orchestration graph — a LangGraph DAG where every step is a node with explicit state passing. Nothing runs speculatively; each node waits for its upstream inputs and produces validated outputs before the next node runs.

The pipeline state object travels through every node and accumulates results. At the end, that state object contains the full audit trail: every agent's inputs, outputs, prompts, retries, and cached responses.

Step 1 — PDF Parsing (deterministic)
Module: src/pdf/parser.py Inputs: raw PDF bytes Outputs: structured text with page tracking

The pdfplumber library extracts every line of text from every page. Unlike simpler PDF parsers, pdfplumber preserves:

Line-level page numbers (so every text span knows its page)
Table structure (tables are extracted as row/column data, not flat text)
Reading order within each page
The output isn't just a flat string — it's a structured object where you can query "give me all text on page 7" or "give me the table labeled 'Table 2' from page 12." This structure is essential for later quote verification.

Failure modes handled: encrypted PDFs fail loudly, scanned/image PDFs trigger a warning that OCR quality will affect extraction, multi-column layouts are detected and reading order corrected.

Step 2 — Methodology Extractor (Agent A1)
Model: claude-opus-4-7 Module: src/agents/extraction/methodology_extractor.py Prompt: src/agents/prompts/methodology_extractor.md Inputs: parsed PDF text with page numbers Outputs: ReplicationSpec object + list of AmbiguityFlag objects

The first LLM call in the pipeline. Claude Opus reads the full PDF text and fills out a Pydantic schema — the ReplicationSpec. This is structured output via Anthropic's tool-use API: Claude must return JSON matching the schema or the call retries with the validation error as feedback.

The spec has six nested sections:

UniverseSpec: which stocks. Universe name, min price filter, sector exclusions, exchange filter, point-in-time convention.
SignalSpec: how stocks are ranked. Kind ("past_return", "fundamentals_ratio", etc.), lookback months, skip months, formula, input columns required.
PortfolioSpec: how ranked stocks become a portfolio. Number of buckets (quintiles, deciles), which bucket is long, which is short, weighting scheme (equal/value/signal), gross exposure.
RebalanceSpec: timing mechanics. Frequency, execution lag days, holding period, signal-date convention (close/open), execution-date convention.
Start/end dates: sample window.
Ambiguities: explicit list of every methodological choice the paper didn't specify, with the default chosen and the alternatives to test later.
Critical rule: every field carries evidence. Each field in the spec has an attached SupportingQuote — verbatim text from the PDF with the page number. The prompt enforces this: Claude cannot assert "monthly rebalancing" without attaching a quote like "We rebalance the long-short portfolio at the end of each month" (page 7).

The prompt also explicitly instructs Claude to flag commonly-unstated choices as ambiguities, even if this feels "overly cautious":

Execution timing (T+0 vs T+1 vs T+5)
Holding period vs rebalance frequency
Signal staleness within holding period
Standardization method (full-sample vs expanding-window)
Winsorization thresholds
Universe filters (price minimums, financial exclusions, NYSE breakpoints)
Fundamentals filing-date convention
If Claude omits any of these as "obvious," the adversarial reviewer later will catch it.

Step 3 — Deterministic Quote Verification
Module: src/pdf/quote_verifier.py Inputs: ReplicationSpec with SupportingQuote objects + parsed PDF Outputs: quote-by-quote verification results

Before any downstream agent trusts A1's output, every quote gets checked against the actual PDF. This is pure string matching with fuzzy tolerance via rapidfuzz:

Exact match first
If no exact match, fuzzy match with configurable Levenshtein distance threshold
If fuzzy match succeeds, record the similarity score as match_confidence
If no match found, flag the quote as unverified
The fuzzy match handles normal PDF extraction artifacts: ligatures (ﬁ → fi), unicode variants (em-dash vs hyphen), extra whitespace, line-break-induced spacing. But it doesn't tolerate hallucinated quotes — text that simply isn't in the paper will fail to match.

Output for each quote: {text, page_claimed, page_actual, match_confidence, status} where status ∈ {verified, fuzzy_match, not_found}. Unverified quotes become blocking issues for A2.

Step 4 — Extraction Verifier (Agent A2)
Model: claude-haiku-4-5-20251001 Module: src/agents/extraction/extraction_verifier.py Inputs: spec + PDF + verified quotes Outputs: per-claim verification + overall confidence score

Even if a quote exists in the PDF, it might not actually support the claim A1 built on top of it. A2's job is to check this logical link.

For each (claim, quote) pair, A2 gets a narrow prompt:

Claim: "The paper rebalances monthly." Quote (from page 7): "We rebalance the long-short portfolio." Does this quote support this claim? Respond yes/no with a one-sentence reason.

Haiku is used here because the task is simple and runs many times (once per claim). Opus would be overkill.

A2 outputs verification for each claim: {claim, quote, supported, reason, severity}. Severity matters — a failed check on "monthly rebalancing" is more serious than a failed check on "excludes penny stocks."

Retry logic: if more than N low-severity failures OR any high-severity failure occurs, the pipeline loops back to A1 with feedback: "Your quote on page 7 doesn't actually specify the rebalance frequency — find a different quote or flag this as ambiguous." A1 gets up to 3 attempts. After that, the system continues with explicit low-confidence markers on the unverified claims.

Step 5 — Adversarial Reviewer (Agent A3)
Model: claude-opus-4-7 Module: src/agents/extraction/adversarial_reviewer.py Inputs: PDF + verified spec Outputs: exactly 3 structured criticisms

A3 exists because A1 will sometimes confidently produce a "clean" spec that misses things. A2 only checks that claims are grounded — it can't catch things A1 didn't claim at all.

A3 is given the PDF and the spec, and forced to produce exactly three criticisms:

What did the extraction miss? (Something in the paper A1 didn't capture)
What did it oversimplify? (A nuance A1 smoothed over)
What alternative interpretation is plausible? (A different reading of the paper's methodology)
Why exactly three? Asking for "any issues you see" typically produces "looks good to me." Forcing a fixed count forces the model to actually search for problems. The forced structure is an adversarial technique borrowed from red-teaming.

Each criticism returns with:

Severity (high/medium/low)
Evidence (quote from the paper)
Proposed remediation (should this become a new ambiguity? a spec field change?)
High-severity criticisms get promoted to AmbiguityFlag objects and appended to the spec's ambiguity list. These become additional hypotheses for the divergence diagnostician later.

Step 6 — Data Mapper (Agent B1)
Model: claude-opus-4-7 Module: src/agents/implementation/data_mapper.py Inputs: verified spec + data source catalog Outputs: DataMapping object with fidelity notes

The paper says "we use CRSP monthly returns for NYSE/AMEX/NASDAQ common stocks 1965-1989 excluding financials." Your system doesn't have CRSP. B1's job is to figure out the best available mapping.

B1 sees:

What the paper asked for (extracted by A1)
What's actually available (PointInTimeDataStore catalog):
defeatbeta/yahoo-finance-data parquets (prices, fundamentals, transcripts, 1994+)
FRED via fredapi (macro series)
ALFRED (vintage FRED data)
SEC EDGAR (filing dates, XBRL)
Ken French Data Library (factors)
aufklarer and istat-ai central bank datasets
fancyzhx news
B1 produces a structured mapping document:

paper_needs: "CRSP monthly returns" 
  → available: "defeatbeta monthly closes"
  → fidelity: medium
  → notes: "missing delisted stocks (survivorship bias); post-1994 only"

paper_needs: "exclude SIC 6xxx financials"
  → available: "sector string from 2026 snapshot"
  → fidelity: low
  → notes: "no historical SIC codes; using current-sector approximation; sector defined differently"

paper_needs: "1965-1989 sample"
  → available: "1995-2024"
  → fidelity: low
  → notes: "entirely post-publication window; compare to JT 2001 follow-up for decay expectations"
Each mapping has explicit fidelity tiers (high/medium/low). Low-fidelity mappings downgrade the final replication confidence score.

Step 7 — Mapping Verifier (Agent B2)
Model: claude-haiku-4-5-20251001 Module: src/agents/implementation/mapping_verifier.py Inputs: B1's mapping document Outputs: verified mapping + substitution warnings

Two checks run:

Deterministic checks (code):

Do the referenced datasets actually exist in the data store?
Does each dataset's date range cover the required sample window?
Are the required columns present in each dataset?
Are the join keys (ticker, date) compatible across datasets?
LLM check (Haiku):

Given a substitution ("Yahoo monthly closes" instead of "CRSP monthly returns"), is this reasonable? What biases does it introduce? What's the magnitude?
The deterministic checks cannot be skipped — if the data doesn't exist, the pipeline fails fast rather than silently producing nonsense later. The LLM check adds judgment about substitution quality.

Every accepted substitution adds a data_quality_flag that propagates through every downstream result. The final report lists every substitution under a "Data Quality" section.

Step 8 — Backtest Engine Execution (deterministic)
Module: src/engine/backtest.py (+ signals.py, portfolio.py, tranches.py, costs.py, metrics.py) Inputs: verified spec + point-in-time data store Outputs: BacktestResult

This is the heart of the system. No LLM involvement. This is the module you built in Phase 1 Step 3 and validated against Ken French's MOM factor.

The execution loop:

For each month in [start_date, end_date]:
    1. Resolve universe as of this month (PIT)
    2. Compute signal for each stock in universe
    3. Rank stocks, form portfolio buckets
    4. Apply execution lag: portfolio effective date = rebal_date + lag
    5. If overlapping tranches (holding > 1 month):
         Add new tranche, drop expired tranche
         Overall portfolio = average of active tranches
    6. Compute return from formation date to next rebalance
    7. Subtract transaction costs based on turnover
    8. Record to returns time series
    9. Propagate provenance from data store → this return
Post-loop computations:

Monthly return series (the core output)
Annualized alpha, Sharpe, volatility
Newey-West corrected t-statistics (lag = holding_period - 1)
Subperiod breakdown (by decade, by regime)
Turnover and transaction cost burden
Drawdown statistics, hit rate
BacktestResult structure:

returns: tuple of ReturnObservation (one per period)
alpha, alpha_tstat, alpha_newey_west_lag
sharpe, volatility, max_drawdown, hit_rate
turnover, cost_burden
return_convention: explicit ("arithmetic monthly")
data_quality_flags: propagated from data store
n_periods, date_range
provenance: full chain back to inputs
The engine is general-purpose. It handles momentum, value, profitability, size — any cross-sectional equity strategy — depending on which SignalSpec.kind is invoked. Each kind dispatches to a signal handler (pure function, no state).

Step 9 — Result Comparator (Agent D1)
Model: claude-haiku-4-5-20251001 Module: src/agents/validation/result_comparator.py Inputs: BacktestResult + paper's claimed results (from A1) Outputs: structured comparison with match labels

Straightforward comparison task. Given:

Paper claimed: 0.95%/month, t-stat 3.07
Engine produced: 0.48%/month, t-stat 2.8
D1 produces a structured comparison:

metric: "monthly_long_short_return"
  claimed: 0.95%
  replicated: 0.48%
  gap_bps: 47
  gap_magnitude: "material"
  direction: "same_sign"
  
metric: "t_statistic"  
  claimed: 3.07
  replicated: 2.80
  status: "both_significant"
Overall match label: {full_match, partial_match, diverged, opposite_sign}.

Partial and diverged matches trigger Step 10.

Step 10 — Divergence Diagnostician (Agent D2)
Model: claude-opus-4-7 Module: src/agents/validation/divergence_diagnostician.py Inputs: comparison result + spec + ambiguity list Outputs: structured diagnosis with causal attribution

This is the cleverest piece of the pipeline. D2 doesn't speculate about why replication diverged — it experiments.

The process:

D2 receives the comparison (gap exists) and the spec's ambiguity list
For each ambiguity, D2 plans a test: "what if we flip this one parameter?"
D2 creates a variant spec via spec.copy(update={...})
Variant spec goes back through the backtest engine
Engine produces a new BacktestResult
D2 reads the new result and decides whether the gap closed
Example for the JT replication:

Original: 0.48%/mo (gap of 47bps vs paper)

Experiment 1 — mutate sample window
  spec.end_date = date(2000, 12, 31)
  → new result: 0.83%/mo (gap of 12bps)
  → gap mostly closed

Experiment 2 — mutate skip month
  spec.signal.skip_months = 0
  → new result: 0.31%/mo (gap worse)
  → skip-month assumption was right

Experiment 3 — mutate weighting
  spec.portfolio.weighting = "value"
  → new result: 0.41%/mo (gap worse)
  → EW assumption was right
After running experiments, D2 writes a structured diagnosis:

primary_cause: "post_publication_decay"
evidence: "Restricting to pre-2000 sample recovers 0.83%/mo, within 12bps of claim"
alternative_causes_tested: ["skip_month", "weighting"]
alternative_causes_ruled_out: "reran with mutations, gap worsened"
residual_gap: 12bps
residual_gap_likely_cause: "universe differences (Yahoo vs CRSP)"
confidence: "high"
The key property: D2's LLM never invents numbers. The LLM picks which experiment to run and interprets the pattern. The backtest engine produces the actual numbers. The diagnosis is grounded in observed engine behavior, not LLM confabulation.

D2 typically runs 3-8 experiments per divergence. Each is a full backtest (~2 minutes), so a diagnosis might take 10-20 minutes of compute.

Step 11 — Robustness Battery (deterministic)
Module: src/robustness/battery.py (+ sub-modules for each test class) Inputs: verified spec + data store Outputs: RobustnessReport

Independent of replication success, every paper runs through a standard battery of stress tests. All tests work by mutating the spec and re-running the engine — no new LLM calls, just systematic re-execution.

Test 1 — Subperiod stability (subperiod.py) Split the sample into halves, thirds, and rolling 5-year windows. Rerun on each. Output: alpha per subperiod + stability metric (coefficient of variation across windows).

Test 2 — Transaction cost sensitivity (costs.py) Rerun with transaction_cost_bps ∈ {0, 5, 10, 25, 50}. Output: net alpha at each cost level + break-even bps.

Test 3 — Capacity analysis (capacity.py) For increasing AUM levels ($1M, $10M, $100M, $1B, $10B), estimate implementation shortfall based on ADV (average daily volume). Output: max AUM before shortfall > 50bps.

Test 4 — Liquidity filter (data_quality.py) Rerun restricting universe to top-500 by ADV. Output: alpha in liquid names only.

Test 5 — Point-in-time contamination check (data_quality.py) Rerun using "latest" data (all current revisions). Compare to PIT version. Large divergence = look-ahead bias contamination in the paper's original methodology.

Test 6 — Survivorship analysis (data_quality.py) Estimate coverage bias from data source (Yahoo vs survivorship-free benchmark). Flag expected alpha overstatement.

Test 7 — Execution lag sensitivity (lag_and_decay.py) Sweep execution_lag_days ∈ {0, 1, 2, 3, 5, 10, 20}. Each lag is a full re-run. Output: alpha by lag, alpha half-life in business days.

Test 8 — Signal decay profile (lag_and_decay.py) With measure_daily_returns=True, track daily returns within each holding period. Compute:

Alpha frontloading ratio (days 1-5 / days 1-20)
Persistence days (until daily alpha becomes insignificant)
Pattern classification (structural / information-based / microstructure / stale)
Test 9 — Lag × Holding grid (lag_and_decay.py) 2D sweep of execution lag (0, 1, 2, 5, 10 days) × holding period (5, 10, 20, 40 days). Produces the implementability matrix.

Each test populates a row in the final RobustnessReport. This whole battery might run 30-50 backtests (~2 minutes each with caching). Total runtime for battery: 15-30 minutes on first run, near-instant on cached reruns.

Step 12 — Robustness Adversary (Agent D3)
Model: claude-opus-4-7 Module: src/agents/validation/robustness_adversary.py Inputs: RobustnessReport + original BacktestResult Outputs: implementability verdict

D3 reads the full robustness report and writes the interpretation. It's not producing numbers — it's interpreting them.

Classifies the signal type:

Structural premium (e.g., value, size): alpha persistent over weeks/months, low decay, survives lag well
Information-based (e.g., post-earnings drift, momentum): alpha concentrated in first 1-4 weeks, moderate decay, moderate lag sensitivity
Microstructure/reversal: alpha in first few days, destroyed by 1-2 days of lag
Stale: flat or negative alpha profile, signal dead
Computes robustness kill count: out of the 9 battery tests, how many the signal survived meaningfully.

Produces implementable alpha: a single headline number. For momentum example:

"Paper claims 0.95%/month. Replicated at 0.48% over post-1994 sample. Under realistic T+2 execution with 25bps costs and liquid-names-only constraint: 0.14%/month implementable alpha. Signal is information-based with 7-day alpha half-life. Capacity limited to ~$500M before implementation shortfall dominates. 4 of 9 stress tests passed meaningfully."

This verdict is the headline output of the entire system.

Step 13 — Report Synthesizer (Agent E1)
Model: claude-opus-4-7 Module: src/report/synthesizer.py Inputs: entire pipeline state Outputs: HTML report + structured JSON

E1 produces the final report. Six sections:

Replication Fidelity

Paper's claimed numbers vs engine's numbers
Match labels per claim
Confidence score
Ambiguity Analysis

Every flagged ambiguity
Which ambiguities drove gaps (from D2)
Which were confirmed correct (tested and held)
Implementability Profile (headline section)

Lag × holding 2D grid
Decay curve
Signal classification
Cost sensitivity chart
Capacity analysis
Robustness Battery

Stress test scorecard
Subperiod stability chart
Kill count visualization
Data Quality

Substitutions made and their fidelity
Survivorship warnings
Point-in-time contamination check result
Universe coverage analysis
Verdict

Implementable alpha under realistic frictions
Signal type classification
"Would we trade this?" answer with reasoning
Provenance drill-down: every number in the rendered HTML has a click target. Click "0.48% replication result" and the UI reveals:

Which BacktestResult object produced it (Node 8 output)
Which ReplicationSpec was the input (Node 5 output after Node 7 verification)
Which SupportingQuote objects backed each spec field
Which data sources fed the engine
Which paper quotes justified each methodological choice
Click "post-publication decay" and the UI shows:

The specific D2 experiment that isolated it
The spec mutation that was tested
The before/after numbers
The confidence level
Nothing is unsourced. The whole report is a browsable audit trail.

Exit: Interactive Report
The final artifact is a self-contained HTML page with embedded JavaScript for the drill-down interactions. Demo flow in the web UI:

User uploads PDF
Agent progress streams in real-time (each node publishes status to the UI)
Report appears progressively as sections complete
User can explore any number via click-through provenance
User can export the full state JSON for integration with other tools
Summary of the flow
PDF IN
  ↓
PARSE (pdfplumber) ────────────── deterministic
  ↓
EXTRACT (A1, Opus) ──────────── LLM, schema-validated
  ↓
VERIFY QUOTES ─────────────────── deterministic fuzzy match
  ↓
VERIFY LOGIC (A2, Haiku) ─────── LLM, quote-supports-claim check
  ↓  (retry A1 up to 3x on failure)
ADVERSARIAL REVIEW (A3, Opus) ── LLM, forced 3 criticisms
  ↓
MAP DATA (B1, Opus) ───────────── LLM, spec → data bindings
  ↓
VERIFY MAPPING (B2, Haiku) ───── deterministic + LLM substitution check
  ↓
RUN BACKTEST ─────────────────── deterministic engine (Phase 1)
  ↓
COMPARE (D1, Haiku) ───────────── LLM, structured match labeling
  ↓  (if gap)
DIAGNOSE (D2, Opus) ──────────── LLM-guided spec mutations + engine reruns
  ↓
ROBUSTNESS BATTERY ───────────── deterministic: 9 test classes, many reruns
  ↓
INTERPRET (D3, Opus) ──────────── LLM, implementability verdict
  ↓
SYNTHESIZE (E1, Opus) ─────────── LLM, HTML report with provenance
  ↓
REPORT OUT
Total LLM calls per paper: ~15-25 depending on retries and experiments. Total runtime: ~10-20 minutes on first run, ~2-5 minutes on cached reruns. Token budget: ~$3-8 per paper with caching enabled.

