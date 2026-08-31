# Neckline

Neckline 是 A 股生产应用：SwiftUI macOS/iOS 客户端与 FastAPI 后端。策略研究、回测与参数标定只在
`/Users/linotsai/Lino/whynotme`；生产代码不读取或导入该目录。

## 当前状态

- 生产现行为 **V2.7.0 / K9-v3 / fp-4 / d2-v2**。macOS/iOS 客户端仍为 `2.7.0 (19)` 与发布集合 `v2.7.0-b19`；NB 云后端在同一 API 合同上运行 `v2.7.0-b20`。Build 20 是后端数据正确性修复，不要求客户端重新安装。
- 生产显式加载获批参数包 `k9-params-20260831-v3-r1`，正式原件为 `Backend/config/k9-params.json`，SHA-256 为 `feb24c8199b061b31e33fa9b47603e3d9cc27d76eaa0aff064f1b451d01b41a2`。文件缺失、无效或谱系错配时仍不得生成候选、预案、成绩包或通知。
- K9-v3 从空队列开始，未用半日数据抢跑。2026-08-31 首个正式晚间链在修复过度日历依赖并补齐 60 个可靠 fp-4 后完成：D2→D1→D0 全部成功，机械结果为可信空清单，首批成绩包和报告状态均为 `empty`。
- macOS/iOS 分发包及校验材料发布在 [GitHub Release `v2.7.0-b19`](https://github.com/linocai/Neckline/releases/tag/v2.7.0-b19)；唯一施工与运行控制面见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

## 日常 V3 流程

交易日事实先冻结为 `fp-4`；晚间链独立推进到期 D2、到期 D1、当天 D0，再由报告引用成绩包。成绩包以 `batchId` 为主实体，保存选股日、信号交易日、D1/D2 日期以及策略、参数、事实和标签谱系。D0 候选与预案不可改写，D1/D2 只追加。

当前 API 合同：

- `GET /api/v1/selection/latest` 与 `GET /api/v1/selection/{trade_date}`：报告三态；
- `GET /api/v1/scoreboard/packages?state=active|settled`：成绩包摘要；
- `GET /api/v1/scoreboard/packages/{batchId}`：冻结候选及阶段结果；
- `GET /api/v1/checklists/{batchId}`：该包的 9:29 三段核对。

UI 必须明确显示 `K9-v3` 与“预案第 N 版”，不显示固定 60 队列、通道 P1、严格/宽松档或综合总分。

历史 `fp-4` 只能使用可靠的逐日完整 SW2021 L2 成员文件，不能把当前成员表倒灌为历史。受控导入入口为：

```bash
cd Backend
.venv/bin/python scripts/import_sw_industry_history.py --file /absolute/path/to/sw2021-history.json --db /path/to/settings.db
```

文件必须逐日列出完整成员及 `trade_date`、L1/L2/L3 身份；每个 `snapshot` 必须显式给出 `complete: true` 和正整数 `expectedMemberCount`，且去重后的成员数必须精确相等。`source.id` 只能是无空白的 1–128 位来源标识；`generatedAt`/`fetchedAt` 必须为带 `Z` 或 UTC 偏移的 ISO-8601 时间且前者不晚于后者。导入会保存原文件 SHA-256、逐日内容哈希和经来源声明、校验后的行数。相同内容可幂等重跑，不同内容不会覆盖已冻结日期。

## 本地验证

```bash
cd Backend
.venv/bin/python -m pytest -q
bash scripts/smoke_api.sh

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

三条 App 构建都是门禁，不能互相替代。测试只使用临时数据库或显式只读快照。

## 参数包后接

获批原件已按原字节写入 `Backend/config/k9-params.json`。本地运行仍必须显式传入，生产则由 `K9_PARAMS_PATH` 指向部署后的同一文件：

```bash
cd Backend
.venv/bin/python scripts/evening.py --k9-params config/k9-params.json
```

加载器只接受完整、不可变的 `K9-v3 / fp-4 / d2-v2` 参数谱系；没有默认文件路径、默认阈值、默认权重或默认额度。该原件已通过加载器、K9-v3 金样、全量后端回归和 API 冒烟。隔离 fp-4 重放不再是发布门禁；正式运行仍由当日 readiness 自身 fail-closed，事实不完整就不生成 Day 1。

## 发布边界

NB 云权威运维事实以 `/Users/linotsai/Lino/NB_info.md` 为准；现行主机为 `114.66.2.205`，公网业务入口仍是 `nk.linotsai.top`。生产发布前必须确认目标、停写入任务、完成可恢复备份、验证 schema/API/timer，再交付同一发布集合的客户端。回滚必须同时恢复服务、数据库、配置、参数状态与客户端，不能留下混合版本。

S3 异机备份仍是可选能力，默认不启用；发布与日常运行不能依赖它。每次切换仍必须制作并验证本地 SQLite 在线回滚包。

仓库根目录只保留 `App/`、`Backend/`、`archive/`、`AGENTS.md`、`PROJECT_PLAN.md` 和本文件。`archive/` 仅存用户明确要求保留的临时材料，默认为空。
