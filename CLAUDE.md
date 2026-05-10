# ds_quant_horse — Claude Code 工作约束文件

> **重要**：每次开始新任务前，先完整阅读本文件，再读 `core/` 目录。
> 
> 本文件从 `cc_quant_horse` 项目迁移，并根据反思结果进行了重构。

---

## 项目概况

- **类型**：日内量化交易系统（引入 DeepSeek 辅助模型反思与重构）
- **经纪商**：Interactive Brokers（ib_insync）
- **标的**：美股指数期货 ES / NQ（优先），活跃个股（TSLA/NVDA 等）
- **账户**：$100,000 USD，Paper Trading 优先
- **平台**：Windows 11，conda 环境 `quant_trading`（暂用原环境，Python 3.11）
- **数据库**：复用原项目 `data/db/futures_data.db`、`data/db/stocks_data.db`（只读）
- **原项目**：`D:\Python_Projects\cc_quant_horse`（只读，不修改）

---

## 核心设计原则（从原项目继承并改进）

### 1. 事件驱动单向流（保留）

```
DataEvent → Strategy.on_bar() → SignalEvent
          → RiskManager.on_signal() → OrderEvent
          → ExecutionHandler.execute_order() → FillEvent
          → Portfolio.on_fill()
```

**改进点**：增加事件链可观测性，每个环节埋点日志。

### 2. 策略独立性（保留并强化）

- 策略文件中禁止 `import ib_insync`
- 策略只调用 `self.emit_signal()`，不调用任何执行或订单接口
- **新增**：策略不依赖任何数据源实现细节，通过统一接口获取历史/实时数据

### 3. 经纪商抽象（保留）

- 所有 IB API 调用封装在 `execution/` 中
- 其他模块通过 `ExecutionHandler` 接口调用
- **新增**：增加模拟执行器用于快速回测迭代

### 4. 配置外置（保留）

- 代码中禁止硬编码：IP、端口、路径、合约参数、风控阈值
- 所有参数从 `config/` 目录 YAML 读取

### 5. 风控不可绕过（保留）

- 所有 `OrderEvent` 必须经过 `RiskManager.on_signal()`
- **新增**：跨策略/跨终端的全局风险预算分配

---

## 与原项目的关键差异

| 维度    | cc_quant_horse（旧） | ds_quant_horse（新）    |
| ----- | ----------------- | -------------------- |
| 策略研发  | 手工参数扫描            | DeepSeek 辅助分析 + 参数优化 |
| 模型类型  | 纯程序化规则            | 规则 + 可能引入 ML 信号      |
| 反思机制  | 人工复盘              | AI 辅助的每日复盘与策略诊断      |
| 代码架构  | 成熟但碎片化            | 重新设计，更模块化            |
| 测试先行  | 事后补测试             | TDD 优先               |
| 信号率诊断 | 人工排查              | 自动化信号率监控与告警          |

---

## 项目目录结构（规划）

```
ds_quant_horse/
├── CLAUDE.md              # 本文件 — 工作约束
├── PROGRESS.md            # 进度追踪
├── REFLECTION.md          # 对旧项目的反思总结
├── PLAN.md                # 重构计划
├── config/                # YAML 配置文件
│   ├── settings.yaml
│   ├── risk.yaml
│   └── strategies/        # 策略配置
├── core/                  # 核心接口（ABC + dataclass）
│   ├── events.py          # 事件定义
│   ├── strategy.py        # 策略基类
│   ├── risk_manager.py    # 风控基类
│   ├── execution.py       # 执行器接口
│   ├── portfolio.py       # 组合管理接口
│   ├── data_handler.py    # 数据处理器接口
│   └── indicators.py      # 统一指标计算（EMA/VWAP/ATR/Regime）
├── strategies/            # 策略实现
│   └── orb.py             # ORB 策略（首批迁移）
├── risk/                  # 风控实现
├── execution/             # IB 执行实现
├── data/                  # 数据层（从 DB 读取）
├── engine/                # 回测引擎
├── live/                  # 实盘/Paper 运行
├── monitoring/            # 监控、日志、报告
├── research/              # Jupyter 研究与分析脚本
│   └── deepseek/          # DeepSeek 辅助分析脚本
├── tests/                 # 单元测试
└── results/               # 回测与 WFV 输出
```

---

## 命名规范

- **策略名称**：`{strategy_type}_{symbol}_{timeframe}`
  例：`orb_tsla_5min`，`trend_cont_tsla_1min`
- **类名**：PascalCase，例：`ORBStrategy`，`IBExecutionHandler`
- **文件名**：snake_case，例：`orb.py`，`ib_execution.py`
- **配置 key**：snake_case，例：`max_risk_per_trade`
- **日志格式**：`[模块名] 事件描述: 关键数据`

---

## 风控参数（初始值，待回测验证）

| 参数                       | 初始值  | 含义       |
| ------------------------ | ---- | -------- |
| `max_risk_per_trade`     | 0.5% | 日内单笔风险   |
| `max_daily_loss`         | 6%   | 日内最大亏损   |
| `max_consecutive_losses` | 2    | 连续亏损停止   |
| `max_trades_per_day`     | 6    | 每日交易次数上限 |
| `max_position_pct`       | 20%  | 单仓最大持仓   |

---

## 原项目遗留的关键教训（必须避免）

1. **代码在跑 ≠ 逻辑在生效** — 关键配置加载后必须 log 解析后的值 + 单测断言
2. **回测/实盘 warmup 必须对称** — 策略基类 `warmup_from_db()` 统一接口
3. **IB 原生止損单 >> 软件止损** — 止损逻辑必须下到经纪商端
4. **多终端共享账户 = 幻象 PnL** — `today_pnl` 以内部口径为准
5. **15min 是最差周期** — 新策略从 5min 或 1min 开始
6. **均值回归 × 趋势日 = 爆仓** — 必须有 Regime 过滤器
7. **策略高度标的特异性** — 不幻想一个策略跑全市场
8. **Windows + ib_insync** — 必须独立 cmd 窗口，关闭 TWS Auto-Logoff
9. **Regime 过滤配置化** — 统一 YAML 键，基类统一处理
10. **指标计算统一** — 不要在策略内部重复实现 VWAP/ATR/EMA

---

## 开发工作流程

**第一步**：读取 `REFLECTION.md` 了解旧项目的经验教训
**第二步**：读取 `PLAN.md` 了解当前阶段的开发计划
**第三步**：读取 `core/` 目录确认接口设计
**第四步**：实现具体模块，遵循 TDD（先写测试）
**第五步**：更新 `PROGRESS.md`

---

## 实盘切换准入清单（与原项目保持一致）

| 准入项            | 通过标准                  |
| -------------- | --------------------- |
| WFV OOS Sharpe | ≥ 基准 Sharpe × 0.7     |
| Paper 天数       | ≥ 10 个有效交易日           |
| Regime 覆盖      | ≥ 3 种不同市场状态           |
| 日志完整性          | 无 CRITICAL 告警、对账 0 差异 |
| 相关性            | 与存量终端日 PnL 相关系数 < 0.5 |

---

## 禁止的行为

1. 策略文件中禁止使用 `ib_insync`
2. 代码中禁止硬编码 IP、端口、路径、合约参数
3. 禁止绕过 `RiskManager` 直接下单
4. `core/` 中禁止写任何实现逻辑
5. `tests/` 中禁止使用真实 IB 连接
6. 禁止修改 `core/events.py` 中已有字段名
7. 禁止回测和实盘写两套策略代码
8. 禁止修改原项目 `D:\Python_Projects\cc_quant_horse` 的任何文件
9. 禁止在策略内部重复实现指标计算（必须使用 core/indicators.py）

---

> 原项目参考：`D:\Python_Projects\cc_quant_horse\CLAUDE.md`
> 本文档将在开发过程中持续更新。