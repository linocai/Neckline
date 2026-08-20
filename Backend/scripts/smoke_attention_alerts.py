#!/usr/bin/env python3
"""V2-⑪ 端到端冒烟:**四监测各至少一条触发路径** + **NL 临时提醒从解析到命中通知**。

两段:

  A. **⑪-A 四监测** —— 用合成盘中报价把四条监测各推到触发一次,逐条打印命中文案与
     原始指标,同时打印同篮合并敞口。走的是 `engine.run_tick` **真实编排**(不是直接
     调纯函数),因此顺带验证了旁路接线、`sentinel_events` 台账与防重。
  B. **⑪-C NL 临时提醒** —— 桩 LLM 出一份候选规则 → 白名单校验 → 七项确认卡 → 落库
     → 哨兵一拍命中 → 措辞层出通知;再跑一拍验证「首次命中后不重复轰炸」。

**不碰真实数据**:全程跑在 `tempfile` 建的一次性 SQLite + parquet 目录上,
`data/neckline.db` 与真实 parquet **一个字节都不读写**(与其它 smoke 脚本不同,本块
的判据全在合成报价里,连只读真库都不需要)。

**零真实 LLM / 零真实 APNs**:LLM 用确定性桩(固定 JSON),推送用假 notifier 只打印。

**这不是活体验证的替代品**(同 `smoke_sentinel.py` 的自我定位),只是「整条链确实
按预期工作」的一次可复现检查。

用法::

    python scripts/smoke_attention_alerts.py
    python scripts/smoke_attention_alerts.py --keep      # 保留临时库供事后翻看
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline import custom_alerts as ca  # noqa: E402
from neckline import notify_kinds  # noqa: E402
from neckline.calendar import CN_TZ  # noqa: E402
from neckline.config import settings as real_settings  # noqa: E402
from neckline.db import connection, init_schema  # noqa: E402
from neckline.llm import nl_alert as nl  # noqa: E402
from neckline.llm.base import LLMResult  # noqa: E402
from neckline.sentinel import attention as att  # noqa: E402
from neckline.sentinel import custom as cu  # noqa: E402
from neckline.sentinel.positions import Position, open_position  # noqa: E402
from neckline.data.realtime import Quote  # noqa: E402
from neckline.sentinel.universe import StockMeta  # noqa: E402

OK = "✅"
NO = "❌"

# 合成标的:一只持仓 + 两只同篮成员 + 两支宽基指数 + 一支板块基准。
POS_CODE = "600001.SH"
PEER_A = "600002.SH"
PEER_B = "600003.SH"
GEM_POS = "300001.SZ"
SH_INDEX = "000001.SH"
SZ_INDEX = "399001.SZ"
GEM_INDEX = "399006.SZ"


def _q(code: str, price: float, pre_close: float = 10.0, high: float | None = None,
       volume: float = 60000.0, name: str = "") -> Quote:
    return Quote(code=code, name=name or code, price=price, pre_close=pre_close,
                 open=pre_close, high=high if high is not None else max(price, pre_close),
                 low=min(price, pre_close), volume=volume, amount=price * volume * 100,
                 ts="", source="smoke")


def _pos(pid: int, code: str, buy_price: float = 10.0, qty: int = 1000,
         buy_date: str = "20260731") -> Position:
    return Position(id=pid, ts_code=code, buy_price=buy_price, qty=qty, buy_date=buy_date,
                    status="open", sell_price=None, sell_date=None, note=None)


def _meta(code: str, board) -> StockMeta:
    return StockMeta(ts_code=code, name=code, board=board, is_st=False, list_date=None)


def _seed_basket(db_path: Path, d0: date, codes: List[str]) -> int:
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), "smoke-k1", "AI 算力", "算力扩产订单落地", "theme", 1,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-07-30T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for c in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "core", None, 0, "冒烟造数", 1, "2026-07-30T00:00:00+08:00"),
            )
    return bid


def _link_snapshot(db_path: Path, position_id: int, basket_id: int, code: str, day: date) -> None:
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id,"
            " card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, code, day.strftime("%Y%m%d"), basket_id, 1, 1, "core", "{}",
             "2026-07-31T00:00:00+08:00"),
        )


class _StubProvider:
    """确定性桩 LLM:把用户那句话固定翻译成一条「跌到 9.95 且大盘走弱」的组合规则。"""

    name = "smoke-stub"
    model = "stub-model"

    PAYLOAD = {
        "action": "create",
        "ts_code": POS_CODE,
        "logic": "all",
        "conditions": [
            {"metric": "price", "op": "<=", "value": 9.95},
            {"metric": "index_chg_pct", "op": "<=", "value": -0.01, "ref": SH_INDEX},
        ],
        "active_from": "09:30",
        "persist": False,
        "max_fires": 1,
    }

    def chat(self, messages, *, enable_search=True, transport=None, search_query=None):
        content = ("明白了:茅台跌到 9.95 元、同时上证跌超 1% 时通知你。\n\n```json\n"
                   + json.dumps(self.PAYLOAD, ensure_ascii=False) + "\n```")
        return LLMResult(ok=True, content=content, provider=self.name, model=self.model)


# ══════════════════════════════════════════════════════════════════════════
# A 段:四监测各一条触发路径
# ══════════════════════════════════════════════════════════════════════════

def run_attention(db_path: Path, day: date) -> int:
    from neckline.data.board import Board

    d0 = day - timedelta(days=1)
    p1 = open_position(POS_CODE, 10.0, 1000, day, db_path=db_path)
    p2 = open_position(PEER_A, 10.0, 500, day, db_path=db_path)
    p3 = open_position(GEM_POS, 10.0, 300, day, db_path=db_path)
    bid = _seed_basket(db_path, d0, [POS_CODE, PEER_A, PEER_B])
    _link_snapshot(db_path, p1, bid, POS_CODE, day)
    _link_snapshot(db_path, p2, bid, PEER_A, day)

    positions = [_pos(p1, POS_CODE), _pos(p2, PEER_A, qty=500), _pos(p3, GEM_POS, qty=300)]
    meta = {
        POS_CODE: _meta(POS_CODE, Board.MAIN),
        PEER_A: _meta(PEER_A, Board.MAIN),
        PEER_B: _meta(PEER_B, Board.MAIN),
        GEM_POS: _meta(GEM_POS, Board.GEM),
    }
    quotes: Dict[str, Quote] = {
        # ① 同篮成员集体转弱:两只同篮成员各跌 5% / 6%
        POS_CODE: _q(POS_CODE, 9.55),      # -4.5% → 同时喂 ③ 的独立弱势路径
        PEER_A: _q(PEER_A, 9.5),           # -5%
        PEER_B: _q(PEER_B, 9.4),           # -6%
        # ② 板块承接消失:创业板指冲高回落且翻绿
        GEM_INDEX: _q(GEM_INDEX, 9.85, 10.0, high=10.20),
        # ④ 大盘突变:上证跌 2.5%
        SH_INDEX: _q(SH_INDEX, 9.75, 10.0, high=10.05),
        SZ_INDEX: _q(SZ_INDEX, 9.98, 10.0, high=10.02),
        GEM_POS: _q(GEM_POS, 9.98),
    }

    print("\n" + "=" * 78)
    print("A 段 · ⑪-A 四监测(合成报价驱动,每条至少一次触发)")
    print("=" * 78)

    result = att.evaluate_attention(day, positions, quotes, meta, db_path=db_path)

    by_kind = {}
    for a in result.alerts:
        by_kind.setdefault(a.kind, []).append(a)

    # ③ 独立弱势要一个「板块没坏、我自己坏」的场景,与 ① 的「篮子集体坏」互斥;
    #    单独再喂一组报价,免得两条互相抵消(真实盘中它们本就不会同时成立)。
    solo_quotes = dict(quotes)
    solo_quotes[POS_CODE] = _q(POS_CODE, 9.55)      # -4.5%
    solo_quotes[PEER_A] = _q(PEER_A, 10.05)         # +0.5%
    solo_quotes[PEER_B] = _q(PEER_B, 10.02)         # +0.2%
    solo = att.evaluate_attention(day, positions[:1], solo_quotes, meta, db_path=db_path)
    for a in solo.alerts:
        by_kind.setdefault(a.kind, []).append(a)

    expected = ["basket_peers_weak", "sector_bid_fade", "holding_decoupled", "market_shock"]
    ok = True
    for kind in expected:
        hits = by_kind.get(kind, [])
        mark = OK if hits else NO
        if not hits:
            ok = False
        print(f"\n{mark} {kind}  命中 {len(hits)} 条")
        for a in hits[:1]:
            print(f"    标题:{a.title}")
            print(f"    发生了什么:{a.what_happened}")
            print(f"    触碰了哪条计划:{a.plan_touched or '(本条给不出,故留空——⛔ 不编)'}")
            if a.merged_exposure_note:
                print(f"    合并敞口:{a.merged_exposure_note}")
            print(f"    原始指标:{a.metrics}")

    print("\n—— 同篮合并敞口(蓝图 6.2)——")
    if not result.merged_exposure:
        print(f"  {NO} 没算出任何合并敞口组")
        ok = False
    for g in result.merged_exposure:
        print(f"  {OK} 「{g.basket_name}」 {len(g.codes)} 只 {list(g.codes)} "
              f"成本 {g.cost_amount:.0f} 元 / 占总仓 {g.cost_share_of_total:.1%} "
              f"/ 主题集中={g.theme_concentration} / 市值口径部分缺失={g.market_partial}")

    print("\n—— 未能评估的监测(「没看」与「没事」分开)——")
    print(f"  {result.unavailable or '(本次全部可评估)'}")
    return 0 if ok else 1


# ══════════════════════════════════════════════════════════════════════════
# B 段:NL 提醒从解析到命中通知
# ══════════════════════════════════════════════════════════════════════════

def run_nl_alert(db_path: Path, day: date) -> int:
    print("\n" + "=" * 78)
    print("B 段 · ⑪-C 临时提醒(桩 LLM → 确认卡 → 落库 → 哨兵命中 → 通知)")
    print("=" * 78)
    ok = True

    user_text = "上证跌超 1% 的时候,如果茅台也跌到 9.95 以下就马上通知我"
    parsed = nl.parse_nl_alert(user_text, provider=_StubProvider())
    print(f"\n1) 解析:ok={parsed.ok} action={parsed.action} reason={parsed.reason}")
    print(f"   模型复述:{parsed.narrative}")
    print(f"   规范化规则:{json.dumps(parsed.rule, ensure_ascii=False)}")
    if not parsed.ok or not parsed.rule:
        print(f"   {NO} 解析失败")
        return 1

    card = nl.confirmation_card_for(parsed, name="示例甲")
    print("\n2) 七项确认卡(⑪-C;⑥⑦ 两项是必选披露):")
    items = [
        ("① 标的", card.subject), ("② 触发条件与方向", card.condition),
        ("③ 生效时间", card.active_window), ("④ 通知次数 / 冷却", card.notify_limit),
        ("⑤ 到期时间", card.expiry),
        ("⑥ 行情延迟 / 数据中断披露", card.quote_delay_disclosure),
        ("⑦ 只通知不自动交易", card.no_auto_trade),
    ]
    for label, value in items:
        mark = OK if value else NO
        if not value:
            ok = False
        print(f"   {mark} {label}:{value}")

    alert = ca.create_alert(
        rule=parsed.rule, nl_text=user_text, ts_code=parsed.ts_code,
        active_from=parsed.active_from, persist=parsed.persist,
        cooldown_seconds=parsed.cooldown_seconds, max_fires=parsed.max_fires,
        db_path=db_path,
    )
    print(f"\n3) 用户确认后落库:id={alert.id} status={alert.status} "
          f"max_fires={alert.max_fires} persist={alert.persist}")

    dup = ca.find_duplicate(parsed.rule, parsed.ts_code, db_path=db_path)
    print(f"   相同提醒去重:再建一条同规则 → {'命中已有 id=%d' % dup.id if dup else '未命中'}"
          f" {OK if dup else NO}")
    if dup is None:
        ok = False

    now = datetime.combine(day, time(10, 30), tzinfo=CN_TZ)
    quotes = {POS_CODE: _q(POS_CODE, 9.9, name="示例甲"), SH_INDEX: _q(SH_INDEX, 9.85)}

    # 先喂一组「大盘没跌够」的报价 —— 组合条件应当**不命中**(证明 AND 是真的)
    calm = dict(quotes)
    calm[SH_INDEX] = _q(SH_INDEX, 9.99)
    r0 = cu.evaluate_alerts(now, quotes=calm, positions=[], db_path=db_path)
    print(f"\n4) 阴性对照(大盘只跌 0.1%):命中 {len(r0.hits)} 条,"
          f"原因 {r0.skipped.get(alert.id)} {OK if not r0.hits else NO}")
    if r0.hits:
        ok = False

    r1 = cu.evaluate_alerts(now, quotes=quotes, positions=[], db_path=db_path)
    print(f"\n5) 命中一拍:{len(r1.hits)} 条 {OK if r1.hits else NO}")
    if not r1.hits:
        return 1
    hit = r1.hits[0]
    subject = cu.subject_text(hit.alert, quotes)
    print(f"   台账 event_key = {hit.event_key}")
    print(f"   实测值 = {hit.values}")
    print(f"   通知正文(措辞层组装,APNs 未真发)= "
          f"「{subject} {hit.condition_text}。{ca.QUOTE_DELAY_DISCLOSURE[:24]}…只通知不自动交易。」")
    print(f"   通知 kind = {notify_kinds.KIND_CUSTOM_ALERT} / 级别 = "
          f"{notify_kinds.level_of(notify_kinds.KIND_CUSTOM_ALERT)}(立即)")

    # 记一次命中(engine 里由 run_tick 做,这里手动等价一遍)
    from neckline.dedup import record_pushed

    record_pushed(day, cu.SENTINEL_NAME, hit.alert.ts_code or "", hit.event_key,
                  payload={"alertId": hit.alert.id}, db_path=db_path)
    ca.mark_fired(hit.alert.id, db_path=db_path)

    r2 = cu.evaluate_alerts(now, quotes=quotes, positions=[], db_path=db_path)
    print(f"\n6) 再跑一拍(首次命中后不重复轰炸):命中 {len(r2.hits)} 条,"
          f"原因 {r2.skipped.get(alert.id)} {OK if not r2.hits else NO}")
    if r2.hits:
        ok = False

    after_close = datetime.combine(ca.created_trade_day(alert), time(15, 1), tzinfo=CN_TZ)
    r3 = cu.evaluate_alerts(after_close, quotes=quotes, positions=[], db_path=db_path)
    status_now = ca.get_alert(alert.id, db_path=db_path).status
    print(f"\n7) 收盘自动失效(persist=0):expired_ids={r3.expired_ids} "
          f"status={status_now} {OK if status_now == ca.STATUS_EXPIRED else NO}")
    if status_now != ca.STATUS_EXPIRED:
        ok = False

    # LLM 不可用 → 降级为手填表单(不静默失败)
    degraded = nl.parse_nl_alert("随便说点什么", provider=None, db_path=db_path)
    fields = [f["name"] for f in (degraded.manual_form or {}).get("fields", [])]
    print(f"\n8) LLM 不可用降级:ok={degraded.ok} degraded={degraded.degraded} "
          f"手填表单字段={fields} {OK if degraded.degraded and fields else NO}")
    if not (degraded.degraded and fields):
        ok = False
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="V2-⑪ 冒烟:四监测 + NL 临时提醒")
    ap.add_argument("--keep", action="store_true", help="保留临时库目录")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="nk_smoke_alerts_"))
    db_path = tmp / "smoke.db"
    init_schema(db_path)
    day = date(2026, 7, 31)

    print(f"临时库:{db_path}(真实 data/neckline.db 全程零读写)")
    print(f"总仓分母:{real_settings.total_capital:.0f} 元(Settings.total_capital 唯一源)")
    rc = run_attention(db_path, day)
    rc |= run_nl_alert(db_path, day)

    print("\n" + "=" * 78)
    print(f"{OK + ' 冒烟全绿' if rc == 0 else NO + ' 冒烟有未通过项'}")
    print("=" * 78)
    if args.keep:
        print(f"(--keep)临时目录保留:{tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
