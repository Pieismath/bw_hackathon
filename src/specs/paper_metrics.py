"""HeadlineClaim — the paper's reported headline result, as a discriminated union.

A1 picks a single primary variant per paper (per the "single headline variant"
rule in the A1 prompt). For D2 (divergence diagnostician) to attribute the gap
between the paper's claim and the engine's replication, we also need the
paper's *own* reported number for that variant — but the number's SHAPE
depends on the paper class:

  - Cross-sectional momentum / reversal papers report a monthly long-short
    return + t-statistic.
  - Conditioning-sort reversal papers (e.g. CHST 2017 — short-term reversal
    within a past-return-bucket) also report a monthly long-short return but
    A1 must source it from a nested-conditional-sort table cell; the
    surrounding-text context differs and the verifier needs to know.
  - Factor-zoo Sharpe-ratio papers (e.g. AQR 2024 "Hidden Value of Streaky
    Returns") report a SHARPE-RATIO DIFFERENCE between top and bottom buckets,
    NOT a monthly L/S return. The replication engine's mean_return is not the
    paper's number.
  - Variance-ratio random-walk-test papers (Lo-MacKinlay 1988, Poterba-Summers
    1988) report a VR(q) statistic vs. the random-walk null. There is no
    tradeable headline; the comparison protocol is statistic-to-statistic.
  - Factor-regression papers (Fama-French 1993, anything reporting a model
    intercept) report a monthly regression alpha + its t-statistic on a
    specified factor model. Comparison runs the engine, regresses its returns
    on the same model, and compares intercepts.
  - StatisticalTestClaim is a catch-all for other test-statistic papers
    (autocorrelation tests, factor-spanning regressions with no tradeable
    output, etc.) — used when none of the more specific variants fit.

Every variant carries:
  - `kind` — the Pydantic discriminator (string Literal).
  - `t_stat` — optional Newey-West / OLS t-statistic associated with the
    headline value, if the paper reports one. Present on the shared base so
    every variant can carry it.
  - `window_label` — the literal sample window the paper used for the number
    (e.g. "January 1965 – December 1989").
  - `paper_location` — where the number appears in the paper (table cell,
    paragraph, etc.).
  - `supporting_quote` — verbatim PDF span containing the value, verified by
    A2 like every other quote.

Variant-specific fields carry the value(s) and any per-variant metadata the
verifier or D1/D2/D3 need to dispatch (factor model name, comparison_description,
null hypothesis, etc.).

If a paper is purely theoretical and reports no empirical headline at all,
A1 sets `headline_claim = None`. This is the ONLY case where None is allowed —
empirical papers must populate one of the variants (per the A1 prompt's rule 8,
which will be updated in Phase C to dispatch on the variants below).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from src.specs.claims import SupportingQuote


def _parse_json_string_if_needed(v: Any) -> Any:
    """Some LLM tool-use surfaces a discriminated-union nested object as a
    JSON-encoded STRING (rather than a nested JSON object) — Anthropic's
    tool-use has been observed to do this when the field schema combines
    ``oneOf`` + ``discriminator`` + ``anyOf [null]`` (i.e. ``Optional`` over
    a discriminated union). The string contents are still valid JSON for the
    variant. Decode here BEFORE Pydantic's union resolver runs so we accept
    the LLM's output without a retry, while leaving real dicts /
    Pydantic-model inputs untouched.
    """
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return v
    return v


HeadlineClaimKind = Literal[
    "monthly_long_short_return",
    "nested_conditional_sort_return",
    "sharpe_ratio_difference",
    "variance_ratio_statistic",
    "regression_alpha",
    "statistical_test",
]


class _HeadlineClaimBase(BaseModel):
    """Shared fields across every claim variant.

    Not exported — Pydantic discriminated unions require every member to set
    the discriminator field as a Literal, so each concrete variant subclasses
    this and adds its own `kind: Literal[...]` plus value field(s).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    t_stat: float | None = Field(
        default=None,
        description=(
            "Newey-West (for time-series statistics) or OLS (for regression "
            "alphas) t-statistic for the headline value, if reported. None if "
            "the paper does not report a t-statistic for this number — do NOT "
            "fabricate one."
        ),
    )
    window_label: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Literal sample window the number applies to — "
            "e.g. 'Jan 1965 – Dec 1989' or '1965–1989 (300 months)'."
        ),
    )
    paper_location: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Where the number appears in the paper — "
            "e.g. 'Table I Panel A, J=6/K=6 \"Buy-sell\" row' or "
            "'Exhibit 7, footnote 9'."
        ),
    )
    supporting_quote: SupportingQuote = Field(
        description=(
            "Verbatim PDF span that literally contains the headline value. A2 "
            "verifies this quote against the PDF like any other supporting "
            "quote. The quote MUST contain the number itself so a human "
            "reviewer can confirm the linkage at a glance."
        ),
    )


class MonthlyLongShortReturn(_HeadlineClaimBase):
    """Mean monthly long-short portfolio return.

    The canonical cross-sectional-momentum / -reversal headline. The engine's
    `BacktestResult.mean_return` is directly comparable — D1 reports
    `gap = mean_return - monthly_return`.

    Sign convention: positive when the long leg outperforms the short leg
    AFTER the paper's stated direction (`signal.direction`) is applied. For
    a momentum paper (`direction='long_high'`), a positive monthly_return
    means winners outperformed losers. For a reversal paper
    (`direction='long_low'`), a positive monthly_return STILL means the
    paper's chosen long leg outperformed its short leg — A1 must report
    the number with that sign convention. NEVER report a negative number
    just because the paper reports "losers earned −X% relative to winners";
    invert the sign so the headline reflects the paper's chosen direction.

    Example (JT 1993, Table I Panel A, J=6/K=6 'Buy-sell' row):
      kind='monthly_long_short_return', monthly_return=0.0095, t_stat=3.07,
      window_label='January 1965 – December 1989',
      paper_location='Table I Panel A, J=6/K=6 \"Buy-sell\" row'.
    """

    kind: Literal["monthly_long_short_return"] = "monthly_long_short_return"
    monthly_return: float = Field(
        description=(
            "Mean monthly long-short return, decimal "
            "(e.g. 0.0095 = 0.95%/month; 0.054 = 5.4%/year ⇒ 0.0045/month). "
            "Convert percentages reported in the paper accordingly. Sign is "
            "always positive for the paper's chosen long-vs-short orientation "
            "— see class docstring."
        ),
    )


class NestedConditionalSortReturn(_HeadlineClaimBase):
    """Monthly long-short return sourced from a nested-conditional-sort table.

    Comparison protocol is identical to MonthlyLongShortReturn (engine's
    mean_return compares directly to monthly_return), but the variant carries
    a `conditioning_description` field describing the conditioning so A2 / D1
    / D2 / D3 understand the methodological context.

    Used when the paper sorts the cross-section on one variable, then sorts
    within the resulting bucket on another variable, and reports the L/S
    return of the inner sort. The engine replicates this by encoding both
    sorts into the spec (universe filters + signal definition) and producing
    a single mean_return.

    Example (Cheng-Hameed-Subrahmanyam-Titman 2017, Table II Panel A,
    1M-reversal within 3M-loser quintile):
      kind='nested_conditional_sort_return', monthly_return=0.01683, t_stat=7.80,
      window_label='January 1980 – December 2011',
      paper_location='Table II Panel A — 1M reversal within 3M-loser quintile',
      conditioning_description='1-month past-return reversal computed WITHIN
        the 3-month past-return loser quintile (double-sort: outer on 3M
        past return, inner on 1M past return).'
    """

    kind: Literal["nested_conditional_sort_return"] = "nested_conditional_sort_return"
    monthly_return: float = Field(
        description=(
            "Mean monthly long-short return of the inner sort, decimal. "
            "Same sign convention as MonthlyLongShortReturn."
        ),
    )
    conditioning_description: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "One- or two-sentence plain-English description of the outer "
            "conditioning sort and how it relates to the inner sort. Use this "
            "field to explain methodological context that doesn't fit cleanly "
            "into a single ReplicationSpec field — e.g. 'sorted within the "
            "3M-loser quintile' or 'computed for institutional-exit stocks "
            "only'."
        ),
    )


class SharpeRatioDifference(_HeadlineClaimBase):
    """Difference in Sharpe ratios between two named buckets / portfolios.

    Used for papers whose headline is a SHARPE-RATIO GAP between top- and
    bottom-bucket portfolios (e.g. AQR 2024 "Hidden Value of Streaky Returns",
    which sorts ~153 JKP factors into VR-terciles and reports the
    top-minus-bottom annualized Sharpe gap with t=2.67/2.78).

    Comparison protocol: the engine produces top- and bottom-bucket portfolios
    via the spec's `portfolio.long_bucket` / `short_bucket`. D1 computes
    `engine_sharpe_diff = sharpe(long_leg) − sharpe(short_leg)` and compares
    to `annualized_sharpe_diff`. NB: this requires the engine to expose
    per-leg returns, not just the L/S combined return; today the engine only
    emits the combined long-short series, so this comparison needs an engine
    extension (Phase E follow-up). Until then, D1 reports gap=None with a
    structured 'engine_does_not_expose_per_leg_sharpe' SpecAdaptation.

    Example (AQR 2024 streaks, Exhibit 7 / footnote 9):
      kind='sharpe_ratio_difference', annualized_sharpe_diff=0.45, t_stat=2.67,
      window_label='1973 – 2024',
      paper_location='Exhibit 7, top vs. bottom variance-ratio tercile; t-stat
        from footnote 9 (annualized, monthly-frequency rebalancing)',
      comparison_description='Top vs. bottom tercile of ~153 JKP factors
        sorted by variance ratio of monthly returns over a 60-month window.'
    """

    kind: Literal["sharpe_ratio_difference"] = "sharpe_ratio_difference"
    annualized_sharpe_diff: float = Field(
        description=(
            "Top-bucket Sharpe minus bottom-bucket Sharpe, ANNUALIZED. "
            "Decimal (e.g. 0.45 = 0.45 annualized Sharpe units). The paper "
            "may report monthly Sharpe — multiply by √12 to convert."
        ),
    )
    comparison_description: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "Plain-English description of WHICH two buckets the Sharpe diff "
            "is between (e.g. 'top vs. bottom tercile of ~153 JKP factors "
            "sorted by variance ratio'). D1's per-leg replication routes "
            "through this — the description must unambiguously identify the "
            "two endpoints."
        ),
    )


class VarianceRatioStatistic(_HeadlineClaimBase):
    """Variance ratio VR(q) statistic on a returns panel — random-walk test.

    The canonical Lo-MacKinlay (1988) and Poterba-Summers (1988) headline.
    The paper reports the VR(q) value computed at a specific aggregation
    horizon q (in periods of the underlying frequency — weeks for LM, months
    for PS) and tests it against the random-walk null (VR(q) = 1).

    Comparison protocol: the engine computes VR(q) on the same data panel
    using the same q and reports `engine_vr_value`. D1 compares
    `gap = engine_vr_value − vr_value` (and the associated t-statistic).
    There is NO monthly L/S return comparison — the verdict strip should
    render the test-statistic comparison, not an "implementable alpha"
    number.

    The engine ALSO runs an implicit contrarian/momentum strategy implied by
    the spec's signal.direction (long mean-revertingly when VR<1) and reports
    that mean_return as a SECONDARY observation, but the PRIMARY headline
    comparison is statistic-to-statistic.

    Example (Lo-MacKinlay 1988, Table 2, q=4 weekly equal-weighted size-1):
      kind='variance_ratio_statistic', q=4, vr_value=1.30, t_stat=7.51,
      null_hypothesis='random walk (VR(q) = 1 for all q)',
      window_label='September 1962 – December 1985',
      paper_location='Table 2, q=4, weekly equal-weighted size-portfolio 1
        (smallest quintile)'.
    """

    kind: Literal["variance_ratio_statistic"] = "variance_ratio_statistic"
    q: int = Field(
        ge=2,
        description=(
            "Aggregation horizon in periods of the underlying frequency "
            "(weeks for weekly-data papers, months for monthly-data papers). "
            "Must be ≥ 2 — VR(1) is identically 1 by definition."
        ),
    )
    vr_value: float = Field(
        description=(
            "VR(q) headline value reported by the paper. > 1 ⇒ positive "
            "autocorrelation (streakiness); < 1 ⇒ mean-reversion; ≈ 1 ⇒ "
            "consistent with random walk."
        ),
    )
    null_hypothesis: str = Field(
        min_length=1,
        max_length=200,
        default="random walk (VR(q) = 1 for all q)",
        description=(
            "The null hypothesis the statistic tests. Default is the "
            "random-walk null which is the standard target for VR tests; "
            "override if the paper specifies otherwise."
        ),
    )


class RegressionAlpha(_HeadlineClaimBase):
    """Monthly regression intercept (alpha) on a named factor model.

    Used for factor-model papers (Fama-French 1993, Carhart 1997 anomaly
    tests, Novy-Marx profitability, etc.) whose headline is the intercept
    of a regression `excess_portfolio_return = α + β·factors + ε`. The
    intercept α is the residual unexplained return per month after the
    factor model accounts for systematic exposures.

    Comparison protocol: the engine produces a return series for the
    portfolio defined by the spec, the spec also names the factor model
    (or B1 maps `factor_model` to a typed factor source), D1 runs OLS of
    engine_returns − rf on factor returns, extracts the intercept, compares
    to `monthly_alpha`. This requires the engine to be wired to a factor
    panel — today the Ken French CSV cache covers FF3 / FF5 / FF6 since
    1963; B1 selects the right slice via `factor_model`.

    Example (Fama-French 1993, Table 9a Panel B, decile 10 intercept on
    FF3 model — the small-cap anomaly):
      kind='regression_alpha', monthly_alpha=0.0021, t_stat=2.34,
      factor_model='Fama-French 3-factor (Mkt-RF, SMB, HML)',
      regressor_description='value-weighted size-decile-10 (largest stocks) '
        'monthly excess returns regressed on Mkt-RF / SMB / HML.',
      window_label='July 1963 – December 1991',
      paper_location='Table 9a Panel B, decile 10 intercept'.
    """

    kind: Literal["regression_alpha"] = "regression_alpha"
    monthly_alpha: float = Field(
        description=(
            "Regression intercept α in monthly units, decimal "
            "(e.g. 0.0021 = 0.21%/month). The t_stat field on the base "
            "carries the OLS t-statistic for the intercept."
        ),
    )
    factor_model: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Canonical name of the factor model — e.g. 'Fama-French 3-factor "
            "(Mkt-RF, SMB, HML)', 'Carhart 4-factor (FF3 + MOM)', 'Fama-French "
            "5-factor (Mkt-RF, SMB, HML, RMW, CMA)'. B1 maps this string to a "
            "concrete factor data source (Ken French CSV slices today). Use a "
            "naming convention recognizable to a finance reader; the verifier "
            "does not enforce a controlled vocabulary."
        ),
    )
    regressor_description: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "Plain-English description of what's on the LEFT side of the "
            "regression — the portfolio whose intercept the paper reports. "
            "E.g. 'value-weighted size-decile-10 (largest stocks) monthly "
            "excess returns'."
        ),
    )


class StatisticalTestClaim(_HeadlineClaimBase):
    """Generic catch-all for statistical-test headlines that don't fit the
    more specific variants above.

    Use this for papers whose headline is a test statistic against a stated
    null hypothesis but is NOT a variance ratio, NOT a regression alpha, and
    NOT a tradeable performance metric. Examples: autocorrelation Q-statistic
    tests, multivariate factor-spanning tests with no tradeable output,
    Hansen-Jagannathan distance, etc.

    Comparison protocol: like VarianceRatioStatistic — engine produces the
    same statistic on the same data, D1 compares statistic-to-statistic.
    The engine MAY also run an implicit tradeable strategy implied by
    `signal.direction` and report mean_return as secondary; the verdict
    strip does NOT use it as the primary headline.

    Prefer the more specific variants (VarianceRatioStatistic, RegressionAlpha)
    when they fit — they carry richer per-variant metadata and let A2 /
    D1 / D2 / D3 specialize. StatisticalTestClaim is the fallback when no
    specific variant fits, and triggers a high-severity AmbiguityFlag with
    parameter='headline_claim.kind' asking a human reviewer to confirm the
    catch-all routing was correct.
    """

    kind: Literal["statistical_test"] = "statistical_test"
    test_statistic_name: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Canonical name of the test statistic — e.g. "
            "'Ljung-Box Q(12)', 'GRS F-statistic', 'Hansen-Jagannathan distance'."
        ),
    )
    test_statistic_value: float = Field(
        description="Numerical value of the test statistic.",
    )
    null_hypothesis: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "The null hypothesis the statistic tests — e.g. "
            "'no serial correlation up to lag 12', 'factor model prices "
            "the test assets'."
        ),
    )
    p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "p-value for the test statistic, if the paper reports one. "
            "None if not reported — do NOT compute one from t_stat."
        ),
    )


# ---------------------------------------------------------------------------
# The exported discriminated union.
#
# Pydantic v2 deserializes `{"kind": "...", ...}` JSON into the matching
# variant by reading the `kind` field. ReplicationSpec.headline_claim has type
# `HeadlineClaim | None`; consumers can dispatch with isinstance() or by
# reading `.kind`.
# ---------------------------------------------------------------------------

HeadlineClaim = Annotated[
    Union[
        MonthlyLongShortReturn,
        NestedConditionalSortReturn,
        SharpeRatioDifference,
        VarianceRatioStatistic,
        RegressionAlpha,
        StatisticalTestClaim,
    ],
    Field(discriminator="kind"),
    BeforeValidator(_parse_json_string_if_needed),
]


__all__ = [
    "HeadlineClaim",
    "HeadlineClaimKind",
    "MonthlyLongShortReturn",
    "NestedConditionalSortReturn",
    "SharpeRatioDifference",
    "VarianceRatioStatistic",
    "RegressionAlpha",
    "StatisticalTestClaim",
]
