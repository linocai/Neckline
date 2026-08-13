"""交易日历原语(plan 0.3)。

接口契约(plan 0.3 钉死的四函数):
    is_trading_day(d) -> bool
    next_trading_day(d) -> date       # 严格 d 之后,不含 d 自身
    prev_trading_day(d) -> date       # 严格 d 之前,不含 d 自身
    trading_days_between(start, end) -> list[date]   # 闭区间,升序

真值源优先级(与 LinoN 不同——Neckline 有 TuShare token):
    1. SQLite `trade_cal` 表(`scripts/init_calendar.py` 落库,覆盖 2015–2027 缓冲区,
       实际回测区间 2020-至今全覆盖)—— **主真值源**。
    2. 静态休市表(`static_holidays.py`,仅 2025–2026,继承 LinoN 已查证来源)——
       DB 缺失 / 请求日期超出 DB 覆盖范围时的兜底,并与 DB 交叉核对告警。
    3. 二者都没有 → 工作日近似(周末非交易,其余交易),打 warning。

注意:本模块所在包名为 `calendar`,与标准库同名。包内 / 全项目一律【绝对导入】
(`from neckline.calendar.xxx import ...`),**禁止裸 `import calendar`**
(在把 `neckline/` 而非项目根加进 sys.path 的场景下会拿到本包而非标准库;继承
LinoN 教训)。本模块只依赖标准库 `datetime` + `sqlite3` + `bisect`。
"""

from __future__ import annotations

import logging
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Set, Union

from neckline.calendar.static_holidays import STATIC_CLOSED, STATIC_YEARS
from neckline.config import settings

logger = logging.getLogger(__name__)

DateLike = Union[date, datetime, str]

# A 股两段交易时段(有午休)。集合竞价价格行为不同,阶段 0 不实现竞价逻辑。
_AM = (datetime.min.time().replace(hour=9, minute=30), datetime.min.time().replace(hour=11, minute=30))
_PM = (datetime.min.time().replace(hour=13, minute=0), datetime.min.time().replace(hour=15, minute=0))

# —— 市场时区与收盘时刻(**全项目「市场时刻」单一事实源**,v1.4-⑥-A 立)——————————
#
# `CN_TZ`:北京时间。A 股**无夏令时**,固定 UTC+8,故直接用固定偏移 `timezone`,不引
# `zoneinfo`(免掉 tzdata 依赖与 IANA 库版本漂移;若哪天真需要历史时区规则再换,换点
# 只有这一处)。**用途**:把「券商交割单成交时刻(北京时间)」与「`strategy_versions.
# activated_at`(UTC 戳)」归一到同一条时间轴上比较 —— 两者不归一就逐笔判纪律,差 8 小时
# 的错判会直接落到「这笔按哪版章程判」上(v1.4-⑥-A 重点防的坑)。
CN_TZ = timezone(timedelta(hours=8))
# 收盘时刻(北京时间)。**与 `_PM` 的收盘边界是同一个事实**,故引用而不另写字面量;
# 盘中窗口判定(`sentinel/intraday.py`)与「交割单只有日期没有时刻 → 按该日收盘时刻取
# 章程」(`review/reconcile.py::trade_instant`)共用这一个源。
MARKET_CLOSE_TIME = _PM[1]


def _to_date(d: DateLike) -> date:
    """归一为 date。支持 date / datetime / 'YYYY-MM-DD' / 'YYYYMMDD'。"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def _iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


# —— DB 缓存(进程内单例,懒加载,首次调用任意对外函数时触发)——————————————

class _CalendarCache:
    loaded: bool = False
    trading_days: List[date] = []
    trading_set: Set[date] = set()
    # 覆盖范围(不论 is_open,只要 trade_cal 有该日记录就算覆盖到)。刻意与下面的
    # "交易日 min/max" 分开:若直接拿 trading_days[0]/[-1] 当覆盖边界,遇到"覆盖
    # 范围内但恰好落在首尾的非交易日"(如 fetch 起点 2015-01-01 本身是元旦休市)
    # 会被误判成"超出 DB 覆盖",错误地跌回静态表 + 工作日近似(单测
    # `test_reset_cache_forces_reload` 踩过的坑:删掉边界那天的记录后,查询该日
    # 期望 False,却因边界收缩落进 fallback 分支被工作日近似判成 True)。
    coverage_min: Optional[date] = None
    coverage_max: Optional[date] = None


_cache = _CalendarCache()


def _load_cache(force: bool = False) -> None:
    if _cache.loaded and not force:
        return
    days: List[date] = []
    cov_min: Optional[date] = None
    cov_max: Optional[date] = None
    db_path = settings.db_path
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                bounds = conn.execute(
                    "SELECT MIN(cal_date), MAX(cal_date) FROM trade_cal WHERE exchange='SSE'"
                ).fetchone()
                if bounds and bounds[0] is not None:
                    cov_min = datetime.strptime(bounds[0], "%Y%m%d").date()
                    cov_max = datetime.strptime(bounds[1], "%Y%m%d").date()
                rows = conn.execute(
                    "SELECT cal_date FROM trade_cal "
                    "WHERE exchange='SSE' AND is_open=1 ORDER BY cal_date"
                ).fetchall()
                days = [datetime.strptime(r[0], "%Y%m%d").date() for r in rows]
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.warning("交易日历 DB 读取失败(%s),退化为静态表 + 工作日近似", e)
    _cache.trading_days = days
    _cache.trading_set = set(days)
    _cache.coverage_min = cov_min
    _cache.coverage_max = cov_max
    _cache.loaded = True
    if not days:
        logger.warning(
            "trade_cal 表为空或未建(先跑 scripts/init_calendar.py),交易日历退化为"
            "静态休市表(仅覆盖 %s)+ 工作日近似,回测前请先补全日历。",
            STATIC_YEARS,
        )


def reset_cache() -> None:
    """清缓存(测试 / init_calendar 写库后热刷新用)。"""
    _cache.loaded = False
    _cache.trading_days = []
    _cache.trading_set = set()
    _cache.coverage_min = None
    _cache.coverage_max = None


def _in_db_coverage(dt: date) -> bool:
    return _cache.coverage_min is not None and _cache.coverage_min <= dt <= _cache.coverage_max


def _static_is_trading_day(dt: date) -> bool:
    """DB 覆盖不到时的兜底判定:周末非交易 + 静态表 + 覆盖年外工作日近似。"""
    if dt.weekday() >= 5:  # 5=周六 6=周日
        return False
    if _iso(dt) in STATIC_CLOSED:
        return False
    if dt.year not in STATIC_YEARS:
        logger.warning(
            "is_trading_day(%s): 超出静态表覆盖年份 %s 且无 DB 数据,退化为工作日近似",
            _iso(dt), STATIC_YEARS,
        )
    return True


# —— 对外接口(plan 0.3 契约)——————————————————————————————————————

def is_trading_day(d: DateLike) -> bool:
    """是否交易日。DB 覆盖范围内查 DB,否则退化静态表 + 工作日近似。"""
    dt = _to_date(d)
    _load_cache()
    if _in_db_coverage(dt):
        return dt in _cache.trading_set
    return _static_is_trading_day(dt)


def next_trading_day(d: DateLike) -> date:
    """严格在 d 之后的下一个交易日(不含 d 自身)。"""
    dt = _to_date(d)
    _load_cache()
    if _in_db_coverage(dt) and dt < _cache.coverage_max:
        idx = bisect_right(_cache.trading_days, dt)
        if idx < len(_cache.trading_days):
            return _cache.trading_days[idx]
    cur = dt + timedelta(days=1)
    for _ in range(40):
        if is_trading_day(cur):
            return cur
        cur += timedelta(days=1)
    raise RuntimeError(f"next_trading_day: 40 天内未找到交易日,起点 {_iso(dt)}")


def prev_trading_day(d: DateLike) -> date:
    """严格在 d 之前的上一个交易日(不含 d 自身)。"""
    dt = _to_date(d)
    _load_cache()
    if _in_db_coverage(dt) and dt > _cache.coverage_min:
        idx = bisect_left(_cache.trading_days, dt)
        if idx > 0:
            return _cache.trading_days[idx - 1]
    cur = dt - timedelta(days=1)
    for _ in range(40):
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    raise RuntimeError(f"prev_trading_day: 40 天内未找到交易日,起点 {_iso(dt)}")


def trading_days_between(start: DateLike, end: DateLike) -> List[date]:
    """闭区间 [start, end] 内全部交易日,升序。start > end 返回空列表。

    用途例:新股上市第 N 个交易日 = ``len(trading_days_between(list_date, trade_date))``
    (list_date 计为第 1 天,供 limit_derived 的上市新股涨跌幅豁免窗口判定用)。
    """
    sd, ed = _to_date(start), _to_date(end)
    if sd > ed:
        return []
    _load_cache()
    if _in_db_coverage(sd) and _in_db_coverage(ed):
        lo = bisect_left(_cache.trading_days, sd)
        hi = bisect_right(_cache.trading_days, ed)
        return _cache.trading_days[lo:hi]
    out: List[date] = []
    cur = sd
    while cur <= ed:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def trading_window(d: DateLike):
    """两段交易时段;非交易日返回 None。[(09:30,11:30),(13:00,15:00)]。"""
    if not is_trading_day(d):
        return None
    return [_AM, _PM]


# —— DB ⟷ 静态表交叉核对(init_calendar 落库后调用,只告警不改数据)——————————

def verify_against_static() -> dict:
    """DB trade_cal 与静态休市表(覆盖 `STATIC_YEARS`)交叉核对。

    返回 {'ok', 'reason', 'mismatches'}。不一致只记录 + 打 warning,不自动改数据
    (以 DB/trade_cal 为准——它是查得到官方交易所权威源,静态表只是兜底)。
    """
    _load_cache(force=True)
    if not _cache.trading_days:
        return {"ok": False, "reason": "DB 日历为空,请先跑 scripts/init_calendar.py", "mismatches": []}
    mismatches = []
    for year in STATIC_YEARS:
        cur = date(year, 1, 1)
        end = date(year, 12, 31)
        while cur <= end:
            if _in_db_coverage(cur):
                db_open = cur in _cache.trading_set
                static_open = _static_is_trading_day(cur)
                if db_open != static_open:
                    mismatches.append(
                        {"date": _iso(cur), "db_is_open": db_open, "static_is_open": static_open}
                    )
            cur += timedelta(days=1)
    if mismatches:
        logger.warning(
            "DB 交易日历与静态表不一致 %d 处(以 DB/trade_cal 为准): %s",
            len(mismatches), mismatches[:10],
        )
    return {"ok": True, "reason": "ok", "mismatches": mismatches}


__all__ = [
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "trading_days_between",
    "trading_window",
    "verify_against_static",
    "reset_cache",
]
