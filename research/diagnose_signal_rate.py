"""
research/diagnose_signal_rate.py — 信号频率诊断工具

逐Bar分析 ORB Enhanced 策略在 2025 全年 TSLA 上的信号生成瓶颈：
  - 统计每个阶段被拒的Bar数量
  - 找出是哪个条件在拦截信号
  - 为参数放松提供数据支持

用法：
    python research/diagnose_signal_rate.py
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime, time

sys.path.insert(0, r"d:\Python_Projects\ds_quant_horse")

from core.data_handler import SqliteDataHandler
from core.events import Bar, DataEvent, OrderSide

logging.basicConfig(
    level=logging.WARNING,  # 屏蔽详细日志
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("diagnose")


class DiagnosticORB:
    """带诊断计数器的 ORB 策略，不发出信号，只统计各阶段的 bar 数"""

    def __init__(self, orb_start_minute: int = 5, entry_break_confidence: float = 0.3):
        self.orb_start_minute = orb_start_minute
        self.entry_break_confidence = entry_break_confidence

        # 日内状态
        self.orb_high: float | None = None
        self.orb_low: float | None = None
        self.orb_formed = False
        self.signal_fired_today = False

        # 诊断计数器
        self.stats = defaultdict(int)  # 全局统计
        self.daily_stats: list[dict] = []

        # 当日计数器
        self._reset_daily_counters()

    def _reset_daily_counters(self):
        self.daily = defaultdict(int)

    def on_session_start(self):
        self.orb_high = None
        self.orb_low = None
        self.orb_formed = False
        self.signal_fired_today = False
        self._reset_daily_counters()

    def _is_entry_time(self, t: time) -> bool:
        market_open = 9 * 60 + 30
        entry_start = _minutes_to_time(market_open + self.orb_start_minute)
        entry_end = time(11, 30)
        force_flat = time(15, 55)
        return entry_start <= t <= entry_end and t < force_flat

    def on_bar(self, event: DataEvent, bar: Bar) -> dict | None:
        """返回诊断信息（或不返回 None）"""
        ts = event.timestamp
        minute_of_day = ts.hour * 60 + ts.minute
        market_open = 9 * 60 + 30
        orb_end_minutes = market_open + self.orb_start_minute

        # ---- 阶段 0：整体统计 ----
        self.daily["total_bars"] += 1

        # ---- 阶段 1：ORB 收集期 ----
        if not self.orb_formed:
            self.daily["orb_collecting"] += 1
            if self.orb_high is None:
                self.orb_high = bar.high
                self.orb_low = bar.low
            else:
                self.orb_high = max(self.orb_high, bar.high)
                self.orb_low = min(self.orb_low, bar.low)

            if minute_of_day >= orb_end_minutes and self.orb_high is not None:
                self.orb_formed = True
                self.daily["orb_formed"] = 1
                # 记录 ORB 区间
                self.daily["orb_width"] = self.orb_high - self.orb_low
            return None

        # ---- 阶段 2：已发信号 ----
        if self.signal_fired_today:
            self.daily["skipped_already_fired"] += 1
            return None

        # ---- 阶段 3：进场时间窗口 ----
        self.daily["in_entry_window"] += 1
        if not self._is_entry_time(bar.timestamp.time()):
            self.daily["blocked_time"] += 1
            return None

        # ---- 阶段 4：K线方向对齐 ----
        self.daily["after_time_filter"] += 1

        signal_side = None
        if bar.is_bullish and bar.close > self.orb_high:
            signal_side = OrderSide.BUY
        elif not bar.is_bullish and bar.close < self.orb_low:
            signal_side = OrderSide.SELL

        if signal_side is None:
            if bar.close > self.orb_high:
                self.daily["blocked_kline_direction_long"] += 1
            elif bar.close < self.orb_low:
                self.daily["blocked_kline_direction_short"] += 1
            else:
                self.daily["no_breakout"] += 1
            return None

        # ---- 阶段 5：信号生成！ ----
        self.signal_fired_today = True
        self.daily["signals_generated"] += 1
        self.daily["signal_side"] = signal_side.value
        self.daily["signal_time"] = str(bar.timestamp)

        # 计算 confidence buffer
        orb_width = self.orb_high - self.orb_low
        buffer_dist = orb_width * self.entry_break_confidence
        self.daily["buffer_distance"] = buffer_dist
        self.daily["entry_price"] = (
            self.orb_high + buffer_dist if signal_side == OrderSide.BUY
            else self.orb_low - buffer_dist
        )

        return {"signal": True, "side": signal_side.value}


def _minutes_to_time(total_minutes: int) -> time:
    return time(total_minutes // 60, total_minutes % 60)


def main():
    print("=" * 70)
    print("  信号频率诊断 — ORB Enhanced (TSLA, 5min, 2025)")
    print("=" * 70)

    # 加载数据
    db_path = r"D:\Python_Projects\cc_quant_horse\data\db\stocks_data.db"
    dh = SqliteDataHandler(
        db_path=db_path,
        symbols=["TSLA"],
        timeframe="5min",
    )
    dh.load_range(
        start=datetime(2025, 1, 1),
        end=datetime(2025, 12, 31),
    )

    # 诊断策略
    strategy = DiagnosticORB(orb_start_minute=5, entry_break_confidence=0.3)

    last_date: str | None = None
    all_daily: list[dict] = []

    for event in dh.stream():
        bar = event.get_bar("TSLA")
        if bar is None:
            continue

        date_str = event.timestamp.strftime("%Y-%m-%d")
        if date_str != last_date:
            if last_date is not None:
                all_daily.append(dict(strategy.daily))
            strategy.on_session_start()
            last_date = date_str

        strategy.on_bar(event, bar)

    # 最后一个交易日
    if last_date is not None:
        all_daily.append(dict(strategy.daily))

    # ---- 汇总分析 ----
    print(f"\n总交易天数: {len(all_daily)}")

    days_with_orb = [d for d in all_daily if d.get("orb_formed")]
    days_with_signal = [d for d in all_daily if d.get("signals_generated", 0) > 0]
    days_no_signal = [d for d in all_daily if d.get("signals_generated", 0) == 0]

    print(f"\nORB 成功形成的天数: {len(days_with_orb)} / {len(all_daily)} ({len(days_with_orb)/max(len(all_daily),1)*100:.1f}%)")
    print(f"产生信号的天数:     {len(days_with_signal)} / {len(days_with_orb)} ({len(days_with_signal)/max(len(days_with_orb),1)*100:.1f}%)")
    print(f"有ORB但无信号的天数: {len(days_no_signal)} / {len(days_with_orb)} ({len(days_no_signal)/max(len(days_with_orb),1)*100:.1f}%)")

    total_signals = sum(d.get("signals_generated", 0) for d in all_daily)
    print(f"\n总信号数: {total_signals}")

    # 瓶颈分析：取有ORB但无信号的日子
    if days_no_signal:
        print(f"\n--- 瓶颈分析：有ORB但无信号的 {len(days_no_signal)} 天 ---")

        # 分析这些天里，bar进入入场窗口后为什么没产生信号
        time_blocked = sum(d.get("blocked_time", 0) for d in days_no_signal)
        after_time = sum(d.get("after_time_filter", 0) for d in days_no_signal)
        no_breakout = sum(d.get("no_breakout", 0) for d in days_no_signal)
        kline_long = sum(d.get("blocked_kline_direction_long", 0) for d in days_no_signal)
        kline_short = sum(d.get("blocked_kline_direction_short", 0) for d in days_no_signal)

        print(f"  - 时间窗口外的 bar: {time_blocked}")
        print(f"  - 时间窗口内但无突破的 bar: {no_breakout}")
        print(f"  - 有突破但 K 线方向不对 (多头): {kline_long}")
        print(f"  - 有突破但 K 线方向不对 (空头): {kline_short}")
        print(f"  - 时间窗口内的总 bar: {after_time}")

        # 深入：在这些无信号天里，有多少天有价格突破但方向不对？
        days_with_wrong_direction = [
            d for d in days_no_signal
            if d.get("blocked_kline_direction_long", 0) > 0
            or d.get("blocked_kline_direction_short", 0) > 0
        ]
        print(f"\n  其中，价格突破但K线方向反的天数: {len(days_with_wrong_direction)}")
        print(f"  价格从未突破ORB区间的天数: {len(days_no_signal) - len(days_with_wrong_direction)}")

    # 有信号的天数详情
    if days_with_signal:
        print(f"\n--- 有信号的 {len(days_with_signal)} 天详情 ---")
        for d in days_with_signal[:10]:
            print(f"  {d.get('signal_time', '?')} | side={d.get('signal_side', '?')} | "
                  f"orb_width={d.get('orb_width', 0):.2f} | "
                  f"buffer={d.get('buffer_distance', 0):.2f} | "
                  f"entry={d.get('entry_price', 0):.2f}")

    # ORB 区间统计
    orb_widths = [d.get("orb_width", 0) for d in days_with_orb if d.get("orb_width", 0) > 0]
    if orb_widths:
        import statistics
        print(f"\n--- ORB 区间宽度统计 (${'TSLA'}) ---")
        print(f"  均值:  {statistics.mean(orb_widths):.2f}")
        print(f"  中位:  {statistics.median(orb_widths):.2f}")
        print(f"  最小:  {min(orb_widths):.2f}")
        print(f"  最大:  {max(orb_widths):.2f}")
        print(f"  标准差:{statistics.stdev(orb_widths):.2f}")

    # 信号月分布
    print(f"\n--- 信号月度分布 ---")
    monthly = defaultdict(int)
    for d in days_with_signal:
        ts = d.get("signal_time", "")
        if ts:
            month = ts[:7]
            monthly[month] += 1
    for m in sorted(monthly.keys()):
        print(f"  {m}: {monthly[m]} 信号")

    print(f"\n{'=' * 70}")
    print("  诊断结束")
    print("=" * 70)


if __name__ == "__main__":
    main()