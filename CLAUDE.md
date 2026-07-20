# Neckline — 项目专属规范与坑(builder/reviewer 必读)

> 全局规范见 `~/.claude/CLAUDE.md`;权威施工件 `PROJECT_PLAN.md`(唯一权威,产品
> 决策/参数/验收标准/变更日志全在那)。本文件只记 Neckline **专属的工程坑**,
> 不复述 plan 内容。数据源坑的权威原文在 `/Users/linotsai/Lino/LinoN/CLAUDE.md`
> (前作,§3.7 明确要求吸收对应节)。

## 跑法

- 虚拟环境 `.venv`(Python 3.11+);`source .venv/bin/activate`。装库走阿里云镜像
  (`pip.conf` 已配)。测试:`python -m pytest tests/ -q`。
- 报告:`python scripts/report.py [YYYYMMDD]`。哨兵:`python scripts/sentinel.py
  [--once]`。持仓台账:`python scripts/positions.py {add,close,list}`。合成盘中
  冒烟(无法活体验证时用):`python scripts/smoke_sentinel.py`。

## 钉死的领域常量(单一事实源,禁止各处漂移——改动前先找源头,别在新文件里抄一份)

- **-5% 止损 / 回落止盈 / 仓位纪律**:唯一源是 SQLite `strategy_versions` 表现役
  版本(`neckline.strategy.brain.get_active().rule["config"]`),不是某个模块里
  的字面量。哨兵(`sentinel/engine.py`)读的也是这张表,不硬编 0.05。
- **涨跌停幅度规则**(10%/20%/5%/30%、制度分界日、新股豁免窗口):唯一源是
  `neckline/data/limit_derived.py` 顶部常量;向量化 EOD 批算是
  `compute_limit_derived`,盘中逐票标量镜像是 `compute_intraday_limit_prices`/
  `resolve_limit_pct`/`resolve_exempt_days`(阶段3新增,同一组常量,单测互相
  对拍)。**新需求要算涨跌停价,去这个模块找函数,不要重新推一遍幅度规则。**
- **板块分类**(主板/创业板/科创板/北交所):唯一源 `neckline/data/board.py` 的
  `classify`/`classify_by_code`(黑名单坑教训:按板块整段正则,禁止枚举精确
  子段,见 LinoN CLAUDE.md)。`sentinel/quotes.py:to_symbol` 的北交所前缀判断
  复用的正是这个模块,没有另起一份 8/4/920 正则。

## 阶段3(盘中哨兵)踩过的坑,下次别再踩

- **polars `df["col"]` 在链式 `df = df.filter(...).with_columns(...)` 里会绑定
  错对象**:`with_columns(...)` 的参数如果用 `df["col"]`(Series 索引,不是
  `pl.col("col")` 表达式)去引用外层变量 `df`,Python 会先算好右边所有子表达式
  再赋值——此时 `df` 还是**赋值前**(过滤前)的那个 DataFrame,行数对不上过滤后
  的目标,会报 `ShapeError`。教训:任何 `df = df.filter(X).with_columns(Y)` 的
  链式写法,`Y` 里一律用 `pl.col(...)` 表达式,不要用 `df["..."]`(`scripts/
  smoke_sentinel.py` 施工期真实踩过)。
- **`neckline.calendar.trading_days_between`/`is_trading_day` 对早于 trade_cal DB
  覆盖范围(默认 2015-01-01 起,见 `scripts/init_calendar.py`)的查询会退化成
  逐自然日循环 + 每天一条 warning 日志**:A 股大量主板老股 `list_date` 在
  1990s-2000s,若不加预筛直接对每只票调用
  `trading_days_between(list_date, trade_date)` 算"上市第几天",会在生产环境
  里对着几十年跨度刷屏 + 显著拖慢(施工期用真实历史数据跑 `smoke_sentinel.py`
  才暴露,单测因为用的合成短日期区间测不出来)。**已在
  `sentinel.universe.is_new_stock_exempt` 加了「自然日差 > 30 天直接判非豁免」
  的廉价预筛**——任何新代码要判断"是不是新股"都复用这个函数,不要重新写一个
  不带预筛的版本。
- **阈值比较的浮点精度**:`stop_pct - buffer_pct` 这类由两个配置值做减法算出的
  阈值,在二进制浮点下可能不精确等于十进制直觉值(如 `0.08-0.02` 算出
  `0.059999999999999996` 而非 `0.06`),导致"回撤恰好等于阈值"的边界情形被
  漏判。`sentinel/holding.py` 已加 `_EPS=1e-9` 容差,任何新的纪律阈值比较
  (止损/止盈/仓位占比等)照此办理,不要写裸的 `>=`/`<=`。
- **Bark(`sentinel/channels.py`)与 GLM/Kimi(阶段2)同类处境**:payload 字段
  (`title`/`body`/`group`/`level`/`sound`)基于官方文档实现,**无真实
  `BARK_URL` 做过活体验证**。拿到真实 key 后建议先手动跑一次
  `BarkChannel(url).send(...)`(不经 MockTransport)确认协议假设仍成立。
- **盘中哨兵的"关注池"是候选+持仓+昨日涨停股的代理样本,不是全市场**:退潮
  哨兵(`sentinel/retreat.py`)不轮询全市场 ~5900 只股票(免费源限流/封禁风险,
  见该模块头注释)。如果实盘发现这个代理样本对"退潮"不够灵敏,下一步候选方案
  是"低频率(如5分钟一次)全市场轮询"叠加,而不是把主循环频率整体提到危险
  区间——这个取舍已写进模块 docstring,改动前先看那段说明。

## 阶段4C(SwiftUI 双端客户端)踩过的坑,4D/4E 续做前先看

- **`CandidateOut.board` 服务端字面是英文枚举码**(`MAIN`/`GEM`/`STAR`/`BSE`,
  唯一源 `neckline/data/board.py` 的 `Board` 枚举),不是"主板"这类中文名——
  联调实测才发现,别凭直觉当中文串直接展示。客户端 `Models.swift` 已加
  `Candidate.boardLabel` 做**纯展示层**四常量换算(未识别值原样透传),不要在
  服务端另建一份中文映射、也不要客户端重新推导分类逻辑。
- **Swift `URLProtocol` 网络桩测试(等价于 Python `httpx.MockTransport`),
  `startLoading()` 里 `request.httpBody` 常是 nil**:URLSession 经自定义
  URLProtocol 转发 POST/PUT 时会把 body 转成 `httpBodyStream`,断言请求体内容
  必须两路都读(`httpBody` ?? 手动 drain `httpBodyStream`),否则会误判"请求体
  丢了"(`NecklineTests/DTODecodeTests.swift` 的 `httpBodyOrStream()` 已处理,
  新增校验请求体的测试直接复用这个 helper)。
- **本地起 dev 后端做真实联调,不要从零 bootstrap**(会卡在无 `trade_cal`/无
  现役策略版本,报"策略大脑无现役版本"):`sqlite3 ATTACH` 真实
  `data/neckline.db`,只拷 `trade_cal`/`strategy_versions`/`stock_basic`/
  `namechange` 四张**只读参考表**进隔离临时库,再跑 `scripts/report.py <date>`
  就能吃真实六年 backfill 数据出一份真报告——不碰用户真实台账(`positions`/
  `app_settings`/`devices` 等业务表留空隔离),也不必重新跑一遍日历回填或
  `research.rule_v1 --commit`。
- **本环境 computer-use 对 Simulator/macOS App 的点击权限可能被拒**
  (`request_access` 返回 `denied`,与 LinoN CLAUDE.md 记的"Dock 守卫誤判"是
  另一种表现形式,结果一样打不通)。视觉核对改走**非交互路径**:
  `xcrun simctl io <device> screenshot` 直接截图(不需要点击权限);要切
  Tab/板块用 `SIMCTL_CHILD_<VAR>=<val> xcrun simctl launch <device> <bundle-id>`
  (iOS,simctl 会把 `SIMCTL_CHILD_` 前缀剥掉传进 App 进程)/macOS 直接跑
  `<App>.app/Contents/MacOS/<App>` 二进制时在同一 shell 设同名环境变量——
  Neckline App 侧已加 `NECKLINE_INITIAL_TAB` 这个纯 QA 钩子(`NecklineApp.init()`
  读 `ProcessInfo.processInfo.environment` 设初始 tab,不影响正常用户路径)。
  macOS 原生 GUI 截图(`screencapture`/`osascript` System Events)在本环境因
  沙盒权限(Screen Recording/Accessibility 未授权)不可用,遇到同样情况直接
  改用"iOS 截图 + 双端 `xcodebuild` BUILD SUCCEEDED"作等价证据,不必死磕。

## 阶段4D(周复盘对账引擎)踩过的坑,4E 续做前先看

- **可配字段映射(`review_col_map`)必须同时驱动"格式判定"本身,不能只驱动"列
  取值"**:`neckline/review/parse.py::_detect_format` 最初只按内置默认列名
  (`证券代码`/`成交价格`/`证券名称`)猜格式,若用户 col_map 把这几个判据列
  也改了名,连"这是哪种格式"都认不出来,col_map 形同虚设——判据列名同样要吃
  `col_map` 覆盖(表头行探测锚点"交易日期"除外,那个固定不受 col_map 影响)。
- **反推价格用到的必需列,压根找不到该列时绝不能静默按 0 兜底**:格式一
  `成交价 = (|发生金额|-费用)/数量`,若"费用"对应列因表头措辞不同(如"费用
  合计")而找不到,`cell(None) or 0.0` 会悄悄把它当 0,算出一个看似合理实则
  错误的价格(施工期实测:`150015/100=1500.15` 而非正确的 `1500.0`)。任何
  "缺列 vs 缺该行这一格数据"要分开处理:前者(`cols[...] is None`)必须
  硬性跳过该行 + 警告,后者(列存在但这一行是 None)才允许当 0 处理。
- **"总仓"(§1.2 固定 12-13 万分母)此前项目里没有单一归属地**——`neckline/
  config/__init__.py` 的 `Settings.total_capital`(默认 12 万,`.env` 的
  `TOTAL_CAPITAL` 可覆盖)是唯一源,新代码要用到"占总仓百分比"一类计算(敞口
  占比、强制复盘阈值等)一律读这个字段,不要各自另开一个字面量。
