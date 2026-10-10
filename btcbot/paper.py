"""仮想売買。本物のお金も取引所の鍵も使わない。"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from . import risk
from .strategy import signal


@dataclass
class State:
    cash: float
    btc: float = 0.0
    peak_equity: float = 0.0
    last_equity: float = 0.0
    day: str = ""
    halted: bool = False
    halt_reason: str = ""
    halt_permanent: bool = False
    consecutive_losses: int = 0
    api_errors: int = 0
    entry_value: float = 0.0
    closed_trades: list = field(default_factory=list)
    fills: list = field(default_factory=list)

    @classmethod
    def load(cls, path, initial_cash):
        p = Path(path)
        return cls(**json.loads(p.read_text("utf-8"))) if p.exists() else cls(cash=initial_cash)

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", "utf-8")

    def halt(self, reason, permanent):
        self.halted, self.halt_reason, self.halt_permanent = True, reason, permanent


def _fill(state, side, price, date, fee, slip):
    if side == "buy":
        spend = state.cash
        exec_price = price * (1 + slip)
        state.btc = spend * (1 - fee) / exec_price
        state.cash -= spend
        state.entry_value = spend
    else:
        exec_price = price * (1 - slip)
        proceeds = state.btc * exec_price * (1 - fee)
        trade_ret = proceeds / state.entry_value - 1
        state.cash += proceeds
        state.btc = 0.0
        state.entry_value = 0.0
        state.closed_trades.append(trade_ret)
        state.consecutive_losses = state.consecutive_losses + 1 if trade_ret < 0 else 0
    state.fills.append({"date": date, "side": side, "price": exec_price})


def _update_markouts(state, close: pd.Series):
    """約定から 1日後・3日後の値動き。日足なので秒単位の markout は意味がなく、日単位で見る。"""
    for f in state.fills:
        sign = 1 if f["side"] == "buy" else -1
        for days in (1, 3):
            key = f"markout_{days}d"
            when = pd.Timestamp(f["date"]) + pd.Timedelta(days=days)
            if key not in f and when in close.index:
                f[key] = sign * (float(close[when]) / f["price"] - 1)


def step(state: State, bars: pd.DataFrame, now: pd.Timestamp, cfg, kill_criteria=None):
    """1回分の判断と仮想約定。戻り値は判断ログ（1行）。"""
    hyp, limits = cfg["hypothesis"], risk.Limits.from_config(cfg["risk"])
    fee, slip = hyp["fee_rate"], hyp["slippage_rate"]
    close = bars["close"]
    last_bar = close.index[-1]
    price = float(close.iloc[-1])
    equity = state.cash + state.btc * price

    today = str(now.date())
    if state.day != today:
        state.day = today
        if state.halted and not state.halt_permanent:
            state.halted, state.halt_reason = False, ""
    state.peak_equity = max(state.peak_equity, equity)

    verdict = risk.check(state, limits, equity, now, last_bar, kill_criteria)
    sig = float(signal(close, hyp["main_lookback"]).iloc[-1])
    target = 0.0 if verdict.flatten else sig * limits.max_position_frac
    if verdict.halt and not state.halted:
        state.halt(verdict.halt, verdict.permanent)

    action = "hold"
    if target == 0 and state.btc > 0:
        _fill(state, "sell", price, str(last_bar.date()), fee, slip)
        action = "sell"
    elif target > 0 and state.btc == 0 and state.cash > 0:
        _fill(state, "buy", price, str(last_bar.date()), fee, slip)
        action = "buy"

    # 手じまいで条件に引っかかったら、次からは入らない
    if not state.halted:
        after = risk.check(state, limits, state.cash + state.btc * price, now, last_bar, kill_criteria)
        if after.halt and after.permanent:
            state.halt(after.halt, True)

    _update_markouts(state, close)
    state.last_equity = state.cash + state.btc * price
    return {
        "time": now.isoformat(),
        "bar": str(last_bar.date()),
        "price": price,
        "signal": sig,
        "risk": verdict.halt or ("flatten" if verdict.flatten else "ok"),
        "action": action,
        "equity": state.last_equity,
        "halted": state.halted,
        "halt_reason": state.halt_reason,
    }


def append_log(path, entry):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
