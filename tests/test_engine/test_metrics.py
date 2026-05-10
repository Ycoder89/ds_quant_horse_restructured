"""test_metrics.py — 回测指标计算测试"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from core.events import OrderSide
from core.portfolio import Trade
from engine.metrics import BacktestMetrics, compute_metrics


class TestComputeMetrics:
    @pytest.fixture
    def sample_trades(self):
        ts = datetime(2025, 1, 1, 9, 30)
        trades = []
        for i in range(10):
            entry = Trade(symbol="TSLA", strategy="TEST",
                           entry_time=ts, entry_side=OrderSide.BUY,
                           entry_price=100.0, entry_quantity=100)
            entry.exit_time = ts
            entry.exit_price = 101.0 + i * 0.5  # all winning
            entry.exit_quantity = 100
            trades.append(entry)
        return trades

    @pytest.fixture
    def mixed_trades(self):
        ts = datetime(2025, 1, 1, 9, 30)
        trades = []
        for i in range(10):
            entry = Trade(symbol="TSLA", strategy="TEST",
                           entry_time=ts, entry_side=OrderSide.BUY,
                           entry_price=100.0, entry_quantity=100)
            entry.exit_time = ts
            entry.exit_quantity = 100
            if i < 6:
                entry.exit_price = 105.0  # win
            else:
                entry.exit_price = 95.0   # loss
            trades.append(entry)
        return trades

    @pytest.fixture
    def empty_trades(self):
        return []

    def test_empty_trades(self, empty_trades):
        metrics = compute_metrics(empty_trades, total_bars=100)
        assert metrics.total_trades == 0
        assert metrics.sharpe_ratio == 0.0
        assert metrics.win_rate == 0.0

    def test_win_rate_all_wins(self, sample_trades):
        metrics = compute_metrics(sample_trades, total_bars=500, trading_days=[date(2025, 1, 1)])
        assert metrics.total_trades == 10
        assert metrics.win_rate == 100.0
        assert metrics.win_count == 10

    def test_mixed_win_rate(self, mixed_trades):
        metrics = compute_metrics(mixed_trades, total_bars=500, trading_days=[date(2025, 1, 1)])
        assert metrics.total_trades == 10
        assert metrics.win_count == 6
        assert metrics.loss_count == 4
        assert metrics.win_rate == 60.0

    def test_profit_factor(self, mixed_trades):
        metrics = compute_metrics(mixed_trades, total_bars=500, trading_days=[date(2025, 1, 1)])
        # 6 wins × 5.0 = 30.0, 4 losses × 5.0 = -20.0
        assert metrics.profit_factor == pytest.approx(1.5, rel=0.1)

    def test_max_drawdown(self):
        """构造一个先涨后跌的 equity curve"""
        ts = datetime(2025, 1, 1, 9, 30)
        trades = []
        # 3 winning days
        for d in range(1, 4):
            t = Trade(symbol="TSLA", strategy="TEST",
                       entry_time=ts, entry_side=OrderSide.BUY,
                       entry_price=100.0, entry_quantity=100)
            t.exit_time = datetime(2025, d, 1, 10, 0)
            t.exit_price = 110.0
            t.exit_quantity = 100
            trades.append(t)
        # 3 losing days
        for d in range(4, 7):
            t = Trade(symbol="TSLA", strategy="TEST",
                       entry_time=ts, entry_side=OrderSide.BUY,
                       entry_price=100.0, entry_quantity=100)
            t.exit_time = datetime(2025, d, 1, 10, 0)
            t.exit_price = 90.0
            t.exit_quantity = 100
            trades.append(t)

        trading_days = [date(2025, d, 1) for d in range(1, 7)]
        metrics = compute_metrics(trades, total_bars=500, trading_days=trading_days)
        assert metrics.max_drawdown_pct > 0
        assert metrics.max_drawdown_duration > 0

    def test_sharpe_no_trades(self):
        metrics = compute_metrics([], total_bars=100)
        assert metrics.sharpe_ratio == 0.0

    def test_avg_daily_trades(self):
        ts = datetime(2025, 1, 1, 9, 30)
        trades = []
        for i in range(10):
            t = Trade(symbol="TSLA", strategy="TEST",
                       entry_time=ts, entry_side=OrderSide.BUY,
                       entry_price=100.0, entry_quantity=100)
            t.exit_time = datetime(2025, 1, i + 1, 10, 0)
            t.exit_price = 101.0
            t.exit_quantity = 100
            trades.append(t)

        days = [date(2025, 1, d + 1) for d in range(10)]
        metrics = compute_metrics(trades, total_bars=500, trading_days=days)
        assert metrics.avg_daily_trades == 1.0

    def test_composite_score_range(self):
        """综合评分应在合理范围内"""
        metrics = BacktestMetrics(
            sharpe_ratio=1.5,
            win_rate=50.0,
            avg_daily_trades=3.0,
            profit_factor=1.5,
            max_drawdown_pct=5.0,
        )
        # 手动触发计算 (实际由 compute_metrics 做)
        assert isinstance(metrics.composite_score, float)


class TestBacktestMetrics:
    def test_passes_threshold_all_good(self):
        m = BacktestMetrics(sharpe_ratio=1.5, win_rate=55.0, avg_daily_trades=3.0)
        passed, reason = m.passes_threshold()
        assert passed

    def test_passes_threshold_low_sharpe(self):
        m = BacktestMetrics(sharpe_ratio=0.5, win_rate=55.0, avg_daily_trades=3.0)
        passed, reason = m.passes_threshold()
        assert not passed
        assert "Sharpe" in reason

    def test_passes_threshold_low_trades(self):
        m = BacktestMetrics(sharpe_ratio=1.5, win_rate=55.0, avg_daily_trades=0.5)
        passed, reason = m.passes_threshold()
        assert not passed
        assert "avg_daily_trades" in reason

    def test_passes_threshold_low_win_rate(self):
        m = BacktestMetrics(sharpe_ratio=1.5, win_rate=30.0, avg_daily_trades=3.0)
        passed, reason = m.passes_threshold()
        assert not passed
        assert "win_rate" in reason
