"""test_store.py — 回测结果存储测试"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.strategy_hunter.store import ResultRecord, ResultStore


class TestResultStore:
    @pytest.fixture
    def store(self, tmp_path: Path):
        return ResultStore(tmp_path / "test_hunter.db")

    def sample_record(self, score=5.0, trades=20, orb_minute=5):
        return ResultRecord(
            strategy="ORBEnhanced",
            timestamp="2025-01-01T00:00:00",
            params_json=json.dumps({"orb_start_minute": orb_minute}, sort_keys=True),
            sharpe_ratio=1.5,
            sortino_ratio=1.2,
            total_return_pct=5.0,
            max_drawdown_pct=3.0,
            win_rate=55.0,
            profit_factor=1.5,
            total_trades=trades,
            avg_daily_trades=2.5,
            composite_score=score,
            passed_threshold=1,
            total_bars=1000,
            trading_days=20,
        )

    def test_insert_and_count(self, store):
        store.insert(self.sample_record())
        assert store.count() == 1
        assert store.count("ORBEnhanced") == 1

    def test_dedup(self, store):
        store.insert(self.sample_record())
        store.insert(self.sample_record())  # same params
        assert store.count() == 1  # dedup

    def test_get_top(self, store):
        store.insert(self.sample_record(score=5.0, trades=20, orb_minute=5))
        store.insert(self.sample_record(score=8.0, trades=15, orb_minute=10))
        top = store.get_top(n=5)
        assert len(top) == 2
        assert top[0].composite_score == 8.0

    def test_get_top_min_trades(self, store):
        store.insert(self.sample_record(score=5.0, trades=20, orb_minute=5))
        store.insert(self.sample_record(score=8.0, trades=3, orb_minute=10))  # too few trades
        top = store.get_top(n=5, min_trades=10)
        assert len(top) == 1
        assert top[0].total_trades == 20

    def test_get_by_params(self, store):
        store.insert(self.sample_record())
        found = store.get_by_params("ORBEnhanced", {"orb_start_minute": 5})
        assert found is not None
        assert found.sharpe_ratio == 1.5

        missing = store.get_by_params("ORBEnhanced", {"orb_start_minute": 10})
        assert missing is None

    def test_summary(self, store):
        store.insert(self.sample_record())
        summary = store.summary()
        assert "Top 5" in summary
        assert "ORBEnhanced" in summary or "Score" in summary

    def test_params_property(self, store):
        store.insert(self.sample_record())
        top = store.get_top(n=1)
        assert top[0].params == {"orb_start_minute": 5}
