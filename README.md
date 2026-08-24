# Neckline

当前生产版本为 V2.5.1 Build 12；V2.5.2 Build 13 正在施工，尚未部署或发版。运行链路仍是
K9。交易日数据在 16:05 更新，报告仅在周一至周四及周日 19:00 生成；周日读取前一周五
盘面并纳入周末消息，周五和节假日不生成报告。

A 股生产应用：SwiftUI 客户端 + FastAPI 服务。离线研究、回测和参数标定在
`/Users/linotsai/Lino/whynotme`；生产仓只消费经用户确认的 K9 参数包。

## 当前状态

- 当前生产版本：**V2.5.1 · Build 12**，已于 2026-08-23 部署到宁波云。
- macOS 已换装 V2.5.1 Build 12；iOS 签名归档已生成，交由用户自行安装。
- 现行引擎：事实包 → 四通道机械召回 → 排序与名额 → 解释 → 预案 → 次日两拍 → 成绩。
- 退役运行时代码、表、路由、设置和数据已删除；历史追溯使用 Git。
- 当前进入上产观察期；具体裁定、发布记录和待观察事项见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

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
所有需要标定的数值保持占位。

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

新库只建立 28 张现行表。由 V2.4.2 升级时会保留仍有用的设置、Provider、Tavily、设备、
周复盘和有效早晨任务记录，再物理删除退役表与退役列。正式迁移前必须做数据库备份、校验值
和 `PRAGMA integrity_check`；旧代码回滚时必须同时恢复迁移前数据库。

### 异机备份与恢复

Build 13 的备份只接受通用 S3 兼容对象存储：每天 02:30 执行 `neckline-backup.timer`，每次
发布前也必须在目标机手动执行一次 `scripts/backup_snapshot.py`。它用用户 Mac 持有私钥对应的
公开密钥加密 SQLite 在线快照与冻结事实包；服务端只保留公钥和对象存储上传凭据。若 S3、公开密钥、
保留策略或独立恢复验证器未显式配置，命令会失败，绝不退回本机“假备份”。

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

V2.5.1 Build 12 于 2026-08-23 部署到宁波云 `114.66.0.38:/opt/neckline`。生产数据库保持
28 张现行表，`PRAGMA integrity_check=ok`；公开 `/api/v1/health` 返回 `v2.5.1`，API
服务运行且重启次数为 0。旧 `scan/basket` 单元已下架，晚间链现为
`facts → strategy → report`。发布没有手动重跑报告，首份正式报告仍由周日 19:00 排程触发。

当前服务器回滚包位于
`/opt/neckline-release-backups/v2.5.1-b12-pre-20260823-165715/`。当前 iOS 签名归档位于
`/Users/linotsai/Lino/releases/Neckline/v2.5.1-b12-20260823/`，由用户自行安装。

完整发布事实与回滚边界以 [PROJECT_PLAN.md](PROJECT_PLAN.md) 为准。
