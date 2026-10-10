"""バックテスト。結果がアイデアを否定しても、そのまま書く。"""
import math

import pandas as pd

from . import backtest, stats

Z_95_ONE_SIDED = 1.645


def run(close: pd.Series, cfg):
    hyp, cv = cfg["hypothesis"], cfg["cv"]
    fee, slip = hyp["fee_rate"], hyp["slippage_rate"]
    warmup = max(hyp["variants"])
    # 全変種が動き出せる日から比べる（それより前は全員現金なので比べようがない）
    variants = {lb: backtest.run(close, lb, fee, slip) for lb in hyp["variants"]}
    window = close.index[warmup:]
    rets = {lb: r.returns.loc[window] for lb, r in variants.items()}
    bh = backtest.buy_and_hold(close.loc[window], fee, slip)

    main = variants[hyp["main_lookback"]]
    main_ret = rets[hyp["main_lookback"]]
    daily_srs = [r.mean() / r.std(ddof=1) for r in rets.values()]
    dsr = stats.deflated_sharpe(main_ret, daily_srs)
    folds, oos = backtest.purged_cv(close, hyp["variants"], fee, slip, cv["n_folds"], cv["embargo_days"])
    pos_share = sum(f["test_return"] > 0 for f in folds) / len(folds) if folds else 0.0

    checks = {
        "dsr": dsr >= hyp["min_dsr"],
        "folds": pos_share >= hyp["min_positive_fold_share"],
        "drawdown": stats.max_drawdown(main_ret) < stats.max_drawdown(bh),
    }
    trades = backtest._trades(main_ret, main.position.loc[window])
    kill = None
    if len(trades) >= 2:
        s = pd.Series(trades)
        kill = {
            "mean": float(s.mean()),
            "std": float(s.std(ddof=1)),
            "z": Z_95_ONE_SIDED,
            "min_trades": cfg["paper"]["min_trades_for_kill_check"],
            "backtest_trades": len(trades),
        }
    return {
        "window": (window[0], window[-1]),
        "rets": rets,
        "bh": bh,
        "main_lookback": hyp["main_lookback"],
        "dsr": dsr,
        "folds": folds,
        "oos": oos,
        "pos_share": pos_share,
        "checks": checks,
        "passed": all(checks.values()),
        "trades": trades,
        "kill": kill,
    }


def _row(name, r):
    return (
        f"| {name} | {stats.cagr(r):+.1%} | {stats.sharpe(r):.2f} | {stats.max_drawdown(r):.1%} |"
    )


def markdown(res, cfg, source, lock_hash):
    hyp = cfg["hypothesis"]
    a, b = res["window"]
    lines = [
        "# バックテスト結果",
        "",
        f"- 期間: {a.date()} 〜 {b.date()}（{len(res['bh'])} 日）",
        f"- データ: {source} の BTC 日足",
        f"- 仮説の固定値: `{lock_hash[:16]}`（hypothesis.lock と一致）",
        f"- 手数料 片道 {hyp['fee_rate']:.2%}、ずれ 片道 {hyp['slippage_rate']:.2%}",
        "",
        "## 判定: " + ("**合格**（仮想売買に進んでよい）" if res["passed"] else "**不合格**（このアイデアは採用しない）"),
        "",
        f"- デフレーテッド・シャープ比 {res['dsr']:.3f}（基準 {hyp['min_dsr']}） → {'OK' if res['checks']['dsr'] else 'NG'}",
        f"- 時期を分けた検証でプラスだった割合 {res['pos_share']:.0%}（基準 {hyp['min_positive_fold_share']:.0%}） → {'OK' if res['checks']['folds'] else 'NG'}",
        f"- 最大下落率がガチホより小さいか → {'OK' if res['checks']['drawdown'] else 'NG'}",
        "",
        "## 試した変種すべて（後から選んだものを隠さない）",
        "",
        "| 戦略 | 年率リターン | シャープ比 | 最大下落率 |",
        "|---|---|---|---|",
        _row("ガチホ（比較用）", res["bh"]),
    ]
    for lb, r in res["rets"].items():
        mark = " ← 本命" if lb == res["main_lookback"] else ""
        lines.append(_row(f"{lb}日移動平均{mark}", r))
    lines += [
        "",
        f"本命の売買回数: {len(res['trades'])} 回"
        + (f"、勝率 {sum(t > 0 for t in res['trades']) / len(res['trades']):.0%}" if res["trades"] else ""),
        "",
        f"## 時期を分けた検証（前後 {cfg['cv']['embargo_days']} 日を空けて学習）",
        "",
        "| 区間 | 期間 | 学習で選ばれた日数 | 検証シャープ比 | 検証リターン |",
        "|---|---|---|---|---|",
    ]
    for f in res["folds"]:
        lines.append(
            f"| {f['fold']} | {f['start']}〜{f['end']} | {f['chosen_lookback']} | {f['test_sharpe']:.2f} | {f['test_return']:+.1%} |"
        )
    if len(res["oos"]):
        lines += ["", "検証区間だけをつないだ成績: " + _row("", res["oos"]).replace("|  |", "").strip(" |").replace(" | ", " / ")]
    k = res["kill"]
    if k:
        lb10 = k["mean"] - k["z"] * k["std"] / math.sqrt(k["min_trades"])
        lines += [
            "",
            "## 仮想売買の停止ルール",
            "",
            f"バックテストの1回あたり平均 {k['mean']:+.2%}、ばらつき {k['std']:.2%}。",
            f"仮想売買で {k['min_trades']} 回以上売買したあと、平均がその時点の下側95%限界"
            f"（{k['min_trades']} 回なら {lb10:+.2%}）を下回ったらボットは自分で止まる。",
        ]
    return "\n".join(lines) + "\n"
