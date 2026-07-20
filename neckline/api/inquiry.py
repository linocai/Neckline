"""问询台服务(plan §2.5 / 4A.5)。用户丢一票进来 → **确定性检查(纪律核对 + 同一
评分管线跑分 + 板块年龄)→ LLM 自然语言回答** → 裁决二值。

**同码不重写铁律**:确定性检查复用 `strategy.brain`(现役规则)+ `research.panel`
(选股域)+ `strategy.signals`(禁买/风控预测)+ `report.candidates`(评分/板块年龄),
**不在本模块另写一份领域规则**。

**裁决二值(硬约束,§2.5:永不「现在就买」)——三重保险**:
    1. `verdict` 只可能是两个模块常量 `VERDICT_REJECT`/`VERDICT_PASS`,由**代码**据确定性
       检查(+ LLM 显式否决)算出,**不从 LLM 自由文本里提取"买"**。
    2. system prompt 显式禁止产出「现在就买 / 买入建议」。
    3. schema `verdict: Literal["不符合","初审通过进海选池"]` 再兜一层。

**降级(缺 key,§2.5「确定性检查照跑,LLM 段返未激活占位,不崩」)**:LLM 未激活 →
裁决只由确定性纪律决定,LLM 段返「未激活」占位文案,`degraded=True`,全链路不崩。

**工具调用的落地范围(诚实标注)**:plan 4A.5 提「LLM 带工具调用(实时取数 / 重算 /
联网搜索)」。本实现把**实时取数(`sentinel.quotes`)与重算(`report.candidates` 评分 +
板块年龄)在调用 LLM 之前跑好、作为结构化上下文注入**,LLM 段本身开启**原生联网搜索**
(`provider.chat(enable_search=True)`,GLM/Kimi 均原生带,覆盖 §2.4 消息面)。未实现
"LLM 主动多轮 function-calling 回调后端函数"这一形态——理由:① 无 key 无法活体验证该
形态;② 预注入 + 原生搜索已覆盖 plan 列的三种能力(取数/重算/搜索),且可被 MockTransport
单测覆盖、缺 key 优雅降级。此简化记入完工欠账。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from neckline.api.schemas import VERDICT_PASS, VERDICT_REJECT
from neckline.api.stores import add_to_inquiry_pool
from neckline.data.board import Board
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.report.candidates import _base_score_expr  # 同码:展示排序分与报告一致
from neckline.report.sectors import (
    SectorScore,
    compute_sector_strength,
    load_index_names,
    load_member_map,
)
from neckline.strategy import brain
from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig, build_entry_mask

logger = logging.getLogger(__name__)

_BOARD_LABEL = {"MAIN": "主板", "GEM": "创业板", "STAR": "科创板", "BSE": "北交所"}
_HIGH_ELASTICITY = ("GEM", "STAR", "BSE")


INQUIRY_SYSTEM_PROMPT = """你是「颈线」系统的问询台审判员。用户从外部消息源看到一只股票,丢进来让你核对。
系统只做审计、不代客下单,你的回答**绝不能包含"现在买入 / 可以买 / 建议买"这类下单建议**
(这是硬约束,违反即为严重错误)。你能给出的结论只有两种:这只票"不符合"系统纪律(说明依据),
或"初审通过,进当晚海选池"(意味着今晚的盘后报告会把它纳入候选评分,而不是让用户现在动手)。

你会先拿到系统跑好的**确定性检查结果**(纪律核对:是否 ST、是否高弹题材板块、是否满足选股域;
若已同时满足母战法买点也会告诉你)与**板块年龄**、以及该票的价量结构。你还配有联网搜索工具,
可查该股票近期的新闻、公告、题材催化。

信息边界(铁律):只能依据给定的结构化数据与联网搜索实际返回的内容判断;搜不到就明说"未搜到相关
消息",绝不编造新闻/传闻/题材。系统选股规则是一套减损纪律系统而非高胜率信号(日线 2-5 日 A 股
均值回归),你的角色是排查"催化是否站得住、有无明显利空",不是预测涨跌,不要暗示"这只会涨"。

输出风格(硬约束):自由叙述,写成一段连贯的分析文字,像分析师口头点评;禁止分点列表、多维打分表、
"技术面/资金面/消息面"固定分栏模板。

结尾格式(唯一机器可读部分):写完叙述后另起一行,只写"裁决:不符合"或"裁决:初审通过"之一
(不要多余标点或解释,正文里不要提前出现"裁决:"以免解析冲突)。若确定性检查已判该票违反硬性
纪律(ST/高弹题材/选股域不过),你应当尊重该结论写"裁决:不符合";否则,只有当你发现明显利空或
催化明显站不住时才写"裁决:不符合",其余写"裁决:初审通过"。"""


@dataclass
class DeterministicResult:
    code: str
    basis_date: date
    has_data: bool
    name: str = ""
    board: str = ""                     # 中文板块标签
    close: Optional[float] = None
    disqualifiers: List[str] = field(default_factory=list)   # 硬性纪律违反项(非空 → 不符合)
    passes_discipline: bool = False
    passes_buypoint_today: bool = False
    score: Optional[float] = None
    sectors: List[str] = field(default_factory=list)          # 所属概念板块名
    hot_sectors: List[str] = field(default_factory=list)      # 命中今日热门(含板块年龄)
    evidence: List[str] = field(default_factory=list)


def _cfg_from_active(db_path: Optional[Path]) -> Optional[MomentumConfig]:
    active = brain.get_active(db_path=db_path)
    if active is None:
        return None
    try:
        return MomentumConfig(**active.rule["config"])
    except (KeyError, TypeError):
        return None


def run_deterministic_checks(
    code: str,
    basis_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    sector_scores: Optional[List[SectorScore]] = None,
    panel_fn: Optional[Callable[..., Any]] = None,
) -> DeterministicResult:
    """确定性纪律核对 + 同码评分 + 板块年龄(§2.5 第一步)。任何异常 → `has_data=False`
    的保守结果(纪律不过不放行),绝不抛崩。`panel_fn`/`sector_scores` 可注入单测,免联网。"""
    det = DeterministicResult(code=code, basis_date=basis_date, has_data=False)
    cfg = _cfg_from_active(db_path)
    if cfg is None:
        det.evidence.append("策略大脑无现役版本,无法核对纪律(配置缺陷)。")
        return det

    build_panel = panel_fn or build_research_panel
    try:
        panel = build_panel(basis_date, basis_date, with_forward=False, parquet_dir=parquet_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台确定性检查建面板失败(%s),保守判无数据", e)
        det.evidence.append("无法加载当日行情面板,保守判定不符合。")
        return det

    if panel is None or panel.is_empty():
        det.evidence.append(f"{basis_date} 全市场面板为空(可能该日无数据),无法核对。")
        return det

    panel = S.add_ret_rank_column(panel)
    sub = panel.filter(panel["ts_code"] == code)
    if sub.is_empty():
        det.evidence.append(
            f"查无 {code} 在 {basis_date} 的行情(可能停牌 / 次新未上市 / 代码有误),无法核对纪律。"
        )
        return det

    row = sub.row(0, named=True)
    det.has_data = True
    det.close = row.get("close")
    board_raw = row.get("board", "MAIN")
    det.board = _BOARD_LABEL.get(board_raw, board_raw)

    # —— 硬性纪律核对(base_universe 选股域 + 现役规则启用的禁买项;不含买点时机)——
    dq = det.disqualifiers
    if row.get("is_st"):
        dq.append("ST/*ST(选股域清洗,禁买)")
    if board_raw == Board.BSE.value:
        dq.append("北交所(选股域排除:数据有新三板回填瑕疵 / 流动性薄)")
    if cfg.forbid_high_elasticity and board_raw in _HIGH_ELASTICITY and board_raw != Board.BSE.value:
        dq.append("高弹题材板块(创业板/科创板,20% 涨跌幅易跌停,现役规则风控剔除)")
    close = row.get("close")
    if close is not None and close < 2.0:
        dq.append("股价 < 2 元(低价股 / 面值退市区,选股域排除)")
    amt = row.get("amount_ma20")
    if amt is not None and amt < 20000:
        dq.append("20 日均额 < 2000 万元(流动性不足 / 滑点失真,选股域排除)")
    if row.get("ma20") is None:
        dq.append("无 MA20(上市未满 20 交易日,形态未成形)")
    if cfg.forbid_new_days is not None:
        dsl = row.get("days_since_listing")
        if dsl is not None and dsl < cfg.forbid_new_days:
            dq.append(f"次新股(上市 {int(dsl)} < {cfg.forbid_new_days} 自然日,现役规则剔除)")

    det.passes_discipline = not dq

    # —— 同码买点/评分(evidence,不作硬门槛)——
    try:
        mask_val = sub.select(build_entry_mask(cfg).alias("_m")).row(0)[0]
        det.passes_buypoint_today = bool(mask_val)
        if det.passes_buypoint_today:
            det.score = round(float(sub.select(_base_score_expr(cfg).alias("_s")).row(0)[0]), 1)
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台买点/评分核算异常(%s,不影响纪律核对)", e)

    # —— 板块名 + 板块年龄(§2.5「板块年龄」)——
    try:
        member_map = load_member_map(parquet_dir=parquet_dir)
        index_names = load_index_names(parquet_dir=parquet_dir)
        boards = member_map.get(code, [])
        det.sectors = [index_names.get(b, b) for b in boards]
        if boards:
            if sector_scores is None:
                sector_scores = compute_sector_strength(basis_date, parquet_dir=parquet_dir)
            hot = {s.index_code: s for s in (sector_scores or [])}
            det.hot_sectors = [
                f"{hot[b].name}(板块年龄{hot[b].board_age}天,20日{hot[b].ret_20d:+.1%})"
                for b in boards if b in hot
            ]
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台板块年龄核算异常(%s,不影响纪律核对)", e)

    _build_evidence(det, cfg)
    return det


def _build_evidence(det: DeterministicResult, cfg: MomentumConfig) -> None:
    ev = det.evidence
    ev.append(f"板块:{det.board}" + ("(允许)" if not any("板块" in d or "北交所" in d for d in det.disqualifiers) else "(被排除)"))
    if det.disqualifiers:
        ev.append("硬性纪律未过:" + ";".join(det.disqualifiers))
    else:
        ev.append("硬性纪律核对通过(非 ST、板块允许、满足选股域流动性/价格/形态门槛)。")
    if det.passes_buypoint_today:
        ev.append(f"今日已同时满足母战法买点(pullback/breakout),展示排序分约 {det.score}。")
    else:
        ev.append("今日尚未满足母战法买点时机(不影响初审:初审通过=进当晚海选池等报告统一评分,不代表现在买)。")
    if det.hot_sectors:
        ev.append("命中今日热门板块:" + "、".join(det.hot_sectors))
    elif det.sectors:
        ev.append("所属概念板块:" + "、".join(det.sectors) + "(今日非热门)")


def build_llm_context(det: DeterministicResult, quote: Optional[Any] = None) -> str:
    """把确定性检查 + 实时行情组装成喂 LLM 的结构化上下文(纯文本块,不是 JSON)。"""
    lines = [
        f"股票代码:{det.code};名称:{det.name or '未知'};交易所板块:{det.board}",
        f"确定性纪律核对:{'通过' if det.passes_discipline else '未通过'}",
    ]
    if det.disqualifiers:
        lines.append("硬性纪律违反项:" + ";".join(det.disqualifiers))
    if det.close is not None:
        lines.append(f"最近收盘:{det.close:.2f} 元")
    lines.append(f"今日母战法买点:{'已满足' if det.passes_buypoint_today else '未满足'}" +
                 (f";展示排序分约 {det.score}" if det.score is not None else ""))
    if det.hot_sectors:
        lines.append("命中今日热门板块(含板块年龄):" + "、".join(det.hot_sectors))
    elif det.sectors:
        lines.append("所属概念板块:" + "、".join(det.sectors))
    if quote is not None:
        try:
            chg = (quote.price / quote.pre_close - 1) if quote.pre_close else None
            chg_txt = f"{chg:+.1%}" if chg is not None else "未知"
            lines.append(f"盘中实时(若在交易时段):现价 {quote.price:.2f},涨跌 {chg_txt}")
        except Exception:  # noqa: BLE001
            pass
    lines.append("请结合以上确定性结果与联网搜索,判断该票近期是否有站得住的催化或明显利空,"
                 "并按系统纪律给出「不符合」或「初审通过」的裁决(绝不给买入建议)。")
    return "\n".join(lines)


_VERDICT_RE = re.compile(r"裁决[:：]\s*(不符合|初审通过)")


def _parse_llm_verdict(content: str) -> tuple:
    """返回 (llm_says_reject: Optional[bool], narrative)。无标签 → (None, 原文)。"""
    matches = list(_VERDICT_RE.finditer(content))
    if not matches:
        return None, content.strip()
    last = matches[-1]
    narrative = (content[: last.start()] + content[last.end():]).strip() or content.strip()
    return (last.group(1) == "不符合"), narrative


def run_inquiry(
    code: str,
    messages: List[Dict[str, str]],
    *,
    basis_date: date,
    pool_date: date,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    provider: Optional[LLMProvider] = None,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Any]]] = None,
    transport: Optional[Any] = None,
    sector_scores: Optional[List[SectorScore]] = None,
    panel_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """跑一次问询。返回 `{reply, verdict, evidence, degraded}`。verdict=初审通过 → 写
    `inquiry_pool[pool_date]`(§2.5)。`provider=None` → LLM 段降级「未激活」,裁决只由确定性
    纪律决定。任何 LLM 异常都不改写"确定性已判不符合"的硬结论。"""
    det = run_deterministic_checks(
        code, basis_date, db_path=db_path, parquet_dir=parquet_dir,
        sector_scores=sector_scores, panel_fn=panel_fn,
    )

    # —— 硬门槛:确定性纪律不过 → 直接不符合,不劳 LLM(纪律不过不放行)——
    if not det.passes_discipline:
        reply = _degraded_reply_reject(det)
        # 硬性不符合也让 LLM(若有)补一段自然语言解释?为省调用 + 避免 LLM 试图翻案,
        # 直接用确定性文案。裁决锁死不符合。
        return {"reply": reply, "verdict": VERDICT_REJECT, "evidence": det.evidence, "degraded": provider is None}

    # —— 确定性通过 → LLM 段(消息面/催化 + 自然语言)——
    degraded = False
    llm_reject = None
    if provider is None:
        degraded = True
        reply = _degraded_reply_pass(det)
    else:
        quote = None
        if quotes_fn is not None:
            try:
                quote = (quotes_fn([code]) or {}).get(code)
            except Exception as e:  # noqa: BLE001
                logger.warning("问询台实时取数失败(%s,LLM 段不注入盘中行情)", e)
        chat_messages = [ChatMessage(role="system", content=INQUIRY_SYSTEM_PROMPT),
                         ChatMessage(role="user", content=build_llm_context(det, quote))]
        for m in messages:
            role = m.get("role")
            if role in ("user", "assistant"):
                chat_messages.append(ChatMessage(role=role, content=m.get("content", "")))
        try:
            result = provider.chat(chat_messages, enable_search=True, transport=transport)
        except Exception as e:  # noqa: BLE001
            logger.warning("问询台 LLM 调用异常(%s,降级为确定性结论)", e)
            result = None
        if result is None or not result.ok:
            degraded = True
            reply = _degraded_reply_pass(det)
        else:
            llm_reject, reply = _parse_llm_verdict(result.content)

    # —— 裁决合成:确定性已通过;仅 LLM 显式否决才翻成不符合 ——
    verdict = VERDICT_REJECT if (llm_reject is True) else VERDICT_PASS
    if verdict == VERDICT_PASS:
        try:
            add_to_inquiry_pool(pool_date, code, name=det.name or None,
                                reason="问询台初审通过", db_path=db_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("写 inquiry_pool 失败(%s,不影响响应)", e)
    return {"reply": reply, "verdict": verdict, "evidence": det.evidence, "degraded": degraded}


def _degraded_reply_reject(det: DeterministicResult) -> str:
    parts = [f"{det.code} 未通过系统的硬性纪律核对。"]
    if det.disqualifiers:
        parts.append("原因:" + ";".join(det.disqualifiers) + "。")
    parts.append("按纪律不予放行——这不是买卖建议,只是说明它不进入今晚的候选评分范围。")
    return "".join(parts)


def _degraded_reply_pass(det: DeterministicResult) -> str:
    parts = [f"{det.code} 通过了系统的硬性纪律核对(非 ST、板块允许、满足选股域门槛)。"]
    if det.passes_buypoint_today:
        parts.append(f"今日已同时满足母战法买点,展示排序分约 {det.score}。")
    else:
        parts.append("今日尚未走出母战法买点时机。")
    if det.hot_sectors:
        parts.append("命中今日热门板块:" + "、".join(det.hot_sectors) + "。")
    parts.append("LLM 消息面审判未激活(未配置 LLM key),本次只做确定性纪律核对;"
                 "按纪律初审通过,已纳入当晚海选池等报告统一评分——这不是买入建议。")
    return "".join(parts)


__all__ = [
    "DeterministicResult",
    "INQUIRY_SYSTEM_PROMPT",
    "run_deterministic_checks",
    "build_llm_context",
    "run_inquiry",
]
