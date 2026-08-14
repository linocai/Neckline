"""竞价层的**编排**(V2.3.3-④):9:26 起跑 · **9:29 硬截止** · 当日防重 · 返回待推项。

⛔ **本模块不自己推送**(照 `precall.run_precall_tick` 既有「落库 + 返回待推项」体例):
真正的 APNs 由 `api/app.py::_sentinel_loop` 调 `notify.push_auction_summary` 完成。

**窗口与防重**(照 `precall.py` 逐条平移):
    · `is_auction_window(now)` = **交易日 且 `09:26:00 ≤ t < 09:29:00`**;时区 / 交易日
      判定唯一源 `neckline.calendar`(`CN_TZ` + `is_trading_day`),⛔ 别在新模块里
      再写一份 `timezone(timedelta(hours=8))`;
    · 当日只跑一次:`sentinel/dedup.py`,市场级 key `(trade_date, "auction", "", "tick")`。
      ⚠ **幂等次序照 `run_precall_tick`**:推送在函数**返回之后**由循环执行,「当日已跑」
      标记在返回**之前**落库 → 中途异常(标记未落)会被下一拍**干净重跑**
      (两张表都是 `INSERT OR IGNORE`,幂等)。
    · ⚠ 新增的 `sentinel='auction'` 台账行 `ts_code` 为空 = **市场级标记**,
      `api/app.py::board` 的既有过滤天然把它挡在盘中看板事件列表之外(同 `capture`/
      `precall` 的 tick 标记先例)。

🔴 **⛔ 事后不许补跑**(K8「报告发出后结束、不持续观察 9:30 以后的价格」):窗口外调用
一律 `skipped_reason='not_auction_window'` + **零落库** —— 补跑会拿 9:30 之后的价格冒充
9:26 那一刻的判断。⚠ 例外只有**显式注入 `now`** 的 CLI / 回放 / 单测(同
`precall.run_precall_tick(now=…)` 既有体例)。

🔴 **9:29 硬截止(结构性保证,⛔ 不靠"应该会及时回来")**:V2.3.2 批 ⑥ 实测 GLM 流式
**同一份输入两次差 1.44 倍**(115.2s vs 166.2s)。9:26 起跑,最坏 9:28:46 才回;而流式的
read timeout 语义是 **chunk 间隔**(90s),**墙钟无固定上限是刻意的** → 单靠 provider
超时兜不住 9:29。所以这里用「另起一个 **daemon 线程** + 主线程限时等」:
    · 超时 → **立刻**按 `pending_explanation` 结案返回,**⛔ 不等它**;
    · 那个线程还在跑是正常的,它的结果**⛔ 一律丢弃** —— 9:35 才落进去的结论会假装是
      9:29 之前给出的。**双保险**(⚠ 施工图 ④-B 写的是「`deadline_passed` 标志位 +
      幂等 WHERE」,实际落地成下面这两条,**结构上更强**,如实登记):
      ① **`llm.explain()` 的签名里根本没有 store 句柄**,工作线程只写一个内存 `box`
         —— 迟到的结论**写不到库里去**,不是"来得及拦住",是**够不着**;
      ② `store.finalize_*` 带幂等 `WHERE llm_stage='pending'` —— 主线程结案后那一行
         已不是 pending,即便将来有人给 LLM 层递了句柄也改不动。
      🔴 ⛔ **别为了"对齐施工图字面"去补一个 `_deadline_passed` 标志位** —— 现有设计
      不依赖任何人记得检查它(复审 🔵-1)。
    · ⚠ 硬截止同时**保护哨兵主循环**:盘前分支里这一段最多阻塞到 9:29,9:30 的
      intraday 第一拍不受影响。

⚠ **实现取舍(如实登记)**:施工图写的是 `ThreadPoolExecutor(max_workers=1, daemon 线程)`,
但 `ThreadPoolExecutor` 的工作线程**不能设 daemon**(`concurrent.futures` 在解释器退出时
会 `join` 它们)—— 那正好与「进程退出不阻塞」这条要求相反。故改用裸
`threading.Thread(daemon=True)` + 一个结果盒子,**语义与施工图要求的完全一致**。

⚠ **两条已知行为,登记在案、⛔ 别当 bug 去"修"**(V2.3.3 复审 🔵-9 / 🔵-10;
§七 **P4-72**):
    1. **D0 零篮子的早晨照样打一次 LLM**:`known_basket_keys=[]` → 模型给的所有篮子
       条目都会被丢弃,只留市场段 `overview`。**市场段本身有价值**(指数环境 + 竞价
       强势股锚点),所以这不是浪费;但要知道「没有篮子的日子也花一次调用」。
    2. **窗口内中途异常会导致重复调用**:`_record_tick`(当日已跑标记)刻意落在
       `finalize` **之后**(施工图 ④-A 的「干净重跑」)。代价 = 若 `finalize_*` 连续
       抛异常(如 DB 忙),9:26–9:29 的 6 拍最多能各打一次 LLM;而**每拍新建一本
       `BudgetLedger`** → 预算账拦不住(它本来就对"只有一次调用"的流程零上界)。
       真正的天花板仍是 9:29 硬截止 —— 最多 6 次、且都在 3 分钟内。
       **部署次日查 journal 时留意一下**;⛔ 别为它把标记提到 `finalize` 之前
       (那会让中途异常变成"今天再也不跑了",代价更大)。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from neckline.auction import (
    LLM_NO_PROVIDER,
    LLM_OK,
    LLM_PENDING_EXPLANATION,
    RISK_LLM_NOTE,
    VERDICT_CONFIRM,
    VERDICT_NEUTRAL,
    VERDICT_PENDING_EXPLANATION,
    VERDICT_VETO,
)
from neckline.auction import collect as ac
from neckline.auction import llm as al
from neckline.auction import mech as am
from neckline.auction import store as astore
from neckline.calendar import is_trading_day
from neckline.llm.budget import BudgetLedger
from neckline.sentinel.dedup import already_pushed, record_pushed
from neckline.sentinel.quotes import DualQuote, Quote
from neckline.sentinel.universe import DEFAULT_BREADTH_CAP

logger = logging.getLogger(__name__)

#: 竞价确认窗口(K8 §二十 原文给的窗口,⛔ 不是本项目发明的数)。
AUCTION_WINDOW_START = ac.AUCTION_WINDOW_START      # 09:26
AUCTION_WINDOW_END = ac.AUCTION_WINDOW_END          # 09:29
#: 硬截止时刻 = 窗口右端(9:29)。
AUCTION_HARD_DEADLINE = AUCTION_WINDOW_END

#: `sentinel_events` 的市场级台账 key(`ts_code` 为空 → 不进盘中看板事件列表)。
AUCTION_SENTINEL = "auction"
EVENT_TICK = "tick"

SKIP_NOT_WINDOW = "not_auction_window"
SKIP_ALREADY_RAN = "already_ran"
#: 🔴 拉价**前**用真实时钟复判,发现窗口已关(复审 🟡-2)—— **零落库**。
#: ⚠ 与 `SKIP_NOT_WINDOW` 分开:那条是"这一拍的名义时刻就不在窗口",这条是"名义时刻
#: 在窗口、但真到拉价那一刻已经越窗了"(precall + capture + 组清单吃掉了几分钟)。
#: 混成一个码 = 部署次日查 journal 时分不出是排程错了还是慢了。
SKIP_WINDOW_CLOSED = ac.SKIP_WINDOW_CLOSED


def is_auction_window(now: datetime) -> bool:
    """交易日 且 `09:26:00 ≤ now.time() < 09:29:00`。

    ⚠ 与 `precall.is_precall_window`(9:25:30–9:30)和 `capture.is_auction_capture_window`
    (9:25–9:30)**刻意重叠**:三者各自防重、各拉各的价(解耦),同一拍里各走各的分支。
    """
    return is_trading_day(now.date()) and AUCTION_WINDOW_START <= now.time() < AUCTION_WINDOW_END


@dataclass
class AuctionRunResult:
    trade_date: date
    now: datetime
    ran: bool = False
    skipped_reason: str = ""             # "not_auction_window" | "already_ran" | ""
    baskets_covered: int = 0
    llm_stage: str = ""
    llm_elapsed_ms: Optional[int] = None
    deadline_hit: bool = False           # True = 9:29 到了模型还没回(**设计内**)
    confirm: int = 0
    neutral: int = 0
    veto: int = 0
    pending: int = 0
    hit_invalidation_codes: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, Any]:
        """推送措辞层要的那几个数。⚠ `llm_stage` 是**字符串**混在里面(措辞要按它
        决定加不加那句「本次 LLM 未给出解释」),同 `PrecallResult.counts` 的位置。"""
        return {
            "confirm": self.confirm, "neutral": self.neutral, "veto": self.veto,
            "pending_explanation": self.pending,
            "hit_invalidation": len(self.hit_invalidation_codes),
            "llm_stage": self.llm_stage,
        }

    @property
    def should_push(self) -> bool:
        """推送门槛(**单一源在这里**)= `veto 篮数 > 0` **或** `命中 D0 失效位的票数 > 0`
        **或** `llm_stage` 非 `ok`。

        🔴 **⛔ 不许"平静的早晨也发一条"** —— 同 `PrecallResult.should_push_summary`
        的既定纪律(V2.2-⑤-B 已取消过一次"必发豁免",⛔ 别以别的形式加回来)。

        ⚠ **`ran=False` 恒 `False`**:窗口外 / 当日已跑那两条路径 `llm_stage` 是空串,
        光看「非 ok」会把"根本没跑"读成"跑了但 LLM 没回"而推一条 —— 门槛的单一源在这里,
        所以这个短路也写在这里,⛔ 不留给调用方各自记得加。
        """
        if not self.ran:
            return False
        return bool(self.veto > 0 or self.hit_invalidation_codes or self.llm_stage != LLM_OK)


def run_auction_pipeline(
    now: datetime,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
    #: 🔴 V2.4.0 P2.2:双源批量抓取的注入点(缺省 = `sentinel.quotes.get_quotes_dual`)。
    #: ⚠ 只给 `quotes_fn`(单源替身)时备源恒缺席、跨源冲突结构性为空 —— 那是替身的
    #: 局限,**不是"已核对无冲突"**(逐票账里 `checks` 只有一条,一眼看得出)。
    dual_quotes_fn: Optional[Callable[[List[str]], Dict[str, DualQuote]]] = None,
    provider: Optional[Any] = None,
    provider_factory: Optional[Callable[[], Any]] = None,
    ledger: Optional[BudgetLedger] = None,
    deadline: Optional[datetime] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> AuctionRunResult:
    """跑一次竞价确认(9:26,当日只跑一次)。**只落库 + 返回待推项**。

    `provider` / `provider_factory` / `quotes_fn` / `deadline` / `now_fn` 都可注入,供
    冒烟脚本与单测走合成数据 + 假 provider,**不改一行编排**(同 `run_precall_tick`
    的既有体例)。缺省时 provider 走 `llm/factory.get_provider(TASK_AUCTION)` —— 那是
    流式与 chunk 间隔超时的**唯一接线点**。

    **预算账每次跑新建一本**(同 `weekly.py` 先例)。🔴 ⛔ 别把它当兜底:
    `exhausted()` 是**调用前**检查,而整个流程只有一次调用 → 预算账对这次调用零上界;
    真正的天花板是 **9:29 硬截止**。
    """
    trade_date = now.date()
    res = AuctionRunResult(trade_date=trade_date, now=now)

    # 1. 窗口 / 防重判定 —— ⛔ 不在窗口一律零落库(事后不许补跑)
    if not is_auction_window(now):
        res.skipped_reason = SKIP_NOT_WINDOW
        return res
    if already_pushed(trade_date, AUCTION_SENTINEL, "", EVENT_TICK, db_path=db_path):
        res.skipped_reason = SKIP_ALREADY_RAN
        return res

    # 2. 冻结抓取(自己拉一次价,⛔ 不搭 precall / capture 的便车)
    #    🔴 `now_fn` = **真实时钟**,一路传到 `collect`:`captured_at` 取拉完价那一刻,
    #    并在拉价前用它复判一次窗口(复审 🟡-2)。
    clock = now_fn or datetime.now
    snap = ac.collect_auction_snapshot(
        trade_date, now, db_path=db_path, parquet_dir=parquet_dir,
        breadth_cap=breadth_cap, quotes_fn=quotes_fn, dual_quotes_fn=dual_quotes_fn,
        now_fn=clock,
    )
    if snap.fetch_skipped_reason:
        # 🔴 真到拉价那一刻窗口已关 → **零落库**(〇b-4:补跑会拿 9:30 之后的价格冒充
        # 9:26 那一刻的判断)。⛔ 也不落「当日已跑」标记 —— 今天压根没跑成。
        res.skipped_reason = snap.fetch_skipped_reason
        res.notes = list(snap.notes)
        return res
    # 3. 机械层(零 LLM、零结论)
    mech = am.build_mech(snap, db_path=db_path, parquet_dir=parquet_dir)

    # 4. 🔴 **第一次写必须在 LLM 之前** —— 这是「LLM 暂时不可用时,机械层继续输出数据
    #    报告和明确失效警报」(K8 §二十)的结构性保证,不是"顺序上先写一下"。
    astore.save_mechanical(mech, db_path=db_path)
    res.ran = True
    res.baskets_covered = len(mech.baskets)
    res.hit_invalidation_codes = [c for b in mech.baskets for c in b.hit_invalidation_codes]
    res.notes = list(mech.notes)

    # 5. 硬截止余量(同一个真实时钟,⛔ 不用那一拍的名义 `now`)
    deadline = deadline or datetime.combine(trade_date, AUCTION_HARD_DEADLINE)
    remaining = (deadline - clock()).total_seconds()
    if provider is None and provider_factory is None:
        from neckline.llm.factory import get_provider

        provider_factory = lambda: get_provider(al.AUCTION_TASK, db_path=db_path)  # noqa: E731
    if remaining <= 0:
        res.deadline_hit = True
        _finalize_pending(mech, res, llm_stage=LLM_PENDING_EXPLANATION, db_path=db_path,
                          note="9:26 那一拍开始时 9:29 已过,本次不发起竞价解释调用")
        _record_tick(trade_date, res, db_path=db_path)
        return res

    prov = provider if provider is not None else _safe_provider(provider_factory)
    if prov is None:
        # 施工图 ④-B 第 5 步的另一半:没有可用 provider → **压根不起线程**,
        # 直接按 `provider_none` 结案。机械段与「命中 D0 失效位」照常在库里
        # (K8:LLM 暂时不可用时,机械层继续输出数据报告和明确失效警报)。
        _finalize_pending(mech, res, llm_stage=LLM_NO_PROVIDER, db_path=db_path,
                          note="未配置可用的 LLM provider,本次竞价结论全部标『待解释』")
        _record_tick(trade_date, res, db_path=db_path)
        return res

    # 6. 另起 **daemon 线程**跑 LLM,主线程最多等到 9:29。超时**不等它**,
    #    那条流式调用还在跑是正常的,但它的结果**一律丢弃**(〇b-5)。
    box: Dict[str, Any] = {}
    ledger = ledger if ledger is not None else BudgetLedger()

    def _work() -> None:
        try:
            box["result"] = al.explain(mech, provider=prov, ledger=ledger)
        except Exception as exc:  # noqa: BLE001 —— 线程内异常不能静默丢
            box["error"] = exc
            logger.warning("[auction] 竞价解释线程异常", exc_info=True)

    th = threading.Thread(target=_work, name="neckline-auction-llm", daemon=True)
    th.start()
    th.join(timeout=max(0.0, remaining))
    if th.is_alive():
        # 🔴 9:29 到了模型还没回 —— **立刻结案**,⛔ 不等它。
        res.deadline_hit = True
        _finalize_pending(mech, res, llm_stage=LLM_PENDING_EXPLANATION, db_path=db_path,
                          note="9:29 硬截止到达时 LLM 尚未返回,本次结论全部标『待解释』"
                               "(设计内:迟到的结论一律丢弃)")
        _record_tick(trade_date, res, db_path=db_path)
        return res

    out: Optional[al.AuctionLLMResult] = box.get("result")
    if out is None:
        _finalize_pending(mech, res, llm_stage=LLM_PENDING_EXPLANATION, db_path=db_path,
                          note="竞价解释线程未产出结果,本次结论全部标『待解释』")
        _record_tick(trade_date, res, db_path=db_path)
        return res

    # 7. 三道机械夹逼闸 → 第二次写(只 UPDATE LLM 列白名单)
    _finalize_with_llm(mech, out, res, db_path=db_path)
    _record_tick(trade_date, res, db_path=db_path)
    return res


def _safe_provider(factory: Optional[Callable[[], Any]]) -> Optional[Any]:
    if factory is None:
        return None
    try:
        return factory()
    except Exception:  # noqa: BLE001 —— provider 取不到只让 LLM 半份缺席
        logger.warning("[auction] 取 LLM provider 失败,本次结论全部标『待解释』", exc_info=True)
        return None


def _finalize_pending(mech: Any, res: AuctionRunResult, *, llm_stage: str,
                      db_path: Optional[Path], note: str) -> None:
    """LLM 半份缺席时的结案:逐篮 `pending_explanation`,**机械段那份原样保留**
    (`risks=None` → 不覆盖机械异常;「命中 D0 失效位」这条独立警报一个字都不丢)。"""
    res.llm_stage = llm_stage
    res.pending = len(mech.baskets)
    res.notes.append(note)
    astore.finalize_report(mech.trade_date, llm_stage=llm_stage, db_path=db_path,
                           notes=res.notes)
    for b in mech.baskets:
        astore.finalize_verdict(b.basket_id, verdict=VERDICT_PENDING_EXPLANATION,
                                llm_stage=llm_stage, db_path=db_path)


def _finalize_with_llm(mech: Any, out: "al.AuctionLLMResult", res: AuctionRunResult, *,
                       db_path: Optional[Path]) -> None:
    res.llm_stage = out.llm_stage
    res.llm_elapsed_ms = out.elapsed_ms
    res.notes.extend(out.notes)
    risks: List[Dict[str, str]] = list(mech.market.risks)
    any_manual = False
    for b in mech.baskets:
        fields = out.by_basket.get(b.basket_key)
        verdict, clamped_by = al.clamp_verdict(fields, b)
        note = al.clamp_risk_note(b.name, fields.verdict if fields else None,
                                  verdict, clamped_by)
        if note is not None:
            risks.append(note)
        conflict = al.evidence_conflict_note(b.name, fields)
        if conflict is not None:
            risks.append(conflict)
        manual = al.manual_note_attached(verdict, fields, clamped_by)
        any_manual = any_manual or manual
        astore.finalize_verdict(
            b.basket_id, verdict=verdict,
            verdict_raw=(fields.verdict if fields else None),
            clamped_by=clamped_by,
            reasons=(fields.reasons if fields else []),
            llm_fields=(fields.to_dict() if fields else {}),
            manual_note_attached=manual, llm_stage=out.llm_stage, db_path=db_path,
        )
        if verdict == VERDICT_CONFIRM:
            res.confirm += 1
        elif verdict == VERDICT_NEUTRAL:
            res.neutral += 1
        elif verdict == VERDICT_VETO:
            res.veto += 1
        else:
            res.pending += 1
    # LLM 自己补的风险条目(小报告第 4 块的 LLM 半份)
    risks.extend({"kind": RISK_LLM_NOTE, "text": t} for t in out.risks)
    astore.finalize_report(
        mech.trade_date, llm_stage=out.llm_stage, market_overview=out.market_overview,
        # 🟡-1:`anchors_note` 是 ③-B 契约里 LLM **必须**输出的键(prompt 逐字要求了)。
        # 原先模型写了、解析了、然后在这一层被整条丢掉 —— 契约字段永远 nil、界面那个
        # 分支永远不执行、每次调用白花 token,而**没有任何东西会报错**。
        anchors_note=out.anchors_note,
        risks=risks, manual_note_attached=any_manual, llm_elapsed_ms=out.elapsed_ms,
        notes=res.notes, db_path=db_path,
    )


def _record_tick(trade_date: date, res: AuctionRunResult, *, db_path: Optional[Path]) -> None:
    """市场级「当日 tick 已跑」标记(**返回前落**,见模块头的幂等说明)。
    `ts_code` 为空 → `api/app.py::board` 的既有过滤天然把它挡在看板事件列表之外。"""
    record_pushed(trade_date, AUCTION_SENTINEL, "", EVENT_TICK,
                  payload={"counts": res.counts}, db_path=db_path)


__all__ = [
    "AUCTION_WINDOW_START", "AUCTION_WINDOW_END", "AUCTION_HARD_DEADLINE",
    "AUCTION_SENTINEL", "EVENT_TICK", "SKIP_NOT_WINDOW", "SKIP_ALREADY_RAN",
    "SKIP_WINDOW_CLOSED",
    "is_auction_window", "AuctionRunResult", "run_auction_pipeline",
]
