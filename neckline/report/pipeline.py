"""报告管线编排(plan 2.5,§2.6 历史回放的天然落地点)。串起情绪仪表盘(2.1)+
强势板块(2.2)+ 候选评分(2.3)+ LLM 审判(2.4,前10只)+ 落库(store.py)+
markdown 渲染(render.py)——`scripts/report.py` 的核心入口就是本模块的
`build_report`。

**历史回放(§2.6)天然支持**:`build_report(trade_date, ...)` 对任意历史交易日都
能跑(只要该日的 Parquet/SQLite 数据已落地),不需要另一套"回放专用"代码——
喂历史 = 回测、喂今日 = 报告,这里的"喂哪天"只是一个参数,同一份代码路径。

**策略大脑是唯一权威**(2026-07-20 用户拍板,凌驾于本文件以外的任何假设):
候选评分的规则从 `neckline.strategy.brain.get_active()` 读,大脑无现役版本时
**拒绝生成报告并抛出清晰错误**(这是配置缺陷,不是"优雅降级"的场景——报告没有
规则基础,生成出来的候选毫无意义,不该假装正常跑完)。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.data.top_list import top_list_lookup
from neckline.llm.base import LLMProvider
from neckline.llm.factory import get_provider
from neckline.llm.judge import JudgeResult, judge_candidate
from neckline.report import store
from neckline.report.candidates import Candidate, build_candidates
from neckline.report.render import render_markdown
from neckline.report.sectors import SectorScore, compute_sector_strength, load_index_names, load_member_map
from neckline.report.sentiment import SentimentDashboard, compute_sentiment
from neckline.strategy import brain

TOP_N_TOTAL = 20
TOP_N_JUDGED = 10


@dataclass
class ReportBundle:
    trade_date: date
    strategy_version: str
    generated_at: str
    sentiment: SentimentDashboard
    sectors: List[SectorScore]
    candidates: List[Candidate]
    judged: Dict[str, JudgeResult]  # ts_code -> JudgeResult(仅前 top_n_judged 只)
    markdown: str


def build_report(
    trade_date: date,
    *,
    llm_provider: Optional[LLMProvider] = None,
    llm_transport: Optional[Any] = None,
    top_n_total: int = TOP_N_TOTAL,
    top_n_judged: int = TOP_N_JUDGED,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    save: bool = True,
) -> ReportBundle:
    """生成 `trade_date` 这一天的完整盘后报告。

    `llm_provider`:显式传入用于测试注入(如 `_StubProvider`/真 provider + Mock
    Transport);为 `None`(默认,生产用法)时从 `.env` 现读(`llm.factory.get_provider()`)
    ——本项目当前无 key,现读结果恒为 `None`,LLM 审判段落走「未激活」占位链路。
    `save=False` 供只想拿 `ReportBundle`/跑历史回放对照、不想写库的调用方(如单测)。
    """
    active = brain.get_active(db_path=db_path)
    if active is None:
        raise RuntimeError(
            "策略大脑无现役版本(strategy_versions 表 is_active=1 为空)——先跑 "
            "`python -m research.rule_v1 --commit` 定版规则 v1,再生成报告。"
        )

    sentiment = compute_sentiment(trade_date, parquet_dir=parquet_dir)
    sector_scores = compute_sector_strength(trade_date, parquet_dir=parquet_dir)
    member_map = load_member_map(parquet_dir=parquet_dir)
    index_names = load_index_names(parquet_dir=parquet_dir)

    candidates = build_candidates(
        trade_date,
        active.rule,
        sector_scores=sector_scores,
        member_map=member_map,
        index_names=index_names,
        top_n=top_n_total,
        parquet_dir=parquet_dir,
        db_path=db_path,
    )

    provider = llm_provider or get_provider()
    top_list = top_list_lookup(trade_date, parquet_dir=parquet_dir)

    judged: Dict[str, JudgeResult] = {}
    for c in candidates[:top_n_judged]:
        result = judge_candidate(
            c, provider=provider, top_list_row=top_list.get(c.ts_code), transport=llm_transport
        )
        judged[c.ts_code] = result
        if save:
            store.save_llm_judgment(trade_date, result, db_path=db_path)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    markdown = render_markdown(
        trade_date=trade_date,
        strategy_version=active.version,
        generated_at=generated_at,
        sentiment=sentiment,
        sectors=sector_scores,
        candidates=candidates,
        judged=judged,
        top_n_judged=top_n_judged,
    )

    if save:
        store.save_report(
            trade_date,
            strategy_version=active.version,
            sentiment=_jsonable(sentiment),
            sectors=[_jsonable(s) for s in sector_scores],
            candidates=[_jsonable(c.public_dict()) for c in candidates],
            markdown=markdown,
            db_path=db_path,
        )

    return ReportBundle(
        trade_date=trade_date,
        strategy_version=active.version,
        generated_at=generated_at,
        sentiment=sentiment,
        sectors=sector_scores,
        candidates=candidates,
        judged=judged,
        markdown=markdown,
    )


def _jsonable(obj: Any) -> Any:
    """dataclass / dict / list 递归转 JSON 安全结构(`date` -> ISO 字符串)。
    `store.save_report` 的 `sentiment`/`sectors`/`candidates` 参数需要能直接
    `json.dumps`,而 `SentimentDashboard.trade_date` 等字段是 `date` 对象。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


__all__ = ["ReportBundle", "build_report", "TOP_N_TOTAL", "TOP_N_JUDGED"]
