import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import config, data, paper, report

DATA = config.ROOT / "data" / "btc_daily.csv"
STATE = config.ROOT / "state" / "paper_state.json"
LOG = config.ROOT / "state" / "decisions.jsonl"
KILL = config.ROOT / "state" / "kill_criteria.json"
REPORT = config.ROOT / "reports" / "backtest.md"


def _fetch(cfg):
    df, source = data.fetch(cfg["paper"]["symbol"])
    data.save(df, DATA)
    return df, source


def cmd_lock(cfg, args):
    print("固定しました:", config.write_lock(cfg))


def cmd_backtest(cfg, args):
    lock_hash = config.check_lock(cfg)
    df, source = (data.load(args.csv), Path(args.csv).name) if args.csv else _fetch(cfg)
    res = report.run(df["close"], cfg)
    md = report.markdown(res, cfg, source, lock_hash)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(md, "utf-8")
    if res["kill"]:
        KILL.parent.mkdir(parents=True, exist_ok=True)
        KILL.write_text(json.dumps({**res["kill"], "passed": res["passed"]}, indent=2) + "\n", "utf-8")
    print(md)


def _kill_criteria():
    return json.loads(KILL.read_text("utf-8")) if KILL.exists() else None


def cmd_paper(cfg, args):
    kill = _kill_criteria()
    if not kill or not kill.get("passed"):
        print("バックテストに合格していないので仮想売買はしません（reports/backtest.md を見てください）。")
        return
    state = paper.State.load(STATE, cfg["paper"]["initial_cash"])
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    try:
        df, _ = _fetch(cfg)
    except Exception as e:  # 取得失敗は数えて、続いたら止める
        state.api_errors += 1
        if state.api_errors >= cfg["risk"]["max_api_errors"]:
            state.halt("api_errors", True)
        state.save(STATE)
        paper.append_log(LOG, {"time": now.isoformat(), "action": "none", "error": str(e), "api_errors": state.api_errors})
        sys.exit(f"データ取得に失敗: {e}")
    state.api_errors = 0 if not state.halted else state.api_errors
    entry = paper.step(state, df, now, cfg, kill)
    state.save(STATE)
    paper.append_log(LOG, entry)
    print(json.dumps(entry, ensure_ascii=False))


def cmd_kill(cfg, args):
    """全部手じまいして止める。データが取れなくても止める。"""
    state = paper.State.load(STATE, cfg["paper"]["initial_cash"])
    state.halt("manual_kill", True)
    try:
        df, _ = _fetch(cfg)
        entry = paper.step(state, df, pd.Timestamp.now(tz="UTC").tz_localize(None), cfg, _kill_criteria())
        paper.append_log(LOG, entry)
    except Exception as e:
        print("データが取れないので、次に取れたときに手じまいします:", e)
    state.save(STATE)
    print("停止しました。BTC 保有:", state.btc)


def cmd_reset(cfg, args):
    state = paper.State.load(STATE, cfg["paper"]["initial_cash"])
    state.halted, state.halt_reason, state.halt_permanent = False, "", False
    state.consecutive_losses = state.api_errors = 0
    if args.peak:
        state.peak_equity = state.last_equity
    state.save(STATE)
    print("停止を解除しました。")


def cmd_status(cfg, args):
    print(STATE.read_text("utf-8") if STATE.exists() else "まだ仮想売買をしていません。")


def main(argv=None):
    p = argparse.ArgumentParser(prog="btcbot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lock", help="仮説を固定する（データを見る前に1回だけ）")
    b = sub.add_parser("backtest", help="バックテスト")
    b.add_argument("--csv", help="取得せずにこの CSV を使う")
    sub.add_parser("paper", help="仮想売買を1回進める")
    sub.add_parser("kill", help="全部手じまいして止める")
    r = sub.add_parser("reset", help="停止を解除する（人が判断してから）")
    r.add_argument("--peak", action="store_true", help="最高値の記録も今の資産に戻す")
    sub.add_parser("status", help="今の状態")
    args = p.parse_args(argv)
    cfg = config.load()
    {"lock": cmd_lock, "backtest": cmd_backtest, "paper": cmd_paper, "kill": cmd_kill,
     "reset": cmd_reset, "status": cmd_status}[args.cmd](cfg, args)
