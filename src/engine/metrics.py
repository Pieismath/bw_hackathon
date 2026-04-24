"""Summary metrics for a backtest's monthly return series.

Newey-West HAC t-stats: the monthly returns of an overlapping-tranche
strategy are serially correlated by construction (K-tranche overlap →
lag-(K-1) positive autocorrelation). Plain OLS understates the variance
and inflates the t-stat. We default to lag = K-1 which is standard in
the momentum literature. The caller passes the lag explicitly so the
choice appears in BacktestResult.newey_west_lag for provenance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm


def newey_west_mean_tstat(returns: pd.Series, lag: int) -> tuple[float, float]:
    """Return (mean, t-stat) for the mean under HAC (Newey-West) SE with lag L.

    Tests the null H0: E[r] = 0 by regressing r on a constant with HAC SE.
    Lag = 0 reduces to White heteroskedasticity-robust SE, which reduces
    to plain OLS SE under homoskedasticity.
    """
    clean = returns.dropna()
    if clean.empty or clean.std() == 0:
        return float(clean.mean() if len(clean) else 0.0), 0.0
    y = clean.to_numpy(dtype=float)
    X = np.ones((len(y), 1))
    model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": max(lag, 0)})
    mean = float(model.params[0])
    tstat = float(model.tvalues[0])
    return mean, tstat


def annualize_return(monthly_mean: float) -> float:
    """Arithmetic annualization — 12 × monthly mean."""
    return 12.0 * monthly_mean


def annualize_vol(monthly_std: float) -> float:
    """Convert monthly standard deviation to annualized volatility."""
    return float(monthly_std) * np.sqrt(12.0)


def sharpe_ratio(returns: pd.Series) -> float:
    clean = returns.dropna()
    if clean.empty or clean.std() == 0:
        return 0.0
    return float(annualize_return(clean.mean()) / annualize_vol(clean.std()))


def max_drawdown(returns: pd.Series) -> float:
    """Max drawdown of the cumulative arithmetic return path (decimal fraction, <= 0)."""
    clean = returns.dropna()
    if clean.empty:
        return 0.0
    cum = (1.0 + clean).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def hit_rate(returns: pd.Series) -> float:
    clean = returns.dropna()
    if clean.empty:
        return 0.0
    return float((clean > 0).mean())
