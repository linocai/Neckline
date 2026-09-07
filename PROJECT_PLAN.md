# Neckline V3 · 3.0.4 / 双端 Build 35（发布准备）· K10-v1.4

本文件是 Neckline 唯一施工控制面。策略裁定以 [`whynotme/K10.md`](../whynotme/K10.md)
为准；生产代码不得读取、导入或以模型记忆替代 `whynotme`。

## 当前目标、事实与边界

- **当前目标**：在已有未提交的 3.0.3 / Build 34 工程增量上，完成独立复核确认的 11 项
  K10-v1.4 一致性缺陷，目标为 **3.0.4 / 双端 Build 35**。用户已追加授权：完成施工后直接一条龙发布，包含提交 main、推送、不可变标签、签名包、生产备份/部署和本机换装；iOS 交付安装包。
- **当前生产恢复锚点**：commit `47c9fd0d2a04c2d924b94fc8d51d53f93bd73d60`、不可变标签
  `v3.0.2-b33`、Neckline 3.0.2 / 双端 Build 33 / Schema 3，服务器发布集合仍为
  `v3.0.2-b33`。保留所有既有标签；不得回滚或丢弃当前工作区的 Build 34 增量。
- **历史验收的正确解释**：Build 34 的 655 项 Backend 回归、双端构建及原生 QA 是先前工程事实，
  不能作为本轮 11 项情景已修复的证据。3.0.4 必须为每个确认复现补上持久化、API 与客户端边界的真实验收。
- **既定运行配置**：只限创业板，排除 ST／*ST 与申万 2021 白酒Ⅱ `801125.SI`，无股价/金额上限；
  全模型路由 DeepSeek V4 Pro；TuShare 长篇通讯用于发现，Tavily 只用于已冻结事件的定向核验和历史同类
  资料检索，绝不回流为全市场发现。TuShare 快讯/全量公告未授权，覆盖缺口必须显示。
- **不在本轮的能力**：K9/K8、交易计划、买卖价、持有/退出、仓位、收益结算、回测与策略校准。不得添加
  兼容表/路由/UI、默认策略数值或 `whynotme` 运行时依赖。

## K10-v1.4 的冻结产品契约

- K10 是纯选股器。每个正式发布公司窗口固定追踪 D1/D2；系统推荐的所有公司均是样本，开盘前冻结为
  留下、明确略过、未处理。用户后续操作、资料补充、晨报、分析改版、撤回和取消关注均不删除样本、不改
  冻结组、不延长窗口。
- 发布前先完成事件内公司比较：共同事实只讲一次，明确主推、备选或并列、优先理由、差距与改变排序的事实；
  晚间最多 30 个不同公司，同公司同批多催化共用卡、选择、成绩和冻结的全局展示顺序。跨日续旧不重开机会；
  同公司窗口相交的新机会为 overlap，完整展示但永不计入主命中率。
- 晨报覆盖仍在固定窗口内的所有正式机会以及新到期提示，包含留下、略过、未处理、同窗口多催化和撤回项；
  固定为重大反证、论点变化、继续/到期、新机会、待核五段。资料不全只能待核；撤回仍可读且原成绩继续至 D2。
- 正反分析只属于曾被用户留下的公司窗口；同一请求生成同一版本的正反全文。新的问题或合法新增证据才递增
  分析版次，不能新建窗口、重置 D1/D2 或领取成绩。
- 主指标是 D1/D2 内至少一次**收盘封板**。分母只含 D2 到期且两日涨停状态完整可核的主样本；未到期、停牌、
  缺数、异常和 overlap 单列。价格变化是观察指标，不是假定成交收益；行情事实按公司代码＋交易日唯一保存。

## 3.0.4 修复合同与施工包

### A · 采集恢复、晨报持久化与模型输出入口（owner：A）

**A1 · 入库至输入冻结的失租恢复（P1）。** 文档版本与本 scan 的 `sourceAcceptedDocumentRefs`
在同一 SQLite 事务保存，记录真实 INSERT 的 `isNew`；相同 scan 的重放不能把已有 true 改成 false。
未冻结输入时只处理本 scan 尚未消费的新版本，重放范围内更早 scan 已消费的旧版本不得再次理解。
文档提交后、输入快照冻结前中断时，接手 worker 从原子记录恢复同一集合、冻结并处理一次；写入前仍验租。
覆盖 old＋fresh 混合来源、提交后硬中断、恢复时重复返回和已冻结输入重试；沿用 Schema 3 coverage metadata。

**A2 · 晨报 item 的 coverage 单一语义（P2）。** `_morning_fallback_item`、模型晨报项与 API 投影统一使用
`coverage.status`／`coverage.gaps`（允许读旧 `state`）；完整 fallback 在客户端必须为“已核”，不完整才为待核。
这只修正 DTO 语义，不改变五段、窗口或晨报排序。

**A3 · 撤回的独立反证引用全链保留（P2）。** 对 material contrary，生命周期事件的 `sourceRefs` 必须是稳定去重的
晨间资料与独立核验资料并集；`content.materialContraryEvidence` 与可阅读的 refs 一一可追溯。后续
`_morning_target_items`、下一份晨报及 opportunity API 必须保留真实独立 refs，不能把普通晨间资料冒充独立核验。

**A4 · 原始事件比较重复公司拒绝（P2，串行接入 B 的 helper）。** DeepSeek `compare_event` 在将数组压成
`dict[companyCode]` 前，必须拒绝重复公司行，报出结构错误并阻断发布；不得静默让后一行覆盖前一行。A 负责
`pipeline.py` 的 provider 边界调用，B 提供无副作用的校验接口和断言语义。

**A 的文件所有权**：`Backend/neckline/k10/pipeline.py`、`ingestion.py`、`morning_runtime.py`、`morning.py`、`store.append_document_version` 与
`Backend/tests/test_k10_v304_sources.py`、`test_k10_v304_morning.py`。除 A 外，其他包不得直接修改
`pipeline.py`；B 对 A 仅交付 helper 契约/测试结论。

### B · 事件比较与历史证据（owner：B）

**B1 · 历史 coverage 引用稳定去重（P1）。** 多个历史公司案例共享一份文档时，保留每个 case 的完整
`sourceRefs`，但 `historicalCoverage.sourceRefs` 按 `(documentId, revision)` 首次出现顺序去重。后续发布校验
不得因合法共享证据拒绝整批候选。

**B2 · 事件内 rank 与全局 display rank 分离（P2）。** `CandidateComparison.rank` 保存模型验证后的事件内
rank（并列必须明确 `tied`）；跨事件排序仅在发布样本/公司窗口写冻结 `displayRank`。不得将全局名次回写到
事件比较，不能拆散明确并列，也不改变跨事件 30 家顺序。新写入 comparison 标明 `rankNamespace=event`
与 `eventRank`；旧无标记的记录不推断事件内名次。新增可选 display rank 不得改变旧发布输入的重放哈希。旧未发布冻结草稿缺少事件排序时明确失败，保留原输入；不得猜测排序或静默重新调用模型。

**B3 · 概率守卫覆盖全部模型派生文本（P2）。** 递归拒绝正向、未经校准的涨停/封板概率预测，包括
“涨停概率可能达到 70%”及倒装同义式；事件摘要、候选比较、历史 assessment、classification 等所有模型输出
路径均必须进入守卫。明确否定或缺口说明（如“不能估计涨停概率”“不输出概率”）继续合法，不能用关键词
黑名单误伤。

**B4 · 比较数组的纯校验契约（供 A 接入）。** 对原始 `candidates` 列表在 map 前验证：每项公司代码唯一、
集合与输入 peer 完全相等、至多一个 primary、rank 全序且并列角色一致。helper 不访问数据库、不改写数据；
`run_discovery` 仍验证所有非 DeepSeek 实现返回的 `EventComparison`。

**B 的文件所有权**：`Backend/neckline/k10/discovery.py`、`historical_cases.py`、`opportunity_discovery.py`、`types.py`、
`store.py` 的 publication 输入序列化/校验/发布函数，与
`Backend/tests/test_k10_v304_discovery.py`。不改 `pipeline.py`。

### C · 结果和来源覆盖 API（owner：C）

**C1 · 每窗口冻结评价配置是成绩前置条件（P2）。** API 只为拥有完整、可验证的该窗口冻结
`evaluationPolicy` 与 `marketCollection` 的窗口投影成绩。缺任何必需项时，不得临时从行情事实合成 records 或
命中率；返回明确 `not_configured`／配置缺口，并保留已有合法历史窗口的可读性。不得用当前绑定配置替代丢失的
窗口配置，也不静默删掉样本。库中已有成绩也必须经过同一配置门禁；无效窗口净化 API 投影，既有记录保持不变。

**C2 · `limit_data_unavailable` 纳入数据缺数（P2）。** `incompleteCount` 继续是总数；若已收市日的
`closeLimitUp` 或 `touchedLimitUp` 因涨停价/状态不可核而为 `null`，其 `limit_data_unavailable` gap 必须计入
`dataGapCount`，同时不伪作普通未命中。未来交易日仅为 `pendingCount`。

**C3 · event group 单列 overlap 指标（P2）。** 每个事件组在已有 `primary` 外增加 `overlap` metrics，
与总体/cohort 的分类和分母定义一致。该组的公司/机会计数可含重叠机会，但 UI/API 不得把 overlap 混入
primary 指标，也不能产生“有重叠公司却所有指标为 0”的误导。

**C4 · 来源时间质量和回补范围投影（P2）。** `ScanOut`／source coverage DTO 显式传递每来源
`timeCoverage`、`unknownPublicationTimeCount`、`uncertainTimeDocumentRefs`，以及该 scan 冻结的
`sourceReplay`（nominal/effective/replay start、cutoff、seconds、gaps）。覆盖完成状态不等于时间精度完整；
API 不得将 partial 时间质量投影为 complete。

**C 的文件所有权**：`Backend/neckline/api/k10.py`、`k10_schemas.py` 与
`Backend/tests/test_k10_v304_api.py`。不改 `pipeline.py`、Swift 或数据库 schema。

### D · Build 35 客户端和真实 DTO fixture（owner：root）

**D1 · 解码新增加性字段。** Swift 模型解码 event-group overlap metrics、scan/source 时间质量和 replay
范围；缺字段仍兼容 Build 33，显示“未记录／待核”，不得编造完整性。

**D2 · 用户可读的覆盖与成绩。** 机会/来源区域展示来源时间待核数量和回补范围/缺口；表现页在事件分组中
独立展示 overlap 指标。晨报 fallback 的真实 complete coverage 显示“已核”。UI 不得改变服务端提供的 rank、
selection、窗口或主/overlap 归属。

**D3 · 真实跨端 fixture。** 从隔离 Schema 3 DB 的 FastAPI 响应解码当前 Swift models，覆盖时间 partial/replay、
完整晨报 fallback、撤回独立 refs、主/重叠 event group、limit 缺数和配置缺失。手写简化 JSON 不能替代此链。

**D 的文件所有权**：`App/Neckline/Networking/K10Models.swift`、`Views/OpportunitiesView.swift`、
`Views/PerformanceView.swift`、`Views/SettingsView.swift`、`Views/EvidenceViews.swift`、`Views/K10Presentation.swift`、
`App/NecklineTests/K10V3Tests.swift`、
`Backend/tests/k10_v304_fixture.py`、`Backend/tests/test_k10_v304_fixture.py`。D 在 C 固定 DTO 后接入；不改 API。

### 版本治理、接口与集成顺序

- 版本与发布文件、README、最终 QA 协调由根会话持有。Build 35 必须在 iOS/macOS 版本、Python release
  元数据及 health/API 版本投影一致；这不是发布授权。
- 本轮保持 **Schema 3**。上述修复使用既有 scan coverage、document metadata、生命周期 content、比较和结果
  投影；无数据库表/约束迁移。实施中若发现必须持久化新结构，先停止该分支，更新本计划并走受控 Schema 迁移，
  不得暗中创建 Schema 4。
- API 保持 additive `k10-api-v2`：Build 33 可忽略 Build 35 新字段，Build 35 使用字段并对历史缺失作诚实
  显示。DTO 字段名以 C 的 schemas 为唯一来源，D 不创建并行本地语义。
- B 与 C 可并行；A 可先完成 A1–A3，接 B 的纯 helper 后串行完成 A4；D 在 C 固定 DTO 后接入。所有包看到
  他人修改必须保留并适配，不得回退既有 Build 34 工程。

## 验收门槛

所有复现使用临时 SQLite、确定性 provider/transport 和临时进程凭据；禁止 `.env`、生产数据库、真实模型、
Tavily 或真实行情回退。新增测试必须从报告的问题边界进入，不能只用手工构造的下游对象弱化场景。

1. 真实 CLI enqueue → worker → handler：文档已入库、水位已写、失租发生在输入冻结前；接手后同一 scan
   冻结同一 refs，模型读取一次，发布一次，候选不从 1 变为 0。另测已冻结后的 retry 仍不重读/重发。
2. 两个历史公司 case 共享同一 document revision 时，coverage refs 去重、case refs 保留、正式发布成功；
   正向概率在事件/候选/历史 assessment/classification 全部拒绝，否定语句通过；DeepSeek raw 重复公司行在
   dict 折叠前失败。明确并列的事件内 rank 在跨事件排序后保持并列，而 `displayRank` 独立可读。
3. 同一隔离 DB 的 API：窗口冻结评价配置缺失不返回临时命中；完整历史配置仍可读；收市后的 limit 状态缺数
   计入 `dataGapCount`；重叠 event group 有独立指标；scan DTO 带 timeCoverage、未知时间 refs 与 replay。
4. 晨报完整 fallback 通过真实 endpoint 投影为 complete/“已核”；独立反证 refs 在 lifecycle、详情 API 和
   下一晨 target 全链可读，普通晨间来源不替代独立核验。
5. 生产 handler 的离线 provider/transport → 临时 Schema 3 DB → 实际 FastAPI JSON → 当前 Swift decoder，
   覆盖 A–C 的所有新增字段和上列缺陷；再查看 macOS/iOS 的真实填充及空态。每平台最多一个 QA 实例，复用
   `/tmp/neckline-v3-qa/macos` 与 `/tmp/neckline-v3-qa/ios`，设置 `NK_DISABLE_PERSISTENT_CREDENTIALS=1`，
   结束时关闭实例、清理 superseded QA bundle，保留生产客户端；视觉对照
   `archive/Neckline_V3_界面参考/` 的白卡、蓝色重点、克制文字和清晰导航。
6. 每次 Swift 改动后必须运行：

```bash
cd Backend
.venv/bin/python -m pytest -q

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

并运行有改动的 XCTest、完整 Backend 回归、`git diff --check`。一个平台构建、HTTP 200、空页面、手写客户端
fixture 或旧回归通过，均不能单独证明本轮情景修复。

## 用户网页操作清单

无。用户已授权本轮完整发布，不重复请求批准；iOS 沿用用户自行安装约定。

## 状态、恢复与下一步

- **状态**：11 项修复及旧成绩/旧草稿边界已闭环；完整 Backend 682 passed。独立复核确认来源恢复与晨报证据链，追加边界由 root 重跑原复现及持久回归通过。
- **当前验收**：双端 build/build-for-testing 通过；每端 XCTest 32 passed、1 个旧可选 smoke skip，Build 35 真实 API 验收已运行。双端原生填充/空态已核对，含来源待核、完整晨报、反证阅读及事件重叠成绩。QA 仅使用 `/tmp/neckline-v304-validation/` 合成数据、固定两个 QA 目录及模拟器 `211DD03C-812D-4A42-97EF-F693D7DF924C`。
- **恢复锚点**：生产 DB/runtime 继续是
  `/opt/neckline/data/backups/v3.0.2-b33-predeploy-20260907-r2`；Mac 备份继续是
  `/Users/linotsai/Lino/app_backups/Neckline-v3.0.1-build32-pre-v302-20260907.app`。有后续业务写入时先制定
  数据保留方案，绝不用旧快照覆盖。
- **下一步**：正在执行用户授权的一条龙发布；实际生产已核验 B33/Schema 3、无运行任务、无 K9 活动。仅追加回补参数的配置修订 2，保留所有既有修订。清理 QA 后提交 main 并推送、创建 `v3.0.4-b35` 不可变标签，严格验签后备份与部署、换装 Mac，交付 iOS IPA，核对公网/鉴权/定时器后更新发布记录。
