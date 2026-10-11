"""5分足 BTC Up/Down の仮想売買ボット（サーバー版）。

GitHub Actions で約6時間ずつ交代で動き、$0 になるまで売買を続ける。
状態は desk-data ブランチの desk.json に保存し、画面（web/index.html）がそれを読む。
本物のお金は使わない。
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

CFG = dict(WIN=300, MIN_EDGE=0.04, FEE=0.01, KELLY=0.25, TICKET_CAP=25.0, WINDOW_CAP=100.0, COOLDOWN=15,
           START=1000.0, MIN_LEFT=15, MIN_PRICE=0.10, MAX_PRICE=0.90, MAX_EDGE=0.15)
BINANCE = "https://data-api.binance.vision/api/v3"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HTTP = requests.Session()
HTTP.headers["User-Agent"] = "btcbot-paper/1.0"


def now():
    return time.time()


def get(url, **params):
    r = HTTP.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ---------- account (same shape the page reads) ----------

def fresh_account():
    return {"cash": CFG["START"], "tickets": [], "hist": [{"t": now(), "eq": CFG["START"]}], "calib": [], "wins": [],
            "pend": [], "broke": False, "started": now(), "status": {}}


def equity(a):
    return a["cash"] + sum(t["stake"] for t in a["tickets"] if not t["settled"])


def is_broke(a):
    """お金がなくなり、持っている券もない = 終わり。"""
    return a["cash"] < 0.01 and not any(not t["settled"] for t in a["tickets"])


# ---------- model ----------

def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def vol_per_sec(closes):
    r = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:])]
    if len(r) < 10:
        return 0.0006 / math.sqrt(60)
    m = sum(r) / len(r)
    v = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return max(math.sqrt(v), 0.00005) / math.sqrt(60)


def p_up(price, ptb, sigma_s, seconds_left):
    """終わりの値段が基準より上になる確率。画面の 20,480 通りのシミュレーションと同じ答え。"""
    sd = sigma_s * math.sqrt(max(seconds_left, 0.5))
    return phi((math.log(price / ptb) - 0.5 * sd * sd) / sd)


# ---------- trading decision ----------

def decide(a, win_start, t, prob, ask_up, ask_down, last_ticket_t):
    """買うなら券（dict）、買わないなら (None, 理由)。"""
    if a["broke"]:
        return None, "broke"
    if ask_up is None or ask_down is None:
        return None, "no book"
    e_up = prob - ask_up - CFG["FEE"]
    e_down = (1 - prob) - ask_down - CFG["FEE"]
    side, edge, ask = ("UP", e_up, ask_up) if e_up >= e_down else ("DOWN", e_down, ask_down)
    left = win_start + CFG["WIN"] - t
    spent = sum(x["stake"] for x in a["tickets"] if x["win"] == win_start)
    if edge < CFG["MIN_EDGE"]:
        return None, "edge too small"
    if edge > CFG["MAX_EDGE"]:
        return None, "edge too good to be true"
    if not CFG["MIN_PRICE"] <= ask <= CFG["MAX_PRICE"]:
        return None, "price out of range"
    if left < CFG["MIN_LEFT"] or left > CFG["WIN"] - 5:
        return None, "too close to the bell"
    if spent >= CFG["WINDOW_CAP"]:
        return None, "window cap"
    if t - last_ticket_t < CFG["COOLDOWN"]:
        return None, "cooling down"
    f = edge / (1 - ask) * CFG["KELLY"]
    stake = min(a["cash"] * f, CFG["TICKET_CAP"], CFG["WINDOW_CAP"] - spent, a["cash"])
    if a["cash"] < 1:
        shares = a["cash"] / (ask + CFG["FEE"])  # last coins: bet everything that is left, so the wallet really reaches $0
    else:
        shares = max(1, math.floor(stake / ask))
    stake = shares * ask
    fee = shares * CFG["FEE"]
    if spent + stake > CFG["WINDOW_CAP"] + 1e-9:
        return None, "window cap"
    if stake + fee > a["cash"] + 1e-9:
        return None, "can't afford this ticket"
    return {"id": int(t * 1000), "win": win_start, "t": t, "side": side, "shares": shares, "price": ask,
            "stake": stake, "fee": fee, "edge": edge, "model": prob, "settled": False}, "fill"


def buy(a, ticket):
    a["cash"] -= ticket["stake"] + ticket["fee"]
    a["tickets"].append(ticket)


def settle(a, w, up, final, src):
    pnl, n = 0.0, 0
    for t in a["tickets"]:
        if t["win"] == w["start"] and not t["settled"]:
            won = (t["side"] == "UP") == up
            t.update(settled=True, won=won, src=src, pnl=(t["shares"] if won else 0) - t["stake"] - t["fee"])
            a["cash"] += t["shares"] if won else 0
            pnl += t["pnl"]
            n += 1
    a["wins"].append({"start": w["start"], "up": up, "ptb": w["ptb"], "final": final, "pnl": pnl, "n": n, "src": src})
    a["wins"] = a["wins"][-500:]
    a["hist"].append({"t": now(), "eq": equity(a)})
    a["hist"] = a["hist"][-2000:]
    a["tickets"] = a["tickets"][-1500:]
    if is_broke(a) and not a["broke"]:
        a["broke"] = True
        a["broke_at"] = now()


# ---------- market data ----------

def binance_price():
    return float(get(f"{BINANCE}/ticker/price", symbol="BTCUSDT")["price"])


def binance_closes():
    return [float(k[4]) for k in get(f"{BINANCE}/klines", symbol="BTCUSDT", interval="1m", limit=61)[:-1]]


def binance_open(t):
    j = get(f"{BINANCE}/klines", symbol="BTCUSDT", interval="1m", startTime=int(t * 1000), limit=1)
    return float(j[0][1]) if j and j[0][0] == int(t * 1000) else None


def poly_market(start):
    slug = f"btc-updown-5m-{start}"
    m = None
    try:
        j = get(f"{GAMMA}/markets", slug=slug)
        m = j[0] if isinstance(j, list) and j else None
    except requests.RequestException:
        pass
    if not m or not m.get("clobTokenIds"):
        j = get(f"{GAMMA}/events", slug=slug)
        ev = j[0] if isinstance(j, list) and j else None
        m = ev["markets"][0] if ev and ev.get("markets") else None
    return m


def poly_tokens(m):
    outs, toks = json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])
    iu = next(i for i, o in enumerate(outs) if "up" in o.lower())
    idn = next(i for i, o in enumerate(outs) if "down" in o.lower())
    return toks[iu], toks[idn]


def poly_result(start):
    """Polymarket の正式な結果。True=上、False=下、None=まだ。"""
    m = poly_market(start)
    if not m or not m.get("outcomePrices"):
        return None
    outs, prices = json.loads(m["outcomes"]), [float(x) for x in json.loads(m["outcomePrices"])]
    iu = next(i for i, o in enumerate(outs) if "up" in o.lower())
    if prices[iu] >= 0.99:
        return True
    if prices[iu] <= 0.01:
        return False
    return None


def best_ask(token):
    asks = get(f"{CLOB}/book", token_id=token).get("asks") or []
    return min((float(x["price"]) for x in asks), default=None)


# ---------- persistence: desk.json on the desk-data branch ----------

def load_state():
    if os.environ.get("RESET") == "true":
        return fresh_account()
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "desk-data"], check=True, capture_output=True)
        out = subprocess.run(["git", "show", "origin/desk-data:desk.json"], check=True, capture_output=True, text=True).stdout
        return json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return fresh_account()


def push_state(a):
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        Path("desk.json").write_text(json.dumps(a), "utf-8")
        return
    with tempfile.TemporaryDirectory() as d:
        Path(d, "desk.json").write_text(json.dumps(a, separators=(",", ":")), "utf-8")
        g = ["git", "-C", d, "-c", "user.name=github-actions[bot]",
             "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
        subprocess.run(["git", "init", "-q", "-b", "desk-data", d], check=True)
        subprocess.run(g + ["add", "desk.json"], check=True)
        subprocess.run(g + ["commit", "-qm", "desk"], check=True)
        subprocess.run(g + ["push", "-qf", f"https://x-access-token:{token}@github.com/{repo}.git", "desk-data"],
                       check=True, capture_output=True)


# ---------- main loop ----------

def run(seconds):
    a = load_state()
    a.setdefault("status", {})
    stop_at = now() + seconds
    win = None
    mk = {}
    closes, closes_t = [], 0
    last_ticket = 0
    last_push = last_book = last_settle = 0
    errors = 0
    print(f"start: cash ${a['cash']:.2f} equity ${equity(a):.2f} broke={a['broke']}", flush=True)
    if a["broke"]:
        print("already at $0. run the desk workflow with reset=true to start again", flush=True)
        return a
    while now() < stop_at:
        t = now()
        try:
            price = binance_price()
            errors = 0
            if t - closes_t > 60:
                closes, closes_t = binance_closes(), t
            start = int(t // CFG["WIN"] * CFG["WIN"])
            if not win or win["start"] != start:
                if win:
                    a["pend"].append({"start": win["start"], "ptb": win["ptb"]})
                ptb = None
                for _ in range(10):  # the window's 1m candle appears a moment after the bell
                    ptb = binance_open(start)
                    if ptb:
                        break
                    time.sleep(1)
                win = {"start": start, "ptb": ptb, "calib": False}
                mk = {"start": start, "tries": 0}
            if win["ptb"] is None:
                time.sleep(1)
                continue
            # find the market once per window, retry every 20 s
            if "up" not in mk and t - mk.get("tried", 0) > 20 and mk["tries"] < 10:
                mk["tried"], mk["tries"] = t, mk["tries"] + 1
                try:
                    m = poly_market(start)
                    if m and m.get("clobTokenIds"):
                        mk["up"], mk["down"] = poly_tokens(m)
                        a["status"]["market"] = f"btc-updown-5m-{start}"
                    else:
                        a["status"]["market"] = "not found"
                except requests.RequestException as e:
                    a["status"]["market"] = f"error {e.__class__.__name__}"
            if "up" in mk and t - last_book >= 2:
                last_book = t
                try:
                    mk["ask_up"], mk["ask_down"] = best_ask(mk["up"]), best_ask(mk["down"])
                    a["status"]["book"] = f"{mk['ask_up']}/{mk['ask_down']}"
                except requests.RequestException as e:
                    mk["ask_up"] = mk["ask_down"] = None
                    a["status"]["book"] = f"error {e.__class__.__name__}"
            left = start + CFG["WIN"] - t
            prob = p_up(price, win["ptb"], vol_per_sec(closes), left)
            if not win["calib"] and left <= 90:
                win["calib"] = True
                a["calib"].append({"start": start, "p": prob, "ptb": win["ptb"]})
                a["calib"] = a["calib"][-500:]
            ticket, why = decide(a, start, t, prob, mk.get("ask_up"), mk.get("ask_down"), last_ticket)
            a["status"]["last_decision"] = why
            if ticket:
                buy(a, ticket)
                last_ticket = t
                print(f"{time.strftime('%H:%M:%S', time.gmtime(t))} BUY {ticket['side']} {ticket['shares']} @ "
                      f"{ticket['price']:.2f} edge {ticket['edge']:+.3f} cash ${a['cash']:.2f}", flush=True)
            if t - last_settle >= 10:
                last_settle = t
                settle_pending(a)
            a["status"].update(updated=t, price=price, p_up=prob, window=start, ptb=win["ptb"], feed="binance")
        except requests.RequestException as e:
            errors += 1
            a["status"]["feed"] = f"error {e.__class__.__name__} x{errors}"
            time.sleep(min(30, 2 ** errors))
        if t - last_push >= 60:
            last_push = t
            try:
                push_state(a)
            except subprocess.CalledProcessError as e:
                print("push failed:", e.stderr, file=sys.stderr, flush=True)
        if a["broke"]:
            print("broke: $0, trading stopped", flush=True)
            push_state(a)
            return a
        time.sleep(max(0.0, 1 - (now() - t)))
    push_state(a)
    print(f"shift over: cash ${a['cash']:.2f} equity ${equity(a):.2f}", flush=True)
    return a


def settle_pending(a):
    for w in list(a["pend"]):
        end = w["start"] + CFG["WIN"]
        has = any(t["win"] == w["start"] and not t["settled"] for t in a["tickets"])
        up, src, final = None, "", None
        try:
            if has:
                up = poly_result(w["start"])
                src = "polymarket" if up is not None else ""
            if up is None and (not has or now() - end > 900):
                final = binance_open(end)
                if final is not None:
                    up, src = final > w["ptb"], "binance"
            if up is None:
                continue
            if final is None:
                final = binance_open(end)
        except requests.RequestException:
            continue
        a["pend"] = [x for x in a["pend"] if x["start"] != w["start"]]
        settle(a, w, up, final, src)


if __name__ == "__main__":
    run(int(os.environ.get("RUN_SECONDS", "21000")))
