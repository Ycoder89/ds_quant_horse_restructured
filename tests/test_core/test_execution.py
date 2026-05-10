"""test_execution.py — ExecutionHandler 接口 + 模拟成交测试"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.events import Bar, FillEvent, OrderEvent, OrderSide, OrderType
from core.execution import SimulatedExecutionHandler, SlippageModel


class TestSimulatedExecution:
    @pytest.fixture
    def exe(self):
        return SimulatedExecutionHandler(
            slippage=SlippageModel(fixed_ticks=0),
            partial_fill_pct=1.0,
        )

    @pytest.fixture
    def bar(self):
        return Bar(datetime(2025, 9, 15, 9, 35), open=400.0, high=405.0, low=398.0, close=403.0, volume=5000)

    @pytest.fixture
    def buy_order(self):
        return OrderEvent(symbol="TSLA", side=OrderSide.BUY, quantity=100, strategy="TEST")

    @pytest.fixture
    def sell_order(self):
        return OrderEvent(symbol="TSLA", side=OrderSide.SELL, quantity=100, strategy="TEST")

    def test_submit_and_process(self, exe, buy_order, bar):
        exe.submit(buy_order)
        assert exe.has_pending()
        assert exe.pending_count == 1

        fills = exe.process_bar(bar)
        assert len(fills) == 1
        assert fills[0].symbol == "TSLA"
        assert fills[0].quantity == 100
        assert fills[0].fill_price == 400.0  # bar.open
        assert not exe.has_pending()

    def test_execute_order_immediate(self, exe, buy_order, bar):
        fill = exe.execute_order(buy_order, bar)
        assert fill is not None
        assert fill.symbol == "TSLA"
        assert fill.quantity == 100

    def test_slippage_buy(self, bar):
        exe = SimulatedExecutionHandler(
            slippage=SlippageModel(fixed_ticks=1),
        )
        fill = exe.execute_order(
            OrderEvent(symbol="TSLA", side=OrderSide.BUY, quantity=100),
            bar,
        )
        assert fill is not None
        assert fill.fill_price > 400.0  # buy slippage adds

    def test_slippage_sell(self, bar):
        exe = SimulatedExecutionHandler(
            slippage=SlippageModel(fixed_ticks=1),
        )
        fill = exe.execute_order(
            OrderEvent(symbol="TSLA", side=OrderSide.SELL, quantity=100),
            bar,
        )
        assert fill is not None
        assert fill.fill_price < 400.0  # sell slippage subtracts

    def test_partial_fill(self, bar):
        exe = SimulatedExecutionHandler(partial_fill_pct=0.5)
        fill = exe.execute_order(
            OrderEvent(symbol="TSLA", side=OrderSide.BUY, quantity=100),
            bar,
        )
        assert fill is not None
        assert fill.quantity == 50

    def test_reset(self, exe, buy_order, bar):
        exe.submit(buy_order)
        exe.reset()
        assert not exe.has_pending()
        assert exe.pending_count == 0

    def test_multiple_orders(self, exe, buy_order, sell_order, bar):
        exe.submit(buy_order)
        exe.submit(sell_order)
        assert exe.pending_count == 2

        fills = exe.process_bar(bar)
        assert len(fills) == 2

    def test_pct_slippage(self, bar):
        exe = SimulatedExecutionHandler(
            slippage=SlippageModel(pct_slippage=0.001),  # 0.1%
        )
        fill = exe.execute_order(
            OrderEvent(symbol="TSLA", side=OrderSide.BUY, quantity=100),
            bar,
        )
        assert fill is not None
        expected = 400.0 * 1.001
        assert fill.fill_price == pytest.approx(expected, rel=0.01)

    def test_fill_has_correct_fields(self, exe, bar):
        order = OrderEvent(
            symbol="AAPL", side=OrderSide.SELL, quantity=50,
            strategy="ORB", risk_id="R000001",
        )
        fill = exe.execute_order(order, bar)
        assert fill is not None
        assert fill.symbol == "AAPL"
        assert fill.side is OrderSide.SELL
        assert fill.quantity == 50
        assert fill.strategy == "ORB"
        assert fill.risk_id == "R000001"
