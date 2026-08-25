# Neckline

V2.6.0 / K9-v2 已于 2026-08-25 完成一条龙发布；当前客户端为 Build 18。正式参数原包为
`k9-params-20260824-v2-r1`，SHA-256 为
`718bf7876d69936937edfdc7432bbea88ec1cd3e6e6107501acd325b7f1098df`；加载器仍无任何策略默认值。

宁波云服务端为 V2.6.0，本机 macOS 客户端为 V2.6.0 Build 18。最新完整交易日 2026-08-24 已作为
K9-v2 Day 1 生成首份正式清单；当前只有观察队列，没有已结算成绩。S3 异机备份仍是默认不启用的
可选能力，日常运行链路只保留 K9-v2。
交易日数据在 16:05 更新，报告仅在周一至周四及周日 19:00 生成；周日读取前一周五
盘面并纳入周末消息，周五和节假日不生成报告。

A 股生产应用：SwiftUI 客户端 + FastAPI 服务。离线研究、回测和参数标定在
`/Users/linotsai/Lino/whynotme`；生产仓只消费经用户确认的 K9 参数包。

## 当前状态

- 当前生产：**V2.6.0 Build 18 客户端 / K9-v2 / fp-3 / d2-v1**；最终后端标签为 `v2.6.0-b16-r3`，最终客户端标签为 `v2.6.0-b18`。
- macOS 已换装 Build 18 并通过真实界面核验；策略身份显示为 `K9-v2`，预案修订号明确显示为“预案第 N 版”。iOS 签名 IPA 已生成，交由用户自行安装。
- 双端交付物位于 `/Users/linotsai/Lino/releases/Neckline/v2.6.0-b18-20260825/`。
- V2.6.0 链路：fp-3 → 有效活跃度 → P1/P2/P3/P4 → 排序与名额 → 解释 → D1 预案两拍 → D2 五指标。
- 两次近期 K9-v1 结果只在迁移归档中保留为 `invalidated / superseded_by_k9-v2`，不会进入 K9-v2 的候选、观察、结算或成绩分母；K9-v1 与 `k9-params-20260822-r1` 的正式历史保留。
- 退役运行时代码、表、路由、设置和数据已删除；历史追溯使用 Git。
- 策略实验状态为 `continue-observing`：已完成 1 次执行有效的 Day 1，尚无 D2 样本，不作优劣结论。
- 具体裁定、发布记录和待观察事项见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

## 仓库结构

```text
App/            iOS / macOS 客户端
Backend/        API、定时任务、数据契约、部署单元与测试
archive/        用户明确要求暂存的材料；默认保持为空
AGENTS.md       项目工作规则
PROJECT_PLAN.md 唯一当前计划
README.md       操作入口
```

## 日常运行

| 时刻 | 工作 |
|---|---|
| 16:05（周一至周五） | 行情数据更新；休市日安全跳过 |
| 16:25（每日） | 幂等扫描并补齐最多 60 个交易日的数据前置；不生成报告、不发通知 |
| 19:00（周一至周四） | facts → 方向背景 → K9 → explain → playbook → report；休市日安全跳过 |
| 19:00（周日） | 使用前一个周五的盘面并纳入周末消息；该周五休市则安全跳过 |
| 9:26–9:29（D1） | 核对“已触发放弃 / 待开盘后观察”并推送 |
| 10:00–10:05（D1） | 零 LLM 结算成立 / 放弃 / 观察，不推送 |

周五不直接生成报告，留到周日 19:00。周日报告有两个日期：`report_date` 是标题、推送
和可见身份；`trade_date` 是行情、清单、预案和审计键。周五休市时不回退重跑周四。

## Backend

```bash
cd Backend
cp .env.example .env        # 已有 .env 时不要覆盖
.venv/bin/python -m pytest -q
.venv/bin/uvicorn neckline.api.app:app --host 127.0.0.1 --port 8002
```

本地隔离冒烟：

```bash
bash scripts/smoke_api.sh
bash scripts/smoke_review.sh
```

### 参数包

生产命令必须显式传参数包：

```bash
.venv/bin/python scripts/evening.py \
  --k9-params config/k9-params.json --notify
```

没有默认路径，也没有策略默认值。缺包、缺键或值非法时，报告写“今天没跑成 · 参数未配置”
并停止出清单；不得把它显示成可信空榜。示例文件只说明结构，其中 K9 固定值可以逐字给出，
所有需要标定的数值保持占位，且示例文件故意无法通过生产加载器。正式原包位于
`Backend/config/k9-params.json`，发布门禁逐字节锁定其 SHA-256；任何后续调参必须生成新包版本，
不得原地覆盖。

### LLM 与 Tavily

现役 LLM 任务只有：

- `market_direction`：冻结市场事实的背景说明；
- `news_scan`：Tavily 搜索后由默认模型读取证据；
- `explain`：解释个股资料和 K 线；
- `playbook`：给机械预案骨架填明确价格。

机械策略层、9:26 条件判断和 10:00 结算均不调用 LLM。Provider 只负责推理，联网统一由
Tavily 完成。macOS 配置路径是“设置 → 高级与诊断 → 模型、联网资料与任务路由 → Tavily 联网搜索”；
Key 只写入服务端，API 和客户端只返回是否已配置。

### 数据库

`init_schema()` 是受控写入口，只能由 API 启动、明确写命令或部署迁移调用。读 helper 使用
只读连接，缺表时返回接口约定的空态，绝不顺手建表或补列。

新库只建立现行表。由旧 K9 schema 升级时会保留仍有用的设置、Provider、Tavily、设备、
周复盘和有效早晨任务记录，再物理删除退役表与退役列。正式迁移前必须做数据库备份、校验值
和 `PRAGMA integrity_check`；旧代码回滚时必须同时恢复迁移前数据库。

V2.6.0 的显式迁移命令是 `scripts/migrate_k9_v2.py`。它要求精确提供两次旧运行身份，先生成
带哈希的报告/元数据归档和 SQLite 回滚库，再清空活动 K9 状态并建立空的 K9-v2 schema；
`restore` 子命令用于成对回滚。该迁移不删除 `fact_packs` 或任何行情 Parquet。

### 可选的异机备份与恢复

Build 13 提供通用 S3 兼容对象存储备份，但生产默认不配置、也不启用
`neckline-backup.timer`，它不是 API、日更、报告或发布的依赖。当前发布仍制作服务器本地的一致性
回滚包：源码、systemd、环境文件与 SQLite 在线快照，并验证数据库完整性。

将来明确选择对象存储后，才配置 `scripts/backup_snapshot.py`：它用用户 Mac 持有私钥对应的
公开密钥加密 SQLite 在线快照与冻结事实包；服务端只保留公钥和对象存储上传凭据。完成首次异机
恢复演练后，才能显式启用每天 02:30 的 timer。手动调用时若 S3、公开密钥、保留策略或隔离恢复
验证器缺失，命令仍会失败，绝不退回本机“假备份”。

恢复验证器必须在与生产隔离、且私钥不在服务端的环境中运行；恢复端使用
`scripts/restore_backup.py --manifest … --private-key … --destination … --bucket …`，并自动验密文、
明文和 SQLite `PRAGMA integrity_check`。保留策略为 30 份日备份和 12 份月备份；备份对象不进 Git。

## App

```bash
cd App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

三条都是发布门禁。macOS 构建不会编译 iOS 专属推送代码，iOS 普通构建也不能替代测试目标编译。
App 的业务板块是“选股 / 成绩 / 复盘”，设置单独入口；系统不展示或跟踪持仓。

## 当前生产

V2.6.0 服务端于 2026-08-25 部署到宁波云 `114.66.0.38:/opt/neckline`，发布代码为
`676d20e`，不可变后端标签为 `v2.6.0-b16-r3`。公开 `/api/v1/health` 返回 `v2.6.0`；
API 服务运行且重启次数为 0，日更、晚间与数据恢复 timer 均 enabled / active；可选的
`neckline-backup.timer` 仍为 disabled / inactive。生产数据库 `PRAGMA integrity_check=ok`。

两次 K9-v1 活动运行 `214566e02ef44c9e88bcf5f812e2cdb0` 与
`103d4f5d76eb47bb8117bf722457bba9` 已从当前候选、D1/D2、待结算和成绩状态中移除，归档位于
`/opt/neckline/data/archive/k9-v1-invalidated-20260824/`。归档清单保存原报告、运行元数据、哈希和
回滚库，状态为 `invalidated / superseded_by_k9-v2`；原始行情快照未动，K9-v1 与
`k9-params-20260822-r1` 正式历史未动。发布前完整服务器回滚包位于
`/opt/neckline-release-backups/v2.6.0-b16-pre-20260824-230400/`。

最新完整交易日 2026-08-24 的 K9-v2 Day 1 运行 ID 为
`bcce862a80fb409e8d6fcf02daeaec97`，事实包为 `b0157268e549403f872ca1c49c939b4e / fp-3`。
机械层严格候选 71、联合候选 172，最终严格清单 20 只；活动观察队列 20、已结算批次 0、成绩分母 0。
解释与作战卡均完成；20 只中 19 只联网核验为干净，`601919.SH 中远海控` 因模型未按格式给出结论
标签而明确显示“未核验”，没有被伪装成已通过，也不改变机械名单。

macOS `/Applications/Neckline.app` 已换装 V2.6.0 Build 18；为连续读取原有 Keychain 令牌，本机副本
沿用原 Apple Development 身份签名，交付目录中的 macOS 分发包仍保留 Developer ID 签名。
真实界面已验证清单 20 只、“观察队列 20/60、已结算 0 批、指标尚不可得”的空成绩基线，
以及次日核对表与个股次日预案均显示 `K9-v2 · 预案第 1 版`。
换包前客户端备份位于
`/Users/linotsai/Lino/app_backups/Neckline-v2.6.0-b17-pre-v2.6.0-b18-20260825-093600/`；
iOS IPA 位于 `/Users/linotsai/Lino/releases/Neckline/v2.6.0-b18-20260825/`，由用户自行安装。

完整发布事实与回滚边界以 [PROJECT_PLAN.md](PROJECT_PLAN.md) 为准。
