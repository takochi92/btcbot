import numpy as np
import pandas as pd

from btcbot import backtest, report, stats


def test_no_lookahead(random_walk):
    """未来の値段を変えても、過去の持ち方は変わらない。"""
    a = backtest.run(random_walk, 50, 0.001, 0.0005)
    changed = random_walk.copy()
    changed.iloc[1000:] *= 3
    b = backtest.run(changed, 50, 0.001, 0.0005)
    pd.testing.assert_series_equal(a.position.iloc[:1001], b.position.iloc[:1001])


def test_decision_day_return_not_captured():
    """シグナルが出た日の値動きは取れない（翌日から）。"""
    close = pd.Series([100.0] * 5 + [200.0, 200.0], index=pd.date_range("2024-01-01", periods=7))
    r = backtest.run(close, 3, 0.0, 0.0)
    assert r.returns.iloc[5] == 0.0  # 100→200 の日はまだ持っていない
    assert r.position.iloc[6] == 1.0


def test_round_trip_cost():
    close = pd.Series([100.0, 100, 100, 101, 101, 101, 90, 90, 90], index=pd.date_range("2024-01-01", periods=9))
    r = backtest.run(close, 2, 0.001, 0.0005)
    assert r.position.diff().abs().sum() == 2  # 1回買って1回売る
    assert len(r.trades) == 1
    gross = (1 + (close.pct_change().fillna(0) * r.position)).prod() - 1
    assert r.trades[0] < gross  # コスト分だけ悪い


def test_deflated_sharpe_penalises_many_variants(random_walk):
    ret = random_walk.pct_change().dropna() + 0.002
    one = stats.deflated_sharpe(ret, [0.05])
    many = stats.deflated_sharpe(ret, list(np.linspace(-0.05, 0.05, 50)))
    assert 0 <= many < one <= 1


def test_cv_embargo_excludes_neighbours(random_walk):
    folds, oos = backtest.purged_cv(random_walk, [20, 50], 0.001, 0.0005, 5, 60)
    assert folds and len(oos) <= len(random_walk)
    assert {f["chosen_lookback"] for f in folds} <= {20, 50}


def test_report_runs_and_reports_failure(random_walk, cfg):
    res = report.run(random_walk, cfg)
    md = report.markdown(res, cfg, "test", "0" * 64)
    assert "判定" in md and "ガチホ" in md
    for lb in cfg["hypothesis"]["variants"]:
        assert f"{lb}日移動平均" in md
