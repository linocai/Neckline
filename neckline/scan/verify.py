"""扫描层三项自检(plan §五 V2-④ 验收:「三表 bootstrap + 单日 refresh + verify
三项自检全绿」),CLI 与单测共用同一实现(同 `industry_strength_store.
verify_industry_strength` 体例)。

**"无洞"在这里的含义与 `industry_strength_daily` 不同,如实登记**:后者的
"无洞"是"区间内每个交易日表里都该有至少一行"——因为行业强度理论上每天都能
评出几个达标行业。本层三张表在"今天没有涨停共振/没有够格的相关对"的正常
交易日会**合法地零行**(§五 V2-④ 原文"当日无篮子是合法输出"),故不能照搬
"每天必须有行"的判据。本模块的①改为**结构健全性**(不出现非交易日 / 越界
日期的行)+ **跨表覆盖一致性**(`limit_cluster_daily` 的簇必须能在
`leader_structure_daily` 里找到对应行;规模未超限的簇也必须能在
`corr_matrix_daily` 里找到对应行)——这两条能捕捉"某一步日更半路跑丢了"的
真实故障,同时不会把"今天确实很安静"误判成"哪里坏了"。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline.db import connection, init_schema
from neckline.scan import cluster, corr, leader

_VALID_ROLES = {"leader", "core", "elastic", "unknown"}


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _default_range(db_path: Optional[Path]) -> Optional[Tuple[date, date]]:
    """三表全空 → `None`(verify 领域是"三表 bootstrap 过了吗",不是"库存不存在
    这件事本身要不要报错")。缺省区间取三表 `trade_date` 的并集范围。"""
    init_schema(db_path)
    bounds: List[str] = []
    with connection(db_path) as conn:
        for table in (cluster.TABLE, corr.TABLE, leader.TABLE):
            row = conn.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {table}").fetchone()
            if row and row[0] is not None:
                bounds.extend([row[0], row[1]])
    if not bounds:
        return None
    return _parse_d(min(bounds)), _parse_d(max(bounds))


def _non_trading_day_rows(db_path: Optional[Path], lo: date, hi: date) -> Dict[str, List[str]]:
    """①交易日范围健全性:三张表里**不许出现**非交易日 / 越界的 `trade_date`。"""
    from neckline.calendar import trading_days_between

    valid = {_d(d) for d in trading_days_between(lo, hi)}
    out: Dict[str, List[str]] = {}
    with connection(db_path) as conn:
        for table in (cluster.TABLE, corr.TABLE, leader.TABLE):
            rows = {
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT trade_date FROM {table} WHERE trade_date>=? AND trade_date<=?",
                    (_d(lo), _d(hi)),
                )
            }
            bad = sorted(rows - valid)
            if bad:
                out[table] = bad
    return out


def _cluster_key_self_consistency(db_path: Optional[Path], lo: date, hi: date) -> List[str]:
    """②键自洽·`limit_cluster_daily`:落库的 `cluster_size` 必须等于该
    (trade_date, cluster_key) 实际的成员数;`anchor_industry`/`anchor_concept`
    必须恰好一个非空(簇只能被一种维度锚定)。"""
    errors: List[str] = []
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, cluster_key, ts_code, cluster_size, anchor_industry, anchor_concept "
            f"FROM {cluster.TABLE} WHERE trade_date>=? AND trade_date<=?",
            (_d(lo), _d(hi)),
        ).fetchall()
    by_key: Dict[Tuple[str, str], List[Tuple]] = {}
    for r in rows:
        by_key.setdefault((r[0], r[1]), []).append(r)
    for (td, key), members in by_key.items():
        actual_size = len({m[2] for m in members})
        stored_size = members[0][3]
        if stored_size != actual_size:
            errors.append(f"limit_cluster_daily {td}/{key}: cluster_size={stored_size} 但实际成员数={actual_size}")
        for _, _, ts_code, _, anchor_ind, anchor_con in members:
            if (anchor_ind is not None) == (anchor_con is not None):
                errors.append(
                    f"limit_cluster_daily {td}/{key}/{ts_code}: anchor_industry/anchor_concept "
                    f"必须恰好一个非空,实得 ({anchor_ind!r}, {anchor_con!r})"
                )
    return errors


def _corr_key_self_consistency(db_path: Optional[Path], lo: date, hi: date) -> List[str]:
    """②键自洽·`corr_matrix_daily`:`code_a<code_b` 严格成立(不存反序/自配对)。"""
    errors: List[str] = []
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, scope_key, code_a, code_b FROM {corr.TABLE} "
            f"WHERE trade_date>=? AND trade_date<=? AND code_a>=code_b",
            (_d(lo), _d(hi)),
        ).fetchall()
    for td, scope, a, b in rows:
        errors.append(f"corr_matrix_daily {td}/{scope}: code_a={a!r} 应严格小于 code_b={b!r}")
    return errors


def _leader_key_self_consistency(db_path: Optional[Path], lo: date, hi: date) -> List[str]:
    """②键自洽·`leader_structure_daily`:`role_mech` 恒在合法枚举内;同一
    (trade_date, cluster_key) 内 `rs_rank` 不重复(允许多个 NULL,但非空值
    必须两两不同——并列必须已被 tie-break 拆开,见 `leader.py` 的确定性排名)。"""
    errors: List[str] = []
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, cluster_key, ts_code, rs_rank, role_mech FROM {leader.TABLE} "
            f"WHERE trade_date>=? AND trade_date<=?",
            (_d(lo), _d(hi)),
        ).fetchall()
    seen: Dict[Tuple[str, str], set] = {}
    for td, key, ts_code, rs_rank, role in rows:
        if role not in _VALID_ROLES:
            errors.append(f"leader_structure_daily {td}/{key}/{ts_code}: role_mech={role!r} 不在合法枚举 {_VALID_ROLES}")
        if rs_rank is not None:
            bucket = seen.setdefault((td, key), set())
            if rs_rank in bucket:
                errors.append(f"leader_structure_daily {td}/{key}: rs_rank={rs_rank} 在簇内重复(并列未拆开)")
            bucket.add(rs_rank)
    return errors


def _cross_table_coverage(db_path: Optional[Path], lo: date, hi: date) -> List[str]:
    """②键自洽(跨表)::`limit_cluster_daily` 的每个成员行都必须能在
    `leader_structure_daily` 找到对应行(leader.py 对簇成员无规模上限,理论上
    不该漏);规模未超限的簇(`2<=size<=MAX_SCOPE_MEMBERS_FOR_CORR`)必须能在
    `corr_matrix_daily` 找到至少一行(哪怕 `corr=NULL`,只要"算过"就该有行)。"""
    errors: List[str] = []
    with connection(db_path) as conn:
        cluster_members = conn.execute(
            f"SELECT trade_date, cluster_key, ts_code, cluster_size FROM {cluster.TABLE} "
            f"WHERE trade_date>=? AND trade_date<=?",
            (_d(lo), _d(hi)),
        ).fetchall()
        leader_keys = {
            (r[0], r[1], r[2]) for r in conn.execute(
                f"SELECT trade_date, cluster_key, ts_code FROM {leader.TABLE} "
                f"WHERE trade_date>=? AND trade_date<=?",
                (_d(lo), _d(hi)),
            )
        }
        corr_scopes = {
            (r[0], r[1]) for r in conn.execute(
                f"SELECT DISTINCT trade_date, scope_key FROM {corr.TABLE} "
                f"WHERE trade_date>=? AND trade_date<=?",
                (_d(lo), _d(hi)),
            )
        }
    reported_corr: set = set()
    for td, key, ts_code, size in cluster_members:
        if (td, key, ts_code) not in leader_keys:
            errors.append(f"limit_cluster_daily {td}/{key}/{ts_code} 未在 leader_structure_daily 中找到对应行")
        if 2 <= size <= corr.MAX_SCOPE_MEMBERS_FOR_CORR and (td, key) not in corr_scopes and (td, key) not in reported_corr:
            errors.append(f"limit_cluster_daily {td}/{key}(size={size})未超规模上限,但 corr_matrix_daily 无任何行")
            reported_corr.add((td, key))
    return errors


def _fingerprint_mismatches(db_path: Optional[Path], lo: date, hi: date) -> List[str]:
    """③口径指纹一致:`corr_matrix_daily.window` 全部等于现行常量
    `corr.PRICE_WINDOW_DAYS`(若常量改动,旧行会被如实点名,同
    `industry_strength_daily` quantile/min_members 指纹纪律)。"""
    with connection(db_path) as conn:
        bad = conn.execute(
            f"SELECT DISTINCT window FROM {corr.TABLE} WHERE trade_date>=? AND trade_date<=? AND window!=?",
            (_d(lo), _d(hi), corr.PRICE_WINDOW_DAYS),
        ).fetchall()
    return [f"corr_matrix_daily 存在 window={w[0]} 的行,与现行常量 {corr.PRICE_WINDOW_DAYS} 不符" for w in bad]


def verify_scan_layer(
    start: Optional[date] = None, end: Optional[date] = None, *, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """三项自检(CLI `scripts/scan_layer.py verify` 与单测共用)。返回
    `{"ok": bool, "range": [lo,hi], ...}`;`ok=False` 时各项明细列出具体问题。"""
    init_schema(db_path)
    if start is None or end is None:
        bounds = _default_range(db_path)
        if bounds is None:
            return {
                "ok": False, "reason": "三张表均为空(未 bootstrap / 未日更)",
                "non_trading_day_rows": {}, "self_consistency_errors": [], "fingerprint_mismatches": [],
            }
        lo, hi = start or bounds[0], end or bounds[1]
    else:
        lo, hi = start, end

    non_trading = _non_trading_day_rows(db_path, lo, hi)
    self_consistency = (
        _cluster_key_self_consistency(db_path, lo, hi)
        + _corr_key_self_consistency(db_path, lo, hi)
        + _leader_key_self_consistency(db_path, lo, hi)
        + _cross_table_coverage(db_path, lo, hi)
    )
    fingerprints = _fingerprint_mismatches(db_path, lo, hi)

    ok = not non_trading and not self_consistency and not fingerprints
    return {
        "ok": ok,
        "range": [_d(lo), _d(hi)],
        "non_trading_day_rows": non_trading,
        "self_consistency_errors": self_consistency,
        "fingerprint_mismatches": fingerprints,
    }


__all__ = ["verify_scan_layer"]
