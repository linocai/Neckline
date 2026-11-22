"""Q 期货 V2 后端全链路测试：schema 3 严格校验、0.0 与 null 区分、纯只读读入口、ETag 304 并发防覆写、
SQLite 单调缓存与坏行自愈、20:15～20:35 静默同步（零 APNs 推送）、只读 MCP 3 工具、/read/<key>/qihuo 安全与错误码。"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import html
import json
import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline import config as neckline_config
from neckline.api import deps
from neckline.api.deps import require_token
from neckline.mcp import server as mcp_server
from neckline.morning import db
from neckline.morning.api import create_router as create_morning_router
from neckline.morning.schedule import CST
from neckline.morning.web import create_router as create_read_router
from neckline.qihuo import services as qihuo_services
from neckline.qihuo import settings as qihuo_settings
from neckline.qihuo.api import create_router as create_qihuo_router
from neckline.qihuo.board import QihuoUnavailable, build_board, fetch_and_persist, reset_board_state
from neckline.qihuo.project import project_mcp_overview, project_mcp_product, project_mcp_rules, require_bundle
from neckline.qihuo.services import QihuoServices
from neckline.qihuo.source import QihuoError, QihuoFetchResult, QihuoSource
from neckline.qihuo.sync import run_sync

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
APP_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "App" / "NecklineTests" / "Fixtures"
READ_KEY = "qihuo_test_read_key_1234567890abcdef"
WEB_READ_KEY = "web_read_key_1234567890abcdef1234"
TOKEN = "b" * 16


def _at(day: str, hm: str, sec: int = 0) -> datetime:
    year, month, dom = (int(p) for p in day.split("-"))
    hour, minute = (int(p) for p in hm.split(":"))
    return datetime(year, month, dom, hour, minute, sec, tzinfo=CST)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start
        self.mono = 100.0
        self.sleeps: list[float] = []

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


class _FakeQihuo:
    def __init__(self, body: dict, *, sequence: list | None = None, etag: str = '"e-1"',
                 down: bool = False, unavailable: str | None = None) -> None:
        self.body = body
        self.sequence = list(sequence) if sequence is not None else None
        self.etag = etag
        self.down = down
        self.unavailable = unavailable
        self.calls: list[str | None] = []

    async def aclose(self) -> None:
        return None

    async def fetch(self, *, etag: str | None = None) -> QihuoFetchResult:
        self.calls.append(etag)
        if self.unavailable is not None:
            raise QihuoError("qihuo_unavailable", self.unavailable)
        if self.down:
            raise QihuoError("qihuo_unreachable", "Qihuo 暂时连不上。")
        if self.sequence:
            nxt = self.sequence.pop(0)
            if isinstance(nxt, QihuoFetchResult):
                return nxt
            if isinstance(nxt, Exception):
                raise nxt
            return QihuoFetchResult(not_modified=False, body=copy.deepcopy(nxt), etag=self.etag)
        if etag is not None and etag == self.etag:
            return QihuoFetchResult(not_modified=True, body=None, etag=self.etag)
        return QihuoFetchResult(not_modified=False, body=copy.deepcopy(self.body), etag=self.etag)


def _load_sample() -> dict:
    return json.loads((FIXTURE_DIR / "qihuo_v2_bundle_20261122.json").read_text(encoding="utf-8"))


def _services(fake: _FakeQihuo, clock: _Clock) -> QihuoServices:
    return QihuoServices(source=fake, now=lambda: clock.now, monotonic=lambda: clock.mono, sleep=clock.sleep)  # type: ignore[arg-type]


def _run(coro):
    return asyncio.run(coro)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "v4.db"
    db.init_schema(path)
    return path


def _seed(db_path: Path, doc: dict, clock: _Clock, *, etag: str = '"e-1"') -> _FakeQihuo:
    fake = _FakeQihuo(doc, etag=etag)
    svc = _services(fake, clock)
    qihuo_services.use(svc)
    _run(fetch_and_persist(svc, db_path=db_path))
    return fake


@pytest.fixture(autouse=True)
def _clean_qihuo(monkeypatch):
    monkeypatch.setenv("QIHUO_READ_KEY", READ_KEY)
    monkeypatch.delenv("QIHUO_BASE_URL", raising=False)
    reset_board_state()
    yield
    qihuo_services.use(None)
    reset_board_state()


def test_real_v2_sample_projects_all_five_products_preserving_zero_wsr_and_null_eg_spot(tmp_path):
    db_path = _db(tmp_path)
    clock = _Clock(_at("2026-11-23", "20:15"))
    _seed(db_path, _load_sample(), clock)

    board = _run(build_board(qihuo_services.current(), db_path=db_path))
    assert board["schemaVersion"] == 3
    assert board["version"] == "qihuo-20261120-r54"
    assert board["asOfDate"] == "2026-11-20"
    assert board["retentionDays"] == 15
    assert board["schedule"]["silent"] is True
    assert board["pool"]["activeCount"] == 5 and board["pool"]["pausedCount"] == 11 and board["pool"]["totalStoredCount"] == 16
    assert [p["code"] for p in board["products"]] == ["SH", "L", "MA", "pp", "eg"]

    # 烧碱 SH 仓单 0.0 必须保持 0.0 绝不当 None
    sh = board["products"][0]
    assert sh["snapshot"]["wsr"]["value"] == 0.0
    assert sh["snapshot"]["wsr"]["changeText"] == "0"
    assert sh["snapshot"]["spot"]["displayValue"] == "584.2"
    assert sh["snapshot"]["basis"]["displayValue"] == "-959.8"
    assert sh["basic"]["sections"][0]["title"] == "这是什么"
    assert sh["basic"]["sections"][0]["markdown"]

    # 乙二醇 eg 现货与基差诚实留空，且 figures.value 统一规范化为字符串
    eg = board["products"][4]
    assert eg["spotPending"] is True
    assert eg["snapshot"]["spot"] is None and eg["snapshot"]["basis"] is None
    assert eg["spotHistory"] == [] and eg["basisHistory"] == []
    assert all(isinstance(f["value"], str) for n in eg["news"] for f in n["figures"])


def test_read_endpoints_never_call_upstream_or_write_db(tmp_path, monkeypatch):
    """P1-1：App / MCP / 只读网页只读 NB 缓存，空库与手动刷新均绝不触发上游请求或写库。"""
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=TOKEN))
    db_path = _db(tmp_path)
    clock = _Clock(_at("2026-11-23", "09:30"))  # 周一盘中 09:30
    fake = _FakeQihuo(_load_sample())
    qihuo_services.use(_services(fake, clock))

    app = FastAPI()
    app.include_router(create_qihuo_router(require_token_dependency=require_token, db_path_provider=lambda: db_path))
    app.include_router(create_read_router(db_path_provider=lambda: db_path, read_key_provider=lambda: WEB_READ_KEY))
    client = TestClient(app)

    # 空缓存：App(refresh=0/1)、只读网页全部返回 503，上游 0 请求，表仍为空
    r0 = client.get("/api/v4/qihuo/board", headers={"Authorization": f"Bearer {TOKEN}"})
    r1 = client.get("/api/v4/qihuo/board?refresh=1", headers={"Authorization": f"Bearer {TOKEN}"})
    r_web = client.get(f"/read/{WEB_READ_KEY}/qihuo")
    r_web_json = client.get(f"/read/{WEB_READ_KEY}/qihuo?format=json")
    assert r0.status_code == 503 and r1.status_code == 503
    assert r_web.status_code == 503 and r_web_json.status_code == 503
    assert r_web.headers["cache-control"] == "no-store"
    assert r_web.headers["x-robots-tag"] == "noindex, nofollow"
    assert r_web.headers["referrer-policy"] == "no-referrer"
    assert fake.calls == []
    with db.read(db_path) as conn:
        assert db.get_qihuo_bundle_cache(conn) is None

    # 窗口外 09:30 直接调 run_sync（未加 force）也必须跳过，0 上游请求
    res_morning = _run(run_sync(_services(fake, clock), db_path=db_path))
    assert res_morning["status"] == "skipped" and res_morning["reason"] == "outside_window"
    assert fake.calls == []


@pytest.mark.parametrize("bad_schema", [None, 2, 4, "3", True])
def test_unknown_schema_version_is_explicitly_rejected_even_with_cached_v3(tmp_path, bad_schema):
    """P1-2：先有 v3 缓存，同步遇到不兼容协议版本时持久化错误码，读接口明确返回 503 绝不拿旧缓存掩盖。"""
    db_path = _db(tmp_path)
    clock = _Clock(_at("2026-11-23", "20:15"))
    _seed(db_path, _load_sample(), clock)

    bad_doc = _load_sample()
    if bad_schema is None:
        bad_doc.pop("schema_version", None)
    else:
        bad_doc["schema_version"] = bad_schema
    fake_bad = _FakeQihuo(bad_doc, etag='"v-bad"')
    svc_bad = _services(fake_bad, clock)
    res = _run(run_sync(svc_bad, db_path=db_path))
    assert res["status"] == "error" and res["reason"] == "qihuo_unsupported_schema"

    with pytest.raises(QihuoUnavailable) as exc_info:
        _run(build_board(svc_bad, db_path=db_path))
    assert exc_info.value.reason == "qihuo_unsupported_schema"
    assert "协议版本" in exc_info.value.message

    # P1-1：随后遇到普通网络断线，绝不解除协议不兼容阻断，仍返回 qihuo_unsupported_schema
    fake_down = _FakeQihuo(_load_sample(), down=True)
    res_down = _run(run_sync(_services(fake_down, clock), db_path=db_path, once=True))
    assert res_down["status"] == "error" and res_down["reason"] == "qihuo_unreachable"
    # 阻断期间发出的请求必须停发旧 ETag
    assert fake_down.calls == [None]
    with pytest.raises(QihuoUnavailable) as still_blocked:
        _run(build_board(_services(fake_down, clock), db_path=db_path))
    assert still_blocked.value.reason == "qihuo_unsupported_schema"

    # 迟到 304 也绝不解除协议阻断
    with db.write_immediate(db_path) as conn:
        assert db.touch_qihuo_bundle_cache(conn, expected_etag='"e-1"', etag='"e-1"', fetched_at=clock.now) is None

    # 仅在下一次合法完整 v3 同步落库后才解除阻断恢复正常
    _seed(db_path, _load_sample(), clock, etag='"v-ok"')
    recovered = _run(build_board(qihuo_services.current(), db_path=db_path))
    assert recovered["schemaVersion"] == 3 and recovered["stale"] is False


def test_corrupted_date_non_finite_values_and_corrupted_db_row_self_heal(tmp_path):
    """P1-5 + P2：非法 as_of_date / 非有限数值拒绝入库；库中损坏行读取报错且下次合法包自动自愈覆盖。"""
    db_path = _db(tmp_path)
    clock = _Clock(_at("2026-11-23", "20:15"))

    for bad_date in (20261123, "9999-99-99", "zzzz", "2026/11/23"):
        doc = _load_sample()
        doc["as_of_date"] = bad_date
        with pytest.raises(QihuoError) as exc:
            require_bundle(doc)
        assert exc.value.code == "qihuo_bad_board"

    nan_doc = _load_sample()
    nan_doc["products"][0]["snapshot"]["close"] = float("nan")
    with pytest.raises(QihuoError) as exc:
        require_bundle(nan_doc)
    assert exc.value.code == "qihuo_bad_board"

    # P2-2：仅含日期的 generated_at 或非法 started_at 必须被拒
    date_only_gen = _load_sample()
    date_only_gen["generated_at"] = "2026-11-23"
    with pytest.raises(QihuoError):
        require_bundle(date_only_gen)
    bad_started = _load_sample()
    bad_started["latest_collect"]["started_at"] = "not-a-timestamp"
    with pytest.raises(QihuoError):
        require_bundle(bad_started)

    # 精确保留 figure 浮点数精度 & 合法 skipped/none 栏状态
    precise = _load_sample()
    precise["products"][0]["news"][0]["figures"].append({
        "metric": "精度测试", "value": 12.3456789, "unit": "元", "source": "测试", "scope": "全国",
    })
    precise["products"][0]["status"]["holding"]["state"] = "skipped"
    precise["products"][0]["status"]["data"]["state"] = "none"
    _seed(db_path, precise, clock)
    board = _run(build_board(qihuo_services.current(), db_path=db_path))
    assert board["products"][0]["news"][0]["figures"][-1]["value"] == "12.3456789"
    assert board["products"][0]["status"]["holding"]["state"] == "skipped"
    assert board["products"][0]["status"]["data"]["state"] == "none"

    # 模拟库内 JSON 可解析但 generated_at 损坏（P2-2）以及 raw_json 彻底损坏：下一次合法包均自动替换自愈
    corrupt_json_doc = _load_sample()
    corrupt_json_doc["generated_at"] = "zzzz-not-time"
    with db.write_immediate(db_path) as conn:
        conn.execute(
            "UPDATE qihuo_bundle_cache SET generated_at='zzzz-not-time', raw_json=? WHERE id=1",
            (json.dumps(corrupt_json_doc, ensure_ascii=False),),
        )
    with pytest.raises(QihuoUnavailable):
        _run(build_board(qihuo_services.current(), db_path=db_path))
    _seed(db_path, _load_sample(), clock, etag='"healed-semantic"')
    assert _run(build_board(qihuo_services.current(), db_path=db_path))["version"] == "qihuo-20261120-r54"

    with db.write_immediate(db_path) as conn:
        conn.execute("UPDATE qihuo_bundle_cache SET raw_json='{broken', as_of_date='zzzz' WHERE id=1")
    with pytest.raises(QihuoUnavailable):
        _run(build_board(qihuo_services.current(), db_path=db_path))
    _seed(db_path, _load_sample(), clock, etag='"healed"')
    healed = _run(build_board(qihuo_services.current(), db_path=db_path))
    assert healed["version"] == "qihuo-20261120-r54"

    # P2-3 + Round-3 P1-1：已有旧 v10 表缺 schema_blocked_error 且留有协议错误时，init_schema 幂等补列并回填阻断，后续断网不能解除阻断
    legacy_v10 = tmp_path / "legacy_v10.db"
    db.init_schema(legacy_v10)
    with db.write_immediate(legacy_v10) as conn:
        conn.execute("DROP TABLE qihuo_bundle_cache")
        conn.execute(
            """CREATE TABLE qihuo_bundle_cache (
                 id INTEGER PRIMARY KEY CHECK(id = 1),
                 schema_version INTEGER NOT NULL,
                 bundle_version TEXT NOT NULL,
                 as_of_date TEXT,
                 generated_at TEXT NOT NULL,
                 etag TEXT,
                 raw_json TEXT NOT NULL,
                 fetched_at TEXT NOT NULL,
                 last_error TEXT,
                 last_error_reason TEXT,
                 last_error_at TEXT)"""
        )
        conn.execute(
            """INSERT INTO qihuo_bundle_cache VALUES (
                 1, 3, 'qihuo-20261120-r54', '2026-11-20', '2026-11-20T20:09:00+08:00', '"e1"', ?, ?, '收到 schema 4', 'qihuo_unsupported_schema', ?)""",
            (json.dumps(_load_sample(), ensure_ascii=False), "2026-11-23T12:15:00+00:00", "2026-11-23T12:16:00+00:00"),
        )
    db.init_schema(legacy_v10)
    with db.write_immediate(legacy_v10) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(qihuo_bundle_cache)").fetchall()}
        assert {"last_error_reason", "schema_blocked_error", "schema_blocked_at"}.issubset(cols)
        db.record_qihuo_bundle_error(conn, reason="qihuo_unreachable", error="网络抖动", at=clock.now)
    with pytest.raises(QihuoUnavailable) as legacy_blocked:
        _run(build_board(qihuo_services.current(), db_path=legacy_v10))
    assert legacy_blocked.value.reason == "qihuo_unsupported_schema"

    # Round-3 P1-2：上游返回合法 JSON 数组 [] 或标量时，统一归入协议阻断，绝不返回旧 v3
    for non_obj in ([], "ok", 123, None):
        db_arr = tmp_path / f"arr_{type(non_obj).__name__}.db"
        db.init_schema(db_arr)
        _seed(db_arr, _load_sample(), clock)
        fake_non_obj = _FakeQihuo(_load_sample(), sequence=[QihuoFetchResult(not_modified=False, body=non_obj, etag='"bad"')])
        res_non_obj = _run(run_sync(_services(fake_non_obj, clock), db_path=db_arr, once=True))
        assert res_non_obj["status"] == "error" and res_non_obj["reason"] == "qihuo_unsupported_schema"
        with pytest.raises(QihuoUnavailable) as arr_err:
            _run(build_board(_services(fake_non_obj, clock), db_path=db_arr))
        assert arr_err.value.reason == "qihuo_unsupported_schema"

    # Round-3 P2-1 + P2-4：并发迟到 v3 绝不清除较新协议阻断；小数秒 .900Z 胜过 .100Z
    db_frac = tmp_path / "frac.db"
    db.init_schema(db_frac)
    d900 = _load_sample()
    d900["generated_at"] = "2026-11-23T12:15:00.900000+00:00"
    d100 = _load_sample()
    d100["generated_at"] = "2026-11-23T12:15:00.100000+00:00"
    with db.write_immediate(db_frac) as conn:
        db.save_qihuo_bundle_cache(
            conn, schema_version=3, bundle_version=d900["version"], as_of_date=d900["as_of_date"],
            generated_at=d900["generated_at"], etag='"900"', raw=d900, fetched_at=clock.now,
        )
        winner_frac = db.save_qihuo_bundle_cache(
            conn, schema_version=3, bundle_version=d100["version"], as_of_date=d100["as_of_date"],
            generated_at=d100["generated_at"], etag='"100"', raw=d100, fetched_at=clock.now,
        )
        assert winner_frac["generatedAt"] == "2026-11-23T12:15:00.900000+00:00"
        # 较新请求写入协议阻断，此前出发的迟到 v3（observed_blocked_at=None）不得清除它
        db.record_qihuo_bundle_error(conn, reason="qihuo_unsupported_schema", error="schema 4", at=clock.now)
        late = db.save_qihuo_bundle_cache(
            conn, schema_version=3, bundle_version=d900["version"], as_of_date=d900["as_of_date"],
            generated_at=d900["generated_at"], etag='"late"', raw=d900, fetched_at=clock.now,
            observed_blocked_at=None,
        )
        assert late["schemaBlockedError"] == "schema 4"


def test_late_304_does_not_clobber_newer_etag_or_clear_newer_alert(tmp_path):
    """P2：并发请求 A 持旧 ETag 收到迟到 304 时，绝不把旧 ETag 覆写到已更新的新版缓存上，也不清掉新告警。"""
    db_path = _db(tmp_path)
    clock = _Clock(_at("2026-11-23", "20:15"))
    _seed(db_path, _load_sample(), clock, etag='"v2"')
    with db.write_immediate(db_path) as conn:
        db.record_qihuo_bundle_error(conn, reason="qihuo_sync_pending", error="新版已有提示", at=clock.now)
        res = db.touch_qihuo_bundle_cache(conn, expected_etag='"v1-stale"', etag='"v1-stale"', fetched_at=clock.now)
        assert res is not None
        assert res["etag"] == '"v2"'
        assert res["lastError"] == "新版已有提示"


def test_evening_sync_final_deadline_check_and_pending_gap(tmp_path):
    """P2：20:33 查到旧包，睡到 20:35 时仍做最后一次有界检查（能抓到 20:34 刚发布的包，仍未发布则记缺口）。"""
    db_path = _db(tmp_path)
    old_doc = _load_sample()
    new_doc = _load_sample()
    new_doc["as_of_date"] = "2026-11-23"
    new_doc["version"] = "qihuo-20261123-r55"
    new_doc["generated_at"] = "2026-11-23T20:34:00+08:00"
    new_doc["latest_collect"] = {
        "run_id": 55, "slot": "20:05", "trade_date": "2026-11-23",
        "started_at": "2026-11-23T20:05:00+08:00", "finished_at": "2026-11-23T20:34:10+08:00",
        "status": "ok", "error": None,
    }

    # 场景 A：20:33 仍是旧包，末检排在截止前 15s（20:34:45），抓到 20:34 刚发布的新包且绝不越过 20:35！
    clock_a = _Clock(_at("2026-11-23", "20:33"))
    fake_a = _FakeQihuo(new_doc, sequence=[old_doc, new_doc])
    res_a = _run(run_sync(_services(fake_a, clock_a), db_path=db_path, poll_seconds=120.0))
    assert res_a["status"] == "synced" and res_a["attempts"] == 2 and clock_a.sleeps == [105.0]

    # 场景 B：始终未出当日新包 → 20:34:45 倒数第二次与 20:34:58 末次有界检查均无新包后持久化当日缺口
    db_path_b = tmp_path / "v4_b.db"
    db.init_schema(db_path_b)
    clock_b = _Clock(_at("2026-11-23", "20:33"))
    fake_b = _FakeQihuo(old_doc, sequence=[old_doc, old_doc, old_doc])
    res_b = _run(run_sync(_services(fake_b, clock_b), db_path=db_path_b, poll_seconds=120.0))
    assert res_b["status"] == "pending" and res_b["attempts"] == 3 and clock_b.sleeps == [105.0, 13.0] and "gap" in res_b
    with db.read(db_path_b) as conn:
        row = db.get_qihuo_bundle_cache(conn)
        assert row["lastErrorReason"] == "qihuo_sync_pending" and "尚未检测到今日" in row["lastError"] and "20:34:58" in row["lastError"]
        assert conn.execute("SELECT count(*) FROM kx_push_log").fetchone()[0] == 0

    # Round-4 P2-5：非 force 模式传 deadline=20:40 在 20:36 仍必须跳过（不得放宽硬截止 20:35）
    clock_c = _Clock(_at("2026-11-23", "20:36"))
    fake_c = _FakeQihuo(new_doc)
    res_c = _run(run_sync(_services(fake_c, clock_c), db_path=db_path_b, deadline_clock=time(20, 40, tzinfo=CST)))
    assert res_c["status"] == "skipped" and res_c["reason"] == "outside_window" and fake_c.calls == []


def test_mcp_and_read_page_security_status_codes_and_nullable_holding(tmp_path, monkeypatch):
    """P1-3 + P2：安全外链过滤、404/503 状态码与隐私头、空持仓字段不 500、.env 白名单与配置校验。"""
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=TOKEN))
    db_path = _db(tmp_path)
    clock = _Clock(_at("2026-11-23", "20:15"))
    sample = _load_sample()
    # 注入不安全新闻链接（含带口令 URL）、档案 Markdown 带码/口令/引用式链接与合法空持仓字段
    sample["products"][0]["news"][0]["url"] = "javascript:alert(document.location)"
    sample["products"][0]["news"][1]["url"] = f"https://nk.linotsai.top/read/{WEB_READ_KEY}/qihuo"
    sample["products"][0]["news"][2]["url"] = "https://alice:topsecret123@example.com/private"
    sample["products"][0]["basic"]["sections"][0]["markdown"] += (
        f"\n参考 [内部页](https://nk.linotsai.top/read/{WEB_READ_KEY}/qihuo) 与 [脚本](javascript:alert(1))"
        f" 与 [带口令](https://bob:pw999@example.com/doc)\r\n"
        f"裸带引号口令 https://user:pa'ss88@example.com/leak 结束\r\n"
        f"相邻地址 https://safe.example.com/ok，https://alice:adj'pw77@evil.example.com/leak，https://中文用户:cn_pw66@evil.example.com/cn，https://ＡＢ:fw_pw55@evil.example.com/fw，https://ｱｲ:hw_pw44@evil.example.com/hw，https://u)ser:paren_pw33@evil.example.com/p\r\n"
        f"逗号相邻 https://a.example.com/1,[安全词条](https://en.wikipedia.org/wiki/Soda_(chemistry))\r\n"
        f"显示文字带口令 [https://lbl:lbl_pw22@evil.example.com/x](https://safe.example.com/dest)\r\n"
        f"[安全引用]: https://safe.example.com/ref \"https://ttl:ttl_pw11@evil.example.com/y\"\r\n"
        f"转义右括号 [转义测试](https://a\\)b:esc_pw01@evil.example.com/e)\r\n"
        f"内嵌查询 [跳板测试](https://safe.example.com/go?next=https://u:nest_pw02@evil.example.com/x)\r\n"
        f"部分编码 [半编码跳板](https://safe.example.com/go?next=https:%2F%2Fu:half_pw03@evil.example.com/x)\r\n"
        f"深层转引 [深链](https://a.example.com/1?u=https://b.example.com/2?u=https://c.example.com/3?u=https://u:deep_pw04@evil.example.com/x)\r\n"
        f"单引号路径 [奥布莱恩](https://safe.example.com/O'Brien)\r\n"
        f"未闭合同行 [草稿](未闭合 [同行邮件](mailto:after_draft@example.com)\r\n"
        f"含反斜杠目标 [反斜杠](https://safe.example.com/a\\_b)\r\n"
        f"裸转引 https://jump.example.com/?next=https://target.example.com/redir 结束\r\n"
        f"裸标点隔段 https://a.invalid/?q=x，说明，https://b.invalid/ 与 https://a.invalid/path，%2fread%2fsecret 结束\r\n"
        f"裸排版撇号 https://user:typo’pw99@evil.example.com/leak 与 https://user:cn，pw98@evil.example.com/leak 结束\r\n"
        f"实体冒号 [实体冒号](https://safe.example.com/A&colon;B) 与 [实体脚本](javascript&colon;alert(1)) 与 [实体跳板](https://safe.example.com/go?u=https&colon;//evil.example.com/x) 与 [实体读码](https://nk.linotsai.top&sol;read&sol;secret)\r\n"
        f"跨行链接 [跨行安全](\r\n  https://safe.example.com/M&colon;N\r\n) 与 [跨行脚本](\r\n  javascript&colon;alert(2)\r\n)\r\n"
        f"嵌套外层 [[内层脚本](javascript:alert(1))](https://safe.example.com/outer1) 与 [[内层安全](https://inner.example.com/1)](https://outer.example.com/2) 与 [含自动链接 <https://inner-auto.example.com/1>](https://outer-auto.example.com/2) 与 <javascript:alert(9)> <mailto:auto@example.com> <email_auto@example.com>\r\n"
        f"[实体引用]: javascript&#x3a;alert(1)\r\n"
        f"嵌套方括号 [说明 [子标签]](mailto:nested@example.com)\r\n"
        f"[邮件引用]: mailto:leak@example.com\r\n"
        f"[跨行引用]:\r\n  mailto:crlf@example.com\r\n"
    )
    sample["products"][0]["snapshot"]["holding"]["long_chg"] = None
    sample["products"][0]["snapshot"]["holding"]["short_chg"] = None
    sample["products"][0]["hold_rows"][0]["long_chg"] = None
    sample["products"][0]["hold_rows"][0]["long_ratio_pct"] = None
    _seed(db_path, sample, clock)

    # MCP 投影
    rules = project_mcp_rules(sample)
    assert rules["status"] == "ok" and "合成测试夹具" in rules["rules"]
    assert "搜索" in rules["researchNote"] and "不要局限" in rules["researchNote"]
    ov = project_mcp_overview(sample)
    assert len(ov["products"]) == 5 and ov["pool"]["pausedCount"] == 11
    assert project_mcp_product(sample, "烧碱")["product"]["code"] == "SH"
    assert project_mcp_product(sample, "EG")["product"]["spotPending"] is True
    assert project_mcp_product(sample, "纯碱")["inPausedPool"] is True
    assert project_mcp_product(sample, "黄金")["status"] == "not_active"

    app = FastAPI()
    app.include_router(create_morning_router(
        require_token_dependency=require_token, db_path_provider=lambda: db_path,
        version="v4.6.0", release_set="v4.6.0-b135",
    ))
    app.include_router(create_qihuo_router(require_token_dependency=require_token, db_path_provider=lambda: db_path))
    app.include_router(create_read_router(db_path_provider=lambda: db_path, read_key_provider=lambda: WEB_READ_KEY))
    client = TestClient(app)

    # 错码 404 必须带三项隐私响应头
    wrong = client.get("/read/wrong-key/qihuo")
    assert wrong.status_code == 404
    assert wrong.headers["cache-control"] == "no-store"
    assert wrong.headers["x-robots-tag"] == "noindex, nofollow"
    assert wrong.headers["referrer-policy"] == "no-referrer"

    # 总览页与单品种页：空持仓字段正常显示不 500，javascript: 与 /read/ 链接在 HTML、App API、MCP 与网页 JSON 全部出口均被剥离
    html_ov = client.get(f"/read/{WEB_READ_KEY}/qihuo")
    assert html_ov.status_code == 200 and "烧碱" in html_ov.text and "自由补充" in html_ov.text
    html_sh = client.get(f"/read/{WEB_READ_KEY}/qihuo/SH")
    assert html_sh.status_code == 200
    assert "javascript:" not in html_sh.text
    assert 'href="https://nk.linotsai.top/read/' not in html_sh.text

    # P1-4 + Round-3 P1-3：四出口（App JSON、MCP 投影、网页 ?format=json、网页 HTML <pre>）均不含不安全或带码 URL，且保留 Markdown 链接标题
    app_board = client.get("/api/v4/qihuo/board", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    web_sh_json = client.get(f"/read/{WEB_READ_KEY}/qihuo/SH?format=json").json()
    mcp_sh = project_mcp_product(sample, "SH")
    decoded_html_sh = html.unescape(html_sh.text)
    for payload_str in (
        json.dumps(app_board, ensure_ascii=False),
        json.dumps(web_sh_json, ensure_ascii=False),
        json.dumps(mcp_sh, ensure_ascii=False),
        decoded_html_sh,
    ):
        assert "javascript:" not in payload_str
        assert "topsecret123" not in payload_str
        assert "pw999" not in payload_str
        assert "pa'ss88" not in payload_str
        assert "adj'pw77" not in payload_str
        assert "cn_pw66" not in payload_str
        assert "fw_pw55" not in payload_str
        assert "hw_pw44" not in payload_str
        assert "paren_pw33" not in payload_str
        assert "lbl_pw22" not in payload_str
        assert "ttl_pw11" not in payload_str
        assert "esc_pw01" not in payload_str
        assert "nest_pw02" not in payload_str
        assert "half_pw03" not in payload_str
        assert "deep_pw04" not in payload_str
        assert "https://safe.example.com/O'Brien" in payload_str
        assert "after_draft@example.com" not in payload_str
        assert "同行邮件" in payload_str
        assert "jump.example.com" not in payload_str and "target.example.com" not in payload_str
        assert "a.invalid" not in payload_str and "b.invalid" not in payload_str
        assert "pw99" not in payload_str and "pw98" not in payload_str
        assert "https://safe.example.com/A:B" in payload_str and "A&colon;B" not in payload_str
        assert "https://safe.example.com/M:N" in payload_str and "M&colon;N" not in payload_str
        assert "javascript&colon;" not in payload_str and "javascript&#x3a;" not in payload_str
        assert "auto@example.com" not in payload_str and "email_auto@example.com" not in payload_str
        assert "https://safe.example.com/outer1" in payload_str
        assert "https://outer.example.com/2" in payload_str and "inner.example.com" not in payload_str
        assert "https://outer-auto.example.com/2" in payload_str and "inner-auto.example.com" not in payload_str
        assert "nested@example.com" not in payload_str
        assert "转义测试" in payload_str and "跳板测试" in payload_str
        assert "https://safe.example.com/ref \"" not in payload_str
        assert "https://a.example.com/1" in payload_str
        assert "https://en.wikipedia.org/wiki/Soda_(chemistry)" in payload_str
        assert "mailto:" not in payload_str
        assert "crlf@example.com" not in payload_str
        assert "https://safe.example.com/ok" in payload_str
        assert "内部页" in payload_str and "脚本" in payload_str and "带口令" in payload_str
    for machine_str in (
        json.dumps(app_board, ensure_ascii=False),
        json.dumps(web_sh_json, ensure_ascii=False),
        json.dumps(mcp_sh, ensure_ascii=False),
    ):
        assert WEB_READ_KEY not in machine_str
        assert "/read/" not in machine_str
    assert "https://nk.linotsai.top/read/" not in decoded_html_sh
    assert app_board["products"][0]["news"][0]["url"] is None
    assert app_board["products"][0]["news"][1]["url"] is None
    assert app_board["products"][0]["news"][2]["url"] is None

    # P2-6：正确访问码但后端数据库未配置时，Q 路由 HTML/JSON 均返回 503 + 隐私头
    unconf_app = FastAPI()
    unconf_app.include_router(create_read_router(db_path_provider=lambda: None, read_key_provider=lambda: WEB_READ_KEY))
    unconf_client = TestClient(unconf_app)
    for ep in (f"/read/{WEB_READ_KEY}/qihuo", f"/read/{WEB_READ_KEY}/qihuo?format=json",
               f"/read/{WEB_READ_KEY}/qihuo/SH", f"/read/{WEB_READ_KEY}/qihuo/SH?format=json"):
        resp = unconf_client.get(ep)
        assert resp.status_code == 503 and resp.headers["cache-control"] == "no-store"

    # 未知品种 HTML 与 JSON 均返回 404 + 隐私头
    unk_html = client.get(f"/read/{WEB_READ_KEY}/qihuo/UNKNOWN")
    unk_json = client.get(f"/read/{WEB_READ_KEY}/qihuo/UNKNOWN?format=json")
    assert unk_html.status_code == 404 and unk_json.status_code == 404
    assert unk_html.headers["cache-control"] == "no-store" and unk_json.headers["cache-control"] == "no-store"

    # .env 白名单与畸形 URL 校验
    assert "QIHUO_BASE_URL" in neckline_config.KNOWN_ENV_KEYS
    assert "QIHUO_READ_KEY" in neckline_config.KNOWN_ENV_KEYS
    monkeypatch.setenv("QIHUO_BASE_URL", "https://bad.host:99999")
    assert qihuo_settings.is_configured() is False
    assert client.get("/api/v4/health").json()["qihuoBoard"] is False
    with pytest.raises(QihuoError) as cfg_err:
        _run(QihuoSource().fetch())
    assert cfg_err.value.code == "qihuo_config_invalid"


def test_cross_end_qihuo_fixtures_match_between_backend_and_app():
    names = (
        "qihuo_board_v3.json",
        "qihuo_board_v3_warn.json",
        "qihuo_board_v3_stale.json",
        "qihuo_board_unavailable.json",
    )
    for name in names:
        b = FIXTURE_DIR / name
        a = APP_FIXTURE_DIR / name
        assert b.exists() and a.exists()
        assert json.loads(b.read_text(encoding="utf-8")) == json.loads(a.read_text(encoding="utf-8"))


def test_sanitize_markdown_unbalanced_angle_quote_and_linear_perf():
    import time
    from neckline.qihuo.project import sanitize_markdown

    assert sanitize_markdown("[click](<javascript:alert(1>)") == "click"
    assert sanitize_markdown('[click](javascript:alert(1) "unbalanced ( title")') == "click"
    assert sanitize_markdown("[safe](<https://safe.example.com/a(b> \"title (x)\")") == (
        '[safe](<https://safe.example.com/a(b> "title (x)")'
    )
    # CommonMark / cmark-gfm 字符引用归一化与防二次解码
    assert sanitize_markdown("[ok](https://safe.example.com/A&colon;B)") == "[ok](https://safe.example.com/A:B)"
    assert sanitize_markdown(r"[lit](https://safe.example.com/A\&colon;B)") == "[lit](https://safe.example.com/A:B)"
    assert sanitize_markdown("[amp](https://safe.example.com/A&amp;colon;B)") == "[amp](https://safe.example.com/A&amp;colon;B)"
    assert sanitize_markdown("[js](javascript&#58;alert(1))") == "js"
    assert sanitize_markdown(r"[js2](javascript\&colon;alert(1))") == "js2"
    # 嵌套链接：保留外层安全链接时剥离内层链接；外层不安全时保留内层安全链接
    assert sanitize_markdown("[[inner](javascript:alert(1))](https://safe.example.com/o)") == "[inner](https://safe.example.com/o)"
    assert sanitize_markdown("[[inner](https://inner.example.com/a)](https://outer.example.com/b)") == "[inner](https://outer.example.com/b)"
    assert sanitize_markdown("[[inner](https://inner.example.com/a)](javascript:alert(1))") == "[inner](https://inner.example.com/a)"
    assert sanitize_markdown("[说明 [子标签]](https://safe.example.com/ok)") == r"[说明 \[子标签\]](https://safe.example.com/ok)"
    # 合法跨行链接 + 邮箱自动链接 + 外层标签内自动链接
    assert sanitize_markdown("[跨行](\n  https://safe.example.com/A&colon;B\n)") == "[跨行](https://safe.example.com/A:B)"
    assert sanitize_markdown("[跨行坏](\n  javascript&colon;alert(1)\n)") == "跨行坏"
    assert sanitize_markdown("<user@example.com>") == ""
    assert sanitize_markdown("[外层 <https://inner.example.com/1>](https://outer.example.com/2)") == "[外层 ](https://outer.example.com/2)"
    # 线性时间回归 1：海量未闭合 [a](
    adversarial = "[a](" * 5000 + "[tail](mailto:lin@example.com)"
    cleaned = sanitize_markdown(adversarial)
    assert "mailto:" not in cleaned and cleaned.endswith("tail")
    # 线性时间回归 2：多行未闭合 `[ref`、深层未闭合长标签、以及深层「全部解析成功」的嵌套链接
    t0 = time.perf_counter()
    multiline_unclosed = "[unclosed_ref_line\n" * 6000 + "[end](mailto:m@example.com)"
    out_multi = sanitize_markdown(multiline_unclosed)
    dt_multi = time.perf_counter() - t0
    assert "mailto:" not in out_multi and out_multi.endswith("end")
    assert dt_multi < 2.0

    t1 = time.perf_counter()
    nested_long = "[" * 3000 + ("a" * 3000) + "](" * 3000
    out_nested = sanitize_markdown(nested_long)
    dt_nested = time.perf_counter() - t1
    assert len(out_nested) == len(nested_long)
    assert dt_nested < 2.0

    t2 = time.perf_counter()
    nested_valid = ("[x " * 3000) + "leaf" + ("](https://safe.example.com/ok)" * 3000)
    out_valid = sanitize_markdown(nested_valid)
    dt_valid = time.perf_counter() - t2
    assert out_valid.startswith("[") and out_valid.endswith("](https://safe.example.com/ok)")
    assert out_valid.count("https://safe.example.com/ok") == 1
    assert dt_valid < 2.0
