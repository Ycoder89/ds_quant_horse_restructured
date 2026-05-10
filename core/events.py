"""
core/events.py — 事件定义（精简版）

cc_quant_horse 原版有 12 字段 SignalEvent，存在以下问题：
  - close_qty / take_profit / timeframe 属于策略/退出逻辑，不应污染信号事件
  - order_type 始终为 MARKET（让执行层决定智能路由）
  - quantity 由风控计算，策略不应预填

ds_quant_horse 精简为 8 字段，新增 EntryConditions 数据类统一表达入场过滤需求。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional


# =============================================================================
# 枚举
# =============================================================================

class OrderSide(Enum):
    """订单方向"""
    BUY = "BUY"
    SELL = "SELL"

    def opposite(self) -> OrderSide:
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"

    @classmethod
    def from_str(cls, s: str) -> OrderType:
        return cls[s.upper()]


class TimeFrame(Enum):
    """K 线周期"""
    M1 = "1min"
    M5 = "5min"
    M15 = "15min"
    H1 = "1hour"
    D1 = "1day"

    @classmethod
    def from_str(cls, s: str) -> TimeFrame:
        mapping = {
            "1min": cls.M1, "1m": cls.M1,
            "5min": cls.M5, "5m": cls.M5,
            "15min": cls.M15, "15m": cls.M15,
            "1hour": cls.H1, "1h": cls.H1, "60min": cls.H1,
            "1day": cls.D1, "1d": cls.D1, "daily": cls.D1,
        }
        return mapping[s.lower()]

    @property
    def minutes(self) -> int:
        return {TimeFrame.M1: 1, TimeFrame.M5: 5, TimeFrame.M15: 15,
                TimeFrame.H1: 60, TimeFrame.D1: 1440}[self]


# =============================================================================
# Bar — 一根 K 线
# =============================================================================

@dataclass(frozen=True)
class Bar:
    """
    单根 OHLCV K 线。

    新增便捷属性：
      - avg_price:  (open + close + high + low) / 4
      - typical_price: (high + low + close) / 3
      - range:       high - low（振幅）
      - is_bullish:  close > open
    """
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    vwap: float = 0.0          # 日内实时 VWAP（仅 1min/5min 有意义）

    @property
    def avg_price(self) -> float:
        return (self.open + self.high + self.low + self.close) * 0.25

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body_pct(self) -> float:
        """实体占振幅百分比（反映 K 线确定性和质量）"""
        if self.range <= 0:
            return 0.0
        return abs(self.close - self.open) / self.range * 100

    def __repr__(self) -> str:
        body = "▲" if self.is_bullish else "▼"
        return (
            f"Bar({self.timestamp.strftime('%Y-%m-%d %H:%M')} "
            f"O={self.open:.2f} H={self.high:.2f} L={self.low:.2f} C={self.close:.2f} "
            f"V={self.volume} {body})"
        )


# =============================================================================
# EntryConditions — 入场确认条件（NEW）
# =============================================================================

@dataclass
class EntryConditions:
    """
    策略发出的入场条件集合。

    RiskManager / EntryFilter 链按需检查这些条件：
      - 任一条件不满足 → 信号被拦截
      - 全部通过 → 允许下单

    所有字段为 Optional：None 表示该条件不检查。
    """
    # VWAP 确认：要求入场价必须相对于 VWAP 在指定侧
    # True = 价格必须在 VWAP 上方（做多）或下方（做空）
    require_vwap_side: bool = False

    # Volume 确认：当前 bar volume 必须 > 近 N 根均值的倍数
    volume_spike_mult: Optional[float] = None       # 如 1.2 = 1.2× 均量
    volume_lookback: int = 20                        # 均量计算窗口

    # ADX 趋势强度确认
    adx_min: Optional[float] = None                 # 如 20.0

    # 最大 bid-ask 价差（股票避免入场时价差过大）
    max_spread_pct: Optional[float] = None           # 如 0.002 = 0.2%

    # 允许的 Regime 状态（空 set = 不限制）
    allowed_regimes: set[str] = field(default_factory=set)

    # 禁止的 Regime 状态（空 set = 不限制）
    blocked_regimes: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        """是否有任何条件需要检查"""
        return (
            not self.require_vwap_side
            and self.volume_spike_mult is None
            and self.adx_min is None
            and self.max_spread_pct is None
            and not self.allowed_regimes
            and not self.blocked_regimes
        )


# =============================================================================
# 事件类型
# =============================================================================

@dataclass(frozen=True)
class DataEvent:
    """
    数据事件：每根 K 线到达时由 DataHandler 推送。

    包含一个时间点的所有标的 Bar，策略在 on_bar() 中处理。
    """
    timestamp: datetime
    bars: dict[str, Bar]     # symbol → Bar

    def get_bar(self, symbol: str) -> Optional[Bar]:
        return self.bars.get(symbol)

    @property
    def symbols(self) -> list[str]:
        return list(self.bars.keys())


@dataclass(frozen=True)
class SignalEvent:
    """
    信号事件（精简版：8 字段）。

    策略发出，由 RiskManager 消费：
      1. 检查 entry_conditions（通过 EntryFilter 链）
      2. 检查风控状态（is_trading_allowed）
      3. 计算仓位（calculate_quantity）
      4. 发出 OrderEvent

    与 cc_quant_horse 的区别：
      - 移除 close_qty（ExitManager 管理部分平仓）
      - 移除 take_profit（ExitManager 管理止盈）
      - 移除 timeframe（策略自身知道）
      - 移除 order_type（始终 MARKET，让执行层决定路由）
      - 移除 quantity（风控计算）
    """
    symbol: str
    side: OrderSide
    entry_price: float               # 预期入场价格（用于滑点估算）
    stop_loss: float                 # 初始止损价
    strategy: str                    # 策略标识（日志/对账用）
    timestamp: datetime              # 信号产生时间
    entry_conditions: EntryConditions = field(default_factory=EntryConditions)
    confidence: float = 1.0          # 信号置信度 [0, 1]，可选用于动态仓位缩放

    def __repr__(self) -> str:
        return (
            f"Signal({self.side.value} {self.symbol} @ {self.entry_price:.2f} "
            f"stop={self.stop_loss:.2f} | {self.strategy})"
        )


@dataclass(frozen=True)
class OrderEvent:
    """
    订单事件。

    RiskManager 发出，ExecutionHandler 消费。
    risk_id 用于对账：追踪订单 → 成交 → PnL 链路。
    """
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float = 0.0
    strategy: str = ""
    risk_id: str = ""               # 风控追踪 ID（RiskManager 生成）

    def __repr__(self) -> str:
        type_str = self.order_type.value
        if self.order_type is OrderType.LIMIT:
            type_str += f"@{self.limit_price:.2f}"
        return (
            f"Order({type_str} {self.side.value} {self.quantity}×{self.symbol} "
            f"| {self.strategy})"
        )


@dataclass(frozen=True)
class FillEvent:
    """
    成交事件。

    ExecutionHandler 发出，Portfolio 消费。
    包含实际成交价和手续费，用于精确 PnL 计算。
    """
    timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: int
    fill_price: float
    commission: float = 0.0
    strategy: str = ""
    risk_id: str = ""

    @property
    def fill_value(self) -> float:
        """成交名义金额（不含手续费）"""
        return self.quantity * self.fill_price

    @property
    def net_value(self) -> float:
        """成交净额（含手续费）"""
        return self.fill_value - self.commission

    def __repr__(self) -> str:
        return (
            f"Fill({self.side.value} {self.quantity}×{self.symbol} "
            f"@{self.fill_price:.2f} comm={self.commission:.2f})"
        )