"""test_filters.py — EntryFilter chain contract tests"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.events import Bar, EntryConditions, OrderSide, SignalEvent
from core.filters import (
    ADXFilter, FilterChain, FilterContext, RegimeFilter, SpreadFilter,
    VWAPSideFilter, VolumeSpikeFilter, default_filter_registry,
)

TS = datetime(2025, 9, 15, 9, 35)


def _make_bars(count: int, volumes: list[int]) -> list[Bar]:
    bars = []
    for i in range(count):
        bars.append(Bar(datetime(2025, 9, 15, 9, 30 + i), 100.0, 102.0, 99.0, 101.0,
                        volume=volumes[i] if i < len(volumes) else 1000))
    return bars


class TestVWAPSideFilter:
    def test_buy_above_vwap_passes(self):
        f = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=400.0, latest_price=405.0)
        signal = SignalEvent("TSLA", OrderSide.BUY, 405.0, 398.0, "TEST", TS,
                             entry_conditions=EntryConditions(require_vwap_side=True))
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_buy_below_vwap_blocks(self):
        f = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=410.0, latest_price=405.0)
        signal = SignalEvent("TSLA", OrderSide.BUY, 405.0, 398.0, "TEST", TS,
                             entry_conditions=EntryConditions(require_vwap_side=True))
        ok, reason = f.filter(signal, ctx)
        assert ok is False
        assert "VWAP" in reason

    def test_sell_below_vwap_passes(self):
        f = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=410.0, latest_price=405.0)
        signal = SignalEvent("TSLA", OrderSide.SELL, 405.0, 410.0, "TEST", TS,
                             entry_conditions=EntryConditions(require_vwap_side=True))
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_sell_above_vwap_blocks(self):
        f = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=400.0, latest_price=405.0)
        signal = SignalEvent("TSLA", OrderSide.SELL, 405.0, 410.0, "TEST", TS,
                             entry_conditions=EntryConditions(require_vwap_side=True))
        ok, _ = f.filter(signal, ctx)
        assert ok is False

    def test_vwap_unavailable_passes(self):
        f = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=0.0, latest_price=405.0)
        signal = SignalEvent("TSLA", OrderSide.BUY, 405.0, 398.0, "TEST", TS)
        ok, reason = f.filter(signal, ctx)
        assert ok is True
        assert "unavailable" in reason.lower()

    def test_price_zero_blocks(self):
        f = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=400.0, latest_price=0.0)
        signal = SignalEvent("TSLA", OrderSide.BUY, 0.0, 398.0, "TEST", TS)
        ok, _ = f.filter(signal, ctx)
        assert ok is False


class TestVolumeSpikeFilter:
    def test_spike_passes(self):
        f = VolumeSpikeFilter()
        bars = _make_bars(6, [3000, 1000, 1000, 1000, 1000, 1000])
        ctx = FilterContext(bars=bars)
        signal = SignalEvent("TSLA", OrderSide.BUY, 101.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(volume_spike_mult=2.0, volume_lookback=5))
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_no_spike_blocks(self):
        f = VolumeSpikeFilter()
        bars = _make_bars(6, [1200, 1000, 1000, 1000, 1000, 1000])
        ctx = FilterContext(bars=bars)
        signal = SignalEvent("TSLA", OrderSide.BUY, 101.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(volume_spike_mult=2.0, volume_lookback=5))
        ok, _ = f.filter(signal, ctx)
        assert ok is False

    def test_mult_not_set_passes(self):
        f = VolumeSpikeFilter()
        ctx = FilterContext(bars=[])
        signal = SignalEvent("TSLA", OrderSide.BUY, 101.0, 99.0, "TEST", TS)
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_insufficient_bars_passes(self):
        f = VolumeSpikeFilter()
        bars = _make_bars(3, [2000, 1000, 1000])
        ctx = FilterContext(bars=bars)
        signal = SignalEvent("TSLA", OrderSide.BUY, 101.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(volume_spike_mult=1.5, volume_lookback=5))
        ok, reason = f.filter(signal, ctx)
        assert ok is True
        assert "insufficient" in reason.lower()


class TestADXFilter:
    def test_no_condition_passes(self):
        f = ADXFilter()
        ctx = FilterContext()
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS)
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_adx_unavailable_passes(self):
        f = ADXFilter()
        ctx = FilterContext()
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(adx_min=20.0))
        ok, reason = f.filter(signal, ctx)
        assert ok is True
        assert "unavailable" in reason.lower()

    def test_adx_above_min_passes(self):
        f = ADXFilter()
        ctx = FilterContext()
        ctx.adx_14 = 30.0
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(adx_min=20.0))
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_adx_below_min_blocks(self):
        f = ADXFilter()
        ctx = FilterContext()
        ctx.adx_14 = 15.0
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(adx_min=20.0))
        ok, reason = f.filter(signal, ctx)
        assert ok is False
        assert "15.0" in reason


class TestSpreadFilter:
    def test_no_condition_passes(self):
        f = SpreadFilter()
        ctx = FilterContext(spread_pct=0.01)
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS)
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_spread_below_max_passes(self):
        f = SpreadFilter()
        ctx = FilterContext(spread_pct=0.001)
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(max_spread_pct=0.002))
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_spread_above_max_blocks(self):
        f = SpreadFilter()
        ctx = FilterContext(spread_pct=0.005)
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(max_spread_pct=0.002))
        ok, _ = f.filter(signal, ctx)
        assert ok is False


class TestRegimeFilter:
    def test_no_condition_passes(self):
        f = RegimeFilter()
        ctx = FilterContext(regime="HIGH_VOL")
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS)
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_allowed_regime_passes(self):
        f = RegimeFilter()
        ctx = FilterContext(regime="TRENDING")
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(allowed_regimes={"TRENDING", "MEAN_REVERTING"}))
        ok, _ = f.filter(signal, ctx)
        assert ok is True

    def test_not_in_allowed_blocks(self):
        f = RegimeFilter()
        ctx = FilterContext(regime="HIGH_VOL")
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(allowed_regimes={"TRENDING"}))
        ok, _ = f.filter(signal, ctx)
        assert ok is False

    def test_blocked_regime_blocks(self):
        f = RegimeFilter()
        ctx = FilterContext(regime="HIGH_VOL")
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS,
                             entry_conditions=EntryConditions(blocked_regimes={"HIGH_VOL"}))
        ok, _ = f.filter(signal, ctx)
        assert ok is False


class TestFilterChain:
    def test_empty_chain_passes(self):
        chain = FilterChain([])
        ctx = FilterContext()
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", TS)
        ok, _ = chain.check(signal, ctx)
        assert ok is True

    def test_from_conditions_returns_matching_filters(self):
        registry = default_filter_registry()
        cond = EntryConditions(require_vwap_side=True, adx_min=20.0)
        chain = FilterChain.from_conditions(cond, registry)
        assert len(chain._filters) == 2

    def test_from_conditions_empty(self):
        registry = default_filter_registry()
        chain = FilterChain.from_conditions(EntryConditions(), registry)
        assert chain.is_empty() is True

    def test_chain_short_circuits(self):
        f1 = VWAPSideFilter()
        ctx = FilterContext(vwap_daily=410.0, latest_price=405.0)
        signal = SignalEvent("TSLA", OrderSide.BUY, 405.0, 398.0, "TEST", TS,
                             entry_conditions=EntryConditions(require_vwap_side=True))
        chain = FilterChain([f1])
        ok, reason = chain.check(signal, ctx)
        assert ok is False
        assert "VWAPSide" in reason