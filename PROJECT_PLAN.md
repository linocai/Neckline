# Neckline · V2.5.2 Build 14 控制面

> 当前生产：V2.5.2 · Build 14
> 现行引擎：K9 ｜当前阶段：10:00 双端同步快修、macOS 换包与发布验证完成
> 更新：2026-08-24 ｜分支：`main` ｜`archive/` 保持空（仅 `.gitkeep`）

本文件是唯一现行计划。Git 负责历史；不建立版本日记、平行计划或归档施工记录。

## 1. 目标与非范围

本版先把已上线的 K9 做成可恢复、可审计、可安全使用的生产系统：16:05 数据真的就绪才放行，漏跑可恢复但绝不暗中重跑报告；客户端凭据与上传入口收紧；已完成迁移的旧壳彻底删除。

- 不改 K9 机械策略、四通道、名额、六项已裁定策略契约或参数包；不跑正式报告。
- 不恢复 K8、篮子、持仓、旧校准、旧通知，不重做复盘业务；研究和回测仍只在 `whynotme`。
- Build 14 只修客户端结算同步：不改后端、策略、报告、通知或生产数据，不重跑报告。
- 本轮已完成双端归档、macOS 换包与验证；iOS 归档由用户自行安装。

## 2. 已裁定且不得重开

1. K9：事实包 → 四通道机械召回 → 排序/名额 → 解释 → 预案 → 次日两拍 → 成绩；LLM 不进机械决策。
2. K8 及篮子、六关、旧校准、持仓、旧通知和兼容壳物理删除，Git 是唯一历史。
3. 策略参数无默认值；缺失或非法时只报“今天没跑成 · 参数未配置”，不产出可信清单。
4. 排序用上方机械空间，预案用第一压力位；行业强度用申万二级成员当日涨跌幅中位数；全天停牌才排除。
5. 放量倍数只用一份共享 `V`：P1 为 `≥ V`、P3 为 `< V`，不得再分档。
6. 消息剔除持续按机械名次补位至原目标或后备耗尽；成立率为 `confirmed / (confirmed + rejected)`。
7. 9:26 只给放弃/待观察；10:00 是终值。周日报告 `report_date=周日`、`trade_date=前周五`。
8. 联网只经 Tavily，推理由用户配置的 OpenAI 兼容 Provider；方向背景每事实包最多一检索一推理，失败不影响 K9。
9. 通知只保留 `report_ready`、`precall`；发布、验收、故障恢复均不得擅自重跑正式报告。
10. GET/read helper 零 DDL；`init_schema()` 只在 API 启动、显式写命令或确认的发布迁移中运行。
11. 远端服务地址覆盖只允许 HTTPS；API Token 必须迁入本机 Keychain，不能继续留在 UserDefaults。
12. DeepSeek 的 Provider 级并发固定为 3；调用总量、K9 结果与现有用量账口径不变，连续观察 3–5 个交易日。
13. 交割单每请求最多 5 文件：单文件 10 MB、原始合计 20 MB、解压后 100 MB、最多 20 个 sheet、20 万行、200 万单元格。
14. 漏跑只自动补齐当前 K9 所需、最多 60 个交易日的数据前置；绝不自动补跑/重跑报告或 APNs。交易日历必须覆盖到下一自然年年底。
15. 通用 S3 加密增量备份是可选能力，默认不配置、不启用，也不阻塞发布；将来用户明确启用后才执行每天 02:30 的异机备份，保留 30 份日备份和 12 份月备份，解密私钥只在用户 Mac。发布前仍必须制作并验证服务器本地一致性回滚包。

## 3. Build 证据与待审风险

- 发布前完成生产只读审计：28 张现行应用表、无退役表，60 个事实包均在 `version=fp-2` 布局；发布后逐表行数与一致性复核均通过。
- 两轮 Review 修复均完成：daily/evening timer 禁止 missed-slot 追跑；恢复改为普通周期的最近 60 个交易日数据扫描；news/explain/playbook 的并发 worker 只做调用和纯计算，用量、版本和保存均回主线程串行。备份恢复器在任何 I/O 前完整校验 manifest，隔离恢复、全量校验后才原子落位。
- 现役源文件已清除退役产品考古词，并由 source guard 防止 K8、六关、篮子、退潮及已删 UI 名称回归。
- 第三轮 Review 的消息面提示词遗漏已修复：实际 user message 明确四类（立案、暴雷、监管、减持），并由回归测试锁定。
- 全量后端 pytest 为 `1365 passed, 21 warnings`；API 与复盘冒烟均通过；macOS 与 iOS 的三条构建及两端真实测试均通过。
- Build 14 在上海时间 09:59:55–10:06:00 每 10 秒只读结算终值，拿到当日非空且全部定案的快照后停止；10:00 后启动或回到前台会补查，未完整则下次激活仍可重试。
- 结算自动刷新不生成报告、不发通知、不写服务端；交易日与时区边界已有单测，双端五道 App 门禁重跑全绿。
- S3 备份脚本在被调用但缺少 S3、公开密钥、保留开关或隔离恢复验证器时仍 fail-closed；该能力本次不配置、不调用、不启用 timer，也不作为发布门禁。
- 第四轮独立复审已核验：恢复命令不触发报告/APNs，备份对象与密钥边界、legacy 删除、客户端 Keychain/HTTPS 与离线边界均通过；若未来选择 S3，首次配置与异机恢复演练是启用 timer 的前置条件。

## 4. 施工顺序与共同纪律

1. **Plan**（本文件）：本轮数值与产品边界已裁定；任何新增数值仍不得自设默认值或启用新行为。
2. **Build**：按 P1 → P2 → P3 小切片施工；测试只用临时 DB/隔离目录，禁止读取或改写 `Backend/data/`。
3. **独立 Review**：另一代理逐项检查边界、故障路径、删除完整性与测试漏洞，不创建 archive 文档。
4. **Builder 修复**：只修 Review 已证实的问题，复跑受影响测试及完整门禁。
5. **独立复审**：确认修复、无 K8/legacy 残留、无凭据泄漏、计划验收逐条成立。
6. **部署前门禁**：确认目标主机、备份可恢复、迁移边界、timer 状态与回滚命令；到此停下等待授权。

所有运行记录进受保护的服务日志/CI 输出；本文件只替换当前状态，不追加流水。每片生产顺序均为：隔离测试 → 独立 Review → 确认目标的备份/只读核验 → 部署但暂不启用新单元/配置 → 验证 → 仅启用本片 → 观察；失败则停用本片并按其回滚项恢复。任何失败必须有可读原因，不得用昨天事实、旧缓存、成功退出码或自动报告重跑掩盖。

## 5. P1 · 数据 readiness、日历、恢复与备份

### P1-A 16:05 关键输入与预检

- **目标/边界**：`Backend/scripts/daily_update.py`、`Backend/neckline/facts/{pack,store}.py`、`Backend/neckline/calendar/`、新增只读 readiness helper 与 `Backend/tests/test_*readiness*.py`；用“目标交易日的必需分区 + 衍生数据 + 申万二级归属 + 完整且冻结的事实包”作为唯一放行事实，覆盖率和成绩线仍是非阻断观察项。
- **行为**：日更先更新，再执行显式只读 preflight；任何必需项失败、残包或无法冻结，进程非零退出，systemd 显示失败。19:00 的 facts 段先验同一 `trade_date`；未就绪时禁止 K9、所有付费 LLM 与可信清单，但 report 段仍零 LLM 持久化带可读缺口的 `not_run`，使 `/selection/latest` 与 App 明确显示“今天没跑成”而非停在旧报告。通知仍遵循现有 `report_ready` 的 unavailable 文案与开关；周日验前周五事实包，绝不以旧日包顶替。
- **验收/回滚**：模拟每个关键失败均非零、无 K9/付费 LLM/可信清单，并生成可读 `not_run`；`/selection/latest` 不回退旧报告，通知只按既有开关与 unavailable 契约发生。完整包返回 0；GET 仍零 DDL。代码回滚前保留原服务单元和已冻结包；数据不迁移、不覆盖、不删除。

### P1-B 排程、漏跑与滚动交易日历

- **目标/边界**：`Backend/deploy/neckline-{daily,evening}.{service,timer}`、`Backend/scripts/{daily_update,init_calendar}.py`、`Backend/neckline/calendar/trading_calendar.py` 与对应测试。
- **行为**：日历成为生产前置条件，自动滚动拉取并验证官方 `trade_cal` 至下一自然年年底；生产范围外不得退化为工作日近似。定时器与恢复命令以“目标槽位/目标交易日/ready 事实包”判重，自动补齐当前 K9 所需且最多 60 个交易日的数据前置；绝不自动补发报告或 APNs，19:00 仍只走正常排程。
- **验收/回滚**：模拟断机、同日重复触发、周日/周五休市、跨年覆盖不足和异机恢复，均不重复报告且有可读状态。先 `daemon-reload`、只启用恢复前置，再观察下一槽；回滚为停用新 timer、恢复已保存单元，不碰事实数据。

### P1-C 可选的异机加密备份

- **目标/边界**：新增 `Backend/scripts/backup_*`、`Backend/deploy/neckline-backup.{service,timer}`、`.env.example`（只列变量名）、README 操作节及临时目录测试；不提交备份、密钥或生产状态。
- **行为**：能力默认禁用且不影响发布、API、日更或报告。用户将来明确选择对象存储后，才用 SQLite 在线一致性快照 + 冻结事实 parquet 生成校验 manifest，加密增量传至通用 S3；每天 02:30 执行，保留 30 份日备份和 12 份月备份。备份成功只在远端校验、可在隔离目录恢复并通过完整性检查后成立；服务器仅持加密所需公开材料，解密私钥只在用户 Mac，日志一律脱敏。
- **验收/回滚**：本地伪 S3 覆盖上传失败、校验失败、恢复成功、私钥不在服务器与保留策略；生产完成配置和一次只读恢复演练后才显式启用 timer。未选择 S3 时 timer 保持 disabled；若手动调用但配置缺失，服务拒绝执行而非退回本机假备份。回滚为停用备份单元，原数据不删。

## 6. P2 · 并发、客户端安全、上传与退役边界

### P2-A DeepSeek 有界并发与观测

- **目标/边界**：`Backend/neckline/{llm,explain,playbook}/`、`report/evening.py`、现有 usage 账和单测；只并行互不依赖的逐票 news/explain/playbook 调用，方向背景单次调用和 K9 机械链不变。
- **行为**：单一 Provider 级有界执行器固定并发 3，保留输入/输出排序、逐票降级、Tavily 一票一次与既有 usage 行；不共享 prompt、密钥或搜索原文。
- **验收/回滚**：伪 provider 证明上限、稳定排序、单任务失败隔离和 usage 完整；观察 3–5 个交易日的 DeepSeek/Tavily 账与耗时。回滚为配置关闭并发/恢复串行，不改报告或策略结果。

### P2-B Token、远端地址与双平台真实测试

- **目标/边界**：`App/Neckline/Networking/AppConfig.swift`、`SettingsView.swift`、`APIClient`、`App/NecklineTests/{AppConfigDefaultTests,URLGateTests}.swift` 与必要项目配置。
- **行为**：一次性把旧 `NK_API_TOKEN` 从 UserDefaults 迁入 Keychain，成功后删旧键；Keychain 是持久唯一源。Release 拒绝任何 HTTP 远端 override；内置 Debug loopback 是单列本地开发能力，非法地址显示配置错误而非暗中改打生产。
- **验收/回滚**：迁移、无 Keychain、清除、泄漏检查与 HTTPS gate 全覆盖；真实执行 macOS `xcodebuild test`，并发现可用 iOS Simulator 后真实执行该目标的 `xcodebuild test`。回滚 App 时旧版只能用用户重新填写 Token，不能恢复明文偏好设置。

### P2-C 交割单应用层限额

- **目标/边界**：`Backend/neckline/api/app.py`、`review/parse.py`、配置读取与 API/解析测试。
- **行为**：每请求最多 5 文件；在读完整文件前按单文件 10 MB、原始合计 20 MB 流式限额拒绝，随后限制 xlsx 解压后 100 MB、20 个 sheet、20 万行和 200 万单元格；临时文件总会清理。`Content-Length` 仅作早拒，真实已读字节才是权威；超限返回用户可理解的 4xx，不进入解析或落库。
- **验收/回滚**：多文件累计、伪造长度、zip bomb、每一项边界与合法券商样本和清理路径均测试；回滚只恢复旧端点代码，不保留上传文件。

### P2-D 一次性删除已迁完的 legacy

- **目标/边界**：`facts/store.py`、`db.py`、相关 tests、部署配置和现役 source 注释。
- **迁移顺序**：先对确认的生产备份做只读审计：28 张现行表、无退役表/列、无 legacy fact-pack 路径、
  无旧 unit/route/config/data 引用；任一命中即停止删除并报出位置。审计全绿后才删 legacy 路径回读/搬迁、
  retired table/column/job-event 迁移壳及其 tests；新库只建当前 schema。
- **验收/回滚**：全仓反向搜索无运行时代码/路由/设置/测试依赖，当前事实包读写与全量测试通过。
  部署前备份是唯一回滚材料；不得把兼容代码、空表或迁移壳重新塞回主线。

## 7. P3 · 推送卫生、文档状态与源文件收口

- **APNs**（`api/{notify,stores}.py`、`push/apns.py`、tests）：仅确认永久失效的 APNs 响应才原子删除 token；
  传输、鉴权、限流和未知失败只计失败不删除。验收覆盖 410/明确设备失效及全部瞬态反例，日志只留尾号。
- **版本/文档**（`Backend/pyproject.toml`、README、PROJECT_PLAN）：元数据升至 2.5.2；发布前区分
  “Build 12 当前生产”与“Build 13 待发布”，发布后以真实验证结果收口为 Build 13 当前生产。
- **考古注释**（现役 `Backend/neckline`、`scripts`、`deploy`、`App/Neckline`）：删除 K8、旧版本号、旧章节号、
  已删组件和迁移故事；保留当前用户契约、安全理由与必要运维说明。验收为 source scan + 全量门禁；不改业务语义。

## 8. 测试、Review 与部署前门禁

```bash
cd Backend
.venv/bin/python -m pytest -q
bash scripts/smoke_api.sh
bash scripts/smoke_review.sh

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' test
# 由 `xcrun simctl list -j devices available` 发现并记录可用 UDID：
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination "platform=iOS Simulator,id=$IOS_SIMULATOR_UDID" test
```

独立 Review/复审还必须检查：临时 DB 隔离、无凭据/备份入 Git、P1 失败非零、无自动报告重跑、
日历不近似、可选 S3 能 fail-closed、legacy 无残留、APNs 删除只针对永久失败。部署前在已确认目标
制作本地一致性回滚包并做完整性/迁移审计，检查 systemd 语法及 next trigger；S3 timer 本次保持 disabled。

## 9. 最终验证与当前下一步

- Plan → Build → Review → Builder 修复 → 第四轮独立复审全部完成；第四轮独立复审无 P0/P1/P2，上一轮 news scan 四类 P2 已关闭。
- Backend 全量测试：1365 passed、21 warnings；本版发布门禁通过。
- Build 14 源码归入 `main`；macOS / iOS Release 签名归档均校验为
  `2.5.2 (14)`，交付目录为
  `/Users/linotsai/Lino/releases/Neckline/v2.5.2-b14-20260824/`。两个压缩交付物 SHA-256 分别为
  `927c66858a20d2483f849c156101e13ab8ef846812428e7c81998d14920d71c8` 和
  `1b5353079d4aa1b9736256e7abff98d5222dfa45658dd36a5c947e0db74ba763`。
- 用户已裁定 S3 异机备份改为可选且本次不用；生产 `neckline-backup.timer` 为
  disabled / inactive。服务器本地一致性回滚包位于
  `/opt/neckline-release-backups/v2.5.2-b13-pre-20260824-093620/`，数据库完整、28 张表；
  `source.tgz`、`systemd.tgz`、`neckline.db` 校验值分别为
  `e6f31f44e7919b947359cbb3766c8998572f7856ccab624fd9a3e1de49489d4e`、
  `235597bd0a6c51bd230d49ca5adff470255d1c7ee9d248f6ec7854177f6450df`、
  `ce8338b30a99565b9f709c2422a95b09868cee4a514cded0ba5cb70eef448456`。
- 宁波云公开健康检查为 `v2.5.2`，服务 active、`NRestarts=0`；28 张表及逐表行数发布前后
  完全一致。日更、晚间、数据恢复 timer 正常，可选备份 timer 未启用；发布未运行正式报告，
  既有 `20260823` 报告仍可读取。
- macOS 已换装并启动 `2.5.2 (14)`，签名有效、归档与安装后可执行文件逐字节一致，
  App HTTPS 请求返回 200。旧 Build 13 备份位于
  `/Users/linotsai/Lino/app_backups/Neckline-v2.5.2-b13-pre-v2.5.2-b14-20260824-103155/`；
  iOS `2.5.2 (14)` 签名归档已交付但未安装。
