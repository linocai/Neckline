"""连续止损**纯提醒** + 熔断整体退役的**反向守门**(V2.2-⑤-B,用户裁定 #8)。

🔴 **裁定 #8 原话**:「**我不需要你替我做决定;这个程序永远是提醒 —— 连续三笔止损真的
发生了,那也是提醒**」。

本文件把 §五 〇b-7 那条铁律钉成**机器判据**:

    ⛔ 不许「为了安全」偷偷留一个锁定标志、一个灰化按钮、或一个「建议今天别开仓」的
       自动状态位。留下的只有一条事件与一条推送。

三类断言:
  ① **防复活**:被删的符号 / 常量 / 端点 / 字段一个都不许回来(`hasattr` + AST/文本扫描)。
  ② **停写留档**:`circuit_breaker` 表在 `neckline/` + `scripts/` 全域零写入调用点,
     跑完一整轮"三笔止损 + 提醒"后**零新增行**。
  ③ **纯提醒语义**:达阈值 → 一条推送 + 一条看板事件 + **零状态**;第 4 笔**再推一条**;
     `POST /positions/{id}/close` 的返回值逐字段不变(在 `test_api_circuit.py`)。

⚠ **反向锁一条**:§2.1 **第 4 条**「单周亏损 ≥ 总仓 2% → 强制复盘」**不是熔断**,
`FORCED_REVIEW_LOSS_FRAC` 必须仍在、周复盘仍判 —— ⛔ 别连坐删掉。
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from neckline import positions_entry
from neckline.db import connection
from neckline.sentinel import circuit
from neckline.sentinel.positions import (
    CLOSE_REASON_MANUAL,
    CLOSE_REASON_STOP_LOSS,
    close_position,
    open_position,
)

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = (_ROOT / "neckline", _ROOT / "scripts")
_SCAN_FILES = sorted(p for d in _SCAN_DIRS for p in d.rglob("*.py"))
# ✅ **V2.2-⑥ 起扫描域扩到 `client/`**(⑤-B 施工时留的那句「⑥ 完工后应把扫描域扩到
# client/」已兑现):熔断的客户端半边(横幅 / 开仓灰化 / 解锁弹层 / 两条活调用)已删,
# 反向守门必须跟着覆盖那半边 —— 否则「服务端删干净了、客户端又长回来」这条路没人看。
_CLIENT_FILES = sorted((_ROOT / "client").rglob("*.swift"))
_EXEC_METHODS = {"execute", "executemany", "executescript"}


# ======================================================================
#  ① 防复活:三件机制的符号一个都不许回来
# ======================================================================

# 锁定态 / 解锁 / 幂等评估 / 派生状态 / 单日亏损档 —— §五 ⑤-B「测试与守门(熔断面)」逐条。
_FORBIDDEN_CIRCUIT_ATTRS = (
    "get_state", "is_locked", "unlock", "evaluate_after_close", "auto_unlock_for_reviews",
    "current_locked_episode", "list_episodes", "get_episode", "detect_trigger",
    "CircuitEpisode", "CircuitState",
    "CIRCUIT_DAILY_LOSS_YUAN", "TRIGGER_DAILY_LOSS", "TRIGGER_CONSECUTIVE_STOPS",
    "UNLOCK_VIA_REVIEW_ACK", "UNLOCK_VIA_WEEKLY_REVIEW",
)


@pytest.mark.parametrize("attr", _FORBIDDEN_CIRCUIT_ATTRS)
def test_circuit_module_has_no_lock_machinery(attr):
    """锁定 / 解锁 / 幂等 / 派生状态整套**已删且不许回来**(裁定 #8 的字面结果)。"""
    assert not hasattr(circuit, attr), (
        f"`sentinel/circuit.py` 又出现了 `{attr}` —— 熔断三件机制已于 V2.2-⑤-B 整体退役"
        f"(用户裁定 #8),⛔ 不许以任何形式复活锁定态 / 次日只减不加 / 强制复盘解锁。"
    )


def test_circuit_public_surface_is_exactly_two_names():
    """本模块公开面**恰好两项**:提醒阈值常量 + 一个无状态纯函数。多一项就该问为什么。"""
    assert set(circuit.__all__) == {"CIRCUIT_CONSECUTIVE_STOPS", "count_tail_consecutive_stops"}
    assert circuit.CIRCUIT_CONSECUTIVE_STOPS == 3


def _text_hits(needle: str, files=None) -> List[Tuple[str, int]]:
    """整行注释剥掉再判。**Python 用 `#`、Swift 用 `//`** —— 两种都要认,否则扩到
    `client/` 之后那些「熔断已删」的留痕注释会让本闸每次都红。"""
    hits: List[Tuple[str, int]] = []
    for path in (files if files is not None else _SCAN_FILES):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//") or needle not in line:
                continue
            hits.append((str(path.relative_to(_ROOT)), i))
    return hits


@pytest.mark.parametrize("needle", ["circuit_locked", "EVENT_CIRCUIT_LOCKED", "circuitLocked"])
def test_no_circuit_locked_state_anywhere_in_server(needle):
    """全服务端(`neckline/` + `scripts/`)**零锁定态字段**。

    ⚠ **注释行剥掉再判**(承 CLAUDE.md「一个对自己的注释报警的闸门等于没有闸门」):本次
    退役在多处留了「`circuit_locked` 已删」这类留痕注释,裸 grep 每次都红。"""
    hits = _text_hits(needle)
    assert not hits, (
        f"服务端仍有 `{needle}` 的活代码(非注释):{hits} —— 熔断锁定态已整体退役,"
        f"⛔ 不许留任何自动状态位(§五 〇b-7)。"
    )


# ✅ V2.2-⑥ 客户端半边的反向守门(⑤-B「客户端连带(归 ⑥ 一起做)」的机器判据)。
#
# 🔴 **两组针脚分开写**,因为它们防的是两件不同的事:
#   ① `model.circuit` / `circuitReview` / 两条活调用 —— 「机制没删干净」;
#   ② `CircuitState` / `CircuitEpisode` —— **v2.3.0 起也进禁用清单**(两步淘汰第二步
#      已完成:服务端 `PositionsOut.circuit` 删键 + 客户端删 DTO,**同一版**落地)。
#      ⚠ V2.2 时它们**刻意保留**过一版,理由是「老客户端装着不换包也解得出」;v2.3.0
#      逐版核实后确认那条前提在这个键上并不存在 —— 历代客户端 `/positions` 一律解进
#      `PositionsListResponse { holdings }`,**从没有一版声明过 `circuit`**(2.0.0 那台
#      iPhone 读的是独立端点 `GET /circuit`,自 V2.2 起 404,与本键无关)。
_FORBIDDEN_IN_CLIENT = (
    "model.circuit",          # 状态位(横幅 / 灰化都读它)
    "circuitReview",          # 强制复盘解锁弹层 + 它的 modal case
    "CircuitLockBanner",      # 锁定横幅
    "CircuitReviewSheet",     # 解锁弹层
    "getCircuit",             # GET /circuit 活调用
    "unlockCircuit",          # POST /circuit/unlock 活调用
    "confirmCircuitReview",   # 解锁动作
    "/api/v1/circuit",        # 路径字面量(含 /unlock)
    "CircuitState",           # v2.3.0 删:DTO 本体(两步淘汰第二步)
    "CircuitEpisode",         # v2.3.0 删:DTO 本体(两步淘汰第二步)
)


@pytest.mark.parametrize("needle", _FORBIDDEN_IN_CLIENT)
def test_circuit_machinery_is_gone_from_the_client_too(needle):
    """🔴 客户端**零熔断机制**(V2.2-⑥ 收口):横幅 / 开仓灰化 / 解锁弹层 / 两条活调用
    全删。⛔ **不许以任何形式接回来** —— §五 〇b-7:不许留锁定标志、灰化按钮、或
    「建议今天别开仓」的自动状态位。用户裁定 #8:「我不需要你替我做决定」。"""
    hits = _text_hits(needle, files=_CLIENT_FILES)
    assert not hits, (
        f"客户端仍有 `{needle}` 的活代码(非注释):{hits} —— 熔断三件机制已整体退役。"
    )


def test_circuit_dtos_are_gone_from_the_client_and_the_key_from_the_contract():
    """🔴 **两步淘汰第二步的机器判据**(v2.3.0):客户端两个 DTO 删干净 **且** 服务端
    `PositionsOut` 不再声明 `circuit` 键 —— 两件事必须**同一版**落地,只做一半就会出现
    「服务端还发着一个谁都不解的键」或「客户端解一个永远不来的键」。

    ⚠ 这条**方向与 V2.2 那一版相反**:当时守的是「两个 DTO 必须还在」(零删键铁律
    〇b-3,怕老客户端解不出)。v2.3.0 逐版核实后确认:历代客户端 `/positions` 一律解进
    `PositionsListResponse {holdings}`,**没有任何一版声明过 `circuit` 字段**,那条顾虑
    在这个键上不成立。⛔ 别把它读成「零删键铁律可以不守」—— 铁律守住了,只是核实之后
    发现这个键根本没有消费方。"""
    models = (_ROOT / "client" / "Models.swift").read_text(encoding="utf-8")
    assert "struct CircuitState" not in models
    assert "struct CircuitEpisode" not in models
    schemas = (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8")
    assert "class CircuitStateOut" not in schemas
    assert "class CircuitEpisodeOut" not in schemas
    # `PositionsOut` 里不再有 circuit 字段声明(注释里提它是必要留痕,故只切该类的块)。
    body = schemas.split("class PositionsOut(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    assert "circuit" not in body, f"`PositionsOut` 仍声明了 circuit:{body!r}"


def test_forced_review_line_is_not_collaterally_deleted():
    """🔴 **反向锁**:§2.1 **第 4 条**「单周亏损 ≥ 总仓 2% → 强制复盘」**不是熔断**,
    ⛔ 别连坐删掉(§五 ⑤-B 测试与守门里明文点名的那一条)。"""
    from neckline.review import reconcile

    assert reconcile.FORCED_REVIEW_LOSS_FRAC == 0.02
    assert callable(reconcile.is_forced_review)


def test_notify_entrypoint_renamed_and_old_one_gone():
    """⑤-B 第 8 项:`push_circuit_breaker` → `push_consecutive_stops_notice`。"""
    from neckline.api import notify

    assert not hasattr(notify, "push_circuit_breaker")
    assert "push_circuit_breaker" not in notify.__all__
    assert "push_consecutive_stops_notice" in notify.__all__


def test_precall_summary_no_longer_takes_circuit_locked():
    """⑤-B 第 8 项:`push_precall_summary` 的 `circuit_locked` 参数已删 ——
    连带「锁定期 9:26 汇总必发」豁免一并取消(§八 第 19 项已当面告知用户)。"""
    import inspect

    from neckline.api import notify
    from neckline.sentinel.precall import PrecallResult

    assert "circuit_locked" not in inspect.signature(notify.push_precall_summary).parameters
    assert not hasattr(PrecallResult(trade_date=date(2026, 8, 10), now=None), "circuit_locked")


def test_should_push_summary_has_no_must_push_exemption():
    """必发豁免真的没了:零 actionable 判定 → **不推**,没有任何第二个析取项能翻盘。"""
    from neckline.sentinel.precall import PrecallResult

    r = PrecallResult(trade_date=date(2026, 8, 10), now=None)
    assert r.summary_actionable == 0 and r.should_push_summary is False


# ======================================================================
#  ② 停写留档:`circuit_breaker` 表零写入调用点(照 test_v1_retirement_guard 体例)
# ======================================================================

def _sql_literal(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else ""
                       for v in node.values)
    return None


def test_circuit_breaker_table_has_zero_write_call_sites():
    """`circuit_breaker` **停写留档不 DROP**(⑤-B 第 3 项;§七 P4-31 七张 → 八张)。
    写法变体成套(承契约线审计 🟡 Y1 第 2 洞:`INSERT OR REPLACE/IGNORE` 也算写)。"""
    table = "circuit_breaker"
    forbidden = (
        f"INSERT INTO {table}", f"UPDATE {table}", f"DELETE FROM {table}",
        f"REPLACE INTO {table}", f"INSERT OR IGNORE INTO {table}",
        f"INSERT OR ABORT INTO {table}", f"INSERT OR FAIL INTO {table}",
        f"INSERT OR ROLLBACK INTO {table}",
    )
    hits: List[Tuple[str, int, str]] = []
    for path in _SCAN_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if name not in _EXEC_METHODS or not node.args:
                continue
            sql = _sql_literal(node.args[0])
            if sql is None:
                continue
            upper = " ".join(sql.upper().split())
            for f in forbidden:
                if f.upper() in upper:
                    hits.append((str(path.relative_to(_ROOT)), node.lineno, f))
    assert not hits, f"`circuit_breaker` 表出现禁止的写入调用点(V2.2-⑤-B 起停写留档):{hits}"


def test_circuit_breaker_table_exists_in_isolated_db(isolated_env):
    """**停写留档 ≠ DROP**(§七 P4-31 纪律):表必须还在,历史行可查。
    ⚠ 显式传 `db_path=isolated_env.db_path`(v1.4-④ 测试隔离纪律:`neckline/db.py` 那份
    `settings` 不被夹具重写,`db_path=None` 会静默写到真实 `data/neckline.db`)。"""
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    with connection(isolated_env.db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='circuit_breaker'"
        ).fetchone()
    assert row is not None, "`circuit_breaker` 表被 DROP 了 —— 停写留档纪律是「不 DROP」"


# ======================================================================
#  ③ 纯提醒语义:尾部连续止损计数 + 达阈值只推不建行
# ======================================================================

_BUY = date(2026, 7, 20)


def _stop_close(env, code: str, *, reason=CLOSE_REASON_STOP_LOSS, sell=9.0,
                sell_date=date(2026, 7, 22)) -> int:
    """开一笔 10.0 的仓,再以 `sell` 平掉(默认 9.0 = 破 -5% 线)。返回 position_id。"""
    pid = open_position(code, 10.0, 100, _BUY, db_path=env.db_path)
    close_position(pid, sell, sell_date, close_reason=reason, db_path=env.db_path)
    return pid


class TestCountTailConsecutiveStops:
    def test_three_explicit_stop_losses_count_three(self, isolated_env):
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            _stop_close(isolated_env, code, sell_date=date(2026, 7, 20 + i))
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 3

    def test_null_reason_price_fallback_counts_as_stop(self, isolated_env):
        """`close_reason` NULL → 价格近似兜底(sell ≤ buy×(1−stop_pct))计止损。"""
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            _stop_close(isolated_env, code, reason=None, sell_date=date(2026, 7, 20 + i))
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 3

    def test_explicit_non_stop_reason_not_second_guessed(self, isolated_env):
        """显式非 STOP_LOSS 码 → **信标注、不用价格二次猜**(哪怕价格深亏)。"""
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            _stop_close(isolated_env, code, reason=CLOSE_REASON_MANUAL, sell_date=date(2026, 7, 20 + i))
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 0

    def test_non_stop_at_tail_breaks_chain(self, isolated_env):
        _stop_close(isolated_env, "000001.SZ", sell_date=date(2026, 7, 20))
        _stop_close(isolated_env, "000002.SZ", sell_date=date(2026, 7, 21))
        _stop_close(isolated_env, "000003.SZ", reason=CLOSE_REASON_MANUAL, sell=11.0,
                    sell_date=date(2026, 7, 22))
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 0

    def test_chain_has_no_time_window(self, isolated_env):
        """链只看「尾部连续」、不看间隔 —— 横跨数月的 3 笔同样计 3(既有口径,刻意保留)。"""
        for i, (code, d) in enumerate([("000001.SZ", date(2026, 3, 2)),
                                       ("000002.SZ", date(2026, 5, 6)),
                                       ("000003.SZ", date(2026, 7, 22))]):
            _stop_close(isolated_env, code, sell_date=d)
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 3

    def test_fourth_stop_makes_it_four(self, isolated_env):
        """⛔ **没有"重置"概念了**:第 4 笔止损 → 计数 4(> 阈值),调用方据此再提醒一条。"""
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]):
            _stop_close(isolated_env, code, sell_date=date(2026, 7, 20 + i))
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 4

    def test_stop_pct_read_from_active_config(self, isolated_env):
        """阈值读现役 config(§3.8 单一源),不硬编 -5%:stop_pct=0.08 时 -6% 不算止损。"""
        from neckline.strategy import brain

        brain.save_version("test-stop8", rule={"config": {"stop_pct": 0.08}},
                           changelog="单测", activate=True, db_path=isolated_env.db_path)
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            _stop_close(isolated_env, code, reason=None, sell=9.4, sell_date=date(2026, 7, 20 + i))
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 0

    def test_empty_ledger_is_zero(self, isolated_env):
        assert circuit.count_tail_consecutive_stops(db_path=isolated_env.db_path) == 0


class TestPureReminderHasZeroState:
    """达阈值时:**一条推送 + 一条看板事件 + 零建行 + 零状态**。"""

    @staticmethod
    def _pushes(monkeypatch) -> List[dict]:
        sent: List[dict] = []
        from neckline.api import notify

        def _fake(count, *, ts_code="", name="", db_path=None, transport=None):
            sent.append({"count": count, "ts_code": ts_code})
            return notify.NotifyOutcome(skipped_reason="test")

        monkeypatch.setattr(notify, "push_consecutive_stops_notice", _fake)
        return sent

    def test_three_stops_push_once_and_create_zero_rows(self, isolated_env, monkeypatch):
        sent = self._pushes(monkeypatch)
        pids = []
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            pid = _stop_close(isolated_env, code, sell_date=date(2026, 7, 20 + i))
            pids.append(pid)
            positions_entry.notice_consecutive_stops_after_close(
                pid, sell_date=date(2026, 7, 20 + i), db_path=isolated_env.db_path)
        # 只有第 3 笔越过阈值 → 恰好一条推送
        assert [s["count"] for s in sent] == [3]
        # 零建行:`circuit_breaker` 表一行都没多
        with connection(isolated_env.db_path) as conn:
            assert conn.execute("SELECT count(*) FROM circuit_breaker").fetchone()[0] == 0
        # 一条看板事件(sentinel='circuit'),锚在那笔卖出的票上
        with connection(isolated_env.db_path) as conn:
            rows = conn.execute(
                "SELECT ts_code, event_key FROM sentinel_events WHERE sentinel='circuit'"
            ).fetchall()
        assert rows == [("000003.SZ", positions_entry.CONSECUTIVE_STOPS_EVENT_KEY)]

    def test_fourth_stop_pushes_again(self, isolated_env, monkeypatch):
        """⛔ 别发明"解锁后才重推":没有锁,第 4 笔照样再来一条。"""
        sent = self._pushes(monkeypatch)
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]):
            pid = _stop_close(isolated_env, code, sell_date=date(2026, 7, 20 + i))
            positions_entry.notice_consecutive_stops_after_close(
                pid, sell_date=date(2026, 7, 20 + i), db_path=isolated_env.db_path)
        assert [s["count"] for s in sent] == [3, 4]

    def test_below_threshold_pushes_nothing(self, isolated_env, monkeypatch):
        sent = self._pushes(monkeypatch)
        for i, code in enumerate(["000001.SZ", "000002.SZ"]):
            pid = _stop_close(isolated_env, code, sell_date=date(2026, 7, 20 + i))
            positions_entry.notice_consecutive_stops_after_close(
                pid, sell_date=date(2026, 7, 20 + i), db_path=isolated_env.db_path)
        assert sent == []

    def test_notice_never_raises(self, isolated_env, monkeypatch):
        """提醒是旁路:任何异常一律吞掉,**绝不阻断清仓已记账这一事实**。"""
        monkeypatch.setattr(circuit, "count_tail_consecutive_stops",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        assert positions_entry.notice_consecutive_stops_after_close(
            1, sell_date=date(2026, 7, 22), db_path=isolated_env.db_path) is None


class TestNoticeTextIsPurelyInformational:
    """⑤-B 第 6 项:文案改**纯告知**,⛔ 禁指令词、⛔ 不许出现「停止开仓」/「只减不加」。"""

    _BANNED = ("只减不加", "停止开仓", "禁开新仓", "停开新仓", "解锁", "熔断", "锁定", "灰化")

    def test_push_text_has_no_command_words(self, isolated_env, monkeypatch):
        from neckline.api import notify

        seen: List[Tuple[str, str]] = []
        monkeypatch.setattr(notify, "push_event",
                            lambda kind, title, body, **kw: seen.append((title, body)) or
                            notify.NotifyOutcome(skipped_reason="test"))
        notify.push_consecutive_stops_notice(3, ts_code="000003.SZ", db_path=isolated_env.db_path)
        title, body = seen[0]
        for word in self._BANNED:
            assert word not in title and word not in body, (
                f"连续止损提醒文案里出现禁用词「{word}」—— 裁定 #8 要求这是**纯提醒**,"
                f"⛔ 不许指令用户、不许暗示任何自动状态。实际:{title} / {body}"
            )
        assert "提醒" in body and "已补录成交" in body   # 诚实边界仍在
