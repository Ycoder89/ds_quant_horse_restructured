# ds_quant_horse — 全面重构计划

> 日期：2026-04-26
> 基于：`REFLECTION.md` 对旧项目的深度反思
> 目标：构建可运行的日内交易系统，从单策略起步，工程+模型质量双优

---

## 总体路线图

```
Phase 0 (当前) → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5
项目初始化      核心接口层   策略实现    回测验证    Paper运营   实盘切换
  1天             3-5天      3-5天      3-5天      2-4周       准入后
```

**核心理念**：

- 🎯 **单策略优先**：先做一个策略做到极致，再做多策略
- 🧪 **TDD 驱动**：每个模块先写测试，再写实现
- 🔍 **可观测性**：每个决策点都有日志，每个过滤原因都可追溯
- 🤖 **DeepSeek 辅助**：策略分析、参数优化、每日复盘由 AI 辅助

---

## Phase 0：项目初始化（当前阶段）✅ 进行中

### 目标

建立新项目骨架，完成对旧项目的反思，制定开发计划。

### 文件清单

| 文件              | 状态     | 说明                    |
| --------------- | ------ | --------------------- |
| `CLAUDE.md`     | ✅ 完成   | 工作约束文件                |
| `PROGRESS.md`   | ✅ 完成   | 进度追踪                  |
| `REFLECTION.md` | ✅ 完成   | 对旧模型的深度反思             |
| `PLAN.md`       | 🔄 编写中 | 本文件 — 全面重构计划          |
| Memory 文件       | ⬜ 待创建  | 供 Claude Code 的项目状态记忆 |
| `.gitignore`    | ⬜ 待创建  |                       |

### 产出物

- 完整的旧项目分析报告
- 新项目设计蓝图
- **等待用户批准后进入 Phase 1**

---

## Phase 1：核心接口层（预计 3-5 天）

### 1.1 设计原则

- `core/` 目录只放抽象接口（ABC + dataclass + Protocol），不放实现
- 所有接口先定义再实现，确保模块间解耦
- 事件驱动流：`DataEvent → Strategy.on_bar() → SignalEvent → RiskManager.on_signal() → OrderEvent → ExecutionHandler.execute() → FillEvent → Portfolio.on_fill()`
- **新增** `DiagnosticEvent` 类型，记录所有被拒绝/过滤的信号及其原因

### 1.2 文件结构

```
core/
├── __init__.py           # 公开导出
├── events.py             # 事件定义（6种事件类型）
├── strategy.py           # 策略基类（ABC）
├── risk_manager.py       # 风控基类（ABC）
├── execution.py          # 执行器接口（Protocol）
├── portfolio.py          # 组合管理基类（ABC）
├── data_handler.py       # 数据处理器接口（ABC）
├── indicators.py         # 统一指标计算（纯函数，无状态）
├── config_loader.py      # 配置加载器（dataclass 验证）
├── position_state.py     # 持仓状态机
└── order_tracker.py      # 订单追踪器
```

### 1.3 核心设计决策

#### events.py — 统一事件定义

```python
# 保留原项目的 4 种事件类型，新增 2 种
EventType = Enum('EventType', [
    'DATA',      # 数据事件
    'SIGNAL',    # 信号事件
    'DIAGNOSTIC',# 诊断事件（新增）
    'ORDER',     # 订单事件
    'FILL',      # 成交事件
    'SESSION',   # 会话事件（新增：开盘/收盘/重置）
])

# Bar 定义保留原设计（OHLCV 载体）
# SignalEvent 增加字段
SignalEvent:
    - signal_id: str          # 唯一信号ID（新增，用于事件链追踪）
    - reason: str             # 生成原因（新增）
    - diagnostic_info: dict   # 诊断信息（新增）

# DiagnosticEvent（新增）
DiagnosticEvent:
    - event_type: str         # 诊断类型：SIGNAL_FILTERED / ORDER_REJECTED / RISK_LIMIT / NO_TRADE_DAY
    - related_signal_id: str  # 关联的信号ID
    - reason: str             # 原因描述
    - context: dict           # 上下文数据（当前Bar、指标值等）
```

#### position_state.py — 显式状态机

```python
class PositionState(Enum):
    NO_POSITION = "no_position"
    PENDING_ENTRY = "pending_entry"   # 订单已发送，等待成交
    IN_POSITION = "in_position"       # 持仓中
    PENDING_EXIT = "pending_exit"     # 平仓订单已发送
    FLAT = "flat"                     # 当日已平仓，不再交易

class PositionStateMachine:
    """唯一的状态转换入口。状态变更只在 on_fill() 中发生。"""
    # 合法转换规则
    TRANSITIONS = {
        NO_POSITION: [PENDING_ENTRY],
        PENDING_ENTRY: [IN_POSITION, NO_POSITION],  # fill成功→持仓，fill失败→回退
        IN_POSITION: [PENDING_EXIT],
        PENDING_EXIT: [NO_POSITION, IN_POSITION],   # fill成功→平仓
        FLAT: [],                                    # 终态，不可转换
    }
```

#### indicators.py — 纯函数指标

```python
# 所有指标都是纯函数，输入 Bar 序列，输出数值
# 不维护任何内部状态，确保回测和实盘完全一致

def calc_atr(bars: List[Bar], period: int = 14) -> float: ...
def calc_vwap(bars: List[Bar]) -> float: ...
def calc_ema(bars: List[Bar], period: int) -> List[float]: ...
def calc_regime(bars: List[Bar]) -> RegimeType: ...
def calc_orb_levels(bars: List[Bar], orb_minutes: int) -> Tuple[float, float]: ...
def calc_volume_ratio(current_vol: float, historical_vol: float) -> float: ...
def calc_trend_strength(bars: List[Bar]) -> float: ...
```

#### config_loader.py — Dataclass 验证

```python
@dataclass
class StrategyConfig:
    """所有配置在此验证，类型错误在加载时暴露"""
    name: str
    symbols: List[str]
    timeframe: str
    # 风控参数
    max_risk_per_trade: float = 0.005
    max_daily_loss: float = 0.06
    max_consecutive_losses: int = 2
    max_trades_per_day: int = 6
    # 策略特定参数（dict，由策略自己验证）
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        assert 0 < self.max_risk_per_trade <= 0.02, f"风险比例不合理: {self.max_risk_per_trade}"
        assert self.max_daily_loss <= 0.10
        # ... 更多验证

@dataclass
class RiskConfig:
    account_size: float = 100_000
    max_portfolio_risk_pct: float = 0.20
    contract_specs: dict = field(default_factory=dict)
    # 新增：标的可交易性检查
    min_tick_value: float = 0.0
    min_stop_distance_ticks: int = 4

    def can_trade(self, symbol: str, notional: float) -> Tuple[bool, str]:
        """检查该标的的 minimum stop 是否在风险预算内"""
        spec = self.contract_specs.get(symbol)
        min_stop_cost = spec['tick_value'] * self.min_stop_distance_ticks
        max_risk = self.account_size * 0.005
        if min_stop_cost > max_risk:
            return False, f"{symbol} 最小止损 ${min_stop_cost} > 风险预算 ${max_risk}"
        return True, ""
```

### 1.4 测试清单（Phase 1）

| 测试文件                                     | 覆盖内容                     |
| ---------------------------------------- | ------------------------ |
| `tests/test_core/test_events.py`         | 所有事件创建、序列化、字段验证          |
| `tests/test_core/test_position_state.py` | 状态机所有合法/非法转换             |
| `tests/test_core/test_indicators.py`     | 所有指标计算公式验证               |
| `tests/test_core/test_config_loader.py`  | 配置加载、验证、错误处理             |
| `tests/test_core/test_strategy.py`       | 策略基类的 Bar 管理、信号发出、warmup |
| `tests/test_core/test_risk_manager.py`   | 风控基类的信号过滤逻辑              |
| `tests/test_core/test_order_tracker.py`  | 订单生命周期管理                 |

### 1.5 产出物

- 完整的 core/ 模块，全部有单元测试
- 类型注解覆盖率 100%
- 可以在没有 IB 连接的情况下创建完整的事件流

---

## Phase 2：策略实现（预计 3-5 天）

### 2.1 首个策略：ORB Enhanced

**选择理由**：

1. 原 T1 ORB 是唯一稳定 VALID 的策略（OOS ~+1.2~1.8）
2. 模型简单，参数少，便于验证整个工程链路
3. 改进空间明确（成交量确认、动态 buffer、时间衰减）

**改进点**（基于 REFLECTION.md 分析）：

| 改进项    | 原实现       | 新实现                             |
| ------ | --------- | ------------------------------- |
| 突破确认   | 纯价格突破     | 价格突破 + 成交量确认（Vol > 前N根均值 × 1.2） |
| Buffer | 固定 8%     | 动态：基于 ATR 的自适应 buffer           |
| 时间衰减   | 无         | 入场时间越晚，buffer 要求越高              |
| 止盈     | 固定 R:R    | 基于 ATR 的动态止盈                    |
| 信号过滤   | Regime 分类 | 增加日内趋势方向一致性检查                   |
| 可观测性   | 仅日志       | 每个决策点输出 DiagnosticEvent         |

### 2.2 文件结构

```
strategies/
├── __init__.py
├── orb_enhanced.py        # ORB 增强版策略
└── config/
    └── orb_enhanced_tsla_5min.yaml
```

### 2.3 ORB Enhanced 策略逻辑

```
每日启动 → warmup_from_db(前N日5min数据)
           → 计算 ORB 区间（开盘前30分钟高低点）
           → 计算 ATR(14)
           → 开始接收实时 Bar

收到 Bar → 检查时间（必须在 9:30-11:30 之间）
          → 检查是否已有仓位（PositionState 检查）
          → 计算动态 buffer：base_buffer × ATR_mult × time_decay
          → 检查价格是否突破 ORB 区间（含 buffer）
          → 检查成交量确认：当前成交量 > 前20根均值 × 1.2
          → 检查趋势方向一致性：5min EMA(20) 方向与突破方向一致
          → 通过 → emit_signal()
          → 未通过 → emit_diagnostic(拒绝原因)

收到 Fill → 计算止损价（入场价 ± ATR × 1.5）
           → 计算止盈价（入场价 ± ATR × 2.5，动态）
           → 下 bracket 订单（IB 原生止盈止损）
           → 状态机 → IN_POSITION

止损/止盈触发 → 平仓 → 状态机 → FLAT（当日不再交易）
```

### 2.4 配置示例

```yaml
# config/strategies/orb_enhanced_tsla_5min.yaml
name: orb_enhanced
symbols: ["TSLA"]
timeframe: 5min

strategy:
  orb_window_minutes: 30
  base_buffer_pct: 0.08
  atr_period: 14
  atr_buffer_mult: 1.0       # ATR 影响 buffer 的乘数
  time_decay_enabled: true
  time_decay_start: "10:00"  # 从此时间开始衰减
  time_decay_rate: 0.02      # 每分钟 buffer +0.02%
  latest_entry_time: "11:30"
  volume_confirmation: true
  volume_lookback: 20        # 成交量均值计算周期
  volume_threshold_mult: 1.2 # 成交量必须 > 均值 × 倍数
  trend_confirmation: true
  trend_ema_period: 20       # 趋势方向 EMA 周期
  entry_side: "both"         # LONG / SHORT / BOTH
  stop_atr_mult: 1.5         # 止损 = 入场价 ± ATR × 1.5
  target_atr_mult: 2.5       # 止盈 = 入场价 ± ATR × 2.5

risk:
  max_risk_per_trade: 0.005
  max_daily_loss: 0.06
  max_consecutive_losses: 2
  max_trades_per_day: 6
```

### 2.5 测试清单（Phase 2）

| 测试文件                                        | 覆盖内容                     |
| ------------------------------------------- | ------------------------ |
| `tests/test_strategies/test_orb.py`         | ORB 区间计算、突破检测、成交量确认、时间衰减 |
| `tests/test_strategies/test_orb_signals.py` | 信号生成、过滤、拒绝原因             |
| `tests/test_strategies/test_orb_warmup.py`  | warmup 数据加载、指标预热         |

### 2.6 产出物

- ORB Enhanced 策略完整实现
- 所有单元测试通过
- 策略可在回测模式下运行（手动构造 Bar 序列）

---

## Phase 3：回测验证（预计 3-5 天）

### 3.1 回测引擎设计

**核心原则**：

- 回测引擎只依赖 Bar 数据，不依赖 IB 连接
- 内建 WFV 支持（train/test split、滚动窗口）
- 输出完整的逐笔交易记录（CSV）和汇总统计（JSON）
- 回测结果可直接供 DeepSeek 分析

### 3.2 文件结构

```
engine/
├── __init__.py
├── backtest.py            # 回测引擎核心
├── wfv.py                 # Walk-Forward Validation
├── metrics.py             # 评估指标计算
└── reporter.py            # 报告生成器
```

### 3.3 回测引擎接口

```python
class BacktestEngine:
    """事件驱动的回测引擎，与实盘共享核心接口"""

    def __init__(self, config: StrategyConfig, data: pd.DataFrame):
        """data: 历史 Bar 数据 DataFrame"""

    def run(self) -> BacktestResult:
        """
        主循环：
        for each bar in data:
            engine.on_bar(bar) → strategy.on_bar() → signal?
                → risk_manager.on_signal() → order?
                → execution.simulate() → fill?
                → portfolio.on_fill()
        """

    def run_wfv(
        self,
        train_days: int = 180,
        test_days: int = 60,
        step_days: int = 30,
        min_trades_per_window: int = 5,  # 新增：窗口有效性阈值
        purge_days: int = 5,              # 新增：purge gap
    ) -> WFVResult:
        """进阶: Purged Walk-Forward Validation"""
```

### 3.4 WFV 改进（基于 REFLECTION.md 第5节）

| 改进项    | 原实现      | 新实现                                         |
| ------ | -------- | ------------------------------------------- |
| 窗口有效性  | 忽略交易次数   | 要求 ≥ 5 笔交易                                  |
| 数据泄露防护 | 无 purge  | train/test 间留 5 天 purge gap                 |
| 准入标准   | 单一阈值 0.7 | 分级：≥0.7=VALID / 0.5-0.7=CAUTION / <0.5=FAIL |
| 统计显著性  | 无        | 报告 OOS Sharpe 的 bootstrap 置信区间              |
| 窗口权重   | 均等       | 指数衰减权重（近期窗口权重更高）                            |
| 输出格式   | 自定义 JSON | 标准化 JSON + 交易记录 CSV                         |

### 3.5 评估指标（多维度）

```python
@dataclass
class BacktestResult:
    # 基础收益指标
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float

    # 交易统计
    total_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_holding_minutes: float

    # WFV 特定
    oos_sharpe_mean: float
    oos_sharpe_std: float
    oos_pass_rate: float
    oos_sharpe_ci_lower: float  # 95% bootstrap CI
    oos_sharpe_ci_upper: float

    # 逐笔交易（供 DeepSeek 分析）
    trades: pd.DataFrame  # 每笔交易的详细记录

    # 信号统计（供诊断）
    signals_total: int
    signals_filtered: int
    filter_reasons: dict  # {reason: count}
```

### 3.6 DeepSeek 分析集成脚本

```python
# research/deepseek/analyze_backtest.py
"""
读取回测输出的 trades.csv 和 summary.json
调用 DeepSeek API 进行：
1. 交易时段分布分析（入场时间 vs 胜率）
2. 失败交易共性诊断
3. 参数敏感性分析
4. 市场状态 vs 策略表现热力图
"""
```

### 3.7 数据管道

```
data/
├── __init__.py
├── db_reader.py           # 从原项目 SQLite 读取数据
├── data_validator.py      # 数据质量检查（新增）
│   - 异常值检测（价格跳变 > 5%）
│   - 时间连续性检查
│   - OHLC 一致性（High ≥ Low, Close 在范围内）
│   - 成交量非负
└── bar_generator.py       # DataFrame → Bar 对象生成器
```

### 3.8 WFV 准入标准（改进版）

| 等级         | 标准                                        | 含义        |
| ---------- | ----------------------------------------- | --------- |
| ✅ VALID    | OOS均值 ≥ IS均值 × 0.7 且 通过率 ≥ 50% 且 活跃窗口 ≥ 8 | 可进入 Paper |
| 🟡 CAUTION | OOS均值 ≥ IS均值 × 0.5 且 通过率 ≥ 40%            | 需更多参数调优   |
| 🔴 FAIL    | 不满足上述                                     | 放弃或重新设计   |

### 3.9 测试清单（Phase 3）

| 测试文件                                 | 覆盖内容                |
| ------------------------------------ | ------------------- |
| `tests/test_engine/test_backtest.py` | 回测引擎基本流程            |
| `tests/test_engine/test_wfv.py`      | WFV 窗口滚动、purge、准入判断 |
| `tests/test_engine/test_metrics.py`  | 指标计算正确性             |
| `tests/test_data/test_reader.py`     | 数据库读取               |
| `tests/test_data/test_validator.py`  | 数据质量检查规则            |

### 3.10 产出物

- 完整回测引擎，支持单次回测和 WFV
- ORB Enhanced 策略的 WFV 报告
- DeepSeek 分析结果（若准入 VALID/CAUTION → 进入 Phase 4）

---

## Phase 3b：Regime Engine — 开盘前 + 开盘后 Regime 确认（预计 5-8 天）

> **触发背景（2026-05-10）**：ES ORB 首轮 OOS 验证通过后，发现最大隐患是  
> "均值回归策略在趋势日爆仓"（REFLECTION.md 第 6 条）。需要在每日交易前  
> 先做 Regime 判断，再决定当天用哪种策略、是否调整仓位、是否跳过交易。

### 3b.0 核心设计思路

**两阶段确认**：

```
阶段 1: Pre-Market (9:00 - 9:25 AM)
  输入：前 N 个交易日日线数据（ADX / ATR 百分位 / EMA 斜率 / 隔夜缺口）
  输出：preliminary_regime + confidence_score
  作用：确定今日"基础市场状态"，设置初始策略偏向

阶段 2: Post-Open Confirmation (9:55 - 10:05 AM，开盘后第 5-6 根 5min bar)
  输入：开盘后前 30 分钟的 K 线（ORB 宽度 / 方向性 / 成交量 / VWAP 位置）
  输出：confirmed_regime（修正或确认 Phase 1 结果）+ preferred_strategies
  作用：用实际开盘行为确认或推翻盘前判断，决定当日策略开关
```

**设计原则**：
- Regime 分类是**每日一次决策**，不是每 bar 实时切换
- 盘前判断 confidence < 0.6 时必须等盘后确认
- Regime 信息注入 FilterContext，策略通过 FilterChain 自动生效
- 回测中模拟两阶段调用（不引入 look-ahead bias）

---

### 3b.1 Regime 类型定义

```python
class RegimeType(Enum):
    TRENDING_BULL  = "TRENDING_BULL"   # 强上升趋势（ADX>25, 价格>EMA20, 连续高点）
    TRENDING_BEAR  = "TRENDING_BEAR"   # 强下降趋势（ADX>25, 价格<EMA20, 连续低点）
    RANGING        = "RANGING"         # 震荡区间（ADX<20, 价格围绕 EMA 振荡）
    HIGH_VOL       = "HIGH_VOL"        # 高波动（ATR > 2×近30日均ATR，或缺口>1%）
    LOW_VOL        = "LOW_VOL"         # 低波动（ATR < 0.5×均ATR，日内振幅极小）
    BREAKOUT       = "BREAKOUT"        # 突破转换（区间收敛 + 成交量放大）
    CHOPPY         = "CHOPPY"          # 混乱/无方向（高ATR但无趋势，多次反转）
    UNKNOWN        = "UNKNOWN"         # 数据不足或盘前状态
```

**策略选择矩阵**：

| Regime | 推荐策略 | 禁用策略 | 仓位系数 | 交易规则 |
|--------|---------|---------|---------|---------|
| TRENDING_BULL | ORB（多偏置）, SwingTrend | VWAP Reversion | 1.0× | 只做多 ORB 突破 |
| TRENDING_BEAR | ORB（空偏置）, SwingTrend | VWAP Reversion | 1.0× | 只做空 ORB 突破 |
| RANGING | VWAP Reversion, PullbackEMA | ORB（趋势跟踪） | 0.8× | 目标缩小至 1R |
| HIGH_VOL | ORB（须方向确认） | 均值回归类 | 0.5× | 仓位减半，止损翻倍 |
| LOW_VOL | VWAP Reversion | SwingTrend | 0.7× | 小目标，少交易 |
| BREAKOUT | ORB（全力参与） | VWAP Reversion | 1.0× | 等待方向确认后入场 |
| CHOPPY | 无（跳过当日） | 全部 | 0× | 当日不交易 |

---

### 3b.2 文件结构

```
core/
└── regime.py              # RegimeType 枚举 + RegimeState + RegimeClassifier ABC

monitoring/
├── __init__.py
├── regime_engine.py       # 具体实现（ES/NQ 专用）
└── regime_logger.py       # Regime 变化日志（审计用）

config/
└── regime.yaml            # 所有阈值参数（ADX/ATR 阈值等，不硬编码）

tests/
└── test_monitoring/
    ├── __init__.py
    └── test_regime.py     # Regime 分类单元测试
```

---

### 3b.3 核心接口设计

#### `core/regime.py`

```python
@dataclass
class RegimeState:
    """当日 Regime 状态快照（由 RegimeClassifier 产出，每日更新两次）"""
    regime_type: RegimeType = RegimeType.UNKNOWN
    confidence: float = 0.0               # 分类置信度 [0, 1]
    size_multiplier: float = 1.0          # 仓位调节系数（CHOPPY=0, HIGH_VOL=0.5 等）
    preferred_strategies: list[str] = field(default_factory=list)  # 推荐策略名称
    blocked_strategies: list[str] = field(default_factory=list)    # 禁用策略名称
    confirmed_at: Optional[datetime] = None   # 盘后确认时间（None=仅盘前）
    indicators: dict = field(default_factory=dict)  # 诊断用：ADX/ATR百分位/缺口等

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    @property
    def can_trade(self) -> bool:
        return self.regime_type is not RegimeType.CHOPPY and self.size_multiplier > 0

    def allows_strategy(self, strategy_name: str) -> bool:
        if strategy_name in self.blocked_strategies:
            return False
        if self.preferred_strategies and strategy_name not in self.preferred_strategies:
            return False
        return True


class RegimeClassifier(ABC):
    """Regime 分类器抽象基类"""

    @abstractmethod
    def classify_premarket(self, daily_bars: list[Bar]) -> RegimeState:
        """盘前分类（用前 N 日日线数据）"""
        ...

    @abstractmethod
    def confirm_postopen(
        self,
        preliminary: RegimeState,
        open_bars: list[Bar],    # 开盘后前 30 分钟的 5min bar
    ) -> RegimeState:
        """盘后确认（用开盘 30 分钟行情）"""
        ...
```

#### `monitoring/regime_engine.py`

```python
class ESRegimeClassifier(RegimeClassifier):
    """ES/NQ 期货 Regime 分类器"""

    # 盘前分类：ADX + ATR 百分位 + EMA 斜率 + 隔夜缺口
    def classify_premarket(self, daily_bars: list[Bar]) -> RegimeState:
        adx = calc_adx(daily_bars, period=14)[-1]
        atr = calc_atr(daily_bars, period=14)[-1]
        atr_pct_rank = percentile_rank(atr, 30)   # ATR 在近30日中的百分位
        ema20_slope = (ema(close, 20)[-1] - ema(close, 20)[-5]) / 5
        gap_pct = (daily_bars[-1].open - daily_bars[-2].close) / daily_bars[-2].close

        # 分类逻辑（阈值从 config/regime.yaml 读取）
        if atr_pct_rank > 0.80 or abs(gap_pct) > 0.01:
            return RegimeState(RegimeType.HIGH_VOL, confidence=0.75, size_multiplier=0.5)
        if adx > 25 and ema20_slope > 0:
            return RegimeState(RegimeType.TRENDING_BULL, confidence=0.7)
        ...

    # 盘后确认：ORB 宽度 / 方向 / 成交量 / VWAP 位置
    def confirm_postopen(self, preliminary: RegimeState, open_bars: list[Bar]) -> RegimeState:
        orb_width = max(b.high for b in open_bars) - min(b.low for b in open_bars)
        direction_bias = (open_bars[-1].close - open_bars[0].open) / orb_width
        vol_ratio = sum(b.volume for b in open_bars) / self._avg_open_volume
        ...
```

---

### 3b.4 集成点

#### `core/filters.py` — FilterContext 升级

```python
# 现在：regime: str = "UNKNOWN"
# 升级为：
from core.regime import RegimeState
@dataclass
class FilterContext:
    ...
    regime_state: RegimeState = field(default_factory=RegimeState)  # 替换原 regime: str

    @property
    def regime(self) -> str:
        """向后兼容：保留字符串访问接口"""
        return self.regime_state.regime_type.value
```

#### `core/strategy.py` — 新增 Regime 钩子

```python
class Strategy(ABC):
    ...
    def on_regime_change(self, regime_state: "RegimeState") -> None:
        """Regime 确认后由引擎调用（可选实现）"""
        pass

    def is_regime_allowed(self, regime_state: "RegimeState") -> bool:
        """策略是否在当前 Regime 下允许交易"""
        return regime_state.allows_strategy(self.name)
```

#### `engine/backtest.py` — 模拟两阶段 Regime

```python
class BacktestEngine:
    def _run_day(self, date: datetime.date, day_bars: list[Bar]) -> None:
        # 1. 盘前 Regime 分类（用前N日日线）
        daily_bars = self._data_handler.get_daily_bars(date, lookback=30)
        regime = self._regime_classifier.classify_premarket(daily_bars)

        # 2. 等待开盘后 30 分钟（第 6 根 5min bar 到达时确认）
        # 回测中：取当日前 5 根 bar 模拟盘后确认
        open_bars = [b for b in day_bars if b.timestamp.time() < time(10, 0)]
        if len(open_bars) >= 5:
            regime = self._regime_classifier.confirm_postopen(regime, open_bars)

        # 3. 将 RegimeState 注入 FilterContext
        self._filter_context.regime_state = regime

        # 4. 若 CHOPPY → 当日跳过
        if not regime.can_trade:
            self._log(f"[Regime] {date} {regime.regime_type.value} — 当日跳过")
            return

        # 5. 执行正常 bar 循环（post-open confirmation 之后的 bar 才允许入场）
        for bar in day_bars:
            if bar.timestamp.time() < time(10, 0):
                continue  # 等待 Regime 确认窗口过后再入场
            self._process_bar(bar, regime)
```

---

### 3b.5 配置文件

```yaml
# config/regime.yaml
regime_classifier:
  lookback_days: 30              # 盘前分类使用的历史日线数

  adx_trending_threshold: 25.0  # ADX > 25 → 趋势
  adx_ranging_threshold: 20.0   # ADX < 20 → 震荡

  atr_high_vol_pct: 0.80        # ATR 百分位 > 80% → HIGH_VOL
  atr_low_vol_pct: 0.25         # ATR 百分位 < 25% → LOW_VOL

  gap_high_vol_threshold: 0.010 # 缺口 > 1% → HIGH_VOL

  open_bars_required: 5         # 盘后确认至少需要 N 根 5min bar
  confirm_after_time: "10:00"   # 盘后确认时间（不允许在此之前入场）

  size_multipliers:             # 各 Regime 的仓位系数
    TRENDING_BULL: 1.0
    TRENDING_BEAR: 1.0
    RANGING: 0.8
    HIGH_VOL: 0.5
    LOW_VOL: 0.7
    BREAKOUT: 1.0
    CHOPPY: 0.0
    UNKNOWN: 0.5                # 谨慎：数据不足时减仓

  # 盘后确认参数
  postopen_orb_wide_atr_ratio: 1.5   # ORB 宽度 > ATR × 1.5 → 偏向 BREAKOUT
  postopen_direction_threshold: 0.3  # 方向偏置 > 30% → 修正 Regime 方向
  postopen_volume_spike: 1.3         # 开盘量 > 均量 × 1.3 → 提高 BREAKOUT 概率
```

---

### 3b.6 测试清单

| 测试文件 | 覆盖内容 |
|---------|---------|
| `tests/test_monitoring/test_regime.py` | RegimeType 枚举 / RegimeState 属性 |
| `tests/test_monitoring/test_regime_classifier.py` | 盘前分类逻辑（各 Regime 路径）|
| `tests/test_monitoring/test_regime_postopen.py` | 盘后确认（修正 / 保持 / 推翻）|
| `tests/test_monitoring/test_regime_filter.py` | Regime 结果注入 FilterContext |
| `tests/test_engine/test_backtest_regime.py` | 回测中 Regime 模拟（CHOPPY 跳过 / size_multiplier 生效）|

---

### 3b.7 产出物

- `core/regime.py` — RegimeType + RegimeState + RegimeClassifier ABC（含测试）
- `monitoring/regime_engine.py` — ESRegimeClassifier 具体实现
- `config/regime.yaml` — 所有阈值参数外置
- `core/filters.py` 升级 — FilterContext.regime_state 替换原 regime: str
- `core/strategy.py` 升级 — on_regime_change 钩子
- `engine/backtest.py` 升级 — 模拟两阶段 Regime 确认
- 回测结果对比：Regime 过滤前 vs 后的 Sharpe / 最大回撤 / 胜率对比
- 单元测试全部通过

---

## Phase 4：Paper 运营（预计 2-4 周）

### 4.1 目标

- 在 Paper Trading 环境运行策略 ≥ 10 个有效交易日
- 验证实盘环境的完整链路
- 积累实盘运营经验

### 4.2 文件结构

```
live/
├── __init__.py
├── trader.py              # 实盘/Paper 交易主循环
├── scheduler.py           # 定时任务（开盘/收盘/对账）
└── paper_runner.py        # Paper 模式启动脚本

execution/
├── __init__.py
├── ib_execution.py        # IB 执行器（从原项目迁移并简化）
└── simulated_execution.py # 模拟执行器（回测用）

monitoring/
├── __init__.py
├── trade_logger.py        # 交易日志
├── dashboard.py           # 实时监控面板（简化版）
└── daily_report.py        # 每日报告生成
```

### 4.3 Paper 准入清单（复用原项目模板）

| 准入项            | 当前状态      | 标准            |
| -------------- | --------- | ------------- |
| WFV OOS Sharpe | 待 Phase 3 | ≥ 基准 × 0.7    |
| Paper 天数       | 0/10      | ≥ 10 个有效交易日   |
| Regime 覆盖      | 0/3       | ≥ 3 种不同市场状态   |
| 日志完整性          | —         | 无 CRITICAL 告警 |
| PnL 对账         | —         | 0 差异          |

### 4.4 每日复盘流程（DeepSeek 驱动）

```
盘后自动执行：
1. daily_report.py → 生成当日交易摘要
2. analyze_backtest.py → DeepSeek 分析当日交易质量
3. 输出：当日复盘报告（胜率、盈亏比、信号过滤原因、建议调整）
```

### 4.5 产出物

- Paper 运营日志（≥ 10 天）
- 每日 DeepSeek 复盘报告
- Phase 5 实盘切换评估

---

## Phase 5：实盘切换

### 5.1 准入条件（全部满足）

- [ ] WFV OOS ≥ IS × 0.7 且 通过率 ≥ 50%
- [ ] Paper ≥ 10 有效交易日
- [ ] Regime 覆盖 ≥ 3 种
- [ ] 无 CRITICAL 告警、对账 0 差异
- [ ] （多策略时）日 PnL 相关性 < 0.5

### 5.2 切换前检查清单

```
□ TWS 已设置禁止 Auto-Logoff
□ IB 账户资金充足
□ 确认 port 7496（实盘）而非 7497（Paper）
□ 运行一次 dry run 确认无启动错误
□ 备好紧急停止脚本
□ 风控参数以实盘口径校准
```

### 5.3 切换执行

```
1. 降低仓位至 Paper 的 50%（首周）
2. 每日 DeepSeek 复盘（重点监控与 Paper 差异）
3. 首周无异常 → 恢复至 Paper 仓位
4. 持续运营
```

---

## 技术栈与依赖

### Python 依赖（新项目）

```
# 核心
pandas>=2.0
numpy>=1.24
pyyaml>=6.0
pydantic>=2.0         # 配置验证（代替手写 dataclass 验证）

# IB 连接（Phase 4+）
ib_insync>=0.9.86

# 回测/分析
scipy>=1.10           # 统计计算
matplotlib>=3.7       # 图表（可选）
seaborn>=0.12         # 热力图（可选）

# DeepSeek 集成（Phase 3+）
openai>=1.0            # DeepSeek API 兼容 OpenAI SDK

# 测试
pytest>=7.0
pytest-cov>=4.0

# 开发
black>=23.0           # 代码格式化
ruff>=0.1              # Linter
mypy>=1.0             # 类型检查
```

### 数据库（复用原项目，只读）

```
D:\Python_Projects\cc_quant_horse\data\db\
├── futures_data.db      # 期货数据 (ES, NQ, MNQ, MES)
└── stocks_data.db       # 股票数据 (TSLA, NVDA, META, AMD)
```

---

## 风险与应对

| 风险                   | 概率  | 影响  | 应对                                 |
| -------------------- | --- | --- | ---------------------------------- |
| ORB Enhanced WFV 不通过 | 中   | 高   | 准备 T4 TrendCont 作为备选策略             |
| DeepSeek API 不稳定     | 低   | 中   | 分析功能有降级方案（纯统计）                     |
| 数据库数据有质量问题           | 中   | 中   | Phase 1 首先运行数据质量审计                 |
| ib_insync 兼容性问题      | 低   | 低   | 使用与原项目相同版本                         |
| 参数过拟合                | 高   | 高   | Purged WFV + bootstrap CI + 减少参数数量 |

---

## 里程碑与时间估算

| 里程碑            | 预计完成日      | 完成标志                   |
| -------------- | ---------- | ---------------------- |
| M0: Phase 0 完成 | 2026-04-26 | 用户批准本计划                |
| M1: Phase 1 完成 | 2026-04-30 | core/ 全部模块 + 测试通过      |
| M2: Phase 2 完成 | 2026-05-05 | ORB Enhanced 策略可运行     |
| M3: Phase 3 完成 | 2026-05-10 | WFV 报告产出，决策 Paper/重新设计 |
| M4: Phase 4 启动 | 2026-05-12 | Paper Trading 开始       |
| M5: Phase 4 完成 | 2026-05-26 | 10 有效交易日达标             |
| M6: Phase 5    | 2026-05-28 | 实盘切换（若准入通过）            |

> 以上为乐观估算。每个 Phase 的实际耗时取决于遇到的问题数量。

---

## 当前状态与下一步

**当前**：Phase 0 即将完成，等待用户审批 PLAN.md

**用户审批后**：

1. 创建 `.gitignore` 和 Memory 文件
2. 初始化 Git 仓库
3. 开始 Phase 1：先写 test_events.py，再写 core/events.py

---

> 本计划将随开发进展持续更新。