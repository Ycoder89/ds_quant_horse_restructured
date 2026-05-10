"""
core/strategy.py — Strategy 基类（精简版）

cc_quant_horse 原版 Strategy 包含：
  - Regime 管理（_current_regime、_regime_history）
  - Bracket 订单逻辑（_bracket_active）
  - 持仓跟踪（_positions dict）
  - 指标缓存（_sma、_atr 等）

ds_quant_horse 的精简原则：
  1. Strategy 只做信号生成（on_bar → 可选 SignalEvent）
  2. Regime 管理 → 移到独立的 RegimeFilter / FilterContext
  3. Bracket 逻辑 → 完全删除（ExitManager 接管）
  4. 持仓跟踪 → 移到 Portfolio（Portfolio 始终是持仓的单一事实来源）
  5. 指标缓存 → 移到 IndicatorCache（轻量 ta-lib wrapper）

Strategy 现在只关心：
  - 接收 bar 数据
  - 计算技术指标（通过 self.indicators）
  - 返回 SignalEvent（如果有入场信号）

这是 '纯' 策略层：不持状态、不管理退出、不关心风控。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable, Optional

from core.events import Bar, DataEvent, EntryConditions, OrderSide, SignalEvent


# =============================================================================
# Strategy interface
# =============================================================================

class Strategy(ABC):
    """
    策略抽象基类。

    子类实现 on_bar() 返回信号（或 None）。

    Usage:
        class ORBStrategy(Strategy):
            def on_bar(self, event: DataEvent) -> Optional[SignalEvent]:
                bar = event.get_bar("TSLA")
                if bar is None:
                    return None
                # ... ORB 逻辑 ...
                return SignalEvent(
                    symbol="TSLA",
                    side=OrderSide.BUY,
                    entry_price=bar.close,
                    stop_loss=bar.close - self.atr * 1.5,
                    strategy=self.name,
                    timestamp=event.timestamp,
                    entry_conditions=EntryConditions(require_vwap_side=True),
                )
    """

    def __init__(self, symbols: list[str], name: str = "Strategy",
                 params: Optional[dict] = None) -> None:
        self._symbols = symbols
        self._name = name
        self._params = params or {}
        self._event_callback: Optional[Callable[[SignalEvent], None]] = None

    def set_event_callback(self, callback: Callable[[SignalEvent], None]) -> None:
        """由引擎注入事件回调，Strategy 通过 emit_signal 将信号放入事件队列"""
        self._event_callback = callback

    def emit_signal(self, signal: SignalEvent) -> None:
        """将入场信号推入事件队列（引擎需先 set_event_callback）"""
        if self._event_callback is None:
            raise RuntimeError("Strategy.emit_signal 被调用，但未注入 event_callback。"
                               "请确保引擎在 on_start 前调用 set_event_callback。")
        self._event_callback(signal)

    @property
    def name(self) -> str:
        return self._name

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @abstractmethod
    def on_bar(self, event: DataEvent) -> Optional[SignalEvent]:
        """
        处理 K 线事件，返回入场信号（或 None）。

        Args:
            event: 包含当前 bar 的 DataEvent

        Returns:
            SignalEvent 如果有入场信号，否则 None
        """
        ...

    def on_start(self) -> None:
        """策略初始化（回测开始时调用一次）"""
        pass

    def on_session_start(self, date_str: str) -> None:
        """新交易日开始（由引擎调用）"""
        pass

    def on_finish(self) -> None:
        """策略清理（回测结束时调用一次）"""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(symbols={self._symbols})"


# =============================================================================
# TimeConstraints — 日内交易时间约束
# =============================================================================

@dataclass
class TimeConstraints:
    """
    日内交易的时间窗口约束。

    策略可以在 on_bar() 中用此对象判断是否在交易窗口内。

    Usage:
        tc = TimeConstraints(
            entry_start=time(9, 35),
            entry_end=time(11, 30),
            force_flat_before=time(15, 55),
        )
        if not tc.can_enter(event.timestamp.time()):
            return None
    """
    entry_start: time = time(9, 35)       # 最早入场时间
    entry_end: time = time(11, 30)         # 最晚入场时间
    force_flat_before: time = time(15, 55) # EOD 强平截止（收市前 5 分钟）

    def can_enter(self, t: time) -> bool:
        """当前时间是否允许开仓"""
        return self.entry_start <= t <= self.entry_end

    def must_exit(self, t: time) -> bool:
        """当前时间是否需要强制平仓"""
        return t >= self.force_flat_before

    def is_trading(self, t: time) -> bool:
        """当前时间是否在日内交易窗口（不含盘前盘后）"""
        return time(9, 30) <= t <= time(16, 0)