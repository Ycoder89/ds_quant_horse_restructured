"""test_portfolio.py — Portfolio + Position + Trade 测试"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.events import FillEvent, OrderSide
from core.portfolio import Position, SimplePortfolio, Trade


class TestPosition:
    def test_default_flat(self):
        pos = Position(symbol="TSLA")
        assert pos.is_flat
        assert pos.side is None
        assert pos.notional == 0.0

    def test_apply_buy_fill(self):
        pos = Position(symbol="TSLA")
        fill = FillEvent(datetime(2025, 1, 1, 9, 30), "TSLA", OrderSide.BUY, 100, 400.0)
        pos.apply_fill(fill)
        assert not pos.is_flat
        assert pos.quantity == 100
        assert pos.side is OrderSide.BUY
        assert pos.avg_price == 400.0
        assert pos.notional == 40000.0

    def test_apply_sell_fill(self):
        pos = Position(symbol="TSLA")
        fill = FillEvent(datetime(2025, 1, 1, 9, 30), "TSLA", OrderSide.SELL, 100, 400.0)
        pos.apply_fill(fill)
        assert pos.quantity == -100
        assert pos.side is OrderSide.SELL

    def test_apply_multiple_fills(self):
        pos = Position(symbol="TSLA")
        pos.apply_fill(FillEvent(datetime(2025, 1, 1, 9, 30), "TSLA", OrderSide.BUY, 100, 400.0))
        pos.apply_fill(FillEvent(datetime(2025, 1, 1, 9, 31), "TSLA", OrderSide.BUY, 50, 410.0))
        assert pos.quantity == 150
        expected_avg = (400.0 * 100 + 410.0 * 50) / 150
        assert pos.avg_price == pytest.approx(expected_avg)

    def test_close_position(self):
        pos = Position(symbol="TSLA")
        pos.apply_fill(FillEvent(datetime(2025, 1, 1, 9, 30), "TSLA", OrderSide.BUY, 100, 400.0))
        pos.apply_fill(FillEvent(datetime(2025, 1, 1, 10, 0), "TSLA", OrderSide.SELL, 100, 410.0))
        assert pos.is_flat
        assert pos.realized_pnl == pytest.approx(1000.0)  # (410-400)*100

    def test_partial_close(self):
        pos = Position(symbol="TSLA")
        pos.apply_fill(FillEvent(datetime(2025, 1, 1, 9, 30), "TSLA", OrderSide.BUY, 100, 400.0))
        pos.apply_fill(FillEvent(datetime(2025, 1, 1, 10, 0), "TSLA", OrderSide.SELL, 40, 410.0))
        assert pos.quantity == 60
        assert pos.realized_pnl == pytest.approx(400.0)  # (410-400)*40


class TestTrade:
    def test_open_trade(self):
        t = Trade(symbol="TSLA", strategy="ORB",
                   entry_time=datetime(2025, 1, 1, 9, 30),
                   entry_side=OrderSide.BUY,
                   entry_price=400.0, entry_quantity=100)
        assert not t.is_closed
        assert t.pnl is None
        assert t.is_win is None

    def test_closed_winning_trade(self):
        t = Trade(symbol="TSLA", strategy="ORB",
                   entry_time=datetime(2025, 1, 1, 9, 30),
                   entry_side=OrderSide.BUY,
                   entry_price=400.0, entry_quantity=100)
        t.exit_time = datetime(2025, 1, 1, 10, 0)
        t.exit_price = 410.0
        t.exit_quantity = 100
        assert t.is_closed
        assert t.pnl == pytest.approx(1000.0)
        assert t.is_win is True
        assert t.pnl_pct == pytest.approx(2.5)  # 1000/40000*100

    def test_closed_losing_short_trade(self):
        t = Trade(symbol="TSLA", strategy="ORB",
                   entry_time=datetime(2025, 1, 1, 9, 30),
                   entry_side=OrderSide.SELL,
                   entry_price=400.0, entry_quantity=100)
        t.exit_time = datetime(2025, 1, 1, 10, 0)
        t.exit_price = 410.0
        t.exit_quantity = 100
        assert t.is_closed
        assert t.pnl == pytest.approx(-1000.0)  # 做空亏损
        assert t.is_win is False


class TestSimplePortfolio:
    @pytest.fixture
    def portfolio(self):
        return SimplePortfolio(initial_capital=100_000.0)

    def make_fill(self, symbol, side, qty, price, timestamp=None, risk_id=""):
        return FillEvent(
            timestamp=timestamp or datetime(2025, 1, 1, 9, 30),
            symbol=symbol, side=side, quantity=qty,
            fill_price=price, strategy="TEST", risk_id=risk_id,
        )

    def test_initial_state(self, portfolio):
        assert portfolio.account_value == 100_000.0
        assert portfolio.total_realized_pnl == 0.0
        assert len(portfolio.trades) == 0

    def test_buy_and_sell(self, portfolio):
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.BUY, 100, 400.0))
        pos = portfolio.get_position("TSLA")
        assert pos is not None
        assert pos.quantity == 100

        portfolio.on_fill(self.make_fill("TSLA", OrderSide.SELL, 100, 410.0))
        assert portfolio.get_position("TSLA") is None  # 已平仓
        assert len(portfolio.trades) == 1
        assert portfolio.trades[0].pnl == pytest.approx(1000.0)
        assert portfolio.total_realized_pnl == pytest.approx(1000.0)
        assert portfolio.account_value == pytest.approx(101_000.0)

    def test_losing_trade(self, portfolio):
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.BUY, 100, 400.0))
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.SELL, 100, 390.0))
        assert portfolio.trades[0].pnl == pytest.approx(-1000.0)
        assert portfolio.total_realized_pnl == pytest.approx(-1000.0)

    def test_no_cross_day_pnl_leak(self, portfolio):
        """跨日 PnL 应该重置"""
        day1 = datetime(2025, 1, 1, 9, 30)
        day2 = datetime(2025, 1, 2, 9, 30)

        # Day 1: win
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.BUY, 100, 400.0, day1))
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.SELL, 100, 410.0, day1))
        assert portfolio.daily_pnl == pytest.approx(1000.0)

        # Day 2: loss (daily_pnl should reset)
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.BUY, 100, 400.0, day2))
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.SELL, 100, 390.0, day2))
        assert portfolio.daily_pnl == pytest.approx(-1000.0)  # 跨日重置了
        assert portfolio.total_realized_pnl == pytest.approx(0.0)  # 累计为0

    def test_reset(self, portfolio):
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.BUY, 100, 400.0))
        portfolio.on_fill(self.make_fill("TSLA", OrderSide.SELL, 100, 410.0))
        portfolio.reset()
        assert len(portfolio.trades) == 0
        assert portfolio.total_realized_pnl == 0.0
        assert portfolio.account_value == 100_000.0
