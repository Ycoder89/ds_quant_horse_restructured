"""
monitoring/regime_engine.py — ES/NQ 期货 Regime 分类器具体实现

两阶段分类：
  阶段 1: classify_premarket()
    - 输入：前 N 日日线 Bar
    - 指标：ADX(14), ATR 百分位, EMA(20) 斜率, 隔夜缺口大小
    - 输出：preliminary RegimeState (confirmed_at=None)

  阶段 2: confirm_postopen()
    - 输入：阶段1结果 + 开盘后前 30 分钟的 5min Bar
    - 指标：ORB 宽度/ATR 比, 方向偏置, 成交量比
    - 输出：confirmed RegimeState (confirmed_at=时间戳)

所有阈值从 config/regime.yaml 读取，不硬编码。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import yaml

from core.events import Bar
from core.indicators import adx as calc_adx, atr as calc_atr, ema as calc_ema
from core.regime import RegimeClassifier, RegimeState, RegimeType

logger = logging.getLogger(__name__)


def _percentile_rank(value: float, series: np.ndarray) -> float:
    """计算 value 在 series 中的百分位排名 [0, 1]"""
    if len(series) == 0:
        return 0.5
    return float(np.mean(series <= value))


def _bars_to_arrays(bars: list[Bar]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """将 Bar 列表转为 (high, low, close, volume) NumPy 数组"""
    highs  = np.array([b.high for b in bars],   dtype=float)
    lows   = np.array([b.low for b in bars],    dtype=float)
    closes = np.array([b.close for b in bars],  dtype=float)
    vols   = np.array([b.volume for b in bars], dtype=float)
    return highs, lows, closes, vols


class ESRegimeClassifier(RegimeClassifier):
    """
    ES/NQ 期货 Regime 分类器。

    实例化时从 config/regime.yaml 读取所有阈值参数。

    Usage:
        classifier = ESRegimeClassifier("config/regime.yaml")

        # 盘前分类（09:00 前）
        daily_bars = data_handler.get_daily_bars(today, lookback=30)
        preliminary = classifier.classify_premarket(daily_bars)

        # 盘后确认（~10:00，开盘 5+ 根 5min bar 后）
        open_bars = [b for b in today_bars if b.timestamp.time() < time(10, 0)]
        confirmed = classifier.confirm_postopen(preliminary, open_bars)
    """

    def __init__(self, config_path: str = "config/regime.yaml") -> None:
        self._cfg = self._load_config(config_path)

    # ─────────────────────────────────────────────────────────────────────────
    # 阶段 1: 盘前分类
    # ─────────────────────────────────────────────────────────────────────────

    def classify_premarket(self, daily_bars: list[Bar]) -> RegimeState:
        """
        基于前 N 日日线数据做盘前 Regime 初步分类。

        分类优先级（从高到低）：
          HIGH_VOL → TRENDING_BULL/BEAR → RANGING → LOW_VOL → CHOPPY → UNKNOWN
        """
        min_bars = self._cfg["adx_period"] + 5
        if len(daily_bars) < min_bars:
            logger.warning("[Regime] 盘前数据不足 %d 根日线（需要 %d）", len(daily_bars), min_bars)
            return RegimeState(
                regime_type=RegimeType.UNKNOWN,
                confidence=0.0,
                size_multiplier=self._cfg["size_multipliers"]["UNKNOWN"],
                indicators={"error": "insufficient_data", "bars": len(daily_bars)},
            )

        highs, lows, closes, _ = _bars_to_arrays(daily_bars)

        # 计算指标
        adx_series = calc_adx(highs, lows, closes, period=self._cfg["adx_period"])
        atr_series = calc_atr(highs, lows, closes, period=self._cfg["adx_period"])
        ema_series = calc_ema(closes, period=self._cfg["ema_period"])

        current_adx = float(adx_series[-1]) if not np.isnan(adx_series[-1]) else 0.0
        current_atr = float(atr_series[-1]) if not np.isnan(atr_series[-1]) else 0.0

        # ATR 百分位（相对近 lookback_days 日）
        lookback = self._cfg["lookback_days"]
        atr_window = atr_series[-lookback:]
        atr_window = atr_window[~np.isnan(atr_window)]
        atr_pct_rank = _percentile_rank(current_atr, atr_window)

        # EMA 斜率
        ema_slope = 0.0
        slope_n = self._cfg["ema_slope_lookback"]
        if not np.isnan(ema_series[-1]) and not np.isnan(ema_series[-slope_n - 1]):
            ema_slope = (float(ema_series[-1]) - float(ema_series[-slope_n - 1])) / slope_n

        # 隔夜缺口
        gap_pct = 0.0
        if len(daily_bars) >= 2 and daily_bars[-2].close > 0:
            gap_pct = (daily_bars[-1].open - daily_bars[-2].close) / daily_bars[-2].close

        indicators = {
            "adx": round(current_adx, 2),
            "atr": round(current_atr, 4),
            "atr_pct_rank": round(atr_pct_rank, 3),
            "ema_slope": round(ema_slope, 4),
            "gap_pct": round(gap_pct, 4),
        }

        regime_type, confidence = self._classify_from_indicators(
            adx=current_adx,
            atr_pct_rank=atr_pct_rank,
            ema_slope=ema_slope,
            gap_pct=gap_pct,
        )

        size_mult = self._cfg["size_multipliers"].get(regime_type.value, 1.0)

        state = RegimeState(
            regime_type=regime_type,
            confidence=confidence,
            size_multiplier=size_mult,
            confirmed_at=None,
            indicators=indicators,
        )
        logger.info("[Regime] 盘前分类: %s (conf=%.2f) | %s", regime_type.value, confidence,
                    " | ".join(f"{k}={v}" for k, v in indicators.items()))
        return state

    def _classify_from_indicators(
        self,
        adx: float,
        atr_pct_rank: float,
        ema_slope: float,
        gap_pct: float,
    ) -> tuple[RegimeType, float]:
        """
        核心分类决策逻辑（纯函数，便于测试）。

        返回：(RegimeType, confidence)
        """
        cfg = self._cfg

        # ── 高优先级：HIGH_VOL ────────────────────────────────────────────────
        if (atr_pct_rank >= cfg["atr_high_vol_percentile"]
                or abs(gap_pct) >= cfg["gap_high_vol_threshold"]):
            conf = max(atr_pct_rank, min(abs(gap_pct) / cfg["gap_high_vol_threshold"], 1.0))
            return RegimeType.HIGH_VOL, round(min(conf, 0.95), 2)

        # ── TRENDING_BULL ─────────────────────────────────────────────────────
        if (adx >= cfg["adx_trending_threshold"]
                and ema_slope >= cfg["ema_slope_up_threshold"]):
            conf = min((adx - cfg["adx_trending_threshold"]) / 20.0 + 0.6, 0.95)
            return RegimeType.TRENDING_BULL, round(conf, 2)

        # ── TRENDING_BEAR ─────────────────────────────────────────────────────
        if (adx >= cfg["adx_trending_threshold"]
                and ema_slope <= cfg["ema_slope_down_threshold"]):
            conf = min((adx - cfg["adx_trending_threshold"]) / 20.0 + 0.6, 0.95)
            return RegimeType.TRENDING_BEAR, round(conf, 2)

        # ── LOW_VOL ───────────────────────────────────────────────────────────
        if atr_pct_rank <= cfg["atr_low_vol_percentile"]:
            conf = 1.0 - atr_pct_rank / cfg["atr_low_vol_percentile"] * 0.4
            return RegimeType.LOW_VOL, round(conf, 2)

        # ── CHOPPY（高 ADX 但方向不明确）─────────────────────────────────────
        if (cfg["adx_ranging_threshold"] <= adx < cfg["adx_trending_threshold"]
                and abs(ema_slope) < cfg["ema_slope_up_threshold"]):
            return RegimeType.CHOPPY, 0.55

        # ── RANGING ───────────────────────────────────────────────────────────
        if adx < cfg["adx_ranging_threshold"]:
            conf = 1.0 - adx / cfg["adx_ranging_threshold"] * 0.3
            return RegimeType.RANGING, round(conf, 2)

        # ── BREAKOUT（ADX 由低向高加速，暂用简化判断）──────────────────────────
        return RegimeType.RANGING, 0.50

    # ─────────────────────────────────────────────────────────────────────────
    # 阶段 2: 盘后确认
    # ─────────────────────────────────────────────────────────────────────────

    def confirm_postopen(
        self,
        preliminary: RegimeState,
        open_bars: list[Bar],
    ) -> RegimeState:
        """
        用开盘 30 分钟行情确认或修正盘前结论。

        逻辑：
          - 计算 ORB 宽度、方向偏置、成交量比
          - 与盘前结论加权融合
          - 极端情况（BREAKOUT/CHOPPY 特征强烈）可推翻盘前结论
        """
        if len(open_bars) < self._cfg["open_bars_required"]:
            logger.warning(
                "[Regime] 盘后确认 bar 不足 %d/%d，保留盘前结论",
                len(open_bars), self._cfg["open_bars_required"],
            )
            return RegimeState(
                regime_type=preliminary.regime_type,
                confidence=preliminary.confidence * 0.8,  # 降低置信度
                size_multiplier=preliminary.size_multiplier,
                confirmed_at=datetime.now(),
                indicators=dict(preliminary.indicators, postopen="insufficient_bars"),
            )

        # 计算开盘行情特征
        orb_high = max(b.high for b in open_bars)
        orb_low  = min(b.low for b in open_bars)
        orb_width = orb_high - orb_low

        first_close = open_bars[-1].close
        first_open  = open_bars[0].open
        direction_move = first_close - first_open  # 正 = 多方向, 负 = 空方向

        # 方向偏置 = 方向移动 / ORB 宽度（归一化）
        direction_bias = direction_move / orb_width if orb_width > 0 else 0.0

        # 成交量比（需有均量参考，此处用开盘量 vs 所有 bar 的平均）
        open_vols = [b.volume for b in open_bars]
        avg_vol = sum(open_vols) / len(open_vols) if open_vols else 0
        first_vol = open_vols[0] if open_vols else 0
        vol_ratio = first_vol / avg_vol if avg_vol > 0 else 1.0

        # ATR 参考（从盘前 indicators 取，若无则估算）
        atr_ref = preliminary.indicators.get("atr", orb_width)
        orb_atr_ratio = orb_width / atr_ref if atr_ref > 0 else 1.0

        postopen_indicators = {
            "orb_high": round(orb_high, 2),
            "orb_low": round(orb_low, 2),
            "orb_width": round(orb_width, 4),
            "orb_atr_ratio": round(orb_atr_ratio, 3),
            "direction_bias": round(direction_bias, 3),
            "vol_ratio": round(vol_ratio, 3),
        }

        cfg = self._cfg
        confirmed_regime, confirmed_conf = self._confirm_from_open(
            preliminary=preliminary,
            direction_bias=direction_bias,
            orb_atr_ratio=orb_atr_ratio,
            vol_ratio=vol_ratio,
        )

        size_mult = cfg["size_multipliers"].get(confirmed_regime.value, 1.0)
        merged_indicators = {**preliminary.indicators, **postopen_indicators}

        state = RegimeState(
            regime_type=confirmed_regime,
            confidence=confirmed_conf,
            size_multiplier=size_mult,
            confirmed_at=datetime.now(),
            indicators=merged_indicators,
        )
        logger.info(
            "[Regime] 盘后确认: %s → %s (conf=%.2f) | bias=%.2f orb_atr=%.2f vol=%.2f",
            preliminary.regime_type.value, confirmed_regime.value, confirmed_conf,
            direction_bias, orb_atr_ratio, vol_ratio,
        )
        return state

    def _confirm_from_open(
        self,
        preliminary: RegimeState,
        direction_bias: float,
        orb_atr_ratio: float,
        vol_ratio: float,
    ) -> tuple[RegimeType, float]:
        """
        盘后确认决策逻辑（纯函数，便于测试）。

        可能推翻盘前结论的极端情况：
          - 放量 + ORB 宽 + 方向明确 → BREAKOUT
          - 极度无方向 + ORB 宽 → CHOPPY
          - 缩量 + ORB 窄 → LOW_VOL
        """
        cfg = self._cfg
        pre_type = preliminary.regime_type
        pre_conf = preliminary.confidence

        vol_spike = cfg["vol_spike_threshold"]
        vol_low   = cfg["vol_low_threshold"]
        orb_wide  = cfg["orb_wide_atr_ratio"]
        orb_narrow = cfg["orb_narrow_atr_ratio"]
        dir_strong = cfg["direction_strong_threshold"]
        dir_weak   = cfg["direction_weak_threshold"]

        # ── 强推翻：BREAKOUT 特征 ─────────────────────────────────────────────
        if (vol_ratio >= vol_spike and orb_atr_ratio >= orb_wide
                and abs(direction_bias) >= dir_strong):
            return RegimeType.BREAKOUT, 0.80

        # ── 强推翻：CHOPPY 特征（宽 ORB 但无方向）────────────────────────────
        if orb_atr_ratio >= orb_wide and abs(direction_bias) < dir_weak:
            return RegimeType.CHOPPY, 0.75

        # ── 修正：缩量 + 窄 ORB → LOW_VOL ────────────────────────────────────
        if vol_ratio <= vol_low and orb_atr_ratio <= orb_narrow:
            if pre_type not in (RegimeType.HIGH_VOL, RegimeType.CHOPPY):
                return RegimeType.LOW_VOL, 0.70

        # ── 趋势方向确认/修正 ────────────────────────────────────────────────
        pre_weight = cfg["premarket_weight"]
        post_weight = cfg["postopen_weight"]

        if pre_type is RegimeType.TRENDING_BULL:
            if direction_bias >= dir_strong:
                # 盘后确认趋势
                conf = pre_conf * pre_weight + 0.85 * post_weight
                return RegimeType.TRENDING_BULL, round(min(conf, 0.95), 2)
            elif direction_bias <= -dir_strong:
                # 方向反转 → 降级到 RANGING 或翻转
                return RegimeType.RANGING, 0.55

        if pre_type is RegimeType.TRENDING_BEAR:
            if direction_bias <= -dir_strong:
                conf = pre_conf * pre_weight + 0.85 * post_weight
                return RegimeType.TRENDING_BEAR, round(min(conf, 0.95), 2)
            elif direction_bias >= dir_strong:
                return RegimeType.RANGING, 0.55

        # ── 默认：保留盘前结论，融合置信度 ────────────────────────────────────
        open_conf_boost = min(abs(direction_bias) / dir_strong, 1.0) * 0.2
        blended_conf = pre_conf * pre_weight + (pre_conf + open_conf_boost) * post_weight
        return pre_type, round(min(blended_conf, 0.90), 2)

    # ─────────────────────────────────────────────────────────────────────────
    # 配置加载
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_config(config_path: str) -> dict:
        """加载 regime.yaml 配置文件"""
        try:
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            cfg = raw.get("regime_classifier", {})
        except FileNotFoundError:
            logger.warning("[Regime] 配置文件 %s 未找到，使用默认值", config_path)
            cfg = {}

        # 默认值（与 config/regime.yaml 对应）
        defaults = {
            "lookback_days": 30,
            "adx_period": 14,
            "adx_trending_threshold": 25.0,
            "adx_ranging_threshold": 20.0,
            "atr_high_vol_percentile": 0.80,
            "atr_low_vol_percentile": 0.25,
            "ema_period": 20,
            "ema_slope_lookback": 5,
            "ema_slope_up_threshold": 0.05,
            "ema_slope_down_threshold": -0.05,
            "gap_high_vol_threshold": 0.010,
            "gap_small_threshold": 0.003,
            "choppy_adx_threshold": 22.0,
            "choppy_direction_limit": 0.20,
            "open_bars_required": 5,
            "confirm_after_time": "10:00",
            "orb_wide_atr_ratio": 1.5,
            "orb_narrow_atr_ratio": 0.5,
            "direction_strong_threshold": 0.40,
            "direction_weak_threshold": 0.15,
            "vol_spike_threshold": 1.50,
            "vol_low_threshold": 0.70,
            "premarket_weight": 0.40,
            "postopen_weight": 0.60,
            "size_multipliers": {
                "TRENDING_BULL": 1.0,
                "TRENDING_BEAR": 1.0,
                "RANGING": 0.8,
                "HIGH_VOL": 0.5,
                "LOW_VOL": 0.7,
                "BREAKOUT": 1.0,
                "CHOPPY": 0.0,
                "UNKNOWN": 0.5,
            },
        }
        return {**defaults, **cfg}
