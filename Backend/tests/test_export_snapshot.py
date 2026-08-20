"""S13 · 标定快照导出的行为判据(PROJECT_PLAN §5.13 / §6 S13)。

🔴 **逐字节相同是这条线的全部意义**:标定要跑在与生产**完全一样**的事实包上,
否则「联合通过率」这个数没有意义。本文件因此把「拷出来的文件 sha256 == 原文件
sha256」当成第一条断言,并锁住「⛔ 不重写 parquet」这条实现纪律。

其余三条:manifest 的身份证四项(`packVersion` / 区间 / Neckline 版本 / 逐日 sha256)、
缺日与孤儿日**都要说出口**、⛔ 区间没有默认值。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    """把 `scripts/export_research_snapshot.py` 当模块加载(它是 CLI,不在包里)。"""
    path = _ROOT / "scripts" / "export_research_snapshot.py"
    spec = importlib.util.spec_from_file_location("_export_snapshot_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


EX = _load_script()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _seed_packs(env, days, *, versioned: bool):
    """铺几天事实包 parquet + 对应的 `fact_packs` 清单行。

    🔴 **`content_fingerprint` 必须是文件真实的 sha256**(⛔ 不是占位串):
    `facts.store.resolve_pack_path` 对**遗留布局**的回落要拿它逐字对拍 ——
    夹具里塞一个假指纹,测的就不再是生产那条路径了(R1-B1 之后布局变成
    `fact_pack/version=<v>/year=YYYY/`,遗留布局的回落是唯一还要过指纹的那一条)。
    """
    from neckline.facts.pack import PACK_VERSION

    base = env.parquet_dir / "fact_pack"
    root = (base / f"version={PACK_VERSION}" / "year=2026") if versioned else (base / "year=2026")
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(env.db_path))
    try:
        for d in days:
            f = root / f"{d}.parquet"
            f.write_bytes(b"PAR1-fixture-" + d.encode())
            conn.execute(
                "INSERT INTO fact_packs (pack_id, trade_date, pack_version, origin, state, "
                "content_fingerprint, row_count, sources_json, market_json, "
                "suspend_anomaly_count, frozen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"pid-{d}", d, PACK_VERSION, "live", "frozen", _sha(f), 5526,
                 "{}", "{}", 0, "t"))
        conn.commit()
    finally:
        conn.close()
    return root


@pytest.fixture
def snapshot_env(isolated_env):
    """临时库 + 临时 parquet 根,铺三天事实包(**遗留布局**)与对应的清单行。

    ⚠ 遗留布局是 R1-B1 之前冻的包在生产上的真实形状,导出必须还认得它。
    带版本的新布局另有 `TestVersionedLayout`。
    ⛔ 全程不碰工作库 / 工作 parquet(AGENTS.md 测试纪律)。"""
    days = ["20260102", "20260105", "20260106"]
    _seed_packs(isolated_env, days, versioned=False)
    return isolated_env, days


class TestByteForByte:
    def test_copied_packs_have_the_same_sha256_as_the_originals(self, snapshot_env, tmp_path):
        """🔴 §5.13:标定必须跑在与生产**逐字节相同**的事实包上。
        重写 parquet 会换压缩块与行组边界 —— 数据看起来一模一样,sha256 却对不上。"""
        env, days = snapshot_env
        out = tmp_path / "snap"
        frag = EX.export_fact_packs(out, "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert frag["fileCount"] == 3
        for entry in frag["files"]:
            src = env.parquet_dir / "fact_pack" / "year=2026" / f"{entry['date']}.parquet"
            dst = out / entry["path"]
            assert dst.read_bytes() == src.read_bytes()
            assert entry["sha256"] == _sha(src) == _sha(dst)

    def test_partition_layout_is_preserved(self, snapshot_env, tmp_path):
        """whynotme 侧要用与生产**相同的路径约定**读 —— 布局一变,那边就得另写一套。"""
        env, _ = snapshot_env
        out = tmp_path / "snap"
        EX.export_fact_packs(out, "20260101", "20260131",
                             parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert (out / "fact_pack" / "year=2026" / "20260105.parquet").is_file()

    def test_layout_matches_the_production_day_file_path(self, snapshot_env, tmp_path):
        """⛔ 不许在导出脚本里另拼一套路径:拿 `market_data.day_file_path` 对拍。"""
        from neckline.data.market_data import day_file_path

        env, _ = snapshot_env
        out = tmp_path / "snap"
        frag = EX.export_fact_packs(out, "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        for entry in frag["files"]:
            expected = day_file_path("fact_pack", entry["date"], parquet_dir=out)
            assert (out / entry["path"]) == expected


class TestRangeSelection:
    def test_range_is_inclusive_on_both_ends(self, snapshot_env, tmp_path):
        env, _ = snapshot_env
        frag = EX.export_fact_packs(tmp_path / "s", "20260102", "20260105",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert [f["date"] for f in frag["files"]] == ["20260102", "20260105"]

    def test_missing_dates_are_reported_not_silently_dropped(self, snapshot_env, tmp_path):
        """🔴 标定方拿到 2 天而不是 3 天,与拿到 3 天是两件事。⛔ 不静默少给。"""
        env, days = snapshot_env
        (env.parquet_dir / "fact_pack" / "year=2026" / "20260105.parquet").unlink()
        frag = EX.export_fact_packs(tmp_path / "s", "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert frag["missingDates"] == ["20260105"]
        assert frag["orphanDates"] == []

    def test_orphan_parquet_without_a_manifest_row_is_reported(self, snapshot_env, tmp_path):
        """拷到了、清单里却没有 —— 同样要说出口(⛔ 不静默带走一份来路不明的包)。"""
        env, _ = snapshot_env
        (env.parquet_dir / "fact_pack" / "year=2026" / "20260107.parquet").write_bytes(b"x")
        frag = EX.export_fact_packs(tmp_path / "s", "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert frag["orphanDates"] == ["20260107"]

    def test_missing_table_directory_degrades_to_zero_files(self, isolated_env, tmp_path):
        frag = EX.export_fact_packs(tmp_path / "s", "20260101", "20260131",
                                    parquet_dir=isolated_env.parquet_dir,
                                    db_path=isolated_env.db_path)
        assert frag["fileCount"] == 0 and frag["files"] == []


class TestManifest:
    def _run(self, env, tmp_path, extra):
        out = tmp_path / "snap" / "neckline.snapshot.db"
        argv = ["export_research_snapshot.py", "--source", str(env.db_path),
                "--out", str(out), "--parquet-dir", str(env.parquet_dir)] + extra
        old = sys.argv
        sys.argv = argv
        try:
            assert EX.main() == 0
        finally:
            sys.argv = old
        return out, json.loads(
            (out.parent / (out.name + ".manifest.json")).read_text(encoding="utf-8"))

    def test_manifest_carries_the_four_identity_fields(self, snapshot_env, tmp_path):
        """🔴 manifest 是这份快照的身份证:`packVersion` / 区间 / Neckline 版本 /
        逐日 sha256。缺任何一项就没法回答「那次标定跑在哪一版事实包上」。"""
        from neckline.api.app import VERSION
        from neckline.facts.pack import PACK_VERSION

        env, _ = snapshot_env
        _out, man = self._run(env, tmp_path,
                              ["--include-fact-packs", "--start", "20260101",
                               "--end", "20260131"])
        assert man["necklineVersion"] == VERSION
        assert man["factPacks"]["packVersion"] == PACK_VERSION
        assert (man["factPacks"]["start"], man["factPacks"]["end"]) == ("20260101", "20260131")
        assert all(f["sha256"] for f in man["factPacks"]["files"])
        assert man["createdAt"]

    def test_parquet_path_reflects_the_root_actually_read(self, snapshot_env, tmp_path):
        """⚠ 一份跑在临时目录上的 manifest ⛔ 不许指着生产目录说话。"""
        env, _ = snapshot_env
        _out, man = self._run(env, tmp_path, [])
        assert Path(man["parquetReadOnlyPath"]) == env.parquet_dir.resolve()

    def test_without_the_flag_there_is_no_fact_pack_section(self, snapshot_env, tmp_path):
        env, _ = snapshot_env
        _out, man = self._run(env, tmp_path, [])
        assert "factPacks" not in man

    def test_sqlite_snapshot_is_a_real_readable_copy(self, snapshot_env, tmp_path):
        env, days = snapshot_env
        out, man = self._run(env, tmp_path, [])
        assert man["sha256"] == _sha(out)
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM fact_packs").fetchone()[0]
        finally:
            conn.close()
        assert n == len(days), "快照里必须能读到 fact_packs 清单行"


class TestNoDefaultRange:
    @pytest.mark.parametrize("extra", [
        ["--include-fact-packs"],
        ["--include-fact-packs", "--start", "20260101"],
        ["--include-fact-packs", "--start", "20260101", "--end", "2026"],
        ["--include-fact-packs", "--start", "20260201", "--end", "20260101"],
    ])
    def test_incomplete_range_is_a_hard_error(self, snapshot_env, tmp_path, extra):
        """⛔ 「导哪一段」是操作者的决定 —— 替他挑一段等于让标定跑在一段他没打算
        用的数据上。缺 / 非法 / 倒序一律当场退出。"""
        env, _ = snapshot_env
        out = tmp_path / "snap" / "neckline.snapshot.db"
        old = sys.argv
        sys.argv = (["export_research_snapshot.py", "--source", str(env.db_path),
                     "--out", str(out), "--parquet-dir", str(env.parquet_dir)] + extra)
        try:
            with pytest.raises(SystemExit) as exc:
                EX.main()
        finally:
            sys.argv = old
        assert exc.value.code == 2
        assert not out.exists(), "参数没过就不该已经写出快照"


class TestVersionedLayout:
    """🔴 R1-B1 之后的**当前**布局:`fact_pack/version=<v>/year=YYYY/YYYYMMDD.parquet`。

    这一组是 R3/R1 交接时点名的那个洞的直接反例:导出脚本原来只 glob
    `fact_pack/year=*/`,新布局下**一个文件都扫不到** —— 区间内每一天都会被报进
    `missingDates`。响亮,但完全是假的:数据明明都在。
    """

    @pytest.fixture
    def versioned_env(self, isolated_env):
        days = ["20260102", "20260105"]
        _seed_packs(isolated_env, days, versioned=True)
        return isolated_env, days

    def test_the_current_layout_is_found_at_all(self, versioned_env, tmp_path):
        from neckline.facts.pack import PACK_VERSION

        env, days = versioned_env
        frag = EX.export_fact_packs(tmp_path / "s", "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert frag["missingDates"] == [], "带版本的布局一个文件都没扫到 —— 那个洞回来了"
        assert [f["date"] for f in frag["files"]] == days
        assert frag["fileCount"] == 2
        # 源布局原样搬过去(whynotme 侧用与生产完全相同的约定读)。
        for entry in frag["files"]:
            assert entry["path"].startswith(f"fact_pack/version={PACK_VERSION}/year=2026/")
            assert (tmp_path / "s" / entry["path"]).is_file()

    def test_bytes_still_match(self, versioned_env, tmp_path):
        from neckline.facts.pack import PACK_VERSION

        env, _ = versioned_env
        out = tmp_path / "s"
        frag = EX.export_fact_packs(out, "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        for entry in frag["files"]:
            src = (env.parquet_dir / "fact_pack" / f"version={PACK_VERSION}"
                   / "year=2026" / f"{entry['date']}.parquet")
            assert (out / entry["path"]).read_bytes() == src.read_bytes()
            assert entry["sha256"] == _sha(src)

    def test_a_legacy_file_whose_bytes_do_not_match_the_manifest_is_a_gap(
            self, versioned_env, tmp_path):
        """⛔ **不许「文件在就用」**:遗留路径是「一天一个坑位」的旧布局,同一天若有过
        第二版,那个文件属于谁在路径上看不出来。指纹对不上 = 那一天算**缺口**,
        ⛔ 不拿一份对不上账的字节去标定。"""
        from neckline.facts.pack import PACK_VERSION

        env, _ = versioned_env
        # 造一个"看起来像那天"的遗留文件,字节与清单指纹**不同**;
        # 并把带版本的那份挪走,逼求解走回落分支。
        (env.parquet_dir / "fact_pack" / f"version={PACK_VERSION}" / "year=2026"
         / "20260105.parquet").unlink()
        legacy_dir = env.parquet_dir / "fact_pack" / "year=2026"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "20260105.parquet").write_bytes(b"another version entirely")
        frag = EX.export_fact_packs(tmp_path / "s", "20260101", "20260131",
                                    parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert frag["missingDates"] == ["20260105"]
        assert "20260105" not in [f["date"] for f in frag["files"]]
