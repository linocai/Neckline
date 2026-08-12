"""V2.3.3 批 ⑤:`GET /api/v1/auction` 三态 + 五块契约。

🔴 最要紧的两条在这里被正面钉死:
  1. **「没跑」与「跑了没有」分得开**(§七 P0-39 同款病):当日无行 = **404**
     `auction_not_ready`;有行但 `baskets_covered=0` = **200** + `basketsUnavailableReason`
     说出口。⛔ 不许混成一句「今天没有竞价报告」。
  2. **「读不出」是独立第三态**(B1 定案):有行但 json 解不出 = **500**
     `auction_corrupt`,⛔ 不许降格成 404 —— 那份报告是冻结件(`INSERT OR IGNORE`
     永不覆盖),坏了就是永久坏的,当成"还没生成"会让客户端永远重试 = 静默永久失败。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

import neckline.auction as auction
from neckline.auction import AUCTION_MANUAL_NOTE, AUCTION_PROXY_SAMPLE_NOTE
from neckline.auction import store as astore
from neckline.db import connection

_CLIENT = Path(__file__).resolve().parents[1] / "client" / "Neckline"

D1 = "20260811"
D0 = "20260810"


def _insert_report(env, *, trade_date=D1, baskets_covered=1, llm_stage="ok",
                   market_overview="指数普遍高开,原主线还在。", manual=1,
                   overrides=None) -> None:
    cols = {
        "trade_date": trade_date, "d0_date": D0, "source": "sina",
        "captured_at": "2026-08-11T09:26:30+08:00",
        "requested_codes": 6, "fetched_codes": 5,
        "missing_codes_json": json.dumps(["300001.SZ"]),
        "conflict_codes_json": json.dumps([]),
        "data_quality": "degraded",
        "index_gaps_json": json.dumps({"000001.SH": {"ts_code": "000001.SH", "name": "上证综指",
                                                     "gap_pct": 0.004}}),
        "market_anchors_json": json.dumps([{"ts_code": "600111.SH", "name": "锚点股",
                                            "gap_pct": 0.031}]),
        "market_overview": market_overview,
        "risks_json": json.dumps([{"kind": "hit_invalidation", "text": "1 只命中 D0 失效位。"}]),
        "manual_note_attached": manual,
        "llm_stage": llm_stage, "llm_elapsed_ms": 1234,
        "baskets_covered": baskets_covered,
        "notes_json": json.dumps(["机械段备注一条"]),
        "created_at": "2026-08-11T01:26:31Z", "updated_at": "2026-08-11T01:27:02Z",
    }
    cols.update(overrides or {})
    with connection(env.db_path) as conn:
        conn.execute(
            f"INSERT INTO auction_reports ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))


def _insert_verdict(env, *, basket_id=1, verdict="neutral", verdict_raw="confirm",
                    clamped_by="clamped_by_single_strong", overrides=None) -> None:
    cols = {
        "basket_id": basket_id, "trade_date": D1, "d0_date": D0, "basket_key": "k1",
        "name": "测试篮", "covered_tier": 1, "engine_code": "Z", "engine_version": "Z1",
        "skeleton_version": "K8-V0.7", "regime_at_d0": "trend_continuation",
        "data_quality": "ok",
        "members_json": json.dumps([{
            "ts_code": "600000.SH", "name": "浦发银行", "role": "leader",
            "auction_price": 10.5, "pre_close": 10.0, "gap_pct": 0.05,
            "auction_volume": 12000.0, "auction_amount": 126000.0,
            "vol_vs_prev5_frac": 0.08, "rel_to_sector": 0.012, "rel_to_index": 0.046,
            "hit_invalidation": False, "gap_up_deviation": True, "anchor_stale": False,
            "plan_fit": "above_max_chase", "data_quality": "ok",
            "volume_note": "竞价放量",
        }]),
        "sector_sync_json": json.dumps({"up_count": 3, "down_count": 1}),
        "rel_strength_json": json.dumps({"median_rel_to_sector": 0.01}),
        "history_json": json.dumps({"history_days_available": 2}),
        "hit_invalidation_json": json.dumps(["600000.SH"]),
        "plan_consistency_json": json.dumps({"counts": {"above_max_chase": 1}}),
        "verdict": verdict, "verdict_raw": verdict_raw, "clamped_by": clamped_by,
        "reasons_json": json.dumps(["只有一只竞价强股"]),
        "llm_fields_json": json.dumps({"verdict": verdict_raw}),
        "manual_note_attached": 1, "llm_stage": "ok",
        "created_at": "2026-08-11T01:26:31Z", "updated_at": "2026-08-11T01:27:02Z",
    }
    cols.update(overrides or {})
    with connection(env.db_path) as conn:
        conn.execute(
            f"INSERT INTO auction_verdicts ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))


# ══════════════════════════════════════════════════════════════════════════
# 三态
# ══════════════════════════════════════════════════════════════════════════

def test_no_row_is_404_auction_not_ready(client, AUTH):
    """当日**无行** = 竞价层没跑过 → 404 + 全新 reason。"""
    r = client.get(f"/api/v1/auction?date={D1}", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "auction_not_ready"


def test_corrupt_json_is_500_and_never_downgraded_to_404(client, AUTH, api_env):
    """**有行但读不出** = 500 `auction_corrupt`。

    ⛔ 降格成 404 `auction_not_ready` 就是让客户端永远重试一份永远不会来的报告
    (报告是冻结件,坏了不会自己好)。"""
    _insert_report(api_env, overrides={"index_gaps_json": "{不是 json",
                                       "market_anchors_json": "[[[",
                                       "risks_json": "}{",
                                       "missing_codes_json": "nope",
                                       "conflict_codes_json": "nope",
                                       "notes_json": "nope"})
    r = client.get(f"/api/v1/auction?date={D1}", headers=AUTH)
    assert r.status_code == 500
    assert r.json()["detail"]["reason"] == "auction_corrupt"


def test_zero_baskets_is_200_with_reason_not_404(client, AUTH, api_env):
    """🔴 **跑过了、D0 当天就没有 T1/T2 篮子** —— 200 + 把原因说出口。

    ⛔ 与「竞价层没跑」(404)必须分得开:混成一句会把系统缺席讲成一次市场判断。"""
    _insert_report(api_env, baskets_covered=0, manual=0)
    r = client.get(f"/api/v1/auction?date={D1}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["baskets"] == []
    assert body["basketsUnavailableReason"], "篮子为空必须说出口"
    assert "没跑" in body["basketsUnavailableReason"]


def test_two_hundred_carries_all_five_blocks(client, AUTH, api_env):
    """五块齐:数据状态 / 市场概览 / 篮子逐票 / 异常风险 / 小纸条。"""
    _insert_report(api_env)
    _insert_verdict(api_env)
    r = client.get(f"/api/v1/auction?date={D1}", headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["tradeDate"] == D1 and b["d0Date"] == D0
    # 第 1 块
    assert b["dataStatus"]["source"] == "sina"
    assert b["dataStatus"]["fetchedCodes"] == 5 and b["dataStatus"]["requestedCodes"] == 6
    assert b["dataStatus"]["missingCodes"] == ["300001.SZ"]
    assert b["dataStatus"]["dataQuality"] == "degraded"
    # 第 2 块
    assert b["marketOverview"]["indexGaps"][0]["tsCode"] == "000001.SH"
    assert b["marketOverview"]["anchors"][0]["tsCode"] == "600111.SH"
    assert b["marketOverview"]["text"]
    assert b["marketOverview"]["textUnavailableReason"] is None
    # 第 3 块
    assert len(b["baskets"]) == 1 and b["basketsUnavailableReason"] is None
    v = b["baskets"][0]
    assert v["basketKey"] == "k1" and v["coveredTier"] == 1
    assert v["engineCode"] == "Z" and v["engineVersion"] == "Z1"
    assert v["members"][0]["tsCode"] == "600000.SH"
    assert v["hitInvalidation"] == ["600000.SH"]
    # 第 4 块
    assert b["risks"][0]["kind"] == "hit_invalidation"
    # 第 5 块 + 恒发披露
    assert b["manualNote"] == AUCTION_MANUAL_NOTE
    assert b["proxySampleNote"] == AUCTION_PROXY_SAMPLE_NOTE


def test_manual_note_absent_when_not_attached(client, AUTH, api_env):
    """小纸条**挂了才发**(K8:只出现在中性 / 证据冲突 / 临界标的旁边)。"""
    _insert_report(api_env, manual=0)
    r = client.get(f"/api/v1/auction?date={D1}", headers=AUTH)
    assert r.json()["manualNote"] is None
    # 但代理样本那句话**恒发**(§五 ⑨-B-2)。
    assert r.json()["proxySampleNote"] == AUCTION_PROXY_SAMPLE_NOTE


# ══════════════════════════════════════════════════════════════════════════
# 诚实披露:枚举码原样发 · 三态布尔不折 · LLM 缺席有原因
# ══════════════════════════════════════════════════════════════════════════

def test_enum_codes_are_sent_raw_not_translated(client, AUTH, api_env):
    """🔴 **一律发枚举码,中文换算在客户端**(CLAUDE.md 连踩三次的坑)。"""
    _insert_report(api_env)
    _insert_verdict(api_env)
    v = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()["baskets"][0]
    assert v["verdict"] == "neutral" and v["verdictRaw"] == "confirm"
    assert v["clampedBy"] == "clamped_by_single_strong"
    assert v["dataQuality"] == "ok"
    assert v["members"][0]["planFit"] == "above_max_chase"
    assert v["members"][0]["role"] == "leader"


def test_member_tristate_booleans_survive_the_wire(client, AUTH, api_env):
    """`hitInvalidation` / `gapUpDeviation` 的 **`null` = 没判**(锚失效 / 无阈值 /
    价拿不到),⛔ 绝不许在契约层折成 `false`「没问题」。"""
    _insert_report(api_env)
    _insert_verdict(api_env, overrides={"members_json": json.dumps([{
        "ts_code": "600000.SH", "hit_invalidation": None, "gap_up_deviation": None,
        "anchor_stale": True, "plan_fit": "unknown", "data_quality": "insufficient"}])})
    m = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()["baskets"][0]["members"][0]
    assert m["hitInvalidation"] is None and m["gapUpDeviation"] is None
    assert m["anchorStale"] is True


@pytest.mark.parametrize("stage,needle", [
    ("pending_explanation", "9:29"),
    ("provider_none", "provider"),
    ("parse_failed", "解析"),
    ("budget_exhausted", "预算"),
    ("call_failed:TimeoutError", "call_failed"),
])
def test_missing_overview_always_says_why(client, AUTH, api_env, stage, needle):
    """`marketOverview.text` 为 `null` 时**必须**有一句原因 —— ⛔ 不冒充"没内容"。

    ⚠ 「9:29 到了模型还没回」是**设计内**的,文案照实说,⛔ 不写成"出错了"。"""
    _insert_report(api_env, llm_stage=stage, market_overview=None)
    b = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()
    assert b["marketOverview"]["text"] is None
    reason = b["marketOverview"]["textUnavailableReason"]
    assert reason and needle in reason
    assert b["llmStage"] == stage


def test_corrupt_basket_row_degrades_that_row_only_and_names_it(client, AUTH, api_env):
    """一篮的 json 读不出 → **不升级成整份 500**(市场段与其余篮子是好数据),
    但**必须当面点名**在 `notes` 里 —— ⛔ 不许静默退化成空段。"""
    _insert_report(api_env, baskets_covered=1)
    _insert_verdict(api_env, overrides={"members_json": "{坏了"})
    r = client.get(f"/api/v1/auction?date={D1}", headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["baskets"][0]["members"] == []
    assert any("members_json" in n and "读不出" in n for n in b["notes"]), b["notes"]


def test_requires_token(client):
    r = client.get(f"/api/v1/auction?date={D1}")
    assert r.status_code in (401, 403)


def test_endpoint_writes_nothing(client, AUTH, api_env):
    """⛔ **零写库**(常驻服务与盘中哨兵同进程,P0-23):打三次,两张表行数不变。"""
    _insert_report(api_env)
    _insert_verdict(api_env)

    def _counts():
        with connection(api_env.db_path) as conn:
            return (conn.execute("SELECT COUNT(*) FROM auction_reports").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM auction_verdicts").fetchone()[0])

    before = _counts()
    for _ in range(3):
        assert client.get(f"/api/v1/auction?date={D1}", headers=AUTH).status_code == 200
    assert _counts() == before


def test_default_date_is_today(client, AUTH, api_env):
    """`date` 缺省 = 今天(D1)。今天没跑过就是 404 —— 与给了非法日期时同一条路径。"""
    today = date.today().strftime("%Y%m%d")
    _insert_report(api_env, trade_date=today, baskets_covered=0, manual=0)
    assert client.get("/api/v1/auction", headers=AUTH).status_code == 200
    assert client.get("/api/v1/auction?date=abc", headers=AUTH).status_code == 200


def test_store_read_path_is_the_only_source(api_env):
    """端点读的就是 `auction/store.py` 那两个读函数(⛔ 不另写一份 SQL)。"""
    _insert_report(api_env)
    assert astore.load_report(D1, db_path=api_env.db_path) is not None
    assert astore.load_report("20260812", db_path=api_env.db_path) is None


# ══════════════════════════════════════════════════════════════════════════
# 展示层换算:**服务端能发的每一个码,客户端都得有中文**
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 这条闸抓的是 CLAUDE.md 里**连踩三次**的那个病:服务端发 `pullback_leader` /
# `rejected_not_above_close` / `role · leader`,客户端直接印上界面。⚠ 那类回归
# **实拍才看得见**,而实拍不是每次都出 —— 所以把"每个码都有 case"钉成机器判据。

def _swift_switch_cases(func_name: str, *, file: str | None = None) -> set:
    # 🔴 V2.4.0 P3.7:DTO 已拆成 `Networking/Models/*.swift` 六份 —— 缺省读**拼接后
    # 的全量 DTO 文本**(带哨兵自检),⛔ 不再按单一文件名读(读不到的文件里当然
    # 也搜不到 case,那会让「每个码都有 case」这条静默变成假绿)。
    from tests.client_sources import models_text
    text = models_text() if file is None else (_CLIENT / file).read_text(encoding="utf-8")
    assert f"func {func_name}(" in text, f"客户端缺换算函数 `{func_name}`"
    body = text.split(f"func {func_name}(", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'case "([a-z0-9_:]+)"', body))


@pytest.mark.parametrize("func,codes", [
    ("nkAuctionVerdictLabel", (auction.VERDICTS + (auction.VERDICT_PENDING_EXPLANATION,))),
    ("nkAuctionDataQualityLabel", (auction.DQ_OK, auction.DQ_DEGRADED, auction.DQ_INSUFFICIENT)),
    ("nkAuctionClampLabel", auction.CLAMP_CODES),
    ("nkAuctionPlanFitLabel", auction.PLAN_FIT_CODES),
])
def test_every_server_code_has_a_client_label(func, codes):
    cases = _swift_switch_cases(func)
    missing = set(codes) - cases
    assert not missing, (
        f"`{func}` 没覆盖服务端可发的码:{sorted(missing)} —— 未覆盖 = 那个码会**原样印在"
        f"界面上**(CLAUDE.md 连踩三次的坑)。"
    )


def test_risk_kind_labels_cover_every_risk_constant():
    """`RISK_*` 是**在服务端一处定义、会直接进契约**的一族 —— 少一个就会在
    「异常与风险」那一块印出机器标识符。"""
    codes = {v for k, v in vars(auction).items() if k.startswith("RISK_") and isinstance(v, str)}
    missing = codes - _swift_switch_cases("nkAuctionRiskKindLabel")
    assert not missing, f"`nkAuctionRiskKindLabel` 没覆盖:{sorted(missing)}"


def test_llm_stage_labels_cover_every_stage_including_the_prefixed_one():
    """`call_failed:<原因>` 带冒号后缀,故走 `hasPrefix` 而不是 `case` —— 这条把
    「前缀分支还在」一并钉住(删了它界面上会印 `call_failed:TimeoutError`)。"""
    from tests.client_sources import models_text
    text = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读
    body = text.split("func nkAuctionLlmStageLabel(", 1)[1].split("\n}", 1)[0]
    assert 'hasPrefix("call_failed")' in body
    codes = {v for k, v in vars(auction).items()
             if k.startswith("LLM_") and isinstance(v, str)} - {auction.LLM_CALL_FAILED}
    missing = codes - set(re.findall(r'case "([a-z_]+)"', body))
    assert not missing, f"`nkAuctionLlmStageLabel` 没覆盖:{sorted(missing)}"


def test_server_facing_text_carries_no_markdown(client, AUTH, api_env):
    """🔴 **服务端下发给界面的每一段文字里⛔ 不许有 Markdown**(V2.3.3 批 ⑤ 实拍逮到)。

    客户端拿到的是 `String`,而 **`Text(String)` 不解析 Markdown**(只有
    `Text("字面量")` 解析)—— `**代理样本**` / `**没判**` 会把两个星号**原样印在屏幕上**。
    ⚠ 这类回归**编译不报错、单测也测不出**,只有实拍看得见 —— 所以把它钉成机器判据。
    要强调就用「」。
    """
    _insert_report(api_env)
    _insert_verdict(api_env)
    body = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()

    def _walk(node, path="$"):
        if isinstance(node, str):
            assert "**" not in node, f"{path} 里带了 Markdown 加粗:{node!r}"
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")

    _walk(body)
    # 文案常量本身也扫一遍(它们不一定每次都在响应里,但每一条都会随契约下发)。
    from neckline.auction import mech as _am

    for const in (AUCTION_MANUAL_NOTE, AUCTION_PROXY_SAMPLE_NOTE,
                  _am.HISTORY_LOOKBACK_NOTE, _am.HISTORY_SAMPLE_INSUFFICIENT_NOTE,
                  _am.SECTOR_PEER_POOL_NOTE, _am.LISTING_BOARD_BENCHMARK_NOTE,
                  _am._LISTING_BOARD_BENCH_LABEL):
        assert "**" not in const, const

    # 机械层的 `risks[].text` 是另一条会直连界面的路 —— 一并扫。
    from neckline.auction import mech as am

    # ⚠ 用真的 `MemberReading`(⛔ 别再用鸭子类型桩:三态字段一加,桩就跟不上,
    # 而这条守门扫的是**文案**,不该因为桩缺个属性而红)。两只票分别命中
    # 「锚失效」与「没判(卡上无冻结价位)」两条风险文案。
    stale = am.MemberReading(ts_code="600000.SH", anchor_stale=True, volume_note="竞价放量",
                             gap_up_deviation=True)
    undet = am.MemberReading(ts_code="600001.SH",
                             hit_invalidation_undetermined_reason="no_stop_line",
                             gap_up_deviation=False)

    class _B:
        hit_invalidation_codes = ["600000.SH"]
        members = [stale, undet]

    market = am.MarketMech(missing_codes=["300001.SZ"], fetched_codes=5, requested_codes=6)
    kinds = [r["kind"] for r in am._mechanical_risks(market, [_B()])]
    assert "anchor_stale" in kinds and "invalidation_undetermined" in kinds
    for r in am._mechanical_risks(market, [_B()]):
        assert "**" not in r["text"], r


def test_no_client_text_concatenates_markdown_with_plus():
    """🔴 **`Text("a" + "b")` 里的 Markdown 会把星号原样印在屏幕上**(V2.3.3 批 ⑦ 实拍
    逮到两处)。

    `"a" + "b"` 的结果是 `String`,而 **`Text(String)` 不解析 Markdown** —— 只有
    `Text("字面量")` 解析。要拼就拼成**一整条字面量**,要传参就把形参声明成
    `LocalizedStringKey`。⚠ 这类回归**编译不报错、单测也测不出**,只有实拍看得见 ——
    所以在这里钉成机器判据(全客户端扫,不只竞价那几个文件)。
    """
    bad = []
    for path in sorted((_CLIENT).rglob("*.swift")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'Text\(\s*"((?:[^"\\]|\\.)*)"\s*\n?\s*\+\s*"((?:[^"\\]|\\.)*)"',
                             src):
            if "**" in (m.group(1) + m.group(2)):
                bad.append(f"{path.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert not bad, (
        f"这些 `Text(...)` 用 `+` 拼了带 Markdown 的字面量,星号会原样印在屏幕上:{bad}"
    )


def test_manual_note_text_lives_only_on_the_server():
    """🔴 小纸条的文案本体是**服务端常量**(K8 §二十 逐字)—— 客户端原样透传,
    ⛔ 不许自己写一份(两份就会漂,而 K8 说的是"固定内容")。"""
    from tests.client_sources import models_text
    srcs = {"Views/AuctionCardView.swift": (_CLIENT / "Views" / "AuctionCardView.swift")
            .read_text(encoding="utf-8"),
            "Networking/Models/*.swift": models_text()}   # V2.4.0 P3.7:拆六份后整体扫
    for name, src in srcs.items():
        assert "虚拟开盘价是否稳定" not in src, f"{name} 里抄了一份小纸条文案"


# ══════════════════════════════════════════════════════════════════════════
# iPhone 402pt 版式契约(**编译不报错、单测测不出、只有实拍看得见** → 钉成判据)
# ══════════════════════════════════════════════════════════════════════════

def test_auction_member_row_stays_402pt_safe():
    """🔴 逐票行在 iPhone 402pt 上的版式契约(体例照
    `test_out_candidates.py::test_out_candidate_row_stays_402pt_safe`)。

    「名称 + 代码 + 角色 + 竞价涨幅 + 量比 + 相对强弱 + 判定徽标」**一行放不下**:
    会把名称挤成两行、把中文徽标压成竖排单字(V2.3 成员卡实拍逮到过)。
    契约 = **iOS 分两行 / macOS 一行**:读数与徽标在 iOS 上必须落到次行。
    """
    src = (_CLIENT / "Views" / "AuctionCardView.swift").read_text(encoding="utf-8")
    block = src.split("struct AuctionMemberRowView: View {", 1)[1]
    head = block.split("#if os(iOS)", 1)[0]
    # ① 首行的名称必须截断(少了它 402pt 上会撑成两行)
    assert ".lineLimit(1)" in head, "首行名称少了 lineLimit(1)"
    # ② 读数与徽标只在 macOS 首行出现;iOS 走次行
    assert "#if os(macOS)" in head and "metrics" in head and "statusBadges" in head
    ios_second_row = block.split("#if os(iOS)", 1)[1].split("#endif", 1)[0]
    assert "metrics" in ios_second_row and "statusBadges" in ios_second_row, (
        "iOS 次行必须承载读数与判定徽标 —— 搬回首行 = 402pt 上挤爆"
    )


def test_auction_verdict_card_head_splits_on_ios():
    """篮子头行同理:名 + T 等级 + 引擎 + 结论徽标 + 数据质量徽标,iOS 分两行。"""
    src = (_CLIENT / "Views" / "AuctionCardView.swift").read_text(encoding="utf-8")
    block = src.split("struct AuctionVerdictCard: View {", 1)[1]
    head = block.split("private var head: some View {", 1)[1].split("\n    }", 1)[0]
    assert "#if os(macOS)" in head and "#if os(iOS)" in head
    assert head.count("badges") >= 2, "两个平台各摆一次徽标行(macOS 首行 / iOS 次行)"


def test_client_never_paints_an_empty_card_when_not_ready():
    """404 `auction_not_ready` = **合法空态** → ⛔ 不画那张卡(空卡是噪声)。
    500 `auction_corrupt` 才画,而且文案必须是「需要排查」不是「还没生成」。"""
    src = (_CLIENT / "Views" / "AuctionCardView.swift").read_text(encoding="utf-8")
    body = src.split("struct AuctionSummaryCard: View {", 1)[1].split("\n}", 1)[0]
    assert "if let a = model.auction" in body and "model.auctionCorrupt" in body
    assert "读不出" in src and "还没生成" not in src.split("corruptCard", 1)[1][:1200]


# ══════════════════════════════════════════════════════════════════════════
# 🔴 用户裁定 P3-69 / P3-70(2026-08-12)的**契约面**守门
# ══════════════════════════════════════════════════════════════════════════

def test_rel_strength_fields_are_passed_through_to_the_contract(client, AUTH, api_env):
    """🔴 裁定 P3-70:逐票必须发出「减的是哪一支 / 哪一组」+ 「没有时为什么」。

    ⛔ 只发两个数字 = 读者(与复盘)永远查不到出处。
    """
    _insert_report(api_env)
    _insert_verdict(api_env, overrides={"members_json": json.dumps([{
        "ts_code": "600000.SH", "name": "浦发银行", "gap_pct": 0.05,
        "rel_to_sector": 0.02, "rel_to_index": 0.046,
        "rel_to_sector_source": "peer_median", "rel_to_sector_reason": None,
        "sector_peer_codes": ["600100.SH", "600101.SH", "600102.SH"],
        "sector_index_code": None, "sector_benchmark_gap_pct": 0.03,
        "industry": "半导体",
        "index_benchmark_code": "000001.SH", "index_benchmark_gap_pct": 0.004,
        "rel_to_index_reason": None,
        "hit_invalidation": False, "gap_up_deviation": False,
        "plan_fit": "in_zone", "data_quality": "ok",
    }])})
    m = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()["baskets"][0]["members"][0]
    assert m["relToSector"] == 0.02 and m["relToIndex"] == 0.046
    assert m["relToSectorSource"] == "peer_median"
    assert m["sectorPeerCodes"] == ["600100.SH", "600101.SH", "600102.SH"]
    assert m["sectorBenchmarkGapPct"] == 0.03 and m["industry"] == "半导体"
    assert m["indexBenchmarkCode"] == "000001.SH" and m["indexBenchmarkGapPct"] == 0.004


def test_missing_rel_readings_stay_null_with_a_reason_and_never_become_zero(client, AUTH, api_env):
    """🔴 裁定 ③ + 本版红线:「没有」≠「持平」。

    `null` **原样透传**、原因码一并发出 —— ⛔ 不许在服务端折成 `0.0`,
    ⛔ 也不许省略键(省略 = 客户端只能猜)。
    """
    _insert_report(api_env)
    _insert_verdict(api_env, overrides={"members_json": json.dumps([{
        "ts_code": "688001.SH", "name": "科创票", "gap_pct": 0.05,
        "rel_to_sector": None, "rel_to_index": None,
        "rel_to_sector_source": "unavailable",
        "rel_to_sector_reason": "data_insufficient",
        "sector_peer_codes": ["600100.SH"],
        "rel_to_index_reason": "board_excluded", "index_benchmark_code": None,
        "hit_invalidation": False, "gap_up_deviation": False,
        "plan_fit": "in_zone", "data_quality": "ok",
    }])})
    m = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()["baskets"][0]["members"][0]
    assert m["relToSector"] is None and m["relToIndex"] is None
    assert "relToSector" in m and "relToIndex" in m, "⛔ 不许省略键"
    assert m["relToSectorReason"] == "data_insufficient"
    assert m["relToIndexReason"] == "board_excluded", "科创板⛔ 不许 fallback 到别的指数"
    assert m["indexBenchmarkCode"] is None


def test_old_rows_without_the_new_keys_degrade_honestly(client, AUTH, api_env):
    """整改**之前**冻的 `members_json` 没有这些键 → 缺省 `unavailable` / `None`,
    客户端照实说「原因未记录」—— ⛔ 仍不许渲染成 0 或「持平」。"""
    _insert_report(api_env)
    _insert_verdict(api_env)          # 老形状(不带新键)
    m = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()["baskets"][0]["members"][0]
    assert m["relToSectorSource"] == "unavailable"
    assert m["relToSectorReason"] is None and m["sectorPeerCodes"] == []
    assert m["indexBenchmarkCode"] is None


def test_history_sufficiency_flag_and_notes_reach_the_contract(client, AUTH, api_env):
    """🔴 裁定 P3-69:`n < 15` 的「历史样本不足」标志与那句话必须到得了界面。"""
    _insert_report(api_env)
    _insert_verdict(api_env, overrides={"history_json": json.dumps({
        "history_days_available": 3,
        "history_lookback_trading_days": 20,
        "history_lookback_days": 60,
        "history_min_sample_for_comparison": 15,
        "history_sample_sufficient": False,
        "history_excludes_today": True,
        "history_insufficient_note": "当期有效样本不足 15 天,按「历史样本不足」处理:"
                                     "只展示原始值,不形成历史比较结论。",
        "history_lookback_note": "这一项回看最近 20 个有效交易日的竞价快照",
    })})
    h = client.get(f"/api/v1/auction?date={D1}", headers=AUTH).json()["baskets"][0]["history"]
    assert h["history_days_available"] == 3
    assert h["history_sample_sufficient"] is False
    assert h["history_lookback_trading_days"] == 20 and h["history_lookback_days"] == 60
    assert "历史样本不足" in h["history_insufficient_note"]
