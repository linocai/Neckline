# Neckline PROJECT_PLAN · V2.5.0（K9 换引擎 + 新架构分层）

> 立项：2026-08-20　·　目标版本：**v2.5.0，Build 10**　·　基线：已部署的 v2.4.2 Build 9
> 本文件是本仓库**唯一当前权威计划**。V2.4.2（K8 时代）全文已归档至
> `archive/施工图/V2.4.2_施工图_20260820归档.md`，仅供追溯，不是施工指令。

---

## 1. 概述

### 1.1 本版做什么

把选股引擎**从 K8 整体换成 K9**，并按《Neckline 新架构》重排分层。这不是一次功能迭代，
是一次换发动机：K8 的「驱动种子 → 方向 → 篮子 → 六关 → Tier → 卡片」整条链退役留档，
K9 的「事实包 → 四通道召回 → 机械排序 → 名额 → 解释 → 预案 → 次日核对 → 三条成绩线」上线。

系统的根本任务（架构 §零、K9 §1.2）：

> **每天盘后交出一份候选清单：这些票在我的逻辑框架与评判体系中，D+1 到 D+4 会涨。**
> 每只票带一份「明天怎么走算成立、怎么走算放弃」的预案；第二天早上看哪几只验证了自己的预案。

### 1.2 权威文件（施工前逐字读完，不许只扫标题）

| 文件 | 管什么 |
|---|---|
| `/Users/linotsai/Lino/whynotme/K9.md` | 选股策略：什么会涨 |
| `/Users/linotsai/Lino/whynotme/Neckline新架构_20260818.md` | 系统架构：分层职责 |
| `AGENTS.md`（本仓根） | 仓库纪律：根目录六条目、archive、生产禁 import whynotme、测试禁落工作库 |
| `.learnings/LEARNINGS.md` | 已固化的教训，**不许退化**（尤其 LRN-20260816-001 双日期契约） |

⚠ 裁定 1、裁定 2、裁定 10 **改动了这两份权威文件的原文**，共 **17 处措辞必须同步修订**（逐处见 §5.1）。
⚠ 之后又追加两批：裁定 12/15 的同步修订（§14「裁定 12 返工」「S6 完工」两行），
以及 **§5.1-E 的第五组四处**（裁定 16 一处 + 裁定 10 的**落实**三处 —— 后者不是新裁定，
是 §5.1 原清单漏掉的两句活坑，见 §5.1-E）。
⛔ 修订完成前不得开工写四通道 —— 否则后来者照着旧文件施工，会把行业指数口径与「上方空间」的混淆再做回来。

### 1.3 K9 的「层」与架构的「层」不是同一套编号 —— 先记住这张对照表

这是本项目最容易串的一处。**说「第三层」之前先说清是谁的第三层。**

| K9 的层 | 架构的层 | 落在哪个包 |
|---|---|---|
| —— | **架构第一层 · 事实层** | `neckline/facts/` |
| K9 第一层 · 股票池边界（9 条排除项） | 架构第二层 · 策略层 | `neckline/k9/boundary.py` |
| K9 第二层 · 形态召回（四通道） | 架构第二层 · 策略层 | `neckline/k9/channels/` |
| K9 第三层 · 排序 + 第五节名额 | 架构第二层 · 策略层 | `neckline/k9/ranking.py` `quota.py` |
| —— | **架构第三层 · 解释层** | `neckline/explain/` |
| K9 第四层 · 预案（§六） | 架构第四层 · 预案层 | `neckline/playbook/` |
| K9 §七 次日核对 | 架构 §四 | `neckline/auction/` |
| K9 §八 成绩单 | 架构 §五 | `neckline/scorecard/` |
| —— | 架构 §六 交割单分析台 | `neckline/review/` |

本文件此后一律写「**K9 第 N 层**」或「**架构第 N 层**」，不写光秃秃的「第 N 层」。

---

## 2. 用户已裁定的事项（⛔ 不得在施工中重开讨论）

以下 **17 条**是 2026-08-19/20 用户的明确裁定（裁定 10、11 为立项收口时追加；
**裁定 12–15 为 2026-08-20 第三组开工时追加，其中裁定 12 是对 S3 的返工；
裁定 16、17 为 2026-08-20 第五组开工时追加**）。
任何与之冲突的「更好的想法」一律作废。

### 裁定 1 · 「上方空间」的循环依赖 → 用机械代理，且命名分开

| 用途 | 名称 | 谁算 | 在哪一层 |
|---|---|---|---|
| 排序 | **上方机械空间** | 机械（过去 N 日最高价） | K9 第三层（架构策略层） |
| 预案 | **第一压力位** | LLM 逐票判断 | K9 第四层（架构预案层） |

两个名字在代码标识符、DTO 字段、数据库列、报告文案、App 文案里**全部分开**，⛔ 不许互相顶替。

- **形态 1** 用**正向**打分：上方机械空间大 → 得分高。
- **形态 3** 的「距关键位置的远近」= **同一个量的反向打分**：空间小 → 得分高
  （K9 §3.4「贴着那个位置还没捅破的状态最好」）。⛔ 不另造第三个量、不另写第二份实现。

### 裁定 2 · 行业强度口径 = 申万行业当日全部成员涨跌幅的**中位数**，剔除当日停牌成员

⛔ **不用行业指数涨跌幅。** 这一条**改动了权威文件原文**，必须同步修订（§5.1）。

### 裁定 3 · 申万分类用**二级（L2）**

### 裁定 4 · 量比问题已被实测消解

见 §4.7：`daily_basic.volume_ratio` 就是 K9 §3.5 要的盘后口径，直接用；但**做排名必须自算**。

### 裁定 5 · 参数标定归 whynotme，Neckline 只消费标定完、用户确认过的参数包

新增硬纪律：

> **Neckline 读不到参数配置时，明确报「参数未配置」并停止出清单，⛔ 不使用任何默认值。**

映射到 K9 报告三态：**参数未配置 = 「今天没跑成」**，⛔ 不是「今天没有」。
「今天没有」= 跑通了、结果为空、可以被信任；「今天没跑成」= 系统没工作。

### 裁定 6 · K8 历史数据留档只读

`baskets` / `basket_members` / `basket_cards` / `tier_history` / `gate_evaluations` /
`out_candidates` / `basket_dropped_handoff` / `basket_stage_handoff` 等表**保留、只读、不迁移、不回填**，
保留**一个**只读入口供追溯（§6.13）。

### 裁定 7 · 盘中提醒整块删除，但两块承重墙搬家后保留

- `sentinel/quotes.py`（405 行，新浪主 / 腾讯备实时源）→ 搬到 `neckline/data/realtime.py`
- `sentinel/dedup.py`（123 行，当日只跑一次的防重）→ 搬到 `neckline/dedup.py`

其余 16 个文件 + `custom_alerts.py` + `api/app.py::_sentinel_loop` 的盘中分支全部删除，
`neckline/sentinel/` 包物理删除。

### 裁定 8 · K8 直接停用留档，不并行

架构 §七 的「策略并行运行」机制**本版不实现**，切换一刀切。

### 裁定 9 · 不做阅读行为记录

架构 §九唯一未决项就此关闭。系统只对清单负责。

### 裁定 10 · 批准新增「10:00 结算读数」，且三分支判定的唯一权威是这一拍

- **动作**：D1 的 **10:00–10:05 一次性结算快照**，代入 D0 冻结的预案条件求出三分支终值。
- **性质定死**：**零 LLM、零推送、不进 App 首屏**。唯一用途是成绩线的三分支结算。
- **9:29 竞价核对表只输出「已触发放弃 / 其余待开盘后观察」两段。
  ⛔ 不许在 9:29 把任何票写成「成立」。** 「成立」只能由 10:00 这一拍产生。
- **依据（写进 Plan，防止后来者当成越界功能）**：
  1. 架构 §5.1 本来就要求冻结「D0 预案条件、**D1 竞价与开盘 30 分钟读数**、三分支判定结果」；
  2. K9 §八 也把三分支定义在「**D1 竞价 + 开盘 30 分钟**」这个窗口上；
  3. 架构 §四 那句「不**持续**观察 9:30 以后的价格」管的是**推送盘中提醒**与**跟踪持仓**，
     一次性结算读数**不落在该禁令内**。
- ⚠ 配套：架构 §四 的边界措辞必须补一句把「一次性结算读数」写成明文例外（§5.1-D），
  否则文件与实现对不上，下一个人会照文件把它删掉。

### 裁定 11 · App 三板块 = 选股 / 成绩 / 复盘

持仓板块整块下线，成绩线升为板块。**次日核对表仍留在「选股」板块内作第二视图**，
9:26–15:00 默认落在它上面。「我的成绩」留在复盘板块（架构 §5.3：它走周末复盘线）。

### 裁定 12 · 盘中临时停牌**计入**行业中位数 —— 这是对 S3 的返工

150 天实测：**全天停牌**（`suspend_type='S'` 且 `suspend_timing` 为空）**2001 行，0 行**
进 daily；**盘中临时停牌**（`suspend_timing` 非空，如 `'9:30-9:40'`）**36 行有 35 行**进了
daily，分布在 **25/150 天**。这些票当天正常交易、有完整涨跌幅。

- **只剔除全天停牌**；盘中临时停牌**照常计入**中位数。
  S3 实现的「一律排除 + 计数 + 告警」**已改掉**（S6 组返工，见 §14）。
- `market_json.suspendAnomaly` 的判别证据**保留**，但只对「全天停牌却出现在 daily」
  这一**真异常**告警。
- 同步修订：K9 §3.0 与架构 §3.1 的「剔除当日停牌成员」已补明确定义为「**只剔全天停牌**」。

### 裁定 13 · 形态 2「非一字跌停且当日有实际换手」的判据形状

- **一字跌停 = 开、高、低、收四价全等于当日跌停价**。这是**零参数**精确定义
  （跌停价由既有 `data/limit_derived.py` 的板块规则算，含两个制度分界日），
  ⛔ 不要为它造参数。
- **有实际换手 = 成交量 ÷ 20 日均量 ≥ [待标定]**。⛔ 不用换手率、不用成交额绝对值。

### 裁定 14 · 形态 3「当日尚未放量爆发」= 成交量 ÷ 20 日均量 < [待标定]

⛔ 只看量，不加涨幅门槛。

### 裁定 15 · 形态 1 补一道放量倍数下限，与形态 3 的上限共用同一个待标定值 **V**

K9 原文有一处自相矛盾（§3.2 把放量倍数列为**强度性**、§3.6 说强度性不设门槛，
但 §九 又列着「形态 1 放量倍数排名门槛」；而 §3.4 要求两形态互斥）。裁定：

- 形态 1 定义性**增加**「放量倍数 ≥ **V**」，形态 3 定义性是「放量倍数 < **V**」，
  **V 是同一个待标定值**，两形态在量上严丝合缝互补，§3.4 的互斥由**判据本身**保证，
  ⛔ 不靠事后仲裁。
- 同步修订 K9（已落地）：§3.2 把「放量倍数」移入定义性；§3.6 补明这是唯一例外及理由；
  §九 的两项合并成同一个参数 V；另新增 §3.0.1 作为放量倍数的全文唯一定义。
- ⚠ 三条裁定用的是**同一个量**「成交量 ÷ 20 日均量」（= K9 §3.2 的放量倍数）。
  **做成一处共享计算**（`k9/volume.py`），⛔ 不许在三个地方各算一份。
  注意它与形态 4 的「量比」（÷ **5** 日均量，盘后口径，取 `daily_basic.volume_ratio`）
  是**两个不同的量**，⛔ 别混。
- 落到参数包里是**两个**不同的待标定值：形态 2 的有效换手门槛，与形态 1/3 共用的 **V**。
  ⛔ 都不许填默认值。见 §8.2 #21、#22。

### 裁定 16 · 放量倍数分界值 **V 豁免分档**（K9 §五-6 的唯一例外）

裁定 15 把放量倍数 V 移进**定义性**条件后，K9 §五-6「定义性条件中带数字的项设两档」
按字面会套到 V 上。用户裁定：**V ⛔ 不分档**（`volume.eruptionMultiple` 是**一个**值，
⛔ 不生 strict/relaxed 两个键）。两条理由：

1. **分档在它身上没有意义**。形态 1 是「放量 ≥ V」、形态 3 是「放量 < V」，两者在放量
   这一维上**穷尽整个空间** —— 移动分界点只把票从 p3 挪到 p1（或反向），
   `p1 ∪ p3` 的召回总量**一只都不会多**。而 §五-6 分档的目的正是「严格档凑不够 10 只
   时把候选变多」（K9 §五-1 的容量下限），V 做不到这件事。
2. **分档会破坏裁定 15 建立的互斥**。若给 V 两档，放宽档一开，放量倍数落在两个值
   **之间**的票会**同时命中「p1 放宽档」与「p3 严格档」**，K9 §3.4 要求的「严丝合缝
   互补」当场破掉，又得回到事后仲裁 —— 那正是裁定 15 要消灭的东西。

⛔ **其余带数字的定义性项照旧分两档**（形态 2 的 `channels.p2.<档>.minVolMultiple`
是真旋钮：调高 = 候选变少，它分档）。

- **落地状态**：S6 已按此实现（`_CHANNEL_TIER_KEYS` 里没有任何带 `erupt` 的键，
  AST 守门断言 p1 与 p3 读同一个属性链 `params.volume.eruption_multiple`），
  本裁定是把 S6 的结构判断**升为正式裁定**，⛔ 不是新的施工要求。
- **同步修订 K9（已落地）**：§五-6 那一行补「⚠ 唯一例外：放量倍数分界值 V ⛔ 不分档
  （裁定 16）」，表下补两条理由与「其余项照旧分两档」的界限
  （whynotme commit `f29a3a3`）。
- ⚠ **标定侧仍需知道这个结构性质**：V 只有**一个**值要标，它同时是 p1 的下限与 p3 的
  上限；「p1 召回太少」⛔ 不能靠放宽 V 解决（那只会把 p3 的票搬过来）。见 §13 Backlog。

### 裁定 17 · 停抓概念板块（`ths_*`），**已抓的数据原地保留**

**事实**（S6/S7 组核实、证据见 §14「概念板块日更核实」那一行）：

- `ths_index` / `ths_member` / `ths_daily` 三份扁平 parquet 在 S3 之后**已零消费方**
  —— 三个读侧模块（`report/sectors.py` / `board_pool.py` / `intel_candidates.py`）
  已在 commit `e89c1fa` 物理删除；
- K9 §3.0 明写**不使用概念板块**（「⛔ 不使用申万行业指数涨跌幅，不使用概念板块」），
  架构 §3.1 同；
- 配额账：`ths_daily` 5 次/日 + `ths_index`+`ths_member` 395 次/周
  ≈ **21,750 次/年**，其中那 395 次是**连续**调用，几乎占满客户端 450 次/分限频
  窗口一整分钟。

**裁定**：

- **移除 `scripts/daily_update.py` 里的 `ths_*` 抓取段**（`update_concept_boards` 及其
  调用点，含周更那 395 次连续调用）。这是**唯一挂在定时器上**的抓取入口。
- **已抓的 parquet 原地保留、⛔ 不删**（`data/parquet/ths_{index,member,daily}.parquet`
  + `ths_snapshot_meta.json`，共约 21 MB）：将来解释层若要拿概念当**背景材料**仍可用。
- `limit_cluster_daily.anchor_concept` 与 `app_settings.intel_watch_boards` 两列
  **保留不 DROP**（裁定 6）。
- ⚠ 概念板块**永远不进任何机械计算**（K9 §3.0），保留数据 ⛔ 不等于恢复它作判据输入。

---

## 3. 技术选型（在此定死，⛔ 不留给 build 再决定）

### 3.1 沿用既有底座（不动）

| 项 | 选型 | 理由 |
|---|---|---|
| 行情底座 | TuShare 薄封装 `data/tushare_client.py`（450 次/分限频 + 退避）+ parquet 日分区 `data/market_data.py` | 已稳定运行一年，K9 第一层 9 条排除项里 8 条有现成数据 |
| 台账 | SQLite（stdlib `sqlite3`，无 ORM），`neckline/db.py::init_schema` 受控写入口 | 既有纪律：只有 API 启动 / 显式写命令 / RC 迁移步骤在**已确认目标库且已备份后**可调 DDL |
| 大表 | parquet 日分区 `year=YYYY/YYYYMMDD.parquet` + `TABLE_FLOAT_COLS` 显式 dtype 声明 | 2026-07-27 脏基准事故的唯一修法，新表必须显式声明数值列 |
| 计算 | polars（lazy scan + 列投影） | 既有 |
| 服务 | FastAPI + 单 Bearer token 鉴权 | 既有 |
| 推送 | APNs token-based（`push/apns.py`） | 既有 |
| 客户端 | SwiftUI（iOS + macOS 同工程），列表 376 + 详情自适应，`Components/DesignTokens.swift` | 既有视觉规范 |
| 联网检索 | Tavily（`search/tavily.py`），LLM Provider 本身不承担联网 | V2.4.2 已收口，保留 |
| LLM | `llm/{base,openai_compat,router,factory,budget}.py`，Provider 可配、任务路由可配、真实 token 记账 | 既有；K9 只有三个岗位用它 |
| 排程 | systemd timer + `neckline-evening.target`（`StopWhenUnneeded=yes` ⛔ 不许删） | 既有；那一行的血泪见 `deploy/neckline-evening.target` 头 |

### 3.2 新增/改选（本版定死）

| 项 | 选型 | 定死理由 |
|---|---|---|
| 行业分类源 | **申万 2021 版二级（SW2021 L2）**，`index_classify(src='SW2021')` + `index_member_all` 分页 | 裁定 3；实测 `src='SW'`（2014 老版）返回 0 行，只有 SW2021 可用 |
| 行业强度 | **成员 `ret_1d` 中位数**（事实层，无参数）+ **排名/门槛（策略层，读参数包）** | 裁定 2；拆开是为了满足架构 §二「凡是我会想去调的东西都落在策略层」 |
| 行业指数行情 `sw_daily` | **不落、不回补** | 裁定 2 之后它不是必需品；落了只会长出第二份行业强度源并悄悄漂移。whynotme 若做对照自己拉 |
| 事实包大表载体 | **parquet 日分区** `fact_pack/`（每天一份，~5500 行 × ~40 列 ≈ 1.5 MB） | SQLite 存 5500 行/天 × 250 天会把 2vCPU/1.6G 的机器拖垮；parquet 是本仓既有大表惯例 |
| 事实包清单/指纹 | **SQLite `fact_packs`**（元数据 + sha256 + 缺口 + 版本），永不裁剪 | 审计要活得比数据久：parquet 被滚动裁剪后，「那次跑用的哪版包」仍查得到 |
| 事实包保留策略 | 生产滚动保留 **250 个交易日**的 parquet；更早的裁剪，清单行保留 | 250 > `MAX_LOOKBACK_PACKS`(120) 有充足余量；全历史包由 whynotme 侧自建 |
| 策略层历史读取上限 | `MAX_LOOKBACK_PACKS = 120` 个交易日（**工程容量上限，不是策略参数**） | 参数包里任何窗口 > 120 一律判为配置无效；120 × 5500 × 10 列 ≈ 53 MB，2vCPU/1.6G 扛得住 |
| 参数包 | **版本化 JSON**，`Backend/config/k9-params.<version>.json`，CLI `--k9-params <path>` 显式传入 | 沿用 V2.4.2 `direction-pipeline.*.json` 的成功先例；**无默认路径、无内嵌默认值** |
| 参数对象 | `@dataclass(frozen=True)`，**每个字段都没有默认值** | 结构性保证「不许 fallback 到默认值」：少一个字段就构造不出来，不是靠 if 判断 |
| 防重台账表名 | 继续用 `sentinel_events`（表不改名） | 改名 = 迁移风险换零产品价值；模块 docstring 记明「包没了、表名留着」 |
| 三态语义 | `ReportState` 三值枚举 + 全映射渲染（无 fallback 分支） | 架构 §3.5；空清单必须可被信任 |
| 守门方式 | **AST 扫描守门单测**（本仓既有先例：`tests/test_v240_p0_retirement_guard.py` 等） | 四条边界靠结构保证，不靠注释提醒 |
| 分支策略 | 直接在 `main` 上做，**不开分支** | 沿用现行做法 |

### 3.3 新的包布局（定死）

```
neckline/
  facts/                    架构第一层 · 事实层
    pack.py                 FactPack DTO / build（返回 CompletePack | IncompletePack）
    store.py                唯一写入口 freeze_pack() + 只读 load_pack / load_pack_range
    industry.py             申万二级成员中位数（无参数事实）
    limitmap.py             涨停分布 + 涨停簇（原 scan/cluster.py 迁入）
    completeness.py         冻结前的缺口判定（原 scan/freshness.py 逻辑并入）
    direction_llm.py        LLM 方向解读（旁路，⛔ 不入机械链）
  k9/                       架构第二层 · 策略层（全机械、零 LLM、确定性）
    contract.py             策略契约三条的代码化
    params.py               参数包加载 + 显式校验（无默认值）
    boundary.py             K9 第一层 9 条排除项
    industry_heat.py        行业热度分（读参数包的 minMembers / excludedL2Codes）
    upside_room.py          上方机械空间（唯一实现，正向/反向两个打分器共用）
    channels/p1_breakout.py p2_rebound.py p3_riser.py p4_moneyflow.py
    ranking.py              三成分排序
    quota.py                名额分配（保底 + 自由竞争 + 分档 + 成色）
    run.py                  唯一编排入口
    store.py                k9_runs / k9_listing_entries / k9_channel_hits + disposition parquet
  explain/                  架构第三层 · 解释层（双盲）
    input.py aggregate.py news_exclusion.py store.py
  playbook/                 架构第四层 · 预案层
    skeleton.py fill.py model.py evaluate.py store.py
  auction/                  次日核对（保留窗口纪律，内容重写）
    pipeline.py collect.py quality.py checklist.py settle.py store.py
  scorecard/                三条成绩线
    listing.py coverage.py mine.py store.py
  review/                   交割单分析台
    parse.py reconcile.py cashflow.py bindery.py conclusions.py store.py material.py
  report/                   报告
    pipeline.py render.py store.py evening.py
  data/
    realtime.py             ← sentinel/quotes.py 搬家
    sw_industry.py          申万分类拉取 / 落库 / 校验
    panel.py                ← strategy/features.py 搬家（它是数据面板，不是策略）
    （tushare_client / market_data / board / limit_derived / adjust / price_stale / top_list 不动）
  dedup.py                  ← sentinel/dedup.py 搬家
  db.py api/ llm/ push/ search/ calendar/ config/ settings_store.py  （保留）
```

**整包删除**：`neckline/sentinel/`（扣除两块承重墙）、`neckline/scan/`、`neckline/selection/`、
`neckline/strategy/`（`features.py` 先搬 `data/panel.py`）、`neckline/profile/`、
`neckline/custom_alerts.py`、`neckline/positions_entry.py`、`neckline/decision_log.py`、
`neckline/user_actions.py`。

---

## 4. 当前状态

### 4.0 施工进度（每完成一片就改这里；细节见 §14）

🔴 **批 A 全部完工（S0–S14）**（**30 个提交**，`448adb5` → 本组末次，
⛔ 全程未部署 / 未 ssh / **未推送**）。
测试 **1430 passed / 0 skipped / 0 failed**（后端）+ **43 passed / 6 skipped**（App；
skip 的 6 条是要真 dev 后端才跑的联调冒烟）。
⚠ **S14 只准备、⛔ 一步没执行**：发版清单在 **§9.6**，能在本地先跑一遍的那几件做成了
`tests/test_v250_s14_release_gate.py`（13 条，全在临时库上）。
🔴 **上云前有一个阻塞项**：容量红线（§9.6 步骤 0 / §13.1-B5）必须先有结论。

| 片 | 状态 | 落在哪 |
|---|---|---|
| S0 立项收口与权威文件修订 | ✅ | 权威文件 17 处 + 第五组 4 处（§5.1-E） |
| S1 退役与搬家 | ✅ | 后端 −106,734 行 / App −6,493 行 |
| S2 申万分类接入 | ✅ | `data/sw_industry.py` + 两张表 |
| S3 事实层与事实包 | ✅ | `facts/`，`PACK_VERSION = fp-2` |
| S4 覆盖率成绩线 | ✅ | `scorecard/{coverage,store}.py` |
| S5 参数包契约与三态 | ✅ | `k9/params.py` + `report/state.py` |
| S6 策略层骨架 | ✅ | `k9/`（全机械、零 LLM） |
| S7 报告骨架与推送 | ✅ | `report/` + `k9_reports` |
| S8 次日核对与 D1 结算 | ✅ | `auction/` + `playbook/{model,evaluate,store}.py` |
| S9 解释层 / S10 预案层 | ✅ | `explain/` + `playbook/{skeleton,fill}.py` |
| **S11 交割单分析台** | ✅ | `review/{bindery,conclusions}.py` + `review_conclusions` 表 |
| **S13 对外接口** | ✅ | `export_research_snapshot.py --include-fact-packs` + `legacy_k8.py` |
| **S12 App 重做** | ✅ | `Views/{Selection,StockDetail,CheckList,Scoreboard,Review}View.swift` + `Models/{K9,CheckList,Scoreboard}Models.swift` + 契约对拍重建 |
| **S14 发布门禁** | ✅ **只准备，⛔ 未执行** | 清单 §9.6 + `tests/test_v250_s14_release_gate.py` |
| 批 B（S15–S17） | ⬜ 等参数标定 | §6 批 B |

🔴 **上线前必须知道的两件事**：① **参数包还没标定** —— `Backend/config/k9-params.json` 不存在，
清单段每天会出「今天没跑成 · 参数未配置」，这是**设计行为**（裁定 5 / §9.5），⛔ 不是故障；
② **生产仍跑 v2.4.2 Build 9**，S1 摘掉的三项 K8 日更与裁定 17 停掉的概念抓取，
在上云之前**都还在生产上继续跑**（§13.1-B6）。

⚠ **等用户裁定的 11 条**收拢在 **§13.1**（B1–B11），施工侧已绕过它们继续做，⛔ 未自行拍板。
🔴 其中 **B5（容量红线）是上云前的阻塞项**；**B10（行业分 / 选票分的「同期」窗口）
是 S17 开工前必须定的一个数** —— ⛔ 施工侧不自行拍板（「定性需求不许自作主张定量」）。

### 4.1 基线

已部署 **v2.4.2 Build 9**，生产在 `deploy@114.66.0.38:/opt/neckline`，
公网健康检查 `https://nk.linotsai.top`（返回 `v2.4.2`）。这也是本版**已验证的回滚目标**。

规模参考：Backend 约 4.6 万行代码 + 5.9 万行测试（162 个测试文件）；App 38 个 Swift 文件 2.9 万行。

### 4.2 可直接接住的（骨架留用，⛔ 别推倒重来）

| 现有资产 | 留用理由 |
|---|---|
| `data/tushare_client.py` + `data/market_data.py` | K9 第一层 9 条排除项里 8 条有现成数据 |
| `data/board.py` | 科创板 / 北交所 / 创业板判定，`market` 字段优先 + 整段正则 fallback |
| `data/limit_derived.py` | 自算涨跌停，含 2020-08-24 创业板改革与 2026-07-06 主板 ST 两个制度分界日；覆盖率成绩线的口径就靠它 |
| `report/industry_strength.py` 的三件套 `_day_local_table` / `_attach_persist` / `next_persist_days` | 算的正好就是成员 `ret_1d` 中位数；本版只换行业来源与加停牌剔除 |
| `report/industry_strength_store.py` 的日更 / bootstrap / verify / 口径指纹体例 | 那张预计算表是为躲生产 OOM 专门做的（见 §12 坑 1），体例照搬 |
| `auction/pipeline.py` | 9:26 起跑 / 9:29 硬截止 / 当日防重 / 窗口外零落库 / 事后不许补跑 —— 正是 K9 §七要的纪律 |
| `auction/collect.py` + `auction/quality.py` | 竞价冻结抓取 + 报价时间戳/陈旧校验 |
| `review/parse.py` + `reconcile.py`（FIFO 部分）+ `cashflow.py` | 两家券商 schema、回合闭合、四分类（刻意不给「账户净变动」合计字段） |
| `selection/run_store.py` 的事务纪律 | 长任务写审计在事务外、一个短事务写终态、partial 不覆盖已发布快照、缺数据不冻结 |
| `report/store.py` 的双日期契约 + `push/apns.py` + 周日排程 | LRN-20260816-001，⛔ 不许退化 |
| `App/.../DesignTokens.swift` + `Components/` + 列表 376/详情自适应骨架 | 视觉规范照旧 |

### 4.3 整块退役

| 退役对象 | 规模 |
|---|---|
| `selection/` 全部 K8 选股件（packs C1/C2/Y1/Y2/Z1/Z2/K8-skeleton、六关、Tier、direction_* 全套、深研、engine_api、threshold_shadow、basket_card、aggregate） | ~15,096 行 |
| `sentinel/`（扣除两块承重墙）+ `custom_alerts.py` + `positions_entry.py` + 持仓 API 六路由 | ~7,500 行 |
| App `PositionsView.swift` + `PositionModels.swift` + `PositionExtras.swift` | ~2,954 行 |
| 双时钟复盘：`review/{selection_clock,trade_clock,out_shadow,basket_review,basket_review_store,handoff}.py` | ~4,200 行 |
| `scan/` 驱动种子体系（seeds/corr/leader/landing/regime/stage/verify/freshness） | ~4,066 行（`cluster.py` 迁入 facts/，`freshness.py` 逻辑并入 facts/completeness.py） |
| `strategy/{brain,charter_copy,momentum_config,signals}.py` + `profile/` + 章程/包激活脚本 | ~1,500 行 |
| `report/` 的 K8 件：`basket_daily / holding_k4_check / holding_store / info_card / intel / news_alerts* / sectors / board_pool / sector_moneyflow / sentiment / score_display / exec_hint / pending_track / card_plan_repair / industry_strength*` | ~6,000 行 |

删除时**同步删对应测试**，⛔ 不许留下 skip 掉的僵尸测试。

### 4.4 已实测的事实 —— ⛔ 不要重测，直接用

对生产 TuShare token（600 元档）实测通过，无权限错误：

- `index_classify(src='SW2021')`：L1 **31** / L2 **134** / L3 **346**。`src='SW'`（2014 老版）返回 **0 行**。
- `index_member_all`：**单次上限 3000 行**，必须 `limit/offset` 翻页；**2 页拿全 5897 只**，
  每只恰好 1 个 L1/L2/L3，`out_date` 为空即当前有效。默认只给**当前**归属；
  历史要走 `index_member(index_code=...)`（带 in_date/out_date，同票可多段），31 个 L1 各拉一次可拼全历史。
- **覆盖率 100%**：本地 20260724 全市场 5526 只全部有申万归属；主板+创业板 4587 只也是 100%。
- `sw_daily` 可用（最早 20130104，按 `trade_date` 一次 439 行），但裁定 2 之后不是必需品 → **本版不落**（§3.2）。
  ⚠ 若将来要回补：历史日会混入 2014 版指数码（20200102 返回 587 行 vs 现在 439 行），必须用 SW2021 分类表过滤。
- **本地行情无缺日**：daily / daily_basic / adj_factor / index_daily / limit_derived / moneyflow_dc
  各 **1589 个日分区**，20200102→20260724，对交易日历 1589 个开市日一天不差。
- **`moneyflow_dc` 覆盖完整**：当日 5749 只 > daily 5526 只，缺的 236 只全是北交所 920.BJ
  （K9 第一层本就排除），`net_amount` 无空值。**形态 4 数据零缺口。**
- **本地 `suspend_d` 只有 5 天 parquet**，标定要跑历史须先回补（一天一次调用）。

### 4.5 申万二级在 K9 池子里的规模分布（主板+创业板 4587 只，落在 131 个二级行业）

min=2 / p10=7 / **中位 23** / p90=76 / max=236。最小成员数门槛的**代价表**（供标定选值）：

| 门槛 | 不参与排名的行业 | 涉及票 | 占池 |
|---|---|---|---|
| <3 | 4 个 | 8 只 | 0.2% |
| <5 | 7 个 | 19 只 | 0.4% |
| <8 | 16 个 | 76 只 | 1.7% |
| <10 | 23 个 | 137 只 | 3.0% |
| <15 | 41 个 | 353 只 | 7.7% |

最小的几个：旅游零售Ⅱ 2 只、医疗美容 2 只、体育Ⅱ 2 只、农业综合Ⅱ 2 只、其他家电Ⅱ 3 只。

### 4.6 停牌过滤的准确实现（⛔ 别写成「过滤 suspend_d 全部行」）

`suspend_d` 返回两类记录：

- `suspend_type='S'` = **停牌**。这类票**天然不在 `daily` 分区里**（20260724 的 5 只、20230103 的 73 只，无一进 daily）
  → 按 `daily` 算中位数时停牌票**自动已剔除**。
- `suspend_type='R'` = **复牌**。这类票当天**正常交易**（20230103 的 000045.SZ 还涨停 +10.01%、成交 14639 手）。

⛔ **过滤只能认 S，认 R 会误杀真实交易日。**
`suspend_d` 在这里的角色是**校验**（S 类若意外出现在 daily 要报警），不是主过滤器。
样本只有两天 → **写成断言 + 告警，不要写成假设**（§6.3 有具体做法）。

### 4.7 量比

`daily_basic.volume_ratio` 与「全天成交量 ÷ 前 5 个交易日均量」实测**完全一致**
（20260724 全市场 5518 只，相关系数 0.99997，最大绝对差 0.005 = 2 位小数四舍五入半步）。
它就是 K9 §3.5 要的**盘后口径**，直接用。

⚠ **但它只有 2 位小数，做「量比排名」会大量并列** → **要排名必须自己用 `vol / vol_ma5` 算**。

### 4.8 白酒排除

申万二级「白酒Ⅱ」= **`801125.SI`**，池内正好 19 只，是一整个二级行业。
排除后该行业当日不产生中位数，下游按「查无该行业」处理。
同 L1 下的「非白酒」是另一个二级（16 只），**不受影响**。
⚠ 一律按**代码** `801125.SI` 识别，⛔ 不按名称字符串（名称会变）。

---

## 5. 当前 Plan · 目标设计

### 5.1 先办：权威文件的措辞修订（⛔ 开工写四通道之前必须完成）

裁定 1、裁定 2、裁定 10 **改动了权威文件原文**。不改，后来者照文件施工就会做回错的口径
（甚至会把已经批准的 10:00 结算拍当成越界功能删掉）。
修订对象在 `/Users/linotsai/Lino/whynotme/`（改文档，不是做研究；不违反「生产不 import whynotme」）。
下表行号是 2026-08-20 的实测位置，⛔ 改之前先按内容确认，别盲改行号。

**A. `K9.md` —— 行业口径（裁定 2）**

| 位置 | 现文 | 改为 |
|---|---|---|
| §3.0 L70 | `相对强度 = 个股当日涨跌幅 − 所属申万行业指数当日涨跌幅` | **`相对强度 = 个股当日涨跌幅 − 所属申万二级行业当日全部成员涨跌幅的中位数（剔除当日停牌成员）`** |
| §3.0 L72 | `基准固定为申万行业分类` | **`基准固定为申万 2021 版二级分类（SW2021 L2）`**，并加一句 **⛔ 不使用申万行业指数涨跌幅，不使用概念板块** |
| §3.3 L118 | `行业指数本身已含市场环境` | **`行业强度（成员中位数）本身已含市场环境`** |
| §四 L175 | `所属申万行业当日强度排名` | **`所属申万二级行业当日强度（成员涨跌幅中位数）排名`** |

**B. `K9.md` —— 上方空间 → 上方机械空间 / 第一压力位（裁定 1）**

| 位置 | 现文 | 改为 |
|---|---|---|
| §3.2 L101 强度性表 | `上方空间（收盘价距第一压力位）` | **`上方机械空间（收盘价距过去 N 日最高价，N 待标定）`**，正向打分 |
| §3.2 L103 正文 | `由**上方空间**和**相对强度**两项回答` | 同步改成 **上方机械空间** |
| §3.4 L127 强度性表 | `距关键位置（前期平台 / 压力位）的远近` | **`上方机械空间的反向打分（同 §3.2 的量，空间小得分高）`** |
| §3.7 L163 举例 | `「上方空间」在形态 1 指向上涨目标，在形态 2 指向的是跌下来之前的位置` | 改写成明确区分 **上方机械空间（排序，机械，K9 第三层）** 与 **第一压力位（预案，LLM，K9 第四层）** |

**C. `K9.md` §九 待标定表 —— 补三项**

新增 **上方机械空间的 N 日**、**行业中位数的最小成员数**、**形态内各强度项的合成权重（4 组）**。

**D. `Neckline新架构_20260818.md`**

| 位置 | 现文 | 改为 |
|---|---|---|
| §二 架构图 L67 | `申万行业指数强度` | **`申万二级行业强度（成员中位数）`** |
| §3.1 L139 | `**申万行业指数强度**` | **`申万二级行业强度（当日成员涨跌幅中位数，剔除当日停牌成员）`** |
| §3.1 L141 | `其中**申万行业指数强度**是核心产物` | 同上替换 |
| §3.1 L143 | `个股涨跌幅 − 所属申万行业涨跌幅` | **`个股涨跌幅 − 所属申万二级行业成员涨跌幅中位数`** |
| §5.1 L288 | `该票所属申万行业同期表现` | **`该票所属申万二级行业同期表现`** |
| §十 L372 | `申万行业强度` | **`申万二级行业强度（成员中位数）`** |
| §七 第 2 条 | `策略并行运行` | 加注 **本版（V2.5.0）不实现；K8 停用留档，切换一刀切（裁定 8）** |
| §四 边界段 | `核对表发出后本次任务结束。系统不持续观察 9:30 以后的价格，不推送盘中提醒，不跟踪持仓。` | 末尾**补一句明文例外**：`⚠ 例外：D1 10:00 的一次性结算读数不在此禁令内 —— 它零 LLM、零推送、不跟踪持仓，唯一用途是为 §5.1 要求的「D1 竞价与开盘 30 分钟读数」结算三分支（裁定 10）。「不持续观察」禁的是盘中提醒与持仓跟踪，不是一次性结算。` |
| §九 | `阅读行为记录` | 标注 **已关闭：不做（裁定 9）。系统只对清单负责** |

**E. 第五组追加的四处（裁定 16 一处 + 裁定 10 的落实三处）**

⚠ 下表**前三行不是新裁定**，是 §5.1 原 17 处清单**漏掉**的两句活坑加一处配套：
K9 §七 与架构 §四 的「输出」段仍写着 9:26–9:29 输出「哪几只已触发成立」，与裁定 10
（9:29 结构上不许出现「成立」、三分支唯一权威是 10:00 结算拍）**直接冲突**。
按裁定 10 修订，⛔ 不另编号。

| 位置 | 现文 | 改为 |
|---|---|---|
| `K9.md` §七 | `输出一张核对表：哪几只已触发成立、哪几只已触发放弃、其余待开盘后观察。` | **两段** `哪几只已触发放弃、其余待开盘后观察`，并补两段说明：⛔ 9:29 不出「成立」的**结构性**原因（四个成立分支都含「前 30 分钟」合取项）+ **三分支终值由 D1 10:00 一次性结算读数产出**、9:29 的「放弃」先到先定不改判 |
| `Neckline新架构_20260818.md` §四「输出」 | `哪几只已触发成立、哪几只已触发放弃、其余待开盘后观察。` | 同上（两段 + ⛔ 无「成立」的原因 + 指向 10:00 结算拍） |
| `K9.md` §八 路径图 / `Neckline新架构_20260818.md` §二 次日时间表 与 §5.1「成立率」行 | 路径图只写 `D1 竞价 + 开盘 30 分钟 → 三分支之一`；时间表只有 9:26–9:29 与 9:30–10:00 两行 | 路径图与时间表**显式画出两拍**（9:26–9:29 只判放弃 → 10:00–10:05 结算出三分支终值），§5.1「成立率」行注明终值出自 10:00 那一拍、⛔ 不取 9:29 |
| `K9.md` §五-6（**裁定 16**） | `分档放宽：定义性条件中带数字的项设两档；严格档不足 10 只时自动切换放宽档` | 行末补 **`⚠ 唯一例外：放量倍数分界值 V ⛔ 不分档（裁定 16）`**，表下补两条理由（穷尽整个空间 → 放宽不增召回；两档会让中间票同时命中 p1 放宽与 p3 严格，破坏裁定 15 的互斥）与「⛔ 其余带数字的定义性项照旧分两档」的界限 |

**验收**（在 `/Users/linotsai/Lino/whynotme/` 下跑）：

```bash
grep -n "上方空间" K9.md                                  # 期望：零命中（只剩「上方机械空间」）
grep -n "行业指数" K9.md Neckline新架构_20260818.md        # 期望：只剩明确写着「不使用」的那一句
grep -n "申万行业" K9.md Neckline新架构_20260818.md        # 期望：零命中（全部已带「二级」）
grep -n "一次性结算读数" Neckline新架构_20260818.md         # 期望：§四 边界段命中 1 处（裁定 10 的明文例外）
grep -n "已触发成立" K9.md Neckline新架构_20260818.md      # 期望：零命中（§5.1-E，裁定 10 的落实）
grep -n "不分档" K9.md                                    # 期望：§五-6 命中（裁定 16）
```

### 5.2 四条边界怎么被**结构性**保证（不是靠注释提醒）

架构 §二给了四条边界。每一条都必须有机器判据。本仓已有 AST 扫描守门单测先例
（`tests/test_v240_p0_retirement_guard.py`、`test_v21_retirement_guard.py`），沿用同一体例。

| 边界 | 结构性保证 |
|---|---|
| **①事实层不知道下游有哪些策略** | ① AST：`neckline/facts/**` 不得 import `neckline.k9` / `explain` / `playbook` / `scorecard`（`direction_llm.py` 也不例外）。② 字段名黑名单：`FactPack` 的列名不得含 `pattern` / `channel` / `recall` / `k9` / `rank` / `score` 词根。 |
| **②召回通道之间互不知道** | ① AST：`k9/channels/pN_*.py` 不得 import 任何其它 `channels/*`，不得 import `ranking` / `quota` / `run`。② 签名固定 `run(pack_range, params) -> list[ChannelHit]`，通道拿不到别人的产物。③ `k9/run.py` 是唯一同时看见四个产物的地方。 |
| **③解释层不知道票是哪个通道选出来的** | ① `ExplainInput` 是独立 DTO，**字段集冻结成常量列表**并被单测逐字断言（加字段必须先改那个列表 = 一次自觉行为）。② AST：`neckline/explain/**` **零 import** `neckline.k9`。③ **排序位次也会从列表顺序泄漏** —— 交给解释层的序列必须按 `ts_code` 升序排序，单测断言构造函数确实排了序、且不透传任何排序键。 |
| **④预案层知道形态，只定条件** | ① `PlaybookInput` 字段集同样冻结断言：**含** `patterns`（骨架需要），**不含** `rank` / `score` / `seat_kind` / `tier` / `upside_room_mech*`。② LLM 返回体 schema 只允许数值与价位键，出现任何自由文本评价键 → 校验拒绝。③ 单测断言 `PlaybookFill` 没有 free-text 评价字段。 |

⚠ 关于 ④ 里排除 `upside_room_mech`：裁定 1 的全部意义就是把两个量分开。
把排序用的机械空间喂给预案 LLM，等于邀请它把那个数原样吐回来当「第一压力位」，
循环依赖当场复活。**这是工程决定，已定死。**

### 5.3 架构第一层 · 事实层

**职责**：只回答「今天市场发生了什么」。**只装一天的事实，不装任何窗口量。**
（窗口长度全是策略参数，装进事实层就把可调项埋进了不该调的地方 —— 架构 §二判据。）

#### 5.3.1 产物：当日事实包

- **大表** → parquet 日分区 `data/parquet/fact_pack/year=YYYY/YYYYMMDD.parquet`，
  一行一只票，必须在 `market_data.TABLE_FLOAT_COLS` 里**显式声明数值列**（⚠ §12 坑 2）。
  列（定死，~40 列）：

  | 组 | 列 |
  |---|---|
  | 身份 | `ts_code` `name` `board` `list_date` `is_st` `suspend_flag`(none/S/R) |
  | 申万 | `sw_l1_code` `sw_l1_name` `sw_l2_code` `sw_l2_name` `sw_l3_code` |
  | 价量（原始未复权） | `open` `high` `low` `close` `pre_close` `pct_chg` `vol` `amount` `adj_factor` |
  | 当日衍生 | `ret_1d` `amp_1d` `limit_up_price` `limit_down_price` `is_limit_up` `is_limit_down` `is_limit_open` `consec_limit_up_days` |
  | daily_basic | `turnover_rate` `turnover_rate_f` `volume_ratio` `circ_mv` `total_mv` `free_share` |
  | 资金流 | `net_amount` `net_amount_rate` `buy_elg_amount` `buy_lg_amount` |
  | 行业相对 | `sw_l2_median_ret` `rel_strength_1d`(= `ret_1d − sw_l2_median_ret`) |

- **行业事实** → SQLite `sw_industry_daily`：`trade_date, l2_code, l2_name, member_count,
  suspended_excluded, median_ret, computed_at`，PK `(trade_date, l2_code)`。
  **无 rank、无强度日、无持续天数** —— 那些要 `minMembers`/分位，是策略参数（§5.4.2）。
- **市场级读数**（指数、涨停家数分布、涨停簇摘要、连板高度、炸板率、全市场中位涨幅）
  → `fact_packs.market_json`（体量小，直接 JSON）。
- **清单** → SQLite `fact_packs`：
  `pack_id(uuid) PK, trade_date, pack_version, origin('live'|'backfill'), state('frozen'),
   content_fingerprint(sha256), row_count, sources_json（每个上游分区的路径+行数+mtime）,
   market_json, suspend_anomaly_count, frozen_at`，
  **UNIQUE(trade_date, pack_version)**。

#### 5.3.2 冻结只读 + 版本号 —— 怎么落地

1. **类型级保证**：`facts.pack.build(trade_date)` 返回 `CompletePack | IncompletePack`。
   `IncompletePack` **没有 freeze 方法**，`facts.store.freeze_pack()` 的签名只接受 `CompletePack`。
   「数据未到齐 → 不冻结」于是是**类型错误**，不是某个人记得检查的布尔标志。
2. **写序**：先写 parquet 到临时路径 → fsync → 算 sha256 → `os.replace` 就位 → **再**一个短事务插 `fact_packs` 行。
   进程死在中间只会留一个没有清单的孤儿 parquet（不可见，下次覆盖）；反序则会留一个指向空气的清单。
3. **不许覆盖**：用 `INSERT`（不是 `INSERT OR REPLACE`）。同一 `(trade_date, pack_version)` 二次冻结直接抛错。
   口径变了就**发新 `pack_version`**，⛔ 没有静默重写这条路。
4. **只读**：`load_pack()` 返回 `@dataclass(frozen=True)` 的 `FactPack`；`rows` 是每次调用现读 parquet
   的属性，调用方拿到的永远是自己的副本，改不脏别人。
5. **唯一写入口**：AST 守门断言 `write_table_day("fact_pack", ...)` 与 `INSERT INTO fact_packs`
   只出现在 `neckline/facts/store.py`。
6. **版本记账**：`k9_runs` 记 `pack_id` + `pack_version` + `params_package_version`。
   成绩单永远记得自己跑在哪版事实包 + 哪版参数上（架构 §3.1）。

#### 5.3.3 完整性判定（「今天没跑成」的第一个来源）

`facts/completeness.py` 逐项检查当日必备输入：
`daily` / `daily_basic` / `adj_factor` / `moneyflow_dc` / `limit_derived` / `suspend_d` 的当日分区存在且行数在合理区间、
`stock_basic` 与 `sw_industry_member` 非空且刷新时间不落后于 N 天、交易日历判定通过。
任一缺失 → `IncompletePack(missing=[...])` → 报告「今天没跑成」并**逐条列出缺口**，保留上一份冻结结果。

#### 5.3.4 停牌：断言而不是假设

- 中位数在 `daily` 现有行上算 → S 类天然已剔除（§4.6）。
- **断言**：`count(suspend_flag=='S' 且出现在 daily) == 0`。违反 → WARNING + 那些行**排除出中位数** +
  `fact_packs.suspend_anomaly_count` 记数（不静默、不掩盖）。
- ⛔ **R 类一律不过滤。**
- 单测夹具双向锁：① 人造一条 S 行混进 daily → 断言被排除且计数 = 1；
  ② 人造一条 R 行涨停 +10% → 断言**计入**中位数。

#### 5.3.5 事实包回填（bootstrap）

策略层要读历史包（形态 3 的长窗），上线首日不能等 120 天。
`scripts/bootstrap_fact_packs.py --start --end`：按日重跑 `build + freeze`，`origin='backfill'`。

⚠ **已知语义差，写在明处、⛔ 别写「自动检测并回改历史」的机灵代码**：
回填包用的是**今天的** `stock_basic` / 申万归属快照，不是那天的。
与既有 `industry_strength_store` bootstrap 的分野一模一样。要重置就整段重跑。

生产回填量：`MAX_LOOKBACK_PACKS + 余量`（建议 150 个交易日）。必须实测墙钟与峰值内存后再上云（§12 坑 1）。

#### 5.3.6 事实层的 LLM 方向解读（旁路）

`facts/direction_llm.py`：在机械读数之上回答「今天在发酵的是哪两三个方向，理由是什么」。
**不参与筛选、不参与排序、不影响任何机械决策**（架构 §八）。
结构性保证：`k9/**` 的 AST 守门顺带断言它不 import `facts.direction_llm`，
且 `FactPack` 不含方向字段（方向存 `fact_packs.market_json` 的独立键，由报告层直接读）。
调用失败 → 报告里那一段缺席，⛔ 不影响事实包冻结、不改变三态。

### 5.4 架构第二层 · 策略层（K9 第一~三层）

**性质**：全机械、参数化、确定性。同样的事实包 + 同样的参数包 → 逐字节相同的清单。

#### 5.4.1 零 LLM 与「取数唯一来源是事实包」的结构性保证

1. **AST**：`neckline/k9/**` 不得 import `neckline.llm` / `neckline.search` /
   `httpx` / `openai` / `requests` / `urllib` / `socket`，也不得 import
   `neckline.data.tushare_client` / `neckline.data.market_data`。
   —— 第二组才是「取数唯一来源是事实包」的真牙齿。
2. **运行时**：全链单测里把 `neckline.llm.factory` 的构造函数 monkeypatch 成「一调就抛」，跑完整选股，断言成功。
3. **确定性**：同一份冻结包 + 同一份参数包跑两遍，产物 canonical JSON **逐字节相等**。

#### 5.4.2 策略契约三条的代码化（架构 §3.2）

| 契约 | 代码化 |
|---|---|
| 声明依赖 | `K9Strategy.DECLARED_FIELDS: frozenset[str]`。策略只能通过 `pack.field(name)` 取列，`name ∉ DECLARED_FIELDS` 直接抛。单测再断言四个通道实际触到的列全在声明集里。`load_pack_range` 按声明集做列投影（顺便省内存）。 |
| 产出署名清单 | 产物 `Shortlist(strategy='K9', params_version=..., pack_version=..., entries=[Entry(ts_code, patterns=[...], primary_pattern, tier, seat_kind, rank, scores)])`。每只票标明由哪个通道召回。 |
| 取数唯一来源是事实包 | 见上条 AST；`load_pack_range(start, end)` 硬断言 `end <= trade_date`（读取范围截止当日）与 `end - start <= MAX_LOOKBACK_PACKS`。 |

#### 5.4.3 参数配置包的格式与校验

路径：`Backend/config/k9-params.<version>.json`。CLI/`systemd` 显式传 `--k9-params <path>`。
**⛔ 无默认路径、⛔ 无内嵌默认值、⛔ 无「暂用某值」。**

```jsonc
{
  "packageVersion": "k9-params-<id>",     // 必填
  "factPackVersion": "fp-1",              // 必须等于事实层当前 PACK_VERSION，否则配置无效
  "calibratedBy": "whynotme <run id>",    // 必填
  "calibratedAt": "...", "approvedBy": "...", "approvedAt": "...",   // 必填
  "boundary":  { /* K9 §二 9 条排除项的每一个数 */ },
  "industry":  { "minMembers": ..., "excludedL2Codes": ["801125.SI"],
                 "heatAbsentPolicy": "renormalize|zero|drop" },   // 取值待标定，三种都要实现
  "volume":    { "maDays": ...,                 // 放量倍数的分母窗口（K9 §3.0.1 原文 20 日）
                 "eruptionMultiple": ... },     // 裁定 15 的 V：p1 ≥ V、p3 < V，**同一个值、不分档**
  "channels":  { "p1": {...}, "p2": {...}, "p3": {...}, "p4": {...} },   // 每档含 strict / relaxed 两组
  "ranking":   { "weights": {"industryHeat":..., "patternStrength":..., "relay":...},
                 "patternSubWeights": {"p1": {...}, "p2": {...}, "p3": {...}, "p4": {...}},
                 "relayLookbackDays": ...,
                 "relaySource": "recalled|shortlisted", "relayScoring": "binary|count",  // 取值待标定
                 "upsideRoomMechDays": ... },
  "quota":     { "min": 10, "max": 20, "floorPerChannel": 1,
                 "overStrictConsecutiveDays": ... },
  "explain":   { "maxBackfillRounds": ... }
}
```

校验（`k9/params.py::load`）：

1. **逐字段必填**：一张显式嵌套的 `REQUIRED_SCHEMA`；缺键 = 错误，⛔ 永不取默认。
2. **类型 / 区间**：正整数、权重和、窗口 ≤ `MAX_LOOKBACK_PACKS`、`excludedL2Codes` 里每个码
   在 `sw_industry_classify` 存在（`801125.SI` 还要额外核名字叫「白酒Ⅱ」，不符只告警不阻断）。
3. **口径指纹**：`factPackVersion` 必须等于事实层常量，否则无效。
4. **结构性无默认值**：`K9Params` 是 `@dataclass(frozen=True)`，**每个字段都没有 default**。
   单测遍历 `dataclasses.fields(K9Params)` 断言 `f.default is MISSING and f.default_factory is MISSING`。
   —— 少一个值就**构造不出对象**，不是靠 if 判断。测试夹具必须显式提供每一个值。
5. 失败 → `ParamsUnavailable(missing=[...], invalid=[...])` → 三态里的 **`not_run`（今天没跑成）**，
   报告说明缺口，**保留上一份冻结结果**。⛔ 绝不降级成「今天没有」。

#### 5.4.4 K9 第一层 · 硬边界（`k9/boundary.py`）

9 条排除项逐条实现，每条产出一个具名 `exclusion_reason`（覆盖率归因要用）：

| # | 排除项 | 实现 |
|---|---|---|
| 1 | 科创板 | `data/board.py` → `Board.STAR` |
| 2 | 白酒 | `sw_l2_code ∈ params.industry.excludedL2Codes`（`801125.SI`） |
| 3 | ST / *ST | `namechange` 当日有效名含 ST（复用 `limit_derived._is_st_name` 口径，⛔ 别另写一份） |
| 4 | 北交所 | `Board.BSE` |
| 5 | 次新股 | `trade_date − list_date < params.boundary.newListingDays` |
| 6 | 停牌 | `suspend_flag == 'S'` 或当日无 daily 行（⛔ 不认 R） |
| 7 | 流动性过弱 | `amount` 的 `liquidityWindowDays` 日均值处于全市场后 `liquidityBottomPct` |
| 8 | 当日涨停 | `is_limit_up`（主板 10% / 创业板 20% 一律排除，**不设例外**） |
| 9 | 当日冲高回落 | 当日涨幅 > `spikeFadeRetPct` **且** 当日最高涨幅 − 收盘涨幅 ≥ `spikeFadeGapPct` |

⚠ 消息面排除**不在这里**，在解释层（K9 §二末段）。

#### 5.4.5 K9 第二层 · 四个召回通道

**共同规则（K9 §3.6 / §3.7）**：
- **定义性条件 = 硬门槛**，在通道内直接过滤。
- **强度性条件 ⛔ 一律不设门槛**，全部转成 K9 第三层的打分项（防止连乘导致召回枯竭）。
- **判据是形态私有的**，⛔ 不存在跨形态通用筛选条件。

| 通道 | 定义性（硬门槛，值全部来自参数包） | 强度性（→ 排序打分项） |
|---|---|---|
| **p1 放量启动** | 过去 `p1.ampWindowDays` 天振幅 ≤ `p1.ampMaxPct`；当日涨幅 > `p1.minRetPct`；**放量倍数 ≥ `volume.eruptionMultiple`（裁定 15）** | 放量倍数（同一个共享量，门槛之上比高低）、**上方机械空间（正向）**、当日相对强度 |
| **p2 超跌反弹** | 归一化跌幅（跌幅 ÷ 该板跌停幅度）≥ `p2.normDropMin`（主板与创业板共用同一门槛）；前一日收盘 ≥ `p2.maDays` 日均线；**非一字跌停**（四价全等于跌停价，**零参数**）**且放量倍数 ≥ `p2.minVolMultiple`**（裁定 13） | 跑输行业的幅度 |
| **p3 中等生转强** | 长窗 `p3.longWindow` 日相对强度 ∈ ±`p3.flatBand`（≈0）；短窗 `p3.shortWindow` 日相对强度 > 0 **且**在改善；**放量倍数 < `volume.eruptionMultiple`（裁定 14/15，与 p1 同一个 V）** | 短窗改善幅度、**上方机械空间（反向）** |
| **p4 资金异动** | 单日主力净流入 > 0 且排名前 `p4.dailyInflowRankPct`；`p4.cumDays` 日累计净流入 > 0 且排名前 `p4.cumInflowRankPct`；资金流入排名 − 涨跌幅排名 ≥ `p4.lagRankGap` | 净流入排名、**量比排名（自算 `vol/vol_ma5`，⛔ 不用 2 位小数的 `volume_ratio` 排名）** |

**每个通道跑两档**：`strict` 与 `relaxed`（K9 §五-6），产出 `ChannelHit(ts_code, pattern, tier)`。
两档都跑、每天都记两档数量（K9 §五末段「区分市场今天确实没有 vs 判据卡得过严」）。

**放量倍数**（`k9/volume.py`，**唯一实现**，裁定 15）：
`vol_multiple = 当日 vol ÷ 前 volume.maDays 个交易日的 vol 均值`（⛔ 分母不含当日）。
p1（≥ V）、p2（≥ p2 门槛）、p3（< V）三处**都调它**，⛔ 不许各算一份。
⚠ 与形态 4 的**量比**（÷ **5** 日均量，排名时自算 `vol/vol_ma5`，§4.7）是两个量，⛔ 别混。
守门单测：全仓「当日量 ÷ N 日均量」这段计算只有一处实现；p1 与 p3 的门槛**读同一个键**。

**上方机械空间**（`k9/upside_room.py`，**唯一实现**）：
`upside_room_mech_high = max(high[-N:])`，N = `ranking.upsideRoomMechDays`（待标定）；
`upside_room_mech_pct = (high_N − close) / close`。
两个打分器 `score_room_far()`（p1，正向）/ `score_room_near()`（p3，反向）**都调它**。
守门单测：全仓 `neckline/` 内「N 日最高」这段计算只有一处实现。

#### 5.4.6 K9 第三层 · 排序（`k9/ranking.py`）

`score = w_ih × 行业热度分 + w_ps × 形态内强度分 + w_relay × 跨日接力分`（三项权重待标定）

- **行业热度分（跨形态可比）**：当日 `sw_industry_daily` 里 `member_count ≥ params.industry.minMembers`
  且不在 `excludedL2Codes` 的行业，按 `median_ret` 降序排名 → 归一到 `1 − (rank−1)/(N_ranked−1)`。
  ⚠ 「查无该行业」的票（成员数不足 / 被排除）：按 `params.industry.heatAbsentPolicy` 处理。
  该键是**取值待标定的参数位**（§8.3 #18），⛔ **三种取值必须全部实现**：
  `renormalize`（按剩余两项权重重新归一，不因行业无排名被当成最差行业）/
  `zero`（记 0 分，等同最差行业）/ `drop`（该票不参与本日清单）。
  ⛔ 无默认值、⛔ 代码里不许有「哪个是默认」的分支、⛔ 不许默认当 0 分。
- **形态内强度分（形态内可比）**：该形态每个强度项 → 在**本形态候选集内**取百分位（并列取平均名次）
  → 按 `patternSubWeights[pattern]` 加权求和 ∈ [0,1]。
- **跨日接力分（跨形态可比）**：过去 `relayLookbackDays` 天内被**其它**形态选中过。
  「选中」的口径与打分形状是**取值待标定的参数位**（§8.3 #19、#20），
  ⛔ **四种组合必须全部实现**：`relaySource` ∈ {`recalled`（被通道召回）, `shortlisted`（进入过清单）}
  × `relayScoring` ∈ {`binary`（有/无二值）, `count`（计次）}。⛔ 无默认值。
  ⚠ 观察一条（供标定判断，**不是预设**）：K9 §四 原文「被其他形态**选中**过」字面上更接近
  「被通道召回」而非「进入清单」，但 K9 没有明确 → 仍作参数位，⛔ 施工侧不预设。
  数据来自 `k9_channel_hits`（append-only，每次运行落全部召回与入选记录）。
- **当日方向 ⛔ 不参与排序**（K9 §四末段），只作报告背景。
- **一只票命中多个形态**：形态内强度分取各命中形态的 **max**，`primary_pattern` = argmax，
  `patterns` 列全部。这是 K9 §五-4「命中多个形态**不加分**」的最保守读法（max 不会超过任何单形态得分）。
- **决定性排序键**：`(score desc, 行业热度分 desc, ts_code asc)` —— 保证逐字节可复现。

#### 5.4.7 K9 第五节 · 名额分配（`k9/quota.py`）

1. 四通道两档全部跑完，得到候选集；每只票带 `tier = strict`（通过严格档）或 `relaxed`。
2. **档位选择**：`strict` 去重后只数 ≥ `quota.min`(10) → 只从 strict 抽；否则用 `strict ∪ relaxed`。
   两种情况下每只票都带自己的 `tier` 标签 → 天然满足 §五-7「成色标注」。
3. **保底席位**：当日有候选的每个形态各先占 1 席。分配次序 = 各形态**最佳候选分数降序**（并列按 p1<p2<p3<p4 定序）
   —— 固定按 p1..p4 会给 p1 系统性优势，这里避免掉，且仍完全确定性。
   若某形态的最佳候选已被别的形态占了席位，取该形态次优（K9 §五-4：一票一席）。
4. **自由竞争**：剩余席位按总分统一分配，不限形态，填到 `quota.max`(20)。
5. **诚实缺席**：某形态当日无候选 → 报告标「今日无此形态」，⛔ 不放宽标准去凑。
6. **容量不足**：放宽档后仍 < `quota.min` → **如实出这么多**，报告显式披露 `capacity_short`。
   ⛔ 不制造候选。
7. **过严提示**：连续 `quota.overStrictConsecutiveDays` 天靠放宽档凑足 → 报告打「判据过严，建议重标」提示。
8. **逐日记录**：每个形态 strict 数 / relaxed 数 / 入选数，落 `k9_runs.channel_counts_json`。

#### 5.4.8 全市场 disposition（覆盖率归因的原料）

每次运行落一份 `data/parquet/k9_disposition/year=YYYY/YYYYMMDD.parquet`：
一行一只票 —— `ts_code, excluded_by(9 条中的哪条 | null), recalled_patterns_json, tier,
score, rank, seated(0/1), seat_kind(floor|free|null), news_excluded(0/1)`。
5500 行/天，小；它让「昨天为什么没选中这只涨停票」变成一次查表而不是一次考古。

### 5.5 架构第三层 · 解释层（`neckline/explain/`）

- **输入**：`ExplainInput`（字段集冻结，见 §5.2 边界③），按 `ts_code` 升序。
- **输出**：每只票的资料聚合 —— 公司是什么、当前消息面、行业里的处境、位置与结构状态、近期表现、**日K 形态评价**。
- **消息面排除也在这一层**：爆雷 / 减持 / 立案 / 监管 → 剔除。检索走 **Tavily**（`search/tavily.py`），
  ⛔ 不用 Provider 自带联网（V2.4.2 已收口的教训）。
- **后备票补位**：清单定稿流程 =
  `策略层出 seated + reserve → 解释层处理 seated → 每剔除一只，从 reserve 取下一名再跑解释层 →
   最多 params.explain.maxBackfillRounds 轮 → 定稿`。
  补位决定由**编排器**（知道排名）做，解释层自己不知道 —— 双盲不破。
- 每次剔除与补位都写进运行审计（谁被剔、为什么、谁补上）。
- **清单是在解释层之后定稿的**：`k9_listing_entries` 在这一步落库。

### 5.6 架构第四层 · 预案层（`neckline/playbook/`）

#### 5.6.1 三个价位（K9 §6.1，D0 冻结）

`first_resistance`（第一压力位 = 预期离场价，判断对错的标准）、`second_resistance`、`invalidation`（失效位）。
赔率 = `(first_resistance − close) / (close − invalidation)`，无需额外计算即可得出。

⛔ 命名铁律（裁定 1）：这三个是 **LLM 产物**，与 `upside_room_mech*`（机械、排序用）**永不互相顶替**。

#### 5.6.2 条件骨架（机械，K9 §6.3）

四个骨架按形态套用，方括号数值由 LLM 填：

```
p1 放量启动   成立：高开幅度 ≤ [A]%  且  前 30 分钟最低价 ≥ [B]
              放弃：跌破 [C]（昨日启动的起点）        其余：观察
p2 超跌反弹   成立：开盘价 ≥ [A]  且  前 30 分钟不创昨日新低
              放弃：跌破昨日最低价 [B]%              其余：观察
p3 中等生转强 成立：前 30 分钟不破 [A]                放弃：跌破 [B]     其余：观察
p4 资金异动   同 p3（埋伏型）
```

#### 5.6.3 结构化可机械求值（架构 §3.4 硬约束）

```
MetricRef  ∈ 闭合枚举 { auction_price, auction_gap_pct, open_price, gap_pct,
                        first30_low, first30_high, prev_close, prev_low, prev_high }
Condition  = { op: "<=|>=|<|>", lhs: MetricRef, rhs: number | MetricRef }
Branch     = { name: "成立"|"放弃", all: [Condition, ...] }
Playbook   = { ts_code, pattern, levels{first_resistance, second_resistance, invalidation},
               branches: [成立, 放弃], default: "观察", filled_by, filled_at }
```

**闭合枚举 = 求值器是全函数**：未知 MetricRef → D0 当场判 playbook 无效并重试/降级，
⛔ 绝不让一个次日早上求不出值的条件被冻结进去。
`playbook/evaluate.py` 是**唯一**求值实现，次日核对与 D1 结算共用它（⛔ 不许各写一份）。

#### 5.6.4 分工

条件骨架 = 机械；具体数值 = LLM 逐票；最终确认 = 用户盘后逐只过目可修改
（App 侧提供修改入口，改动写 append-only 新版本，⛔ 不覆盖原冻结版本）。

### 5.7 次日核对与 D1 结算（`neckline/auction/`）

#### 5.7.1 9:26—9:29 竞价核对表（K9 §七 / 架构 §四）

- 复用 `auction/pipeline.py` 的窗口纪律：**交易日 且 09:26:00 ≤ t < 09:29:00**；当日只跑一次
  （`neckline/dedup.py`，市场级 key）；**窗口外调用零落库**；**⛔ 事后不许补跑**。
- 冻结竞价结果（`auction/collect.py` + `quality.py` 报价校验）。
- **零 LLM，纯条件求值**（架构 §四）。`auction/llm.py` **整个删除**；
  `auction/mech.py`（1651 行，K8 的 Z1/Y1/C1 语义）**整体重写**为 `auction/checklist.py`。
- 9:29 硬截止的**守法**简化：原来的 daemon 线程 + 结果盒子是为了兜住 LLM 的不确定墙钟；
  零 LLM 后求值是毫秒级 → 保留窗口门 + 防重 + 不补跑，**删掉 daemon 线程机制**，
  改成一句朴素的墙钟保护（未在 9:29 前完成则记「未完成」，⛔ 不迟到发布）。
- **输出只有两段：哪几只「已触发放弃」、其余「待开盘后观察」。**
  🔴 **⛔ 9:29 一律不许输出「成立」**（裁定 10）—— 见 §5.7.2 的原因。
  结构性保证：`checklist.py` 的返回类型里根本**没有「成立」这个取值**
  （`ChecklistVerdict` 是二值枚举 `{rejected, pending_open}`），不是靠谁记得别写。
  发出后本次任务结束。

#### 5.7.2 为什么 9:29 判不出「成立」，以及 10:00 结算拍（**裁定 10，已批准**）

把 K9 §6.3 的四个骨架逐条代入 9:26–9:29 可观测的量：

- 四个「成立」分支**全部含有「前 30 分钟」这一合取项**（p1 的 `前30分钟最低价 ≥ [B]`、
  p2 的 `前30分钟不创昨日新低`、p3/p4 的 `前30分钟不破 [A]`）；
- 9:29 时前 30 分钟还没发生 → **「成立」在竞价核对表里结构上永远判不出来**；
- 「放弃」分支四个全是单条破位判定 → 竞价价就能触发。

若不处理，核对表天天是「0 成立 / k 放弃 / 其余观察」，
**K9 §八的第一个指标「成立率」结构性恒为 0**，兑现率与错杀率跟着一起报废。

**处理方式（裁定 10 已批准）：`auction/settle.py` 的 10:00 结算拍**

- **窗口**：D1 **10:00–10:05**，**一次性**快照（`data/realtime.py` 的 `Quote` 自带当日
  high/low，10:00 时的 high/low 即前 30 分钟极值，含 9:25 竞价成交）。
- **性质**：**零 LLM、零推送、不进 App 首屏**（它是**结算**，不是提醒）。
- **求值**：代入同一个 `playbook/evaluate.py`（⛔ 唯一求值器，不许再写一份），
  产出三分支终值 `成立 | 放弃 | 观察`。
- **纪律与 9:26 那一拍完全一致**：交易日门、窗口外零落库、当日防重、**⛔ 事后不许补跑**
  （补跑会拿 10:30 的价格冒充 10:00 那一刻）。
- **落库**：`k9_d1_verdicts.verdict` + `decided_stage='open30'`。
  9:29 已判「放弃」的票**不改判**（`decided_stage='auction'` 先到先定，
  幂等 `WHERE decided_stage IS NULL` 保证）。
- **权威归属**：🔴 **三分支判定的唯一权威是这一拍。** 9:29 那张表只是提前告知
  「哪几只已经死了」，⛔ 它不产生「成立」，也不产生成绩单口径的任何终值。

**为什么这不违反架构 §四**（依据写在这里，防止后来者当越界功能删掉）：

1. 架构 §5.1 本来就要求冻结「D0 预案条件、**D1 竞价与开盘 30 分钟读数**、三分支判定结果」；
2. K9 §八 把三分支定义在「**D1 竞价 + 开盘 30 分钟**」这个窗口上；
3. 架构 §四 那句「不**持续**观察 9:30 以后的价格」管的是**推送盘中提醒**与**跟踪持仓**——
   一次性结算读数不落在该禁令内。
4. ⚠ 尽管如此，架构 §四 的措辞**仍必须补一句明文例外**（§5.1-D 已列为必改项），
   否则文件与实现对不上。

#### 5.7.3 常驻进程的早晨循环

`api/app.py::_sentinel_loop` 更名 `_morning_loop`，盘中四哨兵分支全部删除，只剩两拍：

| 拍 | 窗口 | 推送 | 产物 |
|---|---|---|---|
| 竞价核对表 | 9:26–9:29 | **有**（APNs） | `已触发放弃 / 待开盘后观察` 两段，⛔ 无「成立」 |
| **结算拍**（裁定 10） | 10:00–10:05 | **无** | 三分支终值 → `k9_d1_verdicts` |

非窗口时段 5 分钟一探，不空转；两拍各自独立 `try/except`，一拍炸了不影响另一拍。
🔴 **⛔ 不新增 systemd unit** —— 两拍都跑在既有常驻 `neckline.service` 里（同 9:26 竞价拍
的现行做法）。理由：它们各自是秒级的进程内 tick，多一个 unit 就多一个触发面和一条双跑路径，
而防重台账是按「当日一次」记的，双触发会把「跑没跑过」变成一道要现场推理的题。

### 5.8 三条成绩线（`neckline/scorecard/`，**分开存放，互不进对方的分子分母**）

#### 5.8.1 覆盖率（先跑起来当尺子 —— 架构 §十把它列进「可直接开工」）

- **口径 = 涨停**（K9 §5.2），涨停来自 `data/limit_derived.py` 自算，**不依赖任何待标定数字**。
- `coverage_all`（**头条数字**）：分母 = 当日全部涨停票；分子 = 其中出现在**昨天清单**里的只数。
- `coverage_in_pool`（辅助）：分母限定为 D−1 未被硬边界排除的涨停票。⚠ 它依赖边界参数 → 参数缺失时写 NULL。
- **漏检归因**：每只没被覆盖的涨停票，从 `k9_disposition` 直接读出原因
  —— 被第 N 条边界排除 / 四通道都没召回 / 召回了但排名第 X 没进席 / 被消息面剔除。
- **观察分支仍进覆盖率**（K9 §八）：覆盖率只看「昨天在不在清单里」，与三分支判定无关。
- **参数没到齐也能跑的那半**：涨停普查 + 边界归因 + 涨停簇画像**从第一天就出数**；
  「命中昨日清单」那一项在清单开始产出的次日自动接上。
- ⛔ 不回填历史覆盖率（上线前没有清单，编不出来）。

#### 5.8.2 清单成绩（五个指标，K9 §八）

| 指标 | 定义 | 需要冻结的数据 |
|---|---|---|
| 成立率 | 清单中触发「成立」的比例 | D0 预案条件、D1 竞价 + 开盘 30 分钟读数、三分支判定 |
| 兑现率 | 成立的票里 D+1~D+4 摸到第一压力位的比例 | D0 `first_resistance`、D+1~D+4 行情回填 |
| 错杀率 | 放弃的票里后来仍摸到第一压力位的比例 | 同上（放弃分支同样回填） |
| 行业分 | 该票所属申万二级行业同期表现 | 写入时冻结的 `sw_l2_code` |
| 选票分 | 该票同期表现 − 所属行业同期表现 | 同上 |

铁律：
- 🔴 **三分支的唯一权威是 10:00 结算拍**（裁定 10）。成立率 / 兑现率 / 错杀率一律读
  `k9_d1_verdicts` 里 `decided_stage='open30'` 的终值，或 `decided_stage='auction'` 的
  「放弃」终值。⛔ 不许把 9:29 的「待开盘后观察」当成任何一个分支的结论。
- **行业分与选票分必须分开存**，两列，⛔ **不给任何合计字段**
  （同 `review/cashflow.py` 刻意不给「账户净变动」的先例）。守门单测断言 scorecard 存储层
  没有 `total` / `combined` 一类字段，也没有把两者相加的代码路径。
- **票与行业的从属关系在写入时即冻结**（架构 §5.1）——`k9_listing_entries` 落库时把
  `sw_l2_code`/`sw_l2_name` 一并写死，事后申万调整不回改。
- **观察分支不进任何正确率的分子分母**。单测夹具：全是观察的一天 → 三个比率返回 `None`，⛔ 不是 0。

表：`k9_listing_entries`（D0 清单 + 冻结行业绑定）、`k9_playbooks`（D0 预案，append-only 版本化）、
`k9_d1_verdicts`（三分支 + 两阶段读数）、`k9_followups`（D+1..D+4 逐日回填）、
`k9_scorecard_daily`（滚动汇总）。

#### 5.8.3 我的成绩

来源交割单，走周末复盘线（§5.9），与前两条**完全隔离**。

### 5.9 交割单分析台（架构 §六，`neckline/review/`）

**每周末一次。⛔ 这一层无 LLM 调用**（架构 §六明令）。

系统承担三件事：
1. **交割单解析** —— `review/parse.py` 原样留用（两家券商 schema 已逐字段核实过）。
2. **行情材料装订** —— 新 `review/bindery.py`：每笔交易前后的 K 线 + 买卖点标注 +
   同期大盘 + 同期所属申万二级表现 + 当时那几天的报告与预案快照。
3. **结论存档** —— 新 `review/conclusions.py`：保存本周复盘结论，下周可检索。

保留：`parse.py`、`reconcile.py` 的 **FIFO 回合闭合 + `WeeklyStats`**、`cashflow.py` 四分类、
`material.py`、`store.py`。
删除：`reconcile.py` 里全部 K8 章程判据（`check_plan_and_ledger` / `check_single_cap` /
`check_position_count_and_exposure` / `check_entry_screens` / `check_cooldown` /
`check_time_exit_discipline` / `classify_stop_discipline` / `build_charter_timeline`）
—— 它们绑在持仓与章程语义上，两者都退役了。K9 §六只要解析 / 装订 / 存档。

⚠ 装订好的材料由用户带到聊天框做对话与总结，总结回存 —— 系统不做对话。

### 5.10 报告（架构 §3.5）

- 每天盘后一份，推送到手机，离线可读，十分钟读完。
- **两层视图**：默认视图（方向背景 → 每只票一句话画像 → 关键价位与预案）；
  **结构化完整版默认折叠**，展开可整段复制到聊天框。
- **三种状态，每天必发其一，首行即可分辨**：

  | 状态 | 触发 | 首行 |
  |---|---|---|
  | `has_list` 今天有这些 | 事实包已冻结 + 参数有效 + 清单 ≥1 只 | `今天有这些 · N 只（严格 a / 放宽 b）` |
  | `empty` 今天没有 | 事实包已冻结 + 参数有效 + 清单 0 只 | `今天没有` |
  | `not_run` 今天没跑成 | 事实包未冻结（数据未到齐）/ 参数未配置或无效 / 链路异常 | `今天没跑成 · <缺口逐条>` |

  结构性保证：`ReportState` 是三值枚举，首行由**全映射**渲染（无 fallback 分支）；
  单测断言「参数缺失 → `not_run`」、「清单为空但参数有效 → `empty`」，两者不可互换。
- ⚠ **参数未配置的日子照样发报告**：清单段标「今天没跑成 · 参数未配置」，
  而**方向背景、市场事实、覆盖率成绩线照常呈现**。日节奏不断，尺子照跑。
- **双日期契约不许退化**（LRN-20260816-001）：`report_date` 管标题 / 推送 / 可见身份；
  `trade_date` 管 EOD 读数 / 清单 / 预案 / 审计键。周日报告：`report_date=周日`，
  `trade_date=紧邻上一周五`；该周五休市则安全跳过；同日已人工生成则定时槽整链跳过。
- 表：**新表 `k9_reports`**（沿用双日期契约的列形状）。旧 `reports` 表冻结只读
  —— 它装满了 K8 的 JSON blob，往上加列不如新起一张干净的。

### 5.11 App（`App/Neckline/`）

**三板块 IA：选股 / 成绩 / 复盘 + 设置沉底**（**裁定 11**；替换原「选股 / 持仓 / 复盘」，持仓整块下线）。
iOS 底部 TabView，macOS 50px 工具栏胶囊 —— ⛔ 不要把 240px 玻璃侧栏加回来。

| 板块 | 内容 |
|---|---|
| **选股** | 两个视图：**今日清单**（三态首行 → 方向背景 → 10-20 只，每只带形态标注 / 上方机械空间 / 三个价位 / 三分支预案摘要）与 **次日核对表**（**已触发放弃 / 待开盘后观察 两段**，⛔ **无「成立」段**，并用一行说明「成立由 10:00 结算，9:30–10:00 由我自己判定」）。9:26–15:00 默认落在核对表，其余时间落在清单，用户可切。个股详情 = 解释层资料 + 日K评价 + 完整预案 + 预案修改入口。 |
| **成绩** | 清单成绩五指标（行业分与选票分**分两栏呈现**，⛔ 不合并）+ 覆盖率（含漏检归因列表）。**10:00 结算拍的三分支终值落在这里**（作为成立率的明细），⛔ 不进选股首屏（裁定 10）。 |
| **复盘** | 交割单上传（桌面场景）→ 解析结果 → 装订材料 → 结论存档 → 我的成绩。 |

Swift 侧：
- 删除 `Views/PositionsView.swift`、`Views/PositionExtras.swift`、`Networking/Models/PositionModels.swift`。
- `BasketDailyView` → `SelectionView`；`BasketCardView` → `StockDetailView`；
  `AuctionCardView` → `CheckListView`；新增 `ScoreboardView`；`ReviewView` 重做。
- `BasketModels` → `K9Models`；`AuctionModels` → `CheckListModels`；新增 `ScoreboardModels`。
- 保留 `Components/`（DesignTokens / SharedUI / NKFormKit / NKToolbar / NKDisclosure / NKStopScale / NKGateViews 按需）。
- `MARKETING_VERSION = 2.5.0`，`CURRENT_PROJECT_VERSION = 10`。
- ⚠ 改 SwiftUI View **必须** `xcodebuild` 跑 App target 验证（只跑 SwiftPM 不暴露 View 层问题）。

### 5.12 API 契约（定死的保留 / 删除 / 新增表）

`VERSION = "v2.5.0"`（`api/app.py`）。

**新增**

| 路由 | 内容 |
|---|---|
| `GET /api/selection/latest` | 三态 + reportDate/tradeDate + 方向背景 + 清单 |
| `GET /api/selection/{date}` | 同上，历史 |
| `GET /api/selection/{date}/stock/{code}` | 解释层资料 + 日K评价 + 预案 + 上方机械空间 + 三价位 |
| `POST /api/selection/{date}/stock/{code}/playbook` | 用户修改预案（append-only 新版本） |
| `GET /api/checklist/{date}` | **9:29 竞价核对表**：`已触发放弃 / 待开盘后观察` 两段。⛔ 响应体里**没有「成立」这个取值**（裁定 10） |
| `GET /api/scoreboard/listing?window=` | 五指标（行业分 / 选票分分列） |
| `GET /api/scoreboard/verdicts/{date}` | **10:00 结算拍的三分支终值**（含 `decided_stage`）。挂在 scoreboard 下而不是 checklist 下，是为了让「它属于成绩线、不属于早盘首屏」在路由上就看得出来（裁定 10） |
| `GET /api/scoreboard/coverage?window=` | 覆盖率 + 漏检归因 |
| `GET /api/legacy/k8/baskets?date=` | **K8 只读追溯唯一入口**（裁定 6），只读、无写、无迁移 |

**保留**：`/api/health`、`/api/devices`、`/api/settings/*`（全部）、`/api/review/upload`、
`/api/review`、`/api/review/overview`、`/api/eval/weekly`。

**删除**：`/api/report*`、`/api/baskets*`（→ legacy）、`/api/board`、`/api/positions*`（6 个）、
`/api/decisions*`（3 个）、`/api/clocks/*`（3 个）、`/api/alerts*`（5 个）、`/api/auction`（→ `/checklist`）、
`/api/market-regime`、`/api/packs*`、`/api/profile/*`、`/api/review/handoff`。

⚠ 这是**破坏性 API 版本**：旧客户端会 404。可以接受 —— App 与后端同版发布，且 V2.4.x 已用掉了
上一个兼容周期。发布记录里如实写明。

### 5.13 whynotme 侧的标定接口（Neckline 只写消费契约）

⛔ **本 Plan 不规划 whynotme 的研究工作。** 这里只定 Neckline 的进出口。

**Neckline 导出什么**（`scripts/export_research_snapshot.py` 扩展）：

1. SQLite 一致性快照（既有能力，`sqlite3.backup` + manifest + sha256）——
   含 `sw_industry_classify` / `sw_industry_member` / `sw_industry_daily` / `fact_packs` /
   `k9_*` 全部表。
2. **事实包 parquet 目录**：新增 `--include-fact-packs --start --end`，
   把 `data/parquet/fact_pack/` 指定区间拷进 `artifacts/input/fact_pack/`，附 manifest（逐日 sha256）。
   —— 标定必须跑在与生产**逐字节相同**的事实包上，否则「联合通过率」这个数没有意义。
3. manifest 里写明 `packVersion`、区间、生成时间、Neckline 版本。

**参数包怎么回来**：whynotme 产出 `k9-params.<version>.json`（格式见 §5.4.3），
用户确认后**由用户放入** `Backend/config/`。Neckline 侧的动作只有三件：
校验 → 记 `packageVersion` 进每次运行 → 校验不过就报「今天没跑成 · 参数未配置」。

⛔ Neckline **不自动拉取参数包**、⛔ 不写 whynotme 的任何目录、⛔ 不 import whynotme。

---

## 6. 施工切片

每片给「产出 + 验收」。**批 A 不依赖参数标定，可直接开工；批 B 等参数包回来**（架构 §十）。
切片顺序即建议施工顺序。

### 批 A · 不依赖参数标定

#### S0 · 立项收口与权威文件修订
- 产出：K9.md 与架构共 16 处措辞修订（§5.1 逐处给出现文与改文）；本 PROJECT_PLAN 生效；
  `api/app.py::VERSION = "v2.5.0"`；`project.pbxproj` 的 `MARKETING_VERSION`（**4 处**）改 `2.5.0`、
  `CURRENT_PROJECT_VERSION`（**2 处**）改 `10`。
- **验收**：§5.1 末尾三条 grep 全部达到期望；`/api/health` 本地返回 `v2.5.0`；
  `grep -c "MARKETING_VERSION = 2.5.0"` = 4、`grep -c "CURRENT_PROJECT_VERSION = 10"` = 2。

#### S1 · 退役与搬家（先清地基，再盖房）
- 产出：
  - `sentinel/quotes.py` → `data/realtime.py`；`sentinel/dedup.py` → `neckline/dedup.py`（表名 `sentinel_events` 不变）；
    `strategy/features.py` → `data/panel.py`；`scan/cluster.py` → `facts/limitmap.py`。
  - 物理删除 §4.3 全部对象 + 其测试；删除 `/api` 里 §5.12 列明的路由；
    `_sentinel_loop` 瘦身为 `_morning_loop`（本片先只保留竞价那一拍）。
  - K8 表进「只读留档」名单：`baskets` `basket_members` `basket_cards` `tier_history`
    `gate_evaluations` `out_candidates` `basket_*_handoff` `reports` `industry_strength_daily`
    `positions` `position_plans` `entry_snapshots` `decision_log` `custom_alerts` `selection_packs`
    `strategy_versions` 等 —— **表不删、不迁移、不回填**，应用层删除其写路径。
- **验收**：`pytest -q` 全绿（删掉的测试一并删除，⛔ 零 skip 僵尸）；
  AST 守门单测断言 `neckline/` 内 `import neckline.sentinel` / `neckline.scan` / `neckline.selection` /
  `neckline.strategy` 零命中；`init_schema()` 在临时库上可重复跑；旧表行数不变。

#### S2 · 申万分类接入
- 产出：`data/tushare_client.py` 加 `ts_index_classify(src='SW2021')` 与
  `ts_index_member_all(limit, offset)`（**必须分页**，单次 3000 行上限）；
  `data/sw_industry.py` + 表 `sw_industry_classify`（index_code, name, level, parent_code, src）
  与 `sw_industry_member`（ts_code, l1/l2/l3 code+name, in_date, out_date, is_current, fetched_at）；
  挂进 `scripts/daily_update.py`（日更，尽力而为但**日志用 ERROR**：它是判据输入不是增强项）。
- **验收**：拉全 5897 只（2 页）；L1/L2/L3 = 31/134/346；
  对本地某日全市场做覆盖率断言 = 100%；`801125.SI` 存在且名为「白酒Ⅱ」；
  单测用 MockTransport / 假 client，⛔ 不联网、⛔ 不落工作库。

#### S3 · 事实层与事实包
- 产出：`facts/{pack,store,industry,limitmap,completeness}.py`；表 `fact_packs`、`sw_industry_daily`；
  parquet 表 `fact_pack`（含 `TABLE_FLOAT_COLS` 声明）；`scripts/bootstrap_fact_packs.py`；
  保留策略（滚动 250 交易日）与裁剪脚本。
- **验收**：
  - 类型级：`freeze_pack(IncompletePack)` **类型不通过**（mypy/单测双证）；
  - 冻结不可覆盖：同 `(trade_date, pack_version)` 二次冻结抛错；
  - 中位数三路等价：现算 ≡ 落表 ≡ 读回；
  - 停牌双向夹具（S 混进 daily → 排除且计数 1；R 涨停 → 计入）；
  - 缺一个上游分区 → `IncompletePack` 且 `missing` 列出具体表名；
  - **实测**：单日构建的墙钟与峰值内存、150 日 bootstrap 的墙钟与峰值内存，写进本文件 §14。

#### S4 · 覆盖率成绩线（尺子先跑起来）
- 产出：`scorecard/{coverage,store}.py`；表 `k9_coverage_daily` + 漏检归因；
  `/api/scoreboard/coverage`。清单尚未存在时只出「涨停普查 + 边界归因」。
- **验收**：对本地历史某日跑出涨停家数与归因分布；`coverage_all` 不读任何参数包；
  参数缺失时 `coverage_in_pool` 为 NULL 而不是 0；⛔ 不回填历史覆盖率。

#### S5 · 参数包契约与三态
- 产出：`k9/params.py` + `config/k9-params.example.json`（**示例文件里所有数值位写
  `"__TO_BE_CALIBRATED__"` 字符串**，⛔ 不许放任何真数字）；`ReportState` 三值枚举 + 全映射渲染。
- **验收**：`dataclasses.fields(K9Params)` 每个字段无默认值；
  少任一键 → `ParamsUnavailable` 且 `missing` 精确；窗口 > 120 → invalid；
  `factPackVersion` 不匹配 → invalid；**参数缺失 → 报告 `not_run`，⛔ 不是 `empty`**（单测锁死）；
  上一份冻结结果被保留。

#### S6 · 策略层骨架
- 产出：`k9/{contract,boundary,industry_heat,upside_room,ranking,quota,run,store}.py` +
  `k9/channels/p1..p4`（定义性条件的**结构**齐全，数值位全部从参数包读，参数未配置就跑不起来）；
  表 `k9_runs` `k9_listing_entries` `k9_channel_hits`；parquet `k9_disposition`。
- **验收**：
  - **零 LLM**：AST 三组断言 + monkeypatch「一调即抛」全链跑通；
  - **取数唯一来源**：AST 断言 `k9/**` 不 import tushare_client / market_data；
    未声明字段 `pack.field()` 抛错；
  - **通道互不知道**：AST 断言 channels 之间零 import；
  - **确定性**：同包同参跑两遍 canonical JSON 逐字节相等；
  - **上方机械空间唯一实现**：全仓「N 日最高」只有一处；p1 正向 / p3 反向共用它；
  - **命名分离**：全仓（含 Swift 与 Markdown 模板）`grep "上方空间"` 零命中；
    `k9/**` 内 `first_resistance` 零命中；
  - 名额分配：保底 / 自由 / 一票一席 / 诚实缺席 / 容量不足披露 / 成色标注 各一条夹具；
  - 🔴 **三个「取值待标定」的参数位必须把全部候选取值都实现**（§8.3 #18–#20）：
    `industry.heatAbsentPolicy` 三种、`ranking.relaySource` × `relayScoring` 四种组合，
    每种一条夹具；⛔ 任一取值缺省或未实现 = 本片不通过；⛔ 代码里不许有「哪个是默认」的分支；
  - `k9_disposition` 覆盖全市场每一只票且 `excluded_by` 可解释。

#### S7 · 报告骨架与推送
- 产出：`report/{pipeline,render,store,evening}.py` 重做；表 `k9_reports`；
  两层视图（默认 + 结构化折叠）；APNs；**双日期契约与周日排程原样保留**；
  `scripts/evening.py` 段序重排 → `facts → k9 → explain → playbook → report`。
- **验收**：三态各一份渲染快照，首行可辨；
  参数未配置那份**仍有**方向背景 + 市场事实 + 覆盖率；
  周日报告 `report_date=周日 / trade_date=上一周五`（回归测试保留）；
  该周五休市 → 安全跳过；同日已生成 → 定时槽整链跳过、零重复 APNs。

#### S8 · 次日核对与 D1 结算（含裁定 10 的 10:00 结算拍）
- 产出：
  - `auction/checklist.py`（重写 `mech.py`）——**9:29 拍**，返回类型 `ChecklistVerdict`
    是**二值枚举** `{rejected, pending_open}`，⛔ 结构上没有「成立」这个取值；
  - **`auction/settle.py`（10:00–10:05 结算拍，裁定 10 已批准）** —— 零 LLM、零推送、
    不进 App 首屏；同样的交易日门 / 窗口外零落库 / 当日防重 / ⛔ 事后不补跑；
  - `auction/store.py` 新表 `k9_d1_verdicts`（`verdict` + `decided_stage`）；
  - 删除 `auction/llm.py` 与 `auction/observation.py`；
  - `playbook/evaluate.py` 作为**唯一**求值器（两拍共用）；
  - `_morning_loop` 两拍齐全，各自独立 `try/except`；⛔ 不新增 systemd unit。
- **验收**：
  - 零 LLM（AST + 运行时双证）；两拍各自：窗口外调用零落库、当日防重、⛔ 事后补跑被拒；
  - 9:29 前未完成 → 记「未完成」不迟到发布；求值器对闭合枚举外的 MetricRef 抛错；
  - 🔴 **`ChecklistVerdict` 枚举成员恰好两个**，且 `/api/checklist/{date}` 的响应 schema
    里不存在「成立」取值（守门单测 G20）；
  - 🔴 **结算拍零推送**：跑一次结算后 APNs 调用计数 = 0（守门单测 G21）；
  - D0→D1 端到端夹具：竞价触发放弃 → `decided_stage='auction'`；
    10:00 触发成立 → `decided_stage='open30'`；
    **已在竞价定案的票不被 10:00 改判**（幂等 `WHERE decided_stage IS NULL`）；
    10:00 既不成立也不放弃 → `观察`，且**不进三个比率的分子分母**。

#### S9 · 解释层
- 产出：`explain/{input,aggregate,news_exclusion,store}.py`；Tavily 检索；剔除 + 后备补位编排。
- **验收**：`ExplainInput` 字段集逐字断言（不含通道 / 位次）；
  AST 断言 `explain/**` 零 import `neckline.k9`；
  输入序列按 `ts_code` 升序（单测断言排序发生了）；
  剔除 → 补位 → 定稿全流程夹具，每步进审计；补位轮数受 `maxBackfillRounds` 约束。

#### S10 · 预案层
- 产出：`playbook/{skeleton,fill,model,store}.py`；四骨架；LLM 填值 + 严格 schema 校验；
  D0 冻结 + append-only 版本化；App 修改入口的后端 API。
- **验收**：`PlaybookInput` 字段集逐字断言（**含** `patterns`，**不含** rank/score/seat/tier/`upside_room_mech*`）；
  LLM 返回带自由文本评价键 → 校验拒绝；未知 MetricRef → 拒绝冻结；
  用户修改产生新版本、原版本不变；三个价位与 `upside_room_mech*` 在 DTO / 表 / 文案里名称互不重叠。

#### S11 · 交割单分析台
- 产出：`review/{bindery,conclusions}.py`；删除 `reconcile.py` 的 K8 章程判据；
  `/api/review/*` 收口。
- **验收**：**这一层零 LLM**（AST 断言 `review/**` 不 import `neckline.llm`）；
  两家券商样例解析回归全绿；FIFO 回合闭合与 `WeeklyStats` 不变；
  `cashflow` 四分类仍**没有**「账户净变动」合计字段；
  装订材料含 K 线 + 买卖点 + 大盘 + 申万二级 + 当时报告/预案快照。

#### S12 · App 重做
- 产出：§5.11 全部。
- **验收**：`xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build` 通过；
  macOS 截图覆盖三态首行、清单、个股详情、核对表、成绩、复盘；
  持仓相关 View / Model / 文案全仓零残留（AST + 文案扫描）；
  行业分与选票分**分两栏**且界面上无任何合计数字；
  🔴 次日核对表视图**只有两段**（已触发放弃 / 待开盘后观察），
  全 App 文案扫描：核对表视图内「成立」零命中（裁定 10）；
  10:00 结算的三分支终值只出现在**成绩**板块，⛔ 不在选股首屏。

#### S13 · 对外接口
- 产出：`export_research_snapshot.py` 加 `--include-fact-packs`；`/api/legacy/k8/baskets`。
- **验收**：导出快照 + 事实包目录 + manifest（逐日 sha256）可在 whynotme 侧只读打开；
  legacy 端点只读（写方法 405）；⛔ 全仓 `grep whynotme` 在 `neckline/` 下零命中。

#### S14 · 发布门禁（高危区，见 §8）
- 产出：迁移演练、备份、上云、验证、回滚物锚点、README 更新、变更日志。
- **验收**：见 §8 与 §9。

### 批 B · 参数标定完成、用户确认后

#### S15 · 四通道定义性条件填数 + 分档放宽两档
- 参数包落位后跑真实日：记录四通道 strict/relaxed 各自日均召回量与**联合通过率**。
- **验收**：⛔ **未经联合通过率验证不得上线**。连续 5 个交易日 strict 档能独立凑足 `quota.min`
  的比例、以及触发「判据过严」的天数，都要如实记录在 §14。

#### S16 · 排序三成分权重 + 形态内合成权重
- **验收**：权重变更前后同一批历史日的清单差异对照；⛔ 不许凭单日感觉调。

#### S17 · 清单成绩五指标结算
- 依赖 S8 的 **10:00 结算拍**（裁定 10）与 D+1..D+4 回填。
- **验收**：三个比率一律读 `k9_d1_verdicts` 的终值，⛔ 不读 9:29 的「待开盘后观察」；
  观察分支不进三个比率的分子分母（返回 `None` 不是 0）；
  行业分 / 选票分分开存且无合计；至少一个完整的 D0→D+4 端到端夹具。

---

## 7. 未决项收口（**本节已清空 —— 无任何待用户拍板事项**）

2026-08-20 用户对本节原有四项全部给出处置。**⛔ 施工侧不得以「这里还没定」为由停手或自行发挥。**

| 原条目 | 处置 | 去向 |
|---|---|---|
| 7.1 是否新增 10:00 结算拍 | **批准**，升为正式裁定 | **裁定 10**（§2）；设计见 §5.7.2、切片见 S8 |
| 7.2 行业热度分「查无该行业」的处理 | **不在此时拍板，降为参数位**：三种取值全部实现，选择随参数包确认 | §8.3 #18 |
| 7.3 跨日接力分的口径 | **不在此时拍板，降为参数位**：四种组合全部实现，选择随参数包确认 | §8.3 #19、#20 |
| 7.4 App 三板块 IA | **批准**，升为正式裁定 | **裁定 11**（§2）；设计见 §5.11 |
| 7.5 K9 §九 漏列一项待标定参数 | **保留为提醒**（不是待拍板项） | 见下 |

### 7.5 提醒（保留）：K9 §九待标定表本身还缺一项

「**每个形态内部各强度项的合成权重**」（4 组）在 K9 §九里没有列。
本 Plan 已把它当作待标定项（§8.3 #17），并在 §5.1-C 要求补进 K9 §九。
请在标定任务里一并覆盖，⛔ 别因为 K9 §九没写就漏标。

### 7.6 「降为参数位」意味着什么（⛔ 别理解成「可以先挑一个用」）

- 施工侧**把全部候选取值都实现**，每种一条夹具（S6 验收）。
- **⛔ 无默认值**：参数包里缺这几个键 = 「参数未配置」= 报告「今天没跑成」（裁定 5）。
- **⛔ 代码里不许有「哪个是默认」的分支**，也不许在示例配置里填一个真取值
  （`config/k9-params.example.json` 里一律写 `"__TO_BE_CALIBRATED__"`）。
- 取值由**标定阶段**连同其余参数一起给出，用户确认后进参数包。

---

## 8. 待标定参数总表（共 20 项 → 裁定 13/15 合并两项、新增两项后仍为 **20 项**，⛔ 一个数都不许填默认值）

**纪律（K9 §九原文）**：单条判据合理 **≠** 连乘后仍有样本。
**⛔ 未经单条通过率与联合通过率验证，不得填入默认值上线。**
下表全部标注「待标定」，⛔ Plan 里不写建议值、不写「暂用 X」。

### 8.1 K9 §九原有 14 项

| # | 所在 | 参数 | 状态 |
|---|---|---|---|
| 1 | K9 第一层 | 冲高回落门槛 | 待标定 |
| 2 | 形态 1 | 放量倍数排名门槛 | **已被裁定 15 并入 §8.2 #22（同一个 V）** |
| 3 | 形态 2 | 归一化跌幅门槛 | 待标定 |
| 4 | 形态 2 | 一字跌停 / 有效换手的判定 | **已被裁定 13 拆开**：一字跌停 = 零参数；有效换手 = §8.2 #21 |
| 5 | 形态 3 | 长窗天数 | 待标定 |
| 6 | 形态 3 | 短窗天数 | 待标定 |
| 7 | 形态 3 | 相对强度「≈0」的区间宽度 | 待标定 |
| 8 | 形态 3 | 「尚未爆发」的判定 | **已被裁定 15 并入 §8.2 #22（同一个 V）** |
| 9 | 形态 4 | 净流入排名门槛（单日 / N 日） | 待标定 |
| 10 | 形态 4 | 资金排名 − 涨幅排名的差值阈值 | 待标定 |
| 11 | K9 第三层 | 行业热度分 / 形态内强度分 / 跨日接力分 三项权重 | 待标定 |
| 12 | K9 第三层 | 跨日接力的回溯天数 N | 待标定 |
| 13 | K9 §五 | 分档放宽的两档定义 | 待标定 |
| 14 | K9 §五 | 触发「判据过严」的连续天数 | 待标定 |

### 8.2 本次新增 4 项（裁定 1、裁定 2 派生 2 项；裁定 13/15 派生 2 项）

| # | 所在 | 参数 | 状态 |
|---|---|---|---|
| 15 | K9 第三层 | **上方机械空间的 N 日** | 待标定 |
| 16 | 行业强度 | **行业中位数的最小成员数** | 待标定（代价表见 §4.5，供选值参考，⛔ 不是建议值） |
| 21 | 形态 2 | **有效换手门槛**（`channels.p2.<档>.minVolMultiple`：放量倍数 ≥ 它） | 待标定（裁定 13） |
| 22 | 形态 1 / 形态 3 | **放量倍数分界值 V**（`volume.eruptionMultiple`：形态 1 ≥ V、形态 3 < V，**同一个值**、**不分档**，理由见 §14 S6 登记） | 待标定（裁定 15；合并了 §8.1 的 #2 与 #8） |

⚠ **§8.1 的 #2「形态 1 放量倍数排名门槛」与 #8「形态 3『尚未爆发』的判定」已被裁定 15
合并成上表 #22 的同一个 V**，K9 §九 已同步合并。#4「一字跌停 / 有效换手的判定」被裁定 13
拆开：一字跌停那一半是**零参数**（⛔ 不为它造键），有效换手那一半 = 上表 #21。
放量倍数的分母窗口是 `volume.maDays`（K9 §3.0.1 原文 20 日，按 §8.4 体例转录，非新增待标定项）。

### 8.3 本次施工识别出、K9 §九未列的 4 项

| # | 所在 | 参数 | 状态 |
|---|---|---|---|
| 17 | K9 第三层 | **每个形态内部各强度项的合成权重**（p1 三项 / p2 一项 / p3 两项 / p4 两项） | 待标定；须补进 K9 §九（§5.1-C） |
| 18 | 行业强度 | **`industry.heatAbsentPolicy`** —— 「查无该行业」的票怎么处理 | **取值待标定**（原 §7.2，用户 2026-08-20 降为参数位） |
| 19 | K9 第三层 | **`ranking.relaySource`** —— 跨日接力里「被选中」的口径 | **取值待标定**（原 §7.3） |
| 20 | K9 第三层 | **`ranking.relayScoring`** —— 跨日接力分的打分形状 | **取值待标定**（原 §7.3） |

**#18–#20 是「取值待标定」，不是「实现待定」。**⛔ 施工侧必须把全部候选取值都实现，
标定阶段只挑一个填进参数包：

| 键 | 候选取值（**全部实现**） | 含义 |
|---|---|---|
| `industry.heatAbsentPolicy` | `renormalize` | 该票按剩余两项权重重新归一 —— 行业无排名不被当成「最差行业」 |
| | `zero` | 行业热度分记 0 —— 等同「最差行业」 |
| | `drop` | 该票直接不参与本日清单 |
| `ranking.relaySource` | `recalled` | 「被选中」= 被通道召回 |
| | `shortlisted` | 「被选中」= 进入过清单 |
| `ranking.relayScoring` | `binary` | 有 / 无，二值 |
| | `count` | 计次（被几个不同形态在几天里选过） |

**#18 的代价数字**（供标定判断，⛔ 不是建议值；完整代价表见 §4.5）：
最小成员数门槛取 **10** → 137 只 / **3.0%** 的池子拿不到行业热度分；
取 **15** → 353 只 / **7.7%**。门槛越高，`heatAbsentPolicy` 选哪个就越要紧。

**#19 的一条观察**（供标定判断，⛔ 不是预设）：K9 §四 原文「过去 N 天内被其他形态**选中**过」，
字面上更接近「**被通道召回**」而非「进入清单」——但 K9 没有明确写，故仍作参数位不预设。

⛔ 三个键一律**无默认值**：参数包缺任一个 = 「参数未配置」= 报告「今天没跑成」（裁定 5）。

### 8.4 K9 原文已给定值的数（**出处逐条标注，不是 Plan 提的建议值**）

这些数**权威文件已经写死**，施工时按原文转录进参数包，标记 `origin='K9原文'`，
仍受 §五-6 分档放宽与 §九校准约束。⛔ 施工侧不得改动，⛔ 也不得替它们编造放宽档
（放宽档定义属于待标定第 13 项）。

| 参数 | K9 原文值 | 出处 |
|---|---|---|
| 次新股上市天数 | 30 天以内 | K9 §二 第 5 条 |
| 流动性过弱 | 20 日平均成交额位于全市场后 20% | K9 §二 第 7 条 |
| 冲高回落 | 当日涨幅 > 5% 且 最高涨幅 − 收盘涨幅 ≥ 3 个点 | K9 §二 第 9 条（§九注明「首版值，待校准」） |
| 形态 1 振幅窗口 / 上限 | 过去 20 天振幅 ≤ 25% | K9 §3.2 |
| 形态 1 当日涨幅 | > 0 | K9 §3.2 |
| 形态 2 均线 | 前一日收盘价 ≥ 20 日均线 | K9 §3.3 |
| 形态 3 长窗 / 短窗 | 约 60 日 / 约 5-10 日（§九标为待标定 → 走 8.1 第 5、6 项） | K9 §3.4 |
| 形态 4 累计窗口 | 5 日累计 | K9 §3.5 |
| 清单容量 | 最少 10、最多 20 | K9 §五-1 |
| 保底席位 | 每形态 1 席 | K9 §五-2 |

---

## 9. 迁移与回滚（🔴 高危区：动 schema、删读路径、换定时任务、改发版单元）

本版同时触碰**数据库 schema、大批读路径删除、systemd 定时任务、发版单元、破坏性 API**。
按本仓既有 RC 门禁执行，一步不许省。

### 9.1 升级前

1. **两份一致的 SQLite 备份**：`/opt/neckline/data/neckline.db.bak{,2}-v250-<ts>`，
   两份 `sha256` 必须相同，两份都跑 `PRAGMA integrity_check` 通过。
2. **源码 / 发布物锚点**：生产现有源码归档到
   `/opt/neckline-release-backups/v2.5.0-pre-<ts>/`（含 `deploy/` 全部 unit 与 `config/`）。
3. **明确的生产目标确认**：`deploy@114.66.0.38:/opt/neckline`；
   部署前后各查一次 `https://nk.linotsai.top/api/health`（前 `v2.4.2`，后 `v2.5.0`）。
4. **macOS / iOS 旧包保留**：现役 macOS `2.4.2 (9)` 存
   `/Users/linotsai/Lino/app_backups/v2.4.2-b9-pre-v250-<ts>/`。
5. **parquet 目录**：本版新增 `fact_pack/` 与 `k9_disposition/` 两个目录，
   ⛔ 不动任何既有 parquet 表；回滚只需删这两个新目录。

### 9.2 迁移动作

- `init_schema()` 仍是**受控写入口**：只允许 API 启动、显式写命令、RC 迁移步骤在
  **已确认目标库且已完成备份后**调用。API / 报告 / 复盘的读 helper **不执行 DDL**。
- 本版所有 schema 变更是**纯新增**（新表 + 新 parquet 目录），
  ⛔ **不 ALTER、不 DROP、不 UPDATE 任何 K8 表**。
- 迁移后立刻核对：K8 只读表的行数与迁移前**逐表相等**。

### 9.3 定时任务变更

- `neckline-daily.service`（16:05）：加申万分类日更 + 事实包构建冻结。
- 晚间链段序：`verify,scan,basket,review,report` → **`facts,k9,explain,playbook,report`**。
  三个 oneshot 单元保持 scan/basket/report 三个文件名不动（改 ExecStart 的 `--segments`），
  避免又动一次 unit 拓扑。
- 🔴 `neckline-evening.target` 的 **`StopWhenUnneeded=yes` ⛔ 绝对不许删**
  （删了 = 只跑第一晚，次晚起静默全哑；血泪见该文件头部）。
- 🔴 三段 service ⛔ 永远不许加 `RemainAfterExit=yes`。
- 🔴 在**已经卡住**的机器上首次施加 target 变更前，必须先 `systemctl stop neckline-evening.timer`，
  否则 `daemon-reload` 会当场补跑一次全链（会真推送）。
- `MemoryMax` / `TimeoutStartSec`：事实层段是新的批算大户 → **必须重新实测后再定**，
  ⛔ 不许照抄 K8 时代的 1400M/900M/1000M（P0-23 纪律）。

**10:00 结算拍的部署归属（裁定 10）**

🔴 **⛔ 不新增 systemd unit。** 结算拍与 9:26 竞价拍一样跑在**既有常驻 `neckline.service`** 的
`_morning_loop` 里（沿用现行做法）。理由：两拍各自是秒级的进程内 tick；多一个 unit 就多一个
触发面和一条双跑路径，而「当日只跑一次」是记在防重台账里的，双触发会把「今天跑没跑过」
变成一道要现场推理的题。**部署侧因此只需确认 `neckline.service` 已重启并加载新代码。**

**本版部署单元逐个交代（⛔ 部署时按这张表核，别凭印象）**

| unit | 本版动作 | 要点 |
|---|---|---|
| `neckline.service`（常驻 API） | **重启** | 承载 `_morning_loop` 两拍（9:26 核对表 + 10:00 结算）。重启后核 `NRestarts=0` 与 `/api/health` 返回 `v2.5.0` |
| `neckline-daily.service` / `.timer`（16:05） | **改 ExecStart 内容** | 加申万分类日更 + 事实包构建冻结；`MemoryMax` 重新实测后再定 |
| `neckline-scan.service`（晚间 seg1） | **改 `--segments`** | → `facts`；这是新的批算大户，`MemoryMax` / `TimeoutStartSec` 必须实测 |
| `neckline-basket.service`（晚间 seg2） | **改 `--segments`** + 改 `--direction-pipeline-config` → `--k9-params` | → `k9,explain,playbook`；参数包路径显式传入，⛔ 无默认路径 |
| `neckline-report.service`（晚间 seg3） | **改 `--segments`** | → `report`；保留 `--notify` |
| `neckline-evening.target` | **不改文件** | 🔴 `StopWhenUnneeded=yes` ⛔ 不许删；施加顺序见上 |
| `neckline-evening.timer` | **不改** | Mon–Thu 16:35 + Sun 19:00 排程原样保留 |
| 新增 unit | **无** | 本版**零新增 systemd unit** |

### 9.4 回滚

- **已验证的回滚版本 = `v2.4.2` Build 9**（commit `ee12b9b`）。
- 回滚 = 恢复 §9.1 的源码归档 + 恢复 SQLite 备份 + `daemon-reload` + 重启 `neckline.service`
  + 删除新增的两个 parquet 目录 + 重装 macOS `2.4.2 (9)`。
- ⛔ 任何 GET / 日常读取都**不是**迁移触发器；回滚边界是「迁移前备份 + 已验证的 v2.4.2 源码」。

### 9.5 上线后必须立刻告诉用户的一件事

参数包未标定完成之前，**每日报告的清单段首行会是「今天没跑成 · 参数未配置」**。
这是**设计行为**（裁定 5），不是故障。报告的方向背景、市场事实与覆盖率成绩线照常。
⛔ 不许为了「让报告好看」临时塞一组数字进参数包。

---

### 9.6 v2.5.0 发版清单(**照着走**;S14 已准备,🔴 **本组一步都没执行**)

🔴 **执行状态:未执行。** 本组没有 ssh、没有部署、没有碰生产、没有改 `MemoryMax`、
没有推送。下面每一步都还没做过 —— 这一节是**给执行那天的人照着走的**。
⚠ **能在本地先跑一遍的那几件已经跑过了**(演练 ≠ 写了一份清单),机器判据在
`Backend/tests/test_v250_s14_release_gate.py`(13 条,全在临时库上跑)。

🔴 **前置阻塞项(⛔ 未解决不上云)**:见下方 **步骤 0**。

#### 步骤 0 · 🔴 阻塞:容量红线要先有结论(§13.1-B5)

| 事实 | 数 |
|---|---|
| 策略层 RSS 峰值(**真实数据**,S6 实测,开发机 macOS) | **736 MB**(120 天 × 663,120 行 × 23 列投影,frame 101.6 MB) |
| `deploy/neckline-basket.service` 现挂 | `MemoryMax=900M` → **余量 18%** |
| explain / playbook 增量(S9/S10 实测,合成市场) | **各 +0 MB**(工作集 60 天 × 20 只 ≈ 1,200 行,相对 66 万行的 frame 可忽略) |
| 生产规格 | **2 vCPU / 1.6 G** |
| 前科 | **2026-07-29 被 OOM-kill 挡过一次上云**;超 `MemoryMax` 会被 kill **且不报错**(§12 坑 1) |

⛔ **施工侧未自行改 `MemoryMax`、未拆 unit** —— 那要用户点头(选项 a/b/c/d 见 §13.1-B5)。
**上云前必须做的一件事**:在**生产同规格 Linux 机**上用**真实数据**复测策略层峰值
(736 MB 是 macOS 上的读数,Linux 上 polars 的分配行为不一定相同,拿一个跨平台的数
去改生产 cap 是在猜)。复测结论出来之前 ⛔ 不执行下面的步骤。

#### 步骤 1 · 升级前(⛔ 一步不许省)

1. **停表**(⚠ 必须最先做):`systemctl stop neckline-evening.timer neckline-daily.timer`
   —— 🔴 在**已经卡住**的机器上首次施加 target 变更前不停,`daemon-reload` 会当场
   补跑一次全链(**会真推送**,§9.3)。
2. **两份一致的 SQLite 备份**:
   `sqlite3 /opt/neckline/data/neckline.db ".backup '/opt/neckline/data/neckline.db.bak-v250-<ts>'"`,
   再拷一份 `.bak2-v250-<ts>`。**两份 `sha256sum` 必须相同**,两份都跑
   `PRAGMA integrity_check` 返回 `ok`。⛔ 不用 `cp` 热备(SQLite 在写时 `cp` 会拷出坏库)。
3. **源码 / 发布物锚点**:生产现有源码整目录归档到
   `/opt/neckline-release-backups/v2.5.0-pre-<ts>/`(**含 `deploy/` 全部 8 个 unit 与 `config/`**)。
4. **明确的生产目标确认**:`deploy@114.66.0.38:/opt/neckline`;
   部署**前**查一次 `https://nk.linotsai.top/api/v1/health` → 期望 `v2.4.2`。
5. **macOS / iOS 旧包保留**:现役 macOS `2.4.2 (9)` 用 **`ditto`** 拷到
   `/Users/linotsai/Lino/app_backups/v2.4.2-b9-pre-v250-<ts>/`(⛔ **不用 `cp -R`** ——
   它可能悄悄拷坏 Resources 而签名校验仍通过,§12 坑 10)。

#### 步骤 2 · 迁移动作(**纯新增**:零 ALTER / 零 DROP / 零回填)

- **本版新增 16 张表**(全部 `CREATE TABLE IF NOT EXISTS`,升级后**全空**):
  `fact_packs` · `sw_industry_classify` · `sw_industry_daily` · `sw_industry_member` ·
  `k9_runs` · `k9_channel_hits` · `k9_listing_entries` · `k9_reports` ·
  `k9_coverage_daily` · `k9_coverage_misses` · `k9_checklists` · `k9_d1_verdicts` ·
  `k9_playbooks` · `k9_explain_notes` · `k9_explain_audit` · `review_conclusions`。
- **新增 2 个 parquet 目录**:`data/parquet/fact_pack/` 与 `data/parquet/k9_disposition/`
  (回滚只需删这两个,⛔ 不动任何既有 parquet 表)。
- 🔴 **本版没有新增任何 `ALTER` / `DROP` / `UPDATE`**。⚠ `db.py` 里**确实**有 ALTER/DROP,
  但它们是 **V2.2 / V2.4.2 就在的幂等迁移**(`_relax_holding_eod_check_notnull` /
  `_migrate_selection_generation_tables`),生产上早就跑过 —— 与基线逐条比对为**零新增**
  (`test_v250_adds_no_new_column_migration_and_no_new_alter_or_drop`)。
- **`init_schema()` 的受控写入口边界**:只允许 ① API 启动、② 显式写命令、
  ③ **本节这一步**在**已确认目标库且已完成备份之后**调用。
  ⛔ API / 报告 / 复盘的读 helper **不执行 DDL**。
- **迁移后立刻核对**:K8 只读留档表的**行数逐表相等**(裁定 6:表保留、只读、
  不迁移、不回填)。本地已按 v2.4.2 老库演练过一遍,历史行**逐表逐列不变**、
  `integrity_check` 通过。

#### 步骤 3 · unit 逐个交代(🔴 **本版零新增 unit**;⛔ 按这张表核,别凭印象)

| unit | 本版动作 | 要点 |
|---|---|---|
| `neckline.service`(常驻 API) | **只重启** | 承载 `_morning_loop` **两拍**(9:26 核对表 + 10:00 结算)。重启后核 `NRestarts=0` 与 `/api/v1/health` 返 `v2.5.0` |
| `neckline-daily.service` | **文件未改**,但它跑的 `scripts/daily_update.py` **内容变了** | 加了申万分类日更 + 事实包构建冻结 + 覆盖率;**移除了 `ths_*` 概念抓取**(裁定 17)。`MemoryMax=900M` 未动 —— ⚠ 事实包冻结是新增负载,上云后头一周盯一次 |
| `neckline-daily.timer` | **不改** | Mon–Fri 16:05 |
| `neckline-scan.service`(晚间 seg1) | **`--segments` 已改成 `facts`**(S7 改的文件,**尚未部署**) | 新的批算大户;`MemoryMax=1400M` **未动**,⚠ 归步骤 0 复测 |
| `neckline-basket.service`(晚间 seg2) | **`--segments` 已改成 `k9,explain,playbook`;`--direction-pipeline-config` → `--k9-params`**(S7 改的文件,**尚未部署**) | 🔴 `--k9-params /opt/neckline/config/k9-params.json` **那个文件现在不存在**(参数待标定)→ 这一段每天出「今天没跑成 · 参数未配置」,**设计行为**(§9.5)。`MemoryMax=900M` **未动**,⚠ 步骤 0 的红线就在这个 unit 上 |
| `neckline-report.service`(晚间 seg3) | **`--segments` 已改成 `report`**(S7 改的文件,**尚未部署**),保留 `--notify` | `MemoryMax=1000M` 未动 |
| `neckline-evening.target` | **不改文件** | 🔴 `StopWhenUnneeded=yes` ⛔ **绝对不许删**(删了 = 只跑第一晚,次晚起**静默全哑**)。🔴 三段 service ⛔ **永远不许加** `RemainAfterExit=yes` |
| `neckline-evening.timer` | **不改** | Mon–Thu 16:35 + Sun 19:00 |
| **新增 unit** | **无** | 🔴 两拍都跑在既有常驻 `neckline.service` 里 —— 多一个 unit 就多一个触发面和一条双跑路径,而「当日只跑一次」记在防重台账里,双触发会把「今天跑没跑过」变成一道要现场推理的题 |

⚠ **三个 oneshot 的文件名一个都没改**(只改 `ExecStart` 的 `--segments`)——
避免又动一次 unit 拓扑。8 个 unit 的文件名是**精确集合**,守门单测按集合对拍。

#### 步骤 4 · 部署与验证

1. 拉代码 → `daemon-reload` → `systemctl restart neckline.service`。
2. `curl https://nk.linotsai.top/api/v1/health` → 期望 `v2.5.0`;`NRestarts=0`。
3. 手工先跑一次 16:05 日更(`systemctl start neckline-daily.service`),看
   `fact_packs` 有当日行、`sw_industry_member` 非空、`k9_coverage_daily` 有当日行。
4. **再启表**:`systemctl start neckline-evening.timer neckline-daily.timer`。
5. 客户端:装 macOS `2.5.0 (10)`(**`ditto`**,⛔ 不用 `cp -R`)。
   ⚠ iOS 通知栏图标缓存靠 `AppIconV250` 这个新 asset-set 名失效 —— 覆盖安装后核一眼。

#### 步骤 5 · 🔴 上线后的状态预告(**写给操作者:这不是故障**)

- **22 项待标定参数一个都没填**(§8),`config/k9-params.json` **不存在** → 每天的报告:
  - **清单段** = 「今天没跑成 · 参数未配置(缺键 …)」——**设计行为**(裁定 5 / §9.5);
  - **方向背景段** = 「未接入」(`facts/direction_llm.py` 本版未建);
  - **市场事实段** = **照常有**(它来自已冻结的事实包,与参数无关);
  - **覆盖率成绩线** = **照常跑**(口径 = 涨停,不依赖任何待标定数字)——
    这是上线首日唯一能出真数的一条线;
  - **次日核对表 / 10:00 结算** = 没有清单就没有要核的票 → `/checklist/{date}` 404,
    `/scoreboard/verdicts/{date}` 空数组。
- ⛔ **绝不许为了「让报告好看」临时塞一组数字进参数包**(§9.5 逐字)。
- ⚠ **生产 `neckline-daily.timer` 在部署之前仍跑旧 v2.4.2 代码**:裁定 17(停抓概念)
  与 S1 摘掉的三项 K8 日更**要到这次部署才生效**(§13.1-B6)。

#### 步骤 6 · 回滚(**已验证的回滚目标 = `v2.4.2` Build 9 / commit `ee12b9b`**)

回滚到**步骤 1 结束、步骤 2 开始之前**那个状态。逐步:

1. `systemctl stop neckline-evening.timer neckline-daily.timer neckline.service`;
2. 恢复步骤 1.3 的源码归档整目录(**含 8 个 unit 与 `config/`**);
3. 恢复步骤 1.2 的 SQLite 备份(⚠ 用**第一份**;第二份是对照,别覆盖它);
4. **删掉本版新增的两个 parquet 目录**:`data/parquet/{fact_pack,k9_disposition}/`
   —— ⛔ 别删任何其它 parquet 表;
5. `daemon-reload` → `systemctl start neckline.service`;
6. **怎么验回滚成功**(四条都要过):
   `/api/v1/health` 返 **`v2.4.2`** · `PRAGMA integrity_check` = `ok` ·
   K8 只读表行数与步骤 1.2 备份**逐表相等** · 晚间链手跑一次 seg1 不报错;
7. 客户端回装 macOS `2.4.2 (9)`(**`ditto`**)。
8. ⛔ **任何 GET / 日常读取都不是迁移触发器** —— 回滚边界就是「迁移前备份 + 已验证的
   v2.4.2 源码」这两样。

#### 步骤 7 · 收尾

- `README.md` 的「SQLite schema 边界」一段把回滚锚点从 `v2.4.1` 更到 `v2.4.2`(已改);
- `PROJECT_PLAN.md` §14 追加一行发版记录(实际部署时刻 / 健康检查读数 / 有没有回滚);
- `~/hz_info.md` 同步(HZ 云端唯一事实文件,只写非敏感事实)。

## 10. 验证矩阵

每个切片完成、以及发版前，全跑一遍：

```bash
cd /Users/linotsai/Lino/Neckline/Backend
.venv/bin/python -m pytest -q

# 临时库 API 冒烟（⛔ 绝不允许 fallback 到工作库）
DB_PATH=$(mktemp -d)/smoke.db .venv/bin/python -m uvicorn neckline.api.app:app --port 8002 &
bash scripts/smoke_api.sh

cd /Users/linotsai/Lino/Neckline/App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
```

**测试纪律**（AGENTS.md）：测试必须用临时库或显式只读快照，⛔ 绝不允许 fallback 到工作库。
参数夹具必须**显式提供每一个值**（§5.4.3 的无默认值 dataclass 会强制这一点）。

**守门单测清单**（每条都要有一个具名测试）：

| # | 断言 |
|---|---|
| G1 | `facts/**` 不 import `k9`/`explain`/`playbook`/`scorecard`；FactPack 列名无策略词根 |
| G2 | `k9/**` 不 import `llm`/`search`/`httpx`/`openai`/`requests`/`urllib`/`socket` |
| G3 | `k9/**` 不 import `tushare_client`/`market_data`（取数唯一来源是事实包） |
| G4 | `k9/channels/*` 相互零 import，且不 import `ranking`/`quota`/`run` |
| G5 | `explain/**` 零 import `k9`；`ExplainInput` 字段集逐字相等；输入按 `ts_code` 升序 |
| G6 | `PlaybookInput` 字段集逐字相等（含 patterns，不含 rank/score/seat/tier/upside_room_mech） |
| G7 | `review/**` 与 `auction/checklist.py`、`auction/settle.py` 零 import `neckline.llm` |
| G8 | `K9Params` 每个字段无默认值；缺任一键 → `ParamsUnavailable` |
| G9 | 参数缺失 → `ReportState.not_run`，⛔ 不是 `empty` |
| G10 | 同事实包 + 同参数包跑两遍 → canonical JSON 逐字节相等 |
| G11 | 全仓（Python + Swift + md 模板，排除 `archive/`）`上方空间` 零命中；`k9/**` 内 `first_resistance` 零命中 |
| G12 | 「N 日最高」只有一处实现；p1 正向 / p3 反向共用 |
| G13 | scorecard 无「行业分 + 选票分」的合计字段与合计代码路径 |
| G14 | 观察分支 → 三个比率返回 `None`（不是 0） |
| G15 | 竞价窗口外调用零落库；事后补跑被拒；当日防重生效 |
| G16 | `freeze_pack` 只接受 `CompletePack`；同 `(date, version)` 二次冻结抛错 |
| G17 | 停牌双向夹具（S 混进 daily 被排除并计数；R 涨停被计入） |
| G18 | `neckline/**` 零 `import whynotme` |
| G19 | 退役对象零残留（sentinel / scan / selection / strategy / positions 的 import 与文案） |
| G20 | `ChecklistVerdict` 恰好两个枚举成员；9:29 核对表与 `/api/checklist/{date}` 响应里**不存在「成立」取值**；App 核对表视图文案「成立」零命中（裁定 10） |
| G21 | 10:00 结算拍**零推送**（跑一次后 APNs 调用计数 = 0）、零 LLM；窗口外零落库；⛔ 事后补跑被拒；已在竞价定案的票不被改判 |
| G22 | `heatAbsentPolicy` 三种取值、`relaySource` × `relayScoring` 四种组合**各有一条夹具**；代码里不存在「哪个是默认」的分支；示例配置里三个键都是 `"__TO_BE_CALIBRATED__"` |

发版前另需：macOS 截图覆盖三态 + 清单 + 详情 + 核对表 + 成绩 + 复盘；
若既有测试 target 支持，补 iOS 模拟器等价状态。

---

## 11. 用户网页操作清单

**本版无需用户在网页上手工办理任何事项。**

- TuShare 600 元档 token 已具备本版全部接口权限（`index_classify` / `index_member_all` /
  `sw_daily` 实测通过，无权限错误，§4.4）。
- LLM Provider / Tavily 凭据沿用现有配置，入口在 macOS「设置 → LLM Provider 与任务路由」。
- git 提交推送与上云部署由施工侧自理，不列入本清单。
- ⚠ 唯一需要用户**手工放文件**的动作：标定完成后把 whynotme 产出的
  `k9-params.<version>.json` 放进 `Backend/config/`（本地文件操作，非网页）。

---

## 12. 项目专属的坑（施工前必读，⛔ 踩一次就是一天）

1. **生产是 2 vCPU / 1.6 G，全历史扫 parquet 会被 OOM-kill 且不报错。**
   实测：`daily` 全历史（1589 分区 / 784 万行）在 700M cap 被 OOM-kill、1400M cap 600s 跑不完，
   2026-07-29 挡住整版上云。修法永远是**预计算落表 / 只读当日分区 / 列投影**。
   ⛔ 在线路径禁止现算全历史。本版新增的「读 N 天事实包」路径**必须实测峰值内存**后再定 `MemoryMax`。
   ⛔ 不许用 `get_market_slice` / `scan_table_range` 读单日 —— 它们走 `year=*/**.parquet` 全 glob，
   会打开 1500+ 个 parquet footer。单日一律 `pl.read_parquet(day_file_path(...))`。
2. **新 parquet 表必须在 `market_data.TABLE_FLOAT_COLS` 里显式声明数值列。**
   不声明会退回「向既有分区看齐」，而**基准分区本身可能是脏的**（`moneyflow_dc` 的空 String 首分区
   曾把干净新数据一路写成 String，2026-07-27 当日报告直接崩掉）。空表也要显式写 `()`。
3. **`suspend_d` 的 `R` 是复牌不是停牌**（§4.6）。认 R 会误杀真实交易日。样本只有两天 → 写断言不写假设。
4. **`daily_basic.volume_ratio` 只有 2 位小数**，做排名会大量并列。排名必须自算 `vol/vol_ma5`（§4.7）。
5. **`index_member_all` 单次 3000 行封顶**，不翻页会静默少拿一半票。
6. **申万归属按代码不按名称**（白酒Ⅱ = `801125.SI`）。名称会变，代码不变。
7. **`to_symbol` 的后缀优先不可退化**：前缀启发式对指数会静默拿错标的
   （`000001.SH` 上证综指会被判成 `sz000001` 平安银行）。搬家到 `data/realtime.py` 后单测原样带过去。
8. **`neckline-evening.target` 的 `StopWhenUnneeded=yes`**：删了 = 首晚必成、次晚起静默全哑。
   三段 service ⛔ 永远不许加 `RemainAfterExit=yes`。改这套东西前先读该文件头部整段。
9. **周日报告的双日期**：`report_date=周日`、`trade_date=上一周五`。
   混同会让标题 / 推送 / 可见身份全错，而底层计算看起来是对的 —— 最难发现的一类错（LRN-20260816-001）。
10. **拷贝 `.app` bundle 必须 `ditto`，⛔ 不用 `cp -R`**（cp 可能悄悄拷坏 Resources，签名校验仍通过）。
11. **改 SwiftUI View 必须 `xcodebuild` 跑 App target 验证**，只跑 SwiftPM package 不暴露 View 层问题。
12. **事实包回填的语义差**：回填包用**今天的** `stock_basic` / 申万归属，不是那天的。
    写在明处，⛔ 别写「自动检测行业变更并回改历史」的机灵代码；要重置就整段重跑。
13. **LLM 输出契约**：上一版的血泪是「后一段 LLM 退役后，它原本负责的字段没人产出，
    而校验只检查了『是个 mapping』」（LRN-20260816-002）。本版预案层的数值校验必须查
    **post-clamp 完整性**，空成功一律判失败。
14. **测试禁止落工作库**（AGENTS.md）。所有需要库的测试用临时库或显式只读快照。

---

## 13. Backlog

分两节：**§13.1 等用户裁定**（施工侧 ⛔ 不自行拍板，已绕过它继续做）与
**§13.2 本版不做，记着别忘**。

⚠ §13.1 的每一条都是**各组登记在 §14 各处**的未决项：第五组统一收拢到这里，
第六组续了 B10 / B11。格式一致：**事实 / 选项 / 影响面 / 我的倾向（标明是倾向，不是决定）**。
⛔ 「我的倾向」不是已定案，⛔ 施工侧不得据它施工。

### 13.1 等用户裁定

#### B1 · `MetricRef.FIRST30_LOW` 在 9:26 那一拍绑竞价价的语义（施工侧结构判断，待**策略侧**复核）

- **事实**：K9 §6.3 四条「放弃」全是「跌破 X」；§5.7.2 逐字写「放弃分支四个全是单条
  破位判定 → **竞价价就能触发**」；而 §5.6.3 的闭合枚举里能承载「跌破」的只有
  `first30_low`。S8 因此把它定义为「**本场至今的最低价（含 9:25 竞价成交）**」——
  9:26 时本场只有一笔竞价成交，那个最低价**就是**竞价价；10:00 时它是 `Quote.low`。
  它是**单调下行**的量，这正是「先到先定、⛔ 不改判」在语义上站得住的原因。
  ⛔ S8 未新增第 10 个枚举成员。
- **选项**：(a) 认可该语义，`first30_low` 在两拍上是同一个量的两次读数；
  (b) 拆成两个枚举成员（`auction_low` 与 `first30_low`），预案里两条分支各绑各的；
  (c) 让 9:26 那一拍**完全不判**「跌破」，全部推到 10:00。
- **影响面**：`playbook/model.py` 的闭合枚举、`auction/{checklist,settle}.py`、
  已冻结的预案 JSON 形状；选 (b) 要给存量预案定迁移口径。
- **我的倾向（⚠ 倾向不是决定）**：(a)。(b) 会让「9:29 提前告知哪几只已经死了」这件事
  多出一个只在竞价那三分钟有意义的量；(c) 会让核对表在 9:29 变成一张空表。

#### B2 · 形态 2「跌破昨日最低价 [B]%」落成了**价位**而不是百分比（待**策略侧**复核）

- **事实**：K9 §6.3 形态 2 的「放弃」写的是「跌破昨日最低价 [B]%」，而 §5.6.3 的条件
  语法 `{op, lhs, rhs}` 是**闭合**的、**没有算术**。S9/S10 因此让 LLM 直接给**那个价位**
  （`rhs` 是有限数值），百分比可由 `(prev_low − rhs) / prev_low` 反算，信息一点没丢。
  ⛔ 未擅自给条件语法加乘法。
- **选项**：(a) 维持现状（LLM 给价位，百分比反算）；(b) 给语法加一个受限的
  「基准 × 系数」形式，只允许 `MetricRef × 常数`；(c) 给 `Condition` 加一个可选的
  `rhs_pct` 字段，由求值器按 `lhs` 的基准换算。
- **影响面**：`playbook/model.py` 的语法闭合性（它是「求值器是全函数」的唯一依据）、
  `evaluate.py`、LLM 返回体校验、App 的预案修改入口。
- **我的倾向（⚠ 倾向不是决定）**：(a)。(b)(c) 都是往求值器里开一个口子，而那条闭合性
  正是「⛔ 绝不让一个次日早上求不出值的条件被冻结进去」的全部依据。

#### B3 · `explain/input.py::KLINE_SESSIONS = 60` 是否该搬进参数包

- **事实**：S9 起的一个数，是「给 LLM 看几根日 K」的**上下文长度**。换成 40 或 80
  **不会让任何一只票被选上或落选**，只影响模型看到多长一段图。§8 的 22 项待标定参数里
  没有它，K9 §九 也没有。已写死在一处，`build_inputs(sessions=)` 是必填关键字。
  ⚠ **S11 又添了同类的两个**：`review/bindery.py::PRE_SESSIONS / POST_SESSIONS`（各 20，
  买入前 / 卖出后各看几个交易日），同样是上下文长度，同样做成必填关键字。
- **选项**：(a) 三个都留作模块常量（现状）；(b) 三个都搬进参数包，随标定一起给；
  (c) 只搬 `KLINE_SESSIONS`（它喂给 LLM，可能影响解释质量），装订那两个留常量。
- **影响面**：选 (b)(c) 是一次**参数包 schema 变更**（`REQUIRED_SCHEMA` 加键 →
  存量示例配置与标定产物都要跟），归标定侧。
- **我的倾向（⚠ 倾向不是决定）**：(a)。按架构 §二 的判据，「凡是我会想去调的东西都落在
  策略层」——但这三个数调了不改变**任何一只票的去留**，把它们塞进参数包会让「参数未配置
  = 今天没跑成」这条铁律为一个显示长度而触发。⚠ 若用户认为自己确实会想调它，那按同一条
  判据它就该进参数包 —— 这一条只有用户能判。

#### B4 · V 不分档的**标定侧**复核（裁定 16 已定，但标定方必须知道这个结构性质）

- **事实**：**裁定 16 已定案**（§2），V 是**一个**值、⛔ 不分档。本条不是重开裁定，
  是把它对标定工作的**约束**摆出来：V 同时是形态 1 的下限与形态 3 的上限，
  「p1 召回太少」⛔ **不能**靠放宽 V 解决 —— 那只会把票从 p3 搬到 p1，
  `p1 ∪ p3` 一只都不会多。p1 要变松只能动它**别的**定义性项（振幅窗口 / 涨幅门槛）。
- **选项**：（无需裁定，只需确认标定任务书里写进了这条约束）
- **影响面**：标定阶段对 p1 / p3 日均召回量的调法；S15 的联合通过率验证。
- **我的倾向（⚠ 倾向不是决定）**：把这条原样抄进给 whynotme 的标定任务书。

#### B5 · 🔴 容量红线：`neckline-basket.service` 的 `MemoryMax=900M` 只剩 18% 余量

- **事实**：S6 在**真实数据**上实测策略层 RSS 峰值 **736 MB**（120 天 × 663,120 行 ×
  23 列投影，frame 101.6 MB）；`deploy/neckline-basket.service` 现挂 `MemoryMax=900M`
  → 余量 **18%**。S9/S10 在**合成市场**上实测 explain / playbook **各 +0 MB**
  （工作集 60 天 × 20 只 ≈ 1,200 行，相对策略层那份 66 万行的 frame 可忽略；
  该组的 532 MB 因合成市场字符串区小，⛔ 不能与 736 MB 直接比）。
  S11 的装订新增读取**不落在这个 unit 上**（它跑在常驻 `neckline.service`，
  且窗口受 `MAX_WINDOW_SESSIONS=250` 与「一次 glob」双重约束）。
  🔴 **生产是 2 vCPU / 1.6 G，超 `MemoryMax` 会被 OOM-kill 且不报错**（§12 坑 1）。
- **选项（⛔ 施工侧未自行选）**：
  (a) 维持 `MemoryMax=900M`，接受 18% 余量，S14 在生产同规格 Linux 机上用**真实数据**
      复测后再定；
  (b) 抬高 `neckline-basket.service` 的 `MemoryMax`（要算清 1.6 G 机器上三段并存的账）；
  (c) 把 `explain,playbook` 拆成第四个晚间 oneshot —— ⚠ 它与「两拍 ⛔ 不新增 unit」
      是两码事（那条管的是**早晨**的进程内 tick），但仍会动 unit 拓扑；
  (d) 降 `MAX_LOOKBACK_PACKS`（现 120）—— ⚠ 它会**直接限制**参数包里的窗口上限，
      属于会影响策略的工程参数，⛔ 不该为省内存单方面调。
- **影响面**：S14 发版门禁；(c)(d) 还牵连 `neckline-evening.target` 的段序与参数校验。
- **我的倾向（⚠ 倾向不是决定）**：(a) 先复测再决定 —— 736 MB 是**开发机 macOS** 上的
  读数，Linux 上 polars 的分配行为不一定相同，拿一个跨平台的数去改生产 cap 是在猜。

#### B6 · 生产 `neckline-daily.timer` 仍跑旧 v2.4.2 代码，**部署前概念抓取仍会继续**

- **事实**：裁定 17 已把 `ths_*` 抓取段从 `scripts/daily_update.py` 移除，但**代码还没上云**
  （本版全程 ⛔ 未部署、⛔ 未 ssh）。生产 `/opt/neckline` 上跑的仍是 v2.4.2 Build 9，
  它的 16:05 日更**照旧**每天 5 次 `ths_daily`、每周 395 次连续 `ths_index`/`ths_member`。
  同理，S1 摘掉的三项 K8 日更在生产也仍在跑。
- **选项**：(a) 等 S14 整版上云时一并生效（本版计划）；
  (b) 单独先推一次 `daily_update.py` 止血 —— ⚠ 那要 ssh 生产、且新旧代码混跑，
      而新 `daily_update.py` 会调 S2/S3/S4 的新函数（`sw_industry` / `fact_pack` /
      `coverage`），⛔ 在旧库上跑不起来；
  (c) 在生产手工 `systemctl stop neckline-daily.timer` 直到 S14 —— ⚠ 会同时停掉
      行情主增量（daily/basic/adj/moneyflow），代价远大于配额。
- **影响面**：TuShare 配额（约 60 次/交易日 + 395 次/周）；数据完整性。
- **我的倾向（⚠ 倾向不是决定）**：(a)。(b) 是新旧混跑，(c) 会停掉真正要紧的行情增量；
  配额浪费在 600 元档下不是瓶颈，等 S14 一起切最干净。

#### B7 · 「我的成绩」这条线**落在哪个包**（第五组新发现）

- **事实**：§3.3 的包布局把 `scorecard/mine.py` 列在 `scorecard/` 下，但 §6 的
  **任何一个切片都没有认领它**（S11 的产出清单只有 `review/{bindery,conclusions}.py`，
  S17 只做清单成绩五指标）。而 §5.8.3 要求「我的成绩」与前两条线**完全隔离**，
  §5.9 又把它归在周末复盘线上。⚠ 若真放进 `scorecard/`，那个文件就得
  import `neckline.review`，而本组刚立的守门单测断言 `scorecard/**` **零 import**
  `neckline.review`（那条守门正是「交割单成交不进另外两条线的分子分母」的牙齿）。
  现状：「我的成绩」的**内容**已经存在 —— `reconcile.WeeklyStats`（胜率 / 盈利因子 /
  盈亏比 / 费用 / 毛净盈亏 / `realized_loss`），由 `/review` 与 `/review/overview`
  的对账段下发。
- **选项**：(a) 认定它已由 `review/reconcile.WeeklyStats` 承担，§3.3 里那行
  `scorecard/mine.py` **作废**（Plan 勘误）；
  (b) 建 `review/mine.py`（住复盘包、离数据最近，隔离守门不动）；
  (c) 建 `scorecard/mine.py` 并**放宽**那条守门，改成「`coverage.py` / `listing.py` 零
  import review」的逐文件白名单。
- **影响面**：§3.3 包布局、S12 App「复盘」板块的数据来源、那条隔离守门单测。
- **我的倾向（⚠ 倾向不是决定）**：(a) 或 (b)。⛔ 不建议 (c)：一条带白名单的隔离守门，
  第二次加白名单时就没人拦得住了。

#### B8 · 复盘板块的**校准段**在 K9 之下还成不成立（第五组新发现）

- **事实**：`/review/overview` 的校准段与 `/eval/weekly` 读的是 whynotme 周度离线作业
  落盘的 `calibration_{from}_{to}.{md,json}`，而 `review/{handoff,research_artifact}.py`
  里的排版仍是 **K8 语义**（Tier 入场信号正确率 / C·Z·Y 引擎版本 / 双时钟 / 包成绩单）。
  K8 整链已退役，那份产物在 K9 之下的**形状未定义**。S1 因为这两条路由被 §5.12 明确
  「保留」而**被迫保留**了这两个文件。另外 `handoff.HANDOFF_OBSERVATIONS` 的守门要求
  每个 `id` 能在 PROJECT_PLAN §七 里 grep 到 `[P3-xx]` —— 而**本版的 §7 已清空**。
- **选项**：(a) 本版原样留着（读不到就说读不到，不产生错误信息，只是永远
  `available=false`）；(b) 把校准段与 `/eval/weekly` 一并下线，等 K9 的标定产物格式定了
  再建；(c) 现在就定 K9 版校准产物的格式（属标定侧工作，本 Plan ⛔ 不规划 whynotme）。
- **影响面**：`/eval/weekly`、`/review/overview` 的校准段、S12 App 复盘板块、
  `review/{handoff,research_artifact}.py` 约 500 行。
- **我的倾向（⚠ 倾向不是决定）**：(b)。一个永远 `available=false` 的段，比没有这个段更
  让人以为「系统那一步坏了」；而它现在渲染的字段名（Tier / C·Z·Y）已经指向不存在的东西。

#### B9 · 裁定 17 的**边界**：另外两条抓取路径要不要一起摘（第五组新发现）

- **事实**：裁定 17 逐字只说「移除 `scripts/daily_update.py` 里的 `ths_*` 抓取段」，
  已照办（那是**唯一挂在定时器上**的入口）。但仓里还留着两条**能抓**的路径：
  ① `scripts/backfill_concept.py`（106 行，无人 import、`__main__` 唯一入口、
  `END = "20260722"` 是**写死的过期日**，一跑就是 395 次连续调用）；
  ② `neckline/data/concept_data.py` 里的写函数 `update_ths_daily` / `update_ths_snapshots`
  （现已零调用方）。⚠ 本组**刻意保留**了 `concept_data.py`：裁定 17 要求「已抓的 parquet
  原地保留、将来解释层可拿概念当背景材料」，而删掉这个模块，那 21 MB 就没人读得动了。
  守门单测已锁住「读侧 helper 必须还在」这一半。
- **选项**：(a) 现状（只摘定时器那一段，读写 helper 与 backfill 脚本都留）；
  (b) 删 `scripts/backfill_concept.py`、并把 `concept_data.py` 收窄成**只读**模块
      （删两个 `update_*` 与三个 `ts_ths_*` 客户端函数）；
  (c) 整链退役，连 parquet 一起清 —— ⛔ **与裁定 17 明文冲突**，列在这里只为把选项摆全。
- **影响面**：(b) 会连带删 `tests/test_concept_data.py` 里约一半用例与
  `tests/test_tushare_client.py` 的三行降级夹具。
- **我的倾向（⚠ 倾向不是决定）**：(b) 的前半（删那个带过期硬编码日期的 backfill 脚本），
  后半保留写函数无妨 —— 但这已经越过裁定 17 的字面，所以 ⛔ 本组一个字没动。

#### B10 · 🔴 「行业分 / 选票分」的**同期窗口**没定义(第六组新发现,**S17 开工前必须定**)

- **事实**:K9 §八 把两个指标定义为「**行业分** = 该票所属申万二级行业**同期**表现」与
  「**选票分** = 该票**同期**表现 − 所属行业同期表现」——**「同期」是哪几天,K9 全文
  没说**。架构 §5.1 只说「票与行业的从属关系在写入时即冻结」,同样没给窗口。
  §8 的 **22 项待标定参数里也没有它**(那 22 项是选股与排序的旋钮,这一项是**成绩线的
  口径**)。⚠ 另外三个指标的窗口是明写的:兑现率 / 错杀率是 **D+1~D+4**(K9 §八 表格),
  成立率不需要窗口(它读 10:00 结算终值)。
- **为什么它不是「照着 D+1~D+4 抄一遍」就完了**:兑现率问的是「**摸没摸到**第一压力位」
  (窗口内取极值,窗口长一点只会更宽松);行业分 / 选票分问的是「**涨了多少**」
  (窗口长短直接改变数值本身,而且 D+4 收盘价 vs 窗口内最高价又是两个口径)。
  ⛔ 施工侧**未自行拍板** —— 这正是「定性需求不许自作主张定量」那一条。
- **选项**:(a) D+1~D+4 的**收盘涨幅**(与兑现率同窗口,口径最简单,但把「四天后正好回落」
  与「四天都没动」算成同一个数);(b) D+1~D+4 的**窗口内最高涨幅**(与兑现率同口径,
  但它会系统性偏高、长期看不出选票能力的下降);(c) 另立一个窗口(如 D+1~D+10),
  作为**第 23 项待标定参数**进参数包。
- **影响面**:S17 的 `scorecard/listing.py`、`k9_followups` 的回填形状(要不要多存几天)、
  `GET /api/v1/scoreboard/listing` 的响应、App 成绩板块那两栏的读数。
  ⚠ **回填形状被它决定** —— 选 (c) 而回填只存了 4 天,就得重跑历史,所以**要在 S17
  开工前定**,不能等到写渲染时再问。
- **我的倾向(⚠ 倾向不是决定)**:(a)。理由是这两个分要长期比的是「方向对不对 / 票挑得
  好不好」,**收盘价是唯一不带幸存者偏差的读数**;而 (b) 与兑现率共用口径反而会让两条
  指标高度相关、各自的信息量下降。⛔ 但这是个**定量决定**,请用户拍板。

#### B11 · 契约里两处 **snake_case 混进 camelCase 信封**(第六组登记,不影响运行)

- **事实**:全仓契约约定是「出参 camelCase」(`api/schemas.py` 文件头),但有两处透传件
  是 **snake_case**:① `GET /selection/{date}` 的 `structured.listing[]`(来自
  `k9_store.load_listing`,键是 `ts_code` / `sw_l2_code` / `primary_pattern` / `seat_kind` …);
  ② `GET /selection/{date}/stock/{code}` 的 `explain` 段(来自 `explain_store.load_notes`,
  键是 `kline_comment` / `news_state` / `llm_ok` …)。两处都是**领域层字典原样透传**,
  与「不在 API 层再镜像一套会漂的定义」那条惯例一脉相承,只是那条惯例此前透传的都是
  **自由结构**(`result` / `detail`),这两处透传的是**有名有姓的字段**。
- **现状不影响运行**:S12 的客户端用显式 `CodingKeys` 对齐了 `explain` 段;
  `structured.listing` 客户端**根本不读**(逐只摘要走本版新增的 camelCase `stocks[]`)。
  登记它是因为**下一个人会踩**:照文件头的约定去解,这两处会静默解不出。
- **选项**:(a) 现状 + 在 `schemas.py` 文件头把这两处列为明文例外(最省,零迁移);
  (b) 只把 `explain` 段转成 camelCase(它是**每次请求现拼**的,改它不动任何冻结件);
  (c) 连 `structured.listing` 一起转 —— ⚠ 那是 **`k9_reports.structured_json` 里冻着的
  内容**,改了之后老行与新行两种键形并存,报告 markdown 里内嵌的那段 JSON 也跟着变。
- **影响面**:(b) 动 `api/app.py` 一处 + 客户端 `K9ExplainNote` 的 `CodingKeys`;
  (c) 还要给存量 `k9_reports` 行定读回口径。⚠ v2.5.0 **尚未发版**,现在改 (c) 的代价
  是历史上最小的一刻 —— 但那也意味着现在不改就一直不该改了。
- **我的倾向(⚠ 倾向不是决定)**:(b)。`explain` 段是现拼的、改它零风险;
  `structured` 是**冻结件**,而冻结件的形状本来就该"写入当时什么样就永远什么样"——
  为了键的大小写去改一份审计快照的形状,与本项目一贯的做法相反。

### 13.2 本版不做，记着别忘

- **策略并行运行**（架构 §七第 2 条）：本版不实现（裁定 8）。将来引入 K10 时再做，
  届时 `k9_runs.strategy` 字段与「署名清单」的形状已经预留好了。
- **申万历史归属回补**（`index_member` 逐 L1 拉 31 次）：生产不需要（成绩线在写入时冻结绑定），
  whynotme 若要跑上线前的历史标定，自己拉。
- **`sw_daily` 行业指数行情**：本版明确不落（§3.2）。若将来有对照需求，注意 2014 版指数码混入问题。
- **`suspend_d` 历史回补**（本地只有 5 天）：标定跑历史时需要，属 whynotme 侧的输入准备。
- **`ths_*` 概念板块**：**已由裁定 17 处置**（停抓、保留已有数据，§2）。剩余边界见 §13.1-B9。
- **`neckline/__init__.py::__version__ = "0.1.0"`**：一个从没人读的陈旧版本号
  （真正的单一源是 `api/app.py::VERSION`，版本治理守门锁的也是它）。留着是第二个
  「看起来像版本号的东西」，S14 顺手删或改成读同一处。
- 阅读行为记录：**已关闭，不做**（裁定 9）。

---

## 14. 变更日志

| 日期 | 版本 | 记录 |
|---|---|---|
| 2026-08-20 | v2.5.0 立项 | K9 换引擎 + 新架构分层立项。V2.4.2 全文归档至 `archive/施工图/V2.4.2_施工图_20260820归档.md`。目标 Build 10，基线 v2.4.2 Build 9。 |
| 2026-08-20 | v2.5.0 立项收口 | 用户对 §7 四项给出处置：**批准 10:00 结算拍（裁定 10，且 9:29 ⛔ 不许输出「成立」）**、**批准 App 三板块 选股/成绩/复盘（裁定 11）**；`heatAbsentPolicy` 与 `relaySource`/`relayScoring` **降为取值待标定的参数位**（全部候选取值必须实现，§8.3 #18–#20）。§7 已清空，无待拍板项。待标定参数 17 → **20** 项；权威文件修订 16 → **17** 处（补架构 §四 边界例外措辞）。 |
| 2026-08-20 | **S0 完工** | commit `448adb5`。权威文件 17 处修订全部落地（whynotme commit `a93381c`）；版本收口 `v2.5.0` / Build 10，走 `App/scripts/prepare_release_candidate.sh 2.5.0 10`（`app.py::VERSION` + `project.yml` 两处 MARKETING / 一处 CURRENT + `xcodegen generate` 重生 pbxproj 四处 / 两处）。验收：pbxproj `MARKETING_VERSION = 2.5.0` × 4、`CURRENT_PROJECT_VERSION = 10` × 2；临时库起 API，`/api/v1/health` 返 `v2.5.0`。**与 Plan 不符，如实登记**：① §5.1 两条验收 grep 互相冲突 —— `grep 申万行业` 期望零命中，而 §5.1-A 第 2 行逐字要求写入「⛔ 不使用申万行业指数涨跌幅」，该句本身含「申万行业」；⛔ 未改写该句去凑 grep，故 `申万行业` 现剩 1 处命中，就是那句否定句（与 `行业指数` grep 的期望一致）。② §5.1-A 表漏列 K9.md §八「行业分 = 该票所属**申万行业**同期表现」，但验收 grep 要求零命中 → 按架构 §5.1 同句的改文一并改为「申万二级行业」。③ §5.1 未列 `App/project.yml`（2 处 `MARKETING_VERSION` + 1 处 `CURRENT_PROJECT_VERSION`），而守门单测 `tests/test_client_version_governance.py` 锁「app.py / project.yml / pbxproj」三方恒等 → 已一并改。④ §6 S0 写「16 处」，§1.2 / §5.1 / §14 写「17 处」，按 17 执行。⑤ `tests/test_v240_p4_release.py::_EXPECTED_RC_BUILD` 是写死的构建号（`"9"`→`"10"`），与同文件「⛔ 不在守门里写死版号」的自述不一致，本次只做最小改动，未重构。⑥ 三处**随附一致性改动**（非 §5.1 列举的 17 处，供用户复核）：K9 §3.4 正文「距关键位置的远近」→「上方机械空间的反向打分」（否则表已改、正文引用悬空）；K9 §3.7 补一段「命名铁律」明写两量分离（裁定 1 的落文）；架构 §九 引言「仍未决定的只有一项」→「原先仍未决定的最后一项，已于 2026-08-20 关闭」。 |
| 2026-08-20 | **S1 完工** | 三次提交：`eac2823`（搬家）/ `eaca2d1`（后端退役）/ `6592398`（App 持仓下线）。**规模**：后端删 106,734 行 / 270 个文件；App 删 6,493 行 / 18 个文件。**测试数逐条对上**：4082 → **707**（全绿，**0 skip**；基线那 19 个 skip 随 `test_selection_tier.py` 一并删除）。账：`4082 − 3155（删 120 个测试文件） + 13（新增 S1 守门） − 233（8 个存活文件里剔掉的退役用例） = 707`，逐位对得上。按退役对象：selection 链 −1311 / report K8 件 −473 / sentinel −370 / strategy 章程 −251 / auction K8 竞价层 −223 / 持仓与决策台账 −163 / scan −147 / custom_alerts −131 / review 双时钟 −51 / profile −35。⚠ `test_db_isolation_guardrail.py` 167 → 48 是**参数化基数变化**（它逐个测试文件参数化，162 → 43 个文件），不是覆盖面缩水。**与 Plan 不符 / 被迫提前，如实登记**：① **§4.2 与 §4.3 直接冲突**：§4.2 把 `report/industry_strength.py` 三件套与 `industry_strength_store.py` 的体例列为「骨架留用，⛔ 别推倒重来」，§4.3 又把 `industry_strength*` 列进删除单。处置：删 `industry_strength_store.py`（它是 `industry_strength_daily` 的**写路径**，S1 明令删写路径）；**保留** `report/{industry_strength,board_pool,sectors}.py` 三个纯计算件 —— `facts/limitmap.py`（自 `scan/cluster.py` 搬入）在用它们的 helper，S3 建事实层时一并退役。② 🔴 **`auction/` 整包被迫在 S1 删除，而 §4.2 说它是骨架留用**。原因：`collect.py` 的输入是 `sentinel/universe.load_watch_universe` + `selection.basket_store.BasketRef`，`pipeline.py` 依赖 `auction/{llm,mech}.py`，`quality.py` 依赖 `sentinel/capture`；K8 篮子与盘中关注池全删之后，「这一拍去抓哪些票的竞价」**没有输入源**，而它的新输入（K9 清单 + D0 冻结预案）要到 S6 / S10 才存在。⛔ 未自造一个中间形态。**S8 请从 `eac2823` 取回原件再改**：`git show eac2823:Backend/neckline/auction/pipeline.py`（9:26 起跑 / 9:29 硬截止 / 当日防重 / 窗口外零落库 / ⛔ 事后不补跑）、`collect.py`（竞价冻结抓取）、`quality.py`（双源报价校验）。⛔ 不要凭空重写。③ 同因，**`_morning_loop` 本片没有任何一拍**（§6 S1 写「先只保留竞价那一拍」）—— 只留节奏骨架 + 5 分钟待机 + 盘前 30 秒收紧，两拍位置与纪律已写进 docstring，S8 接。④ **`report/{pipeline,render,evening}.py` 与 `scripts/{evening,report}.py` 提前删除**（§6 S7 写的是「重做」）：它们与 K8 件是死结，拆不开。🔴 **连带损失，S7 必须补回**：`tests/test_weekend_report_schedule.py`（双日期契约 + 周日排程 + 休市跳过的唯一回归守门，LRN-20260816-001）随 `scripts/evening.py` 一并删除；原件在 `git show eac2823:Backend/scripts/evening.py`。`report/store.py` 保留未动。⑤ **`review/handoff.py` 被迫保留**：§4.3 把它列进「双时钟复盘」删除单，但 §5.12 明确**保留**的 `/eval/weekly` 与 `/review/overview` 两条路由靠它读周度校准产物。处置：保留文件，只删 `build_handoff` / `render_handoff` 两个函数（它们服务已删的 `/review/handoff` 路由，且 import `profile.store`），590 → 328 行。⑥ **`review/reconcile.py` 的 K8 章程判据（§5.9，原属 S11）提前在 S1 做**：`strategy/brain` 一删它就编译不过。连带：`WeeklyReview` 删掉 7 个字段、`weekly_review_dict` 删掉 8 个键、`review/material.py` 删掉对应文案段。⛔ **没留恒空的壳** —— 空的「本周违纪」会被读成「这周很干净」，而真相是「这项已经不判了」。`reviews.strategy_version` 列保留，新行写 NULL，历史行不回填。⑦ **`/review/overview` 从五段缩到三段**（`preference` / `capability` 随 `profile/` 退役，`selectionClock` / `tradeClock` / `iterationSuggestions` 随双时钟退役）。同上，不留恒空段。⑧ **§5.12 说持仓 6 路由，实际 7 条**（多一条 `GET /positions/{id}/plans`），已全删。共删 33 条路由；`api/app.py` 3077 → 816 行，`api/schemas.py` 100 → 22 个模型。⑨ **`tests/test_contract_crosscheck.py`（43 条，后端路由面 ↔ Swift 调用面机器对拍）删除**：客户端仍在调那 33 条已删路由，而客户端重做归 S12。🔴 **S12 必须重建这三条机器判据**（原件在 `eac2823`）。⑩ 其它一并删除但 Plan 未点名的：`llm/nl_alert.py`（随 `custom_alerts` 退役）、`App/Views/NoteSheet.swift`（随 `/decisions` 退役）、20 个 K8 驱动脚本 + 11 个 oneoff。`api/notify.py` 与 `notify_kinds.py` **未动**（不在删除单上，自身测试全绿），但其中 8 个 push 函数与一批 push kind 已无生产调用方，S7 / S8 接线时应顺手收口。⑪ 🔴 **`deploy/*.service` 未改，ExecStart 仍指向已删的 `scripts/{evening,report}.py`**。§9.3 把 unit 变更归 S14，且新段名要等 S7 定；⛔ 本次不部署，但**上云前必须先改 unit**。⑫ `scripts/smoke_api.sh` 已重写（退役面 14 条端点反向断言全 404），临时库实跑通过。 |
| 2026-08-20 | **S2 完工** | commit `8e4972d`。`data/tushare_client.py` 加 `ts_index_classify(src='SW2021')` + `ts_index_member_all(limit, offset)`;新 `data/sw_industry.py`(拉取 / 全量快照落库 / 自检);新表 `sw_industry_classify` `sw_industry_member`(**纯新增,零 ALTER**);挂进 `scripts/daily_update.py::update_sw_industry`,失败**打 ERROR**(判据输入,不是增强项)。**实测验证**(真 token,写**临时库**,⛔ 未触碰工作库;墙钟 1.4s):L1/L2/L3 = **31 / 134 / 346**;`index_member_all` **2 页拿全 5897 只**(offset 0 / 3000);`801125.SI` 存在、name = 「白酒Ⅱ」、level = L2;覆盖率对本地只读分区断言:**20260724 全市场 5526/5526 = 100%**(主板+创业板 4587/4587 = 100%),20260723 同为 100%;成分表里出现的不同二级行业数 = **131**(134 个二级里 3 个当前无成员,与 §4.5 的「4587 只落在 131 个二级行业」一致)。单测 21 条走假 fetcher + `tmp_path` 临时库,⛔ 不联网、⛔ 不落工作库。测试数 707 → **729**。**如实登记**:① 🔴 **§4.4 未记的一条实测事实**:`index_classify` 的 `parent_code` 是 TuShare 的 **`industry_code` 形态**(L1 = `'0'`,`801125.SI` = `'340000'`),**不是 `index_code`** —— ⛔ 拿它 join 本表 `index_code` 会静默 join 不上、不报错。已在 `db.py` 建表注释与模块 docstring 双处写明;层级关系一律读 `sw_industry_member` 的 `l1_code/l2_code/l3_code`(一行三层俱全)。② §6 S2 验收写「单测用 MockTransport / 假 client」——`tushare_client.py` 走的是 tushare SDK 的 `pro_api`,不是 httpx,**MockTransport 那条路不适用**;按「假 client」执行(两个 fetcher 全可注入)。③ `index_classify` **逐层各拉一次**(L1/L2/L3 三次调用)而不是一次拉全:不传 `level` 时各版本返回的层级集合不稳定,逐层拉才能让「哪一层几条」成为可断言的账(31/134/346 就是这么出来的)。三次调用的配额代价可忽略。④ `update_sw_industry()` **不接交易日参数** —— 接口只给**当前**归属快照。补跑历史日时它照样把表刷成今天的,这与 §5.3.5 事实包回填的语义差是同一件事;⛔ 别写「按 target 回改历史归属」的机灵代码。 |
| 2026-08-20 | **S3 完工** | commit `e89c1fa`。新增 `neckline/facts/{pack,store,industry,limitmap,completeness}.py`；新表 `fact_packs`（清单/指纹/缺口，**永不裁剪**）与 `sw_industry_daily`；新 parquet 表 `fact_pack`（41 列 = §5.3.1 的 40 列 + `trade_date`，25 个浮点列已在 `TABLE_FLOAT_COLS` 显式声明）；新脚本 `bootstrap_fact_packs.py` / `trim_fact_packs.py`；`daily_update.py` 挂上构建+冻结（数据未到齐 → ERROR 日志 + **不冻结**）。测试 729 → **743** 全绿。**实测**（开发机 Mac；SQLite 一次性只读副本 + 临时 parquet 根，上游六表 symlink 到真实只读分区，⛔ 未触碰工作库 / 生产）：单日 20260724 build **0.06s** / freeze **0.01s** / 5526 行 / **131 个二级行业** / parquet **634 KB** / RSS 峰值 **127 MB**；150 日 bootstrap 墙钟 **9.3s**（0.06 s/日）/ RSS 峰值 **185 MB** / 累计 **93.5 MB**；申万覆盖率 20260724 **5526/5526 = 100%**（与 §4.4 一致）。<br>**🔴 两个遗留的处置**：① `report/industry_strength.py` **整体退役物理删除** —— 它的 `_MIN_MEMBERS = 5` 是 §8.2 第 16 项待标定参数的硬编码值。职责由 `facts/industry.py`（申万二级中位数，**对每个有成员的行业都产出，无任何门槛**）+ 将来的 `k9/industry_heat.py`（读 `params.industry.minMembers`）接手。守门单测 `test_v250_s3_facts_guard.py` 双重锁死：全仓不许把「最小成员数」赋成数字字面量；该概念只允许作为**参数包字段**出现在 `neckline/k9/params.py`。② `facts/limitmap.py` **重写**：涨停簇由旧 `stock_basic.industry`（110 行业）切到**申万二级**（裁定 3）；概念板块锚点整块删除（K9 §3.0 / 架构 §3.1「概念板块不进入任何机械计算」）；按 §5.3.1 改为**纯函数不落表**（涨停簇摘要进 `fact_packs.market_json`；`limit_cluster_daily` 装的是 K8 口径旧行，按裁定 6 只读留档，⛔ 不把新口径的行掺进同一列）。连带 `report/{board_pool,sectors}.py` 一并退役（唯一消费方是概念锚点）。<br>**🔴 实测发现，需要用户拍一个口径（⛔ 施工侧未自行拍板）**：§4.6 说「`suspend_type='S'` 的票**天然不在 daily 分区里**」——**这句话只对「全天停牌」成立**。150 个交易日实测：`suspend_timing IS NULL`（全天停牌）**2001 行，0 行**出现在 daily（§4.6 完全正确）；`suspend_timing` 非空（**盘中临时停牌**，如 `'9:30-9:40'`）**36 行，35 行**出现在 daily，分布在 **25/150 天**（17% 的日子）。也就是说 §5.3.4 那条「S 类出现在 daily 就 WARNING + 排除出中位数 + 计数」的断言在真实数据上**不是异常而是常态**，而被排除掉的是**当天正常交易、只是盘中停过十分钟**的票。**本片严格按 §5.3.4 原文执行**（排除 + 计数 + 告警），⛔ 未自行改口径；同时把判别证据（`fullDay` / `intraday` / `intradayTimings`）原样冻进 `fact_packs.market_json.suspendAnomaly`，让那个决定有据可依。**待定问题：盘中临时停牌的票该不该计入行业中位数？**<br>**与 Plan 不符 / 未写清，如实登记**：① §6 S3 验收要求「`freeze_pack(IncompletePack)` 类型不通过（**mypy/单测双证**）」，但本仓 `.venv` **没有装 mypy**、也没有 mypy 配置，引入静态检查工具不在本片范围内 → 改用 **AST 注解断言**（直接读 `freeze_pack` 的形参注解，断言逐字是 `CompletePack`）+ 运行期 `TypeError` 双证。② §5.3.3 写「当日分区存在且**行数在合理区间**」，但 Plan 全文没给任何区间上下界，且 `limit_derived`（稀疏表）与 `suspend_d`（实测只有 5 只）天然可能 0 行 —— 给它们设行数下限等于把平静的日子判成故障。⛔ 未发明每张表的行数下限：实现为「稠密四表（daily / daily_basic / adj_factor / moneyflow_dc）非空 + 稀疏两表（limit_derived / suspend_d）存在即可」，并把**每个上游分区的实际行数**如实记进 `fact_packs.sources_json`。③ 同理 §5.3.3 的「`sw_industry_member` 刷新时间不落后于 **N** 天」里的 N，Plan 未给值 → ⛔ 未发明 N，改用两条不含阈值的判据：硬闸 = 成分表非空；事实记账 = 当日查无申万归属的票数记进 `market_json.swCoverage`，> 0 打 WARNING（同 §5.3.4「断言而不是假设」的处置）。④ §5.4.2 写 `load_pack_range(start, end)` 硬断言 `end <= trade_date`，但没说 `trade_date` 从哪来（回测里它不是墙钟）→ 改成**必填关键字** `as_of`，调用方必须显式说出「我现在站在哪一天」。⑤ 🔴 **`load_pack_range` 的 `columns` 改成必填**（Plan 只说「按声明集做列投影（**顺便**省内存）」）：实测 120 个交易日 / 659,239 行 —— 10 列投影 frame **53.6 MB** / RSS 峰值 **271 MB**（与 §3.2 估的 53 MB 对上），全 41 列 frame **185 MB** / RSS 峰值 **865 MB**；生产是 2 vCPU / 1.6 G、历史上 700M cap 就被 OOM-kill 过（§12 坑 1）。⛔ 不把那条红线做成缺省路径；要全部列请显式传 `columns=facts.pack.PACK_COLUMNS`。⑥ 事实包的行 = 当日 `daily` 分区的行（全市场横截面）。`daily` 里没有的票**不进包** —— K9 第一层第 6 条「停牌 = `suspend_flag=='S'` **或当日无 daily 行**」的后半句因此是结构性满足的；S6 的全市场 disposition 若要覆盖「一只票整天都没交易过」的情形，自己去 union `stock_basic`。⑦ §6 S3 产出清单里**没有** `facts/direction_llm.py`（§3.3 的包布局里有），架构 §十 也把它列为「独立于主线，可随时接入」→ 本片未做。⑧ `tushare_client.ts_suspend_d_all` 调接口时就传了 `suspend_type='S'`，**R 类记录根本不落地**，§4.6 那个「认 R 会误杀」的坑在当前数据路径上已被结构性避开；本片仍按 `suspend_type` 逐行映射而不是「凡在名单里就算停牌」，⛔ 别把这段「多余」的映射优化掉。⑨ `report/{board_pool,sectors}.py` 退役后，`data/concept_data.py` 与 `daily_update.update_concept_boards`（~400 次/周 + 5 次/日调用）**在机械链上已零消费方**。§13 Backlog 那条「相关日更是否保留由 S1 判断」现在可以真正判了 —— 本片未动它（不在 S3/S4/S5 范围内）。⑩ `tests/conftest.py::seed_synthetic_market` 里有一句 `seed_industry_strength(...)` 调用，而**那个函数在 S1 删测试时就已经不存在了**（靠 `test_review_reconcile.py::TestEntryScreens` 只剩夹具、没有用例才没炸）→ 随本片一并摘除；那个空的 `TestEntryScreens` 类仍在，归后续顺手清理。 |
| 2026-08-20 | **S4 完工** | commit `5f37d34`。新增 `neckline/scorecard/{coverage,store}.py`；新表 `k9_coverage_daily` + `k9_coverage_misses`（纯新增）；新端点 `GET /api/v1/scoreboard/coverage?window=`；挂进 `daily_update.py`（排在事实包冻结**之后**）；`smoke_api.sh` 加第 37 步。测试 743 → **777** 全绿；`smoke_api.sh` 临时库实跑通过。**实测**（150 天真实数据，临时库 + 临时 parquet 根，⛔ 未触碰工作库 / 生产）：墙钟 **4.8s / 150 天**（32 ms/天）；20260724 涨停 **43** / 跌停 24 / 炸板 17（炸板率 **28.3%**）/ 连板高度 **4** / **申万二级涨停簇 12 个**；分板块 MAIN 37 · GEM 4 · STAR 2，ST 2 只；涨停最多的二级 = 电网设备 4 / 半导体 3 / 汽车零部件 3。149 天分布：涨停家数 min 32 / **中位 81** / max 223；申万二级涨停簇个数 min 4 / **中位 20** / max 60。归因分布 13,039 条**全为 `no_listing`**（K9 清单要到 S6 才有，符合预期）。<br>**三条纪律的结构性保证**：① 🔴 `coverage_all` **不读任何待标定参数** —— `compute_day` 的签名只有 `(pack, listing, dispositions)`，**收不下**参数包；AST 守门断言 `scorecard/**` 零 import `neckline.k9`，源码里连参数名（`minMembers` / `heatAbsentPolicy` / `newListingDays` …）都不许出现。策略侧信息只经 `k9_disposition` 这条**数据**通道进来。② 🔴 **NULL 不是 0**：`coverage_all` NULL = 昨天还没有清单；`coverage_in_pool` NULL = 没有 D−1 disposition（边界参数缺失）。落表 / 读回 / API 三处逐条锁死，`smoke_api.sh` 也打印 `coverageAll=None` 提示。③ ⛔ **不回填历史覆盖率**：没有冻结事实包的日子不写行。<br>**与 Plan 不符 / 未写清，如实登记**：① §5.8.1 说「涨停普查 + **边界归因** + 涨停簇画像从第一天就出数」，但 9 条排除项里有 4 条要参数（次新股天数 / 流动性窗口与分位 / 冲高回落两个门槛 / 白酒的 `excludedL2Codes`），而**判定本身**无论要不要参数都是策略主张 → 本层⛔ **不判**「被第 N 条边界排除」，只做两件事：如实报出事实包里的**结构性事实**（板块 / ST / 申万二级，落 `census_json`），以及把 D−1 `k9_disposition` 里已写好的 `excluded_by` **原样转述**。「从第一天就出数」的那一半按结构性分布交付。② Plan **没写覆盖率挂在哪条链上**（§9.3 的晚间段序是 facts→k9→explain→playbook→report，没有 scorecard 段）→ 挂在 16:05 日更、紧随事实包冻结之后（它只读当日那一份冻结包，秒级动作，不值得新增一个段）。③ 漏检归因做成 **6 值闭合枚举**（`no_listing` / `no_disposition` / `excluded_by_boundary` / `not_recalled` / `recalled_not_seated` / `news_excluded`），AST 断言 `_attribute()` 不返回枚举外的字符串 —— Plan 只列了归因的四种语义，枚举值名是本片起的。④ ⚠ `k9_coverage_daily` 在 S3 那次提交里就已建表，但 `census_json` 列是 S4 才加的；`CREATE TABLE IF NOT EXISTS` **不会**给既有表补列。该表**从未在生产存在过**（v2.5.0 未发版，§9.2 本版全部 schema 变更是纯新增），故⛔ 未加 `_COLUMN_MIGRATIONS` 条目；本机若恰在 `e89c1fa` 那一刻建过库，`DROP TABLE k9_coverage_daily` 后重跑 `init_schema` 即可。 |
| 2026-08-20 | **S5 完工** | commit `1d1cac9`。新增 `neckline/k9/{__init__,params}.py`（参数包契约 + 显式校验）、`neckline/report/state.py`（`ReportState` 三值枚举 + **全映射**首行渲染）、`config/k9-params.example.json`。测试 777 → **889** 全绿。<br>**🔴 「无默认值」是结构性的，不是靠注释**：`K9Params` 与全部 12 个嵌套 dataclass 都是 `@dataclass(frozen=True)` 且**每个字段都没有 default**（守门单测遍历 `dataclasses.fields` 逐字段断言 `default is MISSING and default_factory is MISSING`）—— 少一个值就**构造不出对象**。`load()` 的 `path` 是必填位置参数，模块里⛔ 无任何默认路径常量。23 个键逐个删一遍，每次都必须点名到**那一个路径**（22 条参数化夹具）。<br>**🔴 三个「取值待标定」的参数位：全部候选取值都实现，⛔ 无默认分支**（§8.3 #18–#20 / §7.6 / G22）。`HeatAbsentPolicy`（renormalize / zero / drop）、`RelaySource`（recalled / shortlisted）、`RelayScoring`（binary / count）三个**闭合枚举**，解析走 `Enum(value)` 全映射。夹具：3 个 policy 各一条 + 4 种 relay 组合各一条 + 4 条「枚举外取值 → `invalid` 而不是静默退回默认」。守门用 AST 扫四种能造出默认的写法（`.get(x, 枚举成员)` / `x or 枚举成员` / 形参默认值是枚举成员 / `except` 里返回枚举成员 / 枚举定义 `_missing_`），全仓零命中，且扫描器自带自检（一个永远绿的闸门等于没有闸门）。示例配置里三个键一律 `"__TO_BE_CALIBRATED__"`。<br>**🔴 `empty` 与 `not_run` 不可互换**（裁定 5）：`resolve_state(pack_frozen=, params_ok=, listing_count=)` 三个入参全是**必填关键字**（⛔ 不给默认值去猜）；首行由三键全覆盖的 `_HEADLINE` 映射产出，模块加载时就 `assert set(_HEADLINE) == set(ReportState)`（漏写一个状态 = **import 就炸**），AST 断言 `report/state.py` 里不存在带兜底的 `.get(...)`。端到端夹具：缺 `ranking.relayScoring` → 首行 `今天没跑成 · 缺键 ranking.relayScoring`；同一天事实包已冻结 → `latest_pack` 仍指向昨天那一份、指纹与 pack_id 逐字不变（**保留上一份冻结结果**是结构性的：`fact_packs` 是 `INSERT` only）。另有一条夹具锁 §5.10 的「参数未配置的日子照样发报告」：市场事实（来自冻结包）与覆盖率成绩线照常，`not_run` 管的只是**清单段**。<br>**示例配置**：`config/k9-params.example.json` 由 `REQUIRED_SCHEMA` 程序化生成，**所有数值位一律 `"__TO_BE_CALIBRATED__"`**，⛔ 无任何真数字；唯一例外是 `industry.excludedL2Codes = ["801125.SI"]`（白酒Ⅱ 是 K9 §二 第 2 条**给定**的排除项，不是待标定，且按代码识别 §12 坑 6）。单测断言：示例与 `REQUIRED_SCHEMA` **结构逐键相同**（一边加键另一边忘了跟，用户照着填出来的包会当场判「参数未配置」），且**加载示例必然失败且失败得很吵**（≥10 条 invalid）—— ⛔ 绝不能出现「示例居然跑起来了」，那等于给了一组默认值。<br>**⚠ 与 Plan 不符 / 未写清，需要标定侧知道（⛔ 施工侧未自行发明）**：① 🔴 **`p2` 的「一字跌停判定」没有键名**：§8.1 第 4 项把「一字跌停 / 有效换手的判定」列为待标定，§5.4.5 只给了 `normDropMin` / `maDays` / `minTurnover` 三个键 —— `minTurnover` 是「有效换手」那一半，「一字跌停」那一半**既没有键名也没有形状**（按振幅？按 `high==low==limit_down_price`？按开盘即跌停 + 成交量？）。⛔ 未发明键名（那等于替标定方决定判据形状），`REQUIRED_SCHEMA` 里没有它。② 🔴 **`p3` 的 `notErupted*` 是 Plan 里的通配符**：§5.4.5 逐字写的就是 `p3.notErupted*`，§8.1 第 8 项也只说「『尚未爆发』的判定」待标定 → 同理⛔ 未发明。**①② 两项必须在 S6 开工前定下键名与形状**，否则四通道的定义性条件缺一块。③ `patternSubWeights` 的 8 个分项键名（`volMultiple` / `upsideRoomFar` / `relStrength` / `relStrengthShortfall` / `shortWindowImprovement` / `upsideRoomNear` / `inflowRank` / `volumeRatioRank`）是本片起的 —— §8.3 #17 只说「形态 1 三项 / 形态 2 一项 / 形态 3 两项 / 形态 4 两项」，§5.4.5 用中文列出了这 8 个量，这里逐个转成标识符，**是命名不是主张**。④ **「权重和」的目标值 Plan 没写**（§5.4.3 校验 2 只有「权重和」三个字）→ 按 §5.4.6「按 `patternSubWeights[pattern]` 加权求和 **∈ [0,1]**」反推为**和为 1**（容差 1e-6），三项主权重同样按和为 1 校验；不满足 → `invalid` 且给出精确原因。⑤ **未声明的多余键只告警不阻断**：缺键是 `missing`、取值不合法是 `invalid`，多余键两者都不是；参数包多一个键就报「今天没跑成」是误伤。⚠ **S6 起每个通道实际读到的键都必须进 `REQUIRED_SCHEMA`** —— 「标定了但没人读」才是真正要堵的漂移。⑥ **`ReportState` 与首行渲染放在新文件 `report/state.py`**：§3.3 的包布局里 `report/` 只列了 `pipeline / render / store / evening`，而 S7 要「重做」`render.py` —— 把三态守门放进一个会被整体重写的文件里等于把它交出去。`state.py` 是叶子，S7 直接 import。⑦ **G2 / G3（`k9/**` 的两条 import 边界）本是 §6 S6 的验收项，本片提前落位**：`k9/params.py` 已经存在，一条现在就成立的边界没有理由等到下一片；S6 请**扩充** `tests/test_v250_s5_params_guard.py`，⛔ 不要另起一份。⑧ `excludedL2Codes` 的存在性校验在 `sw_industry_classify` **为空时跳过**：那是**数据缺口**（归事实层的完整性判定），⛔ 不误报成「参数写错了」。 |
| 2026-08-20 | **裁定 12 返工(S3)** | commit `4316429`(Neckline)+ `9f8d6c6`(whynotme)。**只剔全天停牌**:`suspend_flag` 从三值扩成**四值闭合集** `none/S/I/R`,判别唯一实现 = `facts/pack.py::_suspend_flag_of`;`S` 起专指**全天停牌**(`suspend_type='S'` 且 `suspend_timing` 为空),`I` = **盘中临时停牌**(当天正常交易,**照常计入**中位数、**不被** K9 第一层第 6 条排除)。为什么把 `I` 做成同一列的一个取值而不是另加一列布尔:「停牌」在本系统有**两个**消费方(行业中位数、K9 第一层第 6 条),裁定 12 要求两处口径一致 —— 做成一列一值,下游写 `suspend_flag == 'S'` 就自动是对的;留成「S + 另看 timing」则每个消费方都要记得再 and 一次,而忘记的那次不会报错、只会把一只正常交易的票悄悄抹掉。`market_json.suspendAnomaly` 收窄成 `{total, codes, intradayCounted, intradayTimings}` —— **只对「全天停牌却出现在 daily」这一真异常告警**,盘中停牌记成常态事实(把常态记成异常等于让告警从此没人看)。🔴 **`PACK_VERSION` 从 `fp-1` 升到 `fp-2`**:改的是中位数本身的口径,§5.3.2 纪律 3 要求发新版本,⛔ 不在 `fp-1` 上静默重算。**单测**(`tests/test_facts_pack.py`,四条):全天停牌混进 daily → 排除 + 计数 1 + `suspendAnomaly.total=1`;盘中停牌 → **计入**(`member_count` 3 而不是 2)+ `suspend_anomaly_count=0` + `suspend_flag=='I'`;两类同日出现互不混淆;判别器四值闭合自检。**端到端**(`tests/test_k9_layer.py`):盘中停牌那只票 `excluded_by is None` 且被 p1 召回,全天停牌那只 `excluded_by='suspended'` 且零召回。**权威文件同步**:K9 §3.0 补三行判别表 + 150 日实测依据;架构 §3.1 两处「剔除当日停牌成员」→「剔除当日**全天停牌**的成员」+ 一段明确定义。 |
| 2026-08-20 | **S6 完工** | commit `691116f`。新增 `neckline/k9/{contract,volume,ranks,upside_room,boundary,industry_heat,ranking,quota,run,store}.py` + `channels/{p1_breakout,p2_rebound,p3_riser,p4_moneyflow}.py`;新表 `k9_runs` / `k9_channel_hits`(append-only)/ `k9_listing_entries`;新 parquet 表 `k9_disposition`。测试 892 → **994** 全绿。<br>**🔴 裁定 13/14/15 的落地**:①「成交量 ÷ N 日均量」**唯一实现** = `k9/volume.py::_multiple`,守门单测 AST 扫 `pl.col("vol").mean()` 全仓只此一处;② 两个新待标定键 —— **`volume.eruptionMultiple`**(裁定 15 的 V:p1 `≥ V`、p3 `< V`,**同一个值**)与 **`channels.p2.<档>.minVolMultiple`**(裁定 13 的有效换手门槛,**原 `minTurnover` 改名** —— 裁定 13 明写 ⛔ 不用换手率,旧名会把标定方引到错的量上);③ 互斥守门两条:AST 断言 p1 与 p3 **读同一个属性链** `params.volume.eruption_multiple`、且 `_CHANNEL_TIER_KEYS` 里没有任何带 `erupt` 的键;行为夹具把 V 在 3.0 上下扫过(2.0 / 2.999 / 3.001)断言那只票**恰好**在临界点换边,另一条对 6 个 V 值断言「没有任何票同时命中 p1 与 p3」;④ 一字跌停 = 开高低收**四价全等于跌停价**、**零参数**、按**整数分**比较(两个 2 位小数从不同路径算出来,浮点相等会在 1e-13 上翻车 —— 这正是 `limit_derived` 自己用整数分的理由)。<br>🔴 **V 为什么不分档**(⚠ 与 K9 §五-6「定义性条件中带数字的项设两档」的张力,**施工侧的结构判断,请标定侧复核**):V 是形态 1 与形态 3 的**分界点**,不是松紧旋钮 —— 调低它只把票从 p3 挪到 p1,`p1 ∪ p3` 的召回总量**一只都不会多**,放宽档在它身上没有意义;反过来若给两档,放宽档一开就会出现「放量倍数落在两值之间」的票同时命中 p1(放宽)与 p3(严格),裁定 15 要的「严丝合缝互补」当场破掉。p2 的 `minVolMultiple` 是真旋钮(调高 = 候选变少),照旧**分两档**。<br>**🔴 真实数据实测**(只读快照:SQLite 一次性 `backup` 副本 + 上游分区 symlink;⛔ 未触碰工作库 / 工作 parquet / 生产。⚠ `suspend_d` 本地只有 5 天(§4.4),为跑通 130 天回补了 **127 次** TuShare 调用,**落在私有临时目录**,⛔ 未写真实 `data/parquet/suspend_d`):申万成分实拉 **L1 31 / L2 134 / L3 346 · 5897 只 / 2 页**(与 §4.4 逐字一致);127 天事实包冻结墙钟 **20.3s(160 ms/日)** / RSS 峰值 **172 MB**;20260724 硬边界 **全市场 5526 → 池内 4090**,逐条 `科创板 610 / 白酒 19 / ST 195 / 北交所 326 / 次新 3 / 全天停牌 0 / 流动性 225 / 涨停 39 / 冲高回落 19` —— 其中**零参数的四条**(科创板 / 北交所 / ST / 白酒)是可复核的硬事实,**白酒 19 只与 §4.8 逐字对上**;其余五条含待标定参数,数字**只是夹具条件下的读数**,⛔ 不是标定证据。🔴 **容量红线实测(S14 定 `MemoryMax` 的依据,§9.3 明令「必须重新实测后再定,⛔ 不许照抄 1400M/900M/1000M」)**:60 个交易日 × 330,481 行 × 23 列 → frame **50.9 MB** / 读 **0.1s** / RSS 峰值 **370 MB**,整段 `compute` 墙钟 **0.72s** / RSS 峰值 **481 MB**;拉到 `MAX_LOOKBACK_PACKS=120` 的**上限**:659,239 行 → frame **101.6 MB** / RSS 峰值 **736 MB**。⚠ `neckline-basket.service` 现挂 `MemoryMax=900M` 且本版要在同一单元里跑 `k9,explain,playbook` 三段 —— **736 MB 的余量只有 18%,而 explain/playbook 还没进来**;上云前必须在生产同规格机器上重测(开发机是 macOS,RSS 口径与 Linux 不同)。<br>**⚠ 与 Plan 不符 / Plan 未写清,如实登记(⛔ 施工侧未自行发明数或策略主张)**:① **三处判据形状 Plan 与 K9 都没给公式**,本片各取一个**零新增参数**的最省事读法,**请标定侧复核**:p1 的「过去 N 天振幅」取**窗口极差** `(max(high) − min(low)) / min(low)`(理由:画像是「横盘很久」,说的是整段区间的宽窄;⛔ 不是逐日 `amp_1d` 的均值 —— 那个量在「每天小幅震荡但一路走高」的票上照样很小,而那种票恰恰不是横盘);p3 的长窗 / 短窗相对强度取**逐日 `rel_strength_1d` 的累计和**,「在改善」= 短窗和 > **紧邻的上一个等长窗口**的和(唯一不引入新参数的比法,代价是要 `2 × shortWindow` 天历史);p4 的三个排名一律取**百分位 ∈ [0,1](1 = 最强)**而不是绝对名次(§5.4.5 写的减法方向要求「大 = 强」;绝对名次会随当日票数漂移 —— 全市场 5526 只 vs `moneyflow_dc` 5749 只,同一个 `lagRankGap` 在不同日子意思不一样),`lagRankGap` 因此也是 0~1 的差值。② **横截面统计的分母**:K9 第一层第 7 条的流动性分位按 K9 原文取**全市场**;四通道的横截面排名取**硬边界之后的池内** —— K9 §二 开宗明义「以下情形当日直接排除,**不进入任何形态召回**」,而一次排名就是召回的一部分,把当日涨停的票留在分母里等于让被排除的票继续影响谁被召回。③ **流动性分位改走名次不走数值分位点**:`quantile()` 配 `<=` 在大量并列的分布上会一口气排掉远超 `bottomPct` 的票(合成夹具里全市场成交额相同 → 整个市场被判流动性过弱)。④ **跨日接力分的 `count` 归一**用「当日候选里的最大计次」—— 三项分必须同量纲才能加权,而「最多被接力几次」没有先验上限。⑤ **形态内强度分缺读数时按剩余项重新归一**,⛔ 不按 0 分算(0 分等于宣称「这项它最差」,而真相是「这项没读到」)。⑥ 🔴 **`k9_disposition` 的 parquet 由 `k9/store.py` 直接写**:§3.3 把它归 `k9/store.py`,而 G3 又禁止 `k9/**` import `market_data`(`write_table_day` 在那里)。处置:**保住 G3**,自带一张显式 schema 每次照造(比 §12 坑 2 的「向既有分区看齐」更强 —— 根本不看齐任何分区),同时把表名与浮点列登记进 `market_data.{_VALID_TABLES,TABLE_FLOAT_COLS}` 供**读侧**用;两条守门:路径拼法与 `day_file_path` 逐字对拍(测试可以 import,生产路径不行)、两处 dtype 声明逐列一致。⑦ **`p3` 的「在改善」要 `2 × shortWindow` 天**,而 §5.4.3 的窗口校验只逐键比 `MAX_LOOKBACK_PACKS`、看不见这个乘 2 → `run.py::required_lookback` 再判一次,超了 = 参数配置无效 = 「今天没跑成」,⛔ 不静默截断窗口。⑧ **量比的 5 日窗口是常量不是参数**(`volume.VOLUME_RATIO_MA_DAYS = 5`):K9 §3.5 原文「全天成交量 ÷ 过去 5 日均量」是**量比这个指标的定义**,换成别的天数算出来的就不叫量比了;⛔ 与 `volume.maDays`(放量倍数的分母窗口,按 §8.4 体例作为参数位转录)不是一回事。⑨ **`k9_runs` 加了一列 `listing_finalized_by`**(`'k9'` / `'explain'`):§5.5 规定清单在**解释层之后**定稿,而解释层是 S9 的产物 —— 这一列让「这份清单还没过消息面」成为**查得到的事实**而不是一句注释,报告里也照直说。S9 接入后由编排器改传 `'explain'`,⛔ 不许删掉这一列或恒填 `'explain'`。⑩ **单位约定有坑,标定侧必读**:名字带 `Pct` 的键分两组 —— 落在 `_UNIT_INTERVAL_PATHS` 的是**比例**(0~1,如 `liquidityBottomPct=0.2` 表示后 20%),其余是**百分点**(如 `ampMaxPct=25.0` 表示 25%)。两组各自有区间校验,`contract.to_percent_points()` 是唯一转换处。⑪ **S3 守门单测 `test_the_hardcoded_min_members_threshold_is_gone_from_the_whole_tree` 的判据从「只许出现在 `k9/params.py`」放宽为「只许出现在 `neckline/k9/`」**:S6 起策略层不止一个文件(`k9/industry_heat.py` 就是那个按 `minMembers` 判够不够格的地方),而该条守的本来就是「⛔ 不许出现在事实层」—— docstring 原文即如此。⑫ **测试夹具走「造一次、之后拷贝」**(`tests/k9_env.py`):70 个交易日 × 逐日冻结要 4.2 秒,而本组十几条用例都要这份市场 → 首次造完把 `data/` 留成模板,之后每个测试**拷贝**一份(0.08 秒),各测试仍拿到自己的库与 parquet 根,⛔ 不共享可写状态。⑬ **G11 的「全仓 grep 零命中」有两处豁免**(与 §6 S6 验收字面不符,同 S0 登记 ① 那类冲突):`PROJECT_PLAN.md`(§5.1 的措辞修订表逐字引用**改之前**的原文,改掉引文那张表就没法核对了)与守门单测自身(要搜一个字符串总得先写出它)。豁免清单写死在测试里,另有一条「排除清单不能把整个仓库排空」的自检。 |
| 2026-08-20 | **S7 完工** | commit `87f651e`。新增 `neckline/report/{pipeline,render,evening}.py`、重写 `report/store.py`、新建 `scripts/evening.py`;新表 `k9_reports`(**新表,⛔ 不往旧 `reports` 上加列**);新端点 `GET /api/v1/selection/latest` 与 `/selection/{trade_date}`(smoke_api.sh 第 38 步,S1 留的那句「S7 落 `/api/selection/latest` 后在此补新步骤」现已兑现)。测试 994 → **996** 全绿。<br>**🔴 双日期契约守门的恢复证据**(S1 登记 ④ 点名要求):`tests/test_weekend_report_schedule.py` 从 `eac2823` 取回并按 K9 报告链改写,**四条契约断言逐字保留** —— 周日槽绑定紧邻上一周五 / 两个日期都传下去(`report_date=周日` + `trade_date=周五`)/ 该周五休市**安全跳过**(⛔ 不回退到周四重发旧报告)/ 同日已生成**整链跳过**(⛔ 零重复 APNs)。改写只动三处:段名(`verify,scan,basket,review,report` → `facts,k9,explain,playbook,report`)、防重查的表(`reports` → `k9_reports`)、参数开关(`--direction-pipeline-config` → `--k9-params`)。**另加三条**:空库 / 无表时**不跳过**(⛔ 别把「查不到」读成「已经跑过」)、三个 oneshot 单元**不重不漏**覆盖新段序一次、跑策略层那个单元必须显式传 `--k9-params`。10 条全绿。<br>**🔴 冒烟当场抓到两个 bug,都已修并各有回归用例**:① **报告自己再 load 一次参数文件** → `neckline-report.service` 只跑 `--segments report`(它不拿也不该拿参数路径),于是在策略层明明跑出 5 只的日子里宣布「今天没跑成」,**同时又把那 5 只落进了库** —— 一份自相矛盾的报告。修法:「参数有没有配好」的**权威是 `k9_runs` 那一行**(有运行账 = 那天确实拿着一份已校验的参数包跑过),报告描述的是**这一天**不是**这一次调用**;上游 k9 段自己知道为什么没跑(参数未配置 / 参数无效 + 逐条缺口),由它把原因**带下去**,⛔ 报告不猜别人的失败原因。② **报告 markdown 无视 `--db`**,把一份合成数据的报告写进了真实 `Backend/data/reports/`(已删除该文件)—— `REPORTS_DIR` 是模块级常量。修法:`--db` 指到哪,markdown 就落在哪一份 `data/` 旁边(AGENTS.md:测试与冒烟⛔ 不许往工作目录落任何东西)。<br>**三态渲染快照**(临时库实跑):`has_list` 首行 `# 今天有这些 · 5 只(严格 5 / 放宽 0)`;`empty` 首行 `# 今天没有`;`not_run` 首行 `# 今天没跑成 · 参数未配置(…)`。参数未配置那一份**仍有**方向背景 + 市场事实 + 覆盖率三段(§5.10 逐字),`listing_size` 落 **NULL**(⛔ 不是 0)。<br>**⚠ 与 Plan 不符 / Plan 未写清,如实登记**:① **删掉了 `report/store.py::save_report()`**(K8 `reports` 表的写路径):§5.10 说旧表**冻结只读留档**,而留着一个「谁都能调回去」的写路径等于让那条纪律靠自觉维持;表本身**不 DROP、不迁移、不回填**,历史行仍可读。连带 `tests/test_report_store.py` 里围绕它的 ~15 条往返 / 幂等 / V1 快照保护用例一并删除(⛔ 不留 skip 掉的僵尸测试),换成一条「写函数确实不在了」+ 一条「历史行仍读得回来」。② **`explain` / `playbook` 两段是 `not_built` 而不是 `ok`**:给还没建的层一个绿灯,等于让报告宣称清单已经过消息面剔除。③ **`deploy/{scan,basket,report}.service` 的 `ExecStart` 已改**(段名 + `--k9-params`):§9.3 把 unit 变更归 S14,但 S1 登记 ⑪ 说「上云前必须先改 unit」,而恢复的守门单测要拿这三行对拍新段序 —— **只改文件,⛔ 未部署、⛔ 未 ssh**。🔴 **`--k9-params /opt/neckline/config/k9-params.json` 这个路径下的文件还不存在**(参数待标定),上云前要么由用户放入标定完的包、要么这一段每天出「今天没跑成 · 参数未配置」(§9.5 说这是**设计行为**)。④ **`MemoryMax` 三个数一个都没改**:实测在开发机上做(见 S6 行的容量红线),生产同规格实测归 S14。⑤ **方向背景段**:`facts/direction_llm.py` 至今未建(S3 登记 ⑦),报告如实写「未接入」,⛔ 不编一段方向解读。⑥ **覆盖率的接线**(`report/evening.py::coverage_inputs`)住**编排器**:守门断言 `scorecard/**` 零 import `neckline.k9`,策略侧信息只能经 `k9_disposition` / `k9_listing_entries` 这条**数据**通道进来。已挂进 `daily_update.refresh_coverage`,端到端夹具锁死「跑了 D−1 的策略层 → D 那天的 `coverage_all` 从 **NULL** 变成一个真数字」(S4 登记那句「清单开始产出的次日自动接上」的证据)。⑦ **§5.12 的 8 条新路由本片只落 2 条**(`/selection/latest` + `/selection/{date}`):个股详情要解释层资料 + 预案(S9/S10),核对表与成绩线归 S8/S17;⚠ §5.12 没把这些路由分配给任何切片,S12 之前必须有人认领。 |
| 2026-08-20 | **概念板块日更核实(S6/S7 组附带)** | 🔴 **结论:`ths_*` 概念板块日更在全仓已零消费方,证据确凿;⛔ 本组未删,等用户点头**(它动 `scripts/daily_update.py` 的抓取段)。**证据**:① 载体是**扁平 parquet 不是 SQLite**(`data/parquet/ths_{index,member,daily}.parquet` + `ths_snapshot_meta.json`,共 ~21 MB)——⚠ §13 Backlog 那条写的是「表」,实际没有任何 SQLite 表名含 `ths_`/`concept`(唯二相关的是 `limit_cluster_daily.anchor_concept` 与 `app_settings.intel_watch_boards`,两者都已按裁定 6 只读留档,⛔ 不 DROP);② **写方三处**:`neckline/data/concept_data.py`(374 行,唯一生产 import 方 = `scripts/daily_update.py:64`)、`daily_update.update_concept_boards`(调用点 `:265`,**零测试覆盖**)、`scripts/backfill_concept.py`(106 行,**无人 import、`__main__` 唯一入口、`END = "20260722"` 是写死的过期日**);③ **读方零**:所有 `read_parquet`/`scan_parquet` 命中都在 `concept_data.py` 自己内部;它 docstring 里点名的三个读侧(`report/sectors.py` / `board_pool.py` / `intel_candidates.py`)**已在 `e89c1fa` 物理删除**;`facts/` `k9/` `scorecard/` `report/` 对概念板块的全部命中都是**反向声明**(如 `facts/limitmap.py` 头「概念板块锚点整块删除」),且 `tests/test_facts_limitmap.py` 有一条守门断言 `anchor_concept` 不再出现;④ **配额账**:`ths_daily` **5 次/日**(尾窗 5 天,一次取全板块)+ `ths_index`+`ths_member` **395 次/周**(1 + 394 个板块逐个拉,`ths_index.parquet` 实有 394 行)≈ **~21,750 次/年**;⚠ 那 395 次是**连续**调用,几乎占满客户端 450 次/分限频窗口一整分钟。**处置建议(供用户裁定)**:整链退役 —— 删 `data/concept_data.py`、`scripts/backfill_concept.py`、`tests/test_concept_data.py`、`daily_update.py:60-93` 与调用点 `:265`、`tushare_client.py` 的 `ts_ths_{index,member,daily}` 三个函数与 `__all__` 条目、`tests/test_tushare_client.py:89-91`、`tests/conftest.py:345-350` 的夹具写入;`data/parquet/ths_*`(21 MB)可一并清理;`anchor_concept` 与 `intel_watch_boards` 两列**保留不 DROP**(裁定 6)。⛔ 未执行。 |
| 2026-08-20 | **S8 完工** | commit `fa294af`。新增 `neckline/auction/{__init__,quality,collect,checklist,settle,pipeline,store}.py` 与 `neckline/playbook/{__init__,model,evaluate,store}.py`;新表 `k9_checklists` / `k9_d1_verdicts` / `k9_playbooks`(**纯新增**);新端点 `GET /api/v1/checklist/{date}` 与 `GET /api/v1/scoreboard/verdicts/{date}`;`_morning_loop` 两拍齐全。测试 996 → **1051** 全绿。<br>**🔴 裁定 10「9:29 不许出现成立」的三重结构性锁**(⛔ 不是靠运行期判断躲开):① **类型层** —— `ChecklistVerdict` 是二值枚举 `{rejected, pending_open}`,「成立」不是一个取值,模块加载时 `assert len(...) == 2`,加第三个成员 = **import 就炸**;② **求值层** —— `checklist.py` **只碰** `playbook.rejection_branch`,守门单测掐掉 docstring 后 AST 扫源码,`confirmation_branch` / `settle_verdict` / `CONFIRMED` **零命中**(够不着,不是「记得别碰」);③ **落库层** —— `store.record_auction_stage()` 收的是二值枚举,`_AUCTION_FINAL` 是**两键全映射** `{rejected → Verdict.REJECTED, pending_open → None}`,`Verdict.CONFIRMED` 在这条写路径上**根本构造不出来**。另有第四道行为锁:9:26 那一拍的读数表(`auction_readings`)**刻意不提供** `open_price` / `gap_pct` / `first30_high`,就算有人把成立分支拿去求值也只得到 `UNKNOWN`。<br>**🔴 三分支唯一权威 = 10:00 结算拍**:`settle.py` 写 `verdict` + `decided_stage='open30'`,幂等条件 `WHERE decided_stage IS NULL` —— **9:29 已判「放弃」的票结构上改不动**(夹具:那只票 10:00 反弹回来、成立分支读数全满足,照样仍是 `rejected`/`auction`,`settled_at` 仍为 NULL,竞价读数逐字未变)。零推送有两条守门:`settle.py` 零 import `neckline.api.notify` / `neckline.push`(AST),`SettleRunResult` **没有** `should_push` 字段;`_morning_settle_tick` 里一行 `notify` 都没有。<br>**🔴 零新增 systemd unit**:守门单测把 `deploy/` 下 8 个 unit 的文件名逐个对拍成一个**精确集合**,并扫全部 unit 正文里 `checklist` / `settle` / `auction` 零命中。两拍都在既有常驻 `neckline.service` 的 `_morning_loop` 里,各自独立 `try/except`(守门断言那段循环里 ≥3 个 `try` 且两个 tick 函数都被调到)。<br>**`auction/` 三件的取回与改造**(自 `git show eac2823:Backend/neckline/auction/*`,⛔ 未凭空重写):`quality.py`(601 行)**七项校验、零容差裁定 #2、`degraded` 那一档的理由、`_is_cross_verified` 判别式一字未动**,只改三处 —— 常量源换到本包新 `__init__.py`(K8 的 verdict / clamp 语义**一个都没取回**)、四类结论性冲突里需要 D0 **K8 卡**的两类合并成需要 **D0 K9 冻结预案**的 `rejection_disagree`(⛔ 只比「放弃」分支:9:26 判不出成立,比成立只会得到两边都 `UNKNOWN` 的假安心)、涨跌幅算式改调 `playbook/model.py::gap_percent_points`(全包唯一源)。`collect.py`(616 行)保住「自己拉一次价 / 拉价前用真实时钟复判窗口 → 越窗**一条价都不拉、零落库** / `captured_at` = 真正拉完价那一刻 / 拉价跨窗 → 降级不丢读数 / 拉价失败不掀翻本层 / `missing` 与 `invalid` 分两栏」,删掉 T1/T2 篮子、盘中关注池、独立观察池、竞价强势股、板块对照股、三支市场对照指数(K9 的核对表是**纯条件求值**,不需要市场环境)。`pipeline.py`(373 行)保住窗口门 / 当日防重 / **⛔ 事后不补跑** / 「当日已跑」标记落在**落库之后**(中途异常下一拍干净重跑),**按 §5.7.1 明令把 daemon 线程那套删掉** —— 那是为兜住 LLM 的不确定墙钟(实测同一份输入两次差 1.44 倍)而生的,零 LLM 之后没有要兜的东西,换成一句朴素墙钟保护(落库前复判,过 9:29 就记 `deadline_missed` **不发布**、也不落已跑标记)。⛔ `llm.py`(489 行)/ `mech.py`(1651 行,Z1/Y1/C1 三道夹逼闸)/ `observation.py`(272 行)**未取回**,守门断言这三个文件不存在且 `VERDICT_CONFIRM` / `clamped_by` / `BasketRef` 等 K8 语义在整包零命中。<br>**⚠ 与 Plan / K9 不符或未写清,如实登记(⛔ 施工侧未自行发明数或策略主张)**:① 🔴 **权威文件里还有两句与裁定 10 直接冲突,本组⛔ 未擅自改**:`K9.md` §七 仍写「输出一张核对表:**哪几只已触发成立**、哪几只已触发放弃、其余待开盘后观察」,`Neckline新架构_20260818.md` §四「输出」段同句。§5.1 的 17 处修订清单里**没有这两句**(只列了架构 §四 **边界段**的明文例外),而 §1.2 明写「不改,后来者照文件施工就会做回错的口径」。**这是一处会绊倒下一个人的活坑,请用户裁定是否补这两句的措辞**;⛔ 本组没有动用户的策略原文。② 🔴 **`MetricRef.FIRST30_LOW` 在 9:26 那一拍绑到竞价价 —— 施工侧的结构判断,请策略侧复核**:K9 §6.3 的四条「放弃」全是「跌破 X」,而 §5.7.2 逐字说「放弃分支四个全是单条破位判定 → **竞价价就能触发**」;§5.6.3 的闭合枚举里能承载「跌破」的只有 `first30_low`。故把它的语义定义为「**本场至今的最低价(含 9:25 竞价成交)**」:9:26 时本场只有一笔竞价成交,那个最低价**就是**竞价价;10:00 时它是 `Quote.low`(Plan §5.7.2 逐字:10:00 的 high/low 即前 30 分钟极值)。它是**单调下行**的量 —— 9:26 已经跌破的价位 10:00 一定仍然跌破,这正是「先到先定、⛔ 不改判」在语义上站得住的原因。⛔ 未新增第 10 个枚举成员。③ 🔴 **K9 §6.3 形态 2 的「跌破昨日最低价 [B]%」落成了一个价位而不是百分比**(见 S9+S10 行的登记 ②,两片共用同一条理由:§5.6.3 的条件语法闭合、**没有算术**)。④ **`k9_d1_verdicts.verdict` / `decided_stage` 为 NULL = 「今天还没定案」,⛔ 不是「观察」** —— 「观察」是 10:00 真看过之后的结论,它带着 `decided_stage='open30'`。两者在表、API、冒烟三处都分开说。⑤ **两拍的防重事件码刻意与 K8 不同**(`checklist_tick` / `settle_tick`,⛔ 不复用 `tick`):老库 `sentinel_events` 里那些行是 K8 竞价确认层留下的,用同一个码会让「今天这一拍跑没跑过」在历史行上撞车。表名仍是 `sentinel_events`(§3.2 定死)。⑥ **`push_auction_summary` → `push_checklist_summary`**(K9 两段措辞,恒带脚注「成立由 10:00 结算,9:30–10:00 由我自己判定」,守门断言正文里「成立」只出现这一次);**`push_precall_summary` 随 `sentinel/precall.py` 退役一并删除**(S1 登记 ⑩ 点名要求 S8 顺手收口:它的四类计数已经没有任何东西能产出)。两者都**沿用 `KIND_PRECALL`、零新 kind**,`ALL_KINDS` 冻结元组一字未动。⚠ 2026-08-11 那条拍板原本的代价(盘前校准与竞价确认共用一个开关)**随盘前校准退役自动消失**。⑦ **推送门槛与 K8 刻意相反**:K8 是「平静的早晨不发」(它推的是**提醒**),K9 是**每个有清单的交易日都推一条**(它推的是**当日清单的核对结果**,「今天一只都没触发放弃」本身就是用户 9:30 要用的信息)。⛔ `ran=False` 恒不推;清单为空也不推。⑧ **`playbook/{model,evaluate,store}.py` 提前在 S8 落地**(§6 把 `playbook/` 整包归 S10):§5.7.1 明令「`playbook/evaluate.py` 作为**唯一**求值器(两拍共用)」是 S8 的产出,而求值器需要 DTO 与读回路径。`skeleton.py` / `fill.py` 留给 S10。⑨ **`GET /scoreboard/verdicts/{date}` 与 `GET /checklist/{date}` 两条路由由本组认领**(S7 登记 ⑦ 说 §5.12 没把 8 条新路由分配给任何切片)。⑩ 🔴 **收尾自查抓到一个真洞并已修**(commit `5a52496`):§5.7.3 写的「非窗口时段 5 分钟一探」撞上**正好 5 分钟宽**的结算窗口(10:00–10:05)会**整窗错过** —— 相邻两次探测可以一次落在 9:58、下一次落在 10:05:30,而「今天跑没跑过」于是变成一道看运气的题。S1 留下的 `_is_preopen`(只收紧 9:20–9:30)换成 `_is_tight_poll`(9:20–9:30 与 9:55–10:06 两段都 30 秒一探)。判据的单一源仍在两个 tick 自己那里,这里只决定**多久探一次**。守门单测从窗口左端前一瞬起按实际间隔推进,断言最坏情况下仍至少落进窗口两次。测试 1119 → **1120**。 |
| 2026-08-20 | **S9 + S10 完工** | commit `7fe0b57`。新增 `neckline/explain/{__init__,input,news_exclusion,aggregate,store}.py` 与 `neckline/playbook/{skeleton,fill}.py`;新表 `k9_explain_notes` / `k9_explain_audit`(纯新增);新端点 `GET /api/v1/selection/{date}/stock/{code}` 与 `POST .../playbook`;晚间链 `explain` / `playbook` 两段**真的会跑**(此前是 `not_built`)。测试 1051 → **1119** 全绿;`smoke_api.sh` 加 39–42 步,临时库实跑通过。<br>**🔴 S9 双盲的结构性证据**(架构 §3.3「解释层收到的输入不含通道身份与排序位次」):① `ExplainInput` 里**根本没有** `patterns` / `channel` / `rank` / `score` / `tier` / `seat_kind`(不是「有但不填」),字段集冻结成 `EXPLAIN_INPUT_FIELDS` 并逐字断言,另有 8 条参数化夹具逐个词根扫「字段名里不许出现它」;② AST 断言 `explain/**` **零 import** `neckline.k9`;③ **列表顺序也会泄漏位次** —— `build_inputs()` 一律按 `ts_code` 升序返回(夹具:正序与逆序传入拿到同一个序列);④ **AST 判据**(⛔ 不是文本扫描)断言解释层**没有真的去读** `reserve` / `rank` / `score` / `seat_kind` / `primary_pattern` / `patterns` / `tier` —— 文本扫描会被 `EXPLAIN_INPUT_FORBIDDEN` 那个**黑名单常量**本身误伤,「声明了什么不许有」与「真的去读了它」是两件事;扫描器自带自检(它**扫得到**编排器确实读了 `shortlist.reserve` 与 `e.rank`);⑤ 运行时证据:喂给模型的材料里「排名 / rank / score / seat / 形态 / 通道」逐个零命中。**补位决定住在编排器 `report/evening.py::_run_explain`**(它才知道名次),守门断言那段代码确实读了 `shortlist.reserve`。<br>**🔴 S10 预案「结构化可机械求值」的证据**:`MetricRef` 是**恰好九个成员**的闭合枚举、`Op` 四个、`BranchName` 两个、`default` 恒「观察」;`Condition` 只有 `{op, lhs, rhs}` 三位,**语法外的键当场抛**;`rhs` 只能是有限数值或另一个 `MetricRef`(⛔ 没有算术);未知 `MetricRef` → `parse_playbook` **D0 当场拒绝冻结**;`Condition`/`Branch`/`Levels` 三个 dataclass 里**一个自由文本位都没有**;三个价位次序(失效位 < 第一压力位 < 第二压力位)是硬校验。**LLM 返回体只收数值**:键集**恰好等于** `required_keys(pattern)` —— 少一个 = **空成功判失败**(§12 坑 13 / LRN-20260816-002),多一个 = **拒绝**(⛔ 不是「忽略多余的」:忽略等于默许它下次塞得更多),值不是有限数值 = 拒绝。**用户修改入口的契约**:`POST /api/v1/selection/{date}/stock/{code}/playbook`,请求体只收那几个数值键(键集由该票 `primary_pattern` 决定,422 的 detail 里逐个列出),**append-only 写新版本、原版本一个字不改**(夹具:v1 的 `first_resistance` 改完之后仍是 11.0,`source` 仍是 `llm`;两拍读 `max(version)`);`k9_playbooks` 主键含 `version`,守门扫本文件的 SQL:`UPDATE` / `DELETE` / `INSERT OR REPLACE` 零命中。**骨架不可改**:用户能改的是方括号里的数,不是「哪个量跟谁比」。<br>**🔴 内存实测(S14 定 `MemoryMax` 的依据;开发机 macOS,一次性临时目录,⛔ 未触碰工作库 / 工作 parquet / 生产)**。⚠ **走的是合成市场不是真实行情,如实说明**:`fact_pack` 的 parquet 不在工作目录里(S3/S6 建在各自私有临时目录、跑完就没了),重建它要 `suspend_d` 的历史分区而本地只有 5 天(S6 那次为此打了 127 次 TuShare)。本组按**生产规模**造包(**5526 行 × 41 列 × 120 天**,与 §14 S3 实测的 5526 行同量级)。读数:<br>　· `explain` **独立**峰值 **192 MB**(基线 110 MB,**增量 82 MB**);`build_inputs` 60 天 × 20 只墙钟 **0.31s**,单只 prompt **3,747 字符**;<br>　· `playbook` 接着跑,进程峰值 **212 MB**(相对基线 **增量 101 MB**,相对 explain 之后 **+19 MB**);`fill_for_listing` 20 只墙钟 **0.39s**,单只 prompt **3,835 字符**;<br>　· 🔴 **三段合并(`k9,explain,playbook` 同一进程)**:先复现策略层的读峰(120 天 × 663,120 行 × 23 列投影 → frame **85.8 MB** / RSS 峰值 **532 MB**),再跑 explain 与 playbook —— **进程高水位仍是 532 MB,两段各自 +0 MB**。<br>**结论(⛔ 施工侧未改任何 `MemoryMax`、未拆 unit,那要用户点头、归 S14)**:**explain / playbook 不会把三段合并的峰值顶上去** —— 它们的工作集(60 天 × 20 只 ≈ 1,200 行)相对策略层那份 66 万行的 frame 可以忽略,而进程高水位早已被策略层设定。S6 在**真实数据**上量到的策略层 **736 MB** 因此仍是那条红线本身,余量仍是 `neckline-basket.service` 现挂 `MemoryMax=900M` 的 **18%**,本组既没改善也没恶化它。⚠ 本组的 532 MB **不能**与 S6 的 736 MB 直接比:合成市场的字符串列只有 97 个不同取值,polars 的字符串区小得多(frame 85.8 MB vs 真实 101.6 MB)。**供用户拍板的选项(⛔ 未自行选)**:(a) 维持 `MemoryMax=900M`,接受 18% 余量,S14 在生产同规格 Linux 机上用真实数据复测后再定;(b) 抬高 `neckline-basket.service` 的 `MemoryMax`;(c) 把 `explain,playbook` 拆成第四个晚间 oneshot —— ⚠ 它与「两拍⛔ 不新增 unit」是两码事(那条管的是早晨的进程内 tick),但仍会动 unit 拓扑,归 S14 且需用户点头。<br>**⚠ 与 Plan 不符 / Plan 未写清,如实登记(⛔ 施工侧未自行发明数或策略主张)**:① 🔴 **`explain/input.py::KLINE_SESSIONS = 60` 是本组起的一个数,请用户复核**。它是「给 LLM 看几根日 K」的**上下文长度**,⛔ 不是待标定的策略阈值(§8 的 22 项里没有它,K9 §九 也没有):换成 40 或 80 **不会让任何一只票被选上或落选**,只影响模型看到多长一段图。已写死在一处、`build_inputs(sessions=)` 是**必填关键字**(调用方必须显式说)。⚠ 若用户认为它属于「我会想去调的东西」,那按架构 §二 的判据它就该搬进参数包 —— 那是一次参数包 schema 变更,归标定侧。② 🔴 **K9 §6.3 形态 2 的「跌破昨日最低价 [B]%」落成了 LLM 直接给那个价位、不是给百分比**:§5.6.3 的条件语法是闭合的 `{op, lhs, rhs}`,**没有算术** —— 要表达 `prev_low × (1 − B/100)` 就得往语法里加乘法,而那等于给求值器开一个「谁都能往里塞表达式」的口子。百分比可由 `(prev_low − rhs) / prev_low` 反算,信息一点没丢。⛔ 未擅自扩语法。**请策略侧复核这个落地形状。**③ **形态 2 的「前 30 分钟不创昨日新低」是零 LLM 的**:它的 `rhs` 是**另一个 `MetricRef`**(`prev_low`),模型不必填也不该填 —— `required_keys("p2")` 里没有它。④ **`llm/news_scan.py` 从三类扩到四类**(加「减持」):K9 §二 末段与架构 §3.3 逐字写的是**四类**,而 K8 时代「减持」走 `ts_stk_holdertrade` 结构化接口、不占该模块。K9 的消息面排除是**一次问全四类**的整体判断 —— ⛔ 不许一半走结构化接口、一半走 LLM,那样「查过了没有」与「这一半根本没查」会混成同一句话。`ts_stk_holdertrade` 本身**没删**。⑤ 🔴 **消息面是三态,`unverified` 单独占一格**:`clean`(查过了、干净)/ `excluded`(命中四类之一、剔除)/ `unverified`(**没查成**:没有 provider / 调用失败 / 模型没按格式收尾 / 类别不在闭合枚举里)。折成 `clean` = 「没看」冒充「看过了没事」;折成 `excluded` = 因为一次检索失败悄悄砍掉一只好票。两种都错,所以报告**逐日报出「N 只消息面未核实」**,夹具锁死「解释层跑过了 ≠ 消息面已核实」这两句话是**两件不同的事**。⑥ **`k9_runs.listing_finalized_by` 由编排器改传 `'explain'`,新增 `k9/store.py::mark_listing_finalized_by()`** —— 它**只动 `listing_finalized_by` 与 `seated_count` 两列**,⛔ 不重写整行运行账(那一行记的是**策略层**那次运行,解释层没有资格改它)。⑦ **策略层产物在进程内交给解释层**(`EveningChainResult.k9_result`):后备补位要拿按名次排好的 `reserve`,而 `k9_listing_entries` 只装**入席**的那些 —— 落库再读回来就把补位所需的东西丢了。⚠ 分段跑(`--segments explain`)时它是 `None`,解释层如实报 `no_k9_result`,⛔ 不去猜一个后备名单。⑧ **预案层「一份都没冻成」返 `STATUS_FAILED` 而不是 `ok`**:没有预案 = 明早那两拍核对不了任何一只(核对表会把它们列进「没有冻结预案」那一栏),⛔ 不给这一段绿灯。⑨ **新增两个 LLM 任务常量 `TASK_EXPLAIN` / `TASK_PLAYBOOK`(`ALL_TASKS` 11 → 13)**,三处都想过了(那条守门单测明令的):预算账 —— 逐票调用、上下文小(单只 prompt ~3.8k 字符);流式分级 —— **不进** `LONG_CONTEXT_TASKS`(不开流式 + 基类 90s 读超时,**两项同路接线**);prompt_context —— 两条 system prompt 都嵌了时效纪律并已加进 `test_prompt_context.py` 的清单(S1 在那张表上留的 ⚠「K9 的三个 LLM 岗位落地时必须逐条加回」现已兑现两条;第三条「事实层方向解读」本版仍未建,S3 登记 ⑦)。⚠ 顺带把 `TASK_AUCTION` 的注释改成「**已退役**」——K9 的次日核对是零 LLM,那个键只为让老库存过的路由行仍解得出来;守门断言 `auction/**` 里 `TASK_AUCTION` 零命中。⚠ `SELECTION_PIPELINE_TASKS` 是 K8 残留(`selection/` 整包已删、三项零调用),⛔ 本组未动它,K9 的两个岗位也**不进**那张 K8 的表。⑩ **消息面证据只联一次网**:检索在 `news_scan`(Tavily,经 `get_provider(TASK_NEWS_SCAN)` 包成 `TavilyGroundedProvider`),证据由编排器喂给资料聚合与预案填值,两者都 `enable_search=False` —— ⛔ 不各自再搜一遍。⑪ **报告新增「逐只」段**(§5.10 默认视图第 2、3 段:一句话画像 → 关键价位与预案),并对三件缺席各自如实标:资料未取得 / 消息面未核实 / **没有冻结预案 —— 明早核对不了这一只**。⑫ **新增 `tests/guard_scan.py`(共用扫描器)**:S8 与 S9/S10 两组守门都要「这个词不许出现」这类判据,两份各自漂移的扫描器比一份写错的更糟。它同时提供**文本判据**(先掐掉 docstring 与注释 —— 一条纪律总要写出它禁止的那个词才解释得清,把说明算进命中会逼着后来者删注释去凑绿)与 **AST 判据**(真的去读了没有)。⚠ docstring 的掐法走**行号**而不是 `ast.get_docstring()` 的返回值:后者是清洗过的,对缩进的函数 docstring 匹配不上原文,会让扫描器悄悄少掐一段而不报错。⑬ **S7 的两条测试按新事实改写**(⛔ 不是放宽):`test_..._news_screening_has_not_run` 改成「分段跑里不带 explain」才是「没跑过」的真实情形;`test_unbuilt_layers_are_not_built_not_ok` 改成断言 explain 段 `ok` + 清单由它定稿 + playbook 段 `failed`。另加三条新夹具锁死「未核实 ⛔ 不许报成干净」「没有预案要逐只说出来」。⑭ **`GET /selection/{date}/stock/{code}` 与 `POST .../playbook` 两条路由由本组认领**(§5.12 的 8 条新路由中,S7 落 2 条、S8 落 2 条、本片落 2 条;剩 `/scoreboard/listing`(S17)与 `/legacy/k8/baskets`(S13)仍待认领)。⑮ **`deploy/*.service` 一个字未改**:`neckline-basket.service` 的 `--segments k9,explain,playbook` 是 S7 就改好的,本组的两段正好落在那个 unit 里;`MemoryMax` 三个数一个没动(见上「供用户拍板的选项」)。⛔ 未部署、⛔ 未 ssh。 |
| 2026-08-20 | **裁定 16 / 17 + K9 §七、架构 §四 措辞修订（第五组回写）** | Neckline commit `aa416ca` + whynotme commit `f29a3a3`。**三条回写全部落地**：① **裁定 16（V 豁免分档）** 写进 §2，K9 §五-6 那一行补「⚠ 唯一例外：放量倍数分界值 V ⛔ 不分档（裁定 16）」+ 表下两条理由（p1「≥ V」与 p3「< V」在放量维上**穷尽整个空间**，移动分界点只把票从 p3 挪到 p1，`p1 ∪ p3` 一只都不会多，而 §五-6 分档的目的正是「凑够 10 只」；两档还会让落在两值之间的票**同时命中 p1 放宽档与 p3 严格档**，破坏裁定 15 建立的互斥）+「⛔ 其余带数字的定义性项照旧分两档」的界限。⚠ **这是把 S6 的结构判断升为正式裁定，⛔ 不是新施工要求** —— S6 已按此实现。② **裁定 17（停抓概念板块、保留已有数据）** 写进 §2；`scripts/daily_update.py::update_concept_boards` 及其调用点**整段移除**（`ths_daily` 5 次/日 + `ths_index`/`ths_member` **395 次连续**/周 ≈ 21,750 次/年，其中那 395 次几乎占满 450 次/分限频窗口一整分钟），模块头补写移除依据；**已抓的 21 MB parquet 原地保留、⛔ 未删一个字节**（实测确认 `ths_{daily,index,member}.parquet` + `ths_snapshot_meta.json` mtime 仍是 7-28，未被触碰），`data/concept_data.py` 读写 helper 与三个 `ts_ths_*` 客户端函数**刻意保留** —— 删掉它们，保留下来的那 21 MB 就没人读得动了。守门单测双向锁死：定时器路径上 `ths_*` / `concept_data` 零命中 **且**「读侧 helper 必须还在」。③ **K9 §七 + 架构 §四「输出」段的措辞修订（实施裁定 10，⛔ 未编新号）** —— 两处**前后对照**：K9 §七 原文「输出一张核对表：**哪几只已触发成立**、哪几只已触发放弃、其余待开盘后观察。」→ 改成两段「哪几只已触发放弃、其余待开盘后观察」，并补两段说明(⛔ 9:29 判不出「成立」的**结构性**原因：§6.3 四个成立分支**全部含有「前 30 分钟」这一合取项**，9:29 时它还没发生；若强行让 9:29 出「成立」，§八 的成立率会**结构性恒为 0**)+「**三分支的唯一权威是 D1 10:00 的一次性结算读数**、9:29 的『放弃』先到先定不改判」；架构 §四「**输出**：哪几只已触发成立、…」→ 同款两段 + 同款原因 + 指向 10:00 结算拍。**配套一致性修订三处**：K9 §八 路径图由一行「D1 竞价 + 开盘 30 分钟 → 三分支之一」改成**显式两拍**（9:26–9:29 只判放弃 → 10:00 结算出终值）并补一段「三个比率一律读 10:00 终值，⛔ 不许把 9:29 的『待开盘后观察』当成任何一个分支的结论」；架构 §二 次日时间表补「输出两段 ⛔ 无「成立」」「10:00–10:05 一次性结算读数」两行、并写明 9:30–10:00 系统不出读数；架构 §5.1「成立率」行注明终值出自 10:00 那一拍。**验收**：`grep -n "已触发成立" K9.md Neckline新架构_20260818.md` **零命中**；`grep -n "不分档" K9.md` 命中 §五-6;S0 立的三条 grep(`上方空间` 零命中 / `行业指数` 只剩那句否定句 / `一次性结算读数` 命中)全部仍然成立。§1.2 与 §5.1-E 已登记这四处修订。 |
| 2026-08-20 | **S11 + S13 完工** | commit `65b0e8e`。新增 `neckline/review/{bindery,conclusions}.py`、`neckline/legacy_k8.py`；新表 `review_conclusions`（**纯新增**，append-only，PK `(week, version)`）；新端点 `GET /api/v1/review/bindery`、`POST`/`GET /api/v1/review/conclusions`、`GET /api/v1/legacy/k8/baskets`；`/review/overview` 加**结论存档段**（三段 → 四段）；`export_research_snapshot.py` 加 `--include-fact-packs --start --end`；`smoke_api.sh` 加 43–46 步，临时库实跑通过。测试 1120 → **1281** 全绿。<br>**测试数逐条对上**（+161）：新文件 `test_review_bindery` 18 + `test_review_conclusions` 16 + `test_legacy_k8` 9 + `test_export_snapshot` 15 + `test_v250_s11_s13_guard` **80** = **138**;`test_api_review` 23 → 41(**+18**,装订 / 结论 / legacy 三组端点);`test_db_isolation_guardrail` 61 → 66(**+5**,它按 `tests/test_*.py` 参数化,新增 5 个测试文件);`test_review_reconcile` **21 → 21**(删 1 条已失效用例 + 补 1 条「那个别名确实没了」的反向断言,净 0)。<br>**🔴 S11 无 LLM 的证据**(架构 §六 逐字「这一层无 LLM 调用」):① 逐文件 AST 断言 `review/**`(10 个文件)零 import `neckline.llm` / `neckline.search` / `openai` / `anthropic`;② 更严一档的文本判据 —— 掐掉 docstring 后 `TASK_` 与 `get_provider` 在全包零命中(**连任务常量都不许出现**:一个 `TASK_*` 引用就是在为接线做准备);③ 扫描器自带自检(喂它一份真的 import 了 llm 的文件,它必须报出来)。<br>**🔴 `cashflow.py` 四分类未被加回合计字段的证据**(蓝图 5.3「账户金额增加不得直接视为策略收益」):① `CashFlowSummary` 的字段名列表**逐字冻结**成断言(`week / transfer_in / transfer_out / dividend / tax / other / other_event_count / trading_pnl / event_count`)—— 想加一个合计字段就得先改那行断言;② `to_dict()` 的键集同样逐字冻结(11 个键,无任何合计);③ 8 个候选合计名(`account_net` / `net_change` / `total_net` / `combined` / `grand_total` …)全仓零命中;④ **AST 判据**:模块里不存在把 `dividend` / `tax` / `trading_pnl` / `other` 中任意两个加减到一起的表达式(⚠ `transfer_in − transfer_out` 是**转入转出这一类内部**的净额,合法且必须留 —— 判据按「跨类才算」写,⛔ 不是一刀切禁加法)。<br>**🔴 三条成绩线隔离的证据**(架构 §五「互不进入对方的分子分母」,隔离是**单向**的):① **最要紧的方向** —— `scorecard/**` 逐文件断言零 import `neckline.review`,且源码里 `round_trip` / `roundTrip` / `交割单` / `realized_pnl` / `cash_flow` 五个词零命中;② **结构性** —— `coverage.compute_day` 的签名**恰好** `(pack, listing, dispositions)` 三个位置,**收不下**交割单;③ 反向 —— `review/**` 零 import `neckline.scorecard`;④ **只读不写** —— `review/**` 逐文件扫写 SQL,`INSERT INTO k9_` / `UPDATE k9_` / `DELETE FROM k9_` / `INSERT OR REPLACE INTO k9_` / `DROP TABLE k9_` 全部零命中;⑤ **反面自检**(⛔ 防止上一条守的是空集):断言装订**确实**调了 `load_k9_report_index` / `load_latest_range` / `load_listing_membership` 三个读函数 —— 架构 §六 明文要求装订「当时那几天的报告与预案快照」,读是必须的,写才是禁的。<br>**🔴 装订的容量实测**(开发机 macOS;**真实 parquet 只读 symlink 到临时根** + SQLite 一次性只读副本,⛔ 未触碰工作库 / 工作 parquet / 生产。窗口 = 买入前 20 + 卖出后 20 个交易日):1 只 **0.135s**(冷 glob)/ 5 只 **0.018s** / 15 只 **0.020s** / 40 只 **0.027s**;RSS 峰值 89 → 94 → 98 → **106 MB**(基线 56 MB);40 只时日 K 822 根、markdown 14,576 字符、JSON 336,865 字符。**为什么这么小**:全部票走**一次** `get_multi_stock_history`(新增,一次 glob + `is_in` 过滤 + 列投影),大盘走一次,行业 / 报告 / 预案 / 清单各走**一次区间 SQL**。守门单测按调用计数锁死「三只票 = 1 次多票取数 + 1 次大盘取数」,并断言 `bindery.py` 里 `get_stock_history` / `get_market_slice` / `scan_table_range` 三个名字零命中。<br>**🔴 S13 逐字节相同的证据**(§5.13:标定必须跑在与生产逐字节相同的事实包上):`shutil.copy2` 原文件、⛔ 不重写 parquet(重写会换压缩块与行组边界,数据看起来一模一样而 sha256 立刻对不上);单测断言「拷出来的 bytes == 原文件 bytes == manifest 里的 sha256」三者相等,且**布局用 `market_data.day_file_path` 对拍**(⛔ 不在导出脚本里另拼一套路径)。manifest 身份证四项齐全:`packVersion`(`fp-2`)/ 区间 / `necklineVersion`(取 `api/app.py::VERSION` 这个**单一源**,⛔ 不另存常量、⛔ 读不出也不退回占位值)/ 逐日 sha256;另报 `missingDates`(清单里有、parquet 拷不到 = **真缺口**)与 `orphanDates`(拷到了、清单里没有)—— **标定方拿到 118 天而不是 120 天,与拿到 120 天是两件事**。⛔ 区间**没有默认值**(缺 / 非法 / 倒序一律退出码 2,且退出时快照还没写出去)。<br>**🔴 legacy 端点只读的证据**:`legacy_k8.py` 走 `sqlite3` 的 `mode=ro` 连接、**⛔ 不 import `neckline.db`**(那条路上的 `connection()` 顺手 `init_schema`,只读入口绝不该给任何库建表),模块里 `INSERT`/`UPDATE`/`DELETE`/`REPLACE`/`CREATE`/`DROP`/`ALTER` 七个词**逐个断言零命中**;行为侧断言调完两次之后源库的**行数 / schema / 文件大小**逐条不变、库文件不存在时**不会凭空造一个空库**;写方法 POST/PUT/DELETE → **405**(路由只注册 GET,⛔ 不是 404)。另断言 K8 留档里**不出现 K9 的字段名**(`pattern` / `seatKind` / `firstResistance` …)—— 翻译过去会让一份 K8 留档看起来像一份 K9 清单,进而被谁拿去算成绩。<br>**冒烟与零污染自查**(第四组建立的做法,照做):`DB_PATH` 显式指到 scratchpad,`PORT=8097`;跑前跑后对**真实 `Backend/data/` 全目录**逐文件 `(路径, 大小, mtime)` 快照做 diff —— **9,559 个文件逐条一致**;`data/reports/` 无新增;`git status` 零 `data/` 文件。⚠ **唯一一处差异如实登记**:容量实测那一步以 `mode=ro` 打开真实 `neckline.db` 做一次性副本,过程中 SQLite 的两个**瞬时 sidecar**(`neckline.db-shm` / 0 字节的 `-wal`)被回收又重建 —— 主库文件**大小 4,325,376 与 mtime 均逐字未变**,`PRAGMA integrity_check` 返回 `ok`,69 张表俱在。那两个 sidecar 是 SQLite 自己的生命周期产物,任何一次连接(含只读)都会碰它们,⛔ 不是本组写了工作库。<br>**⚠ 与 Plan 不符 / Plan 未写清,如实登记(⛔ 施工侧未自行发明数或策略主张)**:① 🔴 **§6 S13 的验收 grep「全仓 `grep whynotme` 在 `neckline/` 下零命中」与一句合法的用户文案冲突** —— `api/app.py::get_eval_weekly` 的空态文案原写「周度作业(whynotme 离线周任务还没跑到这个窗口。」(⚠ 还带一个从 V2.1 起就没配对的括号)。处置:**改写文案**为「**离线**周度校准作业还没跑到这个窗口」并补上那个括号 —— 它按**角色**指认那个作业,信息一点没少,而验收 grep 从此零命中。⚠ **但字面 `grep whynotme` 仍剩 3 处命中,如实说清**:`k9/params.py` 模块头两句(「参数标定归 whynotme」「⛔ 不写 whynotme 的任何目录」)与 `review/research_artifact.py` 模块头一句,**全部在 docstring 里**,且全部是在**解释这条边界本身**。守门单测按 S8 建立的体例走:先掐掉 docstring 与注释再扫源码 —— **一条纪律总要写出它禁止的那个词才解释得清**,把说明算进命中会逼着后来者删注释去凑绿(与 S6 登记 ⑬ 的 G11 豁免同一条理由)。真正的牙齿是另一条:逐文件 AST 断言 `neckline/**` **零 import `whynotme`**(AGENTS.md 原文)。⛔ 没有给守门开可扩展的豁免清单(那种守门第二次加豁免时就没人拦得住了)。② 🔴 **§3.3 的包布局列了 `scorecard/mine.py`,而 §6 的任何切片都没有认领它** —— 且若真放进 `scorecard/`,那个文件就得 import `neckline.review`,与本组刚立的隔离守门直接冲突。「我的成绩」的**内容**其实已经存在(`reconcile.WeeklyStats`)。⛔ 未自行决定它落在哪个包,已登记 **§13.1-B7** 带事实 / 三个选项 / 影响面 / 倾向。③ 🔴 **`review/{handoff,research_artifact}.py` 的排版仍是 K8 语义**(Tier 入场信号正确率 / C·Z·Y 引擎版本 / 双时钟),而它们服务的 `/eval/weekly` 与 `/review/overview` 校准段被 §5.12 明确「保留」;K9 之下那份离线校准产物的**形状未定义**,且 `HANDOFF_OBSERVATIONS` 的守门要求每个 id 能在 PROJECT_PLAN §七 grep 到 `[P3-xx]` —— **而本版 §7 已清空**。⛔ 未擅自下线,已登记 **§13.1-B8**。④ **裁定 17 的边界**:逐字只说移除 `daily_update.py` 那一段(已照办,那是唯一挂在定时器上的入口),但仓里还留着 `scripts/backfill_concept.py`(**`END = "20260722"` 是写死的过期日**,一跑 395 次连续调用)与 `concept_data.py` 的两个写函数(现已零调用方)。⛔ 未越过裁定字面动它们,已登记 **§13.1-B9**。⑤ **新增五个批量读函数**(`market_data.get_multi_stock_history` / `facts.industry.load_series` / `report.store.load_k9_report_index` / `playbook.store.load_latest_range` / `k9.store.load_listing_membership`):不是为了好看 —— 逐日调 `load_day` / `load_latest` 会让 **`init_schema()` 在每次调用里重跑整份 schema 脚本**,40 天的复盘窗口就是 40 次全表建表检查;逐票调 `get_stock_history` 会让 15 只票各 glob 一遍区间年份。两者都是 §12 坑 1 那条链的新入口,所以在**源头**做成批量。⑥ **`load_k9_report_index` 刻意不带 `markdown` / `structured_json`**:40 天窗口塞 40 份报告全文是几百 KB 的无用负担,而首行(`state` + `headline`)已经说清那天系统在说什么;要全文按日走 `load_k9_report()` 点查。⑦ **`review/bindery.py::PRE_SESSIONS / POST_SESSIONS = 20 / 20` 是本组起的两个数** —— 与 S9 的 `KLINE_SESSIONS = 60` 同类:**上下文长度,不是待标定策略参数**(换成 30 不会让任何一笔成交变成另一笔),§8 的 22 项里没有它们。已写死在一处、`bind_week(pre_sessions=, post_sessions=)` 是**必填关键字**(调用方必须显式说)。请用户复核,已并进 **§13.1-B3**。⑧ **大盘基准取上证综指** —— 架构 §六 原文只写「同期大盘走势」(单数),未指定指数。取 `data/panel.py::SSE_INDEX` 这个**全仓既有的「大盘」常量**(⛔ 不另起第二个);`index_daily` 里落着的另一条是深证成指,本层刻意只装一条。**这是命名与复用,不是策略主张。**⑨ **申万归属用的是「今天的」成分快照,不是成交当日的**(`sw_industry_member` 只给当前归属,S2 登记 ④)—— 与事实包回填的语义差是同一件事。写在明处:`RoundTripBinding.industry_source == 'current_snapshot'`,`to_dict().note` 与 markdown 都照直说。⚠ 与 `k9_listing_entries` 的**冻结**绑定刻意不同:那一列是清单成绩拆「行业分 / 选票分」的依据必须冻,而交割单是事后回看、当时的清单里未必有这只票,没有可冻的东西。⑩ **`legacy_k8` 是四态不是三态**:全新 Neckline 库**有** `baskets` 表(裁定 6:表保留只读,`init_schema` 照建,只是应用层没有写路径了)但一行都没有 —— 「这个库从没跑过 K8」与「跑过、只是不是这一天」要人做的下一步完全不同,两句话⛔ 不许合并。⑪ **`/review/overview` 由三段变四段**(加结论存档段),`schemas.py` 与 `app.py` 里遗留的「五段」措辞一并改正(那是 S1 删掉画像与双时钟三段之后没跟上的旧文)。⑫ **装订刻意不进 `/review/overview`**:它要读 parquet 行情,属于「点一下才算」的动作,而 overview 是每次进复盘板块都会拉的聚合读(§12 坑 1 / P0-23)。守门单测按函数扫,断言 `get_review_overview` 函数体内 `bind_week` / `bindery` 零命中。⑬ **`reconcile.py` 的陈旧遗留一并清掉**(S1 删代码时留下的):模块 docstring 仍在逐条讲 `MomentumConfig` / `strategy.signals` / `brain.config_governing_at` / `strategy_activation_log` 这些**已物理删除**的东西;`STOP_TOLERANCE_PP` 与 `PRICE_MATCH_TOLERANCE` 两个常量零引用;`day_close_instant()` 只是 `trade_instant(d, None)` 的别名、零生产调用方,而它的 docstring 写着「归属哪版章程」。三样全删,docstring 重写成「本模块只出事实,不出判据」。**测试侧同批**:`TestStopDiscipline` / `TestEntryScreens` / `TestCharterSwitchReporting` 三个类只剩 helper **一条断言都不跑**,外加 `_DueItem` / `_30k_trades` / `_week_of` 三个零引用 helper 与七块指向已退役概念的段旗 —— 它们长得像在跑测试,下一个人会以为止损纪律还在被测。⛔ 这不是放宽:被测的东西早已不存在。⑭ **`export_snapshot` 的 manifest 从 schemaVersion 1 升到 2**:加 `necklineVersion`,并把 `parquetReadOnlyPath` 从写死的 `settings.parquet_dir` 改成**这次真正读的那个根** —— 一份跑在临时目录上的 manifest 指着生产目录说话,是另一种形式的说谎。⑮ **`deploy/*.service` 一个字未改、⛔ 未部署、⛔ 未 ssh、⛔ 未推送**:本组两条新端点都跑在既有常驻 `neckline.service` 里(零新增 unit),`daily_update.py` 少了一段抓取(不改 unit),导出脚本是手工 CLI(不在任何 timer 上)。⚠ **生产仍跑 v2.4.2 Build 9,裁定 17 在上云之前不生效** —— 已登记 §13.1-B6。<br>**Backlog 收拢**:前四组散在 §14 各处的未决项已统一收进 **§13.1**,共 **9 条**(B1–B9),每条带**事实 / 选项 / 影响面 / 我的倾向(标明是倾向不是决定)**;§13.2 保留「本版不做,记着别忘」。 |
| 2026-08-20 | **S12 + S14 完工(批 A 收官)** | 三次提交:`e4666f4`(S12)/ `ec53a9d`(S14 与文档)/ `47fb473`(热路径收敛 + 补测)。**App 三板块换血**:`AppTab` = `selection` / `scoreboard` / `review` + 设置沉底(裁定 11);新增 `Views/{SelectionView,StockDetailView,CheckListView,ScoreboardView}.swift` 与 `Networking/Models/{K9Models,CheckListModels,ScoreboardModels}.swift`,重做 `ReviewView.swift`;删 12 个 K8 件(`BasketDailyView` / `BasketCardView` / `AuctionCardView` / `InfoCardView` / `IntelSectionView` / `ReviewWorkbenchView` + `BasketModels` / `AuctionModels` / `ReportModels` + `NKGateViews` / `NKMemberCard` / `NKStopScale`)。App 净 **−17,382 / +5,755** 行。后端测试 1281 → **1430** 全绿(+149:契约对拍 50 / S12 App 守门 79 / S14 发版门禁 13 / 逐只摘要 4 / `test_db_isolation_guardrail` 按测试文件参数化 +3);App **43 passed / 6 skipped**(skip = 要真 dev 后端的联调冒烟);`xcodebuild -destination 'platform=macOS' build` 与 `test` 均通过。**冒烟零污染自查**(前几组建立的做法,照做):`DB_PATH` 指到 scratchpad、`PORT=8099`,跑前跑后对真实 `Backend/data/` 全目录 `(路径, 大小, mtime)` 逐文件 diff —— **9,559 个文件逐条一致**,`git status -- Backend/data` 为空。<br>**🔴 裁定 10 在客户端这一侧的三重锁**:① `ChecklistVerdict` 是**二值枚举**,`ChecklistVerdict(rawValue: "confirmed")` 恒 `nil`(客户端单测);② 契约对拍按**行首 + 词边界**取类型块后断言客户端枚举恰好两个成员、且块内 `confirmed` 零命中;③ **文案扫描按字符串字面量长度判** —— `CheckListView.swift` 里带「成立」的**短串**(≤ 20 字,= 段名 / 徽标的形状)零命中,长句放行**且必须至少有一句**(⛔ 不许沉默地少一段却不说为什么)。⚠ 这条判据没有「写了某个词就放行」的口子:想把「成立」做成一枚徽标,怎么写都过不去。另一条:`model.verdicts`(10:00 结算终值)在 `SelectionView` / `CheckListView` / `StockDetailView` 三个文件里**零命中**,只出现在 `ScoreboardView` —— 终值 ⛔ 不进选股首屏。<br>**🔴 行业分 / 选票分分两栏的证据**:`ScoreboardView` 那一卡是 `HStack { 行业分 · Divider · 选票分 }`,中间**没有任何合计**;守门两侧同扫 `combinedScore` / `totalScore` / `industryPlusPick` / `合计分` / `综合分` 零命中,并**正向**断言两个名字都在(⛔ 防止守的是空集)。<br>**契约对拍重建**(S1 登记 ⑨ 点名的活,那半年这条闸是空的):`tests/{client_sources,test_contract_crosscheck}.py`,**50 条**。六组判据:客户端调用面 ⊆ 服务端路由面(`==` 断言,欠账清单**为空** = 本闸最严状态)/ HTTP method 对拍(21 个调用点)/ 已删的 37 条路径**两侧**零残留 / reason 面**双向**闭包 / **32 个冻结快照类 DTO** 手写 `init(from:)` / 裁定 10 与「无合计」的两侧对拍。**实测抓到过什么**:① 🔴 **404 的 fallback 是 `.notHolding`「该持仓已清或不存在」** —— 持仓整块下线之后,K9 的每一条 404(「20260430 没有报告」/「600001.SH 不在清单里」/「没有竞价核对表」)都会显示成那句驴唇不对马嘴的话(v1.4 `watchlist` 与 V2 `card_not_ready` 已经踩过两次同一个坑);已改成 `.notFound(服务端原文)` 并立了守门断言那个 fallback 必须带上服务端 detail。② 🔴 **七个 reason 常量的端点早就删了、常量还留着**(`basket_not_found` / `card_not_ready` / `card_corrupt` / `not_trading_day` / `future_buy_date` / `auction_not_ready` / `auction_corrupt`)—— 它们会**要求客户端一直养着七个死 case**,而那些 case 的存在又让人以为对应端点还在;已连同客户端的 case 一起删,并新增**反向**守门:登记过的每一条都必须**真的还能被 raise**。③ **负向自检实测**:往客户端塞一条 `/api/v1/positions/close` 当场红(`Extra items in the left set`),移回原样恢复 50 绿。<br>**图标改名四处同步**:`AppIconV242` → `AppIconV250` —— `App/project.yml` 的 `ASSETCATALOG_COMPILER_APPICON_NAME` / asset 目录(`git mv`,图稿 **7 张 png 一张没动**)/ `tests/test_v240_p4_release.py::_EXPECTED_PRIMARY_ICON` / `xcodegen generate` 重生成的 pbxproj(**2 处**)。新增守门锁「旧名在配置与 pbxproj 里零残留」+「asset 目录里只许有**一个** `.appiconset`」;**当前值**仍由 `_EXPECTED_PRIMARY_ICON` 单点持有(⛔ 两处不重复写死同一个串)。<br>**服务端两处纯新增**(⛔ 不动任何冻结件):① `api/app.py::_selection_stocks()` —— `/selection/*` 带上**逐只摘要**(形态标注 / **上方机械空间** / 三个价位 / 三分支预案摘要),**每次请求现装**:那四样分别住在 `k9_listing_entries` / `k9_channel_hits` / `k9_playbooks` / `k9_explain_notes` 四张表里,而预案是 **append-only 版本化**的 —— 冻进 `k9_reports.structured_json` 会让「用户改完预案」与「报告快照」当场对不上;四次批量查询取全,⛔ 无按票循环(否则首屏就是 20 次请求)。② 个股详情下发 **`playbookSlots`** —— **改预案要填哪几个数由服务端说**(唯一源 `playbook/skeleton.py`,同 `PushKindOut.label` 的先例),⛔ 客户端不硬编键表;守门拿服务端的槽位表**反扫**全 App,形态槽位键零命中。配套新增 `k9/store.py::load_upside_room_mech()`:从 `k9_channel_hits.strength_json` **反读**机械空间原值(p1 存原值、p3 存其负值,符号唯一源仍在 `k9/upside_room.py`),⛔ 不给 `k9_listing_entries` 加列 —— 同一个数两处落点必然漂。⚠ 只被 p2 / p4 召回的票**没有**这一项(K9 §3.3 / §3.5 的强度性里没有它),界面写「本形态不看这一项」,⛔ 不补 0。<br>**🔴 S14 只准备、⛔ 一步都没执行**:没有 ssh、没有部署、没有碰生产、没有改 `MemoryMax`、没有推送。清单落 **§9.6**(步骤 0~7:容量红线**阻塞项** → 升级前五步 → 纯新增迁移 → **8 个 unit 逐个交代** → 部署验证 → **上线后状态预告** → 回滚七步 → 收尾),README 加了「发版」一节指路并把回滚锚点从 `v2.4.1` 更正到 **`v2.4.2` / `ee12b9b`**。**能在本地先跑一遍的做成机器判据**(`tests/test_v250_s14_release_gate.py`,**13 条,全在 `tmp_path` 临时库**):**迁移演练** —— 拿 `ee12b9b` 那份 `_SCHEMA` 造 v2.4.2 老库、**先跑一遍基线自己的 `_COLUMN_MIGRATIONS`**(⚠ 真实生产库不是"刚 `executescript` 出来的样子",少这一步会把 v2.4.2 自己已经跑过的补列**误报**成 v2.5.0 动了历史行 —— 实测踩到)、塞 12 张历史表的行、跑今天的 `init_schema` → 历史行**逐表逐列不变**、**16 张新表**建出且**全空**、老表一张没少、`integrity_check` = `ok`;**零 ALTER 零 DROP 新增** —— 与基线**逐条比对**(⛔ 不是"扫到 ALTER 就红":那会逼人删掉已经在生产跑过的 V2.2/V2.4.2 幂等迁移);unit 拓扑**精确 8 个**、段序 `facts` + `k9,explain,playbook` + `report` **不重不漏**、`--k9-params` 显式传、`StopWhenUnneeded=yes` 还在、三段无 `RemainAfterExit`、`deploy/` 代码行里 `checklist`/`settle`/`auction` 零命中;**五个 `MemoryMax` 逐个锁死未改**;回滚锚点 `ee12b9b` 真能取到且那一版 `VERSION == v2.4.2`;参数包不在仓库里、示例配置**零真数字**。<br>**⚠ 与 Plan 不符 / 未写清,如实登记(⛔ 施工侧未自行发明数或策略主张)**:① 🔴 **§6 S12 验收要的「macOS 截图覆盖六屏」本组拍不到**:`screencapture` 在本环境返回**全黑图**(屏幕锁定 / 无录屏权限),而 iOS 模拟器那条路要装 App(本组明令 ⛔ 不装)。**没有拿别的东西冒充截图** —— 替代证据是 `xcodebuild build` + `test`(43 条)+ 后端 **79 条** App 结构守门 + **50 条**契约对拍,其中「核对表无成立段」「两栏无合计」「终值不进选股首屏」三条是**直接扫源码**的,比截图更硬;但「布局有没有错位、长文案有没有截断」这类**只有截图能答**,🔴 **发版前请人工过一眼六屏**。② **`AppTab.baskets` 更名 `selection`**:那条「rawValue 一个都不许改」的纪律,理由是**外部截图脚本按 rawValue 传参**,而那些脚本(20 个 K8 驱动脚本)已在 S1 随 K8 整链删除 —— 现在 `NECKLINE_INITIAL_TAB` 的消费方**只剩本工程自己**。留着 `baskets` 反而是坑(「篮子」是已退役的 K8 概念,下一个人 grep 它会以为还在)。⛔ 新的 rawValue 从此**又是**契约,两侧各立了一条守门。③ **`NKStopScale.swift` 删除**(§5.11 把它列在「保留 `Components/`」的**按需**一栏):它整件是**持仓纪律**语义(止损 / 成本 / 现价 / 峰值 / 破线),持仓下线后零消费方;K9 的三个价位另写了一把 `PriceLadder`(失效位 / 第一压力位 / 第二压力位,⛔ 不画概率、不画建议)。同理删 `NKGateViews`(绑 `BasketGates`)与 `NKMemberCard`(绑 `BasketMember`)。⚠ 其中 **`NKListRow`(四板块共用的列表行壳)原本住在 `BasketDailyView.swift` 里** —— 那一页一删,设置屏当场编译不过;已搬进 `Components/SharedUI.swift`,⛔ 别再把共用件放回某一页。④ **`NKCopy.intradaySelfObserve` 整段删除**:V2.4.0 P0 留下的那句盘中提示,落点是已经不存在的「今日篮子页面」,更要紧的是它在解释「盘中证伪」与「全局刹车」两个 **K9 之下根本没有的机制** —— 留着是在为一个不存在的机制辩护。K9 的等价物是**服务端下发**的核对表脚注(`CHECKLIST_FOOTNOTE`),⛔ 客户端不另写一句(守门锁死)。⑤ **`GET /api/v1/scoreboard/listing` 本组没建**:清单成绩五指标的结算是 **S17**(批 B),依赖 `k9_followups` 回填与 **B10 那个还没定的窗口**。App 的成绩板块因此是「**两栏在、数没有**」的诚实壳,并加了一条**反向**守门:那条路由**现在就该不存在** —— 提前挂一个恒空的路由,会让「还没开始结算」看起来像「结算了、结果是空的」。⚠ S17 落地时**同时**做三件事:挂路由 / 加进 `_V250_NEW_ROUTES` / 删掉那条反向守门。⑥ **客户端刻意不调的三条服务端路由**:`/eval/weekly` 与 `/review/overview` 的**校准段**(§13.1-B8:排版仍是 K8 语义,K9 之下形状未定义)、`/legacy/k8/baskets`(追溯与导出入口,§5.11 的三板块里没有它的位置)。⛔ 不渲染成一段永远 `available=false` 的壳 —— 那比没有这个段更让人以为「系统那一步坏了」;三条都在 `APIClient.swift` 文件头写明「服务端有、本客户端刻意不调」及理由。⑦ 🔴 **新登记两条 Backlog**:**B10**「**行业分 / 选票分的『同期』窗口没定义**」(K9 §八 只写「同期表现」,§8 的 22 项待标定参数里也没有它;⛔ 施工侧未拍板 —— **S17 开工前必须定**,因为它决定 `k9_followups` 的回填形状,定晚了要重跑历史)、**B11**「契约里两处 snake_case 混进 camelCase 信封」(`structured.listing` 与个股详情的 `explain` 段;不影响运行,客户端已用显式 `CodingKeys` 对齐,登记是因为下一个人照文件头的约定去解会静默解不出)。⑧ **App 侧删了 129 条 K8 用例**(`DTODecodeTests` 89 + `AppModelTests` 40),换成 `K9ContractTests` 18 + `AppModelTests` 12 + `URLGateTests` 2 —— ⛔ **这不是放宽**:被测的东西(篮子 / 六关 / 双时钟 / 持仓角色 / 行情状态)早就物理不存在了。新用例守的是**那几条不许退化的读法**:三态是三句不同的话、`listingSize == nil` **≠ 0**、核对表恰好两段、「还没定案」**≠**「观察」、上方机械空间缺席 **≠ 0**、覆盖率 NULL **≠ 0**、`unverified` **≠**「无异常」、`NKFmt.slotValue` 能被 `Double(...)` 解回去(⛔ 展示用的 `price` 带千分位,两者不许互换)。⑨ **`_hits` 的假阳性**:S12 App 守门起初用裸子串,把 `paramsPackageVersion` 判成了退役标识符 `Pack` 的命中 —— 已改成**标识符边界**匹配。⚠ 记在这里是因为那类假阳性的真实代价不是"多红一次",是**逼着后来者把守门放宽**,最后连真的都拦不住。同族的第二个:`CREATE TABLE IF NOT EXISTS (\w+)` 里 Python 的 `\w` 在 Unicode 模式下**匹配中文**,把 `db.py` 注释里的「天然幂等」当成了一张表名 —— 已锁成 ASCII 标识符。 |

> 施工侧每完成一个切片在此追加一行：切片号、日期、commit、实测数字（墙钟 / 峰值内存 / 通过率）、
> 以及任何与本 Plan 不符的如实登记。⛔ 不另建第二份计划文件。
