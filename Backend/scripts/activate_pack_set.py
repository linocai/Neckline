#!/usr/bin/env python3
"""**四线原子激活器**(plan §五 V2.4.0 P4.3;K8 §十九「激活与回滚」逐字:骨架
`K8-V0.8` 与三条引擎线 `C2/Z2/Y2` 是**协调升级**,分别激活会留下临时混合态)。

🔴 **为什么非它不可(物理理由,不是洁癖)**:骨架包 `config.threshold_governance`
这张关口闸门模式**对账表按 `pack_version` 对号入座** —— `K8-V0.8` 里写的是
`C2.*/Z2.*/Y2.*` 十一条。在 `C1/Z1/Y1` 仍现役时单独激活 `K8-V0.8`,闸 1 的对账
**当场拒**(「现役引擎线里没有版本 C2」)。所以「四条线一起过闸、一起落库」不是
包装,是唯一走得通的路。

与 `scripts/activate_pack.py` 的分工:
  * 单包切换(如只回滚一条引擎线)仍走 `activate_pack.py`,**本脚本不取代它**;
  * 本脚本 = 同一批四个包**共用一套闸 1–4**,落库走 `pack.activate_pack_set()`
    的**单事务**,activation log 共享一个 `batch_id`。

四道闸(逐包照跑,⛔ 不因为"批量"跳过任何一道):
    1. **JSON Schema 校验**(`pack.validate_pack_doc`,与单包完全同一份实现)
       + **关口闸门模式对账表** × **本批激活后的引擎集合**逐条一致。
       ⚠ 对账的比较对象是**新集合**(本批的 C2/Z2/Y2 + 本批没动的既有现役引擎线),
       不是库里现在那三条 —— 否则四线原子激活自己就过不了自己的闸。
    2. **原语注册表白名单复核** + `engine_api_version` 兼容(逐包)。
    3. **打印 old→new 版本集合 + 逐包 diff**(默认演练模式,零写库)。
    4. **`--confirm` 才写**:`activate_pack_set()` 单事务落库,写后断言**每条线唯一
       现役**且**恰好是本批目标**。

🔴 **持仓章程不参与本事务**:本脚本与 `pack.py` 全程不 import
`neckline.strategy.brain`、不碰 `strategy_versions`(两条版本线、两张表、两套激活
流程,永不混用)。要改章程只有 `scripts/activate_charter.py` 一条路。

跑法:
    python scripts/activate_pack_set.py                      # dry-run(默认四包)
    python scripts/activate_pack_set.py --confirm            # 校验通过 + 原子激活
    python scripts/activate_pack_set.py --file packs/A.json --file packs/B.json
    python scripts/activate_pack_set.py --db /path/to.db --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.selection import engine_api, pack, primitives  # noqa: E402

import activate_pack as _single  # noqa: E402  (复用闸 3 的 diff 打印,⛔ 不抄第二份)

_PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"
# V2.4.0 的四线目标集合(施工图 P4.3 / 版本矩阵)。⛔ 别把它写成"扫 packs/ 下所有
# 文件":`C1/Z1/Y1` 也在那个目录里(回滚绳的一部分),扫目录会把旧包一起卷进来。
_DEFAULT_PACK_FILES: Tuple[Path, ...] = (
    _PACKS_DIR / "K8-skeleton.json",
    _PACKS_DIR / "C2.json",
    _PACKS_DIR / "Z2.json",
    _PACKS_DIR / "Y2.json",
)


def _prospective_engines(
    docs: List[Dict[str, Any]], db_path: Path,
) -> Dict[str, "pack.Pack"]:
    """**本批激活之后**的引擎线集合(`line_code → Pack`),供闸 1 的对账表比对。

    = 本批里 `line_code ∈ {C,Z,Y}` 的包(用内存 `Pack` 对象表达,尚未落库)
      + 本批**没有**覆盖到的既有现役引擎线(照旧从库里读)。

    🔴 为什么不能直接读库:批量激活的全部意义就是「对账表指向的引擎版本与骨架包
    同一时刻生效」—— 拿激活前的库去对账,`K8-V0.8` 永远对不上(库里还是 C1/Z1/Y1)。
    ⚠ 内存 `Pack` 只填对账表真正会读到的字段(manifest/config/版本号),
    `is_active`/`created_at` 是演练期的占位,**不落库、不外泄**。"""
    engines: Dict[str, pack.Pack] = dict(pack.get_active_engines(db_path))
    for doc in docs:
        manifest = doc["manifest"]
        line = pack.manifest_line_code(manifest)
        if line not in ("C", "Z", "Y"):
            continue
        engines[line] = pack.Pack(
            pack_version=manifest["pack_version"],
            name=manifest["name"],
            engine_api_version=manifest["engine_api_version"],
            manifest=manifest,
            config=doc["config"],
            evidence_ref=list(manifest.get("evidence_ref") or []),
            is_active=True,
            created_at="(未落库)",
            activated_at=None,
            line_code=line,
            status="running",
        )
    return engines


def _format_active_map(m: Dict[str, str]) -> str:
    return "{" + ", ".join(f"{k}={v}" for k, v in m.items()) + "}" if m else "(空)"


def run(files: List[Path], db_path: Path, confirm: bool) -> int:
    if not files:
        print("错误:一个包文件都没给(--file 至少一次)。", file=sys.stderr)
        return 2

    # ---- 读文件(任一读不出/不是 JSON/形状不对 → 整批拒,⛔ 不"跳过坏的继续")----
    docs: List[Dict[str, Any]] = []
    for file in files:
        try:
            docs.append(pack.load_pack_file(file))
        except OSError as e:
            print(f"错误:无法读取包文件 {file}:{e}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as e:
            print(f"错误:{file} 不是合法 JSON:{e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"错误:{e}", file=sys.stderr)
            return 2

    # ---- 线号唯一性(同一条线一批里出现两次 = 批内自己顶掉自己)----
    seen_lines: Dict[str, str] = {}
    for doc in docs:
        line = pack.manifest_line_code(doc["manifest"])
        version = doc["manifest"].get("pack_version")
        if line in seen_lines:
            print(f"错误:同一条线 {line!r} 在一批里出现两次({seen_lines[line]} / {version})"
                  " —— 拒绝执行。", file=sys.stderr)
            return 2
        seen_lines[line] = version

    # ---- 闸 1(前半):逐包 schema ----
    for file, doc in zip(files, docs):
        errors = pack.validate_pack_doc(doc)
        if errors:
            print(f"错误:包 schema / 兼容性校验未通过 —— 拒绝激活 {file}:", file=sys.stderr)
            for e in errors:
                print(f"    {e}", file=sys.stderr)
            return 2
    print(f"闸 1 通过(前半):{len(docs)} 个包的 manifest/config schema 全部合法。")

    # ---- 闸 1(后半):关口闸门模式对账表 × **本批激活后的**引擎集合 ----
    from neckline.selection.gates import check_threshold_governance

    prospective = _prospective_engines(docs, db_path)
    for file, doc in zip(files, docs):
        governance = (doc.get("config") or {}).get("threshold_governance")
        if not governance:
            continue
        if not prospective:
            print("⚠ 闸 1 对账:激活后仍无任何现役引擎线,本次**跳过**关口闸门模式对拍 —— "
                  "⛔ 这不代表对得上;引擎线到位后请重跑一次本脚本的演练确认。")
            continue
        gov_errors = check_threshold_governance(governance, prospective)
        if gov_errors:
            print(f"错误:{file.name} 的关口闸门模式对账表与**本批激活后的**引擎包不一致 "
                  "—— 拒绝激活:", file=sys.stderr)
            for e in gov_errors:
                print(f"    {e}", file=sys.stderr)
            return 2
        print(f"闸 1 对账:{file.name} 的 {len(governance)} 条关口闸门模式与本批激活后的引擎包"
              f"({', '.join(f'{k}={v.pack_version}' for k, v in sorted(prospective.items()))})逐条一致。")

    # ---- 闸 2:原语白名单复核 + engine_api_version 兼容(逐包)----
    prim_errors = primitives.validate_all_primitives_whitelisted()
    if prim_errors:
        print("错误:引擎原语注册表未通过特征白名单复核(不应发生,拒绝激活):", file=sys.stderr)
        for e in prim_errors:
            print(f"    {e}", file=sys.stderr)
        return 2
    for file, doc in zip(files, docs):
        if not engine_api.is_compatible(doc["manifest"]):
            print(
                f"错误:{file.name} 的 engine_api_version 不兼容 —— 包声明 "
                f"{doc['manifest'].get('engine_api_version')},引擎现为 "
                f"{engine_api.ENGINE_API_VERSION}。拒绝激活(fail loud,不静默降级)。",
                file=sys.stderr,
            )
            return 2
    print(f"闸 2 通过:原语白名单复核 + {len(docs)} 个包 engine_api_version="
          f"{engine_api.ENGINE_API_VERSION} 全部兼容。")

    # ---- append-only 完整性核对(同 pack_version 内容不得不同,逐包)----
    # 与单包脚本同一条纪律、同一个顺序位置:必须先于"已现役"判断,否则「文件被
    # 篡改但版本号没改、且恰好现役」会被静默放过。
    for doc in docs:
        manifest, config = doc["manifest"], doc["config"]
        target_version = manifest["pack_version"]
        existing = pack.get_pack(target_version, db_path=db_path)
        if existing is not None and (existing.manifest != manifest or existing.config != config):
            print(
                f"错误:pack_version={target_version!r} 已存在但内容不同"
                "(append-only,不可覆盖已登记的包;如需改动请换一个新的 pack_version)。",
                file=sys.stderr,
            )
            return 2

    # ---- 闸 3:旧版本集合 → 新版本集合 + 逐包 diff(零写库)----
    before = {p.line_code: p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active}
    after = dict(before)
    for line, version in seen_lines.items():
        after[line] = version
    print(f"\n旧版本集合(现役):{_format_active_map(before)}")
    print(f"新版本集合(本批后):{_format_active_map(after)}")
    if before == after:
        print("\n提示:四条线目标版本与现役完全一致 —— 本批为幂等空操作(不会写任何事件)。")

    for file, doc in zip(files, docs):
        manifest, config = doc["manifest"], doc["config"]
        line = pack.manifest_line_code(manifest)
        active = pack.get_active_line(line, db_path=db_path)
        print(
            f"\n[{line} 线] 现役 {active.pack_version if active is not None else '(无)'} "
            f"→ 目标 {manifest['pack_version']} 逐项 diff:"
        )
        if active is not None and active.pack_version == manifest["pack_version"]:
            print("  (已是该线现役,无改动)")
            continue
        for line_text in _single._describe_diff(active, manifest, config):
            print(line_text)
        print(f"包名:{manifest['name']}  日期:{manifest['date']}  证据链:{manifest.get('evidence_ref')}")

    # ---- 闸 4:--confirm 才写(单事务 + 共享 batch_id)----
    if not confirm:
        print(f"\n[dry-run] 未带 --confirm,不写库。现役仍为 {_format_active_map(before)}。")
        print("确认无误后加 --confirm 原子激活:python scripts/activate_pack_set.py "
              + " ".join(f"--file {f}" for f in files) + " --confirm")
        return 0

    try:
        result = pack.activate_pack_set(docs, via="cli-set", db_path=db_path)
    except ValueError as e:
        # 单事务:抛错 = 一个字节都没写进去(四线全部维持原值)。
        print(f"错误:原子激活失败,**整批回滚**,四条线全部维持原值:{e}", file=sys.stderr)
        return 3

    print(f"\n已原子激活(batch_id={result.batch_id}):{', '.join(result.activated)}")
    print(f"旧版本集合:{_format_active_map(result.before)}")
    print(f"新版本集合:{_format_active_map(result.after)}")

    # 写后断言:每条**本批动过的**线唯一现役且恰好是目标版本。
    all_packs = pack.list_packs(db_path=db_path)
    for line, version in sorted(seen_lines.items()):
        line_actives = [p.pack_version for p in all_packs if p.is_active and p.line_code == line]
        if line_actives != [version]:
            print(f"错误:{line} 线现役包不唯一或不对:{line_actives}(期望 [{version!r}])",
                  file=sys.stderr)
            return 3
    print(f"现役断言通过:{len(seen_lines)} 条线各自唯一现役 = "
          f"{_format_active_map(dict(sorted(seen_lines.items())))}")
    print(f"全部线现役一览(运维核对用):{_format_active_map(result.after)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="四线原子激活器(plan §五 V2.4.0 P4.3:K8-V0.8 + C2/Z2/Y2 一个事务)")
    ap.add_argument(
        "--file", type=Path, action="append", default=None,
        help=f"包 JSON 文件,可重复;不给则用 V2.4.0 四线默认集合 "
             f"({', '.join(f.name for f in _DEFAULT_PACK_FILES)})",
    )
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    ap.add_argument("--confirm", action="store_true", help="确认写库原子激活(不带则只 dry-run)")
    args = ap.parse_args()
    files: List[Path] = list(args.file) if args.file else list(_DEFAULT_PACK_FILES)
    db_path: Optional[Path] = args.db or settings.db_path
    print(f"目标库:{db_path}")
    print(f"本批包文件({len(files)}):{', '.join(str(f) for f in files)}")
    return run(files, db_path, args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
