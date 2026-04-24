"""Canonical backtest engine.

One tested implementation consumed by every replication via ReplicationSpec.
Agents produce specs; the engine runs them. No per-paper bespoke code.
"""

from src.engine.backtest import run_backtest
from src.engine.calendar import business_day_shift, month_ends_in
from src.engine.costs import per_period_cost
from src.engine.metrics import (
    annualize_return,
    annualize_vol,
    hit_rate,
    max_drawdown,
    newey_west_mean_tstat,
    sharpe_ratio,
)
from src.engine.portfolio import form_portfolio
from src.engine.signals import compute_signal
from src.engine.tranches import Tranche, active_tranches_in

__all__ = [
    "Tranche",
    "active_tranches_in",
    "annualize_return",
    "annualize_vol",
    "business_day_shift",
    "compute_signal",
    "form_portfolio",
    "hit_rate",
    "max_drawdown",
    "month_ends_in",
    "newey_west_mean_tstat",
    "per_period_cost",
    "run_backtest",
    "sharpe_ratio",
]
