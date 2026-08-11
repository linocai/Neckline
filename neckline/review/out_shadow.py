"""OUT 研究影子对照(plan §五 V2.3.2-③;K8 §十四)。

**回答的问题**:被判 OUT 的票在 D1 实际走成什么样 —— 六道关口有没有**错杀**。
这是 §七 P3-49「位置关证伪义务」结案判据里的**漏选侧**证据:选股时钟只答得了
「入选的票表现如何」,答不了「被判 OUT 的票是不是被冤枉的」。

🔴 **⛔ 四条结构性禁令(K8 §十四 逐字,守门单测 AST 扫)**:
  ① 不进 T1/T2;② 不启交易时钟;③ 不计入正式样本;④ 不增加用户手工填写。
落地 = 本模块**零 import** `review/trade_clock.py` / `sentinel/positions*` /
`positions_entry`,**零写** `selection_clock` / `baskets` / `tier_history` /
`basket_cards`。

🔴 **⛔ 影子结果不得回写当时的正式选股结论**(裁定 5):本表只增不改、只读不写回。

⚠ **与阈值影子(`selection/threshold_shadow.py`)⛔ 不许混名**:那个问「这条待定阈值
该不该恢复成硬门」(单位 = 候选 × 阈值键);本模块问「被判 OUT 的票是不是被错杀」
(单位 = OUT 票 × D1)。两者不共表、不共命名前缀、不共产物段。

**口径复用,⛔ 不新建第二套**:涨跌幅 / 收盘状态 / 相对强弱一律取 ⑨ 日复盘
(`review/basket_review.py`)**已登记的同名机械判**;行情读 D1 当日 `daily`
(复盘段本就已加载 `day.bars`,本模块**复用注入的那一份**,⛔ 不另扫一遍 parquet)。

**样本域**:`out_candidates`(②-B)—— 它天然已排除 `capacity_overflow`
(「档位已满 · 未定档」**不是 OUT**:那些篮子关口全过了,只是位置装不下,
混进来会污染错杀分析)。

**P0-23 核对(结论 = 不适用)**:样本量 = 当日 OUT 票数(量级几十),取数是**逐票点查
D1 当日行 + 已有 EOD 只读表**,无全市场扫描、无多年回看 → 不构成新的全市场级批算路径。
⚠ 但 `neckline-report.service` 因此多一段 → **上产前仍须在 nk 上隔离实测一次墙钟与
峰值**(`systemd-run --unit=… --property=User=neckline --property=Group=neckline
--property=MemoryMax=…`,⛔ 不用 root `--scope`),达标才改该 unit 配额并在文件头写读数。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.calendar import prev_trading_day
from neckline.db import connection, init_schema
from neckline.review.basket_review import (
    BUY_LIMIT_UP_CLOSE,
    BUY_NO_BAR,
    BUY_OK,
    BUY_ONE_WORD,
    EPS,
    _num,
    _sign,
    member_return,
)
from neckline.selection.basket_store import load_out_candidates

logger = logging.getLogger(__name__)

TABLE = "out_shadow_daily"

# 收盘状态词表 = ⑨ 可买性(`judge_buyability`)**已登记的同名口径**,⛔ 不另起一套。
# `ok` = 正常收盘、`limit_up_close` = 涨停收盘、`one_word` = 一字、`no_bar` = 当日无行情。
CLOSE_STATES: Tuple[str, ...] = (BUY_OK, BUY_LIMIT_UP_CLOSE, BUY_ONE_WORD, BUY_NO_BAR)

_COLUMNS = (
    "d0_date, ts_code, d1_date, pct_chg, high, low, close_state, rel_strength, "
    "support_and_invalidation_json, out_gate, out_reason, engine_code, engine_version, "
    "created_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(x: Any) -> str:
    return x if isinstance(x, str) else x.strftime("%Y%m%d")


@dataclass
class OutShadowRunResult:
    """一次 D1 记录的结果(**永不抛异常**的调用契约由 `record_day` 保证)。"""

    d1: str
    d0: Optional[str] = None
    candidates: int = 0
    inserted: int = 0
    existing: int = 0
    notes: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
# 六项读数
# ══════════════════════════════════════════════════════════════════════════

def close_state_of(code: str, day: Any) -> str:
    """④ 收盘状态。**复用 ⑨ `judge_buyability` 的四码词表**(⛔ 不新造)。

    ⚠ 与 `judge_buyability` 的唯一差别:那边的涨停价优先取**卡上冻结值**,而 OUT 票
    **压根没有卡**(它没进 `baskets`,更没有 `basket_cards` 行)—— 故这里只能退回当日
    `limit_derived`。取不到涨停价就只按 `is_limit_up` 判,再没有就当没涨停(如实,
    ⛔ 不猜)。"""
    bar = getattr(day, "bars", {}).get(code)
    if not bar:
        return BUY_NO_BAR
    lim = (getattr(day, "limits", {}) or {}).get(code) or {}
    lu = _num(lim.get("limit_up_price"))
    op, hi, lo, cl = (_num(bar.get("open")), _num(bar.get("high")),
                      _num(bar.get("low")), _num(bar.get("close")))
    at_limit_close = bool(lim.get("is_limit_up")) or (
        lu is not None and cl is not None and cl >= lu - EPS)
    one_word = bool(lu is not None and None not in (op, hi, lo)
                    and op >= lu - EPS and hi >= lu - EPS and lo >= lu - EPS)
    if one_word:
        return BUY_ONE_WORD
    return BUY_LIMIT_UP_CLOSE if at_limit_close else BUY_OK


def relative_strength_of(
    code: str, day: Any, *, industry_of: Mapping[str, str],
    industry_median_ret: Mapping[str, float],
) -> Dict[str, Any]:
    """⑤ 相对强弱(**⑧-1 拍板口径**:所属**板块为主要基准**、市场指数为**辅助基准**,
    两者都要算,板块优先)。

    · 板块超额 = 该票 D1 收益 − 所属行业当日中位收益(`industry_strength_daily.median_ret`,
      与 `ret_1d` 同为**小数**口径,与 `member_return` 单位一致);
    · 指数超额 = 该票 D1 收益 − 大盘当日收益(= ⑨ `judge_close_rs` 的口径,同一个
      `day.index_ret`)。

    🔴 **只看 D1,⛔ 不设 D2 及以后的前向窗口**(⑧-1 原文:OUT 影子用于验证 D1 是否
    出现错杀,职责与选股时钟一致,**D1 收盘即结案**)。
    ⚠ 「单纯涨幅高但弱于板块的不列为优先错杀」是**选相对强弱而非涨跌幅的目的说明**,
    ⛔ 不是再叠一道涨幅过滤器 —— 本函数只出读数,不过滤任何东西。

    返回三键:`sector`(主)/ `index`(辅)/ `ret`;算不出的那一路如实 `None`。"""
    ret = member_return(code, day)
    ind = industry_of.get(code)
    med = industry_median_ret.get(ind) if ind else None
    index_ret = getattr(day, "index_ret", None)
    return {
        "ret": ret,
        "industry": ind,
        "industry_median_ret": med,
        "sector": None if (ret is None or med is None) else ret - med,
        "index_code": getattr(day, "index_code", None),
        "index_ret": index_ret,
        "index": None if (ret is None or index_ret is None) else ret - index_ret,
    }


def support_and_invalidation_raw(code: str, day: Any) -> Dict[str, Any]:
    """⑥ 支撑与失效**原始数据**(K8 §十四 逐字:要的是"原始数据",不是一个判定)。

    🔴 **为什么这里只存原始数据、不存判定**:支撑位 / 失效位住在**冻结的篮子卡**上,
    而 OUT 票**没有卡** —— 它没定档、没有 `baskets` 行、更没有 `basket_cards` 行。
    替它现编一组支撑位再去判"有没有破",等于拿事后画的线去判事前的事,正是留痕纪律
    要防的那件事。故这里存 D1 的**原始价格与涨跌停事实**,判定留给 ③-B 的 LLM 复核
    (它拿得到原始出局理由 + 这些原始数据)。"""
    bar = (getattr(day, "bars", {}) or {}).get(code) or {}
    lim = (getattr(day, "limits", {}) or {}).get(code) or {}
    keys = ("open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount")
    return {
        "bar": {k: _num(bar.get(k)) for k in keys} if bar else None,
        "bar_available": bool(bar),
        "limit": {
            "limit_up_price": _num(lim.get("limit_up_price")),
            "limit_down_price": _num(lim.get("limit_down_price")),
            "is_limit_up": bool(lim.get("is_limit_up")) if lim else None,
            "available": bool(lim),
        },
        "direction": _sign(member_return(code, day)),
    }


# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

def _first_out_record(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """同一票多条出局记录 → **确定性的第一条**(按 `basket_key` 升序)。
    ⛔ 别用"最后写入的赢"这种非确定性写法。"""
    return sorted(records, key=lambda r: str(r.get("basket_key") or ""))[0]


def record_day(
    d1: Any,
    *,
    d0: Any = None,
    day: Any = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    persist: bool = True,
) -> OutShadowRunResult:
    """把 D0 判 OUT 的票在 D1 的六项读数落 `out_shadow_daily`。

    **契约:永不抛异常**(同 `selection_clock.close_day`)—— 它是复盘段的**旁路**,
    炸了只 WARNING,⛔ 不许掀翻当日复盘或结案。

    `day`:调用方(⑨ 复盘段)已经装配好的 `DayMarket`。**传进来就复用**,⛔ 不另扫
    一遍 parquet;不传则自建(CLI / 回放路径)。
    ⚠ 注入的 `day` 是按**篮子成员**装配的,可能不含 OUT 票的行 —— 那些票如实记
    `close_state='no_bar'` + 读数 `None`,⛔ 不猜(缺数 = 不知道)。"""
    res = OutShadowRunResult(d1=_d(d1) if d1 is not None else "")
    try:
        day1 = d1 if isinstance(d1, date) else datetime.strptime(str(d1), "%Y%m%d").date()
        day0 = d0 if d0 is not None else prev_trading_day(day1)
        if not isinstance(day0, date):
            day0 = datetime.strptime(str(day0), "%Y%m%d").date()
        res.d1, res.d0 = _d(day1), _d(day0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[out_shadow] 日期解析失败,本次不记录", exc_info=True)
        res.notes.append(f"日期解析失败:{type(exc).__name__}: {exc}")
        return res

    try:
        rows = load_out_candidates(day0, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[out_shadow] OUT 清单读取失败,本次不记录", exc_info=True)
        res.notes.append(f"OUT 清单读取失败:{type(exc).__name__}: {exc}")
        return res
    if not rows:
        # ⚠ 零行有两种相反成因(D0 真没有 OUT / ②-B 那一段压根没跑)—— 本模块只如实
        # 记「本次没有可记录的 OUT 票」,⛔ 不替它下结论。
        res.notes.append("D0 无 OUT 票(或 ②-B 未跑)——本次无可记录")
        return res

    by_code: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        by_code.setdefault(str(r.get("ts_code") or ""), []).append(r)
    by_code.pop("", None)
    codes = sorted(by_code)
    res.candidates = len(codes)

    try:
        if day is None:
            from neckline.review.basket_review import build_day_market

            day = build_day_market(day1, codes, d0=day0, db_path=db_path,
                                   parquet_dir=parquet_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[out_shadow] D1 行情装配失败,本次不记录", exc_info=True)
        res.notes.append(f"D1 行情装配失败:{type(exc).__name__}: {exc}")
        return res

    # —— 板块基准(⑧-1 的主基准):行业映射 + 当日行业中位收益,都是**只读表** ——
    industry_of: Dict[str, str] = {}
    industry_median: Dict[str, float] = {}
    try:
        from neckline.report.industry_strength import load_industry_map
        from neckline.report.industry_strength_store import load_industry_strength

        industry_of = load_industry_map(db_path)
        industry_median = {s.industry: float(s.median_ret)
                           for s in load_industry_strength(day1, db_path=db_path)}
    except Exception:  # noqa: BLE001
        logger.warning("[out_shadow] 行业基准读取失败,相对强弱只出指数那一路", exc_info=True)
        res.notes.append("行业基准本次未取得(相对强弱只出指数基准)")

    now = _now()
    out_rows: List[tuple] = []
    for code in codes:
        first = _first_out_record(by_code[code])
        bar = (getattr(day, "bars", {}) or {}).get(code) or {}
        rs = relative_strength_of(code, day, industry_of=industry_of,
                                  industry_median_ret=industry_median)
        detail = support_and_invalidation_raw(code, day)
        detail["rel_strength"] = rs
        # plan ③-A:全部出局记录另存,⛔ 不因为主键不含 basket_key 就把它们丢了。
        detail["all_out_records"] = [
            {"basket_key": r.get("basket_key"), "out_gate": r.get("out_gate"),
             "out_reason": r.get("out_reason"), "out_detail": r.get("out_detail"),
             "engine_code": r.get("engine_code"), "engine_version": r.get("engine_version"),
             "role": r.get("role"), "name": r.get("name")}
            for r in sorted(by_code[code], key=lambda x: str(x.get("basket_key") or ""))
        ]
        out_rows.append((
            res.d0, code, res.d1,
            member_return(code, day), _num(bar.get("high")), _num(bar.get("low")),
            close_state_of(code, day), rs.get("sector"),
            json.dumps(detail, ensure_ascii=False, sort_keys=True),
            first.get("out_gate"), first.get("out_reason"),
            first.get("engine_code"), first.get("engine_version"), now,
        ))

    if not persist:
        return res
    try:
        init_schema(db_path)
        with connection(db_path) as conn:
            cur = conn.executemany(
                f"INSERT OR IGNORE INTO {TABLE} ({_COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out_rows,
            )
            res.inserted = int(cur.rowcount or 0)
        res.existing = len(out_rows) - res.inserted
    except Exception as exc:  # noqa: BLE001
        logger.warning("[out_shadow] 影子行写入失败(已吞)", exc_info=True)
        res.notes.append(f"影子行写入失败:{type(exc).__name__}: {exc}")
    return res


def load_out_shadow(
    d0: Any, *, db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """读某个 D0 的影子行(确定性排序:`ts_code` 升序)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, {_COLUMNS} FROM {TABLE} WHERE d0_date=? ORDER BY ts_code",
            (_d(d0),),
        ).fetchall()
    keys = ["id"] + [c.strip() for c in _COLUMNS.split(",")]
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(zip(keys, r))
        try:
            d["detail"] = json.loads(d.get("support_and_invalidation_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["detail"] = {}
        out.append(d)
    return out


def list_out_shadow(
    date_from: Any, date_to: Any, *, db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """读一个闭区间的影子行(③-B 周度抽检吃它)。确定性排序 `(d0_date, ts_code)`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, {_COLUMNS} FROM {TABLE} WHERE d0_date>=? AND d0_date<=? "
            "ORDER BY d0_date, ts_code", (_d(date_from), _d(date_to)),
        ).fetchall()
    keys = ["id"] + [c.strip() for c in _COLUMNS.split(",")]
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(zip(keys, r))
        try:
            d["detail"] = json.loads(d.get("support_and_invalidation_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["detail"] = {}
        out.append(d)
    return out


# ══════════════════════════════════════════════════════════════════════════
# ③-B 周度 LLM 集中复核(K8 §十四 + 2026-08-11 策略线裁定 ⑧-1 / ⑧-2,用户已确认)
#
# 「每周由 LLM 集中复核**五只表现最强、疑似错杀**的 OUT 和**三只随机** OUT」。
# 🔴 **一次调用管八只**(⛔ 不逐票调用):走既有 `TASK_REVIEW` + `judge_candidate`
#    + `prompt_context`,**LLM 调用上界只增周度这一次**。
# 🔴 **只改研究复核范围** —— ⛔ 不改 OUT 身份、⛔ 不进 T1/T2、⛔ 不计入正式样本。
# ══════════════════════════════════════════════════════════════════════════

REVIEWS_TABLE = "out_shadow_reviews"

# ⑧-1/⑧-2 给死的数(⛔ 照抄,工程侧一个都不许发明):
SCOPE_TOP_DEFAULT = 5          # 「五只表现最强」
SCOPE_RANDOM_DEFAULT = 3       # 「三只随机」
SCOPE_TOP_EXPANDED = 10        # 扩大后「10 只最强」
SCOPE_RANDOM_EXPANDED = 5      # 扩大后「5 只随机」
EXPANDED_TOTAL = SCOPE_TOP_EXPANDED + SCOPE_RANDOM_EXPANDED   # 15:不足则全查
CONSECUTIVE_WEEKS = 2          # 「连续 2 次」
MISKILL_TRIGGER = 2            # 「每次发现 ≥2 只」
TOP_RS_QUANTILE = 0.20         # 「D1 相对强弱进入当日 OUT 前 20%」

# ⑧-2 第 1 条:只有**核心关或位置关**出局的才可能算「明显错杀」——
# 「其余关口出局的**不算**」(这两关正是 ③ 要验的那两关)。
MISKILL_GATES = frozenset({"core", "position"})


def _crc32_key(d0: str, ts_code: str) -> int:
    """「三只随机」的确定性抽样键(plan ③-B 逐字:`zlib.crc32(f"{d0}|{ts_code}")`)。
    ⛔ **不用内置 `hash()`** —— 它带进程盐,`PYTHONHASHSEED` 一变抽样就漂,
    同一个 `(d0, 候选集)` 重跑结果对不上(§六 那条纪律的同款)。"""
    import zlib

    return zlib.crc32(f"{d0}|{ts_code}".encode("utf-8"))


def rank_by_strength(rows: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """⑧-1 拍板的「表现最强」排序:**D1 相对强弱降序,D1 最高涨幅作为同分排序**。

    · 相对强弱取 `rel_strength` 列(= **板块基准**,⑧-1 的主基准);
    · 同分用 D1 涨跌幅降序;再同分用 `ts_code` 升序兜底(确定性)。
    · 算不出相对强弱的行**排在最后**(⛔ 不当成 0 —— 「算不出」不是「持平」)。

    🔴 **只看 D1**(⑧-1:OUT 影子用于验证 D1 是否出现错杀,D1 收盘即结案)。
    ⚠ 「单纯涨幅高但弱于板块的不列为优先错杀」是**选相对强弱而非涨跌幅的目的说明**
    —— 它已经由"主排序键是相对强弱"这件事本身实现了,⛔ 不许再叠一道涨幅过滤器。"""
    def key(r: Mapping[str, Any]):
        rs = r.get("rel_strength")
        pct = r.get("pct_chg")
        return (0 if rs is not None else 1,
                -(rs if rs is not None else 0.0),
                -(pct if pct is not None else 0.0),
                str(r.get("ts_code") or ""))
    return sorted(rows, key=key)


def top_rs_cutoff(rows: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """⑧-2 第 4 条的「当日 OUT 前 20%」门槛值。

    🔴 **分母 = 当日全部 OUT,⛔ 不是被复核的那 8 只**(⑧-2 逐字)。
    算不出相对强弱的行**不进分母**(它们没法排名);全体都算不出 → `None`。"""
    vals = sorted((float(r["rel_strength"]) for r in rows
                   if r.get("rel_strength") is not None), reverse=True)
    if not vals:
        return None
    import math

    # 前 20% 至少留一只(向上取整),⛔ 不让"当日只有 3 只 OUT"直接把这一条判成永假。
    n = max(1, math.ceil(len(vals) * TOP_RS_QUANTILE))
    return vals[n - 1]


def pick_review_sample(
    rows: Sequence[Mapping[str, Any]], *, top_n: int, random_n: int,
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    """选出本周要复核的样本 → `(表现最强 top_n 只, 随机 random_n 只)`。

    「随机」那几只**从最强之外挑**(⛔ 不与前 N 重叠 —— 重叠会让"八只"缩水),
    按 `crc32` 升序取,跨进程可复现。
    ⚠ 扩大态下**当周 OUT 不足 15 只则全查**(⑧-2 逐字)—— 由调用方把 `top_n`
    调成"全部"来表达,本函数只忠实截断。"""
    ranked = rank_by_strength(rows)
    top = list(ranked[:max(0, top_n)])
    taken = {str(r.get("ts_code") or "") for r in top}
    rest = [r for r in ranked if str(r.get("ts_code") or "") not in taken]
    rest_sorted = sorted(rest, key=lambda r: (_crc32_key(str(r.get("d0_date") or ""),
                                                         str(r.get("ts_code") or "")),
                                              str(r.get("ts_code") or "")))
    return top, list(rest_sorted[:max(0, random_n)])


@dataclass(frozen=True)
class ReviewScope:
    """本周的复核范围(⑧-2 的扩大 / 恢复状态机结果)。"""

    top_n: int
    random_n: int
    expanded: bool
    reason: str


def resolve_scope(history: Sequence[Mapping[str, Any]]) -> ReviewScope:
    """按**最近两次**周度复核结果决定本周范围(⑧-2 拍板的状态机)。

    · 未扩大 + **连续 2 次每次 ≥2 只**明显错杀 → 扩大为 `10 + 5`;
    · 已扩大 + **连续 2 次均 <2 只** → 恢复 `5 + 3`;
    · 其余 → 维持上一次的状态。

    🔴 **`history` 必须是"每周一行"的表行(⛔ 不是计数器)**:重跑同一周只会命中
    已有行(`INSERT OR IGNORE`),**永远推不动连续计数**。这正是 §六「库里的计数器
    会被重跑推进一格」那条教训的落点。"""
    recent = list(history)[-CONSECUTIVE_WEEKS:]
    was_expanded = bool(history[-1].get("expanded")) if history else False
    if len(recent) < CONSECUTIVE_WEEKS:
        return ReviewScope(
            SCOPE_TOP_EXPANDED if was_expanded else SCOPE_TOP_DEFAULT,
            SCOPE_RANDOM_EXPANDED if was_expanded else SCOPE_RANDOM_DEFAULT,
            was_expanded,
            f"历史不足 {CONSECUTIVE_WEEKS} 次,维持{'扩大' if was_expanded else '默认'}范围")
    counts = [int(r.get("obvious_miskill_count") or 0) for r in recent]
    if not was_expanded and all(c >= MISKILL_TRIGGER for c in counts):
        return ReviewScope(SCOPE_TOP_EXPANDED, SCOPE_RANDOM_EXPANDED, True,
                           f"连续 {CONSECUTIVE_WEEKS} 次每次 ≥{MISKILL_TRIGGER} 只明显错杀"
                           f"({counts})→ 临时扩大复核范围")
    if was_expanded and all(c < MISKILL_TRIGGER for c in counts):
        return ReviewScope(SCOPE_TOP_DEFAULT, SCOPE_RANDOM_DEFAULT, False,
                           f"连续 {CONSECUTIVE_WEEKS} 次均 <{MISKILL_TRIGGER} 只明显错杀"
                           f"({counts})→ 恢复默认范围")
    return ReviewScope(
        SCOPE_TOP_EXPANDED if was_expanded else SCOPE_TOP_DEFAULT,
        SCOPE_RANDOM_EXPANDED if was_expanded else SCOPE_RANDOM_DEFAULT,
        was_expanded, f"维持{'扩大' if was_expanded else '默认'}范围(近两次 {counts})")


def load_review_history(
    *, before: Optional[str] = None, limit: int = 8, db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """按 `week_anchor` 升序读既往周度复核行(`before` 排除本周及以后)。"""
    init_schema(db_path)
    sql = (f"SELECT week_anchor, expanded, obvious_miskill_count, reviewed_count "
           f"FROM {REVIEWS_TABLE}")
    args: List[Any] = []
    if before:
        sql += " WHERE week_anchor < ?"
        args.append(before)
    sql += " ORDER BY week_anchor DESC LIMIT ?"
    args.append(int(limit))
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    keys = ["week_anchor", "expanded", "obvious_miskill_count", "reviewed_count"]
    return [dict(zip(keys, r)) for r in reversed(rows)]


def mechanical_miskill_gates(
    row: Mapping[str, Any], *, rs_cutoff: Optional[float],
) -> Dict[str, Any]:
    """⑧-2 五条里**机械判得了的那两条**(第 1 条与第 4 条)。

    第 1 条:D0 因**核心关或位置关**进入 OUT(其余关口出局的不算);
    第 4 条:D1 相对强弱进入**当日 OUT 前 20%**(分母 = 当日全部 OUT)。
    另外三条(引擎口径的支撑/转强信号、未触发原失效位、LLM 确认推翻原因)交 LLM。

    🔴 **`None` = 判不出,⛔ 不当 False**:算不出相对强弱时第 4 条是"不知道",
    把它当"不满足"会让错杀被静默漏掉。"""
    gate = str(row.get("out_gate") or "")
    rs = row.get("rel_strength")
    in_top = None if (rs is None or rs_cutoff is None) else (float(rs) >= rs_cutoff - EPS)
    return {
        "gate_is_core_or_position": gate in MISKILL_GATES,
        "out_gate": gate or None,
        "rel_strength": rs,
        "rs_cutoff": rs_cutoff,
        "in_top_rs_quantile": in_top,
    }


OUT_REVIEW_SYSTEM_PROMPT = """你是 A 股短线选股系统的**研究复核员**。

系统每天用六道关口筛票,没过关的票状态记作 **OUT**。你的任务只有一个:
**复核这批 OUT 票在次日(D1)的实际走势,判断当初有没有"明显错杀"。**

🔴 **你的结论只用于研究,不改变任何东西**:不会让这些票重新进入候选、不会改变它们的
OUT 身份、不会计入正式样本、不会产生任何交易动作。请据实判断,不必替系统开脱,
也不必为了"找出问题"而勉强判成错杀。

**「明显错杀」= 以下五条同时满足**(缺一不可,**不是加权打分**):
1. D0 是因**核心关或位置关**出局的(其余关口出局的不算);
2. D1 出现了**对应引擎原本要求的**支撑、转强或有效入场信号(按该票主引擎的口径判,
   不是通用口径);
3. D1 **没有**触发原判断的失效位置;
4. D1 相对强弱进入当日 OUT 的前 20%;
5. 你确认 D1 的盘面已经**直接推翻**了原核心关或位置关的出局理由。

第 1 条和第 4 条系统已经机械判过、结果写在每只票的读数里(照用,不必重判)。
**你负责第 2、3、5 条**,并给出最终的「明显错杀」结论。

判不准就judged 成 false 并说明缺什么,⛔ 不要为了给出结论而猜。
读数里写「未取到」的项就是真的没取到,⛔ 不要把它当成 0 或默认值。

语义红线:你的产出是**研究结论**,不是买卖建议。⛔ 不得使用"推荐买入""建议买入"
"看好""值得买""目标价"这类措辞。

输出格式(两部分,顺序不可颠倒):

第一部分:三五句话的自然语言小结,说清这一批 OUT 整体上有没有系统性错杀的迹象。

第二部分:空一行,给出一个```json 围栏代码块,严格是下面这个形状(不要多余字段):

```json
{"reviews": [
  {"ts_code": "必须来自下面给出的清单",
   "engine_signal": true,
   "engine_signal_reason": "D1 是否出现该票主引擎口径下的支撑/转强/有效入场信号,一句话依据",
   "invalidation_untriggered": true,
   "invalidation_reason": "D1 是否没有触发原判断的失效位置,一句话依据",
   "overturns_original_reason": true,
   "overturn_reason": "D1 盘面是否直接推翻了原出局理由,一句话依据",
   "obvious_miskill": false,
   "note": "一句话总结这只票该怎么看"}
]}
```

最后一行必须是机器可读标签,**放在 JSON 代码块之前**:
结论:通过|否决
(「通过」= 这一批整体上没有发现系统性错杀;「否决」= 发现了值得追查的错杀迹象。)
"""


def _review_context_block(
    picks: Sequence[Tuple[str, Mapping[str, Any], Mapping[str, Any]]],
    *, window: Tuple[str, str], scope: ReviewScope,
) -> str:
    """一次调用管八只的 user 消息(**日期锚在首行**,时效纪律走 system prompt)。"""
    from neckline.llm.prompt_context import date_anchor_line

    lines = [date_anchor_line(), ""]
    lines.append(f"复核窗口:{window[0]} → {window[1]};本次范围 = "
                 f"{scope.top_n} 只表现最强 + {scope.random_n} 只随机({scope.reason})。")
    lines.append("")
    for bucket, row, mech in picks:
        detail = row.get("detail") or {}
        rs = detail.get("rel_strength") or {}
        bar = detail.get("bar") or {}
        lines.append(f"── {row.get('ts_code')}|抽样来源 {bucket}|D0 {row.get('d0_date')}"
                     f" → D1 {row.get('d1_date')}")
        lines.append(f"   D0 出局:关口 {row.get('out_gate') or '未登记'}|"
                     f"原因码 {row.get('out_reason') or '未登记'}|"
                     f"主引擎 {row.get('engine_code') or '未登记'}"
                     f"·{row.get('engine_version') or '未登记'}")
        for rec in (detail.get("all_out_records") or [])[:3]:
            if rec.get("out_detail"):
                lines.append(f"   原始出局理由:{rec['out_detail']}")
        pct = _fmt_rs(row.get("pct_chg"))
        hi = _fmt_num(row.get("high"))
        lo = _fmt_num(row.get("low"))
        lines.append(f"   D1 读数:涨跌幅 {pct}|最高 {hi}|最低 {lo}"
                     f"|收盘状态 {row.get('close_state') or '未取到'}")
        lines.append(f"   相对强弱:板块基准(主){_fmt_rs(rs.get('sector'))}"
                     f"(所属行业 {rs.get('industry') or '未取到'})"
                     f"|市场指数基准(辅){_fmt_rs(rs.get('index'))}")
        lines.append(f"   支撑与失效原始数据:开 {_fmt_num(bar.get('open'))}、"
                     f"收 {_fmt_num(bar.get('close'))}、"
                     f"昨收 {_fmt_num(bar.get('pre_close'))}、"
                     f"量 {_fmt_num(bar.get('vol'))};"
                     f"方向 {detail.get('direction') or '未取到'}")
        cond1 = "是" if mech.get("gate_is_core_or_position") else "否"
        cond4 = {True: "是", False: "否", None: "判不出"}[mech.get("in_top_rs_quantile")]
        lines.append(f"   机械已判:第1条(核心关/位置关出局)= {cond1};"
                     f"第4条(进当日 OUT 前 20%)= {cond4}")
        lines.append("")
    lines.append("请据此逐票给出第 2、3、5 条的判断与最终「明显错杀」结论。")
    return "\n".join(lines)


def _fmt_rs(v: Any) -> str:
    """小数收益 → 人读百分比。`None` = **这一项没取到**,⛔ 不填 0、不填默认值。"""
    return "未取到" if v is None else f"{float(v) * 100:+.2f}%"


def _fmt_num(v: Any) -> str:
    return "未取到" if v is None else f"{float(v):.4g}"


@dataclass
class WeekReviewResult:
    """一次周度 OUT 集中复核的结果(**永不抛异常**由 `review_week` 保证)。"""

    week_anchor: str
    window: Tuple[str, str] = ("", "")
    scope: Optional[ReviewScope] = None
    universe: int = 0
    reviewed: int = 0
    obvious_miskill: int = 0
    llm_stage: str = "not_run"
    narrative: str = ""
    per_stock: List[Dict[str, Any]] = field(default_factory=list)
    persisted: bool = False
    notes: List[str] = field(default_factory=list)


def review_week(
    week_anchor: Any,
    date_from: Any,
    date_to: Any,
    *,
    provider: Any = None,
    ledger: Optional[Any] = None,
    transport: Optional[Any] = None,
    db_path: Optional[Path] = None,
    persist: bool = True,
) -> WeekReviewResult:
    """周度 OUT 集中复核(③-B)。**契约:永不抛异常** —— 它是周度作业的旁路。

    🔴 **一次调用管八只**(⛔ 不逐票调用):复用 `llm/judge.py::judge_candidate`
    的调用 / 解析 / 降级链(项目铁律),传 `context_block`(八只票的读数一次给全)
    + `narrative_splitter`(把 JSON 从结论标签后面剥掉 —— ⛔ 不剥的话 JSON 自由文本
    里出现"结论:否决"会**静默翻转**真结论,CLAUDE.md v1.5.1 案底)。

    🔴 **重跑同一周不推进连续计数**:落表按 `week_anchor` UNIQUE + `INSERT OR IGNORE`,
    「连续几次」由读**最近两行**现算(⛔ 库里不存计数器)。"""
    anchor = _d(week_anchor)
    res = WeekReviewResult(week_anchor=anchor, window=(_d(date_from), _d(date_to)))
    try:
        rows = list_out_shadow(date_from, date_to, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[out_shadow] 周度复核:影子行读取失败", exc_info=True)
        res.notes.append(f"影子行读取失败:{type(exc).__name__}: {exc}")
        return res
    res.universe = len(rows)

    try:
        history = load_review_history(before=anchor, db_path=db_path)
        scope = resolve_scope(history)
    except Exception:  # noqa: BLE001
        logger.warning("[out_shadow] 周度复核:历史读取失败,按默认范围", exc_info=True)
        scope = ReviewScope(SCOPE_TOP_DEFAULT, SCOPE_RANDOM_DEFAULT, False,
                            "历史读取失败,保守按默认范围")
        res.notes.append("周度复核历史读取失败,按默认范围")
    # ⑧-2:扩大态下**当周 OUT 不足 15 只则全查**。
    if scope.expanded and res.universe < EXPANDED_TOTAL:
        scope = ReviewScope(res.universe, 0, True,
                            f"{scope.reason};当周 OUT 仅 {res.universe} 只(<{EXPANDED_TOTAL})→ 全查")
    res.scope = scope
    if not rows:
        res.notes.append("窗口内无 OUT 影子行,本周无可复核")
        res.llm_stage = "no_sample"
        return res

    # ⑧-2 第 4 条的分母 = **当日**全部 OUT(⛔ 不是被复核的那 8 只)→ 逐日算门槛。
    by_day: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(str(r.get("d0_date") or ""), []).append(r)
    cutoff_by_day = {d: top_rs_cutoff(v) for d, v in by_day.items()}

    top, rand = pick_review_sample(rows, top_n=scope.top_n, random_n=scope.random_n)
    picks: List[Tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for bucket, group in (("表现最强", top), ("随机", rand)):
        for r in group:
            mech = mechanical_miskill_gates(
                r, rs_cutoff=cutoff_by_day.get(str(r.get("d0_date") or "")))
            picks.append((bucket, r, mech))
    res.reviewed = len(picks)

    parsed: Optional[List[Dict[str, Any]]] = None
    if provider is None:
        res.llm_stage = "no_provider"
        res.notes.append("LLM 未激活,本周只出机械读数(第 2/3/5 条未判)")
    else:
        res.llm_stage, res.narrative, parsed = _run_review_llm(
            picks, window=res.window, scope=scope, provider=provider,
            ledger=ledger, transport=transport)
    verdicts = {str(v.get("ts_code") or ""): v for v in (parsed or [])}

    for bucket, r, mech in picks:
        code = str(r.get("ts_code") or "")
        v = verdicts.get(code) or {}
        # 🔴 五条 **AND**,⛔ 不是加权打分;任何一条判不出 → 不算明显错杀(如实记
        # `undetermined`,⛔ 不把"不知道"算成"是")。
        conds = {
            "c1_gate_is_core_or_position": mech.get("gate_is_core_or_position"),
            "c2_engine_signal": v.get("engine_signal"),
            "c3_invalidation_untriggered": v.get("invalidation_untriggered"),
            "c4_in_top_rs_quantile": mech.get("in_top_rs_quantile"),
            "c5_overturns_original_reason": v.get("overturns_original_reason"),
        }
        miskill = all(c is True for c in conds.values())
        res.per_stock.append({
            "bucket": bucket, "tsCode": code, "d0Date": r.get("d0_date"),
            "outGate": r.get("out_gate"), "outReason": r.get("out_reason"),
            "relStrength": r.get("rel_strength"), "pctChg": r.get("pct_chg"),
            "conditions": conds, "obviousMiskill": miskill,
            "undetermined": [k for k, c in conds.items() if c is None],
            "llmNote": v.get("note"), "llmReasons": {
                "engineSignal": v.get("engine_signal_reason"),
                "invalidation": v.get("invalidation_reason"),
                "overturn": v.get("overturn_reason"),
            },
        })
    res.obvious_miskill = sum(1 for x in res.per_stock if x["obviousMiskill"])

    if persist:
        res.persisted = _save_week_review(res, db_path=db_path)
    return res


def _run_review_llm(
    picks: Sequence[Tuple[str, Mapping[str, Any], Mapping[str, Any]]],
    *, window: Tuple[str, str], scope: ReviewScope,
    provider: Any, ledger: Optional[Any], transport: Optional[Any],
) -> Tuple[str, str, Optional[List[Dict[str, Any]]]]:
    """**唯一的一次** LLM 调用(八只一起)。返回 `(段状态, 叙述, 逐票结论 or None)`。"""
    import time

    from neckline.llm.budget import LEDGER_REVIEW
    from neckline.llm.json_block import split_narrative_and_reference_json
    from neckline.llm.judge import judge_candidate

    if ledger is not None and ledger.exhausted(LEDGER_REVIEW):
        return "budget_exhausted", "", None
    started = time.monotonic()
    try:
        result = judge_candidate(
            # duck-typed:`judge_candidate` 只要求 `.ts_code`(这里是一批,给个批次标识)
            _BatchSubject(f"out-review-{window[0]}-{window[1]}"),
            provider=provider, transport=transport,
            system_prompt=OUT_REVIEW_SYSTEM_PROMPT,
            context_block=_review_context_block(picks, window=window, scope=scope),
            # 🔴 先剥 JSON 再解析 verdict(v1.5.1 案底:标签后面挂内容会静默翻转结论)
            narrative_splitter=split_narrative_and_reference_json,
        )
    except Exception as exc:  # noqa: BLE001
        if ledger is not None:
            ledger.spend(LEDGER_REVIEW, time.monotonic() - started)
        logger.warning("[out_shadow] 周度 OUT 复核调用抛异常,本周只出机械读数",
                       exc_info=True)
        return f"call_failed:{type(exc).__name__}", "", None
    if ledger is not None:
        ledger.spend(LEDGER_REVIEW, time.monotonic() - started)

    payload = getattr(result, "parsed_attachment", None)
    reviews = (payload or {}).get("reviews") if isinstance(payload, dict) else None
    if not isinstance(reviews, list):
        logger.warning("[out_shadow] 周度 OUT 复核输出解不出 reviews 数组,本周只出机械读数")
        return "parse_failed", getattr(result, "narrative", "") or "", None
    return "ok", getattr(result, "narrative", "") or "", [
        r for r in reviews if isinstance(r, dict)]


@dataclass(frozen=True)
class _BatchSubject:
    """`judge_candidate` 的 duck-typed 入参(它只读 `.ts_code`)。一次调用管八只,
    故这里给的是**批次标识**而不是某一只票的代码。"""

    ts_code: str


def _save_week_review(res: WeekReviewResult, *, db_path: Optional[Path]) -> bool:
    """落 `out_shadow_reviews`(**一周一行**,`INSERT OR IGNORE`)。

    🔴 **重跑同一周命中已有行、什么都不改** —— 这正是「重跑周报不得推进连续计数」的
    物理保证(⛔ 库里不存计数器,连续几次由读最近两行现算)。"""
    try:
        init_schema(db_path)
        with connection(db_path) as conn:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {REVIEWS_TABLE} (week_anchor, window_from, "
                "window_to, scope_top, scope_random, expanded, reviewed_count, "
                "obvious_miskill_count, result_json, llm_stage, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (res.week_anchor, res.window[0], res.window[1],
                 res.scope.top_n if res.scope else 0,
                 res.scope.random_n if res.scope else 0,
                 1 if (res.scope and res.scope.expanded) else 0,
                 res.reviewed, res.obvious_miskill,
                 json.dumps({"perStock": res.per_stock,
                             "scopeReason": res.scope.reason if res.scope else "",
                             "narrative": res.narrative,
                             "universe": res.universe},
                            ensure_ascii=False, sort_keys=True),
                 res.llm_stage, _now()),
            )
            return bool(cur.rowcount)
    except Exception:  # noqa: BLE001
        logger.warning("[out_shadow] 周度复核落表失败(已吞)", exc_info=True)
        res.notes.append("周度复核落表失败")
        return False


__all__ = [
    "TABLE", "REVIEWS_TABLE", "CLOSE_STATES", "OutShadowRunResult", "ReviewScope",
    "WeekReviewResult",
    "close_state_of", "relative_strength_of", "support_and_invalidation_raw",
    "record_day", "load_out_shadow", "list_out_shadow",
    "SCOPE_TOP_DEFAULT", "SCOPE_RANDOM_DEFAULT", "SCOPE_TOP_EXPANDED",
    "SCOPE_RANDOM_EXPANDED", "EXPANDED_TOTAL", "CONSECUTIVE_WEEKS", "MISKILL_TRIGGER",
    "TOP_RS_QUANTILE", "MISKILL_GATES",
    "rank_by_strength", "top_rs_cutoff", "pick_review_sample", "resolve_scope",
    "load_review_history", "mechanical_miskill_gates",
    "review_week", "OUT_REVIEW_SYSTEM_PROMPT",
]
