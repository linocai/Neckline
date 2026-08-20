"""晚间编排链(V2.5.0 S7,PROJECT_PLAN §9.3 的新段序)。**批算侧**。

    16:05 拉数(`scripts/daily_update.py`,**不在本模块内**)
      → facts    架构第一层 · 事实包构建 + 冻结
      → k9       架构第二层 · 策略层(硬边界 → 四通道 → 排序 → 名额)
      → explain  架构第三层 · 解释层(消息面剔除 + 后备补位 + 资料聚合 → **清单定稿**)
      → playbook 架构第四层 · 预案层(四骨架 + LLM 填值 → D0 冻结)
      → report   报告装配 + 落库(+ APNs)

**为什么批算住这里、不塞进 `pipeline.py`**:那份是**在线路径**,P0-23 纪律要求它
只准读表、不准现算(§12 坑 1,2026-07-29 被生产 OOM 挡过一次上云)。本链的前两段
恰恰是批算大户。⛔ 别为了少一个文件把那条红线钝化掉。

**每段各自包保险丝**:任一段异常只记 WARNING + 在结果里标 `failed`,链继续往下走,
报告照出、缺席**如实披露**。⛔ 绝不因为某段失败而当日无报告 —— 唯一例外是最后那段
报告本身炸了(那才是真的没有报告,退出码必须非零)。

🔴 **`STATUS_NOT_BUILT` 留着但本版已无人用**:S9 / S10 落地后 `explain` / `playbook`
两段真的会跑。给一层没跑过的绿灯等于让报告宣称清单已经过消息面剔除 —— 那件事现在由
`k9_runs.listing_finalized_by` 这一列如实记着(`'k9'` = 还没过消息面,`'explain'` = 过了)。

🔴 **解释层的「后备补位」住在本模块,不住 `explain/`**:补位要按**名次**取下一名,
而解释层**不知道谁是第几名**(架构 §3.3 双盲)。见 `_run_explain` 的说明。

⚠ **`segments` 只挑跑哪几段,不改顺序**:传进来的集合会按 `CHAIN_SEGMENTS` 重排,
乱序传参不会得到乱序执行(三个 oneshot 单元靠这个接缝分段跑)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SEG_FACTS = "facts"
SEG_K9 = "k9"
SEG_EXPLAIN = "explain"
SEG_PLAYBOOK = "playbook"
SEG_REPORT = "report"

#: **顺序定死**(§9.3)。⛔ 改顺序前先读模块头。
CHAIN_SEGMENTS: Tuple[str, ...] = (SEG_FACTS, SEG_K9, SEG_EXPLAIN, SEG_PLAYBOOK, SEG_REPORT)

STATUS_OK = "ok"
STATUS_FAILED = "failed"        # 跑了、炸了(保险丝吞掉,报告里如实标)
STATUS_SKIPPED = "skipped"      # 调用方没要这一段(分段跑)
STATUS_EMPTY = "empty"          # 跑了、没有可做的(**合法输出**)
STATUS_NOT_BUILT = "not_built"  # 🔴 这一层本版还没建(S9 / S10),⛔ 不是 ok


@dataclass
class EveningChainResult:
    """一次晚间链的执行结果。逐段可查 —— 「没做」「做了没东西」「做了炸了」
    「还没建」四态分开,⛔ 不许合并成一个 bool。"""

    trade_date: date
    report_date: date
    status: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    bundle: Any = None
    #: 🔴 **策略层这一次的产物,在内存里传给解释层**(⛔ 不落库再读回来)。
    #: 解释层的后备补位要拿 `reserve`(按名次排好的后备票),而 `k9_listing_entries`
    #: 只装**入席**的那些 —— 落库再读回来就把补位所需的东西丢了。
    #: ⚠ 分段跑(`--segments explain`)时它是 `None`:那时本进程没跑过策略层,
    #: 解释层如实报「拿不到本日策略层产物」,⛔ 不去猜一个后备名单出来。
    k9_result: Any = None

    def ok(self, seg: str) -> bool:
        return self.status.get(seg) in (STATUS_OK, STATUS_EMPTY)


def run_evening_chain(
    trade_date: date,
    *,
    report_date: Optional[date] = None,
    segments: Sequence[str] = CHAIN_SEGMENTS,
    k9_params_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    save: bool = True,
) -> EveningChainResult:
    """跑晚间链(单进程串行)。"""
    report_date = report_date or trade_date
    wanted = [s for s in CHAIN_SEGMENTS if s in set(segments)]
    unknown = sorted(set(segments) - set(CHAIN_SEGMENTS))
    if unknown:
        raise ValueError(f"未知段名 {unknown};可用:{list(CHAIN_SEGMENTS)}")

    res = EveningChainResult(trade_date=trade_date, report_date=report_date)
    for seg in CHAIN_SEGMENTS:
        if seg not in wanted:
            res.status[seg] = STATUS_SKIPPED

    if SEG_FACTS in wanted:
        _fuse(res, SEG_FACTS, lambda: _run_facts(
            trade_date, db_path=db_path, parquet_dir=parquet_dir))
    if SEG_K9 in wanted:
        _fuse(res, SEG_K9, lambda: _run_k9(
            trade_date, k9_params_path=k9_params_path, db_path=db_path,
            parquet_dir=parquet_dir, chain=res))
        # 🔴 **k9 段最清楚它自己为什么没跑**(参数未配置 / 参数无效 + 逐条缺口)。
        # 把原因**带下去**给报告段 —— 报告⛔ 不去猜别人的失败原因,也⛔ 不自己再
        # 读一遍参数文件(那会得出一个与事实无关的结论,见 `pipeline.build_report`)。
        for gap in _upstream_gaps(res):
            res.notes.append(f"k9 段:{gap}")
    if SEG_EXPLAIN in wanted:
        _fuse(res, SEG_EXPLAIN, lambda: _run_explain(
            trade_date, result=res.k9_result, db_path=db_path, parquet_dir=parquet_dir))
    if SEG_PLAYBOOK in wanted:
        _fuse(res, SEG_PLAYBOOK, lambda: _run_playbook(
            trade_date, db_path=db_path, parquet_dir=parquet_dir))

    if SEG_REPORT in wanted:
        # 🔴 报告段**不包保险丝**:它炸了就是真的没有报告,异常必须往上抛
        # (退出码非零,systemd 那侧看得见)。
        res.bundle = _run_report(
            trade_date, report_date=report_date,
            upstream_gaps=_upstream_gaps(res),
            # 🔴 R2-04:「谁炸了」与「谁为什么没跑」分两个口子进报告 ——
            # 前者无论有没有运行账都要采纳,后者只在没有运行账时采纳。
            upstream_failures=_upstream_failures(res),
            db_path=db_path, parquet_dir=parquet_dir, save=save)
        res.status[SEG_REPORT] = STATUS_OK
        res.stats[SEG_REPORT] = {
            "state": res.bundle.state.value,
            "listingSize": res.bundle.listing_size,
        }
    return res


def _fuse(res: EveningChainResult, seg: str, fn) -> None:
    """一段的保险丝:炸了记 WARNING + 标 `failed`,链继续。"""
    try:
        status, stats = fn()
    except Exception as e:  # noqa: BLE001
        logger.warning("[evening] %s 段失败(已吞,链继续):%s", seg, e, exc_info=True)
        res.status[seg] = STATUS_FAILED
        res.notes.append(f"{seg} 段失败:{e}")
        return
    res.status[seg] = status
    if stats:
        res.stats[seg] = stats


def _run_facts(
    trade_date: date, *, db_path: Optional[Path], parquet_dir: Optional[Path]
) -> Tuple[str, Dict[str, Any]]:
    """事实包构建 + 冻结。**已冻结过 → 幂等跳过**(⛔ 不覆盖,§5.3.2 纪律 3)。"""
    from neckline.facts import pack as fact_pack
    from neckline.facts import store as fact_store

    try:
        existing = fact_store.load_pack(
            trade_date, parquet_dir=parquet_dir, db_path=db_path)
        return STATUS_OK, {"packId": existing.pack_id, "frozen": "already"}
    except fact_store.PackNotFrozen:
        pass

    built = fact_pack.build(trade_date, parquet_dir=parquet_dir, db_path=db_path)
    if isinstance(built, fact_pack.IncompletePack):
        # 数据未到齐 → **不冻结**;报告会因此走 `not_run` 并逐条列出缺口。
        logger.error("[evening] %s 事实包数据未到齐,不冻结:%s", trade_date, built.missing)
        return STATUS_EMPTY, {"missing": list(built.missing)}
    frozen = fact_store.freeze_pack(built, parquet_dir=parquet_dir, db_path=db_path)
    return STATUS_OK, {"packId": frozen.pack_id, "rows": frozen.row_count}


def _run_k9(
    trade_date: date, *, k9_params_path: Optional[Path],
    db_path: Optional[Path], parquet_dir: Optional[Path],
    chain: Optional["EveningChainResult"] = None,
) -> Tuple[str, Dict[str, Any]]:
    """策略层。⛔ **无默认参数路径**:没传 = 参数未配置 = 报告「今天没跑成」。"""
    from neckline.k9 import params as k9_params
    from neckline.k9 import run as k9_run

    if k9_params_path is None:
        logger.error(
            "[evening] 未提供 --k9-params;策略层不跑,报告将出「今天没跑成 · 参数未配置」"
            "(裁定 5:⛔ 不使用任何默认值)")
        return STATUS_EMPTY, {"reason": "params_not_configured"}
    try:
        params = k9_params.load(k9_params_path, db_path=db_path)
    except k9_params.ParamsUnavailable as e:
        logger.error("[evening] 参数包无效,策略层不跑:%s", e.describe())
        return STATUS_EMPTY, {"reason": "params_invalid", "gaps": e.gaps()}

    result, run_id = k9_run.run_k9(
        trade_date, params=params, parquet_dir=parquet_dir, db_path=db_path)
    if chain is not None:
        # 🔴 内存传给解释层(见 `EveningChainResult.k9_result` 的说明)。
        chain.k9_result = _K9Handoff(result=result, run_id=run_id, params=params)
    return STATUS_OK, {
        "runId": run_id,
        "seated": result.shortlist.size,
        "tierUsed": result.shortlist.tier_used.value,
        "capacityShort": result.shortlist.capacity_short,
    }


@dataclass
class _K9Handoff:
    """策略层 → 解释层的**进程内**交接件(⛔ 不落库、⛔ 不下发)。"""

    result: Any
    run_id: str
    params: Any


# ══════════════════════════════════════════════════════════════════════════
# 解释层(S9)—— 🔴 **补位决定住在编排器里,双盲不破**
# ══════════════════════════════════════════════════════════════════════════

def _run_explain(
    trade_date: date, *, result: Any,
    db_path: Optional[Path], parquet_dir: Optional[Path],
    provider: Any = None, transport: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """消息面剔除 + 后备补位 + 资料聚合,最后**定稿清单**(§5.5)。

    🔴 **为什么这段接线住编排器、不住 `explain/`**:补位要按**名次**从后备票里取
    下一名,而解释层**不知道谁是第几名**(架构 §3.3 双盲)。守门单测断言
    `explain/**` 零 import `neckline.k9` —— 那条边界的另一半就是:知道名次的这一段
    必须待在编排器里。⛔ 别把本函数搬进 `explain/`。

    流程(§5.5 逐字):
        策略层出 seated + reserve
          → 解释层处理 seated
          → 每剔除一只,从 reserve 取下一名再跑解释层
          → 最多 `params.explain.maxBackfillRounds` 轮
          → 定稿
    """
    from neckline.explain import aggregate as explain_aggregate
    from neckline.explain import input as explain_input
    from neckline.explain import news_exclusion as news_mod
    from neckline.explain import store as explain_store
    from neckline.k9 import store as k9_store
    from neckline.k9.contract import Shortlist
    from neckline.llm.factory import get_provider
    from neckline.llm.router import TASK_EXPLAIN, TASK_NEWS_SCAN

    if result is None:
        # 分段跑时本进程没碰过策略层 —— ⛔ 不去猜一个后备名单出来。
        logger.error("[evening] 解释层拿不到本日策略层产物(本次链没跑 k9 段),不定稿")
        return STATUS_EMPTY, {"reason": "no_k9_result"}
    shortlist: Shortlist = result.result.shortlist
    if not shortlist.entries:
        # 清单本来就是空的 —— 「今天没有」是可信的空,⛔ 不是故障。
        return STATUS_EMPTY, {"reason": "empty_listing"}

    max_rounds = int(result.params.explain.max_backfill_rounds)
    news_provider = get_provider(TASK_NEWS_SCAN, db_path=db_path)
    llm_provider = provider if provider is not None else get_provider(
        TASK_EXPLAIN, db_path=db_path)

    seated = list(shortlist.entries)
    reserve = list(shortlist.reserve)
    checked: Dict[str, Any] = {}
    audit: List[Dict[str, Any]] = []
    excluded_codes: List[str] = []
    #: 🔴 **真正补过几次位**(R2-05)。⛔ 不是「筛查跑了几遍」——
    #: 参数叫 `maxBackfillRounds`,它约束的就得是**补位**的次数。
    #: 从前这里数的是筛查轮次,而 `break` 又落在补位**之前**,于是
    #: `maxBackfillRounds=1` 得到的是「补位 0 次」,审计还写着「已达上限 1」
    #: —— 用户照字面意思填 1 = 把补位功能整个关掉,而系统告诉他它运行过。
    rounds_used = 0
    pass_no = 0                               # 筛查跑了第几遍(审计编号用)

    pending = list(seated)
    while pending:
        pass_no += 1
        # 🔴 **升序交给解释层** —— 位次不从列表顺序泄漏(双盲第 ③ 条)。
        items = sorted(((e.ts_code, e.name) for e in pending), key=lambda t: t[0])
        verdicts = news_mod.screen(items, provider=news_provider, transport=transport)
        for v in verdicts:
            checked[v.ts_code] = v
        hits = [v for v in verdicts if v.excluded]
        if not hits:
            break
        for v in hits:
            label = news_mod.CATEGORY_LABEL[v.category] if v.category else "消息面"
            audit.append({"round_no": pass_no, "action": explain_store.ACTION_EXCLUDED,
                          "ts_code": v.ts_code,
                          "reason": f"{label}:{v.summary}".strip("：: ")})
            excluded_codes.append(v.ts_code)
        seated = [e for e in seated if e.ts_code not in {v.ts_code for v in hits}]
        if rounds_used >= max_rounds:
            # ⚠ 上限判在**补位之前**、按**已补过几次**判 —— 到这里才是真的
            # 「补不动了」。⛔ 别把它挪回自增之后:那就又变成 N−1 次。
            audit.append({"round_no": pass_no,
                          "action": explain_store.ACTION_ROUNDS_EXHAUSTED, "ts_code": "",
                          "reason": f"补位轮数已达上限 {max_rounds},本日清单如实少这几只"})
            break
        # 补位:从后备票里按**名次**取下一名(编排器知道名次,解释层不知道)。
        take = min(len(hits), len(reserve))
        picked = reserve[:take]
        reserve = reserve[take:]
        if not picked:
            # 后备票用完了 —— 如实少这几只(⛔ 不制造候选,K9 §五)。
            break
        rounds_used += 1
        for e in picked:
            audit.append({"round_no": pass_no, "action": explain_store.ACTION_BACKFILLED,
                          "ts_code": e.ts_code, "reason": f"补位(后备第 {e.rank} 名)"})
        seated = seated + picked
        # ⚠ 只对新补进来的跑下一轮 —— 但**一定要跑**:补进来却没过消息面的票
        # 会带着空的 `news_state` 进清单,那是这一层存在的全部意义的反面。
        pending = picked

    # 资料聚合(逐只,升序)。
    codes = sorted(e.ts_code for e in seated)
    inputs = explain_input.build_inputs(
        trade_date, codes, sessions=explain_input.KLINE_SESSIONS,
        parquet_dir=parquet_dir, db_path=db_path)
    notes = explain_aggregate.aggregate(
        inputs, provider=llm_provider, news_by_code=checked, transport=transport)

    # —— 定稿(§5.5:清单在解释层之后定稿)——
    final = Shortlist(
        strategy=shortlist.strategy, params_version=shortlist.params_version,
        pack_version=shortlist.pack_version, pack_id=shortlist.pack_id,
        trade_date=shortlist.trade_date,
        entries=tuple(sorted(seated, key=lambda e: e.rank)),
        reserve=tuple(reserve), tier_used=shortlist.tier_used,
        strict_candidates=shortlist.strict_candidates,
        relaxed_candidates=shortlist.relaxed_candidates,
        channel_counts=shortlist.channel_counts,
        capacity_short=shortlist.capacity_short,
        absent_patterns=shortlist.absent_patterns,
        dropped_by_heat_absent=shortlist.dropped_by_heat_absent,
    )
    k9_store.save_listing(run_id=result.run_id, shortlist=final, db_path=db_path)
    _mark_news_excluded(trade_date, final, excluded_codes, parquet_dir=parquet_dir,
                        rows=result.result.disposition_rows)
    k9_store.mark_listing_finalized_by(
        trade_date, finalized_by=k9_store.FINALIZED_BY_EXPLAIN,
        seated_count=final.size, strategy=final.strategy, db_path=db_path)
    explain_store.save_notes(trade_date, notes, news_by_code=checked, db_path=db_path)
    if audit:
        explain_store.append_audit(trade_date, audit, db_path=db_path)

    counts = news_mod.summarize(list(checked.values()))
    logger.info("[evening] 解释层定稿:清单 %d 只(剔除 %d、补位 %d、未核实 %d)",
                final.size, len(excluded_codes),
                sum(1 for a in audit if a["action"] == explain_store.ACTION_BACKFILLED),
                counts.get("unverified", 0))
    return STATUS_OK, {
        "seated": final.size,
        "excluded": len(excluded_codes),
        "backfilled": sum(1 for a in audit
                          if a["action"] == explain_store.ACTION_BACKFILLED),
        "roundsUsed": rounds_used,
        "news": counts,
        "profilesOk": sum(1 for n in notes if n.llm_ok),
    }


def _mark_news_excluded(
    trade_date: date, final: Any, excluded_codes: Sequence[str], *,
    rows: Sequence[Dict[str, Any]], parquet_dir: Optional[Path],
) -> None:
    """把消息面剔除与补位的结果写回全市场 disposition(覆盖率归因要用,§5.4.8)。

    ⚠ 覆盖率层读的是 `k9_disposition` 这条**数据**通道(守门:`scorecard/**` 零
    import `neckline.k9`)—— 不回写这里,「昨天为什么没选中这只涨停票」就少了
    「被消息面剔除」那一档答案。"""
    from neckline.k9 import store as k9_store

    dead = set(excluded_codes)
    seated_now = {e.ts_code: e for e in final.entries}
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        code = row["ts_code"]
        if code in dead:
            row["news_excluded"] = 1
            row["seated"] = 0
            row["seat_kind"] = None
        elif code in seated_now:
            row["seated"] = 1
            row["seat_kind"] = (None if seated_now[code].seat_kind is None
                                else seated_now[code].seat_kind.value)
        out.append(row)
    k9_store.save_disposition(trade_date, out, parquet_dir=parquet_dir)


def _run_playbook(
    trade_date: date, *, db_path: Optional[Path], parquet_dir: Optional[Path],
    provider: Any = None, transport: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """预案层:为**定稿清单**上每只票冻一份预案(S10)。

    ⚠ 读的是 `k9_listing_entries`(**已定稿**的那一份)—— 预案必须跟着定稿走,
    否则会给一只已经被消息面剔除的票冻一份明早要核对的预案。"""
    from neckline.k9 import store as k9_store
    from neckline.llm.factory import get_provider
    from neckline.llm.router import TASK_PLAYBOOK
    from neckline.playbook import fill as playbook_fill

    listing = k9_store.load_listing(trade_date, db_path=db_path)
    if not listing:
        return STATUS_EMPTY, {"reason": "empty_listing"}
    llm = provider if provider is not None else get_provider(TASK_PLAYBOOK, db_path=db_path)
    stats = playbook_fill.fill_for_listing(
        trade_date, listing, provider=llm, transport=transport,
        parquet_dir=parquet_dir, db_path=db_path)
    if stats["frozen"] == 0 and stats["failed"]:
        # 🔴 **一份都没冻成 = 这一段没达成它的目的**,⛔ 不给它一个 `ok`:
        # 没有预案 = 明早那两拍核对不了任何一只(核对表会把它们列进
        # 「没有冻结预案」那一栏)。报告逐只也会如实说这句话。
        logger.error("[evening] 预案层一份都没冻成(%d 只失败):明早核对不了",
                     len(stats["failed"]))
        return STATUS_FAILED, stats
    return STATUS_OK, stats


def _upstream_gaps(res: EveningChainResult) -> List[str]:
    """把 k9 段自己报出来的缺口交给报告段。

    ⚠ **只在本次真跑过 k9 段时才有话说**:分段跑(`--segments report`)时本次链根本
    没碰策略层,那时的权威是 `k9_runs` 里那一行 —— ⛔ 不许拿「本进程没跑 k9」当成
    「今天没跑成」的理由(那正是冒烟里抓到的 bug)。
    """
    stats = res.stats.get(SEG_K9) or {}
    gaps = list(stats.get("gaps") or [])
    reason = stats.get("reason")
    if reason == "params_not_configured":
        gaps.insert(0, "参数未配置(晚间链未拿到 --k9-params;⛔ 本系统无默认参数)")
    elif reason == "params_invalid" and not gaps:
        gaps.insert(0, "参数包无效")
    return gaps


def _upstream_failures(res: EveningChainResult) -> List[str]:
    """🔴 **本次链里真的炸掉的那些段**(R2-04)。与 `_upstream_gaps` ⛔ 不是一回事。

    `_upstream_gaps` 说的是「k9 段**为什么没跑**」(参数未配置 / 参数无效)——
    那是关于**这一次调用**的,分段跑时不成立,所以只在没有运行账时采纳。
    本函数说的是「某一段**跑了、炸了**」—— 那是关于**这一天**的事实,
    `build_report` **无论有没有运行账都要采纳**。

    ⚠ 为什么这条必须存在:`_run_k9` → `persist` 是 `save_run` → `save_channel_hits`
    → `save_listing` 三步。第一步之后炸掉会留下「运行账有行、清单零行」,
    而保险丝已经把原因**说出来了** —— 从前报告把它整条丢掉,渲染成「今天没有」。
    架构 §3.5 那句「空清单可以被信任」就是被这条路径击穿的。

    🔴 **只看 `k9` 段,⛔ 不要顺手把 explain / playbook 也算进来**:那两段炸掉时
    清单本身仍然成立,而它们的缺席**已经各自有诚实披露** ——
    `k9_runs.listing_finalized_by='k9'`(渲染成「这份清单尚未经过消息面剔除」)与
    逐只那句「⚠ **没有冻结预案** —— 明早核对不了这一只」。把它们也翻成「今天没跑成」
    会把一份**可用**的清单整段藏起来,那是另一个方向的谎话。
    `facts` 段不用管:事实包没冻结本来就走 `pack_frozen=False`。
    """
    if res.status.get(SEG_K9) != STATUS_FAILED:
        return []
    note = next((n for n in res.notes if n.startswith(f"{SEG_K9} 段失败:")), "")
    return [note or "策略层当日执行失败(见服务端日志)"]


def _run_report(
    trade_date: date, *, report_date: date, upstream_gaps: Sequence[str],
    db_path: Optional[Path], parquet_dir: Optional[Path], save: bool,
    upstream_failures: Sequence[str] = (),
):
    from neckline.report import pipeline as pipeline_mod
    from neckline.report import store as report_store

    bundle = pipeline_mod.build_report(
        trade_date, report_date=report_date, upstream_gaps=upstream_gaps,
        upstream_failures=upstream_failures,
        db_path=db_path, parquet_dir=parquet_dir)
    if save:
        report_store.save_k9_report(
            trade_date=bundle.trade_date, report_date=bundle.report_date,
            state=bundle.state.value, headline=bundle.headline, gaps=list(bundle.gaps),
            markdown=bundle.markdown, structured=bundle.structured,
            strategy=bundle.strategy,
            params_package_version=bundle.params_package_version,
            pack_id=bundle.pack_id, pack_version=bundle.pack_version,
            listing_size=bundle.listing_size, strict_count=bundle.strict_count,
            relaxed_count=bundle.relaxed_count, db_path=db_path,
        )
    return bundle


# ══════════════════════════════════════════════════════════════════════════
# 覆盖率的接线 —— 🔴 只能住在**编排器**里
# ══════════════════════════════════════════════════════════════════════════

def coverage_inputs(
    trade_date: date, *, strategy: str = "K9",
    db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
):
    """把 D−1 的 K9 清单与全市场 disposition 翻译成覆盖率层的 DTO。

    🔴 **这段接线为什么在编排器里、不在 `scorecard/` 里**:守门单测断言
    `scorecard/**` **零 import** `neckline.k9` —— 尺子不许读被量的东西(§5.8.1)。
    策略侧的信息只能经**数据**通道(`k9_listing_entries` / `k9_disposition`)进来,
    而「谁去把这两边接上」是编排器的活。⛔ 别把这个函数搬进 `scorecard/`。

    返回 `(ListingSnapshot | None, [DispositionRow] | None)` ——
    两个 `None` 各自表示「昨天没有清单」/「没有 D−1 disposition」,
    覆盖率层据此写 NULL,⛔ 不是 0。
    """
    import json

    from neckline.calendar import prev_trading_day
    from neckline.k9 import store as k9_store
    from neckline.scorecard import coverage as coverage_mod

    d_1 = prev_trading_day(trade_date)
    codes = k9_store.load_listing_codes(d_1, strategy=strategy, db_path=db_path)
    listing = (
        coverage_mod.ListingSnapshot(trade_date=d_1, codes=frozenset(codes))
        if codes else None
    )

    frame = k9_store.load_disposition(d_1, parquet_dir=parquet_dir)
    if frame.is_empty():
        return listing, None
    rows = [
        coverage_mod.DispositionRow(
            ts_code=r["ts_code"],
            excluded_by=r["excluded_by"],
            recalled=bool(json.loads(r["recalled_patterns_json"] or "[]")),
            rank=r["rank"],
            seated=bool(r["seated"]),
            news_excluded=bool(r["news_excluded"]),
        )
        for r in frame.iter_rows(named=True)
    ]
    return listing, rows


__all__ = [
    "SEG_FACTS", "SEG_K9", "SEG_EXPLAIN", "SEG_PLAYBOOK", "SEG_REPORT",
    "CHAIN_SEGMENTS",
    "STATUS_OK", "STATUS_FAILED", "STATUS_SKIPPED", "STATUS_EMPTY", "STATUS_NOT_BUILT",
    "EveningChainResult", "run_evening_chain", "coverage_inputs",
]
