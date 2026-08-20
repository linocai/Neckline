"""申万 2021 版行业分类:拉取 / 落库 / 校验(V2.5.0 S2,PROJECT_PLAN §6 S2)。

**它在哪一层**:架构第一层 · 事实层的取数件 —— 只回答「这只票属于哪个申万二级行业」,
⛔ 不装任何强度、排名、最小成员数门槛。那些是**策略参数**,住 `k9/industry_heat.py`
(架构 §二 判据:凡是我会想去调的东西都落在策略层)。

**为什么是判据输入而不是增强项**:K9 全文的「相对强度」= 个股当日涨跌幅 − 所属申万
**二级**行业当日全部成员涨跌幅的中位数(裁定 2),第三层排序的「行业热度分」也读它。
分类表拉不到 → 中位数算不出 → 清单出不来。故日更失败**日志用 ERROR**(同
`industry_strength` 旧例),⛔ 不是 WARNING。

三条硬纪律(每条都踩过或被明令):

1. 🔴 **`index_member_all` 必须翻页**(§12 坑 5):单次上限 3000 行,全市场约 5897 只。
   接口超限时**不报错、只少给** —— 不翻页就静默丢一半票,下游全变成「查无行业归属」。
   `fetch_members()` 循环 offset 直到某页 < limit 为止。
2. 🔴 **只认 `src='SW2021'`**:2014 老版 `src='SW'` 在 600 元档实测**返回 0 行**(§4.4)。
3. 🔴 **归属按 `index_code` 认,⛔ 不按名称字符串**(§12 坑 6):名称会变、代码不变。
   白酒Ⅱ = `801125.SI`。

⚠ **`parent_code` 不是 `index_code`**(2026-08-20 S2 实测,§4.4 未记):TuShare 给的是
`industry_code` 形态 —— L1 的 parent_code = `'0'`,`801125.SI` 的 = `'340000'`。
⛔ 不许拿它 join `index_code`(join 不上,且不报错、只静默空)。要层级关系读
`sw_industry_member` 的 `l1_code` / `l2_code` / `l3_code`,一行里三层俱全。

**实测事实(§4.4,⛔ 不要重测,直接用)**:
    · `index_classify(src='SW2021')` → L1 **31** / L2 **134** / L3 **346**
    · `index_member_all` → **2 页拿全 5897 只**,每只恰好 1 个 L1/L2/L3,`out_date` 空 = 当前有效
    · 覆盖率 **100%**:本地 20260724 全市场 5526 只全部有申万归属

**写入姿势**:全量重拉 → 一个短事务里 `DELETE` + 批量 `INSERT`(快照语义,同
`concept_data.replace_snapshot` 先例)。⛔ 不做增量 diff —— 分类调整时增量会留下
既不属于新表也不属于旧表的孤儿行,而全量重拉本来就只要两次 API 调用。

**注入点**:`fetch_classify` / `fetch_members` 两个 fetcher 可覆盖(单测传假 client,
⛔ 不联网);`db_path` 可覆盖(⛔ 测试不许落工作库,AGENTS.md)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from neckline.data.tushare_client import (
    SW_MEMBER_PAGE_LIMIT,
    TushareResult,
    ts_index_classify,
    ts_index_member_all,
)
from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

#: 分类源。⛔ 只此一个取值(2014 老版 `'SW'` 实测返 0 行)。
SW_SRC = "SW2021"

#: 白酒Ⅱ 的二级代码(K9 第一层第 2 条排除项)。⛔ 按代码识别,不按名称。
BAIJIU_L2_CODE = "801125.SI"
BAIJIU_L2_NAME = "白酒Ⅱ"

#: 翻页安全阀:5897 只 / 3000 一页 = 2 页;给到 10 页仍拿不完 = 接口行为变了,报错停手,
#: ⛔ 不静默截断(截断正是本模块要防的那个故障)。
_MAX_PAGES = 10

_LEVELS = ("L1", "L2", "L3")


@dataclass(frozen=True)
class SwRefreshStats:
    """一次全量重拉的账。`ok=False` 时 `reason` 必填,调用方据此打 ERROR 日志。"""

    ok: bool
    reason: str = "ok"
    classify_rows: int = 0
    level_counts: Dict[str, int] = None          # type: ignore[assignment]
    member_rows: int = 0
    member_pages: int = 0

    def summary(self) -> str:
        lv = self.level_counts or {}
        return (f"classify={self.classify_rows} 行"
                f"(L1 {lv.get('L1', 0)} / L2 {lv.get('L2', 0)} / L3 {lv.get('L3', 0)})"
                f" · member={self.member_rows} 只({self.member_pages} 页)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rows(res: TushareResult) -> List[Dict[str, Any]]:
    """`TushareResult.data`(pandas DataFrame 或已是 list[dict])→ list[dict]。

    ⚠ 不在这里对 pandas 强依赖:单测喂的是 list[dict],生产喂的是 DataFrame。"""
    data = res.data
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(r) for r in data]
    if hasattr(data, "to_dict"):
        return list(data.to_dict("records"))
    raise TypeError(f"无法识别的 TuShare 返回类型:{type(data)!r}")


def _s(v: Any) -> str:
    """`None` / NaN / 空白 → 空串。TuShare 的空值在 DataFrame 里可能是 `float('nan')`。"""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:      # NaN
        return ""
    return str(v).strip()


# ══════════════════════════════════════════════════════════════════════════
# 拉取
# ══════════════════════════════════════════════════════════════════════════

def fetch_classify(
    fetcher: Callable[..., TushareResult] = ts_index_classify,
) -> TushareResult:
    """三层分类表。逐层各拉一次(`level='L1'|'L2'|'L3'`)再合并。

    ⚠ **逐层拉而不是一次拉全**:`index_classify` 不传 `level` 时各版本返回的
    层级集合并不稳定;逐层拉能让「哪一层拿到几条」变成可断言的账
    (§4.4 的 31/134/346 就是按层数出来的)。三次调用的配额代价可以忽略。
    """
    merged: List[Dict[str, Any]] = []
    for level in _LEVELS:
        res = fetcher(level=level, src=SW_SRC)
        if not res.ok:
            return TushareResult.fail(f"index_classify({level}) 失败:{res.reason}")
        rows = _rows(res)
        if not rows:
            return TushareResult.fail(
                f"index_classify({level}, src={SW_SRC}) 返回 0 行 —— "
                f"⚠ 若换成了 src='SW'(2014 老版)那是**已知会返 0 行**的坑(§4.4)")
        for r in rows:
            r.setdefault("level", level)
        merged.extend(rows)
    return TushareResult.success(merged)


def fetch_members(
    fetcher: Callable[..., TushareResult] = ts_index_member_all,
    page_limit: int = SW_MEMBER_PAGE_LIMIT,
) -> TushareResult:
    """成分归属全量,**循环 offset 翻页直到取尽**。

    🔴 这是本模块存在的核心理由(§12 坑 5):单次上限 3000 行,不翻页会静默少拿。
    终止条件 = 某页行数 **< `page_limit`**;满页则继续下一页。
    翻到 `_MAX_PAGES` 仍是满页 → **报错停手**,⛔ 不静默截断。

    返回的 `TushareResult.data` 是 `(rows, page_count)` 二元组。
    """
    all_rows: List[Dict[str, Any]] = []
    seen: set = set()
    pages = 0
    offset = 0
    while pages < _MAX_PAGES:
        res = fetcher(limit=page_limit, offset=offset)
        pages += 1
        if not res.ok:
            return TushareResult.fail(
                f"index_member_all(offset={offset}) 失败:{res.reason}")
        rows = _rows(res)
        for r in rows:
            code = _s(r.get("ts_code"))
            if not code or code in seen:
                continue
            seen.add(code)
            all_rows.append(r)
        if len(rows) < page_limit:
            return TushareResult.success((all_rows, pages))
        offset += page_limit
    return TushareResult.fail(
        f"index_member_all 翻到第 {_MAX_PAGES} 页仍是满页({page_limit} 行/页,"
        f"已收 {len(all_rows)} 只)—— 接口行为可能变了。⛔ 不静默截断,请人工核对上限")


# ══════════════════════════════════════════════════════════════════════════
# 落库(全量快照替换)
# ══════════════════════════════════════════════════════════════════════════

def _classify_tuples(rows: Sequence[Dict[str, Any]], now: str) -> List[tuple]:
    out = []
    for r in rows:
        code = _s(r.get("index_code"))
        if not code:
            continue
        out.append((
            code,
            _s(r.get("industry_name")) or _s(r.get("name")),
            _s(r.get("level")).upper(),
            _s(r.get("parent_code")) or None,
            SW_SRC,
            now,
        ))
    return out


def _member_tuples(rows: Sequence[Dict[str, Any]], now: str) -> List[tuple]:
    """⚠ **同票两行会当场报错,⛔ 不静默留最后一条**:`ts_code` 是 PK,重复行本来就写不进去,
    但 SQLite 抛的是一句看不出所以然的 `UNIQUE constraint failed`。这里提前点名是哪几只 ——
    §4.4 实测「每只恰好 1 个 L1/L2/L3」,真出现重复说明接口口径变了,是要人看的事。"""
    seen: Dict[str, int] = {}
    out = []
    for r in rows:
        code = _s(r.get("ts_code"))
        l1c, l2c, l3c = _s(r.get("l1_code")), _s(r.get("l2_code")), _s(r.get("l3_code"))
        if not code or not (l1c and l2c and l3c):
            continue
        seen[code] = seen.get(code, 0) + 1
        out_date = _s(r.get("out_date"))
        out.append((
            code, _s(r.get("name")),
            l1c, _s(r.get("l1_name")),
            l2c, _s(r.get("l2_name")),
            l3c, _s(r.get("l3_name")),
            _s(r.get("in_date")) or None,
            out_date or None,
            0 if out_date else 1,          # `out_date` 空 = 当前有效(§4.4)
            now,
        ))
    dupes = sorted(c for c, n in seen.items() if n > 1)
    if dupes:
        raise ValueError(
            f"成分表里有 {len(dupes)} 只票出现多行归属(§4.4 实测每只恰好 1 个 L1/L2/L3):"
            f"{dupes[:10]}{' …' if len(dupes) > 10 else ''}")
    return out


def save_snapshot(
    classify_rows: Sequence[Dict[str, Any]],
    member_rows: Sequence[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> tuple:
    """把两张表整体换成这一份快照(一个短事务内 DELETE + INSERT)。

    ⛔ **不做增量 diff**:分类调整时增量会留下既不属于新表也不属于旧表的孤儿行,
    而全量重拉本来只要两次 API 调用。返回 `(classify_rows, member_rows)` 实写行数。
    """
    init_schema(db_path)
    now = _now()
    ct = _classify_tuples(classify_rows, now)
    mt = _member_tuples(member_rows, now)
    if not ct or not mt:
        raise ValueError(
            f"⛔ 拒绝用空快照覆盖既有分类表(classify={len(ct)} / member={len(mt)}) —— "
            f"空覆盖会把「今天没拉到」变成「这些票查无行业」")
    with connection(db_path) as conn:
        conn.execute("DELETE FROM sw_industry_classify")
        conn.executemany(
            "INSERT INTO sw_industry_classify "
            "(index_code, name, level, parent_code, src, fetched_at) VALUES (?,?,?,?,?,?)", ct)
        conn.execute("DELETE FROM sw_industry_member")
        conn.executemany(
            "INSERT INTO sw_industry_member "
            "(ts_code, name, l1_code, l1_name, l2_code, l2_name, l3_code, l3_name, "
            " in_date, out_date, is_current, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", mt)
    return len(ct), len(mt)


# ══════════════════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════════════════

def load_l2_map(db_path: Optional[Path] = None) -> Dict[str, tuple]:
    """`ts_code → (l2_code, l2_name)`,只取当前有效行。查无表 / 空表 → 空 dict。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, l2_code, l2_name FROM sw_industry_member WHERE is_current=1"
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def level_counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """分类表逐层行数(§4.4 期望 L1 31 / L2 134 / L3 346)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT level, COUNT(*) FROM sw_industry_classify GROUP BY level").fetchall()
    return {r[0]: int(r[1]) for r in rows}


def member_count(db_path: Optional[Path] = None) -> int:
    init_schema(db_path)
    with connection(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM sw_industry_member WHERE is_current=1").fetchone()[0])


def coverage(codes: Sequence[str], db_path: Optional[Path] = None) -> tuple:
    """给定票池的申万归属覆盖率 → `(covered, total, missing_codes)`。

    §4.4 实测:本地 20260724 全市场 5526 只覆盖率 **100%**。覆盖率掉下来是**数据事故**
    (成分表没拉全 / 翻页丢了一半),不是"这些票没有行业" —— 调用方应据此报警。
    """
    m = load_l2_map(db_path)
    missing = [c for c in codes if c not in m]
    return len(codes) - len(missing), len(codes), missing


def verify(db_path: Optional[Path] = None) -> List[str]:
    """落库后的自检,返回**问题清单**(空 = 全通过)。

    ⚠ 白酒Ⅱ 的**名称**不符只告警不阻断(§5.4.3 校验 2 的同款处置:名称会变);
    **代码**不存在则是真问题 —— K9 第一层第 2 条排除项按代码走,代码没了就排不掉。
    """
    problems: List[str] = []
    lv = level_counts(db_path)
    for level in _LEVELS:
        if lv.get(level, 0) == 0:
            problems.append(f"分类表 {level} 层 0 行")
    if member_count(db_path) == 0:
        problems.append("成分表 0 行")

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sw_industry_classify WHERE index_code=?", (BAIJIU_L2_CODE,)
        ).fetchone()
    if row is None:
        problems.append(
            f"白酒Ⅱ 的二级代码 {BAIJIU_L2_CODE} 不在分类表里 —— "
            f"K9 第一层第 2 条排除项按代码识别,代码没了就排不掉")
    elif row[0] != BAIJIU_L2_NAME:
        logger.warning("[sw_industry] %s 的名称是「%s」而非「%s」—— 名称会变,"
                       "按代码识别的判据不受影响(只告警,不阻断)",
                       BAIJIU_L2_CODE, row[0], BAIJIU_L2_NAME)
    # ⚠ 「一只票只有一个归属」由 `sw_industry_member.ts_code` 的 PK **结构性**保证,
    # 重复行在 `_member_tuples` 就被点名拒绝了 —— ⛔ 不在这里再写一遍运行期检查
    # (同一条纪律两处实现,改一处就静默不一致)。
    return problems


# ══════════════════════════════════════════════════════════════════════════
# 编排(供 scripts/daily_update.py 调用)
# ══════════════════════════════════════════════════════════════════════════

def refresh(
    db_path: Optional[Path] = None,
    classify_fetcher: Callable[..., TushareResult] = ts_index_classify,
    member_fetcher: Callable[..., TushareResult] = ts_index_member_all,
) -> SwRefreshStats:
    """全量重拉 + 落库 + 自检。**绝不抛异常**(同 `TushareResult` 的既有姿势):
    失败一律 `ok=False` + 可读 `reason`,由调用方决定日志级别与退出码。"""
    cres = fetch_classify(classify_fetcher)
    if not cres.ok:
        return SwRefreshStats(ok=False, reason=cres.reason)
    classify_rows = cres.data

    mres = fetch_members(member_fetcher)
    if not mres.ok:
        return SwRefreshStats(ok=False, reason=mres.reason)
    member_rows, pages = mres.data

    try:
        n_cls, n_mem = save_snapshot(classify_rows, member_rows, db_path=db_path)
    except Exception as e:  # noqa: BLE001  落库失败同样转 reason,不掀翻调用方
        return SwRefreshStats(ok=False, reason=f"落库失败:{e}")

    problems = verify(db_path)
    stats = SwRefreshStats(
        ok=not problems,
        reason="ok" if not problems else ";".join(problems),
        classify_rows=n_cls,
        level_counts=level_counts(db_path),
        member_rows=n_mem,
        member_pages=pages,
    )
    return stats


__all__ = [
    "SW_SRC",
    "BAIJIU_L2_CODE",
    "BAIJIU_L2_NAME",
    "SwRefreshStats",
    "fetch_classify",
    "fetch_members",
    "save_snapshot",
    "load_l2_map",
    "level_counts",
    "member_count",
    "coverage",
    "verify",
    "refresh",
]
