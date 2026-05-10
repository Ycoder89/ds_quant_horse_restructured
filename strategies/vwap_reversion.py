"""
strategies/vwap_reversion.py — VWAP 均值回归策略

核心逻辑：
  1. 日内实时 VWAP 作为均值基准
  2. 当价格偏离 VWAP 超过 X × ATR 时进入"超买/超卖"区域
  3. 等待反转 K 线确认（实体方向与偏离方向相反）
  4. 入场，止盈目标 VWAP，止损在极端价格之外

vs ORB:
  - ORB 做突破，这个做反转
  - ORB 在开盘，这个在全天
  - 低相关性，适合组合
"""
from __future__ import annotations

from datetime import time
from typing import Optional

from core.events import Bar, DataEvent, EntryConditions, OrderSide, SignalEvent
from core.strategy import Strategy


class VWAPReversion(Strategy):
    """
    VWAP 均值回归策略。

    参数:
      deviation_threshold: float  偏离 VWAP 的 ATR 倍数阈值（默认 1.5）
      reversal_body_pct: float    反转 K 线实体占比最小值%（默认 60）
      volume_spike_mult: float    反转 K 线成交量倍数确认（默认 1.2）
      atr_mult_stop: float        止损 = 入场价 ± ATR × 倍数（默认 1.5）
      max_spread_pct: float       最大价差（默认 0.002）
    """

    def __init__(self, symbols: list[str], params: dict, name: str = "") -> None:
        _sym = (symbols[0] if symbols else "unknown").lower()
        super().__init__(symbols, name=name or f"vwap_rev_{_sym}_5min", params=params)

    # ── 生命周期 ──

    def on_start(self) -> None:
        self._deviation = self._params.get("deviation_threshold", 1.5)
        self._reversal_body = self._params.get("reversal_body_pct", 60.0)
        self._volume_mult = self._params.get("volume_spike_mult", 1.2)
        self._atr_mult = self._params.get("atr_mult_stop", 1.5)
        self._max_spread = self._params.get("max_spread_pct", 0.002)
        self._vwap_period = self._params.get("vwap_period", 5)  # VWAP计算周期

        # 日内状态
        self._daily_vwap: float = 0.0
        self._daily_high: float = 0.0
        self._daily_low: float = 0.0
        self._bar_buffer: list[Bar] = []
        self._signal_fired: bool = False
        self._bias_short: bool = False    # True=当前处于超买区(找空头信号)
        self._bias_long: bool = False     # True=当前处于超卖区(找多头信号)
        self._entry_count: int = 0
        self._max_entries: int = 1

    def on_session_start(self, date_str: str) -> None:
        self._daily_vwap = 0.0
        self._daily_high = 0.0
        self._daily_low = 0.0
        self._bar_buffer = []
        self._signal_fired = False
        self._bias_short = False
        self._bias_long = False

    # ── 核心逻辑 ──

    def on_bar(self, event: DataEvent) -> Optional[SignalEvent]:
        bar = event.get_bar(self.symbols[0])
        if bar is None:
            return None

        # 收集日内 VWAP 和范围
        self._update_daily_state(bar)

        # 检查是否已有信号
        if self._signal_fired:
            return None

        # 检查时间窗口（美东 9:30~15:30）
        t = bar.timestamp.time()
        if not self._is_trading_hours(t):
            return None

        # 需要一定的 bar 积累才能计算 VWAP
        if len(self._bar_buffer) < self._vwap_period:
            return None

        # 判断是否在超买/超卖区
        self._check_deviation(bar)

        # 空头信号：超买区 + 反转阴线
        if self._bias_short and self._is_reversal_signal(bar, OrderSide.SELL):
            return self._build_signal(bar, OrderSide.SELL)

        # 多头信号：超卖区 + 反转阳线
        if self._bias_long and self._is_reversal_signal(bar, OrderSide.BUY):
            return self._build_signal(bar, OrderSide.BUY)

        return None

    # ── 日内状态更新 ──

    def _update_daily_state(self, bar: Bar) -> None:
        """更新日内 VWAP 和高低点"""
        self._bar_buffer.append(bar)

        # 日内 VWAP（累计典型价格×成交量 / 累计成交量）
        total_vp = 0.0
        total_vol = 0
        for b in self._bar_buffer:
            tp = (b.high + b.low + b.close) / 3.0
            total_vp += tp * b.volume
            total_vol += b.volume
        self._daily_vwap = total_vp / total_vol if total_vol > 0 else bar.close

        # 日内高低
        self._daily_high = max(self._daily_high, bar.high)
        self._daily_low = min(self._daily_low, bar.low) if self._daily_low > 0 else bar.low

    # ── 偏差检测 ──

    def _check_deviation(self, bar: Bar) -> None:
        """检查价格是否偏离 VWAP 超过阈值"""
        if self._daily_vwap <= 0:
            return

        atr = self._estimate_atr()
        if atr <= 0:
            return

        threshold = atr * self._deviation
        self._bias_short = (bar.close - self._daily_vwap) > threshold
        self._bias_long = (self._daily_vwap - bar.close) > threshold

    # ── 反转信号检测 ──

    def _is_reversal_signal(self, bar: Bar, direction: OrderSide) -> bool:
        """
        反转确认：
          - 做空：当前 bar 是阴线，实体占比 > reversal_body_pct
          - 做多：当前 bar 是阳线，实体占比 > reversal_body_pct
        """
        if bar.range <= 0:
            return False

        body_pct = abs(bar.close - bar.open) / bar.range * 100
        if body_pct < self._reversal_body:
            return False  # 实体太小，反转力度不足

        if direction is OrderSide.SELL:
            return not bar.is_bullish  # 阴线
        else:
            return bar.is_bullish      # 阳线

    # ── 时间窗口 ──

    def _is_trading_hours(self, t: time) -> bool:
        """交易窗口：开盘后 5 分钟 ～ 15:30 ET（UTC 14:35~20:30）"""
        minutes = t.hour * 60 + t.minute
        # 默认 UTC 时间：14:35 ~ 20:30
        entry_start = self._params.get("entry_start_utc", 14 * 60 + 35)
        entry_end = self._params.get("entry_end_utc", 20 * 60 + 30)
        return entry_start <= minutes <= entry_end

    # ── 信号构建 ──

    def _build_signal(self, bar: Bar, side: OrderSide) -> SignalEvent:
        self._signal_fired = True
        self._entry_count += 1
        atr = max(self._estimate_atr(), bar.range * 0.5)

        if side is OrderSide.SELL:
            entry_price = bar.close
            stop_loss = entry_price + atr * self._atr_mult
        else:
            entry_price = bar.close
            stop_loss = entry_price - atr * self._atr_mult

        cond = EntryConditions(
            require_vwap_side=True,
            volume_spike_mult=self._volume_mult,
            max_spread_pct=self._max_spread,
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

    # ── 辅助 ──

    def _estimate_atr(self) -> float:
        """从 buffer 估算 ATR(14)"""
        if len(self._bar_buffer) < 2:
            return 0.0
        ranges = [abs(b.high - b.low) for b in self._bar_buffer[-14:]]
        return sum(ranges) / len(ranges)
