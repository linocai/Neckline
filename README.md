# Neckline

**当前发布为3.5.1（89）**：K10-v2，后端已上线；Mac已替换，锁屏导致启动验收待完成。iOS签名归档就绪，由用户Xcode安装，无IPA。[发布下载与校验值](https://github.com/linocai/Neckline/releases/tag/v3.5.1-b89)，源码`c9f1bb98ba4fd4edda627a422ce8534d38a5bb3c`。

**本地3.6.1（93）已完成B92审查后的快修与独立复查。** 本版从全新空库开始，B92前的报告、任务、原件、回执、行情和客户端缓存全部退役，运行时禁止读取、导入或恢复。采集与报告仍独立，生产暂停状态不变；当前代码尚未发布，不启用采集、不恢复报告。进度及证据见[主Plan](PROJECT_PLAN.md)。

9月22日晚报已于23:53:11正式partial发布：814标题、58事件全部处理，30家公司正式推荐；一篇正文缺必要事实字段，相关5家公司排除并显示缺口。完成通知23:53:27/29分别获两设备APNs接受，设备是否显示尚未确认。B89修复恢复后旧失败提示残留及采集时钟漂移导致的重复候选冲突；已发布推荐、身份和观察窗口不变。

内部 **Schema10 / 新报告Schema9 / 历史Schema8** 不变，无DDL或新配置。生产策略`k10-v2-production@2`、执行`k10-v2-execution-production@2`，继续绑定`k10-v2-b82-20260922`；原冻结策略、模型、输入和账本保留。资料仍为`local_draft_awaiting_user`。没有补跑其他旧失败/删除报告，没有额外provider探测。

B89相关82项回归及3项严格类型复核通过；双端OS27签名归档、模拟器build-for-testing、新旧同绑定API和真实报告/材料Swift读取通过。临时物及清报告专用快照已核验回收，保留B81/B82/B89恢复集；Mac仍锁屏，启动与隐藏旧包去重待解锁，iOS由用户Xcode安装。见[本版第16节](archive/v3.5.1-b82_execution.md#16-今晚第七轮快修-b89已上线)。

唯一工程状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，产品与视觉方向见
[Neckline V3 前瞻设计](archive/Neckline_V3_前瞻设计.md)。策略研究位于相邻 `whynotme` 工程；
运行时不读取或导入研究仓。根目录仅保留 App、Backend、archive、AGENTS.md、PROJECT_PLAN.md 和本文件。

B93新库准备入口：在 `Backend/` 运行 `.venv/bin/python -m neckline.k10.cli initialize-fresh --db <明确的新文件路径>`。命令只创建不存在的新空库，不接收旧库、不自动配置策略或打开开关。后续发布须停写、核清旧存储范围后清空退役业务数据；`DB_PATH`与`K10_DB_PATH`必须同时绑定同一个新库，`PARQUET_DIR`与`K10_PARQUET_DIR`必须同时绑定同一个新空目录，重新显式登记配置和固定公司池。新库交易日历为空，必须先用 `scripts/init_calendar.py` 在新库重新取得并落库官方日历，再读回核对SSE覆盖起止及接下来晨晚报/行情所需交易日；启动依赖日历的正式入口前完成，不能用旧库复制或工作日近似代替。两份环境文件（`.env`与`/etc/neckline/k10.env`）合并后，API使用前一组变量，worker/晨晚报/采集/行情unit使用后一组；发布readiness必须逐项核对实际unit展开后的环境与命令，任一不一致不得启动切换。当前Schema10只是存储结构号，不能用同号旧库继续运行。旧库迁移入口已退役；恢复工具只接受新起点内的备份，旧恢复集不再具有业务恢复授权。此轮没有执行生产清空或切换。

## 当前线上功能（3.5.1）

入口为 **机会 / 关注 / 选股表现 / 设置**。事件共同事实只讲一次，系统先给出主推、备选或并列，说明
优先理由、差距和改变排序的条件。同公司同批次多催化共用公司卡与一次选择；晚间最多 30 家不同公司，旧催化仍可在每日重选后展示，但不重开两日窗口。
留下才启动正方、反方各一轮，双方共享冻结证据，反方另读正方全文；全文、来源与修订可追溯。

双端已按参考图重做白色轻卡、蓝色线性图标与文字层级。iPhone 逐张浏览公司卡，略过/留下固定在底部，
浏览不提交选择；底栏为机会、关注、选股表现，设置从齿轮进入。macOS 使用公司列表与阅读区双栏，
正反观点并排；原始资料与搜索摘录分开标识，正文引用可定位到冻结资料修订。

K10-v2 资格以已确认的 1,089 公司固定快照为准，运行期不按动态行业或 ST 信息重新增删名单。21:00 为晚扫资料截止
（不含整点）；3.5.0 新晨报资料窗口为前一自然日 21:00 至交易日 08:30（含端点），不声称覆盖至 09:20。旧任务保留原时间绑定；发布、采集、分析、实际可查看和操作时间分别记录。
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

仓库的 [k10-v2.json](Backend/neckline/config/k10-v2.json) 和 [k10-execution-v4.json](Backend/neckline/config/k10-execution-v4.json) 用于本地3.6.1施工；后者已明确B92采集资料研究契约，不能当作线上配置。生产仍使用B89的策略/执行revision2与`k10-v2-b82-20260922`冻结绑定；用户当前生产连接为 `deepseek-flash`，可在 BYOK 显式切换实际端点和模型。两个配置包均须显式登记修订并与策略快照绑定；缺任何一项都报“今天没跑成 · 参数未配置”，不从扫描历史或任意最新修订猜选。

本地B92另有独立的 [采集配置](Backend/neckline/config/k10-collection-v1.json)。先通过 `configure-collection --db <已核对数据库> --config-id <采集配置ID> --file neckline/config/k10-collection-v1.json` 登记并使用实际返回的修订；服务环境显式绑定 `K10_COLLECTION_CONFIG_ID`／`K10_COLLECTION_CONFIG_REVISION`，凭据分别使用受保护环境中的 `TUSHARE_TOKEN` 和 `JIN10_MCP_TOKEN`。登记后采集仍关闭，只有 `collection-control --state open` 配合同一配置ID／修订才打开；它不改变报告开关。App只显示凭据是否配置，不回显或编辑金十Token。

新增 `neckline-k10-collection.timer/service` 每个北京时间自然日08:00／20:00入队，worker复用现有服务；安装材料不自动启用。报告读取已保存的冻结资料，逐源说明实际覆盖和未采到的尾段；不例行补采。已选金十文章按需读正文，关键疑问才查询金十或Tavily，资料足够可零搜索；搜索空结果不代表安全。本轮只做离线验证，未部署这些配置、启用采集或恢复报告。

**B59起支持双端 BYOK**：设置 → 模型配置，可保存多组 HTTPS Chat Completions 连接、修改端点和模型 ID、替换或清除 Key，启用一组即切换新任务的连接。API 基础地址自动补 `/chat/completions`；密钥留空保留，跨服务商地址更新须同时换 Key 或清除旧 Key。iOS 通过 Xcode 安装，实际发布状态以本页顶部和 PROJECT_PLAN 为准。

每个开始执行的任务单独冻结连接名称、端点和模型；切换到另一组不影响原任务，同一组可轮换 Key。直接改原组的端点／模型或删除原组，会阻止旧任务继续；希望保留未完成任务时应新增连接。旧版本已有外呼但未记录连接身份的任务不能自动猜选并恢复。保存配置不试调模型、不探测余额、不打开报告开关。通用接口不发送 DeepSeek 专用推理参数；供应商对具体模型的支持仍需用户日后实际使用确认。


初始化／升级顺序：确认目标和恢复路径 → 受控schema迁移 → 导入固定资料 → 登记配置修订 → 绑定策略快照 → 核对API配置响应。3.5.0内部Schema10新增研究轮次、晨报工作项、搜索回执和交付材料/元数据，公开新报告Schema9。B81生产副本9→10→9→10演练通过；该次正式迁移保留原业务行与付费账本。B82不迁移Schema，代码和旧绑定回退B81已在副本验证；产生B82新任务后须先核对冻结契约与未决费用，不能直接回退。API启动和GET不执行迁移。回退B75只允许在停机核验五张新表为空、旧数据未变后受控降级；已有新写时停止并前向修复，禁止旧库覆盖新写。

在 `Backend/` 使用 `python -m neckline.k10.cli`，按顺序执行以下子命令；`--db` 都传同一已核对并完成当前schema迁移的库，修订号使用前两项登记命令实际返回值，不照抄旧生产修订：

1. `import-v2-profiles`：传 `--db` 与同值 `--confirmed-target`，以及 `--universe-file`、`--profiles-dir`、`--universe-id k10-v2-initial-20260909`、`--profiles-id k10-v2-profiles-20260909`。输入分别是研究侧 `research/K10-v2初始股票池_20260909.json` 和 `artifacts/output/k10-company-profiles-v2-20260909/`；导入核验固定哈希及三份 1,089 公司集合。资料保留 `local_draft_awaiting_user` 和原始引用，导入不等于已核实或获准上传。
2. `configure --config-id <策略配置ID> --file neckline/config/k10-v2.json` 与 `configure-execution --config-id <执行配置ID> --file neckline/config/k10-execution-v4.json`，均传 `--db`。
3. `bind-v2-strategy --snapshot-id k10-v2-20260909`，传 `--db`、上述 `--config-id`／`--config-revision`、`--execution-config-id`／`--execution-config-revision`。启动绑定使用同一组 ID／修订；策略快照不可覆盖，调整配置时需显式更新策略快照 ID。

标题 Agent 分批理解全部精确去重标题，跨批合并同一事项；不因来源、海外、未带公司代码或利好词机械删除资料。公司资料索引用于召回相关公司，无名称或关键词命中不能直接排除。
研究以共享事件为单位，正文、公司档案、核验材料按实际问题取得并复用，不逐标题搜索。Tavily每次搜索均绑定当前事件的问题、目标命题或公司、来源路径和预期判断变化；招股书本体排除，年报等背景材料只按必要问题读取直接相关单元。**没有 80／40 篇、正文或搜索总配额，也没有“剩余额度”准入。**
来源事实未变可复用，正文版本、规则或模型输入改变则重新核对；时效、价格和两日判断不得由旧证据冒充。不设整轮或整日金额、token、模型调用、搜索调用总上限；有界并发、单次输出边界、有限重试、逐次用量账和持久暂停保留。未知调用结果单列，不伪装成功或可免费重试。
402 余额不足终止任务；429 按冻结执行包和 `Retry-After` 延后受影响步骤，正方完成后不因反方重试而重跑。设置页显示当前明确绑定、公司资料来源状态、标题／正文／研究／执行状态与公司比较；待核、排除和程序失败分别保留。
通知退避配置仍见 [notification-delivery-v1.json](Backend/neckline/config/notification-delivery-v1.json)。

TuShare 长篇通讯为当前采集源；快讯、全量公告权限未开通。Tavily 用于重点核验，不冒充全市场来源，
日期未知或截止后取得的新增证据不冒充此前已核验。模型/搜索密钥由 App 设置写入服务器，读取不回显。
历史版本配置和付费试跑只作历史证据，不照抄为现行配置；入口见 PROJECT_PLAN 的里程碑索引。

API 使用 `/api/v1/k10/`，通用连接/推送设置仍在 `/api/v1/settings`，设备注册为 `/api/v1/devices`。
API 启动只验证鉴权和既有 schema；GET 不迁移、不启动模型。重任务由独立 K10 worker 执行，timer 只入队。
设置页与 timer 共用显式配置 ID/修订：尚无扫描时也可显示“已配置”，缺失/错误绑定仍报未配置；不从历史扫描或任意最新配置猜选。
worker 自动冻结 D1 选择、采集共享的公司日行情并生成结果修订。行情缺口按运行包明确的间隔和次数有界补采，
常规补数窗口为收盘后 120 分钟、间隔 300 秒；晚启动仍可回填历史固定日期，缺数不会延长 D1/D2。
后端发布路径沿用 `/opt/neckline`（同步 Backend 内容），详细环境项见 [配置样例](Backend/.env.example)。

## 验证

从 Backend 运行 `.venv/bin/python -m pytest -q -o tmp_path_retention_policy=failed`。夹具禁用 `.env`，使用临时数据库与合成行情；
真实 API/凭据和工作数据不作为测试输入。

修改 Swift 后，从 App 依次运行三条门禁：

使用 Xcode 27 及以上、最低 OS 27.0。运行验收复用 iPhone 18 Pro／iOS 27 的「主模拟器」，按名称与运行环境核验，不固定设备 UUID。测试数据库、构建缓存和 QA 副本由本轮版本记录登记并在收尾核验清理。

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

首页优先显示当前机会；已结束推荐和到期记录默认折叠在“历史记录与已结束机会”，原始来源与窗口仍可查看。

## 生产运行与恢复

现役版本、源码和绑定以本页顶部及[主Plan](PROJECT_PLAN.md)为准。服务器`ser657204219523`（`114.66.2.205`），数据库`/opt/neckline/data/neckline.db`，公网`https://nk.linotsai.top`。

2026-09-23 20:19 CST按用户指令暂停早晚报：两报告timer为inactive/disabled，discovery control为closed/user_paused，检查心跳`neckline`为PAUSED，核验时在途任务与未知外呼均0。API/worker及两行情timer继续active/enabled。晚报21:00、晨报08:30是既定计划时间，不表示当前已启用；恢复须用户明确指示。

`/etc/neckline/k10.env`仍绑定策略`k10-v2-production@2`、执行`k10-v2-execution-production@2`与`k10-v2-b82-20260922`。B90至B92只在隔离库验证，不改生产绑定，也不恢复、延期或替代旧失败/过期/已删除任务。

现役恢复集为B81/B82/B89，分别保留B75/B81/B88代码。恢复前必须核对真实部署manifest、目标、绑定与新写；不得用旧整库覆盖发布后业务数据。Schema9历史回退仅在原归档条件成立时适用。根目录`root:root /0755`、数据库`neckline:neckline /0600`保持。

首份新正式报告及APNs交付核验后，清报告专用快照已于9月23日回收；不得再把它列为现存恢复点。当前发布与清理证据见[B82记录第16节](archive/v3.5.1-b82_execution.md#16-今晚第七轮快修-b89已上线)，B81历史发布证据见[B78记录](archive/v3.5.0-b78_execution.md)。
