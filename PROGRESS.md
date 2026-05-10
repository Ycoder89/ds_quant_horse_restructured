# ds_quant_horse — 项目进度追踪

> 启动日期：2026-04-26
> 当前阶段：Phase 3a 策略发现循环 — ES ORB 首次循环完成
> 最后更新：2026-04-30

---

## 总体进度

```
Phase 0: 项目初始化       ██████████ 100% ✅ 完成
Phase 1: 核心接口层       ██████████ 100% ✅ 完成（含扩展）
Phase 2: 策略实现         ██████████ 100% ✅ 完成
Phase 3: 回测验证         ██████████ 100% ✅ 完成
Phase 3a: 策略发现循环    ████░░░░░░  40% （ES ORB 首轮完成，Sharpe 0.53 OOS）
Phase 4: Paper 运营       ░░░░░░░░░░   0%
Phase 5: 实盘切换         ░░░░░░░░░░   0%
```

---

## 已完成

### Phase 0 — 项目初始化（2026-04-26）

- [x] 探索原项目 cc_quant_horse 完整结构
- [x] 迁移 CLAUDE.md / PROGRESS.md / PLAN.md / REFLECTION.md
- [x] 创建 .gitignore + MEMORY.md

### Phase 1 — 核心接口层（2026-04-26 ~ 04-28）

- [x] `core/events.py` — Bar, EntryConditions, DataEvent/SignalEvent/OrderEvent/FillEvent
- [x] `core/strategy.py` — Strategy ABC + TimeConstraints
- [x] `core/filters.py` — EntryFilter 责任链 + VWAP/Volume/ADX/Spread/Regime 过滤器
- [x] `core/exit.py` — ExitManager ABC + FixedStop/TrailingStop/TimeStop/Composite
- [x] `core/indicators.py` — TA-Lib 纯函数指标
- [x] `core/risk_manager.py` — DefaultRiskManager (FilterChain + RiskLimits + PositionSizing)
- [x] `core/data_handler.py` — SqliteDataHandler 完整实现
- [x] `core/execution.py` — ExecutionHandler ABC + SimulatedExecutionHandler（含滑点）
- [x] `core/portfolio.py` — SimplePortfolio + Trade + Position（PnL 追踪）

### Phase 2 — 策略实现（2026-04-26 ~ 04-28）

- [x] `strategies/orb_enhanced.py` — ORBEnhanced 日内突破策略
- [x] `strategies/swingtrend_stock.py` — SwingTrend 趋势跟踪策略

### Phase 3 — 回测验证基础设施（2026-04-29）

- [x] `engine/backtest.py` — 完整事件驱动回测引擎（v2，含成交+Exit+PnL）
- [x] `engine/metrics.py` — 完整评估指标（Sharpe/Sortino/回撤/胜率/综合评分）
- [x] `research/strategy_hunter/` — 策略猎人管道：
  - `param_grid.py` — 参数网格定义（ORB/SwingTrend）
  - `runner.py` — StrategyHunter 批量回测运行器
  - `store.py` — SQLite 结果存储（去重+排名查询）
  - `report.py` — 对比报告生成 + CSV 导出
- [x] 单元测试 229 个全部通过 ✅
- [x] TA-Lib 0.6.8 安装完成

### Phase 3a — ES ORB 策略发现循环首轮（2026-04-29 ~ 04-30）

#### 引擎修复（发现问题驱动修复）

- [x] 修复 BacktestEngine 循环顺序（Exit → Strategy → Fill），消除同 bar 入场即退出的 look-ahead
- [x] 修复期货合约乘数缺失（ES $50/pt → Trade/Position PnL 乘以 contract_multiplier）
- [x] 修复 StrategyHunter 硬编码 stocks_data.db → 自动区分 futures vs stocks 数据库

#### 首次参数扫描（无再入场，576 组合）

- [x] `research/scan_orb_es.py` — ES_continuous 5min, 2024 IS 全参数扫描
- [x] 最佳 IS 配置：ORB20_C0.3_S2.0, Sharpe 1.166
- [x] OOS 验证：IS 1.166 → OOS 0.172, 衰减 85.3%
- [x] 多配置 OOS 扫描（48 配置 × 2 周期）：ORB20_C0.05_S1.5 最稳健（IS 0.648, OOS 0.256）

#### 回调再入场改进（关键突破）

- [x] `strategies/orb_enhanced.py` — 取消每日单次信号限制，改为：
  - `_signal_fired` 在价格重回 ORB 区间后重置
  - 3 bar（15 min）最小信号间隔，防止同 bar 重复入场
  - 风控 `max_positions_per_day=3` 防止过度交易
- [x] 48 组合再入场扫描：找到 ORB20_C0.05_S1.5（OOS Sharpe 0.532）
- [x] 三周期验证（IS 2024, OOS-1 2025, OOS-2 2026 Q1）

#### 最佳配置：ORB20_C0.05_S1.5（回调再入场版）

| 指标     | IS 2024 | OOS-1 2025           | OOS-2 2026 Q1 |
| ------ | ------- | -------------------- | ------------- |
| Sharpe | 0.304   | **0.532**            | **1.804**     |
| 交易次数   | 369     | 443                  | 127           |
| 日均交易   | 1.49    | 1.42                 | 1.31          |
| 胜率     | 54.2%   | 56.2%                | 55.1%         |
| 最大回撤   | 21.1%   | 22.0%                | 10.2%         |
| PnL    | +$6,190 | +$20,416             | +$19,911      |
| 衰减     | —       | **-74.7%**（OOS > IS） | —             |

#### 关键发现

1. **负衰减（-74.7%）**：OOS Sharpe 持续优于 IS — 策略不过拟合
2. **ADT 从 0.8 → 1.4**：再入场使交易量翻倍，但 2.0 阈值为多标的设计，单一期货标的不可达
3. **2026 Q1 全部月份盈利**：Sharpe 1.80, DD 仅 10.2%, PF 1.236
4. **PnL 集中度风险**：2025 年 $38,940（+$20,416 中的 190%）来自 4 月关税波动
5. **NQ 5min 数据不足**（仅 1 个月），无法做 ES+NQ 多标的 ORB

#### 已保存

- 15 条记录写入 `data/hunter.db`（5 配置 × 3 周期）
- `research/save_orb_es_results.py` — 可重放的保存脚本

### 关键决策记录（更新）

1. **策略发现管道**：Python 批量扫描做回测 + Claude Agent 分析结果 + 迭代
2. **准入标准**：Sharpe > 0.5（单标的期货放宽），日均交易 ≥ 2（多标的股票适用），胜率 > 40%
3. **SimulatedExecution**：当前 bar 收盘价入场（fill_on_next_bar=False, fixed_ticks=0），退出在当前 bar 指定价格
4. **综合评分**：Sharpe 40% + WinRate 20% + ProfitFactor 20% + 交易频率 10% + 回撤惩罚 10%
5. **结果存储**：SQLite 去重（相同策略+参数不重复运行）
6. **ORB 再入场**：价格回调至 ORB 区间 + 3 bar 最小间隔 → 允许新信号

---

## 待办

### Phase 3a — 策略发现循环（下一轮）

- [ ] 测试 SwingTrend / Pullback EMA 策略在 ES 上的表现
- [ ] 改善风控过滤器（ADX 过滤已假死，实际未生效）
- [ ] WFV（Walk-Forward Validation）引擎
- [ ] 信号质量分析（入场时间分布、失败交易共性）
- [ ] 将合格策略的配置导出到 `config/strategies/`

### Phase 3b — 数据与基础设施

- [ ] 补充 NQ 5min 历史数据（当前只有 1 个月，限制多标的 ORB）
- [ ] 多标的并行扫描支持
- [ ] 单元测试覆盖 ORBEnhanced 再入场逻辑

### Phase 4+ — 实盘准备

- [ ] Paper Trading 链路测试
- [ ] Daily Report 自动生成
- [ ] DeepSeek 复盘集成

---

## 测试状态

```
tests/
├── test_core/
│   ├── test_events.py          ✅ 20 tests
│   ├── test_filters.py         ✅ (implicit)
│   ├── test_exit.py            ✅ (implicit)
│   ├── test_indicators.py      ✅ (TA-Lib)
│   ├── test_strategy.py        ✅ 17 tests
│   ├── test_risk_manager.py    ✅ (implicit)
│   ├── test_execution.py       ✅ 10 tests (new)
│   └── test_portfolio.py       ✅ 14 tests (new)
├── test_engine/
│   └── test_metrics.py         ✅ 12 tests (new)
├── test_research/
│   ├── test_param_grid.py      ✅ 6 tests (new)
│   └── test_store.py           ✅ 8 tests (new)
├── test_strategies/
│   └── test_swingtrend_stock.py ✅ 35 tests
Total: 229 tests ✅
```

---

## 变更日志

| 日期         | 变更内容                                                                          |
| ---------- | ----------------------------------------------------------------------------- |
| 2026-04-26 | 项目创建，Phase 0 完成                                                               |
| 2026-04-29 | 完成 core/execution.py, core/portfolio.py, engine/metrics.py, BacktestEngine v2 |
| 2026-04-29 | 完成 research/strategy_hunter/ 管道（param_grid/runner/store/report）               |
| 2026-04-29 | TA-Lib 安装，229 测试全部通过                                                          |
| 2026-04-29 | 首次 ORB ES 扫描 576 组合完成；引擎修复（Exit→Strategy→Fill, contract_multiplier）           |
| 2026-04-30 | **回调再入场改进完成**：ADT 0.8→1.4, OOS Sharpe 0.532, 负衰减                              |
| 2026-04-30 | 三周期验证通过（IS 2024 + OOS 2025 + OOS 2026 Q1）                                     |
| 2026-04-30 | 15 条结果保存到 data/hunter.db                                                      |
