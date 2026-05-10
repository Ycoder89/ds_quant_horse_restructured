"""test_strategy.py — Strategy base class contract tests"""
from __future__ import annotations

from datetime import datetime, time

import pytest

from core.events import Bar, DataEvent, EntryConditions, OrderSide, SignalEvent
from core.strategy import Strategy, TimeConstraints


TS = datetime(2025, 9, 15, 9, 35)


# ── Minimal concrete Strategy ──────────────────────────────

class _MinimalStrategy(Strategy):
    """Concrete strategy that returns None (no signal)."""
    def on_bar(self, event: DataEvent) -> SignalEvent | None:
        return None


class _SpyStrategy(Strategy):
    """Records the last emitted signal."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_emitted: SignalEvent | None = None

    def on_bar(self, event: DataEvent) -> SignalEvent | None:
        return None

    def test_emit(self, signal: SignalEvent) -> None:
        self.emit_signal(signal)
        self.last_emitted = signal


# ── Strategy ───────────────────────────────────────────────

class TestStrategy:
    def test_name_default(self):
        s = _MinimalStrategy(["TSLA"])
        assert s.name == "Strategy"

    def test_name_custom(self):
        s = _MinimalStrategy(["TSLA"], name="ORB")
        assert s.name == "ORB"

    def test_symbols_returns_copy(self):
        s = _MinimalStrategy(["TSLA", "AAPL"])
        syms = s.symbols
        syms.append("MSFT")
        assert s.symbols == ["TSLA", "AAPL"]

    def test_emit_signal_raises_without_callback(self):
        s = _MinimalStrategy(["TSLA"])
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", timestamp=TS)
        with pytest.raises(RuntimeError, match="event_callback"):
            s.emit_signal(signal)

    def test_emit_signal_succeeds_with_callback(self):
        signals: list[SignalEvent] = []
        s = _MinimalStrategy(["TSLA"])
        s.set_event_callback(signals.append)
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", timestamp=TS)
        s.emit_signal(signal)
        assert len(signals) == 1
        assert signals[0] is signal

    def test_set_event_callback_overwrites(self):
        calls1: list[SignalEvent] = []
        calls2: list[SignalEvent] = []
        s = _MinimalStrategy(["TSLA"])
        s.set_event_callback(calls1.append)
        s.set_event_callback(calls2.append)
        signal = SignalEvent("TSLA", OrderSide.BUY, 100.0, 99.0, "TEST", timestamp=TS)
        s.emit_signal(signal)
        assert len(calls1) == 0
        assert len(calls2) == 1

    def test_on_start_on_finish_noop(self):
        s = _MinimalStrategy(["TSLA"])
        s.on_start()
        s.on_finish()

    def test_on_session_start_noop(self):
        s = _MinimalStrategy(["TSLA"])
        s.on_session_start("2025-09-15")

    def test_params_accessible(self):
        s = _MinimalStrategy(["TSLA"], params={"lookback": 5, "atr_mult": 1.5})
        assert s._params == {"lookback": 5, "atr_mult": 1.5}

    def test_repr(self):
        s = _MinimalStrategy(["TSLA"], name="ORB")
        r = repr(s)
        assert "_MinimalStrategy" in r
        assert "TSLA" in r


# ── TimeConstraints ────────────────────────────────────────

class TestTimeConstraints:
    def test_defaults(self):
        tc = TimeConstraints()
        assert tc.entry_start == time(9, 35)
        assert tc.entry_end == time(11, 30)
        assert tc.force_flat_before == time(15, 55)

    def test_can_enter_before_start(self):
        tc = TimeConstraints()
        assert not tc.can_enter(time(9, 30))

    def test_can_enter_at_start(self):
        tc = TimeConstraints()
        assert tc.can_enter(time(9, 35))

    def test_can_enter_mid_window(self):
        tc = TimeConstraints()
        assert tc.can_enter(time(10, 0))

    def test_can_enter_at_end(self):
        tc = TimeConstraints()
        assert tc.can_enter(time(11, 30))

    def test_can_enter_after_end(self):
        tc = TimeConstraints()
        assert not tc.can_enter(time(11, 31))

    def test_must_exit_before_deadline(self):
        tc = TimeConstraints()
        assert not tc.must_exit(time(15, 50))

    def test_must_exit_at_deadline(self):
        tc = TimeConstraints()
        assert tc.must_exit(time(15, 55))

    def test_must_exit_after_deadline(self):
        tc = TimeConstraints()
        assert tc.must_exit(time(15, 59))

    def test_is_trading_during_market(self):
        tc = TimeConstraints()
        assert tc.is_trading(time(10, 0))

    def test_is_trading_before_open(self):
        tc = TimeConstraints()
        assert not tc.is_trading(time(9, 29))

    def test_is_trading_after_close(self):
        tc = TimeConstraints()
        assert not tc.is_trading(time(16, 1))

    def test_custom_windows(self):
        tc = TimeConstraints(
            entry_start=time(10, 0),
            entry_end=time(12, 0),
            force_flat_before=time(15, 0),
        )
        assert tc.can_enter(time(10, 0))
        assert not tc.can_enter(time(9, 59))
        assert tc.must_exit(time(15, 0))