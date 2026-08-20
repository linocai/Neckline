"""**D1 10:00—10:05 结算拍**(V2.5.0 S8,**裁定 10 已批准**)。

    一次性快照(开盘 30 分钟的极值)+ D0 冻结预案 → 三分支终值 → `k9_d1_verdicts`

🔴 **性质定死**(裁定 10 逐字):**零 LLM、零推送、不进 App 首屏**。
它是**结算**,不是提醒。唯一用途是成绩线的三分支结算(K9 §八)。
    · 零 LLM  —— 本模块不 import `neckline.llm` / `neckline.search`(守门 G7/G21);
    · 零推送  —— 本模块不 import `neckline.api.notify` / `neckline.push`
                  (守门 G21:跑一次结算后 APNs 调用计数 = 0);
    · 不进首屏 —— 产物只从 `GET /scoreboard/verdicts/{date}` 出去(挂在成绩线下,
                  ⛔ 不挂在 `/checklist` 下:路由本身就把「它属于成绩线」写明白了)。

🔴 **三分支判定的唯一权威是这一拍**(裁定 10)。9:29 那张核对表只提前告知
「哪几只已经死了」;它 ⛔ 不产生「成立」,也 ⛔ 不产生成绩单口径的任何其它终值。

🔴 **纪律与 9:26 那一拍完全一致**:交易日门 / 窗口外零落库 / 当日防重 /
**⛔ 事后不许补跑** —— 补跑会拿 10:30 的价格冒充 10:00 那一刻。

**为什么这不违反架构 §四**(依据写在这里,防止后来者当越界功能删掉):
    1. 架构 §5.1 本来就要求冻结「D0 预案条件、**D1 竞价与开盘 30 分钟读数**、
       三分支判定结果」;
    2. K9 §八 把三分支定义在「**D1 竞价 + 开盘 30 分钟**」这个窗口上;
    3. 架构 §四 那句「不**持续**观察 9:30 以后的价格」管的是**推送盘中提醒**与
       **跟踪持仓** —— 一次性结算读数不落在该禁令内。架构 §四 已补明文例外(S0 落地)。

⚠ **读数从哪来**:`data/realtime.py` 的 `Quote` 自带当日 high/low,10:00 时的
high/low **即前 30 分钟极值(含 9:25 竞价成交)**。竞价那半(`auction_price` /
`auction_gap_pct`)从 9:26 那一拍**冻结在库里的读数**取回来合并 ——
⛔ 不重新去拉一次「现在的竞价价」(那个东西 10:00 已经不存在了)。
那一拍没跑成的日子,竞价两项就是缺席 → 用到它们的条件求值 `UNKNOWN` → 落「观察」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from neckline.auction import (
    SETTLE_WINDOW_END,
    SETTLE_WINDOW_START,
    SKIP_ALREADY_RAN,
    SKIP_NO_LISTING,
    SKIP_NO_PLAYBOOK,
    SKIP_NOT_WINDOW,
)
from neckline.auction import collect as ac
from neckline.auction import store as astore
from neckline.calendar import is_trading_day, prev_trading_day
from neckline.data.realtime import DualQuote, Quote
from neckline.dedup import already_pushed, record_pushed
from neckline.k9 import store as k9_store
from neckline.playbook import store as pb_store
from neckline.playbook.evaluate import SettleOutcome, Verdict, settle_verdict
from neckline.playbook.model import MetricRef, Playbook, gap_percent_points

logger = logging.getLogger(__name__)

#: 台账 key(与 9:26 那一拍**分开记**:两拍各自防重,一拍跑没跑过不影响另一拍)。
SETTLE_SENTINEL = "auction"
EVENT_SETTLE = "settle_tick"


def is_settle_window(now: datetime) -> bool:
    """交易日 且 `10:00:00 ≤ now.time() < 10:05:00`(裁定 10 给的窗口)。"""
    return is_trading_day(now.date()) and SETTLE_WINDOW_START <= now.time() < SETTLE_WINDOW_END


def open30_readings(
    snap: ac.Snapshot, code: str, *,
    auction_frozen: Optional[Mapping[str, Any]] = None,
) -> Dict[MetricRef, Optional[float]]:
    """**10:00 那一拍**的读数表(九个量全在这里能有值)。

    `auction_frozen` = 9:26 那一拍冻在 `k9_d1_verdicts.auction_readings_json` 里的读数;
    ⚠ 缺席(那一拍没跑成)→ 竞价两项为 `None` → 用到它们的条件 `UNKNOWN` → 落「观察」。
    ⛔ **不拿 10:00 的现价冒充竞价价**。
    """
    prev = snap.prev_bars.get(code)
    open_price = snap.open_of(code)
    frozen = dict(auction_frozen or {})

    def _frozen(ref: MetricRef) -> Optional[float]:
        v = frozen.get(ref.value)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    return {
        MetricRef.AUCTION_PRICE: _frozen(MetricRef.AUCTION_PRICE),
        MetricRef.AUCTION_GAP_PCT: _frozen(MetricRef.AUCTION_GAP_PCT),
        MetricRef.OPEN_PRICE: open_price,
        MetricRef.GAP_PCT: gap_percent_points(open_price, prev.close if prev else None),
        # 10:00 的 high/low 即前 30 分钟极值(含 9:25 竞价成交)—— 裁定 10 逐字。
        MetricRef.FIRST30_LOW: snap.low_of(code),
        MetricRef.FIRST30_HIGH: snap.high_of(code),
        MetricRef.PREV_CLOSE: prev.close if prev else None,
        MetricRef.PREV_LOW: prev.low if prev else None,
        MetricRef.PREV_HIGH: prev.high if prev else None,
    }


@dataclass
class SettleRunResult:
    """一次 10:00 拍的结果。🔴 **没有 `should_push`** —— 这一拍零推送(裁定 10)。"""

    trade_date: date
    now: datetime
    ran: bool = False
    skipped_reason: str = ""
    d0_date: Optional[date] = None
    listing_size: int = 0
    settled: int = 0            # 本次真的定案的
    unchanged: int = 0          # 9:29 就已定案、⛔ 未被改判的
    confirmed: int = 0
    rejected: int = 0
    observed: int = 0
    data_quality: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, Any]:
        return {
            "settled": self.settled, "unchanged": self.unchanged,
            "confirmed": self.confirmed, "rejected": self.rejected,
            "observed": self.observed, "dataQuality": self.data_quality,
        }


def run_settle_tick(
    now: datetime,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    strategy: str = "K9",
    dual_quotes_fn: Optional[Callable[[List[str]], Dict[str, DualQuote]]] = None,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> SettleRunResult:
    """跑一次 10:00 结算(当日只跑一次)。**只落库,⛔ 不推送、⛔ 不返回待推项。**"""
    trade_date = now.date()
    res = SettleRunResult(trade_date=trade_date, now=now)

    # 1. 窗口 / 防重 —— ⛔ 不在窗口一律零落库(事后不许补跑)
    if not is_settle_window(now):
        res.skipped_reason = SKIP_NOT_WINDOW
        return res
    if already_pushed(trade_date, SETTLE_SENTINEL, "", EVENT_SETTLE, db_path=db_path):
        res.skipped_reason = SKIP_ALREADY_RAN
        return res

    d0 = prev_trading_day(trade_date)
    res.d0_date = d0
    listing = k9_store.load_listing(d0, strategy=strategy, db_path=db_path)
    if not listing:
        res.skipped_reason = SKIP_NO_LISTING
        return res
    res.listing_size = len(listing)
    codes = [r["ts_code"] for r in listing]

    # ══════════════════════════════════════════════════════════════════════
    # 🔴 **代入哪一版预案:账上钉死的那一版**(R2-03)
    # ══════════════════════════════════════════════════════════════════════
    # 裁定 10 锁住了「三分支的唯一权威是这一拍」,**没**锁住这一拍「用哪一版条件」。
    # 复审实测过反例:9:27 判待观察 → 9:45 改一版把成立门槛压到脚下 → 10:01 结算
    # 吐 `confirmed/open30`,而 `k9_d1_verdicts.playbook_version` 仍记着 v1
    # —— 权威那一拍代入了一份**在看过竞价之后**才写下的条件,且在账上查不出来。
    #
    # 两道锁(⛔ 缺一不可):
    #   ① `POST …/playbook` 的**冻结闸**:D1 一开始就拒绝改写该交易日的预案
    #      (`api/app.py::post_stock_playbook` → `PlaybookFrozen`);
    #   ② **本段**:9:26 那一拍已经把 `playbook_version` 写进 `k9_d1_verdicts` 了,
    #      结算拍就用账上那一版,⛔ 不再取一次 `MAX(version)`。
    #      ⚠ 必须在 `ensure_rows` **之前**读 —— 那一步之后表里就分不出
    #      「9:26 记的」与「刚补的骨架行」了。
    frozen_rows = {r["ts_code"]: r for r in
                   astore.load_verdicts(trade_date, strategy=strategy, db_path=db_path)}
    pinned = {c: int(r["playbook_version"]) for c, r in frozen_rows.items()}
    latest: Mapping[str, Playbook] = pb_store.load_latest(d0, codes=codes, db_path=db_path)
    playbooks: Dict[str, Playbook] = dict(latest)
    if pinned:
        at_pinned = pb_store.load_at_versions(d0, pinned, db_path=db_path)
        for code, version in sorted(pinned.items()):
            pb = at_pinned.get(code)
            if pb is None:
                # 账上点名的那一版取不回来 → 这一只**本次缺席**,⛔ 不拿最新版顶替
                # (顶替正是本条要防的那件事)。它留在 `decided_stage IS NULL`。
                playbooks.pop(code, None)
                res.notes.append(
                    f"{code}:账上记着的预案 v{version} 取不回来,本次不结算这一只")
                continue
            playbooks[code] = pb
            newer = latest.get(code)
            if newer is not None and newer.version != version:
                # 🔴 有人在 9:26 之后又写了一版 —— **说出来**,⛔ 不静默按旧版跑过去。
                res.notes.append(
                    f"{code}:预案在 9:26 之后被改写(账上 v{version} → 现有 "
                    f"v{newer.version});本拍仍代入 D0 冻结的 v{version}")
    if not playbooks:
        res.skipped_reason = SKIP_NO_PLAYBOOK
        return res

    clock = now_fn or datetime.now
    prev_bars = ac.load_prev_bars(d0, codes, parquet_dir=parquet_dir, db_path=db_path)
    snap = ac.collect_snapshot(
        trade_date, now, codes=codes,
        window=(SETTLE_WINDOW_START, SETTLE_WINDOW_END), d0_date=d0,
        db_path=db_path, parquet_dir=parquet_dir,
        dual_quotes_fn=dual_quotes_fn, quotes_fn=quotes_fn, now_fn=clock,
        prev_bars=prev_bars,
    )
    if snap.fetch_skipped_reason:
        # 拉价那一刻窗口已关 → **零落库**(⛔ 不拿 10:06 的价冒充 10:00 那一刻)。
        res.skipped_reason = snap.fetch_skipped_reason
        res.notes.extend(snap.notes)      # ⚠ 追加:上面那段版本核对的话⛔ 不许被覆盖
        return res

    # 2. 建行(9:26 那一拍没跑成的日子,表里一行都没有)
    astore.ensure_rows(
        trade_date, d0_date=d0,
        rows=[{"ts_code": c, "pattern": pb.pattern, "playbook_version": pb.version}
              for c, pb in sorted(playbooks.items())],
        strategy=strategy, db_path=db_path,
    )

    # 3. 9:26 冻结的竞价读数就在上面那份 `frozen_rows` 里(有就合并,没有就缺席
    #    —— ⛔ 不重拉。`ensure_rows` 刚补出来的骨架行本来就没有竞价读数,
    #    所以「在 `ensure_rows` 之前读」与「之后读」对这一项等价)。

    # 4. 只结算**还没定案**的(`decided_stage IS NULL`)。
    #    ⚠ 已在竞价定案的票连读数都不重算 —— 幂等靠 SQL,这里少做一步只是省事。
    pending = set(astore.undecided_codes(trade_date, strategy=strategy, db_path=db_path))
    outcomes: List[SettleOutcome] = []
    readings_by_code: Dict[str, Dict[str, Optional[float]]] = {}
    for code, pb in sorted(playbooks.items()):
        if code not in pending:
            continue
        row = frozen_rows.get(code) or {}
        readings = open30_readings(snap, code, auction_frozen=row.get("auction_readings"))
        readings_by_code[code] = {k.value: v for k, v in readings.items()}
        outcomes.append(settle_verdict(pb, readings))

    stats = astore.settle_verdicts(
        trade_date, outcomes, readings_by_code=readings_by_code,
        strategy=strategy, db_path=db_path)

    res.ran = True
    res.settled = stats["settled"]
    res.unchanged = len(playbooks) - len(outcomes)
    # 🔴 三分支计数取**真的被 UPDATE 到**的那些(R2-10),⛔ 不从 `outcomes` 里数 ——
    # 账上的数与库里的行对不上是本仓最不该出现的那一类。
    res.confirmed = stats[Verdict.CONFIRMED.value]
    res.rejected = stats[Verdict.REJECTED.value]
    res.observed = stats[Verdict.OBSERVED.value]
    res.data_quality = snap.quality_of(sorted(playbooks))
    res.notes.extend(snap.notes)          # ⚠ 追加,⛔ 不覆盖(R2-03 的版本核对留言)
    record_pushed(trade_date, SETTLE_SENTINEL, "", EVENT_SETTLE,
                  payload={"counts": res.counts}, db_path=db_path)
    logger.info(
        "[settle] %s 结算拍:成立 %d / 放弃 %d / 观察 %d(另 %d 只 9:29 已定案,⛔ 未改判)",
        trade_date, res.confirmed, res.rejected, res.observed, res.unchanged)
    return res


__all__ = [
    "SETTLE_SENTINEL", "EVENT_SETTLE",
    "is_settle_window", "open30_readings", "SettleRunResult", "run_settle_tick",
]
