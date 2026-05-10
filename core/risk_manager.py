"""
core/risk_manager.py — RiskManager 基类 + 默认实现

cc_quant_horse 原版 RiskManager 存在以下问题：
  - 100% 直接传递信号（无入场过滤，信号来就下单）
  - calculate_quantity 散落在多个位置
  - 无日内风控（最大亏损、最大持仓次数等）

ds_quant_horse 重新设计：
  1. on_signal() 执行完整的风控流程：
     a. EntryFilter 链检查（VWAP / Volume / ADX / Regime / Spread）
     b. 风控状态检查（日内亏损上限、最大持仓次数、当前是否有持仓）
     c. 仓位计算（calculate_quantity）
     d. 发出 OrderEvent
  2. 支持的日内风控规则：
     - max_daily_loss_pct: 最大日内亏损（相对于账户净值的百分比）
     - max_positions_per_day: 每日最大开仓次数
     - max_concurrent_positions: 同时最大持仓数
     - require_no_position: 是否要求无持仓才能开仓（默认 True，适合日内）
  3. position_sizing:
     - FIXED: 每笔固定股数
     - FRACTION_ATR: 基于 ATR 的动态仓位（risk_per_trade / ATR）

RiskManager 负责接收 SignalEvent 并决定是否发出 OrderEvent。
策略不关心风控逻辑。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Optional

from core.events import EntryConditions, OrderEvent, OrderSide, OrderType, SignalEvent
from core.filters import EntryFilter, FilterChain, FilterContext, default_filter_registry
from core.exit import ExitSignal


# =============================================================================
# PositionSizing — 仓位计算方法
# =============================================================================

class PositionSizingMethod(Enum):
    FIXED = "FIXED"               # 固定股数
    FRACTION_ATR = "FRACTION_ATR" # 风险百分比 / ATR


@dataclass
class PositionSizing:
    """仓位计算配置"""
    method: PositionSizingMethod = PositionSizingMethod.FIXED
    fixed_quantity: int = 100                                      # FIXED 模式：固定股数
    risk_per_trade_pct: float = 0.01                               # FRACTION_ATR：每笔风险占净值比例（1%）
    account_value: float = 100_000.0                               # 账户净值
    min_quantity: int = 1
    max_quantity: int = 10_000

    def calculate(
        self,
        signal: SignalEvent,
        atr: float = 0.0,
    ) -> int:
        """
        根据当前信号和 ATR 计算仓位。

        FIXED:
            quantity = fixed_quantity

        FRACTION_ATR:
            risk_amount = account_value * risk_per_trade_pct
            stop_distance = abs(entry_price - stop_loss)
            quantity = risk_amount / stop_distance
        """
        if self.method is PositionSizingMethod.FIXED:
            return max(self.min_quantity, min(self.fixed_quantity, self.max_quantity))

        if self.method is PositionSizingMethod.FRACTION_ATR:
            if atr <= 0:
                # fallback: 用 stop_distance
                stop_distance = abs(signal.entry_price - signal.stop_loss)
                if stop_distance <= 0:
                    return self.fixed_quantity
                risk_amount = self.account_value * self.risk_per_trade_pct
                qty = int(risk_amount / stop_distance)
                return max(self.min_quantity, min(qty, self.max_quantity))

            risk_amount = self.account_value * self.risk_per_trade_pct
            qty = int(risk_amount / atr)
            return max(self.min_quantity, min(qty, self.max_quantity))

        return self.fixed_quantity


# =============================================================================
# RiskLimits — 风险管理规则
# =============================================================================

@dataclass
class RiskLimits:
    """日内风控限制"""
    max_daily_loss_pct: float = 0.05            # 最大日亏损：净值的 5%
    max_positions_per_day: int = 10             # 每日最大开仓次数
    max_concurrent_positions: int = 1           # 同时最大持仓数
    require_no_position: bool = True            # 是否要求无持仓才能开仓
    account_value: float = 100_000.0

    @property
    def max_daily_loss_amount(self) -> float:
        return self.account_value * self.max_daily_loss_pct


@dataclass
class RiskState:
    """日内风控状态（由 RiskManager 维护）"""
    date: date | None = None                     # None = 首次传入信号日期时自动设置
    daily_pnl: float = 0.0                       # 当日累计盈亏
    positions_opened_today: int = 0               # 当日已开仓次数
    is_halted: bool = False                       # 是否已被暂停（触及硬止损）
    halt_reason: str = ""

    def can_trade(self, limits: RiskLimits, has_position: bool) -> tuple[bool, str]:
        """检查是否允许新的入场"""
        if self.is_halted:
            return False, f"交易已暂停: {self.halt_reason}"
        if self.daily_pnl <= -limits.max_daily_loss_amount:
            return False, (
                f"日内亏损 {self.daily_pnl:.2f} 超过上限 "
                f"-{limits.max_daily_loss_amount:.2f}"
            )
        if self.positions_opened_today >= limits.max_positions_per_day:
            return False, (
                f"当日开仓 {self.positions_opened_today}/{limits.max_positions_per_day} "
                f"已达上限"
            )
        if limits.require_no_position and has_position:
            return False, "已有持仓，不允许重复入场"
        return True, ""

    def record_fill(self, pnl: float) -> None:
        """记录一笔成交的 PnL"""
        self.daily_pnl += pnl

    def record_open(self) -> None:
        """记录一次开仓"""
        self.positions_opened_today += 1

    def reset_daily(self, target_date: date | None = None) -> None:
        """跨日重置。仅在日期跨过已记录的交易日时才重置计数器。"""
        target = target_date or date.today()
        if self.date is None:
            # 首次设置日期，不重置（保留调用方已修改的状态）
            self.date = target
            return
        if self.date != target:
            self.date = target
            self.daily_pnl = 0.0
            self.positions_opened_today = 0
            self.is_halted = False
            self.halt_reason = ""


# =============================================================================
# RiskManager — 抽象基类
# =============================================================================

class RiskManager(ABC):
    """
    风控管理器抽象基类。

    流程：
      on_signal(signal) → (OrderEvent | None)
        1. 创建 FilterContext
        2. 运行 EntryFilter 链
        3. 检查风控状态
        4. 计算仓位
        5. 发出 OrderEvent
    """

    @abstractmethod
    def on_signal(self, signal: SignalEvent) -> Optional[OrderEvent]:
        """
        处理入场信号，返回订单事件或 None（拦截）。

        Args:
            signal: 策略发出的入场信号

        Returns:
            OrderEvent 如果信号通过所有风控检查，否则 None
        """
        ...

    @abstractmethod
    def on_fill(self, fill_value: float, pnl: float) -> None:
        """记录成交事件，更新风控状态"""
        ...

    @abstractmethod
    def on_exit(self, exit_signal: ExitSignal) -> Optional[OrderEvent]:
        """处理退出信号"""
        ...

    @property
    @abstractmethod
    def is_trading_allowed(self) -> bool:
        """当前是否允许交易"""
        ...

    @property
    @abstractmethod
    def state(self) -> RiskState:
        """当前风控状态"""
        ...


# =============================================================================
# DefaultRiskManager — 默认实现
# =============================================================================

class DefaultRiskManager(RiskManager):
    """
    默认风控管理器。

    组合：
      - EntryFilter 链（检查 VWAP/Volume/ADX/Regime/Spread）
      - RiskLimits 日内风控
      - PositionSizing 仓位计算
    """

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        sizing: Optional[PositionSizing] = None,
        filters: Optional[dict[str, EntryFilter]] = None,
        generate_risk_id: bool = True,
    ) -> None:
        self._limits = limits or RiskLimits()
        self._sizing = sizing or PositionSizing()
        self._filter_registry = filters if filters is not None else default_filter_registry()
        # Import here to avoid circular import
        self._state = RiskState()
        self._has_position: bool = False
        self._generate_risk_id = generate_risk_id
        self._id_counter: int = 0

    # ------- properties -------

    @property
    def is_trading_allowed(self) -> bool:
        return self._state.can_trade(self._limits, self._has_position)[0]

    @property
    def state(self) -> RiskState:
        return self._state

    # ------- signal processing -------

    def on_signal(self, signal: SignalEvent) -> Optional[OrderEvent]:
        """
        完整的风控流程：
          1. 跨日状态重置
          2. 创建 FilterContext
          3. 运行 EntryFilter 链
          4. 风控状态检查
          5. 仓位计算
          6. 发出 OrderEvent
        """
        # 0. 跨日重置（使用信号的日期而非系统今天）
        self._state.reset_daily(signal.timestamp.date())

        # 1. 构建过滤上下文
        ctx = self._build_filter_context(signal)

        # 2. 运行入场过滤链
        conditions = signal.entry_conditions
        if not conditions.is_empty():
            chain = FilterChain.from_conditions(conditions, self._filter_registry)
            ok, reason = chain.check(signal, ctx)
            if not ok:
                self._log_filter_block(signal, reason)
                return None

        # 3. 风控状态检查
        can_trade, reason = self._state.can_trade(
            self._limits, self._has_position,
        )
        if not can_trade:
            self._log_risk_block(signal, reason)
            return None

        # 4. 仓位计算
        atr = ctx.atr_14
        quantity = self._sizing.calculate(signal, atr)

        # 5. 发出订单
        self._state.record_open()
        self._id_counter += 1
        risk_id = f"R{self._id_counter:06d}" if self._generate_risk_id else ""

        return OrderEvent(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            strategy=signal.strategy,
            risk_id=risk_id,
        )

    def on_fill(self, fill_value: float, pnl: float) -> None:
        """记录成交"""
        self._state.record_fill(pnl)
        # 假设成交后持仓状态切换（简化：由 Portfolio 维护）
        self._has_position = True

    def on_exit(self, exit_signal: ExitSignal) -> Optional[OrderEvent]:
        """退出信号直接转为 OrderEvent"""
        self._id_counter += 1
        risk_id = f"R{self._id_counter:06d}" if self._generate_risk_id else ""
        return OrderEvent(
            symbol=exit_signal.symbol,
            side=exit_signal.side,
            quantity=0,  # 由 Portfolio 填入实际持仓量
            order_type=OrderType.MARKET,
            risk_id=risk_id,
        )

    def on_position_closed(self) -> None:
        """持仓已平仓通知"""
        self._has_position = False

    # ------- helpers -------

    def _build_filter_context(self, signal: SignalEvent) -> FilterContext:
        """
        构建 FilterContext。

        子类可覆盖以提供完整的 bar 数据、VWAP 等。
        默认实现只提供最小上下文。
        """
        return FilterContext(
            regime="UNKNOWN",
            vwap_daily=0.0,
            spread_pct=0.0,
            atr_14=abs(signal.entry_price - signal.stop_loss),
            latest_price=signal.entry_price,
        )

    @staticmethod
    def _log_filter_block(signal: SignalEvent, reason: str) -> None:
        import logging
        logger = logging.getLogger("ds_quant_horse.risk")
        logger.info(
            f"Signal BLOCKED by filter: {signal.symbol} {signal.side.value} "
            f"| {reason}"
        )

    @staticmethod
    def _log_risk_block(signal: SignalEvent, reason: str) -> None:
        import logging
        logger = logging.getLogger("ds_quant_horse.risk")
        logger.info(
            f"Signal BLOCKED by risk: {signal.symbol} {signal.side.value} "
            f"| {reason}"
        )