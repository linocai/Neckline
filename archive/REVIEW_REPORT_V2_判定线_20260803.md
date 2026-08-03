# V2.0.0 ①–⑬ 判定线独立审计报告(2026-08-03)

- **审计人**:@reviewer(独立,从零读代码,不采信完工记录自我陈述)
- **范围**:判定语义与纪律红线 —— 第〇原则四锁(§2.8-C 精确化)、选股链判定语义(④–⑨)、
  哨兵与纪律边界(⑧⑪ + ⑬ 哨兵改动)、确定性与可复现。**契约/数据线(表三律/事务/API 契约/
  DTO/拆除完整性)由另一位 reviewer 并行负责,本报告不覆盖。**
- **方法**:PROJECT_PLAN §二/§五 与 CLAUDE.md 坑清单逐条对照;核心判定模块全文精读
  (`selection/tier.py`、`aggregate.py`、`member_hygiene.py`、`verification_rules.py`、
  `basket_card.py`、`scan/seeds.py`、`scan/leader.py`、`scan/stage.py`、
  `sentinel/basket_verify.py` + store、`engine.py`、`precall.py`、`invalidation.py`、
  `universe.py`、`attention.py`、`custom.py`、`notify_kinds.py`、`api/notify.py`、
  `llm/json_block.py`、`eval/exit_sim.py`、`placebo.py`、`metrics.py`);哨兵纪律文件做
  **diff 级核对**(基线 `bb0d72b` = V2 开工前最后一 commit);写探针脚本实测绕过路径
  (scratchpad,不入仓);全量 `pytest tests/ -q` 复核 = **2706 passed + 2 skipped + 0
  failed**(与任务书基线一致);④ 两条三路等价测试单独 3 连跑全绿(全量下偶发失败未复现,
  见 🟡-6)。真实 `data/neckline.db` 与生产零接触,仓库零代码改动(仅本报告)。

**总评**:四锁的主干是真的锁住了 —— 机械分访问锁是运行期证伪而非注释自觉、跨档拒收无绕过、
`basket_falsified` 无 kind 无调用点、`falsified` 定格在 store 层强制、⑨ 判分唯一源以冻结源
文本+行为双对拍锁死、种子确定性修复(`3af64e0`)后四类全部收口。**没有发现可直接绕过纪律
红线的 🔴 级缺陷。** 但有 6 处 🟡:两处是判定语义真缺陷(验证侧部分缺数静默降格、盘前剧本
核对漏除权锚检测),两处是「LLM 产出经间接通道进入哨兵/推送触发输入」的权威冲突(plan 自身
两处条文打架,须 planner 收口而非 builder 背锅),一处守门单测锁错模块名(红线守门本身可
绕),一处确定性主张未闭环(④ 三路等价测试全量下偶发失败,已挂账未定位)。

---

## 🔴(判定错误 / 红线可绕,必须修)

**无。**

---

## 🟡(真缺陷,应修)

### 🟡-1 验证侧「两条 AND」在成员部分阈值缺失时静默降格为「单条 AND」,无任何 flag

- **位置**:`neckline/sentinel/basket_verify.py:184-193`(`_judge_side`:`judged` 只收集
  可判条件,`all(judged)` 在子集上取真)+ `neckline/selection/basket_card.py:298-309`
  (`build_verification_spec` 成员行 `holds_ma20` 可为 `null`)。
- **复现**(scratchpad 探针实测):2 只成员篮,成员 B 的 D0 `ma20` 缺失(面板缺口)→ 冻结
  spec 里 `holds_ma20: null`;D+1 成员 B 收盘仅高于 D0 收盘 → `verify=True` 计入
  `verify_hits`,**`flags=[]` 零披露**。`min_hit=1` 时该篮可仅凭这只"半判"成员进 `verified`。
- **问题**:⑦-b-B 定死「验证 = 收盘 ≥ D0 收盘 **且** ≥ D0 MA20 两条 AND」;MA20 缺失时第二
  条无从确立,现实现等于把该成员的验证判据静默放宽为单条。且**方向不对称**:同一成员的失效
  侧复合条件(`close_below_ref_and_ma20`)因「任一子阈值 null 整条不判」而变**难**触发,验证
  侧却变**易**——系统性偏向 `verified`。`FLAG_SPEC_LEVELS_MISSING` 只在**两侧全部**不可判时
  才打,部分缺失完全无痕。
- **影响面**:`verified`/`partial` 是参考态,不触发纪律;但 ⑨ 评价引擎的验证率是 P3-34 回看
  条件集的唯一依据 —— 被半判成员污染的验证率会误导条件集校准(与 ⑧-E-A 第 4 条同一句话:
  拿被污染的数据去校准判据 = 错上加错)。
- **修法方向**:验证侧改为「`require` 中任一条判不了(`None`)→ 该成员验证侧整体 `None`
  (不计命中)+ 新 flag `spec_levels_partial`」,与失效复合条件的「半条判不了就整条不判」
  对齐;改动属条件集语义变化,**须 bump `VERIFICATION_RULESET_VERSION`**,并让 ⑨ 分层可见。

### 🟡-2 盘前篮子剧本核对(⑬-7)漏掉 ⑧-E 除权除息锚失效检测,分红季会产出成员级假警报并进 9:26 推送

- **位置**:`neckline/sentinel/precall.py:204-241`(`judge_gap_up_invalidate` /
  `judge_low_open_falsify` 直接拿**冻结的 D0 标度价**〔`ref_close` / `stop_line` =
  D0 收盘 × (1−stop_pct)〕与 D+1 **原始开盘价**比较,全模块 `pre_close` 只用于文案,
  无任何锚有效性校验)。
- **复现路径**:成员恰在 D+1 除权除息(⑧-E 完工记录的真实样本 603409.SH:D0 收盘 31.70,
  D+1 除权参考价 21.07)→ 竞价开盘约 21 元,`quote.open <= stop_line(30.1)` 必真 →
  「开盘即在冻结失效位下方」假警,进 9:26 盘前汇总推送(`kind='precall'`,重要不紧急级)。
- **问题**:⑧-E 裁定书量化过频率(分红季每 1–3 天一个被验证成员中招)并在
  `basket_verify.py` 修了同一类错;⑬-7 晚于 ⑧-E 落地,把同一个锚错配在另一个消费方**重新
  引入**。检测器现成(`basket_verify._anchor_mismatch`,盘中数据 `Quote.pre_close` 也现成)。
- **修法方向**:`load_member_scripts` / 两个 judge 函数入口加同款检测:
  `pre_close ≠ ref_close`(带 EPS)→ 该成员两类判定跳过 + 汇总里如实标「疑似除权除息,
  冻结锚今日失效」;⛔ 不做自动 rescale(与 ⑧-E 同理)。
- **附注**:第 4 类「持仓大幅低开」用 `buy_price×(1−stop_pct)` 同样无除权防护,但那是 V1
  既有行为(持仓哨兵 `stop_approach` 同理),非本次引入,建议一并挂账不算本条。

### 🟡-3 `take_profit` 立即级 APNs 的触发阈值 = LLM 产出的离场参考区间(未夹逼),与 §2.0 第 1 条 / §2.8-C-2(a) 的字面红线冲突未收口

- **位置**:`neckline/sentinel/engine.py:543-585`(旁路 E:`price ≥ exit_low` 即推)+
  `neckline/sentinel/holding.py:121-146`(`check_exit_reference_reached`)+
  `neckline/positions_entry.py:158-233`(开仓自动继承卡上 `exit_reference` 进
  `position_plans`,无用户确认环节)+ `neckline/selection/basket_card.py:471-481`
  (`clamp_exit_reference` **只做格式校验**,无涨跌停夹逼〔有理由〕、也无任何 sanity 下限)。
- **问题**:§2.0 第 1 条逐字写「LLM 产出的买入 / **离场参考区间**……**一律不进哨兵判据、
  不进推送**」;§2.8-C-3 说「推送……触发条件永远是机械的」。现在:LLM 给出的
  `exit_low/exit_high` 数字(仅格式校验)→ 开仓时**自动**继承进 `position_plans` →
  直接成为一条**立即级锁屏推送**的触发阈值。比较动作是机械的,但**阈值本身是未经任何机械
  闸的 LLM 数字** —— 极端例:LLM 给 `exit_low=0.01`,买入后第一拍即触发推送。
- **定性**:这**不是 builder 违规** —— 2026-08-03 用户拍板开 kind,且 plan ⑪-B 自己写明
  「V2 的正确指向 = 篮子卡的离场参考区间或 position_plans 继承的离场参考」。是 **plan 两处
  权威正面打架且未按自家标准收口**:⑪-B 对 `basket_falsified` 那条明文要求「要开得三处红线
  一起改」,而 take_profit 这条开闸时 §2.0 第 1 条与 §2.8-C 均一字未动。
- **修法方向**(三选一,须 planner 裁定):① 把「经开仓继承、已落用户 `position_plans`
  的离场参考视为用户计划的一部分,可作 take_profit kind 触发源」这条豁免**逐字写进
  §2.8-C**(照 basket_falsified 条的体例);② 继承时对区间做机械 sanity 闸(如
  `exit_low ≥ D0 close`,不合格即该票此 kind 不武装 + 如实标);③ 改为用户在客户端确认 /
  修订过计划后 kind 才武装。现文案已带「仅供参考,不是止盈信号」,①+② 组合代价最小。

### 🟡-4 退潮红色刹车「主线板块跳水」触发器的样本改成了 LLM 选出的 T1/T2 篮子成员 —— LLM 产出经间接通道进入纪律判定输入

- **位置**:`neckline/sentinel/engine.py:139-151`(`_hot_sector_peer_returns` 遍历
  `wu.targets`)+ `neckline/sentinel/universe.py`(`targets` = D0 冻结 T1/T2 篮子成员)+
  `neckline/sentinel/retreat.py:286-291`(`hot_sector_avg_chg ≤ sector_dive` 是红色刹车
  三路触发之一,红色 = 全天禁开新仓 + 立即级推送 = 纪律层动作)。
- **问题**:V1 该样本 =「关注池里命中今日热门板块标签的候选」,候选是**机械生成**(LLM 无权
  改候选去留);V2 换成 T1/T2 篮子成员 —— 成员是 **LLM 在白名单内挑选**的(⑤),哪几只票、
  多少只进这个均值样本由 LLM 的选择决定。§2.8-C-2(a) 说「LLM 产出的自由文本与数字一律不进
  哨兵判据」;2(b) 的豁免只覆盖「盯谁 = 注意力分配,**每只票的判定阈值**仍只来自章程与机械
  spec」—— 但这里被 LLM 影响的不是单票阈值,是一个**聚合纪律触发器的样本组成**,不在 2(b)
  豁免的字面范围内。⑬ 完工记录如实登记了这次换血(设计判断 2,「阈值未动」),但没对拍
  §2.8-C。
- **缓解因素**(降低但不消除):三路触发之一;样本 ≤ ~21 只且票源限于扫描层白名单;方向上
  LLM 既可能诱发也可能钝化该触发器,无单向套利。
- **修法方向**:① 样本改为机械派生集合(如「T1/T2 篮子所声明种子的**原始成分**(机械)∩
  关注池」或「当日 `limit_cluster_daily` 全体成员 ∩ 池」,不经 LLM 成员精选);或 ② planner
  把「篮子成员作为主线代理样本」的豁免逐字写进 §2.8-C-2(b)(说明为何聚合样本不算判据)。
  二者其一,不能维持现状不表态。

### 🟡-5 「篮子验证不接持仓 / 不进推送」的守门单测封禁了一个不存在的模块名,真实推送路径不在禁入清单 —— 红线守门本身可绕

- **位置**:`tests/test_sentinel_basket_verify.py:463-476`
  (`test_verification_never_touches_positions_or_push_channels`)。禁入清单为
  `neckline.sentinel.channels` / **`neckline.push.notify`** / `neckline.sentinel.positions` /
  `neckline.sentinel.holding` —— 其中 `neckline.push.notify` **不存在**(实际布局:APNs 层
  = `neckline/push/apns.py`,措辞/扇出层 = `neckline/api/notify.py`)。
- **绕过路径**:未来任何人往 `basket_verify.py` 里加
  `from neckline.api import notify` 或 `from neckline.push import apns` 把 `falsified` 接进
  推送,这条守门单测**不会挂**。当前代码干净(已核实两文件零推送 import),但守门的意义
  就是防未来 —— CLAUDE.md 坑条明文「守门单测扫 import」,现在扫的是空靶。
- **修法方向**:禁入清单改为
  `{neckline.sentinel.channels, neckline.api.notify, neckline.push.apns, neckline.notify_kinds,
  neckline.sentinel.positions, neckline.sentinel.holding, neckline.positions_entry}`,并加一条
  反向存在性断言(清单里的模块名必须真实存在,防再次锁空靶)。

### 🟡-6 ④ 扫描层三路等价测试在全量套件下偶发失败(已挂账未定位)—— 「同输入两跑逐位一致」的确定性主张未完全闭环

- **位置**:`tests/test_scan_cluster.py:209` / `tests/test_scan_corr.py:194`
  (`test_bulk_vs_day_by_day_vs_readback*`);⑪ 完工记录「🟡 块外发现」登记:全量套件下
  偶发失败、单独跑恒绿,疑似跨用例状态污染或 polars 分组顺序残留,未修。
- **本次核查**:全量一跑 + 目标文件 3 连跑均绿,**未复现**;但「偶发」意味着 ④ 事实表
  (`limit_cluster_daily`/`corr_matrix_daily`)的批算≡逐日≡读回等价性在某种环境序下**可能**
  不成立 —— 而 `cluster_key`/`rs_rank` 是 ⑤⑥ 判定的直接输入。种子层(`seeds.py`)已用
  `_sort_by_seed_key` 收口,不依赖上游行序,故即便上游行序漂移,**进聚合的种子集合与顺序
  仍确定**;风险残留在测试比较层还是产出层,未经定位不能下结论。
- **修法方向**:⑯ 上生产前定位一次(pytest `-p no:randomly` 若有随机插件 / 固定顺序复跑 /
  在失败现场 dump 两侧 frame diff);若根因是测试内 frame 比较未先 sort,属测试缺陷,改测试;
  若是产出层顺序敏感,必须修产出。**不定位就带上生产,等于把「逐位可复现」降级为口头主张。**

---

## 🔵(便宜改进)

1. **`evaluable_members` 冻进 spec 但无任何消费方,且 docstring 承诺了未实现的语义**:
   `basket_card.py:293-297` 写「若某成员 require 里的阈值全为 null,该成员本次不计入分母
   (见 evaluable_members)」,但 ⑧ 的 `min_hit` 取的是冻结的 `min_members_hit`(按全员数
   算),从不读 `evaluable_members`。实际行为(全 null 成员照占分母 → 偏向 `unclear`)是
   保守方向、符合 ⑦-b「缺数据多到够不着门槛 → unclear」,**该修的是 docstring**,别让后人
   照注释"补全"出第二套门槛。与 🟡-1 一并处理最省。
2. **种子截断优先级 = crc32 任意序**:`scan/seeds.py:284-291` 修复确定性时把四类种子一律按
   `seed_key`(crc32)升序,`hot_industry` 因此失去 `industry_rank` 语义序;⑤ 只取前 20 颗,
   某类内部超过剩余额度时,进聚合的是"crc32 恰好小"的而非"最强的"。确定性已达成,但截断
   语义任意。建议:排序键改为「语义主键(行业名次 / cluster_size 降序)→ seed_key」,
   确定性不减、截断变得可解释。
3. **`CARD_SYSTEM_PROMPT` 未内嵌 `TIMELINESS_RULES`**(`basket_card.py:493-538`):⑤⑥⑪ 的
   system prompt 全部内嵌,⑦ 的卡 LLM 段只有 user 消息首行日期锚。影响低(不联网、证据自带
   日期),但与 §五铁律「日期锚 + 时效纪律」的措辞不齐,补一行即可。
4. **`leader.py:19-27` docstring 单位错**:tie-break 说「成交额(元)」,`daily.amount`
   实为**千元**(TuShare 口径,`aggregate.py:784-787` 写对了)。组内序不受除法影响,纯注释
   错,但这是 CLAUDE.md 点名过的单位坑,注释错会误导下一个人。
5. **`tier.py:371-394` `_dim_tradability` 的一字板识别在 `daily` 分区缺失时静默降格**:
   `limit_derived` 非空而 `daily` 空时,`tradability_available=True` 但 `one_word` 恒空 →
   一字板按「开过板」半罚(0.5)而非全罚(1.0),无 flag。极端数据缺口情形;建议
   `daily` 空且有涨停命中时该维走 `tradability_missing` 中性分。

---

## 🟢(观察项 / 正面核验记录)

1. **哨兵纪律分支 diff 级核对通过**(基线 `bb0d72b` → HEAD):`retreat.py` / `circuit.py` /
   `intraday.py` / `dedup.py` / `channels.py` **零字节改动**;`holding.py` 纯增量(新函数
   `check_exit_reference_reached`,既有三条子检查与 `evaluate_holding` 签名未动);
   `positions.py` 纯增量(四枚新卖出标签);`invalidation.py` / `precall.py` 的搬迁常量与
   V1 原值**逐字核对相等**(`LOW_OPEN_PCT=-0.02`、`VOL_RATIO_LOW/HIGH=0.8/3.0`、
   `MIN_STRUCTURAL_ELAPSED_MINUTES=5`、`PRECALL_GAP_UP_INVALIDATE=0.03`、竞价量双阈)。
2. **四锁主干核验通过**:`_TIER_SCORE_INPUTS` 访问锁是**运行期**证伪(记录访问键的 dict
   子类断言 `accessed == 白名单` 且与 `_LLM_PROVENANCE_KEYS` 不相交,特征行里真装着 LLM
   字段作可证伪对象);`resolve_weights` 缺维/多维双向 fail-loud,堵死"包塞 LLM 维度"
   进机械分的路;⑥ 跨档拒收无绕过(提案缺 `tier` 键时名次仍被钉死在机械档内,`rank` 越界 /
   bool / 抢位 / 重复逐码拒收,未指定篮子按机械序回填 → 可复现);`basket_falsified` 无
   kind、无空开关、`push_event` 单扇出 + 未登记 kind 抛 `ValueError`;⑦-K7 标注件三个标签码
   在 `neckline/sentinel/` 与 `tier.py` 全域 grep 零命中(四不成立)。
3. **⑤ 两道机械闸与证据三态**:白名单闸 = 「实际展示给 LLM 的清单」(卫生线过滤在截断之前,
   fail-closed 分支下 kept=∅ 时任何提案必拒);角色对拍闸「unknown 不是角色」不误判冲突;
   「检索跑过零证据 → 不成篮」与「没搜成 → 篮子照出 + search_unavailable」判据落在
   `_evidence_for` 第三返回值,与 plan 自洽读法逐条一致;提案形状变异(非 dict 成员 / 列表
   ts_code / 未知种子 / 重复成员 / 非法角色)全部落对应拒收码,无静默路径。
4. **⑧ 状态机**:`falsified` 定格在 **store 层**强制(`append_if_changed` 拒写、`append_row`
   改写为 falsified + `latched_over` 留档),不依赖调用方自觉;表 append-only(模块内无
   UPDATE/DELETE);盘中/EOD 共用 `evaluate_specs` 唯一判定函数,锚全取卡内冻结值(改现役
   config 不影响判定,有单测);「还没判 / 判了是 unclear」三路读法唯一实现。⑧-E 锚检测先于
   条件判定、两侧不计、`adj_factor` 交叉确认三态(变 → 除权不报警 / 未变 → 故障 WARNING /
   缺行 → unconfirmed 不猜)、⛔ 不自动 rescale —— 与裁定逐条一致。
5. **⑨ 判分唯一源**:全仓 `_sim_one` 唯一定义在 `eval/exit_sim.py`,`research/h9_exit_reform`
   反向 import 再导出;对拍单测双层真实(冻结源文本 vs `inspect.getsource` 逐字 + 冻结源
   `exec` 独立 Broker 行为逐位);安慰剂臂种子 `zlib.crc32(f"{d}|{pack}|{arm}")` 无进程盐,
   判分一律调 `fill_and_score`(placebo 模块零退出逻辑);小样本结论闸
   (`MIN_CONCLUSION_DAYS=10`,`Verdict.conclusive=False` 只报样本数)落实。
6. **确定性体例**:`assign_tiers` `(分数降序, basket_key 升序)`;主归属 lift 达标篮比较 +
   全不达标确定性兜底;`rs_rank` 三级 tie-break(RS 降序 → 成交额降序 → ts_code 升序)先排
   定再 ordinal;`driver_slug`/`basket_key`/`seed_key`/`cluster_key` 全走 crc32;⑧ 审计
   时间戳(`computed_at`/`observed_at`/`first_falsified_at`)是登记过的双跑合法差异。
7. **⑬-7 换血核对**:盘前判定只借冻结价位(`ref_close`/`stop_line`),不碰
   `verification_rules` 条件集与 ruleset 版本;证伪哨兵 spec 为零入参全局常量,对象换血
   逻辑零改动的说法成立(除 🟡-2 的锚缺口)。⑪ 三条新 kind 的去重 `event_key` 均为无易变量
   固定串(`stop_approach`/`sector_dive`/`exit_reference`/`fade`/`decoupled`/`shock`/
   `basket{id}`),当日每票每 kind 一次成立;custom_alerts 的 `alert{id}#{n}` 序号键是
   `max_fires`+冷却管辖下的刻意设计,不是去重失效。
8. **已挂账债本次核实仍未修(如实转记,不重复立案)**:`llm/news_scan.py` 缺
   `prompt_context` 日期锚(CLAUDE.md V2-② 条已登记);⑥-b 质量线单调性校验只比字面键
   (pack.py 完工记录已登记范围限定);crc32 种子键碰撞时退回构造序(seeds.py docstring
   已登记,概率级可忽略)。
9. **全量测试**:`python -m pytest tests/ -q` = **2706 passed + 2 skipped + 0 failed**,
   与 ⑬ 完工基线一致。

---

## 建议处置顺序

1. 🟡-5(改守门单测禁入清单,十分钟级,先把红线守门变成真的);
2. 🟡-1 + 🔵-1(验证侧语义 + docstring 同批修,bump ruleset 版本,⑨ 分层可见);
3. 🟡-2(precall 复用 ⑧-E 检测器,分红季前必须落);
4. 🟡-3 / 🟡-4(**两条都是 planner 级权威收口**,builder 不许自行拍:要么改触发源/样本为
   机械件,要么把豁免逐字写进 §2.8-C —— 照 basket_falsified 条「三处一起改」的自家标准);
5. 🟡-6(⑯ 上生产前定位一次,不带病割接)。

> 高危区提醒:🟡-3 / 🟡-4 触碰推送与纪律判定输入,按项目惯例建议主会话对这两条再复审一遍,
> 取并集。
