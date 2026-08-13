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

## App

```bash
cd App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
```

当前系统边界、版本和待办见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。历史文档仅供追溯，不作为当前施工指令。
