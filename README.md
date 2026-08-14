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
```

V2.4.2 的晚间选股链必须显式提供经确认的 `direction_pipeline` JSON 配置：

```bash
.venv/bin/python scripts/evening.py \
  --direction-pipeline-config config/direction-pipeline.v2.4.2-balanced.json
```

生产首轮采用用户确认的“均衡档” `v2.4.2-balanced-r1`：首批深挖 20、最多 32，
六关后目标 7 个合格候选，Token 停止线 350,000，墙钟停止线 1,500 秒；行业、种子
类型和潜在 C/Z/Y 覆盖下限分别为 6/4/2。`triage_concurrency=1` 只是如实登记当前
串行执行，不代表已有并发能力。上线后先积累 3–5 个交易日的真实调用账，再决定是否
调整；不得根据单日感觉改参数。配置缺失或无效时，系统仍会标为“选股不可用”并保留
上一份冻结结果，不会回退到历史的前 20 条截断路径。

### SQLite schema 边界

`init_schema()` 是受控写入口：仅允许 API 启动、明确的写入命令或 RC 迁移步骤在
**已确认目标库且已完成备份**后调用。API、报告和复盘的读取 helper 不执行 DDL；旧
schema 只读探测后返回兼容的空值/旧快照。RC 迁移的回滚边界是迁移前 SQLite 备份与
已验证的 v2.4.1 源码，任何 GET 或日常读取都不是迁移触发器。

## App

```bash
cd App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
```

当前系统边界、版本和待办见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。历史文档仅供追溯，不作为当前施工指令。
