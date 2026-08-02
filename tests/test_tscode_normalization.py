"""写入通道 `ts_code` 归一单测(v1.3.3 生产真洞修复)。

根因(生产实测):`POST /positions` 把客户端 `body.code` 原样透传给
`sentinel/positions.py::open_position`,用户在客户端敲裸 6 位 → 裸码入库。裸码在盘中哨兵侧
无碍(`quotes.to_symbol` 自补前缀),但 16:35 EOD 持仓管线
(`report/holding_k4_check.py`)拿 `ts_code` **直接 join 行情面板**(TuShare 口径
`300759.SZ`)→ join 不上 → `has_data=False`/`close=0`/`net_float=None`,K4 派发警报永不触发、
D5 判向被保守锁死,**全程静默无报错**。

修法钉死在**写入通道**(与 `neckline/watchlist.py` 既有姿势一致),不是 API 层——这样
CLI(`scripts/positions.py`)、API、未来任何调用方都自动吃到。本文件锁死两条写入通道
(positions / inquiry_pool)+ 一条查询通道(`list_decisions(ts_code=)`,v2.0.0 起
`decision_log` 写入口已退役,只剩这条读通道仍归一),并直接实证「裸码入库会让
EOD 持仓管线 join 不上」这个原始故障(反向证伪哨兵)。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.decision_log import list_decisions
from neckline.sentinel import positions as pos_store
from tests.conftest import insert_decision_log_row

pytestmark = pytest.mark.usefixtures("isolated_env")

_BARE = "300759"
_FULL = "300759.SZ"


class TestPositionsNormalization:
    @pytest.mark.parametrize("given", [_BARE, _FULL, "300759.sz", " 300759 "])
    def test_open_position_normalizes(self, isolated_env, given):
        pid = pos_store.open_position(given, 39.42, 500, date(2026, 7, 27),
                                      db_path=isolated_env.db_path)
        rows = pos_store.load_open_positions(db_path=isolated_env.db_path)
        assert [p.ts_code for p in rows if p.id == pid] == [_FULL]

    def test_sh_and_bse_prefixes(self, isolated_env):
        """归一走 `normalize_ts_code`(内部复用 board.classify_by_code),沪/北同样对。"""
        pos_store.open_position("600519", 1500.0, 100, date(2026, 7, 27),
                                db_path=isolated_env.db_path)
        pos_store.open_position("920117", 10.0, 100, date(2026, 7, 27),
                                db_path=isolated_env.db_path)
        codes = {p.ts_code for p in pos_store.load_open_positions(db_path=isolated_env.db_path)}
        assert codes == {"600519.SH", "920117.BJ"}


class TestDecisionLogNormalization:
    """v2.0.0 起(⑩-C)`decision_log` 写入口已退役——"写入侧归一"这件事无从测起
    (物理上不存在任何应用层写口能把裸码写进这张表)。仍然成立、仍需锁死的是
    **查询侧**:`list_decisions(ts_code=)` 依然归一后再比对,故 fixture 直接插入
    "已是标准形态"的历史行(模拟割接前 v1.3.3 归一写入口留下的真实历史数据),
    验证传裸码查询依然命中。"""

    def test_list_decisions_filter_normalizes(self, isolated_env):
        db = isolated_env.db_path
        insert_decision_log_row(
            db, ts_code=_FULL, why_buy="x", why_entry_price="y", invalidation="z",
            thesis_tags=["t"], playbook_tag="p",
        )
        assert [d.ts_code for d in list_decisions(ts_code=_BARE, db_path=db)] == [_FULL]
        assert [d.ts_code for d in list_decisions(ts_code=_FULL, db_path=db)] == [_FULL]


class TestInquiryPoolNormalization:
    def test_add_to_inquiry_pool_normalizes(self, isolated_env):
        from neckline.api.stores import add_to_inquiry_pool, load_inquiry_pool
        day = date(2026, 7, 27)
        add_to_inquiry_pool(day, _BARE, db_path=isolated_env.db_path)
        assert [p["ts_code"] for p in load_inquiry_pool(day, db_path=isolated_env.db_path)] == [_FULL]

    def test_idempotent_across_bare_and_full(self, isolated_env):
        """裸码与带后缀视作同一票:UNIQUE(trade_date, ts_code) 幂等不再被格式差异绕过。"""
        from neckline.api.stores import add_to_inquiry_pool, load_inquiry_pool
        day = date(2026, 7, 27)
        add_to_inquiry_pool(day, _BARE, db_path=isolated_env.db_path)
        add_to_inquiry_pool(day, _FULL, db_path=isolated_env.db_path)
        assert len(load_inquiry_pool(day, db_path=isolated_env.db_path)) == 1


class TestOriginalFailureIsReproducible:
    """反向证伪哨兵:直接实证「裸码 join 不上行情面板」这个原始故障,证明本修复不是
    在解决一个想象出来的问题——若哪天有人把归一去掉,本测试会失败。"""

    def test_bare_code_misses_panel_join_but_normalized_hits(self, isolated_env):
        panel = pl.DataFrame({"ts_code": [_FULL, "000001.SZ"], "close": [39.4, 10.0]})
        rows_by_code = {r["ts_code"]: r for r in panel.to_dicts()}   # 同 holding_k4_check 的姿势
        assert rows_by_code.get(_BARE) is None                      # ← 原始故障:静默 miss
        pid = pos_store.open_position(_BARE, 39.42, 500, date(2026, 7, 27),
                                      db_path=isolated_env.db_path)
        stored = next(p for p in pos_store.load_open_positions(db_path=isolated_env.db_path)
                      if p.id == pid)
        assert rows_by_code.get(stored.ts_code) is not None          # ← 归一后 join 得上
