"""
core/exit.py — 退出管理器（NEW for ds_quant_horse）

设计动机：
  cc_quant_horse 的止损/止盈逻辑分布在：
    - Strategy._bracket_active（策略感知 bracket 订单）
    - ExecutionHandler._submit_bracket（IB 原生的 bracket 订单）
    - Strategy.on_bar() 里的手工止损检查
  导致：
    - 策略与退出逻辑耦合（策略不需要知道 Bracket Stop 是否活跃）
    - 部分止损覆盖不到（Bracket Stop 超时撤销后无兜底）
    - 移动止损逻辑无法复用

ds_quant_horse 改为独立 ExitManager：
  1. 策略不管理退出，只提供 stop_loss
  2. ExitManager 在每根 bar 检查是否需要退出
  3. 退出信号同样走 RiskManager → OrderEvent 流程
  4. 支持三种止损类型，可组合

ExitManager 接口：
  - check_exit(position, current_bar, stop_loss) → (should_exit: bool, exit_price: float, reason: str)
  - on_fill(fill_event) — 成交后更新状态
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from core.events import Bar, FillEvent, OrderSide


# =============================================================================
# ExitType — 退出类型枚举
# =============================================================================

class ExitType(Enum):
    """退出原因"""
    STOP_LOSS = auto()      # 触及初始止损
    TRAILING_STOP = auto()  # 移动止损跟进
    TAKE_PROFIT = auto()    # 触及止盈
    TIME_STOP = auto()      # 时间止损（如最新入场时间后未盈利）
    MANUAL = auto()         # 手动 / EOD 强平
    REVERSAL = auto()       # 反向信号平仓


@dataclass(frozen=True)
class ExitSignal:
    """
    退出信号。

    与 SignalEvent 类似但表示'平仓'而非'开仓'。
    RiskManager 在检查到退出信号时按持仓方向发反向 OrderEvent。
    """
    symbol: str
    side: OrderSide            # 平仓方向（与持仓相反）
    exit_price: float          # 预期退出价格
    exit_type: ExitType        # 退出原因（日志/分析用）
    reason: str = ""           # 人类可读的退出原因

    def __repr__(self) -> str:
        return (
            f"Exit({self.exit_type.name} {self.side.value} {self.symbol} "
            f"@{self.exit_price:.2f} | {self.reason})"
        )


# =============================================================================
# Position — 持仓快照（ExitManager 需要的最小信息）
# =============================================================================

@dataclass
class Position:
    """当前持仓的最小表示"""
    symbol: str
    quantity: int              # 正数 = 多，负数 = 空，0 = 无
    avg_entry_price: float
    entry_bar_index: int = 0   # 入场时的 bar 序号（时间止损用）

    @property
    def side(self) -> Optional[OrderSide]:
        if self.quantity > 0:
            return OrderSide.BUY
        elif self.quantity < 0:
            return OrderSide.SELL
        return None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    def is_long(self) -> bool:
        return self.quantity > 0

    def is_short(self) -> bool:
        return self.quantity < 0


# =============================================================================
# ExitManager — 抽象基类
# =============================================================================

class ExitManager(ABC):
    """
    退出管理器抽象基类。

    策略不管理退出，只提供初始 stop_loss。
    ExitManager 每 bar 被调用一次，返回是否需要退出。

    Usage:
        exit_mgr = CompositeExitManager([
            FixedStopExit(initial_stop, atr_mult=1.5),
            TrailingStopExit(activation_r=0.5, trail_distance=0.5),
            TimeStopExit(max_bars=12),
        ])
        should_exit, exit_signal, new_stop = exit_mgr.check(position, bar)
    """

    @abstractmethod
    def check(
        self,
        position: Position,
        bar: Bar,
        current_stop: Optional[float] = None,
    ) -> tuple[bool, Optional[ExitSignal], Optional[float]]:
        """
        检查是否需要退出。

        Args:
            position: 当前持仓
            bar:       当前最新 bar
            current_stop: 当前活跃的止损价（可以被 TrailingStop 更新）

        Returns:
            (should_exit, exit_signal, new_stop)
              - should_exit=True 时 exit_signal 不为 None
              - new_stop 为 TrailingStop 更新后的止损价（None 表示不变）
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """退出管理器名称（日志用）"""
        ...


# =============================================================================
# 具体退出管理器实现
# =============================================================================

class FixedStopExit(ExitManager):
    """固定止损：初始止损 × ATR 倍数"""

    name = "FixedStop"

    def __init__(self, atr_mult: float = 1.5) -> None:
        self.atr_mult = atr_mult

    def check(
        self,
        position: Position,
        bar: Bar,
        current_stop: Optional[float] = None,
    ) -> tuple[bool, Optional[ExitSignal], Optional[float]]:
        stop_price = current_stop
        if stop_price is None:
            return False, None, None

        if position.is_long():
            if bar.low <= stop_price:
                exit_price = min(bar.open, stop_price)  # 开盘跳空可能穿透
                return True, ExitSignal(
                    symbol=position.symbol,
                    side=OrderSide.SELL,
                    exit_price=exit_price,
                    exit_type=ExitType.STOP_LOSS,
                    reason=f"止损触发: low={bar.low:.2f} ≤ stop={stop_price:.2f}",
                ), None
        elif position.is_short():
            if bar.high >= stop_price:
                exit_price = max(bar.open, stop_price)
                return True, ExitSignal(
                    symbol=position.symbol,
                    side=OrderSide.BUY,
                    exit_price=exit_price,
                    exit_type=ExitType.STOP_LOSS,
                    reason=f"止损触发: high={bar.high:.2f} ≥ stop={stop_price:.2f}",
                ), None

        return False, None, None


class TrailingStopExit(ExitManager):
    """
    移动止损：盈利达到 activation_R 后启动，回撤 trail_distance 退出。

    例：R=1.0, activation=0.5R, trail=0.2R
      - 价格到达 +0.5R 时激活移动止损
      - 之后价格回撤 0.2R 退出
      - 止损价只向有利方向移动
    """

    name = "TrailingStop"

    def __init__(
        self,
        activation_r: float = 0.5,   # 激活阈值（以 R 为单位）
        trail_distance: float = 0.3,  # 回撤距离（以 R 为单位）
    ) -> None:
        self.activation_r = activation_r
        self.trail_distance = trail_distance
        self._activated: bool = False
        self._best_price: float = 0.0
        self._stop_price: float = 0.0
        self._last_entry: float = 0.0

    def check(
        self,
        position: Position,
        bar: Bar,
        current_stop: Optional[float] = None,
    ) -> tuple[bool, Optional[ExitSignal], Optional[float]]:
        if position.is_flat:
            self._reset()
            return False, None, None

        entry = position.avg_entry_price
        if entry <= 0 or current_stop is None:
            return False, None, None

        # 检测新入场，重置状态
        if self._last_entry != entry:
            self._reset()
            self._last_entry = entry

        # 初始风险 (1R)
        initial_risk = abs(entry - current_stop)
        if initial_risk <= 0:
            return False, None, None

        # 当前盈亏（以 R 为单位）
        if position.is_long():
            current_r = (bar.close - entry) / initial_risk
        else:
            current_r = (entry - bar.close) / initial_risk

        # 激活检查
        if not self._activated:
            if current_r >= self.activation_r:
                self._activated = True
                self._best_price = bar.close
                self._stop_price = bar.close - self.trail_distance * initial_risk
            return False, None, None

        # 已激活：更新最优价和止损价
        if position.is_long():
            if bar.high > self._best_price:
                self._best_price = bar.high
                self._stop_price = self._best_price - self.trail_distance * initial_risk
            # 止损触发：low <= stop_price
            if bar.low <= self._stop_price:
                exit_price = min(bar.open, self._stop_price)
                return True, ExitSignal(
                    symbol=position.symbol,
                    side=OrderSide.SELL,
                    exit_price=exit_price,
                    exit_type=ExitType.TRAILING_STOP,
                    reason=(
                        f"移动止损触发: low={bar.low:.2f} ≤ trail_stop={self._stop_price:.2f} "
                        f"(best={self._best_price:.2f}, R={current_r:.2f})"
                    ),
                ), None
        else:
            if bar.low < self._best_price:  # 空头 best_price 是最低价
                self._best_price = bar.low
                self._stop_price = self._best_price + self.trail_distance * initial_risk
            if bar.high >= self._stop_price:
                exit_price = max(bar.open, self._stop_price)
                return True, ExitSignal(
                    symbol=position.symbol,
                    side=OrderSide.BUY,
                    exit_price=exit_price,
                    exit_type=ExitType.TRAILING_STOP,
                    reason=(
                        f"移动止损触发: high={bar.high:.2f} ≥ trail_stop={self._stop_price:.2f} "
                        f"(best={self._best_price:.2f}, R={current_r:.2f})"
                    ),
                ), None

        return False, None, self._stop_price

    def _reset(self) -> None:
        self._activated = False
        self._best_price = 0.0
        self._stop_price = 0.0


class TimeStopExit(ExitManager):
    """
    时间止损：持仓超过 max_bars 根 K 线后，在收盘价退出。

    适用于日内：入场后市场不配合，到时间强制退出，避免过夜风险。

    内部通过比较 avg_entry_price 检测新入场，自动重置计数。
    """

    name = "TimeStop"

    def __init__(self, max_bars: int = 12) -> None:
        self.max_bars = max_bars
        self._bar_count: int = 0
        self._last_entry: Optional[float] = None

    def check(
        self,
        position: Position,
        bar: Bar,
        current_stop: Optional[float] = None,
    ) -> tuple[bool, Optional[ExitSignal], Optional[float]]:
        if position.is_flat:
            self._bar_count = 0
            self._last_entry = None
            return False, None, None

        # 检测新入场（avg_entry_price 变化 → 旧持仓已平，新持仓已开）
        if self._last_entry != position.avg_entry_price:
            self._bar_count = 0
            self._last_entry = position.avg_entry_price

        self._bar_count += 1
        if self._bar_count >= self.max_bars:
            exit_side = OrderSide.SELL if position.is_long() else OrderSide.BUY
            return True, ExitSignal(
                symbol=position.symbol,
                side=exit_side,
                exit_price=bar.close,
                exit_type=ExitType.TIME_STOP,
                reason=f"时间止损: {self._bar_count}/{self.max_bars} bar，收盘退出",
            ), None
        return False, None, None


class TakeProfitExit(ExitManager):
    """
    固定止盈：盈利达到 risk_reward_r × R 后退出。

    计算方式：
      - 1R = abs(entry_price - initial_stop)
      - 止盈价 = entry_price ± risk_reward × 1R
    """

    name = "TakeProfit"

    def __init__(self, risk_reward: float = 2.0) -> None:
        self.risk_reward = risk_reward

    def check(
        self,
        position: Position,
        bar: Bar,
        current_stop: Optional[float] = None,
    ) -> tuple[bool, Optional[ExitSignal], Optional[float]]:
        if position.is_flat or current_stop is None:
            return False, None, None

        entry = position.avg_entry_price
        initial_risk = abs(entry - current_stop)
        if initial_risk <= 0:
            return False, None, None

        if position.is_long():
            target_price = entry + self.risk_reward * initial_risk
            if bar.high >= target_price:
                return True, ExitSignal(
                    symbol=position.symbol,
                    side=OrderSide.SELL,
                    exit_price=target_price,
                    exit_type=ExitType.TAKE_PROFIT,
                    reason=f"止盈触发: high={bar.high:.2f} ≥ target={target_price:.2f} (R={self.risk_reward:.1f})",
                ), None
        else:
            target_price = entry - self.risk_reward * initial_risk
            if bar.low <= target_price:
                return True, ExitSignal(
                    symbol=position.symbol,
                    side=OrderSide.BUY,
                    exit_price=target_price,
                    exit_type=ExitType.TAKE_PROFIT,
                    reason=f"止盈触发: low={bar.low:.2f} ≤ target={target_price:.2f} (R={self.risk_reward:.1f})",
                ), None

        return False, None, None


# =============================================================================
# CompositeExitManager — 组合退出管理器
# =============================================================================

class CompositeExitManager(ExitManager):
    """
    组合多个退出管理器，任一触发即退出。

    注意：ExitManager 之间可能有交互——
      - TrailingStop 更新止损价
      - FixedStop 用更新后的止损价检查

    所以按顺序调用，current_stop 在管理器之间传递。
    """

    name = "Composite"

    def __init__(self, exits: list[ExitManager]) -> None:
        self._exits = exits

    def check(
        self,
        position: Position,
        bar: Bar,
        current_stop: Optional[float] = None,
    ) -> tuple[bool, Optional[ExitSignal], Optional[float]]:
        new_stop = current_stop
        for exit_mgr in self._exits:
            should_exit, exit_sig, updated_stop = exit_mgr.check(
                position, bar, new_stop,
            )
            if updated_stop is not None:
                new_stop = updated_stop
            if should_exit and exit_sig:
                return True, exit_sig, None
        return False, None, new_stop