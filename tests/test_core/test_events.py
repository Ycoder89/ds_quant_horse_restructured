"""test_events.py — Event data class contract tests"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.events import (
    Bar, DataEvent, EntryConditions, FillEvent, OrderEvent,
    OrderSide, OrderType, SignalEvent, TimeFrame,
)

# ── Bar ─────────────────────────────────────────────────────
class TestBar:
    def test_creation(self):
        bar = Bar(datetime(2025, 1, 6, 9, 30), 100.0, 105.0, 99.0, 103.0, 1000)
        assert bar.open == 100.0
        assert bar.high == 105.0
        assert bar.low == 99.0
        assert bar.close == 103.0
        assert bar.volume == 1000
        assert bar.vwap == 0.0

    def test_avg_price(self, sample_bar):
        expected = (400.0 + 405.0 + 398.0 + 403.0) / 4.0
        assert sample_bar.avg_price == pytest.approx(expected)

    def test_typical_price(self, sample_bar):
        expected = (405.0 + 398.0 + 403.0) / 3.0
        assert sample_bar.typical_price == pytest.approx(expected)

    def test_range_property(self, sample_bar):
        assert sample_bar.range == pytest.approx(7.0)

    def test_is_bullish_true(self, sample_bar):
        assert sample_bar.is_bullish is True

    def test_is_bullish_false(self, bearish_bar):
        assert bearish_bar.is_bullish is False

    def test_body_pct_zero_range(self):
        bar = Bar(datetime(2025, 1, 6, 9, 30), 100.0, 100.0, 100.0, 100.0, 0)
        assert bar.body_pct == 0.0

    def test_immutable(self, sample_bar):
        with pytest.raises(Exception):
            sample_bar.close = 500.0  # type: ignore

# ── OrderSide ───────────────────────────────────────────────
class TestOrderSide:
    def test_opposite_buy(self):
        assert OrderSide.BUY.opposite() is OrderSide.SELL
    def test_opposite_sell(self):
        assert OrderSide.SELL.opposite() is OrderSide.BUY

# ── OrderType ───────────────────────────────────────────────
class TestOrderType:
    def test_from_str_market(self):
        assert OrderType.from_str("MARKET") is OrderType.MARKET
    def test_from_str_limit(self):
        assert OrderType.from_str("LIMIT") is OrderType.LIMIT
    def test_from_str_case_insensitive(self):
        assert OrderType.from_str("market") is OrderType.MARKET
    def test_from_str_invalid_raises(self):
        with pytest.raises(KeyError):
            OrderType.from_str("GTC")

# ── TimeFrame ───────────────────────────────────────────────
class TestTimeFrame:
    def test_from_str_1min(self):
        assert TimeFrame.from_str("1min") is TimeFrame.M1
    def test_from_str_5min(self):
        assert TimeFrame.from_str("5min") is TimeFrame.M5
    def test_minutes_m1(self):
        assert TimeFrame.M1.minutes == 1
    def test_minutes_m5(self):
        assert TimeFrame.M5.minutes == 5

# ── EntryConditions ─────────────────────────────────────────
class TestEntryConditions:
    def test_default_is_empty(self):
        assert EntryConditions().is_empty() is True
    def test_with_vwap_is_not_empty(self):
        assert EntryConditions(require_vwap_side=True).is_empty() is False
    def test_with_volume_is_not_empty(self):
        assert EntryConditions(volume_spike_mult=1.5).is_empty() is False
    def test_with_adx_is_not_empty(self):
        assert EntryConditions(adx_min=20.0).is_empty() is False
    def test_field_defaults(self):
        cond = EntryConditions()
        assert cond.require_vwap_side is False
        assert cond.volume_spike_mult is None
        assert cond.adx_min is None

# ── DataEvent ───────────────────────────────────────────────
class TestDataEvent:
    @pytest.fixture
    def data_event(self, sample_bar):
        return DataEvent(timestamp=datetime(2025, 9, 15, 9, 35), bars={"TSLA": sample_bar})
    def test_get_bar_exists(self, data_event, sample_bar):
        assert data_event.get_bar("TSLA") is sample_bar
    def test_get_bar_missing(self, data_event):
        assert data_event.get_bar("AAPL") is None
    def test_symbols_property(self, data_event):
        assert data_event.symbols == ["TSLA"]

# ── SignalEvent ─────────────────────────────────────────────
class TestSignalEvent:
    def test_creation(self, buy_signal):
        assert buy_signal.symbol == "TSLA"
        assert buy_signal.side is OrderSide.BUY
        assert buy_signal.entry_price == 403.0
        assert buy_signal.stop_loss == 398.0
    def test_default_confidence(self, buy_signal):
        assert buy_signal.confidence == 1.0
    def test_immutable(self, buy_signal):
        with pytest.raises(Exception):
            buy_signal.entry_price = 500.0  # type: ignore

# ── OrderEvent ──────────────────────────────────────────────
class TestOrderEvent:
    def test_creation(self):
        order = OrderEvent(symbol="TSLA", side=OrderSide.BUY, quantity=100,
                           order_type=OrderType.MARKET, strategy="ORB", risk_id="R000001")
        assert order.quantity == 100
        assert order.risk_id == "R000001"
    def test_default_values(self):
        order = OrderEvent(symbol="TSLA", side=OrderSide.SELL, quantity=50)
        assert order.order_type is OrderType.MARKET
        assert order.limit_price == 0.0

# ── FillEvent ───────────────────────────────────────────────
class TestFillEvent:
    @pytest.fixture
    def fill(self):
        return FillEvent(timestamp=datetime(2025, 9, 15, 9, 36), symbol="TSLA",
                         side=OrderSide.BUY, quantity=100, fill_price=403.5,
                         commission=1.0, strategy="ORB", risk_id="R000001")
    def test_fill_value(self, fill):
        assert fill.fill_value == pytest.approx(40350.0)
    def test_net_value(self, fill):
        assert fill.net_value == pytest.approx(40349.0)