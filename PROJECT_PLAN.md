# Neckline — A 股短线量化交易系统 · PROJECT_PLAN

> 本文件是本项目唯一权威施工件。全局规范见 `~/.claude/CLAUDE.md`。
> **铁律:任何 token / key 绝不写进任何被 git 跟踪的文件,一律从 `.env` 读。**
> 项目名 Neckline(颈线)= 技术形态的分水岭,寓意「过线才动手」的纪律内核。

---

## 一、概述

### 1.1 项目定位

Neckline 是一套**只审计、不代下单**的 A 股短线量化决策系统。它把用户的短线交易从「拍脑袋 + 情绪驱动」重构为「回测验证过的规则 + 盘后计划 + 盘中执行哨兵 + 事后违纪对账」。核心信条:**研究先于产品,规则先于直觉,系统给纪律与证据、下单与止损由人和券商执行。**

系统三态同码(同一套信号代码):喂历史 = 回测,喂今日 = 盘后报告,喂单票 = 问询台。回测引擎是常驻地基,不是一次性脚本。

### 1.2 用户画像与约束(全部写死为设计前提)

- **资金**:总资金约 12–13 万,**不再入金,固定分母**(历史越亏越入金是死因之一,新系统按固定盘子做风控)。
- **时间**:本职工作忙,盘中约每 20–30 分钟看一次手机;交易制度 T+1。
- **执行分工**:止损已改用**券商条件单**执行(在系统之外),每笔开仓必挂 -5% 止损。系统只做盘后计划、盘中提醒、盘后对账,**永不自动下单**。
- **实盘节奏**:2026-05 开始实盘,阶段 0–1(数据层 + 策略研究)期间用户继续实盘,按下方「纪律章程」**手动**执行,系统尚不介入。

### 1.3 交割单归因(立项依据,核心发现)

对 2026-05-12 → 07-17 全量交割单做了归因(源表 `/Users/linotsai/Downloads/2026年股票交割单整理.xlsx`;归因脚本在会话 scratchpad,仅参考,**不入仓库**):

- 账户级:净投入 15.28 万,已实现亏损约 2.51 万(**-16.4%**,约 10 周)。
- 闭合回合 37 笔,**胜率 40.5%**、**盈利因子 0.47**(总盈 / 总亏),典型「小赚扛大亏」。
- **13 笔破 -5% 未止损**多亏约 1.38 万 → **占已实现亏损 85%**。→ 结论:止损纪律是第一死因,故 -5% 条件单强制化。
- 买「绿盘大阴线 / 下跌途中票」是主要死因;**追强势票反而 67% 胜率** → 母战法定为强势股短线。
- **7 笔买入次日跌停**,集中在高弹性题材 / 次新票型 → 该类票型入黑名单或强制减仓。
- **持有 4–7 自然日**是唯一打平的持有桶 → 时间退出思想(D4)有据。
- 开仓日上证在 **MA20 下方 28 笔合计 -1.95 万、上方 9 笔 +0.32 万** → 市场过滤器有强信号,但作为「争议项」交回测定生死。
- **越亏越重仓越入金**的加仓行为与亏损正相关 → 仓位纪律(单笔上限、总敞口上限)写死。

### 1.4 与前作 LinoN 的关系(继承与切割)

前作 LinoN(`/Users/linotsai/Lino/LinoN`,FastAPI + SQLite + SwiftUI 双端 + ECS 部署)被用户判定为「**交易记录系统而非量化**」——选股拍脑袋、无回测、LLM 深判是模板腔,已弃用。

- **切割**:Neckline 以**回测引擎为地基**,一切规则参数须过回测才落地;LLM 一律自由对话体,**禁枚举模板卡**。
- **继承**:LinoN 的**工程资产**(尤其大量已踩平的数据源坑)必须吸收,详见 §3.7。LinoN 的坑清单权威在 `/Users/linotsai/Lino/LinoN/CLAUDE.md`(builder 落地对应模块前必读对应小节)。

---

## 二、设计共识与领域规则(方向已定案,不得擅改)

> 本章是产品圣经。build 照此实现;**任何改动方向须回 planner**。参数值凡标「回测定」的,由阶段 1 过堂后写死,不由 build 拍。

### 2.1 纪律章程 v0.1(系统只审计,不执行)

1. **每笔开仓必挂 -5% 止损条件单**,只许设、不许撤、不许下调;系统盘后对账查单完整性。
2. **止盈不设固定线**:回落止盈或时间退出(D4 思想),参数回测定。
3. **仓位纪律(v1.2 章程修订 = 三仓制,staged 生效)**:**最多持 3 只**(注意力约束「只做 3 仓」,不会更多);**单笔金额不定死**——由用户视股价与当时想控仓位当场在区间内自定,系统只把 **4 万**作为**违纪判定上限**(`single_cap`,**非推荐值**)、不替用户拍单笔金额;总敞口设满仓档(`max_exposure_frac`≈1.0)。**-5% 止损、回落止盈 5%、hold=5 不变。** 唯一事实源 = 现役 `strategy_versions` config;**生效于 v1.2 激活**(staged:用户清掉现有持仓 + 确认后由切换器脚本激活,见 §五 v1.2-A;激活前仍按 K1 现役值执行)。~~单笔仓位上限 2 万;最多持 5 只;总敞口 ≤60%(K1 原值,2026-07-25 前;v1.2 激活后由三仓制取代)~~
4. **单周实现亏损 ≥ 总仓 2%** → 当晚**强制复盘**(材料由系统生成)。~~「≥5% 次周单笔减半」挂起项~~ → **已否决**(2026-07-20 用户拍板:回测显示纯空效应 + 用户明确不要,永久关闭)。
5. **同票割肉后冷却**:冷却期天数参数回测定(初值 10 个交易日)。
6. **违纪审计并入周复盘**:用户每周提供交割单,系统对账(实际成交 vs 当周报告、条件单完整性)。

「买点 / 止损 / 目标 / 证伪条件」四件套的止损口径 **-5.0** 是全系统单一常量,只在一处定义,禁止各处漂移(继承 LinoN「规则常量唯一源」教训)。

### 2.2 母战法(策略内核)

- **风格**:强势股短线,持有 **2–5 个交易日**。
- **两类买点**:① 强势票**回调低吸**;② 平台**放量突破**。
- **禁买规则(第一版,阈值均回测调)**:
  - 当日跌幅 ≤ **-3%** 的绿盘大阴线禁买(初值)。
  - 距 20 日高点过远的**下跌途中票**禁买(阈值回测定,初值 -15%)。
  - **次新股 / 易跌停高弹题材票型**入黑名单或强制减仓。
- **强势的定义交回测赛马**(见 §6):涨停基因 / 20 日涨幅分位 / 量价结构 三选一或组合。

> **⚠ 阶段 1 判决(2026-07-20,以 SQLite 大脑 `strategy_versions.v1` 为唯一现行内核,本节以上文字保留作历史假设记录)**:日线 2–5 日窗口 A 股均值回归,「追强势」无正净期望——强势三候选**全否决**(`strength=none`);绿盘大阴线禁买、距前高禁买、同票冷却期均**被回测否决删除**;次新剔除否决、**高弹题材剔除采纳(风控属性)**、限主板;买点走 pullback,hold=5,回落止盈 5%。规则 v1 定位:**减损纪律系统,非正 alpha 策略**;正 alpha 开放路径 = 情绪信号(阶段 2)+ LLM 消息面(实盘归因考核)。全文见 `research/stage1_report.md`。

### 2.3 盘后报告(每交易日 16:00,CLI/文件形态先行)

> 定 16:00 是因 A 股有盘后交易,收盘数据 16:00 后稳定。

- **情绪仪表盘**:涨停家数 / 跌停家数 / 连板最高高度 / 炸板率 / 昨日涨停股今日平均溢价 → 输出**明日仓位额度**(满额 / 半额 / 休息)。
- **强势板块**:**加权不圈死**——板块只加分,全市场强势形态票均可入池;必须含**板块年龄因子**(启动第几天、涨停梯队扩缩、龙头是否掉队;启动早期加分、连续主线 4–5 天后衰减降权)。
- **候选 20 只带评分**,每只**四件套:买点 / 止损 / 目标 / 证伪条件**。
  - **前 10 只**过 **LLM 逻辑审判**(读新闻 / 题材 / 公告,判催化持续性,**有一票否决权**)。
  - **后 10 只**只给分数与形态标签,不耗 LLM。
- ~~大盘 MA20 市场过滤器 = 争议项~~ → **已否决定案**(2026-07-20 用户拍板,与阶段 1 回测结论一致:回顾性分层相关真实,但做成实时闸门样本内外双双更差——滞后指标不可交易)。**市场择时职责归情绪仪表盘**(其阈值第一版为启发式、标注未回测,靠实盘归因迭代)。

> **⚠ 自选体检拍板(2026-07-21 用户,v1.1 新增报告节)**:16:35 报告新增「**自选体检**」节——对用户自选池(§2.4 v1.1)每只票**用同一套评分管线**跑当日评分 + 形态标签 + 纪律红绿灯(禁买线 / 票型黑名单 / ST / 板块限制核对)+ 形态触发买点条件的给完整四件套。**LLM 只审「当日状态较上一份报告发生变化的」∪「用户点名(pinned)的」**(控成本 / 控时长),其余确定性输出。自选体检**不改候选 20 评分逻辑、不进候选榜**,是独立一节(同码复用评分,只扩输入范围到自选池)。落地 §五 v1.1-C。

### 2.4 盘中哨兵(轮询免费实时源,1 分钟一拍)

> **铁原则:盘中不产生任何新决策,只执行前晚计划,永不盘中推荐新票。**

1. **买点哨兵**:候选触达预设买点**且**确认条件成立(量能折算、站稳 VWAP)→ 进盘中看板。
2. **退潮哨兵**:盘中情绪恶化(炸板率飙升 / 跌停扩大 / 主线板块跳水)→「**今日计划作废、禁开新仓**」红色刹车。
3. **持仓哨兵**:持仓票放量跳水逼近止损线 / 触达目标区间 / 所属板块跳水预警。
4. **证伪哨兵**:候选分时走坏(低开不回 / 全天 VWAP 下方 / 量能异常 / 板块梯队瓦解,证伪条件**前晚写死**)→「剔除勿进」。**盘中主力资金流免费源不可靠,证伪只用价量结构**(VWAP / 量能折算 / 高低开)。

> **⚠ 推送路由拍板(2026-07-20 用户,推翻阶段 3「四哨兵各自推送」的默认设计)**:APNs 锁屏推送**只留两类**——① 每日 16:00 盘后报告就绪;② 退潮红色刹车(默认开、设置屏可关)。**其余哨兵事件(买点触发 / 证伪剔除 / 持仓预警)一律不推 APNs,只进 App「盘中看板」板块**(打开即看)。每类事件的推送开关在设置屏可配。理由:比行情速度必输同花顺,系统价值是把前晚计划执行成判决(看板),不是抢报新闻。上文四哨兵的**判定逻辑一字不改**,变的只是「触发后去哪」——看板(全部四类)vs APNs(仅退潮 + 报告)。阶段 3 已建的 `sentinel/channels.py` 通道抽象保留、阶段 4 新增 APNs 通道(复用 LinoN `push/apns.py`),**Bark 降为备用通道**。

> **⚠ 盘前校准 tick 拍板(2026-07-21 用户,v1.1 新增第 5 类哨兵动作 + 推送白名单扩到四类)**:交易日 **9:25:30** 加一次「**盘前校准 tick**」(集合竞价快照 9:25 后即含开盘价 / 竞价量)——**纯规则判定、零 LLM**:候选高开超阈→「买点已变形今日失效」;低开踩证伪线→「开盘即证伪预警」;竞价量能异常标注;持仓大幅低开→「止损条件单今日可能触发」。**9:26 一条汇总推送 + App 内明细**。这仍是 §2.4 铁原则的题中之义——**盘前不产新决策,只判前晚计划是否已被集合竞价作废**(执行层,不是选新票)。哨兵进程(FastAPI lifespan asyncio 单 unit)加盘前窗口分支,**现有 9:35 起 intraday 判逻辑一字不改**。
>
> **推送白名单(v1.1 更新为四类,推翻上文「只两类」)**:① 16:35 报告就绪、② 退潮红色刹车、③ **9:26 盘前校准汇总(新)**、④ **D5 时间退出(新)**——各自独立开关(设置屏可配)、各自独立 APNs category。**D5 时间退出**是规则 v1 `hold=5` 时间退出纪律的**执行器**(此前无人触发 = SOP 最大裸奔点):D5 当天看板置顶 + 推送。买点 / 证伪 / 持仓仍只进看板不推 APNs(不变)。落地 §五 v1.1-A(盘前校准)/ v1.1-B(D5 + 持仓生命周期)。

### 2.5 问询台(LLM 问询板块)

用户丢外部消息源的票进来 → 系统先跑**确定性检查**(纪律核对 + 同一评分管线跑分 + 板块年龄)→ LLM **带工具调用**(实时取数 / 重算)自然语言回答。

- **裁决只有两种**:「**不符合 + 依据**」或「**初审通过,进当晚海选池**」。
- **永不产出「现在就买」。**

> **拍板落地(2026-07-20)**:阶段 4 把问询台做成 App「问询台」板块(自由对话体聊天,继承 LinoN `/chat` 无状态、客户端持有上下文的对话工程)。「**初审通过进当晚海选池**」= 落 `inquiry_pool` 表(当日),`report.py` 生成当晚报告时把海选池的票**强制纳入候选评分 universe**(不改评分逻辑,只扩输入)。裁决二值 = 硬约束,system prompt guardrail + verdict 枚举只两值双保险,任何路径不产「买」。

### 2.6 回测引擎(系统地基,带笼子的策略进化)

- **同一套信号代码三跑道**:喂历史 = 回测、喂今日 = 报告、喂单票 = 问询台。
- **回测必须正确处理**:前视偏差、T+1、涨停买不进 / 跌停卖不出、停牌、复权(前复权)、滑点、手续费。
- **版本号双线规约(2026-07-22 用户定)**:**系统版本走 `v` 字头**(v1.1、v1.2……,产品/工程升级);**策略版本走 `K` 字头整数**(K1、K2……,`strategy_versions` 表标签,回测过堂的大脑)。两线独立演进互不挂钩;现役策略 = **K1**(原名 v1,2026-07-22 双端 DB 已改名)。任何文档/报告/客户端展示的策略号一律 K 字头。
- **策略进化带笼子**:
  - 按月 / 季调参;任何调整须过**同一道门**——回测 + walk-forward **样本外跑赢现役版本**才可上线。
  - 大脑带**版本号 + 变更日志 + 实盘表现按版本归因**。
  - **调参最终批准权在用户。**

### 2.7 LLM 输出风格(全系统硬约束)

- 一律**自由对话体、自然叙述**;**禁止结构化模板卡 / 枚举腔**(用户明确厌恶 LinoN 三轴卡)。
- 语气自然但**立场铁**:纪律不过,不放行。

---

## 三、技术选型(在此定死,不留给 build 猜)

### 3.1 语言与运行环境

- **Python 3.11+**(本地研究期,不受 LinoN 的 3.9 系统解释器约束;polars / pyarrow 在 3.11 上性能与内存更优)。阶段 0–3 全本地跑。
- 依赖装库走**阿里云 PyPI 镜像**(继承 LinoN 坑:直连公网 PyPI 易超时)。虚拟环境 `.venv`,`requirements.txt` 钉死版本。

### 3.2 数据源

- **主源 TuShare 600 元档 = 6000 积分**(token 从 `.env` 的 `TUSHARE_TOKEN` 读;权限已于 2026-07-19 按官网权限对照表逐项确认,限频 **500 次/分钟**)。
  - **可用**:`daily`(日线)、`daily_basic`(换手 / 量比 / 市值 / PE-PB)、`adj_factor`(复权因子)、`index_daily`(指数日线)、`stock_basic`(代码→行业)、`namechange`(ST 状态历史)、`trade_cal`(交易日历)、`moneyflow_dc`(东财个股资金流,`net_amount` = 主力净额,万元)、**`top_list`(龙虎榜)**、**概念板块和成分**(板块层 / 板块年龄因子的数据源)、融资融券、财务三大报表、沪港通列表。
  - **不可用(档位不够,方案已绕开)**:**`limit_list`(涨停榜单,15000 积分)→ 涨跌停全部自算**(见 0.4b 衍生表:涨停价 = `round(pre_close×(1+幅度),2)`,幅度按板块 10%/20%/ST 5%/北交所 30%,`close==涨停价` 判涨停、`high==涨停价且close<涨停价` 判炸板、连续涨停日计连板;新股上市首日无涨跌幅按 `stock_basic.list_date` 剔除);筹码分布 / 量化因子(8000 积分,不依赖);游资数据 / 个股及行业热板(15000 积分,不依赖);**新闻资讯为单独权限 1000 元/年,未购**(见 §8 决策项)。
  - **无分钟线。**
- **盘中实时源(免费)**:新浪 `hq.sinajs.cn`(主)/ 腾讯 `qt.gtimg.cn`(备)。仅供盘中哨兵,分钟级轮询。解析坑见 §3.7。
- **TuShare 调用姿势铁律**:`ts.pro_api(token)` **直传**,**禁用 `ts.set_token()`**(会往家目录写缓存,继承 LinoN 坑 5)。全市场批量接口只带 `trade_date`(不带 `ts_code`)一次返回全市场。限频:分批 + sleep(阶段 0 落地脚本按 TuShare 每分钟调用上限退避)。

### 3.3 存储(选型论证)

**规模估算**:A 股约 5400 只 × 2020-至今约 1600 交易日 ≈ **850 万行/表**;`daily` + `daily_basic` + `adj_factor` + `moneyflow_dc` 四张大表合计约 **3000–4000 万行** numeric;自算涨跌停衍生表(0.4b)只存涨停/炸板/连板行,约百万行;`index_daily` 极小。回测「喂历史」是**按交易日顺序迭代、每日取全市场横截面**的列式扫描负载。

**选型结论:Parquet(行情大表)+ SQLite(元数据 / 业务台账)混合。**

- **Parquet + polars(回测主力)**:`daily / daily_basic / adj_factor / index_daily / moneyflow_dc / limit_derived(自算)` 落 `data/parquet/<表>/year=YYYY/…`,**按年分区**。列存 + snappy 压缩后全量预计 **< 2 GB**;polars `scan_parquet` 惰性扫描 + 谓词下推,按 `trade_date` 切片近乎零拷贝,反复回测调参内存友好。→ **回测数据层用 polars lazy,选它就是为吃这个负载。**
- **SQLite(`data/neckline.db`)**:交易日历、股票 / 行业元数据、回测结果与净值曲线、盘后报告与候选、持仓审计、纪律台账、大脑版本与变更日志、问询会话。这些要事务、点查、关系查询,且与 LinoN 工程一致、后期客户端好接。
- **pandas 仅用于边缘衔接**:TuShare 返回的是 pandas DataFrame,落地时用 pandas 承接后转 polars / 写 Parquet;回测核心不碰 pandas。
- **为何不全 SQLite**:千万行横截面反复全表扫描,行存聚合慢、库文件臃肿。**为何不全 Parquet**:业务台账要事务与点更新,Parquet 不擅长。故混合,各取所长。

### 3.4 LLM

- **多供应商可切换(2026-07-19 用户拍板,不用 DeepSeek)**:候选 **GLM 5.2 / Kimi K3**(两家 API 均原生带联网搜索工具,正好覆盖 2.4 消息面方案)。设计约束:① `llm/` 层做**供应商抽象**(统一 chat + 工具调用 + 搜索接口,provider 实现可插拔);② key 与 provider 选择从配置读(研究期 `.env`:`LLM_PROVIDER` / `LLM_API_KEY`;**阶段 4 客户端里必须有「填 API key + 切换供应商」的设置入口**,运行时可切不重启)。**阶段 4 拍板(2026-07-20)**:App 可改 key/provider → 走 `PUT /settings/llm` 落服务端 `app_settings` DB 单行(**高危区**:key 服务端存取,DB 文件 600、gitignored、rsync 永不同步覆盖);`get_provider()` 解析优先级改为 **DB 覆盖 → `.env` 兜底**,每次调用现读故运行时生效;③ key 用户后续自填,**缺 key 时全链路优雅降级不崩**(继承 LinoN 降级链思想)。
- 继承 LinoN 的**降级链**(缺 key / 超时 / 非 200 / 非法 JSON → 优雅降级占位,全链路不崩)与**短读超时 + 每次全新连接重试**(`_READ_TIMEOUT=12` / `_CONNECT_TIMEOUT=6` / `_MAX_ATTEMPTS=3`,治 EdgeOne CDN 偶发单连接卡死)。
- **对话工程**:问询台的工具调用(实时取数 / 重算)沿用 LinoN v1.2.1 `/chat` 的姿势重写。

### 3.5 客户端载体(**已拍板 = 方案 A**,2026-07-20)

阶段 0–3 **完全不需要客户端**,CLI + 本地文件(markdown / HTML 报告)即可跑通全部研究与报告逻辑。载体决策推迟到阶段 4 前:

| 选项 | 优点 | 代价 |
|---|---|---|
| A. 复用 LinoN SwiftUI 双端 + APNs | 工程资产在、锁屏推送体验最好 | 最重;要维护 Xcode / 签名 / 真机 |
| B. Bark(推送)+ 轻量 Web(报告 / 问询台) | 最省;盘中哨兵推送即插即用,报告 / 问询台用浏览器看 | 推送样式简单,无原生交互 |
| C. 纯 Web(FastAPI + 简单前端) | 单端维护、跨设备 | 盘中推送需另配通道(可 + Bark) |

~~**推荐 B**:盘中哨兵推送走 Bark + 报告/问询台走轻量 Web。~~ → **已否决**。

> **⚠ 客户端载体拍板(2026-07-20 用户定案 = 方案 A)**:复用 LinoN 的 SwiftUI **iOS + macOS 双端**工程底子 + **APNs 推送**;**新 App、新 Bundle ID `top.linotsai.neckline`**(不改 LinoN 旧 App)。上表 A/B/C 保留作决策留痕。选 A 理由:锁屏推送体验最好、LinoN 工程资产(xcodegen 双端 / DesignTokens / APIClient / PushManager / 平台分叉)可整块搬、绿涨红跌沿用。**信息架构全新**(iPhone 四板块 = 今日计划 / 盘中看板 / 问询台 / 设置;macOS 四板块 + 周复盘工作台),非照搬 LinoN 今日台。复用清单与坑吸收见 §五 阶段 4C。

配色偏好(阶段 4 落地时遵循):用户沿用 LinoN 的**绿涨红跌**(与 A 股本地相反,是用户明确选择,勿「纠正」)。

### 3.6 部署

- **研究期(阶段 0–3)本地跑。**
- ~~盘中哨兵上不上 ECS = 后期决策项。~~ → **已拍板(2026-07-20)= 上 hz ECS,复用 LinoN 基建**。

> **⚠ 部署拍板(2026-07-20 用户,阶段 4 落地)**:后端 + 哨兵跑 **hz 杭州云 ECS**(`deploy@118.178.122.194`),照搬 LinoN 的 **FastAPI + systemd + nginx 反代**模式。复用 LinoN 资产:nginx 站点 + 证书 **`ln.linotsai.top`**(证书 ECDSA 到 2026-09-20 cron 自动续期健康,DNS 已在,**零新增用户网页操作**;域名 `ln` 语义留痕于 LinoN 是可接受的表面不符,不值得为改名新开子域 + 新证书——后者还要碰本机 certbot 已知问题,见 `hz_info.md §13`)、APNs `.p8`(Key ID `Q963AP3VY8` / Team `HX73DFL88G`,**账号级密钥可直接给新 Bundle ID 用**,APNs topic 换成新 Bundle ID 即可)。
>
> **端口/账户/目录**:Neckline 用**独立端口 8002**(⚠ 拍板文字说的「8001 空闲」已过时——`hz_info.md` 2026-07-20 记 LinoN 07-20 晨已恢复运行、仍占 8001 作记录工具,**8001 未空闲**;Neckline 用 8002 与 LinoN 共存,不抢端口)、独立 nologin 用户 `neckline`、独立目录 `/opt/neckline`(`deploy:neckline` setgid 2770,`.env`/`.p8`/`neckline.db` 600 `neckline:neckline`)。**接班切换(阶段 4 部署上线并联调通过后)**:nginx `ln.linotsai.top` upstream 由 `127.0.0.1:8001` 切到 `:8002` → `linon.service` stop + disable(LinoN 退役,兑现 `hz_info.md` 决策)→ 视需归档 `linon.db` / 清理旧站点。
>
> **内存硬约束(hz 仅 1.6G + 2G swap,还跑着 lf / fiscal / 主页 / postgres,v1.4.1 实测 used ~853M / available ~759M)是本阶段第一约束**。polars 全市场重活(backfill / 报告生成)能否在 ECS 上跑,**必须实测门禁定生死**(见 §五 阶段 4B),两套架构方案都写清,以实测定,回退路径 = Mac 定时落数 + 报告生成 → rsync 产物上云,ECS 只管 API + 哨兵轮询(轻量,关注池 ≤200 只)+ APNs。**哨兵不另起进程**——折进 FastAPI 单 unit 的 lifespan asyncio 任务(照搬 LinoN 单 unit 省内存),`scripts/sentinel.py` 独立脚本留本地。

### 3.7 继承资产(从 LinoN 搬什么,搬前必读其 CLAUDE.md 对应节)

代码可搬可重写,由 build 定;但**下列已踩平的坑必须吸收**(权威原文见 `/Users/linotsai/Lino/LinoN/CLAUDE.md`):

- **`tushare_client`**(LinoN `backend/app/data/tushare_client.py`):`TushareResult{ok,data,reason}` 永不抛异常的封装;`pro_api(token)` 直传;字段单位表——`daily.amount`=千元、`daily.vol`=手;`daily_basic.total_mv`=万元;`moneyflow_dc.net_amount`=万元(= 超大单 + 大单,东财主力口径,与用户同花顺一致)。**Neckline 需新增全市场批量拉取 + 落 Parquet。**
- **交易日历**(LinoN `app/calendar/`):静态兜底表(官方休市日 + 调休补班周末股市仍休)+ `trade_cal` 比对告警。**Neckline 需把日历从 2020 起补全**(回测六年)。注意包名勿与标准库 `calendar` 冲突(LinoN 坑:包内绝对导入)。
- **实时源解析**(LinoN `app/data/realtime.py`):新浪**必带 `Referer: https://finance.sina.com.cn`**(否则 `Kinsoku jikou desu!` 无数据)、GBK 解码、volume 股→手;腾讯 GBK、volume=手、amount=万元、bid/ask「价先量后」(**与新浪相反,易写反**)。归一目标:`volume` 单位手、`amount` 单位元。
- **盘中 VWAP 纯函数**(LinoN `app/data/intraday.py`):`VWAP = amount/(volume×100)`(元/股),不自拉价、不联网。
- **LLM 层工程姿势**(LinoN `app/llm/deepseek.py`,供应商换了但姿势保留):降级链 + 短读超时重试 + `MockTransport` 可注入免联网单测。
- **黑名单口径**(LinoN 教训):按**板块整段正则** `^(30|688|689|8|4|920)` 挡创业板 / 科创 / 北交所,**勿枚举精确子段**(会漏新增子段);白酒用 `stock_basic.industry` 精确归类,非名称关键词。

### 3.8 铁律(全项目硬约束,build 逐条守)

- **token / key 绝不入 tracked 文件**:一律从 `.env` 读;`.env` 已在 `.gitignore`。**scratchpad 里的归因脚本 `entry_profile.py` 硬编了一个 TuShare token —— 严禁复制进仓库,重写时一律从 `.env` 读。**
- **系统永不自动下单 / 撤单 / 改止损**;一切成交由人 + 券商执行。
- **同码三跑道**:回测 / 报告 / 问询共用同一套信号代码,不得各写一份(否则报告与回测漂移)。
- **规则常量单一源**:-5.0 止损、仓位上限、各阈值只在一处定义,禁止漂移(继承 LinoN 教训)。
- **无前视偏差**:回测第 T 日任何计算都不得读到 > T 的数据;数据访问层强制截断 + 单测守护。
- **LLM 一律自由对话体**,禁模板卡。

### 3.9 建议仓库布局(build 可微调,大方向照此)

```
Neckline/
  .env                      # gitignored,TUSHARE_TOKEN / LLM_PROVIDER / LLM_API_KEY
  .gitignore                # 已含 data/*.db、data/parquet/
  PROJECT_PLAN.md           # 本文件,唯一权威
  requirements.txt
  neckline/                 # 主包
    config/                 # 读 .env、路径常量
    data/                   # tushare_client / realtime / intraday / 数据访问层(polars)
    calendar/               # 交易日历(勿撞标准库名)
    backtest/               # 引擎、撮合、组合、walk-forward
    strategy/               # 母战法信号(同码三跑道的源)、大脑版本
    report/                 # 盘后报告管线(情绪仪表盘 / 板块 / 候选评分)
    sentinel/               # 盘中四哨兵
    llm/                    # 供应商抽象(GLM/Kimi 可插拔)/ 深判 / 问询台对话与工具
    review/                 # 周复盘 / 违纪对账
  scripts/                  # 落地脚本、每日增量、回测 CLI、报告 CLI
  data/                     # Parquet + SQLite(gitignored)
  archive/                  # 历史 plan 全文、超长记录
  tests/
```

---

## 四、当前状态

**2026-07-25 · v1.2 立项(施工中)· 人机协作配套:三仓章程 + 预注册决策日志 + 归因闭环。** 系统重定位——用户 = 唯一决策人,系统 = 情报收集 + 机械分析 + 纪律执行 + 归因审计(**机器不替选股**,策略线三场战役判死「机器自动出信号」)。完整施工图见 §五「当前版本 Plan(v1.2)」,分块 0/A–F + D2:**v1.2-0** sync_code.sh footgun 先修(chown 收尾 prune `data/` + 脚本末尾只读属主自检)→ **A** 仓位章程三仓制(🔴,唯一源现役 config:`max_positions` 5→3 / `single_cap` 2万→4万〔**语义变为违纪判定上限、非推荐值**〕/ `max_exposure_frac` 0.6→1.0;先落 `v1.2` 行 `activate=False`〔config 承 K1 血缘、仅改三仓位字段、**绝不碰 K 字头**〕,**staged 两步**:清仓 + 用户确认后切换器 `--confirm` 才激活;附带修「周复盘用今天章程重判历史周」洗白洞:`strategy_versions.activated_at` + `reviews.strategy_version` + 按周取「当时现役」)→ **B** 预注册决策日志(六项落库不可编辑〔改动=新增修订行,归因认首版〕+ 服务端 created_at〔客户端不许传〕+ 论点标签枚举码;**审计件非下单件**)→ **C** 挂单未成交追踪(N=5 交易日,折进 16:35 报告管线复用 EOD 面板不新拉源)→ **D** 归因入 4D 周复盘(决策日志 via `position_id` 与 FIFO RoundTrip 按 `(ts_code,buy_date)` 邻近匹配、按论点标签胜率/盈亏比 + 「无决策日志开仓 N 笔」纪律项〔软约束落点,不静默丢〕)→ **D2** 问询台选股域漂移清理(`run_deterministic_checks` 复用 `base_universe_expr()`)→ **E** 客户端双端(录入六项 + 标签 picker 嵌「已按计划买入」流程之前、成交后一键关联、**不新增第六 tab**、entry-suggestion 改区间双档、macOS 归因表)→ **F** 部署 + schema 迁移 + 活体(🔴)。**铁律不变**:同码不重写、单一事实源不漂移(`single_cap` 语义 v1.2 起 = 违纪上限)、系统永不自动下单、不新增推送类(仍四类)。**K1 行原样保留**;激活后需用户在策略线会话同步 `STRATEGY_LAB §一`「现役」表述。需求 3(盘前情报包)推 v1.3(§七)。**v1.2-0 已完工(2026-07-25,@builder)**:`sync_code.sh` 打印的收尾 chown/chmod 两条均改 `find -path .../data -prune` 版跳过 `data/`,脚本末尾新增只读属主自检(`sudo -n stat` 核对远端 `neckline:neckline`,不符红字 + `exit 1`;新增 `--selfcheck-only <path>` 独立验证模式,不触发 rsync);`DRY_RUN=1` 预演肉眼核对两条收尾命令均已 prune、自检按设计跳过,ECS 无害临时文件三态(不存在/属主错/属主对)+ 不可达远端四场景验证自检逻辑本身全过(**全程未碰生产 `neckline.db`**),`~/Lino/hz_info.md §12`/line-191 已同步销账。全量 pytest 833 绿(零回归,本块不改 Python)。**本块未部署、未 restart 服务、未跑 rsync 实传**,真部署 + 收尾命令活体验证留 v1.2-F 合并做。仓库现状:v1.2-A 待 @builder-pro 起手(🔴 高危区:碰纪律章程 + 大脑激活)。

**2026-07-25 · K3 策略研究(系统化超跌反弹,B0–B2)· 已否决中止 → 详见策略线权威 `STRATEGY_LAB.md`(§一 现役与机制 / §二 雷区地图 / §六 策略变更日志)。** B2 四臂组合级回测中心假设否决、2026 生存门禁七臂尽墨、停止挖矿条款触发(K1/K2/K3 三次在日频 + T+1 + 上班族注意力信封内验证无稳健正 α),K3 未落库、**K1 仍唯一现役**。**系统线正文不复述策略研究结论**(2026-07-25 架构拆分,策略线自持图纸);§九变更日志 K 条目留历史留痕不动。

**2026-07-22 · K2 策略研究(B1–B6)完工 · 中心命题否决 · K2 候选大脑落库不激活。** 施工图 §五B 六研究件
逐块过堂(`research/k2_report.md` 完整):B1 情绪三态闸门(二值排除休息**否决**、内外双双更差,满额闸门非平稳
不采纳,答遗留⑤「正解仍开放」)→ B2 主线识别器(资金主战场注意力口径,6% 日翻手极稳,07-22 校准命中用户五主题
4-5/5 但分注意力/动量两轴,领先性弱)→ B3 个股成员判定(共动性代理验证合理、成员偏宽,成分洞降级)→ **B4 中心
命题 = 否决**(「情绪进攻段 × 主线成员内追强势」无正期望,印证阶段 1 P3;样本内组合级 K2 构型 -14%~-72% 灾难,
样本外唯一正收益是情绪 gate 非平稳 regime 效应、消融证明去 gate 即塌、walk-forward K2 输 7/10)→ B5 止盈三方案
(**回落 5%〔K1〕最优**,固定 +15% 更差、用户 +14~17% 先验回测不支持)+ 高弹风险预算(**黑名单〔K1〕最优**,减半
参与最差)。**采纳集为空:K2 config = K1 逐字段相同**,`brain.save_version("K2", activate=False)` 落库,
断言 **K1 仍 is_active=1 唯一现役 / K2 is_active=0**。研究扩展(`require_mainline_member`/`take_profit_fixed`/
`high_elasticity_half`)全部默认关闭,K1 逐位不变护栏单测锁死;**全量 pytest 820 passed(816 基线 + 4 护栏,0
回归)**。生产系统零改动、纪律章程 §2.1 未改一字。K2 裁决书推荐:**不激活**(K2 无稳健改进,K1 仍现役);正
alpha 仍是开放问题(遗留见报告)。详见 `research/k2_report.md` 与下方变更日志。

**2026-07-20 阶段 3(盘中哨兵)完工。** §2.4 四哨兵逐项交付:买点哨兵(候选触达 `entry_spec` 写死买点 + 量能
折算/VWAP 确认)、退潮哨兵(关注池代理样本三条独立触发 + 联动抑制买点)、持仓哨兵(止损逼近 / 回落止盈 / 板块
跳水三条独立,阈值从策略大脑现役版本读,不硬编)、证伪哨兵(`invalidation_spec` 四条价量结构,不看资金面)。
配套工程:极简持仓台账(SQLite + CLI)、事件防重(SQLite 落库,进程重启存活)、推送通道抽象(Console 默认 +
Bark 已备 + 可选 macOS 通知)、哨兵常驻脚本(交易时段轮询、非交易时段优雅退出/待机、午休降频)。全量
**pytest 456 passed**(阶段2收尾 263 → 阶段3 新增 193,0 回归)。四哨兵判定规则字面化清单、防重/重启语义、
推送通道说明、合成盘中冒烟真实结果见下方变更日志。

**2026-07-20 · 阶段 4 立项(施工中)**:客户端 + 云端化 + 问询台 + 周复盘。用户五项拍板已并入设计共识(§2.4 推送路由 /
§2.5 问询台落地 / §3.4 LLM key DB 存取 / §3.5 客户端方案 A / §3.6 上 hz ECS),完整施工图见 §五 阶段 4(4A 后端 API →
4B 云端化 + 部署 → 4C 客户端双端 → 4D 周复盘工作台 → 4E 端到端联调 + APNs 真机)。**阶段 3 三类欠账带进阶段 4 挂账不丢**
(见 §五 阶段 4E):① 真盘中活体验证(阶段 4 哨兵上 ECS 常驻,交易时段首次真跑 `sentinel` 引擎 = 兑现此欠账);② 哨兵
第一版启发式阈值(量能折算 / VWAP / 退潮三阈)需实盘校正;③ GLM/Kimi 真调用未活体验证(阶段 2 遗留)+ Bark 无
`BARK_URL` 活体(阶段 3 遗留)→ 阶段 4 用户在 App 设置屏填 LLM key 后首次真调用即验证,Bark 降为备用不阻塞。

**2026-07-20 · 阶段 4A + 4B 完工(后端 API + 云端化部署)**:FastAPI 脊椎全端点 + 单测(pytest 456 → **527**,+71,0 回归)
+ 本地真 uvicorn 冒烟;ECS(hz)部署 `neckline.service` active 监听 8002(与 LinoN 8001 共存,**未动 linon**),
内存门禁实测走**方案 A(全云)**,systemd 定时(daily 16:05 / report 16:35)+ 哨兵 lifespan asyncio 任务就位,APNs 层
单测过(真推留 4E)。详见变更日志。

**2026-07-20 · 阶段 4C 完工(SwiftUI iOS+macOS 双端客户端)**:新 App `top.linotsai.neckline`(xcodegen 单 target,
iOS+macOS,deploymentTarget 26),四板块(今日计划 / 盘中看板 / 问询台 / 设置)+ macOS 周复盘工作台壳。复用 LinoN
工程资产改造(DesignTokens→NK 命名空间绿涨红跌不变、AppConfig、APIClient actor 禁 `appendingPathComponent`、
PushManager 类别简化为 REPORT/RETREAT 两类信息推送、StaticTradingCalendar 裁掉 LinoN 专属 D4 强平逻辑);领域模型
逐字段对齐 `neckline/api/schemas.py`。**本地真联调**(dev uvicorn + 隔离库 + 真实 backfill 数据跑
`scripts/report.py` 生成真报告,非手造 fixture):report/board/positions 开清仓/inquiry/settings 全端点真请求闭环,
iOS Simulator 四板块真机截图,macOS 二进制稳定运行。联调中发现 `CandidateOut.board` 服务端字面是英文枚举码
(MAIN/GEM/STAR/BSE)非中文名,已加客户端展示层换算(不改服务端、不重造分类逻辑)。单测 38 个(31 离线 + 6 真实
网络集成,后者探活失败自动 skip 不污染门禁)全绿,双端 `xcodebuild` **BUILD SUCCEEDED**、iOS Simulator
**TEST SUCCEEDED**。问询台裁决二值(含对抗性字符串)、URL 门禁(`?` 不被编码)、退潮警示派生均有直接单测覆盖。
**无偏离**:四板块信息架构、坑吸收清单、契约对齐均按 plan 落地;`stopOrderChecked` 因 4A 未提供持久化端点,
按 plan 原文实现为本机会话内本地提醒(非跨端持久化,已在代码注释写明)。

**2026-07-20 · 阶段 4D 完工(周复盘工作台·对账引擎)**:新包 `neckline/review/`(parse/reconcile/material/store),
`neckline/api/` 新增 `POST /review/upload`(multipart)/`GET /review?week=`/`PUT /settings/review-col-map` 三端点,
macOS `ReviewWorkbenchView` 从 4C 占位壳接通真实拖入上传 + 表格化展示。全量 **pytest 527 → 621**(+94,0 回归),
Swift 侧 **38 → 41 个单测**全绿,双端 `xcodebuild` **BUILD SUCCEEDED**。`scripts/smoke_review.sh` 用**真实
uvicorn + 真实 openpyxl 生成的 xlsx**(非 TestClient 模拟)跑通拖入→解析→三查→强制复盘→落库→历史回放全链路。
**本次任务范围内明确不做 4D.3 的 LLM 叙述叠加层**(任务指令原文:"禁模板腔的 LLM 部分本块不做,纯确定性输出
即可"),`review/material.py` 只产出确定性材料。详见变更日志。**剩 4E 端到端联调 + APNs 真机 + LinoN 接班切换。**

**2026-07-20 · 阶段 4E 部分完工(LinoN 接班切换 + 收官接线)**:①**4E.3 LinoN 接班切换已执行并验证**——
退役前 `linon.db` 在线一致性备份(`sqlite3 .backup`)归档本机 `Lino/Archive/linon_decommission_20260720/`(303104B,
integrity ok,10 表行数清单逐表吻合,sha256 逐字节一致)→ `linon.service` stop+disable(8001 不再监听,`/opt/linon`
目录/secrets 原地保留)→ nginx `ln.linotsai.top` upstream 8001→8002(仅改 `proxy_pass` + 加 `client_max_body_size 20m`,
certbot-managed 块与证书路径未动,原配置带时间戳备份;`nginx -t` 过再 reload)。公网验证:health 200(Neckline
`0.4.0-stage4A`)、鉴权端点无 token 401 / 真 token 200、邻居站点(lf/主页/fiscal/xiaoran/裸IP)全不受影响。
②**客户端默认后端改 prod**——`AppConfig` 默认环境 `.dev`→`.prod`(`https://ln.linotsai.top`),保留环境 picker /
`baseURLOverride` 可配置覆盖;顺手把 `AppConfig` 改为可注入 `UserDefaults`(生产恒 `.standard`,单测用隔离 suite
hermetic,治模拟器残留 `NK_ENVIRONMENT` 串味)。双端 `xcodebuild` **BUILD SUCCEEDED**、iOS Simulator **TEST
SUCCEEDED**(45 执行 / 6 skip 探活 / 0 失败;新增 `AppConfigDefaultTests` 4 例)。③**inquiry_pool 消费接线(4A 遗留#5)**
——`report.build_report` 消费当日 `inquiry_pool`,问询台「初审通过」票经 `forced_codes` 强制并入候选评分 universe
(§2.5「只扩输入,不改评分逻辑」:绕过 entry mask 从当日面板取行并入、评分排序同码一视同仁、即便排 top_n 之外也保证
出现),空池零回归。全量 **pytest 621 → 629**(+8,0 回归)。**仍挂账(4E 未完)**:真盘中活体验证(哨兵已随
`neckline.service` 常驻,下一交易时段自动跑,收盘后核查)、APNs 真机真推(待用户 Xcode 装机注册 device token)、
LLM/Bark 活体(待用户 App 填 key)。详见变更日志。

**⚠ 高危区提示**:哨兵接触盘中实时源(新浪/腾讯)+ 推送通道(Bark)是阶段3 新增的对外接口,已用
MockTransport 充分覆盖降级链;持仓台账/防重表是新增的写入路径(SQLite),已用单测覆盖 CRUD + 幂等;
「退潮触发后抑制买点」是本阶段最关键的安全属性(直接对应 §2.4 铁律「永不盘中推荐新票」),已有直接单测断言。
4C 新增 LLM key 客户端录入(🔴,设置屏)与 APNs PushManager(🔴,iOS)均按 plan 坑清单实现;4D 对账引擎涉及
盈亏金额计算(单笔仓位/敞口/止损纪律/强制复盘阈值),plan 原文建议 builder 收尾叫一次 `review`,目前用户尚未
明确要求,记在此处供用户决定是否在阶段4D开工后、或阶段4E之前触发一次独立复审。

**2026-07-21 · v1 上线运行 + 首日生产坑快修**:阶段 4 全部完工,v1 已上线——ECS 全云(`neckline.service` 8002 常驻、
接班 `ln.linotsai.top`)、双端 App 在用户真机、APNs 真推通(报告 + 退潮两类)、**LLM=GLM 已激活**(用户设置屏填 key,
真调用 26.3s 成功)。首日三处生产坑已快修(详见项目 `CLAUDE.md` §「v1 上线首日」+ 变更日志):① TuShare 类型漂移毒化
Parquet 分区(某列全空日落 String 与历史 Float64 冲突,16:35 报告崩)→ `market_data.write_table_day` 落盘统一入口
按既有分区 schema cast;② 带联网搜索 LLM 读超时 25s→90s(生产 10 只审判 5 只 ReadTimeout);③ 客户端 iOS 双标题 +
报告时间文案 16:00→16:35。pytest 631。

**2026-07-21 · v1.1 立项(施工中)· SOP 补洞**:实盘暴露 SOP 四个「裸奔点」,用户拍板 v1.1 补洞(2026-07-21 讨论定案,
方向不得改,并入设计共识 §2.3 自选体检 / §2.4 盘前校准 tick + 推送四类 + D5 执行器):① **盘前校准 tick**(9:25:30 集合竞价
快照纯规则判定、9:26 汇总推送);② **持仓生命周期头版**(D 计数 D1–D5、**D5 时间退出执行器**、一键补录预填、漏录兜底);
③ **自选板块**(≤30 用户主理池 + 16:35 报告自选体检节 + 并入哨兵关注池 + 同花顺 txt 离线对账);④ **问询窗口修复**
(`inquiry_pool` 消费从「当日」改「上次报告生成以来」,补跨日 / 重复消费单测)。**推送白名单扩到四类**(报告 / 退潮 / 盘前
校准 / D5)。完整施工图见 §五「当前版本 Plan(v1.1)」,分块 A–H + 每块验收 + 高危区标注(盘前校准/D5 新推送类 APNs、
哨兵进程盘中常驻改动、部署 点名 @builder-pro)。**铁律不变**:同码不重写、单一事实源不漂移、关注池 ≤200(自选并入后仍守)。

**2026-07-21 · v1.1-A/B 后端完工(本地全绿,@builder-pro)**:A 盘前校准 tick + B 持仓生命周期后端两块 🔴 高危交付。
新 `neckline/sentinel/precall.py`(纯规则零 LLM:高开变形 / 低开证伪 / 竞价量能 / 持仓低开四判定 + D5 扫描 + `run_precall_tick`
当日只跑一次);`api/app.py::_sentinel_loop` 加 9:20–9:30 盘前分支(30s 收紧,**intraday 逻辑一字未改**,盘前一拍异常被吞不掀
翻主循环有单测);`sentinel/positions.d_count`(买入日=D1 交易日历口径,单一源)+ `PositionOut` 五派生字段(dCount/maxHoldDays/
distToStopPct/retraceState/todayAction,stopLine 改读现役 config)+ `GET /positions/entry-suggestion` 预填;推送白名单
扩四类(`apns` 加 PRECALL/D5EXIT category、`notify` 加 `push_precall_summary`/`push_d5_exit`,`__all__` 结构守护);
`app_settings` 幂等加 `push_precall`/`push_d5exit` 两列(`db._migrate_columns`,生产 DB 副本验证 integrity ok + 重跑不炸);
漏录兜底 `compute_missed_entry_hint`(实时算,补录后自动消失)。全量 **pytest 682 passed**(631→682,+51,零回归)。合成竞价
冒烟 `scripts/smoke_precall.py` 真数据跑通(68 只关注池 / 8 变形 / 1 证伪 / D5 命中沙河股份)。**留 v1.1-H 联调**:9:26 真机
推达 + 看板明细、D5 临期持仓真机推达 + 持仓卡置顶(活体验收);G.3 看板中文标签(precall/d5exit 已可透传,标签在 G 补齐)。

**2026-07-21 · v1.1-C/D 后端完工(本地全绿)**:C 自选池表+CRUD+体检+同花顺 txt 对账、D 问询窗口修复两块交付。

**C 自选池**:新 `neckline/watchlist.py`——`watchlist` 表(`ts_code` PK,≤30 上限服务端硬校验,超限 `WatchlistFullError`→
API 层转 422;增删只经 `add_watchlist`/`remove_watchlist`/`set_pinned` 这三个函数,报告管线/哨兵/问询台只调用只读的
`list_watchlist`/`list_watchlist_codes`,无任何系统自动写路径,单测断言)+ 同花顺 txt 互转/对账(`parse_ths_txt` 按
UTF-8(含BOM)→GBK 顺序尝试解码、每行只取行首6位数字兼容多种未经验证的真实变体,复用 `review.parse.normalize_ts_code`/
`sentinel.quotes.to_symbol` 判后缀,不新写正则;`export_ths_txt`/`reconcile_ths`)。`sentinel/universe.py::load_watch_universe`
并入自选池——去重后「持仓∪自选∪候选」全保留(优先级同级),`_load_prev_limit_up_codes` 只填剩余额度到 `breadth_cap=200`,
超限时按「持仓>自选>候选」裁剪(单测覆盖边界);自选池里昨晚体检已触发买点的票转 `Candidate` 形状(`_build_watchlist_candidates`,
过滤「现仍在自选池」+「未与候选重复」)供买点/证伪哨兵(`engine.py`)与盘前校准(`precall.py`)同码消费,**entry_spec/
invalidation_spec 均是昨晚 16:35 报告生成时写死的,盘中/盘前只读不重算,不违反§2.4「不产生新决策」**。

新 `neckline/report/watchlist_check.py`——自选体检:评分**同码复用** `report.candidates._base_score_expr`(在全市场面板
上一次性算好,与候选评分数值恒等,`TestScoreSameAsCandidates` 直接证明);纪律红绿灯 = 选股域(`base_universe_expr()`
整条复用不拆解,避免阈值漂移)+ 现役 config 启用的禁买过滤(P4/P5/P6,逐项拆开展示原因);买点触发直接用
`momentum.build_entry_mask(cfg)` 本尊(与"今天算不算候选"同一把尺);四件套复用 `candidates.py` 已导出的公开函数一字不重写。
**自选体检不受 entry mask 约束**(即便今日不满足买点也照样给出评分/红绿灯,这是与候选评分故意的行为差异,已有直接单测)。
状态变化 diff(`_is_changed`,写死定义:首次出现/红绿灯翻转/买点触发翻转任一方向/形态标签集合变化)+ LLM 控成本
(`apply_llm_review` 只对 changed∪pinned 跑 `llm.judge.judge_candidate`)——`judge_candidate` 新增可选 `system_prompt`
参数(默认值不变,候选审判调用点零改动),自选体检传入新增的 `WATCHLIST_JUDGE_SYSTEM_PROMPT`(两套 prompt 共用同一套
「结论:通过|否决」解析与降级链)。`reports` 表新增 `watchlist_json` 列(幂等迁移,默认 `'[]'`,老报告前向兼容);
`report.store.load_watchlist_snapshot_before`(严格早于目标日,同日补跑不拿自己当基准)供 diff 用。`build_report` 末尾接线
+ `render.py` 新增独立「自选体检」markdown 节(不与候选混排)。API:`schemas.WatchlistCheckOut`/`WatchlistItemOut` 等 +
`GET/POST /watchlist`、`DELETE /watchlist/{code}`、`PUT /watchlist/{code}/pin`、`POST /watchlist/reconcile-ths`(multipart
txt)、`GET /watchlist/export-ths` 五端点(鉴权沿 `require_token`);`ReportOut` 新增 `watchlistCheck[]`(旧报告读回空数组
不是 null)。**真实数据验证**:隔离库 ATTACH 生产 `data/neckline.db` 只读参考表(trade_cal/strategy_versions/stock_basic/
namechange)+ 真实 Parquet backfill,加 3 只真实票跑 `2026-07-17` 真报告——贵州茅台(600519.SH)绿灯+买点触发,给出真实
MA10=1217.11 的完整四件套;宁德时代(300750.SZ)因创业板+`forbid_high_elasticity` 正确判红灯;平安银行(000001.SZ)绿灯
未触发。`scripts/smoke_api.sh` 扩 9 步真实 uvicorn+curl 冒烟(CRUD/pin/超限/txt reconcile 真多部分表单上传/导出/删除)全过。

**D 问询窗口修复**:根因——`inquiry_pool` 旧消费判据 `trade_date(入池当日) == report_date`,16:35 报告已生成后问询通过的票
入池当日停留在"今天",下一份该消费它的报告是明天的,`trade_date` 永远对不上、永久掉缝。修复:新列
`consumed_report_date`(NULL=待消费,幂等迁移);`api/stores.load_pending_inquiry_codes`(`WHERE consumed_report_date IS
NULL OR = 本报告日`)替代旧的 `load_inquiry_pool(trade_date)` 消费查询(后者保留供审计,不再参与消费匹配);
`mark_inquiry_pool_consumed` 在 `build_report` 落库成功后调用(`save=False` 绝无此副作用)。`trade_date` 列退化为纯审计
字段,**不再承担消费匹配职责**——`_inquiry_basis_pool_date` 的 `pool_date` 因此与 `basis_date` 解耦,直接取「今日交易日历
口径」。单测覆盖任务点名的全部场景:跨日边界(16:35 后入池次日报告纳入)、同一票不被两份报告重复计入、报告补跑(同日
重算)幂等重纳、空池 noop 零回归、消费后新入池票不受已消费票影响、迟迟未被消费的票跨多日查询仍待消费。

**无重大偏离**,两处实现选择记录:①退潮哨兵的板块联动样本(`_hot_sector_peer_returns`)刻意不纳入自选票——plan C.2 原文
点名「买点/证伪/持仓/盘前」四类哨兵享自选同级待遇,未点名退潮,故维持只用候选样本;②自选体检的 LLM 审判结果直接嵌入
`WatchlistCheckItem`/`watchlist_json`(而非像候选那样另建 `llm_judgments` 表行),因为同一票同日可能既是候选又是自选,
共用 `llm_judgments` 的 `UNIQUE(trade_date,ts_code)` 会产生互相覆盖的收窄写冲突,自包含存储更安全、隔离。

全量 **pytest 682 → 796 passed**(+114,零回归);新增文件 `neckline/watchlist.py`/`neckline/report/watchlist_check.py`/
`tests/test_watchlist.py`/`tests/test_watchlist_check.py`/`tests/test_api_watchlist.py`,修改 12 个既有模块 + 8 个既有
测试文件(逐条见变更日志)。发现一处 4A 遗留的潜在漂移风险(`api/inquiry.py::run_deterministic_checks` 手写重复选股域
逻辑、未核对两条禁买过滤)已记入挂起任务,非本块范围不改。

**遗留给客户端 E/F/G/H 的接口契约清单(新端点/新字段,E/F/G 落地时对照)**:
- 新端点(鉴权沿 `require_token`,契约见 `neckline/api/schemas.py`):`GET /watchlist` → `WatchlistOut{items:[WatchlistItemOut], maxSize:30}`(每项含 `check:WatchlistCheckOut|null` = 最近一份报告的体检快照,从未体检过 → null);`POST /watchlist` body `{code,name?,note?}` → `WatchlistAddOut{item}`,**满 30 返 422** `{detail:{reason:"watchlist_full"}}`;`DELETE /watchlist/{code}` → `OkOut`,不存在 404;`PUT /watchlist/{code}/pin` body `{pinned:bool}` → `OkOut`,不存在 404;`POST /watchlist/reconcile-ths`(multipart,字段名 `file`,单 txt 文件)→ `ThsReconcileOut{onlyInThs[],onlyInNeckline[],both[]}`(均 ts_code 格式,只算差集不写入,对齐由客户端调上面的 CRUD);`GET /watchlist/export-ths` → `ThsExportOut{text,count}`(text 是可直接存文件的 txt 全文)。
- `ReportOut` 新增字段 `watchlistCheck: WatchlistCheckOut[]`(旧报告 / 空自选池 → 空数组,不是 null,客户端不必特判)。`WatchlistCheckOut` 字段:`code/name/pinned/source/hasData/close/board/score/patternTags/hotSectors/sectorNames/greenLight/disqualifiers/buyPointTriggered/buyPoint/stop/target/invalidation/invalidationSpec/entrySpec/statusChanged/llmJudgment{verdict,narrative,degraded}|null`——四件套字段命名与 `CandidateOut` 一致(`buyPoint/stop/target/invalidation`),**F.2 客户端可直接复用 `CandidateRow` 四件套布局**(plan 原文已点名)。
- ~~v1.1-G 待办(本块未做,按 plan 归属 G 块):`GET/PUT /settings/push` 契约扩至四字段(报告/退潮/盘前/D5)**本块未动**~~ → **已在下方 E/F/G 施工中完成**(G.1)。
- D 块(问询窗口修复)**纯后端内部修复,无任何客户端契约变化**——`POST /inquiry` 请求/响应形状不变,客户端无需改动。

**2026-07-21 · v1.1-E/F/G 客户端完工(本地 + 真实 dev 后端联调全绿)**:三块客户端交付
+ G.1 一小步后端(`GET/PUT /settings/push` 契约扩至四字段,任务范围内)。

**G.1(后端,先行)**:`PushSettingsOut`/`SettingsPushIn` 加 `precall`/`d5exit`;
`settings_store.set_push` 扩签名同步写 `app_settings.push_precall`/`push_d5exit`
两列(v1.1-A/B 已建好列,本块补写入接线);`GET/PUT /settings/push` 端点接线。补
`test_put_push_missing_field_422` + `test_board_labels_precall_and_d5exit_events`
(看板 `_SENTINEL_LABEL` 对 precall/d5exit 的中文标签契约,客户端 `SentinelKind`
枚举依赖此契约,原逻辑已在 v1.1-A/B 就位,本次只补regressions测试硬化)。同步改
`test_notify.py` 三处 `set_push` 调用点 + `smoke_api.sh` 步骤9。**pytest 796→798**。

**E(持仓卡改版 + 一键补录)**:`Position` 加 `dCount`/`maxHoldDays`/
`distToStopPctServer`(=服务端 `distToStopPct`;因与既有客户端计算属性
`distToStopPct` 撞名,显式 `CodingKeys` 改名,两者算法等价但保留旧计算属性不动、
不碰既有单测)/`retraceState`/`todayAction`;`isExitDay`/`todayActionTone` 展示层
派生(优先级 D5>回落止盈已触发>距止损,只选颜色/是否醒目横幅,文案仍来自服务端)。
今日计划持仓区置顶到候选之上;`PositionCard` 加 D 计数徽标/距止损线/回落止盈状态,
D5 时触发顶部醒目横幅。`missedEntryHint` 有值时顶部提示条。候选卡「已按计划买入」
→ `AppModel.openEntrySheet(fromCandidate:)`:买点参考价读新增 `Candidate.entrySpec`
(pullback→ma10/breakout→platformHigh,展示层选择不新推导数字,同 `boardLabel`
先例)预填价格,调 `GET /positions/entry-suggestion` 拉真实推荐 qty + 止损提示,
缺 entrySpec 时留空手填不崩(单测覆盖两条路径)。

**F(自选第五板块 + macOS txt 对账)**:`AppTab` 加 `.watchlist`,iOS 五 tab(今日
计划/盘中看板/**自选**/问询台/设置),macOS 侧栏同步插入。新 `WatchlistView.swift`:
列表(评分/形态标签/纪律红绿灯/买点四件套,四件套展开区抽成 `FourPieceDisclosure`
组件与候选卡共用,不各写一份)、+自选输入框、pin/移除(**增删只经用户显式点击,
无任何自动触发路径**);macOS 独有同花顺 txt 拖入对账区(`reconcile-ths` 展示
仅同花顺有/仅Neckline有/两者都有三分组差异 + 一键对齐 + 导出存文件,iOS 不做)。
候选卡 / 问询台裁决卡加「+自选」一键(`quickAddWatchlist`,满 30 时给出明确
「自选池已满」提示而非裸「字段校验失败」)。`APIClient` 补 6 个 watchlist 方法 +
`entrySuggestion`;新增 `delete()` 传输层 helper;`APIError` 加 `.notFound` 与既有
`.notHolding` 分开映射(watchlist 404 `reason=not_found`,避免误显示"该持仓已清或
不存在")。

**G(设置四推送开关 + 看板中文标签)**:`PushSettings` 加 `precall`/`d5exit`,设置屏
推送区扩 4 toggle。`PushManager` 加 `PRECALL`/`D5EXIT` category(字面对齐后端
`apns.py`),`targetTab` 路由 PRECALL→盘中看板、D5EXIT→今日计划。`SentinelKind`
枚举补 `.precall`(盘前校准)/`.d5exit`(D5退出),`BoardEventRow` 色调分支跟进。

**验收证据**:双端 `xcodebuild` **BUILD SUCCEEDED**、iOS Simulator **TEST
SUCCEEDED**(45→**64 执行 / 9 skip 探活 / 0 失败**)。本地隔离库(`sqlite3 ATTACH`
真实 `data/neckline.db` 只读参考表 + 真实 Parquet backfill,同 4C/C 块姿势)起真
dev uvicorn 做端到端联调,**非 mock**:真实自选增删/pin/txt对账/`entry-suggestion`
请求闭环(`IntegrationSmokeTests` 新增 3 例 + 既有 6 例共 9 例全过);真实持仓造出
D5 场景、模拟器截图核对——持仓卡顶部醒目横幅「D5 时间退出日,按计划离场」+
D5/D5 徽标 + 距止损线 +12.36% + 回落止盈峰值展示;自选 tab 展示贵州茅台
(600519.SH)真实体检(98.7 分、纪律绿灯、买点已触发、真实 entrySpec.ma10=1217.11);
盘中看板展示 precall/d5exit 真实中文标签事件。**无重大偏离**:`FourPieceDisclosure`
从 `CandidateRow` 内联代码抽成共享组件供 `WatchlistRow` 复用,是 plan「F.2 可直接
复用 CandidateRow 四件套布局」原文的字面落实(而非另起一份重复代码)。**遗留**:
D5/9:26 盘前推送真机送达、txt 对账真实同花顺导出文件核对——均属 v1.1-H 活体验收
范围,本块（E/F/G）按 plan 边界不做。

**2026-07-21 · v1.1-H 服务端部署上云完成(服务端可验部分,🔴 高危 @builder-pro)**:v1.1 A–G 全量后端部署上生产 hz
ECS(`0.4.0-stage4A` → **`0.6.0-v1.1ABCD`**),**未发生回滚**。步骤逐一验证:①**迁移前在线一致性备份**
`data/neckline.db.bak-20260721-214141`(`sqlite3 .backup`,integrity ok,7 业务表行数与源逐表吻合);②`sync_code.sh`
rsync(DRY_RUN 先验:仅代码传、`data/`+secrets 零触碰、零删除)+ **补装 3 个缺失依赖**(`python-multipart`/`openpyxl`/
`et-xmlfile`,4C/4D/v1.1 引入但 4B 建 venv 时未装,阿里云镜像);③**幂等迁移四项随 `lifespan.init_schema` 重启执行**:
`watchlist` 表新建 + `app_settings` 加 `push_precall`/`push_d5exit` + `inquiry_pool` 加 `consumed_report_date` + `reports`
加 `watchlist_json`——迁移后 integrity ok、业务数据零丢失(positions=0/devices=1/reports=3 不变);④**服务端验收(公网真
token,过 nginx vhost + TLS)全过**:`GET /watchlist` 空数组 200、`GET /settings` 四推送开关齐、`GET /positions`
`{holdings:[]}`、`GET /report/latest` 老报告 `watchlistCheck=[]` 前向兼容不崩、自选 CRUD(加 600519.SH→删)往返 + 404、
无 token 401,台账验收后回归干净;⑤**哨兵盘前分支已挂载**(out-of-band 跑活 `_sentinel_loop` 证 "含 v1.1 盘前校准分支"
挂载 + 非交易时段 idle 待机无误触发),重启后 idle RSS ~82MB(与部署前持平,MemoryHigh=420M 余量足),`neckline.service`
active + 两 timer(16:05/16:35)未动。**部署踩坑留痕**(已写 `hz_info.md §12`):权限复原若 blanket `chown -R deploy:neckline`
会递归进 rsync 已排除的 `data/` 把 `neckline.db`/`parquet` 翻属主致 DB 只读——正解是复原只作用于代码路径不碰 `data/`;
缺 multipart/xlsx 依赖会在 import 期炸服务,重启前先 preflight import。
- **H 活体验收清单(留待用户 + 真实交易日,逐项)**:① **9:26 盘前校准真机**——下个交易日 9:25:30 哨兵盘前分支首跑 →
  9:26 汇总 APNs 到真机 + 看板明细四判定真数据核对;② **D5 时间退出真机**——造一只 buy_date 使今日恰 D5 的真实持仓 →
  9:2x D5 推送真机 + 持仓卡置顶;③ **自选体检出现在 16:35 真报告**——用户加真实自选票 → 当晚报告含自选体检节
  (评分/红绿灯/四件套,LLM 只审 changed∪pinned);④ **同花顺 txt 真实对账**——用户导出真实自选 txt → macOS 工作台
  差异 + 一键对齐 + 反向导出可被同花顺导入;⑤ **问询窗口修复实证**——16:35 后问询通过一票 → 次日 16:35 报告确实纳入
  (跨日不再掉缝);⑥ **四类推送开关真机往返**——设置屏四 toggle 真机 PUT 生效回读一致。

**2026-07-22 · 版本号双线规约生效 + K2 策略研究立项(纯研究)**:现役策略 v1 已改名 **K1**(系统版号走 v 字头 / 策略号
走 K 字头,双线解耦,§2.6;双端 DB `strategy_versions` 已 UPDATE)。用户两轮讨论定案 **K2 研究方向 = 情绪门控 × 主线票池
× 短线进出**,中心命题 = 检验阶段 1 被否的「追强势」在「**情绪进攻段 × 主线成员**」子域是否有正期望(全时段全市场平均结论
不等于该子域结论)。完整施工图见 **§五B「K2 策略研究 Plan」**(六研究件 B1 情绪三态阈值定量化〔兼答阶段 1 遗留⑤市场
择时正解〕→ B2 主线识别器 + 2026-07-22 校准 → B3 个股成员判定〔共动性代理为主、成分快照为辅降级〕→ B4 中心命题
walk-forward 消融〔对手 K1〕→ B5 止盈三方案赛马 + 高弹风险预算化 → B6 汇总裁决书 + K2 候选大脑落库不激活),每块
验收标准写死,**全程 @builder-pro**。**边界铁律**:纪律章程 §2.1 一字不动、生产系统零改动(研究跑本地)、K2 候选大脑
落库 `is_active=0` **不激活**(上岗须用户批准、过门后另行立项)。仓库现状:K2 研究未开工,施工图就位待 builder-pro。

---

## 五、当前版本 Plan(v1.2 · 三仓章程 + 预注册决策日志 + 归因闭环)

> **本节是 v1.2 唯一权威施工图**,每个交付项写具体行为、build 不用猜;每块末给验收标准(含活体验收)。阶段 0–4 完工路线图见「§五附」;v1.1 施工图已归档(见本节末指针)。
>
> **背景(2026-07-25 用户定案,方向不得改)**:系统重定位为**人机协作**——**用户 = 唯一决策人**,系统 = 情报收集 + 机械分析 + 纪律执行 + 归因审计,**机器不再替用户选股**(策略线三场战役判死的是「机器自动出信号」,见 `STRATEGY_LAB.md` 雷区地图)。v1.2 落地用户行为面配套 **三件事** + 两处真问题清理 + 一个部署 footgun 先行:① **仓位章程改三仓制**(注意力约束「只做 3 仓」;单笔金额不定死,4 万只作违纪上限)+ 修「周复盘用今天章程重判历史周」的洗白洞;② **预注册决策日志**(下单前录六项,时间戳先于成交防结果污染;**审计件、非下单件**);③ **挂单未成交追踪**(检验「逆向选择:专接下坠、错过起飞」);④ 决策归因入 4D 周复盘(按论点标签统计胜率/盈亏比);⑤ 客户端双端录入 + 历史;⑥ 部署活体。**不新增策略、不改评分 / 涨跌停 / 板块领域规则、不新增推送类(仍四类)。**
>
> **铁律(承 §3.8 + v1.1,builder 逐条守)**:同码不重写;**单一事实源不漂移**(下列一律复用,禁在新文件抄字面量)——止损 `stop_pct` / 回落止盈 `take_profit_retrace` / hold 天数 `max_hold_days` / **单笔上限 `single_cap`(v1.2 激活后 = 4 万,语义已从「推荐值」变为「违纪判定上限」)** / **最多持仓 `max_positions`(v1.2 = 3)** / 敞口 `max_exposure_frac`(v1.2 = 满仓档 ≈1.0)全读现役策略 `neckline.strategy.brain.get_active().rule["config"]`(即 `MomentumConfig` 落库值,与 4D 对账同源);涨跌停幅度读 `data/limit_derived.py`;板块分类读 `data/board.py`(含 `sentinel/quotes.py:to_symbol`);总仓 12 万读 `config.total_capital`;实时源批量拉价走 `sentinel/quotes.py:get_quotes`(**关注池 ≤200 不放大**);**落盘一律走 `data/market_data.py:write_table_day`**(v1 类型漂移防线,勿绕开自己 `write_parquet`);带联网搜索的 LLM 读超时守 **90s**;LLM 缺 key 全链路优雅降级不崩。
>
> **两条本版硬约束**:① **系统永不自动下单 / 撤单 / 改止损**(§3.8);**决策日志是审计件、不是下单件**——录入决策日志绝不触发任何下单动作,仅落库供事后归因。② **决策日志强制度 = 软约束**(用户拍板):允许无决策日志补录开仓,周复盘把「无决策日志的开仓」计为纪律项统计出来,**不做硬阻断**(硬阻断会逼出假日志)。
>
> **分块序列**:v1.2-0 sync_code.sh footgun 修复(先行)→ v1.2-A 仓位章程三仓制 + 历史洗白修复(🔴)→ v1.2-B 决策日志后端 → v1.2-C 挂单未成交追踪 → v1.2-D 决策归因入 4D 周复盘 → **v1.2-D2 问询台选股域漂移清理(独立小清理,与 A–D 并行、无依赖)** →(客户端)v1.2-E 双端录入 + 归因表 →(部署)v1.2-F 上云 + schema 迁移 + 活体验收。**🔴 高危区(点名 @builder-pro):v1.2-A(碰纪律章程 + 大脑激活)、v1.2-F(部署 + schema 迁移 + 哨兵/报告 timer 影响核查)**;其余 @builder。
>
> **推送白名单不变(仍四类)**:① 16:35 报告就绪、② 退潮红色刹车、③ 9:26 盘前校准汇总、④ D5 时间退出。**v1.2 不新增推送类(决策日志不推送)。**
>
> **新增 SQLite 表 / 列(`neckline/db.py`,均 `CREATE TABLE IF NOT EXISTS` / `_migrate_columns` 幂等迁移,改 schema 前 `cp -p neckline.db` 备份见 hz_info §12)**:`decision_log`(预注册决策日志)、`decision_pending_track`(挂单 N 日追踪);`strategy_versions` 加 `activated_at`(激活时间戳,历史洗白修复用)+ 一次性回填现役 K1;`reviews` 加 `strategy_version`(该周 governing 版本号)。

---

#### v1.2-0 · sync_code.sh footgun 修复(先行,@builder)

**根因**:`scripts/sync_code.sh` 尾部 heredoc **打印给人复制执行**的收尾命令里 `sudo chown -R deploy:neckline /opt/neckline` 会**递归进 rsync 已排除的 `data/`**,把生产库 `data/neckline.db`(应 `neckline:neckline` 600,服务 `User=neckline` 才写得动)翻成 `deploy` 属主 → DB 对服务只读 → 重启 `init_schema` 炸 `attempt to write a readonly database` → 服务反复 activating/502(**已复发两次**:v1.1-H、v1.1-H2,见 §七待办 + `hz_info.md §12`/§191)。

**修法(两处,builder 落地)**:
- **打印的收尾命令改 prune 版**:`chown` 与 `chmod 2770` **两条都要**排除 `data/`(prune 惯用式,如 `sudo find ${REMOTE_PATH} -path ${REMOTE_PATH}/data -prune -o -exec chown deploy:neckline {} +` / `... -o -type d -exec chmod 2770 {} +`)——`data/` 本就 `--exclude /data/` 未被 rsync 触碰、属主天然正确,收尾一律不碰它;`.env`/`*.p8` 回改属主/权限保持(rsync 已排除)。
- **脚本末尾加只读属主自检**:rsync 后脚本自动 `ssh ${USER_NAME}@${HOST} "stat -c '%U:%G' ${REMOTE_PATH}/data/neckline.db"`(远端 Linux GNU stat),结果**必须 `neckline:neckline`**;不符则红字报警(stderr)+ **非零退出(`exit 1`)**——把「data/ 属主被翻」在部署环节当场拦下,不再等服务 502 才发现。
- **运维事实同步**(builder,非 planner):更新 `~/Lino/hz_info.md §12`(footgun 已修、销 §191 挂账);§七待办条目销账已由本次 planner 标记「→ v1.2-0」。

**0 验收**:①`DRY_RUN=1 bash scripts/sync_code.sh` 打印的收尾命令肉眼核对 chown / chmod 两条均 prune `data/`;②在 ECS 故意把 `data/neckline.db` 临时 chown 成 `deploy`(或指一个属主不符的测试库)→ 跑脚本 → 自检红字 + 退出码非零;恢复属主后自检绿、退出码 0;③真部署一次(可与 v1.2-F 首验合并)后 `data/neckline.db` 属主仍 `neckline:neckline`、服务 restart 无 502。

---

#### v1.2-A · 仓位章程三仓制 + 历史洗白修复(🔴 碰纪律章程 + 大脑激活 @builder-pro)

- **A.1 落 v1.2 章程新行(先 `activate=False`,不激活)**:唯一源仍是现役 `strategy_versions.rule["config"]`,**禁止任何新文件抄常量**。新增落库脚本(如 `scripts/charter_v1_2.py`)**从 DB 读现役 K1 的 `rule["config"]` 复制一份**,**只改三个仓位字段**、其余逐字段原样:
  - `max_positions`: 5 → **3**
  - `single_cap`: 20000.0 → **40000.0**
  - `max_exposure_frac`: 0.60 → **1.0**(满仓档;`total_capital` 12 万 × 1.0 = 12 万 = 3×4 万,第三笔满档恰在边界不算越界)
  - **其余全部 = K1 逐字段相同**（`strength="none"` / `buypoint="pullback"` / `stop_pct=0.05` / `take_profit_retrace=0.05` / `max_hold_days=5` / `forbid_high_elasticity=True` / 主板 only …），靠「读 K1 config 复制」保证,**不手抄**（防漂移）。
  - `brain.save_version("v1.2", rule={"config": <复制并改三字段>, "lineage": "K1"}, changelog="策略内核血缘 = K1 未改一字,本行仅章程仓位字段修订(三仓制:max_positions 5→3 / single_cap 2万→4万〔违纪判定上限,非推荐值〕/ max_exposure_frac 0.6→1.0);系统 v 字头章程修订,不占 K 命名空间。", activate=False)`。**K1 行原样保留、不覆盖、`is_active` 仍在 K1**(保住「实盘表现按版本归因」)。version = **`v1.2`**(系统版本号)——**绝不碰 K 字头**。
  - **db.py 注释同步**(builder 顺带,非 planner):`strategy_versions.version` 现注释「策略版本号,K 字头整数」→ 补一句「章程修订走系统 v 字头(如 v1.2:config 承 K 血缘、仅改仓位字段)」。

- **A.2 切换器脚本(前置硬校验 + diff + `--confirm`)**:新增 `scripts/activate_charter.py`(或复用 A.1 脚本加 `--activate` 子命令):
  - **前置硬校验**:`positions` 表**无 `status='open'` 行**(用户已清仓)。有 open 持仓 → **拒绝激活 + 打印待清仓清单 + 非零退出**(生效时机铁律:清仓后才切)。
  - **打印 old→new 逐字段 diff**:现役 config 与 v1.2 config 全字段对照,高亮变的三个。
  - **必须 `--confirm` 才写库**:无 `--confirm` 只 dry-run 打印 diff、不写库;带 `--confirm` 才 `brain.activate_version("v1.2")`(见 A.3)。
  - **不做 API 端点**:策略大脑激活**绝不暴露给客户端**(§3.8 系统内核永不被客户端改),只走命令行脚本 + 用户在 ECS 手动跑。

- **A.3 激活时间戳 + 历史洗白修复(核心工程)**:此前 `review/reconcile.py::run_weekly_review` **一次性** `brain.get_active()` 应用到所有周(见该函数第一段 + `check_single_cap` / `check_position_count_and_exposure` 调用点)——单笔上限 2 万→4 万后**重跑历史周,当初超限的违纪会凭空消失 = 洗白历史**。修法:
  - `strategy_versions` 加 `activated_at TEXT`(幂等 `_migrate_columns`,NULL 默认;现无此列)。
  - `brain` 新增 `activate_version(version, db_path)`:置该版本 `is_active=1` + `activated_at=now()`、其余 `is_active=0`(切换器 A.2 调用它);`save_version(activate=True)` 同步 stamp `activated_at`(向后兼容,不破坏既有调用)。
  - **一次性回填**:`_migrate_columns` 加列后,对**当前唯一现役且 `activated_at IS NULL`** 的版本(=K1)回填 `activated_at = created_at`(K1 的 `created_at`=2026-07-20,是合理激活代理);幂等(只碰 `is_active=1 AND activated_at IS NULL` 的行,重跑不变);K2(`is_active=0`)保持 NULL(从未激活,正确)。
  - `reviews` 加 `strategy_version TEXT`(幂等迁移);`review/store.py::save_weekly_review` 按周写入该周 governing 版本号。
  - `run_weekly_review` 改「按周取当时现役」:新增 `brain.config_active_at(ref_date)`(或 reconcile 内 helper)——取所有 `activated_at IS NOT NULL` 的版本按 `activated_at` 升序;`candidates = [v for v if v.activated_at <= ref_date]`;`governing = candidates[-1] if candidates else (stamped[0] if 有任何 stamped else get_active())`。每个 ISO 周以 **`week_end`** 为 ref 解析 governing config,判该周止损 / 仓位 / 禁买。
  - **老数据无 `activated_at` 兜底语义(写死)**:若整表**无任何** `activated_at`(纯 legacy,如无 `is_active` 行的隔离测试库)→ `config_active_at` 退回 `get_active()` = **与 v1.2 之前旧行为完全一致**(当前现役判全部周,不臆造历史);生产因一次性回填保证 K1 有 `activated_at`,永远走时间线解析、不落此兜底。
  - **已知简化(诚实标注)**:governing 按**周粒度**(ref = `week_end`)解析;激活恰落某周中时,该周整体按 `week_end` 的 config 判——接受(激活罕见且 staged 在清仓后,无跨边界持仓 / 成交)。
  - **效果**:`single_cap` 2 万→4 万后重跑历史周,激活日之前的周仍用 K1(2 万)判,**当初超限违纪不被洗白**。

- **A.4 §2.1 章程文本同步**:PROJECT_PLAN §2.1 第 3 条已由本次 planner 改为三仓制(带 staged 生效说明);§五铁律 `single_cap` 语义已同步(见本节 intro)。**v1.1 §五 里「single_cap(=2 万)」字面留在归档件不动**(历史留痕)。

- **A.5 生效时机 = staged 两步(builder 不许直接激活)**:
  - **步骤 1(本次 builder 做)**:A.1 落 `v1.2` 行 `activate=False` + A.2 切换器脚本就位 + A.3 洗白修复代码 / 迁移就位。**`is_active` 仍在 K1,生产行为零变化。**
  - **步骤 2(等用户)**:用户清掉现有持仓 + 明确确认后,**在 ECS 权威库 `/opt/neckline/data/neckline.db`(600 `neckline:neckline`)上以能写该库的身份**(服务 `User=neckline`,即 `sudo -u neckline .venv/bin/python scripts/activate_charter.py --confirm`)跑切换器 → `is_active` 从 K1 移到 v1.2。**本地开发库可各自落 v1.2 行供本地测试,激活与否不影响生产**(客户端不读 `strategy_versions`,只读 `report.strategyVersion` 字符串;生产切换只在 ECS 权威库做)。
  - **⚠ 跨线影响(必须显式标注,planner 不改 STRATEGY_LAB)**:激活后 `is_active=1` 从 K1 移到 v1.2,`STRATEGY_LAB.md §一`「现役 = K1」的表述会变得不准确——**系统线无权改策略线文件**,请**用户在策略线会话同步一句**(现役 config = v1.2 章程行,内核血缘仍 K1)。

**A 验收**:①单测——`config_active_at` 时间线解析(激活前的周取旧版本、激活后取新版本、无 stamped 退回 `get_active`、多版本升序边界);**洗白反例锁死**:造「历史周有一笔 3 万买入(K1 下 >2 万违纪)+ 激活 v1.2 后重跑」→ 该周仍报违纪(**不被 4 万上限洗白**);`activate_version` stamp + 唯一现役断言;`reviews.strategy_version` 按周落库正确;`save_version(activate=True)` 回填 `activated_at`。②切换器脚本:有 open 持仓时拒绝激活 + 非零退出;无持仓时打印 diff、无 `--confirm` 不写库、带 `--confirm` 才激活。③迁移:`strategy_versions` 加 `activated_at` + K1 回填、`reviews` 加 `strategy_version`,生产 DB 副本验证 integrity ok + 重跑不炸。④pytest 零回归。⑤**staged 验收(留 v1.2-F / 用户)**:步骤 1 部署后 `is_active` 仍 K1、`/positions/entry-suggestion` 仍按 K1(2 万)区间;用户清仓 + 确认后跑切换器 → `is_active=v1.2`、entry-suggestion 改按 4 万区间、下一份周复盘用 v1.2 判激活后的周 / K1 判之前的周。**高危,建议完工后叫一次 review(用户定)。**

---

#### v1.2-B · 预注册决策日志后端(@builder)

- **B.1 新表 `decision_log`(幂等建表)**:
  - 字段:`id`(PK AUTOINCREMENT)、`ts_code`、`name`、**`created_at`(服务端生成的预注册时间戳,客户端不许传)**、六项预注册字段〔`why_buy`(①为什么买)/ `why_entry_price`(②为什么这个入场价)/ `target_price`(③目标价,REAL)/ `exit_low`+`exit_high`(④离场价格区间,REAL)/ `thesis_tags`(⑤论点标签,枚举码 JSON 数组 TEXT)/ `invalidation`(⑥证伪条件,TEXT)〕、`planned_price`(计划入场价 REAL)、`planned_qty`(计划股数 INTEGER)、`status`(pending / filled / cancelled / expired,默认 pending)、`position_id`(成交后回填,关联 `positions.id`)、`revision_of`(修订链根 id,NULL=首版)、`updated_at`。
  - **六项落库后不可编辑**(防事后被结果污染,与研究铁律「预注册先行」同原理):**无任何 UPDATE 六字段的路径**;可变的只有 `status` / `position_id`(审计结果关联,非六项之一)。改动 = 新增修订行(`revision_of` 指首版),**归因永远只认首版**(见 v1.2-D 匹配取 `revision_of IS NULL` 的根行)。
  - **论点标签枚举(服务端码 + 客户端展示层换算,沿 `CandidateOut.board` / `boardLabel` 先例,见 CLAUDE.md 阶段4C 坑)**:服务端码 `THEME` / `SENTIMENT_CYCLE` / `CAPITAL_FLOW` / `TECH_PATTERN` / `NEWS`;客户端展示 `题材主线` / `情绪周期位` / `资金流向` / `技术形态` / `消息`(未识别码原样透传)。服务端 schema `Literal` 白名单校验入参标签码(非法码 422)。

- **B.2 端点(契约写死,供 v1.2-E 对照;鉴权沿 `require_token`,前缀 `/api/v1`)**:
  - `POST /decisions`(**预注册**)body `{code, name?, whyBuy, whyEntryPrice, targetPrice?, exitLow?, exitHigh?, thesisTags:[码], invalidation, plannedPrice?, plannedQty?}` → 服务端 stamp `created_at`(**忽略客户端任何 createdAt 入参**)+ `status="pending"` → `DecisionOut`。
  - `GET /decisions?status=&code=&from=&to=` → `{items:[DecisionOut]}`(客户端历史 + macOS 归因表;默认返全部,可按 status / code / 日期过滤)。
  - `POST /decisions/{id}/link` body `{positionId}` → `status="filled"` + `position_id` 回填 → `OkOut`(成交后一键关联;id 不存在 404 `reason="not_found"`)。
  - `POST /decisions/{id}/cancel` → `status="cancelled"` → `OkOut`(用户放弃该计划;不存在 404)。
  - `POST /decisions/{id}/revise` body `{同六项 + plannedPrice? plannedQty?}` → 新增一行 `revision_of=<该 id 的链根>`、`status` 置 pending → `DecisionOut{新 id}`(改动只新增修订行,不改旧行)。
  - **无「改六项」端点**(不可编辑硬约束的落点)。

- **B.3 store 层(`neckline/api/stores.py` 扩,或新 `neckline/decision_log.py`)**:`create_decision`(stamp created_at)/ `list_decisions`(过滤)/ `link_decision` / `cancel_decision` / `revise_decision` / `get_decision`。写入只经这些函数,与 `sentinel/positions.py` 台账同款薄封装姿势;**审计件非下单件**——本层绝无任何下单 / 拉价副作用。

**B 验收**:①单测——`created_at` 服务端生成(客户端传 createdAt 被忽略);六项无 UPDATE 路径(revise 新增行、首版行原地不变、`revision_of` 指链根);`thesisTags` 非法码 422、合法码往返;link 置 filled + position_id、cancel 置 cancelled、id 不存在 404;list 过滤 status / code / 日期。②契约形状与「v1.2 客户端契约清单」一致。③pytest 零回归。

---

#### v1.2-C · 挂单未成交追踪(@builder)

**目的**:用户习惯挂低价等回踩,记录**未成交计划**(`decision_log.status='pending'`)的后续 N 日走势,检验用户「逆向选择:专接下坠、错过起飞」假设(飞了 = 错过 / 跌了 = 躲过)。

- **C.1 追踪窗口 N 写死**:`N = 5 个交易日`(常量 `DECISION_PENDING_TRACK_DAYS = 5`,单一源;与 `hold=5` / D5 时间退出 horizon 同口径,覆盖短线 1–2 日打法的相关观察窗)。
- **C.2 新表 `decision_pending_track`(幂等建表)**:`decision_id`、`trade_date`(追踪快照日)、`d_offset`(距 `created_at` 后第几个交易日 1..N)、`close`(当日 EOD 收盘,前复权口径同面板)、`ret_from_plan`(相对 `planned_price` 的累计收益)、`recorded_at`,PK`(decision_id, trade_date)`(同日重跑幂等覆盖)。
- **C.3 折进 16:35 报告管线**:`report/pipeline.py::build_report` 末尾(`if save:` 块内、报告落库后)新增一步 `track_pending_decisions(trade_date, db_path=...)`——对每只 `status='pending'` 且 `created_at` 在 N 交易日内的决策:从**当日 EOD 面板复用既有日线数据访问层**(`build_research_panel(trade_date, trade_date)` / `data/market_data` 读,**不新拉数据源**)取 `close`,算 `ret_from_plan`,落 `decision_pending_track` 一行;到第 N 交易日:`status` pending→**expired**(未成交自动过期,追踪定格)。**落盘若涉及 Parquet 一律走 `write_table_day`;本追踪落 SQLite,不写 Parquet。** 复用 16:35 报告 timer 的独立瞬态进程(跑完释放内存,对紧内存友好)。

**C 验收**:①单测——pending 决策登记后逐交易日落 track 行、`d_offset` 从 1 递增、`ret_from_plan` 相对 `planned_price` 正确、第 N 日置 expired、已 filled / cancelled 的决策不追踪、同日重跑幂等覆盖;②端到端(隔离库):造一只 pending 决策 → 连跑 N 个交易日 `report.py` → track 表 N 行 + 决策 expired;③零新拉数据源(复用面板)、`write_table_day` 未被绕开。

---

#### v1.2-D · 决策归因入 4D 周复盘(@builder)

**核心工程难点(写清)**:4D 现链路是**券商交割单 → `RawTrade` → FIFO `RoundTrip`**(`review/reconcile.py`),决策日志挂在 **`positions` 台账**上(via `decision_log.position_id`)——**两本账要按 `(ts_code, buy_date)` 邻近匹配接回**:`decision_log`→`position_id`→`positions{ts_code, buy_date}`,再与 `RoundTrip{ts_code, buy_date}` 对齐(精确 ts_code + buy_date,±1 交易日容差兜边界)。

- **D.1 归因聚合(按论点标签)**:`run_weekly_review` 产出的每个 `WeeklyReview` 新增归因节——对本周 `closed_round_trips`,匹配决策日志**首版**(`revision_of IS NULL` 的根行)的 `thesis_tags`,按标签码聚合胜率 / 盈亏比(复用 `compute_weekly_stats` 的 `win_rate` / `profit_loss_ratio` 口径,分标签算)。一笔可挂多标签 → 各标签分别计入。
- **D.2 「无决策日志的开仓」= 纪律项统计(软约束落点,不静默丢)**:匹配不上任何决策日志的本周开仓(closed round trip 或 buy)→ **显式报「无决策日志的开仓 N 笔」**并列具体票,当**纪律项**进 `discipline_violations`(或独立 `no_decision_log_count` 字段)。**决策 4 软约束就落在这里**——不硬阻断补录,但周复盘把无日志开仓统计出来。
- **D.3 落库 + 契约**:归因结果 + 无日志计数进 `weekly_review_dict()`(API 响应 = `reviews.result_json` 同一形状),新增 `thesisAttribution`(按标签胜率 / 盈亏比)+ `noDecisionLogTrades`(无日志开仓清单)字段。macOS 周复盘工作台展示(见 v1.2-E)。

**D 验收**:①单测——决策日志与 RoundTrip 按 (ts_code, buy_date) 匹配(精确 + ±1 日容差);多标签分别计入胜率 / 盈亏比;无匹配决策的开仓进「无决策日志开仓」纪律项、**不静默丢**;首版认定(有修订行时归因取 `revision_of IS NULL` 根行的标签,不认修订标签)。②契约形状与「v1.2 客户端契约清单」一致。③pytest 零回归。

---

#### v1.2-D2 · 问询台选股域漂移清理(独立小清理,@builder)

**根因(4A 遗留,v1.1-C/D 施工时发现,原只记 §四正文、未进 Backlog)**:`api/inquiry.py::run_deterministic_checks` **手写重复了选股域逻辑**(ST / 北交所 / 股价<2 元 / 20 日均额<2000 万 / 无 MA20 逐条 Python 重刻,见该函数「硬性纪律核对」段),会与报告口径漂移(且当初未核对两条禁买过滤 P4/P5)。

**修法(照 `report/watchlist_check.py::_discipline_checks` 的正确姿势,见 CLAUDE.md v1.1-C/D 节)**:
- **选股域四项揉成一条组合原因文案**(用 `~research.panel.base_universe_expr()` 整条求值,**不拆解、不重抄阈值**)——`base_universe_expr()` 内部已 AND 成单一布尔,拆开逐项手写会各自维护一份阈值、一改上游即漂移。
- **只有现役 config 可配的禁买过滤(P4 绿盘大阴线 / P5 距前高 / P6 次新 / 高弹题材)才逐项拆开**——它们本就按 `cfg.forbid_* is not None` / `cfg.forbid_*` 分支决定是否启用,拆开展示不产生新的数值维护点,与 `momentum.build_entry_mask` 的 if 分支一一对应。
- **落地** = 复用 `watchlist_check._discipline_checks(cfg)`(promote 成公开函数、或抽到共享模块,builder 定 host;两处 `run_deterministic_checks` 与 `watchlist_check` 共用同一份)。在问询单票的单行面板上求值这批谓词,`disqualifiers = [label for 命中项]`、`passes_discipline = not disqualifiers`,替换现手写段。
- **展示粒度取舍如实标注**:选股域从「逐项(ST / 北交所 / 价格 / 流动性 / MA20)」收敛为「一条组合原因」——刻意的、与 `watchlist_check` 一致的取舍(损一点粒度换零阈值漂移)。

**D2 验收**:①单测——问询台确定性核对与报告 `watchlist_check` 对同一票、同一日**同判**(同码一致,注入同一面板断言 disqualifiers 一致);选股域触发时收敛为一条原因、config 启用的禁买项仍逐项拆开;K1 现役(P4/P5=None)下与旧行为对同一批票裁决等价(仅原因文案粒度变);②pytest 零回归。

---

#### v1.2-E · 客户端双端:决策日志录入 + 关联 + 归因展示(@builder)

- **E.1 录入表单嵌进「已按计划买入」流程之前(建计划 → 录六项 → 成交后一键关联)**:今日计划 / 候选卡「已按计划买入」按钮点开的补录流程**之前**插入决策日志录入:
  - 表单 = 六项(①为什么买 ②为什么这个入场价 文本;③目标价 ④离场价格区间 数字;⑤**论点标签 picker**〔题材主线 / 情绪周期位 / 资金流向 / 技术形态 / 消息,多选,展示层码换算〕;⑥证伪条件 文本)+ 计划价 / 计划量 → `POST /decisions`(预注册,status=pending)。
  - 成交后:补录开仓 sheet(既有 `POST /positions`)提交后,**一键关联** `POST /decisions/{id}/link{positionId}`(或建仓时带 `decisionId` 自动关联,builder 择一,契约见清单)。放弃 → `POST /decisions/{id}/cancel`。
  - **不新增第六个 tab**(iOS 已 5 tab:今日计划 / 盘中看板 / 自选 / 问询台 / 设置)——录入走 sheet / 流程,历史与归因表放 macOS 工作台。
- **E.2 entry-suggestion 区间建议展示口径**:`/positions/entry-suggestion` 改为返**区间**(见清单 `EntrySuggestionOut` 新形状:`qtyLow`/`qtyHigh` 两个手数 + `capFloor`/`capCeil` 两端金额 + `stopLine`)。客户端补录预填 = 展示**下限档 / 上限档两档手数 + 对应金额**,让用户当场在区间内自定,**不替用户拍板单笔金额**(默认可预填任一档或留手填,builder 择一;文案标注「4 万 = 违纪上限、非推荐值」)。
- **E.3 macOS 归因工作台**:周复盘工作台新增决策归因区——按论点标签的胜率 / 盈亏比表(`thesisAttribution`)+ 「无决策日志开仓」清单(`noDecisionLogTrades`)+ 挂单未成交追踪历史(pending 决策的 N 日走势,读 `decision_pending_track`;若需端点补 `GET /decisions/{id}/track` 或并入 `GET /decisions`,builder 定并补进清单)。决策日志历史列表(`GET /decisions`)也放这里。**iOS 只做录入 + 关联,不做归因大表**(桌面场景)。
- **E.4 客户端 Models / APIClient**:加 `DecisionLog` DTO + `thesisTag` 枚举展示映射(码→中文,未识别透传,沿 `boardLabel` 先例)+ `createDecision` / `listDecisions` / `linkDecision` / `cancelDecision` / `reviseDecision` / `entrySuggestion`(改区间);decisions 404(`reason="not_found"`)接线时核对 `APIError.mapReason` 已有 `.notFound` case(v1.1-E/F 已加,见 CLAUDE.md),不必新增、只需核对映射到位。

**E 验收**:双端 `xcodebuild BUILD SUCCEEDED` + iOS Simulator TEST;录入表单六项 + 标签 picker 真实 `POST /decisions`、成交后 link 真实往返、cancel 往返;entry-suggestion 区间双档正确展示(隔离库真实数据截图核对,标注违纪上限);macOS 归因区渲染 `thesisAttribution` / 无日志开仓 / 挂单追踪真实数据;单测覆盖 `DecisionLog` 解码、标签码→中文映射、entry-suggestion 区间解码。绿涨红跌不变。**「不新增 tab」「录入嵌在补录流程之前」两条硬边界各验一次(截图 / 单测)。**

---

#### v1.2-F · 部署上云 + schema 迁移 + 活体验收(🔴 @builder-pro + 用户)

- **F.1 部署(用修好的 sync_code.sh 首验)**:`sync_code.sh`(**v1.2-0 已修 footgun**,首次用新版部署,验证收尾 prune + 只读属主自检)推后端;**schema 迁移前 `sqlite3 .backup` 备份**(照 v1.1-H 姿势:`data/neckline.db.bak-<date>`,integrity ok、业务表行数逐表吻合、原地保留 rsync 排除)。
- **F.2 幂等迁移随 `lifespan.init_schema` 重启执行**:新建 `decision_log` + `decision_pending_track` 表;`strategy_versions` 加 `activated_at` + 一次性回填 K1;`reviews` 加 `strategy_version`。迁移后 integrity ok + 业务数据零丢失(positions / devices / reports 行数不变)。
- **F.3 哨兵 / 报告 timer 影响核查**:v1.2-C 折进 16:35 报告管线新增 `track_pending_decisions`——核查报告 timer(`neckline-report.service` oneshot)跑完仍瞬态释放、峰值 RSS 不涨破 `MemoryHigh`;**哨兵 lifespan asyncio 任务零改动**(v1.2 不碰哨兵判定),重启后 idle RSS 与部署前持平、盘前分支非交易时段不误触发。
- **F.4 章程激活 = staged 步骤 2(等用户,见 v1.2-A.5)**:部署后 `is_active` 仍 K1、生产行为零变化;用户清仓 + 确认后在 ECS 权威库跑切换器 `--confirm` 才切 v1.2。
- **F.5 运维留痕**:ECS 动作后更新 `~/Lino/hz_info.md`(新表 / 列、footgun 已修销 §191、章程 staged 激活现状);变更日志记一行。

**F 验收(活体,逐项写死)**:
1. **`sync_code.sh` 新版首部署**:收尾 prune 生效、部署后 `data/neckline.db` 属主仍 `neckline:neckline`、只读属主自检绿、服务 restart 无 502。
2. **迁移**:两新表建成 + `strategy_versions.activated_at`(K1 回填有值)+ `reviews.strategy_version` 三迁移在生产库 integrity ok、业务零丢失。
3. **公网真 token 验收**:`POST /decisions` 预注册往返(createdAt 服务端生成)、`GET /decisions` 读回、link / cancel、`/positions/entry-suggestion` 返区间双档;老端点(report / positions / watchlist / settings)前向兼容不崩、无 token 401。
4. **报告 timer 首跑含 `track_pending_decisions`** 不炸、内存无压力;哨兵 idle RSS 持平。
5. **章程 staged 激活实证(用户)**:清仓 + 确认后跑切换器 → `is_active=v1.2`、entry-suggestion 改 4 万区间、下一份周复盘用 v1.2 判激活后周 / K1 判之前周(**洗白修复实证**)。
6. **⚠ 碰纪律章程 + 金额区间,建议完工后叫一次 `review`(用户定)。**

---

#### v1.2 客户端契约清单(新端点 / 新字段,供 v1.2-E 对照)

- **决策日志(鉴权沿 `require_token`,契约见 `neckline/api/schemas.py`)**:
  - `POST /decisions` body `DecisionCreateIn{code, name?, whyBuy, whyEntryPrice, targetPrice?, exitLow?, exitHigh?, thesisTags:[码], invalidation, plannedPrice?, plannedQty?}`(**无 createdAt,服务端生成**)→ `DecisionOut`。
  - `GET /decisions?status=&code=&from=&to=` → `{items:[DecisionOut]}`。
  - `POST /decisions/{id}/link` body `{positionId}` → `OkOut`(不存在 404 `reason="not_found"`)。
  - `POST /decisions/{id}/cancel` → `OkOut`(不存在 404)。
  - `POST /decisions/{id}/revise` body `DecisionReviseIn{同六项 + plannedPrice? plannedQty?}` → `DecisionOut{新 id, revisionOf}`。
  - `DecisionOut` 字段:`id, code, name, createdAt, whyBuy, whyEntryPrice, targetPrice, exitLow, exitHigh, thesisTags:[码], invalidation, plannedPrice, plannedQty, status(pending/filled/cancelled/expired), positionId|null, revisionOf|null`。**`thesisTags` 是服务端枚举码,客户端展示层换算中文**(`THEME`→题材主线 / `SENTIMENT_CYCLE`→情绪周期位 / `CAPITAL_FLOW`→资金流向 / `TECH_PATTERN`→技术形态 / `NEWS`→消息,未识别透传,沿 `boardLabel` 先例)。
- **`EntrySuggestionOut` 改区间(替换 v1.1 的单 `qty`)**:`{ok, code, price, qtyLow, qtyHigh, capFloor, capCeil, stopLine}`——`qtyHigh = floor(single_cap/price/100)*100`(违纪上限对应手数)、`qtyLow = floor(single_cap*0.5/price/100)*100`(半仓保守下沿;**0.5 是纯展示层因子,住 `app.py` 一处、非领域常量,`single_cap` 仍是唯一领域源**)、`capCeil = single_cap`(违纪上限金额)、`capFloor = single_cap*0.5`、`stopLine = price×(1−stop_pct)`(读现役 config)。**客户端展示两档 + 标注「上限 = 违纪线、非推荐」,不替用户拍单笔金额。** v1.1-E 客户端读单 `qty` 预填的逻辑需改读区间。
- **周复盘 `weekly_review_dict()` 新增字段**:`thesisAttribution`(按论点标签码的胜率 / 盈亏比)、`noDecisionLogTrades`(无决策日志开仓清单)。macOS 归因区展示。(挂单追踪历史如需端点,builder 定 `GET /decisions/{id}/track` 或并入 `GET /decisions`,补进本清单。)

---

## 五-归档、v1.1 施工图(已完工上线 → archive)

> v1.1(SOP 补洞:盘前校准 tick / 持仓生命周期 D5 / 自选板块 + 体检 / 问询窗口修复)于 2026-07-21 全部完工上线(状态见 §四、变更日志见 §九)。**施工图全文已移 `archive/v1.1_施工图_20260721.md`**(照 §五B / §五C 留指针体例),正文不再复述。

---

## 五B、K2 策略研究(已否决归档 → 策略线)

> **策略研究已迁至策略研究中心权威文件 `STRATEGY_LAB.md`(2026-07-25 架构拆分)。**
> K2 施工图全文:`archive/K2_研究施工图_20260722.md`;结果:`research/k2_report.md`。
> 判决:中心命题否决,K2 落库不激活,K1 仍现役。

---

## 五C、K3 策略研究(已否决中止 → 策略线)

> **策略研究已迁至 `STRATEGY_LAB.md`(2026-07-25 架构拆分)。**
> K3 施工图全文:`archive/K3_研究施工图_20260725.md`;结果:`research/k3_report.md`。
> 判决:B2 四臂全灭、2026 生存门禁七臂尽墨,停止挖矿条款触发,K3 中止(未落库);B3/B5 残项转 STRATEGY_LAB Backlog。
---

## 五附、已完工路线图(阶段 0 → 4,研究先于产品 · 均已完工存档)

> 每个交付项写具体行为,build 不用猜。每阶段末给验收标准。(阶段 0–4 已全部完工上线,见 §四 与 §九;保留作施工留痕。)

### 阶段 0 · 数据层 + 回测引擎骨架

**目标**:全市场日线落地、日历补全、复权就位、回测框架跑通一个 dummy 策略。

- **0.1 脚手架**:建 `neckline/` 包结构(§3.9)、`.venv`、`requirements.txt`(polars、pyarrow、pandas、tushare、python-dotenv、pytest;版本钉死走阿里云镜像)。`config/` 读 `.env` 的 `TUSHARE_TOKEN` / `LLM_PROVIDER` / `LLM_API_KEY`(LLM 两项允许缺省,阶段 2 才用)+ 定义 `data/parquet/`、`data/neckline.db` 路径常量。
- **0.2 tushare_client**:从 LinoN 搬 `TushareResult` 永不抛异常封装 + `pro_api(token)` 直传;新增全市场批量拉取函数:按 `trade_date` 拉全市场 `daily / daily_basic / adj_factor / moneyflow_dc`,以及 `index_daily`(上证 / 深成 / 创业板指等)、`stock_basic`、`namechange`(ST 状态历史,自算涨跌停幅度用)、`trade_cal`。带限频退避(600 档限频 500 次/分钟)。
- **0.3 交易日历**:搬 LinoN `calendar` 包,用 `trade_cal` 拉 **2020-01-01 至今**全量落 SQLite;静态休市表比对告警。提供 `is_trading_day / prev_trading_day / next_trading_day / trading_days_between`。
- **0.4 历史落地脚本**:`scripts/backfill.py` —— 全市场 2020-01-01 至今 `daily / daily_basic / adj_factor / index_daily / moneyflow_dc` 落 Parquet 按年分区(`data/parquet/<表>/year=YYYY/`);断点续跑、限频退避。`scripts/daily_update.py` —— 每交易日盘后拉当日追加(增量)。
- **0.4b 涨跌停衍生表 `limit_derived`(自算,替代不可用的 `limit_list`)**:从 `daily`(o/h/l/c/pre_close)+ `stock_basic`(板块 / `list_date`)+ `namechange`(ST 历史)推导——涨停价 = `round(pre_close×(1+幅度),2)`,幅度:主板 10% / 创业板·科创 20% / ST 5% / 北交所 30%(创业板科创 2020-08 注册制改 20% 的历史分界要处理);`close==涨停价` 判涨停、`high==涨停价 且 close<涨停价` 判炸板、连续涨停计连板;上市首日等无涨跌幅限制日剔除。落 Parquet,backfill 与 daily_update 各接一步。**验收:抽样若干历史交易日,与公开涨停家数统计对照误差 ≤ 个位数。**
- **0.5 复权**:`adj_factor` 落地 + 前复权函数 `qfq(close, adj_factor, latest_adj_factor)`;回测统一用前复权价。
- **0.6 数据访问层**:polars `scan_parquet` 惰性接口——`get_market_slice(trade_date)`(全市场当日横截面)、`get_stock_history(code, start, end)`;**强制前视截断**:任何查询不得返回 > 请求日 的数据。
- **0.7 回测引擎骨架**:事件驱动逐日循环。`Strategy` 接口(`generate_signals(context)`);`Portfolio`(持仓 / 现金 / T+1 锁定);`Broker` 撮合并落实约束——**涨停买不进、跌停卖不出、停牌跳过、滑点 + 手续费模型**(佣金 + 印花税 + 过户费)。跑通 dummy 策略(如「每日等权买入 N 只、持有 K 日」)。产出回测报告:净值曲线、胜率、盈亏比、最大回撤、年化、盈利因子。
- **0.8 walk-forward 骨架**:时间切分器(样本内调参窗 / 样本外验证窗滚动),供阶段 1 用。

**验收标准**:全市场 2020-至今日线落 Parquet 且行数与 TuShare 对齐;随机抽 3 票前复权价与东财 App 一致;dummy 策略跑完六年回测输出净值曲线 + 完整统计,单次全回测 < 数分钟;前视偏差 / T+1 / 涨跌停 / 停牌各有单测通过。

### 阶段 1 · 策略研究(纯研究,无产品)

**目标**:§6 参数清单逐项过堂,产出「策略规则 v1」大脑(版本化)。

- **1.1 母战法信号编码**:两类买点(强势票回调低吸 + 平台放量突破)用价量结构编码为可回测信号;**强势定义三候选赛马**(涨停基因 / 20 日涨幅分位 / 量价结构),各自回测选胜。
- **1.2 禁买规则回测**:绿盘大阴线禁买线(初值 ≤-3%)、距 20 日高点阈值(初值 -15%)、票型黑名单(次新 / 高弹题材);每项做 with/without 对照。
- **1.3 止损验证**:-5% 固定止损(已定,券商执行)对比无止损 / 不同止损线的期望值(印证归因结论)。
- **1.4 止盈研究**:回落止盈参数(从高点回落 X% 或跌破 VWAP / MA)+ 时间退出天数(2/3/4/5 日赛马,印证「4–7 自然日打平」发现)。
- **1.5 市场过滤器生死**:上证 MA20 过滤 vs 情绪仪表盘 vs 二者叠加,样本外对照(印证「MA20 下方 28 笔 -1.95 万」)。
- **1.6 板块年龄因子**:启动第几天 / 涨停梯队扩缩 / 龙头掉队编码,量化其对净值的贡献,定加权曲线(早期加分、4–5 天后降权)。
- **1.7 冷却期**:同票割肉后冷却天数(初值 10 交易日)赛马。
- **1.8 仓位纪律回测**:单笔 ≤2 万 / 最多 5 只 / 总敞口 ≤60% 对期望的影响(印证「越亏越重仓」);「次周单笔减半」挂起项做验证性回测,供用户决策。
- **1.9 汇总**:产出「策略规则 v1」——所有采纳参数写死、带版本号 + 变更日志,落 `strategy` 大脑(SQLite 版本表);walk-forward 样本外跑赢 dummy 基准且盈利因子 > 1。

**验收标准**:§6 参数清单每项有回测结论(采纳 / 否决 / 待观察 + 推荐值 + 样本外显著性);规则 v1 在样本外(如 2025–2026)跑赢 dummy 基准、盈利因子 > 1;大脑 v1 版本化落地。

### 阶段 2 · 盘后报告管线(每交易日 16:00,CLI/文件)

**目标**:16:00 报告落地,同码复用阶段 1 信号。

- **2.1 情绪仪表盘**:从 `limit_derived`(0.4b 自算衍生表)+ `daily` 算涨停 / 跌停家数、连板最高高度、炸板率、昨日涨停今日平均溢价 → 输出明日仓位额度(满额 / 半额 / 休息)三态。
- **2.2 强势板块**:阶段 1 定的板块年龄因子 + 加权不圈死。
- **2.3 候选评分管线**:同一套信号代码喂今日 → 20 只带评分,每只四件套(买点 / 止损 / 目标 / **证伪条件**,证伪条件用价量结构写死供盘中哨兵消费)。
- **2.4 LLM 逻辑审判**:前 10 只深判(判催化持续性,**一票否决**);后 10 只给分数 + 形态标签。输出自由对话体(§2.7),降级链继承 LinoN。**信息源(2026-07-19 用户拍板)**:结构化数据(概念板块和成分 + 龙虎榜 + 板块年龄 + 价量结构,均在 600 档权限内)+ **LLM 联网搜索**(消息面/催化,TuShare 新闻接口不购)。落地注意:① 供应商 = GLM 5.2 / Kimi K3 可切换(§3.4),两家 API 原生带联网搜索工具,直接用;② **每次审判用到的搜索结果全文落库存档**(SQLite),供事后审计「当时为何否决」+ 自建历史新闻快照;③ **LLM 审判层是回测盲区**(搜索回不到历史时点、且被后见之明污染),其价值不进回测,单独用实盘事后归因考核(审判否决 vs 放行的候选,3 日收益对照,candidate_outcomes 思路)。
- **2.5 报告落地**:`scripts/report.py [trade_date]` 生成 markdown / HTML,含情绪仪表盘 + 仓位额度 + 强势板块 + 候选 20 四件套;落 SQLite 存档。
- **2.6 历史回放**:报告管线能对历史任一交易日回放(复用回测数据层),验证逻辑一致性。

**验收标准**:对最近 N 个交易日能生成完整 16:00 报告;候选评分与阶段 1 回测信号同码一致;前 10 只 LLM 返合法自由文本且否决权生效;情绪仪表盘输出三态仓位额度;报告可对历史日回放。

### 阶段 3 · 盘中哨兵(轮询实时源,1 分钟一拍)

**目标**:四哨兵落地,只执行前晚计划。

- **3.1 实时源层**:继承 LinoN realtime(新浪主 / 腾讯备,Referer/GBK/单位归一)+ intraday VWAP;批量拉候选 + 持仓票,1 分钟轮询,归一 `Quote`(volume=手、amount=元)。
- **3.2 买点哨兵**:候选触达预设买点 + 确认(量能折算 / 站稳 VWAP)→ 推送。
- **3.3 退潮哨兵**:盘中情绪恶化(炸板率飙升 / 跌停扩大 / 主线跳水)→ 红色刹车「今日计划作废、禁开新仓」。
- **3.4 持仓哨兵**:持仓放量跳水逼近止损 / 触达目标 / 板块跳水预警。
- **3.5 证伪哨兵**:候选分时走坏(低开不回 / 全天 VWAP 下方 / 量能异常 / 梯队瓦解,证伪条件读阶段 2 前晚写死值)→ 推「剔除勿进」;**只用价量结构**。
- **3.6 推送通道**:先 CLI / 本地通知验证逻辑;正式推送载体待 §8 拍板(推荐 Bark)。
- **原则守护**:代码 + 单测强制——盘中不产新决策、永不推荐新票。

**验收标准**:交易时段实盘轮询,四哨兵各能触发对应推送(**盘中真验**,补上 LinoN「实时源盘中未真验」欠账);证伪哨兵仅用价量;退潮刹车能触发;单测断言无新票推荐路径。

### 阶段 4 · 客户端 + 云端化 + 问询台 + 周复盘(当前施工版本)

**目标**:把阶段 0–3 已跑通的研究/报告/哨兵引擎**产品化**——FastAPI 服务化 + 上 hz ECS 常驻 + SwiftUI 双端 App(四板块)+ 问询台 + 周复盘对账闭环。**铁律:同码不重写**——报告 / 候选评分 / 四哨兵 / 涨跌停 / 板块分类全部复用阶段 0–3 现有模块(§2.4/CLAUDE.md 单一事实源),阶段 4 只加「服务外壳 + 客户端 + 云端化 + 对账」四层新代码,**不得在 API 层或客户端重抄一份领域规则**。

> **拍板前提(2026-07-20 用户,不得改动方向)**:① 客户端 = 方案 A(SwiftUI 双端 + APNs,新 App / 新 Bundle ID `top.linotsai.neckline`,绿涨红跌);② 后端复用 LinoN 基建(hz ECS / nginx `ln.linotsai.top` / APNs `.p8` 账号级密钥,§3.6);③ 哨兵与后端跑云上,内存硬约束下数据管线分工**以实测门禁定**;④ APNs 只推「16:00 报告」+「退潮刹车」两类,其余哨兵事件只进看板(§2.4);⑤ App 四板块 + macOS 周复盘工作台。

**施工序列(每块末给验收标准)**:4A 后端 API → 4B 云端化 + 部署 → 4C 客户端双端 → 4D 周复盘工作台 → 4E 端到端联调 + APNs 真机 + 阶段 3 欠账实盘校正。**🔴 高危区(点名 @builder-pro):鉴权(4A.1)、LLM key 服务端存取(4A.5)、APNs(4B.5 / 4C.4)、部署脚本(4B.2)**;其余 @builder。**新增依赖**(requirements.txt):`fastapi` / `uvicorn[standard]` / `python-multipart`(4D 文件上传)/ `openpyxl`(4D 解析 xlsx)/ `PyJWT` + `cryptography`(4B APNs JWT ES256,版本参照 LinoN 钉死);均走阿里云镜像。**新增 SQLite 表**(`neckline/db.py`,均 `CREATE TABLE IF NOT EXISTS` 幂等):`app_settings`(单行:llm_provider/llm_api_key/push_report/push_retreat/review_col_map JSON) · `devices`(APNs token) · `inquiry_pool`(问询台海选票,`UNIQUE(trade_date,ts_code)`) · `reviews`(周复盘,week PK)。

---

#### 4A · 后端 API 服务化(FastAPI 脊椎;新包 `neckline/api/`)

沿 LinoN `backend/app/api/` 姿势:`api/app.py`(FastAPI + lifespan)、`api/deps.py`(`require_token`)、`api/schemas.py`(pydantic 出入参)。所有端点前缀 `/api/v1`,除 `health` 外全部 Bearer 鉴权。

- **4A.1 鉴权 + 应用骨架(🔴 @builder-pro)**:`require_token` 比对 `.env` 的 `API_TOKEN`(`Authorization: Bearer`,`hmac.compare_digest`,照搬 LinoN `deps.py`);startup fail-fast `len(API_TOKEN)≥16`。`GET /api/v1/health` → `{"status":"ok"}`(免鉴权,供 nginx / 客户端自检)。为 Neckline 生成一枚新随机 `API_TOKEN`(≥32 字符),写 ECS `.env`(gitignored)+ 客户端(§4C.1)。
- **4A.2 报告端点**(复用 `neckline/report/store.py` 读 `reports` 表,不重算):
  - `GET /api/v1/report/latest` → 最新盘后报告:`{tradeDate, sentiment{涨停/跌停家数, 连板最高, 炸板率, 昨涨停今日均溢价, quota∈满额/半额/休息}, sectors[强势板块 + 年龄], candidates[20]{rank, code, name, score, 四件套{buyPoint, stop(-5%), target, invalidation}, llmJudgment?(前 10 只有), formTags}}`。
  - `GET /api/v1/report?date=YYYYMMDD` → 指定交易日报告(历史回放,复用 §2.3 回放能力)。**客户端务必走 makeURL 免 `?` 编码坑(见 4C.2)。**
- **4A.3 盘中看板端点**:`GET /api/v1/board` → `{tradeDate, asof, retreatBrake{active:bool, reason?}, events[]{sentinel∈买点/证伪/持仓, code, name, verdict(判决文案), ts}}`。数据源 = 当日 `sentinel_events` 表聚合(哨兵引擎已落库,看板只读)。**这是拍板 4「其余事件只进看板」的落点**——买点/证伪/持仓事件在此可见,不进 APNs。
- **4A.4 持仓端点**(复用阶段 3 `scripts/positions.py` 台账逻辑,提炼为 `neckline/sentinel/positions.py` 的 service 复用,**不重写台账**):
  - `GET /api/v1/positions` → `{holdings[]{id, code, name, buyPrice, qty, entryReason, buyDate, price(哨兵最近一拍 / EOD 兜底), status, stopLine(=buy×0.95 派生), stopOrderChecked(用户自证)}}`。**条件单对账状态**:券商条件单在系统外不可见,今日计划板块只做「派生止损线展示 + 提醒每笔挂 -5% 条件单」的自证 checklist(真对账在 4D 周复盘用交割单做)。
  - `POST /api/v1/positions`(开仓录入)`{code,name,buy_price,qty,entry_reason}` → `{ok, position_id, stop_line}`;`POST /api/v1/positions/{id}/close`(清仓)`{sell_price, sell_time?}` → `{ok}`。**系统永不自动下单**(§3.8),此处只录台账。
- **4A.5 问询台端点 + 设置端点(🔴 key 存取 @builder-pro)**:
  - `POST /api/v1/inquiry`(无状态,客户端持有对话上下文,继承 LinoN `/chat` 姿势)`{code, messages[]{role∈user/assistant, content}}` → **① 确定性检查**(纪律核对:次新/高弹题材黑名单 / ST / 板块限制,读 strategy v1 规则 `brain.get_active()`)+ **② 同一评分管线跑分**(对该单票调 `report/candidates.py` 评分,同码)+ **③ 板块年龄** → **④ LLM 带工具调用**(实时取数复用 `sentinel/quotes.py` / 重算复用评分 / 联网搜索复用 `llm/providers`)→ `{reply(自由对话体), verdict∈{"不符合","初审通过进海选池"}, evidence}`。**verdict=初审通过 → 写 `inquiry_pool`(当日)**,供当晚 `report.py` 强制纳入候选 universe(§2.5)。**永不「现在就买」**(枚举只两值 + system prompt guardrail)。缺 key → 走 §3.4 降级链(确定性检查照跑,LLM 段返「未激活」占位,不崩)。
  - `GET /api/v1/settings` → `{llmProvider, llmKeySet:bool(不回传明文), push{report:bool, retreatBrake:bool}}`。
  - `PUT /api/v1/settings/llm`(🔴)`{provider∈glm/kimi, apiKey}` → 写 `app_settings` DB 单行 → `{ok}`。**key 绝不回日志、绝不进 git、DB 600、rsync 永不同步**;`get_provider()` 解析改 DB 覆盖 → `.env` 兜底(§3.4),运行时生效不重启。
  - `PUT /api/v1/settings/push` `{report, retreatBrake}` → 写 `app_settings` → `{ok}`(APNs 推送前读此开关)。
  - `POST /api/v1/devices` `{token, platform:"ios"}` → 写 `devices` 表(APNs 注册,复用 LinoN 语义)。

**4A 验收**:本地 uvicorn 起 → curl 全端点闭环(health 200 免鉴权 / 无 token 端点 401 / 报告与看板返真实阶段 2/3 数据形状 / 开清仓走台账 / 问询台丢一票返二值裁决 + 依据、初审通过写 `inquiry_pool` / `PUT /settings/llm` 后 `get_provider()` 现读 DB 生效);pytest 覆盖鉴权、问询台二值裁决「永不买」不变量、settings DB 存取(key 不回传明文),全绿零回归。

---

#### 4B · 云端化 + 部署(hz ECS;🔴 部署脚本 + APNs @builder-pro)

- **4B.1 数据管线分工 + 内存实测门禁(先做,定架构)**:hz 内存是硬约束(§3.6)。**先在 ECS 实测三项峰值 RSS**——① `neckline.service` 启动 + 一次哨兵 tick(关注池 ≤200 拉价)的常驻基线;② `report.py` 单日报告生成(polars lazy 扫单日 Parquet,预期轻);③ `daily_update.py` 单日增量(TuShare 全市场拉 + Parquet 写 + `limit_derived` 算,预期最重)。**判定规则**:
  - **方案 A(全在 ECS)**:若 ②③ 峰值均不把邻居(lf/fiscal/pg)压进 swap 抖动、留安全余量 → ECS 自跑 `daily_update`(systemd timer)+ `report`。
  - **方案 B(Mac–云分工,回退路径)**:若 ③ `daily_update` 在 ECS OOM / swap 抖动 → **Mac 定时跑 backfill/daily_update → 只 rsync `data/parquet/` 产物上云**;ECS 只跑 API + 哨兵轮询 + `report.py`(轻,读已落 Parquet)+ APNs。
  - **两方案共同不变量**:ECS 的 `neckline.db`(业务台账:settings/devices/positions/reports/inquiry_pool/reviews)**永远是权威、绝不被 Mac 同步覆盖**;跨机只同步 `data/parquet/`(行情只读)。**全量 backfill(六年历史)恒在 Mac 一次性跑**(体量大),ECS 不做全量 backfill。门禁结论写进本条 + 4B.3。
- **4B.2 部署脚手架(🔴 @builder-pro)**:`scripts/setup.sh`(幂等,pip 走阿里云镜像 + `PIP_DEFAULT_TIMEOUT=60`)、`scripts/sync_code.sh`(rsync 后端代码)、`scripts/sync_data.sh`(rsync `data/parquet/` 产物,方案 B 用)、`deploy/neckline.service`。**全量吸收 `hz_info.md §12` + LinoN CLAUDE.md 部署坑**:
  - **rsync exclude 必须锚定根** `--exclude '/data/'`(前导斜杠)——Neckline 同时有 `data/`(Parquet+db,排)与源码包 `neckline/data/`(tushare_client/realtime/limit_derived,**绝不能排**),这正是 LinoN 坑 4 的教训;`sync_data.sh` 反向**只同步 `data/parquet/`**、显式排 `*.db`(绝不上传 `neckline.db` 覆盖 ECS 权威台账)。
  - GNU rsync 3.x(macOS openrsync 与 `--delete` 不兼容,`brew install rsync`);排除 `.env` / `*.p8`(远端独立维护,`--delete` 绝不清)。
  - **rsync `-a` 冲 setgid**:每次 rsync 后 `sudo chown -R deploy:neckline /opt/neckline` + 目录 `chmod 2770`、`.env`/`.p8`/`neckline.db` `600`。
  - ECS Python 3.12:`--delete` 不清 stale `__pycache__/*.pyc`,改包结构后手动删旧 `.pyc`。
  - **tushare 禁 `set_token`**(炸 nologin `neckline` 家目录,LinoN 坑 5)——Neckline `tushare_client` 已用 `pro_api(token)` 直传,守住即可。
  - 改 systemd unit / nginx conf 手动 `scp` + `daemon-reload` / `nginx -t && reload`(rsync 排除 `deploy/`)。
- **4B.3 systemd 单 unit + 哨兵云端化**:`neckline.service`(User=neckline,WorkingDir `/opt/neckline`,EnvironmentFile `.env`,ExecStart venv 内 uvicorn `neckline.api.app:app --host 127.0.0.1 --port 8002`)。**哨兵折进 lifespan asyncio 任务**(照搬 LinoN 单 unit 省内存,`run_tick` 每 60s 一拍、交易时段门控、非交易时段待机;测试注入开关关轮询)。回写权威 unit 模板进仓库 `deploy/`。
- **4B.4 nginx + 16:00 报告定时**:nginx `ln.linotsai.top` upstream 指 `127.0.0.1:8002`(接班切换时机见 §3.6;`listen 443 ssl http2` 旧写法,nginx 1.24)。`neckline-report.timer` + `neckline-report.service`(oneshot,交易日 16:00)→ 跑 `report.py`(**独立瞬态进程,跑完即释放内存**,对紧内存友好)→ 落库 → 触发 APNs 报告推送(4B.5)。方案 A 另加 `neckline-daily.timer`(~16:30 数据稳定后跑 `daily_update.py`)。
- **4B.5 APNs 推送(🔴 @builder-pro)**:复用 LinoN `push/apns.py`(JWT ES256 / `build_jwt(key_pem)` / `send_push(transport=)` 可注入免联网单测);`.p8` 从 `/opt/linon` 拷至 `/opt/neckline`(账号级密钥,`chown neckline:neckline` 600),**APNs topic 换成 `top.linotsai.neckline`**,dev 直装走 sandbox 网关。**只推两类**(拍板 4):① `neckline-report.service` 落库后 → 若 `app_settings.push_report` → 推「今日盘后报告已生成」给所有 `devices`;② 哨兵 asyncio 退潮触发 → 若 `app_settings.push_retreat` → 推「退潮红色刹车:今日计划作废、禁开新仓」。**买点/证伪/持仓一律不推**(只进 4A.3 看板)。

**4B 验收**:内存门禁三项峰值实测有数、架构方案 A/B 二选一写死并说明依据;`neckline.service` 在 ECS active、health 公网 HTTPS 200;哨兵在交易时段真跑(兑现 4E 真盘中验证);`neckline-report.timer` 交易日 16:00 生成报告并按开关推 APNs;部署脚本经 DRY_RUN 演练 + 真跑,setgid/pyc/exclude 锚定各坑不复发;LinoN 接班切换(8001→8002 / linon 退役)在联调通过后执行、`hz_info.md` 同步更新。

---

#### 4C · 客户端双端(SwiftUI iOS + macOS;新 App,@builder)

**从 LinoN `client/` 搬什么 vs 重写什么**(坑全部吸收自 LinoN CLAUDE.md 客户端节):

- **搬(改 Bundle ID / key 前缀后整块复用)**:`project.yml`(xcodegen multiplatform 单 target,`supportedDestinations:[iOS,macOS]`,Bundle ID → `top.linotsai.neckline`,`DEVELOPMENT_TEAM HX73DFL88G` 同,deploymentTarget iOS/macOS 26)、`DesignTokens.swift`(**绿涨红跌**,Liquid Glass 克制)、`Networking/AppConfig.swift`(baseURL + token,UserDefaults→env→`LocalSecrets.plist` 优先级;key 前缀 `LN_`→`NK_`,prod URL 复用 `ln.linotsai.top`、dev `127.0.0.1:8002`)、`Networking/APIClient.swift`(actor,**`makeURL` 禁 `appendingPathComponent`**、Bearer、`timeout` 可选参、错误映射;端点按 4A 契约重写)、`Push/PushManager.swift` + `App/LinoNApp.swift` 的 AppDelegate 桥(APNs 授权/token/`POST /devices`/category/ack;category 简化——报告/刹车是信息类推送,动作按钮可精简)、`Calendar/StaticTradingCalendar.swift`(日期解析)、平台分叉壳骨架(iOS 底部 TabBar / macOS 侧栏 + Settings 场景)。
- **重写(信息架构全新)**:`Models.swift`(新领域:Report / Candidate 四件套 / BoardEvent / InquiryResult / Position / Settings)+ 全部 Views(四板块,非照搬 LinoN 今日台;候选卡 / 持仓卡 / 对话气泡的布局可借鉴)。
- **坑吸收(逐条,来自 LinoN CLAUDE.md)**:① iOS ATS——`Info.plist` 加 `NSAppTransportSecurity` `NSAllowsLocalNetworking` + `127.0.0.1` 例外(dev http 明文,否则静默不发请求);prod https 不受影响。② **`makeURL` 免 `?` 编码坑**——带 query 端点(`?date=`/`?week=`)必走 `URL(string:relativeTo:)`,加门禁 `testMakeURLPreservesQueryString`。③ 改 `project.yml` / 加 `.swift` 后**必 `xcodegen generate`** 重生 `.xcodeproj`(否则 "cannot find X in scope")。④ clientProvider 时序——`bind(config:)` 在 `.task` 内**先于** `refresh()`(勿放 `.onAppear`)。⑤ API_TOKEN 不入源码(UserDefaults→env→gitignored `LocalSecrets.plist`)。⑥ 平台分叉 Scene body 内 `#if` 不能跨 WindowGroup+Settings 混写,整支 if/else 分两套 Scene;锁屏推送整文件 `#if os(iOS)`。⑦ 改 View **必 `xcodebuild` App target 验证**(仅 SwiftPM build 不暴露 View 层问题,全局经验);拷 `.app` 用 `ditto` 非 `cp -R`。⑧ macOS test destination 宿主 quirk——XCTest 门禁走 iOS Simulator,macOS 侧只 build。⑨ ImageRenderer 不渲 ScrollView(快照核对裹 VStack);computer-use 全屏 Dock 守卫 → 可视核对退路 = ImageRenderer 离屏快照。⑩ **SwiftUI 动画三禁**(全局 CLAUDE.md 2026-07-16:阴影不参与动画 / 不交叉淡化玻璃大视图树 / 重排文本用 `.transaction` 排除隐式动画)。

**四板块(iPhone;macOS 同四板块 + 4D 工作台)**:
- **4C.1 今日计划**:`GET /report/latest`(候选 20 + 四件套 + 情绪仪表盘 + 仓位额度 + 强势板块)+ `GET /positions`(持仓 + 派生止损线 + 挂条件单自证 checklist)。
- **4C.2 盘中看板**:`GET /board`(打开即拉;退潮刹车红条置顶 + 买点/证伪/持仓判决集中显示)。轮询或下拉刷新。
- **4C.3 问询台**:`POST /inquiry`(自由对话聊天;二值裁决 + 依据卡;初审通过提示「已进当晚海选池」;**永不出现「买」按钮**)。
- **4C.4 设置(APNs 相关 🔴 @builder-pro 复审)**:LLM key 填写 + GLM/Kimi 供应商切换(`GET/PUT /settings/llm`,运行时生效)+ 推送开关(报告 / 退潮刹车,`PUT /settings/push`)+ 后端地址 + API token + 连接自检(`health` + 拉一次 positions)+ iOS 推送重注册。

**4C 验收**:双端 `xcodebuild` iOS Simulator + macOS 各 `BUILD SUCCEEDED`;四板块渲染真实后端数据(today/board/inquiry/settings 端到端走通);问询台无「买」路径(UI + 单测断言);设置屏改 LLM provider/key 后端生效、改推送开关生效;XCTest(iOS Simulator)含 `makeURL` query 门禁全绿;绿涨红跌一致。

---

#### 4D · 周复盘工作台(macOS 拖入交割单;对账逻辑后端 `neckline/review/`,@builder)

`neckline/review/` 阶段 0–3 是空包,阶段 4 首次落地。**对账逻辑放后端**(客户端只负责拖文件 + 展示)。

- **4D.1 交割单解析(可配字段映射)**:`POST /api/v1/review/upload`(multipart xlsx)→ `openpyxl`/pandas 读表 → 按**可配列映射**(`app_settings.review_col_map`)把券商列名映射到规范字段(成交日期 / 代码 / 名称 / 买卖方向 / 成交价 / 成交数量 / 成交金额 / 手续费 / 印花税 / 过户费)。**先支持用户现有「整理格式」**(§1.3 归因用的那份 `2026年股票交割单整理.xlsx` 结构),映射默认值按整理格式钉死、留 `review_col_map` 可覆盖以支持两家券商原始格式;**解析失败逐行降级、缺列优雅提示,不崩**。
- **4D.2 对账引擎**:闭合成买卖回合 → 三查:① **实际成交 vs 当周报告**(成交的票当周是否进过候选 / 报告是否放行);② **破 -5% 未止损清单**(回合亏损超 -5% 却未在止损带离场 = 违纪,对应 §1.3 第一死因、§2.1 第 1 条);③ **章程执行**(§2.1:单笔 ≤2 万 / ≤5 只 / 敞口 ≤60% / 同票冷却 / 追绿盘阴线等禁买规则,逐条查)。产出违纪清单 + 当周实现盈亏。**单周实现亏损 ≥ 总仓 2% → 触发强制复盘材料生成**(§2.1 第 4 条)。落 `reviews` 表(week PK)。
- **4D.3 复盘材料生成**:确定性对账结果为主(违纪清单 / 数字),叙述性复盘材料可选叠加 LLM(自由对话体,§2.7;缺 key 降级为纯确定性材料)。`GET /api/v1/review?week=YYYY-Www` 读历史。
- **4D.4 macOS 工作台 UI**:拖入 xlsx → 上传 → 展示对账结果(违纪高亮 / 实际 vs 报告 / 章程逐条 / 强制复盘触发提示 / 复盘材料)。**iOS 不做工作台**(拖文件 + 阅读长材料是桌面场景)。

**4D 验收**:拖入用户整理格式交割单 → 生成违纪清单(破 -5%未止损 / 章程越界)+ 实际 vs 报告对账 + 当周实现盈亏 + 强制复盘触发正确;字段映射可配(改 `review_col_map` 能吃另一种列名);解析异常不崩;pytest 用样例交割单覆盖三查 + 强制复盘阈值边界。**⚠ 金额计算**:对账涉及盈亏金额,虽非下单高危,builder 收尾建议叫一次 `review`。

---

#### 4E · 端到端联调 + APNs 真机 + 阶段 3 欠账实盘校正(@builder + 用户)

- **4E.1 端到端联调**:三通路真机走通——① 16:00 报告生成 → APNs 推报告 → App 今日计划显示;② 交易时段哨兵在 ECS 常驻真跑 → 退潮 APNs 刹车 + 其余事件进看板;③ 问询台丢票 → 二值裁决 → 初审通过进海选池 → 当晚报告纳入。
- **4E.2 APNs 真机(🔴)**:新 Bundle ID 真机注册 device token → ECS→APNs sandbox→iPhone 真推(报告 + 刹车两类)实测 200;锁屏卡显示正确。**用户网页操作**:Apple Developer 新 App ID + Push capability(见 §八)。
- **4E.3 LinoN 接班切换**:联调通过后 nginx `ln.linotsai.top` 切 8002 → `linon.service` stop+disable → LinoN 退役 → `hz_info.md` 同步更新(LinoN 退役、Neckline 上线、端口/证书/DNS 现状)。
- **4E.4 阶段 3 欠账清偿(挂账不丢)**:① **真盘中活体验证**——哨兵上 ECS 常驻后,交易时段首次用真实新浪/腾讯源真跑 `run_tick`,验证响应格式 / 批量拉价耗时 / 四哨兵触发(兑现阶段 3 欠账①)。② **哨兵第一版启发式阈值校正**——量能折算(pullback 0.8 倍)/ 持仓止损缓冲(2pp)/ 退潮三阈(炸板率 50% / 相对飙升 20pp / 跌停 5 只或 15% / 板块跳水 -3%)均未回测,据首个真实交易日表现校正(常量已命名,定点改成本低)。③ **LLM 真调用**——用户设置屏填 GLM/Kimi key 后首次真连烟测(兑现阶段 2 遗留),验 endpoint/字段/联网搜索协议假设。④ **Bark 降备用**——APNs 为主推通道,Bark 无 `BARK_URL` 活体验证的欠账降级不阻塞。

**阶段 4 总验收**:后端 API 在 hz ECS 常驻(health 公网 200)、哨兵交易时段真跑、16:00 报告 + 退潮刹车两类 APNs 真机推达;iOS/macOS 双端四板块端到端可用(今日计划 / 盘中看板 / 问询台 / 设置)、macOS 周复盘工作台拖入交割单出对账 + 复盘材料;问询台永不产「买」、初审通过进海选池闭环;LLM key App 可改运行时生效;LinoN 完成接班退役、`hz_info.md` 更新;阶段 3 三类欠账在真盘中/真 key 下清偿或明确降级。

---

## 六、回测参数清单(已全部过堂 → 策略线)

> P1–P10 全部有结论并写死进大脑 K1,清单全文已迁 `STRATEGY_LAB.md` 雷区地图节。

---

## 七、Backlog(挂起 / 争议 / 拍板 / 后期决策)

- **[挂起] 纪律章程第 4 条「≥5% 次周单笔减半」**:用户未同意,阶段 1(P10)回测验证后再议是否纳入。
- **[争议] 大盘 MA20 市场过滤器**:不进第一版规则,阶段 1(P1)回测定生死(与情绪仪表盘二选一或叠加)。
- ~~**[拍板] 客户端载体 + 推送通道**~~ → **已拍板(2026-07-20)= 方案 A**(SwiftUI 双端 + APNs,新 App;APNs 只推报告 + 退潮刹车,其余进看板;Bark 降备用)。落地 §五 阶段 4C/4B.5,设计共识 §2.4/§3.5。
- ~~**[后期决策] 盘中哨兵部署**~~ → **已拍板(2026-07-20)= 上 hz ECS**(复用 LinoN 基建,单 unit 内 asyncio 哨兵;内存分工方案 A/B 以 4B.1 实测门禁定)。设计共识 §3.6,落地 §五 阶段 4B。
- **[研究·当前施工件] K3 策略研究(系统化超跌反弹)**:施工图 **§五C**,六研究件 B0–B5,产出 `research/k3_report.md` + K3 候选大脑(`strategy_versions.K3`,`is_active=0` 不激活)+ 「K3 vs K1 对比裁决书」。中心假设 = 顺日线 2–5 日均值回归地形做多超跌(阶段 1 P4/P5 被否 + K2 强势负贡献的正向反证),**带质量过滤 + 趋势背景区分 + -5% 条件单 + 仓位纪律,与「抄底接刀」区分写死**。**三约束不可破**(日频 + T+1 + 上班族注意力)、**明确不碰打板**;**停止挖矿条款**:B1→B4 全否 → 触发「换打法」对话、不再在此信封内立新研究。**B1 末设用户检查点**(地图呈用户校准盘感后才放行 B2)。**K3 激活 / 部署过门后另行立项,不在本次范围**;纪律章程 §2.1 一字不动(B2 例外条款)、生产零改动、K1 逐位不变。
- **[后期决策·K3-B2 副产品] 反向证伪哨兵新条件(降势票诱多做局)**:K3-B2 臂④诊断实测——**年线下(`close<ma250`)的降势票突现涨停 → 事后 3 日 −2.06%(2026 −3.43%、胜率 0.29)、突现放量大阳(ret1d≥5%×量比≥2) → −1.04%**,逐年一致为负(印证用户"诱多做局"直觉)。**可反向用作证伪哨兵新条件**:持仓/自选票若在年线下突现放量大阳/涨停 = 派发/诱多信号 → 提示减仓/勿追。**本轮不实现**(K3 研究结论,非生产改动;实现须另行立项 + 与现役 `invalidation_spec` 四条价量结构合并评估,守"证伪只用价量结构"铁律)。数据落 `research/k3_report.md`「臂④」节。
- **[研究·已完工归档 2026-07-22] K2 策略研究(情绪门控 × 主线票池 × 短线进出)**:施工图 **§五B**,六研究件 B1–B6,产出 `research/k2_report.md` + K2 候选大脑(`strategy_versions.K2`,`is_active=0` 不激活)+ 「K2 vs K1 对比裁决书」。**中心命题否决**(「情绪进攻段 × 主线成员内追强势」无正期望,印证阶段 1 P3);采纳集为空 = K2 config 逐字段等于 K1;K1 仍唯一现役。正 alpha 仍开放 → 接 K3(超跌反向)。
- **[机制] 策略进化门禁**:按月 / 季调参须过回测 + walk-forward 样本外跑赢现役 + 用户批准;大脑按版本归因实盘表现。落地在阶段 1 之后常态运行。
- **[未来] 分钟线数据源**:当前 TuShare 600 元档无分钟线,盘中靠新浪 / 腾讯免费源。若后续需要分钟级回测,评估升档或其他源。
- **[✅ v1.2-0 已修复,待 v1.2-F 首次真部署活体收官] `scripts/sync_code.sh` 尾部 chown 收尾**:`chown -R deploy:neckline /opt/neckline` 会把 rsync 已排除的 `data/` 属主一并翻掉 → 生产 DB 只读 → 服务 502(v1.1.1 部署时老坑复发,当场手工复原)。**修法已落地(2026-07-25)**:打印的收尾 chown/chmod 两条改 `find -path .../data -prune` 版跳过 `data/`,脚本末尾新增只读属主自检(不符红字 + `exit 1`)。`DRY_RUN=1` 预演 + ECS 无害临时路径场景验证通过(未碰生产库);**真实部署场景下"收尾命令+自检"是否如预期拦下问题**,留 v1.2-F 首次真部署合并验收(§五 v1.2-0 「0 验收」③)。
- **[v1.3 排期] 盘前情报包(需求 3,2026-07-25 交接)**:主线识别器改定位为**拥挤情报件**(K2 判决:板块层有效但无次日领先性,当情报展示、不当选股信号)+ 板块资金流展示(`moneyflow_dc` 2023-09+,落盘走 `write_table_day`)+ 复盘情报字段(涨/跌幅榜、涨停梯队、跌停榜、大盘量能、最强题材核心一二名、市值 / 涨跌幅制度偏好、题材持续天数)——全 EOD 可算,竞价 / L2 盘口无数据不做。**v1.2 不做,推 v1.3**;规格来源战法总结 §五/§八(见 `archive/交接_系统线升级需求_20260725.md` 需求 3 + `STRATEGY_LAB §五` B3.1「复活为情报件」)。
- **[已挂 v1.2-D2] `api/inquiry.py::run_deterministic_checks` 选股域漂移**:手写重复 `research/panel.py::base_universe_expr()` 选股域逻辑(4A 遗留,v1.1-C/D 施工时发现,原只记 §四正文未进 Backlog)→ v1.2-D2 复用 `base_universe_expr()`(选股域揉一条不拆解,只 config 可配禁买过滤逐项拆,照 `report/watchlist_check.py::_discipline_checks` 姿势,见 CLAUDE.md v1.1-C/D)。
- **[挂账·跨版本不丢] 三项待清偿**(v1 上线累积,v1.1 不专门做但不丢):① **GLM 顶层 `web_search` 空数组待查**——搜索命中解析不到时 `search_hits` 落库为空数组、审判归因材料缺搜索存档(原因待查:GLM 内部搜索不回传 or 响应形状变化,不影响主链路,见项目 CLAUDE.md);② **Bark 活体**——payload 基于官方文档、无真实 `BARK_URL` 验证,APNs 为主推通道故降备用不阻塞;③ **周复盘首次真实交割单验证**——4D 对账引擎用真实 openpyxl xlsx 冒烟过,但**尚未用用户真实券商交割单**跑过一次(首次真实交割单到手时验证两家券商原始格式解析 + 字段映射)。

---

## 八、用户操作清单(必须由用户手动办)

1. ~~**确认 TuShare 600 元档接口权限**~~:✅ 已确认(2026-07-19,用户提供官网权限对照表截图,600 元档 = 6000 积分列)。结论已吸收进 §3.2:`daily/daily_basic/adj_factor/index_daily/moneyflow_dc/top_list/概念板块和成分` 可用;**`limit_list` 不可用(15000 积分)→ 改 0.4b 自算涨跌停衍生表**,不阻塞。
2. ~~**确认券商条件单能力**~~:✅ 已确认(2026-07-19):止损条件单与**回落止盈**条件单均支持。阶段 1 止盈研究(1.4)按「回落止盈可由券商执行」设计。
3. ~~**(阶段 2 前拍板)TuShare 新闻资讯接口是否增购**~~:✅ 已拍板(2026-07-19):**不购**,消息面走 LLM 联网搜索(方案与代价见 2.4:需带搜索的 API 供应商、搜索结果落库存档、审判层退出回测改实盘归因考核)。唯一会重开此项的场景:未来想做「历史新闻进回测」(搜索给不了历史时点快照,只有新闻库能)。
4. ~~**(阶段 4 前)客户端载体拍板**~~:✅ 已拍板(2026-07-20)= 方案 A(SwiftUI 双端 + APNs,新 Bundle ID `top.linotsai.neckline`)。

### 用户网页操作清单(阶段 4,必须在网页手动办理)

5. **Apple Developer 新 App ID + 推送能力**(`https://developer.apple.com/account/resources/identifiers`):
   - **Xcode automatic signing 能自动办的**:注册新 App ID `top.linotsai.neckline` + 生成 provisioning + 在加了 push entitlement + Push Notifications capability 后为该 App ID 开启 Push(加 `Neckline.entitlements` + capability 后 Xcode 首次真机构建通常自动完成)。
   - **可能需网页手动确认的**:若 automatic signing 未自动为该 App ID 勾上 Push Notifications 能力,去上述 Identifiers 页面手动为 `top.linotsai.neckline` 勾选 Push Notifications 并保存。
   - **无需新建的**:APNs 鉴权密钥 `.p8`(Key ID `Q963AP3VY8`)是**账号级密钥,直接复用**给新 Bundle ID,不必新建密钥;`.p8` 已在 ECS `/opt/linon`,部署时拷进 `/opt/neckline`。
6. **提供 LLM API key**(阶段 4 起,App 内办):在 App「设置」板块填 GLM 或 Kimi 的 API key + 选供应商 → `PUT /settings/llm` 落服务端 DB,运行时生效。**这是问询台 + 报告 LLM 审判 + 复盘材料的激活开关;缺 key 全链路优雅降级不崩**。填入后首次真调用即验证 GLM/Kimi 协议假设(兑现阶段 2 遗留欠账)。
7. **(每周,阶段 4 起)提供交割单**:每周把券商交割单 xlsx 拖进 macOS App 周复盘工作台,供违纪对账 + 复盘材料生成。**首版按用户现有「整理格式」支持,字段映射可配以兼容两家券商原始格式**(§五 阶段 4D)。
8. **(接班,阶段 4 部署上线后)确认 LinoN 退役**:Neckline 联调通过、`ln.linotsai.top` 切到 Neckline 后,LinoN 停用退役——旧交易记录 `linon.db` 是否归档下载由用户定(`hz_info.md §13` 挂账)。
9. **(v1.1,按需)提供同花顺自选 txt**:自选池与同花顺 PC 端对账 = 用户在**同花顺 PC 端手动导出自选 txt 文件**(无官方 API,拒绝模拟登录 = 账号风险)→ 拖进 macOS App 自选工作台做差异对账 + 一键对齐;反向可从 App 导出 Neckline 自选为同花顺可导入 txt。**非每日必办,按需对账时才做**(§五 v1.1-C.4 / v1.1-F.4)。
10. **(v1.1)设置屏开新增两类推送开关**:盘前校准(9:26 汇总)、D5 时间退出——App 设置屏可各自开 / 关(默认开),无网页操作。

---

## 九、变更日志

- **2026-07-25 · v1.2 立项(施工中)· 人机协作配套:三仓章程 + 预注册决策日志 + 归因闭环**:系统重定位(用户 = 唯一决策人,系统 = 情报 / 机械分析 / 纪律执行 / 归因审计,机器不替选股;策略线三战役判死「机器自动出信号」,K1 仍唯一现役、信封判死见 `STRATEGY_LAB`)。用户 2026-07-25 拍板行为面配套落系统线(交接 memo `archive/交接_系统线升级需求_20260725.md`)。完整施工图 §五。**分块**:v1.2-0 sync_code.sh footgun 修复(chown 收尾 prune `data/` + 脚本末尾 ssh stat 只读属主自检非零退出,销 §七待办 + hz_info §191 复发坑)→ A 仓位章程三仓制(🔴,唯一源现役 config:`max_positions` 5→3 / `single_cap` 2万→4万〔**语义变为违纪判定上限、非推荐值**〕/ `max_exposure_frac` 0.6→1.0;落 `v1.2` 新行 `activate=False`〔config 承 K1 血缘、仅改三仓位字段、**绝不碰 K 字头**〕+ 切换器脚本〔前置校验无 open 持仓 + old→new diff + `--confirm` 才激活,不做 API 端点〕+ **staged 两步生效**〔清仓 + 用户确认后才切,builder 不许直接激活〕;**附带修历史洗白洞**〔`strategy_versions.activated_at` 幂等迁移 + K1 回填 `created_at`、`reviews.strategy_version`、`run_weekly_review` 按周取「当时现役」config、无 `activated_at` 兜底退回 `get_active` 旧行为〕)→ B 预注册决策日志(六项落库不可编辑〔改动=新增修订行,归因认首版〕+ 服务端 `created_at`〔客户端不许传〕+ 论点标签枚举码〔展示层换算沿 `boardLabel`〕+ `decision_log` 表 + 端点 POST/GET/link/cancel/revise;**审计件非下单件**)→ C 挂单未成交追踪(N=5 交易日、`decision_pending_track` 表、折进 16:35 报告管线复用 EOD 面板不新拉源、第 N 日 pending→expired)→ D 归因入 4D 周复盘(决策日志 via `position_id` 与 FIFO RoundTrip 按 `(ts_code,buy_date)` 邻近匹配、按论点标签胜率/盈亏比 + 「无决策日志开仓 N 笔」纪律项〔软约束落点,不静默丢〕)→ D2 问询台选股域漂移清理(`run_deterministic_checks` 复用 `base_universe_expr()`,选股域揉一条、config 禁买项逐项拆)→ E 客户端双端(录入六项 + 标签 picker 嵌「已按计划买入」流程之前、成交后一键关联、**不新增第六 tab**、entry-suggestion 改区间双档、macOS 归因表)→ F 部署 + schema 迁移(`sqlite3 .backup` 备份 + 两新表 + 两迁移列)+ 活体(🔴)。**铁律**:同码不重写、单一事实源不漂移、系统永不自动下单、不新增推送类(仍四类)、幂等迁移改 schema 前备份。**v1.1 施工图全文移 `archive/v1.1_施工图_20260721.md`**、§四三条 K3 状态压成一行指针指向 `STRATEGY_LAB`(策略研究结论不在系统线复述,K 变更日志留痕不动)。仓库现状:v1.2 施工图就位待 builder。
- **2026-07-25 · K3 · B2 四臂组合级回测完工 → ★中心假设否决(诚实否定合格)**:B1 用户检查点校准三裁断落地——①降势超卖(C4/C2)只留档不竞选主策略(用户"不敢碰年线下超弱票");②新增硬门禁**2026 段生存测试**(组合级 2026 分段为负即一票否决,不论六年总分);③新增用户偏好臂(升势回撤 → 启动确认 → 买)。四臂预注册后独立 commit(64e77ee,防数据窥探)→ B2.0 生产零改动扩展(`signals.buy_oversold` + `MomentumConfig` 七个默认关闭 `oversold_*` 字段 + `build_entry_mask` 新分支 + 护栏单测 `tests/test_k3_oversold_guardrail.py` 7 例,**K1 逐位不变**,commit e44e455)→ 四臂组合回测 `research/k3_b2_portfolio.py`(commit 9609482)。**★结论:四臂全灭**:(1)组合级样本外冻结无一跑赢 K1(K1 −10.7% / 臂①C4 −16.9% / C2 −29.9% / 臂②A6 −18.4% / 臂③收复MA5 −38.5% / 缩量止跌 −18.7%);(2)**★2026 生存硬门禁七臂全 ❌否决**(C4 −13.5% / C2 −24.7% / A6 −18.7% / 臂③各 −6.8%~−24.8%);(3)**臂①降势超卖组合级样本内灾难**(C4 −67% / C2 −89%)——B1 事件研究里左尾最干净、正期望的 C4/C2 在 -5% 止损 + hold≤5 组合纪律下翻负,**信号级正期望是均值口径幻觉**(消融证降势层组合级负贡献:A3全域 2026 +5.0% → +降势 −13.5%),用户"不敢碰"盘感被坐实;(4)**用户偏好臂③双层否决**(事件研究 + 组合级一致,消融证"确认层"负贡献 = 买死猫跳高点,收复MA5 把 out 从 −32% 拉到 −53%);(5)**止损×天性交互**:超跌臂止损率 2–2.7× K1,仅 A6 深跌见"-5% 扫地板"(止损后 5 日反弹率 53.8%/中位 +1.08%),但放宽亦救不活(A6 2026 底层期望已负),**§2.1 -5% 维持不动**(交用户知悉,无权擅改);(6)walk-forward 仅 A6 7/10 但与 2026 门禁冲突(非稳健);(7)敏感性 ±1 格单调无悬崖但全档 out+2026 一致负(整个超跌子域样本外为负,非差一格);(8)**臂④诱多做局数字**:年线下降势票涨停后 3 日 −2.06%(2026 −3.43%)/放量大阳 −1.04%,逐年一致负 → 入 §七 Backlog 作反向证伪哨兵候选(本轮不实现);(9)K1 自身 2026 亦 −16.5%——**2026 是全日频均值回归子域通杀**,超跌臂更负非独此失效,印证用户"市场变了、量化渗透新常态"。**采纳集 = 空(K3 config = K1,承 K2 先例)**;**⛔ 停止挖矿条款触发位已置**(K1/K2/K3 三次在日频+T+1+上班族注意力信封内验证无稳健正 α);pytest **833 绿**(826 基线 + 7 护栏,零回归);生产零改动、K1 逐位不变、纪律章程 §2.1 未改一字、诚实否定合格。**B2 完成即停,B3 起等用户放行**(B4 情绪软降额 B2 全灭本应跳过;建议直接进 B5 裁决书 + 换打法对话,是否仍跑 B3 资金面首测由用户定)。详见 `research/k3_report.md`「B2」节。
- **2026-07-24 · K3 · B0 数据修缮 + B1 超跌事件研究完工 → ⏸ B1 用户检查点**:
  **B0.1** 修 `moneyflow_dc` 历史分区 TuShare 类型漂移——12 数值列在 900 个分区(897 空 + 2026-07-20/21/22
  三个含真数据毒源)落成 String,与主体 Float64 冲突,`scan_parquet` 整表读取 SchemaError(阻断 B3.1 资金面)。
  `scripts/fix_moneyflow_schema.py`(逐分区 cast Float64 strict=False,显式 canonical 不依赖已坏的整表 scan,幂等)+
  6 mock 单测(先补后动手,含「`select(pl.len())` 投影下推绕开 schema 对账」陷阱)。修后全窗 `scan_table_range`
  4,001,434 行可读、1587 分区 schema 统一、真数据值对拍逐位相等;`_align_to_table_schema` 防线恢复(B0.2 增量的
  07-23/24 落盘即 Float64 为证)。**B0.2** `daily_update` 增量六表到最新交易日 2026-07-24。**总管拍板:主判决窗口
  冻结 `SAMPLE_OUT_END`=2026-07-17 不动(与 K1 严格可比)、`panel_full` 不重建**;K3 另建扩展面板 `k3_panel.py`
  (`_cache/k3_panel.parquet`,载到 07-24 让冻结窗尾前瞻完整,加 ma60/ma250/斜率/连续阴线/横截面分位/跌停暴露前向标;
  ma250 因本地无 2019 数据在 2020 全 null,趋势背景维度有效窗 ~5.5 年,诚实标注)。
  **B1** 预注册(commit 在前,git 时间戳为证)全定义 × 六年事件研究(`k3_b1_eventstudy.py`,左尾 p5/p10 + 跌停暴露 +
  深亏占比,非仅均值)。**核心发现高度反直觉,呈用户校准**:①★**趋势背景闸门方向反了**——设想"会弹"的升势回撤(C1
  站上升年线急跌)样本外 -0.77%/PF 0.756/左尾最肥,设想"接刀"的降势下跌(C2/C4 跌破降年线再跌)反而 +0.87%/+0.88%/
  PF 1.48-1.52/**左尾全场最干净**;趋势背景**确有区分力**(★校验通过)但正负号与中心假设**相反**——照施工图剔除"下降
  趋势下跌"= 扔掉唯一正期望留真刀。②**缩量=刀**(量能判据非阴跌天数):B3 缩量急跌 ld_hold3 13%/深亏 10.7% 全场最差,
  放量(B1/B5)左尾干净。③**2026 通杀**:所有超跌定义(不分深度/跌法/趋势)2024-25 正 /2026 翻负(PF 0.49-0.79),
  均值回归 edge 高度 regime 依赖,与 K2 满额 gate 非平稳同病。④机械买最惨(横截面低分位 A9/A10)、买单日快刀(A1/A2)、
  买位置远者(A7/A8/A11)全负期望——**被杀,不进 B2**。幸存候选(交用户校准后由 B2 定构型):**C4/C2**(降势超卖·
  左尾最干净但反直觉)、**A6**(20 日深回撤·最一致但平淡)、候补 **B5**(放量 5 日跌·唯一 2026 不亏但 out 被 2025 灌水)。
  证据强度:个股价格信号 强/中(无 K2 成分洞)。`research/k3_report.md`「B1 用户检查点摘要」呈用户三校准问题。
  pytest 820→**826 绿**(6 新 mock 单测,零回归)。**⏸ 施工暂停,未获用户放行不进 B2。**

- **2026-07-24 · K3 策略研究立项(纯研究,不动生产 / 不动纪律章程)**:用户两轮讨论定案(2026-07-23)
  **K3 = 系统化超跌反弹**——数据两轮一致(阶段 1 P4/P5 被否 = 被禁弱势票反而更会反弹〔绿盘大阴线被禁那批
  PF 0.880、距前高 -15% 被禁那批 0.951 全场最高〕;K2 强势层组合级负贡献)指向日线 2–5 日均值回归主导,K3 顺
  地形做多超跌。**与「抄底接刀」的区分写死进 plan**:带质量过滤 + **趋势背景区分(上升趋势回撤 vs 下降趋势
  下跌 = 反弹/接刀分水岭)** + -5% 条件单 + 仓位纪律的机械反转,非徒手接下跌趋势票。**§五C 填入完整施工图**
  (B0 数据修缮〔快修级:修 `moneyflow_dc` 12 数值列 2020-2023 String vs 2024-2026 Float64 整表 scan SchemaError,
  `scripts/fix_moneyflow_schema.py` 逐分区 cast Float64 canonical + mock 单测;面板增量到最新交易日、`SAMPLE_OUT_END`
  单一源延展至最新〕→ B1 超跌定义库 + 事件研究〔**预注册**定义清单先落 report 再出结果;面板补 `ma60`/`ma250`/年线
  斜率/`consec_down_days`/横截面分位〔现 panel 缺、趋势背景与距年线必需〕;五维度粗网格〔跌幅深度 / 跌的方式
  急跌放量vs阴跌缩量 / **趋势背景★** / 质量过滤 / 位置〕× 六年事件研究;**报分布+左尾 p5/p10+次日跌停暴露,非仅
  均值**〔承 stage1「均值口径测不到左尾」〕;产出「会弹/是刀」地图;**末设用户检查点暂停**校准盘感,未放行不进
  B2〕→ B2 幸存定义规则化〔新买点 `buypoint="oversold"` 默认关闭 + K1 逐位不变护栏单测 `test_k3_oversold_guardrail.py`〕
  + 组合级回测〔对手 K1、卖出沿现役〕+ **止损扫损率×超跌天性交互**〔-5% 若系统性冲突如实交用户裁决,研究员无权
  改 §2.1〕+ 消融拆每维度贡献〔承 K2「叠加是毒药」〕+ walk-forward → B3 副线〔资金面 `moneyflow_dc` 主力连续净
  流入/流出首测,同窗基线对齐 2023-09+;10/20 日持有期地图纯地形不改短线定位〕→ B4 情绪软降额叠加〔只对 B2 幸存
  者、消融拆净效应、**验收盯最大回撤/左尾改善非收益**,只抬收益不削回撤 = 疑 regime artifact 不采纳〕→ B5 裁决书 +
  K3 候选大脑 `save_version("K3", activate=False)` 落库不激活〔断言 K1 仍 is_active=1、K2/K3 均 0〕+ K3 vs K1 裁决书 +
  **停止挖矿条款出口**),每块验收标准写死,**全程 @builder-pro**。**三约束不可破**(日频 + T+1 + 上班族注意力
  20–30 分钟一次)、**明确不碰打板**、**停止挖矿条款**(B1→B4 全否 → 「换打法」对话,不再在此信封内立新研究)写进
  施工图。复用阶段 1 + K2 研究基建(`research/lab`〔`get_panel`/`run_pf`/`stratify_*`/`summary_row`/`fmt`〕、
  `research/panel`〔`base_universe_expr`/`load_or_build_panel`/`SAMPLE_*`/`MONEYFLOW_START`〕、`research/eventstudy`
  〔`event_study`/`event_study_grouped`/`compare_signals`〕、`walk_forward`、`momentum`〔`MomentumConfig`/
  `build_entry_mask` 加 `oversold` 分支〕、`signals`〔超跌买点 = `forbid_green_bigdown`/`forbid_far_from_high` 取反〕、
  `brain`、`features`、`market_data.write_table_day`)。**铁律**:纪律章程 §2.1 未改一字(B2 例外条款)、生产零改动、
  K3 `is_active=0` 不激活、证据强度三级(**K3 主用纯价格量能信号、无 K2 成分洞**)、资金面同窗对齐、盘中刹车不可
  回测、诚实否定合格。§四 状态 + §七 Backlog 同步。runner 命名 `k3_b0_*`~`k3_b5_*`(加前缀与 K2 区分),结果落
  `research/k3_report.md`。仓库现状:K3 研究未开工,施工图就位待 builder-pro。
- **2026-07-22 · K2 策略研究(B1–B6)完工 · 中心命题否决 · K2 落库不激活(纯研究,生产零改动)**:施工图 §五B
  六研究件全过堂,产出 `research/k2_report.md`(体例沿 stage1)。**B2.0 数据新鲜度**:本地增量拉到 2026-07-22
  (daily/daily_basic/limit_derived/adj_factor/moneyflow 补 07-20/21/22 + 概念 `backfill_concept --force` END=20260722,
  ths_daily 到 07-22、394→362 清洗后真概念板块;守 450/分、未碰 ECS)。**B1 情绪三态闸门**:向量化情绪面板与生产
  `compute_sentiment` 逐字对拍 12/12;二值排除休息闸门样本内外双双更差(内 +5.08%→-7.31%、外 -10.67%→-26.65%,
  与 MA20 同命)**否决**;满额闸门样本外 +22.8% 但样本内 -4.70%、逐年 regime 非平稳,不采纳;答遗留⑤「简单情绪
  闸门亦失败、正解仍开放」;产出 B4 用「情绪进攻段=满额档」日集合。**B2 主线识别器**:剔宽基+市场属性 buckets→
  362 真概念;四信号(②动量③成交额占比=板块层强/①涨停家数④连板=个股层中·成分洞);「资金主战场」注意力口径
  (5 日平滑成交额占比)极稳(top-1 次日 94.8%、top-10 日翻手 6%),**07-22 校准命中用户五主题 4-5/5 但分注意力
  (科技/机器人/电)与动量(药/材料)两轴**;板块级领先性弱(main 前瞻≈none,承阶段1 P2)。**B3 成员判定**:滚动
  相关 20/40d(37.6M 行长表),在册成分共动系统性高于非成分(+0.07~0.13,验证共动口径),成员偏宽(mega-board
  重叠);成分洞降级、前向补强留待。**B4 中心命题=否决(★)**:「情绪进攻段 × 主线成员内追强势」无正期望——印证
  (非推翻)阶段1「追强势削 edge」并延伸到该子域;样本内组合级 K2 构型 -14%~-72% 灾难(-5% 止损下追强势复刻 P3
  爆亏),样本外唯一正数是情绪 gate 非平稳 regime、消融证明去 gate 即塌/强势净贡献≈0/主线成员组合级有害,
  walk-forward K2a 输 K1 7/10;信号级+组合级+内外+WF 四路一致(证据强度强)。**B5 止盈/高弹**:回落5%(K1)最优
  (固定+15% 样本外 -21.6% 更差,用户 +14~17% 先验回测不支持=系统均值盈利了结仅 +2.5~3.7%、命中约1%);高弹黑名单
  (K1)最优(减半参与样本外 -23.9% 最差=腾预算反增高弹暴露,次日跌停暴露<0.3% 未复现立项归因)。**B6 汇总**:采纳集
  为空,**K2 config = K1 逐字段相同**,`save_version("K2", rule={config:K1, central_proposition:"rejected"},
  activate=False)` 落 `strategy_versions`,落库后断言 **K1 仍 is_active=1 唯一现役 / K2 is_active=0**(独立复验
  通过)。研究扩展字段(`require_mainline_member`/`take_profit_fixed`/`high_elasticity_half`)全部默认关闭 = K1 逐位
  不变(护栏 `tests/test_k2_mainline_guardrail.py` 先落地再动策略代码);**全量 pytest 820 passed(816 基线 + 4 护栏,
  0 回归)**。纪律章程 §2.1 未改一字、生产系统零改动、资金面 moneyflow 未纳入(承阶段1 + 历史分区类型漂移坑)、
  盘中刹车不回测。**裁决书推荐:不激活(K2 无稳健改进,K1 现役)**;正 alpha 仍开放(盘中/高频、LLM 消息面、资金面
  待另行立项)。runner 落 `research/b1_sentiment·b1_gate·b2_mainline·b2_analysis·b3_membership·b4_central·
  b5_exits·b6_finalize.py`。
- **2026-07-19 · 立项(v0.1)**:完成交割单全量归因(净投入 15.28 万 / -16.4%、胜率 40.5% / 盈利因子 0.47、13 笔破 -5% 未止损占亏损 85%);定案设计共识(纪律章程 v0.1、母战法、盘后报告、盘中四哨兵、问询台、回测引擎带笼子、LLM 自由对话体硬约束);定技术选型(Python 3.11+、Parquet+polars / SQLite 混合存储、TuShare 600 档 + 新浪/腾讯实时源、DeepSeek、客户端载体列 A/B/C 推荐 B);产出本 PROJECT_PLAN 全骨架,阶段 0→4 划分 + 每阶段验收标准 + 回测参数清单 P1–P10 + 用户操作清单。仓库现状:仅 `.gitignore` / `.env` / 本文件,阶段 0 未开工。
- **2026-07-19 · 立项后确认(v0.1.1)**:① TuShare 权限按官网对照表逐项确认(600 元档 = 6000 积分,500 次/分钟):`top_list`/概念板块和成分/`moneyflow_dc` 可用;**`limit_list` 不可用(15000 积分)→ 新增 0.4b 自算涨跌停衍生表 `limit_derived`**,情绪仪表盘(2.1)改吃衍生表;新闻资讯为单独 1000 元/年未购,LLM 审判(2.4)第一版按无新闻设计,增购列 §8 决策项。② 券商条件单确认:止损与回落止盈均支持。③ **LinoN 停用**:hz 上 `linon.service` stop + disable(数据保留,详见 `~/Lino/hz_info.md`),端口 8001 释放,未来 Neckline 部署可用。§8 前两项确认完毕,**阶段 0 解除阻塞,可开工**。
- **2026-07-19 · 阶段 0 完工(数据层 + 回测引擎骨架)**:0.1–0.8 全交付,验收标准逐项达成。

  **验收标准逐项**:
  1. **全市场 2020-至今日线落 Parquet 且行数与 TuShare 对齐**——达成。全量 backfill(2020-01-02~2026-07-17,1584 交易日)总耗时 **2179 秒(~36 分钟)**,`daily`/`daily_basic`/`adj_factor` 三张大表 **0 失败天**(`daily` 7,803,220 行、`daily_basic` 7,754,541 行、`adj_factor` 7,940,937 行);`moneyflow_dc` 3,972,110 行,**仅覆盖 2023-09-11 起**(逐日核实 TuShare 该接口对更早日期返回 0 行,非拉取失败,是上游数据源本身的历史局限,已如实记入下方遗留问题);`index_daily` 5 指数共 7,358 行;`limit_derived` 224,066 行(命中行稀疏表,计算 780 万行仅耗 2.0 秒)。数据质量抽检:`daily` 无重复 (ts_code,trade_date)、无非正价格,`pre_close` 空值仅 52/780万行(占位新股边界,可忽略)。
  2. **随机抽 3 票前复权价与东财 App 一致**——**部分达成,方法论调整**:浏览器工具反复访问东方财富(`quote.eastmoney.com`、`push2his.eastmoney.com` K 线接口)多次超时未能取得可交互页面做逐格核对,改用四重替代验证锁定 qfq 正确性:① 原始 OHLC 与搜狐证券「历史行情」独立数据源核对贵州茅台近 5+ 个交易日 open/close/high/low/涨跌幅**逐格完全一致**;② qfq 公式与 TuShare 官方文档公式(`qfq=raw×adj_factor/latest_adj_factor`)逐字核对一致,并有 9 个单测锁死;③ 直接验证「锚点日(区间末尾)复权价严格等于原始价」的定义性质;④ 贵州茅台 `adj_factor` 在 2020-06-24、2021-06-25 两次跳变,与其真实年度分红除权日期吻合(常识核对)。三票抽样(600519.SH/000858.SZ/300750.SZ)已算出锚定 2026-07-17 的复权价,数值本身无外部 App 逐格背书,记入遗留问题。
  3. **dummy 策略跑完六年回测输出净值曲线 + 完整统计,单次全回测 < 数分钟**——达成。`DummyStrategy`(等权买 10 只、持有 5 日)跑 2020-01-02~2026-07-17 全量 1584 交易日,**耗时 50.1 秒**,净值曲线 1584 行、3134 笔完整回合、胜率/盈亏比/盈利因子/最大回撤/年化收益率齐全(dummy 策略无选股逻辑,六年 -82% 符合预期,只为验证引擎链路,非策略优劣结论)。
  4. **前视偏差 / T+1 / 涨跌停 / 停牌各有单测通过**——达成,并额外做了引擎级复权正确性 3 单测。全量 **100 个单测全绿**(`pytest tests/ -q`,0.26s)。

  **0.4b 涨跌停衍生表抽样核对(人工核对记录)**:施工期实时数据恰逢 2026-07-13、07-17 两次大跌,用搜狐证券「涨跌停历史数据」页面(`q.stock.sohu.com/cn/zdt.shtml`)逐日核对 2026-06-29~07-17 共 15 个交易日:**跌停家数 15 天中 13 天与公开数据完全一致(如 07-17 212=212、07-13 187=187),其余 2 天差 1**;涨停家数多天完全一致,其余日差 1~3(经排查为个别涨停价 1 分钱之内的"擦边未触及"案例,如北交所 920xxx 收盘价与涨停价差 1 分,判定逻辑正确,非 bug)。另用手工构造场景做 17 个单测覆盖创业板注册制改革(2020-08-24)分界、主板 ST 新规(2026-07-06)分界、科创板/创业板 ST 维持 20%不降、新股 1 日/5 日豁免窗口、炸板、连板计数等全部规则分支。误差远优于「个位数」验收线。

  **技术要点**(详见对应模块 docstring):TuShare 无 `limit_list`(15000 积分),0.4b 涨跌停价用**整数分精确整数运算**(而非浮点 `round`),施工期用 79200 组价格×幅度网格核对与 Decimal(ROUND_HALF_UP)基准零误差(浮点近似写法在网格中有 0.4%~1.7% 误判,已弃用);制度分界日(创业板注册制 2020-08-24、主板 ST 新规 2026-07-06)、科创板/创业板 ST 维持 20%、北交所 ST 维持 30% 等规则均施工期网搜多方源确认,非编造。回测引擎(0.7)补齐了 plan 0.5 "回测统一用前复权价"——初版遗漏此接线,code review 自查发现并修复,详见提交记录。

  **遗留问题(不阻塞阶段 0 验收,记录供阶段 1+ 参考)**:
  1. `moneyflow_dc`(东财资金流)TuShare 仅提供 2023-09-11 起数据,2020-2023 中段回测涉及资金面因子会缺失该区间,阶段 1 若要资金面回测需评估是否可接受这段空窗或找替代源。
  2. 北交所相关股票中,**241 只票共 34,152 行(占 `daily` 全表 0.44%)** 的 `trade_date` 早于 `stock_basic.list_date`——TuShare 用北交所现行 920xxx 代码回填了这些票在新三板阶段的历史行情,`limit_derived` 因"上市第 N 日"算出负值而落入新股豁免窗口、不参与涨跌停判定。该期间新三板本身无与北交所对应的正式涨跌停规则,暂判定为可接受;若阶段 1 策略要用到北交所早期数据需重新评估。
  3. qfq 三票外部 App 核对未能完成(见上「验收标准 2」),已有四重替代验证但非该验收项字面要求的直接比对,如需补齐建议换一条网络路径或用户手动截图核对。
  4. `namechange` 全量分页接口（`limit=8000` 起步）存在已知的分页边界漏行风险(同一 `start_date` 跨页时有小概率漏行),已对当前状态为 ST/*ST 的 326 个代码单独补拉兜底,但非当前 ST 的曾用名区间边界仍有极小概率残留漏行(全表 14138 行,两处分页边界,影响面很小,阶段 1 若发现具体票 ST 历史对不上可针对性单票补拉)。
  5. `backfill.py`/`daily_update.py` 等脚本级代码未建立与 `tests/` 同等的单元测试覆盖(如 `backfill_index_daily` 的多指数拼表逻辑,本次是靠跑完全量后人工写校验脚本抽查发现并修复了一处覆盖写 bug)——已修复且经真实数据验证,但下次改脚本逻辑建议先补一层 mock 单测再动手,避免同类问题只能靠事后人工核对发现。

- **2026-07-20 · 阶段 1 完工(策略研究 P1–P10)**:§6 参数清单逐项过堂,产出「策略规则 v1」版本化落 SQLite 大脑(`strategy_versions.v1`,active)。完整方法/数据窗口/结果表/结论**逐节**见 `research/stage1_report.md`;可复现 runner 在 `research/`(p3_strength / p4p5p6_forbid / p7p8_exits / phase2 / p2_sector_age / rule_v1,共用 `research/lab.py`),概念板块拉取脚本 `scripts/backfill_concept.py`。

  **P1–P10 判决(采纳/否决/待观察 + 样本外证据强度)**:
  - **P3 强势定义——三候选全否决(强)**:涨停基因/20 日涨幅/量价结构单独及叠加买点,信号级把全域 PF 0.834 拉低到 0.67~0.78,组合级 limitup_gene 六年 -96%;样本外同向。**采纳 `strength=none`**。追强势"67% 胜率"是 37 笔小样本幻觉,系统化不复现。
  - **P4/P5 禁买——否决(强)**:日线 2–5 日 A 股**均值回归**,绿盘大阴线(被禁那批 PF 0.880)、距前高过远(被禁那批 0.951 全场最高)反而更会反弹;禁掉=剔除最会反弹的票。
  - **P6 票型黑名单**:次新否决(组合级 -13.3%→-15.4%);高弹**采纳为结构性风控**(-5% 止损匹配 10% 涨跌幅主板品种、契合 §2.2 设计意图、压灾难尾部),**但样本内转正 PF 1.024 不复现于样本外 0.814=过拟合,不作 alpha 主张**。
  - **1.3 止损——-5% 采纳(强)**:全网格 PF 最高(0.922)、回撤最低;-3% 过紧被均值回归扫损(-37% 最差);印证归因(13 笔破 -5% 未止损占亏损 85%),回测/纪律/券商条件单三者一致。
  - **P8 时间退出——hold=5 采纳(中强)**:hold=2 灾难(-44.7%),3/4/5 缓坡 PF~0.92,hold=5 最优;**印证 4–7 自然日打平桶**。
  - **P7 回落止盈——5% 弱采纳(弱)**:0.922→0.931 小幅优、网格非单调,待 walk-forward 复核。
  - **P9 冷却期——否决(中,网格反证)**:0/5/10/20 强非单调,归因初值 10 天最差;均值回归下"刚割肉的正要反弹"。行为软纪律可留,不作系统硬规则。
  - **P10 仓位——纪律值采纳(中)**:单笔 2 万/≤5 只/敞口 60%;敞口越低越少亏(单调,又一"无正 edge"铁证);次周减半空效应。
  - **P2 板块年龄——待观察(弱)**:概念板块首拉(同花顺 `ths_index`/`ths_daily`/`ths_member`,409 板块/51 万板块日线/7.2 万成分,落 `data/parquet/ths_*.parquet`);板块级早期动量微弱(启动 1–5 天前瞻 +0.2~0.37%≈成本量级)、"4–5 天降权"不成立;`ths_member` 仅当前快照→个股映射被幸存者污染无法干净回测。宜作报告层软加权(§2.3),延后严谨回测。
  - **P1 市场过滤器(争议项)** 与 **次周减半(挂起项)** 不由 builder 拍板,证据摘要见下与报告「待拍板项」。

  **规则 v1 与验收(诚实,半达标)**:样本外(2025-2026)规则 v1 **-10.7%、PF 0.814、回撤 14.5%**——**大幅跑赢 dummy 基准(-65.3%,PF 0.601)与裸纪律基线(-98.6%),但盈利因子 <1(仍净亏)**。**「跑赢 dummy」达成、「PF>1」未达成**。诚实结论:**规则 v1 是经验证的减损纪律系统,非正 alpha**——日线 2–5 日母战法无正 net edge(A 股此频率均值回归),把 naive 短线的 -65%~-99% 爆亏压成 -11% 慢渗。**有意不为凑 PF>1 过拟合**(数据窥探禁令)。

  **两个用户拍板项证据摘要**:
  1. **MA20 市场过滤器(P1)**:回顾性分层强烈支持(SSE≤MA20 段吞掉几乎全部亏损),**但实时开仓闸门样本内外双双更差**(样本内 PF 0.931→0.818、样本外 0.837→0.719)——MA20 滞后(站上才买=追高),条件均值≠可交易规则。**建议不采纳**,市场择时改走更快的情绪信号(阶段 2 情绪仪表盘)。
  2. **次周单笔减半(P10 挂起)**:开/关两版回测**逐位相同=空效应**(敞口 60% + -5% 止损下单周实现亏损极少达 5%×12 万,几乎不触发)。**可作零成本行为安全阀纳入,但不指望收益**;阈值口径用 5%(区别于已采纳的 2% 强制复盘线)。

  **工程增量**:`research/` 研究包 + `research/stage1_report.md`;`neckline/strategy/momentum.py`(母战法可配置策略,19 单测)、`brain.py`(大脑版本表读写,4 单测)、`db.py` 加 `strategy_versions` 表;引擎加 `load_adjusted_daily` 注入(参数网格提速,注入==自算等值单测);tushare 加 THS 概念三接口(无 token 降级单测)。**全量 pytest 全绿(见完工提交)**。

  **遗留问题(供阶段 2+)**:① 母战法正 edge 悬而未决——更快情绪/资金信号(moneyflow 仅 2023-09 起、非 P1-P10 因子故未纳入)与 LLM 消息面审判(回测盲区)是开放的 alpha 路径,rule v1 定位为风控地基;② 板块年龄个股级回测受阻于 `ths_member` 无历史成分(需时点成分数据源);③ 禁高弹样本内过拟合的教训——调参门禁(§2.6)务必样本外优先;④ 回落止盈 5% 证据弱,walk-forward 重点复核;⑤ 正确的实时市场择时形态(替代被否的 MA20 闸门)是阶段 2 开放项。

- **2026-07-20 · 阶段1收口拍板(v0.2)**:用户三项拍板——① MA20 市场过滤器**否决定案**(回顾相关真实但实时闸门不可交易,择时职责归情绪仪表盘);② 「次周单笔减半」挂起项**否决**(回测空效应 + 用户不要,永久关闭);③ 免独立 review,直接进阶段 2。§2.1/2.2/2.3 已同步标注;现行策略内核唯一权威 = SQLite 大脑 `strategy_versions.v1`。阶段 2(盘后报告管线)开工。

- **2026-07-20 · 阶段2完工(盘后报告管线,§2.1–2.6)**:分12次分块 commit 交付,全量 **pytest 263 passed**(阶段1收尾 147 → 新增 116,0 回归;含施工中自查发现并修复的1处信息源缺口——候选热门板块展示补上板块年龄数字,详见对应 commit)。

  **验收标准逐项(plan §五 阶段2)**:
  1. **对最近 N 个交易日能生成完整 16:00 报告**——达成。真实数据跑通 2026-07-14~07-17 共 4 个交易日(本地已backfill 的最新连续交易日;07-20 当日 daily 尚未发布,已用 `daily_update.py` 尝试拉取确认),单次生成 **~1.6 秒**。样张见完工报告。
  2. **候选评分与阶段1回测信号同码一致**——达成,且不止靠人工比对,已写自动化测试锁死(`tests/test_report_consistency.py`):`report/candidates.py`(喂"今日"单日面板)与 `strategy/momentum.MomentumStrategy`(喂历史区间面板)在同一交易日、同一规则下选出**逐位相同**的候选集合,末日与区间中段各验一次,ST/创业板剔除两条跑道也逐位相同。
  3. **前10只 LLM 返合法自由文本且否决权生效**——机制达成,`judge.py` 输出"结论:通过/否决"单行收尾 + 自由叙述正文,解析失败保守判否决(否决权覆盖到"格式异常"场景,不留漏洞);**因用户未提供 LLM key,"真调用"本身未活体验证**,当前实测路径是"无 key → 未激活占位"(见下第4条)。
  4. **情绪仪表盘输出三态仓位额度**——达成。真实数据:07-14(涨停87/跌停29/炸板20%)→半额、07-15(73/39/30%)→半额、07-16(48/41/37%)→半额、**07-17(涨停34/跌停212/炸板28%)→休息**——07-17 恰是阶段0记录的施工期两次大跌之一,仪表盘据涨跌停宽度正确识别为最弱一档,是一次意外但有说服力的真实验证。
  5. **报告可对历史日回放**——达成。CLI 手工验证4个历史交易日;`test_report_consistency.py` 另用合成行情验证"同一份代码在不同回放日给出行情相符的不同结果"(不是罐头答案)。

  **关键口径决策(用户拍板/阶段1判决,已按口径实现,无偏离)**:①候选评分管线直接调用 `neckline.strategy.momentum.build_entry_mask`(阶段2从原 `MomentumStrategy._build_entry_mask` 实例方法提炼出的模块级纯函数,行为不变,19条原单测原样全绿),未在 `report/` 里另写信号逻辑;②MA20 市场过滤器未实现(已否决定案);③「次周减半」未实现(已否决);④板块年龄只做报告层软加权(早期1-5天小额加分,不做衰减曲线——回测未见衰减规律,不编造),不进硬评分;⑤仓位额度阈值第一版启发式,报告文案原样标注"未回测,实盘归因迭代中";⑥四件套的证伪条件用价量结构写死(结构化 dict + 自然语言),供阶段3哨兵消费。

  **LLM 层设计说明(§3.4)**:GLM(智谱)/Kimi(Moonshot)均走 OpenAI 兼容 `chat/completions`协议,endpoint/模型名/联网搜索 tool schema 于施工期(2026-07-20)网络核实官方文档(`docs.bigmodel.cn`/`platform.moonshot.cn`),模型名 `glm-5.2`/`kimi-k3` 与官方示例吻合;GLM 联网搜索一轮出结果(响应顶层 `web_search` 数组),Kimi `$web_search` 走"tool_calls→原样回传 arguments→再调一次"协议。**诚实声明:全部基于文档,无 key 无法活体验证**,拿到 key 后建议先跑一次真连烟雾测试(手工脚本,非 pytest)。降级链(缺 key/httpx未装/超时重试/非200/非法JSON/结构缺字段/空内容/工具轮数封顶)已用32个 `httpx.MockTransport` 单测覆盖,是当前唯一能验证的路径,也是本项目现状(`.env` 只有 `TUSHARE_TOKEN`)下唯一会走到的真实路径。

  **无 key 降级链验证(真实环境)**:`get_provider(真实settings)` 现读 `.env` 恒返回 `None`(已有单测锁死这一断言,防止未来 `.env` 被改动后此路径静默失效不被发现);`scripts/report.py` 对 2026-07-17 的真实运行中,前10只候选的 LLM 审判全部正确输出"⏸ 未激活"占位 + "LLM 未激活(.env 未配置 LLM_PROVIDER/LLM_API_KEY),本候选未经过 LLM 审判,仅供参考,不构成否决或通过"文案,`llm_judgments` 表落库 `degraded=1`,全链路未抛异常。

  **工程增量**:`neckline/report/`(sentiment/sectors/candidates/pipeline/render/store 六模块)、`neckline/llm/`(base/openai_compat/factory/judge + providers/glm/kimi)、`neckline/data/top_list.py`(龙虎榜现拉现缓存)、`scripts/report.py` CLI;`db.py` 新增 `reports`/`llm_judgments` 表;`tushare_client.py` 新增 `ts_top_list`;`momentum.py` 提炼 `build_entry_mask` 模块函数;`requirements.txt` 新增 `httpx==0.28.1`;`tests/conftest.py` 新增 `insert_stock_basic`/`insert_namechange`/`write_flat_parquet`/`seed_active_rule_v1`/`seed_synthetic_market` 五个共享测试夹具。

  **设计决策说明(非偏离,plan 留白处的实现选择)**:①`top_list`(龙虎榜)未纳入 `backfill.py`/`daily_update.py` 的全市场批量落地流程,改为 `report/pipeline.py` 现拉现缓存(该数据体量小、只在生成报告的当天才需要,现拉现缓存足够且更省 API 配额;历史批量回填非本阶段候选评分刚需)——若阶段4需要"审判结果 vs 实际3日收益"事后归因(§2.4 提到的 candidate_outcomes 思路),届时可能需要把 `top_list` 也接入每日增量,记入 Backlog。②HTML 报告未实现(plan §2.5 写"可加 HTML",为可选项,markdown 已满足验收);若阶段4客户端需要网页展示,届时再评估用 markdown 渲染库还是自写模板。

  **遗留问题(供阶段3+参考)**:① GLM/Kimi 真调用未活体验证(需用户提供 key 后补一次烟雾测试,可能需要微调 endpoint/字段解析);② 情绪仪表盘三态阈值第一版启发式(§2.3 本身要求诚实标注,非本阶段应交付而未交付的债,但阶段3+ 应持续用实盘归因校正);③ 龙虎榜历史批量回填未做(见上设计决策说明);④ 板块热度加分(`EARLY_AGE_BONUS=3.0`)与情绪仪表盘的三态阈值一样是命中即用的启发式常量,均未过回测,与规则v1(P1-P10 过堂定值)的证据基础不在同一量级,报告渲染层已用免责声明区分,但用户判断候选时应意识到这一层次差异;⑤ 未做独立 `review`(用户在阶段1收口时已拍板"直接进阶段2"未强制要求,阶段2风险等级低于阶段1资金逻辑高危区,但 LLM 降级链是新增关键路径,建议阶段3开工前视精力决定是否补一次)。

- **2026-07-20 · 阶段3完工(盘中哨兵,§2.4)**:分14次分块 commit 交付。全量 **pytest 456 passed**(阶段2收尾 263 → 新增193,0 回归)。

  **验收标准逐项(plan §五 阶段3)**:
  1. **交易时段实盘轮询,四哨兵各能触发对应推送(盘中真验)**——**部分达成,诚实降级为历史回放式冒烟**:今天(2026-07-20)施工日实测是周日(系统日历显示为周一但已过15:00收盘,两种情况下当天都没有可用的盘中窗口),无法做真正的活体盘中轮询。已按用户任务指令做**同码历史回放冒烟**替代:`scripts/smoke_sentinel.py` 用真实backfill数据(`report_day=2026-07-16` 走真实`build_report`生成候选,`today=2026-07-17`——阶段0/2记录的施工期真实大跌日,涨停34/跌停212/炸板28%)反推三个检查点(早盘09:45/盘中10:35/尾盘14:50,尾盘用真实收盘价与真实高低点)喂给与生产**完全同一份**`engine.run_tick`。真实运行结果:退潮哨兵经"热门板块可比个股平均跌幅-4.3%(样本11只)"触发、买点哨兵因退潮生效被正确抑制(0信号)、证伪哨兵抓住18~20只VWAP跌破/低开不回、持仓哨兵抓住合成的"跌幅最大"持仓(920117.BJ)在早盘即已跌破止损线、二次检查点验证防重生效(0新推送,去重跳过19条)、尾盘检查点2只新增证伪信号正确推送(新事件不受已推事件影响)。**真正的盘中活体验证留给用户下一个交易日跑 `scripts/sentinel.py` 实测**,见下方"欠账"。
  2. **证伪哨兵仅用价量**——达成。`invalidation.py`/`invalidation_spec` 全程只碰 `quote.price/open/pre_close/high/low/volume/amount` 与折算量比,不读 `moneyflow_dc` 或任何资金流字段,代码结构上不可能违反(该模块整个文件没有 import 任何资金面数据接口)。
  3. **退潮刹车能触发**——达成,见上第1条真实冒烟结果(经"主线板块跳水"触发);另有14个单测(`test_sentinel_retreat.py`)覆盖炸板率绝对线/相对昨晚飙升/跌停家数绝对数与占比/板块跳水四条独立路径各自的触发与不触发边界。
  4. **单测断言无新票推荐路径**——达成。`test_sentinel_engine.py::TestNeverRecommendsNewStocks` 两个直接断言:①非候选代码即便行情完美满足买点确认条件也不会被评估/推送;②任意一拍 entry_signals 的代码集合必为"昨晚报告候选代码集合"的子集(结构性不变量断言,不依赖具体构造)。

  **四哨兵判定规则清单(触发条件字面化,均为纯函数,均有对应单测文件)**:

  1. **买点哨兵**(`sentinel/entry.py::check_entry`)——两层都过才推送:① 触达 `Candidate.entry_spec`(阶段2报告生成时写死)的买点:pullback 型现价≥ma10(不破位)且开盘涨幅≤2%(不追高开缺口);breakout 型现价>前20日收盘高点(platform_high);either 型二选一。② 确认条件:量能折算(`intraday_vol_ratio`,开盘60分钟内视为数据不足不判断)——pullback 要求≥0.8倍(不是地量死水,**未回测启发式**),breakout 复用报告当晚已定的`breakout_vol_expand`门槛(不新造数字);且现价≥当日VWAP。开盘头5分钟(`MIN_STRUCTURAL_ELAPSED_MINUTES`)结构性判断一律不做。一天每只候选最多推1次(`event_key="trigger"`)。
  2. **退潮哨兵**(`sentinel/retreat.py::check_retreat`)——命中任一条即触发,一天最多触发1次(全天保持生效,不会"退潮又恢复"):① 关注池炸板率≥50%(样本需≥5只) **或** 炸板率较昨晚报告飙升≥20个百分点;② 关注池跌停家数≥5只 **或** 占比≥15%;③ 关注池内标"今日热门板块"的候选平均盘中跌幅≤-3%。**关注池不是全市场**(候选+持仓+昨日涨停股代理样本,详见下方设计决策说明)。触发后**联动抑制买点哨兵**(`engine.py` 编排层显式判断,不是隐式巧合)。
  3. **持仓哨兵**(`sentinel/holding.py::evaluate_holding`)——三条独立,同一持仓可同时命中多条,各自独立防重:① 止损逼近:回撤(相对买入价)达到"策略大脑现役`stop_pct`−2个百分点"即预警,已破位则文案升级为"已跌破止损线,若条件单未成交请立即人工确认"(系统永不代下单/撤单/改止损);② 回落止盈:仅当该持仓历史或当前价确实创出过高于买入价的峰值(有"盈"可回落)才判断,现价较峰值回落达到策略大脑`take_profit_retrace`才触发;③ 板块跳水预警:该持仓所属概念板块内、恰好也在关注池中的其它个股平均盘中跌幅≤-3%(无可比样本时诚实返回"无数据",不是"板块健康")。
  4. **证伪哨兵**(`sentinel/invalidation.py::check_invalidation`)——命中任一条即推送,一天每只候选最多推1次:① 开盘涨幅≤-2%且截至当前仍未翻红(现价<昨收);② 现价跌破当日VWAP;③ 折算量比<0.8倍(地量无接力);④ 折算量比>3.0倍(异常放量疑似出货)。四条阈值全部来自阶段2 `report/candidates.py` 报告生成时写死的 `invalidation_spec`,本阶段未新造。同样受开盘头5分钟结构性判断门槛约束,不受退潮抑制(剔除勿进任何时候都是有效信息)。

  **防重与重启语义**:SQLite `sentinel_events` 表(`neckline/db.py`),唯一约束 `(trade_date, sentinel, ts_code, event_key)`——落库而非进程内存态,是"进程重启不重复推当日已推事件"的关键(内存态在脚本重启后清零,达不到这个要求)。去重粒度按哨兵语义定制:买点/证伪每候选一天1次(`event_key="trigger"`);持仓的止损/止盈/板块跳水三个`event_key`互相独立;退潮是市场级事件(`ts_code=""`,`event_key="brake"`)一天1次且全天保持生效。`neckline.sentinel.dedup` 五个函数、11个单测覆盖(含"模拟进程重启后仍能查到"的显式用例)。

  **推送通道说明(§3.5/§3.6)**:抽象基类 `PushChannel`(`neckline/sentinel/channels.py`),`send()`签名统一含可选`transport`便于单测注入。三实现:①`ConsoleChannel`(默认,打日志,恒成功);②`BarkChannel`(POST JSON到`.env`的`BARK_URL`,含`title/body/group`,critical级别加`level=critical/sound=alarm`;未配置`BARK_URL`时`send()`直接返回False优雅跳过,不发起任何网络请求)——**与阶段2 GLM/Kimi同类处境,payload字段基于Bark官方文档实现,无真实`BARK_URL`做过活体验证**;③`MacNotifyChannel`(可选,`osascript`,非mac/无`osascript`静默降级为False)。`default_channels()`按`.env`组装(Console恒在,Bark视`BARK_URL`是否配置),`push_all()`编排"单通道异常不拖累其它通道"。**客户端/推送载体本身仍未拍板**(§3.5三选项待阶段4前用户定),Bark只是先备着,成本极低。

  **合成盘中冒烟结果(`scripts/smoke_sentinel.py`)**:详见上方验收标准第1条;完整日志见施工记录。关键数字:候选20只(策略v1)、情绪仪表盘涨停48/跌停41/炸板率37%/半额(2026-07-16真实数据);关注池候选20+持仓2(合成,一只"跌幅最大"一只"涨跌平缓",买入价取真实历史收盘价而非瞎编)+昨日涨停股代理样本48=去重70只代码;三检查点全部拉到70/70只行情(合成数据,非真实网络拉取,但走的是真实`Quote`结构+真实`engine.run_tick`)。脚本运行时复制真实`data/neckline.db`到临时文件,持仓/事件写入只落临时副本,Parquet只读用真实数据,跑完自动清理——已核实真实生产库(`positions`/`sentinel_events`/`reports`表)全程未被污染。

  **关键口径决策(非偏离,plan留白处的实现选择)**:①`Candidate`新增`entry_spec`结构化字段(呼应阶段2已有的`invalidation_spec`)——阶段2报告只结构化了证伪条件,买点条件只有自然语言`entry_plan_text`,阶段3需要一个机器可判的买点触发条件,遂在`report/candidates.py`补一个小函数,字段全部复用`entry_plan_text`已经在读的`ma10`/`prev_close_max_20d`/`cfg.breakout_vol_expand`,不新造任何数字,随`public_dict()`落库;②`data/limit_derived.py`新增三个标量纯函数(`resolve_limit_pct`/`resolve_exempt_days`/`compute_intraday_limit_prices`)供哨兵逐票算涨跌停价,与既有向量化EOD批算共用同一组常量,单测互相对拍,未修改任何既有函数(零回归风险);③退潮哨兵的市场宽度统计用"关注池"(候选+持仓+昨日涨停股,容量上限200)代理全市场,**非字面意义的全市场轮询**——理由是免费源(新浪/腾讯)未公开单请求代码数上限与限流阈值,持续6.5小时对数千代码高频轮询是明显偏离个人量化助手正常用量的重负载,长期这样打有很高的限流/封禁风险;代价是样本量小、阈值不能与EOD报告的同名字段直接比量级,已在模块头详细说明,全部阈值标注未回测启发式;④持仓哨兵的止损/回落止盈阈值从策略大脑`strategy_versions`现役版本读(`brain.get_active()`),不是硬编`-5%`,自动跟随未来规则版本调整(§2.6"策略进化带笼子"的题中之义);⑤`scripts/sentinel.py`的"待机"实现为"未到09:30时`sleep`到开盘"而非"轮询等待",更省资源;非交易日/已过15:00两种情形都是直接退出,不做任何轮询尝试。

  **工程增量**:`neckline/sentinel/` 新包(quotes/intraday/universe/positions/dedup/entry/invalidation/holding/retreat/channels/engine,11模块);`neckline/data/limit_derived.py`新增3个标量函数;`neckline/report/candidates.py`新增`entry_spec`字段;`neckline/config`新增`bark_url`;`db.py`新增`positions`/`sentinel_events`两张表;`scripts/positions.py`(持仓CLI)、`scripts/sentinel.py`(常驻脚本)、`scripts/smoke_sentinel.py`(合成盘中冒烟)三个新脚本。施工期额外发现并修复一处性能/日志噪音坑:`is_new_stock_exempt`对`list_date`早于交易日历DB覆盖范围(2015年前,A股大量主板老股属此类)的股票,若不加自然日预筛会退化为逐日`is_trading_day`循环+刷屏warning(合成单测用短日期区间测不出来,靠`smoke_sentinel.py`跑真实历史数据才暴露),已修复并补单测,教训记入项目根新建的`CLAUDE.md`。

  **欠账(真盘中验证,如实记录)**:①**本阶段全部验证止步于单测+合成历史数据回放,没有一分钟是在真实交易时段用真实免费源(新浪/腾讯)拉过活体行情**——`scripts/sentinel.py`本身在真实系统时间(过15:00收盘)下跑过`--once`验证了"非交易时段优雅退出"这一条路径,但09:30-15:00轮询本身、真实新浪/腾讯响应格式是否与2026-06-18的样例报文一致、真实网络延迟下的批量拉价耗时,均未验证;②买点/证伪的量能折算阈值(pullback 0.8倍下限)、持仓止损预警缓冲(2个百分点)、退潮哨兵三条阈值(炸板率50%/相对飙升20pp/跌停家数5只或15%/板块跳水-3%)全部是未回测的第一版启发式,需要用户下一个交易日实测后根据真实表现校正——这些常量已全部收成命名常量并在模块头逐一标注"未回测",定点修改成本很低;③Bark推送的payload格式基于官方文档,未用真实`BARK_URL`验证过。这三类欠账均已在"当前状态"与本条目开头如实标注,不是本阶段应交付而未交付的债——是今天(2026-07-20)客观不具备交易时段这一约束下,已按用户任务指令做的最佳替代。

- **2026-07-20 · 阶段 4 立项(v0.3,客户端 + 云端化 + 问询台 + 周复盘)**:用户五项拍板并入设计共识——① 客户端方案 A(SwiftUI iOS+macOS 双端 + APNs,新 App / Bundle ID `top.linotsai.neckline`,§3.5);② 后端复用 LinoN 基建上 hz ECS(nginx `ln.linotsai.top` / APNs `.p8` 账号级密钥 / 独立端口 8002 + 用户 `neckline` + `/opt/neckline`,LinoN 接班后退役,§3.6);③ 哨兵与后端跑云上,内存硬约束下数据管线分工以 4B.1 实测门禁定(方案 A 全在 ECS / 方案 B Mac 落数 rsync 产物上云,`neckline.db` 恒 ECS 权威);④ APNs 只推「16:00 报告」+「退潮刹车」两类、其余哨兵事件只进 App 盘中看板(§2.4 推翻阶段 3 默认推送);⑤ App 四板块(今日计划 / 盘中看板 / 问询台 / 设置)+ macOS 周复盘工作台(拖入交割单对账)。§五 阶段 4 填入完整施工图(4A 后端 API → 4B 云端化+部署 → 4C 客户端双端 → 4D 周复盘工作台 → 4E 端到端联调+APNs 真机),每块验收标准 + 高危区标注(鉴权/APNs/部署脚本/LLM key 存取 点名 @builder-pro);阶段 3 三类欠账(真盘中冒烟 / 阈值校正 / GLM-Kimi 与 Bark 活体)挂账进 4E 不丢;§七 Backlog 两项([拍板]客户端 / [后期决策]哨兵部署)结案,§八 补阶段 4 用户网页操作清单(新 App ID+Push / LLM key / 每周交割单)。**同码不重写铁律**:阶段 4 只加服务外壳 + 客户端 + 云端化 + 对账,不重抄领域规则。

- **2026-07-20 · 阶段 4A 完工(后端 API 服务化)**:新包 `neckline/api/`(FastAPI 脊椎),分块 commit 交付,全量 **pytest 456 → 527 passed**(+71,0 回归)。**同码不重写**:报告/候选评分/四哨兵/涨跌停/板块分类全部复用阶段 0–3 现有模块,API 层只加服务外壳。

  **验收标准逐项(plan 4A)**:
  1. **本地 uvicorn 起 → curl 全端点闭环**——达成。`scripts/smoke_api.sh`(真 uvicorn + 临时库隔离)跑通:health 免鉴权 200 / 无·错 token 401 / 报告·看板 degraded 空态 / 设置 GET·PUT llm(key 不回明文)·PUT push / 持仓 open→list→close→重复 close 404 / 问询台二值裁决 / 设备注册。冒烟期真实新浪/腾讯顺带拉到 600519.SH 活体价(实时源路径真验)。
  2. **health 200 免鉴权 / 无 token 端点 401**——达成(`test_api_auth`:health 免鉴权、全保护端点无·错 token 401、`hmac.compare_digest` 全等比对、startup fail-fast len<16 抛错)。
  3. **报告与看板返真实阶段 2/3 数据形状**——达成。`/report/latest`·`/report?date`(历史回放)读 `reports` 表**不重算**,四件套(buyPoint/stop/target/invalidation)+ 形态标签 + 前 10 只 LLM 审判贴合;`/board` 读当日 `sentinel_events` 聚合(退潮进红条、买点·证伪·持仓进事件列表,**不进 APNs**——§2.4 拍板落点)。
  4. **开清仓走台账**——达成,复用 `sentinel.positions`(不重写台账);派生止损线 = buy×0.95(§2.1 -5% 单一常量);重复清仓 404。
  5. **问询台丢一票返二值裁决 + 依据、初审通过写 `inquiry_pool`**——达成。确定性纪律核对(读 `brain.get_active()` 现役规则 + `research.panel` 选股域 + `strategy.signals` 禁买预测,**同码**)+ 同码评分(`build_entry_mask`/`_base_score_expr`)+ 板块年龄 → LLM 段(原生联网搜索,预注入实时取数/重算作上下文)。**「永不买」不变量三重保险**:裁决枚举只两值(`Literal["不符合","初审通过进海选池"]`)+ system prompt guardrail + **代码级裁决**(不从 LLM 自由文本提取"买")。缺 key 优雅降级(确定性照跑,LLM 段占位)。合成三票单测:主板通过→初审通过写池 / *ST 剔除 / 创业板(高弹)剔除;LLM 显式否决翻不符合、硬性不符合不劳 LLM;LLM 疯狂喊"买"裁决仍恒二值。
  6. **`PUT /settings/llm` 后 `get_provider()` 现读 DB 生效(key 不回传明文)**——达成(🔴)。`get_provider` 解析改 **DB 覆盖 → `.env` 兜底**(`settings_store.resolve_llm`),运行时生效不重启;`GET /settings` 只回 `llmKeySet:bool`,key 绝不回明文 / 绝不进日志 / 空 key 视为未设降级;provider 白名单 schema `Literal` + store 双校验(非法 provider 422)。

  **工程增量**:`neckline/api/`(deps/schemas/app/inquiry/notify/stores 六模块)+ `neckline/push/apns.py`(复用 LinoN token-based JWT ES256/HTTP2/可注入 transport)+ `neckline/settings_store.py`(app_settings CRUD,🔴 LLM key 存取);`db.py` 新增 4 表(`app_settings` 单行/`devices`/`inquiry_pool`/`reviews`);`config` 新增 `api_token`/`apns_*` + `DB_PATH` env 覆盖 + 容忍不可读 `.env`;`llm/factory.get_provider` 改走 `resolve_llm`;`report/store` 加 `latest_report_date`/`load_report_by_str`;`sentinel/dedup` 加看板读取;`report.py` 加 `--notify`;`requirements.txt` 新增 fastapi/uvicorn[standard]/pydantic/PyJWT/cryptography/h2(版本参照 LinoN 钉死);`scripts/smoke_api.sh` 新。**新增 71 单测**覆盖鉴权/设置(key 不泄漏 + DB 覆盖)/持仓/报告/看板/问询台二值「永不买」不变量/APNs ES256 签名/notify 只两类推送。

- **2026-07-20 · 阶段 4B 完工(云端化 + hz ECS 部署,方案 A)**:后端 + 哨兵上 hz 云 ECS 常驻,与 LinoN(8001)**共存,全程未动 `linon.service` 或其 nginx 站点**(硬约束)。

  **验收标准逐项(plan 4B)**:
  1. **内存门禁三项峰值实测 + 架构方案二选一写死**——达成,**定案方案 A(全云)**。实测(GNU `time -v` Max RSS + 系统级 `free -m` 采样):pip 安装峰 used 1032MB/swap 0;`report.py` 单日 **RSS 277MB / 16.8s**;`daily_update.py` 单日 **RSS 262MB / 30.6s**(含 limit_derived 尾窗 187486 行 0.5s)。两者 **peak_swap=0、min_avail≥501MB**,不把邻居(lf/fiscal/pg)压进 swap、留 ~500MB 安全余量 → 满足方案 A 判据,ECS 自跑 daily+report timer(无需回退方案 B)。**两方案共同不变量落地**:`neckline.db` 业务台账 ECS 权威,`sync_data.sh` 只推 `data/parquet/`、显式排 `*.db` 绝不覆盖;全量六年 backfill 恒在 Mac 一次性跑,ECS 只增量。
  2. **`neckline.service` active + health 200**——达成。systemd 单 unit(User=neckline,uvicorn `127.0.0.1:8002`)active + enabled,idle RSS ~82MB;`curl 127.0.0.1:8002/api/v1/health` → `{"status":"ok"}`。**公网 HTTPS 未接**——`ln.linotsai.top` 归 LinoN(8001),切 8002 是 4E 接班动作,4B 绝不动 linon 站点(参考 nginx 配置已写入 `deploy/nginx-neckline.conf` 待 4E)。ECS 实测已用本地 curl + 真实 API_TOKEN 走通全鉴权端点(报告 20260717/20 候选、问询台 600519.SH 初审通过·300750.SZ 创业板不符合)。
  3. **哨兵折进 lifespan asyncio 任务**——达成。`app.py` lifespan 起 `_sentinel_loop`(交易时段 60s 一拍 `run_tick`(`asyncio.to_thread` 不卡事件循环)、午休 300s、非交易时段 300s 优雅待机;退潮首次触发 → `notify.push_retreat_brake`)。测试注入 `ENABLE_SENTINEL`/`NECKLINE_ENABLE_SENTINEL=0` 关轮询。**真盘中活体验证(交易时段真跑)是 4E 欠账**(4B 部署日 19:xx 非交易时段,哨兵按设计待机)。
  4. **16:00 报告定时 + APNs 报告推送就位**——达成(顺序修正:report 依赖 daily 先拉数,故 **daily 16:05 → report 16:35**,plan 原文「report 16:00/daily 16:30」把依赖写反,已在 timer 注释说明)。`neckline-daily.timer`(16:05 daily_update)+ `neckline-report.timer`(16:35 report.py --notify,oneshot 跑完释放内存)active + enabled,下次触发 Tue 16:05/16:35。
  5. **APNs 层单测过(真推留 4E)**——达成。`neckline/push/apns.py` 复用 LinoN 姿势,`test_apns`(临时 EC P-256 key 验 ES256 签名 + kid/iss、无配置/缺 .p8 → None、缓存/过期重签、send_push 注入 transport 成功·失败·sandbox 网关·topic=新 Bundle ID)+ `test_notify`(**只两个推送入口**结构保证「只推两类」、开关门控、无设备/无配置跳过、单设备失败不拖累)。真机真推留 4E(无 device token)。
  6. **部署脚本经演练 + 真跑,各坑不复发**——达成。`sync_code.sh` **exclude 锚定根 `/data/`**(本地 dry-run 已验:`neckline/data/` 包 8 文件传、顶层 `data/parquet` 0 传——LinoN 坑 4)、GNU rsync 3.x 守卫、setgid/pyc/secret 复原提示;`sync_data.sh` 反向只传 parquet 排 `*.db`;pip 阿里云镜像;tushare `pro_api` 直传(不炸 nologin 家目录)。

  **工程增量**:`scripts/`(setup.sh/sync_code.sh/sync_data.sh)+ `deploy/`(neckline.service / neckline-report.{service,timer} / neckline-daily.{service,timer} / nginx-neckline.conf 参考)+ `.env.example`。`config` 加 `DB_PATH` env + 容忍不可读 `.env`(ECS 实测:.env 600 neckline:neckline,deploy 跑维护命令时 load_dotenv 抛 PermissionError,已加 try/except);`setup.sh` init_schema 以 neckline 用户建库(保证服务 User=neckline 可写 WAL)。ECS 独立 bootstrap 参考数据:trade_cal 4383 / stock_basic 5866 / namechange 14139 / strategy_versions.v1(从 Mac 导出重放,不在 ECS 跑研究)。

- **2026-07-20 · 阶段4C完工(SwiftUI iOS+macOS 双端客户端)**:分2次 commit 交付(骨架+单测 / 联调修正+集成测试)。新工程 `client/`(xcodegen multiplatform 单 target,Bundle ID `top.linotsai.neckline`,deploymentTarget iOS/macOS 26)。

  **验收标准逐项(plan §五 阶段4C)**:
  1. **双端 `xcodebuild` 各 `BUILD SUCCEEDED`**——达成。iOS Simulator(`LinoJ-iPhone16Pro`)与 macOS 目标均 `CODE_SIGNING_ALLOWED=NO` 构建通过,反复验证多轮(每次改动后回归)零报错。
  2. **四板块渲染真实后端数据,端到端走通**——达成,且不是空转 UI:本地起 dev uvicorn(隔离临时 DB + 固定占位 token,同 `scripts/smoke_api.sh` 惯例)、把 `trade_cal`/`strategy_versions`/`stock_basic`/`namechange` 四张只读参考表从真实 `data/neckline.db` 拷进隔离库(不碰用户真实台账),再跑 `scripts/report.py 20260717` 用**真实 backfill 六年数据**生成一份真报告(非手造 fixture)。逐端点真请求验证:`report/latest` 返 20 真候选(四件套含 -5% 止损口径、真实板块/形态标签)、`board` 聚合退潮红条+3类哨兵事件(手工用 `sentinel/dedup.record_pushed` 种入,同后端自身测试姿势)、`positions` 开仓(拉到真实新浪/腾讯实时价 ¥1327.5)/清仓/重复清仓 404、`inquiry` 对 600519.SH 真问出「初审通过进海选池」(reply 原文含「这不是买入建议」)、`settings` GET→PUT llm(key 不回传明文)→PUT push 全链路。iOS Simulator 四板块（今日计划/盘中看板/问询台/设置）真机截图确认渲染正确;macOS 二进制启动稳定运行(未能截图,见下方遗留说明)。
  3. **问询台无「买」路径(UI + 单测断言)**——达成。`InquiryView` 结构上只展示 `VerdictBadge`(纯文本徽标,label 只可能是「不符合」/「初审通过进海选池」二值)+ 依据列表 + 自由对话回复,不存在任何下单/买入控件;`InquiryVerdict.enablesBuyAction` 恒 `false`(穷举写死,不看分支)配对抗性字符串单测(镜像后端 `test_verdict_always_binary_never_buy`)+ 真实网络请求断言。
  4. **设置屏改 LLM provider/key 后端生效、改推送开关生效**——达成,真实网络验证(非 mock):`putSettingsLLM` 后 `fetchSettings()` 立即反映新 provider + `llmKeySet=true`,明文 key 全程不回传;`putSettingsPush` 同样真实往返验证。key 输入框安全态——`llmKeyDraft` 从不用存量 key 预填,发送成功后立即清空草稿。
  5. **XCTest(iOS Simulator)含 `makeURL` query 门禁全绿**——达成。`URLGateTests` 断言 `?date=` 不被编码成 `%3F`(含反面对照测试,留证据防未来"优化"改回 `appendingPathComponent`)。
  6. **绿涨红跌一致**——达成。`DesignTokens.swift`(`NK.up`=绿/`NK.down`=红)全局唯一色源,持仓卡涨跌色、候选四件套均从此派生,未见任何红涨绿跌硬编码。

  **单测与构建数字**:全量 **38 个单测全绿**(`URLGateTests` 2 · `DTODecodeTests` 12,用 `URLProtocol` 网络桩注入对齐 `neckline/api/schemas.py` 的真实 JSON 样例 · `AppModelTests` 15(含问询台「永不买」不变量、退潮警示派生、候选板块码换算、交易日历、开仓表单校验)· `PushRoutingTests` 3(iOS 专属推送路由)· `IntegrationSmokeTests` 6(真实网络,探活失败自动 `XCTSkip` 不污染离线门禁,是当前唯一会真正联网的一组));双端 `xcodebuild BUILD SUCCEEDED`,iOS Simulator `TEST SUCCEEDED`。

  **联调中发现并修正的一处真实契约细节(非臆造)**:`CandidateOut.board` 服务端字面实测是英文枚举码(`MAIN`/`GEM`/`STAR`/`BSE`,唯一源 `neckline/data/board.py` 的 `Board` 枚举),不是中文名——若不做处理会在候选卡上直接显示英文码。已加 `Candidate.boardLabel` 做**纯展示层**四常量换算(未识别值原样透传,不新造分类逻辑、不改服务端),体现「板块分类唯一源」原则(不重新推导,只翻译展示文案)。

  **复用 vs 重写(按 plan §五 阶段4C 清单执行,无偏离)**:搬 `project.yml`/`DesignTokens.swift`(命名空间 `LN`→`NK`,颜色数值不变)/`AppConfig.swift`(key 前缀 `LN_`→`NK_`)/`APIClient.swift`(actor + `makeURL` 骨架,端点按 4A 契约重写)/`PushManager.swift`(类别从 LinoN 的 `HARDLINE` 动作按钮简化为 `REPORT`/`RETREAT` 两个无动作信息推送,§2.4 拍板)/`StaticTradingCalendar.swift`(**裁掉** LinoN 专属的「买入日=D1/`count==4`强平」持仓天数逻辑——Neckline 持仓是审计台账，无 D4 强平规则,只留日期解析 + 交易日判断);全新 `Models.swift`(领域模型)+ 全部 Views(今日计划/盘中看板/问询台/设置/macOS 周复盘工作台壳)。

  **坑吸收逐条落地**:①iOS ATS `127.0.0.1` 明文例外(`Info.plist`);②`APIClient` 全端点走 `makeURL`,**零处**使用 `appendingPathComponent`,+ 门禁单测;③改 `project.yml`/加 `.swift` 后全程 `xcodegen generate` 重生工程;④`RootView.task` 内 `model.bind(config:)` 先于 `refresh()`(两套 Scene 分支各一份 `.task`,未放 `.onAppear`);⑤`API_TOKEN` 走 UserDefaults→env→gitignored `LocalSecrets.plist` 优先级,不入源码;⑥平台分叉 Scene body 用两套独立 `#if os()` 分支(iOS `WindowGroup`+`AppDelegate` / macOS `WindowGroup`,未混写);⑦xcodebuild 验证双端(非仅 SwiftPM build);⑧XCTest 门禁走 iOS Simulator(`-destination 'platform=iOS Simulator,name=LinoJ-iPhone16Pro' test`),macOS 侧只 `build`;⑨本阶段视觉核对靠真机截图(`xcrun simctl io screenshot` + `SIMCTL_CHILD_NECKLINE_INITIAL_TAB`/`NECKLINE_INITIAL_TAB` 免交互切板块的 QA 钩子),未用到 ImageRenderer 离屏快照(视觉核对目标已用真机截图达成,故未额外加此层);⑩SwiftUI 动画三禁本阶段无复杂过渡动效场景,暂未触发。

  **两处小偏离(均记录 + 理由,未回 planner 单议因影响面小且是唯一合理解读)**:① `PositionOut.stopOrderChecked` 服务端 4A 恒回 `false`、无 PUT 端点持久化此字段(见 `neckline/api/app.py::list_positions` 硬编码),客户端遂实现为**本机会话内本地提醒勾选**(非跨端持久化,代码注释已写明"仅本机本次会话记忆"),等 4D/后续若要做真对账时再评估是否需要新增持久化端点。② macOS 端因本环境沙盒无 Screen Recording/Accessibility 权限,未能取得 macOS GUI 截图(iOS 截图 + 双端 `BUILD SUCCEEDED` + 同一份 SwiftUI 视图代码共享逻辑,已能佐证 macOS 渲染正确性,留用户下次用 Xcode 打开工程肉眼确认作为补充)。

  **遗留问题(供阶段4D/4E参考)**:①周复盘工作台目前只是壳(拖入文件回显文件名 + "待4D接入"提示,无实际解析/上传,如 plan 原文明确范围);②macOS 视觉核对因环境权限限制改用"双端 build + iOS 真机截图 + 稳定运行验证"的组合证据,非原计划的 ImageRenderer 离屏快照(该技巧仍可用,只是本次未必要);③真实 APNs 推送、真机 device token 注册、Apple Developer Push capability 手动确认均按 plan 留 4E;④GLM/Kimi 真调用 + 联网搜索协议假设仍待用户在设置屏填真 key 后首次验证(阶段2/4 累积欠账,不因4C客户端就绪而自动兑现,需要真 key)。

  **欠账(带进 4E)**:① **公网 HTTPS + LinoN 接班切换**(nginx 8001→8002 + linon 退役,联调通过后做,`deploy/nginx-neckline.conf` 已备);② **真盘中活体验证**(交易时段首次真跑 `run_tick` + 真实新浪/腾讯,兑现阶段 3 欠账①);③ **APNs 真机真推**(新 Bundle ID device token,4C 客户端建后);④ LLM/Bark 活体(用户 App 填 key 后);⑤ 问询台「初审通过」→ 报告端消费 `inquiry_pool` 扩 universe 的接线(§2.5 闭环的报告侧,4A 已写入池 + 提供读取,报告侧消费留 4E/报告管线小改,避免动阶段 2 评分代码引回归);⑥ 问询台 LLM「主动多轮 function-calling」形态未实现(以预注入取数/重算 + 原生搜索覆盖 plan 三能力,无 key 无法活体验证该形态,记此简化)。

- **2026-07-20 · 阶段4D完工(周复盘工作台·对账引擎)**:新包 `neckline/review/`(parse/reconcile/material/store 四模块),`neckline/api/` 新增 `POST /review/upload`(multipart)/`GET /review?week=`/`PUT /settings/review-col-map` 三端点,macOS `ReviewWorkbenchView` 从 4C 占位壳接通真实上传 + 表格化展示。全量 **pytest 527 → 621**(+94,0 回归),Swift 侧 **38 → 41 个单测**全绿,双端 `xcodebuild` **BUILD SUCCEEDED**。

  **验收标准逐项(plan §五 阶段4D)**:
  1. **拖入交割单生成对账周报(对账三查 + 周统计 + 强制复盘)**——达成。两家券商格式解析(格式一无代码列,靠"证券名称"反查 ts_code + 价格反推 `(|发生金额|-费用)/数量`;格式二零宽空格 strip + 代码补交易所后缀,复用 `sentinel.quotes.to_symbol` 不另写正则)→ 按(代码,日期序)FIFO 闭合回合 → 对账三查(①实际成交 vs 当日报告候选 / 问询台海选池 / 持仓台账;②止损纪律,-5% 容差带 [-6%,-4%] 跟随现役 `stop_pct` 联动,非写死绝对值;③章程执行——单笔 ≤2 万 / ≤5 只并发 / 敞口 ≤60% / 绿盘大阴线 & 距前高 & 次新 & 高弹题材四条禁买过滤 & 同票冷却,全部读 `strategy.brain.get_active()` 现役 config,未启用的过滤项天然 no-op 不硬编)→ 单周统计(胜率/盈利因子/盈亏比/费用/净盈亏/`realized_loss` 只累加亏损,同 `momentum.py::_consume_closed_trades` 的 `week_loss` 口径)→ 强制复盘(单周实现亏损 ≥ 总仓 2%,`FORCED_REVIEW_LOSS_FRAC` 与该口径对应,非另起阈值)。`scripts/smoke_review.sh` 用**真实 uvicorn + 真实 openpyxl 生成的 xlsx**(非 TestClient 模拟)跑通全链路:单笔 ¥150,000 超 2 万仓位上限 + 敞口 125% 超 60% 两条章程违纪均正确触发、-5.02% 卖出正确分类 `kept_stop`(容差带内)、周实现亏损 ¥7,560=总仓 6.3% 正确触发强制复盘、材料生成、落库 + `GET` 历史回放读到同一份结果、`review-col-map` 设置生效——逐条真实数据核验。
  2. **字段映射可配**——达成。`app_settings.review_col_map`(4A 已建字段,4D 首次消费)经新增 `PUT /settings/review-col-map` 端点写入,`parse_workbook` 按 `col_map` 覆盖内置默认列名(**格式判定本身也吃 col_map**——若不这样做,把判据列名也改了的券商格式会连"认出是哪种格式"都做不到,col_map 形同虚设,施工期直接测出这个坑并修正);直接单测 + API 端到端单测均覆盖"改列名后能吃通"。
  3. **解析异常不崩**——达成。未知格式 sheet / 说明性 sheet(无"交易日期"表头)/ 未知业务名称 / 证券名称反查失败或歧义 / 成交数量或日期缺失 / 格式一反推价格所需列缺失(见下"施工期踩坑")/ 非法 xlsx 字节,均降级为 `ParseWarning`(单行 / 单 sheet 跳过)或 `parseWarnings` 返回,不抛异常中断整份 / 整批解析。
  4. **pytest 覆盖三查各分支 + 强制复盘阈值边界**——达成,**94 个新测试**:`test_review_parse.py`(23,两格式解析 / 零宽空格 / 价格反推 / col_map / 表头探测 / 名称反查含歧义与 as-of 历史名)、`test_review_reconcile.py`(59,FIFO 闭合含跨 lot / 超卖 / 未平仓残留、止损纪律四态含边界值、单笔上限、并发持仓 / 敞口扫描线、四条禁买过滤复用 `strategy.signals` 同码验证、冷却期、周统计含"全赢→inf"与"全输→0"两个边界、强制复盘阈值恰好 / 差一点 / 超过三态、`run_weekly_review` 端到端)、`test_review_material.py`(3)、`test_api_review.py`(9,含鉴权 / 多文件合并解析 / 非法文件降级 / col_map 设置生效)。

  **客户端接线(macOS,iOS 不做,照 plan)**:`ReviewWorkbenchView` 从 4C 的占位壳接通真实上传——拖入 xlsx(可一次拖多份,`NSItemProvider` 异步读取包成 `async`)→ `APIClient.uploadReview`(手写 multipart/form-data,非 JSON body,60s 超时同问询台惯例)→ 展示:强制复盘横幅 + 周统计卡 + 确定性材料段落 + 违纪清单 + 计划/台账核对行 + 回合明细行 + 止损纪律行,多周切换用 chip 选择器。**本次任务范围内不做 4D.3 的 LLM 叙述叠加层**(任务指令原文明确"禁模板腔的 LLM 部分本块不做,纯确定性输出即可"),`neckline/review/material.py` 只产出确定性材料,代码注释记录未来若要叠加 LLM 应遵循 `judge.py`/`inquiry.py` 已确立的降级链姿势。Swift 侧新增 3 个 `DTODecodeTests`(覆盖 upload/get 两端点,含 multipart 请求体断言 + `result` 为 JSON null 时正确解码为 `nil`),全量 **41 个单测全绿**,双端 `xcodebuild` **BUILD SUCCEEDED**。**macOS GUI 可视化核验受阻**:本环境 computer-use 访问请求被用户拒绝(同 4C 已记录的沙盒限制一致,非本阶段新增问题),故本阶段"确认 app 跑起来"的证据链是:①真实 uvicorn + 真实 xlsx 端到端冒烟(`scripts/smoke_review.sh`,验证的是领域逻辑本身,证据力最强)②双端 build 成功③iOS Simulator 单测全绿④新增 UI 组件在布局/组件用法上与已经过 4C 视觉验证的 `TodayPlanView`/`BoardView` 同款 `NKCard`/`NKChip` 模式一致。

  **施工期踩坑(如实记录)**:格式一交割单反推价格公式 `(|发生金额|-费用)/数量` 若"手续费"对应列在表头里压根找不到(非 col_map 覆盖场景,如某券商把该列叫"费用合计"而非"费用"),最初实现会静默按 0 兜底继续算价格,产出一个看似合理实则错误的成交价(`150015/100=1500.15` 而非正确的 `1500.0`)——这是纯手工构造测试数据时才暴露的坑(现实中列名换个说法很常见),已修复为"列压根找不到"硬性跳过 + 警告,区别于"该列存在但这一行恰好留空",并补了回归单测锁死(`test_format1_missing_fee_column_skips_not_silently_zero`)。

  **关键设计决策(plan 留白处的实现选择,非偏离)**:①"总仓"(§1.2「12-13 万固定分母」)新增 `config.total_capital`(默认 12 万,`.env` 的 `TOTAL_CAPITAL` 可覆盖),供敞口占比 / 强制复盘阈值计算——此前项目无此常量的单一归属地,今后其它模块如需引用也应读这里,不要各自另写字面量;②"计划内 / 计划外"判定同时核对报告候选与问询台海选池两个来源(plan 4D.2 原文只提"报告候选",但海选池当晚会被纳入报告 universe,一并核对更贴合 §2.5 闭环意图,且只会减少误判"计划外"、不会增加漏判);③持仓台账对账(plan 4D.2 原文"与...持仓台账对账"的落地):买入若在 `positions` 表有同代码同日期记录、价格 1% 容差内视为"台账已录",否则"台账缺失"(提示止损提醒未覆盖该仓位);④并发持仓 / 敞口核算范围限于本次上传数据可见区间(已知简化,模块 docstring 已注明,非漏判 bug——若某票在未上传的更早期间开仓、本次只看到中途卖出,其"占用仓位"在开仓阶段不可追溯);⑤新增 `PUT /settings/review-col-map` 端点(plan 未明确列出但补全"可配"闭环,成本低、复用既有 push/llm 端点同款模式)。

  **工程增量**:`neckline/review/`(parse.py 解析 + 名称反查 / reconcile.py FIFO + 三查 + 统计 + 序列化 / material.py 确定性材料 / store.py `reviews` 表读写)、`neckline/api/app.py` 三新端点、`neckline/api/schemas.py` 新增 4D 出入参(`result` 沿用 `sentiment/sectors` 透传惯例,不重复声明嵌套模型)、`neckline/config` 新增 `total_capital`、`requirements.txt` 新增 `openpyxl`/`python-multipart`;客户端 `Models.swift` 新增 8 个 4D 领域模型、`APIClient.swift` 新增 `uploadReview`/`fetchReview`/`putSettingsReviewColMap`、`AppModel.swift` 新增周复盘状态 + 上传方法、`ReviewWorkbenchView.swift` 从占位壳改为真实接线、`RootView.swift` 传入 `model`;新增 `scripts/smoke_review.sh`(真实 uvicorn + 真实 xlsx 端到端冒烟,清理临时库/文件,不碰生产数据)。

  **遗留问题(供 4E 参考)**:①4D.3 的 LLM 复盘材料叙述叠加层本次任务范围内明确不做(见上,plan 原文本就标注"可选");②周复盘工作台目前是 `review_col_map` 唯一可写入口是设置端点本身,客户端未提供编辑该映射的 UI(编辑 UI 留待用户真遇到第三种券商格式时再评估是否需要);③并发持仓/敞口扫描的"已知简化"若未来发现在多周连续上传场景下不够准确,需要评估是否要做跨周结转;④macOS 端可视化核验同 4C 一样受阻于 computer-use 访问被拒,若用户希望更强的视觉证据,可自行用 Xcode 打开工程运行 App 肉眼确认(或下次会话尝试授权 computer-use 访问)。

- **2026-07-20 · 阶段 4E 部分完工(LinoN 接班切换 + 收官接线)**:兑现「用户已拍板可以接班」——LinoN 退役、Neckline 接管公网 `ln.linotsai.top`、客户端默认后端指向生产、问询台海选池闭环的报告侧接线补齐。全量 **pytest 621 → 629**(+8,0 回归),Swift **41 → 45 执行**(6 skip 探活)、双端 `xcodebuild` **BUILD SUCCEEDED** + iOS Simulator **TEST SUCCEEDED**。

  **4E.3 LinoN 接班切换(🔴 高危,逐步验证)**:①**退役前备份**——ECS 上 `sqlite3 .backup`(LinoN 仍 active 时的在线一致性快照,非裸 cp)→ scp 回本机 `Lino/Archive/linon_decommission_20260720/linon.db`;三处 `PRAGMA integrity_check` 均 ok(live / 快照 / 本机),大小 303104B 一致、sha256 逐字节一致(`a020b10c…`)、10 表行数逐表吻合(candidates 764 / candidate_outcomes 762 / analysis_verdicts 8 / positions 4 / trades 4 / device_tokens 1 / screen_config 1 / memory 0 / reviews 0 / sqlite_sequence 6),另写 `MANIFEST.md` 行数清单(沿 Lino Writing v2 退役先例)。②**停 LinoN**——`systemctl stop linon && disable`(inactive+disabled,8001 不再监听),`/opt/linon` 目录 + `.env`/`.p8` 原地保留不删(彻底清理留后议)。③**nginx 切换**——按 `hz_info §7` 安全流程,先 `cp` 带时间戳备份原站点(`linon.bak.20260721-090140`),再对照现网 certbot-managed 配置**最小合并**:仅改 `proxy_pass` 8001→8002 + 加 `client_max_body_size 20m`(4D 交割单 multipart 上传);**证书路径 / certbot 托管块 / listen 行一律不动**(避免扰动 certbot 续期,`hz_info §13` 已知 certbot 问题不碰);`sudo diff` 确认只这两处变更后 `nginx -t`(仅历史遗留 warning,无 error)过 → reload。④**公网验证**——`https://ln.linotsai.top/api/v1/health` 200 且版本变 Neckline `0.4.0-stage4A`(原 LinoN `1.0.0-stage1A`)= 切换实证;鉴权端点无 token 401 / 真 `API_TOKEN`(经 sudo 从 `/opt/neckline/.env` 读、全程不回显)200 且返真报告数据;8001 无监听;邻居站点(lf ok / 主页 ok / fiscal 200 / xiaoran 200 / 裸 IP 404)全不受影响。**红线全程守住**:除 linon.service 与 nginx linon 站点外未碰 ECS 任何其它服务/配置;备份未验证前未停 LinoN;`nginx -t` 过才 reload;token/key 不进任何 tracked 文件或文档。

  **4E · 客户端默认后端改 prod**:`AppConfig` 默认环境 `.dev` → `.prod`(`https://ln.linotsai.top`),保留设置屏「环境」picker + 手填 `baseURLOverride` 两条可配置覆盖路径。顺手把 `AppConfig` 改为**可注入 `UserDefaults`**(`init(defaults: UserDefaults = .standard)`,生产恒 `.standard`、didSet 也走注入实例)——起因:iOS Simulator `.standard` 残留前几次会话写的 `NK_ENVIRONMENT=dev`,`removeObject` 清不干净致新断言误红,注入隔离 suite 后 hermetic。xcodegen 重生(新测试文件自动纳入),新增 `AppConfigDefaultTests`(4 例:默认 prod / picker 切 dev / override 优先 / 选择持久化往返)。

  **4E · inquiry_pool 消费接线(兑现 4A 遗留#5,§2.5 闭环报告侧)**:`report.pipeline.build_report` 生成当晚报告时 `load_inquiry_pool(trade_date)` → 把「初审通过」票 `ts_code` 作 `forced_codes` 传入 `build_candidates`/`score_candidates`。语义严格照 §2.5「只扩输入,不改评分逻辑」:强制票**绕过 entry mask** 直接从当日全市场面板取行并入(去重,已过 mask 的不重复取)、评分/板块加分/排序对全体**同码一视同仁不特判**、且即便评分排在 `top_n` 之外也**保证出现在输出**(合并后按分重排 rank 连续)。**零回归护栏**:`forced_codes` 缺省 `None`/`[]` 时行为与阶段 2 逐票一致(直接单测断言),lazy import `neckline.api.stores`(沿 `review/reconcile.py` 惯例,不让报告管线在模块加载期依赖 api 包)。新增 8 单测:`test_candidates.py::TestForcedInquiryPoolCodes`(6:绕过 mask / 不重复计 / 保证过 top_n / 空池 noop / 面板缺失忽略 / mask 全空时仍纳入)+ `test_pipeline.py::TestInquiryPoolConsumption`(2:用 `seed_synthetic_market` 的 300001.SZ〔创业板,报告日 mask 会剔〕经海选池被强制纳入、未入池的 *ST 仍剔除的端到端验证)。**未动阶段 2 任何评分代码**(`build_entry_mask`/`_base_score_expr` 一字未改)。

  **工程增量**:`neckline/report/candidates.py`(`build_candidates`/`score_candidates` 加 `forced_codes` 参数 + 并入/top_n 保证逻辑)、`neckline/report/pipeline.py`(`build_report` 消费 `inquiry_pool`)、`client/Neckline/Networking/AppConfig.swift`(默认 prod + 可注入 defaults)、`client/NecklineTests/AppConfigDefaultTests.swift`(新)、`tests/test_candidates.py` + `tests/test_pipeline.py`(新测试类)。ECS 侧只动 linon.service + nginx linon 站点(接班切换),无代码部署(Neckline 已在 8002 常驻)。

  **仍挂账(4E 未完,交接下次)**:① **真盘中活体验证**——哨兵已随 `neckline.service` 常驻,下一交易时段(09:30–15:00)自动跑 `run_tick`,收盘后核查真实新浪/腾讯响应格式 / 批量拉价耗时 / 四哨兵触发(兑现阶段 3 欠账①);② **APNs 真机真推**——待用户 Xcode 装机、注册新 Bundle ID `top.linotsai.neckline` 的 device token 后,验证报告就绪 + 退潮刹车两类锁屏推达(4E.2,含 §八 Apple Developer 新 App ID + Push capability 网页操作);③ **LLM(GLM/Kimi)+ Bark 活体**——待用户 App 设置屏填 key 后首次真调用验协议假设(阶段 2/3 累积欠账);④ 问询台 LLM「主动多轮 function-calling」形态仍以预注入取数/重算 + 原生搜索覆盖(无 key 无法活体验证,记此简化)。

- **2026-07-21 · v1 上线 + 首日生产坑快修**:阶段 4 全部完工,v1 上线运行——ECS 全云(8002 / `ln.linotsai.top`)、双端 App 真机、APNs 真推通(报告 + 退潮)、**LLM=GLM 已激活**(真调用 26.3s 成功,兑现阶段 2/4 GLM 活体欠账)。首日三处生产坑快修:① TuShare 类型漂移毒化 Parquet 分区(`market_data.write_table_day` 加 `_align_to_table_schema` 按既有分区 schema cast,+2 测试);② 带搜索 LLM 读超时 25s→90s(`openai_compat.read_timeout`);③ 客户端 iOS 双标题(自画大标题限 macOS)+ 报告时间文案 16:00→16:35 + ECS 补跑 0720 缺失报告。坑详情入项目 `CLAUDE.md` §「v1 上线首日」。pytest 629→631。**遗留**:GLM `web_search` 空数组待查(见 §七挂账)。

- **2026-07-21 · v1.1 立项(SOP 补洞)**:用户拍板 v1.1(2026-07-21 讨论定案,方向不得改),补 v1 实盘暴露的 SOP 四个裸奔点。设计共识同步标注拍板:**§2.3** 新增「自选体检」报告节(同码评分,LLM 只审 changed∪pinned);**§2.4** 新增「盘前校准 tick」(9:25:30 集合竞价快照纯规则判定、9:26 汇总推送,仍属执行层不产新票)+ **推送白名单扩到四类**(报告 / 退潮 / 盘前校准 / D5)+ **D5 时间退出执行器**(规则 v1 `hold=5` 此前无人触发)。**§五「当前版本 Plan(v1.1)」填入完整分块施工图**:A 盘前校准 tick(🔴)/ B 持仓生命周期 D 计数 + D5(🔴)/ C 自选池表 + CRUD + 体检 + 同花顺 txt 对账 / D 问询窗口修复(`inquiry_pool` 消费「当日」→「上次报告以来」,加 `consumed_report_date` 列)/ E 持仓卡改版 + 一键补录预填 / F 自选第五板块 UI + macOS txt 工作台 / G 设置屏四类开关 / H 部署 + 真机联调 + 活体验收,每块验收标准写死(含盘前校准 9:26 真机推达、D5 临期持仓真实触发、自选体检出现在真报告、txt 用真实同花顺导出对账四项活体验收)。高危区点名 @builder-pro(盘前校准 / D5 新推送类 APNs、哨兵进程盘中常驻改动、部署)。旧 `db.py` 新增 `watchlist` 表 + `app_settings` 两列 + `inquiry_pool` 一列(幂等迁移)。**铁律不变**:同码不重写、单一事实源(D 计数=D1 契约 / hold·止损读现役 config / 关注池 ≤200 自选并入仍守 / `write_table_day` 落盘 / LLM 90s)。§七补三项跨版本挂账(GLM search_hits / Bark 活体 / 周复盘首次真实交割单),§八补同花顺 txt 与两类新推送开关的用户操作。仓库现状:v1.1 未开工,施工图就位待 builder。

- **2026-07-21 · v1.1-C/D 后端完工**:C 自选池(`neckline/watchlist.py` CRUD + THS txt 互转对账,`≤30` 硬校验、增删只经用户端点)+ 自选并入哨兵关注池(`sentinel/universe.py`,持仓/自选/候选同级保留、超限按「持仓>自选>候选」裁剪、≤200 守住)+ 自选票触发买点后享候选同级待遇(`engine.py`/`precall.py` 同码消费昨晚写死的 `entry_spec`/`invalidation_spec`)+ 新 `report/watchlist_check.py` 自选体检节(评分同码复用 `_base_score_expr`、红绿灯复用 `base_universe_expr`/`strategy.signals`、买点触发复用 `build_entry_mask`、LLM 只审 changed∪pinned 且新增 `judge_candidate(system_prompt=...)` 向后兼容扩展)+ `reports.watchlist_json`/`_shape_report` 前向兼容 + markdown 独立节 + 五个新端点。D 问询窗口修复:`inquiry_pool` 加 `consumed_report_date`(NULL=待消费),消费判据从「入池当日==报告日」改「待消费∪已被本报告日消费(幂等补跑)」,根治 16:35 后问询通过票永久掉缝的生产真洞。全量 **pytest 682 → 796**(+114,零回归)。隔离库 ATTACH 真实 `data/neckline.db` 参考表 + 真实 backfill 数据跑 `2026-07-17` 真报告验证自选体检(贵州茅台绿灯触发出真实四件套、宁德时代因创业板正确判红灯);`scripts/smoke_api.sh` 扩真实 uvicorn+curl 冒烟全过。详情/客户端契约清单见 §四。**仍挂账**:v1.1-E/F/G/H(客户端持仓卡/自选板块 UI/设置屏四开关/部署+活体验收)。

- **2026-07-21 · v1.1-H 服务端部署上云(🔴 @builder-pro)**:v1.1 A–G 全量后端上生产 hz ECS(`0.4.0-stage4A` → `0.6.0-v1.1ABCD`),**未回滚**。迁移前 `sqlite3 .backup` 在线一致性备份(integrity ok/7 表行数吻合);`sync_code.sh` DRY_RUN 先验(仅代码、`data/`+secrets 零触碰、零删除)+ 补装 3 缺失依赖(`python-multipart`/`openpyxl`/`et-xmlfile`,4B 建 venv 时漏装、import 期 preflight 提前发现);幂等迁移四项随 `lifespan.init_schema` 重启执行(`watchlist` 表 + `app_settings` 两列 + `inquiry_pool` 一列 + `reports` 一列),迁移后 integrity ok、业务数据零丢失。公网真 token 服务端验收全过(watchlist 空 CRUD 往返 / 四推送开关 / positions 形状 / report/latest 前向兼容 / 401),哨兵盘前分支挂载 + idle RSS ~82MB 持平、timer 未动。踩坑留痕入 `hz_info.md §12`(权限复原勿 blanket chown 翻 `data/` 属主致 DB 只读;缺 multipart/xlsx 依赖 import 期炸服务)。**H 活体验收(9:26 盘前真机 / D5 真机 / 自选体检真报告 / txt 真实对账 / 问询跨日 / 四开关往返)留待用户 + 真实交易日,逐项列 §四。**

- **2026-07-22 · 版本号双线规约**:系统 v 字头 / 策略 K 字头解耦(用户定);现役策略 v1 改名 **K1**(本地 + ECS `strategy_versions` 双端 UPDATE,brain 按 is_active 取现役、零代码改动),db.py 注释同步。下一步:K2 策略讨论 + 回测。

- **2026-07-22 · v1.1-H2 退潮哨兵双级制重构(🔴 高危快修 + 上云)**:修上线首日早盘退潮误触发(拿早盘进行时炸板率 38% 对比昨晚收盘定稿 8%,基数不同结构性假信号 + 早盘小样本噪音)。四条修法(方向用户拍板,`0.6.0-v1.1ABCD` → `0.6.1-v1.1H2`,**未回滚**):① **飙升条件改同时段对比**——新建 `retreat_metrics` 表(逐拍一行 ~330 行/日,PK`(trade_date,hhmm)` 幂等),炸板率飙升改对比**昨日同一时刻(±5min 窗)本关注池**值(同样本同时段,`retreat_store.load_same_time_zaban_baseline`),缺基线静默失效;② **持续性**——任何条件族「连续 2 拍成立」才升红(上一拍触发集从表读),进程重启后首拍不触发红色(`engine._consume_retreat_first_tick` 保守闸);③ **早盘加严**——10:00 前绝对阈值整体上调(炸板率 50%→65% / 跌停 5→8 / 占比 15%→20% / 飙升 20pp→30pp / 跳水 -3%→-4%,`_thresholds`,均命名常量标「启发式待实盘校准」);④ **双级制**——黄色预警(单条件首次)只落 `sentinel_events`(`retreat/warn`,verdict 带「黄色预警」前缀)进看板、不推送不抑制买点;红色刹车(连续 2 拍 或 ≥2 条件同拍)推送+抑制买点+全天闩锁(现有语义不变)。每次黄/红落库带全量指标 payload(供未来算刹车命中率成绩单)。`board` 端点放行 `retreat/warn` 进事件列表(**客户端零改动**,`SentinelKind` 无「退潮」→ 中性色渲染)。**盘前校准与 9:35 起其他三哨兵一行不碰**。单测覆盖四修法各分支(同时段命中/缺基线静默、持续 2 拍、早盘梯度、黄升红两路径、重启保守),全量 **pytest 796→816 零回归**;合成冒烟(真实 20260717 大跌日)验证黄(09:45 首拍单条件 sector_dive)→红(10:35 连续 2 拍持续)→闩锁(14:50)升级路径。**上云**:迁移前 `sqlite3 .backup` 在线备份(`data/neckline.db.bak-20260722-192747`,integrity ok);`sync_code.sh` DRY_RUN 先验(仅代码、零删除、无 db/secret);`retreat_metrics` 表随 `lifespan.init_schema` 重启自动建;health 200(local+public,`0.6.1-v1.1H2`)、integrity ok、**今日(20260722)既成红色闩锁未清除**(新逻辑明天生效)。**再次踩 `hz_info §12` 老坑**:`sync_code.sh` 尾部 `chown -R deploy:neckline /opt/neckline` 会把 rsync 已排除的 `data/` 属主一并翻成 deploy → DB 只读 → 启动 `init_schema` 炸(`attempt to write a readonly database`);已 `chown -R neckline:neckline /opt/neckline/data` 复原、服务恢复(该脚本尾部命令是复发性 footgun,已另起任务修脚本)。**明天首个交易日观察要点**:昨日同时段基线明天才开始积累(后天才完整),故明天飙升子条件全天静默失效、只有绝对条件(炸板率/跌停/跳水)+ 持续性/双级制生效;首拍保守闸使当日首个盘中 tick 不出红色。
- **2026-07-22 · v1.1.1(退潮哨兵双级制)+ 版号规约收严**:退潮刹车过敏快修上线(同时段对比基线/连续2拍/早盘加严/黄红双级,pytest 816,详见前条);系统版号统一 **v主.次.修 三段式**,对外 VERSION 由内部串 `0.6.1-v1.1H2` 更正为 **v1.1.1**;sync_code.sh chown 坑入 Backlog 待办下版修。
- **2026-07-22 · K2 策略研究立项(纯研究,不动生产 / 不动纪律章程)**:用户两轮讨论定案 **K2 = 情绪门控 × 主线票池 × 短线进出**,中心命题 = 阶段 1 被否的「追强势无正期望」是全时段全市场平均结论,「情绪进攻段 × 主线成员内追强势」子域期望未检验。**§五B 填入完整施工图**(B1 情绪三态阈值定量化〔`report/sentiment.py` 启发式阈值回测标定,兼答阶段 1 遗留⑤「MA20 被否后的市场择时正解」,对标 P1 同口径闸门检验〕→ B2 主线识别器〔`ths_daily` 板块指数动量 / 成交额占比 + 成分映射涨停贡献 / 连板归属,复用 `p2_sector_age.add_board_age`,2026-07-22 对用户五主题校准〕→ B3 个股成员判定〔共动性代理为主·纯价格无洞、`ths_member` 快照为辅·成分洞降级〕→ B4 中心命题 walk-forward 消融〔对手 K1、同一纪律设定、主线成员 mask + 情绪 gate 以默认关闭字段 / 外部注入实现、K1 逐位不变单测〕→ B5 止盈三方案赛马〔固定 +15% / 回落 5% / 混合,用户 +14~17% 先验对照〕+ 高弹风险预算化〔黑名单 vs 减半参与,次日跌停暴露〕→ B6 汇总裁决书 + K2 候选大脑 `save_version(activate=False)` 落库不激活),每块验收标准写死,**全程 @builder-pro**。复用阶段 1 研究基建(`research/lab·panel·eventstudy`、`walk_forward`、`momentum` `MomentumConfig`/`build_entry_mask`、`brain`、`features`、`signals`、`ths_*` 概念数据)。**铁律**:纪律章程 §2.1 未改一字、生产零改动(研究跑本地、概念拉取守 450/分)、K2 `is_active=0` 不激活(过门后另行立项)、证据强度三级标注(板块层强 / 个股层中·成分洞 / 前向验证补强)、资金面 `moneyflow_dc` 2023-09+ 窗口对齐、盘中刹车不可回测、诚实否定合格。§四 状态 + §七 Backlog 同步。仓库现状:K2 研究未开工,施工图就位待 builder-pro。
- **2026-07-25 · 架构拆分(双权威文件)**:策略线自本文件独立 → `STRATEGY_LAB.md`(策略研究中心唯一权威:雷区地图/研究铁律/K4 方向/策略 Backlog/策略变更日志);本文件此后专属系统线(APP 建设办公室,v 字头)。§五B/§五C 施工图全文迁 `archive/`、§六 参数清单迁 STRATEGY_LAB,原地留指针;跨线协作协议见两文件头部与 CLAUDE.md「双会话架构」。K3 判决(否决中止/停止挖矿触发)详见 STRATEGY_LAB。
- **2026-07-25 · v1.2-0 完工:`sync_code.sh` 部署 footgun 修复(@builder)**:根因——脚本尾部打印给人复制执行的收尾命令 `sudo chown -R deploy:neckline ${REMOTE_PATH}` 与 `find ... -type d -exec chmod 2770 {} +` 会递归进 rsync 已排除的 `data/`,把生产 DB 属主从 `neckline` 翻成 `deploy` → DB 对服务 `User=neckline` 只读 → 重启 `init_schema` 炸 `attempt to write a readonly database` → 服务 502(v1.1-H、v1.1-H2 部署各复发一次)。**修法两处**:①打印的收尾命令改 `find ${REMOTE_PATH} -path ${REMOTE_PATH}/data -prune -o -exec chown deploy:neckline {} +` / `... -o -type d -exec chmod 2770 {} +` prune 版,两条均跳过 `data/`(`.env`/`*.p8` 属主与 600 权限复原行为不变);②脚本末尾新增 `_check_owner` 函数,rsync 完成后(非 `DRY_RUN` 时)自动 `ssh` 远端 `sudo -n stat -c '%U:%G'` 核对 `${REMOTE_PATH}/data/neckline.db` 属主,不符 `neckline:neckline` 或远端不可达/路径不存在/权限不足,一律 stderr 红字 + `exit 1`,不静默当通过;`DRY_RUN=1` 下自检跳过(dry-run 未实传,data/ 属主不受影响,检查无信息量,脚本注释写明理由)。新增 `--selfcheck-only <path>` 独立验证模式(不触发 rsync),供无害路径单独测自检逻辑。**实测细节**:`deploy` 用户不在 `neckline` 组、对 `data/`(`drwxrws--- neckline:neckline`)无搜索权限,plain `stat` 直接 `Permission denied`,故自检用 `sudo -n stat`(已实测该 host `deploy` 配 `NOPASSWD: ALL`,`-n` 防止免密失效时卡死改为立即报错)。**验收(硬约束:全程未碰生产 `neckline.db`,未执行任何会改动生产 DB 属主/权限的命令)**:①`DRY_RUN=1 bash scripts/sync_code.sh` 肉眼核对两条收尾命令均已 prune `data/`、自检按设计跳过——过;②`--selfcheck-only` 对 ECS 临时无害文件(`/opt/neckline/data/_selfcheck_test_dummy.txt`,非生产库)测四态:路径不存在→红字+exit 1、属主 `root:neckline`(mismatch)→红字+exit 1、`sudo chown` 成 `neckline:neckline` 后→绿字+exit 0、`NECKLINE_DEPLOY_HOST` 指向 TEST-NET-1 保留地址(远端不可达)→`ConnectTimeout=10` 内清楚报错+exit 1——四态全过,测试文件已清理、生产 `data/`(`neckline:neckline`)/`neckline.db`(`neckline:neckline 644`)属主全程复核未变;③全量 `pytest 833` 绿(零回归,本块不改 Python)。**本块严格不部署、不 restart 服务、不跑 rsync 实传**,真部署 + 收尾命令端到端活体验证留 v1.2-F 首次部署合并做。`~/Lino/hz_info.md §12`(权限复原陷阱笔记追加已修复说明)/ line-191(v1.1-H2 复发记录追加销账说明)同步更新;§七 backlog 该条标记 `[✅ v1.2-0 已修复,待 v1.2-F 首次真部署活体收官]`。
