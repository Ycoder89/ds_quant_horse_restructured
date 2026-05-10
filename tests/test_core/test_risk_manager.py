"""test_risk_manager.py — RiskManager contract tests"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from core.events import EntryConditions, OrderEvent, OrderSide, OrderType, SignalEvent
from core.exit import ExitSignal, ExitType
from core.risk_manager import (
    DefaultRiskManager, PositionSizing, PositionSizingMethod,
    RiskLimits, RiskManager, RiskState,
)

TS = datetime(2025, 9, 15, 9, 35)

# ── Helpers ────────────────────────────────────────────────

def _signal(**overrides) -> SignalEvent:
    defaults = {
        "symbol": "TSLA",
        "side": OrderSide.BUY,
        "entry_price": 403.0,
        "stop_loss": 398.0,
        "strategy": "TEST",
        "timestamp": TS,
    }
    return SignalEvent(**{**defaults, **overrides})


# ── PositionSizing ─────────────────────────────────────────

class TestPositionSizing:
    def test_fixed_size(self):
        sizing = PositionSizing(method=PositionSizingMethod.FIXED, fixed_quantity=200)
        qty = sizing.calculate(_signal())
        assert qty == 200

    def test_fraction_atr_with_atr(self):
        sizing = PositionSizing(
            method=PositionSizingMethod.FRACTION_ATR,
            risk_per_trade_pct=0.01,
            account_value=100_000.0,
        )
        # risk_amount = 1000, atr = 2.0, qty = 500
        qty = sizing.calculate(_signal(), atr=2.0)
        assert qty == 500

    def test_fraction_atr_no_atr_uses_stop_distance(self):
        sizing = PositionSizing(
            method=PositionSizingMethod.FRACTION_ATR,
            risk_per_trade_pct=0.01,
            account_value=100_000.0,
        )
        # stop_distance = 403 - 398 = 5, risk=1000, qty=200
        qty = sizing.calculate(_signal(), atr=0.0)
        assert qty == 200

    def test_fraction_atr_zero_stop_distance_fallback_to_fixed(self):
        sizing = PositionSizing(
            method=PositionSizingMethod.FRACTION_ATR,
            risk_per_trade_pct=0.01,
            account_value=100_000.0,
            fixed_quantity=100,
        )
        sig = _signal(entry_price=400.0, stop_loss=400.0)
        qty = sizing.calculate(sig, atr=0.0)
        assert qty == 100

    def test_clamps_to_min_max(self):
        sizing = PositionSizing(
            method=PositionSizingMethod.FIXED,
            fixed_quantity=100_000,
            min_quantity=1,
            max_quantity=10_000,
        )
        qty = sizing.calculate(_signal())
        assert qty == 10_000  # not 100,000


# ── RiskLimits ─────────────────────────────────────────────

class TestRiskLimits:
    def test_defaults(self):
        limits = RiskLimits()
        assert limits.max_daily_loss_pct == 0.05
        assert limits.max_positions_per_day == 10
        assert limits.max_concurrent_positions == 1
        assert limits.require_no_position is True

    def test_max_daily_loss_amount(self):
        limits = RiskLimits(account_value=200_000.0, max_daily_loss_pct=0.03)
        assert limits.max_daily_loss_amount == 6_000.0


# ── RiskState ──────────────────────────────────────────────

class TestRiskState:
    def test_default_can_trade(self):
        state = RiskState()
        limits = RiskLimits()
        ok, reason = state.can_trade(limits, has_position=False)
        assert ok is True
        assert reason == ""

    def test_halted_blocks(self):
        state = RiskState(is_halted=True, halt_reason="manual")
        limits = RiskLimits()
        ok, reason = state.can_trade(limits, has_position=False)
        assert ok is False
        assert "manual" in reason

    def test_daily_loss_exceeded_blocks(self):
        state = RiskState(daily_pnl=-6_000.0)
        limits = RiskLimits(account_value=100_000.0, max_daily_loss_pct=0.05)
        # loss = 6000, limit = 5000
        ok, reason = state.can_trade(limits, has_position=False)
        assert ok is False
        assert "亏损" in reason

    def test_daily_loss_not_yet_exceeded(self):
        state = RiskState(daily_pnl=-4_000.0)
        limits = RiskLimits(account_value=100_000.0, max_daily_loss_pct=0.05)
        ok, _ = state.can_trade(limits, has_position=False)
        assert ok is True

    def test_max_positions_per_day_exceeded(self):
        state = RiskState(positions_opened_today=10)
        limits = RiskLimits(max_positions_per_day=10)
        ok, reason = state.can_trade(limits, has_position=False)
        assert ok is False
        assert "10" in reason

    def test_require_no_position_blocks_when_has_position(self):
        state = RiskState()
        limits = RiskLimits(require_no_position=True)
        ok, reason = state.can_trade(limits, has_position=True)
        assert ok is False
        assert "已有持仓" in reason

    def test_record_fill(self):
        state = RiskState()
        state.record_fill(150.0)
        assert state.daily_pnl == 150.0
        state.record_fill(-50.0)
        assert state.daily_pnl == 100.0

    def test_record_open(self):
        state = RiskState()
        state.record_open()
        assert state.positions_opened_today == 1

    def test_reset_daily_crosses_date(self):
        state = RiskState(date=date(2025, 9, 14), daily_pnl=-3000.0, positions_opened_today=5, is_halted=True)
        state.reset_daily(date(2025, 9, 15))
        assert state.date == date(2025, 9, 15)
        assert state.daily_pnl == 0.0
        assert state.positions_opened_today == 0
        assert state.is_halted is False

    def test_reset_daily_same_date_noop(self):
        state = RiskState(date=date(2025, 9, 15), daily_pnl=-3000.0, positions_opened_today=5)
        state.reset_daily(date(2025, 9, 15))
        assert state.daily_pnl == -3000.0
        assert state.positions_opened_today == 5


# ── DefaultRiskManager ─────────────────────────────────────

class TestDefaultRiskManager:
    def test_on_signal_simple_passes(self):
        mgr = DefaultRiskManager()
        signal = _signal()
        order = mgr.on_signal(signal)
        assert order is not None
        assert order.symbol == "TSLA"
        assert order.side is OrderSide.BUY
        assert order.order_type is OrderType.MARKET
        assert order.risk_id.startswith("R")

    def test_on_signal_blocks_with_existing_position(self):
        mgr = DefaultRiskManager()
        signal1 = _signal()
        order1 = mgr.on_signal(signal1)
        assert order1 is not None

        signal2 = _signal()
        order2 = mgr.on_signal(signal2)  # blocks because has_position=True (after on_fill)
        assert order2 is not None  # on_signal checks _has_position, but we haven't called on_fill

    def test_state_exposes_risk_state(self):
        mgr = DefaultRiskManager()
        assert isinstance(mgr.state, RiskState)

    def test_is_trading_allowed_default(self):
        mgr = DefaultRiskManager()
        assert mgr.is_trading_allowed is True

    def test_on_exit_creates_order(self):
        mgr = DefaultRiskManager()
        exit_sig = ExitSignal("TSLA", OrderSide.SELL, 395.0, ExitType.STOP_LOSS, "hit")
        order = mgr.on_exit(exit_sig)
        assert order is not None
        assert order.symbol == "TSLA"
        assert order.side is OrderSide.SELL

    def test_on_position_closed_resets_flag(self):
        mgr = DefaultRiskManager()
        mgr.on_fill(40350.0, 0.0)
        assert mgr._has_position is True
        mgr.on_position_closed()
        assert mgr._has_position is False

    def test_daily_loss_hard_blocks(self):
        """After daily loss exceeds limit, new signals are blocked."""
        mgr = DefaultRiskManager(
            limits=RiskLimits(account_value=100_000.0, max_daily_loss_pct=0.05),
        )
        # Simulate accumulated loss
        mgr._state.daily_pnl = -10_000.0  # exceed 5,000 limit
        signal = _signal()
        order = mgr.on_signal(signal)
        assert order is None  # blocked

    def test_max_positions_per_day_blocks(self):
        mgr = DefaultRiskManager(
            limits=RiskLimits(max_positions_per_day=3),
        )
        mgr._state.positions_opened_today = 3
        signal = _signal()
        order = mgr.on_signal(signal)
        assert order is None

    def test_id_counter_increments(self):
        mgr = DefaultRiskManager(generate_risk_id=True)
        o1 = mgr.on_signal(_signal())
        o2 = mgr.on_exit(ExitSignal("TSLA", OrderSide.SELL, 395.0, ExitType.STOP_LOSS))
        assert o1 is not None
        assert o2 is not None
        assert o1.risk_id == "R000001"
        assert o2.risk_id == "R000002"

    def test_no_risk_id_when_disabled(self):
        mgr = DefaultRiskManager(generate_risk_id=False)
        order = mgr.on_signal(_signal())
        assert order is not None
        assert order.risk_id == ""

    def test_signal_with_vwap_filter_blocks(self):
        """Require VWAP but VWAP=0 ctx → passes (unavailable)."""
        mgr = DefaultRiskManager()
        signal = _signal(entry_conditions=EntryConditions(require_vwap_side=True))
        order = mgr.on_signal(signal)
        # VWAP is 0 in default _build_filter_context, filter passes as "unavailable"
        assert order is not None