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
- **时间(v1.2 修正,2026-07-25 策略线 register)**:~~本职工作忙,盘中约每 20–30 分钟看一次手机~~ → **用户非上班族、时间充裕可全天盯盘**;交易制度 T+1 不变。**盘中人判层(集合竞价 / L2 盘口)从「不可用」变为「可用」**(此前按上班族注意力设计,盘中只做提醒不做人判;现用户可亲自做盘中强度 / 竞价 / 盘口判断)。**边界(必读,防误用)**:① 本条只改「系统线当前设计前提」这一处;`STRATEGY_LAB.md` 雷区地图信封定义里「上班族注意力」是**历史口径、策略线明确保持原文不改**——此修正**不复活**任何已判死的信封(三场战役死因是 alpha 灭失、非注意力不足),**不得据此宣称信封判决失效**。② 因此可**重新讨论**的既有设计仅限「盘中人判层能力」(如 v1.2.1 呼吸打法的竞价 / 盘口择 T 由人判承担期望假设);**本次不扩范围**,回测审计仍受 EOD 数据边界约束。
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
2. **止盈 / 时间退出(v1.3 退出规则改革,2026-07-25 用户知情越线采纳,staged 生效)**:① **-5% 止损不变**;② **回落止盈 5%→8%**;③ **时间退出仅对非浮盈单**——D5(第 5 个持有交易日)收盘扣双边费后**净浮盈 ≤0 → 次日退出**;④ **浮盈单豁免时间退出**,交回落止盈 8% + -5% 止损管到浪停,**硬上限 15 个交易日**(防"无限期"名义下变相长线)。唯一事实源 = 现役 `strategy_versions` config(`take_profit_retrace=0.08` / `max_hold_days=5`〔非浮盈时间退出档〕/ `max_hold_days_profit=15`〔浮盈硬上限〕/ `time_exit_only_if_unprofitable=True`);哨兵/回测读库自动跟随,**生效于 v1.3 激活**(staged,同第 3 条,激活前仍按 K1 现役值〔回落 5%/hold≤5〕执行)。**风险登记(不得删,原样入 charter 注释)**:② 回落 8% 系 H9 V0 网格观察免测采纳(六格网格唯一同时改良全期与 2026 的格);③④ 组合版**未整体回测**(最接近的 H9-V3 差 724 元未过 2026 生存门禁);**用户知情行使决策权越线采纳(2026-07-25)**,风险已当面告知——组合退出规则的真实期望未经样本外验证,误差主要体现在"刚好在盈亏平衡线附近"的单子判向。证据链:`research/h9_exit_reform.md` + `research/winners_anatomy.md`。~~止盈不设固定线:回落止盈或时间退出(D4 思想),参数回测定(K1:回落 5%/hold≤5,v1.3 前)~~
3. **仓位纪律(v1.2 章程修订 = 三仓制,staged 生效)**:**最多持 3 只**(注意力约束「只做 3 仓」,不会更多);**单笔金额不定死**——由用户视股价与当时想控仓位当场在区间内自定,系统只把 **4 万**作为**违纪判定上限**(`single_cap`,**非推荐值**)、不替用户拍单笔金额;总敞口设满仓档(`max_exposure_frac`≈1.0)。**-5% 止损不变;止盈与时间退出按上方第 2 条 v1.3 新规**(~~回落止盈 5%、hold=5 不变~~ —— 本行 v1.2 立项时的写法已被第 2 条取代,勿按旧值实现)。唯一事实源 = 现役 `strategy_versions` config;**与第 2 条同批、生效于 v1.3 章程行激活**(staged:用户清掉现有持仓 + 确认后由切换器 `activate_charter.py --target v1.3 --confirm` 激活;激活前仍按 K1 现役值执行。⚠ 库里那行过时的 `v1.2` 章程行〔回落 5%/hold=5〕**保留但永不激活**,见 §五 v1.3-①)。~~单笔仓位上限 2 万;最多持 5 只;总敞口 ≤60%(K1 原值,2026-07-25 前;v1.2 激活后由三仓制取代)~~
4. **单周实现亏损 ≥ 总仓 2%** → 当晚**强制复盘**(材料由系统生成)。~~「≥5% 次周单笔减半」挂起项~~ → **已否决**(2026-07-20 用户拍板:回测显示纯空效应 + 用户明确不要,永久关闭)。
5. **同票割肉后冷却**:冷却期天数参数回测定(初值 10 个交易日)。
6. **违纪审计并入周复盘**:用户每周提供交割单,系统对账(实际成交 vs 当周报告、条件单完整性)。
7. **熔断纪律(v1.2 新增,2026-07-25 用户批准,数字已定;落库即生效,不随三仓章程 staged)**:**连续 3 笔止损** 或 **单日实现亏损 ≥ 4000 元**(约总仓 3%)→ **当日停止开新仓、次日只减不加**,**完成一次强制复盘后解锁**(解锁复用第 4 条 / 4D 强制复盘机制,不另造)。系统**无法物理阻止下单** → 以**哨兵强提醒 + 客户端状态标记 + 日志留痕**实现;**熔断是纯提醒层,绝不代下单 / 撤单**(§3.8)。触发与解锁事件都落库(归因用)。**诚实边界**:熔断只能基于**用户已补录进台账**的成交判定,漏录则失灵——判定所依据的数据与时效须显式呈现给用户(看板 / 报告卡注明「基于台账 N 笔已补录成交」)。**阈值单一源 = 命名常量**(住熔断模块,**非** `strategy_versions` config;理由同第 4 条强制复盘线 `FORCED_REVIEW_LOSS_FRAC`——政策值非回测参数,不进大脑);连续止损判据用到的 `stop_pct` 仍读现役 config(不硬编 -5%)。**与第 3 条 staged 章程的关系**:第 3 条三仓制改的是 `strategy_versions` config 仓位字段、须 staged 激活才生效;熔断不读那三个字段、只读命名常量阈值 + `stop_pct`,故**代码部署即生效、不等章程激活**。

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

> **⚠ 候选语义变更拍板(2026-07-25 用户,需求 5,落地 §五 v1.3-③-C3)**:自 v1.3 起,**候选列表不再是「系统认为会涨的票」,改为「过完安检、值得用户花注意力的票」,终选权在用户**。生成源从 **K1 entry mask 退役**——改走「情报筛选管线」四步(板块拥挤度 top〔五板块常驻 + 当日暴起〕→ 全板块 MAIN/GEM/STAR 只过卫生线 + 趋势向上、**不套 K1 主板 only 与回调买点** → K4 安检〔`hard_cut` 拦 / `avoid_flag` 标,读 DB `K4.k4_advisory`〕→ 情报排序〔资金流强度 + 题材持续天数**反用**〕)。**生成域刻意含高弹板块**(贴用户实操,与 K1 哲学相反,用户知情拍板,止损频率代价已在策略线审计定价)。候选卡文案须跟上此语义(不再标「推荐买点」)。**§3.8 铁律「同码三跑道」已相应重述**(候选生成解耦、纪律核对仍同码)。~~候选 = 系统认为会涨的票(v1.3 前 K1 口径)~~
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
> **推送白名单(v1.1 四类、v1.2 扩五类、v1.3 扩六类,推翻更早「只两类」)**:① 16:35 报告就绪、② 退潮红色刹车、③ **9:26 盘前校准汇总(v1.1 新)**、④ **D5 时间退出(v1.1 新)**、⑤ **熔断提醒(v1.2 新,§2.1 第 7 条;默认开、与退潮刹车同级)**、⑥ **K4 持仓派发警报(v1.3-② 新,用户 2026-07-26 拍板独立 category `HOLDINGALERT` + 独立开关 `push_holding_alert`,默认开)**——各自独立开关(设置屏可配)、各自独立 APNs category。**D5 时间退出**是规则 v1 `hold=5` 时间退出纪律的**执行器**(此前无人触发 = SOP 最大裸奔点):D5 当天看板置顶 + 推送。**熔断提醒**是 §2.1 第 7 条熔断纪律的执行器(连续 3 笔止损 / 单日亏损 ≥4000 触发即推 + 客户端状态标记)。**K4 持仓派发警报**只推**强价量证据**命中(年线下涨停 / 放量大阳派发 / 换手 >10%);题材持续天数=概念板块成分弱证据,只进看板不推(守铁律「证伪只用价量结构」)。买点 / 证伪 / 普通警示仍只进看板不推 APNs(不变)。落地 §五 v1.1-A(盘前校准)/ v1.1-B(D5)/ v1.2-A2(熔断)/ v1.3-②(K4 派发警报)。
>
> **⚠ K4 持仓牌强警示推送(v1.3 需求 7;用户 2026-07-26 拍板 = 第六类,推翻 planner「复用 D5EXIT」默认)**:持仓牌每日重算的**强警示级别**(年线下涨停 / 放量大阳 = 派发警报、题材持续 ≥4 天、换手 >10%)推 APNs;**普通警示**(题材 2-3 天等)只进盘中看板。**用户拍板独立第六类推送**(不复用 D5EXIT):理由 = K4 派发警报说的是「你的票可能在被派发」,与 D5 的「持有到期了」是两回事,合并后关一个会连坐另一个 → **独立 category `HOLDINGALERT` + 独立开关 `push_holding_alert`(默认开)**;`notify.__all__` 白名单结构守护五→六、apns category 五→六、`app_settings` 幂等加列、`GET/PUT /settings/push` 契约扩六字段。落地 §五 v1.3-②。守铁律「证伪只用价量结构」:换手/年线下涨停/放量大阳=强价量证据(触发 APNs)、题材天数=概念板块成分弱证据标「参考」(**不单独触发强警示**)。

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
  - **可用**:`daily`(日线)、`daily_basic`(换手 / 量比 / 市值 / PE-PB)、`adj_factor`(复权因子)、`index_daily`(指数日线)、`stock_basic`(代码→行业)、`namechange`(ST 状态历史)、`trade_cal`(交易日历)、`moneyflow_dc`(东财个股资金流,`net_amount` = 主力净额,万元)、**`top_list`(龙虎榜)**、**概念板块和成分**(板块层 / 板块年龄因子的数据源)、融资融券、财务三大报表、沪港通列表、**`stk_holdertrade`(股东增减持,2026-07-26 v1.3-③-C4 探活新确认,2000 积分档,`in_de`=IN增持/DE减持结构化字段,消息面扫描「减持」类数据源,见 §五 v1.3-③-C4)**。
  - **不可用(档位不够,方案已绕开)**:**`limit_list`(涨停榜单,15000 积分)→ 涨跌停全部自算**(见 0.4b 衍生表:涨停价 = `round(pre_close×(1+幅度),2)`,幅度按板块 10%/20%/ST 5%/北交所 30%,`close==涨停价` 判涨停、`high==涨停价且close<涨停价` 判炸板、连续涨停日计连板;新股上市首日无涨跌幅按 `stock_basic.list_date` 剔除);筹码分布 / 量化因子(8000 积分,不依赖);游资数据 / 个股及行业热板(15000 积分,不依赖);**新闻资讯为单独权限 1000 元/年,未购**(见 §8 决策项);**`anns_d`(公告接口)同样为单独权限 1000 元/年、未购**(2026-07-26 v1.3-③-C4 真实 token 探活确认:调用返回「无接口访问权限」,与新闻资讯是 TuShare 两个不同的独立付费产品——立案 / 暴雷 / 监管三类消息面扫描因此走 LLM 联网搜索兜底,不依赖 `anns_d`)。
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
- **同码 = 纪律与回测同码,情报筛选独立(v1.3 重述,取代旧「同码三跑道」)**:旧表述「回测/报告/问询共用同一套信号代码」在 v1.3 需求 5(K1 entry mask 退役)后与候选生成直接冲突,故拆成两条**同时成立**的表述——
  - **(a) 纪律 / 退出规则的参数单一源 + 回测与实盘同码(不变,是「减损纪律系统」的地基)**:止损 / 回落止盈 / hold 两档 / 仓位 / 熔断阈值各有唯一源;退出规则的模拟(回测引擎 `strategy/momentum.py` + `backtest/`)与实盘哨兵读同一现役 config,**回测口径 = 实盘口径**,不得各写一份(否则报告与回测漂移)。**问询台 / 自选体检的纪律红绿灯仍与报告同码**(选股域 `research/panel.py::base_universe_expr` + config 禁买过滤,见 §五 v1.3-⑤)。
  - **(b) 候选生成自 v1.3 起改为「情报筛选管线」,与回测信号解耦**:候选生成(拥挤度 → 卫生线 → K4 安检 → 情报排序)**不再套 K1 entry mask、不再声称是回测过的 alpha 信号**——它输出「值得关注的票」而非「会涨的票」,终选权在用户(三场战役判死「机器自动出信号」)。**故不要求(也不可能)与回测同码**;回测引擎仍是纪律参数的过堂载体与「减损系统」验证地基,与情报管线解耦。落地 §五 v1.3-③-C3。
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

**2026-07-26 · v1.3-③-C4 晚间消息面公告扫描(持仓+自选:减持/立案/暴雷/监管)已完工(@builder,未部署)。** 需求 3 收官(C1/C2/C3 已完工,本块补齐 C4,v1.3-③ 情报官四子块全部完工)。报告新增「消息面」节,扫描对象=**持仓 ∪ 自选**(去重,非全市场)。

**数据源侦察结论(真实 token 活体探活,详见 §3.2/模块 docstring,后人不必重查)**:① `anns_d`(TuShare 通用公告接口)**不可用**——真实调用返回「抱歉,您没有接口(anns_d)访问权限」,官方文档交叉核实是**独立付费权限**(公告信息单独 1000 元/年,与 §3.2 已记的「新闻资讯单独 1000 元/年未购」是**两个不同的独立付费产品**,本次补记 `anns_d` 同样未购)。② `stk_holdertrade`(股东增减持)**意外可用**——只需 2000 积分,在项目 600 元档(6000 积分)覆盖范围内、非独立权限,真实调用返回结构化数据(`in_de`=IN增持/DE减持、`holder_type`=G高管/P个人/C公司、`change_vol`/`change_ratio`/`ann_date`)。**「减持」类改用此结构化接口,不用 `anns_d`、不用 LLM**——零幻觉风险、免 LLM 调用成本,比 plan 原文举例的 `anns_d` 更优的替代方案(数据源侦察的题中之义,非擅改需求)。③ **立案 / 暴雷 / 监管三类无任何 TuShare 接口覆盖**(逐一核实:未找到「立案调查」/「监管处罚」专属接口;`disclosure_date` 是财务预约披露日期,与监管处罚无关,排除)——三类全部走 **LLM 联网搜索兜底**。

**架构**:`data/tushare_client.py` 新增 `ts_stk_holdertrade`;新 `llm/news_scan.py`——**一次调用问三类**(不是三次,控成本:持仓+自选最多 33 只,一次问三类把最坏调用数从 99 压到 33),复用 `judge.py` 同一套 provider/降级链姿势(读超时原样吃 `OpenAICompatProvider.read_timeout=90.0` 基类值,不新设),结尾按「结论-类别:摘要」多行收尾(§2.7 边界,类比 `judge.py` 头注释同一先例——叙述主体仍自由文字,结尾只是轻量机器可读收尾,非固定分栏卡片);**格式缺失时既不假装「确认无消息」也不硬造类别**,标 `degraded=True` 纳入失败计数(与 `judge.py`「格式缺失保守按否决」方向相反,原因写在模块头:消息面是风险警报场景,静默漏报与硬造类别都没有依据,只能诚实标「未解析」)。新 `report/news_alerts.py`(`NewsCategory` 四枚举码 REDUCTION/INVESTIGATION/BLOWUP/REGULATORY,`_scan_reduction`+`_scan_llm_categories`+`build_news_alerts` 主入口)+ `report/news_alerts_store.py`(`news_alerts` 表读写,不存 `name`,同 `llm_judgments` 惯例)。

**「没扫到」vs「扫了没有」区分(§硬要求)**:`NewsAlertScanStatus` 逐源记录 `scanned`/`reason`(+LLM 源额外记 `codes_total`/`codes_failed`,支持「部分标的失败」颗粒度),随报告落新列 `reports.news_alerts_scan_json`(同 `intel_json`/`sector_moneyflow_json` 惯例,保证历史报告回放仍能分清「当时没扫到」与「当时扫了确认没有」,不能只看 `news_alerts` 表当天有没有行——空行两种含义都成立)。命中告警落独立 `news_alerts` 表(`code`/`trade_date`/`category`/`summary`/`source`/`created_at`,`UNIQUE(ts_code,trade_date,category)` 幂等)。**已知简化(接受的 v1 边界,非疏漏)**:`trade_date` = 扫描所属**报告日**(与库内其余表 `trade_date` 惯例一致),非公告/事件本身的日期——同一公告若连续数日仍落在扫描窗口内会在数日报告里重复出现,不做跨日按事件日去重。

**契约**:`ReportOut.newsAlerts:[{code,category,summary,source}]` 严格覆盖 plan「v1.3 客户端契约清单」字面四字段,**额外加 `name`**(超集,向后兼容,不破坏契约);`ReportOut.newsAlertsScan:[{source,scanned,reason,codesTotal,codesFailed}]` 是**本块新增、非字面契约清单**的透明度补充字段,为满足「没扫到 vs 扫了没有必须能区分」这条硬要求而加,已在此报告(非擅自改契约,是补一个契约清单未列出但硬要求需要的字段)。**不阻断主报告管线**(硬要求④):`pipeline.py::build_report` try/except 包一层(两个内部子扫描各自已有降级,这里兜编排逻辑自身意外);扫描对象 = 持仓(`pos_store.load_open_positions`)∪ 自选(`watchlist_store.list_watchlist`)去重,展示名优先取自选自带 name、持仓票经 `stock_basic` 解析。markdown 新增「消息面」节(先亮扫描状态、再列命中,避免读者把「未列出条目」误当「确认干净」)。

全量 **pytest 1140 passed, 2 skipped**(基线 1093 + 47 新:`test_news_scan.py` 11 + `test_news_alerts.py` 14 + `test_news_alerts_store.py` 7 + `test_pipeline.py` 5 + `test_render.py` 4 + `test_report_store.py` 4 + `test_api_report_board.py` 2,`test_tushare_client.py` 补 1 处既有降级列表项,0 回归)。**LLM 单测一律 `httpx.MockTransport` 注入免联网**(`judge.py`/`news_scan.py` 同一套姿势);**TuShare 单测一律 monkeypatch `ts_stk_holdertrade` 免联网**——施工期发现一处遗漏(`TestLLMCategoriesScan` 某测试只关注 LLM 侧,未桩 TuShare 侧,导致该测试意外发起真实 TuShare 网络调用并污染 `tushare_client` 模块级 `_get_pro()` 缓存,使同进程后续依赖「无 token 优雅降级」的测试假阳性)——已修,全量 + 局部 + 正反序多次验证无残留。**未碰**生产 ECS / STRATEGY_LAB / research/* / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1)/ v1.3-①②③(已完工不重做)/④⑤⑥⑦;`data/*.bak-*`、未跟踪 `research/k5_cb.py` 未动。**⚠ 需用户拍板**:(1) 「减持」类改用 `stk_holdertrade` 结构化数据而非 plan 原文举例的 `anns_d`/LLM——超出字面但更优的方案,是否认可;(2) `trade_date`=扫描日(非事件日)的简化导致同一持续性消息可能连续数日重复出现,是否需要后续做跨日按事件日去重(v1 暂不做);(3) `newsAlertsScan` 是否要正式补进「v1.3 客户端契约清单」(目前是本块新增的透明度字段,⑥ 客户端可选择性使用);(4) LLM 侧最坏情况需循环扫描持仓+自选全部标的(≤33 只),每票一次调用(最长 90s×最多 3 次重试),与既有候选审判 / 自选体检量级相当,非本块独有新增负担,一并提醒供部署评估耗时。

---

**2026-07-26 · v1.3-③-C3 候选池情报筛选管线改版(K1 选股逻辑退役)已完工(@builder-pro,未部署)。** 需求 5 落地:候选生成从 K1 entry mask **退役**,改走情报筛选四步管线,新 `report/intel_candidates.py::build_intel_candidates`,`pipeline.py` 候选生成源已切换(自 `build_candidates` → `build_intel_candidates`)。**候选语义变更(§2.3)**:候选=「过完安检、值得关注的票」非「会涨的票」,终选权用户。**四步**:① 板块层=五常驻(`settings_store.get_intel_watch_boards`,DB `app_settings.intel_watch_boards` 幂等加列可配,默认〔芯片概念/创新药/储能/机器人概念/稀土永磁〕,**按 `ths_index.name` 精确匹配**取 ts_code,真实数据五个全命中,禁模糊)+ 当日暴起(`compute_sector_strength` 拥挤度 top-10,**先过 `board_pool` 卫生线**);② 个股层=step① 板块成员 ∩ MAIN/GEM/STAR(排 BSE)∩ `base_universe_expr` 卫生线 ∩ 非次新 120(复用 `signals.forbid_new_stock`)∩ 趋势向上(`close>ma20` 粗代理),**不套 K1 主板 only / 回调买点**;③ K4 安检=读 DB `K4.k4_advisory` 分区(`holding_k4_check.load_k4_sections`)`hard_cut` 拦 / `avoid_flag` 打标,**复用 ②-A 镜像评估器**;④ 情报排序=板块资金流强度(C2)+ 题材天数**反用** → 出 20 只。**性能坑选 (a)**:给 `holding_k4_check._build_holding_feature_panel` 注入 `_bulk_load_codes_table`(一次 `scan_parquet` 谓词下推按 code 过滤,替换 ② 逐票循环),**判据/阈值与 ② 完全同一份**(单一源)。**§3.8 落地核对**:候选生成解耦、**问询台/自选体检纪律红绿灯仍 K1 同码**(未碰 `inquiry.py`/`watchlist_check.py`)。`report/candidates.py` 评分 `_base_score_expr`/四件套/`pattern_tags` 复用(未删)。契约:`CandidateOut` 加 `k4Flags`/`intelRank{sectorFlow,themePersistDays,highElasticity}`(旧报告前向兼容默认空)。全量 **pytest 1093 passed, 2 skipped**(基线 1080 + 13 新 `test_intel_candidates.py`,0 回归)。**端到端真实数据(隔离库 ATTACH 真实四参考表 + 真实 Parquet backfill,`scripts/report.py 20260722` 真报告)**:新口径 20 只全部医药题材(爱美客/华熙生物/天士力,创新药/减肥药/CRO/重组蛋白),含 8 只高弹(创/科)、7 只 avoid_flag 打标;旧 K1 口径 20 只全是主板浅回调蓝筹(中国联通/建筑/银行),**两口径交集为空**——改版把「防御蓝筹回测信号」换成「五板块过安检的注意力清单」。迁移 `app_settings.intel_watch_boards` 生产库副本 integrity ok + 幂等重跑不炸 + 业务零丢失。**未碰**生产 ECS / `STRATEGY_LAB.md` / `research/*` / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1、K4 只读)/ v1.3-①②④⑤⑥⑦ / **C4(晚间公告扫描)本轮不做、留下一轮**;`data/*.bak-*`、未跟踪 `research/k5_cb.py` 未动。**⚠ 需用户拍板**:A3b 年线下放量大阳派发(不在 DB advisory)当前归 `avoid_flag`(打标不拦),是否升级为 hard_cut;`intel_watch_boards` 是否配 GET/PUT 端点(本块未建 HTTP 写路径)。

**2026-07-26 · v1.3-③-C1/C2 情报官(复盘情报件 + 板块资金流展示)已完工(@builder,未部署)。** 需求 3 落地一半——**只做 C1(复盘情报件)+ C2(板块资金流展示),C3(候选四步管线改版)/ C4(消息面公告扫描)本轮不做、留下一轮**(刻意拆段避免长任务半成品)。

新 `report/board_pool.py`(板块池卫生线单一源,C1/C2 共用、C3 下一轮也应复用):394 个同花顺概念板块按**名称模式**(融资融券/股通/成份股/样本股/指数/专精特新/国企改革/预增/预减/贬值/升值/破净/送转/回购/增减持/摘帽/ST/次新/富时/MSCI/标普/QFII/AH股/转债/参股/举牌/重组/壳资源/绩优/超跌/机构/北交所/创业板/科创)+ **成分数上限 `MAX_CONSTITUENTS=1500`** 双闸剔除,互斥归因,剔除审计透出(`audit_lines()`,落日志 + 报告脚注双通道)。**实测校准**(对照 2026-07-24 真实 `ths_index`/`ths_member` 快照逐个核对,非拍脑袋):真实剔除的 28 个板块全部由名称模式命中(融资融券 3842 只/深股通 1886/沪股通 1644/国企改革 1470/专精特新 1213 等,与 plan 原文举例基本吻合),成分数闸当前 0 命中(纯防御性,防未来出现名称模式未覆盖的新宽基标签)。**关键发现**:合法大主题板块成分数同样上千(机器人概念 1213 只、人工智能 1081、新能源汽车 1051、华为概念 1005、芯片概念 908),不能只按数量剔除——`MAX_CONSTITUENTS=1500` 特意留足安全边际,避免误杀用户五个常驻板块之二(机器人概念/芯片概念,C3 会用到)。**误伤修复**:"重组"关键词朴素子串匹配会误伤"重组蛋白"(生物医药主题,与公司重组无关)——已加 `_NAME_PATTERN_ALLOWLIST` 精确名称豁免,真实数据验证已生效(2026-07-22 真报告"重组蛋白"正常出现在最强题材榜)。

**C1** 新 `report/intel.py::compute_intel`——涨幅/跌幅榜(`daily.pct_chg` 排序各 20 只)/ 涨停梯队(`limit_derived` 按 `consec_limit_up_days` 分组,连板数降序展示)/ 跌停榜(展示上限 100 只 + `limitDownTotalCount` 真实总数,截断不撒谎)/ 大盘量能(**沪深两市合计**成交额 = 上证 `000001.SH`〔复用 `features.SSE_INDEX` 单一源〕+ 深证 `399001.SZ`;5 日均,样本不足 5 个交易日时诚实标注)/ 最强题材(`compute_sector_strength` 拿全量排序结果〔`top_n=1000` 远超真实板块数〕→ 过卫生线 → 截前 10,核心龙头 = 板块成分股当日涨幅前 2)+ 题材持续天数(复用 `sectors._add_board_age`,按 `board_age` 分「未站上 MA20 / 新起 1 日 / 持续 2-3 日 / 已延续 ≥4 日」四档 + 汇总分布 `themePersistenceDistribution`)/ 市值偏好(`daily_basic.total_mv` 分 5 档桶,固定桶位跨日可比,即便某档当日 0 只也展示)/ 涨跌停制度偏好(`limit_derived.limit_pct` 分 5/10/20/30cm,只展示当日实际出现的幅度值,两种分桶策略刻意不同,见模块注释)。

**C2** 新 `report/sector_moneyflow.py::compute_sector_moneyflow`——`moneyflow_dc` 个股 `net_amount`(万元)按板块成分(过卫生线后)加总排序,净流入/净流出榜各 15,独立编号。**定位写死**(代码注释 + 展示文案双重标注,不让后人当信号用):拥挤情报件,非选股信号(STRATEGY_LAB K2 判决板块层有效但无次日领先性),不参与任何评分/候选筛选(`report/candidates.py` 评分与 entry mask 本轮零改动,已用 grep 核实无任何调用点)。2023-09-11 前无数据 / 覆盖窗口内当日数据缺失均返回 `available=False` + 诚实原因(前者提"覆盖仅自 2023-09-11 起",后者不提,两条路径已用真实 2023-06-01/2023-09-15 边界数据验证)。

**证据强度标注(硬要求①,透到客户端字段而非只在注释)**:复用 `holding_k4_check.K4AdvisoryOut` 已建立的 `price_volume`/`constituent` 两级词表(不新造第二套)——`ThemeItem.evidenceStrength`/`SectorMoneyflowItem.evidenceStrength` 恒 `constituent`(题材/板块归属依赖 `ths_member` 当前快照,K2「成分洞」);涨跌幅/涨停梯队/跌停榜/大盘量能/市值偏好/涨跌停制度偏好为 EOD 硬数据(`daily`/`limit_derived`/`daily_basic`/`index_daily` 直接读,强证据),`evidenceNote` 顶层文案同步说明,markdown 各小节标题也标注强弱。

**不阻断主报告管线(硬要求④)**:`intel.py` 内 `_safe()` 逐项包裹(8 个子项各自独立降级 + 记警告到 `warnings[]`),`pipeline.py::build_report` 再包一层 try/except 兜底 C1/C2 编排逻辑自身的意外(单测用 monkeypatch 制造 `compute_intel`/`compute_sector_moneyflow` 整体抛异常,验证主报告仍成功产出候选/持仓等全部其余内容)。**落盘**:C1/C2 均纯读 + 内存聚合,零新 Parquet 写入,硬要求③(落盘走 `write_table_day`)自动满足、无需额外防线。

**契约(与 plan「v1.3 客户端契约清单」有一处出入,已选择而非擅改,此处报告)**:`reports` 表幂等加 `intel_json`/`sector_moneyflow_json` 两列,均落 **JSON 对象**(非数组)——plan 契约清单写 `sectorMoneyflow[]`(数组简写),但 C2 需要携带 `available`/`unavailableReason` 承载"2023-09 前无数据"这类诚实留空原因,裸数组无处安放这个信息,故 `ReportOut.sectorMoneyflow` 实现为 `Dict[str, Any]`(单对象,内含 `topInflow`/`topOutflow` 两个子数组),`intel` 本就是对象未受影响。均透传报告落库快照(同 `sentiment`/`sectors` 惯例,不在 API 层重抄字段定义)。markdown 新增「情报 · 复盘情报件(C1)」+「情报 · 板块资金流(C2)」两节。

全量 **pytest 1080 passed, 2 skipped**(基线 1032 + 48 新〔`test_board_pool.py` 12 + `test_intel.py` 18 + `test_sector_moneyflow.py` 8 + `test_report_store.py` 4 + `test_pipeline.py` 4 + `test_api_report_board.py` 2〕,0 回归)。**端到端真实数据验证**(隔离库姿势:`sqlite3` 建新库 + `ATTACH` 真实 `data/neckline.db` 只拷 `trade_cal`/`strategy_versions`/`stock_basic`/`namechange` 四张只读参考表,`DB_PATH` 指向隔离库、`parquet_dir` 走真实六年 backfill):`scripts/report.py 20260722` 跑出真报告,情报节全字段非空且数字合理(涨停梯队"5 连板×1 只、4 连板×1 只…"、猪肉板块"板块年龄 16 天,已延续 ≥4 日"、沪深两市成交额 21,671.4 亿元、市值偏好/涨跌停幅度分布真实占比);板块资金流真实榜单(汽车芯片净流入 +60.2 万元…量级合理)+ 剔除审计真实列出 28 个板块。真实 `FastAPI TestClient` 打 `GET /report?date=20260722` 验证 API 层 camelCase 透传正确、HTTP 200。**未碰**生产 ECS / `STRATEGY_LAB.md` / `research/*` / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1)/ `report/candidates.py` 评分逻辑与 entry mask(硬约束,C3 才动)/ v1.3-①④⑤⑥⑦。本块无 schema 类型变更、只新增两个幂等补列(`ALTER TABLE ADD COLUMN`),无需 `cp -p` 额外备份(仍确认过本地库 integrity ok)。

**给 C3(下一轮)接口/数据准备说明**:① `board_pool.apply_hygiene`/`count_members`/`invert_member_map` 已就绪,C3「①板块层拥挤度 top」直接复用,不必另起一份卫生线;② `holding_k4_check.py` 的 K4 polars 镜像评估器(`_evaluate_hits`/`_trend_below_expr`/`_dispatch_bigred_expr` 等)当前假设「≤3 持仓、逐票 `get_stock_history` 循环载入」的 I/O 模式(内存友好但**不适合 C3 全板块 MAIN/GEM/STAR 的大规模 universe**,数千只票逐票循环会很慢)——C3 落地时需要先决定是重用其判据表达式函数、换一套全市场面板 I/O 接入,还是另建一份镜像;**这是 C3 开工前最大的一处设计决策点**,建议先读一遍 `holding_k4_check.py` 模块头注释再动手;③ `app_settings.intel_watch_boards`(五板块常驻,可配)与 `news_alerts` 表(C4)本轮均未建,留 C3/C4 各自建表/加列。

---

**2026-07-26 · v1.3-② 持仓管理层(K4 牌每日对持仓重算)已完工(@builder-pro,🔴 推送路由 + 金额判定,未部署/未激活)。** 需求 7 落地:K4 红黄牌不再只买前安检,16:35 报告管线(`build_report`)对每只 open 持仓在当日 EOD 面板重算 K4 advisory 命中 + 派发警示。**判据全读 DB `strategy_versions` K4 行 `k4_advisory`**(evidence 文字读 DB;判据阈值 = `report/holding_k4_check.py` 命名常量镜像,advisory 人读字符串→可执行 polars 镜像的逐条对应关系写在模块头 docstring 对照表,改阈值须同改两处)。**分级**:强价量证据(A1 换手>10 / A3 年线下涨停 / A3b 年线下放量大阳〔诱多做局反向哨兵并入,数字依据雷区地图 3-⑤〕)→ 第六类 APNs 派发警报;普通(B1 堆积 / B2 双金叉 / B4 追强)只进看板;题材持续天数(A2≥4 / B3 2-3)= 概念板块 board_age 代理、**弱证据标 constituent(参考)不单独触发强警示**(守铁律「证伪只用价量结构」)。「放量大阳既强又普通」矛盾由**年线下闸**自洽解。**第六类推送**(用户 2026-07-26 拍板,推翻 planner「复用 D5EXIT」默认):独立 category `HOLDINGALERT` + 独立开关 `push_holding_alert`(默认开)——`notify.__all__` 五→六、apns category 五→六、`app_settings` 幂等加列、`GET/PUT /settings/push` 契约扩六字段、§2.4 推送白名单文字改六类。**v1.3-① seam 已接**:16:35 算好每持仓 D5 收盘净浮盈(扣双边费:买入费读 `positions.buy_fees`、卖出费 `fees.estimate_sell_fee` 估算)→ 落 `holding_eod_check` 表 → `sentinel/precall.py::scan_time_exits(net_float_provider=holding_store.net_float_provider(...))` 接线,修复「provider 恒 None → 激活后浮盈豁免形同虚设」(⑦ 激活前置)。②-E 盘中实时派发**降级为纯 EOD**(plan 明允)。**契约增量**:`PositionOut.k4Advisory[]`(服务端 16:35 算好、GET /positions 读快照嵌)+ `scenarioReviewPending`(②-D 情景树每日对照,复用既有 `scenario-outcome` 无新写端点 + `GET /decisions?position_id=` 只读挑出)+ `PushSettingsOut/SettingsPushIn.holdingAlert`。**生效仍 staged**:`is_active` 仍 K1、K4 行只读、行为零变化。全量 **pytest 1031 passed, 2 skipped**(基线 1010 + 21 新,0 回归)。改 schema 前已 `cp -p` 备份;**未碰**生产 ECS / STRATEGY_LAB / research/* / K1/K2/K4/v1.2/v1.3 已落章程行 / v1.3-①③④⑤⑥⑦。**🔴 推送路由改动 + 涉金额判定,与 ① 同批建议 review。**

**2026-07-26 · v1.3-① 退出规则章程变更已完工(@builder-pro,🔴 碰纪律章程,未部署/未激活)。** 需求 6 落地:止损 -5% 不变 / 回落 5%→8% / 时间退出仅对非浮盈单(D5 收盘扣双边费净浮盈 ≤0 次日退)/ 浮盈单豁免时间退出硬上限 15 日。**K1 逐位不变护栏锁死**:`MomentumConfig` 加 `max_hold_days_profit=None`+`time_exit_only_if_unprofitable=False`(默认吃默认 → 时间退出仍无条件 `max_hold_days` 触发),真 k3_panel 六年回测**改动前后逐位吻合 N=1288 / total_return −20.53% / final_equity 95361.50**(实证 `tests/test_v13_exit_6y_baseline.py`,默认 env 门控跳过;逻辑层护栏 `tests/test_v13_exit_guardrail.py` 始终跑)。**回测引擎条件退出**(`momentum.py::_time_exit_reason`/`_d5_net_float`)镜像 `research/h9_exit_reform.py::_sim_one` V1(D5 恰达算净浮盈、>0 一次性豁免续持至硬上限、≤0 照旧退;卖出费用引擎既有 `Broker._sell_fees`,不走实盘估算),六年 v1.3 config 回测硬上限豁免续命单 250 只(分支活着)。**两档口径四消费点齐改**:①`sentinel/precall.py` 新增 `scan_time_exits`+`classify_time_exit`+`TimeExit`(三态 time_exit_next_day/profit_exempt/hard_cap_exit + holding,net_float_provider 注入;**config 未启用退回单档 == max_hold_days = v1.1 完全一致**,`scan_d5_exits` 原语保留);②回测引擎(见上);③`api/notify.py::push_d5_exit` 两档文案(非浮盈标「净浮盈 ≤0」/ 硬上限标「浮盈硬上限 D15」/ K1 单档不标净浮盈;`__all__` 仍五入口、APNs category 仍五类);④`PositionOut` 新增 `maxHoldDaysEffective`/`timeExitState`(服务端按 D5 净浮盈判好下发,客户端不重算)。**卖出费估算唯一源** `neckline/fees.py::estimate_sell_fee`(印花税万5⚠待用户确认 / 过户费万0.1 / 从买入实付反推佣金率 + 5 元地板;诚实标注估算,误差只影响盈亏平衡线附近判向;回测侧不走它)。`positions` 幂等加 `buy_fees`/`sell_fees` 两列(补录开仓/回填清仓真数);`PositionOpenIn.buyFees`/`PositionCloseIn.sellFees` 透传。**charter `v1.3` 行已落本地权威库**(`scripts/charter_v1_3.py`,从 K1 config 复制**只改六字段**、风险登记原样入 changelog、`activate=False`;K1 rule SHA256 `5f331ef6…` 逐字节不变、v1.2 行保留不激活、integrity ok、业务表零改动);切换器 `scripts/activate_charter.py` 目标改默认 `v1.3`(`--target`)、激活前核对 `take_profit_retrace=0.08`、**硬拒绝误选 v1.2**(退出码 2)。**生效仍 staged**:`is_active` 仍 K1、生产/本地行为零变化,清仓 + 用户确认后跑切换器才激活(留 v1.3-⑦)。全量 **pytest 1010 passed, 2 skipped**(964 基线 + 46 新,六年实证 2 个 env 门控跳过,0 回归)。**未碰**生产 ECS / STRATEGY_LAB / research/* / v1.3-②③④⑤⑥⑦。**🔴 碰纪律章程 + 大脑版本行 + 牵动回测/哨兵/推送/客户端四处口径,建议叫一次 review。**

**2026-07-26 · v1.3 立项(施工图就位待 builder)· v1.2 合并发布 + 退出规则改革 + 持仓管理层 + 情报官候选管线改版。** 策略线交接 memo(`archive/交接_系统线升级需求_20260725.md` 最新版:需求 1 定案 / 需求 3 + 补充 / 需求 5 / 需求 6 退出规则 / 需求 7 持仓管理层)+ 用户拍板「v1.2 不单独发、与 v1.3 合并、版号跳 v1.3」。**关键前提**:`app.py::VERSION` 仍 `v1.1.1`(已核实),生产跑 v1.1.1;**v1.2 的 0/A/A2/B/G/E 六块 + entry-suggestion 区间均已完工但从未部署**——原 v1.2-F 部署块并入 v1.3-⑦,**累积迁移一次性执行是本版最大部署风险**。完整施工图见 §五 顶部「v1.3」,分块 ①–⑦:
> **① 退出规则章程变更(🔴 碰纪律章程)**:止损 -5% 不变 / 回落 5%→8% / 时间退出仅对非浮盈单(D5 收盘扣双边费净浮盈 ≤0 次日退)/ 浮盈单豁免时间退出硬上限 15 日;`MomentumConfig` 加 `max_hold_days_profit=None`+`time_exit_only_if_unprofitable=False`(**默认值保 K1 逐位不变**)、回测引擎条件退出、`scan_d5_exits` 两档(判定移 16:35 EOD)、notify 两档、charter `v1.3` 行(**作废过时 `v1.2` 行、保留不删不激活**)、卖出费估算(`neckline/fees.py` 单一源、诚实标注估算);风险登记原样入 charter + §2.1。
> **② 持仓管理层(K4 牌每日重算,推送路由 🔴)**:读 DB `K4.k4_advisory`(不抄常量、polars 镜像同 circuit 先例)对持仓每日体检;强警示(年线下涨停/放量大阳派发、题材≥4天、换手>10%)→ APNs 复用 `CATEGORY_D5EXIT`(**不新增第六类**)、普通 → 看板;情景树每日勾选复用 `scenario-outcome`;证据强度标注(价量强/成分弱)。
> **③ 情报官**:C1 复盘情报件(榜单/涨停梯队/量能/最强题材/市值+涨跌停制度偏好/题材天数,全 EOD、成分类标弱证据)+ C2 板块资金流(`moneyflow_dc`,走 `write_table_day`)+ **C3 候选四步管线改版(需求 5,K1 entry mask 退役、§3.8 铁律重述)** + C4 晚间消息面公告扫描(TuShare 源待查 / LLM 兜底守 90s / 缺 key 降级)。
> **④ 挂单未成交追踪(原 v1.2.1-C 归 v1.3)** + **⑤ 问询台选股域清理(原 v1.2.1-D2 归 v1.3)** + **⑥ 客户端跟进**(两档 D 徽标 / 买卖费录入 / K4 持仓牌 / 情景树每日勾选 / 候选新语义 / 情报包)+ **⑦ 部署 + 章程激活(🔴 合并 v1.2-F)**:一次推 v1.2+v1.3、VERSION→v1.3、累积迁移一次性(备份 + integrity + 业务零丢失 + 回退路径)、sync_code.sh 首验(footgun 活体收官)、timer/16:05 配额核查、staged 激活(清仓 + 确认后跑切换器 `--target v1.3`,**勿误激活 v1.2**)。
> **v1.3.1 后发(不挡上线)**:v1.2.1-D 决策归因(论点 × 打法双维 + 呼吸 T 贡献)——**双打法 A/B 裁决必需件、不许丢**,需 30-50 笔样本。
> **设计前提变更**:§2.1 第 2 条按需求 6 改写 + 风险登记、§2.3 候选语义变更、§2.4 K4 强警示推送(仍五类)、§3.8 同码三跑道重述(候选解耦 / 纪律同码)。**铁律不变**:推送仍五类、单一事实源(仓位/止损/止盈/hold 两档/熔断阈值各自唯一源)、系统永不自动下单、新表列幂等迁移 + 改 schema 前 `cp -p`、K1 逐位不变护栏、落盘走 `write_table_day`、LLM 90s。

**2026-07-25(增补)· v1.2 范围扩大 + 切两波(策略线三版补丁交接)。** 策略线在施工期改了交接需求(`archive/交接_系统线升级需求_20260725.md` 已更新为最新版:需求1 补熔断纪律 + 仓位 2+1 打法切分、需求2 决策日志六项→八项、需求4 呼吸试验仓台账转正式排期),范围扩大,重切版本(完整增补施工图见 §五「v1.2(先发)」/「v1.2.1(后发)」两小节):

> **v1.2(先发,齐了用户即可开新打法首笔;挡上线)**:v1.2-0 ✅ + v1.2-A ✅(步骤1,staged 步骤2 待用户清仓确认)+ v1.2-A2 ✅ 熔断纪律 + v1.2-B ✅ 决策日志八项 + v1.2-G ✅ 呼吸试验仓台账 + v1.2-E ✅ 客户端(八项表单 + 熔断状态标记 + 台账界面 + `close_reason` picker + 第五推送开关)+ **v1.2-F 部署(唯一未完工,🔴 待续)**。
> **v1.2.1(后发,三块都要攒够样本才有用、不挡开工)**:v1.2.1-C 挂单未成交追踪、v1.2.1-D 决策归因(维度从「论点标签」扩为「论点标签 × 打法标签」双维 + 呼吸 T 贡献)、v1.2.1-D2 问询台选股域漂移清理。**这三块施工图全文已移入 v1.2.1 小节(未删),分块序列同步重排。**
> **v1.3**:需求3 盘前情报包(仍推 v1.3,含复盘情报字段清单 + 晚间消息面公告扫描,§七 Backlog 已补全清单、不写施工图)。
> **关键口径已定死(详见 §五 各块)**:熔断阈值 = **命名常量**(住熔断模块、非 config;3 笔 / 4000 元;理由同 `FORCED_REVIEW_LOSS_FRAC` 政策值);单日亏损 = 当日 `sell_date` 已平仓回合**净实现盈亏 ≤ −4000**(**不含费用**,与交割单差异周复盘收敛);`positions` 幂等加 `close_reason`(止损/回落止盈/时间退出/证伪/主动 五枚举码 + NULL 兜底按 `sell_price ≤ buy_price×(1−stop_pct)` 近似判止损);情景树 = JSON 数组 `[{scenario,trigger,action,matched}]`(内容不可编辑、`matched` 事后可勾);呼吸台账 = **子表** `breathing_t_trades`(一票多次 T 是一对多,扩列表达不了);熔断解锁 = 客户端「熔断复盘」按钮(读强制复盘材料后)`POST /circuit/unlock` + 周复盘提交自动解锁,复用 `review/reconcile.py::is_forced_review` 同源。**设计前提变更**:§1.2 用户画像(非上班族、盘中人判层可用,旧文划线留痕)+ §2.1 加第 7 条熔断纪律 + §2.4 推送白名单四类→五类。**v1.1-H 六项活体验收:用户已确认全部验过、无问题(2026-07-25),v1.1 彻底收官。**

**2026-07-25 · v1.2 立项(施工中)· 人机协作配套:三仓章程 + 预注册决策日志 + 归因闭环。**(⚠ **范围已被上方「2026-07-25(增补)」条目扩展并切两波**:决策日志六项→八项、推送四类→五类、新增熔断纪律 A2 + 呼吸台账 G、C/D/D2 移入后发 v1.2.1;本条以下为**立项当时记录 + v1.2-0/A 完工留痕**,分块编号 / 推送类数以 §五 与上方增补条目为准。)系统重定位——用户 = 唯一决策人,系统 = 情报收集 + 机械分析 + 纪律执行 + 归因审计(**机器不替选股**,策略线三场战役判死「机器自动出信号」)。完整施工图见 §五「当前版本 Plan(v1.2)」,分块 0/A–F + D2:**v1.2-0** sync_code.sh footgun 先修(chown 收尾 prune `data/` + 脚本末尾只读属主自检)→ **A** 仓位章程三仓制(🔴,唯一源现役 config:`max_positions` 5→3 / `single_cap` 2万→4万〔**语义变为违纪判定上限、非推荐值**〕/ `max_exposure_frac` 0.6→1.0;先落 `v1.2` 行 `activate=False`〔config 承 K1 血缘、仅改三仓位字段、**绝不碰 K 字头**〕,**staged 两步**:清仓 + 用户确认后切换器 `--confirm` 才激活;附带修「周复盘用今天章程重判历史周」洗白洞:`strategy_versions.activated_at` + `reviews.strategy_version` + 按周取「当时现役」)→ **B** 预注册决策日志(六项落库不可编辑〔改动=新增修订行,归因认首版〕+ 服务端 created_at〔客户端不许传〕+ 论点标签枚举码;**审计件非下单件**)→ **C** 挂单未成交追踪(N=5 交易日,折进 16:35 报告管线复用 EOD 面板不新拉源)→ **D** 归因入 4D 周复盘(决策日志 via `position_id` 与 FIFO RoundTrip 按 `(ts_code,buy_date)` 邻近匹配、按论点标签胜率/盈亏比 + 「无决策日志开仓 N 笔」纪律项〔软约束落点,不静默丢〕)→ **D2** 问询台选股域漂移清理(`run_deterministic_checks` 复用 `base_universe_expr()`)→ **E** 客户端双端(录入六项 + 标签 picker 嵌「已按计划买入」流程之前、成交后一键关联、**不新增第六 tab**、entry-suggestion 改区间双档、macOS 归因表)→ **F** 部署 + schema 迁移 + 活体(🔴)。**铁律不变**:同码不重写、单一事实源不漂移(`single_cap` 语义 v1.2 起 = 违纪上限)、系统永不自动下单、不新增推送类(仍四类)。**K1 行原样保留**;激活后需用户在策略线会话同步 `STRATEGY_LAB §一`「现役」表述。需求 3(盘前情报包)推 v1.3(§七)。**v1.2-0 已完工(2026-07-25,@builder)**:`sync_code.sh` 打印的收尾 chown/chmod 两条均改 `find -path .../data -prune` 版跳过 `data/`,脚本末尾新增只读属主自检(`sudo -n stat` 核对远端 `neckline:neckline`,不符红字 + `exit 1`;新增 `--selfcheck-only <path>` 独立验证模式,不触发 rsync);`DRY_RUN=1` 预演肉眼核对两条收尾命令均已 prune、自检按设计跳过,ECS 无害临时文件三态(不存在/属主错/属主对)+ 不可达远端四场景验证自检逻辑本身全过(**全程未碰生产 `neckline.db`**),`~/Lino/hz_info.md §12`/line-191 已同步销账。全量 pytest 833 绿(零回归,本块不改 Python)。**本块未部署、未 restart 服务、未跑 rsync 实传**,真部署 + 收尾命令活体验证留 v1.2-F 合并做。**v1.2-A staged 步骤 1 已完工(2026-07-25,@builder-pro,🔴)**:三仓章程落库 + 历史洗白修复代码/迁移就位,**`is_active` 仍在 K1、生产行为零变化**。落地=① `brain` 加 `activated_at`/`activate_version`/`config_active_at`(+ 读入口动态投影容忍未迁移老库缺列,防 `no such column` 崩——k2/k3 guardrail 读真实未迁移库时真实踩到并修掉);② `db.py` 幂等加 `strategy_versions.activated_at`(+ 一次性回填现役 K1=`created_at`,幂等)/`reviews.strategy_version`;③ `review/reconcile.py::run_weekly_review` 改**按 `week_end` 取「当时现役」config**(章程升级后重跑历史周不洗白旧违纪),`review/store.py` 按周落 `strategy_version`;④ `scripts/charter_v1_2.py`(从 DB 读 K1 config 复制、只改三仓位字段、`activate=False`,禁手抄)+ `scripts/activate_charter.py`(前置校验无 open 持仓 + old→new diff + `--confirm` 才 `activate_version`,不做 API 端点)。本地权威库已落 `v1.2` 行(`is_active=0`)、K1 回填 `activated_at`、integrity ok、K1 rule_json 逐字节未变(SHA256 `129e7f45…`);**未激活 v1.2**(staged 步骤 2=用户清仓 + 确认后在 ECS 权威库跑切换器,留 v1.2-F)。反例命门单测锁死(历史周 3 万买入激活 v1.2 后重跑仍报违纪,不被 4 万洗白)。全量 **pytest 846 passed**(833 基线 + 13 新,0 回归)。**激活后需用户在策略线会话同步 `STRATEGY_LAB §一`「现役=v1.2 章程行、内核血缘 K1」**。**v1.2-B 预注册决策日志(八项)已完工(2026-07-25,@builder)**:新表 `decision_log`(八项:`why_buy`①/`why_entry_price`②/`target_price`③/`exit_low`+`exit_high`④/`thesis_tags`⑤/`invalidation`⑥/`contingency_scenarios`⑦/`playbook_tag`⑧,外加服务端 `created_at`/`planned_price`/`planned_qty`/`status`/`position_id`/`revision_of`)+ 新模块 `neckline/decision_log.py`(唯一写入通道,同 `watchlist.py`/`sentinel/positions.py` 姿势)+ 六端点(`POST /decisions` / `GET /decisions` / `.../link` / `.../cancel` / `.../revise` / `.../scenario-outcome`,`neckline/api/schemas.py` + `app.py`)。**三处防线**:①`created_at` 由函数签名物理杜绝覆盖(`create_decision`/`revise_decision` 均无该形参,非运行时判断,客户端任何同名入参在 `DecisionCreateIn` 层就已被丢弃);②八项 + ⑦情景文本(`scenario`/`trigger`/`action`)+ ⑧全程无任何 UPDATE 语句触碰,唯一改法是 `revise_decision` 新增行、旧行原地不变,**`revision_of` 落链根 id(非直接父行)**——修订链因此扁平,归因一步 `WHERE revision_of=<根id>` 查全部修订,不必递归;③`scenario-outcome` 专用端点只 UPDATE `contingency_scenarios`+`updated_at` 两列(先全量校验 `index` 合法再一次性写回,一批里有一个越界则整批不生效),`scenario`/`trigger`/`action` 逐字不变。枚举一律服务端码 + 客户端展示层换算(沿 `boardLabel` 先例):论点标签 5 码 / 打法标签 2 码(`playbook_tag` 是打法标签唯一源,v1.2-G 不再另存)/ 情景 `action` 4 码,pydantic `Literal` 白名单校验、非法码 422。契约形状与「v1.2 客户端契约清单」逐字段核对一致。新增单测 46 个(store 层 `tests/test_decision_log.py` 24 + API 层 `tests/test_api_decisions.py` 22),覆盖 B 验收逐条(createdAt 忽略、八项无 UPDATE 路径、revise 首版原地不变 + 链根语义、scenario-outcome 窄口径、非法枚举码 422、link/cancel/revise 404、list 按 status/code/日期过滤);`scripts/smoke_api.sh` 新增 25)-33) 节,真起本地 uvicorn + curl 全端点闭环活体跑通一遍(未碰生产/本地权威 `neckline.db`,用临时库)。全量 **pytest 892 passed**(846 基线 + 46 新,0 回归)。**审计件、非下单件**——单测 `test_create_decision_does_not_open_a_position` 断言创建决策日志不触发任何持仓写入。**未碰**`positions.close_reason`(A2 块专属列)、**未碰** `strategy_versions`/K 字头、**未碰**生产 ECS。**v1.2-A2 熔断纪律已完工(2026-07-25,@builder-pro,🔴 碰纪律章程 + 金额判定 + 第五类推送)**:①数据缺口——`positions` 幂等加 `close_reason`(五枚举码 `STOP_LOSS`/`TAKE_PROFIT`/`TIME_EXIT`/`INVALIDATION`/`MANUAL`,唯一源 `sentinel/positions.py`),`close_position()` + 清仓端点 `PositionCloseIn.closeReason`(Literal 白名单,非法码 422)透传;②新模块 `neckline/sentinel/circuit.py`——阈值命名常量 `CIRCUIT_CONSECUTIVE_STOPS=3`/`CIRCUIT_DAILY_LOSS_YUAN=4000.0`(住本模块、非 config,理由同 `FORCED_REVIEW_LOSS_FRAC`;全仓库 grep 无第二处 3/4000 熔断字面),`stop_pct` 读现役 config 不硬编;③两条触发口径:连续 3 笔止损(尾部连续、遇非止损断链归零、显式 `STOP_LOSS` 或 NULL 价格近似兜底〔兜底只计数不回写库〕、显式非止损码不被价格二次猜)+ 单日**净口径**净亏 ≤−4000(盈亏互抵,大赢单遮蔽缺口由连续止损独立兜住);④新表 `circuit_breaker`(触发/解锁双落列,锁定态 = 派生 `unlocked_at IS NULL`、跨日持续无自动时间解锁、已锁定重复触发幂等不开第二行),`basis_json` 透出诚实边界「基于台账 N 笔已补录成交」;⑤触发折进 `close_position` 端点(尽力而为、异常吞掉不阻断清仓,§3.8 纯提醒层绝不代下单/拦 `POST /positions`)+ 第五类 APNs `push_circuit_breaker`(`CATEGORY_CIRCUIT`,受 `app_settings.push_circuit` 默认开,`notify.__all__` 结构守护四→五入口);⑥两条解锁路径均复用强制复盘同源不另造:`POST /circuit/unlock`(`review_ack`)+ 周复盘 `/review/upload` 覆盖触发周且 `forced_review=True` 自动解锁(`weekly_review`);⑦端点 `GET /circuit`/`POST /circuit/unlock` + `PositionsOut.circuit` 内嵌,契约与「v1.2 客户端契约清单」熔断/离场原因段逐字段一致。新表/列均幂等迁移,生产库副本 integrity ok + 重跑不炸 + 业务零丢失 + `is_active` 仍 K1。新增单测 27(`tests/test_circuit.py` 16 + `tests/test_api_circuit.py` 10 + `test_sentinel_positions.py` close_reason 五码往返 1)+ `test_notify.py` 第五类推送守护 2。全量 **pytest 921 passed**(892 基线 + 29 新,0 回归)。**契约留痕**:`PositionCloseIn.closeReason` 走 camelCase(与本模型既有 snake_case `sell_price` 并存,依客户端契约清单;A2.1 正文写作 `close_reason` 指 DB 列/store 形参,已按契约清单落 `closeReason`)。**v1.2-G 呼吸试验仓台账已完工(2026-07-25,@builder)**:新表 `breathing_t_trades`(幂等建表,`position_id` 关联 `positions.id`——无 SQL 级 FK 约束,同 `decision_log.position_id` 惯例,存在性校验交应用层做)+ 新模块 `neckline/breathing.py`(唯一写入通道,同 `sentinel/positions.py`/`decision_log.py` 姿势)+ 三端点(`GET`/`POST /breathing/{position_id}/trades`、`DELETE /breathing/trades/{id}`,`neckline/api/schemas.py` + `app.py`)。**底仓 / T 仓分离记账**:底仓仍是 `positions` 表一行(本模块任何函数都不写 `positions`,语义零改动,单测 `test_add_trade_does_not_mutate_position`/`test_add_trade_does_not_open_or_close_positions` 断言底仓字段逐字不变);T 仓走子表,一个底仓 → N 次 T 一对多(子表非扩列)。**T 盈亏公式方向无关**:`compute_t_pnl = (sell_price−buy_price)×qty−fees`,先买后卖 / 先卖后买同式,方向仅供 `note` 自由备注、不落结构化列。**费用逐笔如实入账**:`fees` 由调用方(客户端)给,`add_trade` 原样落库,模块内无任何费率字面量(2 万规模 ≈20 元≈0.1% 仅 plan 背景参考,代码不引用)。**「先手」成本优势 = 读时派生、不落列**:`compute_base_cost_adj = buy_price−(ΣT净盈亏)/底仓qty`(T 净赚拉低有效成本、净亏推高,两种方向均有单测)、`compute_edge_to_price = (price−baseCostAdj)/baseCostAdj`(**2026-07-25 用户拍板口径 = 相对自己的摊薄成本**——浮盈率读数,分母是 `baseCostAdj` 本身,不与 `distToStopPct` 分母取现价的口径强行对齐:那个字段问「离止损线多远」参照物是现价,这个字段问「先手成本优势多大」参照物是自己的成本,两个问题不同);现价复用既有 `_resolve_prices`(`sentinel/quotes.py:get_quotes` 同路径,不新拉数据源),无实时价 / 摊薄成本算不出时下发 `null`,不崩、不拿 0 冒充「无优势」。**打法标签单一源 = `decision_log.playbook_tag`**(v1.2-B ⑧),本模块未存第二份。`GET /breathing/{position_id}/trades`/`POST` 底仓不存在 → 404;`DELETE` 硬删除、不存在 → 404、重复删除同样 404(幂等安全)。契约与「v1.2 客户端契约清单」逐字段一致。新增单测 42 个(`tests/test_breathing.py` 27 + `tests/test_api_breathing.py` 15),覆盖 G 验收逐条(CRUD + 外键关联、摊薄成本 / 先手距离两方向、费用如实入账非估算、DELETE 幂等 + 404、契约形状、鉴权、审计件非下单件)。真实 uvicorn 临时库端到端跑通一遍(含真实联网 `get_quotes` 命中真实行情验证 `edgeToPrice` 有价分支)。本地权威库 `data/neckline.db`(`cp -p` 备份为 `data/neckline.db.bak-v12G-20260725145807`)验证:`breathing_t_trades` 建表 + `PRAGMA integrity_check` ok + 重跑 `init_schema()` 幂等不炸 + 核心业务表(positions/decision_log/circuit_breaker/watchlist/strategy_versions/reviews 等)行数与迁移前一致、`is_active` 仍 K1,零数据丢失。全量 **pytest 963 passed**(921 基线 + 42 新,0 回归)。**未碰** `positions.close_reason`(A2 块)/`decision_log` 表结构(B 块)/`strategy_versions` 任何行/生产 ECS。**v1.2-E 客户端双端已完工(2026-07-25,@builder)**:决策日志八项录入(`DecisionLogSheet.swift`,新)嵌「已按计划买入」流程之前——`beginPositionEntryFlow()`/`beginPositionEntryFlow(fromCandidate:)`(原 `openEntrySheet` 系列改名,语义已变)先开 `.decisionLog` 表单,提交成功 → 暂存 `pendingDecisionId` → 转 `.open` 开仓表单 → `submitOpenPosition()` 成交后自动 `link`;**软约束出口**:表单顶部常驻「跳过预注册,直接补录开仓」(`skipDecisionLog()`),不做硬阻断;用户在 `.open` 阶段中途放弃(`dismissModal()`)→ 自动 `cancel` 该预注册计划,不留孤儿 pending 行。⑦情景树次日复盘勾选(`ScenarioOutcomeRow`,只翻 `matched`、情景文本只读)+ ⑧打法标签驱动的入口露出规则(`PositionExtras.swift`,新)——`playbookTag==BREATHING_TRIAL` 主展示区露出绿色「呼吸台账」chip,其余持仓仍经卡片「更多」次级菜单保留入口(不主动露出但不隐藏)。熔断状态(`CircuitLockBanner`/`CircuitReviewSheet`)置顶今日计划面(比退潮刹车更靠前),文案取自服务端 `episode.note`/`basisTradesCount` 不客户端重算;「开新仓」两处入口(持仓区「补录开仓」+ 候选卡「买入补录」)按 `model.circuit.locked` 灰化,**服务端不拦、纯客户端自律**(§3.8)。清仓补 `closeReason` picker(五码,可留空)。呼吸 T 台账(`BreathingLedgerView.swift`,新)展示逐笔 + `baseCostAdj`/`edgeToPrice`(**文案按「先手成本比现价低/高 X%」写**,口径相对成本非相对现价);录入 `fees` 必填、无默认值。`entry-suggestion` 改区间双档(`EntrySuggestionRange`),`OpenPositionSheet`/`DecisionLogSheet` 均只展示参考区间、不再预填单一 `qty`(v1.1-E 旧逻辑已改)。**设计选择(plan 给了「builder 择一」的空间,已选定并记录)**:熔断态走独立 `GET /circuit`(未采用 `PositionsOut.circuit` 内嵌,避免牵动 `fetchPositions()` 既有返回类型 / 既有单测);呼吸台账用 sheet 呈现(不做持仓卡内联展开),iOS/macOS 通用。`reviseDecision` 已实现为 APIClient 方法(满足 E.6 契约)但**未在 UI 挂常规触发入口**——核实后端 `decision_log.py::revise_decision` 发现新增修订行会把 `position_id`/`status` 重置(不继承原行的成交关联),对已成交(filled)决策调用 revise 会产生不再关联该持仓的孤儿 pending 行,贸然挂 UI 按钮会让持仓卡回显对不上;`AppModel.beginReviseDecision(_:)` 已实现好、留给后续块按需接线。**⚠ 发现的后端契约缺口(未擅自改,报告已提出)**:`neckline/api/schemas.py::EntrySuggestionOut` + `app.py::entry_suggestion()` 仍是 v1.1-B.3 单 `qty` 旧形状,未实现「v1.2 客户端契约清单」的 `qtyLow/qtyHigh/capFloor/capCeil`(`curl` 活体验证实测返回 `{"qty":400,"stopLine":47.5}`,无区间字段)——客户端已按新契约实现 + mock 单测覆盖(`testDecodeEntrySuggestionRange` 等),真实请求会因缺字段解码失败,现有 `catch` 分支静默留空不崩(同 v1.1-E.2 既有降级模式),但**区间预填功能在后端补齐前不生效**;`IntegrationSmokeTests.testEntrySuggestionRealRequest` 已改造成「解码失败→ `XCTSkip` 报告已知缺口」而非放任变红。新增 Swift 单测 41 个函数(4 个因契约变化重命名/改写,净新增 37),覆盖决策日志八项 DTO 编解码 + `createdAt` 不可覆盖、枚举码→中文映射(论点/打法/情景动作/离场原因)、情景树数组 Codable 往返、熔断状态解码 + 横幅派生标签、区间预填计算、`closeReason` 编码进请求体(`httpBodyOrStream()` helper)、`DecisionLogForm`/`BreathingTradeForm` 校验、`linkedDecision` 入口露出逻辑、软约束跳过 / 中途放弃自动 cancel 的状态编排。双端 `xcodebuild` **BUILD SUCCEEDED**(iOS Simulator + macOS)、iOS Simulator **TEST SUCCEEDED**(110 个测试,13 个 `IntegrationSmokeTests` 因需本地 dev 后端按设计跳过,0 失败)。**本地真联调**(隔离临时库,同 `IntegrationSmokeTests.swift` 文件头姿势,非生产库):`POST /decisions` 创建 → `POST /positions` → `link` → `scenario-outcome` 真实往返;真实触发连续 3 笔止损 → `circuit_breaker` 落锁定行 → `GET /circuit` 真实渲染;呼吸 T 子账真实增删 + `baseCostAdj`/`edgeToPrice` 真实派生展示(含真实行情命中);`closeReason` 真实写入。iOS Simulator 截图核对(`xcrun simctl io <device> screenshot`,存于会话 scratchpad,未入库)6 张:今日计划熔断横幅+持仓区入口灰化、呼吸底仓试验持仓卡(决策日志回显+绿色呼吸台账 chip)、决策日志预注册表单(含跳过出口)、熔断复盘材料弹层、设置屏 Dev 环境已连通。**新增纯 QA 钩子** `NECKLINE_INITIAL_MODAL`(`NecklineApp.swift`,同 `NECKLINE_INITIAL_TAB` 先例,不影响正常用户路径)供本环境 computer-use 点击权限受限时的非交互截图核对。全量后端 **pytest 963 passed 复核(0 回归,本块未改任何后端 Python)**。**未新增第六个 tab**;`SentinelKind` 核实过不需要补 `.circuit`(熔断从不进 `/board` 事件列表,是独立端点,`_SENTINEL_LABEL` 无 circuit 键)。仓库现状:v1.2-F 部署待续(A 步骤 1 / A2 / B / G / E 均已完工;A2/F 🔴 高危区建议叫一次 review;entry-suggestion 区间契约缺口需 F 或专门一块补齐后端方能实证)。

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

## 五、当前版本 Plan(v1.3 = v1.2 合并发布 · 退出规则改革 + 持仓管理层 + 情报官候选管线改版 + 一次性大迁移上线)

> **本节顶部是 v1.3 唯一权威施工图**(v1.3 = v1.2/v1.2.1 已完工代码 + v1.3 新增,一次合并发布);每块写具体行为、build 不用猜,每块末给验收标准(含活体)。🔴 点名 @builder-pro。**v1.2 / v1.2.1 施工图全文保留在本节下方**(标「已完工待部署 / 施工图待建 · 一次并入 v1.3-⑦」),v1.3-⑦ 部署块**显式覆盖其活体验收项**。

> **关键前提(必读,决定本版全部工程边界)**:**v1.2 全部代码已完工但从未部署**——`neckline/api/app.py::VERSION` 仍是 `v1.1.1`(已核实),生产 ECS 跑的是 v1.1.1;v1.2 的 0/A/A2/B/G/E 六块 + entry-suggestion 区间补齐**均只在本地库/本地代码,生产从未见过**。**用户拍板(2026-07-25):v1.2 不单独发,与 v1.3 合并成一次部署,对外版号直接跳到 `v1.3`**(跳过 v1.2 对外版号)。因此:① 原 **v1.2-F(部署块)并入 v1.3-⑦**,一次推全部;② **累积迁移一次性执行是本版最大部署风险**(v1.3-⑦ 写死备份/验证/回退);③ §五 v1.2/v1.2.1 施工图不归档不删(F 未做),标注状态,v1.3-⑦ 覆盖其活体项。

> **用户已拍板(不得改方向)**:
> 1. **v1.2 + v1.3 合并发布,对外版号 v1.3**(`VERSION` v1.1.1→v1.3)。
> 2. **扣费口径 = 补录开仓时录买入实付费用**(不估算买入费);`positions` 幂等加 `buy_fees`/`sell_fees` 两列,清仓端点录卖出实付费用(真实发生后回填,供周复盘对账用真数)。⚠ 需求 6 判据「D5 收盘扣双边费后净浮盈 ≤0」中,**D5 当天尚未卖出、卖出费未发生 → 卖出费按公式估算并诚实标注为估算**(公式见 v1.3-①-F;误差只影响「刚好在盈亏平衡线附近」的单子判向)。
> 3. **K4 持仓牌推送口径(planner 给的默认,标注"用户可否决")**:**强警示级别才推 APNs、普通警示只进盘中看板;不新增第六类推送**——K4 强警示复用既有持仓提醒通道(`CATEGORY_D5EXIT`,APNs category 仍五类)。
> 4. **v1.2.1-C(挂单未成交追踪)并入 v1.3-④**(它记「挂了没成交的计划后来怎么走」,**不部署就永远补不回那几周数据**——与归因不同,归因晚做只晚出结论、数据仍在);**v1.2.1-D2(问询台选股域清理)并入 v1.3-⑤**(小清理顺手做)。**v1.2.1-D(打法 × 论点归因)留 v1.3.1 后发**(需 30-50 笔样本),但它是「双打法 A/B 裁决」的**必需件、不许丢**。
> 5. **候选池生成域刻意含高弹板块**(需求 5,用户知情拍板,止损频率代价已在策略线审计定价)。
> 6. **章程激活仍是 staged 步骤 2**(用户清仓 + 确认后跑切换器)。

> **分块序列(依赖一眼看清)**:
> · **v1.3-① 退出规则章程变更(🔴 碰纪律章程)**——MomentumConfig 新字段 + K1 逐位不变护栏 + 回测引擎条件退出 + `scan_d5_exits` 两档 + notify 两档 + charter `v1.3` 行(作废过时 `v1.2` 行)+ §2.1 改写 + 风险登记 + 卖出费估算。**基础块,先行**(退出规则/config 是 ②⑥⑦ 的地基)。
> · **v1.3-② 持仓管理层(K4 牌每日对持仓重算)**——读 DB K4 advisory;强/普通分级 + 强警示 APNs 复用既有通道 + 情景树每日对照勾选。**推送路由改动 🔴,体检 compute @builder**。
> · **v1.3-③ 情报官**——C1 复盘情报件 + C2 板块资金流 + C3 候选四步管线改版(需求 5,§3.8 铁律重述)+ C4 晚间消息面公告扫描。@builder;与 ② 共享「读 K4 advisory」。
> · **v1.3-④ 挂单未成交追踪**(原 v1.2.1-C 全文,归 v1.3)——@builder。
> · **v1.3-⑤ 问询台选股域漂移清理**(原 v1.2.1-D2,归 v1.3)——@builder。
> · **v1.3-⑥ 客户端跟进**(两档 D 徽标 + 费用录入 + K4 持仓牌 + 情景树每日勾选 + 候选新语义 + 情报包展示)——@builder,依赖 ①②③ 契约。
> · **v1.3-⑦ 部署 + 章程激活(🔴 合并 v1.2-F)**——一次推 v1.2+v1.3 全部、VERSION→v1.3、累积迁移一次性、sync_code.sh 首验、timer/配额核查、staged 激活。@builder-pro + 用户,**最后**。

> **铁律(承 §3.8 + v1.1/v1.2,builder 逐条守)**:同码不重写;**单一事实源不漂移**——止损 `stop_pct` / **回落止盈 `take_profit_retrace`(v1.3=0.08)** / **hold 两档 `max_hold_days`(非浮盈=5)+ `max_hold_days_profit`(浮盈硬上限=15)** / **条件时间退出开关 `time_exit_only_if_unprofitable`** / 单笔上限 `single_cap`(=4 万违纪上限)/ 最多持仓 `max_positions`(=3)/ 敞口 `max_exposure_frac`(=1.0)全读现役 `brain.get_active().rule["config"]`;**熔断阈值 3/4000 = 命名常量**(住 `sentinel/circuit.py`,非 config,不动);涨跌停读 `data/limit_derived.py`;板块分类读 `data/board.py`;总仓读 `config.total_capital`;实时价走 `sentinel/quotes.py:get_quotes`(关注池 ≤200);**落盘一律走 `data/market_data.py:write_table_day`**(勿自己 write_parquet);带联网搜索 LLM 读超时守 **90s**;LLM 缺 key 全链路优雅降级不崩;**推送仍五类不新增**(K4 强警示复用 D5EXIT category);**系统永不自动下单**;**新表/列幂等迁移 + 改 schema 前 `cp -p` 备份**;**K1 逐位不变护栏**(新 config 字段默认值必须让 K1 回测 N=1288/total_return −20.53% 逐位不变)。

---

### v1.3-① · 退出规则章程变更(🔴 碰纪律章程 @builder-pro)

**背景**:需求 6(2026-07-25 用户知情越线采纳)。替代现行「回落 5% + hold≤5 无差别时间退出」。**证据链**:`research/h9_exit_reform.md`(V0 网格 5×0.08 是唯一双改良格;V1/V3 浮盈续命全期强兑现但 2026 边际否决)+ `research/winners_anatomy.md`(大赢家 88.6% 系 hold=5 强退、时间止损封住右尾)。**新规则**:①止损 -5% **不变**;②回落止盈 5%→**8%**;③时间退出**仅对非浮盈单**(D5 收盘扣双边费后净浮盈 ≤0 → 次日退出);④**浮盈单豁免时间退出**,交回落 8% + 止损管到浪停,**硬上限 15 个交易日**。

- **①-A MomentumConfig 新字段(命名 + 默认值定死,保 K1 逐位不变)**:`neckline/strategy/momentum.py::MomentumConfig` 新增**两字段**(`take_profit_retrace`/`max_hold_days`/`stop_pct` 复用不新增):
  - `max_hold_days_profit: Optional[int] = None`——浮盈单硬上限(交易日);**默认 None = 不启用浮盈豁免 = K1 行为**。
  - `time_exit_only_if_unprofitable: bool = False`——时间退出是否仅对非浮盈单;**默认 False = 无差别时间退出 = K1 行为**。
  - **默认值铁律**:K1/K2/K3/`v1.2` 落库 config 均无这两字段,加载时吃默认(None/False)→ 时间退出仍在 `max_hold_days` 无条件触发 → **K1 回测逐位不变**(护栏单测锁死,同 `tests/test_k3_oversold_guardrail.py` 姿势:默认关闭字段 + K1 逐位不变)。
- **①-B 回测引擎条件退出(退出逻辑在 `backtest/` 平仓,不在 `build_entry_mask`)**:在「held == `max_hold_days`(D5)」判定点插入条件分支:
  - 仅当 `time_exit_only_if_unprofitable=True` 且 `max_hold_days_profit is not None` 时:算 D5 收盘净浮盈(`close×qty − buy_price×qty − 双边费`,**双边费用引擎既有 fee 模型**,与 `research/h9_exit_reform.py` §2 对拍口径一致——那里已 1288/1288 逐位吻合)——`>0` → **豁免时间退出**,继续由回落止盈(`take_profit_retrace`)+ `-5%` 止损管理,到 held == `max_hold_days_profit`(D15 硬上限)无条件退出;`≤0` → 照旧 D6 开盘时间退出。
  - `time_exit_only_if_unprofitable=False`(默认)→ 分支不进,`max_hold_days` 无条件时间退出 = **K1 逐位不变**。
  - **护栏单测**:加载 K1 config 跑六年回测,断言 N=1288 / total_return −20.53% 与冻结基线逐位相等(新字段不改写历史)。
- **①-C `scan_d5_exits` 改两档口径(`sentinel/precall.py`,实盘执行侧)**:现判 `d_count == max_hold_days`(硬编单档)。改为**净浮盈感知的两档**——退出判定的**权威计算移到 16:35 报告管线(EOD,D5 收盘后)**,因为「第 5 日收盘净浮盈」是 EOD 量、precall 9:25:30 时 D5 收盘未出:
  - 16:35 报告管线新增 `scan_time_exits(positions, trade_date, cfg, net_float_provider)`,对每只 open 持仓算 `d_count` 返回三态之一——`time_exit_next_day`(d_count≥`max_hold_days` 且净浮盈 ≤0)/ `profit_exempt`(d_count≥`max_hold_days` 且净浮盈 >0,续持至 `max_hold_days_profit`)/ `hard_cap_exit`(d_count≥`max_hold_days_profit`,无条件次日退出)。净浮盈 = 现价(D5 收盘,EOD 面板)×qty − buy_price×qty − `buy_fees`(实录) − **估算卖出费**(见 ①-F)。
  - precall 9:25:30 tick 退化为**纯执行提醒**:读上一份 16:35 报告持久化的退出计划,对 `time_exit_next_day`/`hard_cap_exit` 的持仓在盘前汇总提示「今日按计划时间退出」。**当 `time_exit_only_if_unprofitable=False`(config 未启用)时,`scan_time_exits` 退回单档 `d_count==max_hold_days` = 与 v1.1 D5 行为完全一致**(兜底,防未激活章程时行为漂移)。
- **①-D notify D5 文案改两档(`api/notify.py::push_d5_exit`)**:非浮盈单「D{n} 时间退出日,净浮盈 ≤0,按计划离场」/ 硬上限单「D{n} 已达浮盈硬上限 {max_hold_days_profit},按计划离场」;浮盈豁免单**不推 D5 执行提醒**(它没到退出,只在客户端 D 徽标转 `D{n}/{15}` 档展示)。**`__all__` 五入口不变**(不新增 push 函数)。
- **①-E charter `v1.3` 行(作废过时 `v1.2` 行,一次激活到位)**:
  - ⚠ **未激活的 `v1.2` 章程行已过时**——它是照 K1 复制的 `take_profit_retrace=0.05`/`max_hold_days=5`,memo 需求 1 明确「勿按早期版本回落 5% 实现」。**作废方式**:**保留 `v1.2` 行不激活**(`is_active=0`/`activated_at=NULL` 不变,留历史留痕、**不删**——`strategy_versions` 是 append-only 归因链,删行破坏「大脑按版本归因」审计),新建 **`v1.3` 章程行**,在其 changelog 注明「取代 v1.2 章程行,勿激活 v1.2」。理由:v1.2 行从未激活、从未 govern 任何真实周,留作 inert 无害;删除破坏审计完整性。
  - **v1.3 config = 从 DB 读 K1 config 复制**(禁手抄,同 `charter_v1_2.py` 姿势;新脚本 `scripts/charter_v1_3.py`),**只改六字段**、其余逐字段 = K1:
    - 仓位三字段(承 v1.2 三仓制):`max_positions` 5→**3** / `single_cap` 20000→**40000** / `max_exposure_frac` 0.6→**1.0**;
    - 退出三字段(v1.3 新):`take_profit_retrace` 0.05→**0.08** / `time_exit_only_if_unprofitable`→**True** / `max_hold_days_profit`→**15**(`max_hold_days` 保持 **5** = 非浮盈时间退出档,`stop_pct` 保持 **0.05**)。
    - `brain.save_version("v1.3", rule={"config": <复制并改六字段>, "lineage": "K1"}, changelog="内核血缘=K1 未改一字;本行=v1.2 三仓制 + v1.3 退出规则改革(回落 8% + 浮盈豁免时间退出硬上限 15 + 非浮盈 D5 退出);**取代 v1.2 章程行,勿激活 v1.2**。风险登记:回落 8% 系 H9 V0 网格观察免测采纳;浮盈豁免组合版未整体回测(最接近 H9-V3 差 724 元未过 2026 门禁);用户知情越线采纳 2026-07-25。", activate=False)`。version=**`v1.3`**(系统版号,绝不碰 K 字头);K1 行原样保留、`is_active` 仍 K1。
  - **切换器**沿用 `scripts/activate_charter.py`(staged 语义:前置校验无 `status='open'` 持仓 + old→new diff + `--confirm` 才 `activate_version`)——**目标版本从 `v1.2` 改为 `v1.3`**(加 `--target v1.3` 或改脚本默认);历史洗白修复(`activated_at`/`config_active_at`/`reviews.strategy_version` 按周取「当时现役」)v1.2-A 步骤 1 已就位,**v1.3 复用不重写**。
- **①-F 卖出费估算(诚实标注为估算,单一源住一处)**:新 `neckline/fees.py`(唯一源)`estimate_sell_fee(sell_amount, buy_fees, buy_amount, cfg)`:
  - **印花税**(卖出单边):`sell_amount × STAMP_DUTY_SELL`(命名常量;**⚠ 当前统计口径 = 万5 = 0.0005**〔2023-08-28 起单边卖出减半〕,**非 planner 上级建议的千1**——政策值住此一处,若变动改此常量;**需用户确认用哪个**);
  - **过户费**(双边):`sell_amount × TRANSFER_FEE`(=0.00001,沪深已统一);
  - **佣金**:优先从买入实付反推 `comm_rate = max(buy_fees − buy_amount×TRANSFER_FEE, 0)/buy_amount`(⚠ 买入若命中 5 元最低佣金则反推偏高、估算偏保守——诚实标注);无 `buy_fees` → 兜底 `DEFAULT_COMMISSION_RATE`(万2.5=0.00025);`commission = max(sell_amount×comm_rate, MIN_COMMISSION=5.0)`;
  - 费率均可由 `app_settings` fee 参数覆盖(用户在设置存一份费率;缺省 = 上述统计值)。
  - **误差影响写死**:估算只用于 ①-C 的 D5 净浮盈判向 + 呼吸台账 `edgeToPrice`/先手成本的实时近似;**误差(几元)只在「净浮盈 ≈0 盈亏平衡线附近」翻转 time-exit vs profit-exempt 判向**,对明显盈/亏单无影响。真实卖出后 `sell_fees` 回填 → **周复盘对账一律用真数、不用估数**。回测引擎侧用引擎既有 fee 模型(双边精确),不走本估算。
- **①-G §2.1 章程文本同步(planner 已改)**:§2.1 第 2 条按需求 6 改写 + 风险登记(见 §2.1);§五铁律 hold 两档 + 回落 8% 已同步(见本节 intro 铁律)。

**① 验收**:①护栏单测——K1 config 六年回测 N=1288/−20.53% 逐位不变(新字段默认 None/False 不改写历史);②回测引擎条件退出——`time_exit_only_if_unprofitable=True`+`max_hold_days_profit=15` 下浮盈单豁免、非浮盈单 D5 退出、D15 硬上限强退,与 `research/h9_exit_reform.py` V1 口径对拍;③`scan_time_exits` 三态正确、config 未启用退回单档 = v1.1 D5 行为;④`estimate_sell_fee` 反推佣金 + 最低佣金地板 + 印花税/过户费,盈亏平衡线附近判向敏感性单测;⑤charter `v1.3` 行落库(config 承 K1 血缘只改六字段、`v1.2` 行仍不激活)、切换器目标 v1.3、洗白反例仍锁死;⑥notify 两档文案、`__all__` 五入口不变;⑦迁移 `positions.buy_fees`/`sell_fees` 生产库副本 integrity ok + 重跑不炸;⑧pytest 零回归。**⑨ staged 活体(留 ⑦/用户)**:清仓 + 确认后激活 v1.3 → entry-suggestion 按 4 万、回落止盈按 8%、D5 两档生效。**🔴 碰纪律章程,建议完工后叫一次 review(用户定)。**

---

### v1.3-② · 持仓管理层:K4 牌每日对持仓重算(推送路由 🔴 + 体检 compute @builder)

**背景**:需求 7。K4 红黄牌现仅买前安检;用户定案「持仓不是冷冻的」——牌须每日对**持仓票**重算并推送警示。**判据全读 DB** `strategy_versions` 的 `K4` 行(`is_active=0`)`rule_json["k4_advisory"]` 六节(`hard_cut`/`avoid_flag`/`exec_hint`/`circuit_breaker`/`intel_order`/`note`),**不抄常量**;证据口径 `research/k4_assembly_report.md` §1/§R3。

- **②-A 持仓 K4 体检(EOD,16:35 报告管线)**:新 `report/holding_k4_check.py`——对每只 open 持仓在当日 EOD 面板上重算 K4 advisory 命中(同「自选体检 `watchlist_check.py`」姿势,只把输入域换成持仓)。⚠ **advisory expr 是字符串/人读规格,非可执行**(如 `"turnover_rate > 10"`、`"行业强度(top20%中位数)连续≥4天成员"`)→ builder 写**可执行 polars 镜像**逐条对齐 advisory 语义,阈值优先从 advisory 结构读、否则镜像为模块常量并注明「advisory=规格档(策略线档案)、polars=可执行镜像(系统线),改阈值须同改两处」——**同 `sentinel/circuit.py` 常量 vs advisory `circuit_breaker` 文字的既定先例**(§七 Backlog 已立此规矩)。
- **②-B 警示分级(强 / 普通,写死)**:
  - **强警示(→ APNs + 看板)**:`hard_cut` 类——**年线下涨停 / 放量大阳 = 派发警报**(`A3_belowyear_limitup` / `B1_volume_stacking`,即 STRATEGY_LAB Backlog「诱多做局反向哨兵」并入本需求,数字依据雷区地图 3-⑤:年线下降势票涨停 → 3 日 −2.06%/2026 −3.43%)、**题材持续 ≥4 天**(`A2_theme_persist_ge_4`)、**换手率 >10%**(`A1_turnover_gt_10`)。
  - **普通警示(→ 盘中看板 / 报告卡,不推 APNs)**:`avoid_flag` 类——题材持续第 2/3 天(`B3`)、双金叉态(`B2`)、量能堆积(`B1`)等标注类。
  - **证据强度标注(守 §2.4 铁律「证伪只用价量结构」)**:换手 / 年线下涨停 / 放量大阳 = **纯价量结构(强证据)**;**题材持续天数依赖概念板块成分(K2「成分洞」)= 弱证据**,展示须标「成分类,参考」,**不作硬证伪**。
- **②-C 强警示推送(复用既有通道,不新增第六类)**:强警示走 `CATEGORY_D5EXIT`(既有「持仓执行提醒」APNs category,语义泛化为「持仓需处置」= D5 时间退出 ∪ K4 派发警报),受既有 `push_d5exit` 开关(**或** builder 加一个 `app_settings.push_holding_alert` 开关解耦——**开关不是「推送类」,不破「五类」约束**);**notify `__all__` 仍五入口**(K4 强警示走 `push_d5_exit` 加 `kind` 参数区分文案,守住「APNs category 仍五类」)。⚠ **用户可否决**:若要 K4 强警示独立开关 / 独立 category → 升级为第六类(须用户明确批准打破「五类」)。
- **②-D 情景树每日对照勾选(复用 v1.2-B `scenario-outcome`,不新造写路径)**:持仓期间每日(16:35 报告持仓卡 + 客户端)提醒用户对照该持仓关联决策日志(via `decision_log.position_id`)第⑦项情景树——哪个情景兑现、有没有按预案执行 → 勾 `matched` 走既有 `POST /decisions/{id}/scenario-outcome`(只翻 `matched`、情景文本只读)。**无任何新写端点**。
- **②-E 盘中涨停派发的实时化(数据边界,诚实定范围)**:v1.3 **权威每日重算走 EOD 16:35**(数据完整、免费源可靠);「持仓票盘中突现年线下涨停」的实时检测**折进既有持仓哨兵**(intraday 用 EOD 派生的 ma250 + 实时价判「现价 ≥ 年线下涨停价」,涨跌停价读 `limit_derived.compute_intraday_limit_prices`)——**在既有持仓哨兵 intraday 判逻辑加一条价量结构分支即可,不新拉资金面源(§2.4:证伪只用价量结构)**;若 intraday 成本/复杂度超预算,**降级为纯 EOD**(memo「每日重算」EOD 已满足),盘中实时派发列为后续增强。

**② 验收**:①单测——持仓 K4 体检读 DB advisory(不抄常量)、polars 镜像与 advisory 语义对齐;强/普通分级正确(年线下涨停/放量大阳/题材≥4天/换手>10% 判强、题材2-3天判普通);证据强度标注(题材类标弱证据);②强警示走 D5EXIT category APNs(开关关时跳过)、`__all__` 五入口不变;③情景树每日勾选复用 `scenario-outcome`、无新写端点、情景文本不可改;④EOD 主路径 + intraday 涨停分支(或降级 EOD)各验;⑤pytest 零回归。**🔴 推送路由改动,与 ① 同批建议 review。**

> **② 完工纪要(2026-07-26,@builder-pro,🔴 未部署)**:需求 7 落地。**第六类推送**(用户 2026-07-26 拍板,推翻 planner「复用 D5EXIT」默认):独立 category `HOLDINGALERT` + 独立开关 `push_holding_alert`(默认开)——`notify.__all__` 五→六(新 `push_holding_alert`)、apns category 五→六、`app_settings` 幂等加 `push_holding_alert` 列、`GET/PUT /settings/push` 契约扩六字段、§2.4 推送白名单文字已改六类。**K4 判据全读 DB `K4.k4_advisory`**(evidence 文字读 DB,不抄常量;判据阈值 = `report/holding_k4_check.py` 命名常量镜像,逐条对应关系写在模块头 docstring 对照表 + 改阈值须同改两处)。**分级**:强价量(A1 换手>10 / A3 年线下涨停 / A3b 年线下放量大阳〔诱多做局反向哨兵并入〕)→ `has_strong` 触发第六类 APNs;普通(B1 堆积 / B2 双金叉 / B4 追强)只进看板;题材持续天数(A2≥4 / B3 2-3)= 概念板块 board_age 代理、**弱证据标 constituent(参考)不单独触发 APNs**。放量大阳「B1 既列强又列普通」的矛盾由**年线下闸**自洽解(年线下=A3b 派发强 / 年线上=B1 堆积普通)。**⚠ A3b 量能门槛贴雷区地图 3-⑤ 实测证据口径 = 量比(vol/vol_ma5)≥2**(那个「年线下放量大阳事后 3 日 −1.04%」正是此口径下测出;强警示推锁屏,门槛必须 = 实测集合、不能比证据更宽,故 A3b 单用量比≥2、不套 B1 的 cnt3 堆积条件);**B1 维持 DB advisory 原文 ×1.5(vol_ma20)不动**(只进看板、不推送,且是 advisory 原文形态)——两者量能阈值刻意分叉、模块头对照表 + 常量注释写明理由(可执行镜像 vs 人读规格档允许分叉、但分叉写明,防后人当笔误"修正"回去)。**② 未降级项**:②-E 盘中实时派发**降级为纯 EOD**(plan 明允「memo 每日重算 EOD 已满足,盘中实时列后续增强」),不碰 intraday 持仓哨兵。**② seam 已接**:`report/holding_k4_check.py` 在 16:35 报告管线(`build_report`)对每只 open 持仓算好 D5 收盘净浮盈(扣双边费:买入费读 `positions.buy_fees`、卖出费 `fees.estimate_sell_fee` 估算)→ 落 `holding_eod_check` 表 → **`sentinel/precall.py::scan_time_exits(net_float_provider=holding_store.net_float_provider(...))` 已接线**(次日 9:25:30 读最近一份 net_float),修复 v1.3-① 留的「provider 恒 None → 激活后浮盈豁免形同虚设」地基缺口(单测 `test_net_float_provider_fixes_profit_exemption` 锁死:同 config 下 provider 给正浮盈→profit_exempt、None→保守 time_exit)。**情景树每日对照**(②-D):复用既有 `POST /decisions/{id}/scenario-outcome`(无新写端点)+ `GET /decisions?position_id=` 只读过滤挑出待对照决策;`PositionOut` 加 `scenarioReviewPending`。**契约增量**:`PositionOut.k4Advisory[]`(服务端 16:35 算好,GET /positions 读快照嵌)+ `scenarioReviewPending` + `PushSettingsOut/SettingsPushIn.holdingAlert`。全量 pytest **1031 passed / 2 skipped**(基线 1010 + 21 新,0 回归)。**未碰**生产 ECS / STRATEGY_LAB / research/* / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1)/ v1.3-①③④⑤⑥⑦。改 schema 前已 `cp -p` 备份。🔴 与 ① 同批建议 review。

---

### v1.3-③ · 情报官(复盘情报件 + 板块资金流 + 候选管线改版 + 消息面扫描,@builder)

**背景**:需求 3 + 需求 3 补充 + 需求 5 合并(①④ 两步要的正是情报件,分开做 = 同批代码写两遍)。系统重定位:机器不替选股,候选 = 「过完安检、值得花注意力的票」,终选权用户。

- **③-C1 复盘情报件(全 EOD 可算,折进 16:35 报告新「情报」节)**:涨幅榜 / 跌幅榜 / 涨停梯队(连板高度分布,复用情绪仪表盘)/ 跌停榜 / 大盘量能(`index_daily` 成交额 + 5 日均)/ 最强题材及其核心一二名(`report/sectors.py::compute_sector_strength` top + members 排序)/ 市值偏好(`daily_basic.total_mv` 给当日涨停股分市值桶)/ 涨跌停制度偏好(把涨停股按 `limit_derived.resolve_limit_pct` 分 10/20/30cm,看哪类受资金欢迎)/ 题材持续天数(`sectors._add_board_age` 启动第几天,区分 2-3 日持续 vs 一日游)。**证据强度标注**:题材 / 成分类字段依赖概念板块成分(K2「成分洞」)**标「参考、不作强证据」**;涨跌停 / 量能 / 榜单 = EOD 硬数据(强)。竞价 / L2 盘口无历史,不做。
- **③-C2 板块资金流展示(`moneyflow_dc`,2023-09+)**:板块层资金净流入排序展示(拥挤情报件,**非选股信号**——K2 判决板块层有效但无次日领先性)。**落盘一律走 `write_table_day`**(v1 类型漂移防线);2023-09 前无数据的板块留空标注、不臆造。
- **③-C3 候选池四步管线改版(需求 5,K1 entry mask 退役)**:新 `report/intel_candidates.py`(或改造 `candidates.py`——但**候选评分 `_base_score_expr` 保留供自选体检/纪律红绿灯复用,不删**):
  - **① 板块层** = 主线识别器拥挤度 top(`compute_sector_strength`):**用户五板块常驻** + **当日暴起板块**(当日拥挤度 top-N)。
    - **五板块常驻 = 用户 2026-07-26 从真实数据里挑定**:**芯片概念 / 创新药 / 储能 / 机器人概念 / 稀土永磁**(存 `app_settings.intel_watch_boards`,可配;builder 落地时按 `ths_index.name` 精确匹配取 `ts_code` 落库,**不要按关键词模糊匹配**——同一方向有多个近义板块,模糊匹配会选错成分)。**选型分工写死**:常驻 = 用户长期盯的**方向锚(宽标签)**;窄板块(CPO / 固态电池 / 人形机器人 / 减速器 这类)**交给「当日暴起」由雷达自己发现,不写死进常驻**(写死会挤占雷达名额)。
    - **⚠ 板块池必须先过卫生线(2026-07-26 实测发现,用户拍板剔除)**:原始 394 个概念板块按成交额排名,**前排全是资格 / 宽基成分类标签**——融资融券(3837 只)、深股通(1875)、沪股通(1640)、沪深300 / 中证500 / 上证180 样本股、专精特新(1212)、国企改革(1468)、人民币贬值受益、中报预增……成分上千、永远霸榜,**当拥挤度信号用等于没信号**。落地:按**名称模式**(融资融券 / 股通 / 成份股 / 样本股 / 指数 / 专精特新 / 国企改革 / 预增预减 / 贬值升值 / 破净 / 送转 / 回购 / 增减持 / 摘帽 / ST / 次新 / 富时 / MSCI / 标普 / QFII / AH股 / 转债 / 参股 / 举牌 / 重组 / 壳资源 / 绩优 / 超跌 / 机构 / 北交所 / 创业板 / 科创 等)+ **成分数上限**双闸剔除,**模式清单住一处命名常量**(同 `board.py` 整段正则先例,禁各处抄)。剔除清单要可配 + 剔了什么要能审计(落日志或报告脚注),**不许静默吞掉板块**。
    - **⚠ `ths_member` 是当前快照、无日期字段(K2「成分洞」)**:成分归属回看历史会有偏差 → 板块成分类判据**一律标弱证据、不作强判据**(与 C1 同口径)。
  - **② 个股层** = 上述板块成员、**全板块 MAIN/GEM/STAR**(排 BSE,读 `board.py`),**只过卫生线**(非 ST + `amount_ma20≥2000万` 流动性底线 + `close≥2` + `ma20 非空`,= `base_universe_expr` 子集)+ **非次新 120**(`list_date` > 120 交易日)+ **趋势向上**(`close>ma20` 代理,标注为粗代理);**不套 K1 主板 only、不套 pullback/breakout 回调买点**(与 K1 entry mask 解耦)。
  - **③ K4 安检** = 读 DB K4 advisory:`hard_cut` 命中 → **拦截出池**;`avoid_flag` 命中 → **打标保留**(机器不禁、情报展示给人判)。**复用 ②-A 的 polars 镜像评估器**(同一份,不写两遍)。
  - **④ 情报排序** = 板块资金流强度(C2)+ 题材持续天数**反用**(1 天新鲜 > 2-3 天警惕 > ≥4 天已在 ③ hard_cut 剔)+ 温和带标注 → 出 **20 只**交用户终选。
    - **⚠ 常驻板块保底名额(用户 2026-07-26 拍板,写死不自由发挥)**:纯情报排序会让当日最强题材簇占满整榜(2026-07-22 实测:20 只几乎全医药,芯片/储能/机器人/稀土一只没进),用户要五个长期方向每天都有情报到手。落地:**每个常驻板块保底 2 只**,取该板块内情报排序最高的 2 只——**只从过完 ②卫生线 + ③K4 hard_cut 的池子里选**(`hard_cut` 命中的**绝不因保底被捞回**;合格票不足 2 只时有几只放几只、缺额退回公共池,**不许为凑数降卫生线 / 放宽 hard_cut**)。**剩余名额**(20 − 实际保底数)按情报排序从全池竞争(常驻其余票 + 暴起板块票),去重、总数仍 20。**一票同属多个常驻板块只占一个保底名额**,归属口径 = **配置顺序(`intel_watch_boards` 名单顺序)里最先轮到且仍有空额的板块认领**(claim 后不再被后续常驻/竞争重复计入)。**出参可识别来源**:`intelRank.source`(`quota` 保底 / `competition` 竞争 / `forced` 问询)供 ⑥ 客户端说清「为什么在榜」。落地 `report/intel_candidates.py`(常量 `QUOTA_PER_PERMANENT_BOARD=2`)。
  - **产品语义变更(§2.3 已同步)**:候选 = 「过完安检、值得关注的票」**非「会涨的票」**,**候选卡文案跟上**(⑥ 客户端)。**生成域刻意含高弹**(用户知情拍板)。
  - **⚠ §3.8 铁律「同码三跑道」已重新表述(见 §3.8)**:候选生成不再走 K1 entry mask,与旧「回测/报告/问询同码」冲突——新表述 = **纪律参数单一源 + 退出规则回测/实盘同码(不变)**;候选生成**单列为「情报筛选管线」,与回测信号解耦**(它输出「值得关注」非「会涨」、不声称回测过的 alpha、故不要求同码);**问询台/自选体检的纪律红绿灯仍与报告同码**(`base_universe_expr` + config 禁买过滤,见 ⑤)。**这是本版最易自相矛盾处,builder 落地对照 §3.8 新表述核对。**
- **③-C4 晚间消息面公告扫描(持仓 + 自选:减持/立案/暴雷/监管)**:**数据源待查**——先查 TuShare 600 元档公告接口(如 `anns_d`)的覆盖与时效;**查不到/覆盖不足 → LLM 联网搜索兜底**(复用 `openai_compat` 带搜索调用,**守 `read_timeout=90` 的坑**,见项目 CLAUDE.md「v1 上线首日」)。命中落 `news_alerts` 表(`code`/`trade_date`/`category`/`summary`/`source`/`created_at`,幂等)+ 报告「消息面」节 + 客户端展示。**缺 key 全链路优雅降级不崩**(§3.4);扫描失败/超时**不阻断主报告管线**(尽力而为)。

**③ 验收**:①C1 情报件各字段 EOD 算对(榜单/梯队/量能/市值/制度/题材天数),成分类标弱证据;②C2 板块资金流走 `write_table_day`、2023-09 前留空不臆造;③C3 四步管线——拥挤度 top + 五板块常驻、全板块 MAIN/GEM/STAR 卫生线 + 非次新 + 趋势向上、K4 hard_cut 拦/avoid_flag 标(读 DB 不抄常量、复用 ②-A 镜像)、情报排序反用题材天数、出 20 只、**不套 K1 entry mask**(单测断言候选不经 `build_entry_mask`);④§3.8 新表述对照核对(候选管线与回测解耦、纪律同码保留);⑤C4 公告扫描——TuShare 源侦察结论写档、LLM 兜底守 90s、缺 key 降级不崩、不阻断主管线;⑥迁移 `news_alerts` 表 integrity ok;⑦pytest 零回归。**端到端(隔离库真实 backfill)**:跑一天真报告出情报节 + 20 只新口径候选 + 持仓/自选消息面(缺 key 降级)。

---

### v1.3-④ · 挂单未成交追踪(原 v1.2.1-C 全文,归 v1.3;@builder)

**内容 = 原 §五 v1.2.1-C 施工图全文**(保留在下方 v1.2.1 小节,**归属改 v1.3**,此处不复制以免双源)。要点重述:追踪 `decision_log.status='pending'`(挂了没成交)的后续 N=5 交易日走势,检验用户「逆向选择:专接下坠、错过起飞」假设;`decision_pending_track` 表幂等建;折进 16:35 报告管线复用 EOD 面板**不新拉源**;第 N 日 `pending→expired`。**归 v1.3 理由(memo)**:它记「挂了没成交的计划后来怎么走」,**不部署就永远补不回那几周数据**(与归因不同——归因晚做只晚出结论、数据仍在)。
**④ 验收 = 原 C 验收全文 + v1.3-⑦ 部署活体**(pending 逐日落 track、第 N 日 expired、复用面板不新拉源、`write_table_day` 未绕开)。

---

### v1.3-⑤ · 问询台选股域漂移清理(原 v1.2.1-D2,归 v1.3;@builder)

**内容 = 原 §五 v1.2.1-D2 施工图全文**(保留在下方,**归属改 v1.3**)。要点:`api/inquiry.py::run_deterministic_checks` 手写重复选股域逻辑 → 复用 `research/panel.py::base_universe_expr()`(选股域揉一条不拆解)+ config 可配禁买过滤逐项拆,照 `report/watchlist_check.py::_discipline_checks` 姿势。**与 ③-C3 §3.8 新表述一致**:这条正是「纪律红绿灯同码保留」的落点(候选生成解耦、纪律核对仍同码)。
**⑤ 验收 = 原 D2 验收全文**(问询台确定性核对与报告 `watchlist_check` 同票同日同判、K1 现役下与旧行为等价)。

---

### v1.3-⑥ · 客户端跟进(@builder,依赖 ①②③ 契约)

> **v1.2-E 已完工的客户端不重做**(八项决策日志表单 / 熔断状态横幅 / 呼吸台账 / `closeReason` picker / entry-suggestion 区间壳均在);v1.3-⑥ 只加**增量**。

- **⑥-A D 计数徽标两档口径**:持仓卡 D 徽标随净浮盈两档——非浮盈单 `D{n}/D{5}`(时间退出档)、浮盈豁免单 `D{n}/D{15}`(硬上限档);读服务端 `PositionOut` 新增 `maxHoldDaysEffective`(=5 或 15,服务端按 D5 净浮盈判好下发,客户端不重算)+ `timeExitState`(`time_exit_next_day`/`profit_exempt`/`hard_cap_exit`/`holding`)。D5 醒目横幅文案随档区分。
- **⑥-B 费用录入**:补录开仓 sheet 加 `buyFees` 必填(实付买入费)→ `POST /positions` 带 `buyFees`;清仓 sheet 加 `sellFees` 可选(真实卖出费,成交后回填)→ `PositionCloseIn.sellFees`。文案标「实付费用,供周复盘对账用真数」。
- **⑥-C K4 持仓牌警示展示**:持仓卡展示 K4 体检(强/普通分级色调 + 命中项文案 + 证据强度标「价量结构/成分参考」);强警示置顶醒目(派发警报「年线下涨停疑似派发,建议减仓/勿追」)。读 `PositionOut.k4Advisory[]`。强警示 APNs 复用既有持仓提醒开关(⑥-E)。
- **⑥-D 情景树每日对照勾选入口**:持仓卡露出该持仓关联决策日志的情景树(via `position_id`),每日提醒勾选兑现项 → 复用既有 `setScenarioOutcome`(v1.2-E 已实现,只需在持仓卡加每日提醒入口,情景文本只读)。
- **⑥-E 候选卡新语义文案**:候选列表标题/副文案从「系统认为会涨」改「过完安检、值得关注,终选在你」;候选卡展示 K4 安检标(hard_cut 已拦不出现、avoid_flag 打标展示)+ 情报排序理由(资金流强度/题材天数)+ 高弹标注。**不再把 K1 四件套买点展示为「推荐买点」**(候选不再是回测信号)——改展示情报维度。
- **⑥-F 情报包展示**:今日计划/新「情报」板块展示 C1 复盘情报件(榜单/题材/制度偏好)+ C2 板块资金流 + C4 消息面提示。iOS/macOS 通用(macOS 大表更适合复盘情报)。

**⑥ 验收**:双端 `xcodebuild BUILD SUCCEEDED` + iOS Simulator TEST;两档 D 徽标(浮盈/非浮盈截图各一)、buyFees/sellFees 录入往返、K4 持仓牌强/普通展示 + 证据强度标注、情景树每日勾选复用既有端点、候选卡新语义文案(无「推荐买点」)、情报包展示。单测覆盖 `maxHoldDaysEffective`/`timeExitState`/`buyFees`/`sellFees`/`k4Advisory` 解码 + 两档 D 徽标派生。绿涨红跌不变。

---

### v1.3-⑦ · 部署 + 章程激活(🔴 合并 v1.2-F,@builder-pro + 用户)

> **本块 = v1.2-F + v1.3 部署合并,一次推 v1.2+v1.3 全部代码 + 累积迁移。本版最大部署风险。**

- **⑦-A 版号**:`app.py::VERSION` `v1.1.1` → **`v1.3`**(跳过 v1.2 对外版号,用户拍板)。
- **⑦-B 累积迁移一次性执行(先备份、逐项验证、写死回退路径)**:
  - **迁移前 `sqlite3 .backup` 在线一致性备份**(照 v1.1-H 姿势:`data/neckline.db.bak-v13-<date>`,integrity ok + 业务表行数逐表吻合 + 原地保留 rsync 排除)。**改 schema 前另 `cp -p` 一份**(双保险)。
  - **迁移项(v1.2 累积 + v1.3 新增,全部 `IF NOT EXISTS`/`_migrate_columns` 幂等)**:v1.2——`decision_log` / `circuit_breaker` / `breathing_t_trades` 三表 + `positions.close_reason` + `app_settings.push_circuit` + (`strategy_versions.activated_at` K1 回填 / `reviews.strategy_version` 校验存在);v1.3——`positions.buy_fees` / `positions.sell_fees` + `decision_pending_track` 表(④)+ `news_alerts` 表(③-C4)+ `app_settings.intel_watch_boards`(五板块可配)+ (可选)`app_settings` fee 参数 / `push_holding_alert`。
  - **逐项验证**:迁移后 `PRAGMA integrity_check` ok + **业务零丢失**(positions/devices/reports/decision_log 等行数迁移前后一致)+ `is_active` 仍 K1(未激活前)。
  - **回退路径写死**:任一验证不过 → 停服 + 从 `.backup` 恢复 + 回滚代码到 v1.1.1 tag + `neckline.service` 起回 v1.1.1(**生产从未跑过 v1.2,回退目标就是当前 v1.1.1,干净**)。
- **⑦-C sync_code.sh 首次真部署(v1.2-0 footgun 活体收官)**:用**已修好的 `sync_code.sh`**(v1.2-0 已修 chown/chmod 收尾 prune `data/` + 末尾只读属主自检 `exit 1`)首次真部署——验证收尾 prune 生效、部署后 `data/neckline.db` 属主仍 `neckline:neckline`、只读属主自检绿、服务 restart 无 502(销 §七 footgun 待办 + hz_info §191)。补装 v1.3 新依赖(若有)走阿里云镜像、重启前 preflight import。
- **⑦-D 哨兵/报告 timer 影响核查 + TuShare 配额**:①哨兵 lifespan asyncio 任务重启后 idle RSS 与部署前持平(v1.3 加了持仓 K4 体检/情报件/消息面扫描,核查内存不超 MemoryHigh);②16:35 报告管线加了情报节/候选改版/持仓 K4/挂单追踪/消息面,**报告 timer oneshot 跑完仍瞬态释放**(紧内存友好);③**⚠ 顺手确认生产 16:05 增量拉数没被 TuShare 配额耗尽影响**——策略线 2026-07-25 把配额跑干过(§七 Backlog 已记),核查生产 `daily_update` 增量正常、`moneyflow_dc`/情报所需表拉全;④9:25:30 盘前 tick 加了「熔断中」提醒 + D5 两档 + K4 盘中涨停分支,盘前分支非交易时段不误触发、异常吞掉不掀翻主循环。
- **⑦-E 章程激活 = staged 步骤 2(等用户)**:部署后 `is_active` 仍 K1、生产行为零变化;用户清仓 + 明确确认后,**在 ECS 权威库以 `sudo -u neckline .venv/bin/python scripts/activate_charter.py --target v1.3 --confirm`** 跑切换器 → `is_active` K1→**v1.3**。⚠ **绝不误激活 `v1.2` 旧行**(它是回落 5% 过时行)——切换器目标参数必须 `v1.3`,激活前 diff 打印核对 `take_profit_retrace=0.08`。**跨线**:激活后 `STRATEGY_LAB §一「现役=K1」`不准确,**用户在策略线会话同步一句**(现役 config=v1.3 章程行、内核血缘 K1)。
- **⑦-F 运维留痕**:更新 `~/Lino/hz_info.md`(新表/列、VERSION v1.3、footgun 销账、章程 staged 现状、配额核查);变更日志记一行。

**⑦ 验收(活体,逐项写死;含覆盖 v1.2 六块活体项)**:
1. **sync_code.sh 首验**:收尾 prune 生效、`data/neckline.db` 属主仍 `neckline:neckline`、自检绿、restart 无 502。
2. **累积迁移**:v1.2 三表 + `close_reason`/`push_circuit`/`activated_at`(K1 回填有值)/`strategy_version` + v1.3 `buy_fees`/`sell_fees`/`decision_pending_track`/`news_alerts`/`intel_watch_boards` 全建成、生产库 integrity ok、业务零丢失、回退路径演练可用。
3. **VERSION**:公网 health 返 `v1.3`。
4. **覆盖 v1.2 六块活体(首次上生产验)**:决策日志八项真机录入(createdAt 服务端生成)+ link/cancel/scenario-outcome;熔断真机推送(造连续 3 笔止损/单日净亏 ≤−4000 → `circuit_breaker` 落锁 + 第五类 APNs + 解锁往返);呼吸台账真机增删 + 先手距离;`close_reason` 真机写入;entry-suggestion 返区间双档;四→五推送开关回读。
5. **覆盖 v1.3 活体**:两档 D5(浮盈豁免/非浮盈退)+ 硬上限 D15;买卖实付费用录入 + 回填;K4 持仓牌强警示真机 APNs(复用持仓提醒通道)+ 普通警示进看板;情报节 + 20 只新口径候选 + 消息面(缺 key 降级);挂单追踪逐日落库。
6. **章程 staged 激活实证(用户)**:清仓 + 确认后跑切换器 → `is_active=v1.3`、回落 8% + 两档时间退出 + 4 万区间生效、下一份周复盘用 v1.3 判激活后周 / K1 判之前周(洗白修复实证);**未误激活 v1.2**。**⚠ 激活前确认 v1.3-② 的 16:35 net-float provider 已生效**(激活当日的前一交易日 16:35 报告已跑过、`holding_eod_check` 已有当日持仓的 net_float 快照)——否则激活后首日 precall 因无快照对该持仓 provider 返 None、保守判非浮盈,浮盈豁免当日形同虚设(次日 16:35 快照落地后自愈)。清仓后再开新仓的 staged 流程下,首笔持仓需至少一个 16:35 EOD 周期后浮盈豁免才完全生效,验收时留意这个一次性冷启动窗口。
7. **老端点前向兼容不崩、无 token 401;哨兵 idle RSS 持平、16:05 配额正常。**
8. **⚠ 一次性大迁移 + 碰纪律章程 + 金额判定,强烈建议完工后叫一次 review(用户定)。**

---

### v1.3 客户端契约清单(新端点/新字段,供 v1.3-⑥ 对照;仅列 v1.2 之上的增量)

- **退出规则两档(①)**:`PositionOut` 新增 `maxHoldDaysEffective:int`(5 或 15,服务端按 D5 净浮盈判)、`timeExitState:码`(`time_exit_next_day`/`profit_exempt`/`hard_cap_exit`/`holding`)。客户端 D 徽标/横幅按此两档展示,不重算净浮盈。
- **费用(用户拍板 2)**:`PositionOpenIn` 加 `buyFees:REAL`(补录开仓实付,必填);`PositionCloseIn` 加 `sellFees:REAL?`(清仓实付,可选回填)。`PositionOut` 回显 `buyFees`/`sellFees`。
- **K4 持仓牌(②,已交付)**:`PositionOut` 内嵌 `k4Advisory:[{code, label, level(strong/normal), evidence, evidenceStrength(price_volume/constituent)}]`(读 DB advisory 命中,服务端 16:35 算好、GET /positions 读快照下发)+ `scenarioReviewPending:bool`(②-D 情景树每日对照,该持仓有关联决策的非空情景树待勾选)。强警示走**第六类独立** APNs `CATEGORY_HOLDINGALERT`(用户 2026-07-26 拍板不复用 D5EXIT)+ 独立开关 `holdingAlert` 于 `GET/PUT /settings/push`(六字段:report/retreatBrake/precall/d5exit/circuit/**holdingAlert**,均必填)。情景树勾选仍走既有 `POST /decisions/{id}/scenario-outcome`(无新写端点),挑出待对照决策走 `GET /decisions?position_id=<id>`(只读过滤,向后兼容)。**客户端注册第六个 `UNNotificationCategory`「HOLDINGALERT」**。
- **候选新语义(③-C3)**:`CandidateOut` 语义变(不再是回测信号)——新增 `k4Flags:[码]`(avoid_flag 打标)、`intelRank:{sectorFlow, themePersistDays, highElasticity:bool}`(情报排序理由);`patternTags`/四件套字段保留但客户端**改为情报维度展示、不标「推荐买点」**。`ReportOut` 新增 `intel:{gainers, losers, limitUpLadder, limitDown, marketVolume, topThemes, mvPreference, limitRegimePreference}`(C1)+ `sectorMoneyflow[]`(C2)+ `newsAlerts:[{code, category, summary, source}]`(C4)。
- **情报官板块**:客户端「情报」展示区读 `ReportOut.intel`/`sectorMoneyflow`/`newsAlerts`。
- **挂单追踪(④)**:如需端点 builder 定 `GET /decisions/{id}/track` 或并入 `GET /decisions`(v1.2 客户端契约清单已占位)。
- **周复盘归因(v1.3.1-D,后发)**:`thesisAttribution`/`playbookAttribution`/`thesisByPlaybook`/`breathingTContribution`/`noDecisionLogTrades` 随 v1.3.1 落客户端,v1.3-⑥ 不做。

---

### 【已完工待部署 / 施工图待建 · 一次并入 v1.3-⑦ 部署】v1.2 / v1.2.1 施工图(全文保留,勿删)

> **状态(2026-07-26 v1.3 立项时标注)**:**v1.2 先发六块(0/A/A2/B/G/E)代码已完工、本地全绿、但从未部署**(生产仍 v1.1.1);其活体验收随 **v1.3-⑦** 首次上生产。**v1.2.1-C/D2 施工图已写、代码待建 → 归 v1.3-④/⑤**(本次施工);**v1.2.1-D(归因)施工图已写、留 v1.3.1 后发**。以下施工图全文不改(历史留痕 + builder 落地对照),**分块编号沿用原 v1.2/v1.2.1**,归属与状态以本横幅 + §四 为准。

## 五(旧)、当前版本 Plan(v1.2 + v1.2.1 两波 · 三仓章程 + 熔断纪律 + 决策日志八项 + 呼吸台账 + 归因闭环)

> **本(旧)节是 v1.2 / v1.2.1 各块的施工细节权威**(版本层已被上方 v1.3 取代:v1.2 不单独发、并入 v1.3-⑦ 一次部署;builder 落 v1.3-④/⑤ 时仍按此节的 C/D2 细节实现)。每个交付项写具体行为、build 不用猜;每块末给验收标准(含活体验收)。**两波切分(2026-07-25 增补,策略线扩需求)**:**v1.2 先发**(0/A/A2/B/G/E/F);**v1.2.1 后发**(C/D/D2)。~~齐了用户即可开新打法首笔,挡上线~~(v1.2 挡上线的口径已被 v1.3 合并发布取代)。阶段 0–4 完工路线图见「§五附」;v1.1 施工图已归档(见本节末指针)。
>
> **背景(2026-07-25 用户定案 + 同日三版补丁,方向不得改)**:系统重定位为**人机协作**——**用户 = 唯一决策人**,系统 = 情报收集 + 机械分析 + 纪律执行 + 归因审计,**机器不再替用户选股**(策略线三场战役判死的是「机器自动出信号」,见 `STRATEGY_LAB.md` 雷区地图)。v1.2 落地用户行为面配套:① **仓位章程改三仓制**(注意力约束「只做 3 仓」;单笔金额不定死,4 万只作违纪上限;**三仓 = 2 短线追击 + 1 呼吸底仓试验**打法切分)+ 修「周复盘用今天章程重判历史周」的洗白洞;② **熔断纪律(新)**(连续 3 笔止损 / 单日亏损 ≥4000 → 停开新仓、次日只减不加、完成强制复盘解锁;纯提醒层、绝不代下单;§2.1 第 7 条);③ **预注册决策日志(八项)**(下单前录八项,时间戳先于成交防结果污染;新增 ⑦ 应对方案·情景树 + ⑧ 打法标签;**审计件、非下单件**);④ **呼吸试验仓台账(新)**(底仓 / T 仓分离记账、T 费用逐笔、先手成本跟踪);⑤ 客户端双端录入(八项 + 熔断状态 + 台账 + `close_reason`)+ 部署活体。**后发(v1.2.1,不挡上线)**:挂单未成交追踪、决策归因(论点 × 打法双维)、问询台选股域清理。**不新增策略、不改评分 / 涨跌停 / 板块领域规则;推送白名单四类→五类(熔断为第五类,§2.4 已改)。**
>
> **铁律(承 §3.8 + v1.1,builder 逐条守)**:同码不重写;**单一事实源不漂移**(下列一律复用,禁在新文件抄字面量)——止损 `stop_pct` / 回落止盈 `take_profit_retrace` / hold 天数 `max_hold_days` / **单笔上限 `single_cap`(v1.2 激活后 = 4 万,语义已从「推荐值」变为「违纪判定上限」)** / **最多持仓 `max_positions`(v1.2 = 3)** / 敞口 `max_exposure_frac`(v1.2 = 满仓档 ≈1.0)全读现役策略 `neckline.strategy.brain.get_active().rule["config"]`(即 `MomentumConfig` 落库值,与 4D 对账同源);涨跌停幅度读 `data/limit_derived.py`;板块分类读 `data/board.py`(含 `sentinel/quotes.py:to_symbol`);总仓 12 万读 `config.total_capital`;实时源批量拉价走 `sentinel/quotes.py:get_quotes`(**关注池 ≤200 不放大**);**落盘一律走 `data/market_data.py:write_table_day`**(v1 类型漂移防线,勿绕开自己 `write_parquet`);带联网搜索的 LLM 读超时守 **90s**;LLM 缺 key 全链路优雅降级不崩。
>
> **三条本版硬约束**:① **系统永不自动下单 / 撤单 / 改止损**(§3.8);**决策日志是审计件、不是下单件**——录入决策日志绝不触发任何下单动作,仅落库供事后归因。② **决策日志强制度 = 软约束**(用户拍板):允许无决策日志补录开仓,周复盘把「无决策日志的开仓」计为纪律项统计出来,**不做硬阻断**(硬阻断会逼出假日志)。③ **熔断是纯提醒层**(§2.1 第 7 条):触发只做哨兵强提醒 + 客户端状态标记 + 落库,**绝不代下单 / 撤单、服务端绝不拦 `POST /positions`**(客户端自律灰化);熔断阈值 `3` / `4000` 是**命名常量单一源**(住熔断模块,非 config;理由同 `FORCED_REVIEW_LOSS_FRAC`),兜底判据的 `stop_pct` 仍读现役 config。
>
> **分块序列(两波,依赖一眼看清)**:
> · **v1.2 先发(挡上线)**:v1.2-0 footgun ✅ → v1.2-A 三仓章程 + 历史洗白 ✅(步骤1,🔴)→ **v1.2-A2 熔断纪律(🔴,依赖 `positions.close_reason` 新迁移 + 熔断模块 + 第五类推送 + 复用强制复盘解锁)** → v1.2-B 决策日志八项 → **v1.2-G 呼吸试验仓台账(子表)** → v1.2-E 客户端(依赖 A2/B/G 契约)→ v1.2-F 部署 + schema 迁移 + 活体(🔴)。
> · **v1.2.1 后发(不挡上线,攒样本才有用)**:v1.2.1-C 挂单未成交追踪(依赖 B 的 decision_log 已积累 pending)→ v1.2.1-D 决策归因(论点 × 打法双维 + 呼吸 T 贡献,依赖 B/G 样本)→ v1.2.1-D2 问询台选股域清理(独立小清理,无依赖)。三块**互不阻塞、均不挡 v1.2 上线**。
> · **🔴 高危区(点名 @builder-pro):v1.2-A(章程 + 大脑激活)、v1.2-A2(碰纪律章程 + 金额判定 + 第五类推送)、v1.2-F(部署 + schema 迁移 + 哨兵/报告 timer 影响核查)**;其余 @builder。
>
> **推送白名单四类→五类(v1.2)**:① 16:35 报告就绪、② 退潮红色刹车、③ 9:26 盘前校准汇总、④ D5 时间退出、⑤ **熔断提醒(v1.2-A2 新增,§2.1 第 7 条 / §2.4;用户批准打破 v1.2 原「不新增推送类」)**。决策日志 / 呼吸台账不推送(只进客户端)。
>
> **新增 SQLite 表 / 列(`neckline/db.py`,均 `CREATE TABLE IF NOT EXISTS` / `_migrate_columns` 幂等迁移,改 schema 前 `cp -p neckline.db` 备份见 hz_info §12)**:
> · **v1.2 先发**:`decision_log`(八项预注册)、`circuit_breaker`(熔断触发/解锁事件)、`breathing_t_trades`(呼吸 T 子账);`positions` 加 `close_reason`(离场原因枚举,熔断判据);`app_settings` 加 `push_circuit`(第五类推送开关)。(`strategy_versions.activated_at` + `reviews.strategy_version` 已随 v1.2-A 步骤1 就位。)
> · **v1.2.1 后发**:`decision_pending_track`(挂单 N 日追踪,随 C)。

---

### v1.2(先发:footgun + 三仓章程 + 熔断 + 决策日志八项 + 呼吸台账 + 客户端 + 部署;齐了即可开新打法首笔,挡上线)

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

#### v1.2-A2 · 熔断纪律(🔴 碰纪律章程 + 金额判定 + 第五类推送 @builder-pro,与 A 同属章程线)

**背景**:§2.1 第 7 条(2026-07-25 用户批准,数字已定)。连续 3 笔止损 或 单日亏损 ≥4000 → 当日停开新仓、次日只减不加,完成一次强制复盘后解锁。系统无法物理阻止下单 → **哨兵强提醒 + 客户端状态标记 + 日志留痕**;熔断是**纯提醒层,绝不代下单 / 撤单**(§3.8)。

- **A2.1 数据缺口先补:`positions` 幂等加 `close_reason`(离场原因)**——现 `positions` 只有 `sell_price`/`sell_date`,`PositionCloseIn` 只收价格 + 日期 → **判不出「止损离场」**(止损与回落止盈 / 时间退出 / 证伪 / 主动在库里长得一样)。两层解决:
  - **① `positions` 加 `close_reason TEXT`**(幂等 `_migrate_columns`,NULL 默认)。枚举码(**服务端码 + 客户端展示层换算,沿 `boardLabel` 先例**):`STOP_LOSS`→止损 / `TAKE_PROFIT`→回落止盈 / `TIME_EXIT`→时间退出(D5) / `INVALIDATION`→证伪离场 / `MANUAL`→主动离场。`PositionCloseIn` 加可选 `close_reason`(客户端清仓时选;不选 → NULL);`close_position()` 加 `close_reason` 形参写库;`app.py` 的 `POST /positions/{id}/close` 透传。
  - **② 老数据 / 未选时的兜底判据(写死,标注近似)**:仅当 `close_reason IS NULL` 时,熔断评估**近似**判定 —— `sell_price ≤ buy_price×(1−stop_pct)+_EPS`(`stop_pct` 读现役 config,不硬编)→ 计为止损;否则计为非止损。**兜底只用于熔断计数、不回写 `close_reason`**(库里仍 NULL,不臆造历史);**用户已显式选了非 NULL 码 → 信用户的标注、不用价格二次猜**(只有 NULL 才走价格兜底)。此兜底为**近似口径**,plan 明确标注,归因材料展示时须标「近似」。
- **A2.2 熔断阈值单一源 = 命名常量(住熔断模块,不进 config)**:新模块(建议 `neckline/sentinel/circuit.py`,builder 定 host)顶部:`CIRCUIT_CONSECUTIVE_STOPS = 3`、`CIRCUIT_DAILY_LOSS_YUAN = 4000.0`。**理由**:熔断是政策值、非回测参数(同 `review/reconcile.py::FORCED_REVIEW_LOSS_FRAC=0.02` 的既有处置——章程拍板的固定政策值不进 `strategy_versions`,不占大脑)。**禁止**把 3 / 4000 抄进任何别处;兜底判据用的 `stop_pct` 仍读 `brain.get_active().rule["config"]`。
- **A2.3 触发判定口径(两条,写死)**:
  - **连续 3 笔止损**:全部 `status='closed'` 持仓按 `(sell_date, id)` 升序,从最近一笔往前数**尾部连续**的「止损离场」(A2.1 口径:显式 `STOP_LOSS` 或 NULL 价格兜底)笔数;**遇到一笔非止损离场即断链归零**;尾部连续 ≥ `CIRCUIT_CONSECUTIVE_STOPS` → 触发。
  - **单日亏损 ≥ 4000**:当日(某 `sell_date`)**已平仓回合的净实现盈亏合计** `Σ (sell_price−buy_price)×qty`(该 `sell_date` 全部平仓行,**盈亏可互抵 = 净口径**)≤ `−CIRCUIT_DAILY_LOSS_YUAN` → 触发。**不含费用**(`positions` 无费用字段);与交割单口径的差异在周复盘对账时收敛(如实标注)。**净口径的「大赢单遮蔽」缺口由「连续 3 笔止损」触发独立兜住**(后者只认止损链、不看盈亏抵消)。
- **A2.4 状态落库 = 熔断事件表(触发 + 解锁都落库)**:新表 `circuit_breaker`(幂等建表):`id` PK、`triggered_at`、`trigger_reason`(`consecutive_stops` / `daily_loss`)、`trigger_ref_date`(触发所在交易日)、`basis_json`(判据留痕:参与判定的持仓 id 清单 + 当日净盈亏 or 连续止损笔数 + 「基于台账 N 笔已补录成交」的 N 与时窗)、`unlocked_at`(NULL=仍锁定)、`unlocked_via`(`review_ack` / `weekly_review`)、`created_at`。**当前是否锁定 = 派生**:存在 `unlocked_at IS NULL` 的行即锁定(照 CLAUDE.md「审计时间戳 + 独立消费标记不用一个字段身兼两职」教训——锁 / 解锁各自落列)。**已锁定时重复触发幂等**(不新开第二行)。
- **A2.5 触发时机 + 强提醒**:评估折进 `close_position` 服务路径(录入卖出后立即 `circuit.evaluate_after_close(sell_date, db_path=...)`)——越过任一阈值且当前未锁定 → 建触发行 + `notify.push_circuit_breaker(...)`。**次日强提醒**:折进 9:25:30 盘前校准 tick(`_sentinel_loop` 的 `_is_preopen` 分支,复用 `run_precall_tick` 的「当日只跑一次 / `sentinel_events` 防重」姿势)——仍锁定则汇总里带一句「熔断中:今日只减不加」。**熔断绝不代下单**:客户端「开新仓」入口只做**提示 / 灰化**(§3.8;服务端永不拦 `POST /positions`,只在状态里回锁定态供客户端自律)。
- **A2.6 第五类 APNs 推送(打破 v1.2 原「不新增推送类」,用户已批准)**:`push/apns.py` 加 `CATEGORY_CIRCUIT = "CIRCUIT"`;`api/notify.py` 加 `push_circuit_breaker(...)`(受 `app_settings.push_circuit` 开关,默认开,与退潮同级);`app_settings` 幂等加 `push_circuit INTEGER NOT NULL DEFAULT 1`;`PushSettingsOut`/`SettingsPushIn` 加 `circuit`;`settings_store.set_push` 扩签名同步写列。**`tests/test_notify.py` 的 `__all__` 白名单结构守护从四入口扩到五入口**(`{NotifyOutcome, push_report_ready, push_retreat_brake, push_precall_summary, push_d5_exit, push_circuit_breaker}`,守住「只这五类」)。§2.4 白名单四类→五类(planner 已改)。
- **A2.7 解锁路径(写死,复用强制复盘,不另造)**:
  - **主路径:客户端「熔断复盘」按钮 → `POST /circuit/unlock`**。按钮**先展示强制复盘材料**(复用 `review/material.py` 风格的**确定性材料**,针对触发回合的亏损成交生成,**非新 LLM 流**),用户读后确认 → 服务端置 `unlocked_at` + `unlocked_via="review_ack"` + 落库。诚实:系统不能验证用户「真的复盘了」,但强制把材料摆到面前 + 记录 ack(归因用)。
  - **自动路径:周复盘提交自动解锁**。`POST /review/upload` 产出的周若覆盖某触发行的 `trigger_ref_date` 且该周走了强制复盘口径(复用 `reconcile.py::is_forced_review` / `run_weekly_review` 同源,不另造),则该触发行自动置 `unlocked_at` + `unlocked_via="weekly_review"`。
  - 端点:`GET /circuit` → `CircuitStateOut`(权威锁定态);`POST /circuit/unlock` → `OkOut`。**「大脑激活绝不暴露给客户端」的约束此处不适用**——熔断是提醒层的用户操作,解锁本就是用户动作,可走 API。
- **A2.8 状态呈现(诚实边界落点)**:`GET /circuit` → `{locked, episode?:{triggerReason, triggeredAt, triggerRefDate, basisTradesCount, basisWindow, note}}`;`PositionsOut` 内嵌 `circuit: CircuitStateOut`(今日计划处置最相关);报告卡 / 看板注明「基于台账 N 笔已补录成交」(A2.4 的 `basis_json` 透出)——**判定所依据的数据与时效显式呈现**(§2.1 第 7 条诚实边界)。

**A2 验收**:①单测——`close_reason` 五枚举码往返 + NULL 兜底价格判止损(显式非 NULL 码不被价格二次猜);连续 3 笔止损计数(尾部连续、遇非止损断链归零、跨日 `(sell_date,id)` 排序);单日净亏 ≤ −4000 触发(大赢单抵消不触发、但连续止损链仍独立触发);已锁定重复触发幂等不开第二行;`circuit_breaker` 触发 / 解锁双落库、锁定态派生正确;两条解锁路径各置对 `unlocked_via`。②`notify.py` 白名单五入口结构守护(`__all__` 五 push_*);`push_circuit` 开关关时跳过、`SettingsPushIn` 缺字段 422。③迁移:`positions.close_reason` + `app_settings.push_circuit` + `circuit_breaker` 表,生产 DB 副本 integrity ok + 重跑不炸。④阈值单一源:全仓库 grep 无第二处 `3` / `4000` 熔断字面(除常量定义与测试);`stop_pct` 读现役 config。⑤诚实边界:状态回包含 `basisTradesCount`。⑥pytest 零回归。**🔴 碰纪律章程 + 金额判定,建议完工后叫一次 review(用户定)。**

---

#### v1.2-B · 预注册决策日志后端(八项,@builder)

> **六项 → 八项(2026-07-25 需求2 扩容)**:①-⑥ 不变,新增 ⑦ 应对方案·情景树预注册、⑧ 打法标签。

- **B.1 新表 `decision_log`(幂等建表,八项预注册)**:
  - 字段:`id`(PK AUTOINCREMENT)、`ts_code`、`name`、**`created_at`(服务端生成的预注册时间戳,客户端不许传)**、**八项预注册字段**〔`why_buy`(①为什么买)/ `why_entry_price`(②为什么这个入场价)/ `target_price`(③目标价,REAL)/ `exit_low`+`exit_high`(④离场价格区间,REAL)/ `thesis_tags`(⑤论点标签,枚举码 JSON 数组 TEXT)/ `invalidation`(⑥证伪条件,TEXT)/ **`contingency_scenarios`(⑦应对方案·情景树,JSON 数组 TEXT,默认 '[]')** / **`playbook_tag`(⑧打法标签,枚举码 TEXT)**〕、`planned_price`(REAL)、`planned_qty`(INTEGER)、`status`(pending / filled / cancelled / expired,默认 pending)、`position_id`(成交后回填,关联 `positions.id`)、`revision_of`(修订链根 id,NULL=首版)、`updated_at`。
  - **⑦ 应对方案(情景树预注册)形状写死**:`contingency_scenarios` = JSON 数组,每项 `{scenario, trigger, action, matched}`——`scenario`(情景描述,文本)/ `trigger`(触发条件,文本)/ `action`(有限枚举码:`BUY`买入 / `HOLD`持有 / `REDUCE`减仓 / `ABANDON`放弃)为**预注册内容**;`matched`(bool,默认 false)是**事后结果标记**。推演 2-3 种次日走势情景(规格来源 `/Users/linotsai/Lino/whynotme/总结.md §六 第⑦项)。
  - **⑧ 打法标签枚举**(服务端码 + 客户端展示层换算,沿 `boardLabel` 先例):`SWING_CHASE`→短线追击 / `BREATHING_TRIAL`→呼吸底仓试验(**单选**;对应三仓 = 2 追击 + 1 呼吸;**归因必须按打法分开统计**,见 v1.2.1-D)。服务端 `Literal` 白名单校验(非法码 422)。
  - **不可编辑口径(八项内容,防结果污染,与研究铁律「预注册先行」同原理)**:①-⑥ 全部 + ⑦ 的 `scenario`/`trigger`/`action` + ⑧ **无任何 UPDATE 路径**;改动 = 新增修订行(`revision_of` 指首版),归因永远只认首版(v1.2.1-D 匹配取 `revision_of IS NULL` 根行)。**唯一例外 = ⑦ 情景树的 `matched` 标记**(结果标记、非预注册内容)——经**专用端点**只翻 `matched`、绝不碰情景文本(见 B.2)。另可变的有 `status`/`position_id`(审计结果关联,非八项之一)。
  - **⑤ 论点标签枚举(不变,五码)**:`THEME` / `SENTIMENT_CYCLE` / `CAPITAL_FLOW` / `TECH_PATTERN` / `NEWS` → 题材主线 / 情绪周期位 / 资金流向 / 技术形态 / 消息(未识别透传)。服务端 `Literal` 白名单校验(非法码 422)。

- **B.2 端点(契约写死,供 v1.2-E 对照;鉴权沿 `require_token`,前缀 `/api/v1`)**:
  - `POST /decisions`(**预注册**)body 八项 + `plannedPrice?`/`plannedQty?`(见客户端契约清单)→ 服务端 stamp `created_at`(**忽略客户端任何 createdAt 入参**)+ `status="pending"` → `DecisionOut`。
  - `GET /decisions?status=&code=&from=&to=` → `{items:[DecisionOut]}`(客户端历史 + macOS 归因表;默认返全部,可按 status / code / 日期过滤)。
  - `POST /decisions/{id}/link` body `{positionId}` → `status="filled"` + `position_id` 回填 → `OkOut`(成交后一键关联;id 不存在 404 `reason="not_found"`)。
  - `POST /decisions/{id}/cancel` → `status="cancelled"` → `OkOut`(用户放弃该计划;不存在 404)。
  - `POST /decisions/{id}/revise` body `{同八项 + plannedPrice? plannedQty?}` → 新增一行 `revision_of=<该 id 的链根>`、`status` 置 pending → `DecisionOut{新 id}`(改动只新增修订行,不改旧行)。
  - **`POST /decisions/{id}/scenario-outcome`(⑦ 结果标记专用)** body `{outcomes:[{index, matched}]}`(index 对齐情景数组下标)→ **只翻对应项 `matched`、绝不改 scenario/trigger/action** → `OkOut`(id 不存在 404;index 越界 422)。这是「情景树内容不可编辑、只允许勾选兑现哪条」的**唯一**落点。
  - **无「改八项内容」端点**(不可编辑硬约束的落点)。

- **B.3 store 层(`neckline/api/stores.py` 扩,或新 `neckline/decision_log.py`)**:`create_decision`(stamp created_at)/ `list_decisions`(过滤)/ `link_decision` / `cancel_decision` / `revise_decision` / `set_scenario_outcomes`(只更 matched)/ `get_decision`。写入只经这些函数,与 `sentinel/positions.py` 台账同款薄封装姿势;**审计件非下单件**——本层绝无任何下单 / 拉价副作用。

**B 验收**:①单测——`created_at` 服务端生成(客户端传 createdAt 被忽略);①-⑥ + ⑦ 情景文本 + ⑧ 无 UPDATE 路径(revise 新增行、首版行原地不变、`revision_of` 指链根);**`scenario-outcome` 只翻 `matched`、不动情景文本**(断言 scenario/trigger/action 逐字不变);`thesisTags` / `playbookTag` / 情景树 `action` 非法码 422、合法码往返;情景树 2-3 项 JSON 往返;link 置 filled + position_id、cancel 置 cancelled、id 不存在 404;list 过滤。②契约形状与「v1.2 客户端契约清单」一致。③pytest 零回归。

---

#### v1.2-G · 呼吸试验仓台账(@builder)

**背景(需求 4 转正式排期;memo:「随本轮升级一并施工」「试验仓在升级完成后才开首笔」)**:三仓 = 2 仓短线追击 + 1 仓呼吸底仓试验(板块中军底仓 1-2 周 + 择机日内 T)。本块只做**台账 + 客户端录入 / 展示**;归因(30-50 笔后按打法裁决胜者扩仓)属 v1.2.1-D。

- **G.1 底仓与 T 仓分离记账 = 子表(非扩列),写死理由**:底仓是普通 `positions` 行(一次开仓一行);日内 T 是「同一底仓持有期内的多次买卖回合」——**一个底仓 → N 次 T 是一对多关系**,`positions` 扩列表达不了 N 笔(要么 N 列、要么序列化,都错)。故新表 **`breathing_t_trades`**(幂等建表)挂在底仓下:`id` PK、`position_id`(FK `positions.id`,底仓)、`buy_price`/`sell_price`/`qty`、`fees`(**该次 T 的实际费用**)、`t_date`、`note`、`created_at`。每行 = 一次已闭合的 T 回合(先买后卖或先卖后买,T 盈亏 `=(sell−buy)×qty−fees` 同式,方向仅备注)。**打法标签单一源 = `decision_log.playbook_tag`**(见 v1.2-B ⑧),`positions` / `breathing_t_trades` 不复制打法字段;「哪个底仓是呼吸仓」由「它下面有 T 子账」表征(只有呼吸打法才录 T)。
- **G.2 T 次数与费用逐笔落账**:每次 T 一行(G.1 表);**费用如实计量、不硬编费率**——2 万规模双边佣金 + 印花税 ≈ 20 元 ≈ 0.1% 是**背景参考、非事实**,实际 `fees` 由用户录入(客户端可给默认值但允许改),供 v1.2.1-D 归因判断 T 是否正贡献。
- **G.3 「先手」成本优势跟踪 = 派生(不存列)**:当前底仓有效成本(原始买入成本按已闭合 T 的净盈亏摊薄)vs 现价的距离,读时算(`breathing_t_trades` 汇总 + 底仓 `buy_price` + 哨兵最近一拍 / EOD 现价),不落冗余列(防漂移)。
- **G.4 端点(契约写死,供 v1.2-E;鉴权沿 `require_token`)**:`GET /breathing/{position_id}/trades` → `{items:[BreathingTradeOut], baseCostAdj, edgeToPrice}`(底仓摊薄成本 + 先手距离派生);`POST /breathing/{position_id}/trades` body `{buyPrice, sellPrice, qty, fees, tDate?, note?}` → `BreathingTradeOut`;`DELETE /breathing/trades/{id}` → `OkOut`(误录可删,不存在 404 `reason="not_found"`)。**写入只经这些端点,无任何系统自动写路径**(同 `positions` / `watchlist` 姿势,单测断言)。

**G 验收**:①单测——T 子账 CRUD + `position_id` 外键关联;底仓摊薄成本 / 先手距离派生正确(含 T 盈亏两种方向);费用逐笔如实入账、不硬编费率(传入 `fees` 原样落库);`DELETE` 幂等 + 404;②契约与「v1.2 客户端契约清单」一致;③迁移 `breathing_t_trades` 表 integrity ok + 重跑不炸;④pytest 零回归。

---

#### v1.2-E · 客户端双端:决策日志八项录入 + 熔断状态 + 呼吸台账(@builder)

- **E.1 决策日志八项录入(嵌「已按计划买入」流程之前:建计划 → 录八项 → 成交后一键关联)**:今日计划 / 候选卡「已按计划买入」补录流程**之前**插入决策日志录入:
  - 表单 = ①②文本 / ③④数字 / ⑤**论点标签 picker**〔题材主线 / 情绪周期位 / 资金流向 / 技术形态 / 消息,多选,码换算〕/ ⑥证伪文本 / **⑦ 应对方案:2-3 行情景,每行 {情景描述 + 触发条件 + 动作 picker(买入 / 持有 / 减仓 / 放弃)}** / **⑧ 打法标签 picker(短线追击 / 呼吸底仓试验,单选)** + 计划价 / 计划量 → `POST /decisions`(预注册,status=pending)。
  - 成交后:补录开仓 sheet(既有 `POST /positions`)提交后,**一键关联** `POST /decisions/{id}/link{positionId}`(或建仓时带 `decisionId` 自动关联,builder 择一)。放弃 → `POST /decisions/{id}/cancel`。**次日复盘勾选情景兑现** → `POST /decisions/{id}/scenario-outcome`(**只勾 `matched`,情景文本 UI 上只读、不可改**)。
  - **不新增第六个 tab**(iOS 仍 5 tab:今日计划 / 盘中看板 / 自选 / 问询台 / 设置)——录入走 sheet / 流程。
- **E.2 清仓补 `close_reason`(熔断数据缺口的客户端落点)**:清仓 sheet(既有 `POST /positions/{id}/close`)加**离场原因 picker**(止损 / 回落止盈 / 时间退出 / 证伪离场 / 主动;码→中文展示层换算,沿 `boardLabel`)→ `PositionCloseIn.closeReason`。不选可留空(服务端 NULL + 价格兜底)。
- **E.3 熔断状态标记 + 第五类推送开关**:今日计划持仓区读 `GET /circuit`(或 `PositionsOut.circuit`)——锁定时置顶醒目横幅「熔断中:今日只减不加(基于台账 N 笔已补录成交)」+「开新仓」入口灰化 / 提示(**客户端自律、服务端不拦**);「熔断复盘」按钮展示强制复盘材料后 `POST /circuit/unlock`。设置屏推送区第 **5** 个 toggle `circuit`(默认开);`PushManager` 加 `CIRCUIT` category 路由(→ 今日计划);`SentinelKind` 若展示则补 `.circuit`(色调分支跟进)。
- **E.4 呼吸台账界面(v1.2-G 的客户端)**:呼吸打法底仓(有 T 子账的 `positions`)展示 T 逐笔列表(买 / 卖价、数量、费用、T 盈亏)+ 底仓摊薄成本 + 先手距离(`GET /breathing/{id}/trades`);录入一次 T(`POST`)/ 删除误录(`DELETE`)。放今日计划持仓卡展开区或 macOS 工作台(builder 择一,桌面场景更适合大表)。
- **E.5 entry-suggestion 区间建议**:`/positions/entry-suggestion` 返**区间**(`EntrySuggestionOut` 新形状:`qtyLow`/`qtyHigh` 两档手数 + `capFloor`/`capCeil` 两端金额 + `stopLine`)。客户端展示两档 + 标注「4 万 = 违纪上限、非推荐值」,**不替用户拍板单笔金额**。v1.1-E 读单 `qty` 预填的逻辑改读区间。
- **E.6 客户端 Models / APIClient**:加 `DecisionLog` DTO(含 `contingencyScenarios`/`playbookTag`)+ `playbookTag`/`action`/`closeReason` 枚举展示映射(码→中文,未识别透传,沿 `boardLabel`)+ `CircuitState` DTO + `BreathingTrade` DTO;方法 `createDecision`/`listDecisions`/`linkDecision`/`cancelDecision`/`reviseDecision`/`setScenarioOutcome`/`getCircuit`/`unlockCircuit`/`breathingTrades`/`entrySuggestion`(区间)。decisions / circuit / breathing 404(`reason="not_found"`)复用 `APIError.mapReason` 已有 `.notFound` case(v1.1-E/F 已加,见 CLAUDE.md),只需核对映射到位。
- **归因大表(论点 × 打法双维)属 v1.2.1-D**:macOS 归因工作台的双维胜率表 + 「无决策日志开仓」清单 + 挂单追踪历史随 **v1.2.1-D/C** 落客户端,**本块(v1.2 E)不做**(后端归因未上线前不建空表)。

**E 验收**:双端 `xcodebuild BUILD SUCCEEDED` + iOS Simulator TEST;八项录入(含情景树 2-3 行 + 动作 picker + 打法 picker)真实 `POST /decisions`、link/cancel/scenario-outcome 往返;清仓 close_reason picker 往返;熔断锁定横幅 + 「开新仓」灰化 + 解锁按钮真实往返(隔离库造触发态截图);第五推送开关真机 PUT 回读一致;呼吸 T 逐笔录入 + 先手距离展示;entry-suggestion 区间双档(标注违纪上限)。单测覆盖 `DecisionLog`(含情景树)/`CircuitState`/`BreathingTrade` 解码、`playbookTag`/`action`/`closeReason` 码→中文映射、entry-suggestion 区间解码。绿涨红跌不变。**「不新增 tab」「八项内容录入后只读、只勾情景兑现」「熔断服务端不拦、仅客户端自律」三条硬边界各验一次。**

---

#### v1.2-F · 部署上云 + schema 迁移 + 活体验收(🔴 @builder-pro + 用户)

- **F.1 部署(用修好的 sync_code.sh 首验)**:`sync_code.sh`(**v1.2-0 已修 footgun**,首次用新版部署,验证收尾 prune + 只读属主自检)推后端;**schema 迁移前 `sqlite3 .backup` 备份**(照 v1.1-H 姿势:`data/neckline.db.bak-<date>`,integrity ok、业务表行数逐表吻合、原地保留 rsync 排除)。
- **F.2 幂等迁移随 `lifespan.init_schema` 重启执行(v1.2 首波)**:新建 `decision_log`(八项)+ `circuit_breaker` + `breathing_t_trades` 表;`positions` 加 `close_reason`;`app_settings` 加 `push_circuit`。(`strategy_versions.activated_at` + `reviews.strategy_version` 已随 v1.2-A 步骤1 就位,本次仍校验存在。)迁移后 integrity ok + 业务数据零丢失(positions / devices / reports 行数不变)。**`decision_pending_track` 属 v1.2.1-C,不在本波。**
- **F.3 哨兵 / 报告 timer 影响核查**:v1.2-A2 熔断评估折进 `close_position`(同步 API 路径,`notify.push_circuit_breaker` 尽力而为、失败吞掉不阻断)+ 9:25:30 盘前 tick 加「熔断中」提醒(复用 `run_precall_tick` 当日只跑一次 / `sentinel_events` 防重,**intraday 判逻辑仍一字不改**)——核查:哨兵 lifespan asyncio 任务重启后 idle RSS 与部署前持平、盘前分支非交易时段不误触发、熔断一拍异常被吞不掀翻主循环(单测已断言)。报告 timer(`neckline-report.service` oneshot)本波未加新步骤(track 属 v1.2.1),仍瞬态释放。
- **F.4 章程激活 = staged 步骤 2(等用户,见 v1.2-A.5)**:部署后 `is_active` 仍 K1、生产行为零变化;用户清仓 + 确认后在 ECS 权威库跑切换器 `--confirm` 才切 v1.2。
- **F.5 运维留痕**:ECS 动作后更新 `~/Lino/hz_info.md`(新表 / 列、footgun 已修销 §191、章程 staged 激活现状);变更日志记一行。

**F 验收(活体,逐项写死)**:
1. **`sync_code.sh` 新版首部署**:收尾 prune 生效、部署后 `data/neckline.db` 属主仍 `neckline:neckline`、只读属主自检绿、服务 restart 无 502。
2. **迁移**:三新表(`decision_log`/`circuit_breaker`/`breathing_t_trades`)建成 + `positions.close_reason` + `app_settings.push_circuit` + (已就位的 `strategy_versions.activated_at` K1 回填有值 / `reviews.strategy_version`)在生产库 integrity ok、业务零丢失。
3. **公网真 token 验收**:`POST /decisions` 八项预注册往返(createdAt 服务端生成)、`GET /decisions` 读回、link / cancel / scenario-outcome、`GET /circuit` + `POST /circuit/unlock`、`POST /positions/{id}/close` 带 `closeReason`、`GET/POST/DELETE /breathing/...`、`/positions/entry-suggestion` 返区间双档、四→五推送开关回读;老端点(report / positions / watchlist / settings)前向兼容不崩、无 token 401。
4. **熔断链路首验**:造连续 3 笔止损 / 单日净亏 ≤ −4000 → `circuit_breaker` 落触发行 + 锁定态 + 第五类 APNs(开关开时);解锁往返置 `unlocked_at`;哨兵 idle RSS 持平、盘前「熔断中」提醒不误触发。
5. **章程 staged 激活实证(用户)**:清仓 + 确认后跑切换器 → `is_active=v1.2`、entry-suggestion 改 4 万区间、下一份周复盘用 v1.2 判激活后周 / K1 判之前周(**洗白修复实证**)。
6. **⚠ 碰纪律章程 + 金额区间,建议完工后叫一次 `review`(用户定)。**

---

#### v1.2 客户端契约清单(新端点 / 新字段,供 v1.2-E 对照)

- **决策日志八项(v1.2-B;鉴权沿 `require_token`,契约见 `neckline/api/schemas.py`)**:
  - `POST /decisions` body `DecisionCreateIn{code, name?, whyBuy, whyEntryPrice, targetPrice?, exitLow?, exitHigh?, thesisTags:[码], invalidation, contingencyScenarios:[{scenario, trigger, action:码, matched?}], playbookTag:码, plannedPrice?, plannedQty?}`(**无 createdAt,服务端生成**)→ `DecisionOut`。
  - `GET /decisions?status=&code=&from=&to=` → `{items:[DecisionOut]}`。
  - `POST /decisions/{id}/link` body `{positionId}` → `OkOut`(不存在 404 `reason="not_found"`)。
  - `POST /decisions/{id}/cancel` → `OkOut`(不存在 404)。
  - `POST /decisions/{id}/revise` body `DecisionReviseIn{同八项 + plannedPrice? plannedQty?}` → `DecisionOut{新 id, revisionOf}`。
  - `POST /decisions/{id}/scenario-outcome` body `{outcomes:[{index, matched}]}` → `OkOut`(**只翻情景 `matched`,不动情景文本**;不存在 404 / index 越界 422)。
  - `DecisionOut` 字段:`id, code, name, createdAt, whyBuy, whyEntryPrice, targetPrice, exitLow, exitHigh, thesisTags:[码], invalidation, contingencyScenarios:[{scenario,trigger,action:码,matched}], playbookTag:码, plannedPrice, plannedQty, status(pending/filled/cancelled/expired), positionId|null, revisionOf|null`。**枚举码客户端展示层换算(沿 `boardLabel` 先例,未识别透传)**:`thesisTags`(`THEME`→题材主线 / `SENTIMENT_CYCLE`→情绪周期位 / `CAPITAL_FLOW`→资金流向 / `TECH_PATTERN`→技术形态 / `NEWS`→消息);`playbookTag`(`SWING_CHASE`→短线追击 / `BREATHING_TRIAL`→呼吸底仓试验);情景 `action`(`BUY`→买入 / `HOLD`→持有 / `REDUCE`→减仓 / `ABANDON`→放弃)。
- **熔断状态(v1.2-A2)**:`GET /circuit` → `CircuitStateOut{locked:bool, episode?:{triggerReason(consecutive_stops/daily_loss), triggeredAt, triggerRefDate, basisTradesCount, basisWindow, note}}`;`POST /circuit/unlock` → `OkOut`。`PositionsOut` 内嵌 `circuit: CircuitStateOut`(今日计划面处置)。`GET/PUT /settings/push` 契约扩第五字段 `circuit`(默认开)。
- **离场原因(v1.2-A2)**:`PositionCloseIn` 加可选 `closeReason:码`(`STOP_LOSS`→止损 / `TAKE_PROFIT`→回落止盈 / `TIME_EXIT`→时间退出 / `INVALIDATION`→证伪离场 / `MANUAL`→主动离场;不传 → NULL,服务端价格兜底判止损)。客户端展示层码换算,沿 `boardLabel`。
- **呼吸台账(v1.2-G)**:`GET /breathing/{position_id}/trades` → `{items:[BreathingTradeOut{id, positionId, buyPrice, sellPrice, qty, fees, tDate, tPnl, note}], baseCostAdj, edgeToPrice}`;`POST /breathing/{position_id}/trades` body `{buyPrice, sellPrice, qty, fees, tDate?, note?}` → `BreathingTradeOut`;`DELETE /breathing/trades/{id}` → `OkOut`(不存在 404)。
- **`EntrySuggestionOut` 改区间(替换 v1.1 的单 `qty`)**:`{ok, code, price, qtyLow, qtyHigh, capFloor, capCeil, stopLine}`——`qtyHigh = floor(single_cap/price/100)*100`(违纪上限对应手数)、`qtyLow = floor(single_cap*0.5/price/100)*100`(半仓保守下沿;**0.5 是纯展示层因子,住 `app.py` 一处、非领域常量,`single_cap` 仍是唯一领域源**)、`capCeil = single_cap`(违纪上限金额)、`capFloor = single_cap*0.5`、`stopLine = price×(1−stop_pct)`(读现役 config)。**客户端展示两档 + 标注「上限 = 违纪线、非推荐」,不替用户拍单笔金额。** v1.1-E 客户端读单 `qty` 预填的逻辑需改读区间。
- **周复盘 `weekly_review_dict()` 新增字段(属 v1.2.1-D,后发)**:`thesisAttribution`(论点标签码)、`playbookAttribution`(打法标签码)、`thesisByPlaybook`(论点 × 打法交叉)、`breathingTContribution`(呼吸打法 T 净贡献)、`noDecisionLogTrades`(无决策日志开仓清单)。macOS 归因区随 v1.2.1-D 展示;挂单追踪历史(v1.2.1-C)如需端点,builder 定 `GET /decisions/{id}/track` 或并入 `GET /decisions`,届时补进本清单。

---

### v1.2.1(后发:挂单追踪 + 决策归因 + 问询台清理;三块都要攒够样本才有用,不挡 v1.2 上线)

> **为何后发**:C 追踪要 decision_log 先积累 pending、D 归因要决策日志 + 打法样本(30-50 笔量级)、D2 是独立小清理——**都不阻塞 v1.2 首笔开工**,故与 v1.2 并行、晚一步施工。三块施工图全文保留(原 v1.2-C/D/D2 移入本小节;D 扩为「论点 × 打法」双维 + 呼吸 T 贡献)。

#### v1.2.1-C · 挂单未成交追踪(@builder,后发)

**目的**:用户习惯挂低价等回踩,记录**未成交计划**(`decision_log.status='pending'`)的后续 N 日走势,检验用户「逆向选择:专接下坠、错过起飞」假设(飞了 = 错过 / 跌了 = 躲过)。

- **C.1 追踪窗口 N 写死**:`N = 5 个交易日`(常量 `DECISION_PENDING_TRACK_DAYS = 5`,单一源;与 `hold=5` / D5 时间退出 horizon 同口径,覆盖短线 1–2 日打法的相关观察窗)。
- **C.2 新表 `decision_pending_track`(幂等建表)**:`decision_id`、`trade_date`(追踪快照日)、`d_offset`(距 `created_at` 后第几个交易日 1..N)、`close`(当日 EOD 收盘,前复权口径同面板)、`ret_from_plan`(相对 `planned_price` 的累计收益)、`recorded_at`,PK`(decision_id, trade_date)`(同日重跑幂等覆盖)。
- **C.3 折进 16:35 报告管线**:`report/pipeline.py::build_report` 末尾(`if save:` 块内、报告落库后)新增一步 `track_pending_decisions(trade_date, db_path=...)`——对每只 `status='pending'` 且 `created_at` 在 N 交易日内的决策:从**当日 EOD 面板复用既有日线数据访问层**(`build_research_panel(trade_date, trade_date)` / `data/market_data` 读,**不新拉数据源**)取 `close`,算 `ret_from_plan`,落 `decision_pending_track` 一行;到第 N 交易日:`status` pending→**expired**(未成交自动过期,追踪定格)。**落盘若涉及 Parquet 一律走 `write_table_day`;本追踪落 SQLite,不写 Parquet。** 复用 16:35 报告 timer 的独立瞬态进程(跑完释放内存,对紧内存友好)。

**C 验收**:①单测——pending 决策登记后逐交易日落 track 行、`d_offset` 从 1 递增、`ret_from_plan` 相对 `planned_price` 正确、第 N 日置 expired、已 filled / cancelled 的决策不追踪、同日重跑幂等覆盖;②端到端(隔离库):造一只 pending 决策 → 连跑 N 个交易日 `report.py` → track 表 N 行 + 决策 expired;③零新拉数据源(复用面板)、`write_table_day` 未被绕开。

---

#### v1.2.1-D · 决策归因入 4D 周复盘(论点 × 打法双维 + 呼吸 T 贡献,@builder,后发)

**核心工程难点(写清)**:4D 现链路是**券商交割单 → `RawTrade` → FIFO `RoundTrip`**(`review/reconcile.py`),决策日志挂在 **`positions` 台账**上(via `decision_log.position_id`)——**两本账要按 `(ts_code, buy_date)` 邻近匹配接回**:`decision_log`→`position_id`→`positions{ts_code, buy_date}`,再与 `RoundTrip{ts_code, buy_date}` 对齐(精确 ts_code + buy_date,±1 交易日容差兜边界)。

- **D.1 归因聚合(论点 × 打法双维)**:`run_weekly_review` 产出的每个 `WeeklyReview` 新增归因节——对本周 `closed_round_trips`,匹配决策日志**首版**(`revision_of IS NULL` 的根行)的 `thesis_tags` + `playbook_tag`,分别按**论点标签**、**打法标签**、**论点 × 打法交叉**三个维度聚合胜率 / 盈亏比(复用 `compute_weekly_stats` 的 `win_rate` / `profit_loss_ratio` 口径,分组算)。一笔可挂多论点标签 → 各论点分别计入;打法标签单选。**打法维度是本块新增的核心**(2026-07-25 双打法 A/B 定向:30-50 笔后按打法裁决胜者扩仓,归因必须按打法分开)。
- **D.2 呼吸打法 T 贡献**:对打法 = `BREATHING_TRIAL` 的底仓,读 `breathing_t_trades`(v1.2-G),汇总每底仓的 T 净贡献(`Σ T盈亏`)与底仓本身收益对比,判「做 T 是否正贡献」(用户对做 T 的保留由此数据裁决,不辩论——STRATEGY_LAB 冲突裁决1)。落 `breathingTContribution` 字段。
- **D.3 「无决策日志的开仓」= 纪律项统计(软约束落点,不静默丢)**:匹配不上任何决策日志的本周开仓(closed round trip 或 buy)→ **显式报「无决策日志的开仓 N 笔」**并列具体票,当**纪律项**进 `discipline_violations`(或独立 `no_decision_log_count` 字段)。**§五 intro 决策日志软约束就落在这里**——不硬阻断补录,但周复盘把无日志开仓统计出来。
- **D.4 落库 + 契约**:归因结果 + 无日志计数进 `weekly_review_dict()`(API 响应 = `reviews.result_json` 同一形状),新增 `thesisAttribution` / `playbookAttribution` / `thesisByPlaybook` / `breathingTContribution` / `noDecisionLogTrades` 字段。macOS 周复盘工作台展示双维胜率表 + 无日志开仓 + T 贡献(客户端归因大表随本块落地,v1.2-E 不做)。

**D 验收**:①单测——决策日志与 RoundTrip 按 (ts_code, buy_date) 匹配(精确 + ±1 日容差);论点 / 打法 / 交叉三维分别计入胜率 / 盈亏比;呼吸 T 净贡献汇总正确;无匹配决策的开仓进「无决策日志开仓」纪律项、**不静默丢**;首版认定(有修订行时归因取 `revision_of IS NULL` 根行的标签,不认修订标签)。②契约形状与「v1.2 客户端契约清单」一致。③pytest 零回归。

---

#### v1.2.1-D2 · 问询台选股域漂移清理(独立小清理,@builder,后发)

**根因(4A 遗留,v1.1-C/D 施工时发现,原只记 §四正文、未进 Backlog)**:`api/inquiry.py::run_deterministic_checks` **手写重复了选股域逻辑**(ST / 北交所 / 股价<2 元 / 20 日均额<2000 万 / 无 MA20 逐条 Python 重刻,见该函数「硬性纪律核对」段),会与报告口径漂移(且当初未核对两条禁买过滤 P4/P5)。

**修法(照 `report/watchlist_check.py::_discipline_checks` 的正确姿势,见 CLAUDE.md v1.1-C/D 节)**:
- **选股域四项揉成一条组合原因文案**(用 `~research.panel.base_universe_expr()` 整条求值,**不拆解、不重抄阈值**)——`base_universe_expr()` 内部已 AND 成单一布尔,拆开逐项手写会各自维护一份阈值、一改上游即漂移。
- **只有现役 config 可配的禁买过滤(P4 绿盘大阴线 / P5 距前高 / P6 次新 / 高弹题材)才逐项拆开**——它们本就按 `cfg.forbid_* is not None` / `cfg.forbid_*` 分支决定是否启用,拆开展示不产生新的数值维护点,与 `momentum.build_entry_mask` 的 if 分支一一对应。
- **落地** = 复用 `watchlist_check._discipline_checks(cfg)`(promote 成公开函数、或抽到共享模块,builder 定 host;两处 `run_deterministic_checks` 与 `watchlist_check` 共用同一份)。在问询单票的单行面板上求值这批谓词,`disqualifiers = [label for 命中项]`、`passes_discipline = not disqualifiers`,替换现手写段。
- **展示粒度取舍如实标注**:选股域从「逐项(ST / 北交所 / 价格 / 流动性 / MA20)」收敛为「一条组合原因」——刻意的、与 `watchlist_check` 一致的取舍(损一点粒度换零阈值漂移)。

**D2 验收**:①单测——问询台确定性核对与报告 `watchlist_check` 对同一票、同一日**同判**(同码一致,注入同一面板断言 disqualifiers 一致);选股域触发时收敛为一条原因、config 启用的禁买项仍逐项拆开;K1 现役(P4/P5=None)下与旧行为对同一批票裁决等价(仅原因文案粒度变);②pytest 零回归。

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
- **[后期决策·K3-B2 副产品] 反向证伪哨兵新条件(降势票诱多做局)**:K3-B2 臂④诊断实测——**年线下(`close<ma250`)的降势票突现涨停 → 事后 3 日 −2.06%(2026 −3.43%、胜率 0.29)、突现放量大阳(ret1d≥5%×量比≥2) → −1.04%**,逐年一致为负(印证用户"诱多做局"直觉)。**可反向用作证伪哨兵新条件**:持仓/自选票若在年线下突现放量大阳/涨停 = 派发/诱多信号 → 提示减仓/勿追。**✅ 已并入 v1.3-② 持仓管理层实现**(2026-07-26 立项):作为「年线下涨停/放量大阳 = 派发警报」的**强警示**条件,与现役 `invalidation_spec` 四条价量结构合并评估,守"证伪只用价量结构"铁律;数字口径见雷区地图 3-⑤。数据落 `research/k3_report.md`「臂④」节。
- **[研究·已完工归档 2026-07-22] K2 策略研究(情绪门控 × 主线票池 × 短线进出)**:施工图 **§五B**,六研究件 B1–B6,产出 `research/k2_report.md` + K2 候选大脑(`strategy_versions.K2`,`is_active=0` 不激活)+ 「K2 vs K1 对比裁决书」。**中心命题否决**(「情绪进攻段 × 主线成员内追强势」无正期望,印证阶段 1 P3);采纳集为空 = K2 config 逐字段等于 K1;K1 仍唯一现役。正 alpha 仍开放 → 接 K3(超跌反向)。
- **[机制] 策略进化门禁**:按月 / 季调参须过回测 + walk-forward 样本外跑赢现役 + 用户批准;大脑按版本归因实盘表现。落地在阶段 1 之后常态运行。
- **[未来] 分钟线数据源**:当前 TuShare 600 元档无分钟线,盘中靠新浪 / 腾讯免费源。若后续需要分钟级回测,评估升档或其他源。
- **[✅ v1.2-0 已修复,待 v1.2-F 首次真部署活体收官] `scripts/sync_code.sh` 尾部 chown 收尾**:`chown -R deploy:neckline /opt/neckline` 会把 rsync 已排除的 `data/` 属主一并翻掉 → 生产 DB 只读 → 服务 502(v1.1.1 部署时老坑复发,当场手工复原)。**修法已落地(2026-07-25)**:打印的收尾 chown/chmod 两条改 `find -path .../data -prune` 版跳过 `data/`,脚本末尾新增只读属主自检(不符红字 + `exit 1`)。`DRY_RUN=1` 预演 + ECS 无害临时路径场景验证通过(未碰生产库);**真实部署场景下"收尾命令+自检"是否如预期拦下问题**,留 v1.2-F 首次真部署合并验收(§五 v1.2-0 「0 验收」③)。
- **[✅ 已进 v1.3 施工图 §五 v1.3-③「情报官」并已全部完工(C1-C4,2026-07-26)] 盘前情报包(需求 3,2026-07-25 交接·最新版清单)**:两部分——
  - **① 复盘情报件(全 EOD 可算)**:主线识别器改定位为**拥挤情报件**(K2 判决:板块层有效但无次日领先性,当情报展示、不当选股信号)+ 板块资金流展示(`moneyflow_dc` 2023-09+,落盘走 `write_table_day`)+ 复盘情报字段清单(**涨幅榜 / 跌幅榜、涨停梯队、跌停榜、大盘量能、最强题材及其核心一二名、市值偏好与涨跌幅制度偏好〔10/20/30cm 哪类受资金欢迎〕、题材持续天数〔区分 2-3 日持续题材 vs 一日游〕**)——竞价 / L2 盘口无历史数据不做。**✅ C1/C2 已完工**(`report/intel.py`/`report/sector_moneyflow.py`,2026-07-26)。
  - **② 晚间消息面公告扫描(战法总结 §5.6)**:**持仓 + 自选票公告扫描**(减持 / 立案 / 暴雷 / 监管)。数据源待查(TuShare 公告接口覆盖与时效);可先以 **LLM 联网搜索兜底**(复用 `openai_compat` 带搜索调用,**注意 `read_timeout=90` 的坑**,见项目 CLAUDE.md「v1 上线首日」)。**✅ C4 已完工**(`report/news_alerts.py`,2026-07-26)——数据源侦察结论:`anns_d` 不可用(单独付费权限未购)、股东减持改走结构化 `stk_holdertrade`(600 元档覆盖内,免 LLM);立案/暴雷/监管三类无 TuShare 结构化接口覆盖,按计划走 LLM 联网搜索兜底,详见 §四/§九 C4 完工条目。
  - **③ 避坑标注规格读 DB,不抄常量(需求 3 补充,2026-07-25 晚)**:唯一权威 = `strategy_versions` 表 **`K4` 行**(`is_active=0`)的 `rule["k4_advisory"]` 六节——`hard_cut`(硬剔)/ `avoid_flag`(回避标注)/ `exec_hint`(执行提示)/ `circuit_breaker`(熔断)/ `intel_order`(展示排序)/ `note`。实现一律读这条 DB 记录。⚠ 其中 `circuit_breaker` 节是**说明文字**(`"3 连止损 → 停手复盘解锁"` 之类),**不是机器可读阈值**——v1.2-A2 已把可执行阈值落成 `sentinel/circuit.py` 的命名常量(数字一致);**两处若要改数字必须同改**(advisory 是策略线档案,归策略线;常量归系统线)。证据口径见 `research/k4_assembly_report.md` §1/§R3。**✅ 已在 v1.3-② 持仓体检 + C3 候选管线两处完工**(C4 消息面扫描不涉及 K4 advisory,减持/立案/暴雷/监管四类判据来源见 C4 完工条目,与 K4 是两套独立机制)。
  - **v1.2 不做,推 v1.3**(不写施工图,立项时定形);规格来源战法总结 §五/§八(见 `archive/交接_系统线升级需求_20260725.md` 需求 3 + `STRATEGY_LAB §五` B3.1「复活为情报件」)。
- **[✅ 已进 v1.3 施工图 §五 v1.3-③-C3,2026-07-26 立项·与上条合并施工] 候选池生成管线改版 —— K1 选股逻辑退役(需求 5,2026-07-25 深夜用户拍板)**:**K1 的 entry mask 不再作为候选列表的生成源**(K1 / v1.2 章程行仍是**纪律参数**的现役载体,该角色不变)。新四步管线出 20 只:① **板块层** = 主线识别器拥挤度 top 板块(用户五板块常驻 + 当日暴起板块);② **个股层** = 上述板块成员、**全板块(MAIN/GEM/STAR)**,只过卫生线(非 ST / 非次新 120 / `amount_ma20` 流动性底线)+ 趋势向上,**不套 K1 的主板 only 与回调买点**;③ **K4 安检** = `hard_cut` 拦截、`avoid_flag` 打标(读上条 ③ 的 DB advisory);④ **情报排序** = 板块资金流强度 + 题材持续天数(**反用**:1 天新鲜 > 2-3 天警惕 > ≥4 天剔)+ 温和带标注。**产品语义变更**:候选列表从「系统认为会涨的票」→「**过完安检、值得用户花注意力的票**」,终选权在用户(候选卡文案要跟上)。生成域**刻意含高弹板块**(贴用户实操、与 K1 哲学相反),用户知情拍板,代价已在策略线审计中定价。**与需求 3 合并成 v1.3「情报官」一起做**(①④ 两步要的正是需求 3 的情报件,分开做等于同一批代码写两遍)。**⚠ 在 v1.3 落地前**:16:35 报告的候选 20 只仍是 K1 口径(主板 only / 剔高弹 / 回调买点),与用户实操域相反,该节参考价值有限(自选体检 / 持仓 / 哨兵 / 纪律对账不受影响)——已当面告知用户(2026-07-25)。
- **[✅ 已进 v1.3-⑤(原 v1.2.1-D2 归 v1.3)] `api/inquiry.py::run_deterministic_checks` 选股域漂移**:手写重复 `research/panel.py::base_universe_expr()` 选股域逻辑(4A 遗留,v1.1-C/D 施工时发现,原只记 §四正文未进 Backlog)→ 复用 `base_universe_expr()`(选股域揉一条不拆解,只 config 可配禁买过滤逐项拆,照 `report/watchlist_check.py::_discipline_checks` 姿势,见 CLAUDE.md v1.1-C/D)。
- **[v1.3.1 后发·双打法 A/B 裁决必需件·不许丢] 决策归因入 4D 周复盘(论点 × 打法双维 + 呼吸 T 贡献,原 v1.2.1-D)**:施工图全文在 §五 v1.2.1-D(保留不删),**归 v1.3.1 后发**——需 30-50 笔样本才有意义,不挡 v1.3 上线;但它是「短线追击 vs 呼吸试验」双打法胜负裁决的**唯一归因载体**(2026-07-25 用户定向 A/B),**不许丢**。决策日志(v1.2-B)via `position_id` 与 FIFO RoundTrip 按 `(ts_code,buy_date)` 邻近匹配、按论点 / 打法 / 交叉三维聚合胜率盈亏比 + 呼吸 T 净贡献 + 「无决策日志开仓 N 笔」纪律项。
- **[风险·跨线共享] TuShare 600 元档配额可被跑干**:策略线 2026-07-25 K5 可转债 Phase-0 拉取把配额耗尽(用户令停、杀进程,见 `STRATEGY_LAB §五`)。**系统线影响面**:生产 16:05 增量拉数(`daily_update` 六表 + `moneyflow_dc`)与研究期大批量拉取**共用同一 token 配额**——若某日研究侧跑干配额,当晚 16:05 增量可能拉不全 → 16:35 报告/情报件缺数。**v1.3-⑦-D 已列入部署核查项**(顺手确认生产增量正常);长期缓解 = 研究侧大批量拉取避开 15:00–17:00 生产增量窗口 / 或研究用独立 token(后期决策)。
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

- **2026-07-26 · v1.3-③-C4 晚间消息面公告扫描(持仓+自选:减持/立案/暴雷/监管)完工(@builder,未部署)**:需求 3 收官(C1/C2/C3 已完工,本块补齐 C4,v1.3-③ 情报官四子块全部完工)。报告新增「消息面」节,扫描对象=**持仓 ∪ 自选**(去重,非全市场)。**数据源侦察结论(真实 token 活体探活,详见 §3.2/模块 docstring,后人不必重查)**:① `anns_d`(TuShare 通用公告接口)**不可用**——真实调用返回「抱歉,您没有接口(anns_d)访问权限」,官方文档交叉核实是**独立付费权限**(公告信息单独 1000 元/年,与 §3.2 已记的「新闻资讯单独 1000 元/年未购」是**两个不同的独立付费产品**,本次补记 `anns_d` 同样未购)。② `stk_holdertrade`(股东增减持)**意外可用**——只需 2000 积分,在项目 600 元档(6000 积分)覆盖范围内、非独立权限,真实调用返回结构化数据(`in_de`=IN增持/DE减持、`holder_type`=G高管/P个人/C公司、`change_vol`/`change_ratio`/`ann_date`)。**「减持」类改用此结构化接口,不用 `anns_d`、不用 LLM**——零幻觉风险、免 LLM 调用成本,比 plan 原文举例的 `anns_d` 更优的替代方案(数据源侦察的题中之义,非擅改需求)。③ **立案 / 暴雷 / 监管三类无任何 TuShare 接口覆盖**(逐一核实:未找到「立案调查」/「监管处罚」专属接口;`disclosure_date` 是财务预约披露日期,与监管处罚无关,排除)——三类全部走 **LLM 联网搜索兜底**。**架构**:`data/tushare_client.py` 新增 `ts_stk_holdertrade`;新 `llm/news_scan.py`——**一次调用问三类**(不是三次,控成本:持仓+自选最多 33 只,一次问三类把最坏调用数从 99 压到 33),复用 `judge.py` 同一套 provider/降级链姿势(读超时原样吃 `OpenAICompatProvider.read_timeout=90.0` 基类值,不新设),结尾按「结论-类别:摘要」多行收尾(§2.7 边界,类比 `judge.py` 头注释同一先例——叙述主体仍自由文字,结尾只是轻量机器可读收尾,非固定分栏卡片);**格式缺失时既不假装「确认无消息」也不硬造类别**,标 `degraded=True` 纳入失败计数(与 `judge.py`「格式缺失保守按否决」方向相反,原因写在模块头:消息面是风险警报场景,静默漏报与硬造类别都没有依据,只能诚实标「未解析」)。新 `report/news_alerts.py`(`NewsCategory` 四枚举码 REDUCTION/INVESTIGATION/BLOWUP/REGULATORY,`_scan_reduction`+`_scan_llm_categories`+`build_news_alerts` 主入口)+ `report/news_alerts_store.py`(`news_alerts` 表读写,不存 `name`,同 `llm_judgments` 惯例)。**「没扫到」vs「扫了没有」区分(§硬要求)**:`NewsAlertScanStatus` 逐源记录 `scanned`/`reason`(+LLM 源额外记 `codes_total`/`codes_failed`,支持「部分标的失败」颗粒度),随报告落新列 `reports.news_alerts_scan_json`(同 `intel_json`/`sector_moneyflow_json` 惯例,保证历史报告回放仍能分清「当时没扫到」与「当时扫了确认没有」,不能只看 `news_alerts` 表当天有没有行——空行两种含义都成立)。命中告警落独立 `news_alerts` 表(`code`/`trade_date`/`category`/`summary`/`source`/`created_at`,`UNIQUE(ts_code,trade_date,category)` 幂等)。**已知简化(接受的 v1 边界,非疏漏)**:`trade_date` = 扫描所属**报告日**(与库内其余表 `trade_date` 惯例一致),非公告/事件本身的日期——同一公告若连续数日仍落在扫描窗口内会在数日报告里重复出现,不做跨日按事件日去重。**契约**:`ReportOut.newsAlerts:[{code,category,summary,source}]` 严格覆盖 plan「v1.3 客户端契约清单」字面四字段,**额外加 `name`**(超集,向后兼容,不破坏契约);`ReportOut.newsAlertsScan:[{source,scanned,reason,codesTotal,codesFailed}]` 是**本块新增、非字面契约清单**的透明度补充字段,为满足「没扫到 vs 扫了没有必须能区分」这条硬要求而加,已在此报告(非擅自改契约,是补一个契约清单未列出但硬要求需要的字段)。**不阻断主报告管线**(硬要求④):`pipeline.py::build_report` try/except 包一层(两个内部子扫描各自已有降级,这里兜编排逻辑自身意外);扫描对象 = 持仓(`pos_store.load_open_positions`)∪ 自选(`watchlist_store.list_watchlist`)去重,展示名优先取自选自带 name、持仓票经 `stock_basic` 解析。markdown 新增「消息面」节(先亮扫描状态、再列命中,避免读者把「未列出条目」误当「确认干净」)。全量 **pytest 1140 passed, 2 skipped**(基线 1093 + 47 新:`test_news_scan.py` 11 + `test_news_alerts.py` 14 + `test_news_alerts_store.py` 7 + `test_pipeline.py` 5 + `test_render.py` 4 + `test_report_store.py` 4 + `test_api_report_board.py` 2,`test_tushare_client.py` 补 1 处既有降级列表项,0 回归)。**LLM 单测一律 `httpx.MockTransport` 注入免联网**(`judge.py`/`news_scan.py` 同一套姿势);**TuShare 单测一律 monkeypatch `ts_stk_holdertrade` 免联网**——施工期发现一处遗漏(`TestLLMCategoriesScan` 某测试只关注 LLM 侧,未桩 TuShare 侧,导致该测试意外发起真实 TuShare 网络调用并污染 `tushare_client` 模块级 `_get_pro()` 缓存,使同进程后续依赖「无 token 优雅降级」的测试假阳性)——已修,全量 + 局部 + 正反序多次验证无残留。**未碰**生产 ECS / STRATEGY_LAB / research/* / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1)/ v1.3-①②③(已完工不重做)/④⑤⑥⑦;`data/*.bak-*`、未跟踪 `research/k5_cb.py` 未动。**⚠ 需用户拍板**:(1) 「减持」类改用 `stk_holdertrade` 结构化数据而非 plan 原文举例的 `anns_d`/LLM——超出字面但更优的方案,是否认可;(2) `trade_date`=扫描日(非事件日)的简化导致同一持续性消息可能连续数日重复出现,是否需要后续做跨日按事件日去重(v1 暂不做);(3) `newsAlertsScan` 是否要正式补进「v1.3 客户端契约清单」(目前是本块新增的透明度字段,⑥ 客户端可选择性使用);(4) LLM 侧最坏情况需循环扫描持仓+自选全部标的(≤33 只),每票一次调用(最长 90s×最多 3 次重试),与既有候选审判 / 自选体检量级相当,非本块独有新增负担,一并提醒供部署评估耗时。
- **2026-07-26 · v1.3-③-C3 候选池情报筛选管线改版(K1 选股逻辑退役)完工(@builder-pro,未部署)**:需求 5 落地(**本版对用户最有感的产品改动**:候选榜从「K1 认为会涨的票」变成「五板块过完安检、值得花注意力的票」,终选权用户,§2.3)。新 `report/intel_candidates.py::build_intel_candidates`,`pipeline.py` 候选源切换(`build_candidates`→`build_intel_candidates`;K1 `build_candidates`/`score_candidates`/`_base_score_expr`/四件套/`pattern_tags` **保留供自选体检/纪律红绿灯/回测同码复用,未删**)。**四步管线**:① 板块层 = 五常驻(唯一源 `settings_store.DEFAULT_INTEL_WATCH_BOARDS`〔芯片概念/创新药/储能/机器人概念/稀土永磁〕,`db.py` 幂等加列 `app_settings.intel_watch_boards`〔NULL=用默认、`[]`=显式清空〕,**按 `ths_index.name` 精确匹配**取 ts_code〔真实数据五个全命中唯一 code;禁模糊——"芯片"会误纳汽车芯片/存储芯片、"机器人"误纳人形机器人〕)+ 当日暴起(`compute_sector_strength` 拥挤度 top-10,**先过 `board_pool.apply_hygiene` 卫生线**剔资格/宽基标签);② 个股层 = step① 板块成员 ∩ MAIN/GEM/STAR(排 BSE,`base_universe_expr` 已含 !=BSE + 显式再挡)∩ `base_universe_expr` 卫生线 ∩ 非次新 120(复用 `signals.forbid_new_stock`,= days_since_listing≥120 同 K4 A4 口径)∩ 趋势向上(`close>ma20` 粗代理,标注),**不套 K1 主板 only / pullback·breakout 回调买点**(§3.8-(b) 与回测信号解耦);③ K4 安检 = 读 DB `strategy_versions.K4.k4_advisory` 分区(**新增 `holding_k4_check.load_k4_sections` 读 section 归属,不抄常量**)`hard_cut` 拦截出池 / `avoid_flag` 打标保留(机器不禁给人判);④ 情报排序 = 板块资金流强度(消费 C2 `compute_sector_moneyflow` 全板块净流入)+ 题材天数**反用**(`_theme_freshness_score`:1天新鲜>2-3警惕;≥4 已在 ③ A2 hard_cut 剔)→ 出 **20 只**。**性能坑(C1/C2 施工者点名交接)二选一选 (a)**:复用 ②-A 判据镜像 + **换全市场 bulk 面板 I/O**——给 `holding_k4_check._build_holding_feature_panel` 加 `load_fn` 参数(默认 `_load_codes_table` 逐票=② 持仓不变、byte-identical),C3 注入 `intel_candidates._bulk_load_codes_table`(一次 `scan_parquet` 谓词下推按 code 集合过滤,免逐票 N 次开文件),**特征/判据/阈值与 ② 完全同一份**(单一源);两处 I/O 一致性由 `test_bulk_and_percode_loaders_agree` 直接对拍(`a.equals(b)`)。**§3.8 新表述落地核对**:候选生成解耦(GEM/STAR 高弹入候选);**问询台 `inquiry.py` / 自选体检 `watchlist_check.py` 纪律红绿灯仍 K1 同码**(未碰这两模块,`test_watchlist_discipline_still_k1_while_candidates_decoupled` 证同一 GEM 票候选纳入但自选体检 K1 红灯并存)。**产品语义**:`Candidate` 加 `k4_flags`/`intel_rank`(默认空,K1 路径向后兼容)、`CandidateOut` 加 `k4Flags`/`intelRank`(`IntelRankOut{sectorFlow,themePersistDays,highElasticity}`)、`_shape_candidate` 透传、`render.py` 候选节改「情报筛选·非买入信号」文案 + K4 标注/情报排序展示;**生成域刻意含高弹**(不偷偷加回 K1 剔高弹,只 `highElasticity` 标注)。forced 问询票语义不变(§2.5 强制纳入,豁免卫生线/hard_cut、仅 K4 打标透出)。全量 **pytest 1093 passed, 2 skipped**(基线 1080 + 13 新;更新 `conftest.seed_synthetic_market` 铺常驻板块「储能」让情报管线产候选、`test_report_consistency` 回放断言改情报口径日期敏感性、`test_render` 一处文案断言)。**端到端真实数据(隔离库:`ATTACH` 真实 `data/neckline.db` 四张只读参考表〔trade_cal/strategy_versions/stock_basic/namechange〕 + 真实六年 Parquet backfill;`scripts/report.py 20260722` 真报告)**:① 五常驻精确匹配全命中、当日暴起 top-10 = 猪肉(年龄16天)/减肥药/养鸡/创新药/重组蛋白/青蒿素/禽流感/CRO/仿制药一致性评价/超级品牌、卫生线剔 28 个资格板块;新口径 20 只候选全部医药题材(爱美客〔创〕/华熙生物〔科〕/天士力〔主〕/奥浦迈…,板块分布主板12·科创3·创业5),8 只高弹、7 只 avoid_flag 打标(B2 双金叉/B4 追强/B1 堆积);**与同日 K1 旧口径对照:旧 20 只全是主板浅回调蓝筹(中国联通/建筑/银行…),交集为空**——把「防御蓝筹回测信号」整体换成「五板块过安检的注意力清单」;store 落库 + `_shape_candidate` API 层 `k4Flags`/`intelRank` 透传验证正确。迁移 `app_settings.intel_watch_boards` 生产库副本 `cp -p` 备份后 integrity ok + 幂等重跑不炸 + 业务表行数零变。**未碰**生产 ECS / STRATEGY_LAB / research/* / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1、K4 只读)/ v1.3-①②④⑤⑥⑦ / **C4 本轮不做留下一轮**;`data/*.bak-*`、未跟踪 `research/k5_cb.py` 未删未提交。**⚠ 需用户拍板**:(1) A3b 年线下放量大阳派发(合成码不在 DB advisory,证据源=雷区地图 3-⑤)当前默认归 `avoid_flag`(打标不拦,严守 hard_cut 单一源=DB)——是否升级为 hard_cut;(2) `intel_watch_boards` 本块只落 DB 列 + 读取器,**未配 GET/PUT HTTP 端点**(留 ⑥/设置屏);(3) 印花税等费率沿 v1.3-① `fees.py` 现值(与本块无关,一并提醒)。
- **2026-07-26 · v1.3-③-C1/C2 情报官(复盘情报件 + 板块资金流展示)完工(@builder,未部署)**:需求 3 落地一半——**只做 C1 + C2,C3(候选四步管线改版)/ C4(消息面公告扫描)本轮不做、留下一轮**(刻意拆段避免长任务半成品)。新 `report/board_pool.py`(板块池卫生线单一源,C1/C2 共用、C3 下一轮也应复用):394 个同花顺概念板块按**名称模式**(融资融券/股通/成份股/样本股/指数/专精特新/国企改革/预增/预减/贬值/升值/破净/送转/回购/增减持/摘帽/ST/次新/富时/MSCI/标普/QFII/AH股/转债/参股/举牌/重组/壳资源/绩优/超跌/机构/北交所/创业板/科创)+ **成分数上限 1500** 双闸剔除,互斥归因,`audit_lines()` 剔除审计透出(落日志 + 报告脚注)。**实测校准**(对照 2026-07-24 真实 `ths_index`/`ths_member` 快照逐个核对):真实剔除 28 个板块全部由名称模式命中(融资融券 3842/深股通 1886/沪股通 1644/国企改革 1470/专精特新 1213 等);成分数闸 0 命中(纯防御)。**关键发现**:合法大主题板块成分数同样上千(机器人概念 1213、人工智能 1081、新能源汽车 1051、华为概念 1005、芯片概念 908)——不能只按数量剔除,`MAX_CONSTITUENTS=1500` 特意留足边际,避免误杀用户五个常驻板块之二(机器人概念/芯片概念)。**误伤修复**:"重组"关键词朴素子串会误伤"重组蛋白"(生物医药,与公司重组无关)→ 加 `_NAME_PATTERN_ALLOWLIST` 精确豁免,真实数据验证已生效。**C1** 新 `report/intel.py::compute_intel`——涨幅/跌幅榜(各 20 只)/ 涨停梯队(按连板数分组降序)/ 跌停榜(展示上限 100 + `limitDownTotalCount` 真实总数)/ 大盘量能(**沪深两市合计**成交额 = 上证 `000001.SH`〔复用 `features.SSE_INDEX`〕+ 深证 `399001.SZ`,5 日均样本不足诚实标注)/ 最强题材(`compute_sector_strength` 全量 → 过卫生线 → 截前 10,核心龙头 = 板块成分股当日涨幅前 2)+ 题材持续天数(`_add_board_age` 复用,四档标签 + 汇总分布)/ 市值偏好(`daily_basic.total_mv` 固定 5 桶,即便 0 只也展示,跨日可比)/ 涨跌停制度偏好(`limit_pct` 分 5/10/20/30cm,只展示当日实际出现的值——两种分桶策略刻意不同,见模块注释)。**C2** 新 `report/sector_moneyflow.py::compute_sector_moneyflow`——`moneyflow_dc.net_amount` 按板块成分(过卫生线)加总,净流入/净流出榜各 15。**定位写死**(代码 + 文案双重标注):拥挤情报件、非选股信号(K2 判决板块层有效但无次日领先性),`report/candidates.py` 评分与 entry mask 零改动(已 grep 核实无调用点)。2023-09-11 前无数据 / 覆盖内当日缺失均 `available=False` + 诚实原因(真实 2023-06-01/2023-09-15 边界数据验证两条路径)。**证据强度标注(硬要求①)**:复用 `holding_k4_check.K4AdvisoryOut` 已建立的 `price_volume`/`constituent` 词表(不新造)——`ThemeItem`/`SectorMoneyflowItem.evidenceStrength` 恒 `constituent`(成分依赖,K2「成分洞」);其余 EOD 硬数据字段强证据,`evidenceNote` 顶层文案 + markdown 小节标题同步标注。**不阻断主报告管线(硬要求④)**:`intel.py` 内 `_safe()` 逐项降级 + 记警告,`pipeline.py::build_report` 再包一层 try/except 兜底整段异常(单测 monkeypatch 制造异常验证主报告仍成功产出)。**落盘**:C1/C2 纯读 + 内存聚合,零新 Parquet 写入,硬要求③自动满足。**契约有一处出入(已选择,非擅改)**:`reports` 表幂等加 `intel_json`/`sector_moneyflow_json`,均落 **JSON 对象**(非数组)——plan 契约清单写 `sectorMoneyflow[]`,但 C2 需要 `available`/`unavailableReason` 承载"2023-09 前无数据"这类诚实留空原因,裸数组无处安放,故 `ReportOut.sectorMoneyflow` 实现为单对象(内含 `topInflow`/`topOutflow` 两个子数组)。均透传报告落库快照(同 `sentiment`/`sectors` 惯例)。markdown 新增两节。全量 **pytest 1080 passed, 2 skipped**(基线 1032 + 48 新〔board_pool 12 + intel 18 + sector_moneyflow 8 + report_store 4 + pipeline 4 + api_report_board 2〕,0 回归)。**端到端真实数据验证**(隔离库:`ATTACH` 真实 `data/neckline.db` 四张只读参考表 + 真实六年 Parquet backfill):`scripts/report.py 20260722` 真报告情报节全字段非空且数字合理(涨停梯队"5 连板×1 只…"、猪肉板块"板块年龄 16 天已延续"、沪深两市成交额 21,671.4 亿元);板块资金流真实榜单 + 剔除审计真实列出 28 个板块;真实 `FastAPI TestClient` 验证 `GET /report?date=` API 层 camelCase 透传正确、HTTP 200。**未碰**生产 ECS / `STRATEGY_LAB.md` / `research/*` / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1)/ `report/candidates.py` 评分逻辑与 entry mask / v1.3-①④⑤⑥⑦。**给 C3 接口/数据准备**:`board_pool` 三函数已就绪可直接复用;`holding_k4_check.py` 的 K4 polars 镜像评估器当前假设「≤3 持仓逐票循环」I/O,不适合 C3 全板块大规模 universe,是 C3 开工前最大的一处设计决策点;`app_settings.intel_watch_boards`/`news_alerts` 表本轮均未建,留 C3/C4 各自建。
- **2026-07-26 · v1.3-② 持仓管理层(K4 牌每日对持仓重算)完工(@builder-pro,🔴 推送路由 + 金额判定,未部署/未激活)**:需求 7 落地(K4 牌不再只买前安检,每日对持仓重算 + 派发警示)。**②-A** 新 `report/holding_k4_check.py`——16:35 EOD 面板对每只 open 持仓重算 K4 advisory 命中(**判据全读 DB `strategy_versions` K4 行 `k4_advisory`**:evidence 文字读 DB;判据阈值 = 模块命名常量镜像,advisory 人读字符串→可执行 polars 镜像的逐条对应写在模块头 docstring 对照表〔A1 换手 / A2/B3 题材持续〔board_age 代理〕/ A3 年线下涨停 / A3b 年线下放量大阳 / B1 堆积 / B2 双金叉〔MACD/KDJ 逐字镜像 `k4p_h4_cross`〕/ B4 追强〕,改阈值须同改两处;ma250/state4/cnt3 等生产面板未算的列在本模块补〔镜像 `k3_panel`/`k4p_common`〕;**held-stocks-only 面板**〔≤3 仓循环 `get_stock_history` 载 ~420 自然日,不载全市场 250 日,内存友好〕)。**②-B** 分级写死:强价量(A1/A3/A3b,`has_strong` 触发第六类 APNs)/ 普通(B1/B2/B4 只进看板)/ 题材天数(A2≥4 强级别但弱证据 constituent〔参考〕、B3 2-3 普通;**成分类不单独触发强警示**,守铁律「证伪只用价量结构」);「放量大阳 B1 既列强又列普通」矛盾由**年线下闸**自洽解(年线下=A3b 派发强 / 年线上=B1 堆积普通,与雷区地图 3-⑤「放量大阳只在年线下为负」证据一致;A3b=Backlog「诱多做局反向哨兵」并入,数字依据雷区地图 3-⑤)。**②-C 第六类推送**(用户 2026-07-26 拍板,推翻 planner「复用 D5EXIT」默认):独立 category `HOLDINGALERT`(`apns.py`)+ 独立开关 `push_holding_alert`(`app_settings` 幂等加列、默认开)+ `notify.push_holding_alert`(`__all__` 五→六,结构性守护单测 `test_push_whitelist_is_exactly_six`);`GET/PUT /settings/push` 契约扩六字段(`PushSettingsOut/SettingsPushIn.holdingAlert`)。**②-D 情景树每日对照**:复用既有 `POST /decisions/{id}/scenario-outcome`(**无新写端点**)+ `decision_log.list_decisions`/`GET /decisions` 加 `position_id` 只读过滤(挑出该持仓待对照决策)+ `PositionOut.scenarioReviewPending`。**②-E** 盘中实时派发**降级为纯 EOD**(plan 明允,不碰 intraday 持仓哨兵)。**seam(v1.3-① 留)已接**:16:35 算好每持仓 D5 收盘净浮盈(`fees.estimate_net_float`,买入费读 `positions.buy_fees`、卖出费 `estimate_sell_fee` 估算)→ 落新表 `holding_eod_check`(`db.py` 幂等建 + `report/holding_store.py` 存取)→ `sentinel/precall.py::scan_time_exits(net_float_provider=holding_store.net_float_provider(db))` 接线(次日 9:25:30 读最近一份 net_float),**修复「provider 恒 None → 激活后浮盈豁免形同虚设」的地基缺口**(单测锁死:同 config 下 provider 给正浮盈→profit_exempt、None→保守 time_exit)。**GET /positions** 读最近一份 16:35 快照嵌 `PositionOut.k4Advisory[{code,label,level,evidence,evidenceStrength}]` + `scenarioReviewPending`(服务端算好,客户端不重算)。K4 强警示推送折进 `scripts/report.py --notify`(逐仓,只推 `has_strong`)。全量 **pytest 1031 passed, 2 skipped**(基线 1010 + 21 新〔`test_holding_k4_check.py` 19 + notify 第六类 2〕,0 回归)。**未碰**生产 ECS / STRATEGY_LAB / research/* / K1/K2/K4/v1.2/v1.3 已落章程行(`is_active` 仍 K1、K4 行只读)/ v1.3-①③④⑤⑥⑦;改 schema 前 `cp -p` 备份、`data/*.bak-*` 未删未提交。🔴 建议叫一次 review。
- **2026-07-26 · v1.3-① 退出规则章程变更完工(@builder-pro,🔴 碰纪律章程,未部署/未激活)**:需求 6 落地(止损 -5% 不变 / 回落 5%→8% / 时间退出仅非浮盈单〔D5 收盘扣双边费净浮盈 ≤0 次日退〕/ 浮盈单豁免时间退出硬上限 15 日)。**①-A** `MomentumConfig` 加 `max_hold_days_profit:Optional[int]=None`+`time_exit_only_if_unprofitable:bool=False`(默认值让 K1 逐位不变——真 k3_panel 六年回测改动前后逐位吻合 **N=1288 / total_return −20.53% / final_equity 95361.50**;逻辑层护栏 `tests/test_v13_exit_guardrail.py` 始终跑、六年实证 `tests/test_v13_exit_6y_baseline.py` env 门控 `NECKLINE_RUN_6Y=1` 默认跳)。**①-B** 回测引擎 `momentum.py::_time_exit_reason`/`_d5_net_float` 镜像 `research/h9_exit_reform.py::_sim_one` V1(D5 恰达算净浮盈 → >0 一次性豁免续持至硬上限、≤0 照旧退;豁免态 per-position `_eff_max`、平仓即清;卖出费用引擎既有 `Broker._sell_fees`,**回测不走实盘估算**);六年 v1.3 config 回测硬上限豁免续命单 250 只。**①-C** `sentinel/precall.py` 新增 `scan_time_exits`+`classify_time_exit`+`TimeExit`(三态 + holding,`net_float_provider` 注入;**config 未启用退回单档 == max_hold_days = v1.1 完全一致**,`scan_d5_exits` 原语保留;权威净浮盈计算移 16:35 EOD、precall provider=None 保守判非浮盈待 v1.3-② 的 16:35 持仓管线接线)。**①-D** `api/notify.py::push_d5_exit` 加 `kind`/`max_hold_effective`/`two_tier` 两档文案(非浮盈标「净浮盈 ≤0」/ 硬上限标「浮盈硬上限 D15」/ K1 单档不标净浮盈;**`__all__` 仍五入口、APNs category 仍五类**)。**①-E** `scripts/charter_v1_3.py`(从 K1 config 复制**只改六字段**〔仓位 3 + 退出 3〕、风险登记原样入 changelog 不精简、`activate=False`;本地权威库已落行:K1 rule SHA256 `5f331ef6…` 逐字节不变 / v1.2 行保留不激活 / integrity ok / 业务表零改动 / K1 仍现役)+ `scripts/activate_charter.py` 目标默认改 `v1.3`(`--target`)、激活前核对 `take_profit_retrace=0.08`、**硬拒绝误选 v1.2**(退出码 2)。**①-F** `neckline/fees.py`(卖出费估算唯一源:印花税万5〔2023-08-28 减半后现行,**⚠待用户确认是否改千1**〕/ 过户费万0.1 / 从买入实付反推佣金率 + 5 元地板;诚实标注估算,误差只影响盈亏平衡线附近判向;真数走 `positions.sell_fees` 回填、周复盘用真数)。`positions` 幂等加 `buy_fees`/`sell_fees`(`db.py` 迁移 + `sentinel/positions.py` store)、`PositionOut` 加 `maxHoldDaysEffective`/`timeExitState`/`buyFees`/`sellFees`(服务端按 D5 净浮盈判好下发)、`PositionOpenIn.buyFees`/`PositionCloseIn.sellFees` 透传。§2.1 第 2 条 planner 已改 + 风险登记(回落 8% 免测采纳 / 组合版未整体回测差 724 元未过 2026 门禁 / 用户知情越线 2026-07-25 + 证据链 `research/h9_exit_reform.md`+`winners_anatomy.md`)原样入 charter changelog。**生效仍 staged**(`is_active` 仍 K1、行为零变化,清仓 + 用户确认后跑切换器,留 v1.3-⑦)。全量 **pytest 1010 passed, 2 skipped**(964 基线 + 46 新,0 回归)。**未碰**生产 ECS / STRATEGY_LAB / research/* / v1.3-②③④⑤⑥⑦ / v1.2 已完工六块记录;`data/*.bak-*`、未跟踪 `research/k5_cb.py` 未动。🔴 建议叫一次 review。
- **2026-07-26 · v1.3 立项(施工图就位待 builder)· v1.2 合并发布 + 退出规则改革 + 持仓管理层 + 情报官候选管线改版**:用户拍板「v1.2 不单独发、与 v1.3 合并、对外版号跳 v1.3」(核实 `app.py::VERSION` 仍 v1.1.1、v1.2 六块从未部署)。交接 memo `archive/交接_系统线升级需求_20260725.md` 最新版(需求 1/3/5/6/7)落系统线施工图 §五 顶部,分块 ①–⑦。**① 退出规则章程变更(🔴)**:止损 -5% 不变 / 回落 5%→8% / 时间退出仅对非浮盈单(D5 收盘扣双边费净浮盈 ≤0 次日退)/ 浮盈豁免时间退出硬上限 15 日;`MomentumConfig` 加 `max_hold_days_profit=None`+`time_exit_only_if_unprofitable=False`(**默认保 K1 逐位不变 N=1288/−20.53%**)、回测引擎条件退出(与 `research/h9_exit_reform.py` V1 对拍)、`scan_d5_exits` 两档(判定移 16:35 EOD、precall 退化纯执行提醒)、notify 两档、charter `v1.3` 行(读 K1 config 只改六字段、**作废过时 `v1.2` 行:保留不删不激活**)、卖出费估算(`neckline/fees.py` 唯一源、印花税万5⚠待用户确认、佣金买入费反推、诚实标注估算、误差只影响盈亏平衡线附近判向);风险登记(回落 8% 免测采纳 / 组合版未整体回测差 724 元 / 用户知情越线)原样入 charter + §2.1。**② 持仓管理层(推送🔴)**:读 DB `K4.k4_advisory`(不抄常量、polars 镜像同 circuit 先例)每日对持仓体检;强警示(年线下涨停/放量大阳派发〔并入 STRATEGY_LAB「诱多做局反向哨兵」〕、题材≥4天、换手>10%)→ APNs 复用 `CATEGORY_D5EXIT`(**不新增第六类**)、普通→看板;情景树每日勾选复用 `scenario-outcome`;证据强度(价量强/成分弱)。**③ 情报官**:C1 复盘情报件(全 EOD、成分类标弱证据)+ C2 板块资金流(`write_table_day`)+ **C3 候选四步管线改版(K1 entry mask 退役、§3.8 铁律重述、生成域含高弹)** + C4 消息面公告扫描(TuShare 源待查/LLM 兜底守 90s/缺 key 降级)。**④ 挂单追踪(原 v1.2.1-C 归 v1.3)⑤ 问询清理(原 v1.2.1-D2 归 v1.3)⑥ 客户端跟进 ⑦ 部署+章程激活(🔴 合并 v1.2-F)**:一次推 v1.2+v1.3、VERSION→v1.3、累积迁移一次性(备份+integrity+业务零丢失+回退路径,**本版最大部署风险**)、sync_code.sh 首验(footgun 活体收官)、timer/16:05 配额核查、staged 激活(切换器 `--target v1.3`,**勿误激活 v1.2**)。**v1.3.1 后发**:v1.2.1-D 归因(双打法 A/B 裁决必需件不许丢,需 30-50 笔)。**设计前提变更**:§2.1 第 2 条按需求 6 改写+风险登记、§2.3 候选语义变更、§2.4 K4 强警示推送(仍五类)、§3.8 同码三跑道重述(候选解耦/纪律同码);§四加立项条目、§七 Backlog 需求 3/5 标「已进 v1.3」+ v1.2.1-D→v1.3.1 + TuShare 配额风险 + 诱多哨兵并入 v1.3-②。**铁律**:推送仍五类、单一事实源(仓位/止损/止盈/hold 两档/熔断阈值各自唯一源)、系统永不自动下单、新表列幂等迁移+改 schema 前 `cp -p`、K1 逐位不变护栏、落盘走 `write_table_day`、LLM 90s。**只改 PROJECT_PLAN.md**(STRATEGY_LAB/CLAUDE.md/research/* 未碰,策略线未跟踪的 `research/k5_cb.py` 等非本会话)。仓库现状:v1.3 施工图就位待 builder(①②⑦ 🔴 高危区,建议完工后叫 review)。
- **2026-07-25 · v1.2-E 客户端双端(SwiftUI iOS+macOS)完工**:决策日志八项录入嵌「已按计划买入」流程之前(`DecisionLogSheet.swift` 新增)——`beginPositionEntryFlow()` 系列(原 `openEntrySheet` 改名)先开决策日志表单 → 提交成功转开仓表单(暂存 `pendingDecisionId`)→ 成交后 `submitOpenPosition()` 自动 `link`;表单顶部常驻「跳过预注册」出口(**软约束不做硬阻断**),`.open` 阶段中途放弃自动 `cancel` 预注册计划(不留孤儿行)。⑦情景树次日复盘勾选 `matched`(情景文本只读)+ ⑧打法标签驱动呼吸台账入口露出规则(`playbookTag==BREATHING_TRIAL` 主区域露出、其余持仓走卡片「更多」次级菜单,`PositionExtras.swift` 新增)。熔断横幅(`CircuitLockBanner`)置顶今日计划面 + 「开新仓」两处入口按锁定态灰化(纯客户端自律,服务端不拦)+「熔断复盘」按钮先展示材料再解锁(`CircuitReviewSheet`)。清仓补 `closeReason` picker(五码可留空)。呼吸 T 台账(`BreathingLedgerView.swift` 新增)展示逐笔 + `baseCostAdj`/`edgeToPrice`(文案按「先手成本比现价低/高 X%」,相对成本口径),`fees` 录入必填无默认值。`entry-suggestion` 改区间双档,两表单均只展示参考区间、不再预填单一 `qty`。**设计选择留痕**:熔断态改走独立 `GET /circuit`(不用内嵌 `PositionsOut.circuit`,避免牵动 `fetchPositions()` 既有类型/单测);呼吸台账用 sheet 呈现(非持仓卡内联展开);`reviseDecision` 已实现 API 方法但未挂常规 UI 入口(核实后端 revise 会重置 `position_id`/`status`,对已成交决策调用会产生孤儿行,UI 若不配套「重新 link」流程会让持仓卡回显对不上,故只留 `beginReviseDecision(_:)` 待后续块按需接线)。**⚠ 发现的后端契约缺口(已提出、未擅自碰后端)**:`EntrySuggestionOut`/`entry_suggestion()` 仍是 v1.1-B.3 单 `qty` 旧形状,未实现区间字段(`curl` 活体验证实测确认);客户端已按「v1.2 客户端契约清单」实现 + 单测覆盖,真实请求会解码失败但走既有降级模式不崩,区间预填功能待后端补齐方能生效,`IntegrationSmokeTests.testEntrySuggestionRealRequest` 已改造成显式 `XCTSkip` 而非放任变红。新增 Swift 单测 41 个函数(4 个因契约变化重命名,净新增 37),覆盖决策日志 DTO 编解码/`createdAt` 不可覆盖/枚举中文映射/情景树数组 Codable 往返/熔断状态解码/区间预填/`closeReason` 请求体编码/表单校验/入口露出逻辑/软约束状态编排。双端 `xcodebuild` BUILD SUCCEEDED、iOS Simulator TEST SUCCEEDED(110 测试,13 个 IntegrationSmokeTests 按设计跳过或视服务器可达性,0 失败)。本地真联调(隔离临时库):决策日志创建→开仓→link→scenario-outcome 真实往返;真实触发连续 3 笔止损→熔断锁定→`GET /circuit` 真实渲染;呼吸 T 增删+派生字段真实展示(含真实行情);`closeReason` 真实写入。iOS Simulator 截图核对 6 张(今日计划熔断横幅+入口灰化、呼吸底仓试验持仓卡回显、决策日志表单含跳过出口、熔断复盘材料弹层、设置屏 Dev 已连通)。新增纯 QA 钩子 `NECKLINE_INITIAL_MODAL`(同 `NECKLINE_INITIAL_TAB` 先例)。后端全量 pytest 963 复核 0 回归(本块未改任何后端 Python/DB/`strategy_versions`,未碰生产 ECS)。未新增第六个 tab;`SentinelKind` 核实不需要补 `.circuit`(熔断不进 `/board` 事件列表)。仓库现状:v1.2-F 部署待续,建议叫一次 review(A2/F 🔴 高危区 + entry-suggestion 契约缺口需一并处理)。
- **2026-07-25 · v1.2-G 呼吸试验仓台账后端完工**:新表 `breathing_t_trades`(幂等建表,子表非扩列——一个底仓 → N 次 T 一对多;`position_id` 关联 `positions.id`,无 SQL 级 FK 约束,同 `decision_log.position_id` 惯例)+ 新模块 `neckline/breathing.py`(唯一写入通道)+ 三端点(`GET`/`POST /breathing/{position_id}/trades`、`DELETE /breathing/trades/{id}`)。**底仓 / T 仓分离记账**:底仓仍是 `positions` 表一行,本模块任何函数都不写 `positions`(单测断言底仓字段逐字不变)。T 盈亏 `compute_t_pnl = (sell−buy)×qty−fees`,先买后卖 / 先卖后买同式,方向只在 `note` 自由备注、不落结构化列。费用 `fees` 由客户端录入、`add_trade` 原样落库,模块内无任何费率字面量(不按 0.1%/20 元估算)。「先手」成本优势读时派生、不落列:`compute_base_cost_adj = buy_price−(ΣT净盈亏)/底仓qty`(净赚拉低成本 / 净亏推高成本两方向均有单测)、`compute_edge_to_price = (price−baseCostAdj)/baseCostAdj`(2026-07-25 用户拍板改口径为**相对自己的摊薄成本**〔浮盈率读数〕,不与 `distToStopPct` 分母取现价对齐——两字段问的问题不同,不强行统一);现价复用既有 `_resolve_prices`/`sentinel/quotes.py:get_quotes`,不新拉数据源,无实时价 → `null` 不崩。打法标签唯一源 = `decision_log.playbook_tag`(v1.2-B ⑧),本模块不存第二份。`GET`/`POST` 底仓不存在 → 404;`DELETE` 硬删除、不存在 → 404(幂等安全)。契约与「v1.2 客户端契约清单」逐字段一致。新增单测 42(store 层 `tests/test_breathing.py` 27 + API 层 `tests/test_api_breathing.py` 15)。真实 uvicorn 临时库端到端跑通(含真实联网 `get_quotes` 命中真实行情验证 `edgeToPrice` 有价分支)。本地权威库 `data/neckline.db` 迁移验证:`cp -p` 备份、`breathing_t_trades` 建表 + `PRAGMA integrity_check` ok + 重跑 `init_schema()` 幂等不炸、核心业务表行数与迁移前一致、`is_active` 仍 K1,零数据丢失。全量 pytest 963 passed(921 基线 + 42 新,0 回归)。未碰 `positions.close_reason`(A2 块)/`decision_log` 表结构(B 块)/`strategy_versions` 任何行/生产 ECS。**迁移验证补记**:本轮跑到「③迁移 integrity ok + 重跑不炸」这一步时,本地权威库 `data/neckline.db` 已应用本块迁移(`breathing_t_trades` 表已存在,系本 session 施工期间较早的 `init_schema()` 调用所致,非另一并发会话),`init_schema()` 幂等重跑无影响,核心业务表行数与 `is_active` 均未受扰动、integrity ok。
- **2026-07-25 · v1.2-A2 熔断纪律后端完工(🔴 碰纪律章程 + 金额判定 + 第五类推送)**:新模块 `neckline/sentinel/circuit.py`(阈值命名常量 `CIRCUIT_CONSECUTIVE_STOPS=3`/`CIRCUIT_DAILY_LOSS_YUAN=4000.0`,住模块非 config;`stop_pct` 读现役 config;两触发口径:连续 3 笔止损尾部连续〔显式 `STOP_LOSS` 或 NULL 价格兜底、显式非止损码不二次猜、兜底只计数不回写〕+ 单日**净口径**净亏 ≤−4000〔盈亏互抵,遮蔽缺口由连续止损兜住〕)+ 新表 `circuit_breaker`(触发/解锁双落列,锁定态派生 `unlocked_at IS NULL`、跨日持续、幂等不开第二行)+ `positions.close_reason`(五枚举码,唯一源 `sentinel/positions.py`)+ `app_settings.push_circuit`(第五类推送开关,默认开)。触发折进 `close_position` 端点(尽力而为、异常吞掉不阻断,纯提醒层绝不代下单/拦 `POST /positions`,§3.8)+ 第五类 APNs `push_circuit_breaker`(`CATEGORY_CIRCUIT`,`notify.__all__` 结构守护四→五)。两解锁路径复用强制复盘同源:`POST /circuit/unlock`(`review_ack`)+ 周复盘覆盖触发周且 `forced_review` 自动解锁(`weekly_review`)。端点 `GET /circuit`/`POST /circuit/unlock` + `PositionsOut.circuit` 内嵌 + `PositionCloseIn.closeReason`(Literal,非法码 422),契约与「v1.2 客户端契约清单」逐字段一致。幂等迁移(生产库副本 integrity ok/重跑不炸/业务零丢失/`is_active` 仍 K1)。新增单测 29(circuit 16 + api_circuit 10 + close_reason 往返 1 + notify 第五类守护 2)。全量 pytest 921 passed(892 基线 + 29 新,0 回归)。诚实边界:`basis_json` 透出「基于台账 N 笔已补录成交」。未碰 `strategy_versions`/K 字头、未碰生产 ECS、未激活 v1.2 章程。
- **2026-07-25 · v1.2-B 预注册决策日志(八项)后端完工**:新表 `decision_log` + 新模块 `neckline/decision_log.py`(唯一写入通道)+ 六端点(`POST /decisions`/`GET /decisions`/`link`/`cancel`/`revise`/`scenario-outcome`)。八项预注册(①为什么买 ②为什么这个入场价 ③目标价 ④离场价格区间 ⑤论点标签 ⑥证伪条件 ⑦应对方案·情景树 ⑧打法标签)落库后不可编辑,唯一改法是 `revise` 新增修订行(`revision_of` 落链根 id、旧行原地不变);情景树 `matched` 是唯一事后可翻字段,专用端点 `scenario-outcome` 只碰这一列。`created_at` 由函数签名物理杜绝客户端覆盖。枚举(论点标签 5 码 / 打法标签 2 码 / 情景 action 4 码)一律服务端码 + 客户端展示层换算(沿 `boardLabel` 先例),非法码 422。契约与「v1.2 客户端契约清单」逐字段一致。新增单测 46(store 24 + API 22),`scripts/smoke_api.sh` 新增 25)-33) 节真实 uvicorn 活体跑通。全量 pytest 892 passed(846 基线 + 46 新,0 回归)。审计件非下单件,未碰 `positions.close_reason`(A2 块)/`strategy_versions`(A 块)/生产 ECS。仓库现状:v1.2-A2/G/E/F 待续。
- **2026-07-25(增补)· v1.2 范围扩大 + 切两波(策略线三版补丁交接)**:策略线改交接需求(`archive/交接_系统线升级需求_20260725.md` 最新版:需求1 补熔断 + 仓位 2+1、需求2 六项→八项、需求4 呼吸台账转正式排期),重切版本。**v1.2 先发**(挡上线,齐了即可开新打法首笔):+ **A2 熔断纪律**(🔴,§2.1 第 7 条:连续 3 笔止损 / 单日**净亏** ≥4000〔不含费用〕→ 停开新仓、次日只减不加、完成强制复盘解锁;**纯提醒层不代下单**;阈值 = **命名常量非 config**〔理由同 `FORCED_REVIEW_LOSS_FRAC` 政策值〕;`positions` 幂等加 `close_reason` 五枚举 + NULL 价格兜底判止损〔近似标注、仅 NULL 才兜底〕;`circuit_breaker` 表触发 / 解锁双落库、锁定态派生;第五类 APNs `CIRCUIT`,notify 白名单结构守护四→五入口;解锁复用 `review/reconcile.py::is_forced_review` / `material.py` 同源,客户端按钮 + 周复盘自动两路)+ **B 决策日志六→八项**(⑦情景树 `[{scenario,trigger,action,matched}]` 内容不可编辑、仅 `matched` 事后勾〔专用 `scenario-outcome` 端点〕;⑧打法标签 `SWING_CHASE`/`BREATHING_TRIAL`)+ **G 呼吸试验仓台账**(**子表** `breathing_t_trades` 一对多挂底仓、T 费用如实逐笔不硬编费率、先手距离派生)+ E 客户端(八项表单 + 熔断状态标记 + 台账 + `close_reason` picker + 第五推送开关)+ F 部署(三新表 + `positions.close_reason` + `push_circuit` 迁移)。**v1.2.1 后发**(不挡上线,攒样本才有用):C 挂单追踪、D 归因扩「论点 × 打法」双维 + 呼吸 T 贡献、D2 问询台选股域清理(施工图全文移入 §五 v1.2.1 小节,分块序列重排)。**v1.3**:需求3 盘前情报包(§七补全:复盘情报字段清单 + 晚间消息面公告扫描 LLM 兜底)。**设计前提变更**:§1.2 用户画像(非上班族 / 盘中人判层可用,旧文划线;边界=不复活已判死信封、雷区地图信封「上班族注意力」历史口径策略线不改)、§2.1 加第 7 条、§2.4 推送四→五类。**v1.1-H 六项活体验收用户确认全过、v1.1 收官**。**铁律**:同码不重写、单一事实源(仓位三字段 / 止损 / 止盈 / hold / 熔断阈值各有唯一源)、系统永不自动下单(熔断纯提醒)、新表 / 列幂等迁移 + 改 schema 前 `cp -p` 备份。仓库现状:v1.2 增补施工图就位待 builder(A2/F 🔴 建议叫 review)。
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
- **2026-07-25 · v1.2-A 完工(staged 步骤 1):仓位章程三仓制落库 + 历史洗白修复(@builder-pro,🔴 碰纪律章程 + 大脑激活)**。**交付**:① **章程落库**(`scripts/charter_v1_2.py`)——从 DB 读现役 K1 的 `rule["config"]` **复制**一份、**只改三个仓位字段**(`max_positions` 5→3 / `single_cap` 20000→40000〔语义=违纪判定上限,非推荐值〕/ `max_exposure_frac` 0.6→1.0),其余逐字段原样(**禁手抄**,靠复制保证 K1 逐位相同),`brain.save_version("v1.2", rule={"config":…, "lineage":"K1"}, activate=False)`;version=`v1.2`(系统 v 字头,**不占 K 命名空间**);来源核对护栏(现役 config 非 K1 基线则拒绝落行)。② **切换器**(`scripts/activate_charter.py`,**非 API 端点**):前置硬校验 `positions` 无 `status='open'`(有则拒绝 + 待清仓清单 + 非零退出)→ old→new 逐字段 diff(高亮变的三字段)→ 无 `--confirm` 只 dry-run 不写库、带 `--confirm` 才 `brain.activate_version("v1.2")`。③ **激活时间戳 + 历史洗白修复(核心工程)**:`strategy_versions` 幂等加 `activated_at`(NULL 默认)+ **一次性回填现役 K1**(`is_active=1 AND activated_at IS NULL → activated_at=created_at`,幂等、只碰现役行、K2 保持 NULL);`brain.activate_version()`(置目标 `is_active=1`+stamp `activated_at`、其余 `is_active=0` **但保留其 activated_at**〔历史时间线不清空〕);`save_version(activate=True)` 同步 stamp、`INSERT OR REPLACE` 携 `activated_at` 防抹列;`brain.config_active_at(ref_date)` 时间线解析(取 `activated_at` 非空版本按激活日升序,governing=激活日≤ref 的最后一个;ref 早于全部激活日→取最早激活版本;**整表无任何 activated_at→退回 `get_active()`=v1.2 前旧行为**);`run_weekly_review` 改**按 `week_end` 取「当时现役」config**(不再一次性 `get_active` 应用全部周),`reviews` 加 `strategy_version` 列 + `save_weekly_review` 按周落库。**robustness 修**:`get_active`/`get_version`/`list_versions` 读入口改**动态投影**(仅当 `activated_at` 列已迁移才 SELECT 它)——reads 不触发迁移(保持读不写库语义),老库缺列读回 None 不崩 `no such column`(k2/k3 guardrail 读真实未迁移库时真实踩到并修掉,单测 `test_reads_tolerate_pre_migration_schema` 锁死)。④ `db.py` `strategy_versions.version` 注释补「章程修订走系统 v 字头」。**staged 铁律**:本块只做步骤 1,**`is_active` 仍在 K1、绝不激活 v1.2**;步骤 2(用户清仓 + 确认后在 ECS 权威库跑切换器)留 v1.2-F。**验收**:反例命门单测(历史周 3 万买入〔K1 2万上限下违纪〕+ 激活 v1.2〔4万〕后重跑该历史周 → **仍报违纪,不被洗白**)+ 时间线双向 + 切换器三闸(拒绝/dry-run/confirm)全绿;隔离库跑真实切换器激活 v1.2 后,pre-激活周 governing=K1 判违纪、post-激活周 governing=v1.2、真实库全程未激活;本地权威库落 `v1.2`(`is_active=0`)+ K1 回填 `activated_at=created_at`、`integrity ok`、**K1 rule_json 逐字节未变(SHA256 `129e7f45…`)**;全量 **pytest 846 passed**(833 基线 + 13 新,0 回归)。**已知简化(诚实标注)**:governing 按周粒度(ref=`week_end`)解析,激活恰落某周中时该周整体按 `week_end` 的 config 判(staged 在清仓后激活,无跨边界持仓/成交)。**跨线**:激活后需用户在策略线会话同步 `STRATEGY_LAB §一`「现役=v1.2 章程行、内核血缘 K1」(系统线无权改策略线文件)。**高危,建议叫一次 review(用户定)。**
