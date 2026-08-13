"""V2.1-⑤ 校准移交件(`neckline/review/handoff.py`)。

**四条要点,逐条对应 plan §五⑤ 的一句硬要求**

1. **五节渲染**:窗口与样本量 / 校准报告原文 / 画像两表 / 观察项清单 / 免责;
2. **观察项 id 与 §七 Backlog 闭合** —— 每个 id 必须能在 `PROJECT_PLAN.md` §七 里
   grep 到字面 `[P3-xx]`。这条比"人工记得同步"可靠:Backlog 那条被删 / 改 ID,清单
   当场报红,而不是默默端给用户一条已经不存在的观察项;
3. **产物缺失时 `available=false`,且「没生成」与「读不出」文案分开** —— 前者会自愈
   (等下一次周度作业),后者**不会**;混成一句就是叫人一直等一份永远好不了的产物
   (承 V2 B1 `card_corrupt` vs `card_not_ready` 的同一条裁定);
4. **⛔ 零在线补算**:`handoff.py` 全文零 `build_report`(静态);端点侧的运行期证明
   在 `tests/test_api_review.py`。

⚠ 本文件全部用 `tmp_path` 造产物目录、**显式传 `out_dir`** —— CLAUDE.md「测试隔离」条:
`isolated_env`/`api_env` 都不重写 `neckline.config.settings`,不显式传就会读到真实项目的
`data/reports/`(那类泄漏"断言全错还不报错")。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from neckline.review import handoff as ho

_ROOT = Path(__file__).resolve().parent.parent
_PLAN = _ROOT.parent / "PROJECT_PLAN.md"


# ══════════════════════════════════════════════════════════════════════════
# 夹具:一份形状与 `eval/calibration.py::write_report` 落盘件相同的产物
# ══════════════════════════════════════════════════════════════════════════

def _report_dict(date_from="20260803", date_to="20260807") -> dict:
    return {
        "specVersion": "weekly_calibration_v1",
        "dateFrom": date_from, "dateTo": date_to, "generatedAt": "20260808",
        "nTradingDays": 5, "nBaskets": 12,
        "strata": [
            {"packVersion": "K7-pack-v1", "rulesetVersion": "vr-1",
             "nDays": 5, "nBaskets": 12,
             "tierMonotonicity": {"counts": {"1": 4, "2": 8}, "monotonic": True},
             "tierVerdict": {"text": "样本 5 天,给数不给结论"}},
        ],
        "placebo": [], "honesty": {"baskets": 12}, "notes": [],
        "disclaimer": "本报告是回看审计。",
    }


def _write_artifact(d: Path, date_from="20260803", date_to="20260807",
                    *, md=True, json_text=None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    stem = f"calibration_{date_from}_{date_to}"
    (d / f"{stem}.json").write_text(
        json_text if json_text is not None
        else json.dumps(_report_dict(date_from, date_to), ensure_ascii=False),
        encoding="utf-8")
    if md:
        (d / f"{stem}.md").write_text(
            f"# 周度校准报告 · {date_from} → {date_to}\n\n## §1 分层成绩单\n\n(原文)\n",
            encoding="utf-8")


_PREF = [
    {"dimension": "theme", "value": "固态电池", "share": 0.4, "sampleN": 8,
     "windowStart": "20260510", "windowEnd": "20260807", "confidence": "medium"},
    {"dimension": "role", "value": "leader", "share": 0.6, "sampleN": 2,
     "windowStart": "20260510", "windowEnd": "20260807", "confidence": "low"},
]
_CAP = [
    {"dimension": "theme", "value": "固态电池", "sampleN": 8, "winRate": 0.5,
     "profitFactor": 1.2, "avgMfe": 0.05, "avgMae": -0.03, "vsPeerDelta": 0.012,
     "windowStart": "20260510", "windowEnd": "20260807", "confidence": "medium"},
    {"dimension": "role", "value": "elastic", "sampleN": 1, "winRate": None,
     "profitFactor": None, "avgMfe": None, "avgMae": None, "vsPeerDelta": None,
     "windowStart": "20260510", "windowEnd": "20260807", "confidence": "low"},
]


# ══════════════════════════════════════════════════════════════════════════
# 产物枚举与三态读
# ══════════════════════════════════════════════════════════════════════════

class TestArtifacts:
    def test_lists_artifacts_newest_first(self, tmp_path):
        _write_artifact(tmp_path, "20260727", "20260731")
        _write_artifact(tmp_path, "20260803", "20260807")
        got = ho.list_calibration_artifacts(tmp_path)
        assert [p.label for p in got] == ["20260803→20260807", "20260727→20260731"]
        assert got[0].markdown_path is not None

    def test_missing_directory_is_an_empty_list_not_a_crash(self, tmp_path):
        """⑥ 还没跑过第一次 = 正常场景,⛔ 不是异常。"""
        assert ho.list_calibration_artifacts(tmp_path / "从未存在") == []

    def test_foreign_files_in_the_directory_are_ignored_not_fatal(self, tmp_path):
        _write_artifact(tmp_path, "20260803", "20260807")
        (tmp_path / "calibration_乱来.json").write_text("{}", encoding="utf-8")
        (tmp_path / "README.md").write_text("hi", encoding="utf-8")
        assert [p.label for p in ho.list_calibration_artifacts(tmp_path)] == ["20260803→20260807"]

    def test_three_states_are_distinguishable(self, tmp_path):
        """🔴 本模块最要紧的一条:**没生成**会自愈、**读不出**不会,⛔ 不许合并。"""
        assert ho.load_calibration_with_status("20260803", "20260807", tmp_path) == (
            None, ho.CAL_NOT_GENERATED)
        _write_artifact(tmp_path, "20260803", "20260807")
        payload, status = ho.load_calibration_with_status("20260803", "20260807", tmp_path)
        assert status == ho.CAL_OK and payload["nBaskets"] == 12
        _write_artifact(tmp_path, "20260727", "20260731", json_text="{坏了")
        assert ho.load_calibration_with_status("20260727", "20260731", tmp_path) == (
            None, ho.CAL_CORRUPT)

    def test_non_object_json_is_corrupt_not_ok(self, tmp_path):
        """`[]` 能解出来但不是一份报告 —— 读得出 ≠ 读到了东西。"""
        _write_artifact(tmp_path, "20260803", "20260807", json_text="[1, 2]")
        assert ho.load_calibration_with_status("20260803", "20260807", tmp_path)[1] == ho.CAL_CORRUPT

    def test_thin_wrapper_keeps_the_plan_signature(self, tmp_path):
        _write_artifact(tmp_path, "20260803", "20260807")
        assert ho.load_calibration("20260803", "20260807", tmp_path)["nBaskets"] == 12
        assert ho.load_calibration("20250101", "20250105", tmp_path) is None


# ══════════════════════════════════════════════════════════════════════════
# 五节渲染
# ══════════════════════════════════════════════════════════════════════════

class TestRenderHandoff:
    def _md(self, **kw):
        base = dict(date_from="20260803", date_to="20260807",
                    calibration=_report_dict(), calibration_status=ho.CAL_OK,
                    calibration_markdown="# 周度校准报告 · 原文\n\n(正文)",
                    preference=_PREF, capability=_CAP,
                    profile_as_of="20260807", generated_at="20260808")
        base.update(kw)
        return ho.render_handoff(**base)

    def test_all_six_sections_are_present_in_order(self):
        """⚠ **V2.2-④ 起是六节**:④「修改建议四分类」插在画像与观察项之间
        (证据 → 建议 → 待过目项 → 免责,读起来才是一条线),观察项与免责各后移一位。"""
        md = self._md()
        heads = ["## ① 窗口与样本量", "## ② 周度校准报告(原文)",
                 "## ③ 用户画像", "## ④ 修改建议四分类", "## ⑤ 观察项清单",
                 "## ⑥ 免责与口径"]
        idx = [md.index(h) for h in heads]
        assert idx == sorted(idx), f"六节顺序错了:{idx}"

    def test_section_one_carries_window_and_every_sample_count(self):
        md = self._md()
        assert "20260803 → 20260807" in md
        assert "5 个交易日 / 12 个篮子 / 1 个分层" in md
        assert "偏好 2 行 / 能力 2 行" in md
        assert "`K7-pack-v1`" in md and "`vr-1`" in md      # 各分层样本量

    def test_section_two_embeds_the_artifact_verbatim(self):
        """⛔ 不重排版:移交件不改写审计件(历史可比性优先)。"""
        md = self._md()
        assert "未重排版" in md
        assert "# 周度校准报告 · 原文" in md and "(正文)" in md

    def test_low_confidence_rows_must_say_the_sentence(self):
        """🔴 `confidence='low'` **必须**当场写「样本不足,不给结论」——
        低置信度的数字旁边不写这一句,它就会被当成结论读(⑫-B 硬要求)。"""
        md = self._md()
        assert md.count("样本不足,不给结论") >= 2          # 偏好 1 行 + 能力 1 行
        assert "| `medium` |" in md or "`medium`" in md

    def test_every_profile_row_carries_sample_window_confidence(self):
        md = self._md()
        for col in ("样本量", "窗口", "置信度"):
            assert col in md
        assert "20260510→20260807" in md

    def test_null_metrics_render_as_dash_not_zero(self):
        """⛔ 「算不出」不用 0 冒充:`vsPeerDelta=null` = 配对样本不足,不是"没有差异"。"""
        md = self._md()
        assert "⛔ 不是「没有差异」" in md
        body = md.split("### ③-2")[1]
        assert "| — |" in body

    def test_two_profiles_are_never_merged_into_one_table(self):
        md = self._md()
        assert "### ③-1 偏好画像" in md and "### ③-2 能力画像" in md
        assert "⛔ 不合并" in md

    def test_missing_artifact_says_not_generated_and_does_not_recompute(self):
        md = self._md(calibration=None, calibration_status=ho.CAL_NOT_GENERATED,
                      calibration_markdown=None)
        assert "尚无周度校准产物" in md
        assert "永不在线补算" in md
        assert "读不出" not in md.split("## ③")[0]          # ⛔ 别把"没生成"说成"读不出"

    def test_corrupt_artifact_says_it_will_not_heal_itself(self):
        """🔴 与上一条互为对照:**两句话必须不一样**。"""
        md = self._md(calibration=None, calibration_status=ho.CAL_CORRUPT,
                      calibration_markdown=None)
        assert "读不出" in md and "需人工排查" in md
        assert "别当成「还没生成」等下去" in md
        assert "尚未生成" not in md

    def test_disclaimer_is_reused_not_rewritten(self):
        from neckline.review.research_artifact import DISCLAIMER

        assert DISCLAIMER in self._md()

    def test_handoff_states_the_manual_loop_is_the_only_channel(self):
        """§五〇 裁定 #3:改包唯一通道是人工门禁闭环,⛔ 系统不做自动反馈回写选股。"""
        md = self._md()
        assert "四道闸激活" in md and "不做任何自动反馈回写选股" in md


# ══════════════════════════════════════════════════════════════════════════
# 观察项清单 ↔ §七 Backlog 闭合
# ══════════════════════════════════════════════════════════════════════════

class TestObservations:
    def test_every_observation_has_the_five_pinned_keys(self):
        for ob in ho.HANDOFF_OBSERVATIONS:
            assert set(ob) == {"id", "title", "question", "evidence_needed", "status"}
            assert all(str(v).strip() for v in ob.values())

    def test_ids_are_unique_and_cover_the_five_the_plan_names(self):
        """⚠ **V2.2-④ 定死五条**(plan ④-D 原文):`P3-33` **摘掉** —— 它的主体随
        门槛制作废,留一条 grep 得到但已经没有意义的观察项比没有更糟;新增
        `P3-49`(位置关前向证伪义务)与 `P3-51`(状态层第五维冷启动缺席)。"""
        ids = [o["id"] for o in ho.HANDOFF_OBSERVATIONS]
        assert len(ids) == len(set(ids))
        assert set(ids) == {"P3-32", "P3-34", "P3-37", "P3-49", "P3-51"}

    def test_p3_33_is_gone_not_left_as_a_dead_entry(self):
        """plan ④-D 原文:⛔ 不许留一条会 grep 到但已经没意义的观察项。"""
        assert "P3-33" not in {o["id"] for o in ho.HANDOFF_OBSERVATIONS}

    def test_ids_are_never_split_into_sub_ids(self):
        """⚠ 拆 id(如写成 `P3-34a`)会让守门 grep 不到 §七 里那条 —— 既有教训。"""
        for ob in ho.HANDOFF_OBSERVATIONS:
            assert ob["id"][-1].isdigit(), f"{ob['id']} 看起来被拆了子号"

    def test_p3_49_states_the_forward_falsification_duty_verbatim(self):
        """🔴 P3-49 是移交件里最该让用户看见的一条:两条义务必须白纸黑字在里面。"""
        ob = next(o for o in ho.HANDOFF_OBSERVATIONS if o["id"] == "P3-49")
        assert "100" in ob["evidence_needed"]                    # 结案样本量门槛
        assert "选股时钟" in ob["evidence_needed"]                # 判据来源写死 = 实盘
        assert "回测" in ob["evidence_needed"]                    # ⛔ 不立回测战役
        assert "无论正负" in ob["status"]                         # 结论都要上报

    def test_p3_34_registers_the_v22_expansion(self):
        """plan ④-D:P3-34 **扩容**含位置关读数口径与三引擎首版阈值(⛔ 不另起第二本账)。"""
        ob = next(o for o in ho.HANDOFF_OBSERVATIONS if o["id"] == "P3-34")
        assert "engineering_v1" in ob["question"] and "platform_days" in ob["question"]
        assert "C2" in ob["status"]

    @pytest.mark.parametrize("ob_id", [o["id"] for o in ho.HANDOFF_OBSERVATIONS])
    def test_each_id_closes_with_the_backlog_in_section_seven(self, ob_id: str):
        """🔴 **清单与 Backlog 漂移当场报红**(plan 点名的守门)。

        判据 = `PROJECT_PLAN.md` §七 里能 grep 到字面 `[P3-xx]`。§七 之外的提及
        (如 §五 施工图正文)**不算** —— 观察项的身份是「Backlog 上一条待办」,
        不是「文档里被提过一次」。"""
        text = _PLAN.read_text(encoding="utf-8")
        start = text.index("\n## 七、Backlog")
        end = text.index("\n## 八、", start)
        section7 = text[start:end]
        assert f"[{ob_id}]" in section7, (
            f"观察项 {ob_id} 在 §七 Backlog 里找不到 —— 要么 Backlog 那条被删/改了 ID"
            f"(该同步本清单),要么这条观察项是凭空发明的(⛔ 不许)")


# ══════════════════════════════════════════════════════════════════════════
# 装配 + ⛔ 零在线补算(静态)
# ══════════════════════════════════════════════════════════════════════════

class TestBuildHandoff:
    def test_defaults_to_the_latest_landed_window(self, tmp_path, isolated_env):
        _write_artifact(tmp_path, "20260727", "20260731")
        _write_artifact(tmp_path, "20260803", "20260807")
        h = ho.build_handoff(out_dir=tmp_path, db_path=isolated_env.db_path)
        assert h.available and (h.window_from, h.window_to) == ("20260803", "20260807")
        assert h.sample_n["tradingDays"] == 5 and h.sample_n["baskets"] == 12
        assert "## ⑤ 观察项清单" in h.markdown

    def test_no_artifact_at_all_is_unavailable_with_a_self_healing_reason(self, tmp_path, isolated_env):
        h = ho.build_handoff(out_dir=tmp_path, db_path=isolated_env.db_path)
        assert h.available is False
        assert "还没跑过第一次" in h.unavailable_reason
        assert "在线路径不补算" in h.unavailable_reason

    def test_corrupt_window_is_unavailable_with_a_different_reason(self, tmp_path, isolated_env):
        _write_artifact(tmp_path, "20260803", "20260807", json_text="{坏了")
        h = ho.build_handoff("20260803", "20260807", out_dir=tmp_path,
                             db_path=isolated_env.db_path)
        assert h.available is False and "读不出" in h.unavailable_reason
        assert "不会自愈" in h.unavailable_reason or "不会自己好" in h.unavailable_reason

    def test_explicit_window_without_artifact_still_exports_the_rest(self, tmp_path, isolated_env):
        """⛔ 没产物 ≠ 什么都不给:画像与观察项照出,§② 如实写"本窗口尚无产物"——
        那仍然是能交给策略台的东西。"""
        h = ho.build_handoff("20260803", "20260807", out_dir=tmp_path,
                             db_path=isolated_env.db_path)
        assert h.available is True
        assert "尚无周度校准产物" in h.markdown and "## ⑤ 观察项清单" in h.markdown

    def test_profile_rows_from_the_isolated_db_land_in_the_document(self, tmp_path, isolated_env):
        from neckline.profile.models import CapabilityRow
        from neckline.profile.preference import PreferenceRow
        from neckline.profile.store import save_capability, save_preference

        save_preference("20260807", [PreferenceRow(
            dimension="theme", value="固态电池", share=0.4, sample_n=2,
            window_start="20260510", window_end="20260807", confidence="low")],
            db_path=isolated_env.db_path)
        save_capability("20260807", [CapabilityRow(
            dimension="theme", value="固态电池", sample_n=2, win_rate=0.5,
            profit_factor=None, avg_mfe=None, avg_mae=None, vs_peer_delta=None,
            window_start="20260510", window_end="20260807", confidence="low", verdict="")],
            db_path=isolated_env.db_path)
        _write_artifact(tmp_path, "20260803", "20260807")
        h = ho.build_handoff(out_dir=tmp_path, db_path=isolated_env.db_path)
        assert h.sample_n["preferenceRows"] == 1 and h.sample_n["capabilityRows"] == 1
        assert "固态电池" in h.markdown
        assert h.markdown.count("样本不足,不给结论") >= 2


def test_handoff_module_never_recomputes_the_calibration_report():
    """🔴 **⛔ 零在线补算**(静态半)——`handoff.py` 全文零 `build_report`。

    理由 = §七 P0-23:两条端点跑在常驻 `neckline.service` 里、与盘中哨兵同进程,
    重活进常驻服务 = `MemoryHigh` 先节流 → 卡死不报错。
    (运行期那一半的证明在 `tests/test_api_review.py`:把 `build_report` 换成会抛的
    桩,两条端点仍 200 —— 静态断言只证明"没写这个名字",运行期才证明"真没走那条路"。)"""
    src = Path(ho.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "build_report" not in called
    # import 面也堵一次:连拿到那个函数的路都不给
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    assert not [m for m in imported if m.endswith("calibration.build_report")]


def test_artifact_naming_matches_the_writer_byte_for_byte():
    """生产读侧与离线写侧通过同一个轻量契约定义文件名。"""
    from neckline.review.research_artifact import ARTIFACT_PREFIX, artifact_stem

    assert ho._ARTIFACT_PREFIX == ARTIFACT_PREFIX == "calibration_"
    assert artifact_stem("20260803", "20260807") == "calibration_20260803_20260807"
