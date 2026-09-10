# Neckline

Neckline 是 A 股生产应用，包含 SwiftUI macOS/iOS 客户端与 FastAPI 后端。2026-09-10 已发布
**3.2.0 / 双端 Build 65 / K10-v2 / Schema 8**，后端发布集合为 `v3.2.0-b65`。K9 已退出活动生产。
[下载安装包与校验值](https://github.com/linocai/Neckline/releases/tag/v3.2.0-b65)；Mac 当前安装 B61，下一次换装与画面验收等待解锁；iOS 通过 Xcode 由用户直接安装，本次不生成 IPA。

**2026-09-10 原晚报正在B65继续运行。** 23:16:05官方恢复同一任务，全部326个完成检查点和原账单保留。B65局部移除比较名单外与重复公司判断，原付费比较已在线复用通过；其余事件继续研究。成功后只派送本报告；API active+enabled、开关open，常规worker/timer和旧任务暂停。

固定 1,089 公司池与完整资料已显式导入生产库，资料仍是 `local_draft_awaiting_user`；批处理全部标题、按事件共享研究及定向资料召回，删除 80／40 全文配额。晚间最多 30 家公司卡，晨间独立更新／新增；日报卡与机会两日成绩分离，混合新旧催化的操作明确对应窗口。未核信息可以条件化推荐并披露来源。

B65 通过 1,169 项离线回归、真实失败结果回放与同任务恢复测试，以及双端签名归档和严格验签；仅代码升级，部署前后 85 表数据未变。当前报告尚未完成，最终输出以任务状态为准；原 TuShare 采集缺口保留。

唯一工程状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，产品与视觉方向见
[Neckline V3 前瞻设计](archive/Neckline_V3_前瞻设计.md)。策略研究位于相邻 `whynotme` 工程；
运行时不读取或导入研究仓。根目录仅保留 App、Backend、archive、AGENTS.md、PROJECT_PLAN.md 和本文件。

## V3 功能

入口为 **机会 / 关注 / 选股表现 / 设置**。事件共同事实只讲一次，系统先给出主推、备选或并列，说明
优先理由、差距和改变排序的条件。同公司同批次多催化共用公司卡与一次选择；晚间最多 30 家不同公司，旧催化仍可在每日重选后展示，但不重开两日窗口。
留下才启动正方、反方各一轮，双方共享冻结证据，反方另读正方全文；全文、来源与修订可追溯。

双端已按参考图重做白色轻卡、蓝色线性图标与文字层级。iPhone 逐张浏览公司卡，略过/留下固定在底部，
浏览不提交选择；底栏为机会、关注、选股表现，设置从齿轮进入。macOS 使用公司列表与阅读区双栏，
正反观点并排；原始资料与搜索摘录分开标识，正文引用可定位到冻结资料修订。

K10-v2 资格以已确认的 1,089 公司固定快照为准，运行期不按动态行业或 ST 信息重新增删名单。21:00 为晚扫资料截止
（不含整点），次一交易日 09:00 为晨间截止（含整点）；发布、采集、分析、实际可查看和操作时间分别记录。
全部正式主推与备选自动记录两日行情，与用户是否留下无关。D1 是推荐可查看后的第一个完整交易日，
D2 为下一交易日；推荐因延迟错过预定 D1 的 09:30 开盘时点，从下一交易日起观察并标迟到，
晚间扫描拖到次晨才完成也遵守相同时点规则。

D1 开盘前最后一次明确操作冻结为留下、明确略过或未处理，开盘后改选不回写历史。普通更新延续旧机会，
反证撤回或取消关注不删除原样本；D2 收盘结束，停牌、缺数或仍看好均不延长。同公司后来新机会与旧窗口
重叠时单列观察，不进入主命中率。主指标只统计已到期且两日涨停状态完整可核的主样本中，至少一次收盘
封板的比例；触板、价格变化和缺数另列。价格变化不代表可成交收益。

价格观察以 D1 开盘为参照，分别给出两日高低收与整个窗口的极值；除权资料不足时跨日指标留空。
同 D1/D2、同评价版本的三组可以对照，晚间与开盘前晨间来源合并统计并保留各批次；同一事件涉及多家公司时另列事件关联，不能相加当作多次独立催化成功。

晨报独立展示公司更新、新增和重大变化，配套事件整体公司比较、带来源及缺口的历史同类资料、追加追问与正反版本阅读链。
行情逐字段展示双源核验、单源回退或冲突原因；重叠机会保留实际命中记录，缺数与异常不会充作未命中。
撤回与两日到期为终态，普通资料更新和补充分析不会重新激活旧机会。

完整交易计划、价位确认、持有退出、交割单、个人持仓/成交/盈亏和旧 K9 链退出 V3。交易纪律仅作个人提醒。

## 配置与数据

当前生产策略包为 [k10-v2.json](Backend/neckline/config/k10-v2.json)，执行包为 [k10-execution-v4.json](Backend/neckline/config/k10-execution-v4.json)；用户当前生产连接为 `deepseek-flash`，可在 BYOK 显式切换实际端点和模型。两个配置包均须显式登记修订并与策略快照绑定；缺任何一项都报“今天没跑成 · 参数未配置”，不从扫描历史或任意最新修订猜选。

**B59起支持双端 BYOK**：设置 → 模型配置，可保存多组 HTTPS Chat Completions 连接、修改端点和模型 ID、替换或清除 Key，启用一组即切换新任务的连接。API 基础地址自动补 `/chat/completions`；密钥留空保留，跨服务商地址更新须同时换 Key 或清除旧 Key。当前后端为B65，Mac已安装B61且支持BYOK；B65换装等待解锁，iOS通过Xcode安装。

每个开始执行的任务单独冻结连接名称、端点和模型；切换到另一组不影响原任务，同一组可轮换 Key。直接改原组的端点／模型或删除原组，会阻止旧任务继续；希望保留未完成任务时应新增连接。旧版本已有外呼但未记录连接身份的任务不能自动猜选并恢复。保存配置不试调模型、不探测余额、不打开报告开关。通用接口不发送 DeepSeek 专用推理参数；供应商对具体模型的支持仍需用户日后实际使用确认。


初始化／升级顺序：确认目标和备份 → 受控 Schema 8 初始化／迁移 → 导入固定资料 → 登记配置修订 → 绑定策略快照 → 核对 API 配置响应。B57 已完成一次生产初始化；B59 不改数据库结构，今后新增生产迁移或恢复须重新确认目标与恢复路径。API 启动和 GET 不执行迁移。Schema 8 新增固定池、资料快照和日报卡账本，保留旧版正式机会、选择及 D1/D2 原记录。

在 `Backend/` 使用 `python -m neckline.k10.cli`，按顺序执行以下子命令；`--db` 都传同一已核对的 Schema 8 库，修订号使用前两项登记命令实际返回值，不照抄旧生产修订：

1. `import-v2-profiles`：传 `--db` 与同值 `--confirmed-target`，以及 `--universe-file`、`--profiles-dir`、`--universe-id k10-v2-initial-20260909`、`--profiles-id k10-v2-profiles-20260909`。输入分别是研究侧 `research/K10-v2初始股票池_20260909.json` 和 `artifacts/output/k10-company-profiles-v2-20260909/`；导入核验固定哈希及三份 1,089 公司集合。资料保留 `local_draft_awaiting_user` 和原始引用，导入不等于已核实或获准上传。
2. `configure --config-id <策略配置ID> --file neckline/config/k10-v2.json` 与 `configure-execution --config-id <执行配置ID> --file neckline/config/k10-execution-v4.json`，均传 `--db`。
3. `bind-v2-strategy --snapshot-id k10-v2-20260909`，传 `--db`、上述 `--config-id`／`--config-revision`、`--execution-config-id`／`--execution-config-revision`。启动绑定使用同一组 ID／修订；策略快照不可覆盖，调整配置时需显式更新策略快照 ID。

标题 Agent 分批理解全部精确去重标题，跨批合并同一事项；不因来源、海外、未带公司代码或利好词机械删除资料。公司资料索引用于召回相关公司，无名称或关键词命中不能直接排除。
研究以共享事件为单位，正文、公司档案、核验材料按实际问题取得并复用，不逐标题搜索。Tavily 每次搜索均关联问题、查询意图、来源路径和预期判断变化；搜索摘录和全文分别标识，必要时取全文。**没有 80／40 篇、正文或搜索总配额，也没有“剩余额度”准入。**
来源事实未变可复用，正文版本、规则或模型输入改变则重新核对；时效、价格和两日判断不得由旧证据冒充。不设整轮或整日金额、token、模型调用、搜索调用总上限；有界并发、单次输出边界、有限重试、逐次用量账和持久暂停保留。未知调用结果单列，不伪装成功或可免费重试。
402 余额不足终止任务；429 按冻结执行包和 `Retry-After` 延后受影响步骤，正方完成后不因反方重试而重跑。设置页显示当前明确绑定、公司资料来源状态、标题／正文／研究／执行状态与公司比较；待核、排除和程序失败分别保留。
通知退避配置仍见 [notification-delivery-v1.json](Backend/neckline/config/notification-delivery-v1.json)。

TuShare 长篇通讯为当前采集源；快讯、全量公告权限未开通。Tavily 用于重点核验，不冒充全市场来源，
日期未知或截止后取得的新增证据不冒充此前已核验。模型/搜索密钥由 App 设置写入服务器，读取不回显。
B36 事故与先前付费测试仅作历史证据，详见 [B36 执行记录](archive/v3.0.4-b36_execution.md)；
发布前离线协议验证曾复用 B38 冻结的 1,430 条有效标题和 37 篇历史正文，合成结果不作为真实筛选质量证据。本次获授权的 9 月 10 日真实试跑及失败用量见执行记录第 24 节。

**暂停的 B53 历史配置（不是新版本配置步骤）：** 策略配置修订 3 保留每来源必填的 `lateArrivalReplaySeconds`，TuShare 显式设为 `86400`，
用于有界回补晚到资料，记录实际回查范围和缺口。它不是无限历史覆盖，也不允许把新取得的资料回填为旧推荐。
既有修订原样保留；当时停用的 API／timer 配置绑定为策略修订 3、执行修订 4，原晚报任务仍绑定执行修订 2，不得改写历史修订或用代码默认补齐。

API 使用 `/api/v1/k10/`，通用连接/推送设置仍在 `/api/v1/settings`，设备注册为 `/api/v1/devices`。
API 启动只验证鉴权和既有 schema；GET 不迁移、不启动模型。重任务由独立 K10 worker 执行，timer 只入队。
设置页与 timer 共用显式配置 ID/修订：尚无扫描时也可显示“已配置”，缺失/错误绑定仍报未配置；不从历史扫描或任意最新配置猜选。
worker 自动冻结 D1 选择、采集共享的公司日行情并生成结果修订。行情缺口按运行包明确的间隔和次数有界补采，
常规补数窗口为收盘后 120 分钟、间隔 300 秒；晚启动仍可回填历史固定日期，缺数不会延长 D1/D2。
后端发布路径沿用 `/opt/neckline`（同步 Backend 内容），详细环境项见 [配置样例](Backend/.env.example)。

## 验证

从 Backend 运行 `.venv/bin/python -m pytest -q`。夹具禁用 `.env`，使用临时数据库与合成行情；
真实 API/凭据和工作数据不作为测试输入。

修改 Swift 后，从 App 依次运行三条门禁：

```bash
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' -derivedDataPath /tmp/neckline-v3-qa/macos NK_BUNDLE_SUFFIX=.qa CODE_SIGNING_ALLOWED=NO build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/neckline-v3-qa/ios NK_BUNDLE_SUFFIX=.qa CODE_SIGNING_ALLOWED=NO build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/neckline-v3-qa/ios NK_BUNDLE_SUFFIX=.qa CODE_SIGNING_ALLOWED=NO build-for-testing
```

Debug 参数 `-K10SyntheticUI` 使用独立演示数据和空凭据，不联网、不注册推送；不能用于宣称真实模型已验通。
测试仅复用上述两个目录和 `top.linotsai.neckline.qa`，每平台最多一个运行实例，启动前先退出上一实例。
结束后退出 QA App、停止临时 API、移除临时 token，清理过期副本；保留生产客户端和签名归档。
真实隔离 QA 显式设置 Debug 进程变量 `NK_DISABLE_PERSISTENT_CREDENTIALS=1`，此时只取进程
`NK_API_TOKEN`，不读写持久凭据。`NK_QA_TAB`、`NK_QA_READING` 仅供 Debug 页面定位；
macOS 的 `NK_QA_RENDER_PATH` 只离屏渲染本 App 的 SwiftUI 视图，不能代替真实窗口的点击/滚动验证。
构建、测试运行和实际页面验证是不同检查，当前完成情况记在 PROJECT_PLAN。

## 生产运行与恢复

发布源码 `0c0a789d7c3cde0b0020400dacfe1dde7787438a`，不可变标签 `v3.2.0-b65`；后续文档提交不移动标签。
服务器 `ser657204219523`（`114.66.2.205`），数据库 `/opt/neckline/data/neckline.db`，公网 `https://nk.linotsai.top`。
API 可读取；持久报告开关已为 9 月 10 日晚间单次试跑打开，专用B64进程继续原任务并在成功后派送本报告；常规 worker 和 timer 不恢复，旧失败任务保持原状态。
`/etc/neckline/k10.env` 绑定策略 `k10-v2-production` 第 1 修订、执行 `k10-v2-execution-production` 第 1 修订；
固定快照 `k10-v2-20260909` 已绑定，首次扫描前四项配置检查通过。

Mac `/Applications/Neckline.app` 当前为 3.2.0（61），Developer ID 严格验签、通用架构和单实例启动通过。
Mac 尚未公证，网络下载后的 Gatekeeper 体验未验收。iOS 真机签名归档和工程配置就绪，由用户通过 Xcode 安装，不导出 IPA。
本地签名归档：`/Users/linotsai/Lino/releases/Neckline/v3.2.0-b65-20260910/`；
后端包、wheel 与 runtime manifest：`/opt/neckline/releases/v3.2.0-b65/`。

本次只更新代码与 wheel；Schema 8、通知 Schema 2 和 85 张表全部数据保持不变，前后备份校验值相同。
服务器恢复集 `/opt/neckline/data/backups/v3.2.0-b65-predeploy/` 包含 B63 代码、环境／绑定、切换前后数据库和回执。
Mac 可恢复副本 `/Users/linotsai/Lino/app_backups/Neckline-v3.2.0-build61-pre-b63-20260910.app`。
回滚前必须停止所有写入者、保存最新现场并核对升级后的业务写入；不得直接拿旧快照覆盖新增数据。
保持根目录 `root:root /0755`、数据库 `neckline:neckline /0600`，恢复后核对完整性、健康、鉴权、实际配置和暂停状态。

旧晚报与原 D1/D2、用户选择、失败晨报检查点仍保留；它们不是 K10-v2 首报，不能为了新版本重放或重开窗口。
此前 B39–B53 故障、恢复及旧执行参数只作历史证据，见 [执行记录](archive/v3.1.0-b39_execution.md)；
本次发布与恢复集的详细哈希见 [3.2.0 执行记录第 27–28 节](archive/v3.2.0-b54_execution.md)。数据库和凭据只留服务器，不公开。
