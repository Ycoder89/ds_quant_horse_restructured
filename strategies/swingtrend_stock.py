"""
strategies/swingtrend_stock.py — SwingTrend 股票版（策略 C，待 WFV 验证）

核心改进（vs cc_quant_horse T3/T9）：
  1. 纯信号生成 — Strategy 不持仓位/止损/风控状态
  2. 5min bar 驱动 + EMA8/21 交叉判定趋势方向
  3. ADX(14) > 25 趋势强度过滤（cc_quant_horse 原版无此过滤）
  4. Swing 回踩检测（还原到 swimg low/high）
  5. EntryConditions 统一管理 VWAP/Volume/ADX 过滤条件
  6. 每方向每天最多 1 次入场

策略逻辑：
  - IDLE → 检测 EMA8/21 金叉/死叉 → TREND_CONFIRMED
  - TREND_CONFIRMED → 检测 swing 回踩 → PULLBACK_DETECTED
  - PULLBACK_DETECTED → 突破确认入场 → 发出 SignalEvent → 回 IDLE
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, time
from typing import Optional

from core.events import (
    Bar,
    DataEvent,
    EntryConditions,
    OrderSide,
    SignalEvent,
)
from core.strategy import Strategy, TimeConstraints


# =============================================================================
# EMA 增量计算器（轻量，无 NumPy 依赖）
# =============================================================================

class _EMA:
    """EMA 增量计算器。"""

    def __init__(self, period: int) -> None:
        self._period = period
        self._alpha = 2.0 / (period + 1)
        self._value: Optional[float] = None
        self._prev_value: Optional[float] = None

    @property
    def value(self) -> Optional[float]:
        return self._value

    @property
    def prev_value(self) -> Optional[float]:
        return self._prev_value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, price: float) -> None:
        self._prev_value = self._value
        if self._value is None:
            self._value = price
        else:
            self._value = self._alpha * price + (1 - self._alpha) * self._value

    def reset(self) -> None:
        self._value = None
        self._prev_value = None


# =============================================================================
# SwingTrendStock 策略
# =============================================================================

class SwingTrendStock(Strategy):
    """SwingTrend 股票版 — 趋势 + 回踩入场（5min bar 驱动）"""

    # ════════════════════════════════════════════════════════════════════
    # 策略参数（可从 YAML config 加载）
    # ════════════════════════════════════════════════════════════════════
    _ema_fast_period: int = 8
    _ema_slow_period: int = 21
    _adx_min: float = 25.0
    _swing_lookback: int = 2       # 回踩前看 N 根 bar
    _swing_confirm: int = 1         # 回踩后确认 M 根 bar
    _volume_spike_mult: float = 1.2
    _require_vwap_side: bool = True
    _atr_mult_stop: float = 1.5
    _max_spread_pct: float = 0.002
    _max_entries_per_direction: int = 1
    _latest_entry_time: str = "13:30"

    # ════════════════════════════════════════════════════════════════════
    # 日内状态
    # ════════════════════════════════════════════════════════════════════
    _bar_buffer: deque[Bar]
    _ema_fast: _EMA
    _ema_slow: _EMA
    _trend_dir: int            # +1 多头, -1 空头, 0 无
    _swing_bar: Optional[Bar]  # 回踩极值 bar
    _signal_fired: bool
    _entries_long: int
    _entries_short: int

    def __init__(
        self,
        symbols: list[str],
        params: dict,
        name: str = "",
    ) -> None:
        _sym = (symbols[0] if symbols else "unknown").lower()
        super().__init__(symbols, name=name or f"swingtrend_{_sym}_5min",
                         params=params)

    # ════════════════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════════════════

    def on_start(self) -> None:
        """加载参数"""
        self._ema_fast_period = self._params.get("ema_fast", 8)
        self._ema_slow_period = self._params.get("ema_slow", 21)
        self._adx_min = self._params.get("adx_min", 25.0)
        self._swing_lookback = self._params.get("swing_lookback", 2)
        self._swing_confirm = self._params.get("swing_confirm", 1)
        self._volume_spike_mult = self._params.get("volume_spike_mult", 1.2)
        self._require_vwap_side = self._params.get("require_vwap_side", True)
        self._atr_mult_stop = self._params.get("atr_mult_stop", 1.5)
        self._max_spread_pct = self._params.get("max_spread_pct", 0.002)
        self._max_entries_per_direction = self._params.get("max_entries_per_direction", 1)
        self._latest_entry_time = self._params.get("latest_entry_time", "13:30")

    def on_session_start(self, date_str: str) -> None:
        """每个交易日重置"""
        self._bar_buffer = deque(maxlen=100)
        self._ema_fast = _EMA(self._ema_fast_period)
        self._ema_slow = _EMA(self._ema_slow_period)
        self._trend_dir = 0
        self._swing_bar = None
        self._signal_fired = False
        self._entries_long = 0
        self._entries_short = 0

    # ════════════════════════════════════════════════════════════════════
    # 核心信号生成
    # ════════════════════════════════════════════════════════════════════

    def on_bar(self, event: DataEvent) -> Optional[SignalEvent]:
        bar = event.get_bar(self.symbols[0])
        if bar is None:
            return None

        self._bar_buffer.append(bar)
        self._update_ema(bar.close)

        # 每天只交易一次
        if self._signal_fired:
            return None

        # 检查入场时间窗口
        if not self._is_entry_time(bar.timestamp.time()):
            return None

        # 阶段 1：检测 EMA 交叉（趋势方向）
        if self._trend_dir == 0:
            self._detect_trend()
            return None

        # 阶段 2：检测 swing 回踩
        if self._swing_bar is None:
            self._detect_swing()
            return None

        # 阶段 3：突破确认入场
        return self._check_entry(bar)

    # ════════════════════════════════════════════════════════════════════
    # EMA 更新
    # ════════════════════════════════════════════════════════════════════

    def _update_ema(self, close: float) -> None:
        self._ema_fast.update(close)
        self._ema_slow.update(close)

    # ════════════════════════════════════════════════════════════════════
    # 趋势检测（EMA 金叉/死叉）
    # ════════════════════════════════════════════════════════════════════

    def _detect_trend(self) -> None:
        """检测 EMA8/21 交叉，判断日内趋势方向"""
        fast = self._ema_fast.value
        slow = self._ema_slow.value
        fast_prev = self._ema_fast.prev_value
        slow_prev = self._ema_slow.prev_value

        if fast is None or slow is None or fast_prev is None or slow_prev is None:
            return

        # 金叉（多头）
        if fast_prev <= slow_prev and fast > slow:
            if self._entries_long < self._max_entries_per_direction:
                self._trend_dir = 1

        # 死叉（空头）
        elif fast_prev >= slow_prev and fast < slow:
            if self._entries_short < self._max_entries_per_direction:
                self._trend_dir = -1

    # ════════════════════════════════════════════════════════════════════
    # Swing 回踩检测
    # ════════════════════════════════════════════════════════════════════

    def _detect_swing(self) -> None:
        """在趋势方向上检测 swing 回踩极值点"""
        n = self._swing_lookback
        m = self._swing_confirm
        needed = n + 1 + m
        bars = list(self._bar_buffer)

        if len(bars) < needed:
            return

        candidate = bars[-(m + 1)]

        if self._trend_dir == 1:
            # 多头：找 swing low
            for i in range(n):
                if bars[-(m + 1 + n - i)].low <= candidate.low:
                    return
            for i in range(m):
                if bars[-(m - i)].low <= candidate.low:
                    return
            self._swing_bar = candidate

        elif self._trend_dir == -1:
            # 空头：找 swing high
            for i in range(n):
                if bars[-(m + 1 + n - i)].high >= candidate.high:
                    return
            for i in range(m):
                if bars[-(m - i)].high >= candidate.high:
                    return
            self._swing_bar = candidate

    # ════════════════════════════════════════════════════════════════════
    # 入场确认
    # ════════════════════════════════════════════════════════════════════

    def _check_entry(self, bar: Bar) -> Optional[SignalEvent]:
        """突破 swing 极值确认入场"""
        swing = self._swing_bar
        if swing is None:
            return None

        # 必须 K 线方向对齐
        if self._trend_dir == 1:
            if not bar.is_bullish or bar.close <= swing.high:
                return None
            side = OrderSide.BUY
            entry_price = bar.close
            stop_loss = swing.low - (swing.high - swing.low) * 0.1  # 略低于 swing low
            self._entries_long += 1
        else:
            if bar.is_bullish or bar.close >= swing.low:
                return None
            side = OrderSide.SELL
            entry_price = bar.close
            stop_loss = swing.high + (swing.high - swing.low) * 0.1  # 略高于 swing high
            self._entries_short += 1

        self._signal_fired = True

        cond = EntryConditions(
            require_vwap_side=self._require_vwap_side,
            volume_spike_mult=self._volume_spike_mult,
            adx_min=self._adx_min,
            max_spread_pct=self._max_spread_pct,
        )

        return SignalEvent(
            symbol=self.symbols[0],
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            strategy=self.name,
            timestamp=bar.timestamp,
            entry_conditions=cond,
            confidence=1.0,
        )

    # ════════════════════════════════════════════════════════════════════
    # 时间约束
    # ════════════════════════════════════════════════════════════════════

    def _is_entry_time(self, t: time) -> bool:
        """入场窗口：9:35 ~ latest_entry_time"""
        entry_start = time(9, 35)
        parts = self._latest_entry_time.split(":")
        entry_end = time(int(parts[0]), int(parts[1]))
        force_flat = self._force_flat_time()
        return entry_start <= t <= entry_end and t < force_flat

    def _force_flat_time(self) -> time:
        fc = self._params.get("force_flat_time", "15:55")
        parts = fc.split(":")
        return time(int(parts[0]), int(parts[1]))