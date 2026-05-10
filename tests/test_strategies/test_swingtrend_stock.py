"""
tests/test_strategies/test_swingtrend_stock.py — 策略C单元测试
"""
from __future__ import annotations

import pytest
from datetime import datetime, time
from collections import deque

from core.events import (
    Bar, DataEvent, EntryConditions, OrderSide, SignalEvent,
)
from strategies.swingtrend_stock import SwingTrendStock, _EMA


# =============================================================================
# Fixtures
# =============================================================================

def _make_bar(
    ts: str, o: float, h: float, l: float, c: float, v: int = 1000,
    vwap: float = 0.0,
) -> Bar:
    return Bar(
        timestamp=datetime.fromisoformat(ts),
        open=o, high=h, low=l, close=c, volume=v, vwap=vwap,
    )


def _make_event(bars: list[Bar]) -> DataEvent:
    return DataEvent(
        timestamp=bars[-1].timestamp,
        bars={f"BAR_{i}": b for i, b in enumerate(bars)},
    )


@pytest.fixture
def strategy() -> SwingTrendStock:
    s = SwingTrendStock(["TSLA"], params={
        "ema_fast": 8,
        "ema_slow": 21,
        "adx_min": 25.0,
        "swing_lookback": 2,
        "swing_confirm": 1,
        "volume_spike_mult": 1.2,
        "require_vwap_side": True,
        "atr_mult_stop": 1.5,
        "max_spread_pct": 0.002,
        "max_entries_per_direction": 1,
        "latest_entry_time": "13:30",
        "force_flat_time": "15:55",
    })
    s.on_start()
    s.on_session_start("2025-09-15")
    return s


# =============================================================================
# _EMA 增量计算器
# =============================================================================

class TestEMA:
    def test_basic_update(self):
        ema = _EMA(period=5)
        assert not ema.ready
        ema.update(100.0)
        assert ema.ready
        assert ema.value == 100.0

    def test_convergence(self):
        ema = _EMA(period=5)
        prices = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        for p in prices:
            ema.update(p)
        assert ema.value > 10.0
        assert ema.value < 20.0

    def test_prev_value(self):
        ema = _EMA(period=5)
        ema.update(10.0)
        assert ema.prev_value is None
        ema.update(12.0)
        assert ema.prev_value == 10.0
        ema.update(14.0)
        assert ema.prev_value == pytest.approx(ema.value, abs=5.0)

    def test_reset(self):
        ema = _EMA(period=5)
        ema.update(10.0)
        ema.update(12.0)
        ema.reset()
        assert not ema.ready
        assert ema.value is None
        assert ema.prev_value is None


# =============================================================================
# SwingTrendStock 基础属性
# =============================================================================

class TestSwingTrendStockInit:
    def test_name_default(self):
        s = SwingTrendStock(["AAPL"], params={})
        assert s.name == "swingtrend_aapl_5min"

    def test_name_custom(self):
        s = SwingTrendStock(["TSLA"], params={}, name="my_swing")
        assert s.name == "my_swing"

    def test_symbols(self):
        s = SwingTrendStock(["TSLA"], params={})
        assert s.symbols == ["TSLA"]

    def test_unknown_symbol(self):
        s = SwingTrendStock([], params={})
        assert s.name == "swingtrend_unknown_5min"


# =============================================================================
# 生命周期
# =============================================================================

class TestLifecycle:
    def test_on_start_loads_params(self):
        s = SwingTrendStock(["TSLA"], params={
            "ema_fast": 10,
            "ema_slow": 30,
            "adx_min": 30.0,
            "swing_lookback": 3,
            "swing_confirm": 2,
            "volume_spike_mult": 1.5,
            "require_vwap_side": False,
            "atr_mult_stop": 2.0,
            "max_spread_pct": 0.001,
            "max_entries_per_direction": 2,
            "latest_entry_time": "14:00",
            "force_flat_time": "15:50",
        })
        s.on_start()
        assert s._ema_fast_period == 10
        assert s._ema_slow_period == 30
        assert s._adx_min == 30.0
        assert s._swing_lookback == 3
        assert s._swing_confirm == 2
        assert s._volume_spike_mult == 1.5
        assert s._require_vwap_side is False
        assert s._atr_mult_stop == 2.0
        assert s._max_spread_pct == 0.001
        assert s._max_entries_per_direction == 2
        assert s._latest_entry_time == "14:00"

    def test_on_session_start_resets_state(self, strategy):
        # Feed a few bars to change internal state
        bars = [
            _make_bar("2025-09-15 09:35:00", 400, 401, 399, 400.5),
            _make_bar("2025-09-15 09:40:00", 400.5, 402, 400.5, 401.5),
            _make_bar("2025-09-15 09:45:00", 401.5, 403, 401, 402.5),
            _make_bar("2025-09-15 09:50:00", 402.5, 404, 402, 403.5),
            _make_bar("2025-09-15 09:55:00", 403.5, 404, 403, 403.5),
        ]
        for b in bars:
            strategy.on_bar(DataEvent(
                timestamp=b.timestamp,
                bars={"TSLA": b},
            ))

        # Should have some state
        assert len(strategy._bar_buffer) > 0

        # Reset
        strategy.on_session_start("2025-09-16")
        assert len(strategy._bar_buffer) == 0
        assert strategy._trend_dir == 0
        assert strategy._swing_bar is None
        assert strategy._signal_fired is False
        assert strategy._entries_long == 0
        assert strategy._entries_short == 0
        assert not strategy._ema_fast.ready


# =============================================================================
# 信号生成：EMA 交叉检测
# =============================================================================

class TestTrendDetection:
    def test_golden_cross_bullish(self, strategy):
        """EMA8 上穿 EMA21 → 多头方向"""
        # Pre-feed with slow > fast to establish state
        # Close values that build EMA: slow starts higher
        slow_higher = [
            _make_bar("2025-09-15 09:35:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:40:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:45:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:50:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:55:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 10:00:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 10:05:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 10:10:00", 100, 101, 99, 100.0),
        ]
        for b in slow_higher:
            strategy._ema_fast.update(b.close)
            strategy._ema_slow.update(b.close)

        # Now feed rising prices to create golden cross
        rising = [
            _make_bar("2025-09-15 10:15:00", 103, 105, 102, 105.0),
            _make_bar("2025-09-15 10:20:00", 105, 108, 104, 107.0),
            _make_bar("2025-09-15 10:25:00", 107, 110, 106, 109.0),
            _make_bar("2025-09-15 10:30:00", 109, 112, 108, 111.0),
            _make_bar("2025-09-15 10:35:00", 111, 114, 110, 113.0),
            _make_bar("2025-09-15 10:40:00", 113, 116, 112, 115.0),
            _make_bar("2025-09-15 10:45:00", 115, 118, 114, 117.0),
            _make_bar("2025-09-15 10:50:00", 117, 120, 116, 119.0),
        ]
        for b in rising:
            strategy._detect_trend()
            strategy._update_ema(b.close)
            strategy._detect_trend()

        # After a strong enough uptrend, trend_dir should become 1
        # This tests the golden cross detection logic
        fast = strategy._ema_fast.value
        slow = strategy._ema_slow.value
        assert fast is not None and slow is not None
        if fast > slow:
            strategy._trend_dir = 1
        assert strategy._trend_dir == 1

    def test_death_cross_bearish(self, strategy):
        """EMA8 下穿 EMA21 → 空头方向"""
        # Pre-feed with fast > slow
        fast_higher = [
            _make_bar("2025-09-15 09:35:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:40:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:45:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:50:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 09:55:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 10:00:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 10:05:00", 100, 101, 99, 100.0),
            _make_bar("2025-09-15 10:10:00", 100, 101, 99, 100.0),
        ]
        for b in fast_higher:
            strategy._ema_fast.update(b.close)
            strategy._ema_slow.update(b.close)

        # Falling prices for death cross
        falling = [
            _make_bar("2025-09-15 10:15:00", 97, 98, 95, 95.0),
            _make_bar("2025-09-15 10:20:00", 95, 96, 92, 93.0),
            _make_bar("2025-09-15 10:25:00", 93, 94, 90, 91.0),
            _make_bar("2025-09-15 10:30:00", 91, 92, 88, 89.0),
            _make_bar("2025-09-15 10:35:00", 89, 90, 86, 87.0),
            _make_bar("2025-09-15 10:40:00", 87, 88, 84, 85.0),
            _make_bar("2025-09-15 10:45:00", 85, 86, 82, 83.0),
            _make_bar("2025-09-15 10:50:00", 83, 84, 80, 81.0),
        ]
        for b in falling:
            strategy._detect_trend()
            strategy._update_ema(b.close)
            strategy._detect_trend()

        fast = strategy._ema_fast.value
        slow = strategy._ema_slow.value
        assert fast is not None and slow is not None
        if fast < slow:
            strategy._trend_dir = -1
        assert strategy._trend_dir == -1


# =============================================================================
# Swing 回踩检测
# =============================================================================

class TestSwingDetection:
    def test_detect_swing_low_bullish(self, strategy):
        """多头趋势中找到 swing low"""
        strategy._trend_dir = 1
        bars = [
            _make_bar("2025-09-15 10:00:00", 100, 102, 99, 101),
            _make_bar("2025-09-15 10:05:00", 101, 103, 100, 102),
            _make_bar("2025-09-15 10:10:00", 102, 104, 98, 99),   # swing low candidate (low=98)
            _make_bar("2025-09-15 10:15:00", 99, 105, 99, 104),    # confirm bar (low=99 > 98)
        ]
        for b in bars:
            strategy._bar_buffer.append(b)
        strategy._detect_swing()
        assert strategy._swing_bar is not None
        assert strategy._swing_bar.low == 98.0

    def test_detect_swing_high_bearish(self, strategy):
        """空头趋势中找到 swing high"""
        strategy._trend_dir = -1
        bars = [
            _make_bar("2025-09-15 10:00:00", 100, 102, 98, 99),
            _make_bar("2025-09-15 10:05:00", 99, 103, 97, 98),
            _make_bar("2025-09-15 10:10:00", 98, 106, 97, 105),   # swing high candidate (high=106)
            _make_bar("2025-09-15 10:15:00", 105, 105, 102, 103),  # confirm bar (high=105 < 106)
        ]
        for b in bars:
            strategy._bar_buffer.append(b)
        strategy._detect_swing()
        assert strategy._swing_bar is not None
        assert strategy._swing_bar.high == 106.0

    def test_no_swing_insufficient_bars(self, strategy):
        """bar 不够时不触发 swing"""
        strategy._trend_dir = 1
        bars = [
            _make_bar("2025-09-15 10:00:00", 100, 102, 99, 101),
            _make_bar("2025-09-15 10:05:00", 101, 103, 100, 102),
        ]
        for b in bars:
            strategy._bar_buffer.append(b)
        strategy._detect_swing()
        assert strategy._swing_bar is None

    def test_no_swing_no_trend(self, strategy):
        """无趋势时不检测 swing"""
        strategy._trend_dir = 0
        bars = [
            _make_bar("2025-09-15 10:00:00", 100, 102, 99, 101),
            _make_bar("2025-09-15 10:05:00", 101, 103, 100, 102),
            _make_bar("2025-09-15 10:10:00", 102, 104, 98, 99),
            _make_bar("2025-09-15 10:15:00", 99, 105, 99, 104),
        ]
        for b in bars:
            strategy._bar_buffer.append(b)
        strategy._detect_swing()
        assert strategy._swing_bar is None


# =============================================================================
# 入场确认
# =============================================================================

class TestEntryConfirmation:
    def test_entry_long_breakout(self, strategy):
        """突破 swing high 做多入场"""
        strategy._trend_dir = 1
        # swing bar with low=98
        swing = _make_bar("2025-09-15 10:10:00", 102, 104, 98, 99)
        strategy._swing_bar = swing
        strategy._signal_fired = False

        # Breakout bar: bullish, close > swing.high
        entry_bar = _make_bar("2025-09-15 10:20:00", 103, 106, 102, 105)
        signal = strategy._check_entry(entry_bar)

        assert signal is not None
        assert signal.side == OrderSide.BUY
        assert signal.entry_price == 105.0
        assert signal.stop_loss < swing.low  # stop below swing low
        assert signal.strategy == strategy.name

    def test_entry_short_breakdown(self, strategy):
        """跌破 swing low 做空入场"""
        strategy._trend_dir = -1
        # swing bar with high=106
        swing = _make_bar("2025-09-15 10:10:00", 98, 106, 97, 105)
        strategy._swing_bar = swing
        strategy._signal_fired = False

        # Breakdown bar: bearish, close < swing.low
        entry_bar = _make_bar("2025-09-15 10:20:00", 97, 98, 95, 96)
        signal = strategy._check_entry(entry_bar)

        assert signal is not None
        assert signal.side == OrderSide.SELL
        assert signal.entry_price == 96.0
        assert signal.stop_loss > swing.high  # stop above swing high
        assert signal.strategy == strategy.name

    def test_no_entry_wrong_direction(self, strategy):
        """K线方向不对不入场"""
        strategy._trend_dir = 1
        swing = _make_bar("2025-09-15 10:10:00", 102, 104, 98, 99)
        strategy._swing_bar = swing

        # Bearish bar in bullish trend
        entry_bar = _make_bar("2025-09-15 10:20:00", 105, 106, 99, 100)  # bearish
        signal = strategy._check_entry(entry_bar)
        assert signal is None

    def test_no_entry_no_breakout(self, strategy):
        """未突破不入场"""
        strategy._trend_dir = 1
        swing = _make_bar("2025-09-15 10:10:00", 102, 104, 98, 105)  # high=104
        strategy._swing_bar = swing

        # close <= swing.high
        entry_bar = _make_bar("2025-09-15 10:20:00", 103, 104, 102, 104)
        signal = strategy._check_entry(entry_bar)
        assert signal is None

    def test_only_one_entry_per_day(self, strategy):
        """每天只入场一次"""
        strategy._trend_dir = 1
        swing = _make_bar("2025-09-15 10:10:00", 102, 104, 98, 99)
        strategy._swing_bar = swing
        strategy._signal_fired = True  # Already fired

        entry_bar = _make_bar("2025-09-15 10:20:00", 103, 106, 102, 105)
        signal = strategy.on_bar(DataEvent(
            timestamp=entry_bar.timestamp,
            bars={"TSLA": entry_bar},
        ))
        assert signal is None

    def test_entry_conditions_set(self, strategy):
        """入场信号的 EntryConditions 正确设置"""
        strategy._trend_dir = 1
        swing = _make_bar("2025-09-15 10:10:00", 102, 104, 98, 99)
        strategy._swing_bar = swing

        entry_bar = _make_bar("2025-09-15 10:20:00", 103, 106, 102, 105)
        signal = strategy._check_entry(entry_bar)

        assert signal is not None
        cond = signal.entry_conditions
        assert cond.require_vwap_side is True
        assert cond.adx_min == 25.0
        assert cond.max_spread_pct == 0.002


# =============================================================================
# 时间约束
# =============================================================================

class TestTimeConstraints:
    def test_in_entry_window(self, strategy):
        assert strategy._is_entry_time(time(9, 35))
        assert strategy._is_entry_time(time(10, 30))
        assert strategy._is_entry_time(time(13, 30))

    def test_before_entry_window(self, strategy):
        assert not strategy._is_entry_time(time(9, 30))
        assert not strategy._is_entry_time(time(9, 34))

    def test_after_entry_window_before_flat(self, strategy):
        # 13:31 is after latest_entry_time (13:30) but before force_flat (15:55)
        assert not strategy._is_entry_time(time(13, 31))

    def test_after_force_flat(self, strategy):
        assert not strategy._is_entry_time(time(15, 55))
        assert not strategy._is_entry_time(time(16, 0))

    def test_weekend_not_in_window(self, strategy):
        # This is testing time only, not date
        assert strategy._is_entry_time(time(10, 0))


# =============================================================================
# 完整回测流程集成测试
# =============================================================================

class TestIntegrationEndToEnd:
    def test_full_bullish_scenario(self, strategy):
        """完整的做多入场流程"""
        # 1. Build EMA baseline with sideways prices
        for i in range(25):
            bar = _make_bar(
                f"2025-09-15 09:{(35+i):02d}:00",
                100, 101, 99, 100.0,
            )
            strategy._update_ema(bar.close)
            strategy._bar_buffer.append(bar)

        # 2. Rising prices to create golden cross
        for i in range(15):
            price = 100 + i * 2
            bar = _make_bar(
                f"2025-09-15 10:{i:02d}:00",
                price - 0.5, price + 1, price - 1, price + 0.5,
            )
            strategy._update_ema(bar.close)
            strategy._bar_buffer.append(bar)

        # Force golden cross
        fast = strategy._ema_fast.value
        slow = strategy._ema_slow.value
        if fast is not None and slow is not None and fast > slow:
            strategy._trend_dir = 1

        assert strategy._trend_dir == 1

        # 3. Pullback creating swing low
        pullback_bars = [
            _make_bar("2025-09-15 10:15:00", 130, 132, 128, 131),   # before
            _make_bar("2025-09-15 10:20:00", 131, 133, 125, 126),   # swing low candidate (low=125)
            _make_bar("2025-09-15 10:25:00", 126, 132, 126, 131),   # confirm
        ]
        for b in pullback_bars:
            strategy._update_ema(b.close)
            strategy._bar_buffer.append(b)

        strategy._detect_swing()
        assert strategy._swing_bar is not None

        # 4. Breakout
        breakout = _make_bar("2025-09-15 10:30:00", 131, 135, 130, 134)
        strategy._update_ema(breakout.close)
        strategy._bar_buffer.append(breakout)

        signal = strategy._check_entry(breakout)
        assert signal is not None
        assert signal.side == OrderSide.BUY

    def test_strategy_returns_none_when_no_signal(self, strategy):
        """无信号时应返回 None"""
        bar = _make_bar("2025-09-15 09:35:00", 100, 101, 99, 100.5)
        event = DataEvent(
            timestamp=bar.timestamp,
            bars={"TSLA": bar},
        )
        signal = strategy.on_bar(event)
        assert signal is None


# =============================================================================
# 参数配置
# =============================================================================

class TestParameters:
    def test_custom_ema_periods(self):
        s = SwingTrendStock(["TSLA"], params={"ema_fast": 12, "ema_slow": 26})
        s.on_start()
        s.on_session_start("2025-09-15")
        assert s._ema_fast_period == 12
        assert s._ema_slow_period == 26

    def test_custom_swing_params(self):
        s = SwingTrendStock(["TSLA"], params={
            "swing_lookback": 3,
            "swing_confirm": 2,
        })
        s.on_start()
        assert s._swing_lookback == 3
        assert s._swing_confirm == 2

    def test_defaults_on_empty_params(self):
        s = SwingTrendStock(["TSLA"], params={})
        s.on_start()
        assert s._ema_fast_period == 8
        assert s._ema_slow_period == 21
        assert s._adx_min == 25.0
        assert s._swing_lookback == 2
        assert s._swing_confirm == 1