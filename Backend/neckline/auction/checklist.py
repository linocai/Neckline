"""**9:26—9:29 竞价核对表**(V2.5.0 S8;重写 K8 的 `auction/mech.py`,K9 §七 / 架构 §四)。

    冻结的集合竞价读数 + D0 冻结预案 → 一张**只有两段**的表

        · **已触发放弃**       竞价价已经跌破预案的失效条件
        · **其余待开盘后观察**  含「条件明确没触发」与「这一项今天早上还读不到」

🔴 **⛔ 结构上没有「成立」**(裁定 10,PROJECT_PLAN §5.7.1/§5.7.2)。三重锁:

    ① **类型层**:`ChecklistVerdict` 是**二值枚举** `{rejected, pending_open}` ——
       「成立」不是一个取值,不是靠谁记得别写。模块加载时 `assert len(...) == 2`。
    ② **求值层**:本模块**只碰** `playbook.rejection_branch`。
       `confirmation_branch` / `settle_verdict` / `Verdict.CONFIRMED` 在本文件
       **零命中**(守门单测 AST 扫源码)。
    ③ **落库层**:`store.record_auction_stage()` 收的是二值枚举,
       映射到终值是**全函数** `rejected → Verdict.REJECTED`;
       `Verdict.CONFIRMED` 在这条写路径上**根本构造不出来**。

**为什么 9:29 判不出成立**(依据,⛔ 别当越界功能删掉):K9 §6.3 的四个成立分支
**全部含有「前 30 分钟」这一合取项**(p1 `前30分钟最低价 ≥ [B]`、p2 `前30分钟不创昨日
新低`、p3/p4 `前30分钟不破 [A]`)—— 9:29 时前 30 分钟**还没发生**。
「成立」只能由 D1 10:00 的结算拍产生(`settle.py`)。

⚠ **`first30_low` 在这一拍绑到竞价价**(施工侧的结构判断,已登记 §14,请复核):
`MetricRef.FIRST30_LOW` 的定义是「**本场至今**的最低价(含 9:25 竞价成交)」——
9:26 时本场只有一笔竞价成交,那个最低价**就是**竞价价。它是**单调下行**的量:
9:26 已经跌破的价位,10:00 一定仍然跌破 —— 这正是「9:29 判的放弃先到先定、
10:00 ⛔ 不改判」在语义上站得住的原因,也是 §5.7.2「放弃分支四个全是单条破位判定
→ **竞价价就能触发**」那句话的落地方式。

🔴 **零 LLM**(架构 §四「纯条件求值」):本模块不 import `neckline.llm` / `neckline.search`。
求值是毫秒级的 —— 这就是 9:29 硬截止可以从 K8 的「daemon 线程兜住 LLM 墙钟」
简化成 `pipeline.py` 里一句朴素墙钟保护的原因(§5.7.1)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.auction.collect import Snapshot
from neckline.playbook.evaluate import BranchOutcome, Truth, evaluate_branch
from neckline.playbook.model import MetricRef, Playbook, gap_percent_points

logger = logging.getLogger(__name__)


class ChecklistVerdict(str, Enum):
    """🔴 **二值闭合枚举**(裁定 10)。核对表只有这两段,⛔ 没有第三个取值。"""

    REJECTED = "rejected"            # 已触发放弃
    PENDING_OPEN = "pending_open"    # 其余待开盘后观察


#: 🔴 结构性断言:核对表恰好两段。加第三个成员 = **import 就炸**(守门 G20)。
assert len(ChecklistVerdict) == 2, "核对表必须恰好两段(裁定 10)"

#: 段名(全映射,⛔ 无 fallback)。
CHECKLIST_SEGMENT_LABEL: Mapping[ChecklistVerdict, str] = {
    ChecklistVerdict.REJECTED: "已触发放弃",
    ChecklistVerdict.PENDING_OPEN: "待开盘后观察",
}
assert set(CHECKLIST_SEGMENT_LABEL) == set(ChecklistVerdict)

#: 界面与报告都要带的那一行(裁定 11 / §5.11):把「谁在什么时候定成立」说清楚。
CHECKLIST_FOOTNOTE = "成立由 10:00 结算,9:30–10:00 由我自己判定。"


def auction_readings(snap: Snapshot, code: str) -> Dict[MetricRef, Optional[float]]:
    """**9:26 那一拍**能读到的量。

    ⚠ 刻意**不提供** `OPEN_PRICE` / `GAP_PCT` / `FIRST30_HIGH` —— 9:29 时开盘还没
    发生,给它们一个值就是编数。读不到 → 求值 `UNKNOWN` → 那一条件不成立也不失败。
    """
    prev = snap.prev_bars.get(code)
    price = snap.price_of(code)
    return {
        MetricRef.AUCTION_PRICE: price,
        MetricRef.AUCTION_GAP_PCT: gap_percent_points(price, prev.close if prev else None),
        # 见模块头:本场至今的最低价 = 竞价价(本场只有这一笔成交)。
        MetricRef.FIRST30_LOW: price,
        MetricRef.PREV_CLOSE: prev.close if prev else None,
        MetricRef.PREV_LOW: prev.low if prev else None,
        MetricRef.PREV_HIGH: prev.high if prev else None,
    }


@dataclass(frozen=True)
class ChecklistRow:
    """核对表上的一只票。"""

    ts_code: str
    name: Optional[str]
    pattern: str
    verdict: ChecklistVerdict
    playbook_version: int
    readings: Mapping[str, Optional[float]]
    branch: BranchOutcome                 # 「放弃」分支的逐条留痕
    quote_state: str                      # `QuoteQuality.freshness` 或 `""`(没抓到)

    def to_dict(self) -> Dict[str, object]:
        return {
            "tsCode": self.ts_code, "name": self.name, "pattern": self.pattern,
            "verdict": self.verdict.value,
            "segment": CHECKLIST_SEGMENT_LABEL[self.verdict],
            "playbookVersion": self.playbook_version,
            "readings": dict(self.readings),
            "rejectionBranch": self.branch.to_dict(),
            "quoteState": self.quote_state,
        }


@dataclass(frozen=True)
class Checklist:
    """一张 9:29 核对表。**两段,⛔ 没有第三段。**"""

    trade_date: date
    d0_date: date
    captured_at: datetime
    data_quality: str
    rows: Tuple[ChecklistRow, ...]
    #: D0 在清单上、但**这次没有可用读数**的票(它们仍落在「待观察」段,如实标注)。
    no_quote_codes: Tuple[str, ...] = ()
    #: D0 在清单上、但**没有冻结预案**的票(⛔ 不拿一份现编的条件顶替)。
    no_playbook_codes: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def segment(self, verdict: ChecklistVerdict) -> Tuple[ChecklistRow, ...]:
        return tuple(r for r in self.rows if r.verdict is verdict)

    @property
    def rejected(self) -> Tuple[ChecklistRow, ...]:
        return self.segment(ChecklistVerdict.REJECTED)

    @property
    def pending_open(self) -> Tuple[ChecklistRow, ...]:
        return self.segment(ChecklistVerdict.PENDING_OPEN)

    @property
    def counts(self) -> Dict[str, object]:
        """推送措辞层要的那几个数(**单一源在这里**)。"""
        return {
            "rejected": len(self.rejected),
            "pendingOpen": len(self.pending_open),
            "noQuote": len(self.no_quote_codes),
            "noPlaybook": len(self.no_playbook_codes),
            "dataQuality": self.data_quality,
        }

    def to_dict(self) -> Dict[str, object]:
        """API / 落库的 canonical 形状。

        🔴 响应体里**没有「成立」这个取值**(G20):两段就是全部,
        `footnote` 说明成立由 10:00 结算。"""
        return {
            "tradeDate": self.trade_date.strftime("%Y%m%d"),
            "d0Date": self.d0_date.strftime("%Y%m%d"),
            "capturedAt": self.captured_at.isoformat(timespec="seconds"),
            "dataQuality": self.data_quality,
            "segments": [
                {
                    "verdict": v.value,
                    "label": CHECKLIST_SEGMENT_LABEL[v],
                    "rows": [r.to_dict() for r in self.segment(v)],
                }
                for v in ChecklistVerdict
            ],
            "noQuoteCodes": list(self.no_quote_codes),
            "noPlaybookCodes": list(self.no_playbook_codes),
            "footnote": CHECKLIST_FOOTNOTE,
            "notes": list(self.notes),
        }


def rejection_triggered(playbook: Playbook, readings: Mapping[MetricRef, Optional[float]]) -> Optional[bool]:
    """「放弃」分支的**三态**判定(`True` 触发 / `False` 看过了没触发 / `None` 判不了)。

    🔴 三态而不是布尔:`None` 是「这个量今天早上还读不到」。把它折成 `False`
    就是把「没判」讲成「判过了、没触发」—— 跨源冲突判定(`quality.detect_conflict`)
    也吃这个三态,折平会把「一边判不了」讲成「两边看法不同」。
    """
    out = evaluate_branch(playbook.rejection_branch, readings)
    if out.truth is Truth.UNKNOWN:
        return None
    return out.truth is Truth.TRUE


def build_checklist(
    snap: Snapshot,
    *,
    playbooks: Mapping[str, Playbook],
    names: Optional[Mapping[str, Optional[str]]] = None,
    listing_codes: Optional[Sequence[str]] = None,
) -> Checklist:
    """代入 D0 冻结预案,出一张两段核对表。**纯函数**(不落库、不推送、零 LLM)。

    ⚠ 顺序:`rejected` 段按 `ts_code` 升序,`pending_open` 段同 —— 确定性,
    ⛔ 不按「谁跌得更狠」排(那是一句评价,不是核对)。
    """
    codes = list(dict.fromkeys(listing_codes if listing_codes is not None else snap.requested))
    name_of = dict(names or {})
    rows: List[ChecklistRow] = []
    no_quote: List[str] = []
    no_playbook: List[str] = []
    for code in sorted(codes):
        pb = playbooks.get(code)
        if pb is None:
            # ⛔ 没有冻结预案就**不判** —— 现编一份条件等于当场发明策略主张。
            no_playbook.append(code)
            continue
        readings = auction_readings(snap, code)
        outcome = evaluate_branch(pb.rejection_branch, readings)
        verdict = (ChecklistVerdict.REJECTED if outcome.truth is Truth.TRUE
                   else ChecklistVerdict.PENDING_OPEN)
        qq = snap.quote_quality.get(code)
        if not snap.is_usable(code):
            no_quote.append(code)
        rows.append(ChecklistRow(
            ts_code=code, name=name_of.get(code), pattern=pb.pattern,
            verdict=verdict, playbook_version=pb.version,
            readings={k.value: v for k, v in readings.items()},
            branch=outcome,
            quote_state=(qq.freshness if qq is not None else ""),
        ))
    judged = [r.ts_code for r in rows]
    return Checklist(
        trade_date=snap.trade_date, d0_date=snap.d0_date, captured_at=snap.captured_at,
        data_quality=snap.quality_of(judged),
        rows=tuple(rows), no_quote_codes=tuple(no_quote),
        no_playbook_codes=tuple(no_playbook), notes=tuple(snap.notes),
    )


__all__ = [
    "ChecklistVerdict", "CHECKLIST_SEGMENT_LABEL", "CHECKLIST_FOOTNOTE",
    "ChecklistRow", "Checklist",
    "auction_readings", "rejection_triggered", "build_checklist",
]
