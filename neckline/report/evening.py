"""16:35 晚间编排链(plan §五 V2-⑭-A)。**批算侧**,与 `pipeline.py` 的报告侧刻意分居。

**为什么单独一个文件,不塞进 `pipeline.py`**(不是洁癖,是一条红线):
`neckline/report/pipeline.py` 在 `tests/test_scan_layer_guardrails.py` 的**在线模块清单**
里 —— P0-23 纪律要求在线路径「只准读表、不准现算」,守门单测逐字 grep 禁止它出现
`refresh_limit_clusters` 之类的**批算写入口**。而本链的第二段恰恰就是调那几个入口。
把链塞进 `pipeline.py` = 为了迁就文件摆放而钝化一条真防线(P0-23 是被生产 OOM 打出来的)。
故:**`pipeline.build_report` 只读不算,批算住这里**。

**顺序定死**(⑭-A,改之前先读完这段):

    16:05 拉数(`scripts/daily_update.py`,**不在本模块内**)
      → ⑧ EOD 篮子验证拍 → ④ 扫描层批算 → ⑤ 驱动聚合 → ⑥ Tier 定档 → ⑦ 卡冻结
      → ⑨ 盘后复盘 → 报告落库

**⑧ 的验证拍为什么必须排在「拉数之后、扫描层之前」**:它判的是**昨日(D0)冻结的卡**
在**今日(D+1)**的收盘表现,吃的是刚拉到的今日 EOD 数据,与今晚要生成的**新**篮子无关
—— 放最前面既符合因果,又保证即使后面某段炸了,昨日的定论行也已经落好(在 ⑭ 落地之前,
`basket_verification` 根本不会有 EOD 定论行,只有盘中暂态)。

**每段各自包保险丝**:任一段异常只记 WARNING + 在结果里标 `failed`,链继续往下走,
报告照出、缺席如实披露。⛔ 绝不因为某段失败而当日无报告 —— 唯一例外是最后那段报告
本身炸了(那才是真的没有报告,异常往上抛,退出码必须非零)。

**分段是 ⑯-D 拆三个 oneshot 的接缝**:`run_evening_chain(segments=...)` 可以只跑其中
几段(`neckline-scan.service` / `neckline-basket.service` / `neckline-report.service`
—— 后者跑 ⑨ 复盘 + 报告落库两段),到那一块不必重写编排逻辑。⚠ `segments` **只挑
跑哪几段,不改顺序**(传进来的集合会按 `CHAIN_SEGMENTS` 重排)。

**V2-⑯-D 补记(2026-08-04 定向小修)**:⑤⑥⑦ 与「⑨+报告」拆进两个独立进程后,
⑥ 的 `TierResult.dropped`(③b 的数据源)原本只在内存里随链传递、跨不过进程边界
——报告段独立跑时(`SEG_BASKET` 不在本次 `segments` 里)会看到 `None`。本函数
的 SEG_REPORT 分支据此加了一条**跨进程回退**:`dropped_baskets` 为 `None` 且
**本次压根没打算跑** `SEG_BASKET` 时,去 `selection/basket_dropped_handoff.py`
的交接表里找"今晚早些时候(另一个进程里)⑥ 是否跑过"。⚠ 只在"没打算跑"时才
查表——若 `SEG_BASKET` 在本次 `segments` 里但结果是 `None`(跑了但炸了 / ⑤ 没
产出),那是**本次**的明确结论,不许被表里可能存在的旧数据覆盖。单进程整链跑法
(`SEG_BASKET` 恒在 `wanted` 里)不触发查表分支,行为逐字节不变。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from neckline.llm.base import LLMProvider
from neckline.llm.factory import get_provider
from neckline.report.pipeline import ReportBundle, build_report

logger = logging.getLogger(__name__)

# 「调用方压根没传这个参数」的哨兵 —— 与「显式传了 `None`」区分开。
# ⑤ `aggregate_baskets` 用同一套约定(显式 `None` = 强制该段缺席,不回落工厂),
# 本模块只是把这个语义原样传下去,**不许简化成 `Optional`**(那会把"没说"讲成"关掉")。
_UNSET = object()


# ══════════════════════════════════════════════════════════════════════════
# 16:35 晚间编排链(V2-⑭-A;⑯-D 拆三个 oneshot 的接缝)
# ══════════════════════════════════════════════════════════════════════════

SEG_VERIFY = "verify"    # ⑧ EOD 篮子验证拍(判**昨日**冻的卡在**今日**收盘的表现)
SEG_SCAN = "scan"        # ④ 市场扫描层三表批算 + ④b 行业阶段 + 驱动种子
SEG_BASKET = "basket"    # ⑤ 驱动聚合 → ⑥ Tier 定档(事务1)→ ⑦ 卡冻结(事务2)
SEG_REVIEW = "review"    # ⑨ 盘后复盘引擎
SEG_REPORT = "report"    # 篮子日报渲染 + 落库

# **顺序定死**(⑭-A;调顺序前先读模块头「⑧ 为什么必须排在拉数之后、扫描层之前」)。
CHAIN_SEGMENTS: tuple = (SEG_VERIFY, SEG_SCAN, SEG_BASKET, SEG_REVIEW, SEG_REPORT)

STATUS_OK = "ok"
STATUS_FAILED = "failed"        # 跑了、炸了(保险丝吞掉,报告里如实标)
STATUS_SKIPPED = "skipped"      # 调用方没要这一段(⑯-D 分段跑)
STATUS_EMPTY = "empty"          # 跑了、没有可做的(如今日无种子 → 无篮子;**合法输出**)


@dataclass
class EveningChainResult:
    """一次 16:35 链的执行结果。`status` 逐段可查 —— 「没做」「做了没东西」「做了炸了」
    三态分开(§3.8),⛔ 不许合并成一个 bool。"""

    trade_date: date
    status: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    bundle: Optional[ReportBundle] = None
    # ⑥ 的溢出篮:**只在本次真跑过 ⑥ 时才是列表**(空列表 = 跑了、零溢出);
    # `None` = 没跑 ⑥ → ③b 如实标「本段未取得」。
    dropped_baskets: Optional[List[Any]] = None

    def ok(self, seg: str) -> bool:
        return self.status.get(seg) in (STATUS_OK, STATUS_EMPTY)


def run_evening_chain(
    trade_date: date,
    *,
    segments: Sequence[str] = CHAIN_SEGMENTS,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    use_llm: bool = True,
    search_provider: Any = _UNSET,
    reason_provider: Any = _UNSET,
    tier_provider: Optional[LLMProvider] = None,
    card_provider: Optional[LLMProvider] = None,
    review_provider: Optional[LLMProvider] = None,
    report_llm_provider: Optional[LLMProvider] = None,
    transport: Optional[Any] = None,
    ledger: Optional[Any] = None,
    save: bool = True,
) -> EveningChainResult:
    """跑 16:35 那条链(单进程串行)。**每段各自包保险丝** —— 任一段异常只记 WARNING
    并在结果里标 `failed`,链继续往下走,报告照出、缺席如实披露。

    **顺序定死**(⑭-A):⑧ 验证拍 → ④ 扫描层 → ⑤ 聚合 → ⑥ Tier → ⑦ 卡 → ⑨ 复盘 →
    报告落库。`segments` 只用来**挑跑哪几段**(⑯-D 的三个 oneshot),**不改顺序**:
    传进来的集合会按 `CHAIN_SEGMENTS` 重排,乱序传参不会得到乱序执行。

    ⚠ **16:05 拉数不在本函数内**(那是 `scripts/daily_update.py` 的 timer);本函数
    假定当日 EOD 数据已落地。数据没到 → 各段自己的"当日无数据"分支照常走(合法输出)。

    `use_llm`:总开关。**默认 True**(生产)—— 无 key 时各段的 provider 解析会拿到
    `None`,各自走"人话半份缺席、结构化半份照出"的既定降级链,不崩。单测/离线冒烟传
    `False` 走纯机械路径。
    """
    from neckline.llm.router import TASK_REVIEW, TASK_SCRIPT, TASK_TIER_RANK

    wanted = [s for s in CHAIN_SEGMENTS if s in set(segments)]
    res = EveningChainResult(trade_date=trade_date)
    for seg in CHAIN_SEGMENTS:
        res.status[seg] = STATUS_OK if seg in wanted else STATUS_SKIPPED

    def _fail(seg: str, what: str, exc: BaseException) -> None:
        res.status[seg] = STATUS_FAILED
        note = f"{what}异常({type(exc).__name__}),该段未完成;报告仍会出,缺席已如实标注。"
        res.notes.append(note)
        logger.warning("[evening] %s", note, exc_info=True)

    def _provider(task: str) -> Optional[LLMProvider]:
        if not use_llm:
            return None
        try:
            return get_provider(task, db_path=db_path)
        except Exception:  # noqa: BLE001  取 provider 失败 = 该段人话半份缺席,不是致命
            logger.warning("[evening] 取任务 %s 的 provider 失败,该段按 LLM 缺席处理",
                           task, exc_info=True)
            return None

    # —— ⑧ EOD 篮子验证拍(位置定死:拉数之后、扫描层之前)——————————————
    if SEG_VERIFY in wanted:
        try:
            from neckline.sentinel.basket_verify import run_eod_verification

            v = run_eod_verification(trade_date, db_path=db_path, parquet_dir=parquet_dir)
            res.stats["verify"] = {
                "evaluated": v.evaluated, "rows_written": v.rows_written,
                "skipped_unchanged": v.skipped_unchanged, "skipped_latched": v.skipped_latched,
                "skipped_not_observed": v.skipped_not_observed,
            }
            if v.evaluated == 0:
                res.status[SEG_VERIFY] = STATUS_EMPTY
            logger.info("[evening] ⑧ EOD 验证:判定 %d 篮,落 %d 行", v.evaluated, v.rows_written)
        except Exception as exc:  # noqa: BLE001
            _fail(SEG_VERIFY, "⑧ EOD 篮子验证拍", exc)

    # —— ④ / ④b 扫描层批算 + 驱动种子 ————————————————————————————————
    seed_set = None
    if SEG_SCAN in wanted:
        try:
            from neckline.scan import cluster, corr, leader
            from neckline.scan.seeds import generate_seeds
            from neckline.scan.stage import refresh_industry_stage

            c = cluster.refresh_limit_clusters([trade_date], db_path=db_path, parquet_dir=parquet_dir)
            r = corr.refresh_corr_matrix([trade_date], db_path=db_path, parquet_dir=parquet_dir)
            l = leader.refresh_leader_structure([trade_date], db_path=db_path, parquet_dir=parquet_dir)
            try:
                st = refresh_industry_stage([trade_date], db_path=db_path, parquet_dir=parquet_dir)
            except Exception:  # noqa: BLE001  ④b 与 ④ 三表无耦合,单独降级
                logger.warning("[evening] ④b 行业阶段批算异常(已吞,不阻断扫描层)", exc_info=True)
                st = {}
            seed_set = generate_seeds(trade_date, db_path=db_path, parquet_dir=parquet_dir)
            res.stats["scan"] = {
                "cluster_rows": c.get("rows"), "corr_rows": r.get("rows"),
                "leader_rows": l.get("rows"), "stage_rows": st.get("rows"),
                "seeds": (seed_set.counts() if seed_set is not None else None),
            }
            if seed_set is None or not seed_set.all_seeds():
                # **合法输出**:无现役包 / 今天没有热点 → 今日无篮子。⛔ 不拿昨日的凑数。
                res.status[SEG_SCAN] = STATUS_EMPTY
                res.notes.append("今日无驱动种子(无现役选股包,或当日没有达标的热点/簇)——"
                                 "今日无篮子是合法输出,不是故障。")
            logger.info("[evening] ④ 扫描层:%s", res.stats["scan"])
        except Exception as exc:  # noqa: BLE001
            _fail(SEG_SCAN, "④ 市场扫描层批算", exc)

    # —— ⑤ 聚合 → ⑥ Tier(事务1)→ ⑦ 卡(事务2)——————————————————————
    if SEG_BASKET in wanted:
        try:
            res.dropped_baskets = _run_basket_segment(
                trade_date, seed_set=seed_set, db_path=db_path, parquet_dir=parquet_dir,
                use_llm=use_llm, search_provider=search_provider, reason_provider=reason_provider,
                tier_provider=(tier_provider if tier_provider is not None else _provider(TASK_TIER_RANK)),
                card_provider=(card_provider if card_provider is not None else _provider(TASK_SCRIPT)),
                transport=transport, ledger=ledger, stats=res.stats, notes=res.notes,
            )
            if not res.stats.get("basket", {}).get("baskets"):
                res.status[SEG_BASKET] = STATUS_EMPTY
        except Exception as exc:  # noqa: BLE001
            _fail(SEG_BASKET, "⑤⑥⑦ 篮子生成", exc)
            # ⚠ **炸了就是 `None`,不是 `[]`** —— `[]` 的意思是「⑥ 跑过、今天零溢出」。
            res.dropped_baskets = None

    # —— ⑨ 盘后复盘引擎(单独一段;⑯-D 会把它拆成 `neckline-review.service`)——
    if SEG_REVIEW in wanted:
        try:
            from neckline.review.basket_review import review_day

            rr = review_day(
                trade_date, db_path=db_path, parquet_dir=parquet_dir,
                use_llm=use_llm,
                provider=(review_provider if review_provider is not None else _provider(TASK_REVIEW)),
                transport=transport, ledger=ledger,
            )
            res.stats["review"] = {
                "baskets": len(rr.reviews), "inserted": rr.rows_inserted,
                "existing": rr.rows_existing, "llm_called": rr.llm_called,
            }
            res.notes.extend(rr.notes)
            if not rr.reviews:
                res.status[SEG_REVIEW] = STATUS_EMPTY
            logger.info("[evening] ⑨ 复盘:%s", res.stats["review"])
        except Exception as exc:  # noqa: BLE001
            _fail(SEG_REVIEW, "⑨ 盘后复盘引擎", exc)

    # —— 报告落库(链的最后一段)————————————————————————————————————
    if SEG_REPORT in wanted:
        try:
            dropped_for_report = res.dropped_baskets
            if dropped_for_report is None and SEG_BASKET not in wanted:
                # V2-⑯-D 补记:本次调用压根没打算跑 SEG_BASKET(⑯-D 拆进程后
                # `neckline-report.service` 独立跑的真实形状)——去跨进程交接表
                # 里找"今晚(另一个进程里)⑤⑥ 是否跑过、结果是什么"。⚠ 只在
                # **没打算跑**时才查:若 SEG_BASKET 在 wanted 里但结果是 `None`
                # (跑了但炸了 / ⑤ 没产出),那是**本次**的明确结论,不许被表里
                # 可能存在的旧数据覆盖(见上面 `_run_basket_segment` 与 `_fail`
                # 分支——炸了就是 `None` 不是回退查表)。
                try:
                    from neckline.selection.basket_dropped_handoff import load_dropped_handoff

                    dropped_for_report = load_dropped_handoff(trade_date, db_path=db_path)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "[evening] ⑥ dropped 跨进程交接表读取异常,按未取得处理"
                        "(不阻断报告)", exc_info=True,
                    )
                    dropped_for_report = None
            res.bundle = build_report(
                trade_date, llm_provider=report_llm_provider, llm_transport=transport,
                parquet_dir=parquet_dir, db_path=db_path, save=save,
                dropped_baskets=dropped_for_report,
            )
        except Exception as exc:  # noqa: BLE001
            # ⚠ 报告这一段炸了是**真的没有报告**(不像前面几段可以缺席披露),故往上抛:
            # 调用方(CLI / systemd)必须看得见非零退出码,不能静默"成功"。
            _fail(SEG_REPORT, "篮子日报生成", exc)
            raise
    return res


def _run_basket_segment(
    trade_date: date, *,
    seed_set: Any,
    db_path: Optional[Path],
    parquet_dir: Optional[Path],
    use_llm: bool,
    search_provider: Any,
    reason_provider: Any,
    tier_provider: Optional[LLMProvider],
    card_provider: Optional[LLMProvider],
    transport: Optional[Any],
    ledger: Optional[Any],
    stats: Dict[str, Any],
    notes: List[str],
) -> Optional[List[Any]]:
    """⑤ → ⑥(事务1)→ ⑦(事务2)。返回 ⑥ 的 `dropped`(③b 的数据源)。

    **⑥→⑦ 的三种形状别搞混**(⑨ 冒烟脚本踩过):`save_tier_decision` 要
    `key→int`(tier)与 `key→dict`(tier_history 行),而 `build_cards` 要
    `key→TierDecision` **对象**。传错那一种不会大声报错,只会让卡上的 Tier 节静静地空掉。
    """
    import dataclasses as _dc

    from neckline.selection import aggregate as agg
    from neckline.selection import basket_card as bc
    from neckline.selection import tier as tr
    from neckline.selection.basket_store import (
        load_baskets_for_date, save_basket_cards, save_tier_decision,
    )

    kwargs: Dict[str, Any] = {}
    if search_provider is not _UNSET:
        kwargs["search_provider"] = search_provider
    elif not use_llm:
        kwargs["search_provider"] = None
    if reason_provider is not _UNSET:
        kwargs["reason_provider"] = reason_provider
    elif not use_llm:
        kwargs["reason_provider"] = None
    result = agg.aggregate_baskets(
        trade_date, seed_set=seed_set, db_path=db_path, parquet_dir=parquet_dir,
        ledger=ledger, transport=transport, **kwargs,
    )
    notes.extend(result.notes)
    stats["aggregate"] = {"baskets": len(result.baskets), "rejected": len(result.rejected),
                          "hygiene_rejected": len(result.hygiene_rejected),
                          "search_stage": result.search_stage, "reason_stage": result.reason_stage}
    if not result.baskets:
        stats["basket"] = {"baskets": 0, "cards": 0}
        # ⑤ 没产出篮子 → ⑥ 没跑过 → **`None`**,不是 `[]`(见 ③b 的两态纪律)。
        return None

    decision = tr.score_and_tier(
        result, trade_date, db_path=db_path, parquet_dir=parquet_dir,
        provider=tier_provider, use_llm=use_llm, ledger=ledger, transport=transport,
    )
    # V2-⑯-D 补记:⑥ 一跑完就落跨进程交接表(`basket_dropped_handoff`,见该模块
    # 头)——这是「⑥ 定档」这件事本身的既成事实,不依赖 ⑦ 卡生成是否成功(同
    # baskets/tier_history 由事务 1 独立提交的既定姿势,"有篮子无卡"是合法中间态)。
    # 整段包保险丝:这一步失败不许连累 ⑤⑥⑦ 已经算好的东西——本次调用仍会把
    # `decision.dropped` 原样返回给调用方走内存路径,单进程行为不受影响。
    try:
        from neckline.selection.basket_dropped_handoff import save_dropped_handoff

        save_dropped_handoff(trade_date, decision.dropped, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[evening] ⑥ dropped 跨进程交接表写入异常(已吞,不影响本次内存路径)",
            exc_info=True,
        )
    tier_by_key = {d.basket_key: d.tier for d in decision.decisions}
    hist_by_key = {
        d.basket_key: {
            "basket_key": d.basket_key, "tier": d.tier, "mech_score": d.mech_score,
            "mech_breakdown": d.breakdown, "rank_in_tier": d.rank_in_tier,
            "rank_mech": d.rank_mech, "llm_rank_delta": d.llm_rank_delta,
            "llm_reason": d.llm_reason, "pack_version": decision.pack_version,
        }
        for d in decision.decisions
    }
    # `AggregateResult` 是 frozen dataclass:溢出未定档的篮子要用 `replace` 剔掉
    # (`baskets.tier` NOT NULL,⑥ 的 `dropped` 不落库,只随报告快照走)。
    kept = [b for b in result.baskets if b.basket_key in tier_by_key]
    result = _dc.replace(result, baskets=tuple(kept))
    stats["tier"] = save_tier_decision(
        result, tier_by_basket_key=tier_by_key,
        tier_history_by_basket_key=hist_by_key, db_path=db_path, via="auto",
    )

    refs = load_baskets_for_date(trade_date, db_path=db_path)
    id_by_key = {r.basket_key: r.basket_id for r in refs}
    cards = bc.build_cards(
        result.baskets, trade_date, db_path=db_path, parquet_dir=parquet_dir,
        use_llm=use_llm, provider=card_provider, ledger=ledger, transport=transport,
        tier_by_basket_key={d.basket_key: d for d in decision.decisions},
    )
    by_id = {id_by_key[c.basket_key]: c.to_card_json() for c in cards if c.basket_key in id_by_key}
    meta = {
        id_by_key[c.basket_key]: {
            "stop_pct": c.stop_pct, "take_profit_retrace": c.take_profit_retrace,
            "charter_version": c.charter_version, "pack_version": c.pack_version,
            "engine_api_version": c.engine_api_version,
        }
        for c in cards if c.basket_key in id_by_key
    }
    stats["card"] = save_basket_cards(by_id, meta_by_basket_id=meta, db_path=db_path)
    stats["basket"] = {"baskets": len(result.baskets), "cards": len(by_id),
                       "dropped": len(decision.dropped)}
    logger.info("[evening] ⑤⑥⑦:篮子 %d 个(溢出/未过线 %d),卡 %d 张",
                len(result.baskets), len(decision.dropped), len(by_id))
    return list(decision.dropped)

__all__ = [
    "CHAIN_SEGMENTS", "SEG_BASKET", "SEG_REPORT", "SEG_REVIEW", "SEG_SCAN", "SEG_VERIFY",
    "STATUS_EMPTY", "STATUS_FAILED", "STATUS_OK", "STATUS_SKIPPED",
    "EveningChainResult", "run_evening_chain",
]
