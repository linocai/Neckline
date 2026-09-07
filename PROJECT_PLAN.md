# Neckline V3 · 3.0.2 / Build 33（发布进行中）· K10-v1.4

本文件是 Neckline 唯一施工控制面。策略裁定以 [`whynotme/K10.md`](../whynotme/K10.md)
为准；生产代码不得读取、导入或以模型记忆替代 `whynotme`。

## 当前目标、事实与边界

- **目标**：十一项一致性修复与复审边界已完成验收。用户已于 2026-09-07 明确授权“走发布一条龙”：
  完成 3.0.2 / 双端 Build 33 的提交推送、不可变标签、双端签名、后端备份迁移部署、Mac 换装与 IPA 交付。
  K10-v1.4、股票池、固定 D1/D2 与现有定时安排保持既定裁定；iOS 最终安装仍由用户执行。
- **发布基线**：生产仍是 commit `47339492573892515f1b2b578459aad082cf1afd`、不可变标签
  `v3.0.1-b32`、3.0.1 / Build 32。`44e9bdf` 只记录发布/恢复事实。Root 已独占将本地版本入口升为
  3.0.2 / Build 33；这是本轮有效改动，不得撤销。
- **复现事实**：`/tmp/neckline-k10-conformance-20260907` 的真实 API JSON 含晨间 lifecycle
  `content` 对象/数组，现有 Swift 解码失败。`/tmp/k10_lifecycle_reactivation_repro.out` 证明
  `withdrawal → evidence_update` 被末事件投影为 active，随后 API 可 `keep` 并新建分析。
  `/tmp/k10_review_compare_guard_repro.py` 证明正文“涨停概率70%”与两个 primary 都能通过。它们仅是
  复现材料；Build 33 必须以仓库内回归测试取代它们。
- **既定运行配置**：只限创业板，排除 ST／*ST 与申万 2021 白酒Ⅱ `801125.SI`，无股价/金额上限；
  全模型路由 DeepSeek V4 Pro；TuShare 长篇通讯发现，Tavily 只用于已冻结事件的定向核验和历史同类
  资料检索，绝不回流为全市场发现。TuShare 快讯/全量公告未授权，覆盖缺口必须显示。
- **不在本轮的能力**：K9/K8、交易计划、买卖价、持有/退出、收益结算、回测与策略校准。不得添加兼容
  表/路由/UI、默认阈值或 `whynotme` 依赖。

## 不变的产品规则

- K10 是纯选股器。每个正式发布的公司窗口固定追踪 D1/D2；选择冻结为留下、明确略过、未处理。
  撤回、取消关注、资料补充、晨报和分析改版不删除样本、不改冻结组、不延长窗口。
- 事件的公司比较在发布前完成。晚间最多 30 个不同公司；同公司同批多催化共用卡、rank、选择和成绩。
  与该公司已发布窗口相交的新机会固定为 overlap，完整展示但永不计入主命中率。
- 主成绩是到 D2 收盘、D1/D2 涨停状态完整可核的主样本中“两日内至少一次收盘封板”的比例。停牌、
  缺数、异常、未到期、不可比和 overlap 单列；行情事实按 `companyCode + tradeDate` 追加保存。
- 正反分析只属于曾被用户留下的公司窗口。后续分析可追加到已有观察链，不能新建窗口、重置 D1/D2 或
  重领成绩。生命周期、选择、rank、统计和主/重叠归属均以服务端投影为准。

## 3.0.2 冻结契约

### 1. 生命周期与选择

`k10_opportunity_lifecycle_events` 仍是追加账本，状态按完整历史折叠，不能再取末事件：

- `withdrawal`、`expired` 是单调终态；后续 `evidence_update` 只能补资料，不能复活；`risk` 也不能
  覆盖终态。
- active/risk 期间，只有 coverage 完整且显式 `reasonStatus=current` 的核验结果能清除既有 risk；
  continuation/普通 evidence update 不得暗中清风险。
- 选择端调用同一投影。终态窗口的新 `keep` 返回 409，零新 observation、analysis task 或 outbox；
  既有选择、窗口、证据、报告和成绩继续可读。

### 2. 晨报：全量、五段、可读

Schema 3 新增不可变 `k10_morning_reports`（每个晨间 scan 一份）与
`k10_morning_report_items`（每个正式 opportunity 一项）。item 保存至少
`reportId, scanId, companyWindowId, opportunityId, displayRank, selectionState, lifecycle, section,
priority, summary, coverage, sourceRefs, independentVerificationRefs, lifecycleEventId, createdAt`。

- target 是截止时仍在固定 D1/D2 窗口的**全部正式发布机会**，以及上次晨报后新到期、需提示一次的机会：包含 kept/skipped/unhandled、同窗口多
  催化和已撤回项；不得按是否留下筛选。撤回项仍作为重大反证可读，不能重开分析。
- 每项恰好属于以下五段，按段号、`displayRank`、稳定 ID 排序：`major_contrary`（重大反证/撤回）→
  `thesis_changed`（论点改变）→ `continuing_or_expiring`（完整覆盖下无实质变化、继续观察或 D2 到期）→
  `new`（本晨正式新发布）→ `needs_review`（资料不全、独立核验不足或任务失败）。无变化不另设第六段；
  coverage 非 `complete` 时禁止写“无变化”，只能进 `needs_review` 并说明缺口。
- handler 输入必须有原候选、冻结晨间 docs、独立核验 refs 及其真实 document versions。模型结论只能
  引用这套资料；新增重大反证结论须有对应独立 refs。完整扫描无新匹配可复用原证据并注明覆盖范围，不能伪称新增核验；自然到期归续旧/到期。完整但无材料变化也必须落
  `continuing_or_expiring` report item，不能只返回 `reused`。
- API 固定为 `GET /api/v1/k10/morning-reports/latest` 和可分页
  `GET /api/v1/k10/morning-reports`。`MorningReportOut={reportId,scanId,cutoffAt,coverage,items}`，item
  使用上述字段。空报告也须返回明确 coverage 和空 items，不能伪装为无变化。

### 3. rank、事件级比较、历史同类证据与概率守卫

- `k10_publication_samples.rank` 是发布时冻结的全局公司顺序。读投影为
  `CompanyWindowOut.displayRank`（首发 batch 最小 sample rank）；机会、窗口、样本、结果和晨报按
  `availableAt DESC, displayRank ASC, stableId ASC`，同批只按冻结 rank。Swift 不得以创建时间、风险或
  本地状态重排；风险优先仅属于晨报 section。
- 用一次 `EventComparison` 替换逐公司 `compare`：输入同一事件的完整 peer 集和冻结证据，输出每个公司
  一次的 `role, rank, priorityReason, gap, rankChangeConditions, twoDayReason, sourceRefs`。校验 rank
  全序；同 rank 只允许明确 `tied`；同事件至多一个 `primary`；拒绝漏公司、重复、双主推和 A>B/B>A 循环。
  跨事件 `prioritize` 只排序已完成的公司结论，不替代事件内比较。
- 每个正式比较追加 `historicalCases` 与 `historicalCoverage`。case 固定为
  `caseId, outcome=success|flat|failure|unclassified, summary, observedAt, sourceRefs, marketFacts`；只有来源明确支持才能分类成功/失败/平淡，未分类真实事实照常保留；coverage 固定为
  `state=complete|partial|unavailable, requestedOutcomes, presentOutcomes, missingOutcomes, reason, sourceRefs`。
  新 `historical_cases.py` 使用配置好的 Tavily 定向检索真实公开历史资料，存为版本化
  `tavily_verification` document，再冻结 refs。它只服务已冻结候选/事件，discovery source boundary
  继续排除它。缺失败/平淡资料或检索失败须显式 partial/unavailable；不得用十日行情、模型记忆或
  `whynotme` 资料充数。
- 概率守卫递归检查对象、数组和字符串，拒绝**正向、未经校准的价格/涨停概率预测**，包括
  “涨停概率70%”“70%概率涨停”与等义表达。允许明确否定/缺口声明，如“不能估计涨停概率”“不输出概率”。
  测试必须锁住正向与否定语境，不能只检查键名或关键词。

### 4. 追加分析和版本阅读链

Schema 3 新增 `k10_analysis_requests`：`requestId, companyWindowId, observationId, targetRevision,
parentRevision, kind=user_question|evidence_update, question, sourceRefs, idempotencyKey, taskId, createdAt`。
初始分析仍是第 1 版；既有 V2 analysis revisions 不伪造 request，读取时投影 `initial`。

- `POST /api/v1/k10/company-windows/{id}/analysis-requests` body 固定为
  `{kind:"user_question"|"evidence_update",question?,sourceRefs:[{documentId,revision}],idempotencyKey}`。
  user question 必有非空问题；evidence update 必有至少一个已经保存、可追溯且与窗口关联的 document ref。
  只允许绑定已有 observation；撤回/取消关注后可补充历史阅读，但不新建观察或改选择/窗口。
- 同一 request 生成同 revision 的 pro/con。`inputLineage.chain` 固定含
  `requestId,kind,parentRevision,question,addedEvidenceRefs`；正方读完整上一版和本次冻结资料，反方读本版
  完整正方。retry 只重跑原 request/revision；只有新的合法请求递增 revision。
- `GET /api/v1/k10/company-windows/{id}/analysis-chain` 返回
  `{companyWindowId,items:[{revision,inputCutoffAt,requestId?,kind,question?,parentRevision?,sourceRefs,
  analyses:[AnalysisOut],job?}]}`，按 revision 升序。UI 显示“分析第 N 版”与每版正反全文、触发原因和 refs，
  不显示裸 `vN`。

### 5. 行情、字段核验与统计

- 成绩 cohort 按 D1/D2/评价版本合并晚间与开盘前晨间新增，公开 `batchIds` 保存全部来源；
  `batchId` 仅是首批代表标识，不能作为 cohort 分组键。
- TuShare 继续保存原始日行情、涨停价、复权与停牌。复用 `data.realtime.get_quotes_dual`（新浪+腾讯）只在
  目标交易日收市后，且两源 quote 的代码、可解析上海时区交易日和采集时间均严格证明同日时，核验可比
  OHLC/pre-close。实时 quote 绝不冒充历史补数或未知日期收盘。
- 每个 `MarketDayFact` 的 metadata/source refs 追加逐字段审计；API 明确投影
  `fieldChecks:[{field,state=verified|conflict|single_source|unavailable,reason,sourceValues:
  [{source,value,observedAt}]}]` 和 `anomalyReason`。冲突保留两边原值和理由，整体是 anomaly，不平均、
  不择优、不派生价格指标。历史补数或缺有效第二源标 `single_source` 和回退原因，可保留 TuShare 事实但
  不得称已交叉验证。涨停价/衍生状态也说明字段来源；最小报价单位沿用现有人民币股票规则及实际涨停价，
  不加策略阈值。
- 主 hit/touch 的分子和分母先筛 `sampleClass=primary`，再筛 D2 到期且两日完整可核；overlap 无论命中与否
  只进入 overlap 指标。`incompleteCount` 是不完整总数，`dataGapCount` 是其信息性子集；Swift “资料不完整”
  只显示前者，绝不相加。

### 6. DTO、迁移与兼容

- API envelope 保持 additive `k10-api-v2`，旧 Build 32 可忽略新字段，Build 33 必须使用新字段；不建立
  K9/K8、双写或旧语义兼容 API。`K10CompanyWindow` 及相关列表用 `displayRank`；`K10Comparison` 增加历史
  cases/coverage；`K10MarketDay` 增加 field checks/异常理由。缺字段显示“未记录/待核”，不编造。
- Swift `K10Value` 改为递归 `string|number|bool|null|object([String:K10Value])|array([K10Value])`，其
  `Codable`/`Equatable` 必须覆盖嵌套 lifecycle content。Swift 必须直接解真实 FastAPI JSON；手写的简化
  JSON fixture 不能代替跨端验收。
- `SCHEMA_VERSION` 从 2 前滚到 3，创建晨报、晨报项、分析请求与必要索引，并扩展行情 availability 的 `anomaly` 状态约束。若 SQLite 需要重建该表，必须逐行核验原主键、版本、数值、refs、行数与外键不变；不删除、改写或伪造既有
  机会、窗口、选择、行情、生命周期或分析修订。`initialize_schema()` 仍只属于 API 启动、显式写命令或
  受控迁移；GET/read helper 零 DDL。
- 分析失败重试沿用同一全局版本：Schema 3 同步移除分析表的 `(observation, revision, role)` 唯一限制，保留工件 ID 唯一及全部失败尝试；公开每版每方显示最新尝试。迁移须核验原行哈希、数量和外键，禁止用反方另增版本绕过配对。
- 本轮只演练临时 DB 迁移。未来生产前必须确认目标路径/schema/完整性/WAL，建立并核验升级前备份哈希，
  再前滚和做配置/数据不变量检查。回滚恢复当次升级前备份和对应运行包，绝不用 Build 32 首扫前空库覆盖
  后续业务数据。`v3.0.1-b32` 仅作不可变代码恢复锚点。

## 并行责任与接缝

所有 Builder 共享工作树，保留他人改动。Root 独占
`Backend/neckline/api/k10.py`、`Backend/neckline/api/k10_schemas.py`（pipeline 模型类由 B 独占、其余工厂和调度现由 D 接手）、
版本入口与最终集成。

| Owner | 独占范围 | 交付 |
|---|---|---|
| **A · Store/Schema** | `Backend/neckline/k10/schema.py`、`store.py`、迁移/store tests | Schema 3；终态 lifecycle projection；晨报 target/report 读写；analysis request 原子入队与完整 chain 读取。只给 Root/C 稳定 Python 接口。 |
| **B · Discovery/Historical** | `k10/discovery.py`、`opportunity_discovery.py`、新 `historical_cases.py`、`pipeline.py` 内 `DeepSeekDiscoveryModel` 类、discovery/historical/pipeline tests | EventComparison、真实 DeepSeek 事件整体比较、语义概率守卫、冻结历史 cases gateway/coverage；Root 仅传显式 loader 和编排；不改 store 或 pipeline 的工厂/调度。 |
| **C · Morning/Analysis** | `k10/morning.py`、`morning_runtime.py`、`analysis.py`、`runtime.py`、`prompts.py` 与测试 | 五段晨报/独立 refs 校验；追加分析 lineage、正反链与 retry。调用 A 接口，不改 API/pipeline。 |
| **D · Market/Evaluation** | `k10/market_observation.py`、`evaluation.py`、`evaluation_runtime.py` 与测试 | 双源时点核验、字段审计/回退/冲突、主/overlap 统计分离。通过现有 metadata/source refs 接口写入，不改 store/API。 |
| **E · Swift** | `App/Neckline/` 所有 Swift、`K10V3Tests.swift` | 递归 K10Value、后端排序直通、无重复缺数计数、晨报/历史/行情/分析链展示。Root 保留 `project.yml`/`.pbxproj`。 |
| **Root · API/Orchestrator** | 上述中心文件、版本、集成验收入口 | 将 A–D 契约投影到 endpoint/DTO，编排 EventComparison、晨报 targets/落库与 children；不在中心文件复写 Builder 逻辑。 |

**接缝顺序**：A 先冻结 Schema 3 和函数签名；B/C/D/E 可并行。Root 在 A/B/C/D 接口就绪后整合 API/pipeline。
任何 Store 接口变更由 Root 协调，Builder 不在中心文件做临时绕过。

## 验收门槛

每项必须有“Build 32 可复现、Build 33 修复”的临时数据库离线测试，禁止真实模型、Tavily、生产数据库
或 `.env` 回退。至少覆盖：

1. lifecycle content 含对象、数组、数字、布尔、null 时，实际 Swift DTO 解码并在详情可读。
2. 晨报覆盖 kept/skipped/unhandled、多催化、撤回和晨间新增；五段顺序、rank、独立 refs、完整无变化与
   覆盖不足待核正确。
3. `withdrawal → evidence_update` 仍终态；新的 keep 为 409 且无新 observation/task；历史可读。
4. primary 命中、overlap 命中、缺数各一时，主/overlap hit 与分母正确，资料不完整只显示一次。
5. API 与 Swift 都保持发布 rank；风险或创建时间不得改变顺序。
6. 双主推、循环比较、遗漏 peer、正文概率预测拒绝；“不能估计涨停概率”通过。成功/平淡/失败历史 case
   都有冻结真实 docs，缺任一类显示 coverage 缺口。
7. 初始、用户问题第 2 版、新资料第 3 版按链可读；request 幂等，失败 retry 不增 revision，终态窗口不被
   补充分析改写。
8. 双源一致、字段冲突、历史单源回退、无法证明 quote 日期四种行情情形均带原因/refs；冲突不派生。

还必须新增一次跨端合成验收，落实 `.learnings/LEARNINGS.md` `LRN-20260831-006`：生产 handler（确定性
provider/transport）→ 临时 Schema 3 DB → 实际 FastAPI router/JSON → 当前 Swift `K10Models` decoder →
真实填充的机会、晨报、关注/分析、表现页面。一个链中必须看见晨报排序、撤回后普通更新、overlap 命中、
缺数、分析第 2 版、历史证据/缺口和行情冲突原因。手写 Swift JSON、HTTP 200、APNs 或空页面均不构成交付。

最终执行：

```bash
cd Backend
.venv/bin/python -m pytest -q

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

任何 Swift 改动后三条 `xcodebuild` 一条不能省，并跑相关 XCTest。原生 QA 复用
`/tmp/neckline-v3-qa/macos` 与 `/tmp/neckline-v3-qa/ios`，每平台一个隔离实例，设置
`NK_DISABLE_PERSISTENT_CREDENTIALS=1`；对照 `archive/Neckline_V3_界面参考/` 的真实空态及填充态，
关闭进程并清理 superseded QA bundle，保留安装中的生产客户端。

## 用户网页操作清单

无。本轮只用已配置的运行契约做离线注入验证，不需要用户网页授权、提交、付款或发布。

## 状态、恢复与下一步

3.0.1 / Build 32 的发布日志已压缩为本文件开头的发布基线；详细操作入口以 README 和不可变
`v3.0.1-b32` 为准。本轮完成施工后才评估发布，测试、schema 演练或 Build 33 编译均不等于生产切换。

- **待用户决定事项**：无。
- **修复完成**：原审查十一项全部落地；发布原子校验、旧分析状态、晚晨归组、历史 as-of/引用一致性、同版本分析重试等复审边界同步闭环。原生 QA 补出的取消误报、同连接刷新/分析链竞态及原因原文丢失也已修复。Store/API、策略历史和最后 Swift 边界独立复验均无剩余可报告 P1/P2。
- **自动验证**：最终 Backend **616 passed**（21 条既有 Polars 警告）；macOS build、iOS Simulator build、iOS Simulator build-for-testing 全通过，额外 macOS build-for-testing 通过。双端 XCTest 各执行 31 项：**30 passed / 1 skipped / 0 failures**；跳过的是旧的独立外部 smoke，新 Schema 3 实际 API 跨端验收在两端均已执行并通过。`git diff --check` 通过。
- **实际页面验收**：双端真实空态和填充态、五段晨报、两版完整正反分析/任务完成状态/来源原文、历史案例及缺口、行情冲突及单源原因、主/重叠命中与缺数均已查看；保持白卡蓝色视觉方向。fixture 走生产 worker claim/finish、真实 handler/store/API，只替换离线 provider/transport，未调用真实模型、搜索或行情服务。
- **验收材料**：临时库 `/tmp/neckline-v302-validation/populated-r2.sqlite`；最终日志为同目录 `backend-full.log`、`macos-final-*.log`、`ios-final-*.log`，双端 `*-final-tests.xcresult`。固定 QA 目录仍是 `/tmp/neckline-v3-qa/macos` 与 `/tmp/neckline-v3-qa/ios`；仅复用 `.qa.livev14`，模拟器 ID `211DD03C-812D-4A42-97EF-F693D7DF924C`。
- **发布边界**：源码与本地 QA 为 3.0.2 / Build 33，尚未提交、推送、打 tag、部署、签名交付或替换正式 App。生产仍是 3.0.1 / Build 32；Schema 3 仅演练于临时库。后续获发布指令后，首先核验实际生产差量和当时业务数据库，按受控备份迁移规则发布。
- **清理完成**：临时 API 的 8769 端口已关闭；Mac QA 已退出，模拟器 QA bundle 已卸载；默认 DerivedData 中额外生成的 Mac 测试 App 与过期 iOS 测试 App 已删除，固定两端构建目录保留复用。进程核验只剩 `/Applications/Neckline.app` 的正式 3.0.1 / Build 32。
- **当前发布**：已确认目标 `deploy@114.66.2.205:/opt/neckline`、主机 `ser657204219523`，API/worker 正常；四个既有 timer 尚未触发。Root 负责目标、完整差量、备份/迁移/部署与版本记录；客户端任务仅归档/导出/验签，不另起测试 App；Store 任务只读核对升级不变量。
- **下一步**：读取线上 manifest、schema 与数据不变量，核对当前差量后冻结提交；在双端归档同时准备 Schema 3 升级及当次回滚，部署后核验配置 scope、计划首跑时刻与正式客户端版本。
