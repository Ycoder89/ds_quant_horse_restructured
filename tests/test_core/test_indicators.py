"""test_indicators.py — 指标纯函数契约测试"""
from __future__ import annotations

import numpy as np
import pytest

from core.indicators import adx, atr, ema, sma, vwap, volatility


# =============================================================================
# SMA
# =============================================================================

class TestSMA:

    def test_constant_series(self):
        data = np.array([10.0] * 20)
        result = sma(data, 5)
        assert not np.any(np.isnan(result[4:]))
        np.testing.assert_allclose(result[4:], 10.0)

    def test_linear_series(self, np_prices_10):
        _, _, close, _ = np_prices_10
        result = sma(close, 3)
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert not np.isnan(result[2])
        expected = (101.0 + 102.0 + 103.5) / 3.0
        assert result[2] == pytest.approx(expected)

    def test_period_too_large_returns_all_nan(self):
        data = np.array([1.0, 2.0, 3.0])
        result = sma(data, 10)
        assert np.all(np.isnan(result))


# =============================================================================
# EMA
# =============================================================================

class TestEMA:

    def test_constant_series(self):
        data = np.array([5.0] * 30)
        result = ema(data, 10)
        assert not np.any(np.isnan(result[9:]))
        np.testing.assert_allclose(result[9:], 5.0, atol=1e-6)

    def test_linear_series(self, np_prices_10):
        _, _, close, _ = np_prices_10
        result = ema(close, 5)
        assert np.isnan(result[0])
        assert not np.isnan(result[4])


# =============================================================================
# ATR
# =============================================================================

class TestATR:

    def test_constant_spread(self):
        n = 30
        high = np.full(n, 100.0)
        low = np.full(n, 98.0)
        close = np.full(n, 99.0)
        result = atr(high, low, close, 14)
        assert not np.any(np.isnan(result[14:]))
        np.testing.assert_allclose(result[14:], 2.0, atol=0.1, rtol=0.1)

    def test_output_same_length(self, np_prices_50):
        high, low, close, _ = np_prices_50
        result = atr(high, low, close, 14)
        assert len(result) == len(close)

    def test_warmup_is_nan(self, np_prices_50):
        high, low, close, _ = np_prices_50
        result = atr(high, low, close, 14)
        assert np.all(np.isnan(result[:14]))
        assert not np.isnan(result[14])

    def test_short_input_all_nan(self):
        arr = np.array([100.0, 101.0, 102.0])
        result = atr(arr, arr, arr, 14)
        assert np.all(np.isnan(result))


# =============================================================================
# ADX
# =============================================================================

class TestADX:

    def test_output_same_length(self, np_prices_50):
        high, low, close, _ = np_prices_50
        result = adx(high, low, close, 14)
        assert len(result) == len(close)

    def test_warmup_is_nan(self, np_prices_50):
        high, low, close, _ = np_prices_50
        result = adx(high, low, close, 14)
        assert np.all(np.isnan(result[:27]))
        assert not np.isnan(result[27])

    def test_adx_low_on_range(self):
        n = 50
        rng = np.random.default_rng(99)
        base = 100.0
        close = base + rng.normal(0, 0.2, n)
        high = close + 0.3
        low = close - 0.3
        result = adx(high, low, close, 14)
        valid = result[~np.isnan(result)]
        assert np.all(valid < 40)


# =============================================================================
# VWAP
# =============================================================================

class TestVWAP:

    def test_cumulative_vwap(self, np_prices_10):
        high, low, close, vol = np_prices_10
        result = vwap(high, low, close, vol)
        assert len(result) == len(close)
        typical = (high + low + close) / 3.0
        vp = typical * vol
        expected_final = np.sum(vp) / np.sum(vol)
        assert result[-1] == pytest.approx(expected_final)

    def test_zero_volume_leads_to_nan(self):
        high = np.array([100.0, 101.0])
        low = np.array([99.0, 100.0])
        close = np.array([100.0, 101.0])
        vol = np.array([0.0, 0.0])
        result = vwap(high, low, close, vol)
        assert np.all(np.isnan(result))


# =============================================================================
# Volatility
# =============================================================================

class TestVolatility:

    def test_output_shape(self, np_prices_50):
        _, _, close, _ = np_prices_50
        result = volatility(close, 5)
        assert len(result) == len(close)

    def test_constant_series_near_zero(self):
        close = np.full(30, 100.0)
        result = volatility(close, 5)
        assert not np.any(np.isnan(result[4:]))
        np.testing.assert_allclose(result[4:], 0.0, atol=0.1)