"""
tests/test_monitoring/test_regime.py — Regime 核心单元测试

TDD: 先测试核心逻辑，再验证分类器行为。
覆盖：
  - RegimeType 枚举完整性
  - RegimeState 属性和策略允许逻辑
  - ESRegimeClassifier._classify_from_indicators() 各分支
  - ESRegimeClassifier._confirm_from_open() 各推翻场景
"""
from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pytest

from core.regime import (
    REGIME_STRATEGY_MATRIX,
    RegimeClassifier,
    RegimeState,
    RegimeType,
)


# =============================================================================
# RegimeType 基本测试
# =============================================================================

class TestRegimeType:
    def test_all_types_exist(self):
        expected = {"TRENDING_BULL", "TRENDING_BEAR", "RANGING",
                    "HIGH_VOL", "LOW_VOL", "BREAKOUT", "CHOPPY", "UNKNOWN"}
        actual = {r.value for r in RegimeType}
        assert actual == expected

    def test_strategy_matrix_covers_all_types(self):
        """所有 RegimeType 都在策略矩阵中有条目"""
        for rt in RegimeType:
            assert rt in REGIME_STRATEGY_MATRIX, f"{rt} 不在 REGIME_STRATEGY_MATRIX"


# =============================================================================
# RegimeState 测试
# =============================================================================

class TestRegimeState:

    def test_default_state_is_unknown(self):
        state = RegimeState()
        assert state.regime_type == RegimeType.UNKNOWN
        assert state.confidence == 0.0

    def test_can_trade_choppy_false(self):
        state = RegimeState(regime_type=RegimeType.CHOPPY, size_multiplier=0.0)
        assert not state.can_trade

    def test_can_trade_trending_true(self):
        state = RegimeState(
            regime_type=RegimeType.TRENDING_BULL,
            size_multiplier=1.0,
        )
        assert state.can_trade

    def test_can_trade_unknown_depends_on_size_mult(self):
        state_zero = RegimeState(regime_type=RegimeType.UNKNOWN, size_multiplier=0.0)
        state_half = RegimeState(regime_type=RegimeType.UNKNOWN, size_multiplier=0.5)
        assert not state_zero.can_trade
        assert state_half.can_trade

    def test_is_confirmed_false_before_postopen(self):
        state = RegimeState(regime_type=RegimeType.RANGING, confirmed_at=None)
        assert not state.is_confirmed

    def test_is_confirmed_true_after_postopen(self):
        state = RegimeState(
            regime_type=RegimeType.RANGING,
            confirmed_at=datetime(2026, 5, 10, 10, 0),
        )
        assert state.is_confirmed

    def test_allows_strategy_blocked(self):
        state = RegimeState(
            regime_type=RegimeType.RANGING,
            blocked_strategies=["orb_enhanced"],
            preferred_strategies=["vwap_reversion"],
        )
        assert not state.allows_strategy("orb_enhanced")

    def test_allows_strategy_not_in_preferred(self):
        state = RegimeState(
            regime_type=RegimeType.RANGING,
            blocked_strategies=[],
            preferred_strategies=["vwap_reversion"],
        )
        assert not state.allows_strategy("orb_enhanced")
        assert state.allows_strategy("vwap_reversion")

    def test_allows_strategy_no_preferred_any_allowed(self):
        state = RegimeState(
            regime_type=RegimeType.TRENDING_BULL,
            blocked_strategies=["vwap_reversion"],
            preferred_strategies=[],
        )
        assert state.allows_strategy("orb_enhanced")
        assert not state.allows_strategy("vwap_reversion")

    def test_auto_fill_from_matrix_trending_bull(self):
        """__post_init__ 应从矩阵自动填充 preferred/blocked"""
        # 手动设置 size_multiplier 以外的字段，让 preferred/blocked 从矩阵取
        state = RegimeState(
            regime_type=RegimeType.TRENDING_BULL,
            confidence=0.8,
            size_multiplier=1.0,
        )
        assert "orb_enhanced" in state.preferred_strategies
        assert "vwap_reversion" in state.blocked_strategies

    def test_auto_fill_choppy_size_zero(self):
        """CHOPPY 矩阵中 size_mult=0，preferred 为空"""
        state = RegimeState(regime_type=RegimeType.CHOPPY)
        assert state.size_multiplier == 0.0
        assert not state.preferred_strategies
        assert not state.can_trade

    def test_repr_contains_regime_type(self):
        state = RegimeState(regime_type=RegimeType.HIGH_VOL, confidence=0.7)
        r = repr(state)
        assert "HIGH_VOL" in r
        assert "0.70" in r


# =============================================================================
# ESRegimeClassifier 分类逻辑测试（隔离内部方法）
# =============================================================================

class TestESRegimeClassifierLogic:
    """测试分类器核心纯函数，不依赖文件系统或真实数据"""

    @pytest.fixture
    def classifier(self, tmp_path):
        """使用 tmp_path 创建无实际 YAML 的分类器（走默认值）"""
        from monitoring.regime_engine import ESRegimeClassifier
        # 传入不存在的路径，会走默认值
        return ESRegimeClassifier(config_path=str(tmp_path / "regime.yaml"))

    # ── 盘前分类逻辑 ───────────────────────────────────────────────────────────

    def test_high_vol_by_atr_percentile(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=22.0,
            atr_pct_rank=0.85,  # > 0.80 → HIGH_VOL
            ema_slope=0.1,
            gap_pct=0.005,
        )
        assert rt == RegimeType.HIGH_VOL
        assert conf > 0.5

    def test_high_vol_by_gap(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=18.0,
            atr_pct_rank=0.5,
            ema_slope=0.02,
            gap_pct=0.015,  # > 0.01 → HIGH_VOL
        )
        assert rt == RegimeType.HIGH_VOL

    def test_trending_bull(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=30.0,          # > 25
            atr_pct_rank=0.5,
            ema_slope=0.08,    # > 0.05
            gap_pct=0.002,
        )
        assert rt == RegimeType.TRENDING_BULL
        assert conf >= 0.6

    def test_trending_bear(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=28.0,
            atr_pct_rank=0.5,
            ema_slope=-0.09,   # < -0.05
            gap_pct=-0.003,
        )
        assert rt == RegimeType.TRENDING_BEAR

    def test_low_vol(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=15.0,
            atr_pct_rank=0.15,  # < 0.25 → LOW_VOL
            ema_slope=0.01,
            gap_pct=0.001,
        )
        assert rt == RegimeType.LOW_VOL

    def test_ranging(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=15.0,           # < 20 → RANGING
            atr_pct_rank=0.5,
            ema_slope=0.02,
            gap_pct=0.003,
        )
        assert rt == RegimeType.RANGING

    def test_choppy(self, classifier):
        rt, conf = classifier._classify_from_indicators(
            adx=21.0,           # 20 <= ADX < 25
            atr_pct_rank=0.5,
            ema_slope=0.01,     # abs < 0.05 → 无明确方向
            gap_pct=0.002,
        )
        assert rt == RegimeType.CHOPPY

    # ── 盘后确认逻辑 ───────────────────────────────────────────────────────────

    def test_postopen_breakout_pushes_through(self, classifier):
        pre = RegimeState(regime_type=RegimeType.RANGING, confidence=0.6)
        rt, conf = classifier._confirm_from_open(
            preliminary=pre,
            direction_bias=0.55,   # > 0.40 strong
            orb_atr_ratio=1.8,     # > 1.5 wide
            vol_ratio=1.8,         # > 1.5 spike
        )
        assert rt == RegimeType.BREAKOUT
        assert conf >= 0.7

    def test_postopen_choppy_wide_no_direction(self, classifier):
        pre = RegimeState(regime_type=RegimeType.TRENDING_BULL, confidence=0.7)
        rt, conf = classifier._confirm_from_open(
            preliminary=pre,
            direction_bias=0.05,   # < 0.15 weak
            orb_atr_ratio=1.8,     # > 1.5 wide
            vol_ratio=1.0,
        )
        assert rt == RegimeType.CHOPPY

    def test_postopen_low_vol_narrow_low_volume(self, classifier):
        pre = RegimeState(regime_type=RegimeType.RANGING, confidence=0.6)
        rt, conf = classifier._confirm_from_open(
            preliminary=pre,
            direction_bias=0.2,
            orb_atr_ratio=0.3,    # < 0.5 narrow
            vol_ratio=0.5,        # < 0.7 low
        )
        assert rt == RegimeType.LOW_VOL

    def test_postopen_confirms_trending_bull(self, classifier):
        pre = RegimeState(regime_type=RegimeType.TRENDING_BULL, confidence=0.7)
        rt, conf = classifier._confirm_from_open(
            preliminary=pre,
            direction_bias=0.5,   # > 0.40 confirms bull
            orb_atr_ratio=1.0,
            vol_ratio=1.0,
        )
        assert rt == RegimeType.TRENDING_BULL
        assert conf > 0.7

    def test_postopen_reverses_trending_bull_on_bear_open(self, classifier):
        pre = RegimeState(regime_type=RegimeType.TRENDING_BULL, confidence=0.7)
        rt, conf = classifier._confirm_from_open(
            preliminary=pre,
            direction_bias=-0.5,  # Strong downward → override to RANGING
            orb_atr_ratio=1.0,
            vol_ratio=1.0,
        )
        assert rt == RegimeType.RANGING

    def test_postopen_insufficient_bars(self, tmp_path):
        """bar 不足时应返回降低置信度的盘前结论"""
        from monitoring.regime_engine import ESRegimeClassifier
        classifier = ESRegimeClassifier(config_path=str(tmp_path / "regime.yaml"))
        pre = RegimeState(regime_type=RegimeType.RANGING, confidence=0.7)
        result = classifier.confirm_postopen(preliminary=pre, open_bars=[])
        assert result.regime_type == RegimeType.RANGING
        assert result.confidence < 0.7  # 降低了置信度
        assert result.is_confirmed  # 仍然标记为 confirmed
