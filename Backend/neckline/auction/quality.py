"""竞价行情的**七项校验**与**有界双源核验**(V2.4.0 P2.1 / P2.2,K8.md §二十)。

本模块回答的问题只有一个:

    **这条读数,能不能当作「今天 9:25 那一刻的集合竞价结果」来用?**

⛔ 它**不回答任何市场问题**(强弱 / 协同 / 掉队都在 `mech.py` 与 LLM 那边),
⛔ 不做 IO、不碰 DB、不调 LLM —— 全是纯函数,`(读数, 交易日, 抓取时刻) → 判定`。

**为什么单起一个文件**:`collect.py` 的身份是「组清单 → 拉一次价 → 冻结,⛔ 不判定」,
而七项校验确实是一种判定;`mech.py` 的身份是「机械读数,⛔ 不出结论」,校验又不是读数。
把它塞进任何一个都要改那两句身份声明 —— 与其如此,不如让它自己占一格。

────────────────────────────────────────────────────────────────────────────
🔴 **本版要修的两个病**(审计规格 P2 目标 ①③)

  ① **上一交易日的缓存行情可能被当作今天的竞价数据** —— 它长得跟正常读数**一模一样**,
     价、量、额都齐全,只有 `ts` 里那个日期不对。V2.3.3 从来没有人看过那个 `ts`。
  ③ **「跨源冲突为空」其实从未交叉核验过** —— `get_quotes()` 是「主源失败**才**降备源」,
     结构上不存在第二个读数可以跟第一个打架,所以那一栏恒空。
     🔴 **V2.4.0 起 `get_quotes_dual()` 真的拉两源**(§五 P2.2 明写此条**推翻**
     V2.3.3 ⑨-B-3「⛔ 不加第二次网络请求」的旧取舍,§七 P4-66 随之改判)。

────────────────────────────────────────────────────────────────────────────
🔴 **零新阈值**(§五 〇b-1;审计规格 P2.1 逐字「不得发明任意『5 分钟新鲜度』之类的新阈值」)

  · 时间判据只用 K8 已规定的**交易日**与 **9:25 / 9:26—9:29** 边界;
  · 冲突判据的四类全是**结论性**的(方向相反 / 触发与否 / 进不进区间 / 身份不一致),
    ⛔ 一个百分比都没有;
  · 浮点容差 `_EPS` 是**二进制表示误差**的容差(`CLAUDE.md`「纪律阈值比较一律加 _EPS」
    的同一条),⛔ 不是判据阈值。

🔴 **`future_timestamp` 走零容差 —— 这是 2026-08-12 的用户裁定 #2,⛔ 不是工程侧默认值**
(出处:`PROJECT_PLAN.md` §五 D 节)。用户原话:

    「竞价时间戳先执行零容差:源时间与本机存在任何偏差即降级为中性。
      若实盘出现误判,再由我确认容差秒数,**施工 Agent 不得自行设定**。」

⚠ 落点是 `src_time > captured_at`(K8 原文「源时间**不晚于**本地抓取时间」)——
**源时间早于抓取时刻是正常的**,⛔ 别把它也判成偏差。
⚠ 上产后第一周每早记 `src_time − captured_at` 的分布;出现误判**拿数据去问用户要秒数**,
⛔ build 不许自己定 1s / 3s / 5s。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from neckline.auction import (
    CONFLICT_DIRECTION_OPPOSITE,
    CONFLICT_IDENTITY_MISMATCH,
    CONFLICT_INVALIDATION_DISAGREE,
    CONFLICT_PLAN_ZONE_DISAGREE,
    QF_CONFLICT,
    QF_DEGRADED,
    QF_FRESH,
    QF_INSUFFICIENT,
    QS_BEFORE_FINAL_AUCTION,
    QS_FRESH,
    QS_FUTURE_TIMESTAMP,
    QS_MALFORMED,
    QS_REQUIRED_FIELD_MISSING,
    QS_TIMESTAMP_UNPARSEABLE,
    QS_WRONG_TRADE_DATE,
    QUOTE_ROLE_BACKUP,
    QUOTE_ROLE_PRIMARY,
)
from neckline.sentinel.capture import AUCTION_CAPTURE_START
from neckline.data.realtime import DualQuote, Quote

logger = logging.getLogger(__name__)

#: 浮点容差(`CLAUDE.md`「纪律阈值比较一律加 `_EPS`」)。⛔ **不是判据阈值**。
_EPS = 1e-9


def _as_market_naive(dt: datetime) -> datetime:
    """把 `captured_at` 归一成**北京时间的 naive** 值(复审 🟡-8)。

    🔴 **为什么必须归一**:`parse_quote_ts` 解出来的源时间恒 naive(源串里没有时区),
    而调用方给的 `captured_at` **两种都可能** —— 生产路径 `api/app.py:305` 走
    `datetime.now()`(naive),但同一个 `app.py` 另外三处用的是 `datetime.now(CN_TZ)`
    (aware,本仓房规就是那一套)。裸 `src > captured_at` 撞上 aware 值会抛
    `TypeError: can't compare offset-naive and offset-aware datetimes`,而
    `collect_auction_snapshot` 的 `resolve_dual` 循环**没有包 try/except** →
    异常一路逃到 lifespan 的兜底 `except` → **每天早晨静默零落库**。

    ⚠ **aware 值按 `CN_TZ` 换算后再剥时区**,⛔ 不是直接 `replace(tzinfo=None)`:
    直接剥会把一个 UTC 时刻当成北京时刻用(差 8 小时 → 整份快照全判 `future_timestamp`)。
    时区单一源 = `neckline.calendar.CN_TZ`(`CLAUDE.md`:⛔ 不在新模块里再写一份 `+8`)。
    """
    if dt.tzinfo is None:
        return dt
    from neckline.calendar import CN_TZ

    return dt.astimezone(CN_TZ).replace(tzinfo=None)

#: 集合竞价结果的**最早可接受源时间** = 9:25(K8 §二十 给的边界,⛔ 不是本项目发明的数)。
#: 🔴 **单一源复用 `sentinel/capture.AUCTION_CAPTURE_START`** —— 同一个 9:25 撮合时刻,
#: ⛔ 不在本包再写一份 `time(9, 25)`(那就是第二份事实源)。
AUCTION_RESULT_TIME_START: time = AUCTION_CAPTURE_START

#: `Quote.ts` 归一后的格式。两源在 `data/realtime.py` 里已经对齐成同一个形状
#: (新浪 `parts[30] + " " + parts[31]`;腾讯 `_fmt_tencent_ts` 把 14 位数字串拆开)。
#: ⚠ 第二个是**防御性**的次要形状(秒位缺失),⛔ 别再往下加更宽松的:解析越宽容,
#: 「时间戳解不出」这个真信号就越容易被吞掉。
_TS_FORMATS: Tuple[str, ...] = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")

# ── 七项校验各自的失败码(**全部留痕**,不是只留胜出的那一个)———————————————
ERR_CODE_MISMATCH = "code_mismatch"                       # ① 代码与市场映射
ERR_WRONG_TRADE_DATE = "wrong_trade_date"                 # ② 源日期 == D1
ERR_FUTURE_TIMESTAMP = "future_timestamp"                 # ③ 源时间不晚于 captured_at(零容差)
ERR_BEFORE_FINAL_AUCTION = "before_final_auction"         # ④ 落在 [09:25, captured_at]
ERR_TIMESTAMP_UNPARSEABLE = "timestamp_unparseable"       # ②③④ 的前置:ts 解得出
ERR_REQUIRED_FIELD_MISSING = "required_field_missing"     # ⑤ price / pre_close 有效(**致命**)
ERR_OPEN_PRICE_MISSING = "open_price_missing"             # ⑤ 源还没发开盘价(**非致命**)
ERR_PRICE_RELATION = "price_relation_inconsistent"        # ⑥ 涨跌幅与价格关系一致
ERR_NEGATIVE_VOLUME = "negative_volume"                   # ⑦ 单位转换后非负
ERR_NEGATIVE_AMOUNT = "negative_amount"                   # ⑦ 同上
VALIDATION_ERROR_CODES: Tuple[str, ...] = (
    ERR_CODE_MISMATCH, ERR_WRONG_TRADE_DATE, ERR_FUTURE_TIMESTAMP,
    ERR_BEFORE_FINAL_AUCTION, ERR_TIMESTAMP_UNPARSEABLE, ERR_REQUIRED_FIELD_MISSING,
    ERR_OPEN_PRICE_MISSING, ERR_PRICE_RELATION, ERR_NEGATIVE_VOLUME, ERR_NEGATIVE_AMOUNT,
)

#: 🔴 **致命失败** = 踩中任何一条,这条读数就**不能当今天的竞价结果用**。
#: ⚠ 唯一的**非致命**项是 `open_price_missing`:开盘价只被「有没有触发 D0 失效位」
#: 用,而那一项本来就有自己的第三态(`UNDET_NO_OPEN_PRICE`)—— 把它算致命,等于
#: 因为一个用不上的字段把好端端的价 / 量 / 额一起扔掉。见 `QF_DEGRADED` 那段说明。
_FATAL_ERRORS: frozenset = frozenset(
    e for e in VALIDATION_ERROR_CODES if e != ERR_OPEN_PRICE_MISSING
)

#: 失败码 → 七态状态。🔴 **次序就是优先级**(`status` 取第一个命中的):
#: 先说「这条记录本身是坏的」,再说「必要字段没有」,最后才说时间。
#: ⚠ 一条读数可以同时踩中好几项 —— `errors` 里**全都留着**,`status` 只是主因。
_STATUS_PRECEDENCE: Tuple[Tuple[str, str], ...] = (
    (ERR_CODE_MISMATCH, QS_MALFORMED),
    (ERR_PRICE_RELATION, QS_MALFORMED),
    (ERR_NEGATIVE_VOLUME, QS_MALFORMED),
    (ERR_NEGATIVE_AMOUNT, QS_MALFORMED),
    (ERR_REQUIRED_FIELD_MISSING, QS_REQUIRED_FIELD_MISSING),
    (ERR_OPEN_PRICE_MISSING, QS_REQUIRED_FIELD_MISSING),
    (ERR_TIMESTAMP_UNPARSEABLE, QS_TIMESTAMP_UNPARSEABLE),
    (ERR_WRONG_TRADE_DATE, QS_WRONG_TRADE_DATE),
    (ERR_FUTURE_TIMESTAMP, QS_FUTURE_TIMESTAMP),
    (ERR_BEFORE_FINAL_AUCTION, QS_BEFORE_FINAL_AUCTION),
)


def parse_quote_ts(raw: Any) -> Optional[datetime]:
    """`Quote.ts` → `datetime`;解不出 → `None`(= `timestamp_unparseable`)。

    🔴 **原始字符串继续保留**(§五 P2.1 逐字):本函数只**派生**,`Quote.ts` 一个字不改。
    ⚠ 返回 naive `datetime`,按**北京时间**读 —— 两个免费源发的都是本地交易时刻,
    与 `captured_at`(`CN_TZ` 唯一源)同一条时间轴(`CLAUDE.md`「时间轴与章程判定」
    那条纪律:naive 的约定必须逐处写死,⛔ 不留给读者猜)。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _digits(code: Any) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class QuoteCheck:
    """**一源**对**一只代码**的读数 + 它的七项校验结果。

    🔴 `price` / `pre_close` / `open` / `volume` / `amount` 是**原始读数留痕**
    (K8 §二十:「两个来源的原始读数全部留存」)—— ⛔ 不许只存胜出的那一个。
    """

    ts_code: str
    role: str                       # primary(新浪)| backup(腾讯)
    source: str                     # `Quote.source`:sina | tencent
    status: str                     # `QUOTE_STATUSES` 之一
    errors: Tuple[str, ...] = ()
    ts_raw: str = ""                # 源自带的原始时刻串(⛔ 一个字不改)
    ts_parsed: Optional[str] = None  # 归一后的 `YYYY-MM-DD HH:MM:SS`;解不出 → None
    price: Optional[float] = None
    pre_close: Optional[float] = None
    open: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None

    @property
    def ok(self) -> bool:
        """七项**全过**。"""
        return self.status == QS_FRESH

    @property
    def usable(self) -> bool:
        """没踩致命项 → 这条读数**可以当今天的竞价结果用**(哪怕缺开盘价)。"""
        return not (set(self.errors) & _FATAL_ERRORS)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_code": self.ts_code, "role": self.role, "source": self.source,
            "status": self.status, "errors": list(self.errors),
            "ts_raw": self.ts_raw, "ts_parsed": self.ts_parsed,
            "price": self.price, "pre_close": self.pre_close, "open": self.open,
            "volume": self.volume, "amount": self.amount,
        }


def validate_quote(
    quote: Optional[Quote],
    *,
    code: str,
    role: str,
    trade_date: date,
    captured_at: datetime,
) -> Optional[QuoteCheck]:
    """**七项校验**(K8 §二十 逐字)。`quote is None`(这一源没拉到)→ `None`。

    ⚠ 「没拉到」与「拉到了但不合格」是两件事:前者返回 `None`(调用方按"这一源缺席"
    处理),后者返回一个 `status != fresh` 的 `QuoteCheck` —— ⛔ 不许折平。

    七项与落点:
      ① 代码与市场映射正确        → `code_mismatch`(裸数字位不一致 = 拿回来的是**另一只票**)
      ② 源日期 == D1 交易日        → `wrong_trade_date`(**本版要修的第 ① 个病**)
      ③ 源时间不晚于 `captured_at` → `future_timestamp`(🔴 **零容差,用户裁定 #2**)
      ④ 源时间落在可接受区间       → `before_final_auction`(早于 9:25 = 不是最终撮合结果)
      ⑤ `open/price/pre_close` 有效 → `required_field_missing`
      ⑥ 涨跌幅与价格关系一致       → `price_relation_inconsistent`
      ⑦ 单位转换后 volume/amount 非异常负数 → `negative_volume` / `negative_amount`

    🔴 **第 ⑥ 项的诚实边界(如实登记,⛔ 别当遗漏)**:两个免费源里只有腾讯自带一个
    「涨跌幅」字段,而 `Quote` **从来没有携带过它**(`data/realtime.py` 只装"源里
    直接给的市场数据",涨跌幅一律由 `gap_pct_of(price, pre_close)` 派生)。所以本项
    落在**派生式所依赖的那组价格关系**上:`low ≤ open/price ≤ high`(两端都 >0 时才比)。
    ⛔ 之所以不去把源的涨跌幅字段抓进来对拍:那要给 `Quote` 加一个字段(牵动
    `sentinel/**` 一整片)、并且需要一个"两位小数四舍五入"的比较容差 —— 后者正好踩
    §五 〇b-1「⛔ 不发明新阈值」。要补这一项,是下一次拍板的事。
    """
    if quote is None:
        return None
    # 复审 🟡-8:aware `captured_at` 会让下面那个裸 `>` 抛 `TypeError`,而调用链上
    # 没人接得住它 → 整层静默零落库。边界处归一,⛔ 别把这件事推给每个调用方。
    captured_at = _as_market_naive(captured_at)
    errors: List[str] = []

    price = _f(getattr(quote, "price", None))
    pre_close = _f(getattr(quote, "pre_close", None))
    open_ = _f(getattr(quote, "open", None))
    high = _f(getattr(quote, "high", None))
    low = _f(getattr(quote, "low", None))
    volume = _f(getattr(quote, "volume", None))
    amount = _f(getattr(quote, "amount", None))
    ts_raw = str(getattr(quote, "ts", "") or "")

    # ① 代码与市场映射:拿回来的必须**就是**我们要的那一只。
    #    ⚠ 判裸数字位而不是带前缀的符号:`Quote.code` 存的是 `_bare_code(symbol)`。
    #    这一项与「指数被前缀启发式拉成另一只股票」(`to_symbol` 那条老坑)同源 ——
    #    真出现时,读数会**完全正常**,只有代码对不上。
    got_code = _digits(getattr(quote, "code", ""))
    if got_code and _digits(code) and got_code != _digits(code):
        errors.append(ERR_CODE_MISMATCH)

    # ⑤ 必要字段有效(⛔ `0` 不是"有效的 0",是"没发")。**拆两档**,理由见
    #    `auction/__init__.py::QF_DEGRADED` 那一段:现价 / 前收盘是竞价涨跌幅的
    #    分子分母(缺了整条读数都算不出)= 致命;开盘价只被失效位判定用,而那一项
    #    本来就有自己的第三态 = 非致命。
    if not (price and price > 0) or not (pre_close and pre_close > 0):
        errors.append(ERR_REQUIRED_FIELD_MISSING)
    if not (open_ and open_ > 0):
        errors.append(ERR_OPEN_PRICE_MISSING)

    # ⑥ 价格关系一致(见 docstring 的诚实边界)。两端都 > 0 才比 —— 9:25 之前
    #    某些源的 high/low 会是 0(还没有区间),那是"没有",不是"违反"。
    if high and low and high > 0 and low > 0:
        if high + _EPS < low:
            errors.append(ERR_PRICE_RELATION)
        else:
            for v in (price, open_):
                if v and v > 0 and not (low - _EPS <= v <= high + _EPS):
                    errors.append(ERR_PRICE_RELATION)
                    break

    # ⑦ 单位转换后非异常负数(0 是合法的 —— 竞价可以一手没成交)。
    if volume is not None and volume < -_EPS:
        errors.append(ERR_NEGATIVE_VOLUME)
    if amount is not None and amount < -_EPS:
        errors.append(ERR_NEGATIVE_AMOUNT)

    # ②③④ 时间三项。前置:ts 得解得出。
    src = parse_quote_ts(ts_raw)
    if src is None:
        errors.append(ERR_TIMESTAMP_UNPARSEABLE)
    else:
        if src.date() != trade_date:
            errors.append(ERR_WRONG_TRADE_DATE)
        # 🔴 **零容差**(用户裁定 #2):严格 `>`。源时间**早于**抓取时刻是正常的。
        if src > captured_at:
            errors.append(ERR_FUTURE_TIMESTAMP)
        if src.time() < AUCTION_RESULT_TIME_START:
            errors.append(ERR_BEFORE_FINAL_AUCTION)

    status = QS_FRESH
    for err, st in _STATUS_PRECEDENCE:
        if err in errors:
            status = st
            break
    return QuoteCheck(
        ts_code=code, role=role, source=str(getattr(quote, "source", "") or "unknown"),
        status=status, errors=tuple(errors), ts_raw=ts_raw,
        ts_parsed=(src.strftime("%Y-%m-%d %H:%M:%S") if src is not None else None),
        price=price, pre_close=pre_close, open=open_, volume=volume, amount=amount,
    )


# ══════════════════════════════════════════════════════════════════════════
# 结论性冲突(四类,🔴 零新百分比阈值 —— K8 §二十:「冲突判定不新设百分比阈值」)
# ══════════════════════════════════════════════════════════════════════════

def _gap(q: Quote) -> Optional[float]:
    """竞价涨跌幅。⚠ 公式的唯一源在 `collect.gap_pct_of`,这里**只是调它**
    (⛔ 不在本模块抄第二份 —— 那正是两处容差各自漂移的复发路径)。"""
    from neckline.auction.collect import gap_pct_of

    return gap_pct_of(getattr(q, "price", None), getattr(q, "pre_close", None))


def detect_identity_conflict(primary: Optional[Quote], backup: Optional[Quote]) -> bool:
    """④ **证券代码 / 前收盘 / 交易日不一致**(K8 §二十 逐字)。

    这一类与另外三类不同:它说的不是「两源对同一件事看法不同」,而是
    **「这两条读数根本不是同一只票 / 同一天的」** —— 出现它时,其余三类的比较
    全部失去意义,故调用方把它排在最前面。
    """
    if primary is None or backup is None:
        return False
    if _digits(getattr(primary, "code", "")) != _digits(getattr(backup, "code", "")):
        return True
    pc_a, pc_b = _f(getattr(primary, "pre_close", None)), _f(getattr(backup, "pre_close", None))
    if pc_a is not None and pc_b is not None and abs(pc_a - pc_b) > _EPS:
        return True
    ta, tb = parse_quote_ts(getattr(primary, "ts", "")), parse_quote_ts(getattr(backup, "ts", ""))
    if ta is not None and tb is not None and ta.date() != tb.date():
        return True
    return False


def detect_conflict(
    primary: Optional[Quote],
    backup: Optional[Quote],
    *,
    invalidation_of: Optional[Callable[[Quote], Optional[bool]]] = None,
    plan_entered_of: Optional[Callable[[Quote], Optional[bool]]] = None,
) -> Optional[str]:
    """两源的**结论性冲突**(返回 `CONFLICT_*` 之一;无冲突 → `None`)。

    次序写死:④ 身份 → ② 失效位 → ③ 预案区间 → ① 方向。只报**第一个**命中的
    (同 `clamp_verdict` 的 `clamped_by` 单值体例)。

    🔴 **两个可选钩子都是三态**(`True` 触发 / `False` 看过了没触发 / `None` **判不了**):
    ⛔ **只有两侧都非 `None` 且不相等**才算冲突 —— 把 `None` 当 `False` 会把
    「一边判不了」讲成「两边看法不同」(`CLAUDE.md`「三态字段:`is not None` 会把它们
    折平」那条纪律的同一个坑)。钩子由 `mech.py` 提供(它才有 D0 冻结卡)。

    ⚠ 钩子为 `None`(调用方压根没给)= **本次不做这一类比较**,⛔ 不是"没冲突"。
    """
    if primary is None or backup is None:
        return None                     # 只有一源 → 没有第二个读数可以打架(⛔ 不是"已核对")
    if detect_identity_conflict(primary, backup):
        return CONFLICT_IDENTITY_MISMATCH
    if invalidation_of is not None:
        a, b = invalidation_of(primary), invalidation_of(backup)
        if a is not None and b is not None and a != b:
            return CONFLICT_INVALIDATION_DISAGREE
    if plan_entered_of is not None:
        a, b = plan_entered_of(primary), plan_entered_of(backup)
        if a is not None and b is not None and a != b:
            return CONFLICT_PLAN_ZONE_DISAGREE
    ga, gb = _gap(primary), _gap(backup)
    if ga is not None and gb is not None:
        # ① 方向相反 —— `> 0` / `< 0` 是涨跌的**自然分界**(`_EPS` 只是浮点容差),
        #    ⛔ 不是阈值;⚠ 一边平盘一边涨**不算**方向相反(那是幅度差,不是结论差)。
        if (ga > _EPS and gb < -_EPS) or (ga < -_EPS and gb > _EPS):
            return CONFLICT_DIRECTION_OPPOSITE
    return None


# ══════════════════════════════════════════════════════════════════════════
# 双源归一:谁可用、要不要记来源降级、有没有冲突
# ══════════════════════════════════════════════════════════════════════════

def _is_cross_verified(checks: Sequence[QuoteCheck]) -> bool:
    """这一格**到底有没有真的做过两源对拍**(复审 🔴-2 的判别式,单一源就是本函数)。

    🔴 定义 = **`detect_conflict` 实际跑起来的那个条件**:两源都返回了读数(所以
    `checks` 恰好两条),**且两侧七项都过**。`resolve_dual` 里那个 `if` 直接调它,
    ⛔ 两处不许各写一遍 —— 那正是"守门停在屏幕前一层"的复发路径。

    ⛔ **「只有一源」「有一源不合格」都不算核验过**:那时 `conflict` 恒 `None`,
    而 `None` 的含义是「**没得比**」,不是「比过了没冲突」(`detect_conflict` 的
    docstring 逐字写着这句话)。把它们讲成"已交叉核验"就是把「没判」折成「没问题」——
    本项目连续三版栽在同一族病上。
    """
    return len(checks) == 2 and all(c.ok for c in checks)


@dataclass(frozen=True)
class QuoteQuality:
    """一只代码经**双源核验**后的完整账。🔴 **两源原始读数全在 `checks` 里**。"""

    ts_code: str
    freshness: str                              # fresh | insufficient | conflict
    chosen_role: Optional[str] = None           # primary | backup | None(两源都没读数)
    chosen_source: Optional[str] = None         # sina | tencent | None
    #: 主源不可用、改用备源 —— K8 §二十「记录来源降级」。⛔ 不许静默换源。
    source_degraded: bool = False
    conflict: Optional[str] = None              # `CONFLICT_*`
    checks: Tuple[QuoteCheck, ...] = ()

    @property
    def usable(self) -> bool:
        """这一格**有没有可用读数**。⚠ `conflict` 算可用(读数本身合格),
        但它会把所在样本域压成 `degraded` → 闸 1 夹成中性(「不能高置信输出」)。"""
        return self.freshness != QF_INSUFFICIENT

    @property
    def cross_verified(self) -> bool:
        """🔴 **本次真的做过两源对拍**(复审 🔴-2)。判别式单一源 = `_is_cross_verified`,
        与 `resolve_dual` 里触发 `detect_conflict` 的那个 `if` **是同一个函数**。

        消费方读它来回答「跨源冲突为空,到底是**比过了没冲突**,还是**压根没得比**」——
        `conflict is None` 答不了这个问题(两种情况下它都是 `None`)。
        """
        return _is_cross_verified(self.checks)

    @property
    def status(self) -> str:
        """胜出那一侧的七项校验状态;两源都不合格 → 主源那一侧的状态(没有主源就取备源)。

        🔴 **一条读数都没有时返回 `""` = 「本次没记这一位」**(复审 🟡-7)。
        ⛔ 旧写法兜底成 `timestamp_unparseable`,那是在**报告一次证明没发生过的校验失败**
        (压根没有时间戳可解析),而这一位会顺着 `mech.py` 进 `members_json` ——
        那是 `INSERT OR IGNORE` 的冻结审计行,**永不重写**,等于把一句假话写死在账上。
        「没拉到」与「拉到了但不合格」必须分得开(本模块 `validate_quote` 的
        docstring 逐字写着这条,⛔ 不许折平)——客户端对 `""` 已有正确标签「本次未记录」。
        """
        for c in self.checks:
            if c.role == self.chosen_role:
                return c.status
        return self.checks[0].status if self.checks else ""

    @property
    def src_ts(self) -> Optional[str]:
        for c in self.checks:
            if c.role == self.chosen_role:
                return c.ts_parsed or (c.ts_raw or None)
        return None

    @property
    def errors(self) -> Tuple[str, ...]:
        """⚠ **两源的错误码并集**(⛔ 不只报胜出那一侧):读者要知道备源当时也怎么了。"""
        seen: List[str] = []
        for c in self.checks:
            for e in c.errors:
                if e not in seen:
                    seen.append(e)
        return tuple(seen)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_code": self.ts_code, "freshness": self.freshness,
            "chosen_role": self.chosen_role, "chosen_source": self.chosen_source,
            "source_degraded": bool(self.source_degraded), "conflict": self.conflict,
            # 🔴 复审 🔴-2:落库留痕,展示层据此决定「说不说『已交叉核验』」。
            # ⚠ 老行(v2.4.0 复审整改之前落的)没有这一键 → 消费方读不到时**当 False**
            #   (保守方向:不声称核验过),⛔ 不许在读侧重新推一遍。
            "cross_verified": bool(self.cross_verified),
            "status": self.status, "errors": list(self.errors),
            "checks": [c.to_dict() for c in self.checks],
        }


def resolve_dual(
    code: str,
    dual: DualQuote,
    *,
    trade_date: date,
    captured_at: datetime,
    invalidation_of: Optional[Callable[[Quote], Optional[bool]]] = None,
    plan_entered_of: Optional[Callable[[Quote], Optional[bool]]] = None,
) -> Tuple[Optional[Quote], QuoteQuality]:
    """双源归一 → `(选用的读数, 这一格的完整账)`(K8 §二十「主备源」四条逐字):

      · 主源新鲜                → 用主源;
      · 主源过期 / 无效 + 备源新鲜 → **用备源** + `source_degraded=True`(记来源降级);
      · 双源都不合格            → `freshness=insufficient`;**读数仍然返回**
        (留给逐票行如实展示 + 留痕),但样本域会把它算作"这一格没有可用读数";
      · 双源都新鲜但结论性冲突   → `freshness=conflict`。

    🔴 **⛔ 不许把不合格的读数悄悄扔掉**:扔掉 = 那一格看起来像"没抓到",而真相是
    "抓到了一份昨天的" —— 两者的排障方向完全相反。
    """
    primary, backup = dual.primary, dual.backup
    cp = validate_quote(primary, code=code, role=QUOTE_ROLE_PRIMARY,
                        trade_date=trade_date, captured_at=captured_at)
    cb = validate_quote(backup, code=code, role=QUOTE_ROLE_BACKUP,
                        trade_date=trade_date, captured_at=captured_at)
    checks = tuple(c for c in (cp, cb) if c is not None)

    conflict: Optional[str] = None
    # 🔴 复审 🔴-2:这个 `if` 与 `QuoteQuality.cross_verified` **共用同一个判别式** ——
    # 「有没有对拍过」与「对拍出没出冲突」从此不可能各说各话。
    if _is_cross_verified(checks):
        conflict = detect_conflict(primary, backup, invalidation_of=invalidation_of,
                                   plan_entered_of=plan_entered_of)

    if cp is not None and cp.ok:
        chosen, role = primary, QUOTE_ROLE_PRIMARY
        degraded = False
        freshness = QF_CONFLICT if conflict else QF_FRESH
    elif cb is not None and cb.ok:
        chosen, role = backup, QUOTE_ROLE_BACKUP
        degraded = True                 # 🔴 来源降级:必须记下来(K8 §二十)
        freshness = QF_CONFLICT if conflict else QF_FRESH
    elif cp is not None and cp.usable:
        # 可以用、但七项里有非致命项没过(目前只有"源还没发开盘价")→ 读数照出、
        # 样本域降级。⛔ 不判 `insufficient`:那会把好的价 / 量 / 额一起扔掉。
        chosen, role, degraded, freshness = primary, QUOTE_ROLE_PRIMARY, False, QF_DEGRADED
    elif cb is not None and cb.usable:
        chosen, role, degraded, freshness = backup, QUOTE_ROLE_BACKUP, True, QF_DEGRADED
    elif cp is not None:
        chosen, role, degraded, freshness = primary, QUOTE_ROLE_PRIMARY, False, QF_INSUFFICIENT
    elif cb is not None:
        chosen, role, degraded, freshness = backup, QUOTE_ROLE_BACKUP, True, QF_INSUFFICIENT
    else:
        chosen, role, degraded, freshness = None, None, False, QF_INSUFFICIENT

    return chosen, QuoteQuality(
        ts_code=code, freshness=freshness, chosen_role=role,
        chosen_source=(str(getattr(chosen, "source", "")) or None) if chosen is not None else None,
        source_degraded=degraded, conflict=conflict, checks=checks,
    )


def domain_quality(
    codes: Sequence[str], quality_by_code: Dict[str, QuoteQuality], *, in_window: bool,
) -> str:
    """一个样本域的三态质量。**结构性判据**(全有 / 全无 / 其余),⛔ 不是百分比。

    ⚠ 与 `collect.AuctionSnapshot.quality_of` 是**同一套判据的两个入口**:那个吃快照,
    这个吃已经算好的逐票账(`mech` 侧要拿它算关键域 / 上下文域)。两者由守门单测对拍。
    """
    from neckline.auction import DQ_DEGRADED, DQ_INSUFFICIENT, DQ_OK

    want = list(dict.fromkeys(codes))
    if not want:
        # 「没有可判的东西」与「判过了都好」必须分得开(§七 P0-39 同款纪律)。
        return DQ_INSUFFICIENT
    usable = [c for c in want if (c in quality_by_code and quality_by_code[c].usable)]
    if not usable:
        return DQ_INSUFFICIENT
    all_fresh = all(c in quality_by_code and quality_by_code[c].freshness == QF_FRESH
                    for c in want)
    if len(usable) == len(want) and all_fresh and in_window:
        return DQ_OK
    return DQ_DEGRADED


def worse_of(a: str, b: str) -> str:
    """两个三态取**更差**的那个(`ok < degraded < insufficient`,越差越靠后)。"""
    from neckline.auction import DQ_DEGRADED, DQ_INSUFFICIENT, DQ_OK

    order = {DQ_OK: 0, DQ_DEGRADED: 1, DQ_INSUFFICIENT: 2}
    return a if order.get(a, 2) >= order.get(b, 2) else b


__all__ = [
    "AUCTION_RESULT_TIME_START",
    "ERR_CODE_MISMATCH", "ERR_WRONG_TRADE_DATE", "ERR_FUTURE_TIMESTAMP",
    "ERR_BEFORE_FINAL_AUCTION", "ERR_TIMESTAMP_UNPARSEABLE", "ERR_REQUIRED_FIELD_MISSING",
    "ERR_OPEN_PRICE_MISSING", "ERR_PRICE_RELATION", "ERR_NEGATIVE_VOLUME", "ERR_NEGATIVE_AMOUNT",
    "VALIDATION_ERROR_CODES",
    "QuoteCheck", "QuoteQuality",
    "parse_quote_ts", "validate_quote", "detect_identity_conflict", "detect_conflict",
    "resolve_dual", "domain_quality", "worse_of",
]
