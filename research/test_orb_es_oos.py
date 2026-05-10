"""
OOS validation: Best ORB ES config on 2025 data.
"""
import logging, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data_handler import SqliteDataHandler
from core.execution import SimulatedExecutionHandler, SlippageModel
from core.risk_manager import DefaultRiskManager, PositionSizing, PositionSizingMethod, RiskLimits
from engine.backtest import BacktestEngine
from strategies.orb_enhanced import ORBEnhanced

logging.basicConfig(level=logging.WARNING, format='%(name)s | %(levelname)s | %(message)s')
DB_PATH = "D:/Python_Projects/cc_quant_horse/data/db/futures_data.db"

# Best params from scan
params = {
    "orb_start_minute": 20,
    "entry_break_confidence": 0.3,
    "volume_spike_mult": 1.0,
    "adx_threshold": 15,
    "atr_mult_stop": 2.0,
    "require_vwap_side": False,
    "market_open_utc_minutes": 14 * 60 + 30,
    "entry_end_utc_minutes": 19 * 60,
    "force_flat_utc_minutes": 20 * 60 + 55,
}

def run_test(start, end, label):
    dh = SqliteDataHandler(db_path=Path(DB_PATH), symbols=["ES_continuous"], timeframe="5min")
    dh.load_range(datetime.fromisoformat(start), datetime.fromisoformat(end))
    strategy = ORBEnhanced(symbols=["ES_continuous"], params=params)
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
    print(f"\n{label}:")
    print(f"  Trades: {len(result.trades)}")
    print(f"  WR: {m.win_rate:.1f}%")
    print(f"  Sharpe: {m.sharpe_ratio:.4f}")
    print(f"  PnL: ${result.total_pnl:+.2f}")
    print(f"  Return: {m.total_return_pct:.2f}%")
    print(f"  Max DD: {m.max_drawdown_pct:.2f}%")
    print(f"  Profit Factor: {m.profit_factor:.4f}")
    return result

# IS: 2024 full year
r_is = run_test("2024-01-01", "2024-12-31", "IN-SAMPLE 2024")
# OOS: 2025 (up to April 2026)
r_oos = run_test("2025-01-01", "2025-12-31", "OUT-OF-SAMPLE 2025")
# Combined
r_all = run_test("2024-01-01", "2025-12-31", "COMBINED 2024-2025")

print(f"\n{'='*50}")
print(f"Comparison:")
print(f"  IS Sharpe:  {r_is.metrics.sharpe_ratio:.4f}")
print(f"  OOS Sharpe: {r_oos.metrics.sharpe_ratio:.4f}")
print(f"  Decay:      {(1 - r_oos.metrics.sharpe_ratio/r_is.metrics.sharpe_ratio)*100 if r_is.metrics.sharpe_ratio > 0 else 0:.1f}%")
