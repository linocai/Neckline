# Neckline

A 股生产应用：SwiftUI 客户端 + FastAPI 服务。离线策略研究和回测已经独立到 `/Users/linotsai/Lino/whynotme`。

## 结构

```text
App/            iOS / macOS 客户端
Backend/        API、定时任务、数据、部署文件与测试
archive/        历史计划、审计、交接、旧设计与退役配置
AGENTS.md       项目工作规则
PROJECT_PLAN.md 当前状态与待办
README.md       本入口
```

## Backend

```bash
cd Backend
cp .env.example .env        # 已有 .env 时不要覆盖
.venv/bin/python -m pytest -q
.venv/bin/uvicorn neckline.api.app:app --host 127.0.0.1 --port 8002
```

常用脚本均从 `Backend/` 运行，例如：

```bash
.venv/bin/python scripts/daily_update.py
.venv/bin/python scripts/evening.py
.venv/bin/python scripts/export_research_snapshot.py \
  --out /Users/linotsai/Lino/whynotme/artifacts/input/neckline.snapshot.db

# V2.5.0 S13：连事实包 parquet 一起导（标定必须跑在与生产逐字节相同的事实包上）。
# 事实包落在 --out 同级的 fact_pack/ 下，保持 year=YYYY/YYYYMMDD.parquet 布局；
# manifest 带 packVersion、区间、Neckline 版本与逐日 sha256。⛔ 区间没有默认值。
.venv/bin/python scripts/export_research_snapshot.py \
  --out /Users/linotsai/Lino/whynotme/artifacts/input/neckline.snapshot.db \
  --include-fact-packs --start 20260101 --end 20260724
```

V2.4.2 的晚间选股链必须显式提供经确认的 `direction_pipeline` JSON 配置：

```bash
.venv/bin/python scripts/evening.py \
  --direction-pipeline-config config/direction-pipeline.v2.4.2-balanced.json
```

生产采用用户确认后修正的“均衡档” `v2.4.2-balanced-r3`：全量方向可见，机械层按既有强弱序
与覆盖要求收口到 48 个初读方向；首批深挖 20、最多 30，六关后目标 7 个合格候选，
Token 停止线 350,000，**不设累计时间停止线**；行业、种子类型和潜在 C/Z/Y 覆盖
下限分别为 6/4/2。每完成一个两方向深研批次就按真实六关结果检查一次，已达到 7 个
合格候选便停止后续深研，否则继续到 Token、方向或补位上限。`triage_concurrency=1` 只是如实登记当前
串行执行，不代表已有并发能力。上线后先积累 3–5 个交易日的真实调用账，再决定是否
调整；不得根据单日感觉改参数。配置缺失或无效时，系统仍会标为“选股不可用”并保留
上一份冻结结果，不会回退到历史的前 20 条截断路径。

Tavily 命中若有正文、来源和链接但未披露发布日期，保留为“日期未披露”证据并阻止
进入 T1，不得再折算成零证据；真正零命中才走 `no_evidence`。涨停簇/异动簇的同花顺
概念代码必须先解析成中文概念名再检索，原始代码只保留在机械审计字段中。

生产晚间链周一至周四在 16:35 跑当日；周日 19:00 发布**周日报告**。报告日取周日当天，
行情截止日取紧邻的上一周五，并纳入周末联网消息：标题/推送写周日，EOD、篮子、六关、
卡片和详情查询仍绑定周五。若该周五休市，周日任务安全跳过，不会回退重跑周四。若同一
周五数据的报告已在周日当天人工生成，19:00 定时槽整链跳过，避免重复 LLM 与 APNs。
人工补跑必须把两日都写清，例如 `.venv/bin/python scripts/evening.py 20260814 --report-date 20260816 ...`。

联网研究统一使用 Tavily Basic，LLM Provider 本身不再承担联网能力。macOS 配置路径是
“设置 → LLM Provider 与任务路由 → Tavily 联网搜索”；输入只写入服务端，客户端和
API 只显示“已配置/未配置”，不回显 Key。默认模型也在同一页选择，只有已启用且已配置
Key 的 Provider 可以保存为默认值；删除、停用或清空其 Key 会同时清掉相关默认值和任务
路由，避免出现“界面选中但运行时不可用”。

为量真实成本，CLI 提供一次性观察参数：

```bash
.venv/bin/python scripts/evening.py \
  --direction-pipeline-config config/direction-pipeline.v2.4.2-balanced.json \
  --observe-selection-cost
```

它只在用户明确授权的人工运行中使用：Token 与墙钟继续如实记账，但墙钟在所有模式下都
只作观测、不再截断；该参数仅额外关闭 Token 截断。机械初读
48、深研最多 30 和最多两轮补位仍然强制执行。定时服务不带这个参数，日常生产仍执行
均衡包的 Token 与方向/补位限制。观察模式不改变六关、Tier 容量、候选充足条件，也不授权
自动重复顶层任务。

### SQLite schema 边界

`init_schema()` 是受控写入口：仅允许 API 启动、明确的写入命令或 RC 迁移步骤在
**已确认目标库且已完成备份**后调用。API、报告和复盘的读取 helper 不执行 DDL；旧
schema 只读探测后返回兼容的空值/旧快照。RC 迁移的回滚边界是迁移前 SQLite 备份与
**已验证的 v2.4.2 源码**（commit `ee12b9b`），任何 GET 或日常读取都不是迁移触发器。

## 发版

V2.5.0 的发版清单在 [PROJECT_PLAN.md](PROJECT_PLAN.md) 的 **§9.6**（步骤 0～7：
容量红线阻塞项 → 升级前备份 → 纯新增迁移 → 8 个 unit 逐个交代 → 部署验证 →
上线后状态预告 → 回滚 → 收尾）。能在本地先跑一遍的那几件已做成机器判据：

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
```

当前系统边界、版本和待办见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。历史文档仅供追溯，不作为当前施工指令。
