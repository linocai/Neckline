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
3. **仓位纪律**:单笔仓位上限 **2 万**;最多持 **5 只**;总敞口 **≤60%**。
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

### 2.4 盘中哨兵(轮询免费实时源,1 分钟一拍)

> **铁原则:盘中不产生任何新决策,只执行前晚计划,永不盘中推荐新票。**

1. **买点哨兵**:候选触达预设买点**且**确认条件成立(量能折算、站稳 VWAP)→ 进盘中看板。
2. **退潮哨兵**:盘中情绪恶化(炸板率飙升 / 跌停扩大 / 主线板块跳水)→「**今日计划作废、禁开新仓**」红色刹车。
3. **持仓哨兵**:持仓票放量跳水逼近止损线 / 触达目标区间 / 所属板块跳水预警。
4. **证伪哨兵**:候选分时走坏(低开不回 / 全天 VWAP 下方 / 量能异常 / 板块梯队瓦解,证伪条件**前晚写死**)→「剔除勿进」。**盘中主力资金流免费源不可靠,证伪只用价量结构**(VWAP / 量能折算 / 高低开)。

> **⚠ 推送路由拍板(2026-07-20 用户,推翻阶段 3「四哨兵各自推送」的默认设计)**:APNs 锁屏推送**只留两类**——① 每日 16:00 盘后报告就绪;② 退潮红色刹车(默认开、设置屏可关)。**其余哨兵事件(买点触发 / 证伪剔除 / 持仓预警)一律不推 APNs,只进 App「盘中看板」板块**(打开即看)。每类事件的推送开关在设置屏可配。理由:比行情速度必输同花顺,系统价值是把前晚计划执行成判决(看板),不是抢报新闻。上文四哨兵的**判定逻辑一字不改**,变的只是「触发后去哪」——看板(全部四类)vs APNs(仅退潮 + 报告)。阶段 3 已建的 `sentinel/channels.py` 通道抽象保留、阶段 4 新增 APNs 通道(复用 LinoN `push/apns.py`),**Bark 降为备用通道**。

### 2.5 问询台(LLM 问询板块)

用户丢外部消息源的票进来 → 系统先跑**确定性检查**(纪律核对 + 同一评分管线跑分 + 板块年龄)→ LLM **带工具调用**(实时取数 / 重算)自然语言回答。

- **裁决只有两种**:「**不符合 + 依据**」或「**初审通过,进当晚海选池**」。
- **永不产出「现在就买」。**

> **拍板落地(2026-07-20)**:阶段 4 把问询台做成 App「问询台」板块(自由对话体聊天,继承 LinoN `/chat` 无状态、客户端持有上下文的对话工程)。「**初审通过进当晚海选池**」= 落 `inquiry_pool` 表(当日),`report.py` 生成当晚报告时把海选池的票**强制纳入候选评分 universe**(不改评分逻辑,只扩输入)。裁决二值 = 硬约束,system prompt guardrail + verdict 枚举只两值双保险,任何路径不产「买」。

### 2.6 回测引擎(系统地基,带笼子的策略进化)

- **同一套信号代码三跑道**:喂历史 = 回测、喂今日 = 报告、喂单票 = 问询台。
- **回测必须正确处理**:前视偏差、T+1、涨停买不进 / 跌停卖不出、停牌、复权(前复权)、滑点、手续费。
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

**⚠ 高危区提示**:哨兵接触盘中实时源(新浪/腾讯)+ 推送通道(Bark)是阶段3 新增的对外接口,已用
MockTransport 充分覆盖降级链;持仓台账/防重表是新增的写入路径(SQLite),已用单测覆盖 CRUD + 幂等;
「退潮触发后抑制买点」是本阶段最关键的安全属性(直接对应 §2.4 铁律「永不盘中推荐新票」),已有直接单测断言。
建议阶段4 开工前、或用户拿到首个盘中实测结果后,视情况决定是否叫一次 `review`。

---

## 五、当前 Plan(阶段 0 → 4,研究先于产品)

> 每个交付项写具体行为,build 不用猜。每阶段末给验收标准。

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

## 六、回测待答参数清单(阶段 1 逐项过堂,过堂后写死进大脑 v1)

| # | 参数 / 问题 | 初值 / 候选 | 印证的归因发现 |
|---|---|---|---|
| P1 | **市场过滤器生死** | 上证 MA20 过滤 vs 情绪仪表盘 vs 叠加 | MA20 下方 28 笔 -1.95 万、上方 9 笔 +0.32 万 |
| P2 | **板块年龄参数** | 启动早期加分斜率、4–5 天后降权曲线、梯队扩缩权重 | 追主线时机 |
| P3 | **强势定义赛马** | 涨停基因 / 20 日涨幅分位 / 量价结构(三选一或组合) | 追强势票 67% 胜率 |
| P4 | **绿盘大阴线禁买线** | 当日跌幅 ≤ -3%(初值) | 买绿盘大阴线是死因 |
| P5 | **距前高阈值** | 距 20 日高点 -15%(初值)以下禁买下跌途中票 | 买下跌途中票是死因 |
| P6 | **票型黑名单** | 次新 / 高弹题材(易跌停)入黑名单或强制减仓 | 7 笔买入次日跌停 |
| P7 | **回落止盈参数** | 从高点回落 X% 或跌破 VWAP / MA | 小赚扛大亏、盈利因子 0.47 |
| P8 | **时间退出天数** | 2 / 3 / 4 / 5 交易日赛马 | 4–7 自然日是唯一打平桶 |
| P9 | **冷却期天数** | 同票割肉后 10 交易日(初值) | 情绪化复仇加仓 |
| P10 | **仓位纪律 / 次周减半** | 单笔 ≤2 万 · ≤5 只 · 敞口 ≤60%;「次周单笔减半」(挂起) | 越亏越重仓 |

每项交付:回测对照结论 + 推荐值 + 样本外显著性判断(采纳 / 否决 / 待观察)。

---

## 七、Backlog(挂起 / 争议 / 拍板 / 后期决策)

- **[挂起] 纪律章程第 4 条「≥5% 次周单笔减半」**:用户未同意,阶段 1(P10)回测验证后再议是否纳入。
- **[争议] 大盘 MA20 市场过滤器**:不进第一版规则,阶段 1(P1)回测定生死(与情绪仪表盘二选一或叠加)。
- ~~**[拍板] 客户端载体 + 推送通道**~~ → **已拍板(2026-07-20)= 方案 A**(SwiftUI 双端 + APNs,新 App;APNs 只推报告 + 退潮刹车,其余进看板;Bark 降备用)。落地 §五 阶段 4C/4B.5,设计共识 §2.4/§3.5。
- ~~**[后期决策] 盘中哨兵部署**~~ → **已拍板(2026-07-20)= 上 hz ECS**(复用 LinoN 基建,单 unit 内 asyncio 哨兵;内存分工方案 A/B 以 4B.1 实测门禁定)。设计共识 §3.6,落地 §五 阶段 4B。
- **[机制] 策略进化门禁**:按月 / 季调参须过回测 + walk-forward 样本外跑赢现役 + 用户批准;大脑按版本归因实盘表现。落地在阶段 1 之后常态运行。
- **[未来] 分钟线数据源**:当前 TuShare 600 元档无分钟线,盘中靠新浪 / 腾讯免费源。若后续需要分钟级回测,评估升档或其他源。

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

---

## 九、变更日志

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
