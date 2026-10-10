"""BTC 日足の取得。確定した足だけを返す（まだ終わっていない今日の足は捨てる）。"""
import time
from pathlib import Path

import pandas as pd
import requests

COLUMNS = ["open", "high", "low", "close", "volume"]
DAY_MS = 86_400_000


def _binance(symbol, start="2017-08-17"):
    url = "https://data-api.binance.vision/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    rows = []
    while True:
        r = requests.get(url, params={"symbol": symbol, "interval": "1d", "startTime": start_ms, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows += [b for b in batch if b[6] < now_ms]  # b[6] = 足の終了時刻
        start_ms = batch[-1][0] + DAY_MS
        if len(batch) < 1000:
            break
    df = pd.DataFrame([b[:6] for b in rows], columns=["time", *COLUMNS])
    df.index = pd.to_datetime(df.pop("time"), unit="ms")
    return df.astype(float)


def _coinbase(symbol, start="2015-07-21"):
    product = "BTC-USD" if symbol.startswith("BTC") else symbol
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
    cur = pd.Timestamp(start)
    rows = []
    while cur < today:
        end = min(cur + pd.Timedelta(days=299), today)
        r = requests.get(url, params={"granularity": 86400, "start": cur.isoformat(), "end": end.isoformat()}, timeout=20)
        r.raise_for_status()
        rows += r.json()  # [time, low, high, open, close, volume]
        cur = end + pd.Timedelta(days=1)
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    df.index = pd.to_datetime(df.pop("time"), unit="s")
    df = df[~df.index.duplicated()].sort_index()
    return df[df.index < today][COLUMNS].astype(float)


def fetch(symbol="BTCUSDT"):
    """(DataFrame, 取得元) を返す。両方失敗したら最後の例外をそのまま投げる。"""
    err = None
    for name, fn in (("binance", _binance), ("coinbase", _coinbase)):
        try:
            df = fn(symbol)
            if len(df) > 0:
                return df.sort_index(), name
        except requests.RequestException as e:
            err = e
    raise err or RuntimeError("データが空でした")


def save(df, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="date")


def load(path):
    return pd.read_csv(path, index_col="date", parse_dates=True)
