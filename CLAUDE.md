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
