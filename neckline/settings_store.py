"""应用设置存取(plan §五 阶段4 / 4A.5,V2-② 起 LLM 部分改为 Provider 注册表,
**🔴 高危区:LLM key 服务端存取**)。

单行 `app_settings` 表(id 恒为 1),存 App 设置屏可改的运行配置:
    · push_kinds —— **V2-⑪ 起的推送开关落点**:JSON `{"<kind>": 0|1}`,**按 kind 配**
      (三级 category 只决定「怎么响」,kind 决定「响不响」;按 category 配会连坐,
      D5 定案)。kind 取值域唯一源 `neckline/notify_kinds.py::ALL_KINDS`;缺键取
      默认开。读 `get_push_kinds` / 写 `set_push_kinds`。
    · push_report / push_retreat / push_precall / push_d5exit / push_circuit /
      push_holding_alert —— **V1 六类开关列,V2-⑪ 起停写留档**(不 DROP,同
      llm_provider 的列级停写纪律)。老库取值已由 `db.py::_seed_push_kinds` 一次性
      播种进 `push_kinds`;本模块此后**既不读也不写**这六列。
    · review_col_map —— 周复盘交割单列映射(4D 用,本块只建字段)。
    · intel_watch_boards —— 候选情报管线「五板块常驻」可配名单(v1.3-③-C3)。
    · llm_task_routes / llm_default_provider —— V2-② 任务→Provider 路由(见
      `get_llm_routes`/`set_llm_routes`),`neckline.llm.factory.get_provider()`
      解析用。
    · llm_provider / llm_api_key —— **V1 遗留列,V2 起停写**(不 DROP,同项目
      "删表一律停写留档"纪律的列级版本)。单 provider 时代的 `set_llm`/
      `resolve_llm` 已随 V2-② 退役,被下方 Provider 注册表(`llm_providers` 表,
      任意 OpenAI 兼容端点自填)取代。

**LLM Provider 注册表(V2-②,plan §3.10-B)**:`llm_providers` 表(建表见
`neckline/db.py::_SCHEMA`)。CRUD 见 `list_providers`/`get_provider_record`/
`create_provider`/`update_provider`/`delete_provider`;安全视图
`list_providers_public` **绝不回明文 key**,只回 `key_set: bool`。

**安全铁律(逐条守)**:
    · key 绝不回 HTTP 明文——HTTP 层只能问 `key_set`(bool);取明文只有服务端
      内部专用的 `list_providers`/`get_provider_record`(供 `get_provider()`)。
    · key 绝不进日志——本模块任何路径不 log key;调用方也不得 log 返回的 key。
    · **空 key 视为未设**(降级)——写入时 `strip()` 后为空串则存 NULL,与
      "根本没配置"同一处理,保证不会拿一个空 key 去乱调。
    · DB 文件 600、gitignored、rsync 永不同步覆盖(部署脚本 + plan 不变量保证,
      非本模块职责)。

写入用「先保证单行存在(INSERT OR IGNORE)→ 只 UPDATE 目标列」的两步,避免一次
`set_push`/`set_llm_routes` 把其它设置连带重置(各 setter 只碰自己的列)。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# `_default_settings` 自 V2-② 起不再被本模块任何函数消费(V1 的 `.env` 单 provider
# 兜底 `resolve_llm` 已退役,自填制下没有这个概念)——**但保留这个导入**:
# `tests/conftest.py::api_env` 固定会
# `monkeypatch.setattr(settings_store, "_default_settings", api_settings)`,
# 删掉这个名字会让该夹具(几乎所有 API 测试都依赖它)在 `setattr` 那一步就
# `AttributeError`。保留一个"被 monkeypatch 但没人读"的名字,好过牵连改一个被
# 几十个测试文件共享的 fixture。
from neckline import notify_kinds
from neckline.config import settings as _default_settings  # noqa: F401
from neckline.db import connection, init_schema

# v1.3-③-C3 候选情报管线「五板块常驻」默认名单(用户 2026-07-26 从真实数据挑定,
# plan §五 v1.3-③-C3-①)。**单一事实源**:DB 列 `app_settings.intel_watch_boards`
# 为 NULL(未配置)时回退到此;存的是**板块中文名**,运行时按 `ths_index.name`
# 精确匹配解析 ts_code(禁关键词模糊匹配——"芯片"会误命中汽车芯片/存储芯片,
# "机器人"会误命中人形机器人;实测见 intel_candidates 模块 docstring)。
DEFAULT_INTEL_WATCH_BOARDS = ("芯片概念", "创新药", "储能", "机器人概念", "稀土永磁")

# 局部更新(`update_provider`)用的"未传"哨兵——与"显式传 None/空串"区分。
_UNSET = object()


@dataclass
class AppSettings:
    """HTTP 层安全视图(V2-② 起不再含任何 LLM 字段——Provider 注册表 + 路由改走
    `list_providers_public`/`get_llm_routes`,`GET /settings` 组装时另外调用;
    V2-⑪ 起推送开关由六个 bool 字段换成 `push_kinds` 一张 kind→bool 映射)。"""

    push_kinds: Dict[str, bool]   # V2-⑪:全部 `ALL_KINDS` 已补齐(缺键取默认开)
    review_col_map: dict
    updated_at: Optional[str]


@dataclass
class ProviderRecord:
    """🔴 内部记录(含明文 `api_key`)——绝不可整体序列化进 HTTP 响应。HTTP 层一律
    经 `list_providers_public`/`_to_public` 转换成 `ProviderPublic`(用 `key_set`
    替代 `api_key`)之后再用。"""

    id: int
    name: str
    base_url: str
    model: str
    api_key: Optional[str]
    has_web_search: bool
    search_engine: Optional[str]
    notes: Optional[str]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass
class ProviderPublic:
    """安全视图(供 HTTP 层 / `GET /settings*`),`key_set` 替代 `api_key`。"""

    name: str
    base_url: str
    model: str
    has_web_search: bool
    search_engine: Optional[str]
    notes: Optional[str]
    enabled: bool
    key_set: bool


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


def _decode_push_kinds(raw: Optional[str]) -> Dict[str, bool]:
    """`app_settings.push_kinds` 的 JSON → 补齐全部 `ALL_KINDS` 的 kind→bool。

    **缺键 / NULL / 非法 JSON 一律取默认开**(`notify_kinds.DEFAULT_ENABLED`),承 V1
    「六类默认开可关」的口径:新增 kind 上线后不需要回填老库,用户没表达过意见的
    通道保持与 V1 相同的默认。**JSON 里出现的未登记 kind 直接丢弃**(白名单之外的
    串不该因为躺在 DB 里就获得存在感)。"""
    data: Dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, TypeError):
            data = {}
    return {
        k: (bool(data[k]) if k in data else notify_kinds.DEFAULT_ENABLED)
        for k in notify_kinds.ALL_KINDS
    }


def get_push_kinds(db_path: Optional[Path] = None) -> Dict[str, bool]:
    """读按 kind 的推送开关(全部 `ALL_KINDS` 已补齐,顺序与 `ALL_KINDS` 一致)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute("SELECT push_kinds FROM app_settings WHERE id=1").fetchone()
    return _decode_push_kinds(row[0] if row else None)


def set_push_kinds(kinds: Dict[str, bool], db_path: Optional[Path] = None) -> None:
    """全量覆盖式写按 kind 的推送开关(PUT 语义,同 `set_llm_routes` 体例)。

    **必须给全** `ALL_KINDS` 的每一个键 —— 承 V1 `set_push`「六字段均显式传入,防
    漏传静默重置某开关」的同一条纪律;缺键 / 出现未登记 kind 一律 `ValueError`
    (HTTP 层映射 422,**不静默忽略**:静默忽略会让用户以为自己关掉了某类通知,
    实际服务端根本没收到)。"""
    unknown = sorted(set(kinds) - set(notify_kinds.ALL_KINDS))
    if unknown:
        raise ValueError(f"未登记的通知 kind:{unknown};合法取值见 notify_kinds.ALL_KINDS")
    missing = [k for k in notify_kinds.ALL_KINDS if k not in kinds]
    if missing:
        raise ValueError(f"推送开关必须给全每一个 kind,缺:{missing}")
    payload = {k: (1 if kinds[k] else 0) for k in notify_kinds.ALL_KINDS}
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET push_kinds=?, updated_at=? WHERE id=1",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), _now()),
        )


def push_kind_enabled(kind: str, db_path: Optional[Path] = None) -> bool:
    """某个 kind 当前是否开着(`api/notify.py` 的闸门唯一读法)。未登记的 kind 抛
    `ValueError`(`notify_kinds.level_of` 的同一条纪律:白名单不开后门)。"""
    notify_kinds.level_of(kind)   # 未登记 → 抛,不给"未知 kind 静默放行/静默拦截"
    return get_push_kinds(db_path=db_path)[kind]


def get_app_settings(db_path: Optional[Path] = None) -> AppSettings:
    """读安全视图(供 `GET /settings`)。从未写过 → 默认值(全部 kind 默认开)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT push_kinds, review_col_map, updated_at FROM app_settings WHERE id=1"
        ).fetchone()
    if row is None:
        return AppSettings(
            push_kinds=_decode_push_kinds(None), review_col_map={}, updated_at=None,
        )
    try:
        col_map = json.loads(row[1]) if row[1] else {}
    except (json.JSONDecodeError, TypeError):
        col_map = {}
    return AppSettings(
        push_kinds=_decode_push_kinds(row[0]),
        review_col_map=col_map,
        updated_at=row[2],
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


# ══════════════════════════════════════════════════════════════════════════
# LLM Provider 注册表(V2-②,plan §3.10-B「Provider 自填制」)
# ══════════════════════════════════════════════════════════════════════════

_PROVIDER_COLUMNS = (
    "id, name, base_url, model, api_key, has_web_search, search_engine, notes, "
    "enabled, created_at, updated_at"
)


def _row_to_record(row: Tuple[Any, ...]) -> ProviderRecord:
    return ProviderRecord(
        id=row[0], name=row[1], base_url=row[2], model=row[3], api_key=_clean(row[4]),
        has_web_search=bool(row[5]), search_engine=_clean(row[6]), notes=_clean(row[7]),
        enabled=bool(row[8]), created_at=row[9], updated_at=row[10],
    )


def _to_public(rec: ProviderRecord) -> ProviderPublic:
    return ProviderPublic(
        name=rec.name, base_url=rec.base_url, model=rec.model, has_web_search=rec.has_web_search,
        search_engine=rec.search_engine, notes=rec.notes, enabled=rec.enabled,
        key_set=bool(rec.api_key),
    )


def list_providers(db_path: Optional[Path] = None) -> List[ProviderRecord]:
    """🔴 内部读(含明文 key)——供 `neckline.llm.factory.get_provider()` 用。
    HTTP 层必须改用 `list_providers_public`。按 `id` 升序(默认路由「挑第一个
    has_web_search 的 provider」需要一个稳定序,见 `neckline.llm.router`)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM llm_providers ORDER BY id ASC"
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_providers_public(db_path: Optional[Path] = None) -> List[ProviderPublic]:
    """安全视图(供 `GET /settings`/`GET /settings/providers`)。"""
    return [_to_public(r) for r in list_providers(db_path=db_path)]


def get_provider_record(name: str, db_path: Optional[Path] = None) -> Optional[ProviderRecord]:
    """🔴 内部读单行(含明文 key)。不存在 → `None`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM llm_providers WHERE name=?", (name,)
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def create_provider(
    name: str,
    base_url: str,
    model: str,
    *,
    api_key: Optional[str] = None,
    has_web_search: bool = False,
    search_engine: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
    db_path: Optional[Path] = None,
) -> ProviderRecord:
    """新建 Provider(自填制)。`name` 已存在 → `ValueError`(HTTP 层转 409;同名
    覆盖必须显式走 `update_provider`,防误覆盖)。空 `api_key`/`search_engine`/
    `notes` 一律存 NULL(同既有 `_clean()` 纪律,视为未设)。"""
    nm = (name or "").strip()
    if not nm:
        raise ValueError("provider name 不可为空")
    bu = (base_url or "").strip()
    if not bu:
        raise ValueError("base_url 不可为空")
    md = (model or "").strip()
    if not md:
        raise ValueError("model 不可为空")
    now = _now()
    init_schema(db_path)
    try:
        with connection(db_path) as conn:
            conn.execute(
                "INSERT INTO llm_providers "
                "(name, base_url, model, api_key, has_web_search, search_engine, notes, enabled, "
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nm, bu, md, _clean(api_key), 1 if has_web_search else 0, _clean(search_engine),
                 _clean(notes), 1 if enabled else 0, now, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"provider 已存在:{nm!r}") from e
    rec = get_provider_record(nm, db_path=db_path)
    assert rec is not None  # 刚插入,必然能读回
    return rec


def update_provider(
    name: str,
    *,
    base_url: Any = _UNSET,
    model: Any = _UNSET,
    api_key: Any = _UNSET,
    has_web_search: Any = _UNSET,
    search_engine: Any = _UNSET,
    notes: Any = _UNSET,
    enabled: Any = _UNSET,
    db_path: Optional[Path] = None,
) -> Optional[ProviderRecord]:
    """局部更新——未传的关键字参数一律留 `_UNSET` 哨兵(与"显式传 None/空串"区分,
    同 `_extract_max_chase_pct_or_400` 的 `model_fields_set` 判据同一条纪律,只是
    这里是 Python 函数层而非 pydantic 层)。`name` 不存在 → 返回 `None`(HTTP 层
    转 404)。`api_key`/`search_engine`/`notes` 显式传空串 → 存 NULL(视为清除)。"""
    init_schema(db_path)
    sets: List[str] = []
    vals: List[Any] = []
    if base_url is not _UNSET:
        sets.append("base_url=?")
        vals.append(str(base_url).strip())
    if model is not _UNSET:
        sets.append("model=?")
        vals.append(str(model).strip())
    if api_key is not _UNSET:
        sets.append("api_key=?")
        vals.append(_clean(api_key))
    if has_web_search is not _UNSET:
        sets.append("has_web_search=?")
        vals.append(1 if has_web_search else 0)
    if search_engine is not _UNSET:
        sets.append("search_engine=?")
        vals.append(_clean(search_engine))
    if notes is not _UNSET:
        sets.append("notes=?")
        vals.append(_clean(notes))
    if enabled is not _UNSET:
        sets.append("enabled=?")
        vals.append(1 if enabled else 0)

    if not sets:
        return get_provider_record(name, db_path=db_path)  # 无字段变更,只探是否存在

    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(name)
    with connection(db_path) as conn:
        cur = conn.execute(f"UPDATE llm_providers SET {', '.join(sets)} WHERE name=?", vals)
        if cur.rowcount == 0:
            return None
    return get_provider_record(name, db_path=db_path)


def delete_provider(name: str, db_path: Optional[Path] = None) -> bool:
    """删除一行。返回 `True`=删除成功,`False`=本来就不存在(HTTP 层转 404)。
    `llm_providers` 不在三律(冻结/追加/不回写)约束范围内(plan §五 V2-① DDL
    注释:「可改(用户自填/编辑)」)——与 `basket_cards`/`user_actions` 等表不同,
    这里允许普通 UPDATE/DELETE。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute("DELETE FROM llm_providers WHERE name=?", (name,))
        return cur.rowcount > 0


def get_llm_routes(db_path: Optional[Path] = None) -> Tuple[Dict[str, str], Optional[str]]:
    """读 (任务路由表, 默认 provider 名)。路由表 JSON 非法/非 dict → 空字典兜底
    (诚实降级,不崩)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT llm_task_routes, llm_default_provider FROM app_settings WHERE id=1"
        ).fetchone()
    if row is None:
        return {}, None
    try:
        routes = json.loads(row[0]) if row[0] else {}
    except (json.JSONDecodeError, TypeError):
        routes = {}
    if not isinstance(routes, dict):
        routes = {}
    return {str(k): str(v) for k, v in routes.items()}, _clean(row[1])


def set_llm_routes(
    routes: Dict[str, str], default_provider: Optional[str], db_path: Optional[Path] = None,
) -> None:
    """全量覆盖式写(同 `set_push` 六字段必填风格——调用方传完整状态,不做"只改
    一个任务的路由"式局部合并;局部合并会让"删掉某任务的路由"这种操作语义含糊)。
    任务名不在 `neckline.llm.router.ALL_TASKS` → `ValueError`,不静默吞掉拼写
    错误(路由这种"配错了不容易被发现"的东西,宁可 400 也不要悄悄 no-op)。

    lazy import `router`:本模块被 `neckline.llm.factory`/`neckline.db` 等较早
    加载的模块间接依赖,`neckline.llm.router` 本身零依赖、不会成环,这里 lazy
    只是保持"settings_store 不在模块顶层依赖 llm 子包"的既有姿势,不是必须。
    """
    from neckline.llm.router import ALL_TASKS

    bad = [t for t in routes if t not in ALL_TASKS]
    if bad:
        raise ValueError(f"未知任务名:{bad}(仅允许 {ALL_TASKS})")
    payload = json.dumps({str(k): str(v) for k, v in routes.items()}, ensure_ascii=False)
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET llm_task_routes=?, llm_default_provider=?, updated_at=? WHERE id=1",
            (payload, _clean(default_provider), _now()),
        )


__all__ = [
    "AppSettings",
    "ProviderRecord",
    "ProviderPublic",
    "DEFAULT_INTEL_WATCH_BOARDS",
    "get_app_settings",
    "get_push_kinds",
    "set_push_kinds",
    "push_kind_enabled",
    "set_review_col_map",
    "get_intel_watch_boards",
    "set_intel_watch_boards",
    "list_providers",
    "list_providers_public",
    "get_provider_record",
    "create_provider",
    "update_provider",
    "delete_provider",
    "get_llm_routes",
    "set_llm_routes",
]
