# Neckline

Neckline 是 A 股生产应用，包含 SwiftUI macOS/iOS 客户端与 FastAPI 后端。当前工作区开发目标为
**3.0.0 / 双端 Build 30 / K10-v1.4**。生产仍为 V2.7/K9，尚未切换；本地构建通过不代表已发布。

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
同批次、同窗口的三组可以对照；同一事件涉及多家公司时另列事件关联，不能相加当作多次独立催化成功。

完整交易计划、价位确认、持有退出、交割单、个人持仓/成交/盈亏和旧 K9 链退出 V3。交易纪律仅作个人提醒。

## 配置与数据

无密钥运行包为 [k10-v1.4.json](Backend/neckline/config/k10-v1.4.json)。全部模型使用
`deepseek-v4-pro`，费用上限明确为空；超时、重试和请求数是明文工程限制，不是选股阈值。
缺配置时显示未配置；记录实际 token，用量缺失或费用口径未核实则明确不可用，不估算充作实付。

TuShare 长篇通讯已核验可用，但响应会截断，适配器拆窗补取并报告缺口。快讯、全量公告权限未开通；
2026-09-06 22:06 CST 已用服务器现有密钥分别实测 DeepSeek V4 Pro 和 Tavily Basic，均 HTTP 200；
Tavily 返回 5 条结果、用量 1 积分，剩余额度未查询。Tavily 只用于重点核验，不冒充全市场源。
随后完成两篇真实通讯的小样本联调：TuShare → DeepSeek 理解 → Tavily 核验 → 公司比较 → 机会卡 → 留下 → 正反分析。
广生堂形成 1 张隔离卡片，欣旺达保留待核，不生成窗口或成绩。最终正反实际请求均已包含 TuShare 原文、
Tavily 核验资料及 11 项行情引用；反方实际请求中的正方全文与存储哈希一致。
Tavily 使用 general 查询，并只凭来源字段或原文明确发布时间判断 cutoff；迟到或日期未知资料不冒充已核证据。
这不是全市场覆盖或策略有效性验证，整晚规模的覆盖、耗时和真实推送仍待检查。模型/搜索密钥由 App 设置写入服务器，读取接口不回显。
首轮扫描需显式指定补取起点，不能伪造已成功水位。

API 使用 `/api/v1/k10/`，通用连接/推送设置仍在 `/api/v1/settings`，设备注册为 `/api/v1/devices`。
API 启动只验证鉴权和既有 schema；GET 不迁移、不启动模型。重任务由独立 K10 worker 执行，timer 只入队。
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

## 切换与恢复

本轮没有发布授权，不运行生产迁移、停旧服务或替换已安装客户端。发布时先依据相邻目录的 `../NB_info.md` 核验目标，
备份服务、数据库、配置、定时器和客户端；停止全部写入并完成 SQLite 检查点后，才能运行显式离线入口
`python -m neckline.k10.migration`。命令要求目标确认、独立备份名和停写声明，校验备份哈希、完整性和新 schema
后才替换数据库；恢复必须匹配备份哈希，并同时恢复同一版本服务/配置/客户端。

迁移仅保留通用市场元数据、连接/设备与 K10 数据，删除旧策略及个人复盘数据；不做旧数据兼容展示。
已有生产备份不能被覆盖。异机加密备份为可选能力，不能代替本次切换的可恢复备份。
