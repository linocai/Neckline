"""②驱动聚合层 `neckline/selection/aggregate.py` 单测(plan §五 V2-⑤ 验收逐条)。

覆盖(与 plan 验收清单一一对应):
    ① **成员白名单闸** —— 喂一只不在成员集里的码 → **整条拒收** + WARNING;
       白名单 = **实际展示给 LLM 的那份清单**(截断后的),不是"理论上的全体成分"。
    ② **角色对拍闸** —— LLM 角色 ≠ `leader_structure_daily.role_mech` →
       `role_conflict=1` 且**两说并存**(两个字段都在,不静默采信任一方)。
    ③ **无驱动文本 / 零证据 → 不成篮**;检索**缺席**与检索**空手**是两回事。
    ④ **主归属唯一性** —— 一票两篮 → `is_primary` 恰一个 1,取行业闸 lift 最高的那篮。
    ⑤ **检索段故障 → 篮子仍出且 `evidence_status` 如实**;**推理段缺席 → 不成篮**。
    ⑥ 两段式编排:两段各走各的 provider、各扣各的预算账、检索产物进推理上下文。
    ⑦ 日期锚(`prompt_context` 唯一实现)出现在两段 user 消息第一行。
    ⑧ v1.5.1 劫持案回归:自由文本里写「结论:否决」不影响本块任何判定
       (本块**不复用** `_parse_verdict`,结构化产出走独立解析层)。
    ⑨ 落库:tier 必填 fail loud、幂等重放不覆盖、三律列(role_conflict/is_primary)落对。
    ⑩ 第〇原则守门:本模块不 import 哨兵、不读写纪律参数。
"""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

from neckline.llm.base import LLMResult
from neckline.llm.budget import LEDGER_REASON, LEDGER_REVIEW, LEDGER_SEARCH, BudgetLedger
from neckline.scan.seeds import DriverSeed, SeedSet
from neckline.scan import seeds as seeds_mod
from neckline.selection import aggregate as ag
from neckline.selection import member_hygiene as mh
from tests.conftest import (
    insert_stock_basic,
    insert_trade_cal,
    write_daily_fixture,
    write_flat_parquet,
)

D0 = date(2024, 4, 8)
D0_S = "20240408"


# ══════════════════════════════════════════════════════════════════════════
# 桩与构件
# ══════════════════════════════════════════════════════════════════════════

class _StubProvider:
    """最小假 provider(不经 httpx),把 `chat()` 的返回值设死,专注测本模块自己的
    编排/解析/降级/闸门逻辑(网络细节见 `test_llm.py`)。同 `test_judge.py::
    _StubProvider` 体例。"""

    name = "stub"
    model = "stub-model"

    def __init__(self, replies: Sequence[LLMResult] | LLMResult, *, raises: bool = False) -> None:
        self._replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self._raises = raises
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls.append({
            "messages": list(messages),
            "enable_search": enable_search,
            "search_query": search_query,
        })
        if self._raises:
            raise RuntimeError("模拟供应商炸了")
        idx = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[idx]


def _fenced(payload: Dict[str, Any], narrative: str = "一段自由叙述。") -> str:
    return narrative + "\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _search_reply(items: Sequence[Dict[str, Any]], *, hint: str = "共同驱动") -> LLMResult:
    return LLMResult(
        ok=True, provider="stub-search", model="stub-model",
        content=_fenced({"driver_hint": hint, "evidence": list(items)}, "查到了一些东西。"),
    )


_EV = [{"claim": "工信部发文推动固态电池中试线", "source": "工信部", "date": "2026-07-31", "url": "http://x"}]


def _reason_reply(baskets: Sequence[Dict[str, Any]], narrative: str = "今天把两个题材归并了。") -> LLMResult:
    return LLMResult(
        ok=True, provider="stub-reason", model="stub-model",
        content=_fenced({"baskets": list(baskets)}, narrative),
    )


def _seed(
    key: str, *, kind: str = seeds_mod.SURGING_CONCEPT, label: str = "固态电池",
    members: Sequence[str] = ("600001.SH", "600002.SH"), evidence: Optional[Dict[str, Any]] = None,
) -> DriverSeed:
    return DriverSeed(seed_key=key, seed_kind=kind, label=label,
                      member_codes=tuple(members), evidence=evidence or {"pct_change": 6.0})


def _seedset(*seeds_: DriverSeed, pack_version: str = "test-pack-v1") -> SeedSet:
    buckets: Dict[str, List[DriverSeed]] = {
        seeds_mod.HOT_INDUSTRY: [], seeds_mod.SURGING_CONCEPT: [],
        seeds_mod.LIMIT_CLUSTER: [], seeds_mod.ANOMALY_CLUSTER: [],
    }
    for s in seeds_:
        buckets[s.seed_kind].append(s)
    return SeedSet(
        trade_date=D0_S, pack_version=pack_version,
        hot_industry=tuple(buckets[seeds_mod.HOT_INDUSTRY]),
        surging_concept=tuple(buckets[seeds_mod.SURGING_CONCEPT]),
        limit_cluster=tuple(buckets[seeds_mod.LIMIT_CLUSTER]),
        anomaly_cluster=tuple(buckets[seeds_mod.ANOMALY_CLUSTER]),
    )


def _basket_payload(
    *, name: str = "固态电池", driver: str = "工信部推动中试线落地",
    seed_keys: Sequence[str] = ("s1",), members: Optional[Sequence[Dict[str, Any]]] = None,
    driver_kind: str = "policy", why_now: str = "文件昨天下发",
) -> Dict[str, Any]:
    # ⚠ `members=[]` 与 `members=None` 必须分开(空列表是"故意给 0 只"的测试意图,
    # 不能被 `or` 兜底成默认成员——这正是本项目「『没有』与『没看』要分得开」那条
    # 纪律在测试脚手架层面的同一个坑)。
    if members is None:
        members = [{"ts_code": "600001.SH", "role": "leader", "reason": "板块内唯一有中试线的"}]
    return {
        "name": name, "driver": driver, "driver_kind": driver_kind, "why_now": why_now,
        "seed_keys": list(seed_keys), "members": list(members),
    }


def _run(env, seed_set: SeedSet, *, search=None, reason=None, ledger=None, **kw) -> ag.AggregateResult:
    _ensure_hygiene_defaults(env, seed_set)
    return ag.aggregate_baskets(
        D0, seed_set=seed_set, db_path=env.db_path, parquet_dir=env.parquet_dir,
        search_provider=search, reason_provider=reason, ledger=ledger, **kw,
    )


# ══════════════════════════════════════════════════════════════════════════
# ⑤-b 卫生线闸的测试脚手架默认值(2026-08-02 追加子项)
# ══════════════════════════════════════════════════════════════════════════
#
# ⑤-b 之前,`_run()` 从不需要一个**真的注册在 DB 里**的策略包(`seed_set.
# pack_version` 只是一个归因用的字符串);⑤-b 起 `aggregate_baskets()` 要拿这个
# 包的 `config.seeds.{stock_hygiene,non_new_stock,k4_advisory_gate}` 参数才能跑
# 三原语,包不存在会 fail closed(整批候选拒收)。**既有 55 个测试函数里只有 3 个
# 显式 `insert_stock_basic`**——绝大多数根本不关心卫生线,是在测白名单闸/对拍闸/
# 两段式降级/预算/落库这些正交的东西,不该因为没登记这张参考表就被拦光。
#
# 故 `_run()` 统一在真正跑编排之前**补齐两处默认值,`INSERT OR IGNORE` 语义**
# (已有的行——含测试故意写的"脏"行,如 ST 名/无行业——一律不覆盖):
#   ① 一个与 `seed_set.pack_version` 同名的现役包,三原语参数**照抄
#      `packs/K4-pack.json` 的现值**(不是另拟一套"宽松测试值"——那样会让测试环境
#      的卫生线判据与生产实际用的阈值不是同一回事)。
#   ② 种子成分里出现过、`stock_basic` 里还没有的码,补一行"干净"默认(非 ST /
#      主板 / 早已上市)。
#
# 这两处默认值本身**不会让任何码通过 ma20/amount_ma20/K4 检查而"造假通过"**——
# 绝大多数测试根本没铺 `daily`/K4 价量历史,`member_hygiene` 对这两项的既定行为
# 就是"面板缺失 → 降级为不拦"(不是这里额外放水);本节只解决"tier-1 便宜数据源
# 完全没注册"这一件事。**要专门测卫生线拒收路径的测试,自己显式
# `insert_stock_basic`/自己注册一个更严的包覆盖这两处默认。**
_HYGIENE_TEST_PACK_CONFIG: Dict[str, Any] = {
    "seeds": {
        "stock_hygiene": {
            "close_min": 2.0, "amount_ma20_min": 20000.0, "require_ma20": True,
            "allowed_boards": ["MAIN", "GEM", "STAR"], "exclude_st": True,
        },
        "non_new_stock": {"min_days": 120},
        "k4_advisory_gate": {"hard_cut_action": "exclude", "avoid_flag_action": "tag"},
    },
    # `config.tier` 是包 schema 必需段,本文件不测 ⑥ Tier 引擎,给一个能过校验的
    # 最小占位即可(⑤-b/⑤-c 都不读这一段)。
    "tier": {"weights": {"placeholder": 1.0}, "dims": ["placeholder"]},
}


def _ensure_test_pack(env, pack_version: str) -> None:
    from neckline.selection import pack as pack_mod

    manifest = {
        "pack_version": pack_version, "name": "⑤-b 测试脚手架包",
        "date": "2024-04-08", "engine_api_version": ag.engine_api.ENGINE_API_VERSION,
        "evidence_ref": [],
    }
    try:
        pack_mod.activate_pack(manifest, _HYGIENE_TEST_PACK_CONFIG, via="seed", db_path=env.db_path)
    except ValueError:
        pass  # 同版本已注册过内容相同的包(幂等重放),或已是唯一现役包,不是错误


def _ensure_stock_basic_defaults(env, seed_set: SeedSet) -> None:
    from neckline.db import init_schema

    codes = sorted({c for s in seed_set.all_seeds() for c in s.member_codes})
    if not codes:
        return
    init_schema(db_path=env.db_path)
    conn = sqlite3.connect(str(env.db_path))
    try:
        for code in codes:
            conn.execute(
                "INSERT OR IGNORE INTO stock_basic "
                "(ts_code,symbol,name,industry,market,list_date,delist_date,list_status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (code, code.split(".")[0], code, None, "主板", "20100101", None, "L"),
            )
            # 既有行(含测试自己显式 `insert_stock_basic` 写的)若没给 `list_date`
            # ——⑤-b 之前这张表从没人读这一列,既有三处 `insert_stock_basic` 调用
            # 全部只给了 `ts_code`/`industry` 就没管它,NULL 会被 `non_new_stock`
            # 原语判"次新"而拦下,不是那些测试的原意。补一个"早已上市"的默认值,
            # **只在 `list_date IS NULL` 时补**,不覆盖测试已显式给出的值(要测
            # 次新拒收路径的测试自己显式传一个贴近 D0 的 `list_date` 覆盖)。
            conn.execute(
                "UPDATE stock_basic SET list_date=? WHERE ts_code=? AND list_date IS NULL",
                ("20100101", code),
            )
        conn.commit()
    finally:
        conn.close()


def _ensure_hygiene_defaults(env, seed_set: SeedSet) -> None:
    _ensure_test_pack(env, seed_set.pack_version)
    _ensure_stock_basic_defaults(env, seed_set)


def _insert_leader_rows(env, rows: Sequence[Dict[str, Any]]) -> None:
    """直接写 ④ 的 `leader_structure_daily`(本块**只读**它,对拍闸的机械侧来源)。"""
    from neckline.db import init_schema

    init_schema(db_path=env.db_path)
    conn = sqlite3.connect(str(env.db_path))
    try:
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO leader_structure_daily "
                "(trade_date, cluster_key, ts_code, rs_rank, limit_height, amount_share, role_mech, computed_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (r.get("trade_date", D0_S), r["cluster_key"], r["ts_code"], r.get("rs_rank"),
                 r.get("limit_height"), r.get("amount_share"), r["role_mech"], "2026-08-02T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# ① 成员白名单闸
# ══════════════════════════════════════════════════════════════════════════

class TestWhitelistGate:
    def test_fabricated_member_rejects_whole_proposal_with_warning(self, isolated_env, caplog):
        env = isolated_env
        insert_trade_cal(env, [D0])
        s = _seed("s1", members=("600001.SH", "600002.SH"))
        payload = _basket_payload(members=[
            {"ts_code": "600001.SH", "role": "leader", "reason": "真成员"},
            {"ts_code": "999999.SZ", "role": "core", "reason": "凭空冒出来的"},
        ])
        with caplog.at_level(logging.WARNING):
            r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                     reason=_StubProvider(_reason_reply([payload])))
        # **整条拒收**,不是"丢掉那一只、留下另一只"
        assert r.baskets == ()
        assert [x.reason for x in r.rejected] == [ag.REJECT_FABRICATED_MEMBER]
        assert "999999.SZ" in r.rejected[0].detail
        assert any("白名单闸" in rec.message for rec in caplog.records)

    def test_unknown_seed_key_rejects_whole_proposal(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        s = _seed("s1")
        payload = _basket_payload(seed_keys=["s1", "ghost-seed"])
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([payload])))
        assert r.baskets == ()
        assert [x.reason for x in r.rejected] == [ag.REJECT_UNKNOWN_SEED]

    def test_whitelist_is_exactly_the_shortlist_shown_to_the_llm(self, isolated_env, monkeypatch):
        """白名单 = **系统实际展示给 LLM 的那份清单**(截断后的),不是理论全体成分。
        把展示上限压到 1,种子里排第二的那只就不在白名单内 → 选它即整条拒收。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}])
        write_daily_fixture(env, "daily", D0, [
            {"ts_code": "600001.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1.0, "amount": 9_000_000.0, "pct_chg": 1.0},
            {"ts_code": "600002.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1.0, "amount": 1_000_000.0, "pct_chg": 1.0},
        ])
        monkeypatch.setattr(ag, "MAX_MEMBERS_IN_CONTEXT", 1)
        s = _seed("s1", members=("600001.SH", "600002.SH"))
        payload = _basket_payload(members=[
            {"ts_code": "600002.SH", "role": "leader", "reason": "成交额小的那只,没被展示"},
        ])
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([payload])))
        assert [x.reason for x in r.rejected] == [ag.REJECT_FABRICATED_MEMBER]

    def test_shortlist_orders_by_amount_desc_then_code_and_missing_amount_last(self):
        ctx = ag.MechContext(trade_date=D0, amount_of={"600002.SH": 5.0, "600003.SH": 9.0})
        got = ag._shortlist(["600001.SH", "600002.SH", "600003.SH"], ctx, 10)
        # 成交额降序;缺成交额的 600001 排最后(-inf,**不当 0**)
        assert got == ("600003.SH", "600002.SH", "600001.SH")


# ══════════════════════════════════════════════════════════════════════════
# ①-b(2026-08-02 planner 裁定)· 成员卫生线闸在编排层的接线
#
# `member_hygiene.py` 自身的原语级行为(两级保险丝各自路径、与 ③ 原语 `run()`
# 交叉断言)见 `tests/test_selection_member_hygiene.py`;本节只测**接线是否接对**:
# 过滤是否真的发生在截断之前、是否真的当日只装配一次、降级 notes 是否如实透出。
# ══════════════════════════════════════════════════════════════════════════

class TestMemberHygieneWiring:
    def test_filtering_happens_before_truncation_not_after(self, isolated_env, monkeypatch):
        """**验收核心**:先过滤再截断,不是先截断再过滤。三只票按成交额降序本该是
        A(最高)/B/C;A 是 ST 票。若"先截断再过滤"(旧序,BUG),`MAX_MEMBERS_
        IN_CONTEXT=2` 会先选出 [A,B] 再筛掉 A,C 永远没机会露面;若"先过滤再截断"
        (⑤-b 定死的正确序),A 先被卫生线剔除,剩 [B,C] 再截断到 2 只,C 应该在场。
        用白名单闸的可观察行为反证:提案只选 C,C 在不在白名单决定它是被接受还是
        被当"凭空冒出来的"整条拒收。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [
            {"ts_code": "600001.SH", "name": "ST吉祥", "list_date": "20100101"},  # A:最高成交额,但 ST
            {"ts_code": "600002.SH", "list_date": "20100101"},                    # B:干净
            {"ts_code": "600003.SH", "list_date": "20100101"},                    # C:干净,成交额最低
        ])
        write_daily_fixture(env, "daily", D0, [
            {"ts_code": "600001.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1.0, "amount": 300.0, "pct_chg": 1.0},
            {"ts_code": "600002.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1.0, "amount": 200.0, "pct_chg": 1.0},
            {"ts_code": "600003.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1.0, "amount": 100.0, "pct_chg": 1.0},
        ])
        monkeypatch.setattr(ag, "MAX_MEMBERS_IN_CONTEXT", 2)
        s = _seed("s1", members=("600001.SH", "600002.SH", "600003.SH"))
        payload = _basket_payload(members=[
            {"ts_code": "600003.SH", "role": "leader", "reason": "成交额最低那只"},
        ])
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([payload])))
        assert r.rejected == ()   # C 在白名单里,提案被接受,不是 REJECT_FABRICATED_MEMBER
        assert len(r.baskets) == 1
        assert r.baskets[0].members[0].ts_code == "600003.SH"
        # 顺带确认 ST 票确实被卫生线剔了(不是巧合通过)
        assert any(x.ts_code == "600001.SH" and x.primitive == mh.REJECT_STOCK_HYGIENE
                   for x in r.hygiene_rejected)

    def test_hygiene_applied_exactly_once_per_day_not_once_per_basket(self, isolated_env, monkeypatch):
        """当日装配次数 = 1(防每篮/每种子重算)。种子数 > 1、成分有重叠,
        `apply_member_hygiene` 只应被调用一次。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [
            {"ts_code": "600001.SH", "list_date": "20100101"},
            {"ts_code": "600002.SH", "list_date": "20100101"},
        ])
        calls: List[int] = []
        real = mh.apply_member_hygiene

        def _counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        monkeypatch.setattr(mh, "apply_member_hygiene", _counting)
        s1 = _seed("s1", members=("600001.SH",))
        s2 = _seed("s2", kind=seeds_mod.HOT_INDUSTRY, members=("600001.SH", "600002.SH"))
        _run(env, _seedset(s1, s2), search=_StubProvider(_search_reply(_EV)),
             reason=_StubProvider(_reason_reply([])))
        assert len(calls) == 1

    def test_hygiene_and_k4_unavailable_notes_disclosed_when_data_absent(self, isolated_env):
        """既没铺 `daily`/K4 价量历史 → ma20/amount_ma20/K4 三项都算不出,**降级
        为不拦 + 如实披露**(P0-23 定案),不是静默当"都合格"。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        s = _seed("s1", members=("600001.SH",))
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload()])))
        assert "hygiene_unavailable" in r.notes
        assert len(r.baskets) == 1   # 没被拦,只是标了降级


# ══════════════════════════════════════════════════════════════════════════
# ② 角色对拍闸
# ══════════════════════════════════════════════════════════════════════════

class TestRoleCrossCheck:
    def test_conflict_marked_and_both_sides_kept(self, isolated_env, caplog):
        env = isolated_env
        insert_trade_cal(env, [D0])
        _insert_leader_rows(env, [
            {"cluster_key": "cl-1", "ts_code": "600001.SH", "role_mech": "elastic", "rs_rank": 7},
        ])
        s = _seed("cl-1", kind=seeds_mod.LIMIT_CLUSTER, members=("600001.SH",))
        payload = _basket_payload(seed_keys=["cl-1"], members=[
            {"ts_code": "600001.SH", "role": "leader", "reason": "我认为它是龙头"},
        ])
        with caplog.at_level(logging.WARNING):
            r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                     reason=_StubProvider(_reason_reply([payload])))
        m = r.baskets[0].members[0]
        assert m.role_conflict == 1
        # **两说并存**:LLM 侧与机械侧都在,谁也没被抹掉
        assert (m.role_llm, m.role_mech, m.rs_rank) == ("leader", "elastic", 7)
        assert any("角色对拍闸" in rec.message for rec in caplog.records)

    def test_agreement_is_not_a_conflict(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        _insert_leader_rows(env, [
            {"cluster_key": "cl-1", "ts_code": "600001.SH", "role_mech": "leader", "rs_rank": 1},
        ])
        s = _seed("cl-1", kind=seeds_mod.LIMIT_CLUSTER, members=("600001.SH",))
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(seed_keys=["cl-1"])])))
        m = r.baskets[0].members[0]
        assert (m.role_conflict, m.role_mech) == (0, "leader")

    def test_mech_unknown_is_not_a_conflict_and_stores_null(self, isolated_env):
        """机械侧 `unknown`(RS20 算不出)**不是一种角色** —— 不能拿"没判定"去跟
        LLM 对拍判冲突,落库应是 `role_mech=NULL` + `role_conflict=0`。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        _insert_leader_rows(env, [
            {"cluster_key": "cl-1", "ts_code": "600001.SH", "role_mech": "unknown", "rs_rank": None},
        ])
        s = _seed("cl-1", kind=seeds_mod.LIMIT_CLUSTER, members=("600001.SH",))
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(seed_keys=["cl-1"])])))
        m = r.baskets[0].members[0]
        assert (m.role_mech, m.role_conflict) == (None, 0)

    def test_no_mech_row_at_all_is_not_a_conflict(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload()])))
        m = r.baskets[0].members[0]
        assert (m.role_mech, m.role_conflict) == (None, 0)

    def test_mech_role_prefers_row_from_the_declared_cluster_seed(self, isolated_env):
        """一只票同时在多个簇里 → 优先取本篮声明的那个簇的行(可复现,不看运气)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        _insert_leader_rows(env, [
            {"cluster_key": "aaa-other", "ts_code": "600001.SH", "role_mech": "leader", "rs_rank": 1},
            {"cluster_key": "zzz-mine", "ts_code": "600001.SH", "role_mech": "core", "rs_rank": 3},
        ])
        s = _seed("zzz-mine", kind=seeds_mod.LIMIT_CLUSTER, members=("600001.SH",))
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(seed_keys=["zzz-mine"])])))
        m = r.baskets[0].members[0]
        assert (m.role_mech, m.rs_rank) == ("core", 3)


# ══════════════════════════════════════════════════════════════════════════
# ③ 篮子规则:驱动文本 / 证据链 / 成员数 / 角色枚举
# ══════════════════════════════════════════════════════════════════════════

class TestBasketRules:
    def test_empty_driver_text_does_not_form_a_basket(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(driver="   ")])))
        assert r.baskets == ()
        assert [x.reason for x in r.rejected] == [ag.REJECT_NO_DRIVER]

    def test_searched_but_zero_evidence_does_not_form_a_basket(self, isolated_env, caplog):
        """**检索跑过、空手而归** → 不成篮(仅历史相关性不足以成篮)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        with caplog.at_level(logging.WARNING):
            r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply([])),
                     reason=_StubProvider(_reason_reply([_basket_payload()])))
        assert r.baskets == ()
        assert [x.reason for x in r.rejected] == [ag.REJECT_NO_EVIDENCE]
        assert r.evidence_by_seed["s1"].status == ag.EVIDENCE_OK   # 检索**真的跑了**

    @pytest.mark.parametrize("members,expect", [
        ([], ag.REJECT_MEMBER_COUNT),
        ([{"ts_code": f"60000{i}.SH", "role": "core", "reason": "x"} for i in range(1, 5)],
         ag.REJECT_MEMBER_COUNT),
    ])
    def test_member_count_must_be_1_to_3(self, isolated_env, members, expect):
        env = isolated_env
        insert_trade_cal(env, [D0])
        s = _seed("s1", members=tuple(f"60000{i}.SH" for i in range(1, 5)))
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(members=members)])))
        assert [x.reason for x in r.rejected] == [expect]

    def test_duplicate_member_in_one_basket_is_malformed(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(members=[
                     {"ts_code": "600001.SH", "role": "leader", "reason": "a"},
                     {"ts_code": "600001.SH", "role": "core", "reason": "b"},
                 ])])))
        assert [x.reason for x in r.rejected] == [ag.REJECT_MALFORMED]

    def test_role_outside_enum_is_rejected(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(members=[
                     {"ts_code": "600001.SH", "role": "跟风", "reason": "a"},
                 ])])))
        assert [x.reason for x in r.rejected] == [ag.REJECT_BAD_ROLE]

    def test_bad_driver_kind_falls_back_to_seed_kind_without_rejecting(self, isolated_env):
        """分类标签写错 ≠ 成员选择不可信 → 机械兜底 + 留痕,**不整条拒收**。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        s = _seed("s1", kind=seeds_mod.SURGING_CONCEPT)
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload(driver_kind="题材")])))
        b = r.baskets[0]
        assert (b.driver_kind, b.driver_kind_fallback) == ("theme", True)
        assert b.driver_kind in ag.DRIVER_KINDS

    def test_duplicate_basket_key_second_proposal_rejected_not_silently_dropped(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        s = _seed("s1")
        p1 = _basket_payload(name="固态电池")
        p2 = _basket_payload(name="固态·电池", driver="换了句话说同一件事")
        r = _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([p1, p2])))
        assert len(r.baskets) == 1
        assert [x.reason for x in r.rejected] == [ag.REJECT_DUPLICATE_KEY]

    def test_empty_baskets_array_is_a_legal_answer(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([])))
        assert r.baskets == () and r.rejected == ()
        assert r.reason_stage == ag.STAGE_OK   # 跑了、明说今天没有,不是降级


# ══════════════════════════════════════════════════════════════════════════
# ④ 主归属(行业闸 lift)
# ══════════════════════════════════════════════════════════════════════════

def _two_basket_env(env) -> SeedSet:
    """两颗种子共用 600001.SH(半导体)。A 篮的成分里半导体高度富集(lift 高),
    B 篮里半导体只是零头(lift 低)→ 主归属应落在 A。**两篮成分池都 ≥5 只**
    (⑤-c `MIN_LIFT_SAMPLE_SIZE` 门槛,2026-08-02 追加),都达标,比的是正常
    lift 比较路径——门槛本身生效时"小簇 vs 大概念"的回归场景见
    `_small_basket_vs_qualified_basket_env`(不复用/不修改本函数,两者是不同
    的测试意图)。"""
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, (
        [{"ts_code": "600001.SH", "industry": "半导体"}, {"ts_code": "600002.SH", "industry": "半导体"}]
        + [{"ts_code": f"60001{i}.SH", "industry": "白酒"} for i in range(0, 8)]
    ))
    a = _seed("s-a", label="A题材",
              members=("600001.SH", "600002.SH", "600010.SH", "600011.SH", "600012.SH"))
    b = _seed("s-b", label="B题材", kind=seeds_mod.HOT_INDUSTRY,
              members=("600001.SH", "600011.SH", "600012.SH", "600013.SH", "600014.SH"),
              evidence={"industry_rank": 3})
    return _seedset(a, b)


# —— ⑤-c 专用构件(直接构造 `BasketCandidate`/`MechContext`,不经 DB/LLM,
#    同 `test_lift_tie_breaks_deterministically_by_basket_key` 既有体例)————————

_SEMI_BAIJIU_INDUSTRY_OF: Dict[str, str] = {
    "600001.SH": "半导体", "600002.SH": "半导体",
    **{f"60001{i}.SH": "白酒" for i in range(0, 8)},
}


def _semi_baijiu_ctx() -> ag.MechContext:
    """与 `_two_basket_env` 同一份行业/市场占比口径(半导体 2 只、白酒 8 只),
    直接灌进 `MechContext`、不经 DB——⑤-c 的 `assign_primary` 单测只关心 lift
    计算与门槛,不需要走卫生线/落库那一整套。"""
    ctx = ag.MechContext(trade_date=D0)
    ctx.industry_of = dict(_SEMI_BAIJIU_INDUSTRY_OF)
    ctx.market_shares = ag.market_industry_shares(ctx.industry_of)
    return ctx


def _basket_stub(key: str, name: str, seed_keys: Sequence[str], member_codes: Sequence[str]) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=name, driver="d", driver_kind="theme",
        why_now="w", seed_keys=tuple(seed_keys),
        members=tuple(ag.BasketMemberCandidate(c, "leader", None, 0, "r") for c in member_codes),
        evidence=(), evidence_status=ag.EVIDENCE_OK, pack_version="p",
        engine_api_version=1, charter_version="v1.3.3",
    )


class TestPrimaryAttribution:
    def test_same_code_two_baskets_exactly_one_primary_and_it_is_the_higher_lift(self, isolated_env):
        env = isolated_env
        ss = _two_basket_env(env)
        payloads = [
            _basket_payload(name="A篮", seed_keys=["s-a"], members=[
                {"ts_code": "600001.SH", "role": "leader", "reason": "a"}]),
            _basket_payload(name="B篮", seed_keys=["s-b"], members=[
                {"ts_code": "600001.SH", "role": "core", "reason": "b"}]),
        ]
        r = _run(env, ss, search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply(payloads)))
        by_name = {b.name: b for b in r.baskets}
        assert len(r.baskets) == 2
        flags = [(b.name, b.members[0].is_primary) for b in r.baskets]
        assert sum(f for _n, f in flags) == 1, flags
        assert by_name["A篮"].members[0].is_primary == 1
        assert by_name["B篮"].members[0].is_primary == 0
        # lift 落在成员行上,可审计
        assert by_name["A篮"].members[0].industry_lift > by_name["B篮"].members[0].industry_lift

    def test_code_in_single_basket_is_primary(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply([_basket_payload()])))
        assert r.baskets[0].members[0].is_primary == 1

    def test_lift_tie_breaks_deterministically_by_basket_key(self):
        """lift 全部算不出(无行业数据)→ 按 `basket_key` 升序定主归属,可复现。"""
        ctx = ag.MechContext(trade_date=D0)
        seeds_by_key = {"s1": _seed("s1"), "s2": _seed("s2")}
        mk = lambda key, name: ag.BasketCandidate(  # noqa: E731
            trade_date=D0_S, basket_key=key, name=name, driver="d", driver_kind="theme",
            why_now="w", seed_keys=("s1",),
            members=(ag.BasketMemberCandidate("600001.SH", "leader", None, 0, "r"),),
            evidence=(), evidence_status=ag.EVIDENCE_OK, pack_version="p",
            engine_api_version=1, charter_version="v1.3.3",
        )
        out = ag.assign_primary([mk("ffff0001", "后"), mk("0000aaaa", "前")], seeds_by_key, ctx)
        primaries = {b.basket_key: b.members[0].is_primary for b in out}
        assert primaries == {"0000aaaa": 1, "ffff0001": 0}
        # lift 算不出时字段仍是 None(「算不出」≠「等于 0」)
        assert all(b.members[0].industry_lift is None for b in out)

    # ══════════════════════════════════════════════════════════════════
    # ⑤-c(2026-08-02 planner 裁定)· lift 主归属的最小成分数门槛
    # ══════════════════════════════════════════════════════════════════

    def test_basket_below_min_sample_size_has_lift_none_with_sample_too_small_reason(self):
        """3 只成分的簇篮(< `MIN_LIFT_SAMPLE_SIZE`=5)→ lift 记 `None` + 精确原因,
        即便只有它自己一个候选、无人可比(门槛管的是"这个估计量本身有没有意义",
        不是"输不输得过别人")。"""
        ctx = _semi_baijiu_ctx()
        seeds_by_key = {"s-small": _seed("s-small", members=("600001.SH", "600002.SH", "600010.SH"))}
        basket = _basket_stub("k1", "小簇", ["s-small"], ["600001.SH"])
        out = ag.assign_primary([basket], seeds_by_key, ctx)
        m = out[0].members[0]
        assert m.is_primary == 1   # 唯一候选,兜底也落它身上(篮子不因不达标被剔)
        assert m.industry_lift is None
        assert m.lift_reason == ag.LIFT_REASON_SAMPLE_TOO_SMALL
        assert m.primary_reason == ag.PRIMARY_REASON_FALLBACK

    def test_primary_falls_on_qualified_basket_despite_smaller_baskets_higher_raw_lift(self):
        """**验收核心场景**:一票跨「小簇篮(不达标)+ 大概念篮(达标)」→ 主归属
        落在达标的那个,即便小簇篮的原始 lift 数值更高——正是 ⑤ 完工记录疏漏 ③
        报告的失真场景的回归(涨停簇成分池小 → lift 虚高 → 挂靠票占位)。"""
        ctx = _semi_baijiu_ctx()
        small = _seed("s-small", members=("600001.SH", "600002.SH", "600010.SH"))   # 3 只,不达标
        big = _seed("s-big", members=("600001.SH", "600011.SH", "600012.SH", "600013.SH", "600014.SH"))  # 5 只,达标
        seeds_by_key = {"s-small": small, "s-big": big}
        small_basket = _basket_stub("k-small", "小簇篮", ["s-small"], ["600001.SH"])
        big_basket = _basket_stub("k-big", "大概念篮", ["s-big"], ["600001.SH"])

        # 先佐证"如果不设门槛"小簇篮的原始 lift 确实更高(证明这条回归测的正是失真,
        # 不是随便挑的两个数)。
        small_lift = ag.industry_lift_map(list(small.member_codes), ctx.industry_of, ctx.market_shares)
        big_lift = ag.industry_lift_map(list(big.member_codes), ctx.industry_of, ctx.market_shares)
        assert small_lift["半导体"] > big_lift["半导体"]

        out = ag.assign_primary([small_basket, big_basket], seeds_by_key, ctx)
        by_key = {b.basket_key: b.members[0] for b in out}
        assert by_key["k-big"].is_primary == 1
        assert by_key["k-small"].is_primary == 0
        assert by_key["k-big"].primary_reason == ag.PRIMARY_REASON_LIFT
        assert by_key["k-big"].industry_lift is not None
        assert by_key["k-small"].industry_lift is None
        assert by_key["k-small"].lift_reason == ag.LIFT_REASON_SAMPLE_TOO_SMALL

    def test_all_candidate_baskets_unqualified_falls_back_by_universe_size_then_key(self):
        """全部候选篮都不达标 → 确定性兜底(**成分池大小降序 → basket_key 升序**),
        `primary_reason` 精确,且**同输入两次跑结果逐位相同**。basket_key 故意取
        与"按成分池大小该赢的那个"相反的字典序(`zzz-large` vs `aaa-small`),证明
        赢的是成分池更大、不是字典序更靠前。"""
        ctx = _semi_baijiu_ctx()
        x = _seed("s-x", members=("600001.SH", "600002.SH", "600010.SH"))   # 3 只
        y = _seed("s-y", members=("600001.SH", "600011.SH"))                # 2 只
        seeds_by_key = {"s-x": x, "s-y": y}
        basket_large = _basket_stub("zzz-large", "大篮", ["s-x"], ["600001.SH"])   # 成分池 3
        basket_small = _basket_stub("aaa-small", "小篮", ["s-y"], ["600001.SH"])   # 成分池 2

        def _once():
            return ag.assign_primary([basket_large, basket_small], seeds_by_key, ctx)

        out1, out2 = _once(), _once()
        assert out1 == out2   # 可复现(BasketCandidate/BasketMemberCandidate 是 frozen dataclass)

        by_key = {b.basket_key: b.members[0] for b in out1}
        assert by_key["zzz-large"].is_primary == 1   # 成分池 3 > 2,即便字典序更靠后
        assert by_key["aaa-small"].is_primary == 0
        assert by_key["zzz-large"].primary_reason == ag.PRIMARY_REASON_FALLBACK
        assert by_key["aaa-small"].primary_reason is None   # 只标在 is_primary=1 的那一行
        assert by_key["zzz-large"].industry_lift is None and by_key["aaa-small"].industry_lift is None

    def test_min_lift_sample_size_matches_industry_strength_source(self):
        """门槛常量必须与 `industry_strength._MIN_MEMBERS` 同源同值(⑤-c 定案)——
        `aggregate.py` 是**直接引用**那个对象(不是另抄一份 5),这里断言的是数值
        没有在某处漂移,不是断言"恰好都是 5"这个巧合。"""
        from neckline.report.industry_strength import _MIN_MEMBERS
        assert ag.MIN_LIFT_SAMPLE_SIZE == _MIN_MEMBERS

    def test_lift_formula_matches_v131_industry_gate_precedent(self):
        """**交叉断言**:本模块的 lift 与 v1.3.1 行业闸(`intel_candidates`)同口径。

        生产代码刻意不 import 那个私有函数(`intel_candidates.py` 按 plan §五 V2-⑬-1
        随候选榜退役,生产不挂在计划删除的模块上);等价性由这条测试锁死,同 v1.5
        自选/持仓两侧 K4 镜像的交叉断言体例。"""
        from neckline.report.intel_candidates import (
            INDUSTRY_GATE_MIN_LIFT,
            _dominant_industries,
            _market_industry_shares,
        )

        industry_of = {
            "600001.SH": "半导体", "600002.SH": "半导体", "600003.SH": "半导体",
            **{f"60001{i}.SH": "白酒" for i in range(0, 7)},
        }
        members = ["600001.SH", "600002.SH", "600010.SH", "600011.SH"]
        theirs_shares = _market_industry_shares(industry_of)
        mine_shares = ag.market_industry_shares(industry_of)
        assert mine_shares == theirs_shares
        mine = ag.industry_lift_map(members, industry_of, mine_shares)
        theirs = _dominant_industries(members, industry_of, theirs_shares, INDUSTRY_GATE_MIN_LIFT)
        assert {k for k, v in mine.items() if v >= INDUSTRY_GATE_MIN_LIFT - 1e-9} == theirs

    def test_industry_lift_skips_industry_absent_from_market_shares(self):
        """全市场查无该行业占比 → lift 未定义,**不写 0 冒充"不富集"**。"""
        got = ag.industry_lift_map(["600001.SH"], {"600001.SH": "外星行业"}, {"半导体": 0.2})
        assert got == {}


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 两段式:单侧降级、诚实披露
# ══════════════════════════════════════════════════════════════════════════

class TestTwoStageDegradation:
    def test_search_stage_absent_basket_still_forms_with_search_unavailable(self, isolated_env):
        """**检索段缺席 → 篮子仍出**,`evidence_status='search_unavailable'`,
        证据链留空并明示(禁编造来源)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        reason = _StubProvider(_reason_reply([_basket_payload()]))
        r = _run(env, _seedset(_seed("s1")), search=None, reason=reason)
        assert len(r.baskets) == 1
        b = r.baskets[0]
        assert b.evidence_status == ag.EVIDENCE_SEARCH_UNAVAILABLE
        assert b.evidence == ()
        assert r.search_stage == ag.STAGE_NO_PROVIDER
        assert r.degraded is True
        # 上下文如实告诉推理段"这颗种子没搜成",不假装它没证据
        ctx_text = reason.calls[0]["messages"][1].content
        assert "本次未取得" in ctx_text

    def test_search_call_failure_degrades_only_that_side(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        search = _StubProvider(LLMResult(ok=False, reason="timeout"))
        r = _run(env, _seedset(_seed("s1")), search=search,
                 reason=_StubProvider(_reason_reply([_basket_payload()])))
        assert len(r.baskets) == 1
        assert r.baskets[0].evidence_status == ag.EVIDENCE_SEARCH_UNAVAILABLE
        assert r.evidence_by_seed["s1"].skip_reason.startswith(ag.STAGE_CALL_FAILED)

    def test_search_provider_raising_is_caught(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider([], raises=True),
                 reason=_StubProvider(_reason_reply([_basket_payload()])))
        assert len(r.baskets) == 1
        assert "RuntimeError" in r.evidence_by_seed["s1"].skip_reason

    def test_reason_stage_absent_means_no_basket(self, isolated_env):
        """**推理段缺席 → 该驱动不成篮**(不拿机械数据硬凑一个"驱动")。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)), reason=None)
        assert r.baskets == ()
        assert r.reason_stage == ag.STAGE_NO_PROVIDER
        # 检索段照样跑完、结果照样如实留着(供审计/⑦ 用)
        assert r.evidence_by_seed["s1"].status == ag.EVIDENCE_OK

    def test_reason_stage_unparsable_output_means_no_basket(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        bad = LLMResult(ok=True, content="我觉得今天挺好的,没有 JSON。", provider="stub")
        r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(bad))
        assert r.baskets == () and r.reason_stage == ag.STAGE_PARSE_FAILED

    def test_partial_search_yields_partial_evidence_status(self, isolated_env):
        """一篮合并两颗种子、只有一颗搜成 → `partial`(不合并进 ok,也不算全缺席)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        s1 = _seed("s1", members=("600001.SH",))
        s2 = _seed("s2", label="另一个题材", members=("600002.SH",))
        search = _StubProvider([_search_reply(_EV), LLMResult(ok=False, reason="timeout")])
        payload = _basket_payload(seed_keys=["s1", "s2"], members=[
            {"ts_code": "600001.SH", "role": "leader", "reason": "a"},
            {"ts_code": "600002.SH", "role": "core", "reason": "b"},
        ])
        r = _run(env, _seedset(s1, s2), search=search, reason=_StubProvider(_reason_reply([payload])))
        assert r.baskets[0].evidence_status == ag.EVIDENCE_PARTIAL
        assert len(r.baskets[0].evidence) == 1
        assert r.search_stage == ag.STAGE_PARTIAL

    def test_zero_evidence_not_fatal_when_some_seed_was_never_searched(self, isolated_env):
        """混合态下零证据**不判不成篮** —— 有一半出处根本没查过,不能据此断言
        "这条驱动站不住"。状态如实标 `partial`。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        s1 = _seed("s1", members=("600001.SH",))
        s2 = _seed("s2", label="另一个", members=("600002.SH",))
        search = _StubProvider([_search_reply([]), LLMResult(ok=False, reason="timeout")])
        payload = _basket_payload(seed_keys=["s1", "s2"], members=[
            {"ts_code": "600001.SH", "role": "leader", "reason": "a"}])
        r = _run(env, _seedset(s1, s2), search=search, reason=_StubProvider(_reason_reply([payload])))
        assert len(r.baskets) == 1
        assert r.baskets[0].evidence_status == ag.EVIDENCE_PARTIAL
        assert r.baskets[0].evidence == ()

    def test_no_seed_set_is_a_legal_empty_day(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = ag.aggregate_baskets(D0, seed_set=None, db_path=env.db_path,
                                 parquet_dir=env.parquet_dir,
                                 search_provider=None, reason_provider=None)
        assert r.baskets == () and "no_active_pack_or_seed_set" in r.notes

    def test_top_level_failure_never_raises(self, isolated_env, monkeypatch):
        """保险丝:聚合层整体塌了也只返回空结果 + 如实 note,绝不掀翻当日报告。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        monkeypatch.setattr(ag, "build_mech_context",
                            lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
        r = _run(env, _seedset(_seed("s1")), search=None, reason=None)
        assert r.baskets == ()
        assert any(n.startswith("aggregate_failed:ValueError") for n in r.notes)


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 两段式编排:路由、预算、上下文串联
# ══════════════════════════════════════════════════════════════════════════

class TestOrchestration:
    def test_two_stages_use_two_providers_and_search_flag_differs(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        search = _StubProvider(_search_reply(_EV))
        reason = _StubProvider(_reason_reply([_basket_payload()]))
        _run(env, _seedset(_seed("s1")), search=search, reason=reason)
        assert len(search.calls) == 1 and len(reason.calls) == 1
        assert search.calls[0]["enable_search"] is True
        assert reason.calls[0]["enable_search"] is False     # 推理段不联网

    def test_search_query_is_explicit_and_carries_the_year(self, isolated_env):
        """v1.3.4 案底:不显式传检索词 → 跟最后一条 user 消息走,身份/时效都丢。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        search = _StubProvider(_search_reply(_EV))
        _run(env, _seedset(_seed("s1", label="固态电池")), search=search,
             reason=_StubProvider(_reason_reply([])))
        q = search.calls[0]["search_query"]
        from neckline.llm.prompt_context import recency_hint
        assert q.startswith("固态电池") and recency_hint() in q

    def test_bare_concept_index_code_label_is_resolved_to_chinese_name(self, isolated_env):
        """④ 的涨停簇种子在只有概念锚时 `label` 是**裸指数代码**(`886086.TI`)。
        拿它当检索词等于什么都没查(v1.3.4 同类病),摊到上下文里也没人看得懂 ——
        本层统一过一遍 `ths_index` 名表。真数据冒烟里实测到过这一幕。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        write_flat_parquet(env, "ths_index.parquet", [
            {"ts_code": "886086.TI", "name": "西部大开发", "count": 50, "type": "N",
             "exchange": "A", "list_date": "20200101"},
        ])
        s = _seed("cl-x", kind=seeds_mod.LIMIT_CLUSTER, label="886086.TI", members=("600001.SH",))
        search = _StubProvider(_search_reply(_EV))
        reason = _StubProvider(_reason_reply([]))
        _run(env, _seedset(s), search=search, reason=reason)
        assert search.calls[0]["search_query"].startswith("西部大开发")
        assert "名称 西部大开发" in reason.calls[0]["messages"][1].content

    def test_amount_is_rendered_in_yi_yuan_from_thousand_yuan_source(self, isolated_env):
        """`daily.amount` 单位是**千元**(TuShare 口径)——换亿元除 1e5,不是 1e8。
        真数据冒烟里 7.27 亿的票一度被写成「0.01 亿」。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        write_daily_fixture(env, "daily", D0, [
            {"ts_code": "600001.SH", "open": 6.0, "high": 6.1, "low": 5.9, "close": 6.07,
             "pre_close": 5.52, "vol": 1.2e6, "amount": 727000.47, "pct_chg": 9.96},
        ])
        reason = _StubProvider(_reason_reply([]))
        _run(env, _seedset(_seed("s1", members=("600001.SH",))),
             search=_StubProvider(_search_reply(_EV)), reason=reason)
        assert "成交额 7.27 亿元" in reason.calls[0]["messages"][1].content

    def test_both_stages_start_user_message_with_the_date_anchor(self, isolated_env):
        """日期锚走 `prompt_context` 唯一实现(2026-07-30 报障根因:模型没有"现在")。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        search = _StubProvider(_search_reply(_EV))
        reason = _StubProvider(_reason_reply([]))
        _run(env, _seedset(_seed("s1")), search=search, reason=reason)
        for calls in (search.calls, reason.calls):
            first_line = calls[0]["messages"][1].content.splitlines()[0]
            assert first_line.startswith("今天是 ")
            assert "本次分析的基准交易日是 2024年4月8日" in first_line

    def test_timeliness_rules_embedded_in_both_system_prompts(self):
        from neckline.llm.prompt_context import TIMELINESS_RULES
        assert TIMELINESS_RULES in ag.DRIVER_SEARCH_SYSTEM_PROMPT
        assert TIMELINESS_RULES in ag.BASKET_REASON_SYSTEM_PROMPT

    def test_search_findings_are_forwarded_into_the_reason_context(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        reason = _StubProvider(_reason_reply([]))
        _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(_EV)), reason=reason)
        ctx_text = reason.calls[0]["messages"][1].content
        assert "工信部" in ctx_text and "2026-07-31" in ctx_text

    def test_search_budget_exhaustion_skips_search_but_not_reason(self, isolated_env):
        """三本账互不透支:检索账见底不影响推理账,篮子照出、状态如实。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        ledger = BudgetLedger(limits={LEDGER_SEARCH: 0.0, LEDGER_REASON: 600.0, LEDGER_REVIEW: 900.0})
        search = _StubProvider(_search_reply(_EV))
        reason = _StubProvider(_reason_reply([_basket_payload()]))
        r = _run(env, _seedset(_seed("s1")), search=search, reason=reason, ledger=ledger)
        assert search.calls == [] and len(reason.calls) == 1
        assert r.evidence_by_seed["s1"].skip_reason == ag.STAGE_BUDGET_EXHAUSTED
        assert r.baskets[0].evidence_status == ag.EVIDENCE_SEARCH_UNAVAILABLE
        assert ledger.spent[LEDGER_REASON] > 0.0

    def test_reason_budget_exhaustion_skips_reason_only(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        ledger = BudgetLedger(limits={LEDGER_SEARCH: 600.0, LEDGER_REASON: 0.0, LEDGER_REVIEW: 900.0})
        search = _StubProvider(_search_reply(_EV))
        reason = _StubProvider(_reason_reply([_basket_payload()]))
        r = _run(env, _seedset(_seed("s1")), search=search, reason=reason, ledger=ledger)
        assert len(search.calls) == 1 and reason.calls == []
        assert r.reason_stage == ag.STAGE_BUDGET_EXHAUSTED and r.baskets == ()
        assert ledger.spent[LEDGER_SEARCH] > 0.0

    def test_providers_default_to_the_task_router_and_degrade_without_keys(self, isolated_env):
        """不显式传 provider 时走 ② 的路由工厂(`TASK_DRIVER_SEARCH` /
        `TASK_BASKET_REASON`);本地零 key → 工厂返 `None` → 两段皆缺席、当日不成篮,
        **全链路在无 key 下优雅降级跑通**(§2.0/§3.8 铁律)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        r = ag.aggregate_baskets(D0, seed_set=_seedset(_seed("s1")), db_path=env.db_path,
                                 parquet_dir=env.parquet_dir)
        assert r.baskets == ()
        assert r.search_stage == ag.STAGE_NO_PROVIDER
        assert r.reason_stage == ag.STAGE_NO_PROVIDER

    def test_seed_count_is_capped_and_disclosed(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        many = [_seed(f"s{i}", kind=seeds_mod.LIMIT_CLUSTER, members=("600001.SH",)) for i in range(30)]
        search = _StubProvider(_search_reply(_EV))
        r = _run(env, _seedset(*many), search=search, reason=_StubProvider(_reason_reply([])),
                 max_seeds=3)
        assert len(search.calls) == 3
        assert any(n.startswith("seeds_truncated:3/30") for n in r.notes)

    def test_deterministic_same_input_twice_gives_identical_baskets(self, isolated_env):
        env = isolated_env
        ss = _two_basket_env(env)
        payloads = [
            _basket_payload(name="A篮", seed_keys=["s-a"], members=[
                {"ts_code": "600001.SH", "role": "leader", "reason": "a"}]),
            _basket_payload(name="B篮", seed_keys=["s-b"], members=[
                {"ts_code": "600001.SH", "role": "core", "reason": "b"}]),
        ]
        runs = [
            _run(env, ss, search=_StubProvider(_search_reply(_EV)),
                 reason=_StubProvider(_reason_reply(payloads)))
            for _ in range(2)
        ]
        assert runs[0].baskets == runs[1].baskets


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 结构化解析层(v1.5.1 劫持案回归)
# ══════════════════════════════════════════════════════════════════════════

class TestStructuredParsingIsolation:
    def test_verdict_phrase_inside_free_text_changes_nothing(self):
        """本块没有"结论:通过|否决"这套标签,自由文本里出现该词组必须**毫无影响**
        ——v1.5.1 的教训是"标签后挂内容会被劫持",本块的落法是**根本不用那套标签**,
        结构化产出一律走 `llm/json_block.py` 独立解析层。"""
        payload = {"baskets": [_basket_payload(
            driver="政策落地(有人说结论:否决,但那只是引用别人的话)",
            why_now="结论:通过 —— 这几个字出现在自由文本里",
        )]}
        content = _fenced(payload, "叙述里也写一句 结论:否决。")
        from neckline.llm.json_block import split_narrative_and_reference_json
        narrative, parsed = split_narrative_and_reference_json(content)
        assert parsed["baskets"][0]["driver"].startswith("政策落地")
        assert "结论:否决" in narrative     # 叙述原样留着,不被吞

    def test_evidence_items_missing_source_or_date_are_dropped(self, isolated_env, caplog):
        """「每条带日期」是硬要求:缺 source/date 的条目直接丢,不补默认值。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        items = [
            {"claim": "有个消息", "source": "", "date": "2026-07-31"},
            {"claim": "另一个", "source": "证券时报", "date": ""},
            {"claim": "合格的", "source": "上交所", "date": "2026-08-01"},
        ]
        with caplog.at_level(logging.WARNING):
            r = _run(env, _seedset(_seed("s1")), search=_StubProvider(_search_reply(items)),
                     reason=_StubProvider(_reason_reply([_basket_payload()])))
        ev = r.baskets[0].evidence
        assert [e.claim for e in ev] == ["合格的"]
        assert any("缺 claim/source/date" in rec.message for rec in caplog.records)

    def test_duplicate_evidence_across_seeds_is_deduped(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        s1 = _seed("s1", members=("600001.SH",))
        s2 = _seed("s2", label="别名题材", members=("600002.SH",))
        search = _StubProvider([_search_reply(_EV), _search_reply(_EV)])
        payload = _basket_payload(seed_keys=["s1", "s2"], members=[
            {"ts_code": "600001.SH", "role": "leader", "reason": "a"}])
        r = _run(env, _seedset(s1, s2), search=search, reason=_StubProvider(_reason_reply([payload])))
        assert len(r.baskets[0].evidence) == 1
        assert r.baskets[0].evidence_status == ag.EVIDENCE_OK


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 落库 `baskets` / `basket_members`
# ══════════════════════════════════════════════════════════════════════════

def _one_basket(env) -> ag.AggregateResult:
    insert_trade_cal(env, [D0])
    _insert_leader_rows(env, [
        {"cluster_key": "cl-1", "ts_code": "600001.SH", "role_mech": "core", "rs_rank": 2}])
    s = _seed("cl-1", kind=seeds_mod.LIMIT_CLUSTER, members=("600001.SH",))
    return _run(env, _seedset(s), search=_StubProvider(_search_reply(_EV)),
                reason=_StubProvider(_reason_reply([_basket_payload(seed_keys=["cl-1"])])))


class TestPersistence:
    def test_missing_tier_fails_loud(self, isolated_env):
        """`baskets.tier` 由 ⑥ 定档;本层**绝不臆造** tier。"""
        env = isolated_env
        r = _one_basket(env)
        with pytest.raises(ValueError, match="缺 tier"):
            ag.save_baskets(r, tier_by_basket_key={}, db_path=env.db_path)

    def test_bad_tier_value_fails_loud(self, isolated_env):
        env = isolated_env
        r = _one_basket(env)
        with pytest.raises(ValueError, match="tier 只能是"):
            ag.save_baskets(r, tier_by_basket_key={r.baskets[0].basket_key: 4}, db_path=env.db_path)

    def test_rows_land_with_conflict_and_primary_flags(self, isolated_env):
        env = isolated_env
        r = _one_basket(env)
        key = r.baskets[0].basket_key
        stats = ag.save_baskets(r, tier_by_basket_key={key: 1}, db_path=env.db_path)
        assert stats == {"baskets_inserted": 1, "baskets_existing": 0, "members_inserted": 1}
        conn = sqlite3.connect(str(env.db_path))
        try:
            b = conn.execute(
                "SELECT trade_date, basket_key, driver_kind, tier, pack_version, "
                "engine_api_version, charter_version, via, evidence_status FROM baskets"
            ).fetchone()
            m = conn.execute(
                "SELECT ts_code, role_llm, role_mech, role_conflict, is_primary FROM basket_members"
            ).fetchone()
        finally:
            conn.close()
        assert b[0] == D0_S and b[1] == key and b[3] == 1
        assert b[2] in ag.DRIVER_KINDS
        assert b[7] == "auto" and b[8] == ag.EVIDENCE_OK
        # LLM 标 leader、机械侧 core → 冲突入库,两说并存
        assert m == ("600001.SH", "leader", "core", 1, 1)

    def test_replay_is_idempotent_and_does_not_overwrite(self, isolated_env, caplog):
        env = isolated_env
        r = _one_basket(env)
        key = r.baskets[0].basket_key
        ag.save_baskets(r, tier_by_basket_key={key: 1}, db_path=env.db_path)
        with caplog.at_level(logging.WARNING):
            stats = ag.save_baskets(r, tier_by_basket_key={key: 3}, db_path=env.db_path)
        assert stats["baskets_inserted"] == 0 and stats["baskets_existing"] == 1
        conn = sqlite3.connect(str(env.db_path))
        try:
            rows = conn.execute("SELECT tier FROM baskets").fetchall()
            n_members = conn.execute("SELECT COUNT(*) FROM basket_members").fetchone()[0]
        finally:
            conn.close()
        # 幂等 no-op:既有行**没被改写**(仍是第一次的 tier=1),成员也没重复
        assert rows == [(1,)] and n_members == 1
        assert any("幂等跳过" in rec.message for rec in caplog.records)

    def test_empty_result_saves_nothing(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        empty = ag.AggregateResult(trade_date=D0_S)
        assert ag.save_baskets(empty, tier_by_basket_key={}, db_path=env.db_path) == {
            "baskets_inserted": 0, "baskets_existing": 0, "members_inserted": 0}


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 第〇原则 / 架构守门(静态)
# ══════════════════════════════════════════════════════════════════════════

_AGG_PATH = Path(__file__).resolve().parent.parent / "neckline" / "selection" / "aggregate.py"


def _imported_modules(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    return mods


def test_aggregate_never_imports_sentinel():
    """§2.8-C 第 2 条:LLM 产出的自由文本与数字一律不进哨兵判据。聚合层与哨兵
    之间不该有任何 import 关系(哨兵只读 ⑦ 冻结的结构化 spec 与现役章程 config)。"""
    assert not [m for m in _imported_modules(_AGG_PATH) if m.startswith("neckline.sentinel")]


def test_aggregate_does_not_reuse_the_verdict_parser():
    """结构化产出走独立解析层(`llm/json_block.py`),**不复用** `judge._parse_verdict`
    ——本块的两段式输出里根本没有"结论:"标签,硬套 last-match 锚点就是 v1.5.1
    那颗雷的复刻。"""
    mods = _imported_modules(_AGG_PATH)
    assert "neckline.llm.judge" not in mods
    assert "neckline.llm.json_block" in mods
    tree = ast.parse(_AGG_PATH.read_text(encoding="utf-8"), filename=str(_AGG_PATH))
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_parse_verdict" not in called


def test_aggregate_reads_no_discipline_thresholds():
    """纪律参数(止损/回落止盈/仓位)不在本层出现 —— 本层只读现役章程的**版本号**
    当口径指纹,不读也不用任何阈值(§2.0:纪律只住章程)。"""
    src = _AGG_PATH.read_text(encoding="utf-8")
    for banned in ("stop_pct", "take_profit_retrace", "active_config(", "0.05", "0.08"):
        assert banned not in src, f"聚合层出现了纪律参数痕迹:{banned}"


def test_charter_version_is_recorded_as_fingerprint_only(isolated_env):
    """无现役章程时记 `unknown`,**不写空串冒充**(「没有」与「没看」要分得开)。"""
    env = isolated_env
    r = _one_basket(env)
    assert r.baskets[0].charter_version == ag.CHARTER_UNKNOWN
    assert r.baskets[0].engine_api_version == 1
