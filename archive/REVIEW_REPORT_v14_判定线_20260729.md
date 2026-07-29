# 独立审计报告 · v1.4 判定线(⑥-A 逐笔章程 / ⑩ P0-23 修复 / P2-10 高危面)

- 审计员:判定线独立 reviewer(未参与施工),2026-07-29
- 分工:本报告只审**判定线**(⑥-A 时区与判据锚点 / ⑩ 递推与保险丝 / P2-10 四个已发布版本的高危改动);K4 对拍与 API 契约由并行的契约线审计员另行出报告,本文不重复。
- 方法:从零读代码,施工自述当待验证声明;plan(§五 ⑥/⑩、§七 P1-4/P0-23/P2-10、§2.1)逐条对照;能跑的全跑(`tests/test_brain.py` + `test_review_reconcile.py` + `test_industry_strength*.py` 共 **170 过 0 挂**;另写 3 个临时脚本做反例/对拍实验,全部在 scratchpad/临时库,零代码修改、零生产访问)。
- 基线:本地全量 1677 过 + 2 skip(§四 快照口径);本审计只跑了范围内四个测试文件,未重跑全量。
- **前提声明**:⑩ 的生产侧(旁路 bootstrap、⑩-D 五判据、⑩-G 五实测)按 §四 尚未执行,本报告只对**代码与本地测试**背书,不为生产侧未做的判据背书。

---

## 一、缺陷清单(按严重度)

### 🟡-1 回滚/重激活会改写激活时间线——历史周的逐笔章程判定整段错判(洗白口经回退路径复活)

- 位置:`neckline/strategy/brain.py:187-209`(`activate_version` 每次激活**刷新** `activated_at`,一个版本只存最后一次激活时刻)+ `config_governing_at`/`config_active_at`(两个解析器都只吃这条单戳时间线)。
- 判据:时间线模型无法表达「一个版本被激活过两次」。docstring 自认「回滚重激活旧版本会把其激活戳前移(边角情形,不在本块生效路径)」——但 `scripts/activate_charter.py` 的白名单**明确保留 `v1.3` 作「唯一合法回退目标」**,即回滚是设计内的、预期在事故场景使用的路径,不是边角。
- 复现(临时库实测,本审计脚本):K1 激活 07-20、v1.3(4 万上限)激活 07-25;07-22 一笔 3 万买入按 K1 判违纪 ✓。执行一次 `activate_version("K1")`(回滚)后,**同一笔历史成交改判 v1.3(single_cap 40000)= 违纪消失**;`config_governing_for_week(07-20 周)` 的周标签同步翻成 v1.3。原因:K1 的戳被刷新到回滚时刻(07-29),07-22 时点上「激活时刻 ≤ ts」的候选只剩 v1.3,而 07-25 之前的时段落进「早于所有激活 → 取最早激活版本」兜底 = v1.3。`reviews` 是幂等覆盖表,重传交割单即重算,历史周报会**静默**整段改判——这正是 2026-07-27 审计 🟡-3 封掉的那类洗白口,经回退路径重新打开。
- 修法方向:①最小修——`activate_version` 检测目标行已有旧 `activated_at` 时打印「⚠ 重激活将改写激活时间线,历史周复盘将被改判」并写 `charter_activation_audit.log`(复用 v1.3.3 留痕设施);②正解——激活历史另立 append-only 表(`strategy_activation_log(version, activated_at)`),`config_governing_at`/`activations_between` 改读事件流,一版本多戳天然可表达(brain 现有接口签名不用动)。

### 🟡-2 行业强度日更对「表 max 与目标日之间的交易日缺口」静默跳过——streak 桥接过洞、无告警,且新鲜度看板照样全绿

- 位置:`neckline/report/industry_strength_store.py::_resolve_targets`(只在 `tbl_max > targets[0]` 时向后延,**不向前补洞**;实测 `_resolve_targets([0709], tbl_max="0704")` 返回 `[0709]`,0707/0708 两个交易日的洞不进处理区间)+ `industry_strength_status`(只看 `MAX(trade_date)`)。
- 失败场景:16:05 日更某天失败(已有 ERROR 日志 + 补算命令,这一天当天的 16:35 报告会走保险丝、诚实披露 ✓)。**次日**日更成功:`_prev_persist` 视缺行为「不存在」,今天的 `persist_days` 直接接到洞之前的评定日——若漏跑日实为强度日,后续 streak **低报**(A2 hard_cut 该拦没拦);若漏跑日会断裂 streak,则**高报**(误拦)。同时 `MAX(trade_date)=今天` → `industryStrengthStale=false`、报告无任何披露——**与保险丝「显式披露后不拦」的设计相反,这是未披露的错数**。三路等价单测全部在「表被喂全」的面板上跑,该前提被打破时等价性不成立,而没有任何机制强制发现(`verify` 能抓洞但无人自动跑)。
- 缓解因素:修复路径本身是好的——只要有人跑当天 ERROR 日志里那条 `refresh --from <洞日>`,自动向后延会把洞之后全部重算回正(有单测锁)。缺的只是「无人跑时不该装没事」。
- 修法方向(任选其一,代价都是每日 1 个分区):① `_resolve_targets` 加一条——`tbl_max < targets[0]` 时把 `(tbl_max, targets[0])` 区间交易日并入(与「补历史自动向后延」对称的「向前自动补洞」);② refresh 检测到缺口时打 ERROR 带补算命令原文并把 `missing` 语义细分;③ 16:05 日更完成后顺手跑一次近 5 日窗口 `verify`,红了 ERROR。

---

## 二、观察项(🟢,不改判定结果或现状无实害,建议顺手处理)

- **🟢-3(⑥-A)冷却判据锚「再买日收盘」而非真实再买时刻**:`run_weekly_review` 冷却段用 `_charter_day_runs` 日粒度取 `cooldown_days`,交割单带真实时刻时也不用(模块 docstring 已自认「沿用整批算一次姿势」;plan 原文「锚再次买入时刻」)。现役各版 `cooldown_days=0`,真 no-op。另 `_cooldown_violation_in_week` 靠正则从违纪文案抠最后一个日期做周归属(自认脆弱)——文案格式一变即静默错分周。冷却若未来启用,建议一并把返回结构化(带 buy_date 字段)再说。
- **🟢-4(⑥-A)北京周一 00:00–08:00 窗口内发生 ≥2 次激活的伪时间线**:周初标签走 UTC 日期口径会把标签定为最后一版,`build_charter_timeline` 随后对窗口内激活产出「v3→v2→v3」形状的伪切换段(展示层瑕疵;成交都在 09:30 后,逐笔判定不受影响)。极端边角,记录备查。
- **🟢-5(⑥-A)golden 等价与切换周单测未覆盖禁买过滤跨切换**:无切换周逐位等价 golden 覆盖止损/单笔/并发/敞口四类;切换周逐笔单测覆盖单笔/止损/并发敞口。**`forbid_high_elasticity`——生产上真正切换过的那个字段**(K1 禁创业板 → v1.3.3 拆墙)没有跨切换单测,而「激活当日买创业板被误标」正是 P1-4 的原型假警报。机制上它与单笔上限共用同一 `buy_groups` 分组(`reconcile.py:978-980`),复合风险低,但建议补一条:激活前买创业板报违纪、激活后同日买不报。
- **🢂🟢-6(⑩)refresh 落行统一贴请求日 `day_key`**(`industry_strength_store.py:375-388`),不校验分区内 `trade_date` 与请求日一致——错分区文件会被静默重贴日期。真实分区单日单文件,风险低;加一行 filter 或断言即可。
- **🟢-7(⑩)`INSERT OR REPLACE` 不删除重算后消失的 (day, industry) 行**:数据修订后重刷某日,某行业从有到无时旧行残留(`verify` 的 streak 项能间接暴露)。罕见;要严谨可在 refresh 每日事务里先 `DELETE WHERE trade_date=?` 再插(幂等性不受影响)。
- **🟢-8(⑩)陈旧注释与实现矛盾**:`tests/test_industry_strength_store.py:88-89` 仍写「`rank(ordinal)` 在并列时由行编码顺序任意打散(模块 docstring 已声明)」——这是 tie-break 修洞**之前**的世界观,与已上线的确定性 tie-break(`_day_local_table` 先按 median 降序 + 行业名升序排定)矛盾,会误导后人。纯注释修正。另:fixture 刻意避开中位数并列的理由随修洞已不成立,可简化(非必须)。
- **🟢-9(⑩)Pass1→Pass2 之间若 16:05 日更插入**:`_prev_persist` 会把「已评定但 persist 尚未回填(NULL)」的 Pass1 行当 `prev=None` → 新日 streak 从 1 起。Pass2 全表重算会自愈;生产按探针纪律(收盘后跑、避开 16:00–17:00)不会发生——**执行 ⑩-D 时别让 Pass1/Pass2 跨过次日 16:05** 即可,提示写在这里备查。
- **🟢-10(⑩)门槛穿越场景无 fixture 覆盖(但对拍通过)**:三路等价 fixture 里各行业成员数恒定,「成员数跨过 `_MIN_MEMBERS` 当口(评定→未评定→评定,streak 不断裂)」这条最微妙的语义没有被 fixture 构造到。本审计临时脚本对拍:`_attach_persist` 与 `next_persist_days` 递推在穿越场景逐位一致([(True,1),(None,None),(True,2)] 两路相同)——**实现正确,只是测试形态缺口**,建议在 `_seed_two_industries` 里给某行业加一段中途跌破门槛的剧本。
- **🟢-11(P2-10/v1.3.1)lift 闸对小板块高波动**:3 只成员的板块里 1 只落在小行业,lift 轻易 >300 → 判主导。常量注释已自认「启发式,待实盘校准」,非缺陷,记录备查。
- **🟢-12(P2-10/v1.3.5)`TABLE_FLOAT_COLS` 机制不罩 ths 扁平文件**:`concept_data.py` 的 `ths_daily`/`ths_member` 走独立 `_atomic_write_parquet` 路径(有自己的 dtype 守门,①-C 单测),不经 `write_table_day`。这是设计内分工,非缺陷——提示后人别误以为全仓库 parquet 写入都被声明机制覆盖。

---

## 三、查过并放行的项(逐条说明凭什么放行)

### A. v1.4-⑥-A 周复盘章程逐笔判(施工者自报必审点逐条过)

1. **双向 naive 时区约定自洽** ✓。全部调用路径只造 aware 北京时刻:`trade_instant`/`day_close_instant`(`reconcile.py:100-124`,`tzinfo=CN_TZ`)、`_week_window`(`:615-620`)、逐笔/逐日/冷却/止损四处入口逐一核过,无一路径把 naive 交给 `config_governing_at`。`activated_at` 侧:生产唯一写入者 `brain._now()` 恒 UTC-aware;`db.py::_backfill_activated_at` 拷 `created_at`(同 `_now()` 产物);`scripts/charter_*.py` 走 `save_version`/`activate_version` → `_now()`。「naive 按 UTC 读」分支只剩手工 SQL 兜底,两侧相反约定各自 docstring 定死并各有单测(`test_config_governing_at_naive_input_read_as_beijing` / `..._naive_activated_at_read_as_utc`)。**未发现任何一条把两个约定用反的路径。**
2. **「等于激活时刻算新章程」边界** ✓。plan 原文「早于激活按旧、之后按新」——「等于」不属「早于」,取新方向与文本一致;与日粒度 `config_active_at`(激活日 ≤ ref 即 govern)真同向(两边界都归新)。单测双向锁(`test_config_governing_at_exact_activation_instant_counts_as_new` 恰好相等 → 新、早一秒 → 旧;reconcile 侧 `test_exact_activation_instant_counts_as_new_charter` 又用收盘兜底时刻复验一遍)。
3. **四判据锚点无漏改错锚** ✓。逐一核过 `run_weekly_review`:单笔上限 + 禁买过滤锚**买入时刻**(`buy_groups`,`:954-959/978-980`);止损锚**卖出时刻**(`rt.sell_instant`,`:936-949`,未平仓诚实跳过);并发/敞口按**日收盘时刻**切日段、每段比该段 cap(`_charter_day_runs` + 段标签,`:964-977`);冷却锚**再买日**所在日段(`:876-885`,见 🟢-3 的粒度保留意见);时间退出刻意**不读 config**(历史事实判据,`check_time_exit_discipline` 注释明确)。周标签 `strategy_version` 降级为纯标签、不再是判据入口,三处注释与 docstring 一致。
4. **收盘兜底的偏宽语义只影响激活当日、不外溢** ✓。`trade_instant` 兜底 15:00:激活时刻落在盘中(如生产真实的 14:36)时,当日无时刻成交全判新章程(偏宽,docstring 诚实登记,且**实测推翻了 plan 原文「激活都在盘后」的理由并写明真正依据**——这一点做得比 plan 严谨);其余任何日期,15:00 锚点与激活时刻恒在同侧,无外溢。交割单带真实时刻时兜底不生效(`test_explicit_trade_time_before_activation_still_old_charter`)。
5. **golden 等价与分段计数** ✓(留一条 🟢-5 测试缺口)。无切换周逐位等价(违纪内容+顺序+文案硬编码 golden + stop_discipline 分类 + 单段计数);切换周分段:`seg.start <= inst` 与 `config_governing_at` 的 `<=` 同界,switch note 的前后计数从 segments 累加(同源),文案/序列化/material 三层单测锁;半开窗口防相邻周重计(`test_activations_between_half_open_window`);「切换后 0 笔仍如实报」「同版本再激活不算切换」各有单测。周初标签(UTC 日期 `<` week_start)与逐笔解析在市场时段内不会互相矛盾(逐情形推演:标签为新 ⇒ 激活实刻 < 北京周一 08:00 < 一切成交;标签为旧且整周判新只能经窗口内切换表达)——除 🟢-4 极端边角。

### B. v1.4-⑩ P0-23 修复

1. **`next_persist_days` 递推边界** ✓(除 🟡-2 的缺口场景)。非交易日/缺分区 → 无行,`_prev_persist` 与 `_attach_persist` 同样按「不存在」处理;门槛穿越(评定→NULL→评定)streak 不断裂,两路对拍一致(本审计实验,见 🟢-10);`NULL≠0` 三列语义有专项单测(`test_thin_industry_lands_row_with_nulls_not_zeros`),读侧 `load_industry_strength` 只收 `industry_rank IS NOT NULL` 行、极端脏数据 persist NULL 按 0 读(方向仍是不拦,注释写明)。
2. **三路等价守门真锁住** ✓。`test_three_way_equivalence_full_vs_recurrence_vs_table` 三路逐位 + 「非平凡 streak ≥2」熔断线防空对空;`test_bootstrap_two_pass_equals_daily_refresh_bit_for_bit` 锁 bootstrap 与日更同物;grep 守门(`test_online_paths_never_reference_full_scan_entrypoints`)对四个在线文件禁两个现算名**连注释都不许出现**;`test_refresh_never_scans_all_partitions` 用 monkeypatch 地雷证明日更路径真不走 `scan_parquet`。绕过路径查过:四文件 import 面上只剩 store 与纯查表函数,`compute_industry_strength` 仅存于离线/对拍/CLI。
3. **保险丝降级方向与 `_DEFAULT_SECTION` 真同向、降级序确定** ✓。表缺行 → `industry_scores=[]` → `stock_persist_days=0` → A2(≥4)不触发 = 不拦,与 `sections.get(h.code, _DEFAULT_SECTION="avoid_flag")`「缺 DB 行保守打标不拦」同向;黄牌计数刻意**不用**默认值兜底(拦截判定与排序权重两个语义分开,注释写明)。降级序:`_sort_key` 末位 `code` 唯一 → 全序,与输入行序无关(`kept` 虽由 set 迭代构造,sort 后确定),`test_fuse_report_degrades_without_crash_and_is_reproducible` 两跑同序。四态(报告/A2/信息卡 null 非 0/问询台 evidence 明说 + 反面「表就绪后告白消失」)各有单测。
4. **幂等两条有实测锁** ✓。同日重跑 bit 级相同(`test_refresh_is_idempotent_same_day_rerun`,含整段重跑 + 单日重跑两种);补历史自动向后延(`test_refresh_backfilling_history_extends_forward_to_table_max`:抹脏中间日后只请求补该日,断言之后每一天全部回正 = 恢复 baseline + INFO 留痕)。
5. **rank tie-break 覆盖全部排名产出点** ✓。全仓库排名唯一产出点 = `_day_local_table`(`_compute_daily_table`/`compute_industry_strength` 均经它),先按 `(trade_date, median_ret 降序, industry 升序)` 排定再 ordinal;专项单测 `test_rank_tie_break_is_deterministic_regardless_of_row_order` 构造真并列 + 打乱行序。quantile 阈是值比较,天然与行序无关。遗留一条陈旧注释见 🟢-8。
6. **CLI 与体例、退路挂账提醒** ✓。`scripts/industry_strength.py` 放顶层(长期修补工具,同 `positions.py` 体例,docstring 写明理由);`bootstrap` 子命令超出 ⑩-C 原文 `{refresh,verify}` 但已在 §九 一行如实登记;`--recent-days` 退路打 WARNING 明示「早于起点走保险丝降级,必须记 §九 + ~/hz_info.md + §七 挂账」;`--pass1-only` 打 WARNING 明示必须补 Pass2;脚本头整段复述探针纪律。`verify` 三项自检 CLI 与单测共用同一实现,三项各有红绿单测,且「窗口首日 streak 依赖窗口外历史」有专项单测(`test_verify_streak_uses_history_before_window`)。

### C. P2-10 四个已发布版本的高危面(git 取证)

- **v1.3.1 行业闸 lift 判据**(`433ec42` → 现役 `intel_candidates.py:183-235`)✓:lift = 板内占比 ÷ 全市场占比,分母口径(板内含无 industry 成员稀释 / 全市场只计有 industry)注释与实现一致;比较带 `_INDUSTRY_GATE_EPS` 容差合项目纪律;「全市场查无该行业 → lift 未定义保守不通过」防除零;误杀纠正 + 噪音反例双向回归测试在 `test_intel_candidates.py`。留 🟢-11 小板块波动观察。
- **v1.3.3 章程二次激活 + 切换器闸 2 窄豁免**(`3441565` → 现役 `scripts/activate_charter.py:103-309`)✓:豁免 = (a) diff ⊆ `{forbid_high_elasticity}` **且** (b) 退出四 + 仓位四八字段逐位相同,两条独立正向核对;缺键按 `<缺>` 参与 diff(多一个陌生字段即硬拒,`test_new_unknown_field_counts_as_diff`);**豁免留痕真落**——留痕写在 `activate_version` **之前**,`_write_audit` 失败 → exit 4 拒绝激活(`test_audit_write_failure_blocks_activation`),留痕内容含理由/diff/持仓清单(`test_exemption_writes_audit_trail`),dry-run 不留痕不激活(`test_dry_run_with_exemption_writes_nothing`);无持仓路径行为逐行不变(`test_no_open_positions_behaviour_unchanged`);白名单 + `_CORE_EXPECTATIONS` 结构性护栏(加白名单漏核对表 → 拒绝)。「二次激活」指 v1.3→v1.3.3 两个独立版本行各自一次激活,每版一戳,时间线无冲突——但见 🟡-1:该脚本保留的 v1.3 回退目标一旦启用会踩时间线改写。
- **v1.3.4 provider 层 `search_query`**(`5df38c5` → 现役 `llm/providers/glm.py:32-57` + `tests/test_llm.py::TestSearchQueryOptIn`)✓:不传/None/空串/纯空白四态 payload 与 v1.3.3 基线**逐字节**相同(含 `json.dumps` 键序比对,基线四键 `"True"/"search_pro"/"True"/"5"` 字符串形态刻意冻结);传了只多 `search_query` 一键、其余四键取值与类型不变;超长截断 78 字符;`enable_search=False` 时忽略;Kimi 无参数位恒同。0 命中 WARNING 埋点有测试(`TestZeroHitTelemetry`)。「字符串 bool/int 已证伪勿再查」的实证结论钉在代码注释与测试 docstring 两处——防复查设计到位。
- **v1.3.5 `TABLE_FLOAT_COLS` 声明对齐 + 守门**(`a2ace7f` → 现役 `market_data.py:90-140`)✓:**`_VALID_TABLES` 新表漏声明真会挂**——`test_every_valid_table_has_a_declaration` 断言 `set(_VALID_TABLES) - set(TABLE_FLOAT_COLS) == set()`,机器可查无裁量;未声明表退回「向既有分区看齐」旧行为但打 WARNING 不静默(`test_missing_declaration_warns_and_falls_back`);「空元组(确无数值列)≠ 未声明」语义区分并有 `suspend_d` 实例注释;声明覆盖不到的新增列仍走旧兜底但不参与已声明列判定。候选管线保险丝(`intel_candidates.py:435-448` 对 `compute_sector_moneyflow` 的 try/except + 诚实降级)与 CLAUDE.md 铁律一致。留 🟢-12 边界提示。

---

## 四、处置建议排序

1. **🟡-1**:在 ⑨ 重新上云**之前**做最小修(activate_charter/activate_version 重激活警告 + 留痕)成本极低;正解(激活历史表)可挂 §七 P 级待办。理由:上云后第一次事故回退就是触发点,而事故时没人会记得这个坑。
2. **🟡-2**:同样建议随 ⑩ 生产侧一起修(`_resolve_targets` 向前补洞是 ~5 行改动 + 1 个单测),否则第一次日更失败后的次日,判据数据就开始静默漂。若赶工序,至少把「refresh 检测缺口打 ERROR」这半步做了。
3. 🟢 各条按顺手原则处理;🟢-5(禁买过滤跨切换单测)与 🟢-8(陈旧注释)建议本轮就补,各 ≤10 分钟。

## 五、审计过程账(为什么可信)

- 读全:`brain.py`(370 行全文)/`reconcile.py`(1156 行全文)/`material.py` 全文/`parse.py` 时刻解析段/`industry_strength.py`(428 行全文)/`industry_strength_store.py`(638 行全文)/`scripts/industry_strength.py` 全文/`activate_charter.py` 豁免全段/四个读侧调用点相关段/`glm.py` `_search_tools`/`market_data.py` 声明段。
- 测试:范围内四文件 170 过;逐个读了 ⑥-A 三组(TestTradeInstant/TestPerTradeCharter/TestNoSwitchWeekBitwiseEquivalence/TestCharterSwitchReporting)与 ⑩ 全部 26 个用例的断言体,确认锁的是规格点不是实现细节。
- 实验(3 个,均临时库/纯函数,零副作用):①回滚改写时间线反例(🟡-1 复现);②门槛穿越两路对拍(🟢-10 放行依据);③ `_resolve_targets` 向前缺口证据(🟡-2 复现)。
- 未做(如实):全量 1677 测试未重跑(与本范围无交集部分信任 §四 快照);⑩ 生产侧判据未验(尚未执行,见前提声明);Swift 客户端侧 `charterSegments`/`dataFreshness` 解码归契约线审计员。

---

## 六、复核结论(2026-07-29 · 两条 🟡 的修复复审,零代码修改)

> 复核对象:`b2e3673`(🟡-1 → 激活历史 append-only 事件流)与 `d6ca89b`(🟡-2 → 日更向前补洞 + 断口响亮失败)。方法同正文:读全 diff 与现役代码、逐条对我自己立的判据、重放我的原始反例、跑全量测试(**1705 过 + 2 skip**,与宣称一致)。

### 主件 `b2e3673`(🟡-1)——六点逐过

1. **append-only 真成立 ✅**。grep 全部生产代码(`neckline/` + `scripts/`):对 `strategy_activation_log` 只有 CREATE / SELECT / INSERT;唯一写入函数 `brain._append_activation`(只 INSERT,docstring 明言「任何 UPDATE/DELETE 都等于改写历史判定」);播种 `_seed_activation_log` 是 INSERT…SELECT。UPDATE/DELETE 仅存在于 `tests/`(`conftest.set_activation_timeline` 自我声明为测试专用重写,且解释了「重写逻辑住 tests/ 不住 brain」的理由;`test_brain.py:448` 的 DELETE 是模拟未播种老库)。既有测试对 `activated_at` 的裸 UPDATE 姿势已全部换成该夹具(grep 零残留)。
2. **`_activation_events` 边界形态 ✅**。同时刻两事件:SQL `(activated_at, id)` + Python 稳定排序 → 后追加者胜,有单测(`test_same_instant_events_last_appended_wins`);乱序写入(后插更早戳):Python 按解析时刻重排——本复核实验补插一条 07-18 事件,07-19 判 v1.3、07-22 仍判 K1 ✓;幽灵版本事件跳过 + WARNING(`test_event_pointing_at_deleted_version_is_skipped_loudly`,并注明「该段历史由上一条事件判定」);表空/表不存在回退单戳路径逐位等价——`test_single_activation_log_equals_legacy_stamp_path` 用 9 个时刻探针 + 4 个日期探针,有表 vs `DROP TABLE` 后结果逐位相同;`test_reads_tolerate_missing_log_table` 锁四个读入口不崩(读不触发迁移的既有纪律保住)。`config_active_at` 改用 `inst.date()`,与旧 `_activated_date` 的 UTC 日期口径逐情形等价(全格式核过)。🟢 一条 nit:同一解析时刻但**文本格式不同**的戳(只可能手工混写)tie 按文本序不按 id——生产戳格式统一(`_now()` 唯一写入者),不构成风险,记录备查。
3. **原始反例修复 + 双向端到端覆盖 ✅**。本复核原样重放正文 🟡-1 的反例(K1@07-20 / v1.3@07-25,回滚 K1):`before == after == ("K1","K1")`(修复前 after 是 v1.3/4 万 = 洗白)。端到端两个方向都有且都防了空对空:**洗白方向** `test_reactivating_the_earliest_charter_does_not_erase_its_violations`(正是我报告的形态——重激活最早版 K1,其治下 3 万违纪回滚后仍在,并显式断言违纪存在);**假警报方向** `test_rollback_reactivation_does_not_whitewash_history`(回滚 v1.3 后逐周快照逐位不变,且前置锚定「第一周有违纪、第二周没有」防两边都空);外加 `test_rollback_shows_up_as_a_new_switch_in_the_current_week`(回滚如实成为**当周**一次切换,历史周零多余切换)。`activate_version` 重激活时打 WARNING(本复核实验中真实触发)。
4. **播种在生产形态下正确 ✅(附一条诚实边界)**。生产 `strategy_versions` 带戳行 = K1(07-20)/ v1.3 / v1.3.3(07-27 14:36),**没有任何版本在迁移前被激活过两次** → 「每版最后一戳」就是完整真历史,一次性播种无损重建时间线;`ORDER BY activated_at, version` 保证事件 id 按时间升序。幂等判据「本表为空才播」+「从未激活的版本不播」有单测(`test_seeded_from_legacy_activated_at_and_idempotent`,含重跑不灌);与 `_backfill_activated_at` 的先后顺序写死在 docstring 并在 `_migrate_columns` 里落实。诚实边界:若迁移前某版本真被激活过两次,播种只能恢复最后一次——生产无此情形,且 docstring 已写明播种是一次性迁移、不是逐对补齐(补齐反而会让手工改列注入伪事件,取舍正确)。⑨-B 迁移清单已加本表(commit 声明)。
5. **「回滚留痕失败不拦」的取舍——认 ✅**。三条理由成立:①回滚**没有放宽任何闸**(白名单/闸2/闸3/核心值逐条照跑),留痕性质是事后审计便利,而闸 2 豁免留痕是**放宽一道闸的成立前提**——非对称有原则依据,不是随手不同,且理由写死在代码注释里;②判定层已回滚安全,拦回滚保护不了任何判定正确性;③事故现场把用户挡在错误章程上的代价大于少一条文件日志,且另有两道不依赖文件的痕(终端横幅列此前每次激活时刻 + `brain.activate_version` 的 WARNING)。测试双向锁:留痕成功时内容断言(`test_rollback_warns_and_leaves_trail`)+ 写失败仍 exit 0 且激活生效(`test_reactivation_trail_failure_does_not_block_rollback`)。
6. **既有断言零删改 ✅**。`git show b2e3673` 中 `test_review_reconcile.py` 与 `test_brain.py` 的**被删除行里 assert 行数均为 0**(删除的 26 行全是夹具/注释);GOLDEN 硬编码串原样;范围内测试文件全绿,全量 1705 过 + 2 skip。
- 🟢 附注(非回归,提示勿误解):事件流只版本化「**哪版何时现役**」,不版本化「该版参数当时是什么」——对现役版本 `save_version(activate=True)` 改参数仍会改写该版 `rule_json`(历史时刻解析到该版时读到的是现在的参数)。这与旧模型相同、非本次引入;「改章程走新版本名 + 四道闸」的既有纪律仍是唯一防线,别把事件流当参数快照史。

### 副件 `d6ca89b`(🟡-2)——原判据逐条对

- **向前补洞 ✅**:`_resolve_targets` 新增 ② 分支(严格介于 `tbl_max` 与目标日之间的交易日并入),我的原始探针原样成为回归测试(`test_resolve_targets_fills_the_gap_forward`:`[0709]/tbl=0704 → [0707,0708,0709]`,并锁既有两种形状不变)。
- **禁止静默桥接 ✅**:命门测试 `test_refresh_fills_hole_and_streak_is_not_bridged` 的判据不是「补了几天」而是**补洞后整表与一次干净全量刷新逐位相同**——streak 一旦桥接,末段 `persist_days` 整体偏小、逐位断言先炸。这正是我要的证明形状。
- **补不动就响亮、不许对错数报绿 ✅**:分区缺失补不动时 `refresh` 返回 `holes` + ERROR 带补算命令原文;CLI `refresh` 据此 **exit 1**(注释点名「跑过了 ≠ 补齐了,别让 systemd Result=success 骗人」——与本项目 timer 铁律同源);`daily_update` 对「落了行但有断口」单开 ERROR 分支不被 INFO 盖过;`industry_strength_status` 近 21 日有断口即 `stale=True`(**哪怕 lag=0**,`test_unfillable_hole_is_loud_and_not_reported_fresh` 逐项断言),`to_public_dict` 仍三键(⑩-F 契约不膨胀,细节由 `note()` 承担且「断口失真」与「落后」分成两句文案)。
- **「断口只认两头有数据的中间洞」——认 ✅**:表尾未落的今天由 `lag_days` 披露(测试:lag=3 → stale,hole=0)、表头之前的远古由保险丝披露,拿边界当断口只会天天假警报;与 `verify` ①「点名区间显式断言」的分工写明并共用取数原语 `_days_present`,不是两套口径。顺带:正文 🟢-8 的陈旧注释也已改正(fixture 注释明写「并列已由确定性 tie-break 定序,互不相等只为剧本可读」)。
- 🟢 两条保留意见(均不推翻结论):① `status` 断口回看窗口 = 21 个日历日——理论上一段 >21 天不间断强势可让更老的断口继续影响今日 streak 而不再触发 stale;实务上 streak 高频重置、refresh 侧 ERROR 不受此窗口限制、全历史体检归 `verify`,可接受,记录在案。② 直接 API 以稀疏列表调 `refresh([老日, 今日])` 且 `tbl_max` 落在两者之间时,向后延分支不填 `(tbl_max, 今日)` 段——但收尾断口检查必 ERROR + `holes` 非空 + CLI exit 1,不静默;CLI 与日更的真实入参形态(连续区间/单日)不会触发。

### 复核裁定

两条 🟡 **均已按我立的判据修复到位,准予销项**;两处刻意取舍(回滚留痕不拦 / 断口定义排除边界)**均认可**。新增 🟢 附注三条(J1 混格式同刻 tie / J1 参数非快照史提示 / J2 21 日窗口与稀疏入参形态)记录在案,不阻塞 ⑨。正文第四节的处置建议第 1、2 条就此关闭;第 3 条(🟢-5 跨切换禁买单测、🟢-8 陈旧注释)已随修复顺带完成(`test_forbid_high_elasticity_across_switch` + fixture 注释改正)。
