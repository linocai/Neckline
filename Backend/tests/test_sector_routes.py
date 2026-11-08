"""板块线三路：B 海外映射、C 发酵、提示词、比较收口、真实入口和 routes 列。"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.evaluation.search import SearchOutcome
from neckline.morning import db
from neckline.morning.api import create_router
from neckline.morning.report import LoadedConfig, Pusher, load_config, run_morning
from neckline.morning.schedule import CST
from neckline.morning.stock_pool import store_rows
from neckline.morning.sector_ferment import board_keywords, count_fermenting
from neckline.morning.sector_line import (
    _compile_rules, _names_in_text, _rule_hit, _word_pattern, apply_prefilter, collect_routes,
    opening_text, validate_sector_settings,
)
from neckline.morning.sector_prompt import (
    EVALUATION_CONTRACT, EVALUATION_SYSTEM, MAPPING_CONTRACT, MAPPING_SYSTEM, SELECTION_CONTRACT,
    SELECTION_SYSTEM, evaluation_user_prompt, mapping_user_prompt, selection_user_prompt,
)
from neckline.screening.deepseek import ChatError, ChatUnknown, DeepSeekClient

from tests.conftest import approved_config, cover_sector_history
from tests.sector_v5_draft import (
    EVALUATION_SYSTEM_DRAFT, MAPPING_SYSTEM_DRAFT, SELECTION_SYSTEM_DRAFT,
)
from tests.test_morning import FakeApns, _client, _with_device, at, doc
from tests.test_sector_line import (
    _answer, _concept, _db, _event, _http, _ids, _leader_codes, _msg, _ok, _pool, _rows, _rules, _run,
    _window,
)

DAY = date(2026, 10, 13)
_COLLECTED = datetime(2026, 10, 13, 8, 2, tzinfo=CST)
_FERMENT_GAP = "板块线 C 路没跑：发酵统计的历史不全"
_SELECT_GAP = "板块线判推 4 个，比较没完成"


def _sectors():
    return load_config().rules["sectors"]


def _meta(messages, boards=()):
    _order, meta, stats = collect_routes(messages, _sectors(), boards=list(boards))
    return meta, stats


def _routes(message, meta) -> list[str]:
    return list((meta.get(message.ref) or {}).get("routes") or [])


def _one_each(user: str):
    events = []
    for index, ref in enumerate(_ids(user), start=1):
        events.append(_event({
            "title": f"事件{index}", "ids": [ref], "sector": "人工智能", "reason": "直接", "repeat": False,
        }))
    return _ok({"events": events, "dropped": []})


class _Script:
    def __init__(self, editor):
        self.editor = editor
        self.calls: list[list[dict]] = []

    def complete(self, messages, options):
        self.calls.append(messages)
        system = messages[0]["content"]
        user = messages[1]["content"]
        if "板块分析员" in system:
            return _one_each(user)
        if "板块编辑" in system:
            return self.editor(messages)
        codes = _leader_codes(user)
        found = re.search(r"事件：(.+)", user)
        payload = _answer(codes[:1] or ["300001"])
        payload["title"] = found.group(1) if found else payload["title"]
        return _ok(payload)


def _four(tmp_path, editor, **kwargs):
    path = _db(tmp_path)
    _concept(path)
    messages = [
        _msg("a", "工信部印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 10, tzinfo=CST)),
        _msg("b", "发改委印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 20, tzinfo=CST)),
        _msg("c", "央行印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 30, tzinfo=CST)),
        _msg("d", "财政部印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 40, tzinfo=CST)),
    ]
    client = _Script(editor)
    outcome = _run(path, messages, client, **kwargs)
    return path, client, outcome


def _editor_calls(client) -> int:
    return sum(1 for call in client.calls if "板块编辑" in call[0]["content"])


def _plant(path, when: datetime, title: str, ext: str, source: str = "jin10-flash") -> None:
    fetched = datetime(2026, 10, 13, 8, tzinfo=CST)
    with db.write(path) as conn:
        db.store_documents(conn, [doc(source, ext, title, when, fetched, title=title)])


def _mentions(path, when: datetime, count: int, title: str, prefix: str) -> None:
    for index in range(count):
        _plant(path, when, f"{title}{index}", f"{prefix}-{index}")


def _ferment_settings(**edits):
    base = {
        "recentDays": 2, "baseDays": 2, "growth": 3, "minDaily": 1, "bodyChars": 300,
        "skipRules": ["tape"], "aliases": {"探针板块": ["探针词"], "空板块": []},
    }
    base.update(edits)
    return base


def _count(path, fermenting):
    concepts = [{"name": "探针板块", "members": ["300001.SZ"]}, {"name": "空板块", "members": ["300001.SZ"]}]
    rules = load_config().rules
    return count_fermenting(
        path, day=DAY, collected_by=_COLLECTED, concepts=concepts,
        prefilter=rules["sectors"]["prefilter"], intake=rules["sectors"]["intake"], fermenting=fermenting,
        window_end=rules["schedule"]["windowEnd"], opening_text=opening_text, rule_hit=_rule_hit,
        word_pattern=_word_pattern, compile_rules=_compile_rules)


# ------------------------------------------------------------------ 配置


def test_shipped_sectors_match_the_three_route_draft():
    rules = load_config().rules
    draft = json.loads(Path("Backend/.work/v45/config-draft.json").read_text(encoding="utf-8")
                       if Path("Backend/.work/v45/config-draft.json").exists()
                       else Path(".work/v45/config-draft.json").read_text(encoding="utf-8"))
    sectors = rules["sectors"]
    assert rules["approval"] == "pending" and "approvedAt" not in rules
    assert rules["note"] == "三路版（11/08 晚）：海外名单 D7、C 路参数 D8 待用户回放后定；fermenting 的 recentDays/growth 是回放比较用的占位"
    assert sectors["overseas"] == draft["overseas"]
    assert sectors["fermenting"] == draft["fermenting"]
    assert sectors["mapping"]["contract"] == MAPPING_CONTRACT == "sector-mapping-v5"
    assert sectors["evaluation"]["contract"] == EVALUATION_CONTRACT == "sector-evaluation-v5"
    assert sectors["selection"] == {
        "contract": SELECTION_CONTRACT, "modelOptions": sectors["evaluation"]["modelOptions"],
    }


def test_new_sector_sections_are_rejected_without_defaults():
    sectors = deepcopy(_sectors())
    sectors["overseas"]["leadChars"] = 0
    with pytest.raises(ValueError, match="leadChars"):
        validate_sector_settings(sectors)
    sectors = deepcopy(_sectors())
    sectors["fermenting"]["growth"] = True
    with pytest.raises(ValueError, match="growth"):
        validate_sector_settings(sectors)
    sectors = deepcopy(_sectors())
    sectors["selection"]["contract"] = "sector-selection-v0"
    with pytest.raises(ValueError, match="selection"):
        validate_sector_settings(sectors)
    sectors = deepcopy(_sectors())
    sectors["overseas"]["prefilterRules"] = ["不存在的规则"]
    with pytest.raises(ValueError, match="prefilterRules"):
        validate_sector_settings(sectors)


# ------------------------------------------------------------------ B 路


def test_route_b_keeps_hits_and_drops_the_named_rules():
    settings = _sectors()
    kept_a = _msg("a-hit", "AMD盘后上涨", "盘后交易活跃。", datetime(2026, 10, 13, 7, tzinfo=CST))
    missed_move = _msg("a-miss", "AMD例会", "普通安排。", datetime(2026, 10, 13, 7, 1, tzinfo=CST))
    too_late = _msg("a-late", "啊" * 20 + "AMD上涨", "正文。", datetime(2026, 10, 13, 7, 2, tzinfo=CST))
    early_enough = _msg("a-early", "啊" * 19 + "AMD上涨", "正文。", datetime(2026, 10, 13, 7, 3, tzinfo=CST))
    kept_b = _msg("b-hit", "半导体设备订单", "美股隔夜上涨明显。", datetime(2026, 10, 13, 7, 4, tzinfo=CST))
    missed_market = _msg("b-market", "半导体设备订单", "国内安排。", datetime(2026, 10, 13, 7, 5, tzinfo=CST))
    missed_move_b = _msg("b-move", "半导体设备", "美股隔夜持平。", datetime(2026, 10, 13, 7, 6, tzinfo=CST))
    excluded = _msg("ex", "AMD盘后上涨", "详见行情一览。", datetime(2026, 10, 13, 7, 7, tzinfo=CST))
    overseas = _msg("ov", "美股AMD盘后上涨", "外围交易活跃。", datetime(2026, 10, 13, 7, 8, tzinfo=CST))
    tape = _msg("tp", "AMD收盘上涨", "收盘了。", datetime(2026, 10, 13, 7, 9, tzinfo=CST))
    listed = _msg("ol", "AMD上涨600519.SH", "点名。", datetime(2026, 10, 13, 7, 11, tzinfo=CST))
    filing = _msg("cn", "AMD盘后上涨", "公告。", datetime(2026, 10, 13, 7, 12, tzinfo=CST), source="cninfo-announcement")
    macro = _msg("ma", "AMD预期1前值0上涨", "数据。", datetime(2026, 10, 13, 7, 13, tzinfo=CST))
    research = _msg("re", "AMD公司点评：上涨", "点评。", datetime(2026, 10, 13, 7, 14, tzinfo=CST))
    periodic = _msg("pe", "AMD三季度报告", "上涨。", datetime(2026, 10, 13, 7, 15, tzinfo=CST))
    roundup = _msg("ru", "AMD上涨要闻速递", "汇总。", datetime(2026, 10, 13, 7, 16, tzinfo=CST))
    both = _msg("ab", "工信部AMD盘后上涨", "国内跟进。", datetime(2026, 10, 13, 7, 17, tzinfo=CST))
    messages = [kept_a, missed_move, too_late, early_enough, kept_b, missed_market, missed_move_b, excluded,
                overseas, tape, listed, filing, macro, research, periodic, roundup, both]
    meta, _stats = _meta(messages)
    assert _routes(kept_a, meta) == ["B"]
    assert missed_move.ref not in meta
    assert too_late.ref not in meta
    assert _routes(early_enough, meta) == ["B"]
    assert _routes(kept_b, meta) == ["B"]
    assert missed_market.ref not in meta and missed_move_b.ref not in meta
    assert excluded.ref not in meta
    for message in (overseas, tape, listed):
        kept, _dropped = apply_prefilter([message], settings["prefilter"])
        assert not kept
        assert _routes(message, meta) == ["B"]
    for message in (filing, macro, research, periodic, roundup):
        assert message.ref not in meta
    assert _routes(both, meta) == ["A", "B"]
    notes = _names_in_text(f"{kept_a.title}\n{kept_a.text}", settings["overseas"]["names"])
    assert [item["name"] for item in notes] == ["AMD"]
    assert _names_in_text(f"{kept_b.title}\n{kept_b.text}", settings["overseas"]["names"]) == []


# ------------------------------------------------------------------ C 路


def test_ferment_keywords_use_aliases_or_the_split_name():
    assert board_keywords("短剧游戏（漫剧）", {}) == ["短剧游戏", "漫剧"]
    assert board_keywords("短剧游戏(漫剧)", {}) == ["短剧游戏", "漫剧"]
    assert board_keywords("人工智能概念", {}) == ["人工智能"]
    assert board_keywords("光刻机概念股", {}) == ["光刻机"]
    assert board_keywords("短剧游戏", {"短剧游戏": ["短剧", "漫剧"]}) == ["短剧", "漫剧"]
    assert board_keywords("人民币贬值受益", {"人民币贬值受益": []}) is None


def test_fermenting_needs_every_recent_day_and_the_daily_floor(tmp_path):
    path = _db(tmp_path)
    cover_sector_history(path, DAY)
    _mentions(path, datetime(2026, 10, 12, 12, tzinfo=CST), 10, "探针词走强", "late")
    assert _count(path, _ferment_settings()).boards == []
    _mentions(path, datetime(2026, 10, 9, 12, tzinfo=CST), 3, "探针词回升", "early")
    _mentions(path, datetime(2026, 10, 12, 12, tzinfo=CST), 3, "探针词再起", "late-more")
    # 上面 late 已经 10 条，early 3 条。再补到两边都够。10 和 3 里 3 < 3*max(1,0) 不成立，所以 early 只有 3、late 有 13。
    outcome = _count(path, _ferment_settings())
    assert [item["name"] for item in outcome.boards] == ["探针板块"]
    assert outcome.boards[0]["recent"] == [3, 13]
    assert outcome.boards[0]["baseDaily"] == 0
    assert _count(path, _ferment_settings(minDaily=5, growth=2)).boards == []
    _mentions(path, datetime(2026, 10, 9, 12, tzinfo=CST), 7, "探针词补齐", "early-more")
    again = _count(path, _ferment_settings(minDaily=5, growth=2))
    assert again.boards[0]["recent"] == [10, 13]


def test_ferment_counts_skip_tape_noise_and_duplicate_text(tmp_path):
    path = _db(tmp_path)
    cover_sector_history(path, DAY)
    for day, prefix in ((datetime(2026, 10, 9, 12, tzinfo=CST), "e"), (datetime(2026, 10, 12, 12, tzinfo=CST), "l")):
        _plant(path, day, "探针词收盘", f"{prefix}-tape")
        _plant(path, day, "探针词股价", f"{prefix}-noise")
        _plant(path, day, "探针词有进展", f"{prefix}-a")
        _plant(path, day, "探针词有进展", f"{prefix}-b")
        _plant(path, day, "探针词另一条", f"{prefix}-c")
        _plant(path, day, "空板块单独出现", f"{prefix}-empty")
    outcome = _count(path, _ferment_settings())
    assert outcome.ran is True
    assert [item["name"] for item in outcome.boards] == []
    # 去重后每天只有「有进展」1 条加「另一条」1 条，低于 3。再各补 1 条才够。
    for day, prefix in ((datetime(2026, 10, 9, 12, tzinfo=CST), "e2"), (datetime(2026, 10, 12, 12, tzinfo=CST), "l2")):
        _plant(path, day, "探针词第三条", f"{prefix}")
    outcome = _count(path, _ferment_settings())
    assert outcome.boards[0]["recent"] == [3, 3]


def test_route_c_does_not_run_when_history_is_uncovered(tmp_path):
    path = _db(tmp_path)
    assert _count(path, _ferment_settings()).blocked == "coverage"
    _concept(path)
    message = _msg("a", "工信部印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, tzinfo=CST))
    outcome = _run(path, [message], _Script(lambda _messages: _ok({"chosen": [], "reason": "不会调用"})), cover=False)
    assert outcome.gap == _FERMENT_GAP
    assert outcome.stats["fermenting"]["ran"] is False
    assert outcome.stats["routes"]["A"] == 1 and outcome.stats["routes"]["C"] == 0
    assert len(outcome.content["entries"]) == 1


def test_route_c_keeps_only_fully_prefiltered_messages(tmp_path):
    path = _db(tmp_path)
    cover_sector_history(path, DAY)
    _mentions(path, datetime(2026, 10, 9, 12, tzinfo=CST), 3, "探针词甲", "e")
    _mentions(path, datetime(2026, 10, 12, 12, tzinfo=CST), 3, "探针词乙", "l")
    outcome = _count(path, _ferment_settings())
    boards = [{**item, "pattern": _word_pattern(item["keywords"])} for item in outcome.boards]
    tape = _msg("tape", "探针词收盘", "行情。", datetime(2026, 10, 13, 7, tzinfo=CST))
    overseas = _msg("ov", "美股探针词上涨", "海外。", datetime(2026, 10, 13, 7, 1, tzinfo=CST))
    macro = _msg("macro", "探针词GDP回升", "数据。", datetime(2026, 10, 13, 7, 2, tzinfo=CST))
    clean = _msg("clean", "探针词有新进展", "继续。", datetime(2026, 10, 13, 7, 3, tzinfo=CST))
    _order, meta, stats = collect_routes([tape, overseas, macro, clean], _sectors(), boards=boards)
    assert tape.ref not in meta and overseas.ref not in meta and macro.ref not in meta
    assert _routes(clean, meta) == ["C"]
    assert meta[clean.ref]["boards"] == ["探针板块"]
    assert stats["routes"]["C"] == 1


def test_one_message_can_take_several_routes_and_skipped_events_store_them(tmp_path):
    path = _db(tmp_path)
    cover_sector_history(path, DAY)
    _mentions(path, datetime(2026, 10, 9, 12, tzinfo=CST), 3, "短剧报道", "e")
    _mentions(path, datetime(2026, 10, 12, 12, tzinfo=CST), 3, "短剧再报", "l")
    _concept(path, name="短剧游戏", ts_code="885728.TI")
    mixed = _msg("mix", "工信部AMD盘后上涨", "短剧有新进展。", datetime(2026, 10, 13, 7, 10, tzinfo=CST))
    policy = _msg("pol", "发改委印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 20, tzinfo=CST))
    abroad = _msg("nv", "英伟达盘后上涨", "股价新高。", datetime(2026, 10, 13, 7, 30, tzinfo=CST))

    def mapping(messages, _options):
        user = messages[1]["content"]
        ids = _ids(user)
        return _ok({"events": [
            _event({"title": "合在一起", "ids": ids[:2], "sector": "短剧游戏", "reason": "同一件事", "repeat": False}),
            _event({"title": "选不出", "ids": ids[2:], "sector": None, "reason": "没有对应板块", "repeat": False}),
        ], "dropped": []})

    client = _Script(lambda _messages: _ok({"chosen": ["e1"], "reason": "不会调用"}))
    client.complete = lambda messages, options: _finish(client, messages, mapping)
    outcome = _run(path, [mixed, policy, abroad], client)
    assert outcome.stats["routes"] == {"A": 2, "B": 2, "C": 1, "kept": 3}
    rows = _rows(path)
    by_title = {row["title"]: row for row in rows}
    assert by_title["合在一起"]["routes"] == ["A", "B", "C"]
    assert by_title["合在一起"]["state"] == "succeeded"
    assert [item["name"] for item in by_title["合在一起"]["package"]["overseasNotes"]] == ["AMD"]
    assert by_title["选不出"]["state"] == "skipped"
    assert by_title["选不出"]["routes"] == ["B"]
    assert "比较没完成" not in (outcome.gap or "")


def _finish(client, messages, mapping):
    client.calls.append(messages)
    system = messages[0]["content"]
    if "板块分析员" in system:
        return mapping(messages, None)
    if "板块编辑" in system:
        raise AssertionError("不超过上限不应比较")
    codes = _leader_codes(messages[1]["content"])
    payload = _answer(codes[:1] or ["300001"])
    payload["title"] = "合在一起"
    return _ok(payload)


# ------------------------------------------------------------------ 提示词


def test_system_prompts_match_the_draft_character_for_character():
    assert MAPPING_SYSTEM == MAPPING_SYSTEM_DRAFT
    assert EVALUATION_SYSTEM == EVALUATION_SYSTEM_DRAFT
    assert SELECTION_SYSTEM == SELECTION_SYSTEM_DRAFT
    assert MAPPING_CONTRACT == "sector-mapping-v5"
    assert EVALUATION_CONTRACT == "sector-evaluation-v5"
    assert SELECTION_CONTRACT == "sector-selection-v1"


def test_user_prompts_show_routes_overseas_notes_and_fermenting():
    mapping = mapping_user_prompt([
        {"id": "n12", "routes": ["C", "A"], "boards": ["短剧游戏", "漫剧"],
         "source": "金十快讯", "time": "2026-11-02 07:30", "title": "标题", "excerpt": "摘录"},
    ], ["人工智能"], [])
    assert "[n12] A、C（短剧游戏、漫剧）｜金十快讯｜2026-11-02 07:30｜标题" in mapping
    assert mapping_user_prompt([], [], []) == mapping_user_prompt([], [], [])
    package = {
        "report": {"tradeDate": "2026-11-02", "windowStart": "2026-11-01T15:00:00+08:00",
                   "windowEnd": "2026-11-02T08:00:00+08:00"},
        "event": {"title": "事件", "reason": "理由"},
        "sector": {"name": "人工智能", "code": "885001.TI"},
        "routes": ["C", "B"],
        "overseasNotes": [{"name": "AMD", "industry": "AI 芯片", "note": "CPU、GPU 与数据中心芯片"}],
        "fermenting": [{
            "name": "短剧游戏", "keywords": ["短剧", "漫剧"], "recent": [12, 15], "baseDaily": 3.2, "baseDays": 5,
        }],
        "members": [],
    }
    rendered = evaluation_user_prompt(package)
    assert rendered == evaluation_user_prompt(package)
    assert "来自哪一路：B 海外映射、C 发酵中题材" in rendered
    assert "海外公司与行业说明（代码按名单给出）：\n- AMD：AI 芯片。CPU、GPU 与数据中心芯片" in rendered
    assert "发酵中题材（代码按新闻报道条数统计）：\n- 短剧游戏（关键词：短剧、漫剧）：最近 2 个交易日每天 12、15 条，此前 5 个交易日日均 3.2 条" in rendered
    selected = selection_user_prompt(3, [{
        "routes": ["A", "B"], "sector": "人工智能", "firstPublished": "2026-11-02 07:30",
        "title": "标题", "conclusion": "结论", "risk": "风险",
        "leaders": [{"name": "银之杰", "code": "300085", "why": "为什么"}],
    }])
    assert selected == "\n".join([
        "上限：3 个",
        "[e1] 来自：A、B｜板块：人工智能｜首次公开：2026-11-02 07:30",
        "标题：标题",
        "结论：结论",
        "承接股：银之杰（300085）：为什么",
        "风险：风险",
    ])


# ------------------------------------------------------------------ 收口


def test_selection_pushes_all_without_a_call_when_within_the_limit(tmp_path):
    path = _db(tmp_path)
    _concept(path)
    messages = [
        _msg("a", "工信部印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 10, tzinfo=CST)),
        _msg("b", "发改委印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 20, tzinfo=CST)),
    ]
    client = _Script(lambda _messages: (_ for _ in ()).throw(AssertionError("不应调用")))
    outcome = _run(path, messages, client)
    assert _editor_calls(client) == 0
    assert outcome.stats["selection"]["called"] is False
    assert outcome.stats["selection"]["candidates"] == 2
    assert [entry["title"] for entry in outcome.content["entries"]] == ["事件1", "事件2"]
    assert outcome.content["overflow"] == 0
    assert outcome.gap is None


def test_selection_calls_once_and_delivers_the_chosen_events_by_time(tmp_path):
    def editor(messages):
        assert len(messages) == 2
        return _ok({"chosen": ["e1", "e4", "e2"], "reason": "留下这三条"})

    _path, client, outcome = _four(tmp_path, editor)
    assert _editor_calls(client) == 1
    assert [entry["title"] for entry in outcome.content["entries"]] == ["事件1", "事件2", "事件4"]
    assert outcome.content["overflow"] == 1
    assert outcome.stats["selection"]["called"] is True
    assert outcome.stats["selection"]["candidates"] == 4
    assert outcome.stats["selection"]["reason"] == "留下这三条"
    assert outcome.stats["selection"]["error"] is None
    assert outcome.stats["selection"]["calls"] == 1
    assert outcome.gap is None


def test_selection_repairs_an_invalid_answer_once(tmp_path):
    def editor(messages):
        if len(messages) == 2:
            return _ok({"chosen": ["e9"], "reason": "坏"})
        return _ok({"chosen": ["e1", "e2", "e3"], "reason": "改好了"})

    _path, client, outcome = _four(tmp_path, editor)
    assert _editor_calls(client) == 2
    assert outcome.stats["selection"]["repairs"] == 1
    assert outcome.stats["selection"]["reason"] == "改好了"
    assert [entry["title"] for entry in outcome.content["entries"]] == ["事件1", "事件2", "事件3"]
    assert outcome.gap is None


def test_selection_drops_the_day_when_the_repair_is_still_invalid(tmp_path):
    _path, _client_obj, outcome = _four(tmp_path, lambda _messages: _ok({"chosen": ["e9"], "reason": "还是不对"}))
    assert outcome.content["entries"] == []
    assert outcome.content["overflow"] == 4
    assert outcome.gap == _SELECT_GAP
    assert "修正后仍不合格" in outcome.stats["selection"]["error"]
    assert outcome.stats["selection"]["repairs"] == 1


def test_selection_drops_the_day_when_the_call_fails(tmp_path):
    def editor(_messages):
        raise ChatError("down")

    _path, _client_obj, outcome = _four(tmp_path, editor)
    assert outcome.content["entries"] == []
    assert outcome.gap == _SELECT_GAP
    assert outcome.stats["selection"]["error"] == "调用失败：down"
    assert outcome.stats["selection"]["repairs"] == 0


def test_selection_drops_the_day_when_the_result_is_unknown(tmp_path):
    def editor(_messages):
        raise ChatUnknown("结果不明")

    _path, _client_obj, outcome = _four(tmp_path, editor)
    assert outcome.content["entries"] == []
    assert outcome.gap == _SELECT_GAP
    assert "不明" in outcome.stats["selection"]["error"]
    assert outcome.stats["selection"]["repairs"] == 0


def test_selection_drops_the_day_when_the_cutoff_has_passed(tmp_path, monkeypatch):
    import neckline.morning.sector_line as line

    real = line._select_delivery

    def wrapped(*args, **kwargs):
        kwargs["clock"] = lambda: datetime(2026, 10, 13, 9, 6, tzinfo=CST)
        return real(*args, **kwargs)

    monkeypatch.setattr(line, "_select_delivery", wrapped)
    _path, client, outcome = _four(tmp_path, lambda _messages: _ok({"chosen": ["e1", "e2", "e3"], "reason": "来不及"}))
    assert _editor_calls(client) == 0
    assert outcome.content["entries"] == []
    assert outcome.gap == _SELECT_GAP
    assert outcome.stats["selection"]["called"] is False
    assert outcome.stats["selection"]["error"] == "收口时刻前没比较"


def test_selection_resume_uses_the_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    path = _db(tmp_path)
    _concept(path)
    messages = [
        _msg(name, f"{name}印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 10 + offset, tzinfo=CST))
        for offset, name in enumerate(("工信部", "发改委", "央行", "财政部"))
    ]
    editor_hits = []

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if "板块分析员" in system:
            return _http(json.loads(_one_each(user).content))
        if "板块编辑" in system:
            editor_hits.append(1)
            raise httpx.ReadTimeout("no answer")
        codes = _leader_codes(user)
        found = re.search(r"事件：(.+)", user)
        payload = _answer(codes[:1] or ["300001"])
        payload["title"] = found.group(1) if found else payload["title"]
        return _http(payload)

    def client():
        return DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                              receipts=db.DbReceipts(path, "morning-2026-10-13"), timeout_seconds=5, max_attempts=1,
                              transport=httpx.MockTransport(handler))

    first = _run(path, messages, client())
    assert first.content["entries"] == [] and first.gap == _SELECT_GAP
    assert len(editor_hits) == 1
    second = _run(path, messages, client())
    assert len(editor_hits) == 1
    assert second.stats["selection"]["calls"] == 0
    assert second.content["entries"] == []


# ------------------------------------------------------------------ 真实入口


class _Search:
    def search(self, **_kwargs):
        return SearchOutcome({"results": []}, False, 0.0, None)


def test_morning_entry_selects_three_of_four_across_routes(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    path = _db(tmp_path)
    _with_device(path)
    cover_sector_history(path, DAY)
    _mentions(path, datetime(2026, 10, 9, 12, tzinfo=CST), 3, "短剧报道", "hist-e")
    _mentions(path, datetime(2026, 10, 12, 12, tzinfo=CST), 3, "短剧再报", "hist-l")
    morning = [
        doc("jin10-flash", "m1", "明确了范围。", at("2026-10-13", "07:10"), at("2026-10-13", "08:00"),
            title="工信部印发行业补贴方案"),
        doc("jin10-flash", "m2", "盘后交易活跃。", at("2026-10-13", "07:20"), at("2026-10-13", "08:00"),
            title="AMD盘后上涨"),
        doc("jin10-flash", "m3", "有新进展。", at("2026-10-13", "07:30"), at("2026-10-13", "08:00"),
            title="短剧游戏有新进展"),
        doc("jin10-flash", "m4", "明确了范围。", at("2026-10-13", "07:40"), at("2026-10-13", "08:00"),
            title="证监会印发行业补贴方案"),
    ]
    with db.write(path) as conn:
        db.store_documents(conn, morning)
        store_rows(conn, [
            {"code": code, "name": name, "fullname": "", "listDate": "2010-01-01", "aliases": [name],
             "summary": None, "summarySource": "none"}
            for code, name in (("300953.SZ", "震裕科技"), ("300683.SZ", "海特生物"), ("300082.SZ", "奥克股份"))
        ], now=datetime(2026, 10, 12, 4, tzinfo=timezone.utc), source="tushare")
        db.replace_sector_concepts(conn, [{
            "tsCode": "885728.TI", "name": "短剧游戏", "memberCount": 3,
            "members": ["300953.SZ", "300683.SZ", "300082.SZ"],
        }], refreshed_at="2026-10-12T00:00:00+00:00", sha256="routes")
    base = approved_config()
    rules = deepcopy(base.rules)
    rules["sectors"]["evaluation"]["concurrency"] = 1
    config = LoadedConfig(rules=rules, sha256=base.sha256, pool=base.pool, profiles=dict(base.profiles),
                          alias_seed=base.alias_seed)

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"] if len(body["messages"]) > 1 else ""
        if "初筛员" in system:
            return _http({"events": []})
        if "板块分析员" in system:
            events = []
            for number, title in re.findall(r"\[(n\d+)\].*｜([^｜]+)$", user, re.M):
                events.append(_event({
                    "title": title, "ids": [number], "sector": "短剧游戏", "reason": "直接", "repeat": False,
                }))
            return _http({"events": events, "dropped": []})
        if "板块编辑" in system:
            return _http({"chosen": ["e1", "e3", "e4"], "reason": "比较后留下"})
        codes = _leader_codes(user)
        found = re.search(r"事件：(.+)", user)
        payload = _answer(codes[:1] or ["300953"])
        payload["title"] = found.group(1) if found else payload["title"]
        return _http(payload)

    now = at("2026-10-13", "08:02")
    result = run_morning(
        path, now=now, config=config, clock=lambda: now, collector=lambda _now: {"status": "completed"},
        client_factory=lambda rid: _client(path, handler, rid, attempts=1),
        pusher=Pusher(path, FakeApns(), lambda: now), search_factory=lambda _rid: _Search())
    assert result["state"] == "available", result
    with db.read(path) as conn:
        report = db.get_report(conn, "2026-10-13")
        stored = db.list_sector_events(conn, "morning-2026-10-13")
    assert report["status"] == "available"
    stats = report["content"]["stats"]["sectors"]
    assert stats["routes"]["A"] >= 1 and stats["routes"]["B"] >= 1 and stats["routes"]["C"] >= 1
    assert stats["selection"]["called"] is True
    assert stats["selection"]["candidates"] == 4
    assert len(stats["selection"]["chosen"]) == 3
    assert stats["fermenting"]["ran"] is True and stats["fermenting"]["messages"] >= 1
    assert {row["state"] for row in stored} == {"succeeded"}
    assert all(row["routes"] for row in stored)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    latest = TestClient(app).get("/api/v4/reports/latest").json()
    sectors = latest["content"]["sectors"]
    assert sectors["overflow"] == 1
    assert [entry["title"] for entry in sectors["entries"]] == [
        "工信部印发行业补贴方案", "短剧游戏有新进展", "证监会印发行业补贴方案",
    ]
    assert all(set(entry) == {
        "conclusion", "firstPublished", "leaders", "risk", "sector", "sources", "title", "views",
    } for entry in sectors["entries"])
    assert "AMD盘后上涨" not in [entry["title"] for entry in sectors["entries"]]


# ------------------------------------------------------------------ 库


def _old_sector_ddl() -> str:
    return db.V6_DDL.replace("  routes TEXT NOT NULL DEFAULT '[]',\n", "")


def test_new_database_has_routes_and_v6_upgrade_adds_the_column(tmp_path):
    fresh = tmp_path / "fresh.db"
    db.init_schema(fresh)
    with closing(sqlite3.connect(fresh)) as conn:
        assert "routes" in [row[1] for row in conn.execute("PRAGMA table_info(sector_events)")]
    legacy = tmp_path / "v6.db"
    with closing(sqlite3.connect(legacy)) as conn:
        conn.executescript(_old_sector_ddl())
        conn.execute(
            """INSERT INTO sector_events(report_id, event_id, title, sector, reason, repeat, state, verdict,
               package_json, output_json, error, usage_json, seconds, prepared_at, settled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("morning-2026-10-13", "0123456789abcdef", "旧事件", "人工智能", "直接", 0, "skipped",
             None, "{}", None, None, "{}", None, "2026-10-13T00:00:00+00:00", "2026-10-13T00:00:00+00:00"))
        conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
        conn.execute("PRAGMA user_version=6")
        conn.commit()
        assert "routes" not in [row[1] for row in conn.execute("PRAGMA table_info(sector_events)")]
    db.init_schema(legacy)
    with closing(sqlite3.connect(legacy)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        row = conn.execute("SELECT title, routes FROM sector_events").fetchone()
        assert row == ("旧事件", "[]")
    already = tmp_path / "v7-old.db"
    with closing(sqlite3.connect(already)) as conn:
        conn.executescript(_old_sector_ddl())
        conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
        conn.execute("PRAGMA user_version=7")
        conn.commit()
    db.init_schema(already)
    with closing(sqlite3.connect(already)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert "routes" in [row[1] for row in conn.execute("PRAGMA table_info(sector_events)")]
