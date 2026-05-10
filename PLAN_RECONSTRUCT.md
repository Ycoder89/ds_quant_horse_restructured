#  PLAN_RECONSTRUCT.md — DeepSeek 日内交易系统重构计划

> **生成日期**：2026-04-26
> **目的**：对 `cc_quant_horse` 现有模型进行深度反思，制定 `ds_quant_horse` 完整重构路线图
> **状态**：✅ 已审批（2026-04-26），修改为：策略 A 优先 + 策略 C 改为股票/ETF 标的，暂缓期货


---

## 目录

- [一、现有系统深度诊断](#一现有系统深度诊断)
- [二、核心发现：什么真正有效](#二核心发现什么真正有效)
- [三、核心发现：什么需要抛弃](#三核心发现什么需要抛弃)
- [四、架构改进清单](#四架构改进清单)
- [五、精简后的策略组合](#五精简后的策略组合)
- [六、分阶段重构路线图](#六分阶段重构路线图)
- [七、风险与缓解](#七风险与缓解)
- [八、成功标准](#八成功标准)

---

## 一、现有系统深度诊断

### 1.1 架构层诊断

| 组件 | 评价 | 问题 | DeepSeek 评级 |
|------|------|------|-------------|
| **事件系统** (core/events.py) | 🟢 设计优秀 | 字段偏多（SignalEvent 12 字段），可精简 | A- |
| **策略基类** (core/strategy.py) | 🟢 设计优秀 | `_bar_buffer` 用 deque 但策略也可自建缓存，有冗余 | A- |
| **风控管理器** (risk/risk_manager.py) | 🟡 中等 | `calculate_quantity()` 函数过长（~70 行），混合了三种模式 | B |
| **数据处理器** (core/data_handler.py) | 🟢 设计优秀 | 接口清晰；但 warmup 调用链散落在策略子类里 | B+ |
| **执行层** (execution/) | 🟢 良好 | IB 封装干净；但重连/重试逻辑不完整 | B+ |
| **组合管理** (portfolio/) | 🟡 中等 | Phantom PnL 问题未修复；对账逻辑脆弱 | B- |
| **监控系统** (monitoring/) | 🟡 可用 | 命令分散，缺乏统一看板 | C+ |
| **参数优化** | 🔴 差 | 4 个碎片脚本，无统一入口 | D |

### 1.2 策略层深度诊断（基于 WFV 结果）

#### T1 ORB TSLA — 唯一有 OOS 正期望的策略

| 指标 | 值 | 解读 |
|------|-----|------|
| avg IS Sharpe | 2.582 | 训练期强 |
| avg OOS Sharpe | +1.175 | 样本外仍为正（唯一！） |
| OOS 通过率 | 50%（16/32） | 不够稳定 |
| 最差窗口 | -11.323 | 2024-08 极端亏损 |
| OOS Std | 4.896 | 波动大 |

**DeepSeek 诊断**：
- ORB 逻辑本身有经济学基础（开盘区间 = 当日的价值发现区间），非纯数据挖掘
- 问题不在策略核心逻辑，而在**入场过滤**和**风控收紧**不够
- TSLA 标的选择正确：高波动 + 清晰的开盘区间
- 极端窗口（2024-08）对应市场恐慌，ORB 假突破增多

**改进空间**：
- 加 VWAP 确认过滤（突破 VWAP 同侧才入场）
- 收紧入场时间窗口（11:30 → 11:00）
- 单笔风险 0.5% → 0.3%
- Regime 过滤增加 HIGH_VOL（极度高波动禁仓）

**保留评级**：🟢 **核心保留**，重点改进

---

#### T2 VWAP ES — 结构性失败

| 指标 | 值 | 解读 |
|------|-----|------|
| avg IS Sharpe | +0.644 | 训练期勉强为正 |
| avg OOS Sharpe | −2.021 | 样本外持续亏损 |
| OOS 通过率 | 0%（0/4 活跃窗口） | 无任何窗口通过 |

**DeepSeek 诊断**：
- VWAP 均值回归在 ES 上有经济学逻辑（机构订单流围绕 VWAP）
- 但 2024-2025 年的市场特征（AI 主题 + 关税政策）制造了大量**单边趋势日**
- VWAP 回归策略在趋势日 = 逆势抄底 = 被打爆
- 虽然加了 Regime 过滤（TRENDING 禁仓），但 Regime 分类器本身不准（滞后性）
- **根本问题**：VWAP 回归是一个"相信市场会回归均值"的策略，而近年市场特征 = "恐慌或贪婪都持续到收盘"

**改进方向**：
- VWAP 不宜独立做方向判断，应退化为**辅助过滤指标**
- 例如：ORB 突破 + 价格在 VWAP 同侧 → 入场，而非 VWAP 自身触发入场
- 或者：VWAP 在午盘（13:00-15:00）作为趋势回归工具，但需要和开盘区间结合

**保留评级**：🔴 **不保留独立策略**，降级为辅助指标

---

#### T3 SwingTrend NQ — 数据不全无法评估，但 Paper 存在集中度风险

| 指标 | 值 | 解读 |
|------|-----|------|
| Paper 累计 | +$1,930（12 天） | 最强 |
| 盈利分布 | 2/12 天占 85% | 极度集中 |
| WFV 状态 | 0 成交（Sizing Bug） | 无法评估 |

**DeepSeek 诊断**：
- SwingTrend 的入场逻辑（趋势 + 回踩）有经济学基础
- 但 Paper 表现集中度过高 → **可能是运气，不是能力**
- "2 天赚 $4,055，其他天小亏" 这种分布不具备实盘可重复性
- NQ 标的选择正确（波动大、趋势强）
- 没有 WFV 结果之前，不能判断真伪

**改进方向**：
- 必须先补数据跑 WFV
- 增加 daily_profit_cap（$1,000 锁利）→ 拉平利润分布
- 增加 latest_entry_time 13:30 → 午后信号质量下降
- 增加趋势强度确认（ADX 过滤）

**保留评级**：🟡 **待 WFV 验证后决定**

---

#### T4 TrendContinuation TSLA — 严重过拟合

| 指标 | 原始 | 参数扫描后 |
|------|------|----------|
| avg IS Sharpe | 4.171 | 1.214 |
| avg OOS Sharpe | −0.720 | +0.894 |
| OOS 通过率 | 21.1% | 41.7%（扫描后） |

**DeepSeek 诊断**：
- 核心参数 `day_range_atr_mult=1.6` 是分水岭——只在真正的大振幅日（≥历史均值 1.6×）入场，过滤了大量假信号 → IS Sharpe 从 4.17 跌到 1.21，但 OOS 从 -0.72 升到 +0.89
- **这是典型的参数过拟合案例的教科书级修复**——限制入场条件 → 牺牲训练期收益 → 换取泛化能力
- 入场窗口 14:00（原 14:30）更好 → 午后趋势确认早 30 分钟
- 但通过率 41.7% 仍不及格 → 策略本身不够稳定

**改进方向**：
- day_range_atr_mult=1.6 是关键，保留
- 中午 12:00 做"趋势预判"（如果上午是强趋势 → 激活下午入场模式）→ 减少盲目等待
- 入场确认增加 Volume 条件（突破放量 > 均量 1.2×）

**保留评级**：🟡 **有条件保留**，需进一步改进

---

#### T5 MomentumDaily TSLA — 过拟合，但改进路径清晰

| 指标 | 原始 | 参数扫描后 |
|------|------|----------|
| avg IS Sharpe | 4.577 | 2.118 |
| avg OOS Sharpe | −0.602 | +0.529 |
| OOS 通过率 | 33.3% | 27.8% |

**DeepSeek 诊断**：
- T5 的 Gap&Go + Pullback 入场逻辑有经济学基础（开盘跳空 = 隔夜信息定价）
- 但单标的 TSLA 限制了 gap_min_pct 的筛选作用（TSLA 开盘跳空几乎总是 ≥ 2%）
- latest_entry_time=10:00（早 30 分钟截止）显著改善 OOS
- pullback_band_pct=0.25（稍宽松）改善

**改进方向**：
- **核心问题**：单标的回测无意义（TSLA 不存在 gap 筛选）
- 多标的股票池的 WFV 才是正确评估方式
- ATR×1.5 止损对高 Beta 标的太紧 → 改为 ATR×2.0 或动态 pivot low

**保留评级**：🟡 **有条件保留**，需多标的回测

---

#### 未评估策略（10 个）

| 策略 | 标的 | 状态 | 初判 |
|------|------|------|------|
| gap_fill.py | — | 未配置 | 🔴 逻辑脆弱（缺口回补不具可预测性） |
| high_52w.py | SPY | 备用库 | 🔴 52周高突破 = 追高，日内不适用 |
| momentum_daily.py | TSLA | 活跃 | 已评估（T5） |
| orb.py | TSLA | 活跃 | 已评估（T1） |
| swing_revert.py | SPY | 备用库 | 🟡 均值回归在 ETF 或许有效 |
| swing_trend.py | NQ | 活跃 | 已评估（T3） |
| tqqq_trend.py | TQQQ | 未配置 | 🔴 杠杆 ETF 日内风险过大 |
| trend_continuation.py | TSLA | 活跃 | 已评估（T4） |
| trend_momentum.py | — | 备用库 | 🔴 1H 周期日内不适用 |
| vix_reversion.py | VIX | 存档 | 🔴 VIX 日内策略不成熟 |
| vrs.py | — | 备用库 | 🔴 1H 周期日内不适用 |
| vwap_revert.py | ES | 活跃 | 已评估（T2） |

### 1.3 系统工程层诊断

| 问题 | 来源 | 严重度 | 状态 |
|------|------|--------|------|
| Phantom PnL（多终端共享账户） | STAGE_SUMMARY §5 P1-1 | 🔴 P1 | 未修复 |
| Regime 过滤从未生效 Bug | STAGE_SUMMARY §3.3 | 🔴 P0 | 已修复 |
| Bracket Stop 被超时撤销 | STAGE_SUMMARY §3.3 | 🔴 P0 | 已修复 |
| EOD 跳过 IB 实仓 | STAGE_SUMMARY §3.3 | 🔴 P0 | 已修复 |
| warmup 回测/实盘不对称 | STAGE_SUMMARY §3.3 | 🔴 P0 | 已修复 |
| 参数优化脚本碎片化 | STAGE_SUMMARY §5 P1-5 | 🟡 P1 | 未修复 |
| 指标实现未完全收敛 | STAGE_SUMMARY §5 P1-7 | 🟡 P1 | 部分迁移 |
| 10:30-14:30 午盘空白 | STAGE_SUMMARY §5 P2 | 🟢 P2 | 无覆盖 |
| 滑点模型过简 | STAGE_SUMMARY §5 P2 | 🟢 P2 | 未改进 |
| 无全局风险敞口约束 | STAGE_SUMMARY §5 P2 | 🟢 P2 | 未实现 |
| 订单拒绝无重试 | STAGE_SUMMARY §5 P2 | 🟢 P2 | 未实现 |

---

## 二、核心发现：什么真正有效

基于 6 周开发 + 12 天 Paper Trading + WFV 全量结果 + DeepSeek 客观分析，得出以下结论：

### 发现 1：事件驱动架构是正确的，不要推翻

cc_quant_horse 的架构分层（Data → Strategy → Risk → Execution → Portfolio）经受住了多策略并行 + 回测/实盘共享的考验。**这应该是 ds_quant_horse 的基石**。

### 发现 2：ORB 是目前唯一被 WFV 证实有阿尔法的策略

T1 ORB TSLA 在 29 个 WFV 窗口中 avg OOS Sharpe = +1.175 > 0。虽然有稳定性问题，但方向是对的。ORB 的经济学基础（开盘区间界定日内价值发现范围）是**可解释的**，不是纯数据挖掘。

### 发现 3：参数过拟合是最大的敌人

T4 和 T5 展示了经典模式：IS Sharpe 4+ → OOS Sharpe 负值。这不是策略逻辑的问题，是**参数选择被训练期数据绑架**的问题。扫描后 IS 降到 1.2-2.1，OOS 升到 0.5-0.9 → 说明"限制入场条件"是解决过拟合的有效手段。

### 发现 4：标的-策略配对是单向的

经验 1 总结得非常正确："ORB 只有 TSLA 工作、VWAP 只有 ES 工作"。没有一个策略能在所有标的上有效。这告诉我们：**不要把时间浪费在"泛化到多标的"上**，一个策略配一个标的，深度优化。

### 发现 5：WFV 比 Paper Trading 更快暴露真相

12 天 Paper 给 T3 打了最高分（+$1,930），但 WFV 显示 T3 没有任何 OOS 证据。Paper 的 12 天 vs WFV 的 3 年数据，后者的信号质量高几个数量级。

### 发现 6：VWAP 不应做独立方向决策

VWAP 回归在趋势日是**致命**的。但 VWAP 作为**辅助确认/过滤条件**（如 ORB 突破必须在 VWAP 同侧）是有价值的。这是"退一步海阔天空"的改进。

---

## 三、核心发现：什么需要抛弃

### 抛弃 1：15 分钟时间周期

经验 6 明确："15 min 是最差的时间周期"。不再使用。

### 抛弃 2：>1 小时的日内策略

TRENDING_UP 日内持有超过 4 小时 = 隔夜风险前没时间退出 → 放弃。只做 <1H K 线 + EOD 强平。

### 抛弃 3：VIX / 杠杆 ETF 日内

VIX reversion 和 TQQQ trend 不具备日内可操作性。存档。

### 抛弃 4：多终端架构

5 终端运行带来 Phantom PnL、跨终端干扰、监控复杂度。**改为 2 终端（上午 + 下午，或 ORB + 午盘）**，简化 80% 的工程复杂度。

### 抛弃 5：工厂模式 YAML 膨胀

cc_quant_horse 有 20+ 个 factory YAML 配置，实际活跃不到 5 个。新系统只保留实际使用的配置，每个策略一个 YAML。

### 抛弃 6：4 个碎片化优化脚本

统一为 `run_optimize.py --strategy X`。

---

## 四、架构改进清单

### 4.1 core/ 改进

| 改进项 | 当前 | 目标 | 原因 |
|--------|------|------|------|
| SignalEvent 精简 | 12 字段 | 8 字段 | close_qty、take_profit、timeframe 可合并/移除 |
| 新增 EntryFilter 接口 | 不存在 | ABC | 入场过滤（VWAP确认、Volume确认等）统一接口 |
| 新增 ExitManager 接口 | 不存在 | ABC | 止损/止盈/移动止损统一管理 |
| Strategy 基类瘦身 | ~210 行 | ~120 行 | Regime 逻辑移到独立 Filter；bracket 状态移到执行层 |
| Bar 增加便捷属性 | 无 | avg_price, typical_price | 减少策略内重复计算 |

### 4.2 risk/ 改进

| 改进项 | 当前 | 目标 | 原因 |
|--------|------|------|------|
| calculate_quantity 拆分 | 70 行单体函数 | 4 个小方法 | 可测试性 |
| Phantom PnL 修复 | 监控取 IB 账户 | 取内部 realized_pnl | 记账准确性 |
| 全局风险敞口 | 无 | 跨终端 notional 上限 | 防止过载 |
| 动态止损计算 | 无 | ATR-based 动态止损 | 替代固定 multipliers |

### 4.3 backtest/ 改进

| 改进项 | 当前 | 目标 | 原因 |
|--------|------|------|------|
| 参数优化统一入口 | 4 个独立脚本 | run_optimize.py | 维护性 |
| WFV 参数扫描集成 | 手动 | WFV + GridSearch 一体化 | 效率 |
| 滑点模型 | 固定值 | 流动性建模（基于 Volume） | 回测真实性 |
| 手续费模型 | 固定 $2.05 | 按标的动态 | 期货/股票差异 |

### 4.4 execution/ 改进

| 改进项 | 当前 | 目标 | 原因 |
|--------|------|------|------|
| 订单重试 | 无 | error code 2104/2106/10197 重试 3 次 | 容错性 |
| 重连进度 | 不可见 | 日志/看板倒计时 | 可观测性 |
| Bracket Stop | 策略感知 | 执行层透明管理 | 关注点分离 |
| EOD 平仓兜底 | 依赖 IB 撤单 | 主动重试 + 告警 | 可靠性 |

---

## 五、精简后的策略组合

基于 WFV 结果 + DeepSeek 诊断，精简为 **核心 2 策略 + 候选 1 策略**：

### 核心策略 A：ORB TSLA 加强版（T1 进化）

| 特性 | cc_quant_horse T1 | ds_quant_horse A |
|------|-------------------|-----------------|
| 核心逻辑 | 开盘区间突破 | 同左 |
| 入场过滤 | 无 | **+ VWAP 同侧确认 + Volume 放量** |
| 止损 | 区间边界 | **ATR(14) × 1.5 动态止损（大于区间边界时取区间边界，小于时取 ATR）** |
| 止盈 | 无 | **区间宽度 × 2（1:2 风险回报）** |
| 风控 | 0.5% / trade | **0.3% / trade** |
| 入场窗口 | 9:30-11:30 | **9:30-11:00（缩短 30 分钟）** |
| Regime | skip PANIC | **+ skip HIGH_VOL（极度高波动容易假突破）** |
| WFV 目标 | OOS Sharpe 1.2 | **OOS Sharpe 1.5+ / 通过率 60%+** |

### 核心策略 B：午盘趋势确认（新增，填补空白）

| 特性 | 设计 |
|------|------|
| 核心逻辑 | 上午趋势判定 + 午盘回踩入场 |
| 标的 | ES（低波动期货，适合午盘） |
| 时间周期 | 5min |
| 入场窗口 | 13:00-14:30 ET |
| 趋势判定 | 12:00 快照：上午 VWAP 偏离 × ADX(14) 确认 |
| 入场条件 | 回踩 VWAP ± 0.15% + 反转 K 线确认 |
| 止损 | VWAP 偏离 × 1.5 |
| 止盈 | VWAP 偏离 × 2（回到 VWAP 另一侧） |
| 风控 | 0.3% / trade |
| WFV 目标 | OOS Sharpe 1.0+ |

**设计理由**：
- ES 午盘波动低（流动性好），适合做回踩入场
- 用上午的趋势方向（不在午盘重新发明方向）
- VWAP 在午盘更有效（全天 VWAP 已稳定）
- 填补 10:30-14:30 空白时段

### 候选策略 C：SwingTrend NQ（T3 进化，待 WFV 验证）

| 特性 | cc_quant_horse T3 | ds_quant_horse C |
|------|-------------------|-----------------|
| 核心逻辑 | 趋势 + 回踩 | 同左 |
| 趋势确认 | 无 | **+ ADX(14) > 25 过滤** |
| 入场过滤 | 无 | **+ 上午趋势斜率（EMA 排列）确认** |
| 午后过滤 | latest_entry_time 无 | **latest_entry_time=13:30** |
| 每日锁利 | 无 | **daily_profit_cap=$800** |
| 止损 | 固定 | **Trailing Stop 从入场根 K 开始** |
| WFV 前提 | 需补数据 | **必须先跑 WFV → OOS Sharpe > 0.7 IS Sharpe** |

---

## 六、分阶段重构路线图

### 阶段零：项目初始化 ✅

```
✅ 2026-04-26 完成：
  - 创建 CLAUDE.md
  - 创建 PROGRESS.md
  - 探索 cc_quant_horse 完整结构
  - 编写本 PLAN_RECONSTRUCT.md（等待审批）
```

### 阶段一：模型反思 + 诊断报告

```
预计：1-2 天
输入：cc_quant_horse 全部策略代码 + WFV JSON + Paper 日志
输出：REFLECTION_DIAGNOSIS.md（完整诊断报告）

具体任务：
1. 用 DeepSeek 分析 14 个策略文件的代码质量
   - 圈复杂度
   - 代码重复率
   - 可测试性评分
2. 分析策略参数敏感性
   - 对 ORB 的 orb_window_minutes 做 15-90 分钟扫描
   - 对 VWAP 的 cooldown_bars 做 2-10 扫描
3. 分析 Regime 分类器的准确性
   - 回溯 2023-2025 的 Regime 标注
   - 与实际市场状态对比
4. 输出策略分类：
   - 🟢 直接保留（ORB TSLA）
   - 🟡 待改进保留（TrendCont TSLA, MomentumDaily）
   - 🔴 放弃（VWAP ES 独立策略, VIX, TQQQ 等）
```

### 阶段二：core/ 重构

```
预计：2-3 天
输入：阶段一的诊断结论
输出：精简后的 core/ 模块

具体任务：
1. core/events.py 精简
   - SignalEvent: 移除 close_qty（移到 ExitManager）
   - 新增 EntryConditions（可选的入场确认条件集合）
2. core/filters.py 新建
   - EntryFilter ABC（VWAP确认、Volume确认、ADX过滤等）
3. core/exit.py 新建
   - ExitManager ABC（固定止损、移动止损、时间止损）
4. core/strategy.py 精简
   - 移除 _regime_skip_list（移到 EntryFilter）
   - 移除 _bracket_active（移到执行层）
   - 保留 on_bar / emit_signal / bars 核心接口
5. core/data_handler.py 保持
   - 已足够好，不需要改
6. 编写 core/ 单元测试
   - 每个 ABC Mock 实现验证接口契约
```

### 阶段三：策略重写

```
预计：3-5 天
输入：阶段二的 core/ + 阶段一的诊断
输出：2 个核心策略 + 候选策略框架

具体任务：
1. 策略 A：ORB_Enhanced TSLA
   - 继承新 Strategy 基类
   - 集成 VWAPFilter + VolumeFilter + RegimeFilter
   - ATR 动态止损
   - WFV 验证：目标 OOS Sharpe 1.5+

2. 策略 B：MiddayTrendCont ES（全新）
   - 从零编写，无历史包袱
   - 上午趋势判定 + 午盘回踩入场
   - 基于计划五的设计
   - WFV 验证：目标 OOS Sharpe 1.0+

3. 策略 C：SwingTrend NQ（可选）
   - 等待 WFV 数据补齐后决定
   - 如果开写，增加 ADX + daily_profit_cap
```

### 阶段四：回测/WFV 增强

```
预计：2-3 天
输入：阶段三的策略代码
输出：统一回测 + WFV + 优化框架

具体任务：
1. run_backtest.py 统一入口
2. run_optimize.py 统一优化入口
   - 整合 4 个碎片脚本
   - GridSearch + WFV 一体化
   - 自动生成扫参报告
3. 滑点模型升级
   - 基于 Volume 的流动性估计
   - 股票用 bid-ask spread，期货用 tick 级滑点
4. 手续费模型细化
   - 期货：$2.05/合约（保持）
   - 股票：$0.005/股（IB Pro 费率）
```

### 阶段五：实盘接入

```
预计：2-3 天
输入：阶段四验证通过的策略
输出：Paper Trading 就绪系统

具体任务：
1. execution/ib_execution.py 新写
   - 订单重试（error 2104/2106/10197）
   - 重连进度可视化
   - Bracket Stop 透明管理
2. monitoring/monitor.py 精简
   - 2 终端看板（不是 5 终端）
   - Phantom PnL 修复（内部记账）
   - 日报自动生成
3. run_all.py
   - 2 终端启动
   - 盘前数据检查
   - screener（如果需要 C 策略）
```

### 阶段六：Paper Trading 验证

```
预计：2-3 周
输入：阶段五的实盘系统
输出：准入决策

具体任务：
1. 策略 A：≥ 10 天 Paper，目标日 Sharpe > 0.5
2. 策略 B：≥ 10 天 Paper，目标日 Sharpe > 0.3
3. 每日对账 0 差异
4. Regime 覆盖 ≥ 3 种
5. WFV 对比 Paper 表现
6. 准入决策：切实盘（port 7496）
```

---

## 七、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 数据不足导致 WFV 失真（期货只有 30 天） | 中 | 高 | 先补历史数据；阶段一优先确认数据可用性 |
| 策略 B 午盘趋势确认逻辑不可行 | 中 | 中 | 先用回测验证；如果失败，退回到只保留策略 A |
| DeepSeek 分析结论有偏见 | 低 | 中 | 所有诊断必须用 WFV 结果验证，不以意见代替数据 |
| core 重构破坏兼容性 | 低 | 中 | 新项目独立，不修改原项目；但需确认数据库可共享 |
| 重构时间超出预期 | 中 | 低 | 阶段可并行（如阶段二 core + 阶段四 backtest 同时做） |

---

## 八、成功标准

### 最小可行产品（MVP）

- [ ] 1 个策略（ORB TSLA 加强版）WFV OOS Sharpe ≥ 1.2，通过率 ≥ 60%
- [ ] Paper Trading ≥ 10 天正收益
- [ ] Phantom PnL 修复（记账准确）
- [ ] 参数优化统一为 `run_optimize.py`
- [ ] 631+ 测试全绿

### 完整目标

- [ ] 2 个策略（A + B）WFV 通过
- [ ] 15+ 天 Paper 验证
- [ ] 日 Sharpe > 0.5
- [ ] 午盘时段有覆盖
- [ ] 监控看板清晰可读

### 超越（Nice to Have）

- [ ] 策略 C 通过 WFV
- [ ] 滑点模型准确度 > 80%
- [ ] 自动 Regime 分类替代启发式

---

## 附录 A：与原项目的对比

| 维度 | cc_quant_horse | ds_quant_horse |
|------|---------------|----------------|
| 策略数量 | 11+ (5 活跃) | 2 核心 + 1 候选 |
| 终端数 | 5（后来减到 3） | 2 |
| 参数优化 | 4 碎片脚本 | run_optimize.py |
| Phantom PnL | 存在 | 已修复 |
| WFV 驱动 | 后期补做 | 从第一天开始 |
| 午盘覆盖 | 无 | 有（策略 B） |
| 代码质量 | B+ | 目标 A- |
| 可维护性 | B- | 目标 A |

## 附录 B：与 cc_quant_horse 共享的资源

| 资源 | 路径 | 用途 | 权限 |
|------|------|------|------|
| 期货数据库 | `D:\Python_Projects\cc_quant_horse\data\db\futures_data.db` | ES/NQ 历史数据 | 只读 |
| 股票数据库 | `D:\Python_Projects\cc_quant_horse\data\db\stocks_data.db` | TSLA 等个股数据 | 只读 |
| Conda 环境 | `quant_trading` | Python 3.11 + 依赖 | 共享 |
| TWS/Gateway | IB 客户端 | Paper/实盘连接 | 共享 |

> ⚠️ **重要**：ds_quant_horse 只读取数据库，不写入。数据下载仍通过 cc_quant_horse 的下载器进行。

---

> **审批决策（2026-04-26）**：
> 1. ✅ 精简为 2 核心策略，但调整顺序：**策略 A 优先 → 策略 C 改为股票/ETF 标的 → 策略 B 暂缓**
> 2. ✅ VWAP ES 独立策略放弃、降级为辅助指标
> 3. ✅ **暂不使用期货**（历史数据不够完整连续），优先股票/ETF
> 4. ✅ 数据库只读共享 cc_quant_horse
> 5. ✅ 分 6 阶段推进，**阶段二（core/ 重构）直接启动**（模型反思已融入 PLAN_RECONSTRUCT.md）
> 
> **修改后的开发顺序**：阶段零 ✅ → 阶段二（core/ 重构）→ 阶段三-1（策略 A：ORB TSLA 加强版）→ 阶段三-2（策略 C：SwingTrend 股票版）→ 阶段四（回测/WFV）→ 阶段五（实盘接入）→ 阶段六（Paper Trading）


