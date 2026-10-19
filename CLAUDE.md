# Neckline 项目规则

自用创业板消息筛选器 V4（4.0.0 已建成，2026-10-19 起维护；v3 选股器 2026-10-14 退役，B113 删除）：Swift 客户端（iOS/macOS，`App/`），Python 服务（定时采集、DeepSeek 筛选、名单与推送，`Backend/`），以及给 ChatGPT、Claude、Grok 用的只读 MCP 服务（与 API 同进程）。工作流以 `~/.claude/CLAUDE.md` 为准；现行目标、状态与计划只看 [PROJECT_PLAN.md](PROJECT_PLAN.md)；服务器事实看 iCloud `NB_info.md`。2026-10-12 起由 Claude 独立开发，Codex 时期的规则不再适用。

## 边界

- 研究、回测、策略实验在 `/Users/linotsai/Lino/whynotme`，生产代码永不 import 它。
- 行情由 Neckline 自己取（2026-10-18 用户）：TuShare 用自己的 token；麦蕊借鉴 `/Users/linotsai/Lino/KXMarketTool`（KX，HZ 云）的取数代码另接。不 import KX、不共用 KX 的 MCP 工具，也不改 KX。
- 根目录只放 `App/`、`Backend/`、`archive/`、`CLAUDE.md`、`PROJECT_PLAN.md`、`README.md`。
- `archive/` 只是历史证据，不是规则来源；需要追溯时按文件名定位后窄读。
- 退役的功能直接删除：生产者、消费者、路由、配置、测试、存量数据一起删，不留兼容层。

## 生产数据与安全

- B92（2026-09-27）是全新数据起点。B92 之前的库、报告、回执、行情与恢复集一律不读、不迁移、不恢复。
- `.env`、凭据、生产库、`Backend/data/`、行情文件永不提交；凭据不进日志、回复和测试夹具。
- 动生产（部署、写库、改 timer、改配置）前核对目标并准备回退；写库前先备份并做还原校验。有新业务写入后，不得用旧快照整库覆盖。
- 部署和恢复保持 `/opt/neckline` 的属主与权限；就绪检查要校验接口的实际配置内容，HTTP 200 不算就绪。
- 只读路径不执行 DDL；`init_schema()` 只在 API 启动、显式写命令或已备份的发布迁移里运行。
- 付费外呼保留精确响应回执，相同请求不重复计费；`unknown` 状态的外呼不自动重试、不改判。
- 生产主机 3.8 GiB 内存、无 swap，worker 单进程：大 JSON 只投影需要的字段，避免整块反序列化。

## 产品不变量

- 产品定义、用户偏好与阶段计划以 PROJECT_PLAN 和 `archive/v4.0.0_execution.md` 为准。
- 筛选规则没有默认值：配置缺失或无效时不出名单，如实写明原因，不加任何兜底数值。
- 局部失败只剔除相关条目，名单照常交付，并写一行如实的缺口摘要；坏引用绝不变成事实。
- 错过的出报时刻不补跑；同一份名单记录同时供推送、App 与 MCP 使用，内容一致。
- 界面要求见 `archive/2026-10-14_前端重做评审.md`：两栏铺满、列表一行看懂、不堆警告、不展示内部术语与重复的版本标识。

## 验证

- 后端在 `Backend/` 下运行：
  ```bash
  .venv/bin/python -m pytest -q -o tmp_path_retention_policy=failed
  ```
  **全量全绿才能发布**，没有「历史失败」基线；一项失败就是一个待处理问题。开发中可先跑相关文件，发布前必须跑全量。
- 测试只用临时库或显式只读快照，绝不回落到工作库。自定义 `--basetemp` 时父目录要先存在；不要用 `tmp_path_retention_policy=none`（pytest 9 下它不逐项清理，会占满磁盘）。
- 名单链路的改动走真实入口验收：定时入口 → worker → 名单记录 → API/MCP，确定性 transport，业务时钟显式传入；中断与续跑要有注入回归，且不重复计费。跨端改动要用隔离库产出的真实 API 响应喂当前 Swift 模型解码。
- MCP 的验收必须在 ChatGPT、Claude、Grok 的手机端与电脑端实际调用工具，服务器冒烟测试不能代替。OAuth 回调只按精确地址放行；新客户端被拒时日志会记下它的回调（`registration rejected, redirect_uris=…`）。
- 改了 `App/` 下任何代码或资源，三条构建都要跑（在 `App/` 下）：
  ```bash
  xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
  xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
  xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
  ```
- 原生 QA 复用 `/tmp/neckline-v3-qa/{macos,ios}` 和共用模拟器「主模拟器」，每个平台最多一个实例。构建时显式传 `NK_BUNDLE_SUFFIX` 并核对产物 bundle ID 不等于生产 ID；进程设 `NK_DISABLE_PERSISTENT_CREDENTIALS=1`，只用临时凭据。XCTest 要核对实际通过数和跳过原因。
- 界面验收用有数据、无数据两种状态的实际截图，对照 `archive/Neckline_V3_界面参考/`；参考图里已退役的功能以现行产品逻辑为准。

## 发布

- 对外版本号由用户决定（现为 4.0.0）；版本号不变的快修只递增 build。后端每次发布递增 `Backend/neckline/api/app.py` 的 `RELEASE_SET`；客户端只在 `App/` 有改动时才递增 build、重新构建与签名。两端一起改版本时用 `App/scripts/prepare_release_candidate.sh <版本> <build>`，它同步所有版本号并重新生成 Xcode 工程。
- 后端发布只走 `Backend/scripts/release.py`：本地 `build --release <rs> --commit <sha> --out <暂存目录>` 从提交构建（不含工作区改动）；目录上传到 `/opt/neckline/releases/<rs>/` 后，在服务器以 root 跑 `release.py switch --release <rs> --previous <线上 rs>`。它会停服、做恢复点并还原校验、按清单增删替换文件并装 wheel，再把服务和 timer 恢复到切换前的状态并核对 health；任何一步失败自动回滚代码，生产库从不被覆盖。
- 每个上线的后端 build 打不可变 tag `v<版本>-b<build>`，打在实际部署的 commit 上。
- 一个 build 一份 `archive/v<版本>-b<build>_execution.md`：范围、验证、部署证据、回退点。PROJECT_PLAN 只留一行索引，README 只写已经上线的事实。

## 坑

- macOS `.app` 用 `ditto` 复制，不用 `cp -R`。
- 并行 `xcodebuild archive` 必须各用独立的 `-derivedDataPath`，否则争用 `build.db`。
- 打 wheel 由 `release.py build` 负责（venv 里没有 `build` 模块，走 `pip wheel --no-deps`）；产物放仓库外，`Backend/build/` 不进仓库。
- `release.py switch` 只装 Neckline 自己的 wheel，不装依赖：`requirements.txt` 变了，要在切换前（避开 V4 任务时段）先 `cp -a` 备份 `/opt/neckline/.venv`，以 deploy 用户装依赖并 `pip check`。NB 走阿里云镜像，可先 `pip download` 到暂存目录再离线装。
- TuShare `major_news` 每个账号每天最多调用 40 次（滚动计数）：彩排、冒烟、手工采集都占额度，动手前先算好次数。
- 改 NPM 的 nk 自定义配置时，保留 `/read/` 那段不写访问日志的 location（只读网页访问码在路径里）；改前备份，`nginx -t` 通过再 reload。
