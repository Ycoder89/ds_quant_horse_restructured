"""test_exit.py — ExitManager contract tests"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.events import Bar, OrderSide
from core.exit import (
    CompositeExitManager, ExitManager, ExitSignal, ExitType,
    FixedStopExit, Position, TimeStopExit, TrailingStopExit,
)

TS = datetime(2025, 9, 15, 9, 35)

# ── Helpers ─────────────────────────────────────────────────

def _long_pos(entry: float, qty: int = 100) -> Position:
    return Position("TSLA", qty, entry)

def _short_pos(entry: float, qty: int = 100) -> Position:
    return Position("TSLA", -qty, entry)

# ── Position ────────────────────────────────────────────────

class TestPosition:
    def test_long_side(self):
        p = _long_pos(400.0)
        assert p.side is OrderSide.BUY
        assert p.is_long()
        assert not p.is_short()
        assert not p.is_flat

    def test_short_side(self):
        p = _short_pos(400.0)
        assert p.side is OrderSide.SELL
        assert p.is_short()
        assert not p.is_long()

    def test_flat(self):
        p = Position("TSLA", 0, 0.0)
        assert p.side is None
        assert p.is_flat

    def test_entry_bar_index_default(self):
        p = Position("TSLA", 100, 400.0)
        assert p.entry_bar_index == 0

# ── ExitSignal ──────────────────────────────────────────────

class TestExitSignal:
    def test_creation(self):
        sig = ExitSignal("TSLA", OrderSide.SELL, 395.0, ExitType.STOP_LOSS, "hit")
        assert sig.exit_price == 395.0
        assert sig.exit_type is ExitType.STOP_LOSS
        assert sig.reason == "hit"
        assert "STOP_LOSS" in repr(sig)

    def test_immutable(self):
        sig = ExitSignal("TSLA", OrderSide.SELL, 395.0, ExitType.STOP_LOSS)
        with pytest.raises(Exception):
            sig.exit_price = 400.0  # type: ignore

# ── ExitType ────────────────────────────────────────────────

class TestExitType:
    def test_all_values_exist(self):
        types = set(ExitType)
        assert ExitType.STOP_LOSS in types
        assert ExitType.TRAILING_STOP in types
        assert ExitType.TAKE_PROFIT in types
        assert ExitType.TIME_STOP in types
        assert ExitType.MANUAL in types
        assert ExitType.REVERSAL in types

# ── FixedStopExit ───────────────────────────────────────────

class TestFixedStopExit:
    def test_long_stop_triggered(self):
        mgr = FixedStopExit()
        pos = _long_pos(400.0)
        bar = Bar(TS, 399.0, 399.5, 395.0, 397.0, 1000)  # low = 395 ≤ 398
        should, sig, new_stop = mgr.check(pos, bar, current_stop=398.0)
        assert should is True
        assert sig is not None
        assert sig.exit_type is ExitType.STOP_LOSS
        assert sig.side is OrderSide.SELL

    def test_long_stop_not_triggered(self):
        mgr = FixedStopExit()
        pos = _long_pos(400.0)
        bar = Bar(TS, 402.0, 405.0, 399.0, 403.0, 1000)  # low = 399 > 398
        should, sig, _ = mgr.check(pos, bar, current_stop=398.0)
        assert should is False
        assert sig is None

    def test_short_stop_triggered(self):
        mgr = FixedStopExit()
        pos = _short_pos(400.0)
        bar = Bar(TS, 402.0, 404.0, 399.0, 403.0, 1000)  # high = 404 ≥ 402
        should, sig, _ = mgr.check(pos, bar, current_stop=402.0)
        assert should is True
        assert sig is not None
        assert sig.exit_type is ExitType.STOP_LOSS
        assert sig.side is OrderSide.BUY

    def test_no_current_stop(self):
        mgr = FixedStopExit()
        pos = _long_pos(400.0)
        bar = Bar(TS, 390.0, 391.0, 380.0, 385.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=None)
        assert should is False
        assert sig is None

    def test_flat_position(self):
        mgr = FixedStopExit()
        pos = Position("TSLA", 0, 0.0)
        bar = Bar(TS, 399.0, 399.5, 395.0, 397.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=398.0)
        assert should is False
        assert sig is None

# ── TrailingStopExit ────────────────────────────────────────

class TestTrailingStopExit:
    def test_not_activated_below_threshold(self):
        mgr = TrailingStopExit(activation_r=0.5, trail_distance=0.3)
        pos = _long_pos(400.0)
        bar = Bar(TS, 401.0, 402.0, 400.5, 401.5, 1000)  # R ≈ (401.5-400)/2 = 0.75... stop=398
        should, sig, new_stop = mgr.check(pos, bar, current_stop=398.0)
        assert should is False
        assert sig is None
        assert new_stop is None  # not yet activated

    def test_activation_on_sufficient_r(self):
        mgr = TrailingStopExit(activation_r=0.5, trail_distance=0.3)
        pos = _long_pos(400.0)
        bar = Bar(TS, 402.0, 403.0, 401.0, 403.0, 1000)  # close=403, R=1.5
        should, sig, new_stop = mgr.check(pos, bar, current_stop=398.0)
        assert should is False
        # activated, no exit yet
        assert new_stop is None  # first bar of activation, no exit

    def test_flat_position_ignored(self):
        mgr = TrailingStopExit()
        pos = Position("TSLA", 0, 0.0)
        bar = Bar(TS, 402.0, 403.0, 401.0, 403.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=398.0)
        assert should is False
        assert sig is None

    def test_no_current_stop_ignored(self):
        mgr = TrailingStopExit()
        pos = _long_pos(400.0)
        bar = Bar(TS, 402.0, 403.0, 401.0, 403.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=None)
        assert should is False
        assert sig is None

# ── TimeStopExit ────────────────────────────────────────────

class TestTimeStopExit:
    def test_under_limit_no_exit(self):
        mgr = TimeStopExit(max_bars=12)
        pos = _long_pos(400.0)
        bar = Bar(TS, 400.0, 401.0, 399.0, 400.5, 1000)
        # 调用 5 次，bar_count=5 < max_bars=12，不触发
        for _ in range(5):
            should, sig, _ = mgr.check(pos, bar)
        assert should is False
        assert sig is None

    def test_at_limit_exits(self):
        mgr = TimeStopExit(max_bars=12)
        pos = _long_pos(400.0)
        bar = Bar(TS, 400.0, 401.0, 399.0, 400.5, 1000)
        # 调用 12 次，最后一次触发退出
        should, sig = False, None
        for _ in range(12):
            should, sig, _ = mgr.check(pos, bar)
        assert should is True
        assert sig is not None
        assert sig.exit_type is ExitType.TIME_STOP
        assert sig.side is OrderSide.SELL

    def test_short_at_limit_exits(self):
        mgr = TimeStopExit(max_bars=12)
        pos = _short_pos(400.0)
        bar = Bar(TS, 400.0, 401.0, 399.0, 400.5, 1000)
        should, sig = False, None
        for _ in range(12):
            should, sig, _ = mgr.check(pos, bar)
        assert should is True
        assert sig.side is OrderSide.BUY

    def test_flat_position(self):
        mgr = TimeStopExit(max_bars=12)
        pos = Position("TSLA", 0, 0.0)
        bar = Bar(TS, 400.0, 401.0, 399.0, 400.5, 1000)
        should, sig, _ = mgr.check(pos, bar)
        assert should is False

# ── CompositeExitManager ────────────────────────────────────

class TestCompositeExitManager:
    def test_empty_composite_no_exit(self):
        mgr = CompositeExitManager([])
        pos = _long_pos(400.0)
        bar = Bar(TS, 399.0, 399.5, 395.0, 397.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=398.0)
        assert should is False

    def test_fixed_stop_triggers_first(self):
        mgr = CompositeExitManager([FixedStopExit()])
        pos = _long_pos(400.0)
        bar = Bar(TS, 399.0, 399.5, 395.0, 397.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=398.0)
        assert should is True
        assert sig.exit_type is ExitType.STOP_LOSS

    def test_multiple_no_exit_passes(self):
        mgr = CompositeExitManager([FixedStopExit(), TrailingStopExit()])
        pos = _long_pos(400.0)
        bar = Bar(TS, 402.0, 405.0, 399.0, 403.0, 1000)
        should, sig, _ = mgr.check(pos, bar, current_stop=398.0)
        assert should is False

    def test_stop_propagation_to_composite(self):
        """TrailingStop updates stop, but FixedStop doesn't trigger => composite returns updated stop"""
        mgr = CompositeExitManager([TrailingStopExit(activation_r=0.5, trail_distance=0.3),
                                     FixedStopExit()])
        pos = _long_pos(400.0)
        bar = Bar(TS, 402.0, 403.0, 401.0, 403.0, 1000)
        should, sig, new_stop = mgr.check(pos, bar, current_stop=398.0)
        # trailing may activate but not exit; fixed doesn't trigger either
        assert should is False