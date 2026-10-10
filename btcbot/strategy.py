import pandas as pd


def signal(close: pd.Series, lookback: int) -> pd.Series:
    """終値が移動平均より上なら 1（保有）、下なら 0（現金）。その日の終値までしか使わない。"""
    sma = close.rolling(lookback).mean()
    return (close > sma).astype(float).where(sma.notna(), 0.0)
