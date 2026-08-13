"""策略包读写(plan §五 V2-③)。manifest/config schema 校验 + 包文件装载 +
`selection_packs` 读写。**append-only + 单现役**(同 `strategy_versions` 既有
分工:新包版本追加行,`is_active`/`activated_at` 在唯一现役行上切换,激活事件
另落 `selection_pack_activation_log`,见 `neckline/db.py` 建表注释)。

**唯一写入口 = `activate_pack()`**(单事务:落新包行〔或识别到内容相同的既有行,
幂等〕+ 激活切换 + 追加事件),供 `scripts/activate_pack.py` 闸 4 调用 ——
本模块不提供"只登记不激活"的旁路(plan 没有要求这个功能,包的登记与激活在这个
产品里刻意是同一个动作,不像章程切换那样分成"先建行再切换"两步)。

**读现役行唯一实现 = `get_active_line()`**(V2.2-① 起;`get_active_pack()` 降为
其骨架线薄封装,照 `neckline.strategy.brain.get_active()` 体例,详见文末
「V2.2-① 多版本线注册表」节)。**不做时间线解析**(不像 `brain.py` 有
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
机械分下限,子键**各自独立可选**,同 `stage_scores` "不要求六态全部出现"同一
纪律)。归属判给"包"而不是"引擎常量"的决定性理由是**标度耦合**:质量线与五维
权重作用在**同一个标度**上,权重已经在包里,线留在代码里 = 换一次权重就静默
改变 T1 的选择性。
**缺键回退 vs `weights` 缺维度 fail loud,两种姿势刻意不同**:`weights` 每个
包 schema 都必须给全,缺了就是包坏了;`quality_lines` 缺(整段缺或单键缺)
一律回退引擎默认——因为 `K4-pack-v1` 不重发版、是 ⑯-E 的回滚锚,不给回退
路径就等于把回滚锚作废。回退的具体数值与"引擎默认"本身住在
`neckline/selection/tier.py`(`TIER1_MIN_SCORE`/`TIER2_MIN_SCORE`,
`tier.resolve_quality_lines()`),**本文件不 import 它们**(方向相反会成环:
`tier.py` 已经 `from neckline.selection.pack import Pack, get_active_pack`)
——`_validate_quality_lines` 的单调性检查因此**只比较字面给出的那些现役键**,
不合并引擎默认值再比较,见该函数 docstring。同样是纯增量可选键、K4-pack-v1
原样重新校验仍通过,**不 bump** `ENGINE_API_VERSION`。

**V2.1-② T3 全链退役**:现役子键收窄为 `{tier1_min, tier2_min}`,`tier3_min`
移入 `_RETIRED_QUALITY_LINE_KEYS` —— **schema 仍受理它**(否则 `K7-pack-v1`
这个回滚锚当场作废),但它不生效、不进单调性;引擎侧忽略它并打 WARNING。
按 ③-K7 的判定规则(「旧包原样重新校验仍通过 + `get_active_pack()` 对旧包行为
逐位不变」),当时**不 bump** `ENGINE_API_VERSION`(仍为 1)。
⚠ 该「回滚锚」语境自 V2.2-① 起**已成历史**:`ENGINE_API_VERSION` 已 bump 到 2
(判定依据见 `engine_api.py` 模块头),K4-pack-v1 / K7-pack-v1 两个回滚锚**作废**
——受理 `tier3_min` 的 schema 宽容**保留**(历史行读回仍要能解析),但那两个包
**不再能过闸激活**,这是刻意的(⛔ 不许再写「回滚 = 激活旧包」)。

════════════════════════════════════════════════════════════════════════════
**V2.2-① 多版本线注册表(plan §五 ①,2026-08-09 K8 立项)** —— 本模块最大一次
语义换血,冷启动先读这一节:

- `selection_packs` 从「单包制(全表唯一现役)」升级为「**一条骨架线 V + 三条
  引擎线 C/Z/Y 并跑**」:每条线独立版本、独立激活、独立运行/停止,唯一现役约束
  改为**每线唯一**(库级 partial unique index `(line_code, is_active)`,见
  `neckline/db.py::_POST_MIGRATION_INDEXES`)。历史两行(K4/K7)= `LEGACY` 线。
- **`line_code` 的声明位置 = `manifest.line_code`**(缺省 = `"LEGACY"`,取值
  ∈ `_LINE_CODES`):它是「这个包是谁」的身份声明,与 `pack_version`/`name` 同属
  manifest 一层,不塞进 config(config 是"包配的参数",身份不该混进参数)。
- **两套 config schema,交叉校验、⛔ 不混**(`_validate_line_cross`):
  骨架线 `V` → 必须有 `seeds`+`tier`、**不许有 `engine`**;引擎线 `C/Z/Y` →
  必须有 `engine`、**不许有 `seeds`/`tier`**,且 `config.engine.engine_code`
  与 `line_code` **逐位相等**。**其他 config 顶层键一律不管**(② 要给骨架包加
  `config.regime`,现在拒了它等于给后续块挖坑)。
  ⚠ **`config.landing` 段已随 2026-08-09 用户裁定 #11 整体删除**(位置关不再有机械
  判定,判定交 LLM)——`_LANDING_THRESHOLD_KEYS` / `_validate_landing` /
  `Pack.landing_config()` 一并退场,`gates.position` 的七个阈值键也全删,只剩一个
  **定性文本键 `guidance`**(⛔ 不走 `provenance` 闸,见 `_QUALITATIVE_GATE_KEYS`)。
- **`provenance` 强制字段(裁定 #4 落成机器判据,闸 1 执行)**:引擎包
  `gates.*` 与 `tier_evidence.*` 下**每个阈值叶子**必须写成
  `{"value": <任意 JSON>, "provenance": {...}}` 的两键对象,`provenance` 二选一:
      `{"source": "audited", "ref": "<指向审计档案的非空指针>"}`
      `{"source": "engineering_v1", "basis": "<从 K8 哪句定性边界翻译来的>",
        "calibration": "pending"}`
  **缺 `provenance` / 形状不对 = 拒绝激活** —— 这条的全部意义是让「工程首版
  ⛔ 不冒充审计结论」变成过不去的闸,而不是一句自觉。
- **引擎阈值键名白名单 = `_ENGINE_GATE_SCHEMA`(plan 字面的一处落地澄清,已由
  orchestrator 裁定)**:施工图原文「`gates.*` 的每个键都必须引用已注册原语
  (白名单制照旧)」—— 但 `PRIMITIVES` 是 **seeds 域**的原语注册表(逐票
  filter/sort 纯函数),六关阈值不是 seeds 原语(六关实现在第 ③ 块的
  `gates.py`,吃的是行情状态/板块强度/落地态等**篮子级/市场级**输入)。硬把
  六关键名塞进 `PRIMITIVES` 等于给每个阈值造一个永远不会被 seeds 管线调用的假
  原语。故「白名单制」落成本文件的 `_ENGINE_GATE_SCHEMA`:五个关口段 +
  `tier_evidence.t1/t2` 各自允许的阈值键名逐个登记,闸 1 校验「键在白名单内 +
  每个叶子带 {value, provenance}」——**"要新玩法先扩白名单"的既有代价一字不变**,
  只是名单住在这里、⛔ 不绑 `PRIMITIVES`。
- **读侧四入口(定死,⛔ 别自创第五个)**:`get_active_line()` 唯一实现;
  `get_active_skeleton()` = `get_active_line("V")`;`get_active_engines()`(只含
  `status='running'` 的 C/Z/Y,按 C→Z→Y 确定性排序);`get_active_pack()` 保留
  为 `get_active_line("V")` 的薄封装(既有消费方 `scan/seeds.py` /
  `selection/tier.py` 零改动——K8 §一 明写骨架管「股票池、篮子、梯度」,而它们
  读的 `seeds`/`tier` 两段正是骨架线内容;⛔ 别把它们改成读引擎线,引擎线里
  根本没有这两段)。
- **现役包缓存自本版按 `(db_path, line_code)` 分桶**:只按 db_path 分桶在多线下
  会「读 V 之后读 C 互相顶掉缓存 → 静默返回错误的线」(plan 点名的陷阱)。
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline.config import settings
from neckline.db import connection, init_schema
from neckline.scan.stage import STAGE_ORDER
from neckline.selection import engine_api
from neckline.selection.primitives import PRIMITIVES, validate_params

logger = logging.getLogger(__name__)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PACK_COLUMNS = (
    "pack_version, name, engine_api_version, manifest_json, config_json, "
    "evidence_ref, is_active, created_at, activated_at, line_code, status"
)

# —— V2.2-① 版本线常量(取值域与 DB 列注释同源,见 neckline/db.py)—————————————
# ⚠ 命名纪律(项目 CLAUDE.md「三条版本线」):骨架版本写全称 `K8-V0.5`,⛔ `V0.5`
# 禁简写;引擎升级写 `C2`/`Z2`/`Y2`,⛔ 不写「K8 v2」。
_LINE_CODES: Tuple[str, ...] = ("V", "C", "Z", "Y", "LEGACY")
_ENGINE_LINE_CODES: Tuple[str, ...] = ("C", "Z", "Y")   # get_active_engines 的确定性序
_LINE_DEFAULT = "LEGACY"

#: 原子激活批次号的形状(P4.3)。🔵 **复审 🔵-4:这里的 12 不是阈值、不进任何判据**,
#: 只是批次名的长度(48 bit 随机,`journalctl` 与 `SELECT DISTINCT batch_id` 里一眼看得完)。
#: ⛔ 别把它读成"证据数量 / 样本量"那一族的数;⛔ 也别把两处长度改成不一样。
_BATCH_ID_HEX_LEN = 12
_BATCH_ID_RE = re.compile(r"^set-[0-9a-f]{%d}$" % _BATCH_ID_HEX_LEN)

# —— V2.2-① 引擎阈值键名白名单(「白名单制」在引擎线上的落地,⛔ 不绑 PRIMITIVES,
# 理由见模块头)。五个关口段名 = plan §五 ① schema 原文;段内键名 = ③-F 三引擎
# 首版阈值表逐条对应的机器名。要新玩法(新阈值键)先来这里登记,再发包 —— 与
# seeds 域「要新玩法先加原语」同一条既有代价。————————————————————————————————
_ENGINE_GATE_SCHEMA: Dict[str, frozenset] = {
    "market": frozenset({
        "primary_regimes",                      # 该引擎的主场行情状态集合
        "high_divergence_min_breadth_pctile",   # C1:高位分歧下要求板块广度分位下限
        "rotation_confirmed_blocks_t1",         # C1:切换确认下不产 T1
        "trend_continuation_required_stages",   # Z1:趋势延续下要求 stage 所属集合
    }),
    "sector": frozenset({
        "industry_rank_max",                    # C1/Y1:行业强度名次上限
        "strength_days_min_5d",                 # C1:近 5 日强度日数下限
        "stage_allowed",                        # Z1:行业题材阶段允许集合
        "cluster_members_min",                  # Z1:簇成员数下限(扩散成层级)
    }),
    # 🔴 **位置关自此零阈值**(2026-08-09 用户裁定 #11:位置关由机械关改判为证据关,
    # 判定直接交 LLM)。原来的七个阈值键(`t1_landing_states` / `t2_landing_states` /
    # `pullback_depth_range` / `landing_states` / `dist_from_high_60d_min` /
    # `platform_days_min` / `platform_amplitude_max`)**全部删除,⛔ 不得恢复** ——
    # K8 §二 对「落地起跳」只有五句定性零个数字,那套翻译出来的阈值连乘后交集近乎
    # 为空(14 个 D0 回放零 T1)。三引擎的位置差别改由**定性描述 + LLM 判断**承担。
    "position": frozenset({
        "guidance",                             # 定性位置准则(文本,⛔ 不是阈值)
    }),
    # 🔴 **核心关自此零阈值**(2026-08-09 用户裁定 #12,与位置关同款):原键
    # `leader_rs_rank_max`(三引擎 3/2/5)**已删除,⛔ 不得恢复** —— 它取数自
    # `leader_structure_daily` 的**簇内**口径,入场券 = 「当天必须涨停」,而 K8 三
    # 引擎找的都是「还没怎么涨、刚要动」的票(生产实测全市场只有 1.4% 判得出)。
    # ⚠ `≤3` 这个数本身是 `audited`(H10),**错的是那把尺子的取数域**;挪到行业域
    # 后同一个「3」意思完全变了,⛔ 不许直接搬。机械侧改出行业域读数
    # (`selection/core_metrics.py`,零阈值、零及格线,**含「行业内前 X%」这类**),
    # 判定交 LLM。
    "core": frozenset({
        "guidance",                             # 定性核心(龙头)准则(文本,⛔ 不是阈值)
    }),
    "evidence": frozenset({
        "independent_evidence_min",             # 三引擎:独立证据份数下限
        "require_news_policy_source",           # Z1:必须含一份消息/政策类来源
    }),
}
# 🔴 **`provenance` 闸的白名单例外**(裁定 #11 位置关 / 裁定 #12 核心关):
# `gates.<关>.guidance` 是**定性文本不是阈值** —— 它进 prompt、**不进任何机械判据**,
# 故不走 `_validate_provenance_leaf`(同 `config.engine.applies_to` 的既有体例:人话
# 字段不自报来源)。⛔ 白名单只此**两关两键**,要再加"不走闸的键"必须先想清楚它是不是
# 真的不进判据。形状要求 = **非空字符串**。
# V2.3.2-④-A:`config.iteration` 的键白名单(K8.md §十七 的两个样本门槛)。
# ⚠ **值的唯一源在包里**(30 / 80),⛔ 本模块不写默认值 —— `eval/iteration.py` 有一条
# 「模块内不许出现阈值默认值」的守门单测,这条纪律在这里同样成立。
_ITERATION_KEYS: frozenset = frozenset({"min_n", "retire_min_n"})

# `config.threshold_governance` 的 mode 词表(= `gates.ENFORCEMENT_*` 两值)。
# ⚠ 刻意写成字面量而不是 import `gates` —— gates 反向 import 本模块,会成环;
# 两处一致性由 `tests/test_selection_pack.py` 的对拍守门保证。
_GOVERNANCE_MODES: frozenset = frozenset({"hard", "evidence"})

_QUALITATIVE_GATE_KEYS: Dict[str, frozenset] = {
    "position": frozenset({"guidance"}),
    "core": frozenset({"guidance"}),
}

_TIER_EVIDENCE_TIERS: Tuple[str, ...] = ("t1", "t2")
_TIER_EVIDENCE_LEAF_KEYS: frozenset = frozenset({
    "max_evidence_degrades",                    # 该档允许证据关降级的处数上限(K8 §八)
})
# —— 🔴 V2.4.0 P1.6:T2 的**正式定档策略**(可选键,只有 t2 段能写)————————————
# `no_hard_fail` = 该引擎的正式定档**不吃** `max_evidence_degrades`(那个数降为影子
# 规则,只统计"若按旧规则会不会 OUT");**缺键 = 旧行为**(C1/Z1/Y1 一字不动 →
# 回滚可复现,已拍板 #8)。
# ⚠ 它**不是阈值叶子**:形状是**枚举字符串**,⛔ 不走 `_validate_provenance_leaf`
# (同 `gates.<关>.guidance` 的既有体例 —— 它不是一个"从 K8 定性边界翻译出来的数",
# 而是一个"按哪套规则定档"的开关)。
# 🔴 **正因为可选 + 旧包行为逐位不变,`ENGINE_API_VERSION` 保持 2**(§3.14-G):
# 升代际会当场废掉「旧四包仍可激活」这条回滚绳,⛔ 别"顺手"升。
_TIER_EVIDENCE_POLICY_KEY = "formal_policy"
_TIER_EVIDENCE_POLICY_TIERS: frozenset = frozenset({"t2"})
_TIER_EVIDENCE_POLICY_VALUES: frozenset = frozenset({"no_hard_fail"})
_PROVENANCE_SOURCES: Tuple[str, ...] = ("audited", "engineering_v1")

# —— V2.2-② 行情状态层五个判定阈值的键名白名单(骨架线 `config.regime` 段)。
# 引擎默认值与语义注释住 `neckline/scan/regime.py::REGIME_THRESHOLD_DEFAULTS`
# (守门单测锁两处键集合相等,防漂);白名单本体住这里而不是 regime.py,理由同
# `_ENGINE_GATE_SCHEMA`:regime.py 已 import 本模块的读入口,反向 import 会成环。
# 每个键的叶子 = {value, provenance} 两键对象(复用 `_validate_provenance_leaf`,
# 裁定 #4 同一道闸)且 value 必须是数值 —— 键写错/形状不对在闸 1 当场拒,⛔ 不许
# 静默回退默认值(那种错看不出来,plan §五 ②-D 点名的陷阱)。——————————————
_REGIME_THRESHOLD_KEYS: frozenset = frozenset({
    "rot_gap",          # 切换确认:新旧方向 5 日中位收益差下限
    "rot_rank",         # 切换确认:资金迁移排名上升名次下限
    "div_core_drop",    # 高位分歧 A:核心强度较 5 日均值下降分位下限
    "div_breadth",      # 高位分歧 B:板块广度分位上限
    "div_limit_drop",   # 高位分歧 C:涨停家数环比降幅下限
})

# ⚠ **`config.landing` 段(落地起跳十二个阈值)已随 2026-08-09 用户裁定 #11 整体删除**:
# 位置关不再有机械判定,骨架包里那一段与 `_LANDING_THRESHOLD_KEYS` / `_validate_landing`
# / `Pack.landing_config()` 一并退场。⛔ 不得恢复 —— 机械层自此**只算读数、不下结论**。

_EVIDENCE_REF_SEP = "; "   # `selection_packs.evidence_ref` 落库时的连接符(展示/grep 友好)

# `config.tier.stage_scores` 键的合法集合(③-K7-D 定案:英文枚举码,唯一源
# `neckline.scan.stage.STAGE_ORDER`,不在本文件复抄第二份六码元组)。
_STAGE_CODES = frozenset(STAGE_ORDER)

# `config.tier.quality_lines` 的**现役**键(V2-⑥-b 新增可选键:档位质量线,
# 与 `weights`/`dims`/`stage_scores` 平级)。V2.1-② 起只剩两档。
_ACTIVE_QUALITY_LINE_KEYS = frozenset({"tier1_min", "tier2_min"})

# **已退役的**质量线键(V2.1-② T3 全链退役)。校验侧对它**受理但不报"未知键"**,
# 🔴 理由:`K4-pack-v1` / `K7-pack-v1` 是**回滚锚**,而 `K7-pack-v1` 里写着
# `tier3_min` —— 拒绝 = 回滚锚当场作废(与 ⑥-b-A 立 `quality_lines` 时"缺键回退
# 保回滚锚"的理由同源)。受理 ≠ 生效:引擎侧 `tier.resolve_quality_lines()` 见到它
# 会打一行 WARNING 并忽略(⛔ 不静默),单调性检查也**只比现役两键**。
# ⛔ 别把它并回 `_ACTIVE_QUALITY_LINE_KEYS`(那等于把 T3 复活)。
_RETIRED_QUALITY_LINE_KEYS = frozenset({"tier3_min"})

_QUALITY_LINE_KEYS = _ACTIVE_QUALITY_LINE_KEYS | _RETIRED_QUALITY_LINE_KEYS

# 现役档位由严到松的固定顺序,单调性检查(`_validate_quality_lines`)按这个顺序
# 逐对比较相邻的**字面给出**的键——不是 DB 列序也不是字典序,是"档位越高线
# 越严"这条产品语义本身。**退役键不进这个元组**(它已经不表达任何档位)。
_QUALITY_LINE_ORDER = ("tier1_min", "tier2_min")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db_cache_key(db_path: Optional[Path]) -> str:
    return str(db_path) if db_path is not None else str(settings.db_path)


def _cache_key(db_path: Optional[Path], line_code: str) -> Tuple[str, str]:
    """V2.2-①:缓存键 = `(解析后的 db 路径, line_code)` 双元组 —— 只按 db_path
    分桶在多线并跑下会「读 V 之后读 C 互相顶掉缓存 → 静默返回错误的线」。"""
    return (_db_cache_key(db_path), line_code)


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
    允许空列表——校验只管形状,"证据链是否该非空"是产品判断不是格式判断)。
    **V2.2-① 新增可选键 `line_code`**(缺省 = `LEGACY`,取值 ∈ `_LINE_CODES`,
    声明位置的裁定见模块头「V2.2-① 多版本线注册表」节)。"""
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
    line_code = manifest.get("line_code", _LINE_DEFAULT)
    if line_code not in _LINE_CODES:
        errors.append(
            f"manifest.line_code 取值非法:{line_code!r}(仅允许 {list(_LINE_CODES)};"
            "缺省 = 'LEGACY')"
        )
    return errors


def manifest_line_code(manifest: Any) -> str:
    """从 manifest 取版本线码(缺省 `LEGACY`;非法值也原样返回,由
    `validate_manifest` 负责报错——本函数只取值不判罪,免得两处各判一套)。"""
    if not isinstance(manifest, dict):
        return _LINE_DEFAULT
    v = manifest.get("line_code", _LINE_DEFAULT)
    return v if isinstance(v, str) else _LINE_DEFAULT


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
    不在这里猜。**子键也各自独立可选**(同 `_validate_stage_scores` "不要求
    六态全部出现"同一纪律)——存在时只校验形状:必须是对象,键必须是
    `tier1_min`/`tier2_min`(现役)或 `tier3_min`(**已退役但受理**)之一,现役键的
    值必须是数值(`bool` 视为非数值,同 `_validate_stage_scores` 的既有陷阱防线)。

    **V2.1-② 退役键的处置(定死)**:`tier3_min` **受理、不报"未知键"、不参与值校验、
    不参与单调性** —— 🔴 因为 `K7-pack-v1` 里就写着它,而那是**回滚锚**;拒绝它 =
    回滚锚当场作废。受理 ≠ 生效:`tier.resolve_quality_lines()` 见到它会打一行
    WARNING 并忽略(⛔ 不静默,静默忽略等于让包以为自己配了个生效的旋钮)。

    **单调性("档位越高线越严")只检查字面给出的那些现役键**,不合并引擎默认值
    再比较——`pack.py` 不 import `tier.py` 的具体默认数字(那个方向会成环,
    `tier.py` 已经反过来 import 本模块的 `Pack`/`get_active_pack`);K4-pack-v1
    等价于两键全部缺省,天然满足单调性(无键可比,不会被这条拒绝)。plan
    验收原文给的反例 `tier1_min < tier2_min` 是两键都给出的场景,本检查逐对
    比较**相邻的**已给出现役键(`_QUALITY_LINE_ORDER` 顺序)。"""
    if not isinstance(quality_lines, dict):
        return ["config.tier.quality_lines 必须是对象(tier1_min/tier2_min → 分数)"]
    errors: List[str] = []
    unknown = sorted(set(quality_lines) - _QUALITY_LINE_KEYS)
    if unknown:
        errors.append(
            f"config.tier.quality_lines 出现未知键:{unknown}"
            f"(仅允许 {sorted(_ACTIVE_QUALITY_LINE_KEYS)};"
            f"另受理已退役键 {sorted(_RETIRED_QUALITY_LINE_KEYS)},受理但不生效)"
        )
    bad_values = sorted(
        k for k, v in quality_lines.items()
        if k in _ACTIVE_QUALITY_LINE_KEYS and (not isinstance(v, (int, float)) or isinstance(v, bool))
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


def _validate_regime(regime: Any) -> List[str]:
    """`config.regime`(V2.2-② 新增可选段:行情状态层五个判定阈值,只住骨架线)。
    **整段可选、子键各自独立可选**(照 `_validate_quality_lines` 体例)——缺段/缺键
    一律由 `scan/regime.py::resolve_regime_thresholds()` 逐键回退引擎默认(+WARNING),
    不在这里猜。存在的键必须:①在 `_REGIME_THRESHOLD_KEYS` 白名单内(**键写错 =
    激活时拒**,⛔ 不许静默回退默认值 —— plan §五 ②-D 点名的陷阱:typo 的阈值键
    会悄悄失效且看不出来);②叶子过 `_validate_provenance_leaf`(裁定 #4 同一道闸:
    工程首版阈值必须自报来源,⛔ 不冒充审计结论);③`value` 是数值。"""
    if not isinstance(regime, dict):
        return ["config.regime 必须是对象(阈值键 → {value, provenance})"]
    errors: List[str] = []
    unknown = sorted(set(regime) - _REGIME_THRESHOLD_KEYS)
    if unknown:
        errors.append(
            f"config.regime 出现白名单外的阈值键:{unknown}"
            f"(仅允许 {sorted(_REGIME_THRESHOLD_KEYS)};键写错会静默回退引擎默认,"
            "故在闸 1 当场拒 —— plan §五 ②-D)"
        )
    for key in sorted(set(regime) & _REGIME_THRESHOLD_KEYS):
        leaf = regime[key]
        leaf_errors = _validate_provenance_leaf(f"config.regime.{key}", leaf)
        errors.extend(leaf_errors)
        if not leaf_errors:
            value = leaf["value"]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"config.regime.{key}.value 必须是数值(得到 {value!r})")
    return errors


def _validate_iteration(iteration: Any) -> List[str]:
    """`config.iteration`(V2.3.2-④-A 新增可选段:四分类样本门槛 30 / 80,只住骨架线)。

    🔴 **非做不可的理由**:骨架线 config **放行任何未知顶层键**(只禁 `engine` 段)——
    一个拼错的段名(`iterations`)会**静默**让四分类退回「未拍板」,而那与"还没配"
    **长得一模一样**(`build_iteration_report` 两种情形都出 `thresholds.available=false`)。
    故段名一旦出现就当场校验;⛔ 别指望运行期能看出来。

    与 `_validate_regime` 的差别(刻意):**两个键都必需**(缺一个 = 四分类没法跑),
    `value` 必须是**非负整数**(不是数值),且 `retire_min_n >= min_n`
    (与 `eval/iteration.py::IterationThresholds.from_pack_config` 的交叉校验同口径)。"""
    if not isinstance(iteration, dict):
        return ["config.iteration 必须是对象(min_n / retire_min_n → {value, provenance})"]
    errors: List[str] = []
    unknown = sorted(set(iteration) - _ITERATION_KEYS)
    if unknown:
        errors.append(
            f"config.iteration 出现白名单外的键:{unknown}"
            f"(仅允许 {sorted(_ITERATION_KEYS)};键写错会让四分类静默退回「未拍板」,"
            "与「还没配」长得一模一样,故在闸 1 当场拒 —— plan §五 ④-A)")
    values: Dict[str, int] = {}
    for key in sorted(_ITERATION_KEYS):
        if key not in iteration:
            errors.append(f"config.iteration.{key} 缺失(两个键都必需)")
            continue
        leaf_errors = _validate_provenance_leaf(f"config.iteration.{key}", iteration[key])
        errors.extend(leaf_errors)
        if leaf_errors:
            continue
        value = iteration[key]["value"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"config.iteration.{key}.value 必须是非负整数(得到 {value!r})")
        else:
            values[key] = value
    if len(values) == len(_ITERATION_KEYS) and values["retire_min_n"] < values["min_n"]:
        errors.append(
            f"config.iteration.retire_min_n({values['retire_min_n']}) 必须 ≥ "
            f"min_n({values['min_n']}) —— 淘汰门槛比观察门槛还低说不通")
    return errors


def _validate_threshold_governance(governance: Any) -> List[str]:
    """`config.threshold_governance`(V2.3.2-④-A 新增可选段:关口闸门模式**对账表**)。

    🔴 **它不是第二个事实源、更不是开关**:闸门模式仍由引擎包叶子的
    `provenance.source` **唯一决定**(唯一实现 `gates.py::enforcement_of`)。这张表只
    负责一件事 —— **让一次悄悄的 provenance 改动过不了闸 1**(裁定 1「零自动升级」的
    物理落点)。⛔ 别让任何运行期代码去读它做判断。

    本函数只校验**形状**(纯函数,无 I/O)。与三个现役引擎包的**逐条一致性**由
    `gates.py::check_threshold_governance` 做 —— 它要读引擎包,而 `pack.py` ⛔ 不能
    import `gates`(gates 反向 import 本模块,会成环)。两处都挂在闸 1 上。"""
    if not isinstance(governance, dict):
        return ["config.threshold_governance 必须是对象(`<引擎>.<关>.<键>` → {mode, basis})"]
    errors: List[str] = []
    for key in sorted(governance):
        entry = governance[key]
        if len(str(key).split(".")) != 3:
            errors.append(
                f"config.threshold_governance 的键 {key!r} 形状不对 —— "
                "必须是 `<引擎版本>.<关>.<阈值键>` 三段(如 C1.sector.industry_rank_max)")
            continue
        if not isinstance(entry, dict) or set(entry) != {"mode", "basis"}:
            errors.append(
                f"config.threshold_governance.{key} 必须恰为 {{mode, basis}} 两键"
                f"(得到 {sorted(entry) if isinstance(entry, dict) else type(entry).__name__})")
            continue
        if entry["mode"] not in _GOVERNANCE_MODES:
            errors.append(
                f"config.threshold_governance.{key}.mode 必须是 "
                f"{sorted(_GOVERNANCE_MODES)} 之一(得到 {entry['mode']!r})")
        if not str(entry.get("basis") or "").strip():
            errors.append(
                f"config.threshold_governance.{key}.basis 不能为空 —— "
                "每条都要写清依据(裁定几、为什么),否则这张表就退化成一堆没人敢动的字面量")
    return errors


def _validate_provenance_leaf(path: str, leaf: Any) -> List[str]:
    """V2.2-① 闸 1:引擎包一个阈值叶子的 `{value, provenance}` 形状校验(裁定 #4
    的机器判据,形状定义见模块头)。`value` 允许任意 JSON(数值 / 布尔 / 数组——
    阈值带、状态集合都是合法阈值),`provenance` 二选一,**缺了 / 混了就是拒**。"""
    errors: List[str] = []
    if not isinstance(leaf, dict) or set(leaf) != {"value", "provenance"}:
        return [
            f"{path} 必须是恰含 value/provenance 两键的对象"
            "(引擎包每个阈值都要自报来源,plan §3.11-D / §五 ① 裁定 #4)"
        ]
    prov = leaf["provenance"]
    if not isinstance(prov, dict):
        return [f"{path}.provenance 必须是对象"]
    source = prov.get("source")
    if source == "audited":
        ref = prov.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            errors.append(f"{path}.provenance 声明 audited 却缺非空 ref(审计结论必须可回指)")
        extra = set(prov) - {"source", "ref"}
        if extra:
            errors.append(f"{path}.provenance(audited)出现未知键:{sorted(extra)}")
    elif source == "engineering_v1":
        basis = prov.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            errors.append(
                f"{path}.provenance 声明 engineering_v1 却缺非空 basis"
                "(必须写清从 K8 哪句定性边界翻译来的,⛔ 不冒充审计结论)"
            )
        if prov.get("calibration") != "pending":
            errors.append(
                f"{path}.provenance(engineering_v1)必须带 calibration='pending'"
                "(工程首版待时钟数据校准,裁定 #4 原文)"
            )
        extra = set(prov) - {"source", "basis", "calibration"}
        if extra:
            errors.append(f"{path}.provenance(engineering_v1)出现未知键:{sorted(extra)}")
    else:
        errors.append(
            f"{path}.provenance.source 取值非法:{source!r}(仅允许 {list(_PROVENANCE_SOURCES)})"
        )
    return errors


def _validate_engine_config(config: Dict[str, Any], line_code: str) -> List[str]:
    """引擎线(C/Z/Y)config 校验:`engine` 段必备、`seeds`/`tier` 禁入、
    `engine_code` 与 line_code 逐位相等、gates/tier_evidence 键名走
    `_ENGINE_GATE_SCHEMA` 白名单、每个阈值叶子过 `_validate_provenance_leaf`。
    其他 config 顶层键一律不管(模块头「V2.2-①」节:② 的 regime、③ 的 landing
    都要往顶层加键,现在拒了它们就是给后续块挖坑)。"""
    errors: List[str] = []
    for forbidden in ("seeds", "tier"):
        if forbidden in config:
            errors.append(
                f"引擎线(line_code={line_code})的 config 不许出现 {forbidden} 段"
                "(骨架/引擎两套 schema 交叉校验,⛔ 不混——plan §五 ① 原文)"
            )
    engine = config.get("engine")
    if not isinstance(engine, dict):
        errors.append(f"引擎线(line_code={line_code})的 config.engine 必须是对象")
        return errors

    unknown = sorted(set(engine) - {"engine_code", "applies_to", "gates", "tier_evidence"})
    if unknown:
        errors.append(f"config.engine 出现未知键:{unknown}")

    engine_code = engine.get("engine_code")
    if engine_code != line_code:
        errors.append(
            f"config.engine.engine_code({engine_code!r})必须与 manifest.line_code"
            f"({line_code!r})逐位相等(闸 1 交叉校验,plan §五 ① 原文)"
        )
    applies_to = engine.get("applies_to")
    if not isinstance(applies_to, str) or not applies_to.strip():
        errors.append("config.engine.applies_to 必须是非空字符串(人话,不进任何判据)")

    gates = engine.get("gates")
    if not isinstance(gates, dict):
        errors.append("config.engine.gates 必须是对象(market/sector/position/core/evidence)")
    else:
        missing = sorted(set(_ENGINE_GATE_SCHEMA) - set(gates))
        if missing:
            errors.append(f"config.engine.gates 缺关口段:{missing}(五关一段都不能少)")
        unknown_sections = sorted(set(gates) - set(_ENGINE_GATE_SCHEMA))
        if unknown_sections:
            errors.append(f"config.engine.gates 出现未知关口段:{unknown_sections}")
        for section, allowed in _ENGINE_GATE_SCHEMA.items():
            body = gates.get(section)
            if body is None:
                continue
            if not isinstance(body, dict):
                errors.append(f"config.engine.gates.{section} 必须是对象(阈值键 → {{value, provenance}})")
                continue
            unknown_keys = sorted(set(body) - allowed)
            if unknown_keys:
                errors.append(
                    f"config.engine.gates.{section} 出现白名单外的阈值键:{unknown_keys}"
                    f"(允许:{sorted(allowed)};要新玩法先扩 _ENGINE_GATE_SCHEMA——"
                    "白名单制既有代价,⛔ 不许包侧自创键名)"
                )
            qualitative = _QUALITATIVE_GATE_KEYS.get(section, frozenset())
            for key in sorted(set(body) & allowed):
                path = f"config.engine.gates.{section}.{key}"
                if key in qualitative:
                    # 定性文本键:只要求非空字符串,⛔ 不过 provenance 闸(它不是阈值)。
                    value = body[key]
                    if not isinstance(value, str) or not value.strip():
                        errors.append(
                            f"{path} 必须是非空字符串(定性准则,进 prompt 不进判据;"
                            "⛔ 不是 {value, provenance} 阈值叶子)"
                        )
                    continue
                errors.extend(_validate_provenance_leaf(path, body[key]))

    tier_evidence = engine.get("tier_evidence")
    if not isinstance(tier_evidence, dict):
        errors.append("config.engine.tier_evidence 必须是对象(t1/t2)")
    else:
        missing_tiers = sorted(set(_TIER_EVIDENCE_TIERS) - set(tier_evidence))
        if missing_tiers:
            errors.append(f"config.engine.tier_evidence 缺档位段:{missing_tiers}")
        unknown_tiers = sorted(set(tier_evidence) - set(_TIER_EVIDENCE_TIERS))
        if unknown_tiers:
            errors.append(f"config.engine.tier_evidence 出现未知档位段:{unknown_tiers}")
        for tier_key in _TIER_EVIDENCE_TIERS:
            body = tier_evidence.get(tier_key)
            if body is None:
                continue
            if not isinstance(body, dict):
                errors.append(f"config.engine.tier_evidence.{tier_key} 必须是对象")
                continue
            allowed_keys = set(_TIER_EVIDENCE_LEAF_KEYS)
            if tier_key in _TIER_EVIDENCE_POLICY_TIERS:
                allowed_keys.add(_TIER_EVIDENCE_POLICY_KEY)
            unknown_keys = sorted(set(body) - allowed_keys)
            if unknown_keys:
                errors.append(
                    f"config.engine.tier_evidence.{tier_key} 出现白名单外的阈值键:"
                    f"{unknown_keys}(允许:{sorted(allowed_keys)})"
                )
            for key in sorted(set(body) & _TIER_EVIDENCE_LEAF_KEYS):
                errors.extend(
                    _validate_provenance_leaf(f"config.engine.tier_evidence.{tier_key}.{key}", body[key])
                )
            # V2.4.0 P1.6:定档策略键 —— 枚举字符串,⛔ 不过 provenance 闸(见常量注释)。
            if _TIER_EVIDENCE_POLICY_KEY in body:
                path = f"config.engine.tier_evidence.{tier_key}.{_TIER_EVIDENCE_POLICY_KEY}"
                if tier_key not in _TIER_EVIDENCE_POLICY_TIERS:
                    errors.append(
                        f"{path} 只允许写在 {sorted(_TIER_EVIDENCE_POLICY_TIERS)} 段"
                        "(T1 由结构条件定档,不吃这个开关,K8 §八)")
                elif body[_TIER_EVIDENCE_POLICY_KEY] not in _TIER_EVIDENCE_POLICY_VALUES:
                    errors.append(
                        f"{path} 取值非法:{body[_TIER_EVIDENCE_POLICY_KEY]!r}"
                        f"(仅允许 {sorted(_TIER_EVIDENCE_POLICY_VALUES)};"
                        "⛔ 缺键才是「沿用旧行为」,别写第二个值 —— 加一个策略值等于给"
                        "定档发明一条新规则,那要用户拍板)")
    return errors


def validate_config(config: Any, *, line_code: str = _LINE_DEFAULT) -> List[str]:
    """config 校验,**按版本线分两套 schema**(V2.2-①,交叉规则见模块头):

    - `V` / `LEGACY`(缺省):必需 `seeds`(原语名 → 参数,键必须是已注册原语,
      值按该原语 `params_schema` 校验)与 `tier`(`weights` 非空对象 + `dims` 非空
      数组 + 可选 `stage_scores` / `quality_lines`)两段;`V` 额外禁止 `engine` 段
      (LEGACY 不加新禁令——历史行为原样保留,那两行反正已被 engine_api 闸挡死)。
    - `C` / `Z` / `Y`:走 `_validate_engine_config`(engine 段 + provenance 闸)。
    """
    if not isinstance(config, dict):
        return ["config 必须是 JSON 对象"]
    if line_code in _ENGINE_LINE_CODES:
        return _validate_engine_config(config, line_code)

    errors: List[str] = []
    if line_code == "V" and "engine" in config:
        errors.append(
            "骨架线(line_code=V)的 config 不许出现 engine 段"
            "(骨架/引擎两套 schema 交叉校验,⛔ 不混——plan §五 ① 原文)"
        )

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

    # V2.2-②:行情状态层阈值段(可选;存在即校验形状,见 `_validate_regime`)。
    regime = config.get("regime")
    if regime is not None:
        errors.extend(_validate_regime(regime))
    # V2.3.2-④-A:四分类分界线 + 关口闸门模式对账表(均可选;存在即校验形状)。
    iteration = config.get("iteration")
    if iteration is not None:
        errors.extend(_validate_iteration(iteration))
    governance = config.get("threshold_governance")
    if governance is not None:
        errors.extend(_validate_threshold_governance(governance))
    return errors


def validate_pack_doc(doc: Any) -> List[str]:
    """闸 1(schema + V2.2-① 版本线交叉校验 + provenance)+ 闸 2 一部分
    (engine_api_version 兼容)的组合入口。返回空列表 = 通过。**结构错误时不再
    往下核对兼容性**(避免在 `manifest` 都不是字典时去 `.get()` 报一堆无意义的
    连锁错误)。config 按 `manifest.line_code` 分线校验;line_code 本身非法时
    (validate_manifest 已报错)config 按缺省 LEGACY 校验,不放大连锁错误。"""
    if not isinstance(doc, dict):
        return ["包文件顶层必须是 JSON 对象(含 manifest / config 两个键)"]
    manifest = doc.get("manifest")
    config = doc.get("config")
    line_code = manifest_line_code(manifest)
    if line_code not in _LINE_CODES:
        line_code = _LINE_DEFAULT
    errors = validate_manifest(manifest) + validate_config(config, line_code=line_code)
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
    # V2.2-① 两个新列(默认值 = DB 列 DEFAULT,老行/直接构造的测试替身同口径)。
    line_code: str = _LINE_DEFAULT
    status: str = "running"

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

    def regime_config(self) -> Dict[str, Any]:
        """`config.regime`(V2.2-② 新增可选段:行情状态层五个判定阈值,每键
        `{value, provenance}` 叶子)。缺省返回空字典 —— 逐键回退引擎默认是
        `scan/regime.py::resolve_regime_thresholds()` 的职责,不在本访问器里猜。"""
        return dict(self.config.get("regime", {}))


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
        line_code=row[9],
        status=row[10],
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


# 现役包缓存:**按 `((db_path, line_code), pack_version)` 失效**。db_path 分桶的
# 既有理由:单纯按版本号做全局缓存,在多个 DB 文件里恰好用了同一个 pack_version
# 字符串时(测试隔离下这完全可能:不同测试各自的 tmp db 都装同一份包文件)会把
# A 库的 Pack 对象错误地喂给 B 库的调用方。**V2.2-① 追加 line_code 分桶**:四条
# 线并跑后同一个库同时有多个"现役",只按 db 分桶会读 V 之后读 C 互相顶掉缓存 →
# **静默返回错误的线**。同一桶内才谈"pack_version 没变就不用重新反序列化 JSON"
# 这层优化(`init_schema`/一次 SELECT 仍然每次都做,只省 `json.loads` + 造对象)。
_ACTIVE_PACK_CACHE: Dict[Tuple[str, str], Tuple[str, Pack]] = {}


def get_active_line(line_code: str, db_path: Optional[Path] = None) -> Optional[Pack]:
    """**读某条版本线现役行的唯一实现**(V2.2-①,plan §五 ① 读侧 API 定死,⛔ 别
    自创第五个入口)。无该线现役行 → `None`,调用方各自决定降级(无骨架线现役 =
    当日不产任何种子;无运行引擎 = 当日不产任何候选,如实披露,不许现造默认包)。
    `line_code` 必须 ∈ `_LINE_CODES`,否则 `ValueError`(fail loud,防手滑传
    `'v'`/`'c1'` 之类静默查空)。"""
    if line_code not in _LINE_CODES:
        raise ValueError(f"line_code 取值非法:{line_code!r}(仅允许 {list(_LINE_CODES)})")
    init_schema(db_path)
    key = _cache_key(db_path, line_code)
    with connection(db_path) as conn:
        # 取两行只为**能发现异常**(🔵 B3,per-line 版):库级 partial unique index
        # `(line_code, is_active)` 之后「同线两行现役」已经进不来,但索引换代前的老库
        # 可能有历史遗留 —— 那时候静默取一行等于让「今天这条线用的是哪个包」看运气
        # (包版本是判定输入与归因分层键)。`pack_version DESC` 是确定性 tie-break。
        rows = conn.execute(
            f"SELECT {_PACK_COLUMNS} FROM selection_packs "
            "WHERE is_active=1 AND line_code=? "
            "ORDER BY created_at DESC, pack_version DESC LIMIT 2",
            (line_code,),
        ).fetchall()
    if len(rows) > 1:
        logger.warning(
            "[pack] selection_packs 线 %s 出现 %d 行 is_active=1(只可能来自手工 SQL / "
            "老库遗留)—— 本次按 (created_at, pack_version) 降序取 %r,**请人工核对并把"
            "多余的行置 0**;现役包版本是判定输入与归因分层键,含糊不得。",
            line_code, len(rows), rows[0][0],
        )
    row = rows[0] if rows else None
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


def get_active_skeleton(db_path: Optional[Path] = None) -> Optional[Pack]:
    """骨架线(`V`)现役行 = `get_active_line("V")`(纯别名,K8 §一「系统骨架管理
    股票池、篮子、梯度」的读入口)。"""
    return get_active_line("V", db_path)


def get_active_engines(db_path: Optional[Path] = None) -> Dict[str, Pack]:
    """现役**运行中**引擎线(V2.2-①):`line_code ∈ {C,Z,Y}` 且 `is_active=1` 且
    `status='running'`,返回**按 C → Z → Y 确定性排序**的有序字典(顺序由本函数的
    `_ENGINE_LINE_CODES` 元组钉死,⛔ 不靠 SQL 行序 —— 行序随库文件/插入历史漂,
    而引擎遍历顺序会影响任何"先到先得"式消费逻辑的可复现性)。

    `status='stopped'` 的线**不出现在返回值里**,但它仍是该线的现役版本
    (`get_active_line` 照常返回它)—— K8 §四「引擎状态」:停止 = 不产候选,
    保留历史版本与复盘数据;"现役版本是谁"与"现在产不产候选"是两个问题。

    ⚠ **`status` 本版只由建表 `DEFAULT 'running'` 落位,无任何切换入口**(⛔ 无
    CLI / 无端点 / 无写函数)——要停一条引擎线只能手工 SQL。这是**用户 2026-08-09
    的裁定**(「引擎线不必设计得那么完善,要开关就让它默认的放在那里」),⛔ 不是
    遗漏,别"顺手补全"。读侧仍尊重该列,是为了将来真需要开关时不必改读侧。"""
    out: Dict[str, Pack] = {}
    for code in _ENGINE_LINE_CODES:
        p = get_active_line(code, db_path)
        if p is not None and p.status == "running":
            out[code] = p
    return out


def get_active_pack(db_path: Optional[Path] = None) -> Optional[Pack]:
    """读现役策略包 = **K8 起返回骨架线(`line_code='V'`)现役行**
    (`get_active_line("V")` 的薄封装,V2.2-① 语义换血,`ENGINE_API_VERSION`
    1→2 的判定依据正是这一条)。

    为什么这样映射恰好正确、且既有调用方零改动(plan §五 ① 原文):K8 §一 明写
    「系统骨架管理**股票池、篮子、梯度**」,而现有包 config 的两段正是 `seeds`
    (股票池 + 种子资格)与 `tier`(梯度容量/权重/质量线)—— `scan/seeds.py` 与
    `selection/tier.py` 继续读本函数,语义反而更准了。⛔ 别把它们改成读引擎线,
    引擎线里根本没有 seeds/tier 段。

    无骨架线现役(含「库里只有 LEGACY 现役行」的割接前状态)→ `None` = 当日不产
    任何种子/Tier,如实披露,不许现造默认包(既有 docstring 纪律原文)。"""
    return get_active_line("V", db_path)


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
      3. 若目标已是**它那条线的**当前现役包(V2.2-① 起现役唯一性按 `line_code`
         分线)→ 不追加任何事件(与 `scripts/activate_pack.py` 的 CLI 层"已现役、
         无需激活"提前拦截一致;直接调用本函数〔绕过 CLI 提前检查〕重复以同版本
         激活同样保持幂等,不产生冗余事件)。
      4. 否则:若**同线**存在其它现役包 → 先给它追加一条 `deactivate` 事件 + 置
         `is_active=0`;再给目标追加一条 `activate` 事件 + 置 `is_active=1`
         `activated_at=now()`。**该线首次激活(此前无该线现役包)只有后半段**——
         没有"旧行"可关,不伪造一条 deactivate 事件。**其它线的现役行一概不碰**
         (陷阱 #1:全表口径的切换会让"激活 C1"静默把骨架线 V 踢下去)。

    `via`:`"cli"`(`scripts/activate_pack.py --confirm`)或 `"seed"`(测试/未来
    预填充脚本,同 `strategy_activation_log.via` 既有取值风格)。

    **不做 schema 校验之外的业务校验**(如"必须比现役更好")——策略包没有章程
    切换器那种"核心值核对"概念(章程的核心值是固定拍板的几个数,包的参数本就
    是每次都可能不同的调参对象),`scripts/activate_pack.py` 的闸 1-3 已经把
    「schema 合法 + 原语白名单 + engine_api 兼容 + 人读 diff」都过了一遍,本函数
    只管落库这一步的原子性与幂等性。

    🔴 **V2.4.0 P4.3 起本函数是 `_activate_one()` 的薄壳**(§3.14-F):事务体抽出去
    给四线原子激活 `activate_pack_set()` 复用,**行为逐位不变**(单测正面锁死)。
    ⛔ 别把激活逻辑复制第二份 —— 那正是「两个入口慢慢长歪」的经典起点。"""
    errors = validate_pack_doc({"manifest": manifest, "config": config})
    if errors:
        raise ValueError("包 schema 校验未通过,拒绝激活:" + "; ".join(errors))

    pack_version = manifest["pack_version"]
    init_schema(db_path)
    with connection(db_path) as conn:
        # `batch_id=None`:单包激活**不属于任何原子批次**(⛔ 不编一个假批次号)。
        _activate_one(conn, manifest, config, via=via, batch_id=None)

    _invalidate_active_cache(db_path)
    activated = get_pack(pack_version, db_path=db_path)
    assert activated is not None and activated.is_active
    return activated


def _activate_one(
    conn: sqlite3.Connection,
    manifest: Dict[str, Any],
    config: Dict[str, Any],
    *,
    via: str,
    batch_id: Optional[str] = None,
) -> str:
    """**在调用方给的连接与事务里**落一个包的登记 + 切换(V2.4.0 P4.3 从
    `activate_pack` 抽出,§3.14-F)。返回该包的 `pack_version`。

    🔴 **本函数刻意不 commit、不清缓存、不 `init_schema`** —— 那三件事归调用方:
    单包走 `activate_pack`,四线原子批走 `activate_pack_set`。**「谁提交」这件事只
    能有一个答案**,否则批量激活里某一包自己 commit 了,后面那包失败就回滚不掉,
    留下**半激活状态**(正是 P4.3 要根除的东西)。

    `batch_id`:属于同一次原子批次的事件共享一个,单包激活传 `None`(落库 NULL)。

    行为语义(逐条对齐 `activate_pack` 的 docstring 1–4 条,一字未改):同版本号
    内容不同 → `ValueError`(append-only);目标已是**同线**现役 → 幂等 no-op、不追加
    事件;否则同线旧行先 deactivate 再 activate,**其它线一概不碰**。"""
    errors = validate_pack_doc({"manifest": manifest, "config": config})
    if errors:
        # 防御性复核(调用方本就该先校验过):批量入口的"四包全部通过校验后才切换"
        # 是在**进事务之前**做的,这里是第二道,防有人日后直接调本函数。
        raise ValueError("包 schema 校验未通过,拒绝激活:" + "; ".join(errors))

    pack_version = manifest["pack_version"]
    line_code = manifest_line_code(manifest)
    manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
    evidence_ref_text = _join_evidence_ref(list(manifest["evidence_ref"]))
    now = _now()

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
        # `status` 刻意不出现在 INSERT 列里 —— 只由 DDL `DEFAULT 'running'` 落位
        # (本版无任何 status 切换入口,见 `get_active_engines` docstring 的裁定)。
        conn.execute(
            "INSERT INTO selection_packs "
            "(pack_version, name, engine_api_version, manifest_json, config_json, "
            " evidence_ref, is_active, created_at, activated_at, line_code) "
            "VALUES (?,?,?,?,?,?,0,?,NULL,?)",
            (
                pack_version, manifest["name"], manifest["engine_api_version"],
                manifest_json, config_json, evidence_ref_text, now, line_code,
            ),
        )

    # 🔴 V2.2-①:现役查找与切换**必须按 line_code 分线**(plan 点名的陷阱 #1)——
    # 全表口径下"激活 C1"会把骨架线 V 踢下去,闸全过、库不报错,**静默**。
    prior_row = conn.execute(
        "SELECT pack_version FROM selection_packs WHERE is_active=1 AND line_code=?",
        (line_code,),
    ).fetchone()
    prior_version = prior_row[0] if prior_row is not None else None

    if prior_version != pack_version:
        if prior_version is not None:
            # WHERE 带 line_code 双保险(pack_version 本身 UNIQUE,加线号是防
            # "prior 查询与 UPDATE 之间语义漂移"的一致性钉子,plan 陷阱 #1 同源)。
            conn.execute(
                "UPDATE selection_packs SET is_active=0 WHERE pack_version=? AND line_code=?",
                (prior_version, line_code),
            )
            conn.execute(
                "INSERT INTO selection_pack_activation_log "
                "(pack_version, action, via, note, at, batch_id) "
                "VALUES (?,?,?,?,?,?)",
                (prior_version, "deactivate", via, f"由 {pack_version} 取代", now, batch_id),
            )
        conn.execute(
            "UPDATE selection_packs SET is_active=1, activated_at=? WHERE pack_version=?",
            (now, pack_version),
        )
        conn.execute(
            "INSERT INTO selection_pack_activation_log "
            "(pack_version, action, via, note, at, batch_id) "
            "VALUES (?,?,?,?,?,?)",
            (pack_version, "activate", via, "", now, batch_id),
        )
    # else: 目标已是现役 —— 幂等 no-op,不追加事件。
    return pack_version


def _invalidate_active_cache(db_path: Optional[Path]) -> None:
    """缓存失效:**整库清**(该 db 下所有线的桶一起清,plan 陷阱 #2 给的两个合法
    选项之一;选整库而不是按线,因为便宜且绝不会漏 —— 单包激活只动一条线,但"少清"
    的代价是静默读旧,"多清"的代价只是一次 json.loads)。

    ⚠ V2.4.0 P4.3 抽成独立函数:四线原子激活一次动四条线,**必须整库清一次**
    (施工图 P4.3 末条),而"整库清"这件事只能有一份实现。"""
    db_key = _db_cache_key(db_path)
    for key in [k for k in _ACTIVE_PACK_CACHE if k[0] == db_key]:
        _ACTIVE_PACK_CACHE.pop(key, None)


@dataclass(frozen=True)
class PackSetActivation:
    """`activate_pack_set()` 的结果(P4.3 要求「输出旧版本集合和新版本集合」)。

    `before`/`after` 是**整表现役快照**(`line_code → pack_version`),不只是本批
    动过的那几条 —— 「没动的那两条线现在是谁」同样是运维要核对的事实。"""

    batch_id: str
    before: Dict[str, str]
    after: Dict[str, str]
    activated: Tuple[str, ...]          # 本批按落库顺序的 pack_version


def _active_map(db_path: Optional[Path]) -> Dict[str, str]:
    """`{line_code: pack_version}` 现役快照(按 `_LINE_CODES` 确定性排序)。"""
    actives = {p.line_code: p.pack_version for p in list_packs(db_path=db_path) if p.is_active}
    order = {code: i for i, code in enumerate(_LINE_CODES)}
    return dict(sorted(actives.items(), key=lambda kv: order.get(kv[0], len(order))))


def activate_pack_set(
    docs: List[Dict[str, Any]],
    *,
    via: str = "cli-set",
    db_path: Optional[Path] = None,
    batch_id: Optional[str] = None,
) -> PackSetActivation:
    """**四线原子激活**(V2.4.0 P4.3;K8 §十九「激活与回滚」逐字:骨架与三引擎是
    协调升级,不能分别激活后留下临时混合态)。

    `docs` = `load_pack_file()` 返回的那种 `{"manifest":…, "config":…}` 列表。

    铁律(逐条对应施工图 P4.3):
      * **一个 SQLite 事务**:`with connection(...)` 里连调 `_activate_one`,
        任一包抛错 → 整个事务不 commit → **四线全部维持原值**,⛔ 不留半激活状态。
      * **全部通过校验后才切换**:schema / 原语白名单 / `engine_api_version` /
        线号唯一性**全在进事务之前**跑完(闸门本身在 `scripts/activate_pack_set.py`,
        本函数只再核一遍 schema 与线号唯一性 —— 它是库侧最后一道)。
      * **共享 `batch_id`**:本批写进 `selection_pack_activation_log` 的每一条事件
        (含被取代的旧包那条 `deactivate`)都带同一个批次号。
      * **持仓章程不参与本事务**:本模块全程不 import `strategy.brain`、不碰
        `strategy_versions`(既有纪律,§五 V2-③「插槽边界」)—— 这条由 import 结构
        保证,不靠自觉。

    ⛔ **同一条线不许在一批里出现两次**:那等于"批内自己把自己顶掉",事件流会写出
    一条谁也解释不清的 deactivate。fail loud。"""
    if not docs:
        raise ValueError("activate_pack_set:docs 为空,没有要激活的包(fail loud,不静默 no-op)")

    errors: List[str] = []
    for i, doc in enumerate(docs):
        for e in validate_pack_doc(doc):
            errors.append(f"docs[{i}]:{e}")
    if errors:
        raise ValueError("包 schema 校验未通过,拒绝激活(**四包全部通过才切换**):" + "; ".join(errors))

    by_line: Dict[str, Dict[str, Any]] = {}
    for doc in docs:
        line = manifest_line_code(doc["manifest"])
        if line in by_line:
            raise ValueError(
                f"activate_pack_set:同一条线 {line!r} 在一批里出现两次"
                f"({by_line[line]['manifest']['pack_version']} / {doc['manifest']['pack_version']})"
                " —— 批内自己顶掉自己,拒绝执行。"
            )
        by_line[line] = doc

    if batch_id is None:
        # 🔵 **复审 🔵-4:这个 `12` 没有任何领域出处** —— 它**不是判据、不参与任何比较**,
        # 只是「一次原子激活的批次名」的长度,取 12 位十六进制(48 bit)纯粹是为了在
        # `journalctl` 与 `SELECT DISTINCT batch_id` 里一眼看得完。⛔ 别把它当阈值读。
        # 格式由 `_BATCH_ID_RE` 锁住(守门单测按它断言),换长度要连守门一起换。
        batch_id = f"set-{uuid.uuid4().hex[:_BATCH_ID_HEX_LEN]}"

    init_schema(db_path)
    before = _active_map(db_path)

    # 落库顺序按 `_LINE_CODES` 钉死(V → C → Z → Y → LEGACY),**与结果无关但可复现**:
    # 四条线互不干扰(每条只碰自己 line_code 的行),且全在**同一个事务**里,
    # 外部观察者永远看不到中间态。⛔ 别改成依赖 `docs` 传入顺序 —— 那会让事件流的
    # id 次序随调用方漂,审计时对不上。
    order = {code: i for i, code in enumerate(_LINE_CODES)}
    ordered = sorted(by_line.items(), key=lambda kv: order.get(kv[0], len(order)))

    activated: List[str] = []
    with connection(db_path) as conn:
        for _line, doc in ordered:
            activated.append(
                _activate_one(conn, doc["manifest"], doc["config"], via=via, batch_id=batch_id)
            )

    _invalidate_active_cache(db_path)
    after = _active_map(db_path)
    return PackSetActivation(
        batch_id=batch_id, before=before, after=after, activated=tuple(activated),
    )


__all__ = [
    "Pack",
    "validate_manifest",
    "manifest_line_code",
    "validate_config",
    "validate_pack_doc",
    "load_pack_file",
    "list_packs",
    "get_pack",
    "get_active_line",
    "get_active_skeleton",
    "get_active_engines",
    "get_active_pack",
    "activate_pack",
    "activate_pack_set",
    "PackSetActivation",
]
