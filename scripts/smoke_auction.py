#!/usr/bin/env python3
"""D1 集合竞价确认层合成冒烟(V2.3.3-④,K8.md §二十;照 `scripts/smoke_precall.py` 体例)。

**用途**:真实盘前无法活体验证时(非交易日 / 未到 9:26),拿某历史交易日的**真实日线
open / pre_close / vol** 合成一份「集合竞价快照」,再注入一个**假 provider**,喂给与生产
完全同一份编排代码(`neckline.auction.pipeline.run_auction_pipeline`)跑一遍全链 ——
**零真实网络、零真实 LLM**。

⚠ **这不是活体验证的替代品**,只是「竞价层确实按预期工作」的一次有真实数据支撑的冒烟;
真正的 9:26–9:29 现场核在部署环节(施工图 ⑦-7 五件)。

**不污染真实数据**:整份复制 `data/neckline.db` 到临时副本,两张竞价表与哨兵事件全落
临时副本;Parquet(只读)仍用真实 `data/parquet/`。跑完清理(除非 `--keep-db`)。

**合成方法(诚实标注局限)**:同 `smoke_precall.synthesize_auction_quote` —— `price`/`open`
取当日真实开盘价(即竞价撮合价)、`pre_close` 取真实昨收、竞价量按 `AUCTION_VOL_FRAC`
比例合成。⚠ **三支市场指数与板块基准指数在 `daily` 里没有行**(它们是指数不是个股)→
本冒烟里它们一律「拉不到」,数据质量因此必然是 `degraded` —— **这是合成环境的局限,
不是代码故障**(生产走 `sentinel/quotes.py` 真拉,指数是有报价的)。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from neckline.auction import pipeline as auction_pipeline  # noqa: E402
from neckline.auction import store as auction_store  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.data.market_data import get_market_slice  # noqa: E402
from neckline.llm.base import LLMResult  # noqa: E402
from neckline.report.pipeline import build_report  # noqa: E402
from neckline.sentinel.quotes import Quote  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_auction")

#: 竞价量约占当日总量的比例(**合成近似**,同 `smoke_precall.AUCTION_VOL_FRAC`;
#: 不代表任何回测结论)。
AUCTION_VOL_FRAC = 0.02


def synthesize_auction_quote(row: dict) -> Quote:
    """一行真实 `daily` EOD → 一个「集合竞价快照」`Quote`。⚠ 竞价阶段 `open == price`。"""
    open_ = float(row["open"] or 0.0)
    return Quote(
        code=row["ts_code"].split(".")[0], name=row["ts_code"], price=round(open_, 2),
        pre_close=float(row["pre_close"] or 0.0), open=open_, high=open_, low=open_,
        volume=float(row["vol"] or 0.0) * AUCTION_VOL_FRAC,
        amount=float(row["amount"] or 0.0) * 1000.0 * AUCTION_VOL_FRAC,
        ts="集合竞价 合成", source="synthetic-auction",
    )


class FakeAuctionProvider:
    """假 provider:按机械层给的篮子清单**原样**造一份合规输出。

    ⛔ 它不"聪明":`verdict` 一律给 `confirm`,好让**三道机械夹逼闸**在冒烟里真的被
    走到(Z1 只给一只强股 → 夹成中性;Y1 没证据 → 夹成中性;数据不 ok → 夹成中性)。
    看 journal 里的 `clamped_by` 就知道闸有没有生效。
    """

    name, model = "smoke-fake", "smoke-fake-model"

    def __init__(self, basket_keys: List[str]) -> None:
        self._keys = list(basket_keys)

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        payload: Dict[str, Any] = {
            "market": {"overview": "(冒烟假 provider)指数环境与主线状态的一段话。",
                       "anchors_note": "(冒烟)市场锚点只解释资金方向,不取得交易资格。"},
            "baskets": [
                {"basket_key": k, "verdict": "confirm",
                 "reasons": ["(冒烟)理由一", "(冒烟)理由二"],
                 "auction_strong_codes": [], "driver_negative": None,
                 "sector_core_negative": None, "candidate_negative": None,
                 "evidence_conflict": False, "members": []}
                for k in self._keys
            ],
            "risks": ["(冒烟)这是假 provider 造的风险条目"],
        }
        content = ("(冒烟假 provider)今早的一段自由叙述。\n\n```json\n"
                   + json.dumps(payload, ensure_ascii=False) + "\n```")
        return LLMResult(ok=True, content=content, provider=self.name, model=self.model)


def seed_synthetic_basket(d0: date, tmp_db: Path, *, codes: List[str]) -> Optional[str]:
    """D0 一个 T1/T2 篮子都没有时,往**临时副本**里塞一个合成篮子 + 一张真卡。

    **为什么需要它**:本地开发库常常没有现役骨架线包(`selection_packs` 无
    `line_code='V'` 的 is_active 行)→ D0 零篮子 → 冒烟只能验到市场段,篮子级的
    三道夹逼闸一行都走不到。合成篮子让「一把跑通全链」名副其实。
    ⚠ 它**只落临时副本**,且 `engine_code='Z'` / `engine_version='Z1'` 是刻意的 ——
    Z 线能把**闸 2**(只有一只竞价强股 → 中性)真的走一遍。
    """
    from neckline.db import connection
    from neckline.selection import basket_card as bc
    from neckline.selection.basket_store import save_basket_card

    rows = _daily_rows_lookup(d0, codes)
    picked = [c for c in codes if c in rows][:2]
    if not picked:
        logger.warning("D0 %s 没有可用日线,合成篮子跳过", d0)
        return None
    mechs = [bc.MemberMech(ts_code=c, name=c, close=float(rows[c]["close"] or 0.0),
                           ma20=float(rows[c]["close"] or 0.0) * 0.95,
                           limit_up=float(rows[c]["close"] or 0.0) * 1.1,
                           limit_down=float(rows[c]["close"] or 0.0) * 0.9,
                           stop_price=round(float(rows[c]["close"] or 0.0) * 0.95, 2))
              for c in picked]
    key = "smoke-synthetic"
    with connection(tmp_db) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status,"
            " engine_code, engine_version, skeleton_version, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), key, "冒烟合成篮", "冒烟合成驱动", "theme", 1,
             "K8-skeleton", 2, "v2.3-k8", "smoke", "ok", "Z", "Z1", "K8-V0.7",
             f"{d0}T16:05:00+08:00"),
        )
        basket_id = int(cur.lastrowid)
        for i, c in enumerate(picked):
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, c, "leader" if i == 0 else "core", None, 0, "冒烟合成成员",
                 1 if i == 0 else 0, f"{d0}T16:05:00+08:00"),
            )
    card = {
        "spec_version": bc.CARD_SPEC_VERSION,
        "members": [{"ts_code": m.ts_code, "name": m.ts_code, "role_llm": "leader",
                     "mech": m.to_dict(),
                     "entry_zone": {"low": m.close * 0.99, "high": m.close * 1.01, "why": "冒烟"},
                     "max_chase": m.close * 1.03} for m in mechs],
        "verification_spec": bc.build_verification_spec(key, d0, mechs),
        "invalidation_spec": bc.build_invalidation_spec(key, d0, mechs, stop_pct=0.05),
        "fingerprint": {"stop_pct": 0.05},
    }
    save_basket_card(basket_id, card, stop_pct=0.05, db_path=tmp_db)
    logger.info("⚠ D0 零篮子 → 已在**临时副本**里合成一个 T1 篮子(Z1 引擎,好把闸 2 走一遍):"
                "%s %s", key, picked)
    return key


def _daily_codes(trade_date: date, *, limit: int = 8,
                 parquet_dir: Optional[Path] = None) -> List[str]:
    """D0 当日 `daily` 分区里前 `limit` 只代码(按 `ts_code` 升序,**确定性**)。

    只服务于合成篮子(见 `seed_synthetic_basket`)——⛔ 它不是任何取样口径,
    别拿它当"样本"用。当日无行 → 空列表,调用方按"合成不了"处理。"""
    df = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    if df.is_empty():
        return []
    return sorted(df["ts_code"].to_list())[:limit]


def _daily_rows_lookup(trade_date: date, codes: List[str],
                       parquet_dir: Optional[Path] = None) -> Dict[str, dict]:
    df = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(codes))
    return {r["ts_code"]: r for r in df.iter_rows(named=True)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-day", default="20260716", help="D0 报告日 YYYYMMDD(篮子与卡的冻结日)")
    parser.add_argument("--today", default="20260717", help="合成集合竞价的 D1 交易日 YYYYMMDD")
    parser.add_argument("--keep-db", action="store_true", help="跑完保留临时 DB 副本(调试用)")
    parser.add_argument("--no-provider", action="store_true",
                        help="不注入假 provider(验「LLM 不可用时机械段照常出报告」那条路径)")
    parser.add_argument("--no-seed-basket", action="store_true",
                        help="D0 零篮子时也不合成篮子(只验市场段)")
    args = parser.parse_args()

    d0 = datetime.strptime(args.report_day, "%Y%m%d").date()
    d1 = datetime.strptime(args.today, "%Y%m%d").date()

    if not settings.db_path.exists():
        logger.error("真实 %s 不存在,无法复制临时副本跑冒烟。", settings.db_path)
        return 1

    tmp_dir = Path(tempfile.mkdtemp(prefix="neckline_smoke_auction_"))
    tmp_db = tmp_dir / "neckline_smoke.db"
    shutil.copy2(settings.db_path, tmp_db)
    logger.info("已复制真实 DB 到临时副本(不污染生产):%s", tmp_db)

    try:
        logger.info("=== 用真实数据生成 %s 报告(供 %s 竞价确认)===", d0, d1)
        bundle = build_report(d0, db_path=tmp_db, save=True)
        logger.info("报告已生成(策略大脑 %s)", bundle.strategy_version)

        from neckline.selection.basket_store import load_baskets_for_date

        baskets = load_baskets_for_date(d0, tiers=(1, 2), db_path=tmp_db)
        logger.info("D0 的 T1/T2 篮子:%d 个 → %s", len(baskets),
                    [b.basket_key for b in baskets])

        from neckline.sentinel.universe import load_watch_universe

        if not baskets and not args.no_seed_basket:
            # 🔴 **V2.4.0 P0 起合成成员不能再取自关注池**:池已缩编成
            # 「持仓 + T1/T2 成员 + 板块指数」,而这条分支的前提恰恰是**零篮子**,
            # 本地开发库又常常零持仓 → `wu.codes` 空 → 合成篮子静默跳过 →
            # **闸 2 一行都走不到,而冒烟看起来照常"跑通"**(实测踩到)。
            # 改为直接从 D0 的 `daily` 分区取前几只有行的票 —— 合成篮子要的本来就是
            # 「有日线可造 MemberMech 的代码」,与关注池无关。
            seed_synthetic_basket(d0, tmp_db, codes=_daily_codes(d0))
            baskets = load_baskets_for_date(d0, tiers=(1, 2), db_path=tmp_db)

        # 合成竞价快照:凡在 `daily` 里有行的代码都给一份;指数没有行 → 拉不到(见模块头)
        wu = load_watch_universe(d1, db_path=tmp_db, parquet_dir=None)
        want = list(dict.fromkeys([c for b in baskets for c in b.member_codes] + list(wu.codes)))
        rows = _daily_rows_lookup(d1, want)
        quotes = {code: synthesize_auction_quote(rows[code]) for code in rows}
        logger.info("合成竞价快照 %d/%d 只(指数与停牌票没有 daily 行,属合成环境局限)",
                    len(quotes), len(want))

        provider = None if args.no_provider else FakeAuctionProvider([b.basket_key for b in baskets])
        now = datetime.combine(d1, time(9, 26, 30))
        logger.info("--- 竞价确认 @ %s(硬截止 %s)---", now.time(),
                    auction_pipeline.AUCTION_HARD_DEADLINE)
        res = auction_pipeline.run_auction_pipeline(
            now, db_path=tmp_db, parquet_dir=None,
            quotes_fn=lambda codes, _q=quotes: {c: _q[c] for c in codes if c in _q},
            provider=provider,
            # ⚠ 冒烟按"刚进窗口"的余量算,不受运行墙钟影响(否则跑到 9:29 之后就永远超时)
            now_fn=lambda: now,
        )
        if not res.ran:
            logger.warning("竞价层未执行(skipped=%s)—— %s 是否真实交易日?",
                           res.skipped_reason, d1)
            return 0
        logger.info("竞价确认结果:确认%d / 中性%d / 否决%d / 待解释%d;命中 D0 失效位 %d 只;"
                    "llm_stage=%s(%sms);推送门槛=%s",
                    res.confirm, res.neutral, res.veto, res.pending,
                    len(res.hit_invalidation_codes), res.llm_stage, res.llm_elapsed_ms,
                    res.should_push)

        rep = auction_store.load_report(d1, db_path=tmp_db)
        logger.info("auction_reports:数据质量=%s 覆盖=%s/%s 篮子数=%s 冻结时刻=%s",
                    rep["data_quality"], rep["fetched_codes"], rep["requested_codes"],
                    rep["baskets_covered"], rep["captured_at"])
        for r in (rep.get("risks_json") or []):
            logger.info("  [异常与风险 %s] %s", r.get("kind"), r.get("text"))
        for v in auction_store.load_verdicts(d1, db_path=tmp_db):
            logger.info("  [篮子 %s|%s] T%s 引擎%s 数据质量%s → verdict=%s(模型原话=%s,"
                        "夹逼=%s)小纸条=%s",
                        v["basket_key"], v["name"], v["covered_tier"], v["engine_version"],
                        v["data_quality"], v["verdict"], v["verdict_raw"], v["clamped_by"],
                        bool(v["manual_note_attached"]))
            # 🔴 用户裁定 P3-69 / P3-70(2026-08-12)的两组读数**打出来**:
            # 历史样本够不够(机械判据 n ≥ 15)+ 相对板块 / 相对市场**分别**减的是什么。
            h = v.get("history_json") or {}
            # ⚠ 篮级那个数是**逐票最小值**(定向复审 🔴-1;原先是全篮日期并集 ——
            # 一只老面孔就能把整篮讲成"够")。逐票明细跟着一起打,⛔ 别只看篮级。
            logger.info("    历史对照:篮内每只票至少 %s 天(窗口 %s 个交易日 / 上界 %s 自然日)→ %s",
                        h.get("history_days_available"), h.get("history_lookback_trading_days"),
                        h.get("history_lookback_days"),
                        "允许比较" if h.get("history_sample_sufficient") else "历史样本不足")
            for e in (h.get("history_days_per_member") or []):
                logger.info("      逐票 %s:%s 天 → %s", e.get("ts_code"),
                            e.get("days_available"),
                            "允许比较" if e.get("sample_sufficient") else "样本不足(只看原始值)")
            for per in ((v.get("rel_strength_json") or {}).get("per_member") or []):
                logger.info("    %s 相对板块 %s(来源 %s%s)· 相对市场 %s(对照 %s%s)",
                            per.get("ts_code"), per.get("rel_to_sector"),
                            per.get("sector_benchmark_source"),
                            f",原因 {per['rel_to_sector_reason']}"
                            if per.get("rel_to_sector_reason") else "",
                            per.get("rel_to_index"), per.get("index_benchmark_code"),
                            f",原因 {per['rel_to_index_reason']}"
                            if per.get("rel_to_index_reason") else "")

        # 第二次跑:验当日防重(⛔ 事后不许补跑的另一半)
        again = auction_pipeline.run_auction_pipeline(
            now, db_path=tmp_db, parquet_dir=None, provider=provider, now_fn=lambda: now)
        logger.info("同日再跑一次 → ran=%s skipped=%s(应为 already_ran)",
                    again.ran, again.skipped_reason)

        # 窗口外跑:验零落库
        out_of_window = auction_pipeline.run_auction_pipeline(
            datetime.combine(d1, time(9, 45)), db_path=tmp_db, parquet_dir=None)
        logger.info("窗口外跑一次 → ran=%s skipped=%s(应为 not_auction_window)",
                    out_of_window.ran, out_of_window.skipped_reason)
        logger.info("=== 竞价确认冒烟结束 ===")
        return 0
    finally:
        if args.keep_db:
            logger.info("--keep-db 已指定,临时 DB 保留:%s", tmp_db)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info("已清理临时 DB 副本。")


if __name__ == "__main__":
    raise SystemExit(main())
