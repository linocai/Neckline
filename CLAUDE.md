# Neckline — 项目专属规范与坑(builder/reviewer 必读)

> 全局规范见 `~/.claude/CLAUDE.md`;系统线权威 `PROJECT_PLAN.md`、策略线权威
> `STRATEGY_LAB.md`(产品决策/参数/验收标准/变更日志全在那两份)。本文件只记
> Neckline **专属的工程坑**:每坑只留判据 + 规则 + 指针,事故叙事全文在
> `archive/`(总索引:`archive/交接与日志/变更日志_详版_20260719-20260728.md`)。数据源坑的
> 权威原文在 `/Users/linotsai/Lino/LinoN/CLAUDE.md`(前作)。
>
> ⚠ **策略研究档案已于 2026-08-08 整体迁出本仓**(K8 立项后本仓只留回测引擎与生产
> 代码)。新家:`~/Lino/whynotme/`(现役策略 `K8.md`;K2–K7 的 41 个 runner + 14 份
> 结果报告 + `STRATEGY_LAB.md` + `K4_STRATEGY.md` 归
> `~/Lino/whynotme/Archive/Neckline量化研究档案_K2-K7/`,该目录 README 记了重建成本)。
> **下文凡提到 `research/…`、`STRATEGY_LAB.md`、`K4_STRATEGY.md` 的,一律指迁出后的
> 新位置,本仓已无这些路径。**⛔ 但**回测核心留在本仓**:`neckline/{backtest,research,
> eval,strategy}/`,判分引擎唯一源 `neckline/eval/exit_sim.py`(考官线 V2-⑨ 下沉件,
> 周复盘/能力画像/周度校准/信息卡都在吃它 —— **「考卷设计不用了」≠ 这份能动**)。

## 记录纪律(2026-07-28 立规,动文档前先读)

- **变更日志一行制**:PROJECT_PLAN §九 每动作一行;事故复盘/完工验收/长记录写
  `archive/` 独立文件,日志一行 + 链接,同一件事全文只存在一处。
- **§四「当前状态」是快照不是账本**:每次会话交接**替换**全文,不追加;历史价值
  内容归 §九 一行 + archive 详版。
- **本坑清单每坑 ≤5 行**:超了就把叙事挪进 archive,这里留规则。

## 跑法

- 虚拟环境 `.venv`(Python 3.11+);`source .venv/bin/activate`。装库走阿里云镜像
  (`pip.conf` 已配)。测试:`python -m pytest tests/ -q`。
- 报告:`python scripts/report.py [YYYYMMDD]`。哨兵:`python scripts/sentinel.py
  [--once]`。持仓台账:`python scripts/positions.py {add,close,list}`。合成盘中
  冒烟:`python scripts/smoke_sentinel.py`。
- `scripts/oneoff/` 是已执行完毕的一次性脚本(charter 落行/bootstrap/数据修缮),
  留档审计用;现役脚本全在 `scripts/` 顶层。

## 仓库布局(2026-08-11 整理定案,⛔ 别把下面几处"改回去")

**一句话:现役目录里只放还在跑的东西,退役件一律进 `archive/`。**

- **`archive/` 分六层**:`施工图/` · `review报告/` · `对照表/` · `交接与日志/` ·
  `deploy_retired/` · `packs_retired/`。⚠ 动 archive 里任何文件名/位置,必须同步全仓
  路径引用(2026-08-11 那次移动牵动 **204 处**,分布在 41 个文件里,含 4 个运行时模块
  的注释、8 个单测、2 个客户端 Swift)。**判据:改完对每个文件 grep 旧路径必须全返 0。**
- 🔴 **`deploy/` 与 nk 上 `/etc/systemd/system/neckline*` 一一对应**(10 个 unit +
  `npm-custom-http.conf` + `npm-le-deploy-hook.sh`)。**这个 1:1 本身就是防误装闸门** ——
  多出来一个文件就意味着「有个没装的东西躺在这里等人 `enable`」。退役的三件
  (`nginx-neckline-nk.conf` 作废独占式模板 / `nginx-neckline.conf` hz 时代 /
  `neckline-report.timer` nk 上 not-found)已进 `archive/deploy_retired/`。
- 🔴 **`packs/` 只放现役**:`K8-skeleton.json` + `C1/Z1/Y1.json`。两个 LEGACY 包
  (`K4-pack.json` / `K7-pack.json`)在 `archive/packs_retired/`,**仍被 5 个单测当负例
  守门读取**(`test_selection_{pack,tier,gates,verification_rules}.py` /
  `test_activate_pack_script.py`)—— 它们按 `_RETIRED_PACK_FILES` 集合分派路径,
  ⛔ **别把 `_PACKS_DIR` 整个改指 archive**(现役包还在原处)。
- **`client/` 根上不放 `.swift`**:`DesignTokens.swift` 在 `Neckline/Components/`
  (与其余 `NK*` 设计件同处)、`Models.swift` 在 `Neckline/Networking/`(与解它的
  `APIClient.swift` 同处)。⚠ **契约守门单测按路径读 `Models.swift`**
  (`test_contract_crosscheck.py` / `test_v1_retirement_guard.py` / `test_circuit.py`),
  再挪必须同步改那 4 处。⚠ 移动 `.swift` 与新增一样,**必须 `xcodegen generate`**。
- **`PROJECT_PLAN.md` §五 只留当前版本全文**,历版一律「存根 + 指向
  `archive/施工图/`」。存根里必须带 **⚑ 交叉引用约定**一行,交代正文其余各节的旧指针
  该往哪读(体例见 V2.0.0 / V2.2.0 两段存根)。

## 钉死的领域常量(单一事实源,改动前先找源头,别在新文件里抄一份)

- **-5% 止损 / 回落止盈 / 仓位纪律**:唯一源是 SQLite `strategy_versions` 现役行
  (`neckline.strategy.brain.get_active().rule["config"]`),不是某模块字面量;
  哨兵读的也是这张表,不硬编 0.05。章程变更只走 `scripts/activate_charter.py` 四道闸。
- **涨跌停幅度规则**(10%/20%/5%/30%、制度分界日、新股豁免):唯一源
  `neckline/data/limit_derived.py` 顶部常量;EOD 批算 `compute_limit_derived`,
  盘中标量镜像 `compute_intraday_limit_prices`/`resolve_limit_pct`/
  `resolve_exempt_days`(单测互相对拍)。要算涨跌停价去这个模块找函数。
- **板块分类**(主板/创业板/科创板/北交所):唯一源 `neckline/data/board.py` 的
  `classify`/`classify_by_code`(按板块整段正则,禁止枚举精确子段——LinoN 黑名单
  坑教训);北交所前缀判断复用此模块,不另起 8/4/920 正则。
- **「总仓」分母**:唯一源 `Settings.total_capital`(默认 12 万,`.env` 的
  `TOTAL_CAPITAL` 覆盖);一切"占总仓百分比"计算读它。
- **题材持续天数(A2/B3 判据,v1.4-② 定案)**:唯一源 `neckline/report/industry_strength.py`
  的 `stock_persist_days`(`stock_basic.industry` 口径,H6 审计对象);持仓体检/候选安检/
  问询台三处判据入口都改读它。概念板块 `board_age`(`sectors.py`)**不退役但只做板块展示**
  (「所属热门板块」文案),**不再是任何判据的数据源**——两者是不同的量(行业一对一 vs
  概念板块多对多),新代码别把两套搞混,也别指望 board_age 反映题材过热度。

## 盘中哨兵(阶段3)

- **polars 链式赋值**:`df = df.filter(X).with_columns(Y)` 的 `Y` 里一律用
  `pl.col(...)` 表达式,不要 `df["col"]`(右侧先求值,绑到过滤前的旧 df →
  `ShapeError`,施工期真踩过)。
- **"上市第几天"判定**一律复用 `sentinel.universe.is_new_stock_exempt`(带
  「自然日差 > 30 天直接判非豁免」预筛)。裸调 `trading_days_between(list_date,…)`
  对 1990s 老股会退化成逐自然日循环 + 刷屏 warning(trade_cal 只覆盖 2015+,见
  `scripts/init_calendar.py`)。
- **纪律阈值比较**一律加 `_EPS=1e-9` 容差(`sentinel/holding.py` 体例):
  `0.08-0.02` 二进制浮点下 ≠ 0.06,裸 `>=`/`<=` 漏判边界。
- **Bark 通道**(`sentinel/channels.py`)从未活体验证;拿到真实 `BARK_URL` 先手动
  `BarkChannel(url).send(...)` 一次验协议。
- **盘中"关注池"是代理样本,不是全市场**(免费源限流取舍;V2-⑧-A 起 = 持仓 +
  T1/T2 篮子成员 + 候选〔⑬-1 前的残留〕+ 板块基准指数 + 昨日涨停,**自选池已退役**)。
  不够灵敏的备选是低频全市场轮询叠加,不是提主循环频率——取舍见
  `sentinel/retreat.py` docstring,改前先读。
- **给关注池加非股票代码(指数/ETF)前先看两处**:① `quotes.to_symbol` 必须让
  `.SH/.SZ/.BJ` **后缀优先**于数字前缀启发式(`000001.SH` 上证综指按前缀会被拉成
  `sz000001` 平安银行,**换了个标的还完全看不出来**,V2-⑧-A 修);② 退潮宽度样本
  `compute_breadth_snapshot` 靠「查无 `stock_basic` 元数据就跳过」天然排除它们
  ——这是它现有口径的副产品,**改那个跳过分支前先想想指数会不会混进分母**。
- **盘中存拍(V2-⑧-B)是旁路**:`engine.run_tick` / lifespan 循环里的存拍与篮子验证
  一律独立 try/except,失败只 WARNING;内存累计 + 15:05 一次 `write_table_day`
  (D4 拍板,**别改成逐分钟写**——那是整日文件覆盖写)。当日状态落 `sentinel_events`
  的 `sentinel='capture'` 行(`missing` 那天零行,状态写不进 parquet 行里)。
- **篮子 `falsified` ≠ 持仓该走**:同一个 `stop_pct`、同一个单一源,但一个问「这个
  驱动假设还成不成立」、一个问「这笔仓该不该走」。⛔ 篮子验证不接任何持仓动作、
  不进推送(守门单测扫 import)。
- 🔴 **「剧本 / script」在本仓有三个互不相干的含义,改任何一处前先认清是哪一个**:
  ① `precall.py::MemberScript` / `load_member_scripts` = **成员级冻结价位**(只有
  `ref_close` + `close_below_stop_line` 两个数,从卡上借来盘前核对用)—— **与卡上那段
  文字毫无关系**;② `basket_review.py::script_branch()` = 竞价 gap 落在强/平/弱哪一档的
  **机械分档**(`AUCTION_STRONG_GAP=0.02` / `WEAK_GAP=-0.02`);③ 卡 #6 的 LLM 文字段。
  ✅ **③ 已于 V2.3.3 批 ① 改名「预期上涨路径」**(卡键 `scripts` → `upside_path`,
  `CARD_SPEC_VERSION` v3 → **v4**),**①② 一行未动**;裸 grep `script` 仍会把三者一起捞上来。
  🔴 **判据码 `upside_script` 字符串一字未改**(已写进历史 `position_plans.plan_json` /
  `trade_clock.entry_plan_json`,改了旧行会假装缺件),只换了中文标签。
  🔴 **老卡兼容是 OR、不是替换**:`basket_card._upside_path_present()` 判
  `upside_path` **或**老 `scripts` 任一格非空 —— 冻结卡 `INSERT OR IGNORE` **永不回填新键**,
  只读新键会让昨天冻的那批篮子今天开仓时全部"缺上涨判断" = 凭空多一条假警示。
  同款三路 OR 在 `review/trade_clock.py` 的 ③ 预期路径(`entry_plan_json` 里同时存在三种形状)。

## D1 集合竞价确认层(`neckline/auction/`,V2.3.3 起;碰它之前先读)

- 🔴 **`baskets.engine_code` 是线码 `C`/`Z`/`Y`,`engine_version` 才是 `C1`/`Z1`/`Y1`**
  (源 `packs/*.json` 的 `manifest.line_code` vs `manifest.pack_version`)。V2.3.3 施工图
  的夹逼闸伪代码写的是 `engine_code == "Z1"` —— **照抄的后果是闸 2/闸 3 永远不触发、
  而且看不出来**。唯一实现 `auction/llm.py::engine_line_of()`(按**线**判,将来出 `Z2`
  照样管用);⛔ 别改回枚举版本号。
- **两阶段写的表不进 `_APPEND_ONLY_TABLES`,代偿闸门是「机械列永不 UPDATE」的列白名单**
  —— 而那条守门**按 AST 取 SQL 字面量**:`UPDATE … SET` 一旦改成动态拼接,守门当场**失明**
  却照样绿。故 `auction/store.py` 的 UPDATE 必须是**静态字面量**,「本次不改」用
  `COALESCE(?, 原值)` 表达;守门另加一条「解析不出列集合就红」。
- **`concurrent.futures.ThreadPoolExecutor` 的工作线程不能设 daemon**(解释器退出时会
  `join`),要「进程退出不阻塞」只能用裸 `threading.Thread(daemon=True)`。硬截止靠
  `join(timeout=)` + `store.finalize_*` 的幂等 `WHERE llm_stage='pending'` **双保险**,
  外加 LLM 层**根本拿不到 store 句柄**(结构上写不进去)。
- 🔴 **`Text(String)` 那条坑有两个新现场(V2.3.3 实拍逮到,两条守门已立)**:① **服务端下发
  的文案里⛔ 不许写 Markdown** —— 客户端拿到的是 `String`,`**代理样本**` 的星号会原样上屏
  (`test_server_facing_text_carries_no_markdown` 递归扫整份响应);② **`Text("a" + "b")` 同病**
  —— `+` 拼出来是 `String` 而非字面量(`test_no_client_text_concatenates_markdown_with_plus`
  **全客户端扫**)。要强调:服务端用「」,客户端拼成**一整条字面量**。
- **竞价卡收起行只写 `验 D0 <日期>`,⛔ 别再带 D1 那个日期**:客户端拉的就是 today,
  「D1 = 今天」是恒真的废话;402pt 上两个八位日期会把后一个截成 `验 D0 202…`
  —— **一个看不出是哪天的日期比不写更糟**。完整日期在弹层标题里。
- **演示竞价报告必须落在「今天」**:客户端 `fetchAuction()` 不带 date → 服务端缺省 `date.today()`。
  演示库写死昨天 → 404 → **卡根本不画**(那是**正确行为**,不是 bug,别去查客户端)。
- 🔴 **「竞价层零新阈值」这句话自 2026-08-12 起有四个例外,四个全是用户裁定值**(§七 P3-69/P3-70):
  `HISTORY_LOOKBACK_TRADING_DAYS=20` · `HISTORY_LOOKBACK_MAX_CALENDAR_DAYS=60` ·
  `HISTORY_MIN_SAMPLE_FOR_COMPARISON=15` · `SECTOR_PEER_MIN=3`。⛔ 工程侧一个都不许改、
  也不许再加第五个。⚠ 其中 **15 把「历史样本够不够」从 LLM 手里挪回了机械侧** ——
  旧注释/旧文档里「交 LLM 判」的说法**已作废**,别照着它改回去。
- 🔴 **`rel_to_index` 与 `rel_to_sector` 是两条独立路径,⛔ 禁止同源同值**(裁定 P3-70):
  前者减**市场指数**(沪主板 000001.SH / 深主板 399001.SZ / 创业板 399006.SZ / 北交所 899050.BJ;
  **科创板按 K8 §三 排除 → `None` + `board_excluded`,⛔ 零 fallback**);后者减**板块基准**。
  ⚠ **板块指数路径(①)本版取不到、且大概率长期取不到**:板块指数是同花顺 `.TI` 代码,而 9:26 实时
  行情走新浪/腾讯、`quotes.to_symbol()` 只认 `.SH/.SZ/.BJ` → `.TI` 会被拉成另一个标的。**这不是 bug,
  ⛔ 别去"修"**;现役恒走 ②「≥3 只同行业(`stock_basic.industry`)对照股中位数」,
  `sector_benchmark_source` 如实落码。⛔ **禁止用市场指数顶替板块基准**(结构性保证:取样域
  `snap.industry_of` 只由 `stock_basic` 派生,指数进不去 + `index_codes` 显式排除 + 正反两条守门)。
  ⚠ 对照股取自**关注池 = 代理样本**,「对照不足」是真实早晨的常态,不是故障。
- **合成竞价冒烟里指数必然「拉不到」**:`daily` 分区只有个股,三支市场指数没有行 →
  `data_quality` 恒 `degraded` → 闸 1 恒命中。**这是合成环境的局限,不是代码故障**
  (生产走 `sentinel/quotes.py` 真拉,指数有报价)。⚠ 本地开发库常无现役骨架包 →
  D0 零篮子 → 冒烟只能验市场段,故 `scripts/smoke_auction.py` 会**往临时副本**合成一个
  Z1 篮子把闸 2 走一遍(⛔ 只写 tmp,不碰 `data/neckline.db`)。

## SwiftUI 客户端(阶段4C / v1.1-E/F/G)

- **`CandidateOut.board` 是英文枚举码**(`MAIN`/`GEM`/`STAR`/`BSE`,源
  `neckline/data/board.py`);中文展示走客户端 `Candidate.boardLabel` 纯展示层
  换算,不要服务端另建中文映射、也不要客户端重推分类。
- **Swift URLProtocol 网络桩里 `request.httpBody` 常是 nil**(POST body 被转成
  `httpBodyStream`);断言请求体复用 `DTODecodeTests.swift::httpBodyOrStream()`
  两路都读,否则误判"请求体丢了"。
- **本地 dev 后端联调不要从零 bootstrap**(会卡"策略大脑无现役版本"):
  `sqlite3 ATTACH` 真实 `data/neckline.db`,只拷 `trade_cal`/`strategy_versions`/
  `stock_basic`/`namechange` 四张只读参考表进隔离临时库,业务表留空,再跑
  `scripts/report.py <date>` 即可吃真数据出报告。
- **本环境 computer-use 点不动 Simulator/macOS App**(权限拒,`mcp__Claude_Code_iOS_
  Simulator__control` 的 `attach`/`tap`/`swipe` 报 `Xcode is installed but not
  selected`;⚠ **v1.5-⑤ 核实:该报错与宿主 shell 的 `xcode-select -p` 是否正确无关**
  ——本机 Bash 里 `xcode-select -p` 已正确指向 Xcode.app,该 MCP 工具仍报同样的错
  〔工具自身另跑一遍检查,大概率跑在不继承 shell `DEVELOPER_DIR` 的沙盒里〕,**别浪费
  时间去修 xcode-select**,直接走下面的 `xcrun simctl` 路线):视觉核对走 `xcrun
  simctl io <device> screenshot`(免点击权限)+ QA 钩子 `NECKLINE_INITIAL_TAB`/
  `NECKLINE_INITIAL_MODAL` 切初始 tab/弹层(iOS 用 `SIMCTL_CHILD_` 前缀传入 launch;
  env 需要 UserDefaults 的项如后端环境/token 用 `xcrun simctl spawn <udid> defaults
  write <bundle_id> <key> <value>` 提前写入);macOS 原生截图被沙盒挡时,拿"iOS 截图 +
  双端 `xcodebuild` BUILD SUCCEEDED"当等价证据,不死磕。
  🔴 **⛔ 一律不许用 iPad 模拟器,任何用途都不许**(2026-08-11 用户当场裁定,V2.3.2 ⑥
  出图时下达)—— **本项目只有 macOS 与 iOS 两个平台,没有 iPadOS**;拿 iPad 出的图
  不代表任何一个真实端。~~原文:内容纵向溢出 iPhone 视口时临时 `simctl create` 一台
  iPad 看更多内容(v1.4-⑧ / v1.5-⑤ 验证过)~~ —— **该做法已作废,⛔ 不得再援引**。
  **页面内容纵向溢出 iPhone 视口时,只有两条路**:① **把演示数据前面几段砍短**
  (演示库改一行的事);② 砍不动就**如实认截图缺口 + 靠单测兜底**(体例:
  `tests/test_out_candidates.py::test_out_candidate_row_stays_402pt_safe` —— 把那一行的
  `lineLimit`/`fixedSize`/两行结构钉成机器判据),并把缺口挂进 §七。⛔ 不死磕、⛔ 不换设备。
- **本地零 LLM key 时,LLM 参考件/K4 派发警示截图靠写库伪造,不必等真 key**:候选
  `llmJudgment` 走独立 `llm_judgments` 表联查(**不在** `candidates_json` 内,塞
  `llm_judgment` 键无效,改用 `report_store.save_llm_judgment(...)` 插行);
  `reference_plan`/`dispatch_alerts` 才在 `candidates_json`/`watchlist_json`
  内、且已是 camelCase(`to_public_dict()` 原样存档)。跑一次 `scripts/report.py`
  生成真报告后直接 `UPDATE reports SET candidates_json=…` 改 2~3 只候选即可拼出
  ok/vetoed/judgeSkipped 三态样例(v1.5-⑤ 验证过)。
- 🔴 **出截图用 `defaults write` 配后端/token 时,⛔ 绝不许写宿主的 `top.linotsai.neckline` 域**
  (2026-08-10 真踩,把用户**正式 macOS App** 打成"连不上、什么都没有"):那个 bundle id
  **就是真 App 用的同一个 UserDefaults 域**,写进去 = 把假后端地址(`http://127.0.0.1:8013`)
  与假 token 塞进用户天天在用的那个 App,而 `NK_BASE_URL_OVERRIDE` **压过默认 prod 基址**。
  **正确姿势**:iOS 模拟器走 `xcrun simctl spawn <udid> defaults write …`(那是模拟器**容器内**的
  域,与宿主无关);**macOS 侧要假数据就别用 `defaults`** —— 改用起一个本地后端 + 手动在设置屏
  切换,或干脆只出 iOS 截图。**排障口诀**:用户报「换包后连不上/一片空白」→ **先
  `defaults read <bundle_id> | grep NK_`**,看有没有被写脏。⚠ 假 token 比没有 token 更坏:
  它让 App 看起来"配好了"却一直 401。
  🔴 **这一步是「读一眼」,⛔ 不是「清空」**:该域里**本来就该有** `NK_API_TOKEN`(用户自己填的
  真 token,打生产返 200)—— 要找的是**多出来的 `NK_BASE_URL_OVERRIDE`**。⛔ 见 `NK_` 就删 =
  把用户的鉴权也一起删了。
- **模拟器截图被推送授权弹窗盖住时,别去点它(点不动)**:`UNUserNotificationCenter`
  的授权弹窗会挡住页面中部,而且**终止 App 甚至重装都不会让它消失**(它挂在
  SpringBoard 上);`xcrun simctl privacy` **不支持 notifications**。两步解:①
  `NECKLINE_SKIP_PUSH_PROMPT=1` 跳过挂推送(v1.5/⑮ 起的 QA 钩子);② 已经弹出来的
  那一个,**`simctl shutdown` + `boot` 重启模拟器**才清得掉。
- **需要展示"报告加载完成后才能确定的内容"(如篮子卡 / 信息卡 / NL 确认卡)时**,`NecklineApp.
  init()` 里的同步 QA 钩子够不着(数据是 `AppModel.refresh()` 异步拉的)——照
  `NECKLINE_INITIAL_TAB` 先例另开一个 env 钩子,放在 `refresh()` 数据到位之后触发
  (v1.4-⑧ `NECKLINE_INITIAL_INFOCARD_CODE` 先例),不要塞进 `init()`。
- **客户端 404 映射**:`APIClient.mapReason` 按 reason 逐 case + fallback;新增会
  返 404 的端点必须检查要不要加新 case,别指望 fallback 猜对文案(watchlist
  `not_found` 被误显示成"持仓已清"踩过;**同一个 reason 字符串复用已有 case 不算
  "没加",只有新字符串才需要新 case**,v1.4-⑦-A `decisions/{id}/track` 验证过)。
- **`NecklineTests` 只在 iOS Simulator destination 跑得动**:`-destination
  'platform=macOS'` 下 `xcodebuild test` 报 `Could not find test host`(`TEST_HOST`
  按 iOS bundle 布局配置,与 macOS `.app/Contents/MacOS/` 嵌套路径不符,既有工程
  设置、与代码改动无关);`build`(非 test)双平台都能跑,验收走「双端 build +
  iOS Simulator test」组合,不必强求 macOS test 绿(v1.4-⑦ 验证过)。
- **服务端字段与客户端既有计算属性撞名**(如 `distToStopPct` 小数 vs 百分比):
  CodingKeys 显式改名解码(`distToStopPctServer`),不改旧属性既有语义(有单测锁)。
- **Swift `Encodable` 合成对 Optional 属性一律走 `encodeIfPresent`(nil→省略键)**,
  但契约要求"键必须永远出现、nil 时编成 JSON `null`"的字段(如 `maxChasePct`,后端
  用 `model_fields_set` 判断"有没有传过")必须手写 `encode(to:)`、对该字段单独用
  `container.encode(optionalValue, forKey:)`(不是 `encodeIfPresent`)——`Optional`
  自身的 `Encodable` conformance 在 nil 时走 `encodeNil`,键因而总会出现
  (v1.4-⑧ `DecisionCreateRequest`/`DecisionReviseRequest` 定案)。
- **落库快照按"是否随每次响应重新拼装"分两类,决定新字段要不要手写容错解码**:
  `intel_rank`/`info_card_summary` 这类挂在 `_shape_candidate` 上的字段,服务端每次
  响应都用 pydantic 默认值重新构造,新字段旧数据也会补全,客户端可以偷懒用
  `Optional`/默认值自动兜底;但 `reviews.result_json`(`review_store`)是**写入当时
  冻住**的历史快照原样读回,不会因服务端升级而补全新键,新增字段(如
  `charterSegments`)必须给该 DTO 手写 `init(from:)` 做 `decodeIfPresent` 兜底
  (v1.4-⑧ `ReviewWeeklyResult` 定案)——加字段前先确认是哪一类,别套错模板。
- **Swift 合成 `Decodable` 对非 Optional 属性「有默认值也不容忍缺键」——V2-⑮ 起客户端 DTO 一律
  手写 `init(from:)`**(不只是冻结快照那一类):B 类(`basket_cards.card_json` /
  `basket_review_daily.mech_json` / `reviews.result_json`)是**硬要求**,守门单测按类型名精确锁
  (`tests/test_contract_crosscheck.py` 的 `..._hand_write_init_from_decoder`);A 类手写是白拿的保险。
  ⚠ 新建 B 类 DTO 别在它前面放**同前缀**类型(守门用 `split("struct <Name>")` 取首个匹配,会切错块)。
- **`NKJSON`(自由结构透传字段的载体)解码顺序:Bool 必须排在 Double 之前**——
  JSON `true` 在 Foundation 里也能解成 `1.0`,顺序反了布尔会悄悄变成数字(界面显示
  「1」而不是「是」),且**看不出是 bug**。
- **服务端删/停发任何键之前,先查已装客户端是不是硬解码**:`Models.swift` 里手写
  `init(from:)` 的 DTO 混着 `try c.decode`(必需)与 `decodeIfPresent`(可选)两种
  写法 —— 如 `Candidate` 的 `buyPoint`/`stop`/`target`/`invalidation` 是 `try c.decode`,
  服务端不发 = 整份报告解不出、今日计划全空。**淘汰老字段的正确顺序**:先发一版客户端
  把该属性改 `decodeIfPresent` + 默认值,**下一版**服务端才可删键(顺序反了就炸,
  v1.5.0 老四件套退役据此走「键保留 + 过渡文案」)。

### V2.3 前端视觉整改新增坑(2026-08-10 实战)

- **⛔ 视图里不再写裸 `.system(size:)`** —— 字阶收在 `NKFont` 八档 + 两数字档(`DesignTokens.swift`)。
  V2.3 之前散着 **22 个字号档 / 455 个调用点**,散着写就必然漂回去。**例外只有图标**
  (`Image(systemName:).font(.system(size:))` 不属字阶)。`label` 档要 tracking,用 `.nkLabel()` 一次给全。
- **`NKChip(text:)` 传空串会渲染成一枚没字的灰胶囊**(截图核对时逮到):它既不是「没有」也不是
  「没看」,只是噪声、还看起来像界面坏了。现已在组件内 `text.isEmpty → EmptyView()`。
  ⚠ 真要说「这一项没取到」得**用一句话说出口**,⛔ 别指望一枚空徽标能暗示什么。
- **iPhone 402pt 宽度放不下「名称 + 代码 + 角色 + 两枚判定徽标 + RS + chevron」一行**:会把名称挤成两行、
  把徽标压成**竖排单字**(「位 置 合 适」)。成员卡收起行因此 **iOS 分两行 / macOS(详情栏 ≥700pt)仍一行**。
  ⚠ 这类挤压**编译不报错、单测也测不出**,只有实拍看得见 —— 新增横向密集行一律先出一张 iPhone 截图。
- 🔴 **「占总仓 %」的分母客户端拿不到**:唯一源是服务端 `Settings.total_capital`(默认 12 万,`.env` 的
  `TOTAL_CAPITAL` 可覆盖),而它**从未下发**(`schemas.py`/`app.py` 零出现)。⛔ 别在客户端写死 12 万 ——
  那是给一个钉死的领域常量造第二份事实源,用户改了 `.env` 界面就一直说谎。要占比先让服务端发这个数。
- 🔴 **六关的「判不出」是篮级、不是格级**:`gates.py` 对判不出的关发的是 `VERDICT_PASS + available=False
  + blocks_t1=True`,而 `tier.py::_gate_breakdown` **只把 `verdicts` 与篮级 `blocks_t1` 写进冻结卡** ——
  「是哪一关判不出」契约里查不到。宫格格级只画 pass/degrade/reject,缺键 → 「未记录」灰格
  (⛔ 绝不渲染成「过」)。要格级得服务端先把逐关 `available` 落进冻结卡。
- **`xcodegen generate` 会顺手修好 project 级 `MARKETING_VERSION` 漂移**:本次重跑发现 pbxproj 的
  **project 级**停在 `2.0.0`、app target 却是 `2.2.0`。守门单测 `test_client_version_governance.py`
  **只比 app target**(project 级块 `PRODUCT_NAME = "$(TARGET_NAME)"` 被刻意排除)→ 这处漂移**一直是绿的**。
  ⚠ 加新 `.swift` 文件必须 `xcodegen generate`(pbxproj 是显式文件引用,**没有** `PBXFileSystemSynchronizedRootGroup`)。
- 🔴 **用户正式 macOS App 在跑时,⛔ 别指望启动 Debug 构建来截 macOS 图**:同 bundle id,
  LaunchServices 只会把**那个还在跑的旧版**切到前台 —— 截出来是旧版,**比没有截图更误导**。
  且全屏 `screencapture` 会拍到用户桌面(不是你该留的东西)。
  ✅ **解法已实测通(2026-08-10,V2.3.1 立项):`ditto` 出构建产物 → `PlistBuddy` 改副本
  `CFBundleIdentifier` 为 `top.linotsai.neckline.dev` + 改 `CFBundleName` → `codesign --force
  --deep --sign -`(macOS 侧无 entitlements、非沙盒,ad-hoc 够用)→ `open` → 用
  `open` → **App 自己截自己的窗口**(`NKDevCapture.swift`,DEBUG-only)。
  🔴 **⛔ 别用 `screencapture -l<windowid>`**:V2.3.1 批 1 实测它会**在会话中途对任何 App 失效**
  (拿 Xcode 窗口对照测过)。⚠ **「窗口标题读得到 = 有屏幕录制权限」这个判据在 macOS 26 上不成立**
  —— `CGWindowListCopyWindowInfo` 仍读得到 27/31 个标题、`CGPreflightScreenCaptureAccess()` 仍返
  `true`,但 `SCShareableContent` 报 **`displays=0`**。**要判权限就查 `SCShareableContent.displays` 非空。**
  ⚠ 截**弹层**时 App 自截必须把 `attachedSheet` 合成进来(sheet 是另一个 `NSWindow`,否则截出一片灰);
  ⚠ `kill -9` 反复杀 dev 变体会留下**损坏的 Saved Application State** → 下次启动卡在 `talagent`、
  窗口永不出现(进程活着、0% CPU、日志为空,极易误判成截图链坏了);解:
  `rm -rf ~/Library/Saved\ Application\ State/<bundle id>.savedState` +
  `defaults write <bundle id> ApplePersistenceIgnoreState -bool YES`。
  ⚠ 顺带拆雷:dev bundle id **另一个 UserDefaults 域** → `defaults write` 配演示后端终于安全。
  🔴 **宿主域自检只查一个键:`defaults read top.linotsai.neckline NK_BASE_URL_OVERRIDE`
  必须报 does not exist**。⛔ **绝不许 `grep NK_` 判空、更不许「不为空当场清掉」** ——
  该域里的 `NK_API_TOKEN` **就是用户从设置屏亲手填的真 token**(2026-08-10 实测:拿它打生产
  `/positions` 返 HTTP 200),照 `grep NK_` 清空 = **把用户正式 App 的鉴权当场清掉、之后一直 401**,
  比原本要防的那颗雷更狠。要防的雷**只有一颗**:`NK_BASE_URL_OVERRIDE` 被写进宿主域。
  「iOS 截图 + 双端 BUILD SUCCEEDED」降级为**兜底**,⛔ 不再是 macOS 的默认结案方式。
- 🔴 **`Text(String)` 不解析 Markdown,只有 `Text("字面量")` 解析**(V2.3.1 批 2 实拍逮到两处):
  诚实披露文案里满是 `**没看**` / `**恒在**`,一旦把它改成 `+` 拼接、或传进一个 `String` 参数,
  **星号会原样印在界面上**。要传参就把形参声明成 `LocalizedStringKey`;要拼接就先拼成一整条字面量。
  ⚠ 这类回归**编译不报错、单测也测不出**,只有实拍看得见。
- **服务端 `dict` 的 key ⛔ 不许直接进 `Text`**(V2.3.1 批 2:行情状态五维缺维在首屏印出
  `moneyflow_migration`,与 V2.3.1 硬伤 2「角色码印英文」同一个病的第七处)。一律照 `nkBoardLabel`
  先例补一个展示层换算函数、**未识别值原样透传**。见到 `.keys` / `objectValue` 直连界面就停一下。
- **本地演示库里 `sentiment_json` / `sectors_json` 是 snake_case 原样透传**(客户端 CodingKeys 就是
  `limit_up_count` 这套,`position_quota` 还必须是中文「满额/半额/休息」)。写成 camelCase 会**静默**
  解不出来 → 界面显示「本次没有情绪仪表盘数据」,看起来像组件坏了。`basketDaily` 内部反而是 camelCase
  —— **同一份报告快照里两种命名并存是既有事实,⛔ 别"统一"**。
- **macOS 换包后图标没变,先怀疑缓存、⛔ 别重做图标资产**(2026-08-10 实证:装机包
  `Assets.car` 10 条 mac 渲染项齐全、`AppIcon.icns` 抽出来就是新图,Dock 上却还是旧图)。
  **可执行的验证链**:`assetutil --info <app>/Contents/Resources/Assets.car`(看 `RenditionName`
  与 `PixelWidth` 齐不齐,**免 sudo**)+ `iconutil -c iconset <app>/Contents/Resources/AppIcon.icns`
  抽位图目视。⛔ 「文件时间戳看着对」不算证据。刷新要 `sudo`(删 `iconservices.store` +
  `lsregister -f` + `killall Dock`)→ 归**用户手动清单**。
- **要让富状态(成员卡 / 六关宫格 / 刻度尺)出现在截图里,得先有数据**:走 `DB_PATH=<临时库>` 起一个隔离
  后端(`API_TOKEN` 需 **len≥16**,否则 lifespan fail-fast),临时库只从真库拷四张只读参考表、业务表手写
  假数据。⛔ **不碰 `data/neckline.db`**。内容纵向溢出时,与其死磕滚动,不如**把演示卡前面那几段砍短**
  (演示库改一行的事)。⚠ **⛔ 不许换 iPad**(2026-08-11 用户裁定,见上文该条);砍不动就认缺口 + 单测兜底。
  ⚠ **篮子卡的 ⑥ 及其之后各段在 iPhone 上恒在第二屏**(V2.3.3 ⑦ 实证):打分卡(~200pt)与
  六关宫格(~330pt)是**结构性压着**它的两张大卡,**演示库把 `scorePercent` 与 `gates` 一起置空**
  才拍得到 ⑥ —— 真实数据下它必然要滑一屏,**这不是缺陷,是那一页本来就长**。
  ⚠ **砍短也有天花板**(V2.3.2 ⑥ 实证):选股屏 ③b-2 前面固定压着 行情状态卡 + ① 情绪卡 +
  ④ 昨日复盘卡 + ③ 的 T1/T2 两个空态 —— **把情绪置空反而更高**(空态卡比填满的还大)、
  把 `basketsAvailable` 由 false 翻 true 也只是把一张大卡换成两个空态,三轮砍下来仍在第二屏。
  这类"结构性压着"的段落**直接认缺口**,别再试第四轮。
- 🔴 **演示库里凡「服务端枚举码」字段,必须喂真词表值,⛔ 别喂中文**(V2.3.1 批 3 / 批 4 / 批 5
  **连踩三次**):喂中文等于把**展示层换算这一整类 bug 全部屏蔽** —— `*_clamp` 印
  `rejected_not_above_close`、画像行印 `role · leader`、信息卡印 `pullback_leader`,三次都是
  **实拍看不出来**、把种子换成真码后当场现形。判据:该字段服务端发什么码,种子就写什么码。
- 🔴 **`.hiddenTitleBar` 之后有四件事必须一起做**(V2.3.1 批 1):① 红绿灯仍被系统钉在标准标题栏
  28pt 中线,要跟自建 50pt 栏对齐得手动挪 `standardWindowButton(_:)` 的 frame;② **拖窗只挂那一条栏**
  (`.gesture(WindowDragGesture())`)—— ⛔ **不许 `isMovableByWindowBackground = true`**,那会让列表、
  卡片处处能拖窗,一次没点准的点选就把窗口拖跑,**编译与单测都发现不了**;③ 内容要
  `.ignoresSafeArea(.container, edges: .top)`,否则 SwiftUI 仍按 32pt 安全区把内容下推、顶上白一条;
  ④ UserDefaults 里的 `NSWindow Frame …` **不再生效**,截图基准要靠 env 钉死。
- **金额格式化分两档,且符号在 `¥` 外**(V2.3.1):`NKFmt.price` 两位小数 + 千分位(股价 / 费用)·
  `NKFmt.amount` 无小数 + 千分位(合计成本 / 敞口)· `NKFmt.signedAmount` 符号在 ¥ 外。
  ⛔ 别把负数直接喂 `NumberFormatter` 再自己拼 `¥` —— 会得到 **`¥-1,116`**(负号跑进货币符号里面),
  **每一笔亏损仓都会中**,编译与单测都发现不了(已立 4 条单测钉死,期望值取自原型行号)。
  locale 钉死 `en_US_POSIX`:跟系统区域走会让不同机器的截图对不上,某些区域还会空格分组。
- **改双端共用件时,⛔ 别只改一个平台的调用点**(V2.3.1 批 7 实拍逮到):批 2 改了「① 情绪与市场语境」
  的共用件却只改 macOS 调用点 → **iOS 上那个标题出现了两次**。判据:动共用件后,**两个平台各出一张实拍**。
- **`xcrun simctl launch <udid> <bid> K=V` 会把 `K=V` 当启动参数**,不是环境变量 ——
  必须写 `SIMCTL_CHILD_K=V xcrun simctl launch …`(V2.3.1 批 7 订正:此前误判成
  `NECKLINE_SKIP_PUSH_PROMPT=1` 失效,其实是传参姿势错了)。

## 周复盘对账(阶段4D)

- **`review_col_map` 必须同时驱动"格式判定"与"列取值"**:`parse.py::_detect_format`
  的判据列也要吃 col_map 覆盖(表头锚点「交易日期」除外),否则映射形同虚设。
- **反推价格的必需列整列缺失必须硬跳该行 + 警告**,绝不 `or 0.0` 静默兜底;
  「缺列」(`cols[...] is None`)与「该行这格是 None」分开处理(费用列名不匹配 →
  价格错得看似合理,踩过)。
- **同花顺自选导出 txt 编码/格式未活体验证**(`watchlist.py::parse_ths_txt`
  UTF-8→GBK 兜底,行首取 6 位数字);拿到真实文件先跑一次
  `POST /watchlist/reconcile-ths` 验协议假设。

## 生产实战定案(v1 上线 → v1.3.5;事故全文见 archive 详版变更日志)

- **Parquet 类型漂移毒化分区(两次崩报告,v1.3.5 定稿)**:某日某列全空 →
  pandas object → polars String,与历史 Float64 分区冲突 → `scan_parquet` 整表
  `SchemaError`。规则四条:① 新落盘路径必须走 `write_table_day`,不自己
  `write_parquet`;② schema 对齐向 `market_data.TABLE_FLOAT_COLS` **声明**看齐,
  永不向"第一个文件"看齐(排序第一个可能是 backfill 落的 0 行空文件 = 脏基准;
  **空分区是脏基准的唯一来源**,对齐要问"基准可信吗"不是"有没有对齐");
  ③ `_VALID_TABLES` 加新表必须同步补声明(守门单测会挂);④ 写侧修好**不会让
  历史脏分区自愈**,修数据照 `scripts/oneoff/fix_moneyflow_schema.py` 体例
  (逐文件 cast、幂等、不整表 scan)。
- **核心管线对可选情报输入的调用必须包保险丝**:一处裸奔就把"排序少一维"升级成
  "当日无报告"(07-27 `intel_candidates` 调 `compute_sector_moneyflow` 真崩过)。
- **大上下文推理走流式,⛔ 别再去抬那个固定读超时**(案底 §七 **P0-40 → P0-44**,同一个病一天
  内复发两次):90s→240s 抬完,当晚 3/3 次**精确各花 240s** —— 「整段生成必须在 X 秒内回完」这个
  判据要求提前猜准一个**与上游吞吐挂钩、每天都不一样**的数字,再抬只是推迟下一次翻车。**根治 =
  换判据**:`LONG_CONTEXT_TASKS`(推理/定档/剧本/复盘)开 `stream:true`,httpx 的 read 超时天然
  作用在每次 socket 读上 → 语义变成 **chunk 间隔 90s**(判「还在不在吐字」,与吞吐无关);⛔ **看见
  90 别以为回退了**,两处 90 含义完全不同。⛔ **检索类刻意保持非流式** —— GLM `web_search` tools
  协议 × 流式本项目从未验证,v1.3.4 案底说明这种组合坏起来是**静默的**。唯一实现
  `llm/router.py::{use_streaming,read_timeout}_for_task()`(两项**必须同路接线**,只接一半 = 原病
  复发),唯一接线点 `factory.get_provider(task)`;⛔ 别改成 `chat()` 参数(漏一个调用点还看不出来)。
  ⚠ 流式下单次调用墙钟**无固定上限是刻意的**,由 chunk 间隔 + 预算账 + `neckline-basket.service`
  的 `TimeoutStartSec` 三层兜(单测把三者钉死,改一个不改另一个会红)。
- **GLM 联网搜索 0 命中(已结案,v1.3.4 真 key 实证)**,两个真因:①
  `search_engine` 取值不被上游认识会 `ok=True` **静默返 0 条**(模型退训练数据
  作答,文字看不出);② 检索词跟**最后一条 user 消息**走,代词提问救不回来。
  修法已上线:`provider.chat()` 可选 `search_query`(不传时 payload 逐字节不变,
  单测锁死)+ 问询台补中文名(`resolve_stock_names` 是查名唯一实现)显式传检索词
  + 0 命中 WARNING 埋点。⚠ **已证伪勿再查**:payload 里 bool/int 发成字符串不是
  bug(GLM 正确解析,刻意保留原样)。
- **喂 LLM 的上下文必须带「今天是哪天」,否则它没有"现在"的概念**(2026-07-30 用户报障
  真踩:问询回答把 2024 年研报目标价当现行参照;**联网是通的**,是模型分不清新旧)。日期锚 +
  时效纪律 + 检索词年份引导的唯一实现 = `llm/prompt_context.py`,新增任何 LLM 链路都得
  import 它(守门单测扫全仓禁止抄第二份)。**排查同类问题先看 prompt 有没有日期,别一上来
  怀疑搜索没通** —— `inquiry_log.search_hits` 字节数能一眼证伪。
- **机器可读标签后面一旦还挂内容,`_parse_verdict` 的 last-match 锚点就被架空**(v1.5.1
  两向复现):「结论:通过|否决」取最后一个匹配,前提是标签**是输出的最后一段**;v1.5 把
  三件套 JSON(script/why/veto_reason 全是自由中文)排在标签之后,JSON 里出现该词组就静默
  翻转结论。规则:**凡标签后面还挂内容的调用方,必须先剥掉那段内容再解析 verdict**
  (`judge_candidate(narrative_splitter=…)` 依赖注入,`llm/` 不反向 import `report/`);
  prompt 的禁令只是背带,不能当安全带。
- **「本地实测廉价」不是生产结论,全历史 parquet 扫描尤其**(2026-07-29 v1.4 ⑨ 部署翻车,
  §七 P0-23):开发机 Mac 上 `<1s` 的 `compute_industry_strength`(扫 `daily` 全history 784 万行),
  在生产(**2 vCPU / 1.6G RAM**)**700M cap OOM-kill、1400M cap 600s 跑不完**。规则:①**新增全表
  `scan_parquet` 路径,上云前必须在生产机上单独计时 + 量峰值**(`systemd-run --scope -p MemoryMax=…`
  跑隔离进程,别拿常驻服务当小白鼠);② 别用「抬 `MemoryMax`」糊算法成本问题(1400M 都不够);
  ③ **重活别放常驻 `neckline.service`**——它与盘中哨兵同进程,`MemoryHigh` 先节流会让进程陷进回收
  死循环(`memory.events.high` 飙升、`oom_kill=0`)= **卡死不报错**,盘中点一次就拖累哨兵。
- ✅ **「生产机性能探针纪律」已于 2026-08-11 整条删除(用户裁定)** —— 原规定探针/压测只在
  收盘后 15:00 之后跑、避开 16:00–17:00、串行、`load > 4` 立即停手。**开发期不看时钟**:
  部署 / 重启 / 跑实测随时可做。用户原话:「**我们开发期就是开发期,投入使用了再要注意开盘
  时间的问题**」。⛔ **不得以任何形式恢复**(包括"顺手加一句稳妥起见避开开盘");真正投入
  使用后若要重新设限,那是**新的一次裁定**,不是把这条翻出来。
  ⚠ 与之无关、**仍然有效**的是收尾卫生:跑完 `pgrep -af` 确认无残留 + `systemctl reset-failed`。
- **等远端长任务:`systemd-run` 记得 `--no-block`,ssh 一律带 keepalive**(2026-08-05 连踩两次)。
  ① `systemd-run` 跑 `Type=oneshot` **默认阻塞到 ExecStart 退出**,一次 29 分钟的链会把"启动命令"
  变成 29 分钟前台调用;② 几十分钟零输出的 ssh 守候会被 NAT/防火墙静默掐断,**既不报错也不返回**
  (同日对照:三条里只有带 `-o ServerAliveInterval=30` 的那条每条事件都送达,另两条全哑)。规则:
  `-o ServerAliveInterval=30 -o ServerAliveCountMax=6` + 轮询每圈打一行心跳,或多次短连接代替长连接。
- **在远端 `pkill -f <pattern>` 前先确认 pattern 不匹配自己**:`pkill -f probe_industry` 会匹配到
  正在跑它的那条 `bash -c` 命令行,自杀式掐断 SSH 会话(exit 255,2026-07-29 真踩)。用
  `pgrep -af` 先看命中集,或按 PID 杀。
- **判据类全市场扫描一律预计算落表,在线路径只读**(2026-07-29 P0-23 定案):任何"扫全市场
  多年历史"的量,若成了判据/排序输入,就必须 16:05 日更算一次落 SQLite,报告/端点/问询台
  **只读表**;缺行走保险丝(**降级方向=不拦** + `dataFreshness` 显式披露),**不许在线现算
  自愈**。现役实现 `report/industry_strength_store.py`(表 `industry_strength_daily`),
  单一源仍在 `report/industry_strength.py`(表只是物化,三路等价单测锁死)。
- **`rank(method="ordinal")` 的并列由行序打散 = 不确定性**(2026-07-29 真数据演练打出来):
  A 股一天里收益完全相同的票成堆,110 个行业当日中位数撞车很常见;行序又随"读的是按年块
  还是单日分区"而变 → 同一天算出两种 rank。**任何进判据/排序的 rank 必须先排定确定性
  tie-break 再 ordinal**(体例:`_day_local_table` 按 `(median_ret 降序, industry 升序)`)。
- **systemd timer 触发 `.target` 必须给 target 配 `StopWhenUnneeded=yes`**(§七 P0-45,08-06 整晚
  没跑):timer 触发后停在 `TIMER_RUNNING`,**只有被触发的 unit 转 inactive 才会重算 NEXT**;
  `.target` 不会自己落下 → NEXT 永远算不出 → **首晚必成、次晚起静默全哑**(自检:
  `systemctl show <timer> -p SubState -p NextElapseUSecRealtime`,正常是 `waiting`+有值)。
  ⛔ **别信"已 active 的 target 吞掉触发"那套说法**(已被 08-05 整链跑完证伪);⛔ 被 target
  拉起的 oneshot **永不许加 `RemainAfterExit=yes`**(那才会让 ExecStart 一行不执行地静默空跑)。
- **在已卡住的机器上首次加那一行:先 `stop` timer 再改**(同上,次生事故真踩):`daemon-reload`
  **异步**落下 target(同步查一次还看得见 `active`,极具迷惑性)→ timer 以过期的 `LastTriggerUSec`
  为基点算日历槽 → 算出**已过去的**槽 → **当场补跑一遍全链**(带 `--notify` 就真推送);
  `Persistent=false` 拦不住(它只管重启后补)。
- **凡"周期性"的东西,验收必须验第二次**(P0-45 通用教训):⑯-D 只验了首跑成功 + NEXT 存在,
  两项首晚都为真;缺的是**跨两次触发的不变量**(跑完后状态有没有复位)。单次跑通证明不了排程还活着。
- **timer 跑过 ≠ 任务成功**:部署/定时任务验收必须看 `ExecMainStatus=0` **且**
  `ExecMainStartTimestamp` 是本次那一跑,别只看 `list-timers` 的 LAST。⚠ **`Result=`
  也不够**(2026-07-28 实测):07-27 那次崩掉的报告在库里是
  `Result=success` + `ExecMainStatus=1` —— `systemctl reset-failed`(或等价操作)会把
  `Result` 抹回 success 而 `ExecMainStatus` 留着,**以 `ExecMainStatus` + 时间戳为准**。

## 现役生产机 = `nk`(V2-⑰ 割接后,2026-08-04 起;冷启动先认这条)

- 🔴 **运维事实文件已拆分(2026-08-08)**:`nk`(= 宁波 `nb`)的事实在 **`~/Lino/NB_info.md`**,
  **不再在 `~/Lino/hz_info.md`**(后者只剩杭州 `hz`)。⛔ 本仓旧文里「以 `hz_info.md` 的 `nk-*` 各节为准」
  **已失效**,一律改查 `NB_info.md`。
- 🔴 **nk 上跑瞬态批算/探针,必须用 `User=neckline`+`Group=neckline` 的 systemd 瞬态 service
  (或 `sudo -u neckline`),⛔ 不许用 root 的 `systemd-run --scope`** —— 会把行情文件写成 root 属主、
  导致服务写入失败(`NB_info.md` 登记)。⚠ **本仓与归档件里凡出现 `systemd-run --scope` 的写法都是
  hz 时代的,在 nk 上照抄就是事故**;要资源隔离就 `systemd-run --unit=… --property=User=neckline
  --property=Group=neckline --property=MemoryMax=…`(2026-08-09 V2.2 批 1 + 2026-08-11 weekly 实测两次验证)。
- ⚠ **nk 是 4 vCPU / 3.8 GiB,不是「2 vCPU / 1.6G」** —— 本文多处 P0-23 叙事里的那台小箱子是 **hz 老机**;
  搬算术前先认清在说哪台。P0-23 的**方法论**(上产前隔离实测、别抬内存糊算法)仍然全部有效。
- ⚠ **`systemd-run` 的 `Memory peak` 读数不可轻信**(2026-08-09 实测报 512K,Python 起步都不止):
  cgroup 对已在页缓存里的文件页不重复计费。**要可信上界就压低 `MemoryMax` 反证**(扛住 = 真上界)。
- **Neckline 的生产机是新机 `nk`(114.66.0.38)/ `https://nk.linotsai.top`**;**hz 老机上的
  Neckline 已 stop + disable 留档**(⛔ 只停不删:五个 unit 文件 + `/opt/neckline` + `.env`/`.p8`/
  `data/` 全在)。hz 上其余四类业务照常在跑,**别把 hz 当"已退役机器"整体处置**。
- **客户端 `AppConfig.prod` = `https://nk.linotsai.top`**(⑮ 曾漏改、⑰ 补上并加了反向闸门单测)。
  ⚠ **`NK_BASE_URL_OVERRIDE` 压过默认值** —— 换包后连不上先查设置屏有没有手填过老基址。
- ⚠ **要复活老机做对照,必须先在新机 disable**:两台都排程 = 同一条报告推两遍(⑯ 双推送处置的原因)。
- `ln.linotsai.top` / `lf.linotsai.top` 的 A 记录 **2026-08-04 起 NXDOMAIN**(非本项目所致,已上报用户)。

## 新机 `nk` 公网入口(V2-⑯-G 定案,碰 nginx / 证书前必读)

- **判「配置里有没有 `ln`」必须先剥注释行**(⑰ 现场踩):`npm-custom-http.conf` 文件头**自己就写着**
  「绝不接管 ln.linotsai.top」这句护栏注释,裸 grep 每次都红。`archive/deploy_retired/preflight_a_route.sh` 已修成
  `grep -vE '^\s*#'` 后再判。**一个对自己的注释报警的闸门等于没有闸门** —— 真出现
  `server_name ln...` 那天,人只会当它又是那条老误报。
- **80/443 归用户既有的 `nginx-proxy-manager` 容器**(还反代着 `nas`/`mt`/`web` + 一个 IP 站,
  **一个都不能坏**);Neckline 只占 NPM 官方扩展位 `/opt/npm/data/nginx/custom/http.conf`
  (仓库副本 `deploy/npm-custom-http.conf`)。系统 nginx 起不来是**正常的**,别去修。
- **三条会当场炸掉那四个站的写法**:① 在 custom include 里写 `default_server`(与 NPM 的
  `conf.d/default.conf` 重复声明 → `nginx -t` 直接挂);② 建 `custom/server_proxy.conf`
  (会被注入**每一个** proxy host);③ 在这里写 `map` 等 http 级指令。
  ⛔ `archive/deploy_retired/nginx-neckline-nk.conf` 是**作废的独占式模板**,搬进 NPM 正好踩中 ①。
- **改完的铁律**:`docker exec nginx-proxy-manager nginx -t` **过了才** `nginx -s reload`;
  reload 后跑新机 `/root/npm-backup-20260804/regress.sh` 与 `regress.baseline.txt` 逐行对照。
- **证书两个目录别搞混**:宿主 `/etc/letsencrypt`(我们的 LE 证书)≠ 容器内 `/etc/letsencrypt`
  (= 宿主 `/opt/npm/letsencrypt`,NPM 自己的库)。容器读的是 hook 拷进去的
  `/data/custom-certs/nk/`,改证书路径前先想清楚在说哪一个。
- **API 路径前缀是 `/api/v1`**,裸 `/health` 返 404 是对的,⛔ 别加 rewrite 去"修"它。

## 概念板块与停牌数据(v1.4-① 定案)

- **`ths_daily.parquet` 是 `write_table_day` 铁律的唯一登记例外**:维持**扁平单文件**,
  日更走「读全表 → 整段替换当日 → **按 `concept_data.THS_DAILY_DTYPES` 声明 cast** →
  写 `.tmp` → `os.replace`」。理由三条见 `neckline/data/concept_data.py` 模块头;守门单测
  `tests/test_concept_data.py::test_all_empty_column_does_not_drift_to_string`(全空列
  dtype 不漂)+ `test_declaration_wins_over_existing_file`。**这不是笔误,别"修正"回去。**
- **`ths_daily` 不带 `ts_code` 时返回全部同花顺板块指数**(概念 N + 行业 I + 地域 R,
  ~2499 行/日),而本项目这张表历来**只含概念指数**(~394 行/日)。日更必须按当前
  `ths_index` 快照过滤,否则「强势板块」top10 语义悄悄变成「强势任意板块」+ 报告显示裸
  代码(`load_index_names` 查不到名)。周更 `ths_index` 排在日更**之前**,过滤名单才是新的。
- **`ths_daily` 当日数据当天发布不了**(2026-07-28 16:20 实测:`trade_date=当天` 返 0 行,
  前几日各 1800~2500 行;`suspend_d` 相反,当天就有)→ 16:05 日更**必然拿不到当天板块**。
  故 `ths_daily` 走**尾窗 5 个交易日重拉**自愈(次日补上前一日,整段替换、不 skip-if-exists),
  `SECTOR_DATA_STALE_MAX_LAG_DAYS=2` 的容忍度就是给这一天缓冲的;板块数据**常态落后
  1 个交易日**,不是故障。
- **`suspend_d` 在 600 元档可用**(2026-07-28 真 token 探活,同 `stk_holdertrade`,
  **不同于** `anns_d`)。日更落 `suspend_d` 分区,`data/price_stale.py` 据此把「当日无
  EOD 行」分成 `suspended`/`data_gap`/`unknown`;**名单拉不到时如实标 unknown,不许猜成
  停牌**——「时间退出判向挂起」这个豁免不能建立在臆测上。

## 🔴 定性需求不许自行定量(2026-08-09 用户当场批评立规,V2.2 返工两轮的唯一根因)

- **K8 / 任何需求给的是定性描述时**(「抛压衰减」「仍处于启动早期」「板块里的头名」),
  **把它翻成阈值 / 名次 / 百分比必须先把具体数字摆给用户拍板** —— ⛔ planner 写进 PROJECT_PLAN
  **不算已定案**,一句「授权工程侧翻译成首版」**≠** 「授权替我决定这些数」。全局同款条目已入
  `~/.claude/CLAUDE.md` 经验记录。
- **已翻译的,上线前必须实测联合通过率**:单条都合理 ≠ 连乘后还剩样本(真实代价:位置关 13 个
  子门 AND 起来,全市场 5500 只每天只剩 1~2 只、14 个历史 D0 回放**零 T1**,整档形同虚设)。
- **判据"算不出"要先问尺子对不对,别急着放宽阈值**:核心关 `leader_rs_rank ≤ 3` 覆盖率只有
  **1.4%**,真因不是阈值严,是 `leader_structure_daily` 的**簇内**口径要求「当天涨停」,而 K8 三引擎
  找的是「**还没涨、刚要动**」的票 —— **涨停是结果,要的是结果之前那一刻**。⚠ 那个 `≤3` 本身是
  H10 `audited` 的真数,错的是取数域。
- **报问题必须连出处一起报**(「这数出自 §五 ③-C、依据你的裁定 #4」),只讲机制会被当成私自加的。
- **现行处置**(裁定 #11/#12):位置关与核心关**均已退出机械闸**,机械侧只出读数、判定交 LLM
  (只降级不除名)。⛔ 别再把它们改回硬否决,也别"顺手"给它们补一条及格线。

## 复用与设计体例(v1.1 修洞定案)

- **"队列表 + 该被哪份消费"一律拆两字段**:审计时间戳 + 独立消费标记
  (`consumed_report_date IS NULL` 判据),不用一个日期字段的"相等"表达"该被
  消费"(`inquiry_pool` 永久掉缝真洞)。
- **"快照上的标"与"响应时现连的表"必须在写侧对齐**(v1.5.1 契约线 🟡-1):`/report` 的
  `llmJudgment` 从 `llm_judgments` 现连、`judgeSkipped` 却来自候选快照 —— 同日重跑时两者
  会讲相反的话。规则:标"本次没做"的同时**删掉该批码当日的既有行**(写侧收口、单事务
  幂等),**不许在读侧遮蔽**(藏真数据不是诚实)。
- **冻结件"读不出"是独立第三态,与"还没生成"必须分开**(V2 B1:`card_corrupt` **500** vs
  `card_not_ready` **404**):`INSERT OR IGNORE` 永不覆盖 → **坏了就是永久坏的**,混成一类 =
  客户端永远重试、永远显示"还没生成" = 静默永久失败。⚠ 判完整性的必需键判据一律取
  **「内容键有其一」,⛔ 不取「都要有」** —— 各消费方吃不同键子集(⑧ 只读两份 spec、⑩ 只读
  `members`),且误判代价不对称(判错 = 好数据看不到且不可自愈)。
- **`available` 标志永远不许挂在「读表成功」上**(案底 §七 **P0-39**,2026-08-05 生产实打):读得
  出表 ≠ 引擎跑过,**零行有两种相反成因**(跑了真没有 / 压根没跑),混成一句就把系统缺席讲成了
  实质性市场判断。规则:**产出物在 = 跑过的活证据**;**零产出必须另有一处段状态可查**(⑤ 的落点
  与判读唯一实现 = `selection/basket_stage_handoff.py`),**查不到 = 不知道 = 照样标未取得**。
  ⚠ 别只看段状态枚举:保险丝常返 dataclass **默认值**(⑤ 是 `no_seeds`),先看 `notes` 里的失败标记。
- **喂"类候选对象"给 LLM 审判**一律复用 `llm/judge.py::judge_candidate`
  (duck-typed,只要求几个属性;可选 `system_prompt`),不另写调用/解析/降级。
- **纪律红绿灯要"拆解展示触发了哪条"时**,不许手写 Python 重抄
  `base_universe_expr()` 已 AND 成一个布尔的表达式(数值漂移);选股域四项揉成
  一条组合文案,只有 config 可配的 P4/P5/P6 才逐项拆
  (`watchlist_check.py::_discipline_checks` 体例)。
- **同一个 `sections` dict,`.get()` 给不给默认值取决于用途,不能"统一"**
  (`intel_candidates.py` v1.4-③):判 hard_cut 排除用
  `.get(code, _DEFAULT_SECTION)`(缺 DB 行保守当 avoid_flag、不拦);算
  `yellow_card_count`(排序键③)用 `.get(code)` **不给默认**(DB 未明确登记的码——
  含不在 DB 的合成码如 A3b——一律不计入黄牌数)。两行代码长得像,语义故意相反,
  勿"修正"成同一种写法。

## 测试隔离(v1.4-④ 定案)

- **`isolated_env`/`api_env` 只重写 `market_data`/`trading_calendar`/`tushare_client`
  三处 `settings` 绑定**,**不含 `neckline.db`**(`brain.py`/`positions.py`/
  `watchlist.py`/`news_alerts_store.py`/`report/store.py` 等经 `neckline.db.connection`
  访问 SQLite 的模块,用的是 `neckline/db.py` 自己另一份未被夹具重写的 `settings`)。
  调用这些模块的函数时**必须显式传 `db_path=env.db_path`**,`db_path=None` 兜底不
  安全——会静默读到真实项目 `data/neckline.db`(`info_card.describe_hits` 一测就踩过,
  查回真实生产 K4 分区,断言全错但不报错、极具迷惑性)。
- **定位这类泄漏别靠肉眼 grep**(2026-08-04 A8 定案):`DB_PATH=<scratch>` 重定向 +
  一个临时 pytest 插件里 patch `sqlite3.connect`、命中 `settings.db_path` 就记
  **nodeid + 栈**,跑一次全量套件即得全部命中点(探针不入仓)。⚠ **「MD5 没变」不等于
  没泄漏**:已迁移完的库上那些写是幂等 no-op,泄漏照样在,换台新机就会真写。
- 🔴 **⛔ 别拿含墙钟审计戳的产物做整体相等比较**(已现身 4 次,全文 §七 P1-36)。戳是秒精度
  `datetime.now()` → 两次独立调用跨过整秒即合法不同 → **失败率 ≈ 两次调用间隔 ÷ 1 秒** →
  **孤立跑恒绿、全量跑间歇红**,每次复查都被「重跑一遍绿了」骗过去。⚠ **别只按"列名"找**:
  `generated_at` 是**印在 markdown 报告头第一行**的,不是表列。**判据**:凡断言"多次/多路产出逐位
  相同",先问这份产出里有没有随调用时刻变化的字段,有就先归一化(`markdown_modulo_generated_at()`
  / `.drop("computed_at")`;业务列仍要逐位相同,**不是放宽**)。⛔ **归一化只是把概率降回去,不算修完**
  —— 必须再加一条**把跨秒钉成必然**的用例(钉死墙钟 + 断言"裸比必定不等",防归一化退化成空操作)。
  ⚠ **反向同查**:`len(set(三次产出)) == 3` 这类"必须互不相同"的断言,戳会让它**恒成立 = 假绿**。

## AST 守门的两个通用失明面(V2.3.3 复审实打,新写守门前先读)

- 🔴 **按 AST 取 SQL 字面量的守门,对「先赋给变量再 execute」完全失明**:
  `sql = "UPDATE x SET " + …; conn.execute(sql)` 与 `stmt = "…"; cur.executemany(stmt, rows)`
  两种写法**一条都逮不到**,而守门照样全绿。两条一起上才补得住:① 对**整份文件文本**
  (剥掉 docstring 与 `#` 注释后)正则数一遍,**文本里有、AST 里没有 = 失明 → 报红**;
  ② 对该表的读写唯一通道那个模块,**任何非字面量 `execute()` 实参直接红**(源头最严)。
  体例 `tests/test_v233_auction_guards.py`。⚠ 立完守门要**逐个注入逃逸写法实测**(探针不入仓)。
- **守门的扫描域别只写 `neckline/**`**:`scripts/`(尤其 `scripts/oneoff/` 的"修数据脚本")
  是同一类风险的常见落点;**禁 UPDATE 的表往往也该禁 `DELETE`** —— 两条分开写,别以为
  「机械列白名单」顺带管住了删行。

## 三态字段:`judge_*()` 的 `None` 常常承载多种语义,`is not None` 会把它们折平

- 🔴 **凡「判不出」要与「判了没事」分开的字段,前置条件必须判在调用方**(V2.3.3 复审 🔴-1 实打):
  `sentinel/precall.py` 的两个 judge 在「真没命中 / 卡上没这个价位 / `open<=0`」**三种**情况
  下都返回 `None`,写成 `judge_x(...) is not None` 就把后两种讲成了「核对过了、没事」——
  **编译不报错、单测不红、界面看不出**。规则:`None` 只许承载一种含义;**每个 `None`
  必配一个可查的原因码**(单一源枚举),并进「异常与风险」+ 进喂 LLM 的摘要 + 客户端画**第三态**。
- ⚠ **prompt 里那条「标了『没判』的项照实当作未知」只有在摘要里真的写了『没判』时才生效** ——
  光在契约里留个 `null` 而摘要不写,等于那条纪律永远不执行。

## 决策日志「必填但可 null」+ 报告时点查表防前视(v1.4-⑤ 定案)

- **pydantic v2 区分「键缺失」vs「显式 null」用 `body.model_fields_set`**,不要用
  `body.field is None` 判——两者在默认值机制下无法区分。落地见
  `decision_log.max_chase_pct`:`api/app.py::_extract_max_chase_pct_or_400` 查
  `"maxChasePct" not in body.model_fields_set` 才 400,显式传 `null` 放行。
- **报告生成期(`pipeline.py::build_report`)里查任何非 EOD 面板的表**(如
  `decision_log`)**都要按 `trade_date` 截断**(`list_decisions(date_to=...)`),
  否则历史回放会读到该历史日之后才写入的数据,是真前视偏差不是展示层细节——
  `exec_hint._latest_decision` 是反例修复过的正确姿势,新增类似"查某表找关联记录"
  的 attach 函数照此办理。

## 时间轴与章程判定(v1.4-⑥ 定案,碰纪律判定前必读)

- **市场时刻的时区/收盘唯一源 = `neckline.calendar` 的 `CN_TZ`(UTC+8 固定,A 股无夏令时)
  + `MARKET_CLOSE_TIME`(15:00,引用 `trading_calendar._PM` 的收盘边界)**;别在新模块里
  再写一份 `timezone(timedelta(hours=8))` 或 `time(15,0)`。
- **`strategy_versions.activated_at` 是 UTC 戳**(唯一写入者 `brain._now()`),**交割单成交
  时刻是北京时间** —— 逐笔判章程前必须归一到同一时间轴。两个 naive 的约定**刻意相反**:
  `activated_at` 无时区 → 按 **UTC** 读(与唯一写入者同口径);调用方传入的 naive 时刻 →
  按**北京时间**读(市场时刻口径)。**别"统一"它们**,各自 docstring 已定死。
- **边界语义定死**:`config_governing_at` 判据是「激活时刻 **≤** 成交时刻」= **恰好等于激活
  时刻的成交算新章程**;交割单只有日期没有时刻时按**该日收盘 15:00** 取章程(代价:激活
  当日、激活时刻之前的成交会偏判新章程 —— 真实 v1.3.3 激活在北京 14:36 **盘中**,plan 原文
  "激活都在盘后跑"的前提并不总成立,已在 `reconcile.trade_instant` docstring 写明)。
- **周复盘每条判据锚在"它审计的那笔成交"**:单笔上限/禁买锚**买入**、止损锚**卖出**(与
  哨兵每拍读现役 `stop_pct` 同源)、并发/敞口按**日**归属(每天比该天自己的上限)、冷却锚
  **再次买入**。`WeeklyReview.strategy_version` 只是**周初标签**,切换周别拿它当"整周口径"。
- **要跨进程/跨天复现的分组一律用 `zlib.crc32`,不用内置 `hash()`**(带进程盐,
  `PYTHONHASHSEED` 一变分组就漂,历史报告不可复现);轮转靠**纯日期函数**
  (`toordinal()` 奇偶)而不是库里的计数器(计数器会被"重跑一次报告"推进一格)。

## LLM Provider 自填制(V2-②定案,碰 `neckline/llm/` 前必读)

- **生产现役 = 单 Provider(GLM 一家兼检索与推理;2026-08-05 用户拍板为常态)**:推理类任务靠
  「路由未命中 → 回退 `llm_default_provider`」走通,**这不是没配好**;双 Agent 分工是可选扩展路径
  (加第二家 + 设路由即恢复,不改代码)。⚠ `llm_providers.base_url` 列语义 = **完整端点**(必须带
  `/chat/completions`,少写 = 拿真 key 打出 404),`search_engine` **留空是对的**(空 = 不发该字段)。
- **`GLMProvider`/`KimiProvider` 降级为预置参考实现,不删、行为逐字节不变**:
  `llm/factory.py::get_provider(task, ...)` 永远构造裸 `OpenAICompatProvider`
  (按 `llm_providers` 行的 base_url/model/has_web_search/search_engine 建),
  不再 import 这两个具体类——新代码别指望 `get_provider()` 返回它们的实例,它们
  只服务于既有单测"要一个真实可用 provider 测试替身"这一用途。
- **通用搜索钩子协议 = 照抄 GLM 的 `web_search` 形状**(项目唯一有文档验证过的
  联网搜索协议),`has_web_search=0` 时 `OpenAICompatProvider._search_tools`
  直接返回 `None`、不发 `tools`/`search_query`。Kimi 的工具调用回合协议**不可
  泛化**——自填一个 Kimi 式端点却勾 `has_web_search=1` 会发错协议,这是登记过的
  已知代价、不是 bug,别去"修"。
- **"两段式流水"(检索 Agent 出证据 → 喂推理 Agent)编排逻辑不住在 `llm/` 包里**:
  `evidence_status`(`ok|search_unavailable|partial`)是 `baskets` 表的列,单侧
  故障的诚实披露只能在产生篮子的那一层(V2-⑤ 驱动聚合层)写;`llm/router.py`/
  `llm/budget.py` 只提供路由与预算原语,别在这两个文件里找编排代码。
- **每条 `provider.chat(...)` 链路都必须 import `prompt_context`**(时效纪律入 system
  prompt + `date_anchor_line()` 放 user 首行 + 联网链路显式传 `search_query`,照
  `judge.py` 姿势,⛔ 不另起一套)。全仓守门在 `tests/test_llm_router_budget.py`
  (AST 扫调用点 + 豁免名单反向校验,名单现为空);`news_scan.py` 那笔欠账已于
  2026-08-04 销账。

## 双会话架构(2026-07-25 起,冷启动必读)

- **本项目双权威文件、双会话分工**:系统线(APP 建设办公室,v 字头版本)权威 =
  `PROJECT_PLAN.md`;策略线(策略研究中心,K 字头版本)权威 = `STRATEGY_LAB.md`。
  **接活先分清是哪条线,拿对图纸**:改产品/客户端/部署/哨兵/报告管线 → PROJECT_PLAN;
  策略假设/回测研究/K 版本 → STRATEGY_LAB(其「雷区地图」节记录三场战役全部判决,
  任何新策略讨论前必读,防止重走死路)。
- 跨线协作:纪律章程唯一源在 PROJECT_PLAN §2.1(策略线只引用);策略过门+用户批准后,
  激活/部署归系统线;`research/*.md` 不可变档案归策略线;本坑清单两线共用。
- **V2 文档层级(防第三份权威;2026-08-05 V2 收官后的常态口径)**:**现行口径权威只有
  `PROJECT_PLAN.md`** —— V2 施工图全文已归档 `archive/施工图/V2.0.0_施工图_20260805归档.md`(**查施工
  细节去那儿,但它不追改后续裁定**,已知两处:单 LLM 常态、⑫ 周度 unit → §七 P3-42);根目录
  `新版本量化交易APP与选股架构.md` = 产品语义蓝图(可读、不可当施工口径),`archive/V2架构设计稿_*.md`
  **作废不得引用**。**选股策略包**(`selection_packs`)与**纪律章程**(`strategy_versions`)是两条版本线、两张表、两套激活流程,**永不混用**。
  ⚠ **V2.1 同款(2026-08-07 立项)**:`archive/交接与日志/V2.1前瞻规划_20260807立项归档.md` 已转写进 PROJECT_PLAN
  §五 V2.1.0,**归档件不追改后续裁定、⛔ 不得当施工口径**(文首已列四处被纠正的出入)。
  ⚠ **V2.1 施工图自 2026-08-09 起也归档**(`archive/施工图/V2.1.0_施工图_20260809归档.md`):**批 1 六块已上产
  (`v2.1.0`)= V2.2 地基;批 2〔`K7-pack-v2` 发版激活〕⛔ 作废永不执行;批 3〔周度 unit〕并入 V2.2 第 ④ 块**。
- **三条版本线(2026-08-09 K8 入仓起,冷启动必读,写号前先分清是哪条)**:① **系统线 `v` 字头**
  (**仓库 `v2.3.3` / 生产仍 `v2.3.2`** —— V2.3.3 完工未部署,权威 `PROJECT_PLAN.md`)
  ② **纪律章程**(`strategy_versions` 表行,现役 **`v2.3-k8`**,权威 §2.1,切换器 `activate_charter.py`)
  ③ **K8 选股线**(权威需求 = **`~/Lino/whynotme/K8.md`(现 V0.7)**,实现口径
  `PROJECT_PLAN.md` §五 当前版本节)—— 它自己又分**骨架**与**引擎 `C1`/`Z1`/`Y1` 各自独立**。
  🔴 **骨架线现在有"两个值"且刻意不同**:**包文件已是 `K8-V0.7`**、**DB 现役仍是 `K8-V0.6`**
  (V2.3.3 只出文件不激活,激活归部署环节)—— 查现役一律以 `selection_packs` 为准,⛔ 别读文件。
  ⛔ **`V0.x` 禁简写**(与满篇 `v` 字头只差一个大小写);⛔ 引擎升级写 `C2`/`Z2`/`Y2`,**不写「K8 v2」**。
  ⚠ **还有第四、第五个"版本号"、但它们不是版本线**:冻结卡形状版本 `CARD_SPEC_VERSION`
  (现 **`basket_card_v4`**,V2.3.3 随卡 #6 换问题 bump)与选股时钟机械段形状版本
  `CLOCK_MECH_SPEC_VERSION`(现 **`selection_clock_mech_v2`**,V2.3.3 随第十项 bump)
  —— **一旦上产,再改形状必须 bump**(`basket_card.py:91-105` / `selection_clock.py` 模块头)。
  ⚠ 本仓 `K8_STRATEGY_ARCH.md` 是 **V0.5 旧快照、⛔ 不得当口径**(§七 P4-60),它缺四样:
  §十七 的 30/80 与 100 篮门槛 · OUT 三态与研究影子对照 · §十九 退出字段语义 · **§二十 竞价确认层**。
  ⚠ **K8 由用户直接转交系统线,没走策略线立项流程 → 它的假设零回测背书**,位置关还正撞在 K3
  已判死的域上(PROJECT_PLAN §七 **P3-49**,施工前必读;用户 2026-08-09 裁定「**信实盘别信回测**」
  = 判据来源写死为选股时钟实盘数据,⛔ 不为它立回测战役)。原「`STRATEGY_LAB.md` §四 仍停在 K7」
  那条账已随策略档案整体迁出**销案**(P3-53)。
