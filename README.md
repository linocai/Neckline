# Neckline

自用创业板选股器（K10-v5：以消息为起点）。**现状（2026-10-29）：现役 4.2.1 B128（App 4.2.1 (128)）。** App 分为 K10（晨报）和 KX（KX 热门池实时看板，带题材、热度；9:15–9:25 标「竞价中」、9:25–9:30 标「开盘价」，按 Neckline 自己的麦蕊报价时间判定；Agent 推荐的票置顶）两块。KX 名单认 schema 1–3，不显示一字板与能否买入。

- **晨报**：每个交易日早上出一份，窗口为上个交易日 15:00 → 当天 08:00。
- **流程**：一级初筛（DeepSeek 分类）→ 二级数据准备（公告 PDF、公司快照、价格背景、业绩另算，纯代码）→ 三级评估（每条一次，回答四个问题，①②一票否决）→ 只推判「推」的，最多 10 条，可以 0 条。判「不推」的评估连同数据包存在后台（`evaluations` 表），不显示。
- **读同一份名单的地方**：iPhone 推送、App（iOS 三标签：晨报、历史、设置；macOS 两栏加系统设置窗口）、只读网页、`/api/v4` 和只读 MCP。
- v3 已在 B113 删除，服务只提供 V4。

计划与状态见 [PROJECT_PLAN.md](PROJECT_PLAN.md)，4.2.0 的契约与证据见 [archive/v4.2.0_execution.md](archive/v4.2.0_execution.md)，V4 详细计划见 [archive/v4.0.0_execution.md](archive/v4.0.0_execution.md)，项目规则见 [CLAUDE.md](CLAUDE.md)。

## 仍然有效的运维事实

- 代码在 `App/`（iOS/macOS）和 `Backend/`；服务入口 `https://nk.linotsai.top`。V4 接口前缀 `/api/v4/`（Bearer 鉴权，`/api/v4/health` 除外），App 只调这些接口；`/api/v4/health` 报版本号与 `releaseSet`。凭据不得进入 Git、日志或回复。
- 只读网页 `https://nk.linotsai.top/read/<访问码>`：顶部是给对话 Agent 的简要提示词；最新一期、`/day/<日期>`、`/history`、`/doc/<id>`、单票页 `/stock/<代码或简称>`（池内给收盘后与盘中的技术面概览，池外写「不在 K10 股票池」），加 `?format=json` 给 Agent 读。访问码只在 `/etc/neckline/v4.env` 和交付给用户本人的链接里；NPM 对 `/read/` 不写访问日志，改 nk 代理时必须保留。
- 只读 MCP `https://nk.linotsai.top/mcp`（和 API 同一个服务）：工具 `read_brief`、`get_morning_report`、`get_document`、`check_pool`、`get_stock_overview`；OAuth 只放行 ChatGPT、Claude 与 Grok 的回调，登录用部署时生成的连接口令（服务器只存 salt 与 scrypt 哈希），不接受 `API_TOKEN`。单票技术面概览只限池内：收盘后数据来自 TuShare，盘中报价与分时来自麦蕊（证书与 KX 共用，同一证书 60 秒内最多 2 个来源 IP，本机不直连）。
- 服务器是 NB 云，目录 `/opt/neckline`。API 是 `neckline.service`；V4 定时任务：`neckline-v4-morning.timer`（每天 08:00 采集，交易日接着出报并推送 iPhone）、`neckline-v4-collect.timer`（每天 20:00 采集）、`neckline-v4-finalize.timer`（09:15 还没出报就记失败并推送「晨报出问题了」）。
- 配置：`/opt/neckline/.env`（TuShare、金十、麦蕊、APNs、API 令牌、MCP 公开地址与连接口令哈希、OAuth 状态库路径）、`/etc/neckline/v4.env`（V4 库路径、DeepSeek 密钥、网页访问码，root:neckline 0640）。V4 库 `/opt/neckline/data/v4.db`，MCP 的 OAuth 状态库 `/opt/neckline/data/mcp-auth.db`。DeepSeek 密钥已不归 App 设置页管。
- V4 命令（以 neckline 用户、先加载两个环境文件）：`/opt/neckline/.venv/bin/python -m neckline.morning.cli {collect|morning|finalize|show|evaluations}`。晨报服务的 `TimeoutStartSec` 为 80 分钟（`/etc/systemd/system/neckline-v4-morning.service`，不由 release.py 管，改动要手工备份后替换）。
- 依赖另装：release.py 只装 Neckline 自己的 wheel；4.2.0 新增依赖 pypdf（读巨潮公告 PDF）。
- 筛选、评估与交付规则在 `Backend/neckline/config/screening-v3.json`（K10-v5，用户 2026-10-27 确认；收录口径沿用 2026-10-18 的点评，见 `archive/2026-10-18_消息偏好点评.md`）。公司池随代码发布并按哈希核对；改规则须升版本并经用户确认。评估收口 09:05（到点没评完的记为缺口），截止 09:15；评估前代码给每条候选调一次 DeepSeek Search（取 10 条，B126 起），结果只补背景和各方观点，不能推翻公告数字。评估输出没通过格式检查时，带着原输出再问模型一次修正格式（每条最多一次，B127 起）。
- 复盘时查看当期全部评估（推与不推）：`/opt/neckline/.venv/bin/python -m neckline.morning.cli evaluations --date <日期> [--json]`（只读）。
- TuShare `major_news` 每个账号每天最多调用 40 次：彩排、手工采集都占这个额度。
- B92（2026-09-27）之前的数据永远不读取、不导入、不恢复。
- 后端发布只走 `Backend/scripts/release.py`，用法见 CLAUDE.md「发布」。
