"""K9 参数包契约单测(V2.5.0 S5,PROJECT_PLAN §6 S5 验收 + §5.4.3,裁定 5)。

S5 的六条验收逐条对应本文件的六个 section:

| # | 验收 | section |
|---|---|---|
| 1 | `dataclasses.fields(K9Params)` 每个字段无默认值 | ① |
| 2 | 少任一键 → `ParamsUnavailable` 且 `missing` **精确** | ② |
| 3 | 窗口 > 120 → invalid | ③ |
| 4 | `factPackVersion` 不匹配 → invalid | ③ |
| 5 | 🔴 参数缺失 → 报告 `not_run`,⛔ 不是 `empty` | 见 `test_report_state.py` |
| 6 | 上一份冻结结果被保留 | 见 `test_report_state.py` |

外加 §8.3 #18–#20 的**全取值实现**夹具(section ④)与示例配置(section ⑤)。

🔴 **本文件的夹具刻意「手写全量」而不是从示例配置读**:`K9Params` 的每个字段都
没有默认值,测试夹具因此**必须显式提供每一个值**(§10 测试纪律)。哪天有人给某个
字段加了默认值,这份夹具不会变红 —— 但 section ① 会。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from neckline.k9 import params as P

_ROOT = Path(__file__).resolve().parent.parent


def _tiers(payload: dict) -> dict:
    return {"strict": dict(payload), "relaxed": dict(payload)}


def make_raw(**overrides) -> dict:
    """一份**结构完整**的参数包原文。

    ⚠ 这些数字是**测试夹具**,不是标定值、不是建议值、不是默认值 —— 它们的唯一
    职责是让校验器有东西可校验(⛔ 生产参数包一律由 whynotme 标定、用户确认后放入
    `Backend/config/`)。真值全部待标定,见 PROJECT_PLAN §8 的 20 项。"""
    raw = {
        "packageVersion": "k9-params-test",
        "factPackVersion": P.PACK_VERSION,
        "calibratedBy": "unit-test",
        "calibratedAt": "2026-08-20T00:00:00Z",
        "approvedBy": "unit-test",
        "approvedAt": "2026-08-20T00:00:00Z",
        "boundary": {
            "newListingDays": 30,
            "liquidityWindowDays": 20,
            "liquidityBottomPct": 0.2,
            "spikeFadeRetPct": 5.0,
            "spikeFadeGapPct": 3.0,
        },
        "industry": {
            "minMembers": 8,
            "excludedL2Codes": ["801125.SI"],
            "heatAbsentPolicy": "renormalize",
        },
        # 裁定 13/14/15:放量倍数是**共享量**,分母窗口与分界值 V 都住 `volume`,
        # ⛔ 不在任何一个通道的档里(V 不分档,理由见 `params.py` 模块 docstring)。
        "volume": {"maDays": 20, "eruptionMultiple": 2.0},
        "channels": {
            "p1": _tiers({"ampWindowDays": 20, "ampMaxPct": 25.0, "minRetPct": 0.0}),
            "p2": _tiers({"normDropMin": 0.7, "maDays": 20, "minVolMultiple": 1.0}),
            "p3": _tiers({"longWindow": 60, "shortWindow": 7, "flatBand": 0.02}),
            # ⚠ `lagRankGap` 曾经在这份夹具里写着 500.0 —— 一个**永远召不到票**的值,
            # 而校验当时是绿的(复审 H2)。它是两个百分位的差值,只可能落在 [0,1]。
            "p4": _tiers({"dailyInflowRankPct": 0.1, "cumDays": 5,
                          "cumInflowRankPct": 0.15, "lagRankGap": 0.2}),
        },
        "ranking": {
            "weights": {"industryHeat": 0.4, "patternStrength": 0.4, "relay": 0.2},
            "patternSubWeights": {
                "p1": {"volMultiple": 0.4, "upsideRoomFar": 0.3, "relStrength": 0.3},
                "p2": {"relStrengthShortfall": 1.0},
                "p3": {"shortWindowImprovement": 0.5, "upsideRoomNear": 0.5},
                "p4": {"inflowRank": 0.6, "volumeRatioRank": 0.4},
            },
            "relayLookbackDays": 10,
            "relaySource": "recalled",
            "relayScoring": "binary",
            "upsideRoomMechDays": 60,
        },
        "quota": {"min": 10, "max": 20, "floorPerChannel": 1,
                  "overStrictConsecutiveDays": 5},
    }
    for path, value in overrides.items():
        cur = raw
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        if value is _DELETE:
            cur.pop(parts[-1], None)
        else:
            cur[parts[-1]] = value
    return raw


_DELETE = object()


def write(tmp_path: Path, raw: dict, name: str = "k9-params.test.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def db(tmp_path):
    from neckline.db import init_schema
    d = tmp_path / "n.db"
    init_schema(d)
    return d


# ══════════════════════════════════════════════════════════════════════════
# ① 结构性无默认值
# ══════════════════════════════════════════════════════════════════════════

class TestNoDefaultsAnywhere:
    def test_every_field_of_every_param_dataclass_has_no_default(self):
        """🔴 §5.4.3 校验 4:**少一个值就构造不出对象**,不是靠 if 判断。

        这一条是裁定 5(「⛔ 不使用任何默认值」)在类型层面的落地 —— 它比任何
        「记得别写默认值」的注释都可靠。"""
        assert P.assert_no_field_defaults(P.K9Params) == []

    def test_k9params_cannot_be_constructed_with_a_missing_field(self):
        with pytest.raises(TypeError):
            P.K9Params(package_version="x")     # type: ignore[call-arg]

    def test_every_param_dataclass_is_frozen(self):
        for cls in (P.K9Params, P.BoundaryParams, P.IndustryParams, P.ChannelParams,
                    P.ChannelTiers, P.P1Tier, P.P2Tier, P.P3Tier, P.P4Tier,
                    P.RankingParams, P.RankingWeights, P.QuotaParams):
            assert cls.__dataclass_params__.frozen, f"{cls.__name__} 不是 frozen"

    def test_a_loaded_pack_is_immutable(self, tmp_path, db):
        p = P.load(write(tmp_path, make_raw()), db_path=db)
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.quota.min = 99            # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# ② 缺键 → ParamsUnavailable,`missing` 精确点名
# ══════════════════════════════════════════════════════════════════════════

class TestMissingKeys:
    def test_a_complete_pack_loads(self, tmp_path, db):
        p = P.load(write(tmp_path, make_raw()), db_path=db)
        assert p.package_version == "k9-params-test"
        assert p.industry.heat_absent_policy is P.HeatAbsentPolicy.RENORMALIZE
        assert p.channels.p3.strict.long_window == 60
        assert p.ranking.relay_source is P.RelaySource.RECALLED
        assert p.industry.excluded_l2_codes == ("801125.SI",)

    @pytest.mark.parametrize("path", [
        "packageVersion", "factPackVersion", "calibratedBy", "approvedAt",
        "boundary.newListingDays", "boundary.spikeFadeGapPct",
        "industry.minMembers", "industry.heatAbsentPolicy", "industry.excludedL2Codes",
        "channels.p1.strict.ampWindowDays", "channels.p2.relaxed.normDropMin",
        "channels.p3.strict.longWindow", "channels.p4.relaxed.lagRankGap",
        "ranking.weights.relay", "ranking.patternSubWeights.p2.relStrengthShortfall",
        "ranking.relayLookbackDays", "ranking.relaySource", "ranking.relayScoring",
        "ranking.upsideRoomMechDays",
        "quota.min", "quota.overStrictConsecutiveDays",
    ])
    def test_dropping_any_single_key_names_that_exact_path(self, tmp_path, db, path):
        """⛔ **永不取默认**:少任何一个键都必须点名到**那一个路径**,
        不是笼统一句「参数不全」。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: _DELETE})), db_path=db)
        assert path in e.value.missing, e.value.missing
        assert path in e.value.describe()

    def test_a_missing_whole_section_names_every_key_under_it(self, tmp_path, db):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"quota": _DELETE})), db_path=db)
        assert "quota" in e.value.missing

    def test_a_nonexistent_file_is_params_unavailable_not_a_crash(self, tmp_path, db):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(tmp_path / "nope.json", db_path=db)
        assert "不存在" in e.value.describe()

    def test_malformed_json_is_params_unavailable(self, tmp_path, db):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(bad, db_path=db)
        assert e.value.invalid

    def test_there_is_no_default_path(self):
        """🔴 §5.4.3:**⛔ 无默认路径**。`load()` 的 `path` 必须是必填位置参数。"""
        import inspect
        sig = inspect.signature(P.load)
        assert sig.parameters["path"].default is inspect.Parameter.empty


# ══════════════════════════════════════════════════════════════════════════
# ③ 区间 / 指纹 / 权重和
# ══════════════════════════════════════════════════════════════════════════

class TestRangesAndFingerprint:
    @pytest.mark.parametrize("path", [
        "boundary.liquidityWindowDays", "ranking.relayLookbackDays",
        "ranking.upsideRoomMechDays", "channels.p3.strict.longWindow",
        "volume.maDays",
    ])
    def test_a_window_longer_than_max_lookback_is_invalid(self, tmp_path, db, path):
        """§3.2:参数包里任何窗口 > `MAX_LOOKBACK_PACKS`(120)一律判为配置无效
        —— 那不是策略参数,是**工程容量上限**,策略层根本读不了那么长的历史。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: 121})), db_path=db)
        assert any(path in s and "MAX_LOOKBACK_PACKS" in s for s in e.value.invalid), e.value.invalid

    def test_exactly_max_lookback_is_allowed(self, tmp_path, db):
        P.load(write(tmp_path, make_raw(**{"ranking.upsideRoomMechDays": 120})), db_path=db)

    def test_a_mismatched_fact_pack_version_is_invalid(self, tmp_path, db):
        """§5.4.3 校验 3:这份参数包是在**另一版事实包口径**上标定的,⛔ 不能拿来跑。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(factPackVersion="fp-99")), db_path=db)
        assert any("factPackVersion" in s for s in e.value.invalid)

    @pytest.mark.parametrize("path,value", [
        ("boundary.newListingDays", 0),
        ("industry.minMembers", -1),
        ("quota.floorPerChannel", 0),
    ])
    def test_non_positive_integers_are_invalid(self, tmp_path, db, path, value):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: value})), db_path=db)
        assert any(path in s for s in e.value.invalid)

    @pytest.mark.parametrize("value", [0, 1, 1.5, -0.1])
    def test_a_percentile_outside_the_open_unit_interval_is_invalid(self, tmp_path, db, value):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"boundary.liquidityBottomPct": value})),
                   db_path=db)
        assert any("liquidityBottomPct" in s for s in e.value.invalid)

    def test_quota_min_above_max_is_invalid(self, tmp_path, db):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"quota.min": 30})), db_path=db)
        assert any("quota.min" in s for s in e.value.invalid)

    @pytest.mark.parametrize("path", [
        "ranking.weights", "ranking.patternSubWeights.p1",
        "ranking.patternSubWeights.p3",
    ])
    def test_weights_that_do_not_sum_to_one_are_invalid(self, tmp_path, db, path):
        """⚠ 「权重和」的目标值 Plan 没写,本片按 §5.4.6「加权求和 ∈ [0,1]」反推为
        **和为 1**(已登记 §14)。"""
        group = dict(_dig(make_raw(), path))
        first = next(iter(group))
        group[first] = group[first] + 0.5
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: group})), db_path=db)
        assert any("权重和" in s for s in e.value.invalid), e.value.invalid

    def test_a_wrong_type_is_invalid_not_missing(self, tmp_path, db):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"quota.min": "十"})), db_path=db)
        assert e.value.missing == ()
        assert any("quota.min" in s for s in e.value.invalid)

    def test_a_boolean_does_not_pass_as_an_integer(self, tmp_path, db):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"industry.minMembers": True})), db_path=db)
        assert any("minMembers" in s for s in e.value.invalid)


# ══════════════════════════════════════════════════════════════════════════
# ③' 复审 H1 / H2:窗口键的整数性、阈值键的结构性区间
# ══════════════════════════════════════════════════════════════════════════

class TestWindowKeysAreReallyIntegers:
    """🔴 H1:档内键曾被一句 `{k: float for k in keys}` 统一声明成 `float`,
    于是 `_check_ranges` 里两个 `isinstance(v, int)` 前置判断把整数性、正数、
    `MAX_LOOKBACK_PACKS` 三道闸**一起**跳过了 —— 而校验是绿的。"""

    @pytest.mark.parametrize("path,value", [
        ("channels.p3.strict.longWindow", 500.0),      # 复审原样的反例:曾经通过
        ("channels.p2.strict.maDays", 0.4),            # → PackRange.history(days=0.4) 抛裸 ValueError
        ("channels.p1.relaxed.ampWindowDays", 20.5),
        ("channels.p4.strict.cumDays", 5.0),
        ("channels.p3.relaxed.shortWindow", 7.5),
    ])
    def test_a_float_in_a_window_slot_is_invalid(self, tmp_path, db, path, value):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: value})), db_path=db)
        assert any(path in s and "整数" in s for s in e.value.invalid), e.value.invalid

    @pytest.mark.parametrize("path", [
        "channels.p3.strict.longWindow", "channels.p1.strict.ampWindowDays",
        "channels.p2.relaxed.maDays", "channels.p4.relaxed.cumDays",
    ])
    def test_a_window_longer_than_max_lookback_is_invalid_in_the_tiers_too(
        self, tmp_path, db, path,
    ):
        """§12 坑 1 的容量红线:上一版档内窗口整条绕过了这道闸。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: 121})), db_path=db)
        assert any(path in s and "MAX_LOOKBACK_PACKS" in s for s in e.value.invalid)

    @pytest.mark.parametrize("path", [
        "channels.p3.strict.longWindow", "channels.p1.strict.ampWindowDays",
        "channels.p2.strict.maDays", "channels.p4.strict.cumDays",
    ])
    def test_a_non_positive_window_is_invalid(self, tmp_path, db, path):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: 0})), db_path=db)
        assert any(path in s and "> 0" in s for s in e.value.invalid)

    def test_the_two_declarations_of_the_tier_key_types_agree(self):
        """schema 与 dataclass 两处各说各话,正是「声明成 float」那类漂移的开头。
        这条在 import 期就断言过,这里再明写一遍(⛔ 别把 import 期那句删了)。"""
        P._assert_tier_types_match_the_dataclasses()
        P._assert_int_paths_are_declared_int()

    def test_every_int_slot_that_has_a_range_gate_is_declared_int(self):
        """整数闸的前提:进 `_POSITIVE_INT_PATHS` / `_WINDOW_PATHS` 的路径,
        schema 必须声明 `int` —— 否则 `isinstance(v, int)` 会把它整条跳过。"""
        for path in set(P._POSITIVE_INT_PATHS) | set(P._WINDOW_PATHS):
            assert P._schema_type_at(path) is int, path


class TestThresholdsHaveStructuralBounds:
    """🔴 H2:一个写错的标定值曾经会**安静地**跑出一份错清单 —— 系统照跑,
    报告只说「今日无此形态」(K9 §五-5 的诚实缺席),看不出是参数写错了。"""

    @pytest.mark.parametrize("path,value", [
        # 放量倍数 ≥ 0:V ≤ 0 → p1 的「≥V」对全体成立、p3 的「<V」对全体不成立
        ("volume.eruptionMultiple", -1.0),
        ("volume.eruptionMultiple", 0),
        # 裁定 13「有实际换手」
        ("channels.p2.strict.minVolMultiple", -1.0),
        # 振幅上限 ≤ 0 → p1 永远召不到票
        ("channels.p1.strict.ampMaxPct", -5.0),
        # 「≈0」的区间宽度 ≤ 0 是空区间
        ("channels.p3.relaxed.flatBand", 0),
        # 最高涨幅 − 收盘涨幅 ≥ 0 恒成立 → 门槛 ≤ 0 让第 9 条退化成只看一半
        ("boundary.spikeFadeGapPct", 0),
    ])
    def test_a_non_positive_threshold_is_invalid(self, tmp_path, db, path, value):
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: value})), db_path=db)
        assert any(path in s for s in e.value.invalid), e.value.invalid

    @pytest.mark.parametrize("value", [0, -0.1, 1.5])
    def test_a_normalised_drop_outside_zero_to_one_is_invalid(self, tmp_path, db, value):
        """归一化跌幅 = −`ret_1d` ÷ 该板跌停幅度 ∈ [−1, 1] —— 跌不过跌停。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"channels.p2.strict.normDropMin": value})),
                   db_path=db)
        assert any("normDropMin" in s for s in e.value.invalid)

    def test_a_normalised_drop_of_exactly_one_is_allowed(self, tmp_path, db):
        """恰好 1 = 「跌到跌停」,是一个有意义的门槛,⛔ 不许连它一起挡掉。"""
        P.load(write(tmp_path, make_raw(**{"channels.p2.strict.normDropMin": 1.0,
                                           "channels.p2.relaxed.normDropMin": 1.0})),
               db_path=db)

    @pytest.mark.parametrize("value", [7, -0.1, 1.01])
    def test_a_lag_rank_gap_outside_the_unit_interval_is_invalid(self, tmp_path, db, value):
        """复审原样的反例:`lagRankGap = 7` 曾经**校验通过**,而百分位差值最大是 1
        —— p4 从此永远召不到票,报告只会说「今日无形态 4」。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"channels.p4.strict.lagRankGap": value})),
                   db_path=db)
        assert any("lagRankGap" in s for s in e.value.invalid), e.value.invalid

    def test_a_lag_rank_gap_of_zero_is_allowed(self, tmp_path, db):
        """0 = 「资金排名不低于涨幅排名」,是一个有意义的取值(闭区间,⛔ 不是开区间)。"""
        P.load(write(tmp_path, make_raw(**{"channels.p4.strict.lagRankGap": 0,
                                           "channels.p4.relaxed.lagRankGap": 0})),
               db_path=db)


class TestExcludedCodes:
    def test_an_unknown_l2_code_is_invalid_when_the_classify_table_is_populated(
        self, tmp_path, db
    ):
        _seed_classify(db)
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"industry.excludedL2Codes": ["999999.SI"]})),
                   db_path=db)
        assert any("999999.SI" in s for s in e.value.invalid)

    @pytest.mark.parametrize("codes", [[], ["801080.SI"]])
    def test_dropping_baijiu_from_the_exclusion_list_is_invalid(self, tmp_path, db, codes):
        """🔴 复审 M4:`excludedL2Codes = []` 曾经**校验通过** —— 一份参数包可以
        安静地把白酒 19 只(§4.8)放回池子。K9 §二 第 2 条是给定项,不是开关。"""
        _seed_classify(db)
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"industry.excludedL2Codes": codes})),
                   db_path=db)
        assert any("801125.SI" in s and "给定" in s for s in e.value.invalid), e.value.invalid

    def test_baijiu_is_required_even_when_the_classify_table_is_empty(self, tmp_path, db):
        """⚠ 这一条核的是「参数包里有没有这个码」,与分类表拉没拉过无关。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{"industry.excludedL2Codes": []})), db_path=db)
        assert any("801125.SI" in s for s in e.value.invalid)

    def test_a_renamed_baijiu_only_warns(self, tmp_path, db, caplog):
        """§12 坑 6:**名称会变、代码不变**。名称不符只告警不阻断。"""
        import logging
        _seed_classify(db, baijiu_name="白酒")
        with caplog.at_level(logging.WARNING):
            P.load(write(tmp_path, make_raw()), db_path=db)     # ⛔ 不抛
        assert any("801125.SI" in r.getMessage() and "白酒Ⅱ" in r.getMessage()
                   for r in caplog.records)

    def test_an_empty_classify_table_skips_the_check_instead_of_blaming_the_params(
        self, tmp_path, db
    ):
        """⛔ 不把「没拉过分类表」误报成「参数写错了」—— 那是**数据缺口**,
        归事实层的完整性判定(「今天没跑成」的另一个来源)。"""
        P.load(write(tmp_path, make_raw()), db_path=db)


def _seed_classify(db, *, baijiu_name: str = "白酒Ⅱ") -> None:
    from neckline.db import connection
    with connection(db) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO sw_industry_classify "
            "(index_code,name,level,parent_code,src,fetched_at) VALUES (?,?,?,?,?,?)",
            [("801125.SI", baijiu_name, "L2", "340000", "SW2021", "now"),
             ("801080.SI", "半导体", "L2", "270000", "SW2021", "now")],
        )


def _dig(raw, path):
    cur = raw
    for part in path.split("."):
        cur = cur[part]
    return cur


# ══════════════════════════════════════════════════════════════════════════
# ④ 🔴 三个「取值待标定」的参数位:全部候选取值都实现,⛔ 无默认分支
# ══════════════════════════════════════════════════════════════════════════

class TestEveryCandidateValueIsImplemented:
    """§8.3 #18–#20 / §7.6 / G22。

    「降为参数位」⛔ **不等于**「可以先挑一个用」:施工侧把**全部候选取值都实现**,
    每种一条夹具;标定阶段只挑一个填进参数包。"""

    def test_the_three_enums_have_exactly_the_declared_members(self):
        assert [m.value for m in P.HeatAbsentPolicy] == ["renormalize", "zero", "drop"]
        assert [m.value for m in P.RelaySource] == ["recalled", "shortlisted"]
        assert [m.value for m in P.RelayScoring] == ["binary", "count"]

    @pytest.mark.parametrize("value", ["renormalize", "zero", "drop"])
    def test_every_heat_absent_policy_loads(self, tmp_path, db, value):
        p = P.load(write(tmp_path, make_raw(**{"industry.heatAbsentPolicy": value})),
                   db_path=db)
        assert p.industry.heat_absent_policy.value == value

    @pytest.mark.parametrize("source", ["recalled", "shortlisted"])
    @pytest.mark.parametrize("scoring", ["binary", "count"])
    def test_all_four_relay_combinations_load(self, tmp_path, db, source, scoring):
        p = P.load(
            write(tmp_path, make_raw(**{"ranking.relaySource": source,
                                        "ranking.relayScoring": scoring})),
            db_path=db)
        assert (p.ranking.relay_source.value, p.ranking.relay_scoring.value) == (source, scoring)

    @pytest.mark.parametrize("path,bad", [
        ("industry.heatAbsentPolicy", "default"),
        ("industry.heatAbsentPolicy", P.TO_BE_CALIBRATED),
        ("ranking.relaySource", "seated"),
        ("ranking.relayScoring", "weighted"),
    ])
    def test_a_value_outside_the_enum_is_invalid_not_silently_defaulted(
        self, tmp_path, db, path, bad
    ):
        """🔴 取值不在枚举里 = `invalid`,**不是「退回某个默认」**。"""
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(write(tmp_path, make_raw(**{path: bad})), db_path=db)
        assert any(path in s and "候选取值" in s for s in e.value.invalid), e.value.invalid

    def test_the_slot_registry_covers_all_three(self):
        assert set(P.ENUM_PARAM_SLOTS) == {
            "industry.heatAbsentPolicy", "ranking.relaySource", "ranking.relayScoring"}


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 示例配置:⛔ 一个真数字都没有
# ══════════════════════════════════════════════════════════════════════════

EXAMPLE = _ROOT / "config" / "k9-params.example.json"


class TestExampleConfig:
    def test_it_exists(self):
        assert EXAMPLE.exists()

    def test_only_fixed_structure_values_and_placeholders_exist(self):
        """B17：正文固定值逐字展示；真正待标定的位置仍必须是占位符。"""
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        offenders = []
        allowed = dict(P.K9_FIXED_VALUES)

        def walk(node, prefix=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(node, list):
                if prefix != "industry.excludedL2Codes":
                    offenders.append(prefix)
            elif node != P.TO_BE_CALIBRATED:
                if prefix not in allowed or allowed[prefix] != node:
                    offenders.append(f"{prefix}={node!r}")

        walk(doc)
        assert offenders == [], f"示例配置里出现了真值:{offenders}"
        for path, value in allowed.items():
            node = doc
            for key in path.split("."):
                node = node[key]
            assert node == value

    def test_the_three_calibrated_value_slots_are_placeholders(self):
        """G22 的一半:三个「取值待标定」的键在示例里⛔ 不许填一个真取值(§7.6)。"""
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        assert doc["industry"]["heatAbsentPolicy"] == P.TO_BE_CALIBRATED
        assert doc["ranking"]["relaySource"] == P.TO_BE_CALIBRATED
        assert doc["ranking"]["relayScoring"] == P.TO_BE_CALIBRATED

    def test_its_shape_matches_required_schema_exactly(self):
        """示例配置与 `REQUIRED_SCHEMA` 必须**结构逐键相同** —— 一边加键另一边忘了跟,
        用户照着示例填出来的包会当场判「参数未配置」。"""
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        missing, _invalid, extras = P.validate(doc)
        assert missing == [], f"示例配置缺键:{missing}"
        assert extras == [], f"示例配置多键:{extras}"

    def test_loading_the_example_is_refused_loudly(self, tmp_path, db):
        """🔴 示例**不是一份能用的参数包** —— 它必须加载失败,而且失败得很吵。
        ⛔ 绝不能出现「示例居然跑起来了」这种事(那等于给了一组默认值)。"""
        target = tmp_path / "k9-params.example.json"
        target.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        with pytest.raises(P.ParamsUnavailable) as e:
            P.load(target, db_path=db)
        assert len(e.value.invalid) >= 10
        assert any("factPackVersion" in s for s in e.value.invalid)
        assert any("heatAbsentPolicy" in s for s in e.value.invalid)


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 多余键:告警不阻断(⚠ 登记见 params.py 模块 docstring 第 5 条)
# ══════════════════════════════════════════════════════════════════════════

def test_an_unknown_key_warns_but_still_loads(tmp_path, db, caplog):
    import logging
    raw = make_raw()
    raw["channels"]["p3"]["strict"]["someUndeclaredKnob"] = 1.5
    with caplog.at_level(logging.WARNING):
        P.load(write(tmp_path, raw), db_path=db)
    assert any("未声明的键" in r.getMessage() for r in caplog.records)


def test_validate_reports_extras_separately_from_missing_and_invalid():
    raw = make_raw()
    raw["somethingNew"] = 1
    missing, invalid, extras = P.validate(raw)
    assert missing == [] and invalid == []
    assert extras == ["somethingNew"]
