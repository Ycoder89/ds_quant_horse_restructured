"""
core/data_handler.py — DataHandler 抽象基类 + SQLite 实现

cc_quant_horse 实际数据库结构：
  - 每标的一表：{SYMBOL}_5_mins, {SYMBOL}_1_min 等
  - 列：date, open, high, low, close, volume, average, barCount

ds_quant_horse 精简为：
  1. DataHandler ABC（load_range / stream / latest_bars）
  2. SqliteDataHandler 实现，匹配实际 DB schema
  3. 不做 bar 合成（回测直接使用对应周期数据）
"""
from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from core.events import Bar, DataEvent

logger = logging.getLogger(__name__)

# DB 表名后缀映射
TIMEFRAME_SUFFIX = {
    "1min": "1_min",
    "5min": "5_mins",
    "15min": "15_mins",
    "1h": "1_hour",
    "1day": "1_day",
}


# =============================================================================
# DataHandler — 抽象基类
# =============================================================================

class DataHandler(ABC):
    """数据处理器抽象基类。"""

    @abstractmethod
    def load_range(self, start: datetime, end: datetime) -> None:
        """加载指定日期范围的 bar 数据。"""
        ...

    @abstractmethod
    def stream(self) -> Iterator[DataEvent]:
        """按时间顺序迭代 DataEvent。"""
        ...

    @abstractmethod
    def latest_bars(self) -> dict[str, Bar]:
        """返回最近一次迭代的 bar 快照。"""
        ...

    @abstractmethod
    def get_bars(self, symbol: str, lookback: int) -> list[Bar]:
        """返回某标的最近 N 根 bar（含当前，从旧到新）。"""
        ...

    def get_daily_bars(
        self,
        symbol: str,
        end_date: date,
        lookback: int = 30,
    ) -> list[Bar]:
        """
        返回指定日期前 lookback 个交易日的日线 Bar（从旧到新）。

        默认实现：从已加载的 intraday bars 中聚合每日 OHLCV。
        子类可重写以直接从日线表读取。
        """
        return []

    @property
    @abstractmethod
    def symbols(self) -> list[str]:
        ...

    @property
    @abstractmethod
    def bar_count(self) -> int:
        ...


# =============================================================================
# SqliteDataHandler — 匹配 cc_quant_horse 实际 DB
# =============================================================================

class SqliteDataHandler(DataHandler):
    """
    从 SQLite 数据库加载 bar 数据。

    支持实际 DB 结构：每标的一表，表名 = {SYMBOL}_{timeframe_suffix}
    列：date, open, high, low, close, volume, average, barCount
    """

    def __init__(
        self,
        db_path: Path,
        symbols: list[str],
        timeframe: str = "5min",
    ) -> None:
        if timeframe not in TIMEFRAME_SUFFIX:
            raise ValueError(f"不支持的周期: {timeframe}，可选: {list(TIMEFRAME_SUFFIX)}")
        self._db_path = db_path
        self._symbols = list(symbols)
        self._timeframe = timeframe
        self._suffix = TIMEFRAME_SUFFIX[timeframe]

        self._all_bars: dict[str, list[Bar]] = {}
        self._events: list[DataEvent] = []
        self._latest_bars: dict[str, Bar] = {}
        self._idx = 0

    # ------- properties -------

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def bar_count(self) -> int:
        return sum(len(b) for b in self._all_bars.values())

    # ------- load -------

    def load_range(self, start: datetime, end: datetime) -> None:
        """
        从每标的独立表加载数据，构建时间对齐的 DataEvent 列表。

        Args:
            start: 开始时间（含）
            end:   结束时间（含）
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        raw_bars: dict[str, dict[datetime, Bar]] = {}

        for symbol in self._symbols:
            table = f"{symbol}_{self._suffix}"

            # 检查表存在
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cursor.fetchone():
                logger.warning("表 %s 不存在，跳过 %s", table, symbol)
                self._all_bars[symbol] = []
                raw_bars[symbol] = {}
                continue

            cursor.execute(
                f"""SELECT date, open, high, low, close, volume
                    FROM "{table}"
                    WHERE date >= ? AND date <= ?
                    ORDER BY date ASC""",
                (start.isoformat(), end.isoformat()),
            )

            bar_map: dict[datetime, Bar] = {}
            bar_list: list[Bar] = []
            for row in cursor.fetchall():
                ts = _parse_timestamp(row["date"])
                bar = Bar(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"] or 0),
                )
                bar_map[ts] = bar
                bar_list.append(bar)

            raw_bars[symbol] = bar_map
            self._all_bars[symbol] = bar_list
            logger.info("已加载 %s: %d 根 bar (%s ~ %s)",
                         symbol, len(bar_list),
                         bar_list[0].timestamp.isoformat() if bar_list else "N/A",
                         bar_list[-1].timestamp.isoformat() if bar_list else "N/A")

        conn.close()

        # 时间对齐：合并所有标的在同一时间点的 bar
        all_ts = set()
        for bar_map in raw_bars.values():
            all_ts.update(bar_map.keys())
        sorted_ts = sorted(all_ts)

        for ts in sorted_ts:
            bars = {}
            for symbol in self._symbols:
                b = raw_bars.get(symbol, {}).get(ts)
                if b:
                    bars[symbol] = b
            if bars:
                self._events.append(DataEvent(timestamp=ts, bars=bars))

        logger.info("时间对齐完成：%d 个时间点", len(self._events))

    # ------- stream -------

    def stream(self) -> Iterator[DataEvent]:
        for event in self._events:
            self._latest_bars = event.bars
            yield event

    def latest_bars(self) -> dict[str, Bar]:
        return dict(self._latest_bars)

    def get_bars(self, symbol: str, lookback: int = 20) -> list[Bar]:
        """返回某标的最近 N 根 bar（含当前，从旧到新）。"""
        symbol_u = symbol.upper()
        all_b = self._all_bars.get(symbol, self._all_bars.get(symbol_u, []))
        if not all_b or symbol not in self._latest_bars:
            return []

        current = self._latest_bars[symbol]
        for i, b in enumerate(all_b):
            if b.timestamp == current.timestamp:
                start = max(0, i - lookback + 1)
                return all_b[start:i + 1]
        return all_b[-lookback:] if lookback else all_b

    def get_daily_bars(
        self,
        symbol: str,
        end_date: date,
        lookback: int = 30,
    ) -> list[Bar]:
        """
        从已加载的 5min bar 聚合每日 OHLCV，返回 end_date 前 lookback 个交易日的日线 Bar。

        聚合规则：每日取 open=当日第一根 bar.open, high=最高, low=最低,
                        close=最后一根 bar.close, volume=求和。
        """
        all_b = self._all_bars.get(symbol, self._all_bars.get(symbol.upper(), []))
        if not all_b:
            return []

        # 按日期分组
        from collections import defaultdict
        day_bars: dict[date, list[Bar]] = defaultdict(list)
        for b in all_b:
            d = b.timestamp.date()
            if d < end_date:
                day_bars[d].append(b)

        sorted_days = sorted(day_bars.keys())[-lookback:]
        result: list[Bar] = []
        for d in sorted_days:
            bars = day_bars[d]
            if not bars:
                continue
            daily_bar = Bar(
                timestamp=datetime.combine(d, bars[0].timestamp.time()),
                open=bars[0].open,
                high=max(b.high for b in bars),
                low=min(b.low for b in bars),
                close=bars[-1].close,
                volume=sum(b.volume for b in bars),
            )
            result.append(daily_bar)

        return result

    # ------- reset -------

    def reset(self) -> None:
        """重置迭代器，复用已加载数据。"""
        self._latest_bars = {}
        self._idx = 0

    def close(self) -> None:
        """清理资源。"""
        self._all_bars.clear()
        self._events.clear()
        self._latest_bars.clear()


def _parse_timestamp(val) -> datetime:
    """解析 SQLite 存储的时间戳（可能是字符串或 datetime 对象）。"""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        # 尝试多种格式
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        # 最后尝试 ISO 格式
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    raise TypeError(f"无法解析时间戳类型: {type(val)}")