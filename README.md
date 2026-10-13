# Neckline

自用 A 股选股应用（K10-v2）。**线上：后端与 Mac 3.7.0 Build109（2026-10-13），采集、晨报、晚报定时运行；晚报从 10/12 21:00 的新起点开始，旧积压不再处理。** 现行计划与状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，项目规则见 [CLAUDE.md](CLAUDE.md)。

## 消息与报告

- **采集**：每自然日北京时间 08:00、20:00，含周末节假日；金十与 TuShare 分别记录覆盖、失败和水位。采集不调研究 LLM、不产生推荐。
- **晚报**：交易日前一自然日 21:00 开始，最多 30 家，允许零推荐。
- **晨报**：交易日 08:30 开始、09:20 前可读；复核昨晚全部推荐，同时发现隔夜新机会。
- 报告只读冻结的本地资料，未采到的时段如实披露。正式推荐使用固定 1,089 家公司池和固定 D1/D2 观察窗口。

## 配置与运维

- 代码在 `App/` 和 `Backend/`；服务入口 `https://nk.linotsai.top`，API 前缀 `/api/v1/`。除健康接口外均需 Bearer 鉴权。
- B92（2026-09-27）是全新数据起点，旧业务数据不读取、不导入、不恢复。新库只能在 `Backend/` 下这样建立：

  ```bash
  .venv/bin/python -m neckline.k10.cli initialize-fresh --db <明确的新文件路径>
  ```

  命令只创建不存在的空库。之后依次：登记已批准的固定资料 → 登记策略、执行与采集配置 → 绑定策略 → 取官方交易日历 → 在尚无扫描时核对实际 API 配置响应。
- 固定资料源：研究仓 `research/K10-v2初始股票池_20260909.json` 与 `artifacts/output/k10-company-profiles-v2-20260909/`，只能离线显式导入并核验 hash 与 1,089 家公司集合。
- 生产配置：策略 `k10-v2-production@2`、执行 `k10-v2-execution-production@2`、采集 `k10-collection-production@1`，策略快照 `k10-v2-20260909-r2`（与修订 1 相比，只改了快照 ID 和晚报 bootstrap）；参数未配置时不得使用默认值。运行时契约见 [k10-v2.json](Backend/neckline/config/k10-v2.json)、[k10-execution-v4.json](Backend/neckline/config/k10-execution-v4.json)。
- 服务器上 `/opt/neckline/.env` 与 `/etc/neckline/k10.env` 合并生效，`DB_PATH=K10_DB_PATH=/opt/neckline/data/neckline.db`、`PARQUET_DIR=K10_PARQUET_DIR=/opt/neckline/data/parquet`；API、worker 与各定时服务必须绑定同一份。
- 模型和 Tavily 凭据可在 App 设置里填写；金十凭据由服务器受保护的环境变量 `JIN10_MCP_TOKEN` 管理。填写凭据不会自动开启报告。
