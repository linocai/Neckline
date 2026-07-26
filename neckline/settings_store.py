"""应用设置存取(plan §五 阶段4 / 4A.5,**🔴 高危区:LLM key 服务端存取**)。

单行 `app_settings` 表(id 恒为 1),存 App 设置屏可改的运行配置:
    · llm_provider / llm_api_key —— 用户在 App 填的 LLM 供应商与 key。
      `get_provider()`(`neckline.llm.factory`)解析优先级 = **DB 覆盖 → `.env` 兜底**
      (§3.4),每次调用现读、运行时生效不重启。
    · push_report / push_retreat / push_precall / push_d5exit —— APNs 四类推送开关
      (§2.4 v1.1 拍板,默认开可关)。`get_app_settings` 读全四类供 notify 查开关;
      `set_push` 四字段写入(v1.1-G.1 设置屏接线)。
    · review_col_map —— 周复盘交割单列映射(4D 用,本块只建字段)。

**安全铁律(逐条守)**:
    · key 绝不回 HTTP 明文——HTTP 层只能问 `llm_key_is_set()`(bool),取明文只有
      服务端内部的 `resolve_llm()`(供 `get_provider()`)。
    · key 绝不进日志——本模块任何路径不 log key;调用方也不得 log 返回的 key。
    · **空 key 视为未设**(降级)——写入时 `strip()` 后为空串则存 NULL,`resolve_llm`
      也把空串当未设,保证「填了空 key」不会让 `get_provider` 拿到一个空 key 去乱调。
    · DB 文件 600、gitignored、rsync 永不同步覆盖(部署脚本 + plan 不变量保证,非本模块职责)。

写入用「先保证单行存在(INSERT OR IGNORE)→ 只 UPDATE 目标列」的两步,避免一次
`set_llm` 把 push 开关连带重置(各 setter 只碰自己的列)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from neckline.config import Settings
from neckline.config import settings as _default_settings
from neckline.db import connection, init_schema

# 允许的 LLM 供应商(与 `neckline.llm.factory._PROVIDERS` 单一口径,恶意/未知值一律拒收)。
ALLOWED_PROVIDERS = ("glm", "kimi")

# v1.3-③-C3 候选情报管线「五板块常驻」默认名单(用户 2026-07-26 从真实数据挑定,
# plan §五 v1.3-③-C3-①)。**单一事实源**:DB 列 `app_settings.intel_watch_boards`
# 为 NULL(未配置)时回退到此;存的是**板块中文名**,运行时按 `ths_index.name`
# 精确匹配解析 ts_code(禁关键词模糊匹配——"芯片"会误命中汽车芯片/存储芯片,
# "机器人"会误命中人形机器人;实测见 intel_candidates 模块 docstring)。
DEFAULT_INTEL_WATCH_BOARDS = ("芯片概念", "创新药", "储能", "机器人概念", "稀土永磁")


@dataclass
class AppSettings:
    """HTTP 层安全视图:**不含** llm_api_key 明文,只有 `llm_key_set` 布尔。"""

    llm_provider: Optional[str]
    llm_key_set: bool
    push_report: bool
    push_retreat: bool
    push_precall: bool          # v1.1-A:盘前校准 9:26 汇总推送开关(默认开)
    push_d5exit: bool           # v1.1-B:D5 时间退出推送开关(默认开)
    push_circuit: bool          # v1.2-A2:熔断提醒推送开关(第五类,默认开)
    push_holding_alert: bool    # v1.3-②:K4 持仓派发警报推送开关(第六类,默认开)
    review_col_map: dict
    updated_at: Optional[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


def _ensure_row(conn) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (id, push_report, push_retreat, review_col_map) "
        "VALUES (1, 1, 1, '{}')"
    )


def get_app_settings(db_path: Optional[Path] = None) -> AppSettings:
    """读安全视图(供 `GET /settings`)。从未写过 → 默认值(provider=None,
    key 未设,两开关默认开)。**绝不返回 key 明文。**"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT llm_provider, llm_api_key, push_report, push_retreat, "
            "push_precall, push_d5exit, push_circuit, push_holding_alert, review_col_map, updated_at "
            "FROM app_settings WHERE id=1"
        ).fetchone()
    if row is None:
        return AppSettings(
            llm_provider=None, llm_key_set=False, push_report=True,
            push_retreat=True, push_precall=True, push_d5exit=True, push_circuit=True,
            push_holding_alert=True, review_col_map={}, updated_at=None,
        )
    try:
        col_map = json.loads(row[8]) if row[8] else {}
    except (json.JSONDecodeError, TypeError):
        col_map = {}
    return AppSettings(
        llm_provider=_clean(row[0]),
        llm_key_set=bool(_clean(row[1])),          # 空/NULL → 未设
        push_report=bool(row[2]),
        push_retreat=bool(row[3]),
        push_precall=bool(row[4]),
        push_d5exit=bool(row[5]),
        push_circuit=bool(row[6]),
        push_holding_alert=bool(row[7]),
        review_col_map=col_map,
        updated_at=row[9],
    )


def set_llm(provider: str, api_key: str, db_path: Optional[Path] = None) -> None:
    """写 LLM 供应商 + key(🔴)。`provider` 须在 `ALLOWED_PROVIDERS`(调用方也应先校验,
    此处再兜一层,非法值 → ValueError,绝不落库);空 key `strip()` 后 → 存 NULL(视为清除,
    走降级)。**不 log 任何 key。**"""
    p = (provider or "").strip().lower()
    if p not in ALLOWED_PROVIDERS:
        raise ValueError(f"未知 LLM 供应商:{provider!r}(仅允许 {ALLOWED_PROVIDERS})")
    key = _clean(api_key)  # 空串 → None(存 NULL,视为清除)
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET llm_provider=?, llm_api_key=?, updated_at=? WHERE id=1",
            (p, key, _now()),
        )


def set_push(
    report: bool, retreat: bool, precall: bool, d5exit: bool, circuit: bool, holding_alert: bool,
    db_path: Optional[Path] = None,
) -> None:
    """写 APNs 六类推送开关(§2.4 白名单;v1.2-A2 扩第五字段熔断,v1.3-② 扩第六字段
    K4 持仓派发警报 `push_holding_alert`)。六字段均显式传入(无默认值,防「漏传静默
    重置某开关」)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET push_report=?, push_retreat=?, push_precall=?, push_d5exit=?, "
            "push_circuit=?, push_holding_alert=?, updated_at=? WHERE id=1",
            (1 if report else 0, 1 if retreat else 0, 1 if precall else 0, 1 if d5exit else 0,
             1 if circuit else 0, 1 if holding_alert else 0, _now()),
        )


def set_review_col_map(col_map: dict, db_path: Optional[Path] = None) -> None:
    """写周复盘交割单列映射(4D 用;本块仅提供存取,不消费)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET review_col_map=?, updated_at=? WHERE id=1",
            (json.dumps(col_map, ensure_ascii=False), _now()),
        )


def get_intel_watch_boards(db_path: Optional[Path] = None) -> list:
    """读候选情报管线「五板块常驻」名单(板块中文名列表,plan §五 v1.3-③-C3-①)。
    DB 列 `intel_watch_boards` 为 NULL(从未配置)→ 返回 `DEFAULT_INTEL_WATCH_BOARDS`
    的**默认五板块**;为 `'[]'`(用户显式清空)→ 返回空列表(无常驻,尊重显式配置,
    不再回退默认);非法 JSON → 回退默认(诚实兜底)。**只读**,写路径留 ⑥/设置屏。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute("SELECT intel_watch_boards FROM app_settings WHERE id=1").fetchone()
    if row is None or row[0] is None:
        return list(DEFAULT_INTEL_WATCH_BOARDS)
    try:
        parsed = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return list(DEFAULT_INTEL_WATCH_BOARDS)
    if not isinstance(parsed, list):
        return list(DEFAULT_INTEL_WATCH_BOARDS)
    return [str(x) for x in parsed]


def set_intel_watch_boards(names: list, db_path: Optional[Path] = None) -> None:
    """写「五板块常驻」名单(板块中文名列表)。空列表 → 存 `'[]'`(显式清空,
    与 NULL「未配置」区分:后者回退默认、前者尊重空)。供 ⑥/设置屏/QA 用。"""
    init_schema(db_path)
    payload = json.dumps([str(x) for x in (names or [])], ensure_ascii=False)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET intel_watch_boards=?, updated_at=? WHERE id=1",
            (payload, _now()),
        )


def resolve_llm(
    default_settings: Optional[Settings] = None, db_path: Optional[Path] = None
) -> Tuple[Optional[str], Optional[str]]:
    """解析生效的 (provider, api_key) —— **DB 覆盖 → `.env` 兜底**(§3.4)。**服务端内部
    专用**(供 `get_provider()`),返回明文 key;调用方绝不可把结果 log 出去或回给 HTTP。

    DB 里 provider 与 key 都非空 → 用 DB;否则回退 `.env`(`settings.llm_provider/
    llm_api_key`)。任一为空即视为「该源未激活」,不做半激活(避免拿到空 key 去调)。
    """
    s = default_settings or _default_settings
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT llm_provider, llm_api_key FROM app_settings WHERE id=1"
        ).fetchone()
    db_provider = _clean(row[0]) if row else None
    db_key = _clean(row[1]) if row else None
    if db_provider and db_key:
        return db_provider, db_key
    return _clean(s.llm_provider), _clean(s.llm_api_key)


__all__ = [
    "AppSettings",
    "ALLOWED_PROVIDERS",
    "DEFAULT_INTEL_WATCH_BOARDS",
    "get_app_settings",
    "set_llm",
    "set_push",
    "set_review_col_map",
    "get_intel_watch_boards",
    "set_intel_watch_boards",
    "resolve_llm",
]
