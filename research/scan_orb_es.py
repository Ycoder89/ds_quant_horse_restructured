"""
Parameter scan: ORB on ES_continuous with fixed engine (exit-before-entry, current-bar fill).
Searches for parameter combinations that produce Sharpe > 1.0.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from itertools import product

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.data_handler import SqliteDataHandler
from core.execution import SimulatedExecutionHandler, SlippageModel
from core.risk_manager import DefaultRiskManager, PositionSizing, PositionSizingMethod, RiskLimits
from engine.backtest import BacktestEngine
from strategies.orb_enhanced import ORBEnhanced

logging.basicConfig(level=logging.WARNING, format='%(name)s | %(levelname)s | %(message)s')

DB_PATH = "D:/Python_Projects/cc_quant_horse/data/db/futures_data.db"

# ── Parameter grid ──
ORB_START = [5, 10, 15, 20]
BREAK_CONFIDENCE = [0.05, 0.1, 0.2, 0.3]
VOLUME_MULT = [1.0, 1.2]
ADX_THRESH = [15, 20, 25]
ATR_MULT_STOP = [1.0, 1.5, 2.0]
USE_VWAP = [False]  # ES ORB works better without VWAP
ENTRY_END = [19 * 60, 20 * 60]  # 19:00 or 20:00 UTC = 14:00 or 15:00 ET

results = []
total = (len(ORB_START) * len(BREAK_CONFIDENCE) * len(VOLUME_MULT)
         * len(ADX_THRESH) * len(ATR_MULT_STOP) * len(USE_VWAP)
         * len(ENTRY_END))
done = 0

for orb_start, break_conf, vol_mult, adx, atr_stop, vwap, entry_end in product(
    ORB_START, BREAK_CONFIDENCE, VOLUME_MULT, ADX_THRESH, ATR_MULT_STOP, USE_VWAP, ENTRY_END
):
    done += 1
    params = {
        "orb_start_minute": orb_start,
        "entry_break_confidence": break_conf,
        "volume_spike_mult": vol_mult,
        "adx_threshold": adx,
        "atr_mult_stop": atr_stop,
        "require_vwap_side": vwap,
        "market_open_utc_minutes": 14 * 60 + 30,
        "entry_end_utc_minutes": entry_end,
        "force_flat_utc_minutes": 20 * 60 + 55,
    }

    dh = SqliteDataHandler(db_path=Path(DB_PATH), symbols=["ES_continuous"], timeframe="5min")
    dh.load_range(datetime(2024, 1, 1), datetime(2024, 12, 31))

    strategy = ORBEnhanced(symbols=["ES_continuous"], params=params)
    engine = BacktestEngine(
        data_handler=dh,
        strategy=strategy,
        execution=SimulatedExecutionHandler(
            slippage=SlippageModel(fixed_ticks=0, fill_on_next_bar=False),
        ),
        risk_manager=DefaultRiskManager(
            sizing=PositionSizing(method=PositionSizingMethod.FIXED, fixed_quantity=2),
            limits=RiskLimits(
                max_daily_loss_pct=0.06, max_positions_per_day=3,
                max_concurrent_positions=1, require_no_position=True,
            ),
        ),
        initial_capital=100_000.0,
        contract_multiplier=50.0,
    )
    result = engine.run()
    m = result.metrics

    label = (f"ORB{orb_start}_C{break_conf}_V{vol_mult}_A{adx}_S{atr_stop}"
             f"_vwap{vwap}_ee{entry_end}")

    results.append({
        "label": label,
        "orb_start": orb_start,
        "break_conf": break_conf,
        "vol_mult": vol_mult,
        "adx": adx,
        "atr_stop": atr_stop,
        "vwap": vwap,
        "entry_end": entry_end,
        "trades": len(result.trades),
        "win_rate": m.win_rate,
        "sharpe": m.sharpe_ratio,
        "total_pnl": result.total_pnl,
        "total_return": m.total_return_pct,
        "max_dd": m.max_drawdown_pct,
        "avg_daily_trades": m.avg_daily_trades,
        "profit_factor": m.profit_factor,
    })

    if done % 20 == 0 or done == total:
        print(f"[{done}/{total}] last: {label} | trades={result.trades} sharpe={m.sharpe_ratio:.3f}")

# ── Print top results ──
results.sort(key=lambda r: r["sharpe"], reverse=True)
print("\n" + "=" * 100)
print("TOP 20 RESULTS (by Sharpe)")
print("=" * 100)
print(f"{'Rank':<5} {'Label':<50} {'Trades':>7} {'WR%':>6} {'Sharpe':>8} {'PnL':>10} {'Ret%':>7} {'DD%':>6} {'ADT':>5} {'PF':>6}")
print("-" * 95)
for i, r in enumerate(results[:20], 1):
    print(f"{i:<5} {r['label']:<50} {r['trades']:>7} {r['win_rate']:>5.1f}% {r['sharpe']:>8.4f} ${r['total_pnl']:>+8.0f} {r['total_return']:>6.2f}% {r['max_dd']:>5.1f}% {r['avg_daily_trades']:>4.1f} {r['profit_factor']:>5.3f}")

print(f"\nScan complete: {done} combinations")
