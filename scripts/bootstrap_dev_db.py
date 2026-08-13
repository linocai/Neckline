#!/usr/bin/env python3
"""**开发 / 临时库 bootstrap**(plan §五 V2.4.0 P4.2:让 v2.4.0 的现役版本集合在本地
可复现)。

**病**:本地 `data/neckline.db` 里 `selection_packs` **一行都没有**、现役章程仍是 `K1`
(2026-08-12 实查)—— 拿它当 v2.4.0 的开发基线,选股链一跑就是「无骨架线现役 = 当日不产
任何种子」,而那**不是** v2.4.0 的行为。本脚本把一个空库(或临时副本)拉到
**`K8-V0.8` + `C2`/`Z2`/`Y2` + 章程 `v2.3-k8`** 这个集合上。

🔴 **三条安全护栏(缺一即拒,fail loud)**
  1. **必须显式 `--db-path`**(⛔ 没有默认值 —— 一个"默认打哪个库"的 bootstrap 脚本
     早晚会有人不带参数跑一次)。
  2. **拒绝生产 / 权威库路径**:`settings.db_path`(仓库 `data/neckline.db`)、
     `/opt/neckline/**`、以及任何名为 `neckline.db` 且位于仓库 `data/` 下的路径。
  3. **⛔ 不复制生产业务数据与凭据**:`--reference-db` 只拷 `_REFERENCE_TABLES` 四张
     **只读参考表**(`trade_cal` / `strategy_versions` / `stock_basic` / `namechange`,
     照 `CLAUDE.md` 既有体例),⛔ **不碰** `app_settings`(LLM api_key / 推送 token)、
     `llm_providers`(api_key)、`positions` / `reports` / `baskets` 等业务表。
     该名单是**白名单**,守门单测按黑名单反向断言(`tests/test_bootstrap_dev_db.py`)。

**单一事实源**:包 = 仓库 `packs/*.json` 四个文件;章程 = **从参考库读出来的那一行**,
或**用既有落行脚本从祖先版本派生**(`charter_v1_3` → `charter_v1_3_3` →
`seed_charter_v22k8` → `seed_charter_v23k8`,与 `tests/test_charter_v23k8.py` 同一条链)
—— 🔴 **本脚本一个章程数值都不手抄**(`CLAUDE.md`「-5% 止损 / 回落止盈 / 仓位纪律唯一源
是 `strategy_versions` 现役行」)。⚠ 派生链住在 `scripts/oneoff/`(留档目录):**引用它们
是刻意的** —— 那四个脚本是那四版 config 的唯一实现,复制一份到这里就是造第二个事实源。

**幂等**:重复跑结果相同 —— 包已现役 → 零事件;章程已现役 → 切换器返回「已是现役」;
参考表用 `INSERT OR REPLACE` 覆盖同主键行。

跑法:
    python scripts/bootstrap_dev_db.py --db-path /tmp/dev.db
    python scripts/bootstrap_dev_db.py --db-path /tmp/dev.db --reference-db data/neckline.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "oneoff"))

from neckline.config import settings  # noqa: E402
from neckline.db import connection, init_schema  # noqa: E402
from neckline.selection import pack  # noqa: E402
from neckline.strategy import brain  # noqa: E402

import activate_charter  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKS_DIR = _REPO_ROOT / "packs"

# V2.4.0 目标现役集合(与 `scripts/activate_pack_set.py` 的默认集合同一份文件清单)。
_TARGET_PACK_FILES: Tuple[Path, ...] = (
    _PACKS_DIR / "K8-skeleton.json",
    _PACKS_DIR / "C2.json",
    _PACKS_DIR / "Z2.json",
    _PACKS_DIR / "Y2.json",
)
_TARGET_CHARTER = "v2.3-k8"

# 🔴 **只读参考表白名单**(`CLAUDE.md`「本地 dev 后端联调」既有体例逐字):四张,
# ⛔ 一张都不许加业务表 / 凭据表进来。加表前先问:「这张表里会不会有 api_key、
# 推送 token、我的真实持仓、真实报告?」——只要不是斩钉截铁的"不会",就不该加。
_REFERENCE_TABLES: Tuple[str, ...] = (
    "trade_cal", "strategy_versions", "stock_basic", "namechange",
)
# 反向名单:出现在这里的表**永远**不许被本脚本拷贝(守门单测正面断言)。
_FORBIDDEN_TABLES: Tuple[str, ...] = (
    "app_settings",        # LLM api_key / 推送开关 / 后端 token
    "llm_providers",       # api_key
    "positions", "reports", "baskets", "decision_log", "inquiry_log",
    "device_tokens", "sentinel_events",
)

# 章程派生链:(`scripts/oneoff/` 里的模块名, 该脚本落的版本号, 是否吃 confirm 参数)。
# 顺序即依赖顺序(每一版都从上一版的 DB 行复制),⛔ 别调、⛔ 别跳。
# 与 `tests/test_charter_v23k8.py::_seed_through_v22k8` 同一条链、同一批脚本。
_CHARTER_CHAIN: Tuple[Tuple[str, str, bool], ...] = (
    ("charter_v1_3", "v1.3", False),
    ("charter_v1_3_3", "v1.3.3", False),
    ("seed_charter_v22k8", "v2.2-k8", True),
    ("seed_charter_v23k8", "v2.3-k8", True),
)


def _is_protected_db(db_path: Path) -> Optional[str]:
    """返回拒绝理由(None = 可以用)。**宁可误拒,不许误放**。"""
    resolved = db_path.expanduser().resolve()
    if resolved == settings.db_path.expanduser().resolve():
        return f"{resolved} 就是 settings.db_path(项目权威库)"
    if str(resolved).startswith("/opt/neckline"):
        return f"{resolved} 在生产部署目录 /opt/neckline 下"
    try:
        resolved.relative_to((_REPO_ROOT / "data").resolve())
    except ValueError:
        pass
    else:
        return f"{resolved} 在仓库 data/ 目录下(权威库与行情分区的家)"
    return None


def _copy_reference_tables(db_path: Path, reference_db: Path) -> List[str]:
    """只拷白名单四张只读参考表(`INSERT OR REPLACE`,幂等)。

    🔴 **⛔ 刻意不用 `ATTACH`**:`ATTACH` 是**可写**附加(python 的 `sqlite3.connect()`
    默认没开 URI,`file:…?mode=ro` 那种写法在这里根本不生效,会被当成一个字面文件名)——
    而 `--reference-db` 指的往往正是**权威库** `data/neckline.db`。改成:参考库另开一条
    `uri=True` + `mode=ro` 的**独立只读连接**,读进内存,再写目标库。源库连"被 WAL
    checkpoint 顺手改一下"的机会都没有。

    列名按**源表实际列**逐列点名(⛔ 不用 `SELECT *` + 隐式列序):目标库可能因
    `_MIGRATE_COLUMNS` 多出源库没有的列,靠列序对齐迟早错位且看不出来。
    参考库缺某张表 / 某列 → 如实跳过并打印,⛔ 不静默。

    🔴 **V2.4.0 复审 🟡-1:`strategy_versions` 的 `is_active` 是"本库自己的状态",
    ⛔ 不是参考数据**。`INSERT OR REPLACE` 把它连同源库的现役标记一起拷进来 ——
    第二次跑就会出现**两行 `is_active=1`**(第一次跑激活的 `v2.3-k8` + 参考库带来的
    那一行),而 `brain.get_active()` 用 `ORDER BY created_at DESC LIMIT 1` 把它遮住,
    `strategy_versions` 也没有 `selection_packs` 那种部分唯一索引,库层静默接受 ——
    于是「今天用的是哪版章程」变成「看 `created_at` 谁大」。P4.2 逐字要求「可以重复
    运行且结果幂等」,这一条当时不成立。
    **修法**:拷之前记下本库的现役版本,拷完**按它把标记复原**(本库原本没有现役行
    → 保留参考库带来的那一个,但仍收敛成恰好一行)。
    """
    notes: List[str] = []
    init_schema(db_path)
    prior_active = brain.get_active(db_path=db_path)
    prior_active_version = prior_active.version if prior_active is not None else None
    src = sqlite3.connect(f"file:{reference_db}?mode=ro", uri=True)
    try:
        have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with connection(db_path) as dst:
            dst_tables = {r[0] for r in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in _REFERENCE_TABLES:
                if table not in have:
                    notes.append(f"⚠ 参考库没有表 {table} —— 跳过(⛔ 不静默,这里如实记一笔)")
                    continue
                if table not in dst_tables:
                    notes.append(f"⚠ 目标库没有表 {table}(schema 未建?)—— 跳过")
                    continue
                src_cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                dst_cols = {r[1] for r in dst.execute(f"PRAGMA table_info({table})")}
                cols = [c for c in src_cols if c in dst_cols]
                dropped = [c for c in src_cols if c not in dst_cols]
                rows = src.execute(
                    f"SELECT {', '.join(cols)} FROM {table}").fetchall()
                n_before = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if rows:
                    dst.executemany(
                        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({', '.join('?' * len(cols))})",
                        rows,
                    )
                n_after = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                note = f"参考表 {table}:源 {len(rows)} 行 → 本库 {n_before} → {n_after} 行"
                if dropped:
                    note += f"(源库多出的列本库没有,已跳过:{dropped})"
                notes.append(note)
            notes.extend(_restore_single_active_charter(dst, prior_active_version))
    finally:
        src.close()
    return notes


def _restore_single_active_charter(conn, prior_active_version: Optional[str]) -> List[str]:
    """把 `strategy_versions.is_active` 收敛回**恰好一行**(复审 🟡-1 的落点)。

    · 本库拷之前就有现役行 → 复原成那一行(参考库带来的标记是**别人库的状态**);
    · 本库拷之前没有现役行 → 留下参考库标记的那一行(派生链要靠它当祖先),
      多于一行时按 `version` 升序取第一个 —— **确定性**的收敛,⛔ 不按 `created_at`
      (那正是 `get_active()` 用来遮住这个洞的那个排序)。
    ⚠ 一行都没有(参考库里根本没有章程行)→ 不动、如实记一笔,由 `_ensure_charter` 报错。
    """
    notes: List[str] = []
    actives = [r[0] for r in conn.execute(
        "SELECT version FROM strategy_versions WHERE is_active=1 ORDER BY version")]
    if not actives:
        notes.append("⚠ 拷完之后库里没有任何现役章程行 —— 交给下一步如实报错")
        return notes
    keep = prior_active_version if prior_active_version in actives else actives[0]
    if actives == [keep]:
        return notes                      # 本来就恰好一行,零动作
    conn.execute("UPDATE strategy_versions SET is_active=0 WHERE version<>?", (keep,))
    conn.execute("UPDATE strategy_versions SET is_active=1 WHERE version=?", (keep,))
    notes.append(
        f"⚠ 参考表把 is_active 一起拷了进来 → 现役行有 {len(actives)} 个 {actives};"
        f"已收敛回本库自己的现役版本 {keep}(复审 🟡-1:现役标记不是参考数据)"
    )
    return notes


def _ensure_charter(db_path: Path) -> int:
    """确保 `v2.3-k8` 章程行存在:缺就用既有落行脚本从祖先派生(⛔ 不手抄 config)。

    返回 0 = 就绪;非 0 = 拒绝继续(缺祖先 / 派生失败)。"""
    if brain.get_version(_TARGET_CHARTER, db_path=db_path) is not None:
        print(f"章程 {_TARGET_CHARTER} 行已存在 —— 跳过派生(幂等)。")
        return 0

    base = brain.get_active(db_path=db_path)
    if base is None:
        print(
            f"错误:库里没有任何章程行,无法派生 {_TARGET_CHARTER}。\n"
            "      章程的唯一事实源是 `strategy_versions` 行本身,本脚本**不手抄一份 config**\n"
            "      —— 请带 `--reference-db <一个有章程行的库>` 再跑一次。",
            file=sys.stderr,
        )
        return 1

    print(f"章程 {_TARGET_CHARTER} 行不存在 → 从现役 {base.version} 沿既有落行脚本派生:")
    import importlib

    for module_name, version, takes_confirm in _CHARTER_CHAIN:
        if brain.get_version(version, db_path=db_path) is not None:
            print(f"  · {version} 已存在,跳过")
            continue
        mod = importlib.import_module(module_name)
        rc = mod.land_charter(db_path, confirm=True) if takes_confirm else mod.land_charter(db_path)
        if rc != 0:
            print(f"错误:派生 {version} 失败(rc={rc},见上面这一段的原因)。", file=sys.stderr)
            return 2
        print(f"  · {version} 已落行")
    return 0


def bootstrap(db_path: Path, reference_db: Optional[Path]) -> int:
    reason = _is_protected_db(db_path)
    if reason is not None:
        print(
            f"错误:拒绝在受保护的库上跑 bootstrap —— {reason}。\n"
            "      本脚本**只服务临时 / 开发库**(P4.2);要动权威库请走 "
            "`scripts/activate_pack_set.py` 与 `scripts/activate_charter.py` 各自的四道闸。",
            file=sys.stderr,
        )
        return 2

    print(f"目标库:{db_path}")
    init_schema(db_path)

    if reference_db is not None:
        if not reference_db.exists():
            print(f"错误:参考库不存在:{reference_db}", file=sys.stderr)
            return 2
        print(f"\n—— 只读参考表(⛔ 不含业务数据与凭据)——")
        for note in _copy_reference_tables(db_path, reference_db):
            print(f"  {note}")
    else:
        print("\n⚠ 未给 `--reference-db` —— 只建空 schema + 激活策略包;"
              "章程只有在库里已有祖先行时才派生得出来。")

    # ---- 四线策略包(原子激活,复用 P4.3 的唯一实现;⛔ 不抄第二份激活逻辑)----
    # 包文件在仓库里,**永远拿得到** → 先做这一步,哪怕后面章程派生不出来,拿到的
    # 也是一个「包齐了、章程缺」的**可诊断**状态,而不是一个什么都没有的空库。
    print("\n—— 四线策略包(原子激活)——")
    docs = [pack.load_pack_file(f) for f in _TARGET_PACK_FILES]
    result = pack.activate_pack_set(docs, via="bootstrap-dev", db_path=db_path)
    if result.before == result.after:
        print("四条线已是目标版本 —— 幂等空操作(零事件)。")
    else:
        print(f"batch_id={result.batch_id}:{result.before} → {result.after}")

    # ---- 章程(⛔ 与策略包是两条版本线,两套流程,永不混用)----
    print(f"\n—— 纪律章程 {_TARGET_CHARTER} ——")
    rc = _ensure_charter(db_path)
    if rc != 0:
        return rc
    rc = activate_charter.activate(db_path, _TARGET_CHARTER, confirm=True)
    if rc != 0:
        print(f"错误:章程激活失败(rc={rc})。", file=sys.stderr)
        return rc

    # ---- 输出激活后的四条 pack line + 章程(P4.2 验收要的那两行读数)----
    active_packs = {p.line_code: p.pack_version
                    for p in pack.list_packs(db_path=db_path) if p.is_active}
    active_charter = brain.get_active(db_path=db_path)
    print("\n══ bootstrap 完成 ══")
    for line in ("V", "C", "Z", "Y"):
        print(f"  pack line {line}:{active_packs.get(line, '(无)')}")
    print(f"  纪律章程:{active_charter.version if active_charter else '(无)'}")

    expected = {"V": "K8-V0.8", "C": "C2", "Z": "Z2", "Y": "Y2"}
    if {k: active_packs.get(k) for k in expected} != expected:
        print(f"错误:激活后的四线现役集合不是预期的 {expected}:{active_packs}", file=sys.stderr)
        return 3
    # 🔴 复审 🟡-1:**裸计数**自检 —— 必须排在下面那条 `active_charter.version` **之前**。
    # `brain.get_active()` 用 `ORDER BY created_at DESC LIMIT 1`,两行现役时它照样返回
    # 一个"看起来正常"的答案 —— 先问「到底有几行」,再谈「那一行是不是目标」。
    # ⛔ 别用 `get_active()` 复查自己;直接数行(`strategy_versions` 没有
    # `selection_packs` 那种部分唯一索引,库层不会拦,这条自检就是那道闸)。
    with connection(db_path) as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT version FROM strategy_versions WHERE is_active=1 ORDER BY version")]
    if len(rows) != 1:
        print(
            f"错误:`strategy_versions` 里现役行有 {len(rows)} 个:{rows}(必须恰好 1)。\n"
            "      P4.2 要求可重复运行且结果幂等;多于一行 = 「今天用的是哪版章程」\n"
            "      退化成「看 created_at 谁大」。",
            file=sys.stderr,
        )
        return 3
    print(f"  现役章程行计数:{len(rows)}(恰好 1 ✓)")
    if active_charter is None or active_charter.version != _TARGET_CHARTER:
        print(f"错误:激活后的现役章程不是 {_TARGET_CHARTER}", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="开发 / 临时库 bootstrap(plan §五 V2.4.0 P4.2:K8-V0.8 + C2/Z2/Y2 + v2.3-k8)")
    # 🔴 `required=True` 且**无默认值**:见模块头护栏 1。
    ap.add_argument("--db-path", type=Path, required=True,
                    help="目标 SQLite 库(必填;⛔ 拒绝生产 / 仓库 data/ 下的路径)")
    ap.add_argument("--reference-db", type=Path, default=None,
                    help=f"可选:只读参考库,只拷 {', '.join(_REFERENCE_TABLES)} 四张参考表")
    args = ap.parse_args()
    return bootstrap(args.db_path, args.reference_db)


if __name__ == "__main__":
    raise SystemExit(main())
