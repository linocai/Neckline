# Neckline

自用创业板消息筛选器。**现状（2026-10-18）：现役 4.0.0 B112。每个交易日早上出一份晨报，窗口为上个交易日 15:00 → 当天 08:00，第一期在 10/19（周一）早上；iPhone 收推送，App（iOS 三标签：晨报、历史、设置；macOS 两栏加系统设置窗口）、只读网页和 `/api/v4` 都读同一份名单。MCP 尚未上线（P2）。v3 的采集与出报已退役，同一服务保留 v3 只读历史，P5 删除。** 计划与状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，V4 详细计划见 [archive/v4.0.0_execution.md](archive/v4.0.0_execution.md)，项目规则见 [CLAUDE.md](CLAUDE.md)。

## 仍然有效的运维事实

- 代码在 `App/`（iOS/macOS）和 `Backend/`；服务入口 `https://nk.linotsai.top`。V4 接口前缀 `/api/v4/`（Bearer 鉴权，`/api/v4/health` 除外），App 只调这些接口；v3 只读历史仍在 `/api/v1/`，新 App 不再调用，P5 删除。凭据不得进入 Git、日志或回复。
- 只读网页 `https://nk.linotsai.top/read/<访问码>`：最新一期、`/day/<日期>`、`/history`、`/doc/<id>`，加 `?format=json` 给 Agent 读。访问码只在 `/etc/neckline/v4.env` 和交付给用户本人的链接里；NPM 对 `/read/` 不写访问日志，改 nk 代理时必须保留。
- 服务器是 NB 云，目录 `/opt/neckline`。API 是 `neckline.service`；V4 定时任务：`neckline-v4-morning.timer`（每天 08:00 采集，交易日接着出报并推送 iPhone）、`neckline-v4-collect.timer`（每天 20:00 采集）、`neckline-v4-finalize.timer`（09:15 还没出报就记失败并推送「晨报出问题了」）。
- 配置：`/opt/neckline/.env`（TuShare、金十、APNs、API 令牌）、`/etc/neckline/v4.env`（V4 库路径、DeepSeek 密钥、网页访问码，root:neckline 0640）、`/etc/neckline/k10.env`（v3，P5 删除）。V4 库 `/opt/neckline/data/v4.db`；v3 库 `/opt/neckline/data/neckline.db` 原样只读保留。DeepSeek 密钥已不归 App 设置页管。
- V4 命令（以 neckline 用户、先加载两个环境文件）：`/opt/neckline/.venv/bin/python -m neckline.morning.cli {collect|morning|finalize|show}`。
- 筛选规则在 `Backend/neckline/config/screening-v1.json`（用户批准），公司池随代码发布并按哈希核对；改规则须升版本并经用户确认。
- TuShare `major_news` 每个账号每天最多调用 40 次：彩排、手工采集都占这个额度。
- B92（2026-09-27）之前的数据永远不读取、不导入、不恢复。
- 后端发布只走 `Backend/scripts/release.py`，用法见 CLAUDE.md「发布」。
