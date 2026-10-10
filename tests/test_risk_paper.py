import copy

import pandas as pd
import pytest

from btcbot import config, paper, risk
from conftest import make_bars

UP = [100.0 + i for i in range(150)]  # ずっと上がる → 100日線の上


def _cfg(cfg, **risk_over):
    c = copy.deepcopy(cfg)
    c["risk"].update(risk_over)
    return c


def _now(bars, days_after=1):
    return bars.index[-1] + pd.Timedelta(days=days_after, minutes=10)


def test_hard_caps_cannot_be_loosened(cfg):
    lim = risk.Limits.from_config({**cfg["risk"], "max_position_frac": 5.0, "max_drawdown": 0.9})
    assert lim.max_position_frac == 1.0
    assert lim.max_drawdown == 0.5


def test_buys_on_signal_and_sells_when_it_turns(cfg):
    st = paper.State(cash=10000)
    bars = make_bars(UP)
    assert paper.step(st, bars, _now(bars), cfg)["action"] == "buy"
    assert st.btc > 0 and st.cash == 0
    down = make_bars(UP + [150.0 - 3 * i for i in range(1, 40)])
    assert paper.step(st, down, _now(down), cfg)["action"] == "sell"
    assert st.btc == 0 and len(st.closed_trades) == 1


def test_max_drawdown_kill_switch(cfg):
    st = paper.State(cash=0, btc=100, peak_equity=100 * 249 / 0.69, entry_value=10000)
    bars = make_bars(UP + [249.0])
    log = paper.step(st, bars, _now(bars), cfg)
    assert log["action"] == "sell" and st.halted and st.halt_permanent
    assert st.halt_reason == "max_drawdown"
    # 次の日になっても止まったまま
    later = make_bars(UP + [249.0, 260.0])
    assert paper.step(st, later, _now(later), cfg)["action"] == "hold"
    assert st.btc == 0


def test_daily_loss_flattens_then_resumes_next_day(cfg):
    bars = make_bars(UP)
    st = paper.State(cash=0, btc=100, peak_equity=100 * 249, last_equity=100 * 249 / 0.94, entry_value=10000)
    log = paper.step(st, bars, _now(bars), cfg)
    assert log["action"] == "sell" and log["risk"] == "daily_loss" and not st.halt_permanent
    nxt = make_bars(UP + [250.0])
    assert paper.step(st, nxt, _now(nxt), cfg)["action"] == "buy"


def test_stale_data_does_not_open(cfg):
    st = paper.State(cash=10000)
    bars = make_bars(UP)
    assert paper.step(st, bars, _now(bars, days_after=5), cfg)["action"] == "hold"
    assert st.btc == 0 and not st.halted


def test_consecutive_losses_halt(cfg):
    st = paper.State(cash=0, btc=1, entry_value=10**9, consecutive_losses=4)
    down = make_bars(UP + [150.0 - 3 * i for i in range(1, 40)])
    paper.step(st, down, _now(down), cfg)
    assert st.consecutive_losses == 5 and st.halted and st.halt_reason == "consecutive_losses"


def test_live_below_backtest_stops(cfg):
    kill = {"mean": 0.05, "std": 0.1, "z": 1.645, "min_trades": 10}
    st = paper.State(cash=10000, closed_trades=[-0.02] * 10)
    bars = make_bars(UP)
    assert paper.step(st, bars, _now(bars), cfg, kill)["action"] == "hold"
    assert st.halt_reason == "live_below_backtest"


def test_manual_kill_flattens(cfg):
    st = paper.State(cash=0, btc=10, entry_value=1000)
    st.halt("manual_kill", True)
    bars = make_bars(UP)
    assert paper.step(st, bars, _now(bars), cfg)["action"] == "sell"


def test_markouts_recorded(cfg):
    st = paper.State(cash=10000)
    bars = make_bars(UP)
    paper.step(st, bars, _now(bars), cfg)
    more = make_bars(UP + [250.0, 251.0, 252.0])
    paper.step(st, more, _now(more), cfg)
    assert "markout_1d" in st.fills[0] and "markout_3d" in st.fills[0]


def test_lock_detects_change(cfg, tmp_path):
    lock = tmp_path / "hypothesis.lock"
    config.write_lock(cfg, lock)
    config.check_lock(cfg, lock)
    changed = copy.deepcopy(cfg)
    changed["hypothesis"]["main_lookback"] = 50
    with pytest.raises(config.HypothesisChanged):
        config.check_lock(changed, lock)
