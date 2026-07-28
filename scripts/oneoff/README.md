# scripts/oneoff — 一次性脚本留档

已在生产执行完毕的一次性脚本(charter 落行 / bootstrap / 数据修缮),留档供审计与
体例复用,**不属于日常运行面**;现役脚本全在上一层 `scripts/`。

- `charter_v1_2.py` / `charter_v1_3.py` / `charter_v1_3_3.py` — 各版章程 payload 落行
  (激活另走 `scripts/activate_charter.py` 四道闸,那个是常设机制、不在本目录)。
- `bootstrap_k4.py` + `bootstrap_k4_payload.json` — K4 advisory 半身行 bootstrap
  (payload sha256 硬校验,改一字节即拒跑)。
- `fix_moneyflow_schema.py` — 2026-07-28 生产 `moneyflow_dc` 902 个脏分区修缮
  (逐文件 cast、幂等;同类 schema 修缮照此体例)。
- `fix_position_buy_dates.py` — 2026-07-28 生产 3 笔持仓买入日纠正(v1.4-①-A / P0-1)。
  幂等 + ts_code 防呆断言 + `.backup`/`cp -p` 双备份 + 改前改后逐行对拍 + 清
  `holding_eod_check.time_exit_locked_*` 三列让次日 16:35 按正确 D 重新定格。
  **同类「改生产台账」脚本照此体例**(默认演练,`--confirm` 才写)。

对应单测仍在 `tests/`(`test_charter_v13/v133.py`、`test_fix_moneyflow_schema.py`),
sys.path 已指向本目录。
