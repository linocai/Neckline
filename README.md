# Neckline

**当前发布为3.5.1（82）**：K10-v2，后端及macOS已部署并启动，iOS签名归档就绪，由用户通过Xcode安装，不生成IPA。[发布下载与校验值](https://github.com/linocai/Neckline/releases/tag/v3.5.1-b82)；源码`60f4822b2a13dff873b2fa4d545ecace1235c420`。

本版修复标题输出噪声、研究跨轮续跑、重复命题、精确付费回执恢复、晨报收口和瞬时存储争用等报告阻碍。选股规则、固定公司池和报告时刻不变：晚报 **21:00** 启动、不设整报业务截止；晨报交易日 **08:30** 启动、**09:20** 前提供可读结果。四个定时器保持active/enabled，资讯处理开放，推送就绪；未补跑历史任务或额外调用真实provider。

内部 **Schema10 / 新报告Schema9 / 历史Schema8** 不变，本次无DDL。生产策略`k10-v2-production@2`、执行`k10-v2-execution-production@2`，绑定`k10-v2-b82-20260922`；策略仅变更快照身份，原参数与1,089公司资料不变。资料仍为`local_draft_awaiting_user`。发布前后91张表的原业务/付费行保留，5份历史报告及今晨18条安全材料可读；历史失败仍为失败，不冒充新正式报告。

双端正式归档、严格签名、三项Apple构建、真实FastAPI→Swift三态与历史读取、双端画面及生产副本/回退验证通过。macOS采用既有未公证Developer ID方式；两部OS27手机开发服务和符号已按9月22日更新指令核验，18Pro旧符号告警为已修复误报。真实模型质量、耗时与首份正式报告交付需正常任务验证，不能由离线通过替代。恢复和清理已完成，详见[本版记录第8节](archive/v3.5.1-b82_execution.md#8-一条龙发布2026-09-22)。

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

当前生产策略参数源为 [k10-v2.json](Backend/neckline/config/k10-v2.json)（生产revision2仅将strategySnapshotId改为`k10-v2-b82-20260922`），执行包为 [k10-execution-v4.json](Backend/neckline/config/k10-execution-v4.json)（生产revision2）；用户当前生产连接为 `deepseek-flash`，可在 BYOK 显式切换实际端点和模型。两个配置包均须显式登记修订并与策略快照绑定；缺任何一项都报“今天没跑成 · 参数未配置”，不从扫描历史或任意最新修订猜选。

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

发布源码`626c902097a91481513ac9792257309858c9e031`，不可变标签`v3.5.0-b81`；后续文档提交不移动标签。服务器`ser657204219523`（`114.66.2.205`），数据库`/opt/neckline/data/neckline.db`，公网`https://nk.linotsai.top`。2026-09-21 15:49 CST核验125份runtime文件与发布manifest一致，数据库完整性正常，API/worker active，发布时四timer关闭；同日15:59按用户新指令恢复active/enabled，晚报下一次21:00、晨报次日08:30。行情18:30更新、19:30/20:30有限重试。

`/etc/neckline/k10.env`显式绑定策略`k10-v2-production`第1修订、执行`k10-v2-execution-production`第1修订及`k10-v2-20260909`快照。无扫描的独立配置检查和线上DTO检查通过，原开关保持open。禁止自动恢复、延期或替代旧过期任务，已删报告不重放；旧事故证据保留在3.3.0归档。

Mac `/Applications/Neckline.app`已换装3.5.0（81），Developer ID严格验签、arm64/x86_64；实际页面确认版本、线上历史报告与配置正常。Mac未公证，网络下载后的Gatekeeper体验未验收。iOS签名归档及工程Build81就绪，实际真机安装仍由用户在Xcode完成。

本地发布根`/Users/linotsai/Lino/releases/Neckline/v3.5.0-b81-release-20260921/`保留双端xcarchive、六份发布资产、B69 Mac回退副本（`recovery/Neckline-3.3.0-b69.app`）与必要证据。服务器正式包位于`/opt/neckline/releases/v3.5.0-b81/`；GitHub六资产大小及SHA256均匹配。

恢复集仅留`/opt/neckline/data/backups/v3.3.0-b75-predeploy`和`v3.5.0-b81-predeploy`，分别保留B74/B75代码；压缩快照已解压核验哈希。B81回退B75须先停机核验旧数据未变、Schema10五张新表为空，再执行已演练的受控10→9与代码回退；已有新写则前向修复，禁止旧整库覆盖。根目录`root:root /0755`、数据库`neckline:neckline /0600`保持。

清报告专用`/opt/neckline/data/archive/report-cleanup-20260913/pre-cleanup.db.gz`继续保留，首份新正式报告完成且可读后回收。此次本地临时目录及远端上传/演练副本已按归属、内容和打开句柄核验回收；详细证据、测试限制及恢复条件见[3.5.0执行记录第12节](archive/v3.5.0-b78_execution.md)。

9月21日下午恢复时的行情补采发现daily_basic两项字段尚未就绪，保留原分区；晚间更新/重试及20:55报告前检查已安排。今晚心跳跟踪至报告实际可读和通知结果确认，完成后只暂停心跳，四个正式定时器继续运行；详见本版记录第13节。
