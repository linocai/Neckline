#!/usr/bin/env python3
"""V2-⑧ 合成盘中冒烟(体例照 `scripts/smoke_sentinel.py`)。今天不是交易日 / 盘中,
无法活体验证「盘中存拍 + 篮子验证状态机」——本脚本拿**真实 D0 锚点**(真实收盘 /
真实 MA20 / 真实跌停价 / 现役章程 stop_pct)冻四张卡,再喂**合成分钟报价**驱动状态
机跑完四态,最后落盘并读回核对 dtype。

**这不是活体验证的替代品**,只是"代码路径确实按预期工作"的一次有真实数据支撑的
冒烟检查(同 `smoke_sentinel.py` 的自我定位)。

**不污染真实数据**:整份 `data/neckline.db` 先复制到临时副本,篮子 / 卡 / 验证流水
全写在副本;parquet **只读**真实目录,存拍落盘写到临时 parquet 目录。跑完清理。

用法::

    python scripts/smoke_basket_verify.py
    python scripts/smoke_basket_verify.py --d0 20260723 --keep
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from neckline.calendar import next_trading_day  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.data.market_data import (  # noqa: E402
    TABLE_FLOAT_COLS, day_file_path, get_market_slice, get_stock_history,
)
from neckline.db import connection  # noqa: E402
from neckline.selection import basket_card as bc  # noqa: E402
from neckline.selection import verification_rules as vr  # noqa: E402
from neckline.selection.basket_store import save_basket_card  # noqa: E402
from neckline.sentinel import basket_verify as bv  # noqa: E402
from neckline.sentinel import basket_verify_store as bvs  # noqa: E402
from neckline.sentinel import capture  # noqa: E402
from neckline.data.realtime import Quote  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_basket_verify")

# 合成分钟检查点(与 smoke_sentinel 的三检查点同精神:头 / 中 / 尾)
CHECKPOINTS = [(9, 31), (10, 35), (13, 30), (14, 59)]


def _real_ma20(code: str, d0: date) -> Optional[float]:
    """真实 MA20(最近 20 个交易日**前复权**收盘均值)。

    ⚠ **必须走前复权**(`apply_qfq`,同 `data/panel.py::add_features` 的口径):
    拿原始价算 20 日均线,区间内有过除权的票会算出与现价差 30%+ 的"均线",冒烟一跑
    满屏 `falsified` —— 那是冒烟脚本的锅,不是状态机的。生产里 ⑦ 的 MA20 来自 ⑤ 的
    `MechContext` 前复权面板,本函数只是冒烟侧的等价粗算。
    """
    from neckline.data.adjust import apply_qfq

    hist = get_stock_history(code, date(d0.year - 1, 1, 1), d0, table="daily")
    if hist.is_empty():
        return None
    adj = get_stock_history(code, date(d0.year - 1, 1, 1), d0, table="adj_factor")
    if not adj.is_empty():
        hist = hist.join(adj.select(["ts_code", "trade_date", "adj_factor"]),
                         on=["ts_code", "trade_date"], how="left")
        hist = apply_qfq(hist)
        closes = hist.sort("trade_date")["close_qfq"].to_list()[-20:]
    else:
        closes = hist.sort("trade_date")["close"].to_list()[-20:]
    closes = [c for c in closes if c is not None]
    return round(sum(closes) / len(closes), 2) if closes else None


def _pick_codes(d0: date, n: int = 6) -> List[Tuple[str, float]]:
    """挑几只**真实**主板票(收盘价适中、有量),返回 `(ts_code, D0 收盘)`。"""
    df = get_market_slice(d0, table="daily")
    df = df.filter(
        (pl.col("close") > 8.0) & (pl.col("close") < 40.0) & (pl.col("amount") > 200000.0)
        & pl.col("ts_code").str.starts_with("60")
    ).sort("amount", descending=True).head(n)
    return [(r["ts_code"], float(r["close"])) for r in df.iter_rows(named=True)]


def _seed_basket(db: Path, d0: date, key: str, name: str, tier: int,
                 members: List[Tuple[str, float]], stop_pct: float, *,
                 with_card: bool = True) -> int:
    """造一个冒烟篮子;`with_card=False` 时**根本不生成卡**,用来演「有篮子无卡」这个
    合法中间态。

    ⚠ 原写法是「先照常发卡、再对冻结表 `basket_cards` 下一条 DELETE 抹掉」—— 仓里因此
    真的存在一条打冻结表的删除语句(契约线审计 🟡 Y1 点名),而三律守门当时只扫
    `neckline/`、看不见它。**不要为了造中间态去删冻结行**:不发卡本来就是这个中间态的
    真实成因(⑦ 的 LLM 不可用 / 预算耗尽 / 生成失败),照它演才是对的。

    ⚠⚠ 顺带记一条:冻结守门是**纯文本 grep**(见 `tests/test_v2_schema_guard.py`),
    连注释与 docstring 里的那四个字面短语也一并拦 —— 讲解这件事时得绕开写法本身,
    别把守门的靶子写进散文里。这是"钝但强"换来的代价,是刻意的。
    """
    with connection(db) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), key, name, "冒烟合成驱动", "theme", tier,
             "smoke", 1, "v1.3.3", "auto", "ok", datetime.now().isoformat(timespec="seconds")),
        )
        bid = int(cur.lastrowid)
        for code, _close in members:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, code, "core", None, 0, "冒烟", 1, datetime.now().isoformat(timespec="seconds")),
            )

    if not with_card:
        logger.info("    (故意不发卡:演「有篮子无卡」合法中间态)")
        return bid

    mechs = list(bc.build_member_mech(
        {c: px for c, px in members}, d0, stop_pct=stop_pct,
        ma20_of={c: _real_ma20(c, d0) for c, _ in members}, db_path=db,
    ).values())
    card = {
        "spec_version": bc.CARD_SPEC_VERSION,
        "verification_spec": bc.build_verification_spec(key, d0, mechs,
                                                        next_trade_date=next_trading_day(d0)),
        "invalidation_spec": bc.build_invalidation_spec(key, d0, mechs, stop_pct=stop_pct,
                                                        next_trade_date=next_trading_day(d0)),
        "fingerprint": {"stop_pct": stop_pct,
                        "verification_ruleset_version": vr.VERIFICATION_RULESET_VERSION},
    }
    save_basket_card(bid, card, stop_pct=stop_pct, db_path=db)
    for m in mechs:
        logger.info("    锚 %s:D0 收盘 %.2f / MA20 %s / 跌停 %s / 止损线 %s",
                    m.ts_code, m.close or 0.0, m.ma20, m.limit_down, m.stop_price)
    return bid


def _quote(code: str, price: float, low: float, cum_vol: float, pre_close: float) -> Quote:
    """⚠ `pre_close` 是**当日固定值**(D0 真实收盘,不随分钟检查点变化)——⛔ 不能传
    `price`。⑧-E 上线前 `Quote.pre_close` 未被 `basket_verify` 消费,拿移动的
    `price` 顶替只是个无害的占位;⑧-E 之后 `basket_verify` 会拿它跟卡里的
    `ref_close`(=真实 D0 收盘)比对锚有效性,传移动的 `price` 会让每一拍都"看起来
    除权",把整份合成剧本打成假阳性锚失效(施工期真踩过,四态齐的剧本会被打成清一色
    `unclear`)。"""
    return Quote(code=code.split(".")[0], name=code, price=round(price, 2),
                 pre_close=round(pre_close, 2), open=price, high=price, low=round(low, 2),
                 volume=cum_vol, amount=cum_vol * price * 100.0, ts="合成", source="synthetic")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--d0", default="20260723", help="基准日 YYYYMMDD(默认 20260723)")
    ap.add_argument("--keep", action="store_true", help="保留临时目录(调试用)")
    args = ap.parse_args()

    d0 = datetime.strptime(args.d0, "%Y%m%d").date()
    d1 = next_trading_day(d0)
    if not settings.db_path.exists():
        logger.error("真实 %s 不存在,无法复制临时副本。", settings.db_path)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="neckline_smoke8_"))
    db = tmp / "smoke.db"
    pq = tmp / "parquet"
    pq.mkdir()
    shutil.copy2(settings.db_path, db)
    logger.info("临时库 %s;临时 parquet %s(真实 parquet 只读)", db, pq)

    try:
        stop_pct, _tpr = bc.resolve_charter_pcts(db)
        logger.info("=== D0=%s → D1=%s;现役章程 stop_pct=%s(系统算止损线,禁硬编)===",
                    d0, d1, stop_pct)
        picks = _pick_codes(d0, 8)
        if len(picks) < 8:
            logger.error("真实数据里挑不出足够的票(%d 只),换个 --d0 试试。", len(picks))
            return 1
        logger.info("真实成员样本:%s", ", ".join(f"{c}@{px:.2f}" for c, px in picks))

        # 五个篮子**成员互不重叠**:同一只票分到两个篮里,剧本价会互相打架(第一版
        # 冒烟就踩过 —— "全员站稳"篮里混进了"破位"篮的票,一开盘就被定格成 falsified)。
        baskets = {
            "verified": _seed_basket(db, d0, "smk-v", "冒烟·全员站稳", 1, picks[0:2], stop_pct),
            "partial": _seed_basket(db, d0, "smk-p", "冒烟·只对一半", 1, picks[2:5], stop_pct),
            "unclear": _seed_basket(db, d0, "smk-u", "冒烟·中间地带", 2, [picks[5]], stop_pct),
            "falsified": _seed_basket(db, d0, "smk-f", "冒烟·破位证伪", 2, [picks[6]], stop_pct),
        }
        # 「有篮子无卡」= **压根没发卡**(不是发了再删,见 `_seed_basket` 的 docstring)
        baskets["no_card"] = _seed_basket(db, d0, "smk-n", "冒烟·有篮无卡", 2, [picks[7]],
                                          stop_pct, with_card=False)

        refs = {k: v for k, v in baskets.items()}
        member_of: Dict[int, List[Tuple[str, float]]] = {
            baskets["verified"]: picks[0:2], baskets["partial"]: picks[2:5],
            baskets["unclear"]: [picks[5]], baskets["falsified"]: [picks[6]],
            baskets["no_card"]: [picks[7]],
        }

        # —— 合成盘中:每个检查点给每只票一个"剧本价" ——————————————————————
        # 剧本(相对 D0 收盘):verified 篮全员 +2%;partial 篮只有第一只 +2%、其余 −1%;
        # unclear 篮 −1%(守住结构但没跟上);falsified 篮先砸破止损线、尾盘再拉回
        # (用来演「当日终态不撤回」)。
        verified_codes = {c for c, _ in picks[0:2]}          # 全员站稳 → verified
        partial_lead = picks[2][0]                            # 三只里只有它站稳 → partial
        falsified_code = picks[6][0]                          # 早盘砸破止损线,尾盘拉回

        def price_for(code: str, base: float, cp_idx: int) -> Tuple[float, float]:
            if code in verified_codes or code == partial_lead:
                return base * 1.02, base * 1.01
            if code == falsified_code:
                # 前两拍 −7%(破 −5% 止损线)→ 后两拍拉回 +2%:演「当日终态不撤回」
                return (base * 0.93, base * 0.93) if cp_idx <= 1 else (base * 1.02, base * 0.93)
            return base * 0.99, base * 0.985                  # 中间地带(没跌破、也没跟上)

        capture.reset_capture_state()
        capture.record_auction_snapshot(
            d1, datetime.combine(d1, time(9, 25)),
            {c: _quote(c, px * 1.005, px * 1.005, 100.0, pre_close=px) for c, px in picks},
            requested=len(picks),
        )
        for idx, (hh, mm) in enumerate(CHECKPOINTS):
            now = datetime.combine(d1, time(hh, mm))
            quotes = {}
            for c, base in picks:
                p, lo = price_for(c, base, idx)
                quotes[c] = _quote(c, p, lo, 1000.0 * (idx + 1), pre_close=base)
            capture.record_intraday_tick(d1, now, quotes)
            res = bv.run_intraday_verification(d1, quotes, attempted_codes=[c for c, _ in picks],
                                               now=now, db_path=db)
            logger.info("--- %02d:%02d 判定 %d 篮,落 %d 行(定格跳过 %d):%s",
                        hh, mm, res.evaluated, res.rows_written, res.skipped_latched,
                        {k: res.states.get(v) for k, v in refs.items()})

        # —— 15:05 一次性落盘 + capture_status ————————————————————————————
        flush = capture.flush_day(d1, db_path=db, parquet_dir=pq,
                                  now=datetime.combine(d1, time(15, 5)))
        logger.info("存拍落盘:ticks=%d(%s,覆盖 %d/%d 分钟)auction=%d(%s)errors=%s",
                    flush.tick_rows, flush.tick_status, flush.covered_minutes,
                    flush.expected_minutes, flush.auction_rows, flush.auction_status,
                    flush.errors)

        for table in ("intraday_ticks", "auction_snapshots"):
            path = day_file_path(table, d1, pq)
            if not path.exists():
                logger.error("  %s 分区没落成:%s", table, path)
                return 1
            df = pl.read_parquet(path)
            bad = [c for c in TABLE_FLOAT_COLS[table] if df.schema[c] != pl.Float64]
            logger.info("  读回 %s:%d 行 / %d 列;声明的数值列 dtype %s",
                        table, df.height, df.width, "全部 Float64 ✓" if not bad else f"不合声明 {bad}")
            if bad:
                return 1
            logger.info("  样例行:%s", df.head(1).to_dicts())

        # —— EOD 那一拍:真实 D1 收盘价代入同一份 spec ————————————————————
        eod = bv.run_eod_verification(d1, db_path=db, parquet_dir=None)
        logger.info("=== EOD(真实 %s 收盘价)判定 %d 篮,落 %d 行 ===", d1, eod.evaluated,
                    eod.rows_written)
        for label, bid in refs.items():
            cur = bvs.current_state(bid, d1, db_path=db)
            rows = bvs.list_rows(bid, d1, db_path=db)
            logger.info("  [%s] basket_id=%d → 当前 **%s**(%s);流水:%s", label, bid,
                        cur.state, cur.label,
                        " → ".join(f"{r.source}:{r.state}" for r in rows))

        states_seen = {bvs.current_state(b, d1, db_path=db).state for b in refs.values()}
        intraday_states = set()
        for bid in refs.values():
            intraday_states.update(r.state for r in bvs.list_rows(bid, d1, db_path=db)
                                   if r.source == "intraday")
        logger.info("=== 盘中出现过的状态:%s;收盘定论状态:%s ===",
                    sorted(intraday_states), sorted(states_seen))
        missing = set(vr.STATES) - intraday_states
        if missing:
            logger.warning("四态未跑全,缺:%s(剧本没覆盖到,不算失败但要看一眼)", sorted(missing))
        return 0
    finally:
        capture.reset_capture_state()
        if args.keep:
            logger.info("--keep:临时目录保留在 %s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            logger.info("已清理临时目录。")


if __name__ == "__main__":
    raise SystemExit(main())
