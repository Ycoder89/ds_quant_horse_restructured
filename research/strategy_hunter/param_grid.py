"""
research/strategy_hunter/param_grid.py — 策略参数网格定义

定义策略的搜索空间。每个策略模板有对应的 ParamGrid。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterator


@dataclass
class ParamGrid:
    """
    参数网格基类。

    每个字段是一个列表，表示该参数的候选值。
    iterate() 返回所有参数组合的字典。
    """
    def iterate(self) -> Iterator[dict[str, Any]]:
        """迭代所有参数组合"""
        names, values = zip(*self._items())
        for combo in product(*values):
            yield dict(zip(names, combo))

    def count(self) -> int:
        """参数组合总数"""
        _, values = zip(*self._items())
        n = 1
        for v in values:
            n *= len(v)
        return n

    def _items(self) -> list[tuple[str, list]]:
        return [
            (k, v) for k, v in self.__dict__.items()
            if isinstance(v, list) and len(v) > 0
        ]


@dataclass
class OrbParamGrid(ParamGrid):
    """
    ORB Enhanced 策略参数网格。

    默认值覆盖合理的日内 ORB 参数范围。
    """
    orb_start_minute: list[int] = field(default_factory=lambda: [5, 10, 15])
    entry_break_confidence: list[float] = field(default_factory=lambda: [0.1, 0.3, 0.5])
    volume_spike_mult: list[float] = field(default_factory=lambda: [1.0, 1.2, 1.5])
    adx_threshold: list[float] = field(default_factory=lambda: [15, 20, 25])
    atr_mult_stop: list[float] = field(default_factory=lambda: [1.0, 1.5, 2.0])

    @classmethod
    def quick(cls) -> OrbParamGrid:
        """快速扫描（少量组合）"""
        return cls(
            orb_start_minute=[5, 15],
            entry_break_confidence=[0.2, 0.4],
            volume_spike_mult=[1.0, 1.5],
            adx_threshold=[20],
            atr_mult_stop=[1.5, 2.0],
        )

    @classmethod
    def focused(cls) -> OrbParamGrid:
        """针对已知有效范围的精细扫描"""
        return cls(
            orb_start_minute=[5, 8, 10, 12, 15],
            entry_break_confidence=[0.15, 0.2, 0.25, 0.3, 0.35],
            volume_spike_mult=[1.0, 1.1, 1.2, 1.3],
            adx_threshold=[18, 20, 22, 25],
            atr_mult_stop=[1.2, 1.5, 1.8, 2.0],
        )


@dataclass
class SwingTrendParamGrid(ParamGrid):
    """SwingTrend 策略参数网格"""
    ema_fast: list[int] = field(default_factory=lambda: [5, 8, 10])
    ema_slow: list[int] = field(default_factory=lambda: [13, 20, 34])
    swing_period: list[int] = field(default_factory=lambda: [5, 8, 10])
    breakout_threshold_pct: list[float] = field(default_factory=lambda: [0.3, 0.5, 0.8])


@dataclass
class VWAPReversionParamGrid(ParamGrid):
    """VWAP 均值回归策略参数网格"""
    deviation_threshold: list[float] = field(default_factory=lambda: [1.0, 1.5, 2.0, 2.5])
    reversal_body_pct: list[float] = field(default_factory=lambda: [50.0, 65.0, 80.0])
    volume_spike_mult: list[float] = field(default_factory=lambda: [1.0, 1.2, 1.5])
    atr_mult_stop: list[float] = field(default_factory=lambda: [1.0, 1.5, 2.0])

    @classmethod
    def quick(cls) -> VWAPReversionParamGrid:
        return cls(
            deviation_threshold=[1.5, 2.0, 2.5],
            reversal_body_pct=[50.0, 80.0],
            volume_spike_mult=[1.0, 1.5],
            atr_mult_stop=[1.5, 2.0],
        )


@dataclass
class PullbackEMAParamGrid(ParamGrid):
    """Pullback EMA 趋势跟踪策略参数网格"""
    ema_fast: list[int] = field(default_factory=lambda: [5, 8, 13, 20])
    ema_slow: list[int] = field(default_factory=lambda: [21, 34, 50])
    trend_slope_pct: list[float] = field(default_factory=lambda: [0.05, 0.1, 0.2])
    pullback_max_pct: list[float] = field(default_factory=lambda: [0.3, 0.5, 1.0])
    atr_mult_stop: list[float] = field(default_factory=lambda: [1.0, 1.5, 2.0])

    @classmethod
    def quick(cls) -> PullbackEMAParamGrid:
        return cls(
            ema_fast=[5, 13],
            ema_slow=[21, 50],
            trend_slope_pct=[0.05, 0.2],
            pullback_max_pct=[0.3, 1.0],
            atr_mult_stop=[1.5, 2.0],
        )
