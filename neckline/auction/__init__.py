"""D1 集合竞价确认层(V2.3.3,K8.md **§二十**)。

读 9:25 已经形成的竞价结果,解释**市场对 D0 交易假设投出的第一次票**:

    D0 建立篮子与预案 → 9:26—9:29 冻结竞价数据 → 机械层整理证据
    → LLM 解释竞价含义 → 输出竞价小报告(五块)。

**为什么单起一个包、⛔ 不塞进 `sentinel/`**(§3.13-A 定死,⛔ 不重开):
    ① `sentinel/` 的身份是「**纯规则判定、零 LLM、零资金面**」(`precall.py` 模块头
       逐字),而竞价确认层**必须调 LLM** —— 塞进去等于把那条身份声明作废;
    ② `tests/test_selection_basket_card.py` 已有依赖方向守门锁着若干条边,再加一条
       LLM 边会让那张网自相矛盾。

🔴 **依赖方向单向 `auction → sentinel → selection`**。⛔ `sentinel/**` 与
`selection/**` **一行都不许 import `neckline.auction`**;`auction/**` 也 ⛔ 不许反向
import `review/**`(`review/selection_clock.py` 读 `auction/store.py` 是允许的,
review 不在那条链上、不成环)。三条方向全部有 AST 守门
(`tests/test_v233_auction_guards.py`)。

🔴 **竞价层不接任何交易动作**:不下单、不改持仓、不改 T1/T2、不进
`baskets` / `tier_history` / `basket_cards` / `selection_clock` 的写路径。做成**结构性
保证** —— 本包零 import `sentinel/positions*`、`positions_entry`、
`review/trade_clock.py`,零写上述四张正式结论表(守门 AST + SQL 双向扫)。

🔴 **本层不许工程侧自己发明数字**(§五 〇b-1)。K8 §二十 的分工写死是「机械层只出读数、
判定交 LLM」:
    · 「触发 D0 明确失效位置」→ 用卡上**冻结**的 `close_below_stop_line`(D0 已过闸);
    · 高开偏离 / 竞价量能 → 复用 `sentinel/precall.py` 的既有常量
      (`PRECALL_GAP_UP_INVALIDATE` / `PRECALL_AUCTION_VOL_{HIGH,LOW}_FRAC`)**一字不改**;
    · 「掉队 / 协同 / 强弱」→ **交 LLM**,机械侧只出读数。

⚠ **「零新阈值」这句话自 2026-08-12 起有四个例外,四个**全部**是用户裁定值**
(§七 P3-69 / P3-70,⛔ 工程侧一个都不许改、也不许再加第五个):
`mech.HISTORY_LOOKBACK_TRADING_DAYS=20` · `mech.HISTORY_LOOKBACK_MAX_CALENDAR_DAYS=60` ·
`mech.HISTORY_MIN_SAMPLE_FOR_COMPARISON=15` · `SECTOR_PEER_MIN=3`(见下)。
🔴 其中 **15 让「历史样本够不够」从 LLM 手里挪回机械侧** —— 别再照着旧注释以为那还是 LLM 判的。

**边界七条(K8 §二十「定位与职责」逐字)**:不改变 D0 的行情状态 / T1 / T2 / 主引擎和
交易预案;不从竞价排行中临时增加交易标的;新发现的强势股只作为市场锚点和后续 D0
研究线索;报告发出后结束本次任务;不持续观察 9:30 以后的价格;⛔ 不输出
`qualified` / `wait` / `cancelled` 等盘中交易状态;竞价结论只说明竞价反映出的信息,
**不等于买入指令**。

**六个模块**(职责定死,⛔ 不许合并 `mech.py` 与 `llm.py`):
    · `collect.py`  冻结抓取(组装清单 → 拉一次价 → 冻结);⛔ 不判定、不落库、不写 parquet
    · `quality.py`  **V2.4.0 P2.1/P2.2 新增**:逐条行情的七项校验 + 双源核验(纯函数,
                    零 IO、零 DB、零 LLM)—— 它只回答「这条读数能不能当今天 9:25 的
                    竞价结果用」,⛔ 不回答任何市场问题
    · `mech.py`     机械层六条职责;🔴 ⛔ **不出任何结论**、零 LLM、除四个裁定值外零阈值
    · `llm.py`      一次调用覆盖全部篮子 + 输出契约 + **三道机械夹逼闸**
    · `store.py`    两张表的两阶段读写(机械列永不 UPDATE)
    · `pipeline.py` 编排 + 9:29 硬截止 + 窗口判定 + 当日防重
"""

from __future__ import annotations

# ── 三种竞价结论(K8 §二十)+ 「待解释」——————————————————————————————
# 🔴 ⛔ 全仓禁止出现 `qualified` / `wait` / `cancelled` 作为竞价结论码(K8 §二十 明令,
#    守门单测全仓扫)。`pending_explanation` 不是第四种结论,它是「LLM 没给出解释」
#    这件事本身 —— K8 原文:「LLM 暂时不可用时……其余结论标记为『待解释』」。
VERDICT_CONFIRM = "confirm"
VERDICT_NEUTRAL = "neutral"
VERDICT_VETO = "veto"
VERDICT_PENDING_EXPLANATION = "pending_explanation"
VERDICTS = (VERDICT_CONFIRM, VERDICT_NEUTRAL, VERDICT_VETO)

# ── 三道机械夹逼闸的命中码(单值,只记**第一个**命中的;⛔ 不许静默夹逼)——————
CLAMPED_BY_DATA_QUALITY = "clamped_by_data_quality"              # 闸 1
CLAMPED_BY_SINGLE_STRONG = "clamped_by_single_strong"            # 闸 2
CLAMPED_BY_MISSING_STRONG_EVIDENCE = "clamped_by_missing_strong_evidence"   # 闸 2(字段没给)
CLAMPED_BY_Y1_LOW_WEIGHT = "clamped_by_y1_low_weight"            # 闸 3
CLAMP_CODES = (
    CLAMPED_BY_DATA_QUALITY, CLAMPED_BY_SINGLE_STRONG,
    CLAMPED_BY_MISSING_STRONG_EVIDENCE, CLAMPED_BY_Y1_LOW_WEIGHT,
)

# ── 数据质量三态(**结构性判据,⛔ 不是百分比阈值**;判据见 `mech.data_quality_of`)——
DQ_OK = "ok"
DQ_DEGRADED = "degraded"
DQ_INSUFFICIENT = "insufficient"

# ── 🔴 V2.4.0 P2.3:数据质量**分域**(K8 §二十「数据质量分域」逐字)————————————
# 「一只无关指数缺失导致整篮强制中性」是 V2.4.0 要修的第 ② 个病:域太宽,
# 什么都算「关键」。K8 把它拆成两域,**只有关键域**才夹逼结论:
#   · 关键域 = 篮子成员自身竞价数据 · 每只成员**实际使用的**市场基准 ·
#     **实际用于**相对板块计算的板块基准 · D0 失效判断所需的冻结锚;
#   · 上下文域 = 其他市场指数 · **未实际用于**当前成员计算的对照股 · 市场锚点 ·
#     历史比较 · 前五日量能背景。
# 关键域非 `ok` → confirm/veto 夹成 neutral;上下文域降级**只降置信度 + 披露缺失**。
# 🔴 **⛔ 不许用无关字段缺失掩盖已有的失效事实**:机械失效警报走独立通道
# (`hit_invalidation_json`),不受任何一域的质量影响。
DOMAIN_CRITICAL = "critical"
DOMAIN_CONTEXT = "context"
QUALITY_DOMAINS = (DOMAIN_CRITICAL, DOMAIN_CONTEXT)

#: 🔴 关键域里的**四类组成**(逐票留痕用的分量码,K8 §二十 逐条)。
#: ⚠ `frozen_anchor` 不是一个"代码",它是卡上冻结的 `close_below_stop_line` ——
#: 拿不到它就判不了「有没有触发 D0 失效位」,故 K8 把它划进关键域。
CRIT_MEMBER_QUOTE = "member_quote"
CRIT_MARKET_BENCHMARK = "market_benchmark"
CRIT_SECTOR_BENCHMARK = "sector_benchmark"
CRIT_FROZEN_ANCHOR = "frozen_anchor"
CRITICAL_COMPONENTS = (CRIT_MEMBER_QUOTE, CRIT_MARKET_BENCHMARK,
                       CRIT_SECTOR_BENCHMARK, CRIT_FROZEN_ANCHOR)

# ── 🔴 V2.4.0 P2.1:逐条竞价行情的**七项校验**结果(单一源,K8 §二十 逐字)————
# 🔴 **⛔ 不得发明「5 分钟新鲜度」之类的新阈值**(审计规格 P2.1 明文):时间判据
# 只用 K8 已规定的**交易日**与 **9:25 / 9:26—9:29 边界** —— 「可接受区间」=
# `[09:25:00, captured_at]`,而 `captured_at` 本就被 `AUCTION_WINDOW_START/END`
# 约束在 `[09:26, 09:29)`(现役常量,一字不改)。
#
# 🔴 **`future_timestamp` 走零容差**(2026-08-12 **用户裁定 #2**,出处
# `PROJECT_PLAN.md` §五 D 节;⛔ 这不是工程侧默认值):
#     「竞价时间戳先执行零容差:源时间与本机存在任何偏差即降级为中性。
#       若实盘出现误判,再由我确认容差秒数,**施工 Agent 不得自行设定**。」
# ⚠ 落点是 `src_time > captured_at`(K8 原文「源时间**不晚于**本地抓取时间」)——
# 源时间**早于**抓取时刻是正常的,⛔ 别把它也判成偏差。
# ⚠ 上产后第一周每早记 `src_time − captured_at` 的分布,出现误判**拿数据来问用户**;
# ⛔ build 不许自己定 1s / 3s / 5s。
QS_FRESH = "fresh"
QS_WRONG_TRADE_DATE = "wrong_trade_date"
QS_BEFORE_FINAL_AUCTION = "before_final_auction"
QS_FUTURE_TIMESTAMP = "future_timestamp"
QS_TIMESTAMP_UNPARSEABLE = "timestamp_unparseable"
QS_REQUIRED_FIELD_MISSING = "required_field_missing"
QS_MALFORMED = "malformed"
QUOTE_STATUSES = (
    QS_FRESH, QS_WRONG_TRADE_DATE, QS_BEFORE_FINAL_AUCTION, QS_FUTURE_TIMESTAMP,
    QS_TIMESTAMP_UNPARSEABLE, QS_REQUIRED_FIELD_MISSING, QS_MALFORMED,
)

# ── 🔴 V2.4.0 P2.2:双源核验后**这一只代码**的可用状态(K8 §二十「主备源」逐条)——
#   · `fresh`        至少一源通过**全部**七项校验,且两源没有结论性冲突;
#   · `degraded`     读数**可以用**、但七项里有非致命项没过(目前只有一种:源还没
#                    发出开盘价)—— 读数照出、样本域降级,⛔ 不当"没有";
#   · `insufficient` 双源(或唯一源)都踩了**致命项** → 这一格**没有可用读数**;
#   · `conflict`     双源都新鲜、但出现**结论性冲突** → ⛔ 不能高置信输出。
#
# 🔴 **为什么要有 `degraded` 这一档**(施工实打,⛔ 别"简化"回三态):K8 第 ⑤ 项把
# 「开盘价 / 现价 / 前收盘价」并列成"必要字段",但三者在本系统里的**后果完全不同** ——
# 现价与前收盘是竞价涨跌幅的分子分母(缺了整条读数都算不出),而开盘价只被
# 「有没有触发 D0 失效位」这一项用,而那一项**本来就有自己的第三态**
# (`UNDET_NO_OPEN_PRICE`,V2.3.3 复审 🔴-1 立的)。把三者一视同仁地判成"整条不可用",
# 等于用一个新病(把好的价量额一起扔掉)换掉一个老病。
#
# ⚠ `degraded` / `insufficient` 与 `DQ_*` 字面相同是刻意的(它们就是那一格的三态),
# 但**语义层级不同**:这几个是逐票的,`DQ_*` 是样本域的。
QF_FRESH = QS_FRESH
QF_DEGRADED = DQ_DEGRADED
QF_INSUFFICIENT = DQ_INSUFFICIENT
QF_CONFLICT = "conflict"
QUOTE_FRESHNESS_CODES = (QF_FRESH, QF_DEGRADED, QF_INSUFFICIENT, QF_CONFLICT)

#: 双源**结论性冲突**的四类(K8 §二十 逐字,🔴 **⛔ 零新百分比阈值**)。
#: ⚠ 「方向相反」用 `> 0` / `< 0` 的**自然分界**(带 `_EPS` 浮点容差),⛔ 不是阈值;
#: 「触发 / 不触发」「进 / 不进区间」都拿 **D0 卡上冻结的价位**比,同样零新数。
CONFLICT_DIRECTION_OPPOSITE = "direction_opposite"          # ① 两源涨跌方向相反
CONFLICT_INVALIDATION_DISAGREE = "invalidation_disagree"    # ② 一源触发 D0 失效位、另一源不触发
CONFLICT_PLAN_ZONE_DISAGREE = "plan_zone_disagree"          # ③ 一源进入/突破预案区间、另一源不进入
CONFLICT_IDENTITY_MISMATCH = "identity_mismatch"            # ④ 代码 / 前收 / 交易日不一致
CONFLICT_CODES = (
    CONFLICT_DIRECTION_OPPOSITE, CONFLICT_INVALIDATION_DISAGREE,
    CONFLICT_PLAN_ZONE_DISAGREE, CONFLICT_IDENTITY_MISMATCH,
)

#: 主源(新浪)/ 备源(腾讯)的角色码。⚠ `Quote.source` 存的是**源的名字**
#: (`sina` / `tencent`),这两个码存的是**它在本次核验里的角色** —— 两者不是一回事。
QUOTE_ROLE_PRIMARY = "primary"
QUOTE_ROLE_BACKUP = "backup"

# ── 🔴 「没判」的原因码(逐票 `hit_invalidation` / `gap_up_deviation` 的**第三态**)——
# 这两项是**三态**:`True`(命中)/ `False`(看过了、没命中)/ `None`(**没判**)。
# 🔴 ⛔ **`None` 绝不许折成 `False`「没问题」** —— 那是把「一个字都没核对」讲成
# 「核对过了、没事」(同 `precall` 的 `member_ex_rights`、⑧-E 的 `anchor_unconfirmed`、
# 夹逼四态的 `rejected_no_close` 一脉相承)。
# ⚠ **`None` 必须同时给一个可查的原因码**:光有 `None` 没有原因,读者还是只能猜。
UNDET_NO_QUOTE = "no_quote"                    # 这只票本次一条报价都没抓到
UNDET_NO_MEMBER_SCRIPT = "no_member_script"    # D0 卡上没有这只成员的冻结剧本(有篮无卡 / 漏了这只)
UNDET_ANCHOR_STALE = "anchor_stale"            # 冻结锚今日失效(疑似除权除息)→ 那是**错的比较**
UNDET_NO_STOP_LINE = "no_stop_line"            # 卡上没冻结 `close_below_stop_line`
UNDET_NO_REF_CLOSE = "no_ref_close"            # 卡上没冻结 `ref_close`(D0 收盘锚)
UNDET_NO_OPEN_PRICE = "no_open_price"          # 行情源还没发开盘价(`quote.open <= 0`)
#: 🔴 V2.4.0 P2.1:抓到了读数,但它**没通过七项校验**(过期 / 时间戳解不出 / 必要字段
#: 无效 …)→ **不拿它去判失效位**。拿上一交易日的收盘价跟 D0 冻结止损线比,必然
#: 得到一条**看起来很像真的假警报** —— 那比不判更糟。⛔ 与 `no_quote` 分开:
#: 「没抓到」和「抓到了一份不能用的」排障方向完全相反。
UNDET_QUOTE_INVALID = "quote_invalid"
UNDETERMINED_CODES = (
    UNDET_NO_QUOTE, UNDET_NO_MEMBER_SCRIPT, UNDET_ANCHOR_STALE,
    UNDET_NO_STOP_LINE, UNDET_NO_REF_CLOSE, UNDET_NO_OPEN_PRICE, UNDET_QUOTE_INVALID,
)

# ── 🔴 相对强弱的两条**独立路径**(用户裁定 P3-70,2026-08-12)————————————————
# 裁定原文:「给『市场指数』建立独立路径,`rel_to_index` 与 `rel_to_sector` **分开计算,
# 禁止同源同值**」+「⛔ 禁止使用市场指数代替板块基准」+「『三支指数等权平均』正式停用」。
#
# `rel_to_sector` 的基准来源码(**如实落下走的是哪条路径**,⛔ 不许假装走了 ①):
SECTOR_BENCH_SECTOR_INDEX = "sector_index"    # ① D0 主要驱动对应的**板块指数**
SECTOR_BENCH_PEER_MEDIAN = "peer_median"      # ② ≥3 只有效板块对照股竞价涨跌幅的**中位数**
SECTOR_BENCH_UNAVAILABLE = "unavailable"      # ③ 对照不足 → `null` + 原因码
SECTOR_BENCH_SOURCES = (
    SECTOR_BENCH_SECTOR_INDEX, SECTOR_BENCH_PEER_MEDIAN, SECTOR_BENCH_UNAVAILABLE,
)

#: 🔴 **用户裁定的数**(P3-70 ②,2026-08-12):板块对照股**至少 3 只**才允许取中位数。
#: ⛔ 这是拍板值,不是工程侧翻译的首版 —— 改它要重新拍板。
SECTOR_PEER_MIN = 3

# ── 相对强弱「算不出」的原因码(🔴 `None` 必配一个可查原因,⛔ 不留光秃秃的 null)——
# 🔴 「没有」≠「不满足」≠「持平」:下面每一个码都是一种**不同的**成因,⛔ 不许折平,
#    更 ⛔ 不许把 `None` 渲染成 `0`(那是把「取不到」讲成「跟基准一样」)。
REL_UNDET_NO_MEMBER_GAP = "no_member_gap"        # 这只票自己的竞价涨跌幅就算不出
REL_UNDET_BOARD_EXCLUDED = "board_excluded"      # 科创板:K8 §三「排除科创板股票」→ ⛔ 不 fallback
REL_UNDET_NO_BOARD_META = "no_board_meta"        # 查不到板块归属(元数据缺)
REL_UNDET_NO_INDEX_QUOTE = "no_index_quote"      # 对应市场指数本次没抓到报价
REL_UNDET_NO_INDUSTRY = "no_industry"            # 查不到**这一只**票的行业口径 = 取数域缺这一格
#: 🔴 **整张行业表都没读到**(`load_industry_map` 抛异常 → `industry_of` 全空)——
#: 那是**系统缺席**,不是「这只票在 `stock_basic` 里真没登记行业」(P0-39 的一贯纪律:
#: 系统没跑成 ≠ 实质性判断)。⛔ 不与 `no_industry` 折平(复审 🔵-7)。
REL_UNDET_INDUSTRY_MAP_UNAVAILABLE = "industry_map_unavailable"
REL_UNDET_DATA_INSUFFICIENT = "data_insufficient"   # 看了、有效板块对照股**不足 3 只**(裁定 ③ 原文码)
REL_UNDETERMINED_CODES = (
    REL_UNDET_NO_MEMBER_GAP, REL_UNDET_BOARD_EXCLUDED, REL_UNDET_NO_BOARD_META,
    REL_UNDET_NO_INDEX_QUOTE, REL_UNDET_NO_INDUSTRY, REL_UNDET_INDUSTRY_MAP_UNAVAILABLE,
    REL_UNDET_DATA_INSUFFICIENT,
)

#: 🔵-7 的落点:`collect.py` 在整张行业表读失败时落的那条 report 级 note ——
#: `sector_benchmark_of` 据它把原因码分成两种(⛔ 别在别处再抄一份字面量)。
NOTE_INDUSTRY_MAP_UNAVAILABLE = "industry_map_unavailable"

# ── 开盘价与 D0 冻结预案的一致性五态(全部来自**卡上冻结值**,零阈值)——————————
PLAN_FIT_IN_ZONE = "in_zone"
PLAN_FIT_ABOVE_ZONE_BELOW_CHASE = "above_zone_below_chase"
PLAN_FIT_ABOVE_MAX_CHASE = "above_max_chase"
PLAN_FIT_BELOW_ZONE = "below_zone"
PLAN_FIT_UNKNOWN = "unknown"          # 卡上没给 or 价拿不到(⛔ 不拿 in_zone 冒充)
PLAN_FIT_CODES = (
    PLAN_FIT_IN_ZONE, PLAN_FIT_ABOVE_ZONE_BELOW_CHASE, PLAN_FIT_ABOVE_MAX_CHASE,
    PLAN_FIT_BELOW_ZONE, PLAN_FIT_UNKNOWN,
)

# ── LLM 段状态(照 `basket_card.py` 既有 `llm_stage` 体例)————————————————
LLM_PENDING = "pending"                          # 机械段刚落库,还没轮到 LLM
LLM_OK = "ok"
LLM_PENDING_EXPLANATION = "pending_explanation"  # 9:29 硬截止到了模型还没回(**设计内**)
LLM_NO_PROVIDER = "provider_none"
LLM_PARSE_FAILED = "parse_failed"
LLM_BUDGET_EXHAUSTED = "budget_exhausted"
LLM_CALL_FAILED = "call_failed"                  # 实际落库为 `call_failed:<原因>`

# ── 异常与风险的种类码(小报告第 4 块;🔴 一律发码,中文换算在客户端)————————
RISK_DATA_MISSING = "data_missing"
RISK_SOURCE_CONFLICT = "source_conflict"
#: 🔴 V2.4.0 P2.1:**抓到了、但这条读数没通过七项校验**(过期 / 时间戳解不出 / 必要
#: 字段无效 / 单位转换后为负 …)。⛔ 别并进 `RISK_DATA_MISSING` —— 「没抓到」与
#: 「抓到了一份不能用的」是两种成因,后者尤其危险:**它长得跟正常读数一模一样**
#: (上一交易日的缓存行情被当成今天的竞价结果,正是本版要修的第 ① 个病)。
RISK_QUOTE_INVALID = "quote_invalid"
#: 🔴 V2.4.0 P2.2:主源不可用、**本次改用了备源**(K8 §二十「记录来源降级」)。
#: ⚠ 这不是故障 —— 备源新鲜时用备源正是设计;但 ⛔ 不许**静默**换源。
RISK_SOURCE_DEGRADED = "source_degraded"
RISK_SINGLE_STRONG = "single_strong"
RISK_GAP_UP_DEVIATION = "gap_up_deviation"
RISK_HIT_INVALIDATION = "hit_invalidation"
RISK_ANCHOR_STALE = "anchor_stale"
#: 🔴 「这两项**没判**」的独立风险条(与 `RISK_ANCHOR_STALE` 并列,但成因不同:
#: 那条是「锚失效所以不判」,这条是「卡上没冻结价位 / 开盘价还没发布所以判不了」)。
#: ⛔ 别把它并进 `RISK_DATA_MISSING` —— 票抓到了、价也有,缺的是**判据**。
RISK_INVALIDATION_UNDETERMINED = "invalidation_undetermined"
RISK_AUCTION_VOLUME = "auction_volume_anomaly"
RISK_EVIDENCE_CONFLICT = "evidence_conflict"
RISK_VERDICT_CLAMPED = "verdict_clamped"
RISK_LLM = "llm_unavailable"
#: LLM 在第 4 块自己补的那几条(与机械异常并列,但来源不同,⛔ 别混成一类)。
RISK_LLM_NOTE = "llm_note"

# ── APP 人工观察小纸条(K8.md §二十 **固定文案,逐字照抄**;**单一源**)——————————
# 🔴 客户端 ⛔ 不许自己写这段字:服务端下发 `manualNote` 字符串,客户端原样透传
#    (同 `BASKET_CARD_DISCLAIMER` 既有体例)。
# K8 逐字要求:「它只负责提醒,**不要求点击或录入,不进入系统评分和正式样本,
# 不改变系统结论**」—— 9:20—9:25 的虚拟开盘价路径 / 匹配量变化 / 买卖方未匹配量
# 三项系统当前取不到,统一归「竞价订单流辅助观察」,**不录入、不打分**。
AUCTION_MANUAL_NOTE = (
    "APP 观察:请在同花顺看 9:20—9:25 虚拟开盘价是否稳定、匹配量是否持续增加、"
    "未匹配量偏买方还是卖方。价格稳定且匹配量增加,可视为额外正面;尾段明显撤弱、"
    "价格突然下压或卖方未匹配量明显占优,保持谨慎。"
)

# ── 「竞价强势股只能是代理样本」的诚实披露(§五 ⑨-B-2,**恒发**)——————————————
# 关注池 = 持仓 + T1/T2 篮子成员 + 板块基准指数 + 昨日涨停(V2-⑧-A 起,自选池已退役),
# **不是全市场竞价排行**。这句话必须印在小报告上,⛔ 不许只写在代码注释里。
# ⚠ 契约字段 `proxySampleNote` 的下发在 V2.3.3 批 ⑤(端点)接线;本常量是**文案单一源**。
# 🔴 **⛔ 这段字里不许有 Markdown**(V2.3.3 批 ⑤ 实拍逮到):它作为 `String` 下发,
# 客户端 `Text(String)` **不解析 Markdown** —— `**代理样本**` 会把星号原样印在屏幕上
# (与 V2.3.1 批 2 那两处同一个病)。要强调就用「」。
AUCTION_PROXY_SAMPLE_NOTE = (
    "竞价强势股取自系统的盘中关注池(持仓 + T1/T2 篮子成员 + 板块基准指数 + 昨日涨停),"
    "是「代理样本」,不是全市场竞价排行;它们只解释资金方向,不取得交易资格。"
)

__all__ = [
    "VERDICT_CONFIRM", "VERDICT_NEUTRAL", "VERDICT_VETO", "VERDICT_PENDING_EXPLANATION",
    "VERDICTS",
    "CLAMPED_BY_DATA_QUALITY", "CLAMPED_BY_SINGLE_STRONG",
    "CLAMPED_BY_MISSING_STRONG_EVIDENCE", "CLAMPED_BY_Y1_LOW_WEIGHT", "CLAMP_CODES",
    "DQ_OK", "DQ_DEGRADED", "DQ_INSUFFICIENT",
    "DOMAIN_CRITICAL", "DOMAIN_CONTEXT", "QUALITY_DOMAINS",
    "CRIT_MEMBER_QUOTE", "CRIT_MARKET_BENCHMARK", "CRIT_SECTOR_BENCHMARK",
    "CRIT_FROZEN_ANCHOR", "CRITICAL_COMPONENTS",
    "QS_FRESH", "QS_WRONG_TRADE_DATE", "QS_BEFORE_FINAL_AUCTION", "QS_FUTURE_TIMESTAMP",
    "QS_TIMESTAMP_UNPARSEABLE", "QS_REQUIRED_FIELD_MISSING", "QS_MALFORMED", "QUOTE_STATUSES",
    "QF_FRESH", "QF_DEGRADED", "QF_INSUFFICIENT", "QF_CONFLICT", "QUOTE_FRESHNESS_CODES",
    "CONFLICT_DIRECTION_OPPOSITE", "CONFLICT_INVALIDATION_DISAGREE",
    "CONFLICT_PLAN_ZONE_DISAGREE", "CONFLICT_IDENTITY_MISMATCH", "CONFLICT_CODES",
    "QUOTE_ROLE_PRIMARY", "QUOTE_ROLE_BACKUP",
    "UNDET_NO_QUOTE", "UNDET_NO_MEMBER_SCRIPT", "UNDET_ANCHOR_STALE",
    "UNDET_NO_STOP_LINE", "UNDET_NO_REF_CLOSE", "UNDET_NO_OPEN_PRICE",
    "UNDET_QUOTE_INVALID", "UNDETERMINED_CODES",
    "SECTOR_BENCH_SECTOR_INDEX", "SECTOR_BENCH_PEER_MEDIAN", "SECTOR_BENCH_UNAVAILABLE",
    "SECTOR_BENCH_SOURCES", "SECTOR_PEER_MIN",
    "REL_UNDET_NO_MEMBER_GAP", "REL_UNDET_BOARD_EXCLUDED", "REL_UNDET_NO_BOARD_META",
    "REL_UNDET_NO_INDEX_QUOTE", "REL_UNDET_NO_INDUSTRY", "REL_UNDET_DATA_INSUFFICIENT",
    "REL_UNDET_INDUSTRY_MAP_UNAVAILABLE", "NOTE_INDUSTRY_MAP_UNAVAILABLE",
    "REL_UNDETERMINED_CODES",
    "PLAN_FIT_IN_ZONE", "PLAN_FIT_ABOVE_ZONE_BELOW_CHASE", "PLAN_FIT_ABOVE_MAX_CHASE",
    "PLAN_FIT_BELOW_ZONE", "PLAN_FIT_UNKNOWN", "PLAN_FIT_CODES",
    "LLM_PENDING", "LLM_OK", "LLM_PENDING_EXPLANATION", "LLM_NO_PROVIDER",
    "LLM_PARSE_FAILED", "LLM_BUDGET_EXHAUSTED", "LLM_CALL_FAILED",
    "RISK_DATA_MISSING", "RISK_SOURCE_CONFLICT", "RISK_QUOTE_INVALID",
    "RISK_SOURCE_DEGRADED", "RISK_SINGLE_STRONG",
    "RISK_GAP_UP_DEVIATION", "RISK_HIT_INVALIDATION", "RISK_ANCHOR_STALE",
    "RISK_INVALIDATION_UNDETERMINED",
    "RISK_AUCTION_VOLUME", "RISK_EVIDENCE_CONFLICT", "RISK_VERDICT_CLAMPED", "RISK_LLM", "RISK_LLM_NOTE",
    "AUCTION_MANUAL_NOTE", "AUCTION_PROXY_SAMPLE_NOTE",
]
