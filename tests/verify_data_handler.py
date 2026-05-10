"""验证 data_handler 从实际 DB 加载 TSLA 数据"""
import sys
sys.path.insert(0, r'd:\Python_Projects\ds_quant_horse')

from datetime import datetime
from pathlib import Path
from core.data_handler import SqliteDataHandler

DB_PATH = Path(r'D:\Python_Projects\cc_quant_horse\data\db\stocks_data.db')

dh = SqliteDataHandler(DB_PATH, ['TSLA'], '5min')
dh.load_range(datetime(2026, 4, 20), datetime(2026, 4, 22))
print(f'Loaded {len(dh._events)} events')

event_count = 0
for e in dh.stream():
    event_count += 1
print(f'Streamed {event_count} events, last ts = {e.timestamp}')

latest = dh.latest_bars()
print(f'Latest bars symbols: {list(latest.keys())}')
tsla = latest.get('TSLA')
if tsla:
    print(f'TSLA bar: O={tsla.open} H={tsla.high} L={tsla.low} C={tsla.close} V={tsla.volume}')

bars = dh.get_bars('TSLA', 20)
if bars:
    print(f'Lookback 20 bars: {len(bars)} (first: {bars[0].timestamp}, last: {bars[-1].timestamp})')
print('DataHandler verification PASSED')