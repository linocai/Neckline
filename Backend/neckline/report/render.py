"""报告渲染(架构 §3.5,PROJECT_PLAN §5.10)。

**两层视图**:
· **默认视图**面向直接阅读:首行三态 → 方向背景 → 市场事实 → 清单 → 覆盖率;
· **结构化完整版**默认折叠(markdown 里是一段 `<details>`),展开可**整段复制**到
  聊天框做深度分析。

🔴 **首行由 `report/state.py::headline` 的全映射产出**,⛔ 本模块不许再写一次三态
文案 —— 两处文案迟早各说各话,而那正是「空清单可以被信任」这件事最怕的。

⚠ **参数未配置的日子照样有内容**(§5.10):方向背景、市场事实、覆盖率照常渲染,
缺的只是清单段。⛔ 不许因为 `not_run` 就把整份报告渲染成一句「坏了」。

⚠ **诚实缺席**:方向解读没接入就写「未接入」、某形态今日无候选就写「今日无此形态」、
覆盖率还没有昨日清单就写「昨天还没有清单」—— ⛔ 一律不拿 0 或空字符串糊过去
(0 与「没有」是两件事,§3.8 的老纪律)。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from neckline.k9.contract import PATTERN_LABEL, Pattern
from neckline.report.state import ReportState

#: 形态代码 → 人话(全映射,⛔ 无 fallback)。
_PATTERN_TEXT = {p.value: PATTERN_LABEL[p] for p in Pattern}

_TIER_TEXT = {"strict": "严格", "relaxed": "放宽"}
_SEAT_TEXT = {"floor": "保底", "free": "竞争"}


def _pct(x: Optional[float], digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def _num(x: Optional[float], digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def structured(bundle) -> Dict[str, Any]:
    """结构化完整版(可整段复制到聊天框)。键序固定 → 同一份输入逐字节相同。

    🔴 **必须含 `explain` 与 `playbooks`**(R2-08):§5.10 与架构 §3.5 给这一份的
    用途逐字是「展开可**整段复制到聊天框做深度分析**」—— 深度分析要的恰恰是
    每只票的资料聚合、日K 评价、消息面三态、三个价位与两条分支。少了这两样,
    复制过去的只是市场读数 + 一张排名表,那份「完整版」名不副实。

    ⚠ **预案是 append-only 版本化的**,而本函数的产物会冻进
    `k9_reports.structured_json`(冻结件,写入当时什么样就永远什么样)。
    所以这里存的是**写入那一刻的那一版**,每份都自带 `version` ——
    用户之后改了预案,快照与库里对不上是**正常且可查**的(版本号不同),
    ⛔ 不许为了「跟上」而去改冻结件。个股详情那条路(`api/app.py::
    _selection_stocks`)⚠ 刻意用**现装**的最新版,两者用途不同。
    """
    return {
        "reportDate": bundle.report_date.isoformat(),
        "tradeDate": bundle.trade_date.isoformat(),
        "state": bundle.state.value,
        "headline": bundle.headline,
        "gaps": list(bundle.gaps),
        "strategy": bundle.strategy,
        "paramsPackageVersion": bundle.params_package_version,
        "packId": bundle.pack_id,
        "packVersion": bundle.pack_version,
        "listingSize": bundle.listing_size,
        "strictCount": bundle.strict_count,
        "relaxedCount": bundle.relaxed_count,
        "listing": [dict(e) for e in bundle.listing],
        # 🔴 R2-08:解释层资料 + 预案 —— 这一份存在的**理由**就是这两样。
        # 空 dict 各自表示「那天这一层没跑过」/「那天一份预案都没冻」(⛔ 不是「没有」)。
        "explain": dict(bundle.explain or {}),
        "playbooks": dict(bundle.playbooks or {}),
        "run": bundle.run,
        "market": bundle.market,
        "direction": bundle.direction,
        "coverage": bundle.coverage,
    }


def markdown(bundle, payload: Optional[Dict[str, Any]] = None) -> str:
    """默认视图 + 折叠的结构化完整版。"""
    body = payload if payload is not None else structured(bundle)
    lines: List[str] = [
        f"# {bundle.headline}",
        "",
        f"报告日 {bundle.report_date.isoformat()} · 行情截至 {bundle.trade_date.isoformat()}",
        "",
    ]
    lines += _direction_section(bundle)
    lines += _market_section(bundle)
    lines += _listing_section(bundle)
    lines += _coverage_section(bundle)
    lines += [
        "<details>",
        "<summary>结构化完整版(展开可整段复制)</summary>",
        "",
        "```json",
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)


def _direction_section(bundle) -> List[str]:
    out = ["## 方向背景", ""]
    if not bundle.direction:
        out += [
            "今日方向解读**未接入**(事实层的 LLM 旁路 `facts/direction_llm.py` 尚未建)。",
            "",
            "⚠ 它只是报告背景 —— **不参与筛选、不参与排序、不影响任何机械决策**(架构 §八)。",
            "",
        ]
        return out
    out += [str(bundle.direction.get("summary", "")), ""]
    for item in bundle.direction.get("themes", []) or []:
        out.append(f"- **{item.get('name', '')}**:{item.get('reason', '')}")
    out.append("")
    return out


def _market_section(bundle) -> List[str]:
    out = ["## 市场事实", ""]
    if not bundle.market:
        out += ["当日事实包未冻结,无市场读数。", ""]
        return out
    lm = bundle.market.get("limitMap") or {}
    out += [
        f"- 涨停 **{lm.get('limitUpCount', '—')}** 只 / 跌停 {lm.get('limitDownCount', '—')} 只"
        f" / 炸板 {lm.get('zabanCount', '—')} 只(炸板率 {_pct(lm.get('zabanRate'))})",
        f"- 连板高度 **{lm.get('maxConsecDays') if lm.get('maxConsecDays') is not None else '—'}**"
        f";申万二级涨停簇 {len(lm.get('clusters') or [])} 个",
        f"- 全市场中位涨幅 {_pct(bundle.market.get('marketMedianRet'), 2)}",
    ]
    anomaly = bundle.market.get("suspendAnomaly") or {}
    if anomaly.get("total"):
        out.append(
            f"- ⚠ 停牌断言被违反:{anomaly['total']} 只**全天停牌**的票出现在当日行情里")
    if anomaly.get("intradayCounted"):
        out.append(
            f"- 盘中临时停牌 {anomaly['intradayCounted']} 只(当天正常交易,"
            f"**照常计入**行业中位数)")
    out.append("")
    return out


def _listing_section(bundle) -> List[str]:
    out = ["## 今日清单", ""]
    if bundle.state is ReportState.NOT_RUN:
        out += [f"**{bundle.headline}**", ""]
        for g in bundle.gaps:
            out.append(f"- {g}")
        out += [
            "",
            "⚠ 这是**系统没工作**,不是「今天没有」。上一份冻结结果原样保留。",
            "",
        ]
        return out
    if bundle.state is ReportState.EMPTY:
        out += [
            "**今天没有。** 跑通了、结果是空的 —— 这个结论可以被信任。",
            "",
        ]
        return out

    out += [
        f"共 **{bundle.listing_size}** 只(严格 {bundle.strict_count} / "
        f"放宽 {bundle.relaxed_count})。",
        "",
        "| # | 代码 | 名称 | 形态 | 成色 | 席位 | 申万二级 | 总分 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in bundle.listing:
        pats = "、".join(_PATTERN_TEXT.get(p, p) for p in e["patterns"])
        out.append(
            f"| {e['rank']} | {e['ts_code']} | {e['name'] or '—'} | {pats} | "
            f"{_TIER_TEXT.get(e['tier'], e['tier'])} | "
            f"{_SEAT_TEXT.get(e['seat_kind'], '—')} | {e['sw_l2_name'] or '—'} | "
            f"{_num(e['score'], 3)} |"
        )
    out.append("")
    out += _run_notes(bundle)
    out += _news_notes(bundle)
    out += _per_stock_section(bundle)
    return out


def _news_notes(bundle) -> List[str]:
    """消息面三态的**如实披露**(§5.5 / `explain/news_exclusion.py`)。

    🔴 三态各说各的:`excluded` 是「查出来了、剔了」;`unverified` 是「**没查成**」;
    ⛔ 不许把后者说成「都干净」。"""
    ex = bundle.explain or {}
    if not ex:
        return []
    counts = ex.get("newsCounts") or {}
    out: List[str] = []
    unverified = int(counts.get("unverified", 0))
    excluded = sum(1 for a in (ex.get("audit") or []) if a["action"] == "excluded")
    backfilled = sum(1 for a in (ex.get("audit") or []) if a["action"] == "backfilled")
    if excluded or backfilled:
        out.append(f"- 消息面剔除 **{excluded}** 只,后备补位 **{backfilled}** 只")
        for a in (ex.get("audit") or []):
            if a["action"] == "excluded":
                out.append(f"  - 剔除 {a['ts_code']}:{a['reason']}")
            elif a["action"] == "backfilled":
                out.append(f"  - 补位 {a['ts_code']}:{a['reason']}")
            elif a["action"] == "rounds_exhausted":
                out.append(f"  - ⚠ {a['reason']}")
    if unverified:
        out.append(
            f"- ⚠ **{unverified} 只消息面未核实**(没查成,⛔ 不等于确认无消息):"
            f"这几只的爆雷 / 减持 / 立案 / 监管**没有人查过**")
    ok = int(ex.get("profilesOk", 0))
    total = len(ex.get("notes") or {})
    if total and ok < total:
        out.append(f"- ⚠ 资料聚合 {ok}/{total} 只成功,其余如实缺席")
    if out:
        out.append("")
    return out


def _per_stock_section(bundle) -> List[str]:
    """每只票一句话画像 + 关键价位与预案(§5.10 默认视图第 2、3 段)。"""
    notes = (bundle.explain or {}).get("notes") or {}
    pbs = bundle.playbooks or {}
    if not notes and not pbs:
        return []
    out = ["### 逐只", ""]
    for e in bundle.listing:
        code = e["ts_code"]
        out.append(f"**{e['name'] or code}({code})**")
        n = notes.get(code)
        if n:
            prof = n.get("profile") or {}
            one = prof.get("company") or ""
            if one:
                out.append(f"- {one}")
            if n.get("kline_comment"):
                out.append(f"- 日K:{n['kline_comment']}")
            state = n.get("news_state")
            if state == "excluded":
                out.append(f"- ⚠ 消息面命中 {n.get('news_category') or ''}")
            elif state == "unverified":
                out.append("- ⚠ 消息面**未核实**(没查成,不等于确认无消息)")
        else:
            out.append("- 资料未取得(解释层这一只没跑成)")
        pb = pbs.get(code)
        if pb:
            lv = pb["levels"]
            out.append(
                f"- 价位:第一压力位 {lv['firstResistance']:g} / 第二 "
                f"{lv['secondResistance']:g} / 失效位 {lv['invalidation']:g}"
                f"(v{pb['version']},{pb['source']})")
            for b in pb["branches"]:
                conds = " 且 ".join(
                    f"{c['lhs']} {c['op']} "
                    f"{c['rhs'] if isinstance(c['rhs'], str) else format(c['rhs'], 'g')}"
                    for c in b["all"])
                out.append(f"  - {b['name']}:{conds}")
            out.append(f"  - 其余:{pb['default']}")
        else:
            out.append("- ⚠ **没有冻结预案** —— 明早核对不了这一只")
        out.append("")
    return out


def _run_notes(bundle) -> List[str]:
    """K9 §五 的诚实披露:今日无此形态 / 容量不足 / 判据过严 / 谁定的稿。"""
    run = bundle.run
    if not run:
        return []
    out: List[str] = []
    absent = run.get("absent_patterns") or []
    if absent:
        names = "、".join(_PATTERN_TEXT.get(p, p) for p in absent)
        out.append(f"- **今日无此形态**:{names}(保持标准不变,⛔ 未放宽去凑)")
    if run.get("capacity_short"):
        out.append(
            "- ⚠ **容量不足**:放宽档后仍不足下限,**如实出这么多** —— ⛔ 未制造候选")
    if run.get("over_strict"):
        out.append(
            f"- ⚠ **判据过严,建议重标**:已连续 {run.get('relaxed_streak')} 天"
            f"靠放宽档凑足(K9 §五-8)")
    if run.get("listing_finalized_by") == "k9":
        out.append(
            "- ⚠ 这份清单**尚未经过消息面剔除**(解释层未跑):"
            "爆雷 / 减持 / 立案 / 监管还没有人查过")
    dropped = run.get("dropped_heat_absent") or []
    if dropped:
        out.append(f"- 因「查无该行业」被丢出本日清单:{len(dropped)} 只")
    if out:
        out.append("")
    return out


def _coverage_section(bundle) -> List[str]:
    out = ["## 覆盖率(尺子)", ""]
    cov = bundle.coverage
    if not cov:
        out += ["当日尚无覆盖率行(没有冻结事实包的日子⛔ 不编一行 0)。", ""]
        return out
    all_rate = cov.get("coverage_all")
    out += [
        f"- 当日涨停 **{cov.get('limit_up_count')}** 只;申万二级涨停簇 "
        f"{cov.get('cluster_count')} 个",
        "- 昨日清单命中率:"
        + ("**昨天还没有清单**(⛔ 不是 0)" if all_rate is None else f"**{all_rate:.1%}**"),
    ]
    in_pool = cov.get("coverage_in_pool")
    out.append(
        "- 池内命中率:"
        + ("**无 D−1 disposition / 边界参数缺失**(⛔ 不是 0)"
           if in_pool is None else f"{in_pool:.1%}")
    )
    out.append("")
    return out


__all__ = ["structured", "markdown"]
