"""V2.3.3 批 ⑥:事后复盘(选股时钟**第十项**)+ 周度机械聚合。

🔴 三条最要紧的在这里被正面钉死:
  1. **六个标签 × 判据**(K8 §二十 逐字),且 `data_missing` **必须带分因** ——
     「竞价层没跑」与「D1 结果本身没判出来」是两种**相反**的成因(§七 P0-39 同款病);
  2. **第十项不属于 §十四 的九项**:`MECH_ITEM_KEYS` 仍然**恰好九个**、键名一字不变,
     `auction_review` 只是 `mech_json` 顶层多出来的第十个键(混进去 = 让「九项」这个
     数字变成一句谎话);
  3. **零新指标 / 零新 LLM / 零自动回写**:「D1 结果对不对」读的是既有 `tier_accuracy`
     四态,周度门槛读的是骨架包里那两个数 —— ⛔ 拍板前一个建议都不提。
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from neckline.db import connection
from neckline.eval import auction_eval as ae
from neckline.eval.iteration import IterationThresholds
from neckline.review import selection_clock as sc

pytestmark = pytest.mark.usefixtures("isolated_env")

_ROOT = Path(__file__).resolve().parent.parent
_D0, _D1 = "20260810", "20260811"


def _insert_verdict(env, *, basket_id=1, verdict="confirm", clamped_by=None,
                    llm_stage="ok") -> None:
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO auction_verdicts (basket_id, trade_date, d0_date, basket_key, name,"
            " covered_tier, engine_code, engine_version, skeleton_version, regime_at_d0,"
            " data_quality, members_json, sector_sync_json, rel_strength_json, history_json,"
            " hit_invalidation_json, plan_consistency_json, verdict, verdict_raw, clamped_by,"
            " reasons_json, llm_fields_json, manual_note_attached, llm_stage,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (basket_id, _D1, _D0, f"k{basket_id}", "篮", 1, "Z", "Z1", "K8-V0.7",
             "trend_continuation", "ok", "[]", "{}", "{}", "{}", "[]", "{}",
             verdict, verdict, clamped_by, "[]", "{}", 0, llm_stage,
             "2026-08-11T01:26:31Z", "2026-08-11T01:27:02Z"),
        )


# ══════════════════════════════════════════════════════════════════════════
# ⑥-A 第十项:六个标签 + data_missing 三种分因
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("verdict,accuracy,label", [
    ("confirm", "verified", sc.AUCTION_LABEL_CORRECT_CONFIRM),
    ("confirm", "falsified", sc.AUCTION_LABEL_WRONG_CONFIRM),
    ("veto", "falsified", sc.AUCTION_LABEL_CORRECT_VETO),
    ("veto", "verified", sc.AUCTION_LABEL_WRONG_VETO),
    # 🔴 中性结论**无对错**,不论 D1 结果(K8 §二十 逐字)
    ("neutral", "verified", sc.AUCTION_LABEL_NEUTRAL_SAMPLE),
    ("neutral", "falsified", sc.AUCTION_LABEL_NEUTRAL_SAMPLE),
    ("neutral", "unclear", sc.AUCTION_LABEL_NEUTRAL_SAMPLE),
])
def test_six_labels_match_k8(isolated_env, verdict, accuracy, label):
    _insert_verdict(isolated_env, verdict=verdict)
    item = sc.judge_auction_review(1, accuracy, db_path=isolated_env.db_path)
    assert item["label"] == label
    assert item["source_note"] == "K8.md §二十"
    assert item["not_one_of_the_nine"] is True


def test_all_six_labels_are_reachable(isolated_env):
    """六个标签**一个都不许是死码**(K8 §二十 逐字六个)。"""
    reached = set()
    for i, (v, acc) in enumerate([("confirm", "verified"), ("confirm", "falsified"),
                                  ("veto", "falsified"), ("veto", "verified"),
                                  ("neutral", "partial")], start=1):
        _insert_verdict(isolated_env, basket_id=i, verdict=v)
        reached.add(sc.judge_auction_review(i, acc, db_path=isolated_env.db_path)["label"])
    # 第六个:没有竞价行
    reached.add(sc.judge_auction_review(999, "verified",
                                        db_path=isolated_env.db_path)["label"])
    assert reached == set(sc.AUCTION_LABELS)


@pytest.mark.parametrize("setup,accuracy,reason", [
    (None, "verified", sc.AUCTION_UNDETERMINED_NO_ROW),
    ("pending_explanation", "verified", sc.AUCTION_UNDETERMINED_PENDING),
    ("confirm", "partial", sc.AUCTION_UNDETERMINED_D1_UNCLEAR),
    ("confirm", "unclear", sc.AUCTION_UNDETERMINED_D1_UNCLEAR),
    ("confirm", None, sc.AUCTION_UNDETERMINED_D1_UNCLEAR),
    ("confirm", "not_evaluated", sc.AUCTION_UNDETERMINED_D1_UNCLEAR),
])
def test_data_missing_always_carries_its_cause(isolated_env, setup, accuracy, reason):
    """🔴 §七 P0-39 同款病:「竞价层没跑」「跑了但没解释」「D1 结果没判出来」是**三种
    相反的成因** —— ⛔ 不许混成一个 `data_missing` 了事。"""
    if setup is not None:
        _insert_verdict(isolated_env, verdict=setup)
    item = sc.judge_auction_review(1, accuracy, db_path=isolated_env.db_path)
    assert item["label"] == sc.AUCTION_LABEL_DATA_MISSING
    assert item["undetermined_reason"] == reason
    assert item["available"] is False and item["unavailable_reason"]


def test_reads_but_never_writes_the_auction_tables(isolated_env):
    """🔴 **三段互不回写**:第十项只读 `auction_verdicts`,⛔ 不改它一个字。"""
    _insert_verdict(isolated_env, verdict="confirm", clamped_by="clamped_by_single_strong")

    def _snapshot():
        with connection(isolated_env.db_path) as conn:
            return conn.execute("SELECT * FROM auction_verdicts").fetchall()

    before = _snapshot()
    for _ in range(3):
        sc.judge_auction_review(1, "verified", db_path=isolated_env.db_path)
    assert _snapshot() == before


def test_the_tenth_key_is_not_one_of_the_nine():
    """🔴 `MECH_ITEM_KEYS` 仍然**恰好九个**、键名一字不变 —— 第十项是 K8 §二十 的东西,
    混进 §十四 的清单就会让「九项」这个数字变成一句谎话。"""
    assert len(sc.MECH_ITEM_KEYS) == 9
    assert "auction_review" not in sc.MECH_ITEM_KEYS
    assert sc.MECH_ITEM_KEYS == (
        "regime_at_d0", "driver_persistence", "sector_sync", "core_strength",
        "entry_zone_triggered", "liftoff_signal", "intraday_support_and_close",
        "untriggered_reason", "tier_accuracy",
    )


def test_clock_mech_spec_version_was_bumped():
    """形状变了就 bump(模块头写死的纪律)。"""
    assert sc.CLOCK_MECH_SPEC_VERSION == "selection_clock_mech_v2"


def test_no_new_scoring_logic_in_the_tenth_item():
    """🔴 **零新指标**:第十项只读既有 `tier_accuracy` 四态 —— 源码里不许出现第二套
    「D1 结果好不好」的判分(⛔ 不许自己定义 verified/falsified 之外的对错线)。"""
    src = (_ROOT / "neckline" / "review" / "selection_clock.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "judge_auction_review")
    # ⚠ **先剥掉 docstring 再扫**:注释里提到 `STATE_SCORES` 是在说"我们用的是那份既有
    # 换算",裸文本匹配会把说明当成违规(守门自己制造误报 = 下次真违规也没人信)。
    if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    assert "STATE_VERIFIED" in body and "STATE_FALSIFIED" in body
    for banned in ("STATE_SCORES", "0.5", "accuracy_of("):
        assert banned not in body, f"第十项里出现了 {banned} —— 那是在新造判分口径"


def test_tenth_item_has_its_own_fuse(isolated_env, monkeypatch):
    """保险丝:第十项算炸了只让**这一项** error,九项照出。"""
    import neckline.auction.store as astore

    def _boom(*a, **kw):
        raise RuntimeError("库炸了")

    monkeypatch.setattr(astore, "load_verdict_for_basket", _boom)
    item = sc.judge_auction_review(1, "verified", db_path=isolated_env.db_path)
    assert item["available"] is False
    assert item["label"] == sc.AUCTION_LABEL_DATA_MISSING
    assert "RuntimeError" in item["error"]


# ══════════════════════════════════════════════════════════════════════════
# ⑥-B 周度机械聚合
# ══════════════════════════════════════════════════════════════════════════

def _closure(label, *, regime="trend_continuation", tier=1, engine=("Z", "Z1"),
             reason=None, with_item=True):
    mech = {}
    if with_item:
        mech["auction_review"] = {"label": label, "undetermined_reason": reason,
                                  "source_note": "K8.md §二十"}
    return {"mech": mech, "regime_at_d0": regime, "covered_tier": tier,
            "skeleton_version": "K8-V0.7", "verification_ruleset_version": "vr-1",
            "engine_breakdown": {"engine_code": engine[0], "engine_version": engine[1]}}


def test_cross_table_is_the_k8_dimensions_including_the_skeleton_version():
    """K8 §二十 末段:「周度按**行情状态、T 等级、引擎和版本**聚合」。

    🔴 **骨架版本也是一维**(复审 🟡-4):升 `K8-V0.6` → `K8-V0.7` 的唯一理由就是
    「竞价层上线前后的样本必须分得开」(施工图 ⑦-2)—— 丢了这一维,那次升版本白升。
    """
    rep = ae.build_auction_report([
        _closure("correct_confirm"),
        _closure("wrong_confirm", regime="high_divergence"),
        _closure("neutral_sample", tier=2),
        _closure("correct_veto", engine=("Y", "Y1")),
    ])
    assert rep["cellKey"] == ["regime", "tier", "skeletonVersion",
                              "engineCode", "engineVersion"]
    assert len(rep["byCell"]) == 4, "四条样本各自落在不同的单元格"
    assert rep["overall"]["n"] == 4
    assert rep["overall"]["counts"]["correct_confirm"] == 1
    # 重点复核那两类单独拎出来(⛔ 别埋在六个计数里)
    assert rep["overall"]["focus"] == {"wrong_confirm": 1, "wrong_veto": 0}


def test_two_skeleton_versions_never_share_a_cell():
    """🔴 复审 🟡-4 的**失败场景**正面钉死:`K8-V0.6`(没有竞价层)与 `K8-V0.7`
    (有竞价层)的**同一** `(行情状态, T1, Z, Z1)` 样本 ⛔ 不许落进同一个单元格 ——
    否则共用一个 `n`、共用一条 30/80 样本量闸,「保留 / 降权 / 淘汰」的分母是两个
    系统的混合。"""
    old = _closure("correct_confirm")
    old["skeleton_version"] = "K8-V0.6"
    rep = ae.build_auction_report([old, _closure("correct_confirm")])
    assert len(rep["byCell"]) == 2, "两个骨架版本被混成了一层"
    assert {r["skeletonVersion"] for r in rep["byCell"]} == {"K8-V0.6", "K8-V0.7"}
    assert [r["n"] for r in rep["byCell"]] == [1, 1]
    # markdown 表格里也得看得见那一列(⛔ 别只在 overall 里补)
    md = "\n".join(ae.render_auction_section(rep))
    assert "骨架" in md and "K8-V0.6" in md and "K8-V0.7" in md


def test_shares_denominator_includes_data_missing():
    """占比分母是**该层全部样本**(含缺失)—— 把缺失剔出分母会让「我们看见了多少」
    看起来比实际大(§3.8)。"""
    rep = ae.build_auction_report([_closure("correct_confirm"),
                                   _closure("data_missing", reason="no_auction_row")])
    assert rep["overall"]["shares"]["correct_confirm"] == 0.5
    assert rep["overall"]["undeterminedReasons"] == {"no_auction_row": 1}


def test_empty_input_gives_no_fake_zero_shares():
    rep = ae.build_auction_report([])
    assert rep["overall"]["n"] == 0
    assert rep["overall"]["shares"] == {}, "⛔ 不发一堆 0.0 冒充「都是零」"


def test_old_closures_without_the_item_are_counted_separately():
    """V2.3.3 之前冻的结案件**连这一项都没有** —— 它不是「竞价层当天没跑」,
    ⛔ 不许混进那一类。"""
    rep = ae.build_auction_report([_closure("", with_item=False), _closure("correct_confirm")])
    assert rep["withoutAuctionItem"] == 1
    assert rep["overall"]["counts"]["data_missing"] == 1   # 标签上仍归缺失(如实)
    assert "还没有竞价确认层" in rep["withoutAuctionItemNote"]


@pytest.mark.parametrize("n,gate", [
    (29, ae.GATE_OBSERVE_ONLY), (30, ae.GATE_MAY_ADJUST),
    (79, ae.GATE_MAY_ADJUST), (80, ae.GATE_MAY_RETIRE),
])
def test_sample_gates_read_the_pack_thresholds_not_a_new_number(n, gate):
    """🔴 **零新阈值**:30 / 80 全部来自骨架包 `config.iteration`(唯一源)。"""
    th = IterationThresholds(min_n=30, retire_min_n=80)
    assert ae.gate_of(n, th) == gate


def test_before_the_thresholds_are_decided_nothing_is_suggested():
    """🔴 拍板前一律 `thresholds_undecided` —— ⛔ 不许"临时用 30/80 顶一下"
    (「定性需求不许自行定量」)。"""
    rep = ae.build_auction_report([_closure("correct_confirm")], thresholds=None)
    assert rep["overall"]["gate"] == ae.GATE_UNDECIDED
    assert rep["thresholds"]["available"] is False
    assert "不设默认值" in rep["thresholds"]["unavailableReason"]


def test_legacy_engine_fallback_is_shared_with_iteration():
    """引擎两维复用 `stratum_of()` —— 老样本落 `LEGACY` 的退回逻辑与迭代段逐字一致。"""
    key = ae.cell_key_of(_closure("correct_confirm", engine=(None, None)))
    assert key[3] == "LEGACY" and key[4] == "LEGACY"
    # D0 当天 `market_regime_daily` 缺行 → 哨兵串,**不是**第四种行情状态
    assert ae.cell_key_of(_closure("correct_confirm", regime=None))[0] == ae.REGIME_UNKNOWN


def test_the_tier_dimension_has_its_own_sentinel_not_the_engine_one():
    """🔵-13:等级缺失时的哨兵是 `T?`,⛔ 不借用引擎维的 `LEGACY` ——
    引擎维的串跑到等级维上,读表的人只会以为那一格在讲引擎。"""
    key = ae.cell_key_of(_closure("correct_confirm", tier=None))
    assert key[1] == ae.TIER_UNKNOWN == "T?"
    assert key[1] != "LEGACY"


def test_weekly_aggregation_is_read_only_and_llm_free():
    """🔴 **零 LLM、零写库、零自动回写**(K8 §二十 末段 + V2.1 裁定 #3)。"""
    src = (_ROOT / "neckline" / "eval" / "auction_eval.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    for c in calls:
        name = getattr(c.func, "attr", None)
        assert name not in ("chat", "get_provider"), "周度聚合里出现了 LLM 调用"
    for banned in ("INSERT", "UPDATE", "DELETE", "save_", "activate_"):
        assert banned not in src, f"周度聚合里出现了写路径:{banned}"


def test_report_carries_the_sample_unit_and_disclaimer():
    rep = ae.build_auction_report([])
    assert "D0 日期 × 篮子 × 引擎版本" in rep["sampleUnit"]
    assert "互不回写" in rep["disclaimer"]
    assert "不改 K8" in rep["disclaimer"]
    assert list(rep["labels"]) == list(sc.AUCTION_LABELS)


def test_markdown_section_renders_and_says_when_absent():
    lines = ae.render_auction_section(None)
    assert any("没有产出" in ln for ln in lines)
    rep = ae.build_auction_report([_closure("wrong_veto")])
    lines = ae.render_auction_section(rep)
    assert any("竞价确认层复盘" in ln for ln in lines)
    assert any("错误否决 1" in ln for ln in lines)


def test_weekly_script_has_step_five_and_its_skip_flag():
    """⑥-B 接线:`scripts/weekly.py` 的步 5 + `--skip-auction-eval`。"""
    src = (_ROOT / "scripts" / "weekly.py").read_text(encoding="utf-8")
    assert "def step_auction_eval(" in src
    assert "--skip-auction-eval" in src
    assert "failures.append(\"auction_eval\")" in src
    body = src.split("def step_auction_eval(", 1)[1].split("\ndef ", 1)[0]
    assert "get_provider" not in body and ".chat(" not in body, "步 5 必须零 LLM"


def test_calibration_report_carries_the_auction_segment():
    """落点:并进 `eval/calibration` 的产物(**不新建第三张表、不新建端点**)。"""
    from neckline.eval.calibration import CalibrationReport

    rep = CalibrationReport(spec_version="x", date_from="20260810", date_to="20260814",
                            generated_at="20260815", n_trading_days=5, n_baskets=0)
    assert "auction" in rep.to_dict()
    src = (_ROOT / "neckline" / "eval" / "calibration.py").read_text(encoding="utf-8")
    assert "auction_eval" in src
    assert "CREATE TABLE" not in src
