"""晚间链的只读数据就绪门禁。

这不是另一个事实包构建器：它只核验 17:10 首拉/后续有界重试落下的分区、申万归属和冻结包是否仍
完整可读。任何缺口都让 19:00 链写出 ``not_run``，绝不拿昨天的数据或现场重算顶替。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from neckline.db import readonly_tables
from neckline.calendar import official_is_trading_day
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
    pack_version: Optional[str] = None,
) -> Readiness:
    """验证当日 K9 的所有机械输入，整个函数严格只读、零 DDL。"""
    gaps: list[str] = []
    if pack_version == "fp-4" and official_is_trading_day(trade_date, db_path=db_path) is not True:
        gaps.append("trade_cal:目标交易日不是已落库官方开市日")
    gaps.extend(completeness.check(
        trade_date, parquet_dir=parquet_dir, db_path=db_path,
        require_current_sw=pack_version != "fp-4").missing())
    pack_id: Optional[str] = None
    try:
        kwargs = {} if pack_version is None else {"pack_version": pack_version}
        pack = store.load_pack(trade_date, parquet_dir=parquet_dir, db_path=db_path, **kwargs)
        rows = pack.rows
        if rows.height != pack.row_count:
            gaps.append(
                f"冻结事实包:行数不一致(清单 {pack.row_count}，parquet {rows.height})")
        else:
            pack_id = pack.pack_id
            if pack_version == "fp-4":
                from neckline.facts.v4 import hard_boundary_gaps, missing_columns
                missing = missing_columns(rows.columns)
                if missing:
                    gaps.append(f"fp-4字段缺失:{','.join(missing)}")
                else:
                    gaps.extend(hard_boundary_gaps(rows))
                fp4_market = pack.market.get("fp4") if isinstance(pack.market, dict) else None
                if not isinstance(fp4_market, dict) or fp4_market.get("dailyAmountUnit") != "kCNY":
                    gaps.append("fp-4 amount 单位合同缺失或不是 kCNY")
                if not isinstance(fp4_market, dict) or fp4_market.get("freeFloatMarketValueUnit") != "CNY":
                    gaps.append("fp-4 自由流通市值单位合同缺失或不是 CNY")
                membership = next((item for item in pack.sources
                                   if item.get("name") == "sw_industry_member_snapshots"), None)
                market_source = ((pack.market.get("fp4") or {}).get("swMembershipSource")
                                 if isinstance(pack.market, dict) else None)
                if not isinstance(membership, dict) or not isinstance(membership.get("metadata"), dict):
                    gaps.append("fp-4缺少冻结 SW2021 成员来源记录")
                elif market_source != membership["metadata"]:
                    gaps.append("fp-4 SW2021 成员来源与 market_json 不一致")
                else:
                    source = membership["metadata"]
                    with readonly_tables("sw_industry_snapshot_manifests", db_path=db_path) as conn:
                        live = None if conn is None else conn.execute(
                            "SELECT content_sha256,row_count,source_id,source_generated_at,source_fetched_at,raw_file_sha256,imported_at FROM sw_industry_snapshot_manifests WHERE trade_date=?",
                            (trade_date.strftime("%Y%m%d"),),
                        ).fetchone()
                    expected = None if live is None else {
                        "contentSha256": live[0], "rowCount": live[1], "sourceId": live[2],
                        "sourceGeneratedAt": live[3], "sourceFetchedAt": live[4],
                        "rawFileSha256": live[5], "importedAt": live[6],
                    }
                    if expected != source:
                        gaps.append("fp-4 SW2021 成员来源快照或内容哈希不可验证")
    except (store.PackNotFrozen, store.FactPackIntegrityError, FileNotFoundError, OSError, ValueError) as exc:
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
