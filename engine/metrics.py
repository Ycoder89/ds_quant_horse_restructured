"""
engine/metrics.py — 回测评估指标

所有指标从 Trade 列表和每日 PnL 序列计算。
纯函数，不保存状态。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

from core.portfolio import Trade


# =============================================================================
# BacktestMetrics — 回测指标
# =============================================================================

@dataclass
class BacktestMetrics:
    """回测评估指标"""

    # 基础收益
    total_return_pct: float = 0.0        # 总收益率 %
    annualized_return_pct: float = 0.0   # 年化收益率 %

    # 风险调整收益
    sharpe_ratio: float = 0.0            # 夏普比率（日频）
    sortino_ratio: float = 0.0           # 索提诺比率

    # 回撤
    max_drawdown_pct: float = 0.0        # 最大回撤 %
    max_drawdown_duration: int = 0       # 最大回撤持续天数

    # 交易统计
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0                # 胜率 %

    avg_win: float = 0.0                 # 平均盈利
    avg_loss: float = 0.0                # 平均亏损
    profit_factor: float = 0.0           # 盈亏比
    expectancy: float = 0.0              # 期望收益

    avg_holding_bars: float = 0.0        # 平均持仓 bar 数

    # 交易频率
    total_trading_days: int = 0
    avg_daily_trades: float = 0.0        # 日均交易次数
    trades_on_win_days: int = 0
    trades_on_loss_days: int = 0

    # 综合评分
    composite_score: float = 0.0         # 综合评分（用于策略排序）

    def summary(self) -> str:
        """人类可读的指标摘要"""
        lines = [
            "=" * 60,
            "  Backtest Metrics Summary",
            "=" * 60,
            "",
            "  ── 收益 ──",
            f"    Total Return:       {self.total_return_pct:>8.2f}%",
            f"    Annualized Return:  {self.annualized_return_pct:>8.2f}%",
            "",
            "  ── 风险调整 ──",
            f"    Sharpe Ratio:       {self.sharpe_ratio:>8.4f}",
            f"    Sortino Ratio:      {self.sortino_ratio:>8.4f}",
            f"    Max Drawdown:       {self.max_drawdown_pct:>8.2f}%",
            "",
            "  ── 交易统计 ──",
            f"    Total Trades:       {self.total_trades:>8d}",
            f"    Win Rate:           {self.win_rate:>8.2f}%",
            f"    Profit Factor:      {self.profit_factor:>8.4f}",
            f"    Avg Win / Avg Loss: ${self.avg_win:>7.2f} / ${self.avg_loss:>7.2f}",
            f"    Expectancy:         ${self.expectancy:>8.2f}",
            "",
            "  ── 交易频率 ──",
            f"    Trading Days:       {self.total_trading_days:>8d}",
            f"    Avg Daily Trades:   {self.avg_daily_trades:>8.2f}",
            "",
            "  ── 综合评分 ──",
            f"    Composite Score:    {self.composite_score:>8.4f}",
            "=" * 60,
        ]
        return "\n".join(lines)

    def passes_threshold(
        self,
        min_sharpe: float = 1.0,
        min_avg_trades: float = 2.0,
        min_win_rate: float = 40.0,
    ) -> tuple[bool, str]:
        """
        检查是否通过准入阈值。

        Returns:
            (passed, reason)
        """
        reasons: list[str] = []
        if self.sharpe_ratio < min_sharpe:
            reasons.append(f"Sharpe={self.sharpe_ratio:.3f} < {min_sharpe}")
        if self.avg_daily_trades < min_avg_trades:
            reasons.append(f"avg_daily_trades={self.avg_daily_trades:.1f} < {min_avg_trades}")
        if self.win_rate < min_win_rate:
            reasons.append(f"win_rate={self.win_rate:.1f}% < {min_win_rate}%")

        if not reasons:
            return True, "ALL thresholds passed"
        return False, "; ".join(reasons)


# =============================================================================
# 指标计算函数
# =============================================================================

def compute_metrics(
    trades: list[Trade],
    total_bars: int,
    initial_capital: float = 100_000.0,
    trading_days: Optional[list[date]] = None,
) -> BacktestMetrics:
    """
    从交易记录列表计算完整回测指标。

    Args:
        trades: 已平仓的交易列表
        total_bars: 总 bar 数
        initial_capital: 初始资金
        trading_days: 交易日列表（用于计算日均交易）

    Returns:
        BacktestMetrics 对象
    """
    metrics = BacktestMetrics()
    closed = [t for t in trades if t.is_closed]

    # ── 交易统计 ──
    metrics.total_trades = len(closed)
    if metrics.total_trades == 0:
        return metrics

    pnls = np.array([t.pnl for t in closed if t.pnl is not None], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    metrics.win_count = len(wins)
    metrics.loss_count = len(losses)
    metrics.win_rate = (metrics.win_count / metrics.total_trades) * 100 if metrics.total_trades > 0 else 0.0

    metrics.avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    metrics.avg_loss = float(abs(np.mean(losses))) if len(losses) > 0 else 0.0
    metrics.profit_factor = float(np.sum(wins) / abs(np.sum(losses))) if np.sum(losses) != 0 else float("inf")
    metrics.expectancy = float(np.mean(pnls))

    # 平均持仓时间
    holding = np.array([t.holding_bars for t in closed if t.holding_bars is not None], dtype=float)
    metrics.avg_holding_bars = float(np.mean(holding)) if len(holding) > 0 else 0.0

    # ── 收益率 ──
    cum_pnl = float(np.cumsum(pnls)[-1]) if len(pnls) > 0 else 0.0
    metrics.total_return_pct = (cum_pnl / initial_capital) * 100

    # ── 交易频率 ──
    if trading_days:
        metrics.total_trading_days = len(trading_days)
        metrics.avg_daily_trades = metrics.total_trades / metrics.total_trading_days

        # 按日统计
        daily_pnls: dict[date, list[float]] = {}
        for t in closed:
            if t.exit_time is not None:
                d = t.exit_time.date()
                daily_pnls.setdefault(d, []).append(t.pnl or 0.0)

        win_days = sum(1 for dp in daily_pnls.values() if sum(dp) > 0)
        loss_days = sum(1 for dp in daily_pnls.values() if sum(dp) <= 0)
        metrics.trades_on_win_days = win_days
        metrics.trades_on_loss_days = loss_days

        # ── 夏普比率（日频） ──
        daily_returns = np.array([sum(dp) / initial_capital for dp in daily_pnls.values()], dtype=float)
        if len(daily_returns) > 1:
            mean_daily = np.mean(daily_returns)
            std_daily = np.std(daily_returns, ddof=1)
            if std_daily > 0:
                metrics.sharpe_ratio = float((mean_daily / std_daily) * math.sqrt(252))

            # ── 索提诺 ──
            downside = daily_returns[daily_returns < 0]
            if len(downside) > 0:
                downside_std = np.std(downside, ddof=1)
                if downside_std > 0:
                    metrics.sortino_ratio = float((mean_daily / downside_std) * math.sqrt(252))

            # ── 年化收益 ──
            num_years = metrics.total_trading_days / 252
            if num_years > 0:
                metrics.annualized_return_pct = metrics.total_return_pct / num_years

        # ── 最大回撤 ──
        equity_curve = [initial_capital]
        for dp in daily_pnls.values():
            equity_curve.append(equity_curve[-1] + sum(dp))
        equity = np.array(equity_curve[1:], dtype=float)  # 对齐交易日

        if len(equity) > 1:
            peak = np.maximum.accumulate(equity)
            drawdown = (peak - equity) / peak * 100
            metrics.max_drawdown_pct = float(np.max(drawdown))

            # 回撤持续时间
            in_drawdown = False
            max_dur = 0
            current_dur = 0
            for i in range(1, len(equity)):
                if equity[i] < peak[i]:
                    if not in_drawdown:
                        in_drawdown = True
                        current_dur = 1
                    else:
                        current_dur += 1
                    max_dur = max(max_dur, current_dur)
                else:
                    in_drawdown = False
                    current_dur = 0
            metrics.max_drawdown_duration = max_dur

    # ── 年化收益（基于 bar 数估算） ──
    if total_bars > 0 and metrics.annualized_return_pct == 0.0:
        # 5min bar 一年约 252天 * 78根 = 19656
        bars_per_year = 252 * 78
        num_years = total_bars / bars_per_year
        if num_years > 0:
            metrics.annualized_return_pct = metrics.total_return_pct / num_years

    # ── 综合评分（用于策略排序） ──
    metrics.composite_score = _compute_composite(
        sharpe=metrics.sharpe_ratio,
        win_rate=metrics.win_rate,
        avg_daily_trades=metrics.avg_daily_trades,
        profit_factor=metrics.profit_factor,
        max_dd=metrics.max_drawdown_pct,
    )

    return metrics


def _compute_composite(
    sharpe: float,
    win_rate: float,
    avg_daily_trades: float,
    profit_factor: float,
    max_dd: float,
) -> float:
    """
    综合评分（0-10 范围，越高越好）。

    权重设计：
      - Sharpe: 40%（核心风险调整收益）
      - Win Rate: 20%（交易质量）
      - Profit Factor: 20%（盈亏比）
      - Avg Daily Trades: 10%（交易频率，防止过拟合）
      - Max Drawdown penalty: 10%（回撤惩罚）
    """
    score = 0.0

    # Sharpe: 0→0, 1→0.4, 2→0.7, 3→1.0
    score += min(sharpe / 3.0, 1.0) * 4.0

    # Win Rate: 30%→0, 50%→0.6, 70%→1.0
    wr_score = max(0, (win_rate - 30) / 40)
    score += min(wr_score, 1.0) * 2.0

    # Profit Factor: 1.0→0, 1.5→0.5, 2.0→1.0
    pf_score = max(0, (profit_factor - 1.0) / 1.0)
    score += min(pf_score, 1.0) * 2.0

    # Avg Daily Trades: 1→0, 3→0.7, 5→1.0
    freq_score = max(0, (avg_daily_trades - 1) / 4)
    score += min(freq_score, 1.0) * 1.0

    # Max DD penalty: 10%→-0.5, 20%→-1.0
    dd_penalty = min(max_dd / 20.0, 1.0)
    score -= dd_penalty * 1.0

    return round(score, 4)
