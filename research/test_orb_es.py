"""
Quick test: ORB on ES_continuous with current-bar fill + futures multiplier.
Verifies the engine swap works and produces meaningful metrics.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.data_handler import SqliteDataHandler
from core.execution import SimulatedExecutionHandler, SlippageModel
from core.risk_manager import DefaultRiskManager, PositionSizing, PositionSizingMethod, RiskLimits
from engine.backtest import BacktestEngine
from strategies.orb_enhanced import ORBEnhanced

logging.basicConfig(level=logging.INFO, format='%(name)s | %(levelname)s | %(message)s')
# Silence noisy loggers
logging.getLogger("ds_quant_horse.risk").setLevel(logging.WARNING)
logging.getLogger("ds_quant_horse.backtest").setLevel(logging.WARNING)

DB_PATH = "D:/Python_Projects/cc_quant_horse/data/db/futures_data.db"

# ---- Parameters (from the best ORB config found previously) ----
params = {
    "orb_start_minute": 10,
    "entry_break_confidence": 0.1,
    "volume_spike_mult": 1.0,
    "adx_threshold": 20,
    "atr_mult_stop": 1.5,
    "require_vwap_side": False,        # ES ORB works better without VWAP
    "market_open_utc_minutes": 14 * 60 + 30,   # 14:30 UTC = 9:30 ET
    "entry_end_utc_minutes": 20 * 60,           # 20:00 UTC = 15:00 ET
    "force_flat_utc_minutes": 20 * 60 + 55,     # 20:55 UTC = 15:55 ET
}

# ---- Data ----
dh = SqliteDataHandler(
    db_path=Path(DB_PATH),
    symbols=["ES_continuous"],
    timeframe="5min",
)

# ---- Test 1: Current-bar fill (fill_on_next_bar=False) ----
print("=" * 60)
print("Test 1: Current-bar fill (engine swap active)")
print("=" * 60)

dh.load_range(
    datetime.fromisoformat("2024-01-01"),
    datetime.fromisoformat("2024-12-31"),
)

strategy = ORBEnhanced(symbols=["ES_continuous"], params=params)

engine = BacktestEngine(
    data_handler=dh,
    strategy=strategy,
    execution=SimulatedExecutionHandler(
        slippage=SlippageModel(fixed_ticks=0, fill_on_next_bar=False),
    ),
    risk_manager=DefaultRiskManager(
        sizing=PositionSizing(
            method=PositionSizingMethod.FIXED,
            fixed_quantity=2,  # 2 ES contracts
        ),
        limits=RiskLimits(
            max_daily_loss_pct=0.06,
            max_positions_per_day=3,
            max_concurrent_positions=1,
            require_no_position=True,
        ),
    ),
    initial_capital=100_000.0,
    contract_multiplier=50.0,  # ES = $50/pt
)

result1 = engine.run()

print(f"Total trades: {len(result1.trades)}")
print(f"Win rate: {result1.metrics.win_rate:.1f}%")
print(f"Sharpe: {result1.metrics.sharpe_ratio:.4f}")
print(f"Total PnL: ${result1.total_pnl:+.2f}")
print(f"Total return: {result1.metrics.total_return_pct:.2f}%")
print(f"Max DD: {result1.metrics.max_drawdown_pct:.2f}%")
print(f"Avg daily trades: {result1.metrics.avg_daily_trades:.2f}")
print(f"Profit factor: {result1.metrics.profit_factor:.4f}")
print()

# ---- Test 2: Next-bar fill (old behavior, for comparison) ----
print("=" * 60)
print("Test 2: Next-bar fill (old behavior)")
print("=" * 60)

dh2 = SqliteDataHandler(
    db_path=Path(DB_PATH),
    symbols=["ES_continuous"],
    timeframe="5min",
)
dh2.load_range(
    datetime.fromisoformat("2024-01-01"),
    datetime.fromisoformat("2024-12-31"),
)

strategy2 = ORBEnhanced(symbols=["ES_continuous"], params=params)

engine2 = BacktestEngine(
    data_handler=dh2,
    strategy=strategy2,
    execution=SimulatedExecutionHandler(
        slippage=SlippageModel(fixed_ticks=0, fill_on_next_bar=True),
    ),
    risk_manager=DefaultRiskManager(
        sizing=PositionSizing(
            method=PositionSizingMethod.FIXED,
            fixed_quantity=2,
        ),
        limits=RiskLimits(
            max_daily_loss_pct=0.06,
            max_positions_per_day=3,
            max_concurrent_positions=1,
            require_no_position=True,
        ),
    ),
    initial_capital=100_000.0,
    contract_multiplier=50.0,
)

result2 = engine2.run()

print(f"Total trades: {len(result2.trades)}")
print(f"Win rate: {result2.metrics.win_rate:.1f}%")
print(f"Sharpe: {result2.metrics.sharpe_ratio:.4f}")
print(f"Total PnL: ${result2.total_pnl:+.2f}")
print(f"Total return: {result2.metrics.total_return_pct:.2f}%")
print(f"Max DD: {result2.metrics.max_drawdown_pct:.2f}%")
print(f"Avg daily trades: {result2.metrics.avg_daily_trades:.2f}")
print(f"Profit factor: {result2.metrics.profit_factor:.4f}")
print()

# Summary comparison
print("=" * 60)
print("COMPARISON")
print("=" * 60)
print(f"{'Metric':<25} {'Current-bar':>15} {'Next-bar':>15}")
print("-" * 55)
print(f"{'Trades':<25} {len(result1.trades):>15d} {len(result2.trades):>15d}")
print(f"{'Win Rate':<25} {result1.metrics.win_rate:>14.1f}% {result2.metrics.win_rate:>14.1f}%")
print(f"{'Sharpe':<25} {result1.metrics.sharpe_ratio:>15.4f} {result2.metrics.sharpe_ratio:>15.4f}")
print(f"{'Total PnL':<25} ${result1.total_pnl:>+13.2f} ${result2.total_pnl:>+13.2f}")
print(f"{'Total Return':<25} {result1.metrics.total_return_pct:>14.2f}% {result2.metrics.total_return_pct:>14.2f}%")
print(f"{'Max DD':<25} {result1.metrics.max_drawdown_pct:>14.2f}% {result2.metrics.max_drawdown_pct:>14.2f}%")
print(f"{'Avg Daily Trades':<25} {result1.metrics.avg_daily_trades:>15.2f} {result2.metrics.avg_daily_trades:>15.2f}")
print(f"{'Profit Factor':<25} {result1.metrics.profit_factor:>15.4f} {result2.metrics.profit_factor:>15.4f}")
