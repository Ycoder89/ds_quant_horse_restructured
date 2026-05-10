"""
engine/backtest.py — 完整事件驱动回测引擎（v2）

完整事件链：
  DataEvent → Strategy.on_bar() → SignalEvent
           → RiskManager.on_signal() → OrderEvent
           → ExecutionHandler.execute_order() → FillEvent
           → Portfolio.on_fill() → Trade + PnL

主要改进（vs v1）：
  - 模拟成交（含滑点）
  - PnL 追踪（逐笔交易 + 每日 PnL）
  - ExitManager 集成
  - 完整评估指标输出
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from core.data_handler import DataHandler
from core.events import DataEvent, FillEvent, OrderEvent, OrderSide, SignalEvent
from core.execution import ExecutionHandler, SimulatedExecutionHandler
from core.exit import (
    CompositeExitManager,
    ExitManager,
    ExitSignal,
    FixedStopExit,
    Position,
    TakeProfitExit,
    TimeStopExit,
    TrailingStopExit,
)
from core.filters import FilterContext
from core.portfolio import Portfolio, SimplePortfolio, Trade
from core.regime import RegimeClassifier, RegimeState, RegimeType
from core.risk_manager import DefaultRiskManager, RiskManager
from core.strategy import Strategy
from engine.metrics import BacktestMetrics, compute_metrics

logger = logging.getLogger("ds_quant_horse.backtest")


# =============================================================================
# 回测结果（v2）
# =============================================================================

@dataclass
class BacktestResult:
    """
    回测结果（v2：含 PnL 和指标）。

    包含从 BacktestEngine.run() 返回的完整信息。
    """
    strategy_name: str = ""
    total_bars: int = 0
    trading_days: list[date] = field(default_factory=list)

    # 信号统计
    signals_fired: int = 0
    signals_passed: int = 0
    signals_blocked_filter: int = 0
    signals_blocked_risk: int = 0

    # 交易和 PnL
    trades: list[Trade] = field(default_factory=list)
    total_pnl: float = 0.0
    initial_capital: float = 100_000.0

    # 评估指标
    metrics: Optional[BacktestMetrics] = None

    # Regime 统计
    regime_skipped_days: int = 0      # 因 CHOPPY/HIGH_VOL 跳过的交易日数

    # 参数（用于策略搜索对比）
    params: dict = field(default_factory=dict)

    @property
    def final_capital(self) -> float:
        return self.initial_capital + self.total_pnl

    @property
    def return_pct(self) -> float:
        return (self.total_pnl / self.initial_capital) * 100

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"  Backtest Result: {self.strategy_name}",
            "=" * 60,
            f"  Bars: {self.total_bars}  Days: {len(self.trading_days)}",
            f"  Signals: {self.signals_fired} fired, "
            f"{self.signals_passed} passed, "
            f"{self.signals_blocked_filter} filter, "
            f"{self.signals_blocked_risk} risk",
            f"  Trades: {len(self.trades)}  PnL: ${self.total_pnl:+.2f}",
        ]
        if self.regime_skipped_days > 0:
            lines.append(f"  Regime Skipped Days: {self.regime_skipped_days}")
        if self.metrics:
            lines.append("")
            for line in self.metrics.summary().split("\n"):
                lines.append(line)
        return "\n".join(lines)


# =============================================================================
# 回测引擎（v2）
# =============================================================================

class BacktestEngine:
    """
    完整事件驱动回测引擎。

    流程（每根 bar）：
      1. 处理挂单成交
      2. 检查持仓退出
      3. 策略信号生成
      4. 风控过滤
      5. 提交入场订单
      6. 记录统计

    Usage:
        engine = BacktestEngine(
            data_handler=SqliteDataHandler(...),
            strategy=ORBEnhanced(...),
        )
        result = engine.run()
        print(result.metrics.sharpe_ratio)
    """

    def __init__(
        self,
        data_handler: DataHandler,
        strategy: Strategy,
        risk_manager: Optional[RiskManager] = None,
        execution: Optional[ExecutionHandler] = None,
        portfolio: Optional[Portfolio] = None,
        exit_manager: Optional[ExitManager] = None,
        initial_capital: float = 100_000.0,
        contract_multiplier: float = 1.0,
        regime_classifier: Optional[RegimeClassifier] = None,
        regime_daily_lookback: int = 30,
        regime_confirm_hour: int = 10,   # 10:00 AM 后才允许入场（Regime 确认后）
    ) -> None:
        self._dh = data_handler
        self._strategy = strategy
        self._rm = risk_manager or DefaultRiskManager()
        self._execution = execution or SimulatedExecutionHandler()
        self._portfolio = portfolio or SimplePortfolio(
            initial_capital=initial_capital,
            contract_multiplier=contract_multiplier,
        )
        self._exit_mgr = exit_manager or CompositeExitManager([
            # 顺序: TrailingStop 先更新止损价 → FixedStop 用新止损检查 → TakeProfit → TimeStop
            TrailingStopExit(activation_r=0.5, trail_distance=0.3),
            FixedStopExit(atr_mult=1.5),
            TakeProfitExit(risk_reward=2.0),
            TimeStopExit(max_bars=18),  # 18 bars = 90 分钟
        ])
        # Regime 分类器（可选）
        self._regime_classifier = regime_classifier
        self._regime_daily_lookback = regime_daily_lookback
        self._regime_confirm_hour = regime_confirm_hour

        self._initial_capital = initial_capital
        self._result = BacktestResult(
            strategy_name=strategy.name,
            initial_capital=initial_capital,
        )

        # 内部状态
        self._current_position: Optional[Position] = None
        self._current_stop: Optional[float] = None
        self._pending_stop: Optional[float] = None  # 策略计算的止损（传递给 exit）
        self._last_date: Optional[date] = None
        self._current_regime: RegimeState = RegimeState()  # 当日 Regime 状态
        self._day_open_bars: list = []           # 当日开盘后已积累的 bar（用于盘后确认）
        self._regime_confirmed: bool = False     # 当日是否已完成盘后确认

    # ---- 事件回调 ----

    def _on_signal(self, signal: SignalEvent) -> None:
        """接收策略 SignalEvent → RiskManager → 提交订单"""
        self._result.signals_fired += 1
        order = self._rm.on_signal(signal)

        if order is not None:
            self._result.signals_passed += 1
            # 保存策略计算的止损价，供成交后传递给 ExitManager
            self._pending_stop = signal.stop_loss
            self._submit_entry(order)
        elif hasattr(self._rm, "_filter_registry"):
            # 尝试区分 filter 和 risk block
            try:
                _, reason = self._rm.state.can_trade(
                    getattr(self._rm, "_limits"),
                    getattr(self._rm, "_has_position", False),
                )
                if not reason:
                    self._result.signals_blocked_filter += 1
                else:
                    self._result.signals_blocked_risk += 1
            except Exception:
                self._result.signals_blocked_filter += 1

    # ---- 订单提交 ----

    def _submit_entry(self, order: OrderEvent) -> None:
        """提交入场订单到执行器"""
        self._execution.submit(order)
        logger.debug("Entry order submitted: %s", order)

    def _submit_exit(self, exit_sig: ExitSignal) -> None:
        """提交平仓订单并立即执行"""
        order = OrderEvent(
            symbol=exit_sig.symbol,
            side=exit_sig.side,
            quantity=abs(self._current_position.quantity) if self._current_position else 0,
            strategy=self._result.strategy_name,
        )
        if order.quantity <= 0:
            logger.warning("Exit order with zero quantity, skipping")
            return

        # 平仓单在当前 bar 收盘价立即成交
        fill = FillEvent(
            timestamp=self._current_bar.timestamp,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            fill_price=exit_sig.exit_price,
            strategy=order.strategy,
        )
        self._portfolio.on_fill(fill)
        self._rm.on_position_closed()

        self._current_position = None
        self._current_stop = None
        logger.info("Exit: %s %d×%s @ %.2f | type=%s",
                     exit_sig.side.value, order.quantity, exit_sig.symbol,
                     exit_sig.exit_price, exit_sig.exit_type.name)

    # ---- 主循环 ----

    def run(self) -> BacktestResult:
        """执行完整回测"""
        logger.info("BacktestEngine.run() starting... [strategy=%s]", self._strategy.name)

        self._strategy.on_start()
        self._strategy.set_event_callback(self._on_signal)

        self._current_bar: Optional[DataEvent] = None

        symbol = self._strategy.symbols[0]

        for event in self._dh.stream():
            self._current_bar = event
            self._result.total_bars += 1
            bar_date = event.timestamp.date()

            # 跨日处理
            if bar_date != self._last_date:
                if self._last_date is not None:
                    self._handle_eod()
                self._result.trading_days.append(bar_date)
                self._last_date = bar_date
                self._day_open_bars = []
                self._regime_confirmed = False
                self._strategy.on_session_start(bar_date.isoformat())

                # ── 盘前 Regime 分类 ──────────────────────────────────────────
                if self._regime_classifier is not None:
                    daily_bars = self._dh.get_daily_bars(
                        symbol=symbol,
                        end_date=bar_date,
                        lookback=self._regime_daily_lookback,
                    )
                    self._current_regime = self._regime_classifier.classify_premarket(
                        daily_bars
                    )
                    self._strategy.on_regime_change(self._current_regime)
                    logger.info(
                        "[Regime] %s 盘前: %s (conf=%.2f)",
                        bar_date, self._current_regime.regime_type.value,
                        self._current_regime.confidence,
                    )
                    # CHOPPY 当日直接跳过
                    if not self._current_regime.can_trade:
                        logger.info("[Regime] %s 当日跳过（%s）",
                                    bar_date, self._current_regime.regime_type.value)
                        self._result.regime_skipped_days += 1

            # 取主要标的的 bar
            bar = event.get_bar(symbol)
            if bar is None:
                continue

            # ── 当日跳过（CHOPPY 或 size_mult=0）───────────────────────────────
            if (self._regime_classifier is not None
                    and not self._current_regime.can_trade):
                # 即使跳过也要处理持仓成交（避免挂单堆积）
                fills = self._execution.process_bar(bar)
                for fill in fills:
                    self._portfolio.on_fill(fill)
                continue

            # ── 盘后 Regime 确认 (~10:00 AM) ─────────────────────────────────
            bar_hour = event.timestamp.hour
            bar_minute = event.timestamp.minute
            if (self._regime_classifier is not None
                    and not self._regime_confirmed):
                self._day_open_bars.append(bar)
                # 到达确认时间且有足够 bar 时执行确认
                if (bar_hour >= self._regime_confirm_hour
                        or (bar_hour == self._regime_confirm_hour - 1 and bar_minute >= 55)):
                    self._current_regime = self._regime_classifier.confirm_postopen(
                        self._current_regime, self._day_open_bars
                    )
                    self._regime_confirmed = True
                    self._strategy.on_regime_change(self._current_regime)
                    logger.info(
                        "[Regime] %s 盘后确认: %s (conf=%.2f, size=%.1f×)",
                        bar_date, self._current_regime.regime_type.value,
                        self._current_regime.confidence,
                        self._current_regime.size_multiplier,
                    )
                    if not self._current_regime.can_trade:
                        logger.info("[Regime] 盘后确认后跳过 (%s)",
                                    self._current_regime.regime_type.value)
                        self._result.regime_skipped_days += 1

            # ── 盘后确认时间前：不入场（持仓正常管理）────────────────────────
            regime_entry_blocked = (
                self._regime_classifier is not None
                and not self._regime_confirmed
                and bar_hour < self._regime_confirm_hour
            )

            # ── 第一步：检查持仓退出（优先于入场）───────────────────────────
            if self._current_position is not None and not self._current_position.is_flat:
                should_exit, exit_sig, new_stop = self._exit_mgr.check(
                    self._current_position, bar, self._current_stop,
                )
                if new_stop is not None:
                    self._current_stop = new_stop
                if should_exit and exit_sig is not None:
                    self._submit_exit(exit_sig)

            # ── 第二步：策略信号生成（Regime 确认后才允许入场） ──────────────
            if not regime_entry_blocked:
                signal = self._strategy.on_bar(event)
                if signal is not None:
                    self._on_signal(signal)

            # ── 第三步：处理挂单成交 ─────────────────────────────────────────
            fills = self._execution.process_bar(bar)
            for fill in fills:
                self._portfolio.on_fill(fill)
                self._rm.on_fill(fill.fill_value, self._calc_fill_pnl(fill, is_entry=True))

                # 更新持仓状态
                pos = self._portfolio.get_position(fill.symbol)
                if pos and not pos.is_flat:
                    self._current_position = Position(
                        symbol=fill.symbol,
                        quantity=pos.quantity,
                        avg_entry_price=pos.avg_price,
                    )
                    self._current_stop = self._calc_stop_price(fill)

        # 结束：平所有持仓
        self._handle_eod()
        self._strategy.on_finish()

        # 构建结果
        self._build_result()
        logger.info("BacktestEngine.run() finished. Trades: %d, PnL: %.2f",
                     len(self._result.trades), self._result.total_pnl)
        return self._result

    # ---- EOD / 清理 ----

    def _handle_eod(self) -> None:
        """日终处理：平所有持仓"""
        if self._current_position is None:
            return
        if not self._current_position.is_flat:
            # 在最后已知价格平仓
            exit_price = self._calc_eod_price()
            symbol = self._current_position.symbol
            exit_side = OrderSide.SELL if self._current_position.is_long() else OrderSide.BUY
            exit_sig = ExitSignal(
                symbol=symbol,
                side=exit_side,
                exit_price=exit_price,
                exit_type=type("ExitType", (), {"name": "EOD"})(),
                reason="EOD force flat",
            )
            self._submit_exit(exit_sig)
            logger.info("EOD force flat: %s @ %.2f", symbol, exit_price)

    def _calc_eod_price(self) -> float:
        """估算 EOD 平仓价"""
        if self._current_bar:
            symbol = self._strategy.symbols[0]
            bar = self._current_bar.get_bar(symbol)
            if bar:
                return bar.close
        return 0.0

    # ---- 辅助 ----

    def _calc_fill_pnl(self, fill: FillEvent, is_entry: bool) -> float:
        """估算成交 PnL（入口处简单估算）"""
        return 0.0  # PnL 在 Trade 级别计算，这里不影响

    def _calc_stop_price(self, fill: FillEvent) -> float:
        """根据入场成交估算初始止损价（优先用策略计算的止损）"""
        if self._pending_stop is not None:
            stop = self._pending_stop
            self._pending_stop = None
            return stop
        # fallback: 1% 估算
        if fill.side is OrderSide.BUY:
            return fill.fill_price * 0.99
        return fill.fill_price * 1.01

    def _build_result(self) -> None:
        """从 Portfolio 收集结果"""
        self._result.trades = list(self._portfolio.trades)
        self._result.total_pnl = self._portfolio.total_realized_pnl

        # 计算指标
        self._result.metrics = compute_metrics(
            trades=self._result.trades,
            total_bars=self._result.total_bars,
            initial_capital=self._initial_capital,
            trading_days=self._result.trading_days,
        )

    def reset(self) -> None:
        """重置引擎（可复用运行）"""
        self._result = BacktestResult(
            strategy_name=self._strategy.name,
            initial_capital=self._initial_capital,
        )
        self._current_position = None
        self._current_stop = None
        self._pending_stop = None
        self._last_date = None
        self._current_bar = None
        self._execution.reset()
        self._portfolio.reset()
