"""test_param_grid.py — 参数网格定义测试"""
from __future__ import annotations

from research.strategy_hunter.param_grid import OrbParamGrid, SwingTrendParamGrid


class TestOrbParamGrid:
    def test_default_count(self):
        grid = OrbParamGrid()
        assert grid.count() == 3 * 3 * 3 * 3 * 3  # 243

    def test_quick_count(self):
        grid = OrbParamGrid.quick()
        assert grid.count() == 2 * 2 * 2 * 1 * 2  # 16

    def test_focused_count(self):
        grid = OrbParamGrid.focused()
        assert grid.count() == 5 * 5 * 4 * 4 * 4  # 1600

    def test_iterate_returns_dicts(self):
        from dataclasses import dataclass, field
        from research.strategy_hunter.param_grid import ParamGrid
        @dataclass
        class MiniGrid(ParamGrid):
            a: list[int] = field(default_factory=lambda: [1, 2])
            b: list[str] = field(default_factory=lambda: ["x"])
        grid = MiniGrid()
        results = list(grid.iterate())
        assert len(results) == 2
        assert results[0]["a"] == 1
        assert results[0]["b"] == "x"
        assert results[1]["a"] == 2


class TestSwingTrendParamGrid:
    def test_count(self):
        grid = SwingTrendParamGrid()
        assert grid.count() == 3 * 3 * 3 * 3

    def test_iterate(self):
        grid = SwingTrendParamGrid(
            ema_fast=[5, 10],
            ema_slow=[20],
            swing_period=[5],
            breakout_threshold_pct=[0.5],
        )
        results = list(grid.iterate())
        assert len(results) == 2
