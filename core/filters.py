"""
core/filters.py — 入场过滤链（NEW for ds_quant_horse）

设计动机：
  cc_quant_horse 的入场过滤逻辑散布在 Strategy.on_bar()、RiskManager.on_signal()、
  以及各个策略子类里，导致：
    - 策略逻辑与过滤逻辑耦合
    - 过滤条件不可复用（ORB 的 VWAP 逻辑不能给 SwingTrend 用）
    - 测试困难（要 mock 整个策略对象才能测单个过滤条件）

ds_quant_horse 改为责任链模式：
  1. 策略在 SignalEvent.entry_conditions 中声明需要的过滤条件
  2. RiskManager.on_signal() 用 EntryFilter 链逐一检查
  3. 所有条件通过 → 发出 OrderEvent；任一不通过 → 拦截

EntryFilter 接口：
  - filter(signal, bars, context) → (approved: bool, reason: str)
  - context 包含当前 regime、最新价格、账户信息等运行时数据
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from core.events import Bar, EntryConditions, OrderSide, SignalEvent

if TYPE_CHECKING:
    from core.regime import RegimeState


# =============================================================================
# FilterContext — 过滤上下文
# =============================================================================

@dataclass
class FilterContext:
    """
    运行时环境快照，传给每个 EntryFilter。

    字段：
      bars:         最近 N 根 5min K 线（从最新到最旧）
      regime_state: 当日 Regime 状态（RegimeState，含 regime_type / size_multiplier / preferred 等）
      vwap_daily:   日内实时 VWAP 均值
      spread_pct:   当前 bid-ask 价差百分比
      atr_14:       当前 14 周期 ATR（平均真实波幅）
      latest_price: 最新成交价

    向后兼容：regime 属性返回 regime_state.regime_type.value 字符串。
    """
    bars: list[Bar] = field(default_factory=list)
    regime_state: Optional["RegimeState"] = None  # None 时 regime 属性返回 "UNKNOWN"
    vwap_daily: float = 0.0
    spread_pct: float = 0.0
    atr_14: float = 0.0
    latest_price: float = 0.0

    @property
    def regime(self) -> str:
        """向后兼容：返回 regime_type 的字符串值"""
        if self.regime_state is None:
            return "UNKNOWN"
        return self.regime_state.regime_type.value

    @property
    def size_multiplier(self) -> float:
        """当前 Regime 的仓位调节系数"""
        if self.regime_state is None:
            return 1.0
        return self.regime_state.size_multiplier

    @property
    def can_trade(self) -> bool:
        """当前 Regime 是否允许任何交易"""
        if self.regime_state is None:
            return True  # 无 Regime 信息时不阻止（默认允许）
        return self.regime_state.can_trade


# =============================================================================
# EntryFilter — 抽象基类
# =============================================================================

class EntryFilter(ABC):
    """
    入场过滤器抽象基类。

    每个子类负责检查一个维度：
      - VWAP 方向确认
      - Volume 放量确认
      - ADX 趋势强度确认
      - Regime 允许/禁止
      - 价差过滤

    Usage:
        chain = FilterChain([VWAPFilter(), VolumeFilter(), RegimeFilter()])
        approved, reason = chain.check(signal, context)
    """

    @abstractmethod
    def filter(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        """
        检查入场条件。

        Returns:
            (approved, reason): approved=False 时 reason 说明拒绝原因。
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """过滤器名称（日志用）"""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# =============================================================================
# FilterChain — 责任链
# =============================================================================

class FilterChain:
    """
    顺序执行多个 EntryFilter，任一失败即停止（短路求值）。

    Usage:
        chain = FilterChain.from_signal(signal, [VWAPFilter(), RegimeFilter()])
        # 只实例化 signal.entry_conditions 实际需要的 filter
    """

    def __init__(self, filters: list[EntryFilter]) -> None:
        self._filters = filters

    @classmethod
    def from_conditions(
        cls,
        conditions: EntryConditions,
        available: dict[str, EntryFilter],
    ) -> FilterChain:
        """
        根据 EntryConditions 从可用池中选择实际需要的 filter。

        Args:
            conditions: 信号中声明的入场条件
            available: 已注册的 filter 名称 → 实例映射
        """
        selected: list[EntryFilter] = []
        if conditions.require_vwap_side:
            selected.append(available["vwap_side"])
        if conditions.volume_spike_mult is not None:
            selected.append(available["volume_spike"])
        if conditions.adx_min is not None:
            selected.append(available["adx_min"])
        if conditions.max_spread_pct is not None:
            selected.append(available["spread_max"])
        if conditions.allowed_regimes or conditions.blocked_regimes:
            selected.append(available["regime"])
        return cls(selected)

    def check(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        """
        顺序检查所有 filter，任一不通过则短路返回。

        Returns:
            (approved, reason)
        """
        for f in self._filters:
            ok, reason = f.filter(signal, ctx)
            if not ok:
                return False, f"[{f.name}] {reason}"
        return True, ""

    def is_empty(self) -> bool:
        return len(self._filters) == 0


# =============================================================================
# 具体过滤器实现
# =============================================================================

class VWAPSideFilter(EntryFilter):
    """VWAP 方向确认：入场价必须在 VWAP 同侧"""

    name = "VWAPSide"

    def filter(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        vwap = ctx.vwap_daily
        price = ctx.latest_price

        if price <= 0:
            return False, f"price={price:.2f} 无效"
        if vwap <= 0:
            # VWAP 数据不可用（如未计算），放行不阻拦
            return True, f"pass (VWAP unavailable, price={price:.2f})"

        if signal.side is OrderSide.BUY:
            if price < vwap:
                return False, f"做多信号但 price={price:.2f} < VWAP={vwap:.2f}"
        else:
            if price > vwap:
                return False, f"做空信号但 price={price:.2f} > VWAP={vwap:.2f}"

        return True, f"pass (price={price:.2f} {'>' if signal.side is OrderSide.BUY else '<'} VWAP={vwap:.2f})"


class VolumeSpikeFilter(EntryFilter):
    """Volume 放量确认：当前 bar 成交量必须 > 均量 × 倍数"""

    name = "VolumeSpike"

    def filter(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        conditions = signal.entry_conditions
        mult = conditions.volume_spike_mult
        lookback = conditions.volume_lookback

        if mult is None:
            return True, "pass (no check)"

        if len(ctx.bars) < lookback + 1:
            # Phase 1 MVP：bar 历史未注入时放行不阻拦
            return True, f"pass (bar history insufficient: {len(ctx.bars)} < {lookback + 1})"

        latest = ctx.bars[0]
        avg_vol = sum(b.volume for b in ctx.bars[1:lookback + 1]) / lookback

        if avg_vol <= 0:
            return False, f"均量为 0"

        if latest.volume < avg_vol * mult:
            return False, (
                f"vol={latest.volume} < {mult:.1f}× avg_vol={avg_vol:.0f} "
                f"(threshold={avg_vol * mult:.0f})"
            )

        return True, f"pass (vol={latest.volume} ≥ {mult:.1f}× avg_vol={avg_vol:.0f})"


class ADXFilter(EntryFilter):
    """ADX 趋势强度确认：当前 ADX(14) 必须 ≥ 最小值"""

    name = "ADXMin"

    def filter(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        adx_min = signal.entry_conditions.adx_min
        if adx_min is None:
            return True, "pass (no check)"

        # ADX 由外部指标计算器提供，通过 ctx.adx_14 字段
        current_adx = getattr(ctx, "adx_14", 0.0)
        if current_adx <= 0:
            # Phase 1 MVP：ADX 数据未计算时放行不阻拦
            return True, f"pass (ADX unavailable, min={adx_min:.1f})"
        if current_adx < adx_min:
            return False, f"ADX={current_adx:.1f} < min={adx_min:.1f}"

        return True, f"pass (ADX={current_adx:.1f} ≥ {adx_min:.1f})"


class SpreadFilter(EntryFilter):
    """价差过滤：当前 bid-ask spread 必须 ≤ 最大值"""

    name = "SpreadMax"

    def filter(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        max_spread = signal.entry_conditions.max_spread_pct
        if max_spread is None:
            return True, "pass (no check)"

        if ctx.spread_pct > max_spread:
            return False, (
                f"当前价差 {ctx.spread_pct:.4%} > 上限 {max_spread:.4%}"
            )

        return True, f"pass (spread {ctx.spread_pct:.4%} ≤ {max_spread:.4%})"


class RegimeFilter(EntryFilter):
    """
    Regime 过滤：同时检查两层：
      1. EntryConditions 中的 allowed_regimes / blocked_regimes（策略声明的偏好）
      2. RegimeState 中的 preferred_strategies / blocked_strategies（Regime 引擎的决定）
    """

    name = "Regime"

    def filter(self, signal: SignalEvent, ctx: FilterContext) -> tuple[bool, str]:
        conditions = signal.entry_conditions
        regime = ctx.regime  # 字符串，向后兼容

        # ── 层 1: EntryConditions 中的 regime 偏好 ─────────────────────────────
        if regime in conditions.blocked_regimes:
            return False, f"Regime={regime} 在策略禁止列表 {conditions.blocked_regimes}"

        if conditions.allowed_regimes and regime not in conditions.allowed_regimes:
            return False, f"Regime={regime} 不在策略允许列表 {conditions.allowed_regimes}"

        # ── 层 2: RegimeState 的策略矩阵（若已注入）──────────────────────────
        if ctx.regime_state is not None:
            rs = ctx.regime_state
            if not rs.can_trade:
                return False, f"Regime={regime} 当日禁止交易 (size_mult={rs.size_multiplier})"
            if not rs.allows_strategy(signal.strategy):
                blocked = signal.strategy in rs.blocked_strategies
                reason = "策略被 Regime 禁用" if blocked else "策略不在 Regime 推荐列表"
                return False, f"[RegimeEngine] {reason}: strategy={signal.strategy}, Regime={regime}"

        return True, f"pass (Regime={regime})"


# =============================================================================
# 默认 filter 注册表
# =============================================================================

def default_filter_registry() -> dict[str, EntryFilter]:
    """返回默认的 filter 名称 → 实例映射"""
    return {
        "vwap_side": VWAPSideFilter(),
        "volume_spike": VolumeSpikeFilter(),
        "adx_min": ADXFilter(),
        "spread_max": SpreadFilter(),
        "regime": RegimeFilter(),
    }