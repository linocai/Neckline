#!/usr/bin/env python3
"""选股策略包切换器(plan §五 V2-③,复刻 `scripts/activate_charter.py` 的四道闸
体例)。**包与章程是两条永不混用的版本线**:本脚本只碰 `selection_packs` /
`selection_pack_activation_log`,不 import `neckline.strategy.brain`,不碰
`strategy_versions`——纪律参数(止损/仓位/熔断)与选股策略包各有各的表、各有
各的激活脚本(见 plan §2.8-A「两条版本线、两张表、两套激活流程」)。

四道闸(缺一不激活):
    1. **JSON Schema 校验**:`manifest`(pack_version/name/date/engine_api_version/
       evidence_ref)+ `config`(seeds 引用的原语必须已注册且参数合法、tier.weights/
       dims 结构合法)。
    2. **原语 / 白名单核对 + `engine_api_version` 兼容**:防御性复核整个原语
       注册表仍满足特征白名单(构造期已经拦过一次,这里是第二道);
       `manifest.engine_api_version` 必须与引擎现版本逐位相等,不兼容 →
       拒绝、fail loud。
    3. **打印 old→new 逐项 diff**(默认演练模式,不写库):`seeds.*` 改动标注
       「影响 ④ 市场扫描层」,`tier.*` 改动标注「影响 ⑥ Tier 分层引擎」。
    4. **`--confirm` 才写**:单事务落库 + 激活切换(`neckline.selection.pack.
       activate_pack()`),写后断言现役唯一。

**不做 API 端点**(同章程激活铁律,§3.8:系统内核永不被客户端改),只走命令行:
    python scripts/activate_pack.py                                   # dry-run(默认 packs/K4-pack.json)
    python scripts/activate_pack.py --confirm                         # 校验通过 + 激活
    python scripts/activate_pack.py --file packs/其它包.json --confirm
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

_DEFAULT_PACK_FILE = Path(__file__).resolve().parent.parent / "packs" / "K4-pack.json"


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
    old_config = active.config if active is not None else {}
    old_seeds = dict(old_config.get("seeds", {}) or {})
    new_seeds = dict(new_config.get("seeds", {}) or {})
    old_tier = dict(old_config.get("tier", {}) or {})
    new_tier = dict(new_config.get("tier", {}) or {})

    lines: List[str] = []
    lines += _format_diff_block("seeds(原语参数)", "影响 ④ 市场扫描层驱动种子生成", old_seeds, new_seeds)
    lines.append("")
    lines += _format_diff_block("tier.weights", "影响 ⑥ Tier 分层引擎机械分", old_tier.get("weights", {}) or {}, new_tier.get("weights", {}) or {})
    lines.append("")
    old_dims, new_dims = old_tier.get("dims", []), new_tier.get("dims", [])
    marker = "  ← 改动" if old_dims != new_dims else ""
    lines.append("—— tier.dims(影响 ⑥ Tier 分层引擎机械分维度选择)——")
    lines.append(f"    {old_dims} → {new_dims}{marker}")
    return lines


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
    active = pack.get_active_pack(db_path=db_path)
    if active is not None and active.pack_version == target_version:
        print(f"\n提示:{target_version} 已是现役策略包,无需激活(is_active 已在该版本)。")
        return 0

    print(f"\n现役 {active.pack_version if active is not None else '(无)'} → 目标 {target_version} 逐项 diff:")
    for line in _describe_diff(active, manifest, config):
        print(line)
    print(f"\n包名:{manifest['name']}  日期:{manifest['date']}  证据链:{manifest.get('evidence_ref')}")

    # ---- 闸 4:--confirm 才写(单事务)----
    if not confirm:
        print(f"\n[dry-run] 未带 --confirm,不写库。现役仍为 {active.pack_version if active is not None else '(无)'}。")
        print(f"确认无误后加 --confirm 激活:python scripts/activate_pack.py --file {file} --confirm")
        return 0

    activated = pack.activate_pack(manifest, config, via="cli", db_path=db_path)
    print(
        f"\n已激活:{activated.pack_version}"
        f"(is_active={int(activated.is_active)}, activated_at={activated.activated_at})"
    )
    actives = [p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active]
    if actives != [activated.pack_version]:
        print(f"错误:现役包不唯一或不对:{actives}", file=sys.stderr)
        return 3
    print(f"现役断言通过:唯一现役 = {activated.pack_version}")
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
