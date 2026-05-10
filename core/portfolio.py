"""
core/portfolio.py — 组合管理 + PnL 追踪

Portfolio 是持仓和 PnL 的唯一事实来源。
追踪：
  - 当前持仓（单标的，日内只允许一个方向）
  - 逐笔交易日志
  - 每日 PnL
  - 账户净值和现金余额
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from core.events import FillEvent, OrderSide

logger = logging.getLogger(__name__)


# =============================================================================
# Trade — 单笔交易记录
# =============================================================================

@dataclass
class Trade:
    """
    一笔完整的交易（开仓 → 平仓）。

    entry_* 和 exit_* 字段在开仓/平仓时分别填入。
    """
    symbol: str
    strategy: str

    # 开仓
    entry_time: datetime
    entry_side: OrderSide
    entry_price: float
    entry_quantity: int
    entry_risk_id: str = ""

    # 平仓（填入选入）
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_quantity: int = 0
    exit_risk_id: str = ""

    # 合约乘数（期货：ES=$50/pt, NQ=$20/pt；股票=1）
    contract_multiplier: float = 1.0

    # 计算属性
    @property
    def is_closed(self) -> bool:
        return self.exit_time is not None

    @property
    def pnl(self) -> Optional[float]:
        """交易盈亏（不含手续费）"""
        if not self.is_closed:
            return None
        qty = min(self.entry_quantity, self.exit_quantity)
        if self.entry_side is OrderSide.BUY:
            return (self.exit_price - self.entry_price) * qty * self.contract_multiplier
        else:
            return (self.entry_price - self.exit_price) * qty * self.contract_multiplier

    @property
    def pnl_pct(self) -> Optional[float]:
        """盈亏百分比"""
        if not self.is_closed or self.entry_price <= 0:
            return None
        return (self.pnl / (self.entry_price * self.entry_quantity)) * 100

    @property
    def holding_bars(self) -> Optional[int]:
        """持仓 bar 数（近似估算）"""
        if not self.is_closed:
            return None
        delta = self.exit_time - self.entry_time
        return int(delta.total_seconds() / 300)  # 5min bar

    @property
    def is_win(self) -> Optional[bool]:
        if self.pnl is None:
            return None
        return self.pnl > 0

    def __repr__(self) -> str:
        status = "OPEN" if not self.is_closed else f"CLOSED PnL={self.pnl:.2f}"
        return (
            f"Trade({self.symbol} {self.entry_side.value} "
            f"entry={self.entry_price:.2f}×{self.entry_quantity} "
            f"exit={self.exit_price or 0:.2f}×{self.exit_quantity} | {status})"
        )


# =============================================================================
# Position — 当前持仓
# =============================================================================

@dataclass
class Position:
    """当前持仓（简化版：日内单一方向）"""
    symbol: str
    quantity: int = 0            # 正=多 负=空 0=无
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    contract_multiplier: float = 1.0  # 期货合约乘数

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

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.avg_price

    def apply_fill(self, fill: FillEvent) -> None:
        """根据成交更新持仓"""
        qty = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity

        # 平仓或反向：先计算已实现盈亏
        if not self.is_flat and (self.quantity * qty) < 0:
            close_qty = min(abs(self.quantity), abs(qty))
            mult = self.contract_multiplier
            if self.quantity > 0:  # 平多
                self.realized_pnl += (fill.fill_price - self.avg_price) * close_qty * mult
            else:  # 平空
                self.realized_pnl += (self.avg_price - fill.fill_price) * close_qty * mult

            # 更新剩余数量
            if abs(qty) >= abs(self.quantity):
                self.quantity = 0
                self.avg_price = 0.0
                return
            else:
                self.quantity += qty
                return

        # 开仓：计算新均价
        if qty != 0:
            total_cost = self.avg_price * abs(self.quantity) + fill.fill_price * abs(qty)
            self.quantity += qty
            if self.quantity != 0:
                self.avg_price = total_cost / abs(self.quantity)
            else:
                self.avg_price = 0.0


# =============================================================================
# Portfolio — 组合管理
# =============================================================================

class Portfolio(ABC):
    """组合管理抽象基类"""

    @abstractmethod
    def on_fill(self, fill: FillEvent) -> None:
        """处理成交事件"""
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """获取某标的当前持仓"""
        ...

    @property
    @abstractmethod
    def positions(self) -> dict[str, Position]:
        """所有持仓"""
        ...

    @property
    @abstractmethod
    def trades(self) -> list[Trade]:
        """所有交易记录"""
        ...

    @property
    @abstractmethod
    def total_realized_pnl(self) -> float:
        """总已实现盈亏"""
        ...

    @property
    @abstractmethod
    def daily_pnl(self) -> float:
        """当日已实现盈亏"""
        ...

    @property
    @abstractmethod
    def account_value(self) -> float:
        """当前账户净值"""
        ...

    def on_start(self) -> None:
        pass

    def on_finish(self) -> None:
        pass


class SimplePortfolio(Portfolio):
    """
    简单组合管理实现（日内单标的）。

    设计约束：
      - 同时只允许一个方向持仓
      - 日内平仓后不再开仓（符合 ORB 等日内策略）
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        contract_multiplier: float = 1.0,
    ) -> None:
        self._initial_capital = initial_capital
        self._contract_multiplier = contract_multiplier
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._current_trade: Optional[Trade] = None
        self._daily_realized_pnl: float = 0.0
        self._current_date: Optional[date] = None

    # ------- properties -------

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def total_realized_pnl(self) -> float:
        return sum(t.pnl for t in self._trades if t.is_closed and t.pnl is not None)

    @property
    def daily_pnl(self) -> float:
        return self._daily_realized_pnl

    @property
    def account_value(self) -> float:
        return self._initial_capital + self.total_realized_pnl

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    # ------- fill processing -------

    def on_fill(self, fill: FillEvent) -> None:
        """处理成交：更新持仓 + 记录交易"""
        # 日期跟踪
        fill_date = fill.timestamp.date()
        if self._current_date is None:
            self._current_date = fill_date
        elif fill_date != self._current_date:
            # 跨日重置（日内策略不需要隔夜持仓）
            self._daily_realized_pnl = 0.0
            self._current_date = fill_date

        # 获取或创建持仓
        pos = self._positions.get(fill.symbol)
        if pos is None:
            pos = Position(symbol=fill.symbol, contract_multiplier=self._contract_multiplier)
            self._positions[fill.symbol] = pos

        # 记录盈亏
        old_realized = pos.realized_pnl

        # 开仓 vs 平仓判断
        is_entry = (
            pos.is_flat
            or (fill.side is OrderSide.BUY and pos.quantity >= 0)
            or (fill.side is OrderSide.SELL and pos.quantity <= 0)
        )

        pos.apply_fill(fill)

        # 更新 PnL
        pnl_change = pos.realized_pnl - old_realized
        self._daily_realized_pnl += pnl_change

        if is_entry:
            # 开仓：创建新交易记录
            if self._current_trade is not None and not self._current_trade.is_closed:
                # 上一个交易未关闭（反向开仓），先关闭它
                self._current_trade = None
            self._current_trade = Trade(
                symbol=fill.symbol,
                strategy=fill.strategy,
                entry_time=fill.timestamp,
                entry_side=fill.side,
                entry_price=fill.fill_price,
                entry_quantity=fill.quantity,
                entry_risk_id=fill.risk_id,
                contract_multiplier=self._contract_multiplier,
            )
        else:
            # 平仓或减仓：更新当前交易
            if self._current_trade is not None and not self._current_trade.is_closed:
                self._current_trade.exit_time = fill.timestamp
                self._current_trade.exit_price = fill.fill_price
                self._current_trade.exit_quantity = fill.quantity
                self._current_trade.exit_risk_id = fill.risk_id
                self._trades.append(self._current_trade)
                self._current_trade = None

                # 平仓后删除持仓
                if pos.is_flat:
                    del self._positions[fill.symbol]

        logger.debug(
            "Portfolio fill: %s %d×%s @ %.2f | daily_pnl=%.2f",
            fill.side.value, fill.quantity, fill.symbol,
            fill.fill_price, self._daily_realized_pnl,
        )

    def reset(self) -> None:
        """重置组合状态"""
        self._positions.clear()
        self._trades.clear()
        self._current_trade = None
        self._daily_realized_pnl = 0.0
        self._current_date = None
