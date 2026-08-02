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

**V2-③-K7 新增(K7 需求 4 末条,plan §五 ③-K7-C/D):`config.tier.stage_scores`**
——与 `weights`/`dims` 平级的新增**可选**键(行业题材五态打分映射,K7 需求 1b
「打分映射必须做成 pack 可配参数」的落点),键必须是 `neckline.scan.stage.
STAGE_ORDER` 六个英文枚举码之一(唯一源,本模块特意 import 它而不是抄一份
第二份六码元组——`CandidateOut.board` 同款纪律:库列值与配置键必须同源,
中文键已被 ③-K7-D 明令淘汰)。`neckline/scan/` 依赖 `neckline/selection/`
(`seeds.py` 读 `pack.get_active_pack()`)是既有的正向依赖;本模块反过来读
`neckline/scan/stage.py` 的**纯常量**(不读任何 I/O 函数)不构成循环 import
(`neckline/scan/__init__.py` 不预先加载任何子模块,`stage.py` 自身也不
import `neckline.selection`,已核实)。

**`stage_scores` 的 `engine_api_version` 判定(定死,不许含糊)**:这是一个
"新增可选键、旧包不受影响"的纯增量扩展——`validate_config` 对没有这个键的
包(如 K4-pack-v1)完全不进入 `_validate_stage_scores` 分支,`is_compatible()`
判据也毫发未动。按 plan §五 ③-K7-C 的判定规则("旧包原样重新校验仍通过、
`get_active_pack()` 对旧包行为逐位不变 → `ENGINE_API_VERSION` 保持不变"),
本次扩展**不 bump** `engine_api.ENGINE_API_VERSION`(仍为 1)。

**V2-⑥-b 新增(2026-08-02 planner 裁定):`config.tier.quality_lines`**——与
`weights`/`dims`/`stage_scores` 平级的新增**可选**键(档位质量线:每档一道
机械分下限,`{tier1_min, tier2_min, tier3_min}` 三个子键也**各自独立可选**,
同 `stage_scores` "不要求六态全部出现"同一纪律)。归属判给"包"而不是"引擎
常量"的决定性理由是**标度耦合**:质量线与五维权重作用在**同一个标度**上,
权重已经在包里,线留在代码里 = 换一次权重就静默改变 T1 的选择性。
**缺键回退 vs `weights` 缺维度 fail loud,两种姿势刻意不同**:`weights` 每个
包 schema 都必须给全,缺了就是包坏了;`quality_lines` 缺(整段缺或单键缺)
一律回退引擎默认——因为 `K4-pack-v1` 不重发版、是 ⑯-E 的回滚锚,不给回退
路径就等于把回滚锚作废。回退的具体数值与"引擎默认"本身住在
`neckline/selection/tier.py`(`TIER1_MIN_SCORE`/`TIER2_MIN_SCORE`/
`TIER3_MIN_SCORE`,`tier.resolve_quality_lines()`),**本文件不 import 它们**
(方向相反会成环:`tier.py` 已经 `from neckline.selection.pack import Pack,
get_active_pack`)——`_validate_quality_lines` 的单调性检查因此**只比较字面
给出的那些键**,不合并引擎默认值再比较,见该函数 docstring。同样是纯增量
可选键、K4-pack-v1 原样重新校验仍通过,**不 bump** `ENGINE_API_VERSION`。
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
from neckline.scan.stage import STAGE_ORDER
from neckline.selection import engine_api
from neckline.selection.primitives import PRIMITIVES, validate_params

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PACK_COLUMNS = (
    "pack_version, name, engine_api_version, manifest_json, config_json, "
    "evidence_ref, is_active, created_at, activated_at"
)

_EVIDENCE_REF_SEP = "; "   # `selection_packs.evidence_ref` 落库时的连接符(展示/grep 友好)

# `config.tier.stage_scores` 键的合法集合(③-K7-D 定案:英文枚举码,唯一源
# `neckline.scan.stage.STAGE_ORDER`,不在本文件复抄第二份六码元组)。
_STAGE_CODES = frozenset(STAGE_ORDER)

# `config.tier.quality_lines` 键的合法集合(V2-⑥-b 新增可选键:档位质量线,
# 与 `weights`/`dims`/`stage_scores` 平级)。
_QUALITY_LINE_KEYS = frozenset({"tier1_min", "tier2_min", "tier3_min"})

# 三档由严到松的固定顺序,单调性检查(`_validate_quality_lines`)按这个顺序
# 逐对比较相邻的**字面给出**的键——不是 DB 列序也不是字典序,是"档位越高线
# 越严"这条产品语义本身。
_QUALITY_LINE_ORDER = ("tier1_min", "tier2_min", "tier3_min")


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


def _validate_stage_scores(stage_scores: Any) -> List[str]:
    """`config.tier.stage_scores`(V2-③-K7 新增可选键,见模块头「V2-③-K7 新增」
    节)。**可选**——K4-pack-v1 及任何不需要五态打分的包可以整段不写这个键
    (`Pack.tier_stage_scores()` 缺省返回空字典;`driver_freshness` 维度拿不到
    映射时怎么降级为中性分是 ⑥ 的保险丝职责,见 ④b-C,不在这里猜)。存在时只
    校验形状:必须是对象,键必须是 `_STAGE_CODES`(`neckline.scan.stage.
    STAGE_ORDER`)六个英文枚举码之一——中文键已被 ③-K7-D 明令淘汰(库列值与
    配置键必须同源),值必须是数值。**不要求六态全部出现**(允许包只对部分
    阶段给出非默认打分,缺的那态如何降级同样是消费方的职责,不是格式判断)。"""
    if not isinstance(stage_scores, dict):
        return ["config.tier.stage_scores 必须是对象(阶段码 → 分数)"]
    errors: List[str] = []
    unknown = sorted(set(stage_scores) - _STAGE_CODES)
    if unknown:
        errors.append(
            f"config.tier.stage_scores 出现未知阶段码:{unknown}"
            f"(仅允许英文枚举码 {sorted(_STAGE_CODES)}——中文键已被 ③-K7-D 淘汰,"
            "库列值与配置键必须同源)"
        )
    bad_values = sorted(
        k for k, v in stage_scores.items()
        if k in _STAGE_CODES and (not isinstance(v, (int, float)) or isinstance(v, bool))
    )
    if bad_values:
        errors.append(f"config.tier.stage_scores 存在非数值分数:{bad_values}")
    return errors


def _validate_quality_lines(quality_lines: Any) -> List[str]:
    """`config.tier.quality_lines`(V2-⑥-b 新增可选键,plan §五 ⑥-b-A 裁定)。
    **整段可选**——K4-pack-v1(回滚锚)完全不写这个键,`Pack.tier_quality_lines()`
    缺省返回空字典,逐键回退引擎默认是 `tier.resolve_quality_lines()` 的职责,
    不在这里猜。**三个子键也各自独立可选**(同 `_validate_stage_scores` "不要求
    六态全部出现"同一纪律)——存在时只校验形状:必须是对象,键必须是
    `tier1_min`/`tier2_min`/`tier3_min` 之一,值必须是数值(`bool` 视为非数值,
    同 `_validate_stage_scores` 的既有陷阱防线)。

    **单调性("档位越高线越严")只检查字面给出的那些键**,不合并引擎默认值
    再比较——`pack.py` 不 import `tier.py` 的具体默认数字(那个方向会成环,
    `tier.py` 已经反过来 import 本模块的 `Pack`/`get_active_pack`);K4-pack-v1
    等价于三键全部缺省,天然满足单调性(无键可比,不会被这条拒绝)。plan
    验收原文给的反例 `tier1_min < tier2_min` 是两键都给出的场景,本检查逐对
    比较**相邻的**已给出键(`_QUALITY_LINE_ORDER` 顺序),靠传递性覆盖任意
    两个给出键之间的比较(哪怕中间那一档缺省)。"""
    if not isinstance(quality_lines, dict):
        return ["config.tier.quality_lines 必须是对象(tier1_min/tier2_min/tier3_min → 分数)"]
    errors: List[str] = []
    unknown = sorted(set(quality_lines) - _QUALITY_LINE_KEYS)
    if unknown:
        errors.append(
            f"config.tier.quality_lines 出现未知键:{unknown}"
            f"(仅允许 {sorted(_QUALITY_LINE_KEYS)})"
        )
    bad_values = sorted(
        k for k, v in quality_lines.items()
        if k in _QUALITY_LINE_KEYS and (not isinstance(v, (int, float)) or isinstance(v, bool))
    )
    if bad_values:
        errors.append(f"config.tier.quality_lines 存在非数值分数:{bad_values}")

    present = [
        (k, float(quality_lines[k])) for k in _QUALITY_LINE_ORDER
        if k in quality_lines and k not in bad_values
    ]
    for (stricter_key, stricter_val), (looser_key, looser_val) in zip(present, present[1:]):
        if stricter_val < looser_val:
            errors.append(
                f"config.tier.quality_lines 三线必须单调不增(档位越高线越严):"
                f"{stricter_key}={stricter_val} < {looser_key}={looser_val}"
            )
    return errors


def validate_config(config: Any) -> List[str]:
    """config 必需两段(plan §五 V2-③「插槽边界」):`seeds`(原语名 → 参数,
    键必须是已注册原语,值按该原语 `params_schema` 校验)与 `tier`(`weights` 非空
    对象 + `dims` 非空数组,`dims` 引用的维度必须都在 `weights` 里出现;
    **V2-③-K7 新增**:与 `weights`/`dims` 平级的可选键 `stage_scores`,见
    `_validate_stage_scores`;**V2-⑥-b 新增**:同平级的可选键 `quality_lines`,
    见 `_validate_quality_lines`)。"""
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
        stage_scores = tier.get("stage_scores")
        if stage_scores is not None:
            errors.extend(_validate_stage_scores(stage_scores))
        quality_lines = tier.get("quality_lines")
        if quality_lines is not None:
            errors.extend(_validate_quality_lines(quality_lines))
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

    def tier_stage_scores(self) -> Dict[str, float]:
        """`config.tier.stage_scores`(V2-③-K7 新增可选键:行业题材五态打分
        映射,K4-pack-v1 没有这一段,缺省返回空字典——`driver_freshness` 缺
        映射/缺行时怎么降级为中性分是 ⑥ 的保险丝职责,见 ④b-C,不在本访问器
        里猜)。"""
        return dict(self.config.get("tier", {}).get("stage_scores", {}))

    def tier_quality_lines(self) -> Dict[str, float]:
        """`config.tier.quality_lines`(V2-⑥-b 新增可选键:三档质量线,
        K4-pack-v1 没有这一段〔或只给部分子键〕,缺省返回空字典——逐键回退
        引擎默认是 ⑥ 的职责〔`tier.resolve_quality_lines()`〕,不在本访问器
        里猜)。"""
        return dict(self.config.get("tier", {}).get("quality_lines", {}))


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
