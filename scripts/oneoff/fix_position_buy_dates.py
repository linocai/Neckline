#!/usr/bin/env python3
"""一次性纠正已落库持仓的**真实买入日**(plan §五 v1.4-①-A / §七 P0-1)。🔴 碰持仓判定。

**背景**:`api/app.py::open_position` 在 v1.4 之前把 `buy_date` 写死成 `date.today()`
(`PositionOpenIn` 也没有买入日字段)→ 用户 2026-07-27 补录的 3 笔历史持仓,买入日全被
盖成补录当天。**为什么必须纠正**:D 计数、两档时间退出 D5/D15 判向、回落止盈峰值追踪
起点、周复盘持有天数、按打法归因的持有周期,**全部以买入日为起点**。写侧的口子已由
v1.4-①-A 补上(`buyDate` 可选入参),但**写侧修好不会让历史错行自愈**——那是本脚本的活
(同 `fix_moneyflow_schema.py` 的分工:写侧防线 + 存量修缮各一份)。

**做四件事,全程幂等**:
  1. **备份**:`sqlite3 .backup`(在线一致性备份,照 v1.1-H 姿势)+ `cp -p` 双保险;
  2. **校正 `positions.buy_date`**(改前改后逐笔打印 id / ts_code / 旧值 / 新值);
  3. **清定格**:这些 position_id 在 `holding_eod_check` 里的 `time_exit_locked_state` /
     `time_exit_locked_date` / `time_exit_locked_net_float` **三列有值就清空** —— 买入日
     错 → D 计数错 → 定格判向建立在错误的 D 上,必须让下一份 16:35 报告按正确 D 重新
     定格。**只清定格三列,不动 `d_count` / `net_float` / `time_exit_state` 等历史记录列**
     (那是「当时系统怎么看的」的审计留痕,改它等于篡改历史;下一份 16:35 会按正确买入日
     写当日新行,判向由重新定格产生);
  4. **复查**:`PRAGMA integrity_check` + 三笔终值 + 定格三列已清 + **其余行零改动**
     (逐表行数与内容摘要前后对拍)。

**安全闸**:
  · 默认 `--dry-run` 语义 —— 不带 `--confirm` 只报告不落盘;
  · 每笔校正必须带**预期 ts_code**,与库里不符即整体中止(不写任何一笔)——防「id 记错
    把别人家的持仓改了」;
  · 目标日必须是 `trade_cal` 交易日且不晚于今天(与 ①-A 服务端同口径);
  · 校正后的 `buy_date` 已经等于目标值 → 该笔跳过(幂等,二次运行零改动)。

用法(生产上跑前先读 plan §五 v1.4-① 的 🔴 纪律):
    python scripts/oneoff/fix_position_buy_dates.py --db /opt/neckline/data/neckline.db \\
        --fix 1:300759.SZ:20260727 --fix 2:300261.SZ:20260727 --fix 3:002036.SZ:20260722
    # 加 --confirm 才真写;不加只演练打印

核心 `apply_buy_date_fixes` 可导入,单测见 `tests/test_fix_position_buy_dates.py`
(承阶段 0 教训:改脚本级写库代码先补一层单测)。
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("fix_position_buy_dates")

# 仓库根入 sys.path(同目录内其它 oneoff 脚本惯例)。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# 定格三列 —— 单一事实源是 `holding_eod_check` 的 schema(`neckline/db.py`);这里只列名字,
# 语义见 `report/holding_store.py` 模块头「D5 判一次定格」。
LOCKED_COLS: Tuple[str, ...] = (
    "time_exit_locked_state",
    "time_exit_locked_date",
    "time_exit_locked_net_float",
)


@dataclass
class Fix:
    """一笔校正意图。`expect_ts_code` 是**防呆断言**,不是查询条件的补充。"""
    position_id: int
    expect_ts_code: str
    new_buy_date: str          # 'YYYYMMDD'


@dataclass
class FixReport:
    changed: List[Dict[str, str]] = field(default_factory=list)      # 真改了的笔
    skipped: List[Dict[str, str]] = field(default_factory=list)      # 已是目标值(幂等跳过)
    locks_cleared: List[Dict[str, str]] = field(default_factory=list)  # 被清掉的定格行
    integrity: str = ""
    dry_run: bool = True

    @property
    def touched_rows(self) -> int:
        return len(self.changed) + len(self.locks_cleared)


def _parse_fix(spec: str) -> Fix:
    """`'3:002036.SZ:20260722'` → Fix。格式错即抛(不猜)。"""
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValueError(f"--fix 格式应为 id:ts_code:YYYYMMDD,收到 {spec!r}")
    pid, code, buy_date = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if not pid.isdigit():
        raise ValueError(f"--fix 的 id 必须是整数,收到 {pid!r}")
    if not (len(buy_date) == 8 and buy_date.isdigit()):
        raise ValueError(f"--fix 的日期必须是 YYYYMMDD,收到 {buy_date!r}")
    return Fix(position_id=int(pid), expect_ts_code=code, new_buy_date=buy_date)


def _validate_target_dates(fixes: Sequence[Fix], conn: sqlite3.Connection) -> None:
    """目标买入日必须是 `trade_cal` 交易日且不晚于今天 —— **与 ①-A 服务端校验同口径**
    (那边 400 `not_trading_day` / `future_buy_date`)。这里直接查库而非 import
    `neckline.calendar`,因为本脚本要能对着**任意一份 db 文件**跑(生产库路径由 --db 指定,
    而 `neckline.calendar` 读的是本机 settings 指向的库)。"""
    today = date.today().strftime("%Y%m%d")
    for f in fixes:
        if f.new_buy_date > today:
            raise SystemExit(f"[ABORT] #{f.position_id} 目标买入日 {f.new_buy_date} 晚于今天 {today}")
        row = conn.execute(
            "SELECT is_open FROM trade_cal WHERE cal_date=? ORDER BY exchange LIMIT 1",
            (f.new_buy_date,),
        ).fetchone()
        if row is None:
            raise SystemExit(
                f"[ABORT] #{f.position_id} 目标买入日 {f.new_buy_date} 不在 trade_cal 里"
                f"(日历未覆盖?先跑 scripts/init_calendar.py)"
            )
        if not int(row[0]):
            raise SystemExit(f"[ABORT] #{f.position_id} 目标买入日 {f.new_buy_date} 不是交易日")


def snapshot(conn: sqlite3.Connection) -> Dict[str, object]:
    """改动前后的**全表对拍快照**(证明「其余行零改动」)。positions 取全字段元组集合、
    holding_eod_check 取全字段元组集合 —— 直接比对象,不比 count(改一格而行数不变的
    篡改,count 看不出来)。"""
    return {
        "positions": conn.execute("SELECT * FROM positions ORDER BY id").fetchall(),
        "holding_eod_check": conn.execute(
            "SELECT * FROM holding_eod_check ORDER BY position_id, trade_date"
        ).fetchall(),
    }


def diff_snapshots(before: Dict[str, object], after: Dict[str, object]) -> Dict[str, List[Tuple]]:
    """返回 `{表名: [(before_row, after_row), ...]}`,只含真变化的行(逐行按位置对拍;
    行数不同 → 单列 `('__ROW_COUNT_CHANGED__', n_before, n_after)`,那绝不该发生)。"""
    out: Dict[str, List[Tuple]] = {}
    for table in before:
        b, a = list(before[table]), list(after[table])   # type: ignore[arg-type]
        if len(b) != len(a):
            out[table] = [("__ROW_COUNT_CHANGED__", len(b), len(a))]
            continue
        changed = [(rb, ra) for rb, ra in zip(b, a) if rb != ra]
        if changed:
            out[table] = changed
    return out


def apply_buy_date_fixes(
    db_path: Path,
    fixes: Sequence[Fix],
    *,
    confirm: bool = False,
) -> FixReport:
    """核心:校正买入日 + 清定格三列。`confirm=False` → 全程只读演练(事务回滚),
    打印的 diff 与真跑一致。任何一笔的 ts_code 与库里不符 → 整体中止,不写任何一笔。"""
    rep = FixReport(dry_run=not confirm)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _validate_target_dates(fixes, conn)

        # ① 防呆:逐笔核对 id ↔ ts_code,任何不符即整体中止(绝不部分写入)。
        current: Dict[int, Tuple[str, str]] = {}
        for f in fixes:
            row = conn.execute(
                "SELECT ts_code, buy_date FROM positions WHERE id=?", (f.position_id,)
            ).fetchone()
            if row is None:
                raise SystemExit(f"[ABORT] positions 里没有 id={f.position_id}")
            if row[0] != f.expect_ts_code:
                raise SystemExit(
                    f"[ABORT] id={f.position_id} 实际 ts_code={row[0]},与 --fix 声明的 "
                    f"{f.expect_ts_code} 不符(id 记错?)—— 一笔不写,整体中止"
                )
            current[f.position_id] = (row[0], row[1])

        before = snapshot(conn)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # ② 校正 buy_date(幂等:已是目标值的笔跳过,连 updated_at 都不动)。
        for f in fixes:
            code, old = current[f.position_id]
            entry = {
                "id": str(f.position_id), "ts_code": code,
                "old_buy_date": old, "new_buy_date": f.new_buy_date,
            }
            if old == f.new_buy_date:
                rep.skipped.append(entry)
                logger.info("[skip] #%s %s buy_date 已是 %s,不动", f.position_id, code, old)
                continue
            conn.execute(
                "UPDATE positions SET buy_date=?, updated_at=? WHERE id=? AND ts_code=?",
                (f.new_buy_date, now, f.position_id, f.expect_ts_code),
            )
            rep.changed.append(entry)
            logger.info("[fix ] #%s %s buy_date %s → %s", f.position_id, code, old, f.new_buy_date)

        # ③ 清定格三列(**只对本次涉及的 position_id**,且只清「三列任一非空」的行)。
        #    买入日错 → D 计数错 → 定格建立在错误的 D 上,必须清掉让下一份 16:35 重新定格。
        pids = [f.position_id for f in fixes]
        qmarks = ",".join("?" * len(pids))
        lock_rows = conn.execute(
            f"SELECT position_id, trade_date, {', '.join(LOCKED_COLS)} FROM holding_eod_check "
            f"WHERE position_id IN ({qmarks}) AND ("
            + " OR ".join(f"{c} IS NOT NULL" for c in LOCKED_COLS) + ")",
            pids,
        ).fetchall()
        for r in lock_rows:
            rep.locks_cleared.append({
                "position_id": str(r[0]), "trade_date": r[1],
                "locked_state": str(r[2]), "locked_date": str(r[3]), "locked_net_float": str(r[4]),
            })
            logger.info(
                "[lock] 清定格 #%s@%s(原 state=%s date=%s net_float=%s)——"
                "下一份 16:35 将按正确 D 重新定格", r[0], r[1], r[2], r[3], r[4],
            )
        if lock_rows:
            conn.execute(
                f"UPDATE holding_eod_check SET "
                + ", ".join(f"{c}=NULL" for c in LOCKED_COLS)
                + f" WHERE position_id IN ({qmarks}) AND ("
                + " OR ".join(f"{c} IS NOT NULL" for c in LOCKED_COLS) + ")",
                pids,
            )
        else:
            logger.info("[lock] 涉及的持仓在 holding_eod_check 里没有任何已定格行,无需清")

        # ④ 复查:逐行对拍 + integrity_check。
        after = snapshot(conn)
        deltas = diff_snapshots(before, after)
        for table, rows in deltas.items():
            for pair in rows:
                logger.info("[diff] %s:%s", table, pair)
        rep.integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])

        if confirm:
            conn.commit()
            logger.info("[COMMIT] 已落盘(integrity_check=%s)", rep.integrity)
        else:
            conn.rollback()
            logger.info("[DRY-RUN] 已回滚,库未改动(加 --confirm 才真写)")
        return rep
    finally:
        conn.close()


def backup_db(db_path: Path, tag: str) -> Tuple[Path, Path]:
    """双保险备份:`sqlite3 .backup`(在线一致性,WAL 也安全)+ `cp -p` 文件级快照。
    返回 (backup_path, cp_path)。**改 schema / 改生产数据前必跑**(§3.8 铁律)。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = db_path.with_name(f"{db_path.name}.bak-{tag}-{stamp}")
    cpbak = db_path.with_name(f"{db_path.name}.cpbak-{tag}-{stamp}")
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(bak))
        try:
            src.backup(dst)            # 在线一致性备份(不受并发写/WAL 影响)
        finally:
            dst.close()
    finally:
        src.close()
    shutil.copy2(db_path, cpbak)       # cp -p 等价(保留 mtime/权限)
    logger.info("[backup] .backup → %s", bak)
    logger.info("[backup] cp -p   → %s", cpbak)
    return bak, cpbak


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="纠正持仓真实买入日(v1.4-①-A / P0-1)")
    ap.add_argument("--db", required=True, help="SQLite 库路径(生产 /opt/neckline/data/neckline.db)")
    ap.add_argument("--fix", action="append", required=True, metavar="id:ts_code:YYYYMMDD",
                    help="一笔校正(可重复);ts_code 是防呆断言,与库里不符即整体中止")
    ap.add_argument("--confirm", action="store_true", help="真写(不加 = 只演练打印)")
    ap.add_argument("--skip-backup", action="store_true",
                    help="跳过备份(**只在已由外部完成备份时用**,如生产上手工先 .backup 过)")
    ap.add_argument("--tag", default="buydates", help="备份文件名标签")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("库文件不存在:%s", db_path)
        return 1
    fixes = [_parse_fix(s) for s in args.fix]

    if args.confirm and not args.skip_backup:
        backup_db(db_path, args.tag)
    elif args.confirm:
        logger.warning("[backup] --skip-backup:本次不备份(确认外部已备份过)")

    rep = apply_buy_date_fixes(db_path, fixes, confirm=args.confirm)
    logger.info(
        "完成:改 %d 笔 / 幂等跳过 %d 笔 / 清定格 %d 行 / integrity=%s / %s",
        len(rep.changed), len(rep.skipped), len(rep.locks_cleared), rep.integrity,
        "已落盘" if not rep.dry_run else "演练(未落盘)",
    )
    return 0 if rep.integrity == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
