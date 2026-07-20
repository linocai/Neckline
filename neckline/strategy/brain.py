"""策略大脑(版本化规则库,plan 1.9 / §2.6「策略进化带笼子」)。

把「过堂后写死」的规则参数快照 + 变更日志 + 定版回测指标落 SQLite `strategy_versions`。
调参门禁(§2.6)后续常态运行时,新版须过 walk-forward 样本外跑赢现役版本 + 用户批准
才可 `activate`。本模块只管**读写与激活**,不做门禁判定(那是后续机制)。

rule 是纯参数字典(可直接喂 `MomentumConfig(**rule["config"])`),不含代码——同码三跑道
的执行逻辑在 `momentum.py`,大脑只存「用哪套参数」。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from neckline.db import connection, init_schema


@dataclass
class StrategyVersion:
    version: str
    created_at: str
    rule: Dict
    changelog: str
    metrics: Dict
    is_active: bool


def _row_to_version(row: sqlite3.Row) -> StrategyVersion:
    return StrategyVersion(
        version=row[0],
        created_at=row[1],
        rule=json.loads(row[2]),
        changelog=row[3],
        metrics=json.loads(row[4]) if row[4] else {},
        is_active=bool(row[5]),
    )


def save_version(
    version: str,
    rule: Dict,
    changelog: str,
    metrics: Optional[Dict] = None,
    activate: bool = True,
    db_path: Optional[Path] = None,
) -> StrategyVersion:
    """写入(或覆盖同名)一个策略版本。`activate=True` 时把它设为唯一现役版本
    (其余版本 is_active 置 0)。幂等:同 version 再写覆盖参数与日志。"""
    init_schema(db_path)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategy_versions "
            "(version, created_at, rule_json, changelog, metrics_json, is_active) "
            "VALUES (?,?,?,?,?,?)",
            (version, created, json.dumps(rule, ensure_ascii=False),
             changelog, json.dumps(metrics or {}, ensure_ascii=False), 1 if activate else 0),
        )
        if activate:
            conn.execute("UPDATE strategy_versions SET is_active=0 WHERE version<>?", (version,))
    return get_version(version, db_path=db_path)  # type: ignore[return-value]


def get_version(version: str, db_path: Optional[Path] = None) -> Optional[StrategyVersion]:
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT version, created_at, rule_json, changelog, metrics_json, is_active "
            "FROM strategy_versions WHERE version=?", (version,)
        ).fetchone()
    return _row_to_version(row) if row else None


def get_active(db_path: Optional[Path] = None) -> Optional[StrategyVersion]:
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT version, created_at, rule_json, changelog, metrics_json, is_active "
            "FROM strategy_versions WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return _row_to_version(row) if row else None


def list_versions(db_path: Optional[Path] = None) -> List[StrategyVersion]:
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT version, created_at, rule_json, changelog, metrics_json, is_active "
            "FROM strategy_versions ORDER BY created_at"
        ).fetchall()
    return [_row_to_version(r) for r in rows]


__all__ = ["StrategyVersion", "save_version", "get_version", "get_active", "list_versions"]
