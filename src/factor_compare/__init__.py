"""Factor-comparison module: check whether the published Ken French factor
realized the paper's claimed return over the paper's sample window.

Free, deterministic, no LLM. Used for papers whose window predates the
defeatbeta panel (1994-11-30) so we can't run a full stock-level
replication. The realized KF factor return is the closest free
falsification test we have.
"""

from src.factor_compare.compare import (
    compare_to_kf_factor,
    map_signal_to_kf_factor,
)
from src.factor_compare.loader import (
    load_ff3_monthly,
    load_ff5_daily,
    load_kf_csv,
    load_mom_daily,
    daily_to_monthly,
)

__all__ = [
    "compare_to_kf_factor",
    "map_signal_to_kf_factor",
    "load_ff3_monthly",
    "load_ff5_daily",
    "load_kf_csv",
    "load_mom_daily",
    "daily_to_monthly",
]
