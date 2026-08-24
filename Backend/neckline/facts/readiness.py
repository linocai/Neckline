"""晚间链的只读数据就绪门禁。

这不是另一个事实包构建器：它只核验 16:05 已落下的分区、申万归属和冻结包是否仍
完整可读。任何缺口都让 19:00 链写出 ``not_run``，绝不拿昨天的数据或现场重算顶替。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from neckline.db import readonly_tables
from neckline.facts import completeness
from neckline.facts import store


@dataclass(frozen=True)
class Readiness:
    trade_date: date
    pack_id: Optional[str]
    gaps: Tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.gaps and self.pack_id is not None


def preflight(
    trade_date: date,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Readiness:
    """验证当日 K9 的所有机械输入，整个函数严格只读、零 DDL。"""
    gaps = list(completeness.check(
        trade_date, parquet_dir=parquet_dir, db_path=db_path).missing())
    pack_id: Optional[str] = None
    try:
        pack = store.load_pack(trade_date, parquet_dir=parquet_dir, db_path=db_path)
        rows = pack.rows
        if rows.height != pack.row_count:
            gaps.append(
                f"冻结事实包:行数不一致(清单 {pack.row_count}，parquet {rows.height})")
        else:
            pack_id = pack.pack_id
    except (store.PackNotFrozen, FileNotFoundError, OSError, ValueError) as exc:
        gaps.append(f"冻结事实包不可用:{exc}")

    # 冻结时应同时写入 L2 每日派生事实；只检查存在性，不自行补算。
    with readonly_tables("sw_industry_daily", db_path=db_path) as conn:
        if conn is None:
            gaps.append("sw_industry_daily:数据库未迁移")
        elif conn.execute(
            "SELECT 1 FROM sw_industry_daily WHERE trade_date=? LIMIT 1",
            (trade_date.strftime("%Y%m%d"),),
        ).fetchone() is None:
            gaps.append("sw_industry_daily:当日行业派生数据缺失")

    return Readiness(trade_date=trade_date, pack_id=pack_id, gaps=tuple(gaps))


__all__ = ["Readiness", "preflight"]
