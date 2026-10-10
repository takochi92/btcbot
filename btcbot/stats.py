import math
from statistics import NormalDist

import numpy as np
import pandas as pd

PERIODS = 365  # 暗号資産は毎日動く
EULER_GAMMA = 0.5772156649
_N = NormalDist()


def sharpe(returns: pd.Series, periods=PERIODS) -> float:
    sd = returns.std(ddof=1)
    return 0.0 if sd == 0 or np.isnan(sd) else float(returns.mean() / sd * math.sqrt(periods))


def max_drawdown(returns: pd.Series) -> float:
    eq = (1 + returns).cumprod()
    return float((1 - eq / eq.cummax()).max())


def cagr(returns: pd.Series, periods=PERIODS) -> float:
    total = float((1 + returns).prod())
    years = len(returns) / periods
    return total ** (1 / years) - 1 if years > 0 and total > 0 else -1.0


def deflated_sharpe(returns: pd.Series, variant_sharpes_per_period) -> float:
    """Bailey & López de Prado (2014) のデフレーテッド・シャープ比。

    試した変種が多いほど「一番よかったもの」は運でよく見えるので、そのぶん基準を上げて
    「本当にプラスである確率」を出す。シャープ比は日次（年率化しない）で渡す。
    """
    r = returns.dropna()
    t = len(r)
    sd = r.std(ddof=1)
    if t < 3 or sd == 0:
        return 0.0
    sr = r.mean() / sd
    n = len(variant_sharpes_per_period)
    if n > 1:
        v = float(np.var(variant_sharpes_per_period, ddof=1))
        sr0 = math.sqrt(v) * (
            (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n) + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n * math.e))
        )
    else:
        sr0 = 0.0
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3  # pandas は超過尖度を返すので通常の尖度に直す
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))
