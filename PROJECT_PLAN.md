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

以下 **11 条**是 2026-08-19/20 用户的明确裁定（裁定 10、11 为 2026-08-20 立项收口时追加）。
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

**验收**（在 `/Users/linotsai/Lino/whynotme/` 下跑）：

```bash
grep -n "上方空间" K9.md                                  # 期望：零命中（只剩「上方机械空间」）
grep -n "行业指数" K9.md Neckline新架构_20260818.md        # 期望：只剩明确写着「不使用」的那一句
grep -n "申万行业" K9.md Neckline新架构_20260818.md        # 期望：零命中（全部已带「二级」）
grep -n "一次性结算读数" Neckline新架构_20260818.md         # 期望：§四 边界段命中 1 处（裁定 10 的明文例外）
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
| **p1 放量启动** | 过去 `p1.ampWindowDays` 天振幅 ≤ `p1.ampMaxPct`；当日涨幅 > `p1.minRetPct` | 放量倍数（当日量 ÷ `p1.volMaDays` 日均量）、**上方机械空间（正向）**、当日相对强度 |
| **p2 超跌反弹** | 归一化跌幅（跌幅 ÷ 该板跌停幅度）≥ `p2.normDropMin`（主板与创业板共用同一门槛）；前一日收盘 ≥ `p2.maDays` 日均线；非一字跌停且当日换手 ≥ `p2.minTurnover` | 跑输行业的幅度 |
| **p3 中等生转强** | 长窗 `p3.longWindow` 日相对强度 ∈ ±`p3.flatBand`（≈0）；短窗 `p3.shortWindow` 日相对强度 > 0 **且**在改善；当日尚未放量爆发（`p3.notErupted*`） | 短窗改善幅度、**上方机械空间（反向）** |
| **p4 资金异动** | 单日主力净流入 > 0 且排名前 `p4.dailyInflowRankPct`；`p4.cumDays` 日累计净流入 > 0 且排名前 `p4.cumInflowRankPct`；资金流入排名 − 涨跌幅排名 ≥ `p4.lagRankGap` | 净流入排名、**量比排名（自算 `vol/vol_ma5`，⛔ 不用 2 位小数的 `volume_ratio` 排名）** |

**每个通道跑两档**：`strict` 与 `relaxed`（K9 §五-6），产出 `ChannelHit(ts_code, pattern, tier)`。
两档都跑、每天都记两档数量（K9 §五末段「区分市场今天确实没有 vs 判据卡得过严」）。

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

## 8. 待标定参数总表（共 20 项，⛔ 一个数都不许填默认值）

**纪律（K9 §九原文）**：单条判据合理 **≠** 连乘后仍有样本。
**⛔ 未经单条通过率与联合通过率验证，不得填入默认值上线。**
下表全部标注「待标定」，⛔ Plan 里不写建议值、不写「暂用 X」。

### 8.1 K9 §九原有 14 项

| # | 所在 | 参数 | 状态 |
|---|---|---|---|
| 1 | K9 第一层 | 冲高回落门槛 | 待标定 |
| 2 | 形态 1 | 放量倍数排名门槛 | 待标定 |
| 3 | 形态 2 | 归一化跌幅门槛 | 待标定 |
| 4 | 形态 2 | 一字跌停 / 有效换手的判定 | 待标定 |
| 5 | 形态 3 | 长窗天数 | 待标定 |
| 6 | 形态 3 | 短窗天数 | 待标定 |
| 7 | 形态 3 | 相对强度「≈0」的区间宽度 | 待标定 |
| 8 | 形态 3 | 「尚未爆发」的判定 | 待标定 |
| 9 | 形态 4 | 净流入排名门槛（单日 / N 日） | 待标定 |
| 10 | 形态 4 | 资金排名 − 涨幅排名的差值阈值 | 待标定 |
| 11 | K9 第三层 | 行业热度分 / 形态内强度分 / 跨日接力分 三项权重 | 待标定 |
| 12 | K9 第三层 | 跨日接力的回溯天数 N | 待标定 |
| 13 | K9 §五 | 分档放宽的两档定义 | 待标定 |
| 14 | K9 §五 | 触发「判据过严」的连续天数 | 待标定 |

### 8.2 本次新增 2 项（裁定 1、裁定 2 派生）

| # | 所在 | 参数 | 状态 |
|---|---|---|---|
| 15 | K9 第三层 | **上方机械空间的 N 日** | 待标定 |
| 16 | 行业强度 | **行业中位数的最小成员数** | 待标定（代价表见 §4.5，供选值参考，⛔ 不是建议值） |

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

## 13. Backlog（本版不做，记着别忘）

- **策略并行运行**（架构 §七第 2 条）：本版不实现（裁定 8）。将来引入 K10 时再做，
  届时 `k9_runs.strategy` 字段与「署名清单」的形状已经预留好了。
- **申万历史归属回补**（`index_member` 逐 L1 拉 31 次）：生产不需要（成绩线在写入时冻结绑定），
  whynotme 若要跑上线前的历史标定，自己拉。
- **`sw_daily` 行业指数行情**：本版明确不落（§3.2）。若将来有对照需求，注意 2014 版指数码混入问题。
- **`suspend_d` 历史回补**（本地只有 5 天）：标定跑历史时需要，属 whynotme 侧的输入准备。
- **`ths_*` 概念板块**：K9 明令「概念板块不进入任何机械计算」。相关日更是否保留由 S1 判断；
  若无消费方则一并退役。
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

> 施工侧每完成一个切片在此追加一行：切片号、日期、commit、实测数字（墙钟 / 峰值内存 / 通过率）、
> 以及任何与本 Plan 不符的如实登记。⛔ 不另建第二份计划文件。
