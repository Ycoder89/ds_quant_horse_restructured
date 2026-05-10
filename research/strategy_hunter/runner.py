"""
research/strategy_hunter/runner.py — 批量回测运行器

StrategyHunter 是核心类：
  1. 遍历参数网格
  2. 为每组参数创建策略实例
  3. 运行回测
  4. 评估指标
  5. 存储结果
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Type

from core.data_handler import DataHandler, SqliteDataHandler
from core.strategy import Strategy
from engine.backtest import BacktestEngine, BacktestResult
from research.strategy_hunter.param_grid import ParamGrid
from research.strategy_hunter.store import ResultRecord, ResultStore

logger = logging.getLogger("ds_quant_horse.hunter")


@dataclass
class HunterConfig:
    """策略猎人配置"""
    strategy_class: type  # Strategy subclass
    param_grid: ParamGrid
    symbols: list[str]
    db_path: str | Path
    start: str = "2024-01-01"
    end: str = "2024-12-31"
    timeframe: str = "5min"
    initial_capital: float = 100_000.0
    contract_multiplier: float = 1.0  # 期货乘数（ES=50, NQ=20）
    min_sharpe: float = 1.0
    min_avg_trades: float = 2.0
    min_win_rate: float = 40.0
    # 数据源
    data_db_path: str | None = None  # 覆盖默认 DB 路径（None = 自动选择）

    def __post_init__(self):
        self.db_path = Path(self.db_path)


class StrategyHunter:
    """
    策略猎人 — 批量参数扫描 + 回测 + 评估。

    Usage:
        hunter = StrategyHunter(
            strategy_class=ORBEnhanced,
            param_grid=OrbParamGrid.default(),
            symbols=["TSLA"],
            db_path="results/hunter.db",
        )
        summary = hunter.run()
        print(summary)
    """

    def __init__(self, config: HunterConfig) -> None:
        self._config = config
        self._store = ResultStore(config.db_path)
        self._results: list[BacktestResult] = []

    # ------- public API -------

    def run(self) -> str:
        """
        执行完整扫描。

        Returns:
            人类可读的摘要报告
        """
        total = self._config.param_grid.count()
        logger.info("StrategyHunter starting: %d combinations for %s",
                     total, self._config.symbols)

        passed = 0
        skipped = 0

        for i, params in enumerate(self._config.param_grid.iterate(), 1):
            # 去重检查
            existing = self._store.get_by_params(
                self._config.strategy_class.__name__,
                params,
            )
            if existing is not None:
                skipped += 1
                continue

            # 运行回测
            result = self._run_single(params)

            # 存储
            record = self._to_record(result)
            self._store.insert(record)

            if record.passed_threshold:
                passed += 1

            logger.info(
                "[%d/%d] %s | Sharpe=%.3f Win=%.1f%% Trades=%d Score=%.3f %s",
                i, total,
                _brief_params(params),
                result.metrics.sharpe_ratio if result.metrics else 0,
                result.metrics.win_rate if result.metrics else 0,
                len(result.trades),
                result.metrics.composite_score if result.metrics else 0,
                "✓" if record.passed_threshold else "",
            )

        # 总结（先 count 再 close）
        final_count = self._store.count()
        self._store.close()
        return (
            f"Hunter done: {final_count} total ({skipped} skipped), "
            f"{passed} passed threshold"
        )

    def get_best(self, n: int = 5) -> list[ResultRecord]:
        """获取最优结果"""
        return self._store.get_top(n=n, min_trades=5)

    # ------- internal -------

    def _run_single(self, params: dict) -> BacktestResult:
        """运行单次回测"""
        # 创建数据处理器
        dh = self._make_data_handler()
        dh.load_range(
            datetime.fromisoformat(self._config.start),
            datetime.fromisoformat(self._config.end),
        )

        # 创建策略实例
        strategy = self._config.strategy_class(
            symbols=self._config.symbols,
            params=params,
        )

        # 运行回测
        engine = BacktestEngine(
            data_handler=dh,
            strategy=strategy,
            initial_capital=self._config.initial_capital,
            contract_multiplier=self._config.contract_multiplier,
        )
        result = engine.run()

        # 附加参数信息
        result.params = params

        return result

    def _make_data_handler(self) -> DataHandler:
        """创建数据处理器"""
        db_path = self._config.data_db_path
        if db_path is None:
            # 自动选择：futures 或 stocks
            futures_symbols = {"ES", "NQ", "MES", "MNQ", "RTY", "YM"}
            is_futures = any(s.upper().split("_")[0] in futures_symbols for s in self._config.symbols)
            if is_futures:
                db_path = Path("D:/Python_Projects/cc_quant_horse/data/db/futures_data.db")
            else:
                db_path = Path("D:/Python_Projects/cc_quant_horse/data/db/stocks_data.db")

        if not Path(db_path).exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")

        return SqliteDataHandler(
            db_path=Path(db_path),
            symbols=self._config.symbols,
            timeframe=self._config.timeframe,
        )

    def _to_record(self, result: BacktestResult) -> ResultRecord:
        """BacktestResult → ResultRecord"""
        metrics = result.metrics
        passed = 0
        if metrics:
            ok, _ = metrics.passes_threshold(
                min_sharpe=self._config.min_sharpe,
                min_avg_trades=self._config.min_avg_trades,
                min_win_rate=self._config.min_win_rate,
            )
            passed = 1 if ok else 0

        return ResultRecord(
            strategy=result.strategy_name or self._config.strategy_class.__name__,
            timestamp=datetime.now().isoformat(),
            params_json=json.dumps(result.params, sort_keys=True),
            sharpe_ratio=metrics.sharpe_ratio if metrics else 0.0,
            sortino_ratio=metrics.sortino_ratio if metrics else 0.0,
            total_return_pct=metrics.total_return_pct if metrics else 0.0,
            max_drawdown_pct=metrics.max_drawdown_pct if metrics else 0.0,
            win_rate=metrics.win_rate if metrics else 0.0,
            profit_factor=metrics.profit_factor if metrics else 0.0,
            total_trades=metrics.total_trades if metrics else 0,
            avg_daily_trades=metrics.avg_daily_trades if metrics else 0.0,
            composite_score=metrics.composite_score if metrics else 0.0,
            passed_threshold=passed,
            total_bars=result.total_bars,
            trading_days=len(result.trading_days),
        )


def _brief_params(params: dict) -> str:
    """简短的参数摘要（日志用）"""
    parts = [f"{k}={v}" for k, v in sorted(params.items())]
    return "|".join(parts[:5])
