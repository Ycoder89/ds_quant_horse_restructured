"""
core/indicators.py — 轻量技术指标封装

基于 TA-Lib 的简洁函数式封装，提供策略所需的常用指标计算。
不面向对象，所有函数为纯函数，输入 NumPy 数组、输出 NumPy 数组。
"""
from __future__ import annotations

import numpy as np
import talib


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Average True Range"""
    return talib.ATR(high, low, close, timeperiod=period)


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Average Directional Index"""
    return talib.ADX(high, low, close, timeperiod=period)


def sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average"""
    return talib.SMA(data, timeperiod=period)


def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average"""
    return talib.EMA(data, timeperiod=period)


def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray) -> np.ndarray:
    """Volume Weighted Average Price (cumulative)"""
    typical = (high + low + close) / 3.0
    vp = typical * volume
    cum_vp = np.cumsum(vp)
    cum_vol = np.cumsum(volume)
    vwap_arr = np.full_like(close, np.nan)
    mask = cum_vol > 0
    vwap_arr[mask] = cum_vp[mask] / cum_vol[mask]
    return vwap_arr


def vwap_daily(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               volume: np.ndarray, daily_start_indices: np.ndarray) -> np.ndarray:
    """Session-reset VWAP (daily/session based on provided start indices)"""
    result = np.full_like(close, np.nan)
    for i, start in enumerate(daily_start_indices):
        end = daily_start_indices[i + 1] if i + 1 < len(daily_start_indices) else len(close)
        result[start:end] = vwap(high[start:end], low[start:end],
                                 close[start:end], volume[start:end])
    return result


def pivots(high: np.ndarray, low: np.ndarray, period: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Swing highs and lows within rolling windows"""
    highs = np.zeros_like(high)
    lows = np.zeros_like(low)
    for i in range(period - 1, len(high)):
        window_h = high[i - period + 1:i + 1]
        window_l = low[i - period + 1:i + 1]
        if high[i] == window_h.max():
            highs[i] = high[i]
        if low[i] == window_l.min():
            lows[i] = low[i]
    return highs, lows


def volatility(close: np.ndarray, period: int = 20) -> np.ndarray:
    """Rolling standard deviation of returns (annualizable, pct)"""
    returns = np.diff(np.log(close), prepend=np.log(close[0:1]))
    return talib.STDDEV(returns, timeperiod=period) * 100.0
