"""**9:26 竞价核对表**的编排(V2.5.0 S8;自 `git show eac2823:.../auction/pipeline.py`
(373 行)取回再改,⛔ 未凭空重写)。

    9:26 起跑 · **9:29 硬截止** · 当日防重 · 窗口外零落库 · 返回待推项

⛔ **本模块不自己推送**(照 K8 原件的「落库 + 返回待推项」体例):真正的 APNs 由
`api/app.py::_morning_loop` 调 `notify.push_checklist_summary` 完成。

🔴 **取回时逐条保住的原有纪律**:
    · `is_auction_window(now)` = **交易日 且 `09:26:00 ≤ t < 09:29:00`**;
      时区 / 交易日判定唯一源 `neckline.calendar`,⛔ 别在新模块里再写一份 `+8`;
    · **当日只跑一次**:`neckline/dedup.py`,市场级 key(`ts_code` 为空);
      ⚠ **幂等次序照原件**:「当日已跑」标记落在**落库之后、返回之前** ——
      中途异常(标记未落)会被下一拍**干净重跑**(两张表都幂等);
    · 🔴 **⛔ 事后不许补跑**:窗口外调用一律 `skipped_reason` + **零落库**
      —— 补跑会拿 9:30 之后的价格冒充 9:26 那一刻的判断。
      ⚠ 例外只有**显式注入 `now`** 的 CLI / 回放 / 单测(同原件体例)。

🔴 **9:29 硬截止:从「daemon 线程」简化成一句朴素墙钟保护**(§5.7.1 明令)。
K8 时代那套 daemon 线程 + 结果盒子是为了兜住 **LLM 的不确定墙钟**(实测同一份输入
两次差 1.44 倍);K9 的核对表是**零 LLM 纯条件求值**,毫秒级 —— 那套机制没有了
要兜的东西。现在的保护是:落库前用真实时钟再看一眼,**已过 9:29 就不发布**
(记 `deadline_missed`,⛔ 不迟到发布)。⚠ 拉价那一层还有它自己的两层窗口保护
(拉价前复判 + `captured_in_window`),三层各管一段。

🔴 **零 LLM**:本模块与 `checklist.py` 都不 import `neckline.llm` / `neckline.search`
(守门 G7)。K8 的 `auction/llm.py` 与 `auction/mech.py` **⛔ 不取回**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from neckline.auction import (
    AUCTION_WINDOW_END,
    AUCTION_WINDOW_START,
    SKIP_ALREADY_RAN,
    SKIP_NO_LISTING,
    SKIP_NO_PLAYBOOK,
    SKIP_NOT_WINDOW,
)
from neckline.auction import checklist as checklist_mod
from neckline.auction import collect as ac
from neckline.auction import store as astore
from neckline.calendar import is_trading_day, prev_trading_day
from neckline.dedup import already_pushed, record_pushed
from neckline.data.realtime import DualQuote, Quote
from neckline.k9 import store as k9_store
from neckline.playbook import store as pb_store

logger = logging.getLogger(__name__)

#: 硬截止时刻 = 窗口右端(9:29)。
AUCTION_HARD_DEADLINE = AUCTION_WINDOW_END

#: 早晨任务防重作用域。
AUCTION_SCOPE = "auction"
EVENT_CHECKLIST = "checklist_tick"

#: 9:29 已过还没落完 → 记这个,**不发布**。
SKIP_DEADLINE_MISSED = "deadline_missed"


def is_auction_window(now: datetime) -> bool:
    """交易日 且 `09:26:00 ≤ now.time() < 09:29:00`。"""
    return is_trading_day(now.date()) and AUCTION_WINDOW_START <= now.time() < AUCTION_WINDOW_END


@dataclass
class ChecklistRunResult:
    """一次 9:26 拍的结果。⚠ **窗口外 / 当日已跑时 `ran=False` 且零落库**。"""

    trade_date: date
    now: datetime
    ran: bool = False
    skipped_reason: str = ""
    d0_date: Optional[date] = None
    listing_size: int = 0
    rejected: int = 0
    pending_open: int = 0
    no_quote: int = 0
    no_playbook: int = 0
    data_quality: str = ""
    checklist: Any = None
    notes: List[str] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, Any]:
        """推送措辞层要的那几个数(**单一源在这里**)。"""
        return {
            "rejected": self.rejected, "pendingOpen": self.pending_open,
            "noQuote": self.no_quote, "noPlaybook": self.no_playbook,
            "dataQuality": self.data_quality,
        }

    @property
    def should_push(self) -> bool:
        """推送门槛(**单一源在这里**)。

        🔴 **每个交易日的核对表都推一条** —— 与 K8 竞价层「平静的早晨不发」
        刻意相反:K8 推的是**提醒**(没事就别吵),K9 推的是**当日清单的核对结果**,
        「今天一只都没触发放弃」本身就是用户 9:30 要拿去做决定的信息。
        ⚠ `ran=False` 恒 `False`:根本没跑 ⛔ 不许推(那会把「没跑」讲成「跑了、没事」)。
        ⚠ 清单为空(`listing_size == 0`)也不推:昨天就没有票要核对。
        """
        return bool(self.ran and self.listing_size > 0)


def run_checklist_tick(
    now: datetime,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    strategy: str = "K9",
    dual_quotes_fn: Optional[Callable[[List[str]], Dict[str, DualQuote]]] = None,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    deadline: Optional[datetime] = None,
) -> ChecklistRunResult:
    """跑一次竞价核对(9:26,当日只跑一次)。**只落库 + 返回待推项。**"""
    trade_date = now.date()
    res = ChecklistRunResult(trade_date=trade_date, now=now)

    # 1. 窗口 / 防重 —— ⛔ 不在窗口一律零落库(事后不许补跑)
    if not is_auction_window(now):
        res.skipped_reason = SKIP_NOT_WINDOW
        return res
    if already_pushed(trade_date, AUCTION_SCOPE, "", EVENT_CHECKLIST, db_path=db_path):
        res.skipped_reason = SKIP_ALREADY_RAN
        return res

    d0 = prev_trading_day(trade_date)
    res.d0_date = d0
    listing = k9_store.load_listing(d0, strategy=strategy, db_path=db_path)
    if not listing:
        # 「昨天没有清单」是**可信的空**(报告三态里的 `empty` / `not_run`),
        # ⛔ 不落库、⛔ 不推送 —— 今天没有要核对的东西。
        res.skipped_reason = SKIP_NO_LISTING
        return res
    res.listing_size = len(listing)
    codes = [r["ts_code"] for r in listing]
    names = {r["ts_code"]: r.get("name") for r in listing}
    playbooks = pb_store.load_latest(d0, codes=codes, db_path=db_path)
    if not playbooks:
        # 有清单、但一份预案都没冻结 → ⛔ 不现编条件、⛔ 零落库。
        res.skipped_reason = SKIP_NO_PLAYBOOK
        res.no_playbook = len(codes)
        return res

    clock = now_fn or datetime.now
    # ⚠ **先读 D0 冻结事实包**:跨源冲突钩子要拿 `prev_*` 才判得了「放弃」,
    # 而钩子在拉价的循环里被调用 —— 读在前面,`collect_snapshot` 就不必再读一遍。
    prev_bars = ac.load_prev_bars(d0, codes, parquet_dir=parquet_dir, db_path=db_path)

    def _rejection_of(code: str, quote: Quote) -> Optional[bool]:
        """跨源冲突判定用的三态钩子:**这一源**的读数会不会触发「放弃」。

        ⚠ 只比「放弃」分支 —— 9:26 判不出成立(裁定 10),拿一个两边都
        `UNKNOWN` 的分支去比只会得到恒等的假安心。"""
        pb = playbooks.get(code)
        if pb is None:
            return None
        prev = prev_bars.get(code)
        from neckline.playbook.model import MetricRef, gap_percent_points

        price = getattr(quote, "price", None)
        price = float(price) if price else None
        readings = {
            MetricRef.AUCTION_PRICE: price,
            MetricRef.AUCTION_GAP_PCT: gap_percent_points(price, prev.close if prev else None),
            MetricRef.FIRST30_LOW: price,
            MetricRef.PREV_CLOSE: prev.close if prev else None,
            MetricRef.PREV_LOW: prev.low if prev else None,
            MetricRef.PREV_HIGH: prev.high if prev else None,
        }
        return checklist_mod.rejection_triggered(pb, readings)

    # 2. 冻结抓取(自己拉一次价;拉价前复判窗口 → 越窗零落库)
    snap = ac.collect_snapshot(
        trade_date, now, codes=codes,
        window=(AUCTION_WINDOW_START, AUCTION_WINDOW_END), d0_date=d0,
        db_path=db_path, parquet_dir=parquet_dir,
        dual_quotes_fn=dual_quotes_fn, quotes_fn=quotes_fn, now_fn=clock,
        rejection_of=_rejection_of, prev_bars=prev_bars,
    )
    if snap.fetch_skipped_reason:
        # 🔴 真到拉价那一刻窗口已关 → **零落库**,⛔ 也不落「当日已跑」标记
        # (今天压根没跑成)。
        res.skipped_reason = snap.fetch_skipped_reason
        res.notes = list(snap.notes)
        return res

    # 3. 代入 D0 冻结预案 → 两段核对表(零 LLM,毫秒级)
    checklist = checklist_mod.build_checklist(
        snap, playbooks=playbooks, names=names, listing_codes=codes)

    # 4. 🔴 9:29 硬截止的**朴素墙钟保护**:落库前用真实时钟再看一眼。
    #    过点了就**不发布**(⛔ 不迟到发布 —— 一张 9:31 才落的表会假装是 9:29 之前
    #    给出的结论)。⚠ 同样**零落库**、也不落「当日已跑」标记。
    deadline = deadline or datetime.combine(trade_date, AUCTION_HARD_DEADLINE)
    if clock() >= deadline:
        logger.warning("[auction] 9:29 硬截止已过而本拍尚未落库,记「未完成」,⛔ 不迟到发布")
        res.skipped_reason = SKIP_DEADLINE_MISSED
        res.notes = list(snap.notes) + [SKIP_DEADLINE_MISSED]
        return res

    # 5. 落库(整张表 + 逐票行)。⚠ 先落库,再落「当日已跑」标记 —— 中途异常
    #    会被下一拍干净重跑(两处都幂等)。
    astore.save_checklist(checklist, strategy=strategy, db_path=db_path)
    astore.record_auction_stage(checklist, strategy=strategy, db_path=db_path)

    res.ran = True
    res.checklist = checklist
    res.rejected = len(checklist.rejected)
    res.pending_open = len(checklist.pending_open)
    res.no_quote = len(checklist.no_quote_codes)
    res.no_playbook = len(checklist.no_playbook_codes)
    res.data_quality = checklist.data_quality
    res.notes = list(checklist.notes)
    record_pushed(trade_date, AUCTION_SCOPE, "", EVENT_CHECKLIST,
                  payload={"counts": res.counts}, db_path=db_path)
    logger.info("[auction] %s 竞价核对表:已触发放弃 %d / 待开盘后观察 %d(质量 %s)",
                trade_date, res.rejected, res.pending_open, res.data_quality)
    return res


__all__ = [
    "AUCTION_WINDOW_START", "AUCTION_WINDOW_END", "AUCTION_HARD_DEADLINE",
    "AUCTION_SCOPE", "EVENT_CHECKLIST", "SKIP_DEADLINE_MISSED",
    "is_auction_window", "ChecklistRunResult", "run_checklist_tick",
]
