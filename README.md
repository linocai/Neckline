# Neckline

Neckline 是 A 股生产应用，包含 SwiftUI macOS/iOS 客户端与 FastAPI 后端。2026-09-15 当前发布为
**3.3.0 / 双端发布 Build 69 / 后端 Build 72 / K10-v2 / 内部 Schema 9**，公开报告协议保持 Schema 8，后端发布集合为 `v3.3.0-b72`。K9 已退出活动生产。
[后端B72与校验值](https://github.com/linocai/Neckline/releases/tag/v3.3.0-b72)；[客户端B69下载](https://github.com/linocai/Neckline/releases/tag/v3.3.0-b69)；Mac 已安装并验收 3.3.0（69）；iOS 签名归档就绪，通过 Xcode 由用户直接安装，不生成 IPA。

**9月13日原晚报尚未完成，原截止时间9月14日03:00:13已经过去。** 68/86事件完成、3项待核结论、15项查证执行失败，累计23,633,932模型token。新Tavily Key于9月15日通过真实小样本，19:53已开放今晚新报告的正式运行；过期原任务保持failed，不能自动恢复、延长或新建替代任务。9月10日及更早报告已清除，不恢复或重推。

固定 1,089 公司池与完整资料已显式导入生产库，资料仍是 `local_draft_awaiting_user`；批处理全部标题、按事件共享研究及定向资料召回，删除 80／40 全文配额。晚间最多 30 家公司卡，晨间独立更新／新增；日报卡与机会两日成绩分离，混合新旧催化的操作明确对应窗口。未核信息可以条件化推荐并披露来源。

**3.3.0后端Build72已发布并部署。** B69排除日报招股书补证，限制查证于当前事件必要问题；正文按结构定位，公司资料按动作读取，共享来源事实随新增内容更新，原始付费回复可本地重验。B70修复必要限定遗漏、普通词内误报、跨句共享及旧目录恢复；B71从事件唯一来源补齐正文事实省略的重复引用，原付费回复本地重验通过即可复用。B72过滤有合法替代路径的混公司搜索，并复用原付费计划；本地范围拦截记录不再误当搜索结果。缺事实、来源冲突和无效JSON仍拒绝。实际请求容量检查不新增策略总用量上限；字符回放和离线测试不能证明真实费用节省。

正式节奏保持每个交易日09:00晨报、交易日前一自然日21:00晚报，使用交易所日历。B69将明确暂停记为正常跳过（9月15日09:00真实晨间定时触发已验证：退出0、未建任务），配置、日历和存储错误仍报错。异地自动备份功能已删除，发布回退副本保留。

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

**B59起支持双端 BYOK**：设置 → 模型配置，可保存多组 HTTPS Chat Completions 连接、修改端点和模型 ID、替换或清除 Key，启用一组即切换新任务的连接。API 基础地址自动补 `/chat/completions`；密钥留空保留，跨服务商地址更新须同时换 Key 或清除旧 Key。iOS 通过 Xcode 安装，实际发布状态以本页顶部和 PROJECT_PLAN 为准。

每个开始执行的任务单独冻结连接名称、端点和模型；切换到另一组不影响原任务，同一组可轮换 Key。直接改原组的端点／模型或删除原组，会阻止旧任务继续；希望保留未完成任务时应新增连接。旧版本已有外呼但未记录连接身份的任务不能自动猜选并恢复。保存配置不试调模型、不探测余额、不打开报告开关。通用接口不发送 DeepSeek 专用推理参数；供应商对具体模型的支持仍需用户日后实际使用确认。


初始化／升级顺序：确认目标和恢复路径 → 受控schema迁移 → 导入固定资料 → 登记配置修订 → 绑定策略快照 → 核对API配置响应。3.3.0内部Schema9仅新增私有模型响应回执，公开报告协议保持Schema8。生产发布已完成隔离8→9→8→9演练，旧B68 runtime可读取受控回退后的副本；正式迁移保留原业务行与付费账本。API启动和GET不执行迁移。Schema9有回执时禁止自动降级，保持暂停并前向修复。

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

后端发布源码 `ee5c2dee13d03857cfcc85bb7b88d99215b02a16`，不可变标签 `v3.3.0-b72`；后续文档提交不移动标签。服务器 `ser657204219523`（`114.66.2.205`），数据库 `/opt/neckline/data/neckline.db`，公网 `https://nk.linotsai.top`。

2026-09-15 23:14 CST核验：B72 `ee5c2de` 已部署并发布GitHub，客户端仍B69；23:13续做今晚原task，563个原完成检查点、付费账本、冻结输入及9月16日03:00:18截止均保留。混公司搜索计划已本地复用，拦截记录错误对应事件已恢复；另一个事件 `research_0c66f51015fc79526dc7be68b215dd05` 仍报 `investigation_execution_failed`，需继续定位。23:09受控暂停产生了双端未完成推送，是待修通知误报。报告和最终推送尚未完成，心跳每2分钟继续；Xcode暂停处理。

9月13日原报告仍失败／未发布／0卡；592次模型调用、23,633,932 token（输入19,451,196、输出4,182,736）均未增加。9月14日03:00:13原期限已经过去；用户授权今晚新的定时报告，未授权恢复／延期过期原任务或创建它的替代。9月10日及此前的报告、关注和成绩保持删除，配置、股票池、来源进度、行情及付费／送达账本保留。

`/etc/neckline/k10.env`仍显式绑定策略`k10-v2-production`第1修订、执行`k10-v2-execution-production`第1修订及`k10-v2-20260909`快照。无scan的独立配置检查和当前线上真实接口均通过；不能以HTTP200代替DTO状态验证。

Mac `/Applications/Neckline.app`已换装3.3.0（69），Developer ID严格验签、通用架构、Dock目标和单实例通过，发布时实际页面显示新版号、配置就绪和暂停，运行开关现已开放。iOS工程及签名Build69就绪，用户已通过Xcode安装并确认脱离调试器正常使用；Xcode问题按用户指令暂缓，不导出IPA。Mac未公证，网络下载后的Gatekeeper体验未验收。
B72相对B69没有App源码改动，沿用B69客户端与既有验签／验收记录；本次后端快修没有新增Apple构建或换装。
本地后端发布目录：`/Users/linotsai/Lino/releases/Neckline/v3.3.0-b72-20260915/`；服务器正式后端包、wheel与manifest：`/opt/neckline/releases/v3.3.0-b72/`；GitHub五份后端资产与本地SHA256一致。B69客户端归档仍在原`v3.3.0-b69-20260914/`目录。

服务器仅留`/opt/neckline/data/backups/v3.3.0-b71-predeploy/`和`v3.3.0-b72-predeploy/`，分别保留B70／B71代码恢复能力，清理时合计234,062,603字节。gzip解压哈希已验证，B72相同pre/post共用inode；部署86表指纹未变，B72和前版代码均已在当前Schema9隔离副本验证。有私有回执时禁止自动Schema降级；旧快照不能覆盖后续业务写入。

清报告专用`/opt/neckline/data/archive/report-cleanup-20260913/pre-cleanup.db.gz`继续保留，首份清理后正式报告完成且可读再回收；原事故私有付费回复仍是未解决报告的证据。两者不能作为恢复整库覆盖新写入的理由。
Mac可恢复副本为`/Users/linotsai/Lino/app_backups/Neckline-v3.2.1-build62-pre-v3.3.0-b69-20260914.app`。根目录`root:root /0755`、数据库`neckline:neckline /0600`已经复核。B72临时构建、私有原回复、用户／系统测试隔离目录和远端上传／演练产物已按精确归属核验清理，签名归档、安装包、必要证据与上述恢复集保留。

9月15日隔离小样本使用固德电材两篇已存历史资讯，真实CLI→worker→DeepSeek→Tavily→报告API完成1卡：成功轮138,092 token、5次搜索／1次提取、供应商实报5点；包括首次测试窗口设置错误，累计148,887 token。测试无设备／推送，生产86表在测试期间未变。临时报告、数据库、复制的Key、原回复及本地／云端脚本已删除，仅留非敏感用量和清理证据。

当前发布、失败演练修正及清理证据见[3.3.0执行记录](archive/v3.3.0-b69_execution.md)；原报告事故见前版第17节。正式全量运行的耗时和费用尚未验证，不能将本地字符减少或离线复用测试当作实际节省。
