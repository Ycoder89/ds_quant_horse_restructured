"""
core/execution.py — ExecutionHandler 接口 + 模拟实现

ExecutionHandler 负责将 OrderEvent 转换为 FillEvent。
模拟实现 SimulatedExecutionHandler：
  - 在下一根 bar 开盘价成交
  - 可选滑点模型（固定 tick / 百分比）
  - 可选部分成交
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from core.events import Bar, FillEvent, OrderEvent, OrderSide

logger = logging.getLogger(__name__)


# =============================================================================
# ExecutionHandler — 抽象基类
# =============================================================================

class ExecutionHandler(ABC):
    """执行器接口：OrderEvent → FillEvent"""

    @abstractmethod
    def execute_order(self, order: OrderEvent, bar: Bar) -> Optional[FillEvent]:
        """
        执行订单并返回成交结果。

        Args:
            order: 订单事件
            bar:   当前最新 bar（用于模拟成交价）

        Returns:
            FillEvent 如果成交，None 如果未成交
        """
        ...

    def on_start(self) -> None:
        """执行器初始化"""
        pass

    def on_finish(self) -> None:
        """执行器清理"""
        pass


# =============================================================================
# SimulatedExecutionHandler — 模拟成交
# =============================================================================

@dataclass
class SlippageModel:
    """滑点模型"""
    fixed_ticks: int = 0           # 固定 tick 滑点（0 = 无滑点）
    pct_slippage: float = 0.0      # 百分比滑点（0.0005 = 0.05%）
    fill_on_next_bar: bool = True  # True=下根bar开盘价成交, False=当前bar收盘价

    def apply(self, price: float, side: OrderSide) -> float:
        """根据买卖方向应用滑点"""
        if self.fixed_ticks > 0:
            tick_size = price * 0.01  # 近似 tick（美股 $0.01）
            slippage = self.fixed_ticks * tick_size
        else:
            slippage = price * self.pct_slippage

        if side is OrderSide.BUY:
            return price + slippage  # 买=更贵
        return price - slippage      # 卖=更便宜


class SimulatedExecutionHandler(ExecutionHandler):
    """
    模拟执行器。

    在每根 bar 到来时检查是否有挂单，如有则在当前 bar 价格成交。
    支持两种模式：
      - next_bar_open: 下一根 bar 的开盘价成交（默认，更保守）
      - current_bar_close: 当前 bar 收盘价成交（更乐观）
    """

    def __init__(
        self,
        slippage: Optional[SlippageModel] = None,
        partial_fill_pct: float = 1.0,       # 1.0 = 全部成交
    ) -> None:
        self._slippage = slippage or SlippageModel(fixed_ticks=1)
        self._partial_fill_pct = partial_fill_pct
        self._pending_orders: list[OrderEvent] = []
        self._fill_count: int = 0

    # ------- 订单管理 -------

    def submit(self, order: OrderEvent) -> None:
        """提交订单（等待下次 on_bar 成交）"""
        self._pending_orders.append(order)
        logger.debug("订单入队: %s", order)

    def has_pending(self) -> bool:
        return len(self._pending_orders) > 0

    @property
    def pending_count(self) -> int:
        return len(self._pending_orders)

    # ------- 执行 -------

    def execute_order(self, order: OrderEvent, bar: Bar) -> Optional[FillEvent]:
        """
        立即执行一笔订单（不排队）。
        用于退出单等需要立即成交的场景。
        """
        fill_price = self._resolve_fill_price(order, bar)
        if fill_price is None:
            return None

        qty = int(order.quantity * self._partial_fill_pct)
        if qty <= 0:
            return None

        self._fill_count += 1
        return FillEvent(
            timestamp=bar.timestamp,
            symbol=order.symbol,
            side=order.side,
            quantity=qty,
            fill_price=round(fill_price, 2),
            strategy=order.strategy,
            risk_id=order.risk_id,
        )

    def process_bar(self, bar: Bar) -> list[FillEvent]:
        """
        处理当前 bar：将所有挂单按当前价格成交。

        Args:
            bar: 当前 bar

        Returns:
            本次成交的 FillEvent 列表
        """
        fills: list[FillEvent] = []
        remaining: list[OrderEvent] = []

        for order in self._pending_orders:
            fill = self.execute_order(order, bar)
            if fill is not None:
                fills.append(fill)
                logger.info("成交: %s @ %.2f (qty=%d)", fill.side.value, fill.fill_price, fill.quantity)
            else:
                remaining.append(order)

        self._pending_orders = remaining
        return fills

    # ------- 辅助 -------

    def _resolve_fill_price(self, order: OrderEvent, bar: Bar) -> Optional[float]:
        """确定成交价"""
        base_price = bar.open if self._slippage.fill_on_next_bar else bar.close
        if base_price <= 0:
            return None
        return self._slippage.apply(base_price, order.side)

    def reset(self) -> None:
        self._pending_orders.clear()
        self._fill_count = 0
