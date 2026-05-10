"""
research/strategy_hunter/store.py — 策略回测结果存储

使用 SQLite 存储所有策略候选的回测结果，支持：
  - 插入新结果
  - 按指标排序查询
  - 去重（相同参数不重复运行）
  - 导出为 DataFrame
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class ResultRecord:
    """单条回测结果记录"""
    run_id: int = 0
    strategy: str = ""
    timestamp: str = ""
    params_json: str = "{}"
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_daily_trades: float = 0.0
    composite_score: float = 0.0
    passed_threshold: int = 0
    total_bars: int = 0
    trading_days: int = 0

    @property
    def params(self) -> dict:
        return json.loads(self.params_json) if self.params_json else {}


class ResultStore:
    """
    回测结果存储。

    Usage:
        store = ResultStore("results/hunter_results.db")
        store.insert(result)
        best = store.get_top(n=10, order_by="sharpe_ratio")
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                params_json TEXT NOT NULL,

                -- 指标
                sharpe_ratio REAL DEFAULT 0,
                sortino_ratio REAL DEFAULT 0,
                total_return_pct REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                avg_daily_trades REAL DEFAULT 0,
                composite_score REAL DEFAULT 0,
                passed_threshold INTEGER DEFAULT 0,
                total_bars INTEGER DEFAULT 0,
                trading_days INTEGER DEFAULT 0,

                -- 去重: 相同策略+参数不重复插入
                UNIQUE(strategy, params_json)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_composite
            ON results(composite_score DESC)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy
            ON results(strategy, composite_score DESC)
        """)
        self._conn.commit()

    def insert(self, record: ResultRecord) -> bool:
        """
        插入一条结果。如果已存在相同策略+参数则跳过。

        Returns:
            True=新插入, False=已存在跳过
        """
        try:
            self._conn.execute("""
                INSERT OR IGNORE INTO results(
                    strategy, timestamp, params_json,
                    sharpe_ratio, sortino_ratio, total_return_pct,
                    max_drawdown_pct, win_rate, profit_factor,
                    total_trades, avg_daily_trades, composite_score,
                    passed_threshold, total_bars, trading_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.strategy, record.timestamp, record.params_json,
                record.sharpe_ratio, record.sortino_ratio, record.total_return_pct,
                record.max_drawdown_pct, record.win_rate, record.profit_factor,
                record.total_trades, record.avg_daily_trades, record.composite_score,
                record.passed_threshold, record.total_bars, record.trading_days,
            ))
            self._conn.commit()
            return self._conn.total_changes > 0
        except sqlite3.IntegrityError:
            return False

    def get_top(
        self,
        n: int = 10,
        strategy: str = "",
        order_by: str = "composite_score",
        min_trades: int = 10,
    ) -> list[ResultRecord]:
        """
        获取排名前 N 的结果。

        Args:
            n: 返回数量
            strategy: 策略名称过滤（空=全部）
            order_by: 排序字段
            min_trades: 最少交易次数过滤
        """
        valid_cols = {"composite_score", "sharpe_ratio", "win_rate",
                      "total_return_pct", "avg_daily_trades"}
        col = order_by if order_by in valid_cols else "composite_score"

        where = ["total_trades >= ?"]
        params: list[Any] = [min_trades]

        if strategy:
            where.append("strategy = ?")
            params.append(strategy)

        sql = f"""
            SELECT * FROM results
            WHERE {' AND '.join(where)}
            ORDER BY {col} DESC
            LIMIT ?
        """
        params.append(n)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_params(self, strategy: str, params: dict) -> Optional[ResultRecord]:
        """按策略名+参数精确查找"""
        params_json = json.dumps(params, sort_keys=True)
        row = self._conn.execute(
            "SELECT * FROM results WHERE strategy=? AND params_json=?",
            (strategy, params_json),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def count(self, strategy: str = "") -> int:
        """结果总数"""
        if strategy:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM results WHERE strategy=?",
                (strategy,),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) as cnt FROM results").fetchone()
        return row["cnt"] if row else 0

    def summary(self, strategy: str = "") -> str:
        """打印结果摘要"""
        count = self.count(strategy)
        top = self.get_top(5, strategy)
        lines = [
            f"Result Store: {self._path}",
            f"Total runs: {count}",
            "",
            "Top 5:",
            "-" * 60,
        ]
        if top:
            lines.append(f"{'Rank':>4} {'Score':>7} {'Sharpe':>7} {'Win%':>6} "
                         f"{'Trades':>6} {'AvgD':>5} {'Params'}")
            for i, r in enumerate(top, 1):
                fp = r.params
                brief = ",".join(f"{k}={v}" for k, v in list(fp.items())[:4])
                lines.append(
                    f"{i:>4} {r.composite_score:>7.3f} {r.sharpe_ratio:>7.3f} "
                    f"{r.win_rate:>6.1f} {r.total_trades:>6} {r.avg_daily_trades:>5.1f} "
                    f" {brief}"
                )
        else:
            lines.append("  (no results)")
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> ResultRecord:
        return ResultRecord(
            run_id=row["run_id"],
            strategy=row["strategy"],
            timestamp=row["timestamp"],
            params_json=row["params_json"],
            sharpe_ratio=row["sharpe_ratio"],
            sortino_ratio=row["sortino_ratio"],
            total_return_pct=row["total_return_pct"],
            max_drawdown_pct=row["max_drawdown_pct"],
            win_rate=row["win_rate"],
            profit_factor=row["profit_factor"],
            total_trades=row["total_trades"],
            avg_daily_trades=row["avg_daily_trades"],
            composite_score=row["composite_score"],
            passed_threshold=row["passed_threshold"],
            total_bars=row["total_bars"],
            trading_days=row["trading_days"],
        )
