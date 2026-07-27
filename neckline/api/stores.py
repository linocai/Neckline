"""API 层业务台账存取(plan 4A.4/4A.5):`devices`(APNs 注册)+ `inquiry_pool`
(问询台海选票)。极简 CRUD,幂等——沿本项目既有 store 姿势(`report/store.py`/
`sentinel/dedup.py`),stdlib sqlite3 直连,不引 ORM。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.db import connection, init_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# —— devices(APNs 设备注册,plan 4A.5 / 4B.5)——————————————————————————————

def upsert_device(token: str, platform: str = "ios", db_path: Optional[Path] = None) -> None:
    """注册/更新一个 APNs device token(复用 LinoN `upsert_device_token` 语义)。同一
    token 再注册只刷新 `updated_at`,不重复建行。空 token → 静默忽略(客户端首次授权前
    可能拿不到 token,不应因此报错)。"""
    token = (token or "").strip()
    if not token:
        return
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO devices (token, platform, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET platform=excluded.platform, updated_at=excluded.updated_at",
            (token, (platform or "ios").strip() or "ios", now, now),
        )


def list_device_tokens(db_path: Optional[Path] = None) -> List[str]:
    """全部已注册 device token(16:00 报告 / 退潮刹车推送时遍历)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute("SELECT token FROM devices ORDER BY created_at").fetchall()
    return [r[0] for r in rows]


# —— inquiry_pool(问询台海选池,plan §2.5 / 4A.5)——————————————————————————

def add_to_inquiry_pool(
    trade_date: date, ts_code: str, name: Optional[str] = None,
    reason: Optional[str] = None, db_path: Optional[Path] = None,
) -> None:
    """把一只票纳入某交易日的海选池(供当晚 `report.py` 扩候选 universe,§2.5)。
    `INSERT OR IGNORE`——同日同票复入不重复(UNIQUE(trade_date, ts_code) 幂等)。

    **v1.3.3 起问询台不再自动写本表**(「初审通过进海选池」退役,改由用户在客户端一键
    加自选);本函数与消费侧(`load_pending_inquiry_codes`/`mark_inquiry_pool_consumed`)
    **保留不动**作向后兼容——空池 noop,历史待消费行仍会被正常消费掉。

    **`ts_code` 写入通道归一(v1.3.3,与 positions/decision_log 同批)**:消费侧
    `load_pending_inquiry_codes` 的返回值会被并进候选评分 universe 去 join 行情面板
    (TuShare 口径 `300759.SZ`),裸码 join 不上 = 强制纳入静默失效。"""
    init_schema(db_path)
    from neckline.review.parse import normalize_ts_code
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO inquiry_pool (trade_date, ts_code, name, reason, created_at) "
            "VALUES (?,?,?,?,?)",
            (_d(trade_date), normalize_ts_code(ts_code), name, reason, _now()),
        )


def load_inquiry_pool(trade_date: date, db_path: Optional[Path] = None) -> List[dict]:
    """某交易日**入池当日**(`trade_date` 列)的票(供审计 / 单测断言「有没有入池」)。
    返回 `{ts_code, name, reason, created_at}` 列表,按入池时间升序。**注意(v1.1-D
    后)**:`build_report` 的消费不再调用本函数——入池当日 ≠ 消费目标日,消费改走
    `load_pending_inquiry_codes`(见该函数 docstring「根因」说明)。本函数保留只为
    「某天究竟有没有票入池」这一审计/展示用途。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, name, reason, created_at FROM inquiry_pool "
            "WHERE trade_date=? ORDER BY created_at, ts_code",
            (_d(trade_date),),
        ).fetchall()
    return [{"ts_code": r[0], "name": r[1], "reason": r[2], "created_at": r[3]} for r in rows]


def load_pending_inquiry_codes(report_date: date, db_path: Optional[Path] = None) -> List[dict]:
    """**v1.1-D 问询窗口修复**:`build_report(report_date)` 真正消费的海选池查询。

    根因(生产真洞,见 PROJECT_PLAN §五 v1.1-D.1):旧消费逻辑是
    `WHERE trade_date = report_date`——但入池时的 `trade_date` 是「入池当日」,
    不是「哪份报告该消费它」。16:35 报告已生成后再问询通过的票,入池当日 = 今天,
    而下一份要消费它的报告是**明天**的报告,`trade_date` 永远对不上明天的
    `report_date`,该票被永久晾在原地——这就是"问询台永久掉缝"的生产真洞。

    修复:消费判据从"匹配入池当日"改成"待消费标记"——
        `WHERE consumed_report_date IS NULL OR consumed_report_date = report_date`
    不论哪天入池,只要还没被任何报告标记消费(`NULL`),下一份生成的报告就必然
    收进来(不再要求入池当日与报告日相等);`= report_date` 这半支路是**同日补跑
    幂等**(同一天报告重新生成,应该重新纳入上次这天报告已消费过的同一批票,不能
    "生成过一次就再也拿不到")。

    返回 `{ts_code, name, reason, created_at}` 列表,按入池时间升序(去重由调用方
    `build_candidates`/`score_candidates` 的 forced_codes 逻辑处理,本函数原样吐
    全部匹配行,允许同一 ts_code 因跨日多次问询而有多行——那也无妨,消费方只取
    `ts_code` 去重后使用)。"""
    init_schema(db_path)
    td = _d(report_date)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, name, reason, created_at FROM inquiry_pool "
            "WHERE consumed_report_date IS NULL OR consumed_report_date=? "
            "ORDER BY created_at, ts_code",
            (td,),
        ).fetchall()
    return [{"ts_code": r[0], "name": r[1], "reason": r[2], "created_at": r[3]} for r in rows]


def mark_inquiry_pool_consumed(report_date: date, db_path: Optional[Path] = None) -> None:
    """报告(`report_date`)落库成功后调用:把所有仍待消费(`consumed_report_date
    IS NULL`)的行标记为「已被本报告日消费」。**只碰待消费行**——已被别的报告日
    消费过的历史行(`consumed_report_date` 非空)不会被本次 UPDATE 覆盖(`WHERE`
    子句本身就排除了它们),保持"谁消费的就记谁"的可审计性。同日补跑(`build_report`
    对同一天重复调用)天然幂等:第一次跑把当时的待消费行标记成 `= report_date`;
    `load_pending_inquiry_codes` 的 `consumed_report_date = report_date` 分支下次
    还会选中它们(见该函数 docstring),但它们此时已不是 NULL,本函数的 `WHERE
    consumed_report_date IS NULL` 不会再碰它们——不重复标记,也不会把它们的
    `consumed_report_date` 错误改写成别的日期,该批票在"同一天"内保持一致。"""
    init_schema(db_path)
    td = _d(report_date)
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE inquiry_pool SET consumed_report_date=? WHERE consumed_report_date IS NULL",
            (td,),
        )


__all__ = [
    "upsert_device", "list_device_tokens",
    "add_to_inquiry_pool", "load_inquiry_pool",
    "load_pending_inquiry_codes", "mark_inquiry_pool_consumed",
]
