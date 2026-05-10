"""
OOS test for multiple ORB ES configs to find robust parameters.
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

configs = []
# Top IS results
for orb in [5, 10, 15, 20]:
    for c in [0.05, 0.1, 0.2, 0.3]:
        for s in [1.0, 1.5, 2.0]:
            configs.append({
                "orb_start_minute": orb,
                "entry_break_confidence": c,
                "atr_mult_stop": s,
            })

def run_test(params, start, end):
    dh = SqliteDataHandler(db_path=Path(DB_PATH), symbols=["ES_continuous"], timeframe="5min")
    dh.load_range(datetime.fromisoformat(start), datetime.fromisoformat(end))
    full_params = {
        **params,
        "volume_spike_mult": 1.0,
        "adx_threshold": 15,
        "require_vwap_side": False,
        "market_open_utc_minutes": 14 * 60 + 30,
        "entry_end_utc_minutes": 19 * 60,
        "force_flat_utc_minutes": 20 * 60 + 55,
    }
    strategy = ORBEnhanced(symbols=["ES_continuous"], params=full_params)
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
    return m.sharpe_ratio, result.total_pnl, len(result.trades), m.win_rate, m.max_drawdown_pct, m.profit_factor

results = []
for cfg in configs:
    label = f"ORB{cfg['orb_start_minute']}_C{cfg['entry_break_confidence']}_S{cfg['atr_mult_stop']}"
    is_sharpe, is_pnl, is_trades, is_wr, is_dd, is_pf = run_test(cfg, "2024-01-01", "2024-12-31")
    oos_sharpe, oos_pnl, oos_trades, oos_wr, oos_dd, oos_pf = run_test(cfg, "2025-01-01", "2025-12-31")
    results.append((label, is_sharpe, oos_sharpe, is_pnl, oos_pnl, is_trades, oos_trades, is_wr, oos_wr, is_dd, oos_dd, is_pf, oos_pf))
    sys.stdout.write(".")
    sys.stdout.flush()

# Sort by OOS Sharpe
results.sort(key=lambda r: r[2], reverse=True)

print(f"\n\n{'='*120}")
print(f"{'Config':<25} {'IS Sharpe':>10} {'OOS Sharpe':>10} {'IS PnL':>10} {'OOS PnL':>10} {'IS WR':>8} {'OOS WR':>8} {'IS DD':>8} {'OOS DD':>8} {'IS PF':>8} {'OOS PF':>8}")
print("-"*120)
for r in results:
    label, is_s, oos_s, is_p, oos_p, is_t, oos_t, is_wr, oos_wr, is_dd, oos_dd, is_pf, oos_pf = r
    print(f"{label:<25} {is_s:>10.4f} {oos_s:>10.4f} ${is_p:>+8.0f} ${oos_p:>+8.0f} {is_wr:>7.1f}% {oos_wr:>7.1f}% {is_dd:>7.2f}% {oos_dd:>7.2f}% {is_pf:>7.4f} {oos_pf:>7.4f}")
