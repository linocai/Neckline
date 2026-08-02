"""策略包读写(plan §五 V2-③)。manifest/config schema 校验 + 包文件装载 +
`selection_packs` 读写。**append-only + 单现役**(同 `strategy_versions` 既有
分工:新包版本追加行,`is_active`/`activated_at` 在唯一现役行上切换,激活事件
另落 `selection_pack_activation_log`,见 `neckline/db.py` 建表注释)。

**唯一写入口 = `activate_pack()`**(单事务:落新包行〔或识别到内容相同的既有行,
幂等〕+ 激活切换 + 追加事件),供 `scripts/activate_pack.py` 闸 4 调用 ——
本模块不提供"只登记不激活"的旁路(plan 没有要求这个功能,包的登记与激活在这个
产品里刻意是同一个动作,不像章程切换那样分成"先建行再切换"两步)。

**读现役包唯一入口 = `get_active_pack()`**(照 `neckline.strategy.brain.
get_active()` 体例)。**不做时间线解析**(不像 `brain.py` 有
`config_active_at`/`config_governing_at` 那一整套"某历史时刻该按哪版判"的机关)
——包只需要回答"现在现役的是哪个",篮子/卡在生成当下把 `pack_version` 抄一份
到自己行里做归因快照(`baskets.pack_version`),不需要日后按历史时刻反查。

**本模块全程不 import `neckline.strategy.brain`,不碰 `strategy_versions`**
(纪律章程与策略包两条版本线、两张表、两套激活流程,永不混用,见 plan §五
V2-③「插槽边界」)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline.config import settings
from neckline.db import connection, init_schema
from neckline.selection import engine_api
from neckline.selection.primitives import PRIMITIVES, validate_params

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PACK_COLUMNS = (
    "pack_version, name, engine_api_version, manifest_json, config_json, "
    "evidence_ref, is_active, created_at, activated_at"
)

_EVIDENCE_REF_SEP = "; "   # `selection_packs.evidence_ref` 落库时的连接符(展示/grep 友好)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cache_key(db_path: Optional[Path]) -> str:
    return str(db_path) if db_path is not None else str(settings.db_path)


def _join_evidence_ref(refs: List[str]) -> Optional[str]:
    return _EVIDENCE_REF_SEP.join(refs) if refs else None


def _split_evidence_ref(text: Optional[str]) -> List[str]:
    if not text:
        return []
    return [p for p in text.split(_EVIDENCE_REF_SEP) if p]


# ══════════════════════════════════════════════════════════════════════════
# manifest / config schema 校验(轻量版"JSON Schema";理由见 `primitives.py`
# 模块头「参数 schema 校验」节:§3.1 钉死依赖清单没有 `jsonschema`,不为此新增
# 第三方库)。
# ══════════════════════════════════════════════════════════════════════════

def validate_manifest(manifest: Any) -> List[str]:
    """manifest 必需字段(plan §五 V2-③「包格式定死」):`pack_version` / `name` /
    `date`(`YYYY-MM-DD`)/ `engine_api_version`(int)/ `evidence_ref`(字符串数组,
    允许空列表——校验只管形状,"证据链是否该非空"是产品判断不是格式判断)。"""
    if not isinstance(manifest, dict):
        return ["manifest 必须是 JSON 对象"]
    errors: List[str] = []
    for key in ("pack_version", "name", "date"):
        v = manifest.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"manifest.{key} 必须是非空字符串")
    date_v = manifest.get("date")
    if isinstance(date_v, str) and date_v.strip() and not _DATE_RE.match(date_v):
        errors.append("manifest.date 必须是 YYYY-MM-DD 格式")
    eav = manifest.get("engine_api_version")
    if not isinstance(eav, int) or isinstance(eav, bool):
        errors.append("manifest.engine_api_version 必须是整数")
    evidence = manifest.get("evidence_ref")
    if not isinstance(evidence, list) or not all(isinstance(x, str) and x.strip() for x in evidence):
        errors.append("manifest.evidence_ref 必须是非空字符串组成的数组(可以是空数组)")
    return errors


def validate_config(config: Any) -> List[str]:
    """config 必需两段(plan §五 V2-③「插槽边界」):`seeds`(原语名 → 参数,
    键必须是已注册原语,值按该原语 `params_schema` 校验)与 `tier`(`weights` 非空
    对象 + `dims` 非空数组,`dims` 引用的维度必须都在 `weights` 里出现)。"""
    if not isinstance(config, dict):
        return ["config 必须是 JSON 对象"]
    errors: List[str] = []

    seeds = config.get("seeds")
    if not isinstance(seeds, dict):
        errors.append("config.seeds 必须是对象(原语名 → 参数)")
    else:
        for prim_name, params in seeds.items():
            primitive = PRIMITIVES.get(prim_name)
            if primitive is None:
                errors.append(
                    f"config.seeds 引用了未注册的原语:{prim_name!r}"
                    f"(已注册:{sorted(PRIMITIVES)})"
                )
                continue
            if not isinstance(params, dict):
                errors.append(f"config.seeds.{prim_name} 的参数必须是对象")
                continue
            errors.extend(validate_params(primitive, params))

    tier = config.get("tier")
    if not isinstance(tier, dict):
        errors.append("config.tier 必须是对象")
    else:
        weights = tier.get("weights")
        dims = tier.get("dims")
        if not isinstance(weights, dict) or not weights:
            errors.append("config.tier.weights 必须是非空对象")
        else:
            bad = [k for k, v in weights.items() if not isinstance(v, (int, float)) or isinstance(v, bool)]
            if bad:
                errors.append(f"config.tier.weights 存在非数值权重:{bad}")
        if not isinstance(dims, list) or not dims:
            errors.append("config.tier.dims 必须是非空数组")
        elif isinstance(weights, dict):
            missing = [d for d in dims if d not in weights]
            if missing:
                errors.append(f"config.tier.dims 引用了 weights 里没有的维度:{missing}")
    return errors


def validate_pack_doc(doc: Any) -> List[str]:
    """闸 1(schema)+ 闸 2 一部分(engine_api_version 兼容)的组合入口。返回空
    列表 = 通过。**结构错误时不再往下核对兼容性**(避免在 `manifest` 都不是字典
    时去 `.get()` 报一堆无意义的连锁错误)。"""
    if not isinstance(doc, dict):
        return ["包文件顶层必须是 JSON 对象(含 manifest / config 两个键)"]
    manifest = doc.get("manifest")
    config = doc.get("config")
    errors = validate_manifest(manifest) + validate_config(config)
    if not errors and not engine_api.is_compatible(manifest):
        errors.append(
            f"engine_api_version 不兼容:包声明 {manifest.get('engine_api_version')},"
            f"引擎现为 {engine_api.ENGINE_API_VERSION}(拒绝激活,fail loud)"
        )
    return errors


def load_pack_file(path: Path) -> Dict[str, Any]:
    """读一个包 JSON 文件,只做「读得进来 + 顶层形状对」的最低限度检查
    (`manifest`/`config` 两个顶层键必须存在);字段级 schema 校验交
    `validate_pack_doc`。文件不存在 / 不是合法 JSON → 原样抛
    `OSError`/`json.JSONDecodeError`,调用方(`scripts/activate_pack.py`)负责
    转成清晰的错误提示 + 非零退出码。"""
    text = Path(path).read_text(encoding="utf-8")
    doc = json.loads(text)
    if not isinstance(doc, dict) or "manifest" not in doc or "config" not in doc:
        raise ValueError(f"{path}: 包文件必须是含 manifest/config 两个顶层键的 JSON 对象")
    return doc


# ══════════════════════════════════════════════════════════════════════════
# Pack 只读视图 + DB 读写
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Pack:
    """`selection_packs` 一行的只读视图(与 `strategy.brain.StrategyVersion` 同一
    个设计意图:不可变快照,消费方拿到手就是当时那一行,不会背着调用方悄悄变)。"""

    pack_version: str
    name: str
    engine_api_version: int
    manifest: Dict[str, Any]
    config: Dict[str, Any]
    evidence_ref: List[str]
    is_active: bool
    created_at: str
    activated_at: Optional[str]

    def seeds_config(self, primitive_name: str) -> Dict[str, Any]:
        """`config.seeds.<primitive_name>` 那一段参数(缺省 = 空字典,由调用方
        经 `Primitive.merge_params`/`Primitive.run` 补上该原语自己的 schema
        默认值 —— 本方法只管"包里写了什么",不越权决定默认值)。"""
        return dict(self.config.get("seeds", {}).get(primitive_name, {}))

    def tier_weights(self) -> Dict[str, float]:
        return dict(self.config.get("tier", {}).get("weights", {}))

    def tier_dims(self) -> List[str]:
        return list(self.config.get("tier", {}).get("dims", []))


def _row_to_pack(row: Tuple[Any, ...]) -> Pack:
    return Pack(
        pack_version=row[0],
        name=row[1],
        engine_api_version=row[2],
        manifest=json.loads(row[3]),
        config=json.loads(row[4]),
        evidence_ref=_split_evidence_ref(row[5]),
        is_active=bool(row[6]),
        created_at=row[7],
        activated_at=row[8],
    )


def list_packs(db_path: Optional[Path] = None) -> List[Pack]:
    """全部包版本,按 `created_at` 升序(append-only 历史,同 `brain.list_versions`
    体例)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_PACK_COLUMNS} FROM selection_packs ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_pack(r) for r in rows]


def get_pack(pack_version: str, db_path: Optional[Path] = None) -> Optional[Pack]:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_PACK_COLUMNS} FROM selection_packs WHERE pack_version=?", (pack_version,)
        ).fetchone()
    return _row_to_pack(row) if row is not None else None


# 现役包缓存:**按 `(db_path, pack_version)` 失效**,不是只按 `pack_version`——
# 单纯按版本号做全局缓存,在多个 DB 文件里恰好用了同一个 pack_version 字符串时
# (测试隔离下这完全可能:不同测试各自的 tmp db 都装同一份 `K4-pack.json`)会
# 把 A 库的 Pack 对象错误地喂给 B 库的调用方。故缓存键必须先按解析后的 db 路径
# 分桶,同一桶内才谈"pack_version 没变就不用重新反序列化 JSON"这层优化
# (`init_schema`/一次 SELECT 仍然每次都做,只省了 `json.loads` 两遍 + 造对象)。
_ACTIVE_PACK_CACHE: Dict[str, Tuple[str, Pack]] = {}


def get_active_pack(db_path: Optional[Path] = None) -> Optional[Pack]:
    """读现役策略包(照 `neckline.strategy.brain.get_active()` 体例)。无现役包
    (全新库、或曾激活后又被后续激活切走且没有"当前唯一现役"的中间态——正常流程
    不会出现,`activate_pack()` 保证任一时刻至多一行 `is_active=1`)→ `None`,
    调用方各自决定降级(④/⑥ 未来的消费方:无现役包 = 当日不产出驱动种子/Tier,
    如实披露,不许现造一份默认包)。"""
    init_schema(db_path)
    key = _cache_key(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_PACK_COLUMNS} FROM selection_packs "
            "WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        _ACTIVE_PACK_CACHE.pop(key, None)
        return None
    pack_version = row[0]
    cached = _ACTIVE_PACK_CACHE.get(key)
    if cached is not None and cached[0] == pack_version:
        return cached[1]
    pack = _row_to_pack(row)
    _ACTIVE_PACK_CACHE[key] = (pack_version, pack)
    return pack


def activate_pack(
    manifest: Dict[str, Any],
    config: Dict[str, Any],
    *,
    via: str = "cli",
    db_path: Optional[Path] = None,
) -> Pack:
    """**唯一写入口**,单事务(plan §五 V2-③ 闸 4 原文:"单事务:旧行
    is_active=0、新行 is_active=1、activation_log 追加两条事件")。

    行为:
      1. `pack_version` 在库里不存在 → 追加新行(`is_active=0` 起步)。
      2. `pack_version` 已存在 → 逐字节比对 `manifest`/`config`:相同则视为
         幂等重放(不重复插入,不报错);不同则 `ValueError`(append-only:
         改内容必须换一个新的 `pack_version`,不可静默覆盖已登记的包)。
      3. 若目标已是当前唯一现役包 → 不追加任何事件(与
         `scripts/activate_pack.py` 的 CLI 层"已现役、无需激活"提前拦截一致;
         直接调用本函数〔绕过 CLI 提前检查〕重复以同版本激活同样保持幂等,不
         产生冗余事件)。
      4. 否则:若存在其它现役包 → 先给它追加一条 `deactivate` 事件 + 置
         `is_active=0`;再给目标追加一条 `activate` 事件 + 置 `is_active=1`
         `activated_at=now()`。**首次激活(此前无任何现役包)只有后半段**——
         没有"旧行"可关,不伪造一条 deactivate 事件。

    `via`:`"cli"`(`scripts/activate_pack.py --confirm`)或 `"seed"`(测试/未来
    预填充脚本,同 `strategy_activation_log.via` 既有取值风格)。

    **不做 schema 校验之外的业务校验**(如"必须比现役更好")——策略包没有章程
    切换器那种"核心值核对"概念(章程的核心值是固定拍板的几个数,包的参数本就
    是每次都可能不同的调参对象),`scripts/activate_pack.py` 的闸 1-3 已经把
    「schema 合法 + 原语白名单 + engine_api 兼容 + 人读 diff」都过了一遍,本函数
    只管落库这一步的原子性与幂等性。"""
    errors = validate_pack_doc({"manifest": manifest, "config": config})
    if errors:
        raise ValueError("包 schema 校验未通过,拒绝激活:" + "; ".join(errors))

    pack_version = manifest["pack_version"]
    init_schema(db_path)
    manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
    evidence_ref_text = _join_evidence_ref(list(manifest["evidence_ref"]))
    now = _now()

    with connection(db_path) as conn:
        existing = conn.execute(
            "SELECT manifest_json, config_json FROM selection_packs WHERE pack_version=?",
            (pack_version,),
        ).fetchone()
        if existing is not None:
            if existing[0] != manifest_json or existing[1] != config_json:
                raise ValueError(
                    f"pack_version={pack_version!r} 已存在但内容不同"
                    "(append-only,不可覆盖已登记的包;如需改动请换一个新的 pack_version)。"
                )
        else:
            conn.execute(
                "INSERT INTO selection_packs "
                "(pack_version, name, engine_api_version, manifest_json, config_json, "
                " evidence_ref, is_active, created_at, activated_at) "
                "VALUES (?,?,?,?,?,?,0,?,NULL)",
                (
                    pack_version, manifest["name"], manifest["engine_api_version"],
                    manifest_json, config_json, evidence_ref_text, now,
                ),
            )

        prior_row = conn.execute(
            "SELECT pack_version FROM selection_packs WHERE is_active=1"
        ).fetchone()
        prior_version = prior_row[0] if prior_row is not None else None

        if prior_version != pack_version:
            if prior_version is not None:
                conn.execute(
                    "UPDATE selection_packs SET is_active=0 WHERE pack_version=?", (prior_version,)
                )
                conn.execute(
                    "INSERT INTO selection_pack_activation_log (pack_version, action, via, note, at) "
                    "VALUES (?,?,?,?,?)",
                    (prior_version, "deactivate", via, f"由 {pack_version} 取代", now),
                )
            conn.execute(
                "UPDATE selection_packs SET is_active=1, activated_at=? WHERE pack_version=?",
                (now, pack_version),
            )
            conn.execute(
                "INSERT INTO selection_pack_activation_log (pack_version, action, via, note, at) "
                "VALUES (?,?,?,?,?)",
                (pack_version, "activate", via, "", now),
            )
        # else: 目标已是现役 —— 幂等 no-op,不追加事件。

    _ACTIVE_PACK_CACHE.pop(_cache_key(db_path), None)
    activated = get_pack(pack_version, db_path=db_path)
    assert activated is not None and activated.is_active
    return activated


__all__ = [
    "Pack",
    "validate_manifest",
    "validate_config",
    "validate_pack_doc",
    "load_pack_file",
    "list_packs",
    "get_pack",
    "get_active_pack",
    "activate_pack",
]
