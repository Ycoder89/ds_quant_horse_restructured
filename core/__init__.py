"""
core/ — ds_quant_horse 核心抽象层

事件驱动架构基础：
  events.py     — Bar / DataEvent / SignalEvent / OrderEvent / FillEvent
  filters.py    — EntryFilter ABC + 具体实现（VWAP/Volume/ADX/Regime）
  exit.py       — ExitManager ABC + 具体实现（固定止损/移动止损/时间止损）
  strategy.py   — Strategy 基类
  data_handler.py — DataHandler 抽象基类
  risk_manager.py — RiskManager 基类
"""
from __future__ import annotations

from core.events import (
    Bar,
    DataEvent,
    EntryConditions,
    FillEvent,
    OrderEvent,
    OrderSide,
    OrderType,
    SignalEvent,
    TimeFrame,
)
from core.filters import EntryFilter
from core.exit import ExitManager
from core.strategy import Strategy

__all__ = [
    "Bar",
    "DataEvent",
    "EntryConditions",
    "EntryFilter",
    "ExitManager",
    "FillEvent",
    "OrderEvent",
    "OrderSide",
    "OrderType",
    "SignalEvent",
    "Strategy",
    "TimeFrame",
]