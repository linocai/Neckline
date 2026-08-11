#!/usr/bin/env python3
"""选股策略包切换器(plan §五 V2-③,复刻 `scripts/activate_charter.py` 的四道闸
体例)。**包与章程是两条永不混用的版本线**:本脚本只碰 `selection_packs` /
`selection_pack_activation_log`,不 import `neckline.strategy.brain`,不碰
`strategy_versions`——纪律参数(止损/仓位/熔断)与选股策略包各有各的表、各有
各的激活脚本(见 plan §2.8-A「两条版本线、两张表、两套激活流程」)。

四道闸(缺一不激活):
    1. **JSON Schema 校验**:`manifest`(pack_version/name/date/engine_api_version/
       evidence_ref/line_code)+ `config`(V2.2-① 起按版本线分两套:骨架线 V =
       seeds+tier〔原语必须已注册且参数合法〕;引擎线 C/Z/Y = engine 段 +
       `engine_code`×`line_code` 逐位交叉 + gates/tier_evidence 键名白名单 +
       **每个阈值叶子必须带 `{value, provenance}`,缺 provenance 即拒**)。
    2. **原语 / 白名单核对 + `engine_api_version` 兼容**:防御性复核整个原语
       注册表仍满足特征白名单(构造期已经拦过一次,这里是第二道);
       `manifest.engine_api_version` 必须与引擎现版本逐位相等,不兼容 →
       拒绝、fail loud(V2.2 起 = 2,K4/K7 两个 v1 包**被拒是刻意的**,⛔ 别
       "修"——回滚绳一律是「代码 commit + DB 备份还原」,不是激活旧包)。
    3. **打印 old→new 逐项 diff**(默认演练模式,不写库):对照物 = **同一条
       版本线**的现役行;骨架线打 seeds/tier 三段,引擎线打五关 + tier_evidence。
    4. **`--confirm` 才写**:单事务落库 + 激活切换(`neckline.selection.pack.
       activate_pack()`),写后断言**该线**现役唯一(每线唯一,不再是全表唯一)。

**不做 API 端点**(同章程激活铁律,§3.8:系统内核永不被客户端改),只走命令行:
    python scripts/activate_pack.py                                   # dry-run(默认 packs/K8-skeleton.json)
    python scripts/activate_pack.py --confirm                         # 校验通过 + 激活
    python scripts/activate_pack.py --file packs/C1.json --confirm    # 引擎线各自过闸
    python scripts/activate_pack.py --db /path.db --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.selection import engine_api, pack, primitives  # noqa: E402

# V2.2-① 起默认包 = K8 骨架包(旧默认 K4-pack.json 已被 engine_api 闸作废——留一个
# 必被拒的默认值只会让运维以为脚本坏了;K4/K7 文件本身留档不删,负例守门单测在吃)。
_DEFAULT_PACK_FILE = Path(__file__).resolve().parent.parent / "packs" / "K8-skeleton.json"


def _format_diff_block(title: str, impact: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """打印一段 `old`→`new` 顶层键逐项 diff(缺键显示 `<无>`)。`impact` 是给人看
    的一句话("影响 ④ .../影响 ⑥ ..."),不参与比较逻辑。"""
    lines = [f"—— {title}({impact})——"]
    keys = sorted(set(old) | set(new))
    if not keys:
        lines.append("  (空)")
        return lines
    any_changed = False
    for key in keys:
        ov = old.get(key, "<无>")
        nv = new.get(key, "<无>")
        if ov != nv:
            any_changed = True
            lines.append(f"  * {key}: {ov} → {nv}   ← 改动")
        else:
            lines.append(f"    {key}: {nv}")
    if not any_changed:
        lines.append("  (无改动)")
    return lines


def _describe_diff(active: Optional["pack.Pack"], new_manifest: Dict[str, Any], new_config: Dict[str, Any]) -> List[str]:
    """闸 3 的人读 diff。V2.2-① 起按版本线分两种形状:骨架/LEGACY 线打 seeds/tier
    三段(既有体例);引擎线(C/Z/Y)打 engine.gates 五关 + tier_evidence(引擎线
    里根本没有 seeds/tier,硬套旧模板只会打出三段"(空)")。`active` 由调用方保证
    是**同一条线**的现役行(跨线 diff 没有意义)。"""
    line_code = pack.manifest_line_code(new_manifest)
    old_config = active.config if active is not None else {}

    lines: List[str] = []
    if line_code in ("C", "Z", "Y"):
        old_engine = dict(old_config.get("engine", {}) or {})
        new_engine = dict(new_config.get("engine", {}) or {})
        old_gates = dict(old_engine.get("gates", {}) or {})
        new_gates = dict(new_engine.get("gates", {}) or {})
        for section in ("market", "sector", "position", "core", "evidence"):
            lines += _format_diff_block(
                f"engine.gates.{section}", "影响 ③ 六道关口门槛判定",
                dict(old_gates.get(section, {}) or {}), dict(new_gates.get(section, {}) or {}),
            )
            lines.append("")
        lines += _format_diff_block(
            "engine.tier_evidence", "影响 ③ T1/T2 证据成熟度定档",
            dict(old_engine.get("tier_evidence", {}) or {}),
            dict(new_engine.get("tier_evidence", {}) or {}),
        )
        return lines

    old_seeds = dict(old_config.get("seeds", {}) or {})
    new_seeds = dict(new_config.get("seeds", {}) or {})
    old_tier = dict(old_config.get("tier", {}) or {})
    new_tier = dict(new_config.get("tier", {}) or {})

    lines += _format_diff_block("seeds(原语参数)", "影响 ④ 市场扫描层驱动种子生成", old_seeds, new_seeds)
    lines.append("")
    lines += _format_diff_block("tier.weights", "影响 ⑥ Tier 分层引擎机械分", old_tier.get("weights", {}) or {}, new_tier.get("weights", {}) or {})
    lines.append("")
    old_dims, new_dims = old_tier.get("dims", []), new_tier.get("dims", [])
    marker = "  ← 改动" if old_dims != new_dims else ""
    lines.append("—— tier.dims(影响 ⑥ Tier 分层引擎机械分维度选择)——")
    lines.append(f"    {old_dims} → {new_dims}{marker}")

    # V2.3.2-④-A:骨架线的两个新段也要在闸 3 的 diff 里露面 —— 否则「演练输出里
    # 的 diff 恰为哪两段」这条验收根本看不出来(它们不在上面三块里)。
    for section, note in (
        ("regime", "影响 ② 行情状态层五个判定阈值"),
        ("iteration", "影响 ⑨ 四分类样本门槛(min_n / retire_min_n)"),
        ("threshold_governance", "关口闸门模式**对账表**(不是开关;闸 1 会与引擎包逐条对拍)"),
    ):
        old_sec = old_config.get(section) or {}
        new_sec = new_config.get(section) or {}
        if not old_sec and not new_sec:
            continue
        lines.append("")
        lines.append(f"—— config.{section}({note})——")
        for key in sorted(set(old_sec) | set(new_sec)):
            o, n = old_sec.get(key), new_sec.get(key)
            mark = "  ← 改动" if o != n else ""
            lines.append(f"    {key}: {_brief(o)} → {_brief(n)}{mark}")
    return lines


def _brief(v: Any) -> str:
    """叶子的一行摘要(带 provenance 的叶子只显示 value + source,整片 JSON 摊开
    会让 diff 变成没人读得完的一堵墙)。"""
    if isinstance(v, dict):
        if "value" in v:
            src = ((v.get("provenance") or {}).get("source")) if isinstance(
                v.get("provenance"), dict) else None
            return f"{v['value']!r}" + (f"({src})" if src else "")
        if "mode" in v:
            return f"{v['mode']!r}"
    return "(无)" if v is None else repr(v)


def run(file: Path, db_path: Path, confirm: bool) -> int:
    # ---- 闸 1:读文件 + JSON Schema 校验 ----
    try:
        doc = pack.load_pack_file(file)
    except OSError as e:
        print(f"错误:无法读取包文件 {file}:{e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"错误:{file} 不是合法 JSON:{e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"错误:{e}", file=sys.stderr)
        return 2

    manifest, config = doc.get("manifest"), doc.get("config")
    errors = pack.validate_pack_doc(doc)
    if errors:
        print(f"错误:包 schema / 兼容性校验未通过 —— 拒绝激活 {file}:", file=sys.stderr)
        for e in errors:
            print(f"    {e}", file=sys.stderr)
        return 2
    # ---- 闸 1 的第二半(V2.3.2-④-A):关口闸门模式**对账表** × 现役引擎包逐条一致 ----
    # 🔴 对账表**不是第二个事实源**:模式仍由引擎包叶子的 `provenance.source` 唯一决定。
    # 这一步只负责让**一次悄悄的 provenance 改动**过不了闸 —— 有人把某条阈值从
    # `engineering_v1` 改成 `audited`(= 悄悄恢复机械硬否决)却没同步这张表,当场拒。
    # 这是策略线裁定 1「零自动升级」的物理落点。
    governance = (config or {}).get("threshold_governance")
    if governance:
        from neckline.selection.gates import check_threshold_governance

        engines = pack.get_active_engines(db_path)
        if not engines:
            # ⚠ 现役引擎线为空(全新库 / bootstrap 顺序是"先骨架后引擎")—— 无从对拍。
            # **如实说跳过,⛔ 不静默当通过**:这句话必须打出来,否则一次空库激活会让
            # 人以为对账表已经核过了。
            print("⚠ 闸 1 对账:现役引擎线为空,本次**跳过**关口闸门模式对拍 —— "
                  "⛔ 这不代表对得上;引擎线激活后请重跑一次本脚本的演练确认。")
        else:
            gov_errors = check_threshold_governance(governance, engines)
            if gov_errors:
                print("错误:关口闸门模式对账表与现役引擎包不一致 —— 拒绝激活:",
                      file=sys.stderr)
                for e in gov_errors:
                    print(f"    {e}", file=sys.stderr)
                return 2
            print(f"闸 1 对账:{len(governance)} 条关口闸门模式与现役引擎包逐条一致。")

    print(f"闸 1 通过:{file} 的 manifest/config schema 合法。")

    # ---- 闸 2:原语注册表防御性复核 + engine_api_version 兼容(闸1已核对过,
    #      这里再显式打印一遍确认结果,便于运维读日志时一眼看到两道闸都跑过)----
    prim_errors = primitives.validate_all_primitives_whitelisted()
    if prim_errors:
        print("错误:引擎原语注册表未通过特征白名单复核(不应发生,拒绝激活):", file=sys.stderr)
        for e in prim_errors:
            print(f"    {e}", file=sys.stderr)
        return 2
    if not engine_api.is_compatible(manifest):
        print(
            f"错误:engine_api_version 不兼容 —— 包声明 {manifest.get('engine_api_version')},"
            f"引擎现为 {engine_api.ENGINE_API_VERSION}。拒绝激活(fail loud,不静默降级)。",
            file=sys.stderr,
        )
        return 2
    print(f"闸 2 通过:原语白名单复核 + engine_api_version={engine_api.ENGINE_API_VERSION} 兼容。")

    # ---- append-only 完整性核对:同 pack_version 内容不得不同 ----
    # **必须先于下面"已现役,无需激活"的快捷退出**——否则「文件被篡改但版本号
    # 没改、且该版本恰好当前现役」这种情况会被快捷退出直接放过,完整性问题被
    # 静默掩盖(2026-08-02 手工演练时真的复现过这个顺序漏洞,见 §九/完工记录)。
    # 同版本号内容不同永远是硬错误,不因为它当前是不是现役而改变判断。
    target_version = manifest["pack_version"]
    existing = pack.get_pack(target_version, db_path=db_path)
    if existing is not None and (existing.manifest != manifest or existing.config != config):
        print(
            f"错误:pack_version={target_version!r} 已存在但内容不同"
            "(append-only,不可覆盖已登记的包;如需改动请换一个新的 pack_version)。",
            file=sys.stderr,
        )
        return 2

    # ---- 闸 3:打印 old→new 逐项 diff(默认演练模式,本步骤零写库)----
    # V2.2-①:「现役」按**本包所属的版本线**取(`get_active_line`),⛔ 不再是全表
    # 口径 —— 激活 C1 时的对照物是 C 线现役行,拿骨架线来 diff 既误导也误拦。
    target_line = pack.manifest_line_code(manifest)
    active = pack.get_active_line(target_line, db_path=db_path)
    if active is not None and active.pack_version == target_version:
        print(f"\n提示:{target_version} 已是 {target_line} 线现役策略包,无需激活(is_active 已在该版本)。")
        return 0

    print(
        f"\n[{target_line} 线] 现役 {active.pack_version if active is not None else '(无)'} "
        f"→ 目标 {target_version} 逐项 diff:"
    )
    for line in _describe_diff(active, manifest, config):
        print(line)
    print(f"\n包名:{manifest['name']}  日期:{manifest['date']}  证据链:{manifest.get('evidence_ref')}")

    # ---- 闸 4:--confirm 才写(单事务)----
    if not confirm:
        print(
            f"\n[dry-run] 未带 --confirm,不写库。{target_line} 线现役仍为 "
            f"{active.pack_version if active is not None else '(无)'}。"
        )
        print(f"确认无误后加 --confirm 激活:python scripts/activate_pack.py --file {file} --confirm")
        return 0

    activated = pack.activate_pack(manifest, config, via="cli", db_path=db_path)
    print(
        f"\n已激活:{activated.pack_version}"
        f"(line={activated.line_code}, is_active={int(activated.is_active)}, "
        f"activated_at={activated.activated_at})"
    )
    all_packs = pack.list_packs(db_path=db_path)
    line_actives = [p.pack_version for p in all_packs if p.is_active and p.line_code == target_line]
    if line_actives != [activated.pack_version]:
        print(f"错误:{target_line} 线现役包不唯一或不对:{line_actives}", file=sys.stderr)
        return 3
    all_actives = {p.line_code: p.pack_version for p in all_packs if p.is_active}
    print(f"现役断言通过:{target_line} 线唯一现役 = {activated.pack_version}")
    print(f"全部线现役一览(运维核对用):{all_actives}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="选股策略包切换器(plan §五 V2-③,四道闸激活)")
    ap.add_argument("--file", type=Path, default=_DEFAULT_PACK_FILE, help=f"包 JSON 文件(默认 {_DEFAULT_PACK_FILE})")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    ap.add_argument("--confirm", action="store_true", help="确认写库激活(不带则只 dry-run)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return run(args.file, db_path, args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
