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

> **销项状态(@builder-pro 修复批次,2026-08-03)**:🟡-1 ✅ · 🟡-2 ✅ · 🟡-5 ✅ · 🔵-1 ✅。
> **未修**:🟡-3 / 🟡-4(planner 已裁定,§五 新增 ⑪-D 与 ⑧-F,另派施工)、🟡-6(⑯ 上生产前
> 定位,已挂 background task)、🔵-2/3/4/5。逐条标注见各条目下的 `✅ 已修` 行。
>
> **🔍 reviewer 销项复核(2026-08-03 晚间,独立复验)**:**六张 🟡 全部销项 ✅**
> (🟡-1/2/5 探针复跑治愈实证;🟡-3 → ⑪-D 记名豁免 + 两道机械闸;🟡-4 → ⑧-F 红线闭合 +
> ⑧-G 灵敏度修复;🟡-6 → P1-36 根因结案,确定性主张未破)。⑪-D / ⑧-F / ⑧-G 三批新改动
> 增量审计:**新开 1 🟡(N1)+ 1 🔵(N2)+ 2 🟢**,0 🔴。全文见文末「销项复核与增量审计」节。

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
- **✅ 已修**(commit `0f885b2`):照修法方向,并把「一侧结论怎么合成」**上收到
  `verification_rules.combine_side()`** —— 「判不了怎么算」与「什么算命中」同样决定判据松紧,
  同属条件集单一源;⑧ 只负责代入观测,`basket_verify` 那条「不写任何阈值、不定任何门槛」的
  纪律对这套读法同样成立。合成读法取 **Kleene 三值**(AND 任一 False→False,否则任一
  None→None;OR 对称):既不放宽 AND(原「扔掉不可判再取 all()」),也不丢掉「已经确定跌破
  D0 收盘」这种能下定论的否定 —— 「有 None 就整侧不判」会犯后一种错。失效侧对称核查过:
  改后 OR 侧在「已判的全 False、还有一条判不了」时同样返 `None`(计数上仍不加分,但证据里
  分得开「确实没破位」与「今天根本判不了」),有专门单测锁。新 flag `spec_levels_partial`
  (成员级 + evidence 顶层名单)与「两侧全 null」的 `spec_levels_missing` 分开;evidence 增
  `ruleset_version_engine`(卡上冻的版本 vs 判定代码当下版本,跨版本那几天 ⑨ 分层才不会记错
  层)。**已 bump `VERIFICATION_RULESET_VERSION = verify_ruleset_v2`**;ruleset 快照守门同步
  纳入 `side_logic` 真值表(光锁「什么算命中」锁不住这次的变化)。回归用 reviewer 的探针路径
  (成员 B 的 D0 `ma20` 缺失)。

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
- **✅ 已修**(commit `47775cb`):照修法方向。`basket_verify._anchor_mismatch` 公开为
  `anchor_mismatch`(全项目唯一一份检测器,⛔ 不许抄第二份 —— 抄一份 = 两处容差各自漂移,
  正是 ⑧-E 那场事故的复发路径),precall 侧薄封装 `member_anchor_stale(script, quote)`。
  两个 judge 函数**入口**先检测再判阈值(与 ⑧-E「检测先于任何条件判定」同序),编排层另标
  `member_ex_rights` + 落 `sentinel_events`;⛔ 不自动 rescale,文案写「疑似除权除息(或行情源
  异常)」——盘前没有 `adj_factor` 交叉确认能力,确诊留今晚 ⑧-E EOD 那一拍。该标记**不计入
  `summary_actionable`**(否则分红季天天推);竞价量能附注照常(比的是量,不读冻结锚)。
  9:26 汇总文案增「另 N 只疑似除权除息、冻结锚失效,今日未核对」——「没核对」与「核对过没
  异常」分得开。回归造了除权样本(用本报告点名的 603409.SH 真实数值)。
  **附注那条(第 4 类持仓低开)按建议未动**,仍挂账。

### 🟡-3 `take_profit` 立即级 APNs 的触发阈值 = LLM 产出的离场参考区间(未夹逼),与 §2.0 第 1 条 / §2.8-C-2(a) 的字面红线冲突未收口

> **✅ 2026-08-03 planner 已裁定并落 Plan(本条判得对,plan 缺口属我)**:采纳 **①+②** 组合 —— **记名豁免**(§2.0 第 1 条加附注、**§2.8-C-3 改写**成「判定动作永远机械 / 数值来源分两类」+ 四条前提 + 不外延)**加两道机械 sanity 闸**(新增 **§五 ⑪-D**):卡生成时要求 `exit_low > D0 close`(**语义驱动、零发明阈值** —— 压力位按定义在现价之上),不满足落新 reason `rejected_not_above_close` 不落卡;开仓继承时 `exit_low ≤ 实际成交价` → **该票此 kind 不武装**。**仍不夹涨跌停、也不加上界**(上界荒谬只会永不触发,无伤害)。**未采纳 ③(用户确认才武装)**:与 ⑩「表单退役、减摩擦」的立项主题相悖;改以 **per-position「不提醒」开关**(⑮ 应做项,非红线前提)回应「无用户确认环节」。裁定详见 `PROJECT_PLAN.md` §2.8-C-3 与 §五 ⑪-D。

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

> **✅ 2026-08-03 planner 已裁定并落 Plan(采纳 ①,⛔ 不写豁免)**:新增 **§五 ⑧-F**,样本改为**当日 ④ 扫描层机械种子(热点行业 / 暴起概念)的原始成分 ∩ 关注池**。**两处与本报告措辞不同,请注意**:① **不取「T1/T2 篮子所声明种子」** —— 那仍过了一道 LLM 筛(哪些种子被拿去建篮由它决定),只是把塑形上移一层,**必须不经篮子直接取 ④ 的种子集**;② **不同意「无单向套利」这条缓解判断** —— LLM 挑的是龙头 / 中军,而**领涨股在跳水日恰恰最抗跌**,样本偏向强者 → 均值跌幅被低估 → **红色刹车更不容易响**,偏差朝着「保护失效」**单向**,故按「从严」办。另把本条升级成 **§2.8-C-2(b) 的通用适用边界**(2(b) 只覆盖「盯谁」,⛔ 不覆盖任何**聚合**纪律触发器的样本组成;自查判据 = 「换一批成员这个数会不会变」)。样本不足 → **不触发 + 如实披露**(理由三条见 ⑧-F)。

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
- **✅ 已修**(commit `0f885b2`):照修法方向逐字落地,反向存在性断言用 `importlib.util.find_spec`。
  另补一处报告未提的漏网:原 AST 扫描只看 `ImportFrom.module`,`from neckline.api import notify`
  的 module 是 `neckline.api`、被 import 的名字才是 `notify` —— 这种写法**照样绕过**。已改成
  同时收集 `module` 与 `module.name`,三种 import 写法逐一验证过会被抓到。

### 🟡-6 ④ 扫描层三路等价测试在全量套件下偶发失败(已挂账未定位)—— 「同输入两跑逐位一致」的确定性主张未完全闭环

> **✅ 已结案,本条无需排工(2026-08-03 planner 核档:reviewer 未追溯到结案记录)**:§七 **[P1-36] 已于 2026-08-03 结案**(commit `a239bcc`,Plan 内该条目末尾已有 ✅ 结案段)。**根因 = (a) 类良性测试缺陷,已明确排除 (b)**:等价比较里误含 `computed_at` **秒精度审计戳**,业务列(`cluster_key`/`scope_key`/`corr`/`n_obs` 等)**永远逐位相同** —— 即**确定性主张本身没有破**,破的是测试的比较口径。`cluster`/`corr` 两侧已修;`leader` 侧同一缺陷的漏网已由定向快修补上(commit `53fab7a`)。**本条的核心担忧(可能是真不确定性)已被证伪。**

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
   **✅ 已修**(commit `0f885b2`):按实际行为改口 —— 明写「`evaluable_members` 只是留痕计数,
   不是第二道门槛,⑧ 从不读它;阈值全 null 的成员照占分母,这是刻意的保守方向」,并留下
   ⛔ 别照旧注释补全出第二套门槛的告诫。
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

---

# 销项复核与增量审计(2026-08-03 晚间,@reviewer 独立复验)

- **方法**:逐条重跑首轮审计的探针 / 绕过路径(scratchpad,不入仓);⑪-D(`66e218f` +
  `e1fe049`)、⑧-F(`66e218f` + `47f1a93`)、⑧-G(`93dc2b8`)三批新改动按"新代码从零审"
  做增量审计(它们碰推送触发阈值与纪律触发器读数);§2.0 / §2.8-C 修宪文与代码逐点对照;
  全量 `pytest tests/ -q` = **2801 passed + 2 skipped + 1 failed** —— 唯一 fail 为预告的
  挂钟脆弱测试 `test_sentinel_custom.py::test_cooldown_blocks_second_hit_and_expires`
  (本次跑在北京 18:38 > 14:48,必红,与销项无关),与协调基线一致。

## 一、六张 🟡 逐条销项结论

| 条目 | 处置 | 复核结论 |
|---|---|---|
| 🟡-1 验证侧静默降格 | `0f885b2` | **✅ 治愈(探针复跑实证)**:原探针场景下成员 B(`holds_ma20:null`)现为 `verify=None` + `flags=['spec_levels_partial']`,verify_hits 不再计入。修法上收到 `verification_rules.combine_side()` 走 **Kleene 三值**(AND 任一 False→False、否则任一 None→None)—— 比我建议的"任一条判不了即整侧不计"更周全:保留了"已确定跌破"这类决定性否定,两侧对称、不再单向偏 `verified`。`VERIFICATION_RULESET_VERSION` bump 至 `verify_ruleset_v2`,evidence 增 `ruleset_version_engine`(卡上冻版 vs 引擎当下版分开留痕,⑨ 分层可辨),ruleset 快照守门纳入 `side_logic` 真值表。 |
| 🟡-2 precall 漏除权锚检测 | `47775cb` | **✅ 治愈(探针实证)**:真实除权样本(603409.SH 31.70→21.07)下 `judge_low_open_falsify` / `judge_gap_up_invalidate` 均返 `None`,非除权日照常判。检测器公开为 `basket_verify.anchor_mismatch` **全项目唯一一份**(precall import,不抄第二份);9:26 汇总带 `member_ex_rights` 计数附注(「今天没核对」与「核对过没异常」分得开)。 |
| 🟡-3 take_profit 触发阈值 = LLM 数字 | `66e218f` + `e1fe049`(⑪-D) | **✅ 收口(权威冲突消除 + 机械闸落地)**:①**修宪**:§2.0 第 1 条加记名豁免附注、§2.8-C-3 改写为「判定动作永远机械 / 数值来源分两类」+ 四条前提 + **豁免不外延**清单(逐字点名买入区间 / 剧本 / 评语 / 问询 / `basket_falsified` 不得援引)——正是首轮报告要求的"写进宪法"。②**闸①**卡生成 `clamp_exit_reference(raw, close)` 要求 `exit_low > D0 close`(`close` 必填无默认、签名守门;语义驱动零发明阈值;`exit_low=0.01` 极端例有回归单测,卡生成期即拦);**闸②**开仓武装 `evaluate_exit_reference_arming`(`exit_low ≤ 成交价` → 不武装,`user_muted` 优先级最高、「没有」与「不合格」分码)。③读侧 **fail-closed**:engine 旁路 E 只认 `exit_reference_armed is True`,缺键 = 不武装;`create_position_plan_version` 新版本重过闸 + 静音承袭,堵死"写新版本绕闸"后门。④前提③文案已补齐「纪律仍是回落止盈」半句(`e1fe049`)且单测锁死。⑤「无用户确认」以 ⑮ per-position 静音开关回应(planner 未采纳前置确认,理由成立:与减摩擦立项主题相悖)。**四条前提与代码落点逐一对上,豁免边界干净。** |
| 🟡-4 退潮主线样本被 LLM 塑形 | `66e218f`/`47f1a93`(⑧-F)+ `93dc2b8`(⑧-G) | **✅ 收口且修得比要求更深**:①**修宪**:§2.8-C-2(b) 加通用适用边界(2(b) 只覆盖「盯谁」,⛔ 不覆盖聚合纪律触发器的样本组成;自查判据「换一批成员这个数会不会变」)——把个案升级成了通用规则。②**红线闭合(⑧-F)**:样本改 ④ 机械种子派生,`derive_mainline_sample` **签名无篮子入口**(结构性守门)+ 整拍级"换 LLM 成员样本逐位不变"单测;planner 且不采纳我"无单向套利"的缓解判断(LLM 挑龙头 → 跳水日最抗跌 → 单向偏"保护失效"),从严处理,判得比我准。③**⑧-F 对拍诚实暴露了更深的洞**(∩ 关注池使该路"聋",与 LLM 无关)→ **⑧-G 修复**:crc32 配额切片(K=4)+ per-seed 估计量 + 池位保底(29/100/71 = 200 守门),真跌日 07-24 读数 −0.18% → **−2.80%**,与「主线板块本身」per-seed(−2.75%)差 0.05pp;`sector_dive` 阈值一字未动(守门断言),准入 `>0 → >=5` 系 ⑧-G-E 明文授权(`hot_n=3→5` 断言改动显式登记);测量预算按必需项上界 29 固定数算 + `restrict()` 只看额度,**残留耦合 ②b 关死**(压力单测:篮子成员 1→21 只三向重叠,两份测量样本逐位相同)。阈值灵敏度(07-24 主线本身 −2.75% 仍在 −3% 之上 → 0/4 触发)如实挂 P3-37,**⛔ 未调 K 凑触发**(K=6 会触发,刻意不采用)——诚实。 |
| 🟡-5 守门锁空靶 | `0f885b2` | **✅ 治愈**:禁入清单换真实七模块(channels / api.notify / push.apns / notify_kinds / positions / holding / positions_entry)+ `importlib.util.find_spec` 反向存在性断言(锁空靶不再可能)+ AST 补 `from neckline.api import notify` 写法(原实现只看 module 名会漏,修得比我要求的多一层)。 |
| 🟡-6 ④ 三路等价偶发失败 | `a239bcc`(P1-36 结案) | **✅ 确认结案,收回"根因未定位"**:根因 = 等价比较误含 `computed_at` **秒精度审计戳**(批算与逐日是两次独立调用,全量套件负载下更易跨秒边界 → 偶发;单独跑快 → 恒绿)——与症状完全吻合,且 `leader` 侧同缺陷早有 `53fab7a` 先例佐证。业务列(`cluster_key`/`corr`/`n_obs` 等)等价断言未放宽。**确定性主张本身没有破,破的是测试比较口径** —— 我首轮"不能下结论"的保留就此撤销。 |

**销项结论:六张 🟡 全清,无开口。** 🔵-1 已随 `0f885b2` 修复(docstring 按实际行为改口);
🔵-2(种子截断 crc32 任意序)/ 🔵-3(CARD prompt 无 TIMELINESS_RULES)/ 🔵-4(leader.py
成交额单位注释)/ 🔵-5(tradability daily 缺失边缘)**未处理,维持开放**(均为便宜改进,
不阻塞;本批未承诺)。

## 二、⑪-D / ⑧-F / ⑧-G 增量审计(新代码从零审)

**增量发现:1 🟡 + 1 🔵 + 2 🟢,无 🔴。**

### 🟡-N1 昨日涨停样本的截断无确定性 tie-break —— ⑧-G 把一个休眠的既有洞常态化激活了

- **位置**:`neckline/sentinel/universe.py:333-340`(`_load_prev_limit_up_codes`:只
  `sort("consec_limit_up_days", descending=True)`,**并列(绝大多数涨停股 consec=1)由
  parquet 行序打散**)+ `universe.py:228`(`breadth_extra = limit_up_all[:N]` 截断)。
- **问题**:这是 V1 原样继承的既有写法(`bb0d72b` 逐字相同,**非 ⑧-G 引入**),但 ⑧-G 把
  昨日涨停的池位从"剩余 ~171"压到**保底 71**,截断从偶发(旧窗口需求 43~129,7 月极值 223)
  变成**常态**(07-22 需求 121 > 71)——「哪 71 只涨停股进退潮宽度样本」现在**依赖 parquet
  分区文件的行序**。行序对同一文件稳定,但数据修缮 / 回填重写分区会静默换一批样本 →
  炸板率 / 跌停计数(红色刹车另两路触发的输入)读数漂移。这正是项目自家铁律点名的洞
  (「SQL SELECT 未加 ORDER BY 不能当确定性基础」,🩹 `3af64e0` 修种子时的原话)。
- **修法方向**:一行 —— `.sort(["consec_limit_up_days", "ts_code"], descending=[True, False])`
  (体例照 `_day_local_table`);顺手补一条"同数据两次加载截断结果逐位相同"的单测。
- **影响面**:退潮哨兵宽度代理样本(纪律输入)的可复现性;不涉 LLM、不涉阈值。

### 🔵-N2 ⑧-G 的池组成换血只对拍了炸板率,limit_down 一路的读数影响未量化

- ⑧-G 把池里 ~50 个涨停股位换成 100 个普通股切片位,`compute_breadth_snapshot` 的
  `limit_down_count`(绝对触发)与 `limit_down_rate`(分母 = sample_size)的样本组成随之
  变化;完工记录只给了被压两天的**炸板率**不变(42.9%/37.9%),没有 limit_down 两个读数的
  改前改后对照。方向估计偏保守(普通股在崩盘日比昨日涨停股更易跌停 → 该路**更**容易触发),
  但"方向估计"不是数字 —— 建议 P3-37 回看时把同四天的 `limit_down_count/rate` 改前改后
  补进对照表(对拍脚本现成,增量成本一列)。

### 🟢 增量观察两条(不立案)

1. **per-seed 一票的边缘噪声**:准入门槛作用在 `quoted` 总数(≥5),不约束单颗种子的
   有数只数 —— 两颗种子一颗 4 只有价、一颗 1 只有价时,单只票占读数 50% 权重。单种子日
   已被算术排除(K=4 < 5),此为 planner per-seed 裁定的固有代价,随 P3-37 一并观察即可。
2. **mainline 当日冻结是进程内缓存**:重启后重派生(已如实登记);「盘中激活新包 + 进程
   重启」组合会让同日两段样本口径不同 —— 极罕见(V2 红线期不换包),留观察不立案。

## 三、复核后的总账

- 首轮:0 🔴 / 6 🟡 / 5 🔵 / 9 🟢 → **六 🟡 全清**(🟡-1/2/5 代码修复实证、🟡-3/4 修宪 +
  机械闸 + 灵敏度修复、🟡-6 根因结案),🔵-1 已修,🔵-2/3/4/5 维持开放。
- 增量:**+1 🟡(N1,一行修)+1 🔵(N2,补一列对照数字)+2 🟢**,0 🔴。
- 修宪三处(§2.0-1 附注 / §2.8-C-2(b) 收紧 / §2.8-C-3 改写)与代码、守门单测三方一致,
  「记名豁免 + 不外延」的体例给未来同类冲突立了正确的先例。⑧-F 对拍推翻 planner 预判后
  停手回报、⑧-G 拒绝调 K 凑触发 —— 两处诚实值得记一笔。
- 基线:**2801 passed + 2 skipped**(+1 预告的挂钟脆弱测试,北京 14:48 后必红,建议顺手
  改成冻结时钟,免得每晚的全量跑都带一个"已知红")。

> 建议:🟡-N1 一行修 + 一条单测,⑭ 开工前顺手落;🔵-N2 归 P3-37 回看清单。
