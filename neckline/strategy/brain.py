"""策略大脑(版本化规则库,plan 1.9 / §2.6「策略进化带笼子」)。

把「过堂后写死」的规则参数快照 + 变更日志 + 定版回测指标落 SQLite `strategy_versions`。
调参门禁(§2.6)后续常态运行时,新版须过 walk-forward 样本外跑赢现役版本 + 用户批准
才可 `activate`。本模块只管**读写与激活**,不做门禁判定(那是后续机制)。

rule 是纯参数字典(可直接喂 `MomentumConfig(**rule["config"])`),不含代码——同码三跑道
的执行逻辑在 `momentum.py`,大脑只存「用哪套参数」。

**v1.2-A 激活时间线(历史洗白修复)**:`activated_at` 记录每个版本「成为现役」的时刻,
`config_active_at(ref_date)` 据此解析「某历史日当时的 governing 版本」;**周复盘按周判纪律
一律走 `config_governing_for_week(week_start)`**(判据「激活日 < week_start」= 激活当周仍按
旧章程判,2026-07-27 审计 🟡-3 修复),避免用今天的章程(如 single_cap 4 万)重判历史周把
当初超限的违纪洗白掉(见 `review/reconcile.py::run_weekly_review`)。激活 = 系统 v 字头章程
修订也走这张表(config 承 K 血缘、仅改仓位字段),不占 K 命名空间。

**v1.4-⑥-A 时刻粒度(周复盘逐笔判)**:`config_governing_at(ts)` 把时间线解析下沉到
**时刻**,供周复盘「成交时刻早于激活时刻按旧章程、之后按新」逐笔取 config
(§七 P1-4;`review/reconcile.py`)。三个解析器同一条时间线、三种粒度,各有其位:
    · `config_active_at(date)` —— 日粒度时点原语(**周复盘不要直接用**,见其 docstring);
    · `config_governing_for_week(week_start)` —— 周粒度(判据「激活日 < week_start」);
      ⑥-A 之后周复盘的**逐笔判据**已换成 `config_governing_at`,本函数仍是「这一周整体
      归属哪版章程」的标签口径(`WeeklyReview.strategy_version` 的语义未变);
    · `config_governing_at(ts)` —— **时刻粒度**(判据「激活时刻 ≤ ts」,等于算新章程)。
**时区**:`activated_at` 由本模块 `_now()` 写,恒为 **UTC** ISO8601(`+00:00`);而成交时刻
是**北京时间**。两者必须归一到同一条时间轴再比 —— 归一规则写死在 `_activated_instant`
与 `config_governing_at` 的 docstring 里,调用方不要自己 strip 时区凑合比。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from neckline.db import connection, init_schema

_BASE_COLS = "version, created_at, rule_json, changelog, metrics_json, is_active"


def _select_cols(conn: sqlite3.Connection) -> str:
    """读投影:仅当 `activated_at` 列已迁移存在时才带上它(v1.2-A)。**reads 不触发
    迁移**——保持"读不写库"的既有语义(get_active/list_versions 从不 ALTER),未迁移
    的老库读回 `activated_at=None`(见 `_row_to_version` 的 len 守卫),`config_active_at`
    落 legacy 兜底 = 与 v1.2 之前完全一致。否则裸 `SELECT ..., activated_at` 会在未迁移
    库上炸 `no such column: activated_at`(该模块读入口不 init_schema,不能假设列已加)。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_versions)")}
    return _BASE_COLS + (", activated_at" if "activated_at" in cols else "")


@dataclass
class StrategyVersion:
    version: str
    created_at: str
    rule: Dict
    changelog: str
    metrics: Dict
    is_active: bool
    activated_at: Optional[str] = None   # ISO8601;None=从未激活过(v1.2-A 新增)


def _row_to_version(row: sqlite3.Row) -> StrategyVersion:
    return StrategyVersion(
        version=row[0],
        created_at=row[1],
        rule=json.loads(row[2]),
        changelog=row[3],
        metrics=json.loads(row[4]) if row[4] else {},
        is_active=bool(row[5]),
        activated_at=row[6] if len(row) > 6 else None,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _activated_date(v: StrategyVersion) -> Optional[date]:
    """把 `activated_at`(ISO8601 时间戳或纯日期串)取到 date 粒度,供 `config_active_at`
    按周(week_end 是 date)比较。解析不了 → None(视作无效激活戳,不参与时间线)。

    ⚠ **取的是 UTC 日期**(不换北京日期)——这是 v1.2-A 起的既有语义,`config_active_at` /
    `config_governing_for_week` 的判据与单测都建立在它之上,**不要"顺手修正"成本地日期**
    (为什么在 `<` 判据下这不制造洗白口,见 `config_governing_for_week` 的「时区注记」)。
    需要真正的时刻比较请用 `_activated_instant` / `config_governing_at`(v1.4-⑥-A)。"""
    if not v.activated_at:
        return None
    try:
        return datetime.fromisoformat(v.activated_at).date()
    except ValueError:
        try:
            return date.fromisoformat(v.activated_at[:10])
        except ValueError:
            return None


def _activated_instant(v: StrategyVersion) -> Optional[datetime]:
    """把 `activated_at` 解析成 **tz-aware 时刻**(v1.4-⑥-A 逐笔判纪律的基础)。

    **时区归一(写死,不许改)**:
      · 带时区偏移的串(生产唯一写入者 `_now()` 写的就是 `...+00:00`)→ **原样保留其时区**,
        比较时由 Python 自行跨时区换算(aware vs aware 比较是按绝对时刻,正确)。
      · **不带时区**的串(手工 SQL 补的、老库遗留)→ **按 UTC 解读**。理由:本表 `activated_at`
        的**唯一写入者**是 `_now()`,它写的就是 UTC;把 naive 戳当北京时间读会凭空把激活时刻
        往前挪 8 小时 = 拿一个从没发生过的激活时点去判纪律。**注意与 `config_governing_at`
        的入参约定刻意相反**(那边的 naive 是"市场时刻"故按北京时间读)——两个 naive 的来源
        不同、约定就不同,各自在 docstring 里定死,不"统一"。
      · 纯日期串 `'YYYY-MM-DD'` → 该日 00:00 UTC。
      · 解析不了 → `None`(视作无效激活戳,不参与时间线;与 `_activated_date` 同款诚实降级)。
    """
    if not v.activated_at:
        return None
    try:
        dt = datetime.fromisoformat(v.activated_at)
    except ValueError:
        d = _activated_date(v)
        return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) if d else None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _as_aware(ts: datetime) -> datetime:
    """入参时刻归一:naive → **按北京时间**(`CN_TZ`)解读。

    调用方(周复盘逐笔判)手上的 naive datetime 一律是「市场时刻」(交割单成交时刻 /
    该日收盘时刻),不是 UTC —— 与 `_activated_instant` 对 naive 的相反约定见那边 docstring。
    正常路径调用方应直接传 aware 时刻(`reconcile.trade_instant` 就是这么造的),本函数
    只是防御性兜底,不鼓励依赖。"""
    from neckline.calendar import CN_TZ

    return ts if ts.tzinfo is not None else ts.replace(tzinfo=CN_TZ)


def save_version(
    version: str,
    rule: Dict,
    changelog: str,
    metrics: Optional[Dict] = None,
    activate: bool = True,
    db_path: Optional[Path] = None,
) -> StrategyVersion:
    """写入(或覆盖同名)一个策略版本。`activate=True` 时把它设为唯一现役版本
    (其余版本 is_active 置 0)并 stamp `activated_at=now()`(v1.2-A 向后兼容:既有
    `activate=True` 调用点自动获得激活时间戳)。幂等:同 version 再写覆盖参数与日志。

    **`activated_at` 保全(v1.2-A 关键)**:`INSERT OR REPLACE` 会先删后插,若不显式
    携带 `activated_at`,既有行的激活戳会被抹成 NULL——故 `activate=False` 覆盖时读回
    旧 `activated_at` 原样带回(不臆造、不抹掉历史激活戳),只有 `activate=True` 才
    stamp 新戳。

    **「全库无现役版本」硬护栏(2026-07-27 审计 🔵-8)**:对**当前现役**版本调
    `save_version(..., activate=False)` 会把 `is_active` 抹成 0 → 全库无现役 →
    `active_config()` 返 `{}` → 哨兵 / 报告 / entry-suggestion **全线静默退回
    `MomentumConfig` 字段默认**(max_hold_days=3、无回落止盈、单笔 2 万),只留一条
    warning 日志。这是纪律层面的极危险状态(整套章程凭空换成一组从没人拍板过的默认值),
    故直接 `ValueError` 拒绝:要改现役版本的参数就带 `activate=True`(保持现役),要换
    现役版本就走 `activate_version`。"""
    init_schema(db_path)
    created = _now()
    with connection(db_path) as conn:
        prior = conn.execute(
            "SELECT activated_at, is_active FROM strategy_versions WHERE version=?", (version,)
        ).fetchone()
        prior_activated = prior[0] if prior else None
        if not activate and prior is not None and bool(prior[1]):
            raise ValueError(
                f"拒绝把现役版本 {version} 覆盖成非现役(activate=False)——会造成「全库无现役版本」,"
                f"哨兵/报告/entry-suggestion 全线静默退回 MomentumConfig 默认值(hold=3、无回落止盈、"
                f"单笔 2 万),极危险。改现役参数请带 activate=True;换现役版本请用 activate_version()。"
            )
        activated_at = created if activate else prior_activated
        conn.execute(
            "INSERT OR REPLACE INTO strategy_versions "
            "(version, created_at, rule_json, changelog, metrics_json, is_active, activated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (version, created, json.dumps(rule, ensure_ascii=False),
             changelog, json.dumps(metrics or {}, ensure_ascii=False),
             1 if activate else 0, activated_at),
        )
        if activate:
            conn.execute("UPDATE strategy_versions SET is_active=0 WHERE version<>?", (version,))
    return get_version(version, db_path=db_path)  # type: ignore[return-value]


def activate_version(version: str, db_path: Optional[Path] = None) -> StrategyVersion:
    """把 `version` 设为唯一现役版本:置其 `is_active=1` + stamp `activated_at=now()`、
    其余版本 `is_active=0`(**但保留它们的 `activated_at`** —— 那是历史激活时间线,
    洗白修复靠它按周解析当时 governing 版本,绝不清空)。v1.2-A 切换器脚本
    (`scripts/activate_charter.py --confirm`)的唯一激活入口;策略大脑激活不暴露给
    客户端(§3.8 系统内核永不被客户端改)。版本不存在 → `ValueError`。

    注:`activated_at` 每次激活都刷新为 now()(照 plan A.3 写死语义)。正常 staged 流程
    只激活一次;若回滚重激活旧版本会把其激活戳前移(边角情形,不在本块生效路径)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT version FROM strategy_versions WHERE version=?", (version,)
        ).fetchone()
        if row is None:
            raise ValueError(f"策略版本 {version} 不存在,无法激活(先 save_version 落库)。")
        conn.execute(
            "UPDATE strategy_versions SET is_active=1, activated_at=? WHERE version=?",
            (now, version),
        )
        conn.execute("UPDATE strategy_versions SET is_active=0 WHERE version<>?", (version,))
    return get_version(version, db_path=db_path)  # type: ignore[return-value]


def get_version(version: str, db_path: Optional[Path] = None) -> Optional[StrategyVersion]:
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_select_cols(conn)} FROM strategy_versions WHERE version=?", (version,)
        ).fetchone()
    return _row_to_version(row) if row else None


def get_active(db_path: Optional[Path] = None) -> Optional[StrategyVersion]:
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_select_cols(conn)} FROM strategy_versions "
            "WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return _row_to_version(row) if row else None


def config_active_at(ref_date: date, db_path: Optional[Path] = None) -> Optional[StrategyVersion]:
    """解析 `ref_date` **当天**(时点语义)governing 的策略版本(v1.2-A 历史洗白修复的
    时间线解析器)。

    ⚠ **周复盘判纪律不要直接用本函数**(2026-07-27 审计 🟡-3):按周判必须用
    `config_governing_for_week(week_start)`,否则「激活日 ≤ week_end」会把**刚结束那一周**
    交给新章程判、洗白该周在旧章程下的违纪。本函数保留作时点原语(它自身语义是对的)。

    语义(写死,不许改):
      · 取所有 `activated_at` 非空的版本,按激活日升序;
      · governing = 激活日 <= ref_date 的最后一个;
      · ref_date 早于所有激活日 → 取**最早激活**的版本(不臆造更早历史,用已知最早
        版本判深过去);
      · **整表无任何 `activated_at`(纯 legacy 老库,如无 is_active 行的隔离测试库)
        → 退回 `get_active()` = 与 v1.2 之前旧行为完全一致**(当前现役判全部周)。

    生产因一次性回填(`db.py::_backfill_activated_at`)保证现役 K1 有 `activated_at`,
    永远走时间线解析、不落 legacy 兜底。
    """
    stamped = [v for v in list_versions(db_path=db_path) if _activated_date(v) is not None]
    if not stamped:
        return get_active(db_path=db_path)
    stamped.sort(key=lambda v: _activated_date(v))  # type: ignore[arg-type,return-value]
    candidates = [v for v in stamped if _activated_date(v) <= ref_date]  # type: ignore[operator]
    return candidates[-1] if candidates else stamped[0]


def config_governing_for_week(
    week_start: date, db_path: Optional[Path] = None
) -> Optional[StrategyVersion]:
    """解析某 ISO 周(以 `week_start` 标识)**整周**该按哪版章程判纪律。**周复盘唯一入口。**

    **判据 = 激活日 `<` week_start**(2026-07-27 独立审计 🟡-3 修复,方案 (a),用户倾向):
    章程**激活当周仍按旧章程判**,新章程从**下一个完整 ISO 周**起 govern。

    修复的是什么:旧判据是「激活日 ≤ week_end」,于是周六/周日(或北京周一凌晨,UTC 戳落
    在周日)跑切换器,**刚刚结束那一周**的 week_end(周日)≥ 激活日 → 整周改由新章程判 →
    该周在旧章程下的违纪(如 K1 单笔 2 万上限下的 3 万买入)被 4 万新上限**整周洗白**
    (审计副本实测:1 条 → 0 条)。周末恰是用户最可能跑切换器的时点。

    为何取 (a) 而不是「`_activated_date` 换 Asia/Shanghai + 流程只在周一~周五盘后激活」:
    **(a) 不依赖人的操作纪律**——无论用户什么时点激活,已经发生过的整周永远按当时的章程判。
    与 staged 语义(清仓后才切,激活当周不再有旧仓跨边界)自洽:激活当周的**已平仓成交**
    确实全发生在旧章程治下,理应按旧章程判。

    时区注记(诚实边界):`_activated_date` 仍取 UTC 日期(比北京日期最多早一天)。在 `<`
    判据下这不再制造洗白口——把激活日往早挪一天,只可能让「激活当周」提前一周被新章程 govern,
    而**已结束的那一周**的 week_start 恒 ≤ 该周内任何一天,`activated < week_start` 仍为假。
    唯一可见效果:北京周一 00:00–08:00 激活(UTC 落周日)→ 本周即按新章程判——那本就是事实
    (激活发生在本周任何一笔成交之前),方向正确。

    实现刻意委托 `config_active_at(week_start − 1 天)`(= 激活日 < week_start),**不另抄一份
    时间线遍历**:legacy 兜底 / 早于所有激活日 / 空库三种边界与时点语义共用同一份实现。
    """
    from datetime import timedelta

    return config_active_at(week_start - timedelta(days=1), db_path=db_path)


def config_governing_at(
    ts: datetime, db_path: Optional[Path] = None
) -> Optional[StrategyVersion]:
    """解析 **某一时刻** governing 的策略版本(v1.4-⑥-A「周复盘按成交时刻逐笔判」的
    时间线解析器,§七 P1-4)。

    **判据(写死,不许改)**:governing = `激活时刻 <= ts` 的**最后一个**版本。
      · **恰好等于激活时刻的成交算「新章程」**(`<=` 而非 `<`)。定死的理由:章程在那一
        刻已经生效,plan §五-⑥-A 原文是「成交时刻**早于**激活时刻按旧章程、之后按新」,
        「等于」不属于「早于」;且与日粒度原语 `config_active_at`(判据「激活日 ≤ ref」)
        同向,两个粒度的边界语义一致,不制造第二套直觉。
      · `ts` 早于所有激活时刻 → 取**最早激活**的版本(不臆造更早的历史章程,用已知最早
        版本判深过去;与 `config_active_at` 同款)。
      · **整表无任何 `activated_at`**(纯 legacy 老库)→ 退回 `get_active()`(与 v1.2 之前
        旧行为一致;同 `config_active_at` 的 legacy 兜底)。

    **时区(⑥-A 重点防的坑)**:`activated_at` 是 **UTC** 戳(`_now()` 写的),而调用方
    手上的成交时刻是**北京时间** —— 两边都归一成 aware datetime 后再比绝对时刻:
    `activated_at` 的归一见 `_activated_instant`,入参 `ts` 的归一见 `_as_aware`
    (naive 入参按北京时间读;正常路径请直接传 aware,别指望兜底)。**差 8 小时的错判
    会直接落到「这笔按哪版章程判」上**,这也是本函数不复用 `_activated_date` 的原因。

    ⚠ 与 `config_governing_for_week` 的分工:后者回答「这一周整体挂哪版章程的名」
    (`WeeklyReview.strategy_version` 标签,判据「激活日 < week_start」不变);本函数回答
    「**这一笔**该按哪版判」。周复盘的**判据入口**自 v1.4-⑥-A 起走本函数。
    """
    ref = _as_aware(ts)
    stamped = [
        (inst, v) for inst, v in
        ((_activated_instant(v), v) for v in list_versions(db_path=db_path))
        if inst is not None
    ]
    if not stamped:
        return get_active(db_path=db_path)
    stamped.sort(key=lambda pair: pair[0])
    candidates = [v for inst, v in stamped if inst <= ref]
    return candidates[-1] if candidates else stamped[0][1]


def activations_between(
    start: datetime, end: datetime, db_path: Optional[Path] = None
) -> List[Tuple[datetime, StrategyVersion]]:
    """区间 **[start, end)** 内发生的激活,按时刻升序返回 `(激活时刻, 版本)`。

    供周复盘回答「本周有没有发生过章程切换、几点切的」(⑥-A 周报分段计数文案)。
    半开区间:周窗口用 `[周一 00:00, 下周一 00:00)` 表达,相邻周不会把同一次激活各算一遍。
    时刻归一同 `config_governing_at`(`activated_at` 按 UTC 读、入参 naive 按北京时间读),
    **切换时刻不另处理时区**——它就是从这条时间线上取出来的那个绝对时刻。
    """
    lo, hi = _as_aware(start), _as_aware(end)
    out = [
        (inst, v) for inst, v in
        ((_activated_instant(v), v) for v in list_versions(db_path=db_path))
        if inst is not None and lo <= inst < hi
    ]
    out.sort(key=lambda pair: pair[0])
    return out


def active_config(db_path: Optional[Path] = None) -> Dict:
    """现役版本的规则参数 `config`(= `MomentumConfig` 落库值)。无现役版本 → `{}`
    (调用方各自套用兜底,见 engine.py `_DEFAULT_STOP_PCT` / api 的 `_active_config`)。
    **单一事实源**:任何要读 `stop_pct` / `max_hold_days` / `single_cap` /
    `take_profit_retrace` 的代码统一走这里,不在别处抄字面量(§3.8 铁律)。"""
    v = get_active(db_path=db_path)
    if v is None:
        return {}
    return dict(v.rule.get("config", {}) or {})


def list_versions(db_path: Optional[Path] = None) -> List[StrategyVersion]:
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_select_cols(conn)} FROM strategy_versions ORDER BY created_at"
        ).fetchall()
    return [_row_to_version(r) for r in rows]


__all__ = [
    "StrategyVersion", "save_version", "activate_version", "get_version",
    "get_active", "config_active_at", "config_governing_for_week", "config_governing_at",
    "activations_between", "active_config", "list_versions",
]
