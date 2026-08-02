"""盘中存拍(plan §五 V2-⑧-B,D4 已拍板)。把哨兵**每一拍本来就已经拉到**的关注池
报价累计在内存里,**15:05 收盘后一次性**落 `intraday_ticks` 当日分区;09:25 竞价快照
同样先入内存、收盘后落 `auction_snapshots`。

**为什么是「内存累计 + 一次落盘」而不是逐分钟写**(D4 裁定,别改回去):parquet 按日
分区、`write_table_day` 是**整日文件覆盖写**,逐分钟写等于每分钟重写一次当日全文件
(200 只 × 240 分钟),I/O 与毒化风险都不划算;而当日数据在收盘后才被 ⑨ 消费,盘中
没有任何读者。**代价**:进程中途挂掉 = 当天已累计的部分丢失 → 落 `capture_status`
如实标 `partial`/`missing`(⛔ 不装完整)。

**三条硬约束(逐条落在代码里)**

    1. **零额外网络**:本模块**不拉价**,只接收 `engine.run_tick` 已经拉到的 `Quote`
       (免费源限流取舍照既有;关注池是代理样本这一认知不变)。
    2. **绝不拖垮哨兵主循环**:落盘失败只 WARNING;调用点(`engine.py` / `api/app.py`)
       一律裹独立 try/except —— 存拍是**旁路**,四哨兵与熔断的判定路径一行不动。
    3. **写 parquet 一律 `write_table_day`**(§铁律),两张表的数值列 canonical dtype
       已在 ① 登记进 `market_data.TABLE_FLOAT_COLS`(漏了守门单测会挂,v1.3.5 血训)。

**`capture_status` 三态(供 ⑨ 如实标注,判据写死在这里)**

    · `missing` —— 当日**一条 tick 都没落盘**(进程没跑 / 全程拉价失败 / 落盘失败)。
    · `full`    —— 有 tick,且 ①没有任何一拍拉价返回空、②首条 tick 落在开盘后
                   `CAPTURE_OPEN_GRACE_MIN` 分钟内、③末条 tick 在收盘前
                   `CAPTURE_CLOSE_GRACE_MIN` 分钟内(= 头尾都没缺口)。
    · `partial` —— 其余(中途启动 / 中途挂掉 / 有拍拉空)。

⚠ **标签之外必须看数字**:三态是给报告一眼看的,`payload` 里同时落
`covered_minutes` / `expected_minutes` / `empty_ticks` / `rows` / `codes` 原始值 ——
⑨ 要用哪条线判「够不够精确算 MFE/MAE」是 ⑨ 的事,**本模块不替它定阈值**。

**外源可插拔**(裁定 #1):`source` 列区分 `sina|tencent|external`;用户日后谈定分时
数据源,走**同一张表**回补 `source='external'` 的行,⑨ 无感切换。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from neckline.sentinel.dedup import already_pushed, record_pushed
from neckline.sentinel.intraday import FULL_DAY_MINUTES, elapsed_trading_minutes

logger = logging.getLogger(__name__)

CAPTURE_SENTINEL = "capture"          # `sentinel_events.sentinel` 取值(日级台账,非推送)
EVENT_INTRADAY = "intraday"
EVENT_AUCTION = "auction"

STATUS_FULL = "full"
STATUS_PARTIAL = "partial"
STATUS_MISSING = "missing"

# 头尾宽限(分钟)。**不是策略阈值,是本项目哨兵轮询节奏的直接换算**:最长的一档
# 轮询间隔是午休的 300s = 5min,取它作宽限,等价于「只要头尾缺口没超过一个最长轮询
# 周期就算完整」。改哨兵轮询节奏时顺手回看这两个数。
CAPTURE_OPEN_GRACE_MIN = 5
CAPTURE_CLOSE_GRACE_MIN = 5

_TICK_COLUMNS: Tuple[str, ...] = (
    "ts_code", "trade_date", "ts", "price", "volume", "amount",
    "cum_volume", "cum_amount", "source",
)
_AUCTION_COLUMNS: Tuple[str, ...] = (
    "ts_code", "trade_date", "auction_price", "auction_volume", "auction_amount",
    "pre_close", "gap_pct", "captured_at",
)


def _tick_schema():
    import polars as pl

    return {
        "ts_code": pl.String, "trade_date": pl.Date, "ts": pl.String,
        "price": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
        "cum_volume": pl.Float64, "cum_amount": pl.Float64, "source": pl.String,
    }


def _auction_schema():
    import polars as pl

    return {
        "ts_code": pl.String, "trade_date": pl.Date, "auction_price": pl.Float64,
        "auction_volume": pl.Float64, "auction_amount": pl.Float64, "pre_close": pl.Float64,
        "gap_pct": pl.Float64, "captured_at": pl.String,
    }


@dataclass
class DayBuffer:
    """当日累计(**进程内内存态**,重启即丢 —— 丢了就如实标 `partial`/`missing`)。

    行按**元组**存(不是 dict):关注池 200 只 × 240 分钟 ≈ 4.8 万行,元组比 dict 省
    一个数量级的内存 —— 哨兵与 API 同进程跑在 2 vCPU / 1.6G 的箱子上,这点省得值。
    """

    trade_date: date
    ticks: List[Tuple[Any, ...]] = field(default_factory=list)
    auction: List[Tuple[Any, ...]] = field(default_factory=list)
    auction_requested: int = 0
    last_cum: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    minutes: Set[int] = field(default_factory=set)
    attempted_ticks: int = 0
    empty_ticks: int = 0
    flushed: bool = False


_BUFFERS: Dict[str, DayBuffer] = {}


def _key(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def _buffer(trade_date: date) -> DayBuffer:
    k = _key(trade_date)
    buf = _BUFFERS.get(k)
    if buf is None:
        buf = DayBuffer(trade_date=trade_date)
        _BUFFERS[k] = buf
    return buf


def reset_capture_state() -> None:
    """清空全部内存累计(单测隔离用;等价于进程刚重启)。"""
    _BUFFERS.clear()


def buffered_rows(trade_date: date) -> int:
    buf = _BUFFERS.get(_key(trade_date))
    return len(buf.ticks) if buf else 0


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f else None


def record_intraday_tick(
    trade_date: date,
    now: datetime,
    quotes: Mapping[str, Any],
) -> int:
    """把这一拍的报价累计进内存。返回本拍记了几行。

    `volume`/`amount` 是**本拍增量**(源给的是当日累计值,这里减去上一拍的累计);
    **该票当日第一次被观测时增量是 `None`** —— 那一格不是"这一分钟成交了 0",而是
    "自开盘到此刻的量,算不出本拍增量",⛔ 不许写 0 冒充(「没有」与「没看」)。
    累计值原样落 `cum_volume`/`cum_amount`,两者都在,⑨ 想用哪个用哪个。
    """
    buf = _buffer(trade_date)
    buf.attempted_ticks += 1
    if not quotes:
        buf.empty_ticks += 1
        return 0
    minute = max(0, min(FULL_DAY_MINUTES, elapsed_trading_minutes(now)))
    buf.minutes.add(minute)
    stamp = now.strftime("%H:%M:%S")
    n = 0
    for code, q in quotes.items():
        price = _f(getattr(q, "price", None))
        cum_v = _f(getattr(q, "volume", None)) or 0.0
        cum_a = _f(getattr(q, "amount", None)) or 0.0
        prev = buf.last_cum.get(code)
        if prev is None:
            d_v = d_a = None
        else:
            # 累计值理应单调不减;源偶发回退(免费源快照抖动)→ 增量算不出,落 null,
            # ⛔ 不 clamp 成 0(那会把"数据有问题"伪装成"这一分钟没成交")。
            d_v = cum_v - prev[0] if cum_v >= prev[0] else None
            d_a = cum_a - prev[1] if cum_a >= prev[1] else None
        buf.last_cum[code] = (cum_v, cum_a)
        buf.ticks.append((
            code, trade_date, stamp, price, d_v, d_a, cum_v, cum_a,
            str(getattr(q, "source", "") or "unknown"),
        ))
        n += 1
    return n


def record_auction_snapshot(
    trade_date: date,
    now: datetime,
    quotes: Mapping[str, Any],
    *,
    requested: Optional[int] = None,
) -> int:
    """09:25 竞价快照(**当日只记一次**,后到的调用直接忽略)。`gap_pct` =
    `竞价价 / 昨收 − 1`,昨收缺失或 ≤0 → `None`(不拿 0 冒充"平开")。"""
    buf = _buffer(trade_date)
    if buf.auction:
        return 0
    stamp = now.isoformat(timespec="seconds")
    buf.auction_requested = int(requested if requested is not None else len(quotes))
    for code, q in quotes.items():
        price = _f(getattr(q, "price", None))
        pre_close = _f(getattr(q, "pre_close", None))
        gap = (price / pre_close - 1.0) if (price and pre_close and pre_close > 0) else None
        buf.auction.append((
            code, trade_date, price, _f(getattr(q, "volume", None)),
            _f(getattr(q, "amount", None)), pre_close, gap, stamp,
        ))
    return len(buf.auction)


# —— 窗口判定(供 `api/app.py` 的轮询循环挂两个旁路分支;放这里是为了让循环那侧
#    只多两行 `elif`,判定口径不散落)————————————————————————————————————
AUCTION_CAPTURE_START = time(9, 25)
AUCTION_CAPTURE_END = time(9, 30)
# 15:05 = D4 拍板的落盘时刻;给到 15:35 是因为**非交易时段轮询是 5 分钟一探**,
# 单点判等于赌轮询相位,窗口才落得稳(落盘本身幂等,窗口内多探几次不会重复写)。
FLUSH_WINDOW_START = time(15, 5)
FLUSH_WINDOW_END = time(15, 35)


def is_auction_capture_window(now: datetime) -> bool:
    from neckline.calendar import is_trading_day

    return is_trading_day(now.date()) and AUCTION_CAPTURE_START <= now.time() < AUCTION_CAPTURE_END


def is_flush_window(now: datetime) -> bool:
    from neckline.calendar import is_trading_day

    return is_trading_day(now.date()) and FLUSH_WINDOW_START <= now.time() < FLUSH_WINDOW_END


def run_auction_capture(
    trade_date: date,
    now: datetime,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    quotes_fn: Optional[Any] = None,
    breadth_cap: Optional[int] = None,
) -> int:
    """09:25 竞价快照:自己组装关注池 + **拉一次价**,当日只跑一次。

    **为什么自己拉、而不是搭 `precall` 那一拍的便车**:⑧-D 要求 `precall.py` 判定逻辑
    一行不动,把存拍塞进去(哪怕只是旁路)也会让那个文件多一条与盘前校准无关的路径。
    代价是**每天多一次批量请求**(1 次 / 天,相对既有 ~240 次 / 天可忽略),换来存拍与
    纪律外壳彻底解耦。返回记了几行。
    """
    buf = _buffer(trade_date)
    if buf.auction:
        return 0
    try:
        from neckline.sentinel.universe import DEFAULT_BREADTH_CAP, load_watch_universe

        wu = load_watch_universe(
            trade_date, breadth_cap=breadth_cap or DEFAULT_BREADTH_CAP,
            db_path=db_path, parquet_dir=parquet_dir,
        )
        if not wu.codes:
            return 0
        if quotes_fn is None:
            from neckline.sentinel.quotes import get_quotes

            quotes = get_quotes(wu.codes)
        else:
            quotes = quotes_fn(wu.codes)
        return record_auction_snapshot(trade_date, now, quotes, requested=len(wu.codes))
    except Exception:  # noqa: BLE001
        logger.warning("[capture] 竞价快照采集失败(存拍是旁路,不影响盘前校准)", exc_info=True)
        return 0


@dataclass
class CaptureFlushResult:
    trade_date: date
    ran: bool = False                       # False = 本次没做(当日已落过盘)
    tick_status: str = STATUS_MISSING
    auction_status: str = STATUS_MISSING
    tick_rows: int = 0
    auction_rows: int = 0
    codes: int = 0
    covered_minutes: int = 0
    expected_minutes: int = FULL_DAY_MINUTES
    empty_ticks: int = 0
    errors: List[str] = field(default_factory=list)


def _tick_status(buf: DayBuffer, wrote_rows: int, ok: bool) -> Tuple[str, int]:
    covered = len({m for m in buf.minutes if m < FULL_DAY_MINUTES})
    if wrote_rows <= 0 or not ok:
        return STATUS_MISSING, covered
    first, last = (min(buf.minutes), max(buf.minutes)) if buf.minutes else (FULL_DAY_MINUTES, 0)
    head_ok = first <= CAPTURE_OPEN_GRACE_MIN
    tail_ok = last >= FULL_DAY_MINUTES - 1 - CAPTURE_CLOSE_GRACE_MIN
    if buf.empty_ticks == 0 and head_ok and tail_ok:
        return STATUS_FULL, covered
    return STATUS_PARTIAL, covered


def _write(table: str, trade_date: date, rows: Sequence[Tuple[Any, ...]], schema,
           parquet_dir: Optional[Path]) -> Tuple[int, Optional[str]]:
    """落一张表的当日分区(走 `write_table_day` 铁律)。返回 `(行数, 错误描述或 None)`
    —— **落盘失败绝不抛给调用方**,存拍是旁路。"""
    if not rows:
        return 0, None
    try:
        import polars as pl

        from neckline.data.market_data import write_table_day

        df = pl.DataFrame(rows, schema=schema, orient="row")
        write_table_day(table, trade_date, df, parquet_dir)
        return len(rows), None
    except Exception as e:  # noqa: BLE001
        logger.warning("[capture] %s %s 落盘失败(存拍是旁路,不影响哨兵):%s",
                       table, trade_date, e, exc_info=True)
        return 0, f"{table}: {e}"


def flush_day(
    trade_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> CaptureFlushResult:
    """15:05 之后一次性落盘 + 记 `capture_status`。**幂等**:当日已记过台账就直接
    返回 `ran=False`(跨进程重启也不会重复写 —— 台账在 SQLite,不是内存标记)。"""
    res = CaptureFlushResult(trade_date=trade_date)
    buf = _buffer(trade_date)
    if already_pushed(trade_date, CAPTURE_SENTINEL, "", EVENT_INTRADAY, db_path=db_path):
        logger.info("[capture] %s 当日存拍台账已存在,本次跳过(幂等)", trade_date)
        return res

    res.ran = True
    tick_rows, tick_err = _write("intraday_ticks", trade_date, buf.ticks, _tick_schema(), parquet_dir)
    auc_rows, auc_err = _write("auction_snapshots", trade_date, buf.auction, _auction_schema(), parquet_dir)
    res.errors = [e for e in (tick_err, auc_err) if e]
    res.tick_rows, res.auction_rows = tick_rows, auc_rows
    res.codes = len(buf.last_cum)
    res.empty_ticks = buf.empty_ticks
    res.tick_status, res.covered_minutes = _tick_status(buf, tick_rows, tick_err is None)
    if auc_rows <= 0 or auc_err is not None:
        res.auction_status = STATUS_MISSING
    elif buf.auction_requested and auc_rows < buf.auction_requested:
        res.auction_status = STATUS_PARTIAL
    else:
        res.auction_status = STATUS_FULL

    # 日级台账落 `sentinel_events`(⑨ 读它做「这一天的存拍可信吗」的如实标注)。
    # **为什么不落在 parquet 行里**:`missing` 恰恰是"一行都没有"的那一天,行里写不下
    # (「没有」与「没看」必须分得开);而 `sentinel_events` 本就是哨兵的当日事件台账、
    # 已有非推送用法(退潮黄色预警只落看板不推送),不必为此新开第 22 张表。
    record_pushed(
        trade_date, CAPTURE_SENTINEL, "", EVENT_INTRADAY,
        payload={
            "capture_status": res.tick_status, "rows": tick_rows, "codes": res.codes,
            "covered_minutes": res.covered_minutes, "expected_minutes": FULL_DAY_MINUTES,
            "attempted_ticks": buf.attempted_ticks, "empty_ticks": buf.empty_ticks,
            "errors": res.errors,
        },
        db_path=db_path,
    )
    record_pushed(
        trade_date, CAPTURE_SENTINEL, "", EVENT_AUCTION,
        payload={
            "capture_status": res.auction_status, "rows": auc_rows,
            "requested": buf.auction_requested, "errors": [e for e in (auc_err,) if e],
        },
        db_path=db_path,
    )
    buf.flushed = True
    buf.ticks.clear()
    buf.auction.clear()
    logger.info(
        "[capture] %s 存拍落盘:ticks=%d(%s,覆盖 %d/%d 分钟,空拍 %d)auction=%d(%s)",
        trade_date, tick_rows, res.tick_status, res.covered_minutes, FULL_DAY_MINUTES,
        buf.empty_ticks, auc_rows, res.auction_status,
    )
    return res


def load_capture_status(
    trade_date: date, kind: str = EVENT_INTRADAY, *, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """读某日存拍状态(⑨ / 报告的数据新鲜度节用)。**没有台账 ≠ full**:返回
    `{"capture_status": "missing", "recorded": False}`,「没记过」与「记了是 missing」
    靠 `recorded` 分开。"""
    from neckline.sentinel.dedup import load_events_for_date

    for ev in load_events_for_date(trade_date, db_path=db_path):
        if ev.get("sentinel") == CAPTURE_SENTINEL and ev.get("event_key") == kind:
            out = dict(ev.get("payload") or {})
            out["recorded"] = True
            out.setdefault("capture_status", STATUS_MISSING)
            return out
    return {"capture_status": STATUS_MISSING, "recorded": False}


__all__ = [
    "CAPTURE_SENTINEL", "EVENT_INTRADAY", "EVENT_AUCTION",
    "STATUS_FULL", "STATUS_PARTIAL", "STATUS_MISSING",
    "CAPTURE_OPEN_GRACE_MIN", "CAPTURE_CLOSE_GRACE_MIN",
    "DayBuffer", "CaptureFlushResult",
    "record_intraday_tick", "record_auction_snapshot", "flush_day",
    "is_auction_capture_window", "is_flush_window", "run_auction_capture",
    "AUCTION_CAPTURE_START", "AUCTION_CAPTURE_END", "FLUSH_WINDOW_START", "FLUSH_WINDOW_END",
    "load_capture_status", "reset_capture_state", "buffered_rows",
]
