# Neckline

Neckline 是 A 股生产应用，包含 SwiftUI macOS/iOS 客户端与 FastAPI 后端。2026-09-08 已发布
**3.0.4 / 双端 Build 36 / K10-v1.4 / Schema 4**，后端发布集合为 `v3.0.4-b36`。K9 已退出活动生产。
[下载安装包与校验值](https://github.com/linocai/Neckline/releases/tag/v3.0.4-b36)；Mac 已换装并启动，iOS development IPA 由用户自行安装。

Build 36 修复昨晚大批量扫描被单条模型异常中断的问题：HTML 正文提纯、有界并行理解、逐项检查点、分片续跑、
有限重试和实际进度。冻结输入恢复不重新采集；缺证据或异常资料如实标为部分覆盖。推送增加可签名检查、阻塞和持久退避。

**运行已被用户紧急叫停。** K10 worker和晚/晨定时器已停止并禁用，今晚不再自动扫描；API保留读取。
当前“全量资料逐篇送模型”方案未经用户接受，必须先重议程序初筛、调用规模和预算，并获明确授权后才能恢复。
强制停止可能让历史任务仍显示执行中；以进程/单元停止回执为准，不代表后台还在调用。

本地目标为 **3.0.5 / 双端 Build 37 / Schema 5**，本地施工和离线验收已完成，尚未发布；付费调用和生产恢复仍未获准。
遵循当前全局工作流：主 Plan 保存当前计划，详细契约和证据进入其链接的 archive 版本记录。
iOS 后续通过 Xcode 由用户直接安装，默认不再生成或交付 IPA；历史 B36 发布资产保留原样。

唯一工程状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，产品与视觉方向见
[Neckline V3 前瞻设计](archive/Neckline_V3_前瞻设计.md)。策略研究位于相邻 `whynotme` 工程；
运行时不读取或导入研究仓。根目录仅保留 App、Backend、archive、AGENTS.md、PROJECT_PLAN.md 和本文件。

## V3 功能

入口为 **机会 / 关注 / 选股表现 / 设置**。事件共同事实只讲一次，系统先给出主推、备选或并列，说明
优先理由、差距和改变排序的条件。同公司同批次多催化共用公司卡与一次选择；晚间最多 30 家新机会公司。
留下才启动正方、反方各一轮，双方共享冻结证据，反方另读正方全文；全文、来源与修订可追溯。

双端已按参考图重做白色轻卡、蓝色线性图标与文字层级。iPhone 逐张浏览公司卡，略过/留下固定在底部，
浏览不提交选择；底栏为机会、关注、选股表现，设置从齿轮进入。macOS 使用公司列表与阅读区双栏，
正反观点并排；原始资料与搜索摘录分开标识，正文引用可定位到冻结资料修订。

仅创业板、无股价上限，排除 ST/*ST 和 SW2021 白酒Ⅱ（801125.SI）全部成员。21:00 为晚扫资料截止
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

全候选五段晨报已就绪，配套事件整体公司比较、带来源及缺口的历史同类资料、追加追问与正反版本阅读链。
行情逐字段展示双源核验、单源回退或冲突原因；重叠机会保留实际命中记录，缺数与异常不会充作未命中。
撤回与两日到期为终态，普通资料更新和补充分析不会重新激活旧机会。

完整交易计划、价位确认、持有退出、交割单、个人持仓/成交/盈亏和旧 K9 链退出 V3。交易纪律仅作个人提醒。

## 配置与数据

策略包为 [k10-v1.4.json](Backend/neckline/config/k10-v1.4.json)，全部模型继续使用 `deepseek-v4-pro`。
3.0.5 将执行控制独立为 `k10-execution-v2`：必须显式批准非空筛分模板、资料包限制，以及整轮、各阶段和每次请求预算；没有生产默认数值。
资料先程序去重、筛分、合包，再轻量理解、事件优先级和 Tavily 重点核验；空事件不再自动补读全文。
来源事实未变可复用，正文版本、规则或模型输入改变则失效；时效、价格和两日判断仍重新核验。
每次实际模型/搜索请求先原子预留，重试单独计入，未知用量保留预留。额度不足留下待处理队列；缓存命中不收费。

Schema 5 迁移默认持久暂停。设置页显示暂停、未配置、处理计数、事实缓存命中和预算状态，并可再次暂停。
批准模板与数值额度、明确恢复前，不安装生产执行配置、不启动扫描。原 B36 执行绑定只保留为冻结历史，不能绕过 V2 门禁。
通知退避配置仍见 [notification-delivery-v1.json](Backend/neckline/config/notification-delivery-v1.json)。

TuShare 长篇通讯为当前采集源；快讯、全量公告权限未开通。Tavily 用于重点核验，不冒充全市场来源，
日期未知或截止后取得的新增证据不冒充此前已核验。模型/搜索密钥由 App 设置写入服务器，读取不回显。
B36 事故与先前付费测试仅作历史证据，详见 [B36 执行记录](archive/v3.0.4-b36_execution.md)；
本轮仅使用冻结 2472 篇资料作离线验证，零真实模型/搜索调用，不以它宣称生产运行成功。

生产配置修订 2 包含每来源必填的 `lateArrivalReplaySeconds`，TuShare 显式设为 `86400`，
用于有界回补晚到资料，记录实际回查范围和缺口。它不是无限历史覆盖，也不允许把新取得的资料回填为旧推荐。
修订 1 原样保留，API 和 timer 的显式绑定已更新为修订 2；不得改写现有修订或用代码默认补齐。

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
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' -derivedDataPath /tmp/neckline-v3-qa/macos NK_BUNDLE_SUFFIX=.qa.livev14 CODE_SIGNING_ALLOWED=NO build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/neckline-v3-qa/ios NK_BUNDLE_SUFFIX=.qa.livev14 CODE_SIGNING_ALLOWED=NO build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/neckline-v3-qa/ios NK_BUNDLE_SUFFIX=.qa.livev14 CODE_SIGNING_ALLOWED=NO build-for-testing
```

Debug 参数 `-K10SyntheticUI` 使用独立演示数据和空凭据，不联网、不注册推送；不能用于宣称真实模型已验通。
测试仅复用上述两个目录和 `top.linotsai.neckline.qa.livev14`，每平台最多一个运行实例，启动前先退出上一实例。
结束后退出 QA App、停止临时 API、移除临时 token，清理过期副本；保留生产客户端和签名归档。
真实隔离 QA 显式设置 Debug 进程变量 `NK_DISABLE_PERSISTENT_CREDENTIALS=1`，此时只取进程
`NK_API_TOKEN`，不读写持久凭据。`NK_QA_TAB`、`NK_QA_READING` 仅供 Debug 页面定位；
macOS 的 `NK_QA_RENDER_PATH` 只离屏渲染本 App 的 SwiftUI 视图，不能代替真实窗口的点击/滚动验证。
构建、测试运行和实际页面验证是不同检查，当前完成情况记在 PROJECT_PLAN。

## 生产运行与恢复

源码提交 `6017a0c36bf1bbc6e842ef512ca66a29b7e7f607`，不可变标签 `v3.0.4-b36`；后续发布记录提交不移动标签。
双端签名归档来源为 `c6e91795617d778503820377f6d843587fccc36c`，其 App 源码树与发布提交完全相同，manifest 保留真实归档来源。
已核验服务器 `ser657204219523`（`114.66.2.205`）、`/opt/neckline/data/neckline.db`、公网 `https://nk.linotsai.top`。
发布时 API/worker 验收通过；用户叫停后仅保留 API 读取，worker 已禁用，未鉴权请求为 401；三范围配置与实际 DTO 通过，K8/K9 无活动链。
`/etc/neckline/k10.env` 同时绑定策略 `k10-v1.4-production` 修订 2、执行 `k10-execution-production` 修订 1。
晚/晨 timer 原为21:00/09:00，当前已停止并禁用；行情更新单元未改动。未经用户明确授权不能恢复模型worker和扫描timer。

APNs 对应私钥从受控历史部署备份恢复，保持 neckline / 0600，实际签名和 readiness 通过。
此前积压的两条通知在 11:42 均有两份设备投递台账；这证明服务端发送成功，不等于用户已阅读。
历史失败尝试次数保留，不清零掩盖事故；恢复后无重复无效发送或待重试积压。

Mac 位于 `/Applications/Neckline.app`，严格验签、通用架构及单实例启动通过，实际设置页确认 Build 36、Prod、三范围配置和推送就绪。
iOS 包为 `/Users/linotsai/Downloads/Neckline-v3.0.4-b36-iOS-development.ipa`，由用户安装。
GitHub 六文件下载后 SHA256 与本地逐一一致；后端归档、wheel 和 manifest 保存在 `/opt/neckline/releases/v3.0.4-b36/`。
QA 临时客户端与 API 已退出，过期编译产物释放约 714 MiB；正式包、截图和验证日志保留。

本次恢复集位于 `/opt/neckline/data/backups/v3.0.4-b36-predeploy/`：`receipt.json`、B35 runtime、旧绑定/部署单元及迁移前后数据库。
停止全部写入者后才建立唯一基线；真实副本演练和正式迁移均核对 45 个旧表的全部旧列/行等价。
追加 Schema 4、通知 Schema 2 与执行配置修订 1，策略修订 2 和原任务/2472 篇资料保留。

- `neckline-pre.db` SHA256：`e2c6e4f8b17bc95a80121784dc0049a6fb2fd61743439a3142caf1d01b750c7d`
- `neckline-post-migration.db` SHA256：`4347d00aa59e3f68db2523f1898ee9a5faddbe7f5de25ea767a641491173cf3e`

新 worker 已产生业务写入、后因用户要求停机，现阶段故障只能先备份现场再前向修复，不能用迁移前快照覆盖新增任务、候选或成绩。
仅在 B36 业务写入前，受控部署助手才允许恢复同一恢复集的 B35 runtime、wheel、数据库和配置；该时点已过。
任何修复保持 `/opt/neckline` 为 root:root / 0755、数据库为 neckline，并复核 health、鉴权、配置、完整性与定时器。
Mac 可恢复副本为 `/Users/linotsai/Lino/app_backups/Neckline-v3.0.4-build35-pre-b36-20260908.app`。

受控恢复只允许对无正式发布批次、已失败且有冻结输入的 scan 执行一次 `neckline.k10.cli recover-scan`，
必须提供原引用摘要和显式执行配置。已恢复任务不得重复建单、回改策略、换输入或重设时限。
诊断使用只读 API `GET /api/v1/k10/scans/{scan_id}` 和 `GET /api/v1/k10/operations/readiness`。
本轮生产恢复任务、只读验收与下一步见 PROJECT_PLAN；含凭据的数据库/备份只留服务器，禁止下载或公开。
