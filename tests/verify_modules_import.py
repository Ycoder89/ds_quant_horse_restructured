"""全面验证 ds_quant_horse 所有模块导入"""
import sys
sys.path.insert(0, r'd:\Python_Projects\ds_quant_horse')

print("===== core 模块 =====")

from core.events import SignalEvent, Bar, OrderEvent, FillEvent
print("  ✅ core/events.py — SignalEvent, Bar, OrderEvent, FillEvent")

from core.filters import EntryFilter, VWAPSideFilter, VolumeSpikeFilter, FilterChain
print("  ✅ core/filters.py — EntryFilter, VWAPSideFilter, VolumeSpikeFilter, FilterChain")

from core.exit import ExitManager, FixedStopExit, TrailingStopExit
print("  ✅ core/exit.py — ExitManager, FixedStopExit, TrailingStopExit")

from core.strategy import Strategy, TimeConstraints
print("  ✅ core/strategy.py — Strategy, TimeConstraints")

from core.data_handler import DataHandler, SqliteDataHandler
print("  ✅ core/data_handler.py — DataHandler, SqliteDataHandler")

from core.risk_manager import RiskManager, DefaultRiskManager
print("  ✅ core/risk_manager.py — RiskManager, DefaultRiskManager")

from core.indicators import atr, adx, sma, vwap
print("  ✅ core/indicators.py — atr, adx, sma, vwap (纯函数)")

print("\n===== strategies 模块 =====")
from strategies.orb_enhanced import ORBEnhanced
print("  ✅ strategies/orb_enhanced.py — ORBEnhanced")

print("\n===== 总结 =====")
print("全部 8 个模块导入验证通过 ✅")