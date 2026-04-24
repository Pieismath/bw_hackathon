"""Step 11.7 — Point-in-time / survivorship contamination check.

The intent is "would the result change if we used today's universe
membership for past formations?" — a quantification of survivorship bias.

For Yahoo-sourced data this test is degenerate: Yahoo already drops
delisted stocks, so the PIT universe == the today-universe by construction.
Running a meaningful PIT-vs-latest comparison requires a delisted-tickers
source like CRSP, which we don't have.

Rather than fake-running the test, we emit ONE informational
StressTestResult that names the limitation explicitly. D3 will read it
and know not to claim survivorship-bias quantification.

If a future data source carries delisted history (CRSP, Norgate), this
module should be replaced with a real PIT-vs-latest comparison.
"""

from __future__ import annotations

from src.specs import BacktestResult, StressTestResult


def run_data_quality_check(
    baseline: BacktestResult,
) -> list[StressTestResult]:
    """Emit one informational entry naming the limitation."""
    return [
        StressTestResult(
            name="pit_vs_latest_universe",
            family="data_quality",
            parameter_swept={"universe_source": "defeatbeta_yahoo"},
            # Not a real measurement — flag with surviving=False so it
            # doesn't inflate the surviving_count.
            headline_metric=0.0,
            headline_tstat=None,
            n_periods=baseline.n_periods,
            surviving=False,
            notes=(
                "PIT-vs-latest survivorship comparison NOT RUN: defeatbeta_yahoo "
                "drops delisted tickers, so the PIT universe equals today's "
                "universe by construction. Quantifying survivorship bias "
                "requires a delisted-aware source (CRSP / Norgate). The "
                "engine's existing data_quality_flags already mark survivorship "
                "as a known bias on every JT result; this entry exists to "
                "make the limitation legible to D3 and the final report."
            ),
        )
    ]
