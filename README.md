# Neckline

Neckline 是 A 股生产应用，包含 SwiftUI macOS/iOS 客户端与 FastAPI 后端。2026-09-08 已发布
**3.1.0 / 双端 Build 39 / K10-v1.4 / Schema 7**，后端发布集合为 `v3.1.0-b39`。K9 已退出活动生产。
[下载安装包与校验值](https://github.com/linocai/Neckline/releases/tag/v3.1.0-b39)；Mac 已换装并启动，iOS 通过 Xcode 由用户直接安装，本次不生成 IPA。

全部标题先由 DeepSeek 理解，经全局去重排序及只减不补的标题终检后，晚间最多深读 80 篇、晨间最多 40 篇。
入选正文一次提取关键命题，随后按具体缺口使用 Tavily 搜索、审读和必要补读，再完成公司比较。未核传闻允许正常推荐，但必须保留“未核实”、来源和条件化分析。

**首轮正式试跑失败，生产再次暂停。** 用户授权后于 2026-09-08 21:49 启动 21:00 截止的晚报，但标题批次返回不合格 JSON；21:53 关闭开关及 worker／晚晨 timer，防止自动重复。已完成的标题结果保留。
用户已弃用的 2,472 篇事故批次及关联任务已备份后定点取消，不会补跑。
B39 首轮收到 1,063 标题、完成 384 条；9 次模型调用、92,021 tokens，正文和 Tavily 调用均为 0，没有正式报告。具体失败响应未保存，不能猜测解析根因；B38 的比较失败也不能充作 B39 的效果证据。

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
执行控制为 [k10-execution-v3.json](Backend/neckline/config/k10-execution-v3.json)，显式绑定批准的标题筛选规则与 80/40 篇数限额。
程序只做精确去重；标题 Agent 分批理解全部标题，再跨批合并同一事项并排序。不因来源、海外、未带创业板代码或利好词机械丢弃资料；保留独立催化与重要反证，不足不凑数。
全局清单冻结后才允许正文模型调用。入选缺正文仍占名额且如实显示，不以其他文章替补；重试和恢复沿用同一清单。
Tavily 只核验入选事件，每次搜索必须关联具体问题、查询意图、来源路径和预期判断变化；默认使用搜索摘录，必要时申请全文。全文不预留名额，只使用冻结清单之外的剩余额度，不挤占入选正文。
来源事实未变可复用，正文版本、规则或模型输入改变则失效；时效、价格和两日判断仍重新核验。
不设整轮或整日金额、token、模型调用、搜索调用总上限。逐次实际用量账、有限重试、缓存与持久暂停保留；未知调用结果单列，不伪装成功或可免费重试。

Schema 7 支持生产 4→7 与本地 6→7，增加可恢复研究记录，保留既有标题、正文准入与实际用量审计，迁移默认持久暂停。设置页显示标题、正文、研究与执行状态以及完整公司比较；待核、排除和程序失败分别保留。
缺执行规则或批准绑定时安全停止；历史 V1/V2 执行包不能绑定新的付费任务。
通知退避配置仍见 [notification-delivery-v1.json](Backend/neckline/config/notification-delivery-v1.json)。

TuShare 长篇通讯为当前采集源；快讯、全量公告权限未开通。Tavily 用于重点核验，不冒充全市场来源，
日期未知或截止后取得的新增证据不冒充此前已核验。模型/搜索密钥由 App 设置写入服务器，读取不回显。
B36 事故与先前付费测试仅作历史证据，详见 [B36 执行记录](archive/v3.0.4-b36_execution.md)；
本轮仅复用 B38 冻结的 1,430 条有效标题和 37 篇历史正文做离线协议验证；不启动新的付费外呼，合成模型结果不作为真实筛选质量证据。

生产策略配置修订 3 保留每来源必填的 `lateArrivalReplaySeconds`，TuShare 显式设为 `86400`，
用于有界回补晚到资料，记录实际回查范围和缺口。它不是无限历史覆盖，也不允许把新取得的资料回填为旧推荐。
既有修订原样保留；API 和停用 timer 的显式绑定现为策略修订 3、执行修订 2，不得改写历史修订或用代码默认补齐。

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

源码提交 `a3732ab3a1c6ddf7042246c30710ee109fa8208b`，不可变标签 `v3.1.0-b39`；后续发布记录提交不移动标签。
Mac 归档来源 `7c52f0a` 的 App 源码树与该发布提交一致，iOS 归档来源为发布提交；真实来源见 `manifest.json`。
服务器为 `ser657204219523`（`114.66.2.205`），数据库 `/opt/neckline/data/neckline.db`，公网 `https://nk.linotsai.top`。
API active+enabled，四范围配置与实际 DTO 通过，未鉴权请求为 401；APNs 密钥可读／可签名且 readiness 就绪，本轮未发送测试推送。
`/etc/neckline/k10.env` 显式绑定策略 `k10-v1.4-production` 修订 3、执行 `k10-execution-production` 修订 2。
首轮失败后数据库 run control 为 `closed`，接口投影 `paused`；worker 与晚／晨 timer inactive+disabled，行情更新／重试 timer 保持原态。标题 policy `k10-title-triage-v1@1` 已按既有批准配置原样登记；首次启动暴露的 readiness／部署登记缺口见 PROJECT_PLAN。

Mac 位于 `/Applications/Neckline.app`，Developer ID 严格验签、通用架构和单实例启动通过；实际设置页确认 Build 39、Prod、四项已配置及暂停状态。
Mac 尚未公证，网络下载后可能被 Gatekeeper 拦截；严格签名通过不代表公证通过。
iOS 真机签名归档及工程版本／签名配置已就绪，由用户通过 Xcode 安装，不导出 IPA。
本地签名归档与包位于 `/Users/linotsai/Lino/releases/Neckline/v3.1.0-b39-20260908/`。
GitHub 6 个资产下载后与本地逐一校验；后端 tar、wheel 和 runtime manifest 保存在 `/opt/neckline/releases/v3.1.0-b39/`。

成功发布恢复集为 `/opt/neckline/data/backups/v3.1.0-b39-predeploy-3/`：原 B36 runtime、旧绑定、迁移前后数据库与回执。
停下全部写入者后建立基线；真实副本演练和正式 Schema 4→7 迁移均核对 50 个旧表全部旧列／行等价。通知 Schema 保持 2。
前两次因发布清单的占位文件和 API 暂停状态断言错误而安全回滚；原恢复集 `v3.1.0-b39-predeploy`、`v3.1.0-b39-predeploy-2` 保留。具体证据见 [B39 执行记录](archive/v3.1.0-b39_execution.md)。

- 迁移前数据库 SHA256：`2c5ea97e77ea02c83077e9f30d05fc620973f824a4293e14274344771c4d6dd2`。
- 迁移后数据库 SHA256：`f4c2c7e6d1ad22daceb45240dd0eb964b852109f4c15407a497ea0d9b2737eb3`。

回滚必须先停下所有写入者并保存最新现场；只有确认升级后没有新增业务写入，才可恢复同一恢复集的 runtime、wheel、数据库和绑定。
API、客户端和行情 timer 已恢复运行，不能直接假定当前库仍等于发布快照；存在新写入时优先前向修复。
保持 `/opt/neckline` 为 root:root / 0755、数据库为 neckline:neckline / 0600，复核 health、鉴权、配置、完整性与定时器。
Mac 可恢复备份为 `/Users/linotsai/Lino/app_backups/Neckline-v3.0.4-build36-pre-v310-b39-20260908.app`。

受控恢复只允许对无正式发布批次、已失败且有冻结输入的 scan 执行 `neckline.k10.cli recover-scan`，
必须提供原引用摘要和相同执行配置。B39 复用原任务，保留标题清单、正文准入、实际用量及成功检查点；不得重新采集、回改策略、换输入或重设时限。未知外呼结果仍阻止盲目重发。
诊断使用只读 API `GET /api/v1/k10/scans/{scan_id}` 和 `GET /api/v1/k10/operations/readiness`。
本轮生产恢复任务、只读验收与下一步见 PROJECT_PLAN；含凭据的数据库/备份只留服务器，禁止下载或公开。
