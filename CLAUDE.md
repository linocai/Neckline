# Neckline — 项目专属规范与坑(builder/reviewer 必读)

> 全局规范见 `~/.claude/CLAUDE.md`;系统线权威 `PROJECT_PLAN.md`、策略线权威
> `STRATEGY_LAB.md`(产品决策/参数/验收标准/变更日志全在那两份)。本文件只记
> Neckline **专属的工程坑**:每坑只留判据 + 规则 + 指针,事故叙事全文在
> `archive/`(总索引:`archive/变更日志_详版_20260719-20260728.md`)。数据源坑的
> 权威原文在 `/Users/linotsai/Lino/LinoN/CLAUDE.md`(前作)。

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
- **盘中"关注池"是候选+持仓+昨日涨停的代理样本,不是全市场**(免费源限流取舍)。
  不够灵敏的备选是低频全市场轮询叠加,不是提主循环频率——取舍见
  `sentinel/retreat.py` docstring,改前先读。

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
- **本环境 computer-use 点不动 Simulator/macOS App**(权限拒):视觉核对走
  `xcrun simctl io <device> screenshot`(免点击权限)+ QA 钩子
  `NECKLINE_INITIAL_TAB` 切初始 tab(iOS 用 `SIMCTL_CHILD_` 前缀传入;macOS 直接
  跑二进制带环境变量);macOS 原生截图被沙盒挡时,拿"iOS 截图 + 双端
  `xcodebuild` BUILD SUCCEEDED"当等价证据,不死磕。
- **客户端 404 映射**:`APIClient.mapReason` 按 reason 逐 case + fallback;新增会
  返 404 的端点必须检查要不要加新 case,别指望 fallback 猜对文案(watchlist
  `not_found` 被误显示成"持仓已清"踩过)。
- **服务端字段与客户端既有计算属性撞名**(如 `distToStopPct` 小数 vs 百分比):
  CodingKeys 显式改名解码(`distToStopPctServer`),不改旧属性既有语义(有单测锁)。

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
- **带联网搜索的 LLM 调用不能用短读超时**:现值 `openai_compat.read_timeout=90`
  (带搜索正常生成 30-60s+;25s 下 10 只审判 5 只 ReadTimeout);卡死场景由
  max_attempts 全新连接重试兜住,勿因个别慢调用回调短超时。
- **GLM 联网搜索 0 命中(已结案,v1.3.4 真 key 实证)**,两个真因:①
  `search_engine` 取值不被上游认识会 `ok=True` **静默返 0 条**(模型退训练数据
  作答,文字看不出);② 检索词跟**最后一条 user 消息**走,代词提问救不回来。
  修法已上线:`provider.chat()` 可选 `search_query`(不传时 payload 逐字节不变,
  单测锁死)+ 问询台补中文名(`resolve_stock_names` 是查名唯一实现)显式传检索词
  + 0 命中 WARNING 埋点。⚠ **已证伪勿再查**:payload 里 bool/int 发成字符串不是
  bug(GLM 正确解析,刻意保留原样)。
- **timer 跑过 ≠ 任务成功**:部署/定时任务验收必须看 `ExecMainStatus=0` **且**
  `ExecMainStartTimestamp` 是本次那一跑,别只看 `list-timers` 的 LAST。⚠ **`Result=`
  也不够**(2026-07-28 实测):07-27 那次崩掉的报告在库里是
  `Result=success` + `ExecMainStatus=1` —— `systemctl reset-failed`(或等价操作)会把
  `Result` 抹回 success 而 `ExecMainStatus` 留着,**以 `ExecMainStatus` + 时间戳为准**。

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

## 复用与设计体例(v1.1 修洞定案)

- **"队列表 + 该被哪份消费"一律拆两字段**:审计时间戳 + 独立消费标记
  (`consumed_report_date IS NULL` 判据),不用一个日期字段的"相等"表达"该被
  消费"(`inquiry_pool` 永久掉缝真洞)。
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

## 双会话架构(2026-07-25 起,冷启动必读)

- **本项目双权威文件、双会话分工**:系统线(APP 建设办公室,v 字头版本)权威 =
  `PROJECT_PLAN.md`;策略线(策略研究中心,K 字头版本)权威 = `STRATEGY_LAB.md`。
  **接活先分清是哪条线,拿对图纸**:改产品/客户端/部署/哨兵/报告管线 → PROJECT_PLAN;
  策略假设/回测研究/K 版本 → STRATEGY_LAB(其「雷区地图」节记录三场战役全部判决,
  任何新策略讨论前必读,防止重走死路)。
- 跨线协作:纪律章程唯一源在 PROJECT_PLAN §2.1(策略线只引用);策略过门+用户批准后,
  激活/部署归系统线;`research/*.md` 不可变档案归策略线;本坑清单两线共用。
