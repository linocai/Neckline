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
  双端 `xcodebuild` BUILD SUCCEEDED"当等价证据,不死磕。**页面内容纵向溢出 iPhone 视口
  且需要看到滚动才可见的部分**(如候选卡多行 chips、长表单末尾字段):临时
  `xcrun simctl create` 一台 iPad(`TARGETED_DEVICE_FAMILY` 含 "2" 时是原生 iPad
  布局、非缩放兼容模式),同样点尺寸下可见内容显著更多,比试图裁剪测试数据凑
  首屏更可靠(v1.4-⑧/v1.5-⑤ 验证过;**用完 `simctl delete` 收掉临时设备**,
  别留常驻);仍不够时才认截图缺口、靠单测兜底,不死磕。
- **本地零 LLM key 时,LLM 参考件/K4 派发警示截图靠写库伪造,不必等真 key**:候选
  `llmJudgment` 走独立 `llm_judgments` 表联查(**不在** `candidates_json` 内,塞
  `llm_judgment` 键无效,改用 `report_store.save_llm_judgment(...)` 插行);
  `reference_plan`/`dispatch_alerts` 才在 `candidates_json`/`watchlist_json`
  内、且已是 camelCase(`to_public_dict()` 原样存档)。跑一次 `scripts/report.py`
  生成真报告后直接 `UPDATE reports SET candidates_json=…` 改 2~3 只候选即可拼出
  ok/vetoed/judgeSkipped 三态样例(v1.5-⑤ 验证过)。
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
- **生产机性能探针纪律**(2026-07-29 立规,2 vCPU/1.6G 箱经不起折腾):探针/压测**只在收盘后
  15:00 之后跑,且避开 16:00–17:00**(16:05 日更 + 16:35 报告窗口);一律
  `systemd-run --scope -p MemoryMax=… -p CPUQuota=…` 隔离单进程、**串行**,别并行开多个、
  更别拿常驻 `neckline.service` 当小白鼠;跑完 `pgrep -af` 确认无残留 + `reset-failed` + 看 load 回落。
  **判据:`load > 4` 立即停手**(07-29 探针在**交易时段**把 load 推到 65)。
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
- **timer 跑过 ≠ 任务成功**:部署/定时任务验收必须看 `ExecMainStatus=0` **且**
  `ExecMainStartTimestamp` 是本次那一跑,别只看 `list-timers` 的 LAST。⚠ **`Result=`
  也不够**(2026-07-28 实测):07-27 那次崩掉的报告在库里是
  `Result=success` + `ExecMainStatus=1` —— `systemctl reset-failed`(或等价操作)会把
  `Result` 抹回 success 而 `ExecMainStatus` 留着,**以 `ExecMainStatus` + 时间戳为准**。

## 现役生产机 = `nk`(V2-⑰ 割接后,2026-08-04 起;冷启动先认这条)

- **Neckline 的生产机是新机 `nk`(114.66.0.38)/ `https://nk.linotsai.top`**;**hz 老机上的
  Neckline 已 stop + disable 留档**(⛔ 只停不删:五个 unit 文件 + `/opt/neckline` + `.env`/`.p8`/
  `data/` 全在)。hz 上其余四类业务照常在跑,**别把 hz 当"已退役机器"整体处置**。
- **客户端 `AppConfig.prod` = `https://nk.linotsai.top`**(⑮ 曾漏改、⑰ 补上并加了反向闸门单测)。
  ⚠ **`NK_BASE_URL_OVERRIDE` 压过默认值** —— 换包后连不上先查设置屏有没有手填过老基址。
- ⚠ **要复活老机做对照,必须先在新机 disable**:两台都排程 = 同一条报告推两遍(⑯ 双推送处置的原因)。
- `ln.linotsai.top` / `lf.linotsai.top` 的 A 记录 **2026-08-04 起 NXDOMAIN**(非本项目所致,已上报用户)。

## 新机 `nk` 公网入口(V2-⑯-G 定案,碰 nginx / 证书前必读)

- **判「配置里有没有 `ln`」必须先剥注释行**(⑰ 现场踩):`npm-custom-http.conf` 文件头**自己就写着**
  「绝不接管 ln.linotsai.top」这句护栏注释,裸 grep 每次都红。`deploy/preflight_a_route.sh` 已修成
  `grep -vE '^\s*#'` 后再判。**一个对自己的注释报警的闸门等于没有闸门** —— 真出现
  `server_name ln...` 那天,人只会当它又是那条老误报。
- **80/443 归用户既有的 `nginx-proxy-manager` 容器**(还反代着 `nas`/`mt`/`web` + 一个 IP 站,
  **一个都不能坏**);Neckline 只占 NPM 官方扩展位 `/opt/npm/data/nginx/custom/http.conf`
  (仓库副本 `deploy/npm-custom-http.conf`)。系统 nginx 起不来是**正常的**,别去修。
- **三条会当场炸掉那四个站的写法**:① 在 custom include 里写 `default_server`(与 NPM 的
  `conf.d/default.conf` 重复声明 → `nginx -t` 直接挂);② 建 `custom/server_proxy.conf`
  (会被注入**每一个** proxy host);③ 在这里写 `map` 等 http 级指令。
  ⛔ `deploy/nginx-neckline-nk.conf` 是**作废的独占式模板**,搬进 NPM 正好踩中 ①。
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
  `PROJECT_PLAN.md`** —— V2 施工图全文已归档 `archive/V2.0.0_施工图_20260805归档.md`(**查施工
  细节去那儿,但它不追改后续裁定**,已知两处:单 LLM 常态、⑫ 周度 unit → §七 P3-42);根目录
  `新版本量化交易APP与选股架构.md` = 产品语义蓝图(可读、不可当施工口径),`archive/V2架构设计稿_*.md`
  **作废不得引用**。**选股策略包**(`selection_packs`)与**纪律章程**(`strategy_versions`)是两条版本线、两张表、两套激活流程,**永不混用**。
