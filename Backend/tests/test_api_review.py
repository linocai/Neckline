"""4D 周复盘 API 端点单测(plan 4D 验收:上传解析对账/历史回放/col_map 可配/鉴权)。"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import openpyxl
import pytest

from .conftest import insert_stock_basic


FORMAT1_HEADER = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额", "费用", "发生金额", "资金余额", "备注"]


def _xlsx_bytes(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "对账单"
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sample_workbook() -> bytes:
    return _xlsx_bytes([
        FORMAT1_HEADER,
        [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
        [datetime(2026, 7, 16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""],
    ])


@pytest.fixture
def review_env(api_env):
    insert_stock_basic(api_env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"}])
    return api_env


def test_upload_requires_auth(client):
    r = client.post("/api/v1/review/upload", files={"files": ("t.xlsx", _sample_workbook(), "application/octet-stream")})
    assert r.status_code == 401


def test_upload_and_get_roundtrip(client, AUTH, review_env):
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("交割单.xlsx", _sample_workbook(), "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["weeks"]) == 1
    week = body["weeks"][0]
    assert week["week"].startswith("2026-W")
    result = week["result"]
    assert result["stats"]["closedCount"] == 1
    assert result["roundTrips"][0]["tsCode"] == "600519.SH"
    assert week["material"]   # 确定性材料非空

    # GET /review?week= 应能读到刚落库的结果
    g = client.get(f"/api/v1/review?week={week['week']}", headers=AUTH)
    assert g.status_code == 200
    gbody = g.json()
    assert gbody["found"] is True
    assert gbody["result"]["stats"]["closedCount"] == 1
    assert gbody["material"]


def test_get_unknown_week_returns_not_found(client, AUTH, review_env):
    r = client.get("/api/v1/review?week=2099-W01", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_get_missing_week_param_returns_not_found(client, AUTH, review_env):
    r = client.get("/api/v1/review", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_upload_multiple_files_combined(client, AUTH, review_env):
    """两份文件(如分批导出)应合并解析、FIFO 跨文件闭合。"""
    file1 = _xlsx_bytes([FORMAT1_HEADER, [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""]])
    file2 = _xlsx_bytes([FORMAT1_HEADER, [datetime(2026, 7, 16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""]])
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files=[("files", ("a.xlsx", file1, "application/octet-stream")), ("files", ("b.xlsx", file2, "application/octet-stream"))],
    )
    body = r.json()
    assert len(body["weeks"]) == 1
    assert body["weeks"][0]["result"]["stats"]["closedCount"] == 1


def test_upload_invalid_file_degrades_not_crashes(client, AUTH, review_env):
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("bad.xlsx", b"not a real xlsx", "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["weeks"] == []
    assert any("bad.xlsx" in w for w in body["parseWarnings"])


def test_review_col_map_setting_roundtrip(client, AUTH, review_env):
    put = client.put("/api/v1/settings/review-col-map", headers=AUTH, json={"colMap": {"手续费": "费用合计"}})
    assert put.status_code == 200 and put.json()["ok"] is True
    got = client.get("/api/v1/settings", headers=AUTH).json()
    assert got["reviewColMap"] == {"手续费": "费用合计"}


def test_upload_uses_stored_col_map(client, AUTH, review_env):
    """存进 `review_col_map` 的映射应在下一次上传时生效(4D 验收:「改 review_col_map
    能吃另一种列名」)。"""
    custom_header = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额", "费用合计", "发生金额", "资金余额", "备注"]
    data = _xlsx_bytes([
        custom_header,
        [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
    ])
    # 未配置 col_map 前:内置默认列名"费用"找不到 → 该行反推价格失败,解析出 0 笔交易
    baseline = client.post("/api/v1/review/upload", headers=AUTH, files={"files": ("t.xlsx", data, "application/octet-stream")})
    assert baseline.json()["weeks"] == []

    client.put("/api/v1/settings/review-col-map", headers=AUTH, json={"colMap": {"手续费": "费用合计"}})
    result = client.post("/api/v1/review/upload", headers=AUTH, files={"files": ("t.xlsx", data, "application/octet-stream")})
    body = result.json()
    assert len(body["weeks"]) == 1
    assert body["weeks"][0]["result"]["roundTrips"][0]["buyPrice"] == pytest.approx(1500.0)


# ══════════════════════════════════════════════════════════════════════════
# V2.1-⑤ 复盘板块:GET /review/overview · GET /review/handoff
#
# 🔴 **两条端点的三条硬边界**(plan §五⑤,逐条在下面有断言):
#   ① **零现算** —— 只读离线落盘 / 已冻结的产物。这两条跑在常驻 `neckline.service`
#      里、**与盘中哨兵同进程**;§七 P0-23:重活进常驻服务 = `MemoryHigh` 先节流 →
#      卡死不报错。**静态守门在 `test_review_handoff.py`,运行期证明在这里**
#      (把 `build_report` 换成会抛的桩,两条端点仍 200)。
#   ② **一律不 404** —— 空态走 `available=false` → V2.1 零新增 reason 字符串。
#   ③ **五段各自独立说「有 / 没有 / 没取到」** —— ⛔ 不许一个总开关罩住五段。
# ══════════════════════════════════════════════════════════════════════════

from datetime import date as _date                                    # noqa: E402

from .conftest import business_days, insert_trade_cal                 # noqa: E402

_WEEK_MON = _date(2026, 8, 3)          # 2026-08-03 是周一
_WEEK_ANY = "20260805"                 # 该周的周三(端点接"该周任意一天")


def _cal_dir(api_env):
    return api_env.data_dir / "reports" / "calibration"


def _write_calibration(api_env, date_from="20260803", date_to="20260807",
                       *, md=True, json_text=None):
    import json as _json

    d = _cal_dir(api_env)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"calibration_{date_from}_{date_to}"
    payload = {
        "specVersion": "weekly_calibration_v1", "dateFrom": date_from, "dateTo": date_to,
        "generatedAt": "20260808", "nTradingDays": 5, "nBaskets": 12,
        "strata": [{"packVersion": "K7-pack-v1", "rulesetVersion": "vr-1",
                    "nDays": 5, "nBaskets": 12,
                    "tierMonotonicity": {"counts": {"1": 4, "2": 8}}}],
        "placebo": [], "honesty": {"baskets": 12}, "notes": [], "disclaimer": "回看审计。",
    }
    (d / f"{stem}.json").write_text(
        json_text if json_text is not None else _json.dumps(payload, ensure_ascii=False),
        encoding="utf-8")
    if md:
        (d / f"{stem}.md").write_text("# 周度校准报告 · 原文\n\n(正文)\n", encoding="utf-8")


@pytest.fixture
def week_env(api_env):
    """把 2026-08-03 那一周的交易日历落进隔离库 —— `week_bounds()` 取的是**交易日**
    首尾,校准产物的文件名也由它决定,两者同源。"""
    insert_trade_cal(api_env, business_days(_WEEK_MON, 5))
    return api_env


def _overview(client, AUTH, week=_WEEK_ANY):
    r = client.get(f"/api/v1/review/overview?week={week}", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


class TestReviewOverview:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/review/overview").status_code == 401

    def test_each_segment_answers_independently(self, client, AUTH, week_env):
        """验收原文:**各段各自能独立说出「有 / 没有 / 没取到」**。

        🔴 V2.5.0 S1:原「五段」中的 `preference` / `capability` 两段随 `profile/` 整包
        退役而**删除**(不是留成恒 `available=false` 的壳);V2.2-④ 追加的
        `selectionClock` / `tradeClock` / `iterationSuggestions` 三段随双时钟复盘退役
        一并删除。剩下三段。"""
        body = _overview(client, AUTH)
        assert body["weekStart"] == "20260803" and body["weekEnd"] == "20260807"
        assert body["weekKey"].startswith("2026-W")
        for seg in ("calibration", "reconcile", "observations"):
            assert seg in body, f"缺段:{seg}"
            assert "available" in body[seg] and "unavailableReason" in body[seg]
        for gone in ("preference", "capability", "selectionClock", "tradeClock",
                     "iterationSuggestions"):
            assert gone not in body, f"{gone} 段已随 K8 退役,⛔ 不许留成恒空的壳"

    def test_calibration_segment_reads_the_landed_artifact_only(self, client, AUTH, week_env):
        _write_calibration(week_env)
        seg = _overview(client, AUTH)["calibration"]
        assert seg["available"] is True and seg["asOf"] == "20260803→20260807"
        # 🔴 包成绩单 = 产物里的 `strata` **本身**,⛔ 不另建第二份聚合
        assert seg["detail"]["strata"][0]["packVersion"] == "K7-pack-v1"
        assert seg["detail"]["nBaskets"] == 12

    def test_missing_artifact_is_not_generated_not_a_failure(self, client, AUTH, week_env):
        seg = _overview(client, AUTH)["calibration"]
        assert seg["available"] is False
        assert "尚未生成" in seg["unavailableReason"] and "不补算" in seg["unavailableReason"]
        assert "读不出" not in seg["unavailableReason"]

    def test_corrupt_artifact_says_something_different(self, client, AUTH, week_env):
        """🔴 承 V2 B1 同一条裁定:**「没生成」会自愈、「读不出」不会** —— 混成一句
        就是叫人一直等一份永远好不了的产物。"""
        _write_calibration(week_env, json_text="{坏了")
        seg = _overview(client, AUTH)["calibration"]
        assert seg["available"] is False
        assert "读不出" in seg["unavailableReason"] and "人工排查" in seg["unavailableReason"]
        assert "尚未生成" not in seg["unavailableReason"]

    def test_missing_window_points_at_the_latest_one_that_does_exist(self, client, AUTH, week_env):
        _write_calibration(week_env, "20260727", "20260731")
        seg = _overview(client, AUTH)["calibration"]
        assert seg["available"] is False
        assert seg["detail"]["latestAvailable"] == "20260727→20260731"


    def test_reconcile_without_a_statement_is_found_false_not_unavailable(
            self, client, AUTH, week_env):
        """🔴 **「没有」不是「没看」**:对账的必需输入(券商交割单)只能由用户手动给,
        系统查过 `reviews` 表、确实没有这一行 —— 那是**有答案**,故 `available=true`
        + `found=false`。⚠ 与画像段(系统自己那一步没跑 = 没看 = `available=false`)
        **刻意判得不同**,⛔ 别"统一"。"""
        seg = _overview(client, AUTH)["reconcile"]
        assert seg["available"] is True
        assert seg["detail"]["found"] is False
        assert "尚未上传交割单" in seg["detail"]["note"]

    def test_reconcile_finds_the_uploaded_week(self, client, AUTH, week_env, review_env):
        client.post("/api/v1/review/upload", headers=AUTH,
                    files={"files": ("交割单.xlsx", _sample_workbook(), "application/octet-stream")})
        seg = _overview(client, AUTH, week="20260715")["reconcile"]   # 交割单落在 2026-W29
        assert seg["available"] is True and seg["detail"]["found"] is True
        assert seg["detail"]["result"]["stats"]["closedCount"] == 1

    def test_observations_segment_is_always_there(self, client, AUTH, week_env):
        seg = _overview(client, AUTH)["observations"]
        assert seg["available"] is True
        # V2.2-④:清单改成五条(plan ④-D 定死)—— `P3-33` **摘掉**(主体随门槛制作废),
        # 新增 `P3-49`(位置关前向证伪义务)与 `P3-51`(状态层第五维冷启动缺席)。
        assert {o["id"] for o in seg["items"]} == {"P3-32", "P3-34", "P3-37", "P3-49", "P3-51"}

    def test_week_without_trading_days_says_so_instead_of_crashing(self, client, AUTH, week_env):
        seg = _overview(client, AUTH, week="20200105")     # 隔离库日历覆盖不到
        assert seg["calibration"]["available"] is False
        assert seg["calibration"]["unavailableReason"]

    def test_bad_week_degrades_to_this_week_not_4xx(self, client, AUTH, week_env):
        r = client.get("/api/v1/review/overview?week=notadate", headers=AUTH)
        assert r.status_code == 200

    def test_one_broken_segment_never_takes_down_the_other_four(
            self, client, AUTH, week_env, monkeypatch):
        """段级保险丝:一段炸了只让那一段 `available=false`,其余四段照出(⛔ 不 500)。"""
        import neckline.review.store as store_mod

        def _boom(*a, **kw):
            raise RuntimeError("对账表读炸了")

        monkeypatch.setattr(store_mod, "load_weekly_review", _boom)
        body = _overview(client, AUTH)
        assert body["reconcile"]["available"] is False
        assert "未取得" in body["reconcile"]["unavailableReason"]
        assert body["observations"]["available"] is True       # 其余段不连坐


# ══════════════════════════════════════════════════════════════════════════
# V2.2-④ 双时钟端点 + 复盘板块三段
# ══════════════════════════════════════════════════════════════════════════


class TestNoOnlineRecompute:
    """🔴 plan §五⑤ 点名的机器判据:**在线端点的实现路径零调用
    `calibration.build_report`**(§七 P0-23:重活进常驻服务 = 卡死不报错)。

    ⚠ **V2.2-④ 起覆盖面从两条扩到三条**:`/eval/weekly` 随 §七 **P4-46** 结案,也
    改成「读周度 unit 落盘的产物、查不到才降级」—— 它**不再是**那条现算的反面教材。
    """

    def test_runtime_proof_endpoints_never_call_build_report(
            self, client, AUTH, week_env, monkeypatch):
        """运行期证明:研究实现已物理迁出，两个端点仍只读落盘产物。"""
        from pathlib import Path

        assert not (Path(__file__).resolve().parents[1] / "neckline" / "eval").exists()

        _write_calibration(week_env)
        assert _overview(client, AUTH)["calibration"]["available"] is True
        # `/eval/weekly` 读的是同一份落盘产物。
        w = client.get("/api/v1/eval/weekly?week=20260805", headers=AUTH)
        assert w.status_code == 200 and w.json()["available"] is True

    def test_eval_weekly_says_not_generated_instead_of_recomputing(
            self, client, AUTH, week_env):
        """§七 **P4-46 结案**的另一半:**没产物就说没产物**,⛔ 不在线补算。

        两种 `available=false` 的话必须**不一样**(会自愈 vs 要人排查)—— 合成一句
        就是叫人一直等一份永远好不了的产物。"""
        body = client.get("/api/v1/eval/weekly?week=20260805", headers=AUTH).json()
        assert body["available"] is False and "会自愈" in body["unavailableReason"]

        _write_calibration(week_env, json_text="{坏了")
        body2 = client.get("/api/v1/eval/weekly?week=20260805", headers=AUTH).json()
        assert body2["available"] is False and "读不出" in body2["unavailableReason"]
        assert body2["unavailableReason"] != body["unavailableReason"]

    def test_static_proof_the_two_endpoint_bodies_are_clean(self):
        """静态半:两个端点函数 + 它们的四个段装配函数,函数体内零 `build_report`。
        ⚠ **同一个 `app.py` 里 `get_eval_weekly` 是合法调用方**,所以这条守门必须
        **按函数**扫,⛔ 不能整文件 grep(整文件扫会把那条合法路径也判红,一个总是
        误报的守门等于没有守门 —— ⑰ 现场刚踩过同类)。"""
        import ast as _ast
        from pathlib import Path as _Path

        import neckline.api.app as app_mod

        tree = _ast.parse(_Path(app_mod.__file__).read_text(encoding="utf-8"))
        targets = {"get_review_overview", "_calibration_segment",
                   "_reconcile_segment", "_observations_segment"}
        seen = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name in targets:
                seen.add(node.name)
                names = {
                    c.func.attr if isinstance(c.func, _ast.Attribute) else getattr(c.func, "id", "")
                    for c in _ast.walk(node) if isinstance(c, _ast.Call)
                }
                assert "build_report" not in names, f"{node.name} 调了 build_report(P0-23 红线)"
        assert seen == targets, f"守门的函数清单过期了,少扫到:{sorted(targets - seen)}"
