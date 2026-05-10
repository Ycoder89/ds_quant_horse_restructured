"""
research/strategy_hunter/report.py — 策略猎手对比报告生成

生成格式化的排名报告和简单的 CSV 导出。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from research.strategy_hunter.store import ResultRecord, ResultStore


def generate_report(
    db_path: str | Path,
    strategy: str = "",
    n: int = 20,
    output_path: Optional[Path] = None,
) -> str:
    """
    生成策略排名报告。

    Args:
        db_path: 结果数据库路径
        strategy: 过滤策略名
        n: 返回前 N 个
        output_path: 可选，写入文件

    Returns:
        报告文本
    """
    store = ResultStore(db_path)
    top = store.get_top(n=n, strategy=strategy, min_trades=5)
    total = store.count(strategy)
    passed = len(store.get_top(n=9999, strategy=strategy, min_trades=5))

    lines = [
        "=" * 80,
        "  Strategy Hunter Report",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"  Database: {db_path}",
        f"  Strategy: {strategy or 'ALL'}",
        f"  Total candidates: {total}  (≥5 trades: {passed})",
        "=" * 80,
        "",
    ]

    if not top:
        lines.append("  No results found.")
        store.close()
        return "\n".join(lines)

    # 表头
    header = (
        f"{'Rank':>4} {'Score':>7} {'Sharpe':>7} {'Sortino':>8} "
        f"{'Ret%':>7} {'DD%':>6} {'Win%':>6} {'PF':>6} "
        f"{'Trades':>6} {'AvgD':>5} {'Pass':>4}  Params"
    )
    lines.append(header)
    lines.append("-" * 80)

    for i, r in enumerate(top, 1):
        fp = r.params
        # 选择最关键的参数展示
        brief = " ".join(f"{k}={v}" for k, v in sorted(fp.items())[:6])

        lines.append(
            f"{i:>4} {r.composite_score:>7.3f} {r.sharpe_ratio:>7.3f} "
            f"{r.sortino_ratio:>8.3f} {r.total_return_pct:>7.2f} "
            f"{r.max_drawdown_pct:>6.2f} {r.win_rate:>6.1f} "
            f"{r.profit_factor:>6.2f} {r.total_trades:>6} "
            f"{r.avg_daily_trades:>5.1f} {'✓' if r.passed_threshold else '':>4} "
            f" {brief}"
        )

    # 通过阈值的候选
    passing = [r for r in top if r.passed_threshold]
    if passing:
        lines.extend([
            "",
            "─" * 80,
            f"  PASSING ({len(passing)}/{len(top)}):",
            "─" * 80,
        ])
        for r in passing:
            lines.append(
                f"  Sharpe={r.sharpe_ratio:.3f} Win={r.win_rate:.1f}% "
                f"Trades={r.total_trades} AvgD={r.avg_daily_trades:.1f} "
                f"Score={r.composite_score:.3f}"
            )

    store.close()

    report = "\n".join(lines)

    if output_path:
        output_path.write_text(report, encoding="utf-8")

    return report


def export_csv(
    db_path: str | Path,
    output_path: Path,
    strategy: str = "",
    n: int = 200,
) -> None:
    """
    导出结果为 CSV。

    Args:
        db_path: 结果数据库
        output_path: CSV 输出路径
        strategy: 策略名过滤
        n: 最大行数
    """
    import csv

    store = ResultStore(db_path)
    top = store.get_top(n=n, strategy=strategy, min_trades=0)

    fieldnames = [
        "composite_score", "sharpe_ratio", "sortino_ratio",
        "total_return_pct", "max_drawdown_pct", "win_rate",
        "profit_factor", "total_trades", "avg_daily_trades",
        "passed_threshold", "strategy",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in top:
            writer.writerow(r.__dict__)

    store.close()
    print(f"Exported {len(top)} rows to {output_path}")
