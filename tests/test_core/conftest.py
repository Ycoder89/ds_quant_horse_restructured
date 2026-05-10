"""
conftest.py — shared fixtures for test_core tests.
Ensures project root is on sys.path.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pytest

from core.events import (
    Bar, DataEvent, EntryConditions, OrderSide,
    OrderType, OrderEvent, FillEvent, SignalEvent, TimeFrame,
)
from core.filters import FilterContext

TS = datetime(2025, 9, 15, 9, 35)
TS_STR = "2025-09-15T09:35:00"


@pytest.fixture
def sample_bar() -> Bar:
    return Bar(timestamp=TS, open=400.0, high=405.0, low=398.0, close=403.0, volume=5000, vwap=401.5)


@pytest.fixture
def bearish_bar() -> Bar:
    return Bar(timestamp=TS, open=400.0, high=402.0, low=390.0, close=393.0, volume=8000, vwap=395.0)


@pytest.fixture
def bars_10() -> list[Bar]:
    result: list[Bar] = []
    for i in range(10):
        base = 100.0 + i
        result.append(Bar(
            timestamp=datetime(2025, 9, 15, 9, 30 + i),
            open=base, high=base + 1.5, low=base - 0.5, close=base + 1.0,
            volume=1000 + i * 100,
        ))
    return result


@pytest.fixture
def buy_signal() -> SignalEvent:
    return SignalEvent(symbol="TSLA", side=OrderSide.BUY, entry_price=403.0, stop_loss=398.0, strategy="TEST", timestamp=TS)


@pytest.fixture
def sell_signal() -> SignalEvent:
    return SignalEvent(symbol="TSLA", side=OrderSide.SELL, entry_price=393.0, stop_loss=398.0, strategy="TEST", timestamp=TS)


@pytest.fixture
def signal_with_vwap() -> SignalEvent:
    return SignalEvent(symbol="TSLA", side=OrderSide.BUY, entry_price=403.0, stop_loss=398.0, strategy="TEST", timestamp=TS,
                       entry_conditions=EntryConditions(require_vwap_side=True))


@pytest.fixture
def signal_with_volume_spike() -> SignalEvent:
    return SignalEvent(symbol="TSLA", side=OrderSide.BUY, entry_price=403.0, stop_loss=398.0, strategy="TEST", timestamp=TS,
                       entry_conditions=EntryConditions(volume_spike_mult=1.2, volume_lookback=5))


@pytest.fixture
def signal_with_adx() -> SignalEvent:
    return SignalEvent(symbol="TSLA", side=OrderSide.BUY, entry_price=403.0, stop_loss=398.0, strategy="TEST", timestamp=TS,
                       entry_conditions=EntryConditions(adx_min=20.0))


@pytest.fixture
def default_ctx() -> FilterContext:
    return FilterContext(bars=[], regime="TRENDING", vwap_daily=400.0, spread_pct=0.001, atr_14=2.5, latest_price=403.0)


@pytest.fixture
def np_prices_10() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    high  = np.array([102.0, 103.0, 104.5, 105.5, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0])
    low   = np.array([99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0])
    close = np.array([101.0, 102.0, 103.5, 104.5, 105.5, 106.0, 107.5, 108.5, 109.5, 110.5])
    vol   = np.array([1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900])
    return high, low, close, vol


@pytest.fixture
def np_prices_50() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    n = 50
    base = 100.0
    close = base + np.cumsum(rng.normal(0, 1.0, n))
    high  = close + np.abs(rng.normal(0.5, 0.3, n))
    low   = close - np.abs(rng.normal(0.3, 0.25, n))
    volume = rng.integers(500, 2000, n).astype(float)
    return high, low, close, volume