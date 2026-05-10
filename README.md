# ds_quant_horse — 日内量化交易系统（重构版）

> 基于 `cc_quant_horse` 的深度反思，从零重建的日内量化交易系统。  
> 引入 AI 辅助策略发现、TDD 驱动开发、模块化事件驱动架构。

---

## 项目背景与目的

本项目从旧项目 `cc_quant_horse` 迁移重构而来，核心动机是：

1. **旧项目积累了大量工程债务**：策略逻辑与执行层耦合、指标在多处重复实现、回测与实盘代码不共享、信号被过滤后无任何记录。
2. **策略发现缺乏系统化**：参数扫描靠手工，没有对比框架，没有 OOS 验证流程。
3. **风控问题严重**：均值回归策略在趋势日爆仓、软件止损不如 IB 原生、多终端共享账户导致幻象 PnL。

**目标**：构建一个可在 Interactive Brokers Paper Trading 账户上运行、经过严格回测验证的日内量化交易系统，交易标的为 ES/NQ 指数期货及 TSLA/NVDA 等活跃个股。

---

## 系统架构

### 事件驱动单向流

```
DataEvent
  └─► Strategy.on_bar()          # 纯信号生成，不含风控逻辑
        └─► SignalEvent
              └─► FilterChain    # 入场过滤责任链（VWAP/Volume/ADX/Regime）
                    └─► RiskManager.on_signal()  # 仓位计算 + 风控限制
                          └─► OrderEvent
                                └─► ExecutionHandler.execute()  # IB API / 模拟成交
                                      └─► FillEvent
                                            └─► Portfolio.on_fill()  # PnL 追踪
```

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **策略独立性** | 策略文件中禁止 `import ib_insync`，只调用 `emit_signal()` |
| **风控不可绕过** | 所有 OrderEvent 必须经过 RiskManager.on_signal() |
| **配置外置** | 代码中禁止硬编码 IP、端口、路径、合约参数、风控阈值 |
| **指标统一** | 所有指标计算通过 `core/indicators.py`，禁止策略内部重复实现 |
| **TDD 驱动** | 先写测试，再写实现，229 个单元测试全部通过 |
| **AI 辅助** | DeepSeek 辅助策略参数优化和每日复盘 |

---

## 目录结构

```
ds_quant_horse/
├── CLAUDE.md              # 工作约束文件（开发必读）
├── PROGRESS.md            # 进度追踪（详细变更日志）
├── REFLECTION.md          # 对旧项目的深度反思分析
├── PLAN.md                # 全面重构计划（6 阶段路线图）
├── PLAN_RECONSTRUCT.md    # DeepSeek 辅助的策略诊断与重构计划
│
├── core/                  # 核心抽象接口层（只放 ABC + dataclass，不放实现）
│   ├── events.py          # Bar / DataEvent / SignalEvent / OrderEvent / FillEvent
│   ├── strategy.py        # Strategy ABC + TimeConstraints
│   ├── filters.py         # EntryFilter 责任链（VWAP/Volume/ADX/Regime/Spread）
│   ├── exit.py            # ExitManager ABC + FixedStop/TrailingStop/TimeStop
│   ├── indicators.py      # TA-Lib 纯函数指标（ATR/ADX/EMA/VWAP/Pivots）
│   ├── risk_manager.py    # DefaultRiskManager（仓位计算 + 风控限制 + 日内状态）
│   ├── execution.py       # ExecutionHandler ABC + SimulatedExecutionHandler
│   ├── portfolio.py       # SimplePortfolio + Trade + Position（PnL 追踪）
│   └── data_handler.py    # SqliteDataHandler（从 SQLite 数据库加载历史数据）
│
├── strategies/            # 策略实现
│   ├── orb_enhanced.py    # ORBEnhanced — 开盘区间突破策略（含回调再入场）
│   ├── pullback_ema.py    # PullbackEMA — EMA 回调趋势策略
│   ├── swingtrend_stock.py # SwingTrend — 趋势跟踪策略（适用于个股）
│   ├── vwap_reversion.py  # VWAPReversion — VWAP 均值回归策略
│   └── config/
│       └── orb_tsla.yaml  # ORB TSLA 策略完整配置
│
├── engine/                # 回测引擎
│   ├── backtest.py        # BacktestEngine v2（完整事件驱动，含 Exit + PnL）
│   └── metrics.py         # BacktestMetrics（Sharpe/Sortino/回撤/胜率/综合评分）
│
├── research/              # 策略发现与分析
│   ├── strategy_hunter/   # 批量参数扫描管道
│   │   ├── param_grid.py  # 参数网格定义
│   │   ├── runner.py      # StrategyHunter 批量回测运行器
│   │   ├── store.py       # SQLite 结果存储（去重 + 排名）
│   │   └── report.py      # 对比报告生成 + CSV 导出
│   ├── hunter_results/    # 各策略扫描结果数据库
│   ├── scan_orb_es.py     # ES ORB 参数扫描脚本
│   ├── diagnose_signal_rate.py  # 信号质量诊断工具
│   └── ES_ORB_RESEARCH_LOG.md  # ES ORB 研究日志
│
├── tests/                 # 单元测试（TDD 优先）
│   ├── test_core/         # core 层测试
│   ├── test_engine/       # 引擎测试
│   ├── test_research/     # 研究工具测试
│   └── test_strategies/   # 策略测试
│
├── data/                  # 数据层
│   ├── futures.db         # 期货数据（从 cc_quant_horse 复用，只读）
│   └── hunter.db          # 策略发现结果存储
│
├── config/                # YAML 配置文件（全局设置）
├── main_backtest.py       # 回测主入口
└── main_swingtrend.py     # SwingTrend 回测入口
```

---

## 已实现功能

### 核心基础设施（Phase 1 ✅）

- **事件系统**：精简的 Bar + 4 种事件类型，相比旧项目减少 33% 字段冗余
- **入场过滤责任链**：FilterChain 根据 EntryConditions 自动组装过滤器链
  - VWAPSideFilter：入场价格必须在 VWAP 同侧
  - VolumeSpikeFilter：当前成交量 > 均量 × 倍数
  - ADXFilter：趋势强度最小值（ADX 过滤）
  - RegimeFilter：禁止 PANIC / HIGH_VOL 等市场状态入场
- **退出管理器**：ExitManager 责任链模式
  - FixedStopExit：固定止损（相对 ATR）
  - TrailingStopExit：移动止损
  - TakeProfitExit：目标止盈
  - TimeStopExit：时间止损（收盘前强制平仓）
- **风控管理器**：DefaultRiskManager
  - 仓位计算：固定金额 / ATR 分数两种模式
  - 日内风控：max_daily_loss / max_positions_per_day / max_consecutive_losses
- **数据层**：SqliteDataHandler，从 SQLite 加载 OHLCV 历史数据

### 策略实现（Phase 2 ✅）

| 策略 | 类型 | 适用标的 | 状态 |
|------|------|----------|------|
| ORBEnhanced | 开盘区间突破 | ES/NQ 期货 | ✅ 验证通过 |
| SwingTrend | 趋势跟踪 | 个股（TSLA/NVDA） | ✅ 实现完成 |
| PullbackEMA | EMA 回调入场 | 通用 | ✅ 实现完成 |
| VWAPReversion | VWAP 均值回归 | 高波动标的 | ✅ 实现完成 |

### 回测验证（Phase 3 ✅）

- **BacktestEngine v2**：完整事件驱动，修复了 look-ahead bias（退出先于入场检查）
- **评估指标**：Sharpe / Sortino / 最大回撤 / 胜率 / 盈利因子 / 综合评分
- **策略猎人管道**：576 参数组合批量扫描，结果存储 SQLite，支持去重和排名
- **单元测试**：229 个测试全部通过 ✅

---

## 关键研究成果

### ES ORB 策略发现（Phase 3a，最新）

对 ES_continuous 5min 数据进行了两轮参数扫描：

**第一轮（576 组合，无再入场）**

- 最佳 IS 配置：ORB20_C0.3_S2.0，Sharpe 1.166
- OOS 验证：IS 1.166 → OOS 0.172，衰减 85.3%（严重过拟合）

**第二轮（48 组合，引入回调再入场）**

| 指标 | IS 2024 | OOS 2025 | OOS 2026 Q1 |
|------|---------|----------|-------------|
| Sharpe | 0.304 | **0.532** | **1.804** |
| 交易次数 | 369 | 443 | 127 |
| 胜率 | 54.2% | 56.2% | 55.1% |
| 最大回撤 | 21.1% | 22.0% | 10.2% |
| PnL | +$6,190 | +$20,416 | +$19,911 |

**关键发现**：

1. **负衰减（OOS Sharpe > IS Sharpe）**：策略不过拟合，在未见数据上表现更好
2. **2026 Q1 全部月份盈利**：Sharpe 1.80，最大回撤仅 10.2%，盈利因子 1.24
3. **回调再入场机制有效**：每日平均交易次数从 0.8 提升到 1.4

---

## 项目进度

```
Phase 0: 项目初始化       ██████████ 100% ✅ 完成（2026-04-26）
Phase 1: 核心接口层       ██████████ 100% ✅ 完成（2026-04-28）
Phase 2: 策略实现         ██████████ 100% ✅ 完成（2026-04-28）
Phase 3: 回测验证         ██████████ 100% ✅ 完成（2026-04-29）
Phase 3a: 策略发现循环    ████░░░░░░  40% ES ORB 首轮完成（2026-04-30）
Phase 4: Paper 运营       ░░░░░░░░░░   0%
Phase 5: 实盘切换         ░░░░░░░░░░   0%
```

### 下一步计划

- [ ] 测试 SwingTrend / PullbackEMA 在 ES 上的表现
- [ ] 修复 ADX 过滤器（当前 stub 状态，实际未生效）
- [ ] 实现 Walk-Forward Validation（Purged WFV）引擎
- [ ] 补充 NQ 5min 历史数据（当前仅 1 个月）
- [ ] Paper Trading 链路测试（IB Paper 账户集成）

---

## 环境配置

### 依赖环境

```
Python 3.11
conda 环境: quant_trading
平台: Windows 11
经纪商: Interactive Brokers（ib_insync）
数据库: SQLite（OHLCV 历史数据）
```

### 关键依赖

```
TA-Lib==0.6.8      # 技术指标计算
ib_insync          # IB API 封装（仅 execution/ 层使用）
pandas / numpy     # 数据处理
pytest             # 单元测试
pyyaml             # 配置文件
```

### 运行测试

```bash
conda activate quant_trading
cd D:\Python_Projects\ds_quant_horse_restructured
pytest tests/ -v
```

### 运行回测

```bash
python main_backtest.py
```

---

## 风控参数（初始配置）

| 参数 | 值 | 含义 |
|------|-----|------|
| `max_risk_per_trade` | 0.5% | 日内单笔风险 |
| `max_daily_loss` | 6% | 日内最大亏损熔断 |
| `max_consecutive_losses` | 2 | 连续亏损停止 |
| `max_trades_per_day` | 6 | 每日交易次数上限 |
| `max_position_pct` | 20% | 单仓最大持仓 |

---

## 实盘切换准入清单

| 准入项 | 通过标准 |
|--------|----------|
| WFV OOS Sharpe | ≥ 基准 Sharpe × 0.7 |
| Paper 天数 | ≥ 10 个有效交易日 |
| Regime 覆盖 | ≥ 3 种不同市场状态 |
| 日志完整性 | 无 CRITICAL 告警、对账 0 差异 |
| 相关性 | 与存量终端日 PnL 相关系数 < 0.5 |

---

## 与旧项目的关键差异

| 维度 | cc_quant_horse（旧） | ds_quant_horse（新） |
|------|---------------------|---------------------|
| 策略研发 | 手工参数扫描 | AI 辅助 + 批量扫描管道 |
| 过滤可追溯性 | 过滤原因不记录 | FilterChain 逐步记录 |
| 退出管理 | 分散在策略内 | 独立 ExitManager 模块 |
| 指标计算 | 各策略内部重复实现 | 统一 core/indicators.py |
| 测试覆盖 | 事后补测试 | TDD 优先，229 测试全覆盖 |
| 配置管理 | 部分硬编码 | 全部 YAML 外置 |

---

## 重要约束

1. 策略文件中**禁止** `import ib_insync`
2. 代码中**禁止**硬编码 IP、端口、路径、合约参数
3. **禁止**绕过 `RiskManager` 直接下单
4. `core/` 中**禁止**写任何实现逻辑（只放接口定义）
5. `tests/` 中**禁止**使用真实 IB 连接
6. **禁止**修改原项目 `D:\Python_Projects\cc_quant_horse` 的任何文件
7. **禁止**在策略内部重复实现指标计算

---

## 参考资料

- 旧项目反思：`REFLECTION.md`
- 重构计划详细版：`PLAN.md`
- 策略发现计划：`PLAN_RECONSTRUCT.md`
- 开发进度日志：`PROGRESS.md`
- ES ORB 研究日志：`research/ES_ORB_RESEARCH_LOG.md`
