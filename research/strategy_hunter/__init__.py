"""
research/strategy_hunter/ — 策略自动发现管道

流程:
  1. 定义策略参数网格 (param_grid.py)
  2. 批量回测所有参数组合 (runner.py)
  3. 存储结果到 SQLite (store.py)
  4. 生成对比报告 (report.py)

使用示例:
    from research.strategy_hunter.runner import StrategyHunter
    from research.strategy_hunter.param_grid import OrbParamGrid
    from research.strategy_hunter.store import ResultStore

    hunter = StrategyHunter(
        strategy_class=ORBEnhanced,
        param_grid=OrbParamGrid.default(),
        db_path="path/to/db",
        symbols=["TSLA"],
        start="2024-01-01",
        end="2024-12-31",
    )
    results = hunter.run()
    print(results)
"""
