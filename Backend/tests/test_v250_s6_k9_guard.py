"""V2.5.0 S6 策略层的**结构性**守门(PROJECT_PLAN §10 G4/G10/G11/G12 + 裁定 15)。

| # | 断言 |
|---|---|
| G4 | `k9/channels/pN_*.py` 相互零 import,且不 import `ranking` / `quota` / `run`;四个通道签名逐字相同 |
| G11 | 全仓(Python + Swift + md,排除 `archive/`)`上方空间` 零命中;`k9/**` 内 `first_resistance` 零命中 |
| G12 | 「N 日最高」只有一处实现;p1 **正向** / p3 **反向**共用它 |
| 裁定 15 | 「当日量 ÷ N 日均量」只有一处实现;p1 与 p3 的门槛读**同一个键** |
| 零 LLM | AST(在 `test_v250_s5_params_guard.py`)+ **运行时**:把 LLM 工厂 monkeypatch 成「一调就抛」,全链照样跑通 |

⚠ **G2 / G3 不在本文件**:S5 已经把 `k9/**` 的两条 import 边界立在
`test_v250_s5_params_guard.py` 里,而那两条扫的是 `k9/**` 全包 —— 本片新增的模块
自动被覆盖。⛔ 不另起一份重复的扫描(两份判据迟早各说各话)。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Dict, List, Set

import pytest

from neckline.k9 import contract as contract_mod
from neckline.k9 import store as k9_store
from neckline.k9 import upside_room as upside_room_mod
from neckline.k9 import volume as volume_mod
from neckline.k9.channels import p1_breakout, p2_rebound, p3_riser, p4_moneyflow
from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_K9 = _PKG / "k9"
_CHANNEL_FILES = sorted((_K9 / "channels").glob("p?_*.py"))
_REPO = _ROOT.parent


def _imports(path: Path) -> Set[str]:
    """🔴 扫描器走 `tests/guard_scan.py`(S15 收敛)。

    本文件原来抄了一份跳过相对 import 的 `_imports` —— 复审实测 `from . import
    p2_rebound`(CE5)当场穿过 G4「通道互不知道」。
    """
    return guard_scan.imports(path)


def test_scan_covers_all_four_channels():
    """扫描器自检:一个扫不到东西的闸门等于没有闸门。"""
    assert [p.name for p in _CHANNEL_FILES] == [
        "p1_breakout.py", "p2_rebound.py", "p3_riser.py", "p4_moneyflow.py"]


# ══════════════════════════════════════════════════════════════════════════
# G4 通道之间互不知道(架构 §二 边界②)
# ══════════════════════════════════════════════════════════════════════════

def test_channels_never_import_each_other():
    hits: List[str] = []
    for path in _CHANNEL_FILES:
        others = {p.stem for p in _CHANNEL_FILES} - {path.stem}
        for mod in sorted(_imports(path)):
            if any(o in mod for o in others):
                hits.append(f"{path.name} → {mod}")
    assert hits == [], "召回通道之间互不知道 —— 这条边界被破了:\n" + "\n".join(hits)


@pytest.mark.parametrize("forbidden", ["neckline.k9.ranking", "neckline.k9.quota",
                                       "neckline.k9.run", "neckline.k9.store"])
def test_channels_never_see_ranking_quota_or_the_orchestrator(forbidden):
    """通道拿不到别人的产物,也看不见名次与席位。"""
    hits = [
        f"{p.name} → {forbidden}"
        for p in _CHANNEL_FILES
        if any(m == forbidden or m.startswith(forbidden + ".") for m in _imports(p))
    ]
    assert hits == [], "\n".join(hits)


def test_all_four_channels_share_one_signature():
    """签名固定 `run(pack, params) -> list[ChannelHit]`(§5.2 边界② 第 2 条)。"""
    sigs = {
        mod.__name__: list(inspect.signature(mod.run).parameters)
        for mod in (p1_breakout, p2_rebound, p3_riser, p4_moneyflow)
    }
    assert set(sigs.values().__iter__().__next__()) == {"pack", "params"}
    assert len({tuple(v) for v in sigs.values()}) == 1, sigs


def test_run_is_the_only_place_that_sees_all_four_channels():
    """🔴 `k9/run.py` 是**唯一**同时看见四个产物的地方。"""
    offenders: List[str] = []
    for path in sorted(_PKG.rglob("*.py")):
        if path.name == "run.py" and path.parent == _K9:
            continue
        mods = _imports(path)
        seen = {m for m in mods if ".channels." in m or m.endswith(".channels")}
        if len({m for m in seen if any(s in m for s in ("p1_", "p2_", "p3_", "p4_"))}) > 1:
            offenders.append(f"{path.relative_to(_ROOT)} → {sorted(seen)}")
    assert offenders == [], "\n".join(offenders)


# ══════════════════════════════════════════════════════════════════════════
# G11 命名分离(裁定 1):上方机械空间 ≠ 第一压力位
# ══════════════════════════════════════════════════════════════════════════

#: 允许出现含混旧名的**两个**文件,各有明确理由(⚠ 与 §6 S6 验收「全仓零命中」
#: 的偏差,已登记 §14):
#:   · `PROJECT_PLAN.md` —— §5.1 的措辞修订表逐字引用了**改之前**的原文,那正是
#:     它作为施工指令的职责;把引文也改掉,那张表就没法核对了;
#:   · 本文件 —— 要搜一个字符串,总得先写出它。
_NAME_SCAN_ALLOW = {"PROJECT_PLAN.md", Path(__file__).name}

#: ⛔ **二进制**(唯一的排除面)。⚠ 这不是「要扫哪些」的清单,是「扫不动哪些」的清单。
_BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".pdf", ".thumbnail", ".zip", ".ico")


def _repo_files() -> List[Path]:
    """扫描域 = **git 追踪的每一个文本文件**(减去 `archive/` 与二进制)。

    🔴 **2026-08-21 第二轮自查:从「后缀白名单」反过来写。** 上一版是
    `_TEXT_SUFFIXES = (".py", ".swift", ".md", ".json", ".sh")` —— 一张「要扫哪些」
    的清单,而复审 CE15 的全部花招就是**挑一个没人列进去的后缀**(把「上方空间」
    种进 `App/project.yml`,全绿)。第一轮修复的做法是把清单从 5 类补到 16 类 ——
    那仍然是同一种判据,只是这次多想到了十一种。

    现在反过来:**被 git 追踪的一切都在域里**,只有二进制被排除。裁定 1 说的是
    「含混旧名**全仓**零命中」,判据的形状终于和这句话对上了 —— 明天有人加一种
    新文件类型,它自动进域,⛔ 不需要谁记得回来改这张表。

    ⚠ 走 `git ls-files` 而不是 `rglob`:`Backend/data/` 下有 9500 个 parquet(未追踪),
    走文件系统会把它们全读一遍。取不到 git 时退回文件系统遍历并显式排除数据目录 ——
    ⛔ 但域**不许因此变小到扫不出东西**,那由 `test_the_repo_scan_actually_reads_files`
    看着。
    """
    import subprocess  # noqa: PLC0415

    rel: List[str] = []
    try:
        out = subprocess.run(["git", "ls-files"], cwd=_REPO,
                             capture_output=True, check=True)
        rel = [line for line in out.stdout.decode("utf-8").splitlines() if line]
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        for path in _REPO.rglob("*"):
            if not path.is_file():
                continue
            parts = set(path.parts)
            if parts & {".git", ".venv", "__pycache__", "build", "DerivedData",
                        ".build", ".pytest_cache", "data"}:
                continue
            rel.append(str(path.relative_to(_REPO)))
    out_paths: List[Path] = []
    for name in rel:
        if name.startswith("archive/") or name.lower().endswith(_BINARY_SUFFIXES):
            continue
        path = _REPO / name
        if path.is_file():
            out_paths.append(path)
    return out_paths


def test_the_repo_scan_actually_reads_files():
    """扫描器自检:排除清单不能把整个仓库排空。"""
    assert len(_repo_files()) > 100


def test_the_repo_scan_is_not_an_extension_allowlist_any_more():
    """扫描域自检:**CE15 那一类「挑一个没人列进去的后缀」的花招不许再成立**。

    ⛔ 不许只断言「文件总数够多」—— 那条断言在 `.yml` 漏掉的时候也是绿的
    (Python 文件本来就上百个)。这里断言的是「域里有多少**种**文件」。
    """
    files = _repo_files()
    names = {p.name for p in files}
    assert "project.yml" in names, "工程配置不在扫描域里(CE15 就是从这儿绕过去的)"
    suffixes = {p.suffix for p in files}
    for wanted in (".yml", ".service", ".target", ".timer", ".entitlements",
                   ".pbxproj", ".plist", ".toml", ".conf", ".xcscheme", ".example"):
        assert wanted in suffixes, f"{wanted} 不在扫描域里"
    assert len(suffixes) >= 15, (
        f"域里只有 {len(suffixes)} 种后缀 —— 它又退回成一张后缀清单了?{sorted(suffixes)}")
    # ⛔ 二进制确实被挡在外面(不然读文件会满屏乱码,扫描器迟早被人加 try/except 吞掉)。
    assert not (suffixes & set(_BINARY_SUFFIXES))


def test_the_old_ambiguous_name_is_gone_from_the_whole_repo():
    """🔴 裁定 1:排序用的**上方机械空间**与预案用的**第一压力位**是两个量,
    名字分开、永不互相顶替。含混的旧名全仓零命中(`archive/` 与 `_NAME_SCAN_ALLOW` 除外)。"""
    needle = "上方" + "空间"
    hits = [
        str(p.relative_to(_REPO))
        for p in _repo_files()
        if p.name not in _NAME_SCAN_ALLOW
        and needle in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == [], "含混旧名还在:\n" + "\n".join(sorted(set(hits)))


def test_the_playbook_price_level_never_leaks_into_the_strategy_layer():
    """`first_resistance` 是 K9 第四层的 LLM 产物。它出现在 `k9/**` 里 =
    循环依赖复活的第一步(§5.2 的工程决定)。"""
    hits = [
        str(p.relative_to(_ROOT))
        for p in sorted(_K9.rglob("*.py"))
        if "first_resistance" in p.read_text(encoding="utf-8")
    ]
    assert hits == [], "\n".join(hits)


# ══════════════════════════════════════════════════════════════════════════
# G12 上方机械空间:唯一实现,两个方向共用
# ══════════════════════════════════════════════════════════════════════════

def test_the_upside_room_columns_are_defined_in_exactly_one_module():
    """两个列名只在 `k9/upside_room.py` 里以**字面量**出现 ——
    别处出现字面量 = 有人在另一处又算了一遍。

    ⚠ 这是**第一道线**,不是唯一一道:它只认那两个列名,换个列名再算一遍就绕过去了
    (复审 M6 实测:`p3_riser.py` 里写 `pl.col("high").max().alias("_p3_my_high")`
    → 72 passed 全绿)。真牙齿是下面那个 AST 检测器。"""
    hits: List[str] = []
    for p in sorted(_PKG.rglob("*.py")):
        if p == _K9 / "upside_room.py":
            continue
        src = p.read_text(encoding="utf-8")
        if '"upside_room_mech_high"' in src or '"upside_room_mech_pct"' in src:
            hits.append(str(p.relative_to(_ROOT)))
    assert hits == [], "「N 日最高」被人另写了一份:\n" + "\n".join(hits)


def _col_under(node: ast.AST) -> str:
    """顺着 `pl.col("X").shift(1).rolling_max(20)` 这条链往下找那个 `pl.col("X")`。

    ⛔ 不能只看直接接受者:`.shift(1)` 一插进去,只查一层的判据就瞎了。
    """
    cur: ast.AST = node
    while True:
        if (isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute)
                and cur.func.attr == "col" and cur.args
                and isinstance(cur.args[0], ast.Constant)
                and isinstance(cur.args[0].value, str)):
            return cur.args[0].value
        if isinstance(cur, ast.Call):
            cur = cur.func
        elif isinstance(cur, ast.Attribute):
            cur = cur.value
        else:
            return ""


#: 取「窗口内最大值」的 polars 方法名。⚠ 这一半仍然是**黑名单**(polars 的 API 面
#: 不封闭,`top_k(1)` / `sort().last()` 之类绕得过去)—— 但列名那一半已经反成白名单,
#: 两者相乘之后要绕过去得同时换方法**和**躲开验收单,成本已经不是「换个列名」了。
_WINDOW_MAX_METHODS = ("max", "rolling_max", "cum_max", "cummax")


def _window_high_sites(path: Path) -> List[str]:
    """找「在一个窗口上取价格列极大值」的形状 —— 「N 日最高」的实现特征。

    体例照同文件里给放量倍数写的 `_mean_over_vol_sites` —— G12 原来只有一个
    **列名字面量**的文本判据,姊妹条款(裁定 15)用的却是真 AST(复审 M6 / 🟡-8
    点名的就是这个落差)。

    🔴 **2026-08-21 第二轮自查:列名从白名单反过来写。** 上一版是
    `_HIGH_LIKE_COLUMNS = ("high", "close")` —— 只有这两列的窗口极值算数,
    换一列(比如先 `alias` 成 `_hi2` 再取 max、或者对 `pre_close` 取窗口最高)就绕过去了。
    现在**任何列**的窗口极值都进结果集,由下面那张**逐处写明理由**的验收单收口。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _WINDOW_MAX_METHODS):
            continue
        col = _col_under(node.func.value)
        if col:
            out.append(f"{path.name}:{node.lineno} .{node.func.attr}() over "
                       f"pl.col({col!r})")
    return out


def test_the_window_high_detector_actually_detects(tmp_path):
    """扫描器自检 —— 两种写法、隔一层 `.shift(1)` 都要看得见。"""
    for src in (
        'import polars as pl\nx = pl.col("high").max()\n',
        'import polars as pl\nx = pl.col("high").rolling_max(20)\n',
        'import polars as pl\nx = pl.col("close").shift(1).rolling_max(20).over("k")\n',
        # ⚠ 换一个没人想到的列 —— 白名单反转之后这一条也必须命中
        'import polars as pl\nx = pl.col("pre_close").cum_max()\n',
    ):
        p = tmp_path / "s.py"
        p.write_text(src, encoding="utf-8")
        assert _window_high_sites(p), src
    # 反向:别的聚合⛔ 不许被判红(min / mean 不是「最高」)。
    p = tmp_path / "s.py"
    p.write_text('import polars as pl\nx = pl.col("low").min()\ny = pl.col("vol").mean()\n',
                 encoding="utf-8")
    assert _window_high_sites(p) == []


#: 🔴 已知的、**不是**上方机械空间的窗口极值,连同理由。
#: ⛔ 这不是「豁免清单」而是**验收单**:多出一处 = 有人又算了一遍「N 日最高」。
_KNOWN_WINDOW_HIGH_FILES: Dict[str, str] = {
    "neckline/k9/upside_room.py":
        "上方机械空间的**唯一**实现(裁定 1),p1 正向 / p3 反向共用它",
    "neckline/k9/channels/p1_breakout.py":
        "过去 N 天**振幅**的窗口极差(高 − 低)÷ 低,与机械空间是两个量(§14 S6 ① 已登记)",
    "neckline/data/panel.py":
        "K8 研究面板遗留的 `rolling_max(20)`,不在 K9 机械链上(无 K9 消费方)",
    "neckline/facts/limitmap.py":
        "连板天数的最大值(`max` over `consec_limit_up_days`),不是价格极值",
}


def test_the_n_day_high_has_exactly_one_implementation_on_the_k9_path():
    """🔴 **G12 / 裁定 1**:「N 日最高」只有一处实现。

    裁定 1 的全部意义就是「这个量只有一处」—— 换个列名再算一遍,上面那条列名判据
    拦不住,这条拦得住。
    """
    sites: Dict[str, List[str]] = {}
    for p in sorted(_PKG.rglob("*.py")):
        got = _window_high_sites(p)
        if got:
            sites[str(p.relative_to(_ROOT))] = got
    unexpected = sorted(set(sites) - set(_KNOWN_WINDOW_HIGH_FILES))
    assert unexpected == [], (
        f"这些地方又算了一遍窗口最高价:{ {k: sites[k] for k in unexpected} } —— "
        f"裁定 1:上方机械空间只有一处实现。若确是**另一个量**,把它连同理由加进 "
        f"`_KNOWN_WINDOW_HIGH_FILES`,让它成为一次自觉行为。")
    # 反向:清单里每一条都**真的还在**(⛔ 不许留指向空气的豁免)。
    missing = sorted(set(_KNOWN_WINDOW_HIGH_FILES) - set(sites))
    assert missing == [], f"这些豁免已经没有对应实现,可以删了:{missing}"


def test_only_p1_and_p3_consume_the_upside_room_and_they_use_both_directions():
    """p1 走**正向**(空间大得分高)、p3 走**反向**(贴着还没捅破最好),
    两个打分器都调同一个 `compute()`(§5.4.5 末)。"""
    consumers = {
        p.stem for p in sorted(_PKG.rglob("*.py"))
        if any(m.endswith("upside_room") or ".upside_room" in m for m in _imports(p))
    }
    assert consumers == {"p1_breakout", "p3_riser"}, consumers

    p1_src = (_K9 / "channels" / "p1_breakout.py").read_text(encoding="utf-8")
    p3_src = (_K9 / "channels" / "p3_riser.py").read_text(encoding="utf-8")
    assert "score_room_far" in p1_src and "score_room_near" not in p1_src
    assert "score_room_near" in p3_src and "score_room_far" not in p3_src


def test_the_reverse_scorer_is_the_forward_one_negated():
    """⛔ 不另造第三个量:反向读法就是同一个量取负(裁定 1)。"""
    assert upside_room_mod.score_room_far(0.25) == 0.25
    assert upside_room_mod.score_room_near(0.25) == -0.25
    assert upside_room_mod.score_room_far(None) is None
    assert upside_room_mod.score_room_near(None) is None


# ══════════════════════════════════════════════════════════════════════════
# 裁定 15:放量倍数是**一处**共享计算,p1 与 p3 读**同一个键**
# ══════════════════════════════════════════════════════════════════════════

def _mean_over_vol_sites(path: Path) -> List[str]:
    """找 `pl.col("vol").mean()` 形状的写法 —— 「N 日均量」的实现特征。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "mean"):
            continue
        inner = node.func.value
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "col" and inner.args
                and isinstance(inner.args[0], ast.Constant)
                and inner.args[0].value == "vol"):
            out.append(f"{path.name}:{node.lineno}")
    return out


def test_the_mean_volume_detector_actually_detects():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.py"
        p.write_text('import polars as pl\nx = pl.col("vol").mean()\n', encoding="utf-8")
        assert _mean_over_vol_sites(p)


def test_the_volume_multiple_has_exactly_one_implementation():
    """🔴 裁定 15:「⛔ 不许在三个地方各算一份」。"""
    sites: Dict[str, List[str]] = {}
    for p in sorted(_PKG.rglob("*.py")):
        got = _mean_over_vol_sites(p)
        if got:
            sites[str(p.relative_to(_ROOT))] = got
    assert list(sites) == ["neckline/k9/volume.py"], sites


def _attribute_chain(node: ast.AST) -> str:
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _reads_param(path: Path, chain: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(n, ast.Attribute) and _attribute_chain(n).endswith(chain)
        for n in ast.walk(tree)
    )


def test_p1_and_p3_read_the_same_eruption_key():
    """🔴 裁定 15 的结构性落点:互斥要靠**同一个 V**。

    两个通道都读 `params.volume.eruption_multiple`;⛔ 任何一边改读自己档里的键,
    「严丝合缝互补」当场变成两条独立的门。
    """
    p1 = _K9 / "channels" / "p1_breakout.py"
    p3 = _K9 / "channels" / "p3_riser.py"
    assert _reads_param(p1, "volume.eruption_multiple")
    assert _reads_param(p3, "volume.eruption_multiple")


def test_the_v_is_a_single_value_not_a_per_tier_knob():
    """V ⛔ 不分档(见 `params.py` 模块 docstring 的理由):它是**分界点**不是松紧旋钮。"""
    from neckline.k9 import params as P

    assert "eruptionMultiple" in P.REQUIRED_SCHEMA["volume"]
    for ch, keys in P._CHANNEL_TIER_KEYS.items():
        assert not any("erupt" in k.lower() for k in keys), (ch, keys)


#: `k9/volume.py` 在别处的模块别名(`import ... as` 的两种常见写法)。
_VOLUME_MODULE_ALIASES = {"volume", "volume_mod"}


def _calls_on_module(path: Path, aliases: Set[str]) -> Set[str]:
    """这个文件在那些模块别名上**调用**过哪些函数(`volume_mod.f(...)` 的 `f`)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: Set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases):
            out.add(node.func.attr)
    return out


def test_the_module_call_detector_actually_detects(tmp_path):
    """扫描器自检。"""
    p = tmp_path / "s.py"
    p.write_text("volume_mod.compute(pack, ma_days=3)\nvolume_mod.volume_ratio(pack)\n",
                 encoding="utf-8")
    assert _calls_on_module(p, _VOLUME_MODULE_ALIASES) == {"compute", "volume_ratio"}


def test_the_volume_ratio_is_not_confused_with_the_volume_multiple():
    """⚠ 量比(÷ **5** 日均量)与放量倍数(÷ `volume.maDays`)是两个量。

    🔴 复审 M7:原来这条是两句文本断言 ——
        `assert "volume_ratio" in p4_src`      ← 被 p4 的 **docstring** 满足,恒真
        `assert "eruption_multiple" not in p4_src`  ← 只挡住了 V 这一个名字
    实测把 `volume_mod.volume_ratio(pack)` 换成
    `volume_mod.compute(pack, ma_days=params.volume.ma_days)`,**72 passed 全绿**:
    分母从 5 日悄悄变成 `volume.maDays`,而裁定 15 的 ⚠ 正是「这两个量⛔ 别混」。

    现在的判据是 **AST**:p4 在 `volume` 模块上调的函数**恰好只有** `volume_ratio`。
    文本那一半改走 `guard_scan.code_without_docstrings()`(⛔ 不再让 docstring 满足断言)。
    """
    assert volume_mod.COLUMN != volume_mod.RATIO_COLUMN
    assert volume_mod.VOLUME_RATIO_MA_DAYS == 5
    p4 = _K9 / "channels" / "p4_moneyflow.py"
    called = _calls_on_module(p4, _VOLUME_MODULE_ALIASES)
    assert called == {"volume_ratio"}, (
        f"形态 4 在 `k9/volume.py` 上调了 {sorted(called)} —— 它只该读**量比**"
        f"(÷ 5 日均量);`compute` 是放量倍数(÷ `volume.maDays`),两个量⛔ 别混。")
    p4_code = guard_scan.code_without_docstrings(p4)
    assert "volume_ratio" in p4_code, "p4 的**代码**里没有量比 —— 上一版是被 docstring 满足的"
    assert "eruption_multiple" not in p4_code, "形态 4 与 V 无关,⛔ 别把两个量接到一起"


# ══════════════════════════════════════════════════════════════════════════
# 契约一:声明依赖 = 列投影
# ══════════════════════════════════════════════════════════════════════════

def test_declared_fields_is_a_subset_of_the_pack_columns():
    from neckline.facts.pack import PACK_COLUMNS

    assert contract_mod.DECLARED_FIELDS <= set(PACK_COLUMNS)


def test_every_column_the_channels_touch_is_declared():
    """§5.4.2:「单测再断言四个通道实际触到的列全在声明集里」。

    扫的是 `pl.col("…")` 的字面量参数 —— 通道里凡是这么取的列都必须已声明。
    """
    known_internal = {"_"}          # 通道自己造的中间列一律以 `_` 开头
    offenders: List[str] = []
    for path in _CHANNEL_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "col" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                name = node.args[0].value
                if name.startswith(tuple(known_internal)):
                    continue
                if name in (volume_mod.COLUMN, volume_mod.RATIO_COLUMN,
                            upside_room_mod.HIGH_COLUMN, upside_room_mod.PCT_COLUMN):
                    continue
                if name not in contract_mod.DECLARED_FIELDS:
                    offenders.append(f"{path.name}:{node.lineno} pl.col({name!r})")
    assert offenders == [], (
        "通道读了没声明的列(契约一):\n" + "\n".join(offenders))


# ══════════════════════════════════════════════════════════════════════════
# 落库:布局与全仓一致,但生产路径⛔ 不 import 取数模块(G3)
# ══════════════════════════════════════════════════════════════════════════

def test_the_disposition_layout_matches_the_repo_convention(tmp_path):
    """`k9/store.py` 自己拼路径(为了不破 G3),布局必须与 `market_data` 逐字相同。

    ⚠ 测试**可以** import `market_data` —— 守门的是**生产路径**,不是测试。
    """
    from datetime import date

    from neckline.data.market_data import day_file_path

    d = date(2026, 8, 20)
    assert k9_store.disposition_path(d, tmp_path) == day_file_path(
        k9_store.PARQUET_TABLE, d, tmp_path)


def test_the_disposition_frame_has_an_explicit_schema():
    """§12 坑 2 的更强形态:每次都按同一张显式 schema 造,⛔ 不向既有分区看齐。"""
    schema = k9_store._DISPOSITION_SCHEMA
    assert set(schema) == {
        "trade_date", "ts_code", "excluded_by", "recalled_patterns_json", "tier",
        "score", "rank", "seated", "seat_kind", "news_excluded"}


def test_the_two_declarations_of_the_disposition_dtypes_agree():
    """写侧(`k9/store.py` 的显式 schema)与读侧(`market_data.TABLE_FLOAT_COLS`)
    必须逐列一致 —— 两处声明各说各话,正是 §12 坑 2 那类事故的开头。"""
    import polars as pl

    from neckline.data.market_data import TABLE_FLOAT_COLS

    floats = {c for c, t in k9_store._DISPOSITION_SCHEMA.items() if t == pl.Float64}
    assert set(TABLE_FLOAT_COLS[k9_store.PARQUET_TABLE]) == floats


# ══════════════════════════════════════════════════════════════════════════
# 零 LLM:AST(S5 那份)+ **运行时**双证(§5.4.1 第 2 条)
# ══════════════════════════════════════════════════════════════════════════

def test_the_whole_chain_runs_with_the_llm_factory_rigged_to_explode(
    isolated_env, tmp_path, monkeypatch,
):
    """🔴 §5.4.1 第 2 条:把 `neckline.llm.factory` 的构造函数 monkeypatch 成
    「一调就抛」,跑完整选股,断言成功。

    AST 扫描能证明「没 import」,运行时这一条证明的是「连间接调用都没有」。
    """
    import neckline.llm.factory as factory

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("策略层里⛔ 不许有任何 LLM 调用(架构 §3.2)")

    for name in dir(factory):
        obj = getattr(factory, name)
        if callable(obj) and not name.startswith("__"):
            monkeypatch.setattr(factory, name, boom, raising=False)

    from neckline.k9 import run as k9_run
    from tests import k9_env

    env = isolated_env
    day = k9_env.seed(env)
    params = k9_env.params(env, tmp_path)
    result = k9_run.compute(day, params=params, parquet_dir=env.parquet_dir,
                            db_path=env.db_path)
    k9_run.persist(result, parquet_dir=env.parquet_dir, db_path=env.db_path)
    assert result.shortlist.size > 0
