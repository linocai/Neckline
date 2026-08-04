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
- `backfill_holding_data_unavailable.py` — 2026-07-28 v1.4.0-p1 上云补丁:把
  `holding_eod_check.data_unavailable`(①-B 新列)按**分区实况推导**回填到历史行。
  不补的话,上云当天的 9:25:30 盘前那一拍读到的还是旧代码写的 NULL 快照 → 停牌票照推
  「D5 时间退出」(**实测踩到**)。分区不可读的行**留 NULL 不推导**。
- `retire_k4_b3.py` + `retire_k4_b3_payload.json` — **V2-⑯-I(2026-08-04 已在生产执行)**:
  K4 advisory 的 B3「题材持续2-3天」黄牌退役(从 `k4_advisory.avoid_flag` 摘掉那一个键)。
  声明件里 **before/after 两个 rule_json sha256 硬钉**、fail-closed(基线不符即拒,
  `--allow-unknown-base` 是唯一逃生口且必须显式给);默认演练、`--confirm` 才写、写前
  `.backup`+`cp -p` 双备份、单事务、**三态幂等**(已退役则 0 改动);写后逐条断言
  K4 仍 `is_active=0` / `activated_at` 未变 / `strategy_activation_log` 不增行 /
  A2 红牌未动。**改 `strategy_versions` 里 inert 行的 advisory 载体一律照此体例。**
  全文 diff → `archive/K4_advisory_B3退役_20260804.md`。
- `preseed_baskets.py` — **V2-⑯-F(脚本已就绪,但 2026-08-04 当次未灌:无外部输入件)**:
  把外部准备好的近期若干交易日篮子/Tier/卡灌进权威库做 Tier 冷启动。四道闸:JSON Schema /
  成员白名单闸(复用 `selection.member_hygiene.apply_member_hygiene`)/ 角色对拍(读
  `leader_structure_daily`,**分歧原样入库不覆盖**)/ 夹逼(复用 `basket_card.clamp_entry_zone`
  + `clamp_max_chase`,被拦置空**不改成边界值**)。⚠ 与在线路径相反,**降级在这里 fail-closed**
  (往权威库灌历史,宁可不灌);`via='preseed'` + `pack_version='preseed'` **两处都标、
  ⛔ 不许填 K7-pack-v1**(会污染按包归因)。`--example` 打输入件模板。
- `compare_a2b3_industry_switch.py` — v1.4-②-C 硬要求:A2/B3 题材持续天数判据从概念板块
  `board_age` 代理切到 `industry_strength` 行业强度唯一源的切换对拍,只读本地库/parquet
  (零生产访问、零写库),产出 `archive/v1.4_A2B3口径切换对拍_<date>.md`(全市场 + 候选池
  逐票新增/消失命中 + 数量小结 + 抽样人读说明)。可重跑(换 `[YYYYMMDD]` 参数对别的交易日
  再对拍一次)。

对应单测仍在 `tests/`(`test_charter_v13/v133.py`、`test_fix_moneyflow_schema.py`),
sys.path 已指向本目录。
