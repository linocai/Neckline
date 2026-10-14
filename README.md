# Neckline

自用创业板消息筛选器。**现状（2026-10-14）：v3（3.7.0 Build109）已退役，采集与出报都已停止，服务器只保留只读 API；V4.0.0 开发中。** 计划与状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，V4 详细计划见 [archive/v4.0.0_execution.md](archive/v4.0.0_execution.md)，项目规则见 [CLAUDE.md](CLAUDE.md)。

## 仍然有效的运维事实

- 代码在 `App/`（iOS/macOS）和 `Backend/`；服务入口 `https://nk.linotsai.top`，API 前缀 `/api/v1/`，除健康接口外都需要 Bearer 鉴权，凭据不得进入 Git 或日志。
- 服务器是 NB 云，目录 `/opt/neckline`。`/opt/neckline/.env` 与 `/etc/neckline/k10.env` 合并生效，`DB_PATH=K10_DB_PATH=/opt/neckline/data/neckline.db`；主机事实见 iCloud `NB_info.md`。
- v3 的 `neckline.service`（API）仍在运行；worker 与五个 timer 已 stop + disable，恢复命令见 V4 记录 §7。
- B92（2026-09-27）之前的数据永远不读取、不导入、不恢复。
- 后端发布只走 `Backend/scripts/release.py`，用法见 CLAUDE.md「发布」。
- 模型与 Tavily 凭据由 App 设置写入服务器；金十凭据由服务器受保护的环境变量 `JIN10_MCP_TOKEN` 管理。
