import numpy as np
import pandas as pd
import pytest

from btcbot import config


@pytest.fixture
def cfg():
    return config.load()


def make_bars(closes, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0})


@pytest.fixture
def random_walk():
    rng = np.random.default_rng(0)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 1500))),
                     index=pd.date_range("2018-01-01", periods=1500, freq="D"))
