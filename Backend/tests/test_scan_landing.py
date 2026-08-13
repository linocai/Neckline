"""V2.2-③-C 落地起跳位置关(🔴 2026-08-09 用户裁定 #11 后:`neckline/scan/landing.py`
+ `landing_store.py` + `landing_metrics_daily`,机械层**只算原始读数、零判定**;
判定移交 LLM,不在本模块覆盖范围内)。

覆盖(逐条对应本次交付清单):
· 十四项原始读数逐项数值正确性(独立 Python oracle 交叉验证,不复用被测代码
  的任何计算逻辑——`_expected_metrics` 是另一条实现路径);
· 缺数不猜:短历史 / 无下跌日 / 行业未映射 / 涨停分区缺失 四类 `metrics_missing`
  原因码场景,逐项断言缺的是哪个键、原因码是什么(⛔ 不是笼统"有缺失"就算过);
· **bulk 与 day-by-day 与读回三路等价**(比较前 `.drop("computed_at")`,P1-36 体例);
· 🆕 零判定守门(裁定 #11 的机器判据):`landing.py` 不出现四态枚举字面量、不
  出现旧十二阈值的任何一个、不出现 `decide_*` 判定函数、不出现名字带 THRESHOLD
  的字典;
· 🔴 雷区对照(含新增第 5 条)逐字在场;反向守门(零 import sentinel /
  score_display / selection.pack);零写库;
· 骨架包 `packs/K8-skeleton.json` 不再携带 `config.landing` 段;
· 表结构(`landing_metrics_daily` 五列 DDL)。
"""

from __future__ import annotations

import ast
import json
import math
import random
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import neckline.scan.landing as landing
from neckline.db import connection
from neckline.scan import landing_store
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

SKELETON_FILE = Path(__file__).resolve().parent.parent / "packs" / "K8-skeleton.json"
LANDING_SRC = Path(landing.__file__).read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# 0. 独立 Python oracle(不复用 landing.py 的任何计算逻辑,另一条实现路径)
# ══════════════════════════════════════════════════════════════════════════

def _linear_quantile(values: List[float], q: float) -> float:
    """标准线性插值分位数(numpy 默认 method="linear" 同款;已用 polars
    `rolling_quantile(interpolation="linear")` 逐位核对过,两者数值完全一致)。"""
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    h = (n - 1) * q
    lo, hi = math.floor(h), math.ceil(h)
    if lo == hi:
        return float(s[int(lo)])
    frac = h - lo
    return float(s[int(lo)] + frac * (s[int(hi)] - s[int(lo)]))


def _gen_bars(n: int, *, seed: int, base_price: float = 10.0) -> List[Dict[str, float]]:
    """确定性伪随机日线序列(seed 固定可复现):日收益率 ±3% 均匀分布复合生成
    close,low/high 在 close 基础上各留一点随机偏移(low ≤ close ≤ high),amount
    独立随机。第 0 根 bar 的 pre_close = 自身 close(视作序列首日无涨跌)。"""
    rng = random.Random(seed)
    bars: List[Dict[str, float]] = []
    close = base_price
    pre_close = base_price
    for i in range(n):
        if i > 0:
            ret = rng.uniform(-0.03, 0.03)
            pre_close = close
            close = close * (1.0 + ret)
        low = close * (1.0 - abs(rng.uniform(0.0, 0.02)))
        high = close * (1.0 + abs(rng.uniform(0.0, 0.02)))
        amount = rng.uniform(80000.0, 150000.0)
        bars.append({"close": close, "low": low, "high": high,
                     "amount": amount, "pre_close": pre_close})
    return bars


def _force_no_down_tail(bars: List[Dict[str, float]], k: int = 5, step: float = 0.01) -> List[Dict[str, float]]:
    """把最后 `k` 根 bar 就地强改成逐日非负收益(近 5 日无下跌日),供
    `no_down_days` 缺项场景使用。"""
    n = len(bars)
    prev_close = bars[n - k - 1]["close"]
    for j in range(k):
        idx = n - k + j
        close = prev_close * (1.0 + step)
        bars[idx] = {
            "close": close, "low": close * 0.995, "high": close * 1.01,
            "amount": 100000.0, "pre_close": prev_close,
        }
        prev_close = close
    return bars


def _expected_metrics(
    bars: List[Dict[str, float]], industry_medians: List[float], *, is_limit_up_today: Optional[bool],
) -> Dict[str, Any]:
    """纯 Python 独立实现十四项读数(oracle,与 `landing.py` 的 polars 管线完全
    不共用代码)。`bars`/`industry_medians` 等长,判定日 = 最后一根。全部键均假定
    可算(调用方需保证 `bars` 足够长、`industry_medians` 覆盖同一窗口)。"""
    n = len(bars)
    closes = [b["close"] for b in bars]
    lows = [b["low"] for b in bars]
    highs = [b["high"] for b in bars]
    amounts = [b["amount"] for b in bars]
    rets = [b["close"] / b["pre_close"] - 1.0 for b in bars]

    out: Dict[str, Any] = {}

    low5 = min(lows[n - 5:n])
    backlow = min(lows[n - 25:n - 5])
    out["low5_over_low20_ratio"] = low5 / backlow

    prior_low = min(lows[n - 21:n - 1])
    out["is_new_low_20d"] = lows[n - 1] < prior_low

    ma20 = sum(closes[n - 20:n]) / 20.0
    out["close_over_ma20_dev"] = closes[n - 1] / ma20 - 1.0

    plat_low = _linear_quantile(closes[n - 20:n], 0.20)
    out["close_over_platform_floor_dev"] = closes[n - 1] / plat_low - 1.0

    down_idx = [i for i in range(n - 5, n) if rets[i] < 0]
    if down_idx:
        down_amt5 = sum(amounts[i] for i in down_idx)
        amt20 = sum(amounts[n - 20:n]) / 20.0
        out["down_day_amount_ratio_5v20"] = (down_amt5 / len(down_idx)) / amt20
    else:
        out["down_day_amount_ratio_5v20"] = None

    out["max_daily_drop_5d"] = min(rets[n - 5:n])

    ma5 = sum(closes[n - 5:n]) / 5.0
    out["close_over_ma5_dev"] = closes[n - 1] / ma5 - 1.0

    out["pct_chg"] = rets[n - 1]

    stock5 = sum(rets[n - 5:n])
    ind5 = sum(industry_medians[n - 5:n])
    out["rs5"] = stock5 - ind5

    high60 = max(highs[n - 60:n])
    out["dist_from_high_60d"] = closes[n - 1] / high60 - 1.0

    out["cum_return_3d"] = sum(rets[n - 3:n])

    out["is_limit_up"] = is_limit_up_today

    prior_high60 = max(highs[n - 61:n - 1])
    out["is_new_high_60d"] = highs[n - 1] > prior_high60

    def amp_ok(i: int) -> Optional[bool]:
        if i < 19:
            return None
        wh, wl = max(highs[i - 19:i + 1]), min(lows[i - 19:i + 1])
        return (wh - wl) / wl <= 0.25

    run = 0
    i = n - 1
    while i >= 0 and amp_ok(i) is True:
        run += 1
        i -= 1
    out["platform_days"] = min(run, 120)

    return out


# ══════════════════════════════════════════════════════════════════════════
# 1. 表结构(plan §五 ③-C DDL 逐列)
# ══════════════════════════════════════════════════════════════════════════

def test_landing_metrics_daily_columns_match_plan(isolated_env):
    with connection(isolated_env.db_path) as conn:
        info = conn.execute("PRAGMA table_info(landing_metrics_daily)").fetchall()
    cols = [r[1] for r in info]
    pk = [r[1] for r in info if r[5]]
    assert cols == ["trade_date", "ts_code", "metrics_json", "metrics_missing", "computed_at"]
    assert set(pk) == {"trade_date", "ts_code"}


def test_old_landing_state_daily_table_no_longer_declared(isolated_env):
    """裁定 #11:旧表 `landing_state_daily` 从未上产,本次干净重构不留 DDL。"""
    with connection(isolated_env.db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='landing_state_daily'"
        ).fetchone()
    assert row is None


# ══════════════════════════════════════════════════════════════════════════
# 2. 骨架包:裁定 #11 后不再携带 config.landing 段
# ══════════════════════════════════════════════════════════════════════════

def test_skeleton_pack_has_no_landing_section():
    doc = json.loads(SKELETON_FILE.read_text(encoding="utf-8"))
    assert "landing" not in doc["config"], "裁定 #11 后骨架包不应再携带 config.landing(十二阈值已全部删除)"
    # ⚠ V2.3.2-④-A 又追加了两段(iteration / threshold_governance)——本测试只管
    # 「landing 段不许回来」,故断言改成**不含 landing** 而不是钉死整个段集合。
    assert "landing" not in set(doc["config"])
    assert doc["manifest"]["pack_version"] == "K8-V0.8", "V2.4.0-P1.8+ 升 K8-V0.8"


# ══════════════════════════════════════════════════════════════════════════
# 3. 🆕 零判定守门(裁定 #11 的机器判据):四态字面量 / 旧阈值 / 判定函数一律不许出现
# ══════════════════════════════════════════════════════════════════════════

_BANNED_STATE_LITERALS = (
    '"falling"', "'falling'",
    '"landing_pending"', "'landing_pending'",
    '"liftoff_confirmed"', "'liftoff_confirmed'",
    '"high_extended"', "'high_extended'",
)

# 十二个旧阈值键名(治理性"及格线",裁定 #11 后应彻底消失——不像窗口常量
# `n_low`/`n_back`/`platform_win`/`lift_win`/`platform_amp_win`/`platform_amp_max`
# 那样以新名字留存,这六个是纯粹的"多严格才算过关",没有任何等价物)。
_BANNED_THRESHOLD_NAMES = (
    "low_tol", "sup_tol", "sell_decay", "panic_drop", "high_gap", "lift_max",
    "LANDING_THRESHOLD_DEFAULTS", "resolve_landing_thresholds", "landing_config",
    "decide_landing", "STATE_LABELS", "STATE_ORDER", "skeleton_version",
)


def test_landing_module_has_no_four_state_literals():
    for lit in _BANNED_STATE_LITERALS:
        assert lit not in LANDING_SRC, f"landing.py 出现四态枚举字面量:{lit!r}(裁定 #11 已删四态)"


def _collect_identifiers(tree: ast.AST) -> set:
    """收集模块里所有**实际用作标识符**的名字(函数/参数名、变量名、属性访问名、
    关键字实参名)。⛔ 不含字符串字面量/文档字符串里的散文引用——模块头「本次
    改动删掉了什么」段落按 CLAUDE.md 记录纪律必须点名旧标识符(如
    `decide_landing()`)才能说清楚删了什么,那是历史说明不是代码,必须能与
    "代码里真的还有这个东西"区分开,后者才是裁定 #11 真正要防的复发。"""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def test_landing_module_has_no_retired_threshold_identifiers():
    """裁定 #11 机器判据(AST 版,只看"真正用作标识符"的名字,不误伤模块头
    「删掉了什么」段落里必要的历史点名——见 `_collect_identifiers` docstring)。"""
    tree = ast.parse(LANDING_SRC)
    used = _collect_identifiers(tree)
    for name in _BANNED_THRESHOLD_NAMES:
        assert name not in used, f"landing.py 出现已作废判定标识符(代码里真的还在用):{name!r}"


def test_landing_module_ast_has_no_decide_function_or_threshold_dict():
    """AST 版零判定守门:不许有 `decide_*` 判定函数,不许有名字带 THRESHOLD 的
    模块级字典/赋值(防后人换个名字把阈值表塞回来)。"""
    tree = ast.parse(LANDING_SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not node.name.startswith("decide_"), (
                f"landing.py 出现判定函数 {node.name}(裁定 #11:机械层零判定)"
            )
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assert "THRESHOLD" not in target.id.upper(), (
                        f"landing.py 出现疑似阈值字典 {target.id}(裁定 #11:阈值全部删除)"
                    )


def test_landing_module_never_imports_selection_pack():
    """裁定 #11 后机械层没有阈值要从包里读,零 import `neckline.selection.pack`
    (比裁定 #11 之前的"只许 import 读入口"更严——现在是完全零依赖)。"""
    tree = ast.parse(LANDING_SRC)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert "selection.pack" not in name, f"landing.py 禁止 import {name}(裁定 #11 后零阈值可读)"


# ══════════════════════════════════════════════════════════════════════════
# 4. 雷区对照逐字在场 / 反向 import / 零写库 / METRIC_KEYS 契约
# ══════════════════════════════════════════════════════════════════════════

def test_minefield_notes_are_verbatim_in_module_header():
    """🔴 plan §五 ③-C「雷区对照」四条 + 裁定 #11 新增第 5 条必须原样在
    `landing.py` 模块头(一字不省)——同时防「后人当新发现」与「后人当禁令删掉」
    (§七 P3-49-(a)/(e))。"""
    for phrase in (
        "K3-B2 臂③「升势回撤 + 启动确认」",
        "确认信号 = 死猫跳顶点,比直接买更差",
        "research/k3_report.md",
        "K3 系统化超跌反弹四臂全灭",
        "K2「追强势」全否决 + K7-C1 诱多做局",
        "站在案底同侧",
        "只产注意力分层",
        "不得被读成买入期望背书",
        "选股时钟",
        "P3-49",
        # 🆕 裁定 #11 新增第 5 条(判定交给 LLM 后证伪义务不减反增)
        "证伪义务不减反增",
        "gate_evaluations.evidence_json",
        "必须同时存下当次读数与 LLM 理由",
    ):
        assert phrase in LANDING_SRC, f"雷区对照缺字:{phrase!r}"


def test_landing_module_never_imports_sentinel_or_score_display():
    """反向守门(plan §五 ③ 原文):位置态 ⛔ 不接持仓动作、不进推送、不碰展示
    标度 —— 靠「没有那条 import」结构性担保,不靠自觉。"""
    tree = ast.parse(LANDING_SRC)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith("neckline.sentinel"), f"landing.py 禁止 import {name}"
            assert "score_display" not in name, f"landing.py 禁止 import {name}"


def test_landing_module_is_read_only_no_write_sql():
    """`landing.py` 零写库(写只发生在 `landing_store.py`)——源码里不许出现
    INSERT/UPDATE/DELETE/executemany(regime.py 同款分工)。"""
    for banned in ("INSERT ", "UPDATE ", "DELETE ", "executemany"):
        assert banned not in LANDING_SRC, f"landing.py 出现写库痕迹:{banned!r}"


def test_metric_keys_contract_has_fourteen_keys_verbatim():
    """两个 agent 共用的 `metrics_json` 键名契约:恰好十四个键,逐字匹配任务
    交付清单(⛔ 不许改名、不许增减)。"""
    assert landing.METRIC_KEYS == (
        "low5_over_low20_ratio", "is_new_low_20d",
        "close_over_ma20_dev", "close_over_platform_floor_dev",
        "down_day_amount_ratio_5v20", "max_daily_drop_5d",
        "close_over_ma5_dev", "pct_chg", "rs5",
        "dist_from_high_60d", "cum_return_3d",
        "is_limit_up", "is_new_high_60d", "platform_days",
    )
    assert len(landing.METRIC_KEYS) == 14
    assert len(set(landing.METRIC_KEYS)) == 14


# ══════════════════════════════════════════════════════════════════════════
# 5. 全市场批算(isolated_env):读数正确性 + 缺数分布 + 三路等价 + 覆盖率
# ══════════════════════════════════════════════════════════════════════════

_IND = "顶级行业"
_RICH = "600001.SH"        # 150 日完整历史,一切读数均应可算
_MONOUP = "600002.SH"      # 150 日完整历史,但近 5 日强制无下跌日
_UNMAPPED = "600003.SH"    # 150 日完整历史,但不在 stock_basic 里(行业未映射)
_SHORT = "600004.SH"       # 只有最后 2 天历史(短历史,且当日无 limit_derived)

_N_DAYS = 150


def _build_market(env):
    """150 个交易日 × 4 只票的合成盘(judgment day = 最后一天)。返回
    `(days, judgment_day, rich_bars, monoup_bars, unmapped_bars, industry_medians)`
    ——后三者与 `industry_medians` 供各测试喂给 `_expected_metrics` oracle 交叉核对。"""
    days = business_days(date(2026, 1, 5), _N_DAYS)
    insert_trade_cal(env, days)
    insert_stock_basic(env, [
        {"ts_code": _RICH, "name": "甲", "industry": _IND},
        {"ts_code": _MONOUP, "name": "乙", "industry": _IND},
        # _UNMAPPED 故意不写 stock_basic 行 —— 行业未映射场景
    ])

    rich_bars = _gen_bars(_N_DAYS, seed=20260801)
    monoup_bars = _force_no_down_tail(_gen_bars(_N_DAYS, seed=20260802), k=5, step=0.01)
    unmapped_bars = _gen_bars(_N_DAYS, seed=20260803)
    short_bars = _gen_bars(2, seed=20260804)   # 仅 2 天,独立小序列(judgment day = 第 2 天)

    rng_ind = random.Random(20260809)
    industry_medians = [rng_ind.uniform(-0.01, 0.01) for _ in range(_N_DAYS)]

    def _row(ts_code: str, bar: Dict[str, float]) -> dict:
        return {
            "ts_code": ts_code, "open": bar["pre_close"], "high": bar["high"],
            "low": bar["low"], "close": bar["close"], "pre_close": bar["pre_close"],
            "amount": bar["amount"],
        }

    for i, d in enumerate(days):
        rows = [
            _row(_RICH, rich_bars[i]),
            _row(_MONOUP, monoup_bars[i]),
            _row(_UNMAPPED, unmapped_bars[i]),
        ]
        if i >= _N_DAYS - 2:   # 600004.SH 只在最后 2 天有行
            rows.append(_row(_SHORT, short_bars[i - (_N_DAYS - 2)]))
        write_daily_fixture(env, "daily", d, rows)

    conn = sqlite3.connect(str(env.db_path))
    try:
        for i, d in enumerate(days):
            conn.execute(
                "INSERT OR REPLACE INTO industry_strength_daily "
                "(trade_date, industry, median_ret, member_count, industry_rank, "
                "is_strength_day, persist_days, quantile, min_members, computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d.strftime("%Y%m%d"), _IND, industry_medians[i], 2, 1, 0, 0, 0.8, 2, "t"),
            )
        conn.commit()
    finally:
        conn.close()

    # 判定日 limit_derived(**稀疏表**,见 `landing.py` 模块头「口径细则」):只给
    # _RICH/_MONOUP/_UNMAPPED 三票显式非涨停行,_SHORT 故意不给行——但分区**文件
    # 本身存在**(这三票的行就写在这个文件里),所以 _SHORT 的 is_limit_up 应读
    # 成确定的 False(稀疏表「不在 = 不成立」),⛔ 不是缺数;`limit_data_unavailable`
    # 场景改由 `test_limit_derived_partition_missing_for_whole_day` 覆盖(整个
    # 分区文件都不存在的那一种"真的不知道")。
    write_daily_fixture(env, "limit_derived", days[-1], [
        {"ts_code": c, "board": "MAIN", "limit_pct": 0.10,
         "limit_up_price": 999.0, "limit_down_price": 1.0,
         "is_limit_up": False, "is_limit_down": False, "is_zaban": False,
         "consec_limit_up_days": 0}
        for c in (_RICH, _MONOUP, _UNMAPPED)
    ])
    return days, days[-1], rich_bars, monoup_bars, unmapped_bars, industry_medians


class TestReadingCorrectness:
    """十四项读数逐项数值正确性——与独立 Python oracle 交叉核对(不是"跑起来不
    崩"这种弱断言)。"""

    def test_rich_ticker_all_fourteen_metrics_match_oracle(self, isolated_env):
        env = isolated_env
        days, d0, rich_bars, _mono, _unm, ind_med = _build_market(env)
        stats = landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert stats == {"days": 1, "rows": 4, "failed": 0}

        row = landing_store.load_landing_metric(d0, _RICH, db_path=env.db_path)
        assert row is not None
        assert row["metrics_missing"] == {}, f"完整 150 日历史不应有任何缺项:{row['metrics_missing']}"

        expected = _expected_metrics(rich_bars, ind_med, is_limit_up_today=False)
        got = row["metrics"]
        assert set(got) == set(landing.METRIC_KEYS)
        for key in landing.METRIC_KEYS:
            if isinstance(expected[key], bool) or isinstance(expected[key], int):
                assert got[key] == expected[key], f"{key}: got={got[key]} expected={expected[key]}"
            else:
                assert got[key] == pytest.approx(expected[key], abs=1e-6), (
                    f"{key}: got={got[key]} expected={expected[key]}"
                )

    def test_monoup_ticker_matches_oracle_except_forced_field(self, isolated_env):
        """_MONOUP 除 `down_day_amount_ratio_5v20`(下面缺数测试单独断言)外,
        其余十三项也应与 oracle 一致——证明"强改尾部"没有连带破坏其它读数。"""
        env = isolated_env
        days, d0, _rich, monoup_bars, _unm, ind_med = _build_market(env)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        row = landing_store.load_landing_metric(d0, _MONOUP, db_path=env.db_path)
        assert row is not None
        expected = _expected_metrics(monoup_bars, ind_med, is_limit_up_today=False)
        got = row["metrics"]
        for key in landing.METRIC_KEYS:
            if key == "down_day_amount_ratio_5v20":
                continue
            if isinstance(expected[key], (bool, int)):
                assert got[key] == expected[key], key
            else:
                assert got[key] == pytest.approx(expected[key], abs=1e-6), key


class TestMetricsMissingReasons:
    """缺数不猜:四类场景逐项断言缺的是哪个键、原因码是什么。"""

    def test_short_history_ticker_missing_everything_except_pct_chg_and_limit_up(self, isolated_env):
        """`_SHORT` 只有 2 天历史:除 `pct_chg`(只需当日 pre_close,不需要滚动
        窗口,2 天足够)外,其余十二项全部因窗口不足而缺失(`insufficient_history`)。
        `is_limit_up` 反而是**可算的**:`limit_derived` 是稀疏表,判定日分区文件
        确实存在(写着 _RICH/_MONOUP/_UNMAPPED 三票的行)只是 `_SHORT` 不在里面
        —— 这恰恰是「三者皆不成立」的确定事实,读成 `False`,⛔ 不是缺数(见
        `landing.py` 模块头「口径细则」;`limit_data_unavailable` 场景改由
        `test_limit_derived_partition_missing_for_whole_day` 覆盖)。"""
        env = isolated_env
        days, d0, *_ = _build_market(env)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        row = landing_store.load_landing_metric(d0, _SHORT, db_path=env.db_path)
        assert row is not None
        assert set(row["metrics"]) == {"pct_chg", "is_limit_up"}, row["metrics"]
        assert row["metrics"]["is_limit_up"] is False
        missing = row["metrics_missing"]
        for key in landing.METRIC_KEYS:
            if key in ("pct_chg", "is_limit_up"):
                continue
            assert missing[key] == landing.REASON_INSUFFICIENT_HISTORY, f"{key}: {missing.get(key)!r}"
        assert set(missing) | {"pct_chg", "is_limit_up"} == set(landing.METRIC_KEYS)

    def test_monoup_ticker_down_ratio_missing_as_no_down_days(self, isolated_env):
        """近 5 日无下跌日:`down_day_amount_ratio_5v20` 缺失且原因码是
        `no_down_days`(⛔ 不是 `insufficient_history`——历史很充分,只是这个
        比值本身对"零下跌日"无定义)。"""
        env = isolated_env
        days, d0, *_ = _build_market(env)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        row = landing_store.load_landing_metric(d0, _MONOUP, db_path=env.db_path)
        assert row is not None
        assert row["metrics_missing"] == {"down_day_amount_ratio_5v20": landing.REASON_NO_DOWN_DAYS}

    def test_unmapped_ticker_rs5_missing_as_industry_unmapped(self, isolated_env):
        """`_UNMAPPED` 不在 `stock_basic` 里:`rs5` 缺失且原因码是
        `industry_unmapped`,其余十三项均可算(证明"行业查无此票"只影响 rs5
        一项,不连累其它读数)。"""
        env = isolated_env
        days, d0, _rich, _mono, unmapped_bars, ind_med = _build_market(env)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        row = landing_store.load_landing_metric(d0, _UNMAPPED, db_path=env.db_path)
        assert row is not None
        assert row["metrics_missing"] == {"rs5": landing.REASON_INDUSTRY_UNMAPPED}
        expected = _expected_metrics(unmapped_bars, ind_med, is_limit_up_today=False)
        got = row["metrics"]
        for key in landing.METRIC_KEYS:
            if key == "rs5":
                continue
            if isinstance(expected[key], (bool, int)):
                assert got[key] == expected[key], key
            else:
                assert got[key] == pytest.approx(expected[key], abs=1e-6), key

    def test_limit_derived_partition_missing_for_whole_day(self, isolated_env):
        """判定日 `limit_derived` 分区**整体缺失**(不是"该票没有行"这种局部
        缺失,是文件都不在):`_RICH` 的 `is_limit_up` 应缺失
        (`limit_data_unavailable`),其余十三项不受影响(仍可算,证明这个
        独立分区的缺失不会连累价格类读数)。"""
        env = isolated_env
        import os

        from neckline.data.market_data import day_file_path

        days, d0, rich_bars, _mono, _unm, ind_med = _build_market(env)
        p = day_file_path("limit_derived", d0, env.parquet_dir)
        if p.exists():
            os.remove(p)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        row = landing_store.load_landing_metric(d0, _RICH, db_path=env.db_path)
        assert row is not None
        assert row["metrics_missing"] == {"is_limit_up": landing.REASON_LIMIT_DATA_UNAVAILABLE}


class TestNoDailyData:
    def test_no_daily_data_yields_zero_rows_not_error(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 1, 5), 3)
        insert_trade_cal(env, days)
        stats = landing_store.refresh_landing_metrics(
            [days[-1]], db_path=env.db_path, parquet_dir=env.parquet_dir
        )
        assert stats["rows"] == 0 and stats["failed"] == 0
        assert landing_store.load_landing_metrics(days[-1], db_path=env.db_path).is_empty()
        assert landing_store.load_landing_metric(days[-1], _RICH, db_path=env.db_path) is None


class TestThreeWayEquivalence:
    def test_bulk_vs_day_by_day_vs_readback_are_identical(self, isolated_env):
        """三路等价:全量批算(一次调用 3 天)≡ 逐日循环 ≡ 落表读回,比较前
        `.drop("computed_at")`(P1-36 定案体例:审计戳跨秒边界合法不同,业务列
        仍逐位相同)。metrics 只存缩放不变量(比值/收益率),qfq 基准因子随取数
        区间尾端漂移不影响任何业务列——这正是本断言成立的前提(模块头登记)。"""
        env = isolated_env
        days, _d0, *_ = _build_market(env)
        last3 = days[-3:]

        landing_store.refresh_landing_metrics(last3, db_path=env.db_path, parquet_dir=env.parquet_dir)
        bulk = {d: landing_store.load_landing_metrics(d, db_path=env.db_path) for d in last3}

        with connection(env.db_path) as conn:
            conn.execute(f"DELETE FROM {landing_store.TABLE}")
        for d in last3:
            landing_store.refresh_landing_metrics([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
        daybyday = {d: landing_store.load_landing_metrics(d, db_path=env.db_path) for d in last3}

        for d in last3:
            assert not bulk[d].is_empty()
            assert bulk[d].drop("computed_at").equals(daybyday[d].drop("computed_at")), (
                f"{d} 批算与逐日结果不一致(业务列,已排除审计戳 computed_at)"
            )

    def test_metrics_json_and_missing_are_valid_json(self, isolated_env):
        env = isolated_env
        days, d0, *_ = _build_market(env)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        df = landing_store.load_landing_metrics(d0, db_path=env.db_path)
        assert df.height == 4
        for metrics_s, missing_s in zip(df["metrics_json"].to_list(), df["metrics_missing"].to_list()):
            m = json.loads(metrics_s)
            miss = json.loads(missing_s)
            assert set(m) | set(miss) == set(landing.METRIC_KEYS)
            assert set(m) & set(miss) == set()


class TestCoverage:
    """`landing_metrics_coverage`:读数覆盖率 + 缺项分布(CLI 回放用)。"""

    def test_coverage_on_missing_day_is_zero(self, isolated_env):
        env = isolated_env
        cov = landing_store.landing_metrics_coverage(date(2026, 1, 5), db_path=env.db_path)
        assert cov == {"total": 0, "missing_counts": {}}

    def test_coverage_aggregates_missing_counts_across_universe(self, isolated_env):
        env = isolated_env
        days, d0, *_ = _build_market(env)
        landing_store.refresh_landing_metrics([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        cov = landing_store.landing_metrics_coverage(d0, db_path=env.db_path)
        assert cov["total"] == 4
        # _MONOUP 贡献 1 次 down_day_amount_ratio_5v20 缺失,_UNMAPPED 贡献 1 次
        # rs5 缺失,_SHORT 贡献 12 项缺失(不含 is_limit_up——稀疏表「不在=False」,
        # 判定日分区文件本身存在,_SHORT 因此有确定读数,不算缺数)。
        assert cov["missing_counts"]["down_day_amount_ratio_5v20"] == 2  # _MONOUP + _SHORT
        assert cov["missing_counts"]["rs5"] == 2                        # _UNMAPPED + _SHORT
        assert "is_limit_up" not in cov["missing_counts"], "四票当日均有确定读数(稀疏表不在=False),不应有缺失"
        assert cov["missing_counts"]["platform_days"] == 1              # 只有 _SHORT
