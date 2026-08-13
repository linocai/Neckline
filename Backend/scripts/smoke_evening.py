#!/usr/bin/env python3
"""V2-⑭-A 端到端冒烟:**16:35 整条晚间链 + 五段篮子日报**(真实历史交易日,隔离库)。

跑的是 `neckline/report/evening.py::run_evening_chain` 本尊:
**⑧ EOD 验证拍 → ④ 扫描层批算 → ⑤ 聚合 → ⑥ Tier → ⑦ 卡冻结 → ⑨ 复盘 → 报告落库**,
然后把 markdown 全文 + `reports.basket_daily_json` 快照打印出来供**人工核对**:

  · 五段齐不齐(① 市场语境 / ② 持仓体检 / ③ 今日篮子 / ③b 未定档 / ④ 昨日复盘 / ⑤ 新鲜度);
  · ③b 的两个原因码分不分得开(`capacity_overflow` vs `below_quality_line`);
  · `dataFreshness` 三组键(板块 / 行业强度 / 扫描层)在不在。

**这不是活体验证的替代品**(同 `smoke_basket_review.py` 的自我定位),只是"整条链
确实按预期工作"的一次有真实数据支撑的冒烟检查。

**不污染真实数据**:`data/neckline.db` 先 `sqlite3.backup` 出一份临时副本,篮子 / 卡 /
验证流水 / 复盘 / 报告全写在副本上;**真实 parquet 全程只读**。跑完清理(`--keep` 保留)。

**零真实 LLM**:`--no-llm` 是默认 —— ⑤ 的两段喂 `smoke_basket_review.py` 里那个确定性
桩,⑥⑦⑨ 一律 `use_llm=False`。本脚本要验的是编排与版式,不是模型输出。

用法::

    python scripts/smoke_evening.py                     # D0=20260723 → 链跑在 D1=20260724
    python scripts/smoke_evening.py --date 20260724 --keep
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import prev_trading_day  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.llm.budget import BudgetLedger  # noqa: E402
from neckline.report import evening as ev  # noqa: E402
from neckline.report import store as report_store  # noqa: E402
from neckline.selection.pack import activate_pack, get_active_pack, load_pack_file  # noqa: E402

# 复用 ⑨ 冒烟脚本里那个确定性桩(同码不重写:两处各写一份桩必然漂)。
from scripts.smoke_basket_review import StubProvider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_evening")

_SECTIONS = (
    "## ① 情绪与市场语境",
    "## ② 持仓体检",
    "## ③ 今日篮子",
    "### ③b 今日未定档篮子",
    "## ④ 昨日篮子复盘",
    "## ⑤ 数据新鲜度与降级披露",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="20260724",
                    help="链跑在哪一天(= D+1;它复盘/验证的是上一交易日冻的篮子)")
    ap.add_argument("--keep", action="store_true", help="保留临时库(默认跑完删)")
    ap.add_argument("--llm", action="store_true", help="真实 LLM(默认全桩,本机无 key)")
    args = ap.parse_args()

    day = datetime.strptime(args.date, "%Y%m%d").date()
    d0 = prev_trading_day(day)
    if not settings.db_path.exists():
        logger.error("真实 %s 不存在,无法复制临时副本。", settings.db_path)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="neckline_smoke14_"))
    db = tmp / "smoke.db"
    src = sqlite3.connect(str(settings.db_path))
    dst = sqlite3.connect(str(db))
    src.backup(dst)
    dst.close()
    src.close()
    logger.info("临时库 %s(真实 parquet 只读,真实库全程不写)", db)

    try:
        if get_active_pack(db_path=db) is None:
            doc = load_pack_file(Path(__file__).resolve().parent.parent / "packs" / "K8-skeleton.json")
            p = activate_pack(doc["manifest"], doc["config"], via="smoke", db_path=db)
            logger.info("[前置] 隔离库激活策略包 %s", p.pack_version)

        # —— 前置:先把 D0 那天的篮子造出来(不然 ⑧/⑨ 无对象可判)——————————
        logger.info("=== 前置:在 D0=%s 上跑一遍 ④⑤⑥⑦(桩 LLM),给 ⑧/⑨ 备料 ===", d0)
        stub = None if args.llm else StubProvider()
        pre = ev.run_evening_chain(
            d0, segments=[ev.SEG_SCAN, ev.SEG_BASKET], db_path=db, use_llm=args.llm,
            search_provider=stub, reason_provider=stub, ledger=BudgetLedger(),
        )
        logger.info("  前置结果:%s;篮子 %s", pre.status, pre.stats.get("basket"))

        # —— 正戏:D+1 全链 ————————————————————————————————————————
        logger.info("=== 16:35 全链(D+1=%s)===", day)
        res = ev.run_evening_chain(
            day, db_path=db, use_llm=args.llm,
            search_provider=stub, reason_provider=stub, ledger=BudgetLedger(),
        )
        for seg in ev.CHAIN_SEGMENTS:
            logger.info("  [%s] %-8s %s", seg, res.status[seg], res.stats.get(seg, ""))
        for n in res.notes:
            logger.warning("  ⚠ %s", n)

        if res.bundle is None:
            logger.error("没有报告 —— 链的最后一段没跑?")
            return 1

        print("\n" + "=" * 78)
        print("篮子日报 markdown 全文(人工核对五段结构)")
        print("=" * 78 + "\n")
        print(res.bundle.markdown)

        # —— 自检:五段齐不齐 / ③b 原因码 / dataFreshness 三组键 ——————————
        print("\n" + "=" * 78)
        print("自检")
        print("=" * 78)
        missing = [h for h in _SECTIONS if h not in res.bundle.markdown]
        print(f"五段结构:{'齐' if not missing else '缺 ' + str(missing)}")

        loaded = report_store.load_report(day, db_path=db) or {}
        fresh = loaded.get("data_freshness") or {}
        for group, keys in (
            ("板块", ("sectorDataDate", "sectorLagDays", "stale")),
            ("行业强度", ("industryStrengthDate", "industryStrengthLagDays", "industryStrengthStale")),
            ("扫描层", ("scanLayerDate", "scanLayerLagDays", "scanLayerStale")),
        ):
            have = [k for k in keys if k in fresh]
            print(f"dataFreshness · {group}:{len(have)}/{len(keys)} 键在 → {[(k, fresh.get(k)) for k in have]}")

        snap = loaded.get("basket_daily") or {}
        print(f"basketDaily 三段可得性:baskets={snap.get('basketsAvailable')} "
              f"dropped={snap.get('droppedBasketsAvailable')} reviews={snap.get('reviewsAvailable')}")
        print(f"今日篮子 {len(snap.get('baskets') or [])} 个;"
              f"未定档 {len(snap.get('droppedBaskets') or [])} 个;"
              f"昨日复盘 {len(snap.get('reviews') or [])} 篇")
        reasons = sorted({d.get("reason") for d in (snap.get("droppedBaskets") or [])})
        print(f"③b 原因码:{reasons or '(今日零溢出 —— 节仍在,写「今日无未定档篮子」)'}")

        print("\n--- basket_daily_json 快照(截断展示)---")
        print(json.dumps(snap, ensure_ascii=False, indent=2)[:4000])
        return 0
    finally:
        if args.keep:
            logger.info("临时库保留在 %s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
