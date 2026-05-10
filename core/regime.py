"""
core/regime.py — Regime 分类器核心定义

设计原则：
  1. RegimeType 枚举：7 种市场状态 + UNKNOWN
  2. RegimeState dataclass：当日 Regime 快照（每日更新两次）
  3. RegimeClassifier ABC：定义盘前 + 盘后两阶段分类接口

两阶段确认流程：
  阶段 1 (09:00-09:25): classify_premarket() 用前 N 日日线数据给出初步判断
  阶段 2 (09:55-10:05): confirm_postopen() 用开盘 30 分钟行情确认或修正

RegimeState 注入 FilterContext，策略通过 RegimeFilter 自动生效。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from core.events import Bar


# =============================================================================
# RegimeType 枚举
# =============================================================================

class RegimeType(Enum):
    """
    7 种市场状态 + UNKNOWN（数据不足 / 盘前状态）。

    分类依据：
      - ADX(14):  趋势强度
      - ATR 百分位：波动水平（相对近 30 日）
      - EMA(20) 斜率：方向偏置
      - 开盘缺口：隔夜情绪
      - ORB 宽度 / 方向偏置（盘后确认）
    """
    TRENDING_BULL = "TRENDING_BULL"   # 强上升趋势（ADX>25, EMA向上, 连续高点）
    TRENDING_BEAR = "TRENDING_BEAR"   # 强下降趋势（ADX>25, EMA向下, 连续低点）
    RANGING       = "RANGING"         # 震荡区间（ADX<20, 价格围绕EMA振荡）
    HIGH_VOL      = "HIGH_VOL"        # 高波动（ATR百分位>80%, 或缺口>1%）
    LOW_VOL       = "LOW_VOL"         # 低波动（ATR百分位<25%, 日内振幅极小）
    BREAKOUT      = "BREAKOUT"        # 突破转换（区间收敛后放量突破）
    CHOPPY        = "CHOPPY"          # 混乱无方向（高ATR但无趋势, 多次反转）
    UNKNOWN       = "UNKNOWN"         # 数据不足或尚未确认


# =============================================================================
# 策略矩阵（Regime → 推荐/禁用策略 + 仓位系数）
# =============================================================================

REGIME_STRATEGY_MATRIX: dict[RegimeType, dict] = {
    RegimeType.TRENDING_BULL: {
        "preferred": ["orb_enhanced", "swingtrend"],
        "blocked":   ["vwap_reversion"],
        "size_mult": 1.0,
        "note":      "只做多突破，禁止均值回归",
    },
    RegimeType.TRENDING_BEAR: {
        "preferred": ["orb_enhanced", "swingtrend"],
        "blocked":   ["vwap_reversion"],
        "size_mult": 1.0,
        "note":      "只做空突破，禁止均值回归",
    },
    RegimeType.RANGING: {
        "preferred": ["vwap_reversion", "pullback_ema"],
        "blocked":   ["orb_enhanced"],
        "size_mult": 0.8,
        "note":      "目标缩小至 1R，禁止 ORB 趋势跟踪",
    },
    RegimeType.HIGH_VOL: {
        "preferred": ["orb_enhanced"],
        "blocked":   ["vwap_reversion", "pullback_ema"],
        "size_mult": 0.5,
        "note":      "仓位减半，ORB 须方向确认，止损翻倍",
    },
    RegimeType.LOW_VOL: {
        "preferred": ["vwap_reversion"],
        "blocked":   ["swingtrend"],
        "size_mult": 0.7,
        "note":      "小目标快速兑现，不做趋势跟踪",
    },
    RegimeType.BREAKOUT: {
        "preferred": ["orb_enhanced"],
        "blocked":   ["vwap_reversion"],
        "size_mult": 1.0,
        "note":      "等待方向确认后全力参与 ORB",
    },
    RegimeType.CHOPPY: {
        "preferred": [],
        "blocked":   ["orb_enhanced", "swingtrend", "vwap_reversion", "pullback_ema"],
        "size_mult": 0.0,
        "note":      "当日不交易",
    },
    RegimeType.UNKNOWN: {
        "preferred": [],
        "blocked":   [],
        "size_mult": 0.5,
        "note":      "数据不足，谨慎减仓",
    },
}


# =============================================================================
# RegimeState — 当日 Regime 快照
# =============================================================================

@dataclass
class RegimeState:
    """
    当日 Regime 状态快照，由 RegimeClassifier 产出，每日最多更新两次。

    第一次更新：盘前分类（09:00-09:25），置 confirmed_at=None
    第二次更新：盘后确认（~10:00），置 confirmed_at=时间戳
    """
    regime_type: RegimeType = RegimeType.UNKNOWN
    confidence: float = 0.0               # 分类置信度 [0, 1]
    size_multiplier: float = 0.5          # 仓位调节系数
    preferred_strategies: list[str] = field(default_factory=list)
    blocked_strategies: list[str] = field(default_factory=list)
    confirmed_at: Optional[datetime] = None   # None = 仅盘前，有时间戳 = 盘后已确认
    # 诊断信息：分类时计算的各指标值
    indicators: dict = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        # 如果未手动设置策略偏好，从策略矩阵自动填充
        if not self.preferred_strategies and not self.blocked_strategies:
            matrix = REGIME_STRATEGY_MATRIX.get(self.regime_type, {})
            self.preferred_strategies = list(matrix.get("preferred", []))
            self.blocked_strategies = list(matrix.get("blocked", []))
            if self.size_multiplier == 0.5 and self.regime_type is not RegimeType.UNKNOWN:
                self.size_multiplier = matrix.get("size_mult", 1.0)
            if not self.note:
                self.note = matrix.get("note", "")

    @property
    def is_confirmed(self) -> bool:
        """是否已经过盘后确认"""
        return self.confirmed_at is not None

    @property
    def can_trade(self) -> bool:
        """当前 Regime 是否允许任何交易"""
        return (
            self.regime_type is not RegimeType.CHOPPY
            and self.size_multiplier > 0
        )

    def allows_strategy(self, strategy_name: str) -> bool:
        """
        判断指定策略在当前 Regime 下是否允许运行。

        逻辑：
          1. 被禁用 → False
          2. 有推荐列表且不在其中 → False
          3. 否则 → True
        """
        if strategy_name in self.blocked_strategies:
            return False
        if self.preferred_strategies and strategy_name not in self.preferred_strategies:
            return False
        return True

    def __repr__(self) -> str:
        status = "confirmed" if self.is_confirmed else "preliminary"
        return (
            f"RegimeState({self.regime_type.value}, "
            f"conf={self.confidence:.2f}, "
            f"size={self.size_multiplier:.1f}x, "
            f"{status})"
        )


# =============================================================================
# RegimeClassifier — 抽象基类
# =============================================================================

class RegimeClassifier(ABC):
    """
    Regime 分类器抽象基类。

    子类实现两阶段分类：
      1. classify_premarket(): 盘前，基于日线历史数据
      2. confirm_postopen():   盘后，基于开盘 30 分钟行情

    子类通过 YAML 配置文件读取阈值，不硬编码。
    """

    @abstractmethod
    def classify_premarket(self, daily_bars: list[Bar]) -> RegimeState:
        """
        盘前 Regime 分类（每日 09:00-09:25 调用）。

        Args:
            daily_bars: 前 N 个交易日的日线 Bar 列表（从旧到新）

        Returns:
            RegimeState（confirmed_at=None，表示尚未盘后确认）
        """
        ...

    @abstractmethod
    def confirm_postopen(
        self,
        preliminary: RegimeState,
        open_bars: list[Bar],
    ) -> RegimeState:
        """
        盘后确认（每日 ~10:00 调用，开盘后前 5-6 根 5min bar 到达后）。

        Args:
            preliminary: 盘前分类结果
            open_bars:   开盘后前 30 分钟的 5min Bar 列表

        Returns:
            RegimeState（confirmed_at 已设置，表示盘后确认完成）
        """
        ...

    def get_regime_for_backtest(
        self,
        daily_bars: list[Bar],
        open_bars: list[Bar],
    ) -> RegimeState:
        """
        回测用：一次性完成两阶段分类（避免重复调用）。

        对于每日 bar 循环中，在第一根 post-open bar 到达时调用。
        """
        preliminary = self.classify_premarket(daily_bars)
        if len(open_bars) >= 4:
            return self.confirm_postopen(preliminary, open_bars)
        return preliminary
