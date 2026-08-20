# Neckline

A 股生产应用：SwiftUI 客户端 + FastAPI 服务。离线策略研究、回测与**参数标定**已经独立到
`/Users/linotsai/Lino/whynotme`。

## 现在是什么状态

**V2.5.0（K9 换引擎）已经写完、门禁全过、⛔ 一次都没上云。** 生产上跑的仍是 v2.4.2 Build 9。

- 当前系统边界、施工进度、22 条 Backlog（B13 已还清，**21 条在等你裁定**）、发版清单，全在
  [PROJECT_PLAN.md](PROJECT_PLAN.md) —— **它是本仓唯一当前权威计划**。
- V2.4.2（K8 时代：驱动种子 → 方向 → 篮子 → 六关 → Tier → 卡片）**整条链已退役留档**。
  它的施工图在 `archive/施工图/`，历史数据表按裁定 6 **保留、只读、不迁移、不回填**，
  唯一只读追溯入口是 `GET /api/v1/legacy/k8/baskets`。
  ⛔ archive 里的东西**不是当前施工指令**。

## 结构

```text
App/            iOS / macOS 客户端
Backend/        API、定时任务、数据、部署文件与测试
archive/        历史计划、审计、交接、旧设计与退役配置
AGENTS.md       项目工作规则
PROJECT_PLAN.md 当前状态与待办（唯一权威）
README.md       本入口
```

## Backend

```bash
cd Backend
cp .env.example .env        # 已有 .env 时不要覆盖
.venv/bin/python -m pytest -q
.venv/bin/uvicorn neckline.api.app:app --host 127.0.0.1 --port 8002
```

常用脚本均从 `Backend/` 运行。

### 一天跑什么

| 时刻 | 谁跑 | 干什么 |
|---|---|---|
| 16:05（Mon–Fri） | `scripts/daily_update.py` | 行情增量 + 申万二级分类日更 + **事实包构建冻结** + 覆盖率 |
| 16:35（Mon–Thu）/ 19:00（Sun） | `scripts/evening.py` | 晚间链：**facts → k9 → explain → playbook → report** |
| 9:26–9:29（D1） | 常驻 `neckline.service` 的进程内 tick | 次日核对表（**二值**：已触发放弃 / 待开盘后观察） |
| 10:00–10:05（D1） | 同上，第二拍 | 三分支结算（**零 LLM、零推送、不进 App 首屏**） |

```bash
.venv/bin/python scripts/daily_update.py              # 16:05 那一段
.venv/bin/python scripts/evening.py                   # 最近一个交易日，全链
.venv/bin/python scripts/evening.py 20260724 --segments facts,k9
.venv/bin/python scripts/evening.py --k9-params config/k9-params.v1.json --notify
```

🔴 **两拍都跑在既有常驻 `neckline.service` 里，⛔ 本版零新增 systemd unit。** 多一个 unit
就多一个触发面和一条双跑路径，而「当日只跑一次」是记在防重台账里的。

### 参数包：没有默认值，读不到就停

```bash
.venv/bin/python scripts/evening.py --k9-params config/k9-params.v1.json
```

⛔ **无默认路径、无内嵌默认值**（裁定 5）。没传 `--k9-params`、或参数包缺键 / 越界，
报告清单段首行就是「**今天没跑成 · 参数未配置**」——**这是设计行为，不是故障**。

- 「今天没有」= 跑通了、结果为空、**可以被信任**；「今天没跑成」= 系统没工作。两者绝不混用。
- **19 项待标定参数目前一个都没填**，`Backend/config/k9-params.json` **不存在** ——
  标定归 whynotme，Neckline 只消费标定完、用户确认过的参数包。
  示例配置 `config/k9-params.example.json` 里所有数值位都是 `"__TO_BE_CALIBRATED__"`，
  ⛔ 里面没有一个真数字。
- 因此上线首日：清单段「今天没跑成」，方向背景段「未接入」（`facts/direction_llm.py`
  本版未建），**市场事实段与覆盖率成绩线照常出真数**。

### 周日报告的双日期契约（⛔ 不许退化）

生产晚间链周一至周四 16:35 跑当日；周日 19:00 发布**周日报告**：
`report_date=周日`（管标题 / 推送 / 可见身份），`trade_date=紧邻的上一周五`
（管 EOD 读数 / 清单 / 预案 / 审计键）。该周五休市则**安全跳过**，⛔ 不回退重跑周四；
同一周五数据的报告已在周日当天人工生成，19:00 定时槽**整链跳过**，避免重复 LLM 与 APNs。
人工补跑必须把两日都写清：

```bash
.venv/bin/python scripts/evening.py 20260814 --report-date 20260816 --notify
```

这三条各有一条回归守门（`tests/test_weekend_report_schedule.py`，LRN-20260816-001）。

### 对外接口（给 whynotme 标定用）

```bash
.venv/bin/python scripts/export_research_snapshot.py \
  --out /Users/linotsai/Lino/whynotme/artifacts/input/neckline.snapshot.db

# 连事实包 parquet 一起导（标定必须跑在与生产逐字节相同的事实包上）。
# 事实包落在 --out 同级的 fact_pack/ 下，保持 fact_pack/version=<v>/year=YYYY/YYYYMMDD.parquet 布局；
# manifest 带 packVersion、区间、Neckline 版本与逐日 sha256。⛔ 区间没有默认值。
.venv/bin/python scripts/export_research_snapshot.py \
  --out /Users/linotsai/Lino/whynotme/artifacts/input/neckline.snapshot.db \
  --include-fact-packs --start 20260101 --end 20260724
```

### LLM 与联网检索

K9 之下只有**三个岗位**用 LLM，全部住在**机械链之外**：解释层（`explain`）、
预案层（`playbook`）、以及给解释层喂证据的消息面扫描（`news_scan`）。
🔴 **策略层零 LLM、次日核对与 10:00 结算零 LLM**，这几条各有 AST 守门 + 运行期双证看着。
（架构 §八 的第一个岗位「方向解读」= `facts/direction_llm.py`，**本版未建**。）

联网研究统一使用 Tavily Basic，LLM Provider 本身不再承担联网能力。macOS 配置路径是
「设置 → LLM Provider 与任务路由 → Tavily 联网搜索」；输入只写入服务端，客户端和
API 只显示「已配置/未配置」，不回显 Key。默认模型也在同一页选择，只有已启用且已配置
Key 的 Provider 可以保存为默认值；删除、停用或清空其 Key 会同时清掉相关默认值和任务
路由，避免出现「界面选中但运行时不可用」。

⚠ `llm/router.py` 的 `ALL_TASKS` 里还留着一批 **K8 时代的任务键**
（`basket_reason` / `tier_rank` / `script` / `driver_search` / `auction` …）——
它们**在生产链上零调用**，留着只为让老库里存过的路由行仍解得出来。
⛔ 别照旧注释以为那些层还在。

### SQLite schema 边界

`init_schema()` 是受控写入口：仅允许 API 启动、明确的写入命令或 RC 迁移步骤在
**已确认目标库且已完成备份**后调用。API、报告和复盘的读取 helper 不执行 DDL；旧
schema 只读探测后返回**文档化的空态**（「那天没冻结 / 那天没跑过 / 那天没有清单」）。

🔴 **这一条到 v2.5.0 才由代码保证。** 在此之前（含 v2.4.2）有 43 个读 helper 会在函数体里
`init_schema()` —— 实测一次 `load_k9_report` 就把 v2.4.2 老库从 59 表迁成 75 表。
现已修完（账面 43 → 0），两条闸看着它：
`tests/test_v250_s14_release_gate.py::test_no_read_helper_triggers_a_schema_migration`（静态）
与 `tests/test_read_path_no_ddl.py`（行为：未迁移库上读一整轮，`sqlite_master` 逐行不变）。

⚠ **但启动路径与写命令仍然会建表** —— `api/app.py` 的 lifespan、`scripts/*.py` 的写入口、
任何显式 `init_schema()`。🔴 **⛔ 不要用任何 Neckline 侧工具去打开迁移前的备份**，
要比对行数请直接 `sqlite3 <备份> "SELECT COUNT(*) FROM …"`。

RC 迁移的回滚边界是迁移前 SQLite 备份与**已验证的 v2.4.2 源码**（commit `ee12b9b`），
任何 GET 或日常读取都不是迁移触发器。

## 发版

V2.5.0 的发版清单在 [PROJECT_PLAN.md](PROJECT_PLAN.md) 的 **§9.6**（步骤 0～7：
容量红线阻塞项 → 升级前备份 → 纯新增迁移 → 8 个 unit 逐个交代 → 部署验证 →
上线后状态预告 → 回滚 → 收尾）。🔴 **步骤 0 是阻塞项**：策略层实测 736 MB 而
`neckline-basket.service` 挂着 `MemoryMax=900M`，余量 18%，必须先在生产同规格 Linux 机上
用真实数据复测（§13.1-B5）。

能在本地先跑一遍的那几件已做成机器判据：

```bash
cd Backend
.venv/bin/python -m pytest tests/test_v250_s14_release_gate.py -q
```

它在**临时库**上拿 v2.4.2 的 schema 造一个老库、跑今天的 `init_schema`、逐表逐列
对拍历史行，并锁住 unit 拓扑零新增与回滚锚点可取。⛔ 它不替代 §9.6 里那几步要在
生产上做的事（两份备份 + `integrity_check` + 源码锚点 + 明确目标确认）。

## App

```bash
cd App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
```

🔴 **两条都要跑，改了任何 `.swift` 都一样。** iOS 与 macOS 是同一个工程的两个一等公民
（`project.yml` 的 `supportedDestinations: [iOS, macOS]`），而 App 里有大量
`#if os(iOS)` / `#else` 分叉 —— **一个平台的构建不能替另一个平台作证**。
V2.5.0 就是因为验收口径只写了 macOS，iOS 侧 6 条编译错（推送路由引用了已下线的板块）
一路绿到发版前。⚠ 锁屏推送 entitlement 只给 iOS，全 App 唯一依赖 iOS 的能力正好落在
那个文件上。

三板块 = **选股 / 成绩 / 复盘**，设置沉底（裁定 11）。持仓板块整块下线 ——
系统不跟踪持仓，界面上摆着持仓板块是在谎报能力。
