# ds_quant_horse — 项目状态记忆

> 供 Claude Code 跨会话传递上下文
> 最后更新：2026-04-26

---

## 项目当前状态

**阶段**：Phase 0 ✅ 完成 → 等待进入 Phase 1

**上次任务**：项目初始化 — 从 cc_quant_horse 迁移设定、完成模型反思、制定重构计划

**下一步**：Phase 1 核心接口层开发（先写 test_events.py，再写 core/events.py）

---

## 关键文件索引

| 文件              | 用途           |
| --------------- | ------------ |
| `CLAUDE.md`     | 工作约束（先读这个）   |
| `PROGRESS.md`   | 进度追踪         |
| `REFLECTION.md` | 对旧项目模型的深度反思  |
| `PLAN.md`       | 全面重构计划       |
| `MEMORY.md`     | 本文件 — 项目状态记忆 |

---

## 原项目信息

- **路径**：`D:\Python_Projects\cc_quant_horse`（只读，不修改）
- **环境**：conda `quant_trading`，Python 3.11
- **数据库**：`D:\Python_Projects\cc_quant_horse\data\db\futures_data.db` 和 `stocks_data.db`（只读复用）
- **账户**：$100,000 USD，Paper Trading 优先

---

## 核心设计决策（已批准）

1. **单策略优先**：先做 ORB Enhanced，打通全流程再扩展多策略
2. **TDD 驱动**：每个模块先写测试，再写实现
3. **新增 DiagnosticEvent**：解决信号过滤不可追溯问题
4. **显式状态机**：PositionStateMachine 消除状态管理 Bug
5. **纯函数指标**：core/indicators.py 确保回测/实盘一致
6. **Purged WFV**：train/test 间留 gap 防止数据泄露
7. **放弃**：Multiterminal、GlobalRiskAllocator、VWAP均值回归、MomentumDaily screener

---

## Phase 1 开发清单

```
core/
├── __init__.py
├── events.py             # 第一步：6种事件类型定义
├── strategy.py           # 策略基类 ABC
├── risk_manager.py       # 风控基类 ABC
├── execution.py          # 执行器接口 Protocol
├── portfolio.py          # 组合管理基类 ABC
├── data_handler.py       # 数据处理器接口 ABC
├── indicators.py         # 纯函数指标
├── config_loader.py      # Dataclass 配置验证
├── position_state.py     # 显式状态机
└── order_tracker.py      # 订单追踪器
```

---

## 下一步工作

1. 初始化 Git 仓库：`git init`
2. 创建目录结构：`core/`, `tests/test_core/`, `config/`
3. 先写 `tests/test_core/test_events.py`
4. 再写 `core/events.py`
5. 运行测试验证：`pytest tests/ -v`

---

## 重要约束

- 禁止修改原项目 `D:\Python_Projects\cc_quant_horse` 的任何文件
- 策略文件中禁止 `import ib_insync`
- 代码中禁止硬编码 IP、端口、路径、合约参数
- `core/` 中禁止写实现逻辑（只放抽象接口）
- `tests/` 中禁止使用真实 IB 连接