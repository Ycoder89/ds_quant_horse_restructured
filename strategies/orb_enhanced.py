"""
strategies/orb_enhanced.py — ORB 增强策略（重构版）

核心改进（vs cc_quant_horse 原版）：
  1. 纯信号生成 — Strategy 不持仓位/止损/风控状态
  2. EntryConditions 使用标准字段（require_vwap_side / volume_spike_mult / adx_min）
  3. 时间约束通过 TimeConstraints 对象统一管理
  4. FilterContext 构建由 RiskManager 负责，策略不再构建

策略逻辑：
  - ORB (Opening Range Breakout)：9:30-9:35 形成高低区间
  - 突破确认：价格突破 ORB 高/低 + 30% 置信度缓冲
  - VWAP 对齐：entry_conditions.require_vwap_side = True
  - 量能确认：entry_conditions.volume_spike_mult = 1.2
  - ADX 过滤：entry_conditions.adx_min = 20
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from core.events import (
    Bar,
    DataEvent,
    EntryConditions,
    OrderSide,
    SignalEvent,
)
from core.filters import FilterContext
from core.strategy import Strategy, TimeConstraints


class ORBEnhanced(Strategy):
    """ORB 增强策略 — 日内开盘区间突破 + VWAP 对齐 + 量能 + ADX 趋势过滤"""

    # ════════════════════════════════════════════════════════════════════
    # 策略参数（从 YAML config 加载）
    # ════════════════════════════════════════════════════════════════════
    _orb_start_minute: int = 5          # 开盘后 N 分钟形成 ORB 区间
    _entry_break_confidence: float = 0.3  # 突破置信度缓冲（30% 超出 ORB 范围）
    _adx_threshold: float = 20.0        # ADX 最低趋势阈值
    _volume_spike_mult: float = 1.2     # 放量倍数
    _require_vwap_side: bool = True     # 是否要求 VWAP 对齐
    _atr_mult_stop: float = 1.5         # ATR 止损倍数
    _max_spread_pct: float = 0.002      # 最大价差 0.2%

    # ════════════════════════════════════════════════════════════════════
    # 日内状态（每天 reset）
    # ════════════════════════════════════════════════════════════════════
    # 单标的 ORB 状态
    _orb_high: Optional[float] = None
    _orb_low: Optional[float] = None
    _orb_mid: float = 0.0
    _orb_formed: bool = False
    _signal_fired: bool = False
    _bar_count: int = 0
    _last_signal_bar: int = -100         # 上次信号产生的 bar 序号（再入场间隔用）
    _min_bars_between_signals: int = 3   # 同标的再入场最小 bar 间隔

    def __init__(
        self,
        symbols: list[str],
        params: dict,
        name: str = "",
    ) -> None:
        _sym = (symbols[0] if symbols else "unknown").lower()
        super().__init__(symbols, name=name or f"orb_enhanced_{_sym}_5min",
                         params=params)

    # ════════════════════════════════════════════════════════════════════
    # Strategy 生命周期
    # ════════════════════════════════════════════════════════════════════

    def on_start(self) -> None:
        """从 params 加载配置参数"""
        self._orb_start_minute = self._params.get("orb_start_minute", 5)
        self._entry_break_confidence = self._params.get("entry_break_confidence", 0.3)
        self._adx_threshold = self._params.get("adx_threshold", 20.0)
        self._volume_spike_mult = self._params.get("volume_spike_mult", 1.2)
        self._require_vwap_side = self._params.get("require_vwap_side", True)
        self._atr_mult_stop = self._params.get("atr_mult_stop", 1.5)
        self._max_spread_pct = self._params.get("max_spread_pct", 0.002)

    def on_session_start(self, date_str: str) -> None:
        """每个交易日重置日内状态"""
        self._orb_high = None
        self._orb_low = None
        self._orb_mid = 0.0
        self._orb_formed = False
        self._signal_fired = False
        self._bar_count = 0
        self._last_signal_bar = -100

    # ════════════════════════════════════════════════════════════════════
    # 核心信号生成
    # ════════════════════════════════════════════════════════════════════

    def on_bar(self, event: DataEvent) -> Optional[SignalEvent]:
        self._bar_count += 1
        bar = event.get_bar(self.symbols[0])
        if bar is None:
            return None

        # ---- 阶段 1：收集 ORB 区间（前 N 根 bar） ----
        if not self._orb_formed:
            return self._collect_orb(bar, event.timestamp)

        # ---- 阶段 2：检查信号 ----
        if self._signal_fired:
            # 回调再入场：价格回到 ORB 区间内且间隔足够 → 重置信号
            gap_ok = self._bar_count - self._last_signal_bar >= self._min_bars_between_signals
            price_back_inside = bar.low <= self._orb_high and bar.high >= self._orb_low
            if price_back_inside and gap_ok:
                self._signal_fired = False
            else:
                return None

        if not self._is_entry_time(bar.timestamp.time()):
            return None

        if not bar.is_bullish and not self._is_short_side(bar):
            return None  # 严格 K 线方向对齐

        # 多头突破
        if bar.is_bullish and bar.close > self._orb_high:
            return self._build_signal(bar, OrderSide.BUY)

        # 空头突破
        if not bar.is_bullish and bar.close < self._orb_low:
            return self._build_signal(bar, OrderSide.SELL)

        return None

    # ════════════════════════════════════════════════════════════════════
    # ORB 区间收集
    # ════════════════════════════════════════════════════════════════════

    def _collect_orb(self, bar: Bar, ts: datetime) -> None:
        """收集前 N 分钟的高低点形成 ORB 区间（UTC 时间）"""
        minute_of_day = ts.hour * 60 + ts.minute
        market_open = self._market_open_utc()
        orb_end = market_open + self._orb_start_minute

        if minute_of_day < market_open or minute_of_day > orb_end + 5:
            return  # 超出 ORB 窗口，跳过

        if self._orb_high is None:
            self._orb_high = bar.high
            self._orb_low = bar.low
        else:
            self._orb_high = max(self._orb_high, bar.high)
            self._orb_low = min(self._orb_low, bar.low)

        # 检查 ORB 是否完成
        if minute_of_day >= orb_end and self._orb_high is not None:
            self._orb_mid = (self._orb_high + self._orb_low) / 2.0
            self._orb_formed = True

    # ════════════════════════════════════════════════════════════════════
    # 入场时间检查（所有时间以 UTC 为准）
    # ════════════════════════════════════════════════════════════════════

    def _market_open_utc(self) -> int:
        """市场开盘 UTC 分钟（默认 14:30 = 9:30 ET）"""
        return self._params.get("market_open_utc_minutes", 14 * 60 + 30)

    def _entry_end_utc(self) -> int:
        """入场截止 UTC 分钟（默认 16:30 = 11:30 ET）"""
        return self._params.get("entry_end_utc_minutes", 16 * 60 + 30)

    def _force_flat_utc(self) -> int:
        """强平 UTC 分钟（默认 20:55 = 15:55 ET）"""
        return self._params.get("force_flat_utc_minutes", 20 * 60 + 55)

    def _is_entry_time(self, t: time) -> bool:
        """入场窗口：开盘 + ORB 时间后 ～ entry_end，且不晚于强平时间"""
        current_minutes = t.hour * 60 + t.minute
        entry_start = self._market_open_utc() + self._orb_start_minute
        entry_end = self._entry_end_utc()
        force_flat = self._force_flat_utc()
        return entry_start <= current_minutes <= entry_end and current_minutes < force_flat

    # ════════════════════════════════════════════════════════════════════
    # 信号构建
    # ════════════════════════════════════════════════════════════════════

    def _build_signal(self, bar: Bar, side: OrderSide) -> Optional[SignalEvent]:
        """构建入场信号"""
        # 突破置信度：价格必须超出 ORB 区间 30% 宽度
        orb_width = self._orb_high - self._orb_low
        if orb_width <= 0:
            return None

        buffer_dist = orb_width * self._entry_break_confidence

        if side == OrderSide.BUY:
            entry_price = self._orb_high + buffer_dist
            stop_loss = entry_price - orb_width * self._atr_mult_stop
        else:
            entry_price = self._orb_low - buffer_dist
            stop_loss = entry_price + orb_width * self._atr_mult_stop

        # 确保止损在合理位置（ORB 区间内）
        if side == OrderSide.BUY:
            stop_loss = min(stop_loss, self._orb_low)
        else:
            stop_loss = max(stop_loss, self._orb_high)

        cond = EntryConditions(
            require_vwap_side=self._require_vwap_side,
            volume_spike_mult=self._volume_spike_mult,
            adx_min=self._adx_threshold,
            max_spread_pct=self._max_spread_pct,
        )

        self._signal_fired = True
        self._last_signal_bar = self._bar_count
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
    # 辅助
    # ════════════════════════════════════════════════════════════════════

    def _is_short_side(self, bar: Bar) -> bool:
        """简化空头侧检查：阴线即可"""
        return not bar.is_bullish


def _minutes_to_time(total_minutes: int) -> time:
    return time(total_minutes // 60, total_minutes % 60)