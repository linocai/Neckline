"""消息面告警存取(plan §五 v1.3-③-C4)。`news_alerts` 表(命中告警,独立表,
`neckline.db`)的读写单一通道——同 `report/holding_store.py` 之于
`holding_eod_check` 的角色。

**不存 `name`**(展示名读时从 `stock_basic` 解析,同 `llm_judgments` 表惯例:
该表也只存 `ts_code`,展示名在 `_shape_report`/`_shape_candidate` 读时另外解析,
不在事件表里重复存一份随时可能过期的名字快照)。

扫描**状态**(`NewsAlertScanStatus`,"没扫到 vs 扫了没有")不在本模块——那是随
整份报告快照落 `reports.news_alerts_scan_json`,见 `report/store.py::save_report`
的 `news_alerts_scan` 参数。本模块只管**命中告警条目**本身。

**跨日事件级去重(2026-07-26 必改,plan §五 v1.3-③-C4)**:`event_date`(事件本身
发生日,REDUCTION 类=TuShare `ann_date`)+ `event_key`(同一事件的稳定标识,
REDUCTION 类=`holder_name|change_vol|change_ratio`)一起决定"这是不是已经报过的
同一件事"——`load_seen_event_keys` 供 `report/news_alerts.py::_scan_reduction`
在生成本次报告前先查"哪些事件已经在更早的报告里出现过",过滤掉不再重复生成。
LLM 来源的行 `event_date`/`event_key` 恒为 `NULL`/`''`,不参与、也查不出任何
匹配(SQL `event_date IS NOT NULL` 天然把它们排除在外)——LLM 侧维持"可能跨日
重复"的现状,见 `news_alerts.py` 模块头。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from neckline.db import connection, init_schema


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_news_alerts(trade_date: date, items: List[Any], db_path: Optional[Path] = None) -> None:
    """把当日扫描命中的告警落库(幂等覆盖同 (ts_code, trade_date, category,
    event_key),同一报告日重跑取最新一次扫描结果,同 `llm_judgments`/
    `holding_eod_check` 惯例)。`items` 为 `report.news_alerts.NewsAlertItem`
    (duck-typed:需 ts_code/category/summary/source,`event_date`/`event_key`
    缺失时按 `None`/`''` 兜底,兼容未来其它调用方不带这两个字段)。空 → 不写
    (不清空当日已有告警——若本次扫描因异常整体降级为空〔见
    `empty_news_alerts_report`〕,不应把此前成功扫到的告警覆盖删掉;真实的
    "扫了确认没有"体现在 `scan_statuses` 里,不体现在清空这张表)。"""
    if not items:
        return
    init_schema(db_path)
    td = _d(trade_date)
    now = _now()
    with connection(db_path) as conn:
        for it in items:
            conn.execute(
                "INSERT OR REPLACE INTO news_alerts "
                "(ts_code, trade_date, event_date, event_key, category, summary, source, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    it.ts_code, td, getattr(it, "event_date", None), getattr(it, "event_key", "") or "",
                    it.category, it.summary, it.source, now,
                ),
            )


def load_news_alerts(trade_date: date, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某报告日命中的全部告警条目(不含 `name`,调用方自行解析展示名——同
    `report.store.load_llm_judgments` 惯例)。防御性 `init_schema`:查一个还没
    扫描过的交易日是正常场景,不应因表未建过而崩。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, category, summary, source, created_at FROM news_alerts "
            "WHERE trade_date=? ORDER BY id",
            (_d(trade_date),),
        ).fetchall()
    return [
        {"ts_code": r[0], "category": r[1], "summary": r[2], "source": r[3], "created_at": r[4]}
        for r in rows
    ]


def load_seen_event_keys(category: str, db_path: Optional[Path] = None) -> Set[Tuple[str, str, str]]:
    """已经落库过的 `(ts_code, event_date, event_key)` 三元组集合,按 `category`
    过滤,只算 `event_date` 非空且 `event_key` 非空串的行(有可靠事件日 + 事件
    标识才谈得上"同一事件"跨日去重;LLM 来源两列恒空,天然被这个查询排除,不会
    被误判成"已出现过"——见模块 docstring)。供 `report/news_alerts.py::
    _scan_reduction` 在生成本次候选列表前过滤掉已经报过的事件。跨越**全部**
    历史 `trade_date`(不限定某一天),这正是"跨日"去重的含义。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, event_date, event_key FROM news_alerts "
            "WHERE category=? AND event_date IS NOT NULL AND event_key != ''",
            (category,),
        ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


__all__ = ["save_news_alerts", "load_news_alerts", "load_seen_event_keys"]
