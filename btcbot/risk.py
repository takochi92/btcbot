"""どのモデルも設定も上書きできない安全装置。

config.toml の値はここに書いた上限より緩くできない（緩く書いても上限に丸める）。
"""
from dataclasses import dataclass

import pandas as pd

HARD_CAPS = {
    "max_position_frac": 1.0,     # 借金での売買はしない
    "daily_loss_limit": 0.20,
    "max_consecutive_losses": 20,
    "max_drawdown": 0.50,
    "stale_data_days": 7,
    "max_api_errors": 10,
}


@dataclass(frozen=True)
class Limits:
    max_position_frac: float
    daily_loss_limit: float
    max_consecutive_losses: int
    max_drawdown: float
    stale_data_days: int
    max_api_errors: int

    @classmethod
    def from_config(cls, risk_cfg):
        return cls(**{k: min(type(cap)(risk_cfg[k]), cap) for k, cap in HARD_CAPS.items()})


@dataclass
class Verdict:
    flatten: bool = False
    halt: str = ""          # 空でなければ止める理由
    permanent: bool = False  # True なら人が reset するまで止まったまま


def live_below_backtest(closed_trades, kill_criteria):
    """実際の売買の平均が、バックテストの下側信頼限界を下回ったら True。"""
    if not kill_criteria:
        return False
    n = len(closed_trades)
    if n < kill_criteria["min_trades"]:
        return False
    bound = kill_criteria["mean"] - kill_criteria["z"] * kill_criteria["std"] / n**0.5
    return sum(closed_trades) / n < bound


def check(state, limits: Limits, equity: float, now: pd.Timestamp, last_bar: pd.Timestamp, kill_criteria=None) -> Verdict:
    if state.halted:
        return Verdict(True, state.halt_reason, state.halt_permanent)
    if state.peak_equity > 0 and equity <= state.peak_equity * (1 - limits.max_drawdown):
        return Verdict(True, "max_drawdown", True)
    if state.api_errors >= limits.max_api_errors:
        return Verdict(True, "api_errors", True)
    if state.consecutive_losses >= limits.max_consecutive_losses:
        return Verdict(True, "consecutive_losses", True)
    if live_below_backtest(state.closed_trades, kill_criteria):
        return Verdict(True, "live_below_backtest", True)
    if state.last_equity > 0 and equity <= state.last_equity * (1 - limits.daily_loss_limit):
        return Verdict(True, "daily_loss", False)
    # 日足は 00:00 UTC 始まりで翌 00:00 に確定する
    if now - (last_bar + pd.Timedelta(days=1)) > pd.Timedelta(days=limits.stale_data_days):
        return Verdict(True)  # 古いデータで新しく持たない。止めはしない
    return Verdict()
