"""
Save ORB ES re-entry results to StrategyHunter DB.
Includes IS (2024), OOS-1 (2025), OOS-2 (2026 Q1).
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_handler import SqliteDataHandler
from core.execution import SimulatedExecutionHandler, SlippageModel
from core.risk_manager import DefaultRiskManager, PositionSizing, PositionSizingMethod, RiskLimits
from engine.backtest import BacktestEngine
from research.strategy_hunter.store import ResultRecord, ResultStore
from strategies.orb_enhanced import ORBEnhanced

logging.basicConfig(level=logging.WARNING, format='%(name)s | %(levelname)s | %(message)s')
DB_PATH = "D:/Python_Projects/cc_quant_horse/data/db/futures_data.db"

BASE = {
    "volume_spike_mult": 1.0,
    "adx_threshold": 15,
    "require_vwap_side": False,
    "market_open_utc_minutes": 14 * 60 + 30,
    "entry_end_utc_minutes": 19 * 60,
    "force_flat_utc_minutes": 20 * 60 + 55,
}

# Top configs from re-entry scan (by OOS Sharpe)
configs = {
    "orb20_c0.05_s1.5":   {"orb_start_minute": 20, "entry_break_confidence": 0.05, "atr_mult_stop": 1.5},
    "orb20_c0.1_s1.5":    {"orb_start_minute": 20, "entry_break_confidence": 0.1,  "atr_mult_stop": 1.5},
    "orb20_c0.3_s2.0":    {"orb_start_minute": 20, "entry_break_confidence": 0.3,  "atr_mult_stop": 2.0},
    "orb20_c0.2_s2.5":    {"orb_start_minute": 20, "entry_break_confidence": 0.2,  "atr_mult_stop": 2.5},
    "orb15_c0.2_s2.5":    {"orb_start_minute": 15, "entry_break_confidence": 0.2,  "atr_mult_stop": 2.5},
}

periods = [
    ("IS_2024", "2024-01-01", "2024-12-31"),
    ("OOS_2025", "2025-01-01", "2025-12-31"),
    ("OOS_2026Q1", "2026-01-01", "2026-04-24"),
]

store = ResultStore("D:/python_projects/ds_quant_horse/data/hunter.db")

for name, p in configs.items():
    for period_label, start, end in periods:
        full_params = {**BASE, **p}

        dh = SqliteDataHandler(db_path=Path(DB_PATH), symbols=["ES_continuous"], timeframe="5min")
        dh.load_range(datetime.fromisoformat(start), datetime.fromisoformat(end))

        strategy = ORBEnhanced(symbols=["ES_continuous"], params=full_params,
                               name=f"orb_es_reentry_{period_label}")
        engine = BacktestEngine(
            data_handler=dh,
            strategy=strategy,
            execution=SimulatedExecutionHandler(slippage=SlippageModel(fixed_ticks=0, fill_on_next_bar=False)),
            risk_manager=DefaultRiskManager(
                sizing=PositionSizing(method=PositionSizingMethod.FIXED, fixed_quantity=2),
                limits=RiskLimits(max_daily_loss_pct=0.06, max_positions_per_day=3,
                                  max_concurrent_positions=1, require_no_position=True),
            ),
            initial_capital=100_000.0,
            contract_multiplier=50.0,
        )
        result = engine.run()
        m = result.metrics

        record = ResultRecord(
            strategy=f"ORBEnhanced_reentry_ES_{name}_{period_label}",
            timestamp=datetime.now().isoformat(),
            params_json=json.dumps(full_params, sort_keys=True),
            sharpe_ratio=m.sharpe_ratio,
            sortino_ratio=m.sortino_ratio,
            total_return_pct=m.total_return_pct,
            max_drawdown_pct=m.max_drawdown_pct,
            win_rate=m.win_rate,
            profit_factor=m.profit_factor,
            total_trades=m.total_trades,
            avg_daily_trades=m.avg_daily_trades,
            composite_score=m.composite_score,
            passed_threshold=1 if m.sharpe_ratio > 0.5 else 0,
            total_bars=result.total_bars,
            trading_days=len(result.trading_days),
        )
        store.insert(record)
        print(f"Saved: {name} {period_label} | Sharpe={m.sharpe_ratio:.4f} WR={m.win_rate:.1f}% ADT={m.avg_daily_trades:.2f} PnL=${result.total_pnl:+.0f}")

print(f"\nSaved {len(configs) * len(periods)} records to DB.")
store.close()
