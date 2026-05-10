"""
main_swingtrend.py — 策略C (SwingTrendStock) 回测入口

用法：
    python main_swingtrend.py

目的：
    验证 SwingTrendStock 完整数据流：DataHandler → Strategy → RiskManager → 信号/订单结果
"""
from __future__ import annotations

import logging
import sys

sys.path.insert(0, r"d:\Python_Projects\ds_quant_horse")

from datetime import datetime

from core.data_handler import SqliteDataHandler
from core.risk_manager import DefaultRiskManager, PositionSizing, PositionSizingMethod, RiskLimits
from engine.backtest import BacktestEngine
from strategies.swingtrend_stock import SwingTrendStock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main() -> None:
    # ---- 1. 数据源 ----
    logger.info("初始化 SqliteDataHandler...")
    db_path = r"D:\Python_Projects\cc_quant_horse\data\db\stocks_data.db"
    data_handler = SqliteDataHandler(
        db_path=db_path,
        symbols=["TSLA"],
        timeframe="5min",
    )
    data_handler.load_range(
        start=datetime(2025, 1, 1),
        end=datetime(2025, 12, 31),
    )
    logger.info(f"  loaded {len(data_handler._events)} events")

    # ---- 2. 策略 C ----
    logger.info("初始化 SwingTrendStock 策略...")
    strategy = SwingTrendStock(
        symbols=["TSLA"],
        params={
            "ema_fast": 8,
            "ema_slow": 21,
            "adx_min": 25.0,
            "swing_lookback": 2,
            "swing_confirm": 1,
            "volume_spike_mult": 1.2,
            "require_vwap_side": True,
            "atr_mult_stop": 1.5,
            "max_spread_pct": 0.002,
            "max_entries_per_direction": 1,
            "latest_entry_time": "13:30",
            "force_flat_time": "15:55",
        },
    )

    # ---- 3. 风控 ----
    logger.info("初始化 RiskManager...")
    risk_manager = DefaultRiskManager(
        limits=RiskLimits(
            max_daily_loss_pct=0.05,
            max_positions_per_day=1,
            max_concurrent_positions=1,
            require_no_position=True,
        ),
        sizing=PositionSizing(
            method=PositionSizingMethod.FIXED,
            fixed_quantity=100,
        ),
    )

    # ---- 4. 引擎 ----
    logger.info("启动回测引擎...")
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        risk_manager=risk_manager,
    )

    result = engine.run()

    # ---- 5. 输出结果 ----
    print("\n")
    print(result.summary())


if __name__ == "__main__":
    main()