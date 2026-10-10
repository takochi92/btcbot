from dataclasses import dataclass

import pandas as pd

from . import stats
from .strategy import signal


@dataclass
class Result:
    lookback: int
    returns: pd.Series   # 手数料・ずれ込みの日次リターン
    position: pd.Series  # その日に持っていた量（0 か 1）
    trades: list         # 1回の売買ごとのリターン（入口と出口のコスト込み）


def run(close: pd.Series, lookback: int, fee_rate: float, slippage_rate: float) -> Result:
    # t日の終値で決めて、t+1日の値動きを持つ。決めた日の値動きは取れない（先読みなし）。
    pos = signal(close, lookback).shift(1).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    cost = pos.diff().abs().fillna(pos.iloc[0]) * (fee_rate + slippage_rate)
    strat = pos * ret - cost
    return Result(lookback, strat, pos, _trades(strat, pos))


def _trades(strat: pd.Series, pos: pd.Series) -> list:
    trades, acc, holding = [], 1.0, False
    for r, p in zip(strat.to_numpy(), pos.to_numpy()):
        if p > 0:
            acc *= 1 + r
            holding = True
        elif holding:
            acc *= 1 + r  # 手じまいの日のコスト
            trades.append(acc - 1)
            acc, holding = 1.0, False
    return trades


def buy_and_hold(close: pd.Series, fee_rate: float, slippage_rate: float) -> pd.Series:
    ret = close.pct_change().fillna(0.0)
    ret.iloc[0] -= fee_rate + slippage_rate
    return ret


def purged_cv(close: pd.Series, variants, fee_rate, slippage_rate, n_folds, embargo_days):
    """時期を n_folds 個に分け、1つを検証、残りで変種を選ぶ。

    検証期間の前後 embargo_days 日は学習に使わない（移動平均を通じて検証期間の値段が漏れるのを防ぐ）。
    """
    results = {lb: run(close, lb, fee_rate, slippage_rate).returns for lb in variants}
    n = len(close)
    edges = [round(i * n / n_folds) for i in range(n_folds + 1)]
    folds, oos = [], []
    for k in range(n_folds):
        a, b = edges[k], edges[k + 1]
        train = [i for i in range(n) if i < a - embargo_days or i >= b + embargo_days]
        # 一番長い移動平均ができるまでの期間は全変種が現金なので、学習から外す
        train = [i for i in train if i >= max(variants)]
        if len(train) < 30:
            best = None
        else:
            best = max(variants, key=lambda lb: stats.sharpe(results[lb].iloc[train]))
        if best is None:
            continue
        test = results[best].iloc[a:b]
        oos.append(test)
        folds.append({
            "fold": k + 1,
            "start": str(close.index[a].date()),
            "end": str(close.index[b - 1].date()),
            "chosen_lookback": best,
            "test_sharpe": stats.sharpe(test),
            "test_return": float((1 + test).prod() - 1),
        })
    return folds, (pd.concat(oos) if oos else pd.Series(dtype=float))
