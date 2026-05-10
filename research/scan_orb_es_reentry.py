"""
Parameter scan with re-entry ORB: IS 2024 → OOS 2025.
Tests key ORB params with pullback re-entry enabled.
"""
import logging, sys
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

BASE = {
    "volume_spike_mult": 1.0,
    "adx_threshold": 15,
    "require_vwap_side": False,
    "market_open_utc_minutes": 14 * 60 + 30,
    "entry_end_utc_minutes": 19 * 60,
    "force_flat_utc_minutes": 20 * 60 + 55,
}

ORB_START = [5, 10, 15, 20]
BREAK_CONF = [0.05, 0.1, 0.2, 0.3]
ATR_STOP = [1.5, 2.0, 2.5]

def run_test(params, start, end):
    dh = SqliteDataHandler(db_path=Path(DB_PATH), symbols=["ES_continuous"], timeframe="5min")
    dh.load_range(datetime.fromisoformat(start), datetime.fromisoformat(end))
    strategy = ORBEnhanced(symbols=["ES_continuous"], params=params, name="orb_es_reentry")
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
    return m.sharpe_ratio, result.total_pnl, len(result.trades), m.avg_daily_trades, m.win_rate, m.max_drawdown_pct, m.profit_factor

results = []
total = len(ORB_START) * len(BREAK_CONF) * len(ATR_STOP)
done = 0

for orb, conf, atr in product(ORB_START, BREAK_CONF, ATR_STOP):
    done += 1
    label = f"ORB{orb}_C{conf}_S{atr}"
    p = {**BASE, "orb_start_minute": orb, "entry_break_confidence": conf, "atr_mult_stop": atr}

    is_s, is_pnl, is_trades, is_adt, is_wr, is_dd, is_pf = run_test(p, "2024-01-01", "2024-12-31")
    oos_s, oos_pnl, oos_trades, oos_adt, oos_wr, oos_dd, oos_pf = run_test(p, "2025-01-01", "2025-12-31")

    results.append((label, is_s, oos_s, is_pnl, oos_pnl, is_trades, oos_trades, is_adt, oos_adt, is_wr, oos_wr, is_dd, oos_dd, is_pf, oos_pf))
    print(f"[{done}/{total}] {label} | IS S={is_s:.3f} OOS S={oos_s:.3f} T={is_trades}/{oos_trades} ADT={is_adt:.1f}/{oos_adt:.1f}", flush=True)

# Sort by OOS Sharpe
results.sort(key=lambda r: r[2], reverse=True)

print(f"\n{'='*130}")
hdr = f"{'Config':<20} {'IS_SR':>7} {'OOS_SR':>7} {'IS_PnL':>10} {'OOS_PnL':>10} {'IS_T':>5} {'OOS_T':>5} {'IS_ADT':>6} {'OOS_ADT':>6} {'IS_WR':>6} {'OOS_WR':>6} {'IS_DD':>6} {'OOS_DD':>6} {'IS_PF':>7} {'OOS_PF':>7}"
print(hdr)
print("-" * 130)
for r in results:
    print(f"{r[0]:<20} {r[1]:>7.3f} {r[2]:>7.3f} ${r[3]:>+8.0f} ${r[4]:>+8.0f} {r[5]:>5} {r[6]:>5} {r[7]:>6.2f} {r[8]:>6.2f} {r[9]:>5.1f}% {r[10]:>5.1f}% {r[11]:>5.1f}% {r[12]:>5.1f}% {r[13]:>6.3f} {r[14]:>6.3f}")
