#!/usr/bin/env python3
"""V2-⑨ 端到端冒烟(真实历史交易日,隔离库)。把 **④ 扫描 → ⑤ 聚合 → ⑥ 定档 →
⑦ 冻卡 → ⑧ D+1 验证 → ⑨ 盘后复盘 → 周度校准报告** 整条链在真实数据上跑通一遍,
并把某一篮的**机械判九项**逐项打印出来供人工核对。

**这不是活体验证的替代品**(同 `smoke_sentinel.py` / `smoke_basket_verify.py` 的
自我定位),只是"整条链确实按预期工作"的一次有真实数据支撑的冒烟检查。

**不污染真实数据**:`data/neckline.db` 先 `sqlite3.backup` 出一份临时副本,策略包
激活 / 篮子 / 卡 / 验证流水 / 复盘全写在副本上;**真实 parquet 全程只读**。跑完清理。

**零真实 LLM**:⑤ 的检索段与推理段都喂**确定性桩**(由当日真实种子拼出的固定
JSON),⑦ 的卡 LLM 段与 ⑨ 的复盘解释段一律 `use_llm=False` / `provider=None`
—— 本脚本要验的是机械链路,不是模型输出。

用法::

    python scripts/smoke_basket_review.py                        # D0=20260723 → D1=20260724
    python scripts/smoke_basket_review.py --d0 20260722 --keep
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import next_trading_day  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.eval import calibration  # noqa: E402
from neckline.llm.budget import BudgetLedger  # noqa: E402
from neckline.review import basket_review as br  # noqa: E402
from neckline.scan import cluster, corr, leader  # noqa: E402
from neckline.scan.seeds import generate_seeds  # noqa: E402
from neckline.selection import aggregate as agg  # noqa: E402
from neckline.selection import basket_card as bc  # noqa: E402
from neckline.selection import tier as tr  # noqa: E402
from neckline.selection.basket_store import (  # noqa: E402
    load_baskets_for_date, save_basket_cards, save_tier_decision,
)
from neckline.selection.pack import activate_pack, get_active_pack, load_pack_file  # noqa: E402
from neckline.sentinel import basket_verify as bv  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_basket_review")

MAX_STUB_BASKETS = 4        # 桩最多编几个篮子(够验链路,不追求真实选股质量)
MAX_STUB_MEMBERS = 3        # 每篮最多几个成员(与 ⑤ 的成员上限同量级)


class _Result:
    def __init__(self, content: str):
        self.ok, self.content, self.reason = True, content, "stub"


class StubProvider:
    """确定性 LLM 桩:按 system prompt 分辨检索段 / 推理段。

    **推理段的成员一律从 user 上下文里"读"出来**(解析「成员清单」那几行),不是从
    种子原始 `member_codes` 拿 —— 后者没过 ⑤-b 的成员卫生线闸,喂回去会被成员白名单
    闸整条拒收(施工时真踩过:4 条提案全灭)。这也更像一个真实模型的行为:**只能从
    给它看的清单里选**。

    ⚠ 桩的存在是为了让 ⑤ 那两段"有输出",**不是**为了模拟模型判断力。同一天跑两次
    逐位相同。
    """

    _SEED_RE = re.compile(r"──\s*种子编号\s*(\S+)\|类型\s*(\S+)\|名称\s*(.*)")
    _CODE_RE = re.compile(r"\b(\d{6}\.(?:SZ|SH|BJ))\b")

    def __init__(self, max_baskets: int = MAX_STUB_BASKETS, max_members: int = MAX_STUB_MEMBERS):
        self.max_baskets, self.max_members = max_baskets, max_members
        self.search_calls = 0
        self.reason_calls = 0

    def _baskets_from_context(self, text: str) -> Dict[str, Any]:
        baskets: List[Dict[str, Any]] = []
        seed_key = seed_kind = label = None
        collecting = False
        members: List[str] = []

        def flush():
            if seed_key and members and len(baskets) < self.max_baskets:
                picked = members[: self.max_members]
                # V2.2-③:引擎归属按篮子序**轮转 C→Z→Y**(⛔ 不是"挑一个最容易过的"):
                # 三条引擎线的机械关阈值不同,轮转才能让冒烟真的走过三套分支;真实
                # 模型是按语义选,桩只需给出**形状正确且确定性**的主张,机械对拍照跑。
                engine = ("C", "Z", "Y")[len(baskets) % 3]
                baskets.append({
                    "name": f"冒烟·{label}",
                    "driver": f"{label} 当日共振(冒烟桩,非真实驱动判断)",
                    "driver_kind": {"hot_industry": "theme", "surging_concept": "theme",
                                    "limit_cluster": "limit_cluster",
                                    "anomaly_cluster": "rotation"}.get(seed_kind, "theme"),
                    "engine_code": engine,
                    "why_now": "冒烟桩:今天该组票同步放量",
                    # ② 驱动关四问的另外三问(V2.2-③ 起同一次调用一并产出,
                    # LLM 调用增量 = 0;桩缺答会让驱动关 degrade,那验的是别的分支)。
                    "common_trait": "冒烟桩:同题材、同日放量",
                    "persistence": "冒烟桩:资金承接尚未见衰减",
                    "strengthen_and_invalidate": "冒烟桩:再出政策则强化,龙头炸板则证伪",
                    "evidence_conflicts": "",
                    "seed_keys": [seed_key],
                    "members": [{"ts_code": c, "role": "leader" if i == 0 else "core",
                                 "reason": "冒烟桩理由"} for i, c in enumerate(picked)],
                })

        for line in text.splitlines():
            m = self._SEED_RE.search(line)
            if m:
                flush()
                seed_key, seed_kind, label = m.group(1), m.group(2), m.group(3).strip()
                members, collecting = [], False
                continue
            if "成员清单" in line:
                collecting = True
                continue
            if collecting:
                found = self._CODE_RE.findall(line)
                if found and line.strip().startswith("·"):
                    members.extend(found)
                elif not line.strip().startswith("·"):
                    collecting = False
        flush()
        return {"baskets": baskets}

    def chat(self, messages, **kw):
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in messages if m.role == "user"), "")
        if "检索员" in system:
            self.search_calls += 1
            # V2.2-③ 证据关按 `evidence_kind` 归并计独立份数(同来源同类只算一份)。
            # **按种子键 crc32 奇偶给两种成色**:一半给 3 份不同类来源(够 C1 的
            # `independent_evidence_min=3`)、一半只给 1 份 —— 让一次冒烟同时走通
            # 「证据充分」与「证据不足 → 证据关 degrade」两条路(⛔ 不是为了让结果
            # 好看而全给足;那样降级分支永远测不到)。
            seed_line = next((ln for ln in user.splitlines() if "待查题材" in ln), user[:40])
            rich = zlib.crc32(seed_line.encode("utf-8")) % 2 == 0
            items = [{"claim": "冒烟桩:某部委发布产业扶持政策(非真实新闻,勿当事实)",
                      "source": "smoke_stub_政策", "date": "2026-07-23", "url": ""}]
            if rich:
                items += [
                    {"claim": "冒烟桩:上市公司公告签订重大合同(非真实新闻)",
                     "source": "smoke_stub_公告", "date": "2026-07-22", "url": ""},
                    {"claim": "冒烟桩:媒体报道产业链排产回升(非真实新闻)",
                     "source": "smoke_stub_媒体", "date": "2026-07-21", "url": ""},
                ]
            body = {"driver_hint": "冒烟桩:该题材当日有资金与消息面共振", "evidence": items}
            return _Result("这是检索段的叙述。\n\n```json\n"
                           + json.dumps(body, ensure_ascii=False) + "\n```")
        self.reason_calls += 1
        payload = self._baskets_from_context(user)
        return _Result("这是推理段的叙述。\n\n```json\n"
                       + json.dumps(payload, ensure_ascii=False) + "\n```")


def _fmt(x: Any, pct: bool = False) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—" if x is None else str(x)
    return f"{float(x) * 100:+.2f}%" if pct else f"{float(x):.4f}"


def _print_gates(gate_out: Any, decision: Any, db: Path) -> None:
    """六关判定 + ③b 逐行打印(**人工核对用**:每个候选卡在哪一关、差多少,
    与定档篮的引擎归属,都要一眼看得出来 —— plan §五 ③ 验收原文的那三句)。"""
    print(f"\n{'=' * 78}")
    print(f"③ 六道关口(引擎线 {list(gate_out.engines)},骨架 {gate_out.skeleton_version})")
    print(f"{'=' * 78}")
    for key in sorted(gate_out.summaries):
        s = gate_out.summaries[key]
        head = (f"· {s.name or key}|{key}|引擎 {s.engine_code}×{s.engine_version}"
                f"({s.engine_source})")
        print(head + (f"  ⛔ 除名:{s.exclusion_reason}" if s.excluded else "  ✅ 留在正式候选"))
        for c in s.checks:
            who = f"[{c.ts_code}]" if c.ts_code else "[篮]"
            gap = ""
            if c.score is not None and c.threshold is not None:
                gap = f"  读数 {c.score:g} / 阈值 {c.threshold:g}"
            mark = {"pass": "过", "degrade": "降", "reject": "拒"}[c.verdict]
            avail = "" if c.available else "  (判定输入缺失:不拦、但不给 T1)"
            print(f"    {mark} {c.gate:9s}{who:14s} {c.reason}{gap}{avail}")
        if s.removed_members:
            print(f"    ⚠ 位置关对拍出篮:{[(r.ts_code, r.reason) for r in s.removed_members]}")

    print(f"\n{'-' * 78}\n③b 今日未定档披露(名 / 分 / 卡在哪一关 / 差多少 / 原因码)")
    if not decision.dropped:
        print("  今日无未定档篮子(**已算过**)。")
    for d in decision.dropped:
        print(f"  · {d.name or d.basket_key}|机械分 {d.mech_score:.3f}|关 {d.gate or '—'}"
              f"|{d.gate_detail or '—'}|`{d.reason}`")

    print(f"\n{'-' * 78}\n定档篮(每个成员继承篮子引擎;库里读回)")
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT b.basket_key, b.name, b.tier, b.engine_code, b.engine_version, "
            "b.skeleton_version, GROUP_CONCAT(m.ts_code) FROM baskets b "
            "LEFT JOIN basket_members m ON m.basket_id = b.id GROUP BY b.id "
            "ORDER BY b.tier, b.basket_key"
        ).fetchall()
        n_gate_rows = conn.execute("SELECT COUNT(*) FROM gate_evaluations").fetchone()[0]
    finally:
        conn.close()
    if not rows:
        print("  今日无篮子定档(**合法输出**:门槛制下没有候选过关就是空档)。")
    for bk, name, tier, ec, ev, sk, members in rows:
        print(f"  · T{tier} {name}|{bk}|engine {ec}×{ev}|skeleton {sk}|成员 {members}")
    print(f"\n  gate_evaluations 留痕 {n_gate_rows} 行。")


def _print_nine(review: br.BasketReview) -> None:
    """机械判九项逐项打印(**人工核对用**:每一项都要能一眼看出"这个数是怎么来的")。"""
    m = review.mech
    meta = m["meta"]
    print(f"\n{'=' * 78}")
    print(f"机械判九项 · {review.name}(basket_key {review.basket_key},T{review.tier},"
          f"{review.depth})")
    print(f"  D0={meta['d0']} → 复盘日={meta['review_date']};成员 {meta['members']}")
    print(f"  分层键:pack={meta['pack_version']} / ruleset={meta['verification_ruleset_version']}")
    print(f"{'=' * 78}")

    a = m["auction_vs_script"]
    print(f"① 竞价 vs 剧本:{'可判' if a['available'] else '算不出(' + str(a['unavailable_reason']) + ')'}")
    print(f"   竞价/开盘中位 {_fmt(a['gap_median'], True)}(来源 {a['source']})→ 落「{a['branch']}」分支")
    print(f"   卡上该分支剧本:{'有' if a['script_present'] else '无'};卡上共有分支 {a['scripts_branches_on_card']}")
    for code, row in a["per_member"].items():
        print(f"     · {code}:{_fmt(row['gap'], True)} → {row['branch']}")

    o = m["open_direction"]
    print(f"② 开盘首方向:跳空中位 {_fmt(o['gap_median'], True)}({o['gap_dir']}),"
          f"日内中位 {_fmt(o['intraday_median'], True)}({o['intraday_dir']}),同向={o['aligned']}")
    print(f"   有盘中存拍={o['has_intraday_capture']}(无存拍时 first_tick_dir 恒为 None)")

    f3 = m["mfe_mae"]
    print(f"③ 分时 MFE/MAE:MFE 中位 {_fmt(f3['mfe_median'], True)} / MAE 中位 {_fmt(f3['mae_median'], True)}")
    print(f"   **数据来源 {f3['mfe_source']}**;存拍台账 status={f3['capture_status']} "
          f"recorded={f3['capture_recorded']} covered={f3['capture_covered_minutes']}/"
          f"{f3['capture_expected_minutes']} empty={f3['capture_empty_ticks']}")
    if f3["note"]:
        print(f"   ⚠ {f3['note']}")
    for code, row in f3["per_member"].items():
        print(f"     · {code}:MFE {_fmt(row['mfe'], True)} @ {row['mfe_at'] or '时刻未知'};"
              f"MAE {_fmt(row['mae'], True)} @ {row['mae_at'] or '时刻未知'}(source {row['source']})")

    al = m["member_alignment"]
    align_txt = "—" if al["alignment"] is None else f"{float(al['alignment']):.0%}"
    print(f"④ 成员同向率:{al['observed']}/{al['member_count']} 只有行情 → "
          f"涨 {al['up']} / 跌 {al['down']} / 平 {al['flat']};同向率 {align_txt}"
          f";占多数方向 {al['dominant_direction']};缺数据 {al['missing']}")
    for code, r in al["returns"].items():
        print(f"     · {code}:{_fmt(r, True)}")

    lp = m["leader_pull"]
    print(f"⑤ 龙头带动:卡上龙头 {lp['leaders'] or '(认不出)'};龙头 "
          f"{_fmt(lp['leader_ret_median'], True)} vs 其余 {_fmt(lp['others_ret_median'], True)};"
          f"差 {_fmt(lp['spread'], True)};带住={lp['led']};无对照组={lp['no_peer_group']}")

    b = m["buyability"]
    print(f"⑥ 可买性:{b['buyable']}/{b['member_count']} 只买得进;"
          f"一字 {b['one_word']} / 收在涨停 {b['limit_up']} / 无行情 {b['no_bar']}")
    for code, row in b["per_member"].items():
        print(f"     · {code}:{row['reason']}(涨停价 {row['limit_up']},来源 {row['limit_up_source']})")

    v = m["verification_timing"]
    print(f"⑦ 验证与证伪时点:当前 **{v['state']}**({v['state_label']});流水 {v['rows']} 行"
          f"(盘中 {v['intraday_rows']},有 EOD 定论={v['has_eod_verdict']} → {v['eod_state']})")
    print(f"   首次 verified {v['first_verified_at'] or '未发生'};"
          f"首次 falsified {v['first_falsified_at'] or '未发生'};定格={v['latched_falsified']}")

    rs = m["close_rs"]
    print(f"⑧ 收盘 RS:大盘({rs['index_code']}){_fmt(rs['index_ret'], True)};"
          f"篮子超额中位 {_fmt(rs['excess_median'], True)};跑赢 {rs['outperformers']} 只;"
          f"rs_positive={rs['rs_positive']}")

    tv = m["tier_vs_outcome"]
    print(f"⑨ D0 判断 vs 今日结果:T{tv['tier']} 档内第 {tv['rank_in_tier']} 位"
          f"(机械分 {tv['mech_score']});五维 {(tv['tier_breakdown'] or {}).get('dims')}")
    print(f"   今日篮子收益中位 {_fmt(tv['basket_ret_median'], True)};"
          f"当日 {tv.get('day_baskets')} 篮里 D0 序第 {tv.get('rank_by_tier')} / "
          f"结果序第 {tv.get('rank_by_outcome')}(名次差 {tv.get('rank_gap')})")
    print(f"   ⚠ {tv.get('rank_note')}")
    print(f"{'=' * 78}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--d0", default="20260723", help="基准日 YYYYMMDD(默认 20260723)")
    ap.add_argument("--keep", action="store_true", help="保留临时目录(调试用)")
    ap.add_argument("--draws", type=int, default=30, help="安慰剂臂抽样次数(冒烟用小值)")
    args = ap.parse_args()

    d0 = datetime.strptime(args.d0, "%Y%m%d").date()
    d1 = next_trading_day(d0)
    if not settings.db_path.exists():
        logger.error("真实 %s 不存在,无法复制临时副本。", settings.db_path)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="neckline_smoke9_"))
    db = tmp / "smoke.db"
    src = sqlite3.connect(str(settings.db_path))
    dst = sqlite3.connect(str(db))
    src.backup(dst)
    dst.close()
    src.close()
    logger.info("临时库 %s(真实 parquet 只读,真实库全程不写)", db)

    try:
        # —— 前置:激活 K8 骨架包 + 三条引擎线(V2.2-③:六道关口按引擎包阈值分支,
        # 零运行引擎 = 当日不产任何候选 —— 冒烟必须四线齐)——————————————————
        packs_dir = Path(__file__).resolve().parent.parent / "packs"
        if get_active_pack(db_path=db) is None:
            doc = load_pack_file(packs_dir / "K8-skeleton.json")
            p = activate_pack(doc["manifest"], doc["config"], via="smoke", db_path=db)
            logger.info("[前置] 隔离库激活骨架包 %s", p.pack_version)
        from neckline.selection.pack import get_active_engines
        if not get_active_engines(db_path=db):
            for fname in ("C1.json", "Z1.json", "Y1.json"):
                doc = load_pack_file(packs_dir / fname)
                p = activate_pack(doc["manifest"], doc["config"], via="smoke", db_path=db)
                logger.info("[前置] 隔离库激活引擎包 %s(线 %s)", p.pack_version, p.line_code)

        # —— ④ 市场扫描层(V2.2 起含 ② 行情状态 + ③-C 落地起跳两张预计算表)————
        logger.info("=== ④ 扫描层批算(D0=%s)===", d0)
        logger.info("  cluster %s", cluster.refresh_limit_clusters([d0], db_path=db))
        logger.info("  corr    %s", corr.refresh_corr_matrix([d0], db_path=db))
        logger.info("  leader  %s", leader.refresh_leader_structure([d0], db_path=db))
        try:
            from neckline.scan.regime_store import refresh_market_regime
            logger.info("  regime  %s", refresh_market_regime([d0], db_path=db))
        except Exception:  # noqa: BLE001  缺行由六关按「不拦不给 T1」披露
            logger.warning("  regime 批算失败(六关按缺行处理)", exc_info=True)
        # 🔴 **`industry_strength_daily` 必须排在 landing 之前**(顺序不是摆设):
        #   · ③ 板块关吃它的**名次 + 近 5 日强度日**;
        #   · ⑤ 位置关的**判据 4 RS5**(相对所属行业中位 5 日超额)也读它 ——
        #     该表为空时全市场 `c4.rs5=na` → `c4` 永远判不出 → **`liftoff_confirmed`
        #     整个市场恒为 0 → T1 结构性不可达**(每行都诚实写着 na,但没人汇总,
        #     是一次**静默的系统级降级**)。生产靠 16:05 日更保证它先落地;隔离库是
        #     真库副本、该表可能为空,故这里先补算 D0 及其前 4 个交易日。
        try:
            from neckline.calendar import prev_trading_day
            from neckline.report.industry_strength_store import refresh_industry_strength
            win, cur = [d0], d0
            for _ in range(4):
                cur = prev_trading_day(cur)
                win.append(cur)
            logger.info("  strength %s", refresh_industry_strength(
                sorted(win), db_path=db, parquet_dir=settings.parquet_dir))
        except Exception:  # noqa: BLE001
            logger.warning("  行业强度补算失败(板块关 unavailable + 位置关 RS5 恒 na)",
                           exc_info=True)
        try:
            from neckline.scan.landing_store import refresh_landing_states
            logger.info("  landing %s", refresh_landing_states([d0], db_path=db))
        except Exception:  # noqa: BLE001
            logger.warning("  landing 批算失败(位置关按缺行处理)", exc_info=True)
        seed_set = generate_seeds(d0, db_path=db)
        if seed_set is None or not seed_set.all_seeds():
            logger.error("D0=%s 没有种子,换一天试试。", d0)
            return 1
        logger.info("  种子 %d 颗(hot_industry %d / surging_concept %d / limit_cluster %d / "
                    "anomaly_cluster %d)", len(seed_set.all_seeds()),
                    len(seed_set.hot_industry), len(seed_set.surging_concept),
                    len(seed_set.limit_cluster), len(seed_set.anomaly_cluster))

        # —— ⑤ 驱动聚合(桩 LLM)————————————————————————————————
        logger.info("=== ⑤ 驱动聚合(确定性桩,零真实 LLM)===")
        stub = StubProvider()
        result = agg.aggregate_baskets(d0, seed_set=seed_set, db_path=db,
                                       search_provider=stub, reason_provider=stub,
                                       ledger=BudgetLedger())
        logger.info("  篮子 %d 个;桩调用 检索 %d 次 / 推理 %d 次;notes=%s",
                    len(result.baskets), stub.search_calls, stub.reason_calls,
                    result.notes[:3])
        if not result.baskets:
            logger.error("⑤ 没产出篮子(notes=%s),冒烟到此为止。", result.notes)
            return 1

        # —— ③ 六道关口 + ⑥ Tier 定档(V2.2 门槛制)——————————————————————
        logger.info("=== ③ 六道关口 + ⑥ Tier 分层引擎(门槛制,离线不调 LLM)===")
        from neckline.selection import gates as gt
        gate_out = gt.evaluate_day(result, d0, db_path=db)
        logger.info("  关口:候选 %d,除名 %d,引擎线 %s;留痕 %d 行",
                    len(gate_out.summaries), len(gate_out.excluded_summaries()),
                    list(gate_out.engines),
                    gt.save_gate_evaluations(gate_out, db_path=db))
        # ⚠ 传**对拍前**的 result(被除名候选只活在 summaries 里,⑥ 靠它出 ③b)。
        decision = tr.score_and_tier(result, d0, db_path=db, use_llm=False,
                                     gates_outcome=gate_out)
        result = decision.gated_result

        # —— ⑦ 卡先构建(四件套判定要看卡;事务 2 仍在事务 1 之后)—————————————
        logger.info("=== ⑦ 篮子卡构建(use_llm=False)===")
        tentative_kept = [b for b in result.baskets
                          if b.basket_key in decision.tier_by_basket_key()]
        tier_by_key_obj = {d.basket_key: d for d in decision.decisions}
        cards = bc.build_cards(tentative_kept, d0, db_path=db, use_llm=False,
                               tier_by_basket_key=tier_by_key_obj)
        missing_by_key = {c.basket_key: bc.trade_plan_missing_pieces(c.to_card_json())
                          for c in cards}
        decision = tr.enforce_plan_completeness(decision, missing_by_key)

        tier_by_key = {d.basket_key: d.tier for d in decision.decisions}
        hist_by_key = {
            d.basket_key: {"basket_key": d.basket_key, "tier": d.tier,
                           "mech_score": d.mech_score, "mech_breakdown": d.breakdown,
                           "rank_in_tier": d.rank_in_tier, "rank_mech": d.rank_mech,
                           "llm_rank_delta": d.llm_rank_delta, "llm_reason": d.llm_reason,
                           "pack_version": decision.pack_version}
            for d in decision.decisions
        }
        # `AggregateResult` 是 frozen dataclass:未定档的篮子要用 `replace` 剔掉,
        # 不能就地赋值(`baskets.tier` NOT NULL,⑥ 的 `dropped` 今日不落库)。
        kept = [b for b in result.baskets if b.basket_key in tier_by_key]
        result = dataclasses.replace(result, baskets=tuple(kept))
        stats1 = save_tier_decision(result, tier_by_basket_key=tier_by_key,
                                    tier_history_by_basket_key=hist_by_key, db_path=db, via="smoke")
        logger.info("  定档 %s(③b %d);事务 1 落库 %s",
                    # V2.1-②:按引擎现役档位统计(⛔ 别写死 —— 写死 3 会打印一个恒为 0 的幽灵档)
                    {f"T{t}": sum(1 for d in decision.decisions if d.tier == t) for t in tr.TIERS},
                    len(getattr(decision, "dropped", []) or []),
                    {k: v for k, v in stats1.items() if k != "frozen_conflicts"})
        _print_gates(gate_out, decision, db)

        # —— ⑦ 事务 2:落卡(tier 机械字段对齐最终裁定)—————————————————————
        refs = load_baskets_for_date(d0, db_path=db)
        id_by_key = {r.basket_key: r.basket_id for r in refs}
        dec_by_key = {d.basket_key: d for d in decision.decisions}
        final_cards = []
        for c in cards:
            d = dec_by_key.get(c.basket_key)
            if d is None:
                continue
            if (c.tier, c.rank_in_tier, c.rank_mech) != (d.tier, d.rank_in_tier, d.rank_mech):
                c = dataclasses.replace(c, tier=d.tier, rank_in_tier=d.rank_in_tier,
                                        rank_mech=d.rank_mech,
                                        tier_breakdown=dict(d.breakdown or {}))
            final_cards.append(c)
        cards = final_cards
        by_id = {id_by_key[c.basket_key]: c.to_card_json() for c in cards
                 if c.basket_key in id_by_key}
        meta = {id_by_key[c.basket_key]: {"stop_pct": c.stop_pct,
                                          "take_profit_retrace": c.take_profit_retrace,
                                          "charter_version": c.charter_version,
                                          "pack_version": c.pack_version,
                                          "engine_api_version": c.engine_api_version}
                for c in cards if c.basket_key in id_by_key}
        stats2 = save_basket_cards(by_id, meta_by_basket_id=meta, db_path=db)
        logger.info("  卡 %d 张;事务 2 落库 %s", len(by_id),
                    {k: v for k, v in stats2.items() if k != "frozen_conflicts"})

        # —— ⑧ D+1 EOD 验证(真实收盘价)——————————————————————————
        logger.info("=== ⑧ D+1=%s EOD 验证(真实收盘价)===", d1)
        vres = bv.run_eod_verification(d1, db_path=db)
        counts: Dict[str, int] = {}
        for st in vres.states.values():
            counts[st] = counts.get(st, 0) + 1
        logger.info("  判定 %d 篮 → %s;落 %d 行", vres.evaluated,
                    ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), vres.rows_written)

        # —— ⑨ 盘后复盘(机械判九项,零 LLM)——————————————————————
        logger.info("=== ⑨ 盘后复盘(机械判九项,provider=None)===")
        rres = br.review_day(d1, d0=d0, db_path=db, use_llm=False)
        logger.info("  复盘 %d 篮(full %d / brief %d);落库 新增 %d / 已存在 %d",
                    len(rres.reviews),
                    sum(1 for r in rres.reviews if r.depth == br.DEPTH_FULL),
                    sum(1 for r in rres.reviews if r.depth == br.DEPTH_BRIEF),
                    rres.rows_inserted, rres.rows_existing)
        for n in rres.notes:
            logger.warning("  ⚠ %s", n)
        if not rres.reviews:
            return 1

        # 幂等:同日重跑 = no-op
        again = br.review_day(d1, d0=d0, db_path=db, use_llm=False)
        logger.info("  重跑一次 → 新增 %d / 已存在 %d(每日一行幂等)%s",
                    again.rows_inserted, again.rows_existing,
                    f";差异 {again.notes}" if again.notes else ";零差异")

        # —— 人工核对:挑成员最多的那一篮,逐项打印 ————————————————
        pick = max(rres.reviews, key=lambda r: (len(r.mech["meta"]["members"]), r.basket_key))
        _print_nine(pick)

        # —— 周度校准报告(含两条对照臂)——————————————————————————
        logger.info("=== ⑨-C / ⑨-C2 周度校准报告(draws=%d)===", args.draws)
        rep = calibration.build_report(d0, d0, db_path=db, draws=args.draws)
        print(calibration.render_markdown(rep))
        return 0
    finally:
        if args.keep:
            logger.info("临时目录保留:%s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
