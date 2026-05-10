"""
strategies/pullback_ema.py — Pullback-to-EMA 趋势跟踪策略

核心逻辑：
  1. 计算 EMA_fast 和 EMA_slow
  2. EMA_fast > EMA_slow × (1 + slope_threshold) → 上升趋势
  3. 价格回撤至 EMA_fast 附近 → 确认支撑/阻力有效
  4. 反转 K 线确认后入场（与趋势同向）
  5. VWAP 对齐过滤（仅在 VWAP 同侧入场）
  6. 入场窗口限制在 UTC 14:35~17:30，确保有足够日内时间

改进（v2）：
  - 增加 VWAP 过滤（close 必须在 VWAP 同侧）
  - 日内可多次入场（原限制一次，改为取决于持仓状态）
  - 时间窗口从 20:30 UTC 缩短到 17:30 UTC = 1:30 PM ET
  - 宽松反弹确认（body > 40% 范围）
  - better stop calculation（结合 ATR 和近期 swing point）
"""
from __future__ import annotations

from collections import deque
from datetime import time
from typing import Optional

from core.events import Bar, DataEvent, EntryConditions, OrderSide, SignalEvent
from core.strategy import Strategy


class PullbackEMA(Strategy):
    """
    Pullback-to-EMA 趋势跟踪策略（v2 改进版）。

    参数:
      ema_fast: int       快速 EMA 周期（默认 8）
      ema_slow: int       慢速 EMA 周期（默认 21）
      trend_slope_pct: float    判断趋势的 EMA 斜率阈值%（默认 0.1）
      pullback_max_pct: float   回撤到 EMA 的最大距离%（默认 0.8）
      pullback_min_pct: float   回撤到 EMA 的最小距离%（默认 0.05）
      bounce_body_pct: float    反弹确认 K 线实体占比最低%（默认 40）
      volume_spike_mult: float  放量确认倍数（默认 1.2）
      atr_mult_stop: float      止损 ATR 倍数（默认 1.5）
      lookback_atr: int         计算 ATR 的 bar 数（默认 14）
      use_vwap_filter: int      是否使用 VWAP 过滤（1/0，默认 1）
      max_entries_per_day: int  日内最大入场次数（默认 3）
      entry_start_utc: int      入场窗口开始（UTC 分钟，默认 875 = 14:35）
      entry_end_utc: int        入场窗口结束（UTC 分钟，默认 1050 = 17:30）
    """

    def __init__(self, symbols: list[str], params: dict, name: str = "") -> None:
        _sym = (symbols[0] if symbols else "unknown").lower()
        super().__init__(symbols, name=name or f"pullback_{_sym}_5min", params=params)

    def on_start(self) -> None:
        self._ema_fast = self._params.get("ema_fast", 8)
        self._ema_slow = self._params.get("ema_slow", 21)
        self._trend_slope = self._params.get("trend_slope_pct", 0.1) / 100.0
        self._pullback_max = self._params.get("pullback_max_pct", 0.8) / 100.0
        self._pullback_min = self._params.get("pullback_min_pct", 0.05) / 100.0
        self._bounce_body = self._params.get("bounce_body_pct", 40.0)
        self._volume_mult = self._params.get("volume_spike_mult", 1.2)
        self._atr_mult = self._params.get("atr_mult_stop", 1.5)
        self._atr_period = self._params.get("lookback_atr", 14)
        self._use_vwap = bool(self._params.get("use_vwap_filter", 1))
        self._max_entries = self._params.get("max_entries_per_day", 3)

        # 日内状态
        self._prices: deque[float] = deque(maxlen=max(self._ema_slow, self._atr_period) + 5)
        self._highs: deque[float] = deque(maxlen=self._atr_period + 2)
        self._lows: deque[float] = deque(maxlen=self._atr_period + 2)
        self._volumes: deque[int] = deque(maxlen=20)
        self._bar_buffer: list[Bar] = []    # 用于 VWAP 计算
        self._entry_count: int = 0
        self._bars_collected: int = 0

    def on_session_start(self, date_str: str) -> None:
        self._prices.clear()
        self._highs.clear()
        self._lows.clear()
        self._volumes.clear()
        self._bar_buffer.clear()
        self._entry_count = 0
        self._bars_collected = 0

    # ── 核心逻辑 ──

    def on_bar(self, event: DataEvent) -> Optional[SignalEvent]:
        bar = event.get_bar(self.symbols[0])
        if bar is None:
            return None

        self._bars_collected += 1
        self._prices.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._volumes.append(bar.volume)
        self._bar_buffer.append(bar)

        # 日内入场次数限制
        if self._entry_count >= self._max_entries:
            return None

        # 需要足够数据计算 EMA
        if self._bars_collected < self._ema_slow + 1:
            return None

        # 时间窗口
        t = bar.timestamp.time()
        if not self._is_trading_hours(t):
            return None

        # 计算当前趋势方向
        trend = self._detect_trend()
        if trend is None:
            return None

        # 检查价格是否回撤到 EMA_fast 附近
        ema_fast_val = self._calc_ema(self._ema_fast)
        if ema_fast_val <= 0:
            return None

        atr = self._calc_atr()

        # VWAP 对齐过滤
        if self._use_vwap:
            vwap = self._calc_vwap()
            if vwap <= 0:
                return None
            if trend is OrderSide.BUY and bar.close < vwap:
                return None  # 上升趋势但价格在 VWAP 下方
            if trend is OrderSide.SELL and bar.close > vwap:
                return None  # 下降趋势但价格在 VWAP 上方

        # 上升趋势：等价格回撤到 EMA_fast 附近 + 阳线反弹
        if trend == OrderSide.BUY:
            pullback_dist = (ema_fast_val - bar.low) / ema_fast_val
            min_dist = self._pullback_min * atr / ema_fast_val if atr > 0 else self._pullback_min
            if not (min_dist <= pullback_dist <= self._pullback_max):
                return None
            if not self._is_bounce_signal(bar, OrderSide.BUY):
                return None
            return self._build_signal(bar, OrderSide.BUY, ema_fast_val, atr)

        # 下降趋势：等价格反弹到 EMA_fast 附近 + 阴线回落
        if trend == OrderSide.SELL:
            pullback_dist = (bar.high - ema_fast_val) / ema_fast_val
            min_dist = self._pullback_min * atr / ema_fast_val if atr > 0 else self._pullback_min
            if not (min_dist <= pullback_dist <= self._pullback_max):
                return None
            if not self._is_bounce_signal(bar, OrderSide.SELL):
                return None
            return self._build_signal(bar, OrderSide.SELL, ema_fast_val, atr)

        return None

    # ── 趋势检测 ──

    def _detect_trend(self) -> Optional[OrderSide]:
        """使用 EMA 斜率判断趋势（v2：增加趋势强度幅度检查）"""
        ema_fast = self._calc_ema(self._ema_fast)
        ema_slow = self._calc_ema(self._ema_slow)

        if ema_fast <= 0 or ema_slow <= 0:
            return None

        # 快 EMA > 慢 EMA = 上升趋势
        if ema_fast > ema_slow * (1 + self._trend_slope):
            if self._ema_slope_confirmed(self._ema_fast, lookback=3):
                return OrderSide.BUY

        # 快 EMA < 慢 EMA = 下降趋势
        if ema_fast < ema_slow * (1 - self._trend_slope):
            if self._ema_slope_confirmed(self._ema_fast, lookback=3, upward=False):
                return OrderSide.SELL

        return None

    def _ema_slope_confirmed(self, period: int, lookback: int = 3, upward: bool = True) -> bool:
        """检查过去 N 根 bar 的 EMA 方向是否一致"""
        if len(self._prices) < period + lookback:
            return True
        vals = [self._calc_ema(period, i) for i in range(lookback)]
        if upward:
            return all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
        else:
            return all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))

    # ── 反弹确认 ──

    def _is_bounce_signal(self, bar: Bar, direction: OrderSide) -> bool:
        """反弹确认（v2：body > bounce_body_pct%，默认 40%）"""
        if bar.range <= 0:
            return False

        body_pct = abs(bar.close - bar.open) / bar.range * 100
        if body_pct < self._bounce_body:
            return False

        if direction is OrderSide.BUY:
            return bar.is_bullish
        else:
            return not bar.is_bullish

    # ── VWAP 计算 ──

    def _calc_vwap(self) -> float:
        """日内累计 VWAP"""
        if not self._bar_buffer:
            return 0.0
        total_vp = 0.0
        total_vol = 0
        for b in self._bar_buffer:
            tp = (b.high + b.low + b.close) / 3.0
            total_vp += tp * b.volume
            total_vol += b.volume
        return total_vp / total_vol if total_vol > 0 else 0.0

    # ── 时间窗口 ──

    def _is_trading_hours(self, t: time) -> bool:
        minutes = t.hour * 60 + t.minute
        entry_start = self._params.get("entry_start_utc", 14 * 60 + 35)    # 14:35 UTC = 9:35 ET
        entry_end = self._params.get("entry_end_utc", 17 * 60 + 30)        # 17:30 UTC = 12:30 ET
        return entry_start <= minutes <= entry_end

    # ── 信号构建 ──

    def _build_signal(self, bar: Bar, side: OrderSide,
                      ema_val: float, atr: float) -> SignalEvent:
        self._entry_count += 1
        stop_dist = atr * self._atr_mult

        if side is OrderSide.BUY:
            entry_price = bar.close
            stop_loss = min(entry_price - stop_dist, bar.low - (bar.range * 0.1))
        else:
            entry_price = bar.close
            stop_loss = max(entry_price + stop_dist, bar.high + (bar.range * 0.1))

        cond = EntryConditions(
            require_vwap_side=bool(self._use_vwap),
            volume_spike_mult=self._volume_mult,
        )

        return SignalEvent(
            symbol=self.symbols[0],
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            strategy=self.name,
            timestamp=bar.timestamp,
            entry_conditions=cond,
        )

    # ── 指标计算 ──

    def _calc_ema(self, period: int, offset: int = 0) -> float:
        """从尾部偏移计算 EMA（offset=0 表示当前）"""
        n = len(self._prices)
        if n < period + offset:
            return 0.0
        prices = list(self._prices)[:n - offset]
        if len(prices) < period:
            return 0.0
        k = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _calc_atr(self) -> float:
        """简易 ATR"""
        n = min(len(self._highs), len(self._lows), self._atr_period)
        if n < 2:
            return 0.0
        total = 0.0
        for i in range(-n, 0):
            total += abs(self._highs[i] - self._lows[i])
        return total / n
