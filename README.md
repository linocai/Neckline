# Neckline

A 股生产应用：SwiftUI 客户端 + FastAPI 服务。离线研究、回测和参数标定在
`/Users/linotsai/Lino/whynotme`；生产仓只消费经用户确认的 K9 参数包。

## 当前状态

- 当前生产版本：**V2.5.0 · Build 10**，已于 2026-08-23 部署到宁波云。
- macOS 已换装 V2.5.0 Build 10；iOS 签名归档已生成，交由用户自行安装。
- 现行引擎：事实包 → 四通道机械召回 → 排序与名额 → 解释 → 预案 → 次日两拍 → 成绩。
- 退役运行时代码、表、路由、设置和数据已删除；历史追溯使用 Git。
- 当前进入上产观察期；具体裁定、发布记录和待观察事项见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

## 仓库结构

```text
App/            iOS / macOS 客户端
Backend/        API、定时任务、数据契约、部署单元与测试
archive/        历史材料；不作为当前指令
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
Tavily 完成。macOS 配置路径是“设置 → LLM Provider 与任务路由 → Tavily 联网搜索”；
Key 只写入服务端，API 和客户端只返回是否已配置。

### 数据库

`init_schema()` 是受控写入口，只能由 API 启动、明确写命令或部署迁移调用。读 helper 使用
只读连接，缺表时返回接口约定的空态，绝不顺手建表或补列。

新库只建立 28 张现行表。由 V2.4.2 升级时会保留仍有用的设置、Provider、Tavily、设备、
周复盘和有效早晨任务记录，再物理删除退役表与退役列。正式迁移前必须做数据库备份、校验值
和 `PRAGMA integrity_check`；旧代码回滚时必须同时恢复迁移前数据库。

## App

```bash
cd App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

三条都是发布门禁。macOS 构建不会编译 iOS 专属推送代码，iOS 普通构建也不能替代测试目标编译。
App 的业务板块是“选股 / 成绩 / 复盘”，设置单独入口；系统不展示或跟踪持仓。

## 生产发布记录

V2.5.0 Build 10 于 2026-08-23 部署到宁波云 `114.66.0.38:/opt/neckline`。正式参数包从
whynotme 原样纳入 `Backend/config/k9-params.json`，文件 SHA-256 为
`5775641b989e9553ad29e0178a059007f1f663b422e8134130c99922e0dee952`。

发布时已完成：

- 停止旧链、备份源码/unit/密钥与数据库，并在数据库副本上完成迁移演练；
- 使用 TuShare `suspend_d` 补齐 131 个缺失交易日，154/154 个交易日分区均可读；
- 正式数据库迁移为 26 张现行表，`PRAGMA integrity_check=ok`，设置、DeepSeek、Tavily、
  Provider、设备、交易日历与股票基础信息均保留；
- API 与两个 timer 均为 enabled/active，公开健康检查返回 `v2.5.0`，服务重启次数为 0；
- 未在部署时重跑报告；下一次晚间任务由系统按周日 19:00 的正式计划触发。

服务器回滚包位于
`/opt/neckline-release-backups/v2.5.0-pre-20260823-095437/`；旧 macOS App 位于
`/Users/linotsai/Lino/app_backups/Neckline-v2.4.2-b8-pre-v2.5.0-b10-20260823/`。
签名归档、交付压缩包与 SHA-256 清单位于
`/Users/linotsai/Lino/releases/Neckline/v2.5.0-b10-20260823/`。

完整发布事实与回滚边界以 [PROJECT_PLAN.md](PROJECT_PLAN.md) 为准。
