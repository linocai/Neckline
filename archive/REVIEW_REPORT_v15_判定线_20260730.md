# 独立审计报告 · v1.5 判定线(参考件链路 / 预算硬闸 / 配菜判定面 / 生产抽查)

- 审计员:判定线独立 reviewer(未参与施工),2026-07-30(16:35 首战之前)
- 分工:本报告只审**判定线/LLM 链路**(A 参考件 / B 预算硬闸 / C 配菜判定面 / D 生产只读抽查);API 三方对拍与客户端由并行的契约线审计员另行出报告,本文不重复。
- 方法:从零读代码(`0c33367..510bf20` 全部 6 个提交,git log 自查),施工自述与 plan §五 v1.5-①②③④ 逐条对照;能跑的全跑;边角行为用临时脚本现场复现(全部在会话内,零代码修改);生产只读(sudo sqlite `mode=ro`,只 SELECT 需要的列,secrets 不落输出——`.env`/`app_settings` 只查「是否配置」布尔,不读取值)。
- 测试证据:本地全量 `pytest tests/ -q` → **1942 passed + 2 skipped**(43.0s),与基线逐字吻合。
- **结论先行:未发现会让今晚 16:35 报告整段失败的活缺陷(🔴 零)**。生产侧迁移态、日历覆盖、LLM key、systemd 限额均核实就绪(见 §三-D)。今晚预期行为:按 07-29 实测节奏(10 只审判的整份报告 22m10s)推演,20 只全覆盖下候选审判段大概率在审到第 ~9–14 只时触发 1200s 预算硬闸,尾部候选如实标 `judgeSkipped`——**这是设计内截断,不是故障**,验收时别误判。

---

## 一、缺陷清单(按严重度)

### 🔴(无)

### 🟡-1 「结论:」标签取**最后一个**匹配,而 v1.5 起三件套 JSON 排在标签**之后**——JSON 自由文本里出现「结论:通过/否决」字样会静默翻转 verdict(现场已复现)

- 位置:`neckline/llm/judge.py:138-147`(`_VERDICT_RE` + `matches[-1]`)× `neckline/report/reference_plan.py:132-156`(`REFERENCE_PLAN_SYSTEM_PROMPT` 规定输出顺序:叙述 → 标签 → json 围栏)。
- 判据:v1.5 之前标签是输出的**最后一段**,取 last-match 天然安全;v1.5 首次把机器解析内容(`script`/`why`/`veto_reason` 三个**自由中文文本**字段)放到了标签之后,last-match 的锚被架空。prompt 只写了「**正文里**不要提前出现『结论:』」,没有约束 JSON 内部——而 `script` 恰恰被要求写「带分支的行动指引」,出现「若跌破证伪线则结论:否决」这类措辞并非臆想。
- 现场复现(本审计脚本,实跑通过):真实标签「结论:通过」+ `script` 含「按结论:否决 处理」→ `_parse_verdict` 返回**否决**,且从 JSON 字符串内部抠掉了匹配片段(本例抠完仍是合法 JSON,`parsed_json` 非 None)→ `build_reference_plan` 走 `vetoed` 分支,三件套整体丢弃、卡面显示「LLM 判风险大」,与叙述内容自相矛盾。反方向同理:真实否决 + JSON 里出现「结论:通过」→ 徽章翻成 ✅ 通过(否决票的 buy/exit 按 prompt 是 null,实害限于错徽章+错状态,但已是「会错判」的展示)。
- 边界确认:**不触发任何机器动作**(第〇原则四锁均不受影响,verdict 全链路只进展示与参考件门,见 §二放行-5),错的是给人看的判定结论与参考件去留——概率中低、后果中等,定 🟡。
- 修法方向:① 最小修——在 `split_narrative_and_reference_json` 之前先剥离最后一个 ```json 围栏、再对剩余文本跑 `_parse_verdict`(等价说法:`_parse_verdict` 忽略落在围栏区间内的匹配);`judge_candidate` 内部解析顺序不便动的话,可在 `REFERENCE_PLAN_SYSTEM_PROMPT` 路径上由 `judge_and_build_reference_plan` 自行重解析;② 皮带加背带——prompt 补一句「JSON 三个文本字段里也不得出现『结论:』字样」。注意 `_parse_verdict` 是 plan 点名「一字不改」的共用件,动它需连同候选/自选两条老路径回归(老路径标签仍是末段,改动应保持等价)。

### 🟡-2 报告渲染层把「章程 −5%」写死在文案里,与「stop_pct 读现役 config」的单一源纪律割裂——章程一改,数字与标签当场打架

- 位置:`neckline/report/render.py:293`(`f"止损参考约 {stop_txt}(章程 −5%,以实际成交价为准)"`);同根问题:`ReferencePlan.to_public_dict()`(`reference_plan.py:484-513`)不输出 `stop_pct`,渲染层想动态写也拿不到比例。
- 判据:`stop_price` 数字本身正确地随现役 `stop_pct` 变(单测锁死 0.05/0.08 两档,①验收⑥),但括号里的「−5%」是字面量。若未来章程把止损改成 −8%,markdown 报告会显示「止损参考约 11.04(章程 −5%…)」——数字是 8% 的、标签是 5% 的,恰是 §2.1「常量唯一源,禁止各处漂移」要防的形态。溯源:plan §五 ①-C 示例文案原文就带「(章程 −5%)」,施工是照抄了示例——但示例不豁免纪律。
- 修法方向:① 落库行已有 `stop_pct` 口径指纹,`to_public_dict` 增补 `stopPct`(增量可选键,老客户端忽略;需与契约线/客户端排期协同)后渲染层动态写;② 更省的:文案改成「按现役章程止损比例,以实际成交价为准」,不写具体百分数。同时排查发现 `REFERENCE_PLAN_SYSTEM_PROMPT` 喂给 LLM 的阈值块此处是**对的**(`_threshold_block` 用 `{stop_pct:.1%}` 动态格式化,`reference_plan.py:244-245`),只有渲染层这一处硬编。

---

## 二、观察项(🟢,不改判定结果或现状无实害,建议顺手处理)

- **🟢-3 「模型丢标签」被记成 `vetoed` 而非 `unavailable`**:`_parse_verdict` 无匹配时保守按否决(既有纪律「解析歧义不放行」,plan 点名一字不改),v1.5 下连带 `ReferencePlan.status=vetoed`、卡面显示「LLM 判风险大」——语义上这是「没看清」不是「判了风险大」。缓解:narrative 尾部自动追加的「[系统提示:模型未按格式给出结论标签…]」会随卡面展示,人能看出真相。方向保守(拦不放),仅记录;若要精确,可在 reference 路径把「标签缺失导致的否决」细分进 `degrade_reason`。
- **🟢-4 叙述残留原始 JSON 的两个化妆缺口(现场复现)**:① 围栏**未闭合**(输出中途被截断)→ 正则不匹配、裸 JSON 也解析不动 → 叙述**原样**返回,用户可见「```json {"buy": …」半截;② **多个围栏**只删最后一个,前面的残留在叙述里。两者都正确落 `unavailable`/正常态,不违反「从不抛异常」,只违 §2.7「不把 JSON 摊给用户」的观感。修法:`split_narrative_and_reference_json` 清理时顺手把剩余 ```json 片段(含未闭合)一并剥掉。
- **🟢-5 pipeline 保险丝的回退分支自身不设防 + 会二次耗预算**:`pipeline.py:219-225` 外层 except 的回退 `judge_candidate(...)` 不在任何 try 内——若它确定性抛异常,整份报告崩,与「16:35 绝不因参考件失败而无报告」的意图相反。实查触发面极小:`openai_compat.chat()`/`_post` 全程包络(网络异常/非 200/畸形 200/空输出全部返回 `ok=False`,`openai_compat.py:69-147` 逐分支核实),能穿透 `judge_candidate` 的只剩消息组装层的字段格式化(如 `close` 为 None,生产候选不会)。另:回退是**第二次完整 LLM 调用**,墙钟照算进预算——`_judge_candidates_with_budget` docstring「预算计时不因异常处理而额外消耗」表述偏乐观。修法:回退调用再包一层 try,失败落一条 degraded 占位 JudgeResult。
- **🟢-6 A4 守门的漏网姿势(设计内窄栅栏,记录残余面)**:`tests/test_db_isolation_guardrail.py` 只禁**零参数**裸调 `active_config`/`get_active`,且只扫 `tests/*.py`。显式传 `db_path=None`、经别名/partial 间接调用、以及其它经 `neckline.db` 访问真库的模块(`watchlist`/`positions`/`report.store` 等)的裸调,均不在网内。P4-25 立项就只针对 brain 两函数(实锤事故源),窄是刻意的;若再出同类事故,扩名单即可(`_BANNED_BARE_CALLS` 结构已备好)。
- **🟢-7 逐票落库姿势的两处小账**:`_judge_candidates_with_budget` 里 `save_reference_plans(trade_date, [plan])` 每票一次(每次 `init_schema` + 新连接),20 只 = 20 次建连;且 `save_llm_judgment`/`save_reference_plans` 两个写调用在逐票 try 之外——SQLite 写失败(锁/盘满)会掀翻报告。与 v1.5 之前 `save_llm_judgment` 的既有暴露同类(WAL + 默认 5s busy timeout,16:35 时段常驻服务基本不写库),非新风险类,记录备查。
- **🟢-8 预算最坏超调量化(放行,但把数写死备查)**:预算检查只在**票间**进行(设计如此,「不掐进行中调用」),单票最坏 = `max_attempts=3 × read_timeout=90s` ≈ 270s+,故候选段墙钟上限 ≈ 1200 + ~300 ≈ 25 分钟;生产 `neckline-report.service` `Type=oneshot` + `TimeoutStartUSec=infinity`(实查),systemd 不会中途击杀。整份报告最坏 ≈ 25m(候选)+ 5m(消息面预算)+ ~5m(其余)≈ 35m,无超时风险,内存路径 v1.5 未变。

---

## 三、放行项(查过什么、为什么放行)

### A. 参考件链路(`reference_plan.py` / `reference_plan_store.py`)

1. **解析鲁棒性 /「从不抛异常」宣称成立**:`split_narrative_and_reference_json` 全部解析点包络核实——围栏正则 `finditer` 不抛;`json.loads` 捕 `JSONDecodeError/TypeError/ValueError`;裸 JSON 走 `raw_decode` 捕 `JSONDecodeError`、且只认「恰好吃到文本末尾的 dict」(嵌套边界交给解析器,不手写括号计数)。边角逐一过:嵌套对象(单测 `test_nested_braces_inside_fence_do_not_truncate_parse`)、多围栏取最后(单测)、截断围栏 → None + 原文(本审计复现,见 🟢-4)、中文引号/非法 JSON → None + 叙述照清(单测 `test_malformed_fence_json_returns_none_but_still_cleans_narrative`)、degraded 占位文案原样透传(单测)。`_extract_bare_trailing_json` 对满是 `{` 的病态文本是 O(n·m),叙述量级(KB)下无害。
2. **夹逼四态优先级与宣称逐位一致**:`_clamp_buy` 实现顺序 = absent(整体缺/两数全缺)→ malformed(非数/NaN/bool/缺一个/低>高/≤0)→ no_limit(数字合法但涨跌停算不出)→ out_of_limit → ok,与 docstring/plan ①-C 定死顺序相同;「absent 优先于 no_limit」有专测(`test_absent_takes_priority_over_no_limit`)。`_is_finite_number` 排除 bool 与 NaN/inf,核实。
3. **涨跌停真同源**:`_resolve_next_day_limit_prices` 唯一入口 `compute_intraday_limit_prices`,`board`/`is_st` 走 `sentinel.universe.load_stock_meta`(不自判 ST/不自分板块),日期传 `next_trading_day(报告日)`(ST 制度分界日敏感,方向正确);单测直接与 `compute_intraday_limit_prices` 本尊对拍相等(`test_resolves_and_matches_...directly`)。`close` 可当明日 pre_close 的前复权论证已写进 docstring(与 info_card 同一结论)。三类算不出(缺 meta / close≤0 / 日历异常)各自返回理由不崩,有单测。
4. **离场不夹逼**:`_clamp_exit` 只校验 `0<low<=high`,压力位远超明日涨停仍展示(单测两处:纯函数层 + `build_reference_plan` 层)。
5. **`stop_price` 口径**:`_resolve_stop_pct` 读 `brain.active_config(db_path=...)`,无现役返 None(单测);`stop_price = round(close×(1−stop_pct),2)` 随 config 参数化锁死(0.05/0.08 两档单测)。喂 LLM 的阈值块同样动态(`_threshold_block`)。唯一硬编残余是渲染文案,见 🟡-2。
6. **状态机 ①-D**:`degraded → unavailable`、`否决 → vetoed(数字即便给了也丢弃,单测)`、`通过+解析失败 → unavailable(degraded=1,degrade_reason 写明,叙述与 verdict 不丢,单测)`、`通过+dict → ok(子项各自 absent/rejected 细分)`——四分支逐行核对与 docstring/plan 一致。
7. **落库幂等与两层兜底自洽**:`reference_plans` PK `(trade_date, ts_code)` + `INSERT OR REPLACE`(本地 schema 与生产实查 schema 逐列一致),同日重跑逐位相同有单测;「解析失败**落库**(status=unavailable 行,留审计痕)vs 意外异常**不落库**(plan=None,pipeline 保持 `reference_plan=None`)」两层核实自洽,且 `test_reference_plan_exception_does_not_block_main_report` 断言异常路径下 `reference_plans` 表零行、审判结论照留。`save=False` 不写库(reference_plan 层与 pipeline 层各一测)。
8. **veto 唯一开关,无第二暗门**:全仓库 grep,`STATUS_VETOED` 唯一写入点 = `build_reference_plan` 的 `verdict == VERDICT_VETO` 分支;verdict 唯一来源 = `_parse_verdict` 的「结论:」标签(含丢标签保守否决,同一个开关的既有降级,见 🟢-3);`veto_reason` 只从 `parsed_json` 取、缺失置 null 不硬凑(单测)。已知的意外触发路径 = 🟡-1(标签被 JSON 文本劫持),已单列。
9. **第〇原则四条机器断言 + 绕过路径扫描**:① sentinel 目录 grep 零命中(`test_sentinel_never_references_reference_plan`,逐文件参数化;实查 `neckline/sentinel/` 是**扁平目录**无子包,`glob("*.py")` 覆盖完整);② 排序键白名单用**运行期访问追踪**断言 `_sort_key` 读的键 == 五键白名单、且显式喂了 `reference_plan` 键断言不被读(`test_sort_key_does_not_read_reference_plan_related_inputs`,比静态 grep 强);③ 推送白名单仍六类(`test_notify.py:43`);④ 否决不移除候选(`test_veto_verdict_keeps_candidate_in_list_...`)。绕过路径审:候选顺序在 `build_intel_candidates` 内定死、发生在 LLM 审判**之前**,判官循环只原地补字段不重排;verdict/reference 的全部消费点 = `render.py`(徽章/文案)与 API 展示层,`sentinel/` 在 v1.5 全版零 diff(git 实查);盘中关注池语义未动。**未发现绕过。**

### B. 预算硬闸(`pipeline.py::_judge_candidates_with_budget`)

- 宣称的测试**真锁**,逐条对号:「调用前检查不掐进行中调用」= 检查在循环顶、票间进行(代码 201 行,`test_budget_exhausted_after_first_call_...` 用慢 handler 证明第 1 只完整跑完);「尾部整体 skip 不是随机丢」= `max(judged_ranks) < min(skipped_ranks)` 直接断言(`test_skips_are_always_the_ranked_tail_...`);「`judgeSkipped` 与 `degraded` 两计数不合并」= 前 3 只 provider=None(degraded=3, skipped=0)+ 后 3 只预算 0(skipped=3, judged 字典空,transport 被调即断言失败)双向证明(`test_judge_skipped_and_degraded_are_distinct_...`);「跳过的不落库」= 落库判词与跳过码零交集(`test_save_true_persists_..._only_for_attempted`);端到端「预算 0 → 报告照出、markdown 写『预算耗尽未发起』且不误标『未执行』异常」(`TestCandidateJudgeBudgetWiring`,连快照 `judge_skipped` 落库读回都断言了)。防退化反测(预算充裕必审)也在。
- 最坏情形推演(20 只 × 30–70s):70s/只 → 审完第 18 只时 ≈1260s ≥1200 → 跳 2 只;生产实测口径(07-29:10 只审判的整份报告 22m10s,折合约 100–130s/只含信息卡取数)→ 预计审到第 ~9–14 只截断、其余 `judgeSkipped`。**截断即设计**;单票在途最坏超调 ~270s(重试链),总墙钟无 systemd 超时风险(🟢-8 已量化)。渲染/客户端把跳过如实标注、不冒充否决(render 单测 + markdown 断言)。
- 串行决策留痕核实:无并发,理由与 `news_alerts` 先例一致且写明「无 key 未实测限频,取保守分支」——与 plan ②-C「不许拍脑袋开并发」相符。

### C. 配菜判定面

- **A1 逐位同源宣称成立,且测试有代表性**:两侧命中判定都经**同一个** `_build_holding_feature_panel`(源码级同名引用:`watchlist_check.py:426` 与 `holding_k4_check.py:633`,**实参签名逐位相同** `(codes, trade_date, parquet_dir)`)、同一份 `_add_hit_columns` 列、同一个 `describe_hits` 装饰;`TestDispatchAlertsMatchHoldingK4Check` monkeypatch 面板证明两模块确经该名字取面板 + 同行喂两管线断言 code/label/evidence/evidenceStrength 逐位相等。自选侧只取 A3/A3b 两码是 plan 明文(防第二张 K4 牌),不是漏。保险丝在 pipeline(`pipeline.py:373-376`),函数自身不吞异常,分工与 docstring 一致。⛔ 不推 APNs 核实(notify 六类白名单未动)。
- **A4 AST 守门**:实现精确(只认真实语法树上的零参调用,attribute 与裸名两姿势都抓;白名单当前空且有防悄改哨兵测试;三个已知真库护栏文件在扫描域断言)。漏网姿势见 🟢-6(设计内窄栅栏)。
- **③ 哨兵判据源完整性**:`candidates.py` v1.5 diff 逐 hunk 核对——只做了四个文案字段改默认空串 + 字段重排(全仓库构造点关键字传参,无位置参数风险)+ 新增 `judge_skipped` 字段;`entry_spec`/`invalidation_spec` 两函数与 `intel_candidates` 里的生成调用**一字未动**(diff 实查);`sentinel/` 目录 v1.5 全版零改动(git diff 空);`sentinel/universe.candidate_from_dict` 对空文案字段有默认值兜底。哨兵判据源完整。
- **A3 search_engine**:`_SEARCH_ENGINE` 类常量单一源(payload 与 `_search_engine_value()` 同读,glm.py diff 核实);只在成功路径填充、失败/未激活/未开搜索恒 None(openai_compat 成功分支 + judge.py 两条降级分支核实,test_judge/test_llm 各有锁);`save_llm_judgment` 落列、老行 NULL 不回填(生产实查:20260723–29 历史 10 行/日全 NULL,正确)。

### D. 生产只读抽查(2026-07-30 10:00 CST,全部 `mode=ro`)

- `reference_plans`:表已建,schema 与本地 `db.py` **逐列一致**(24 列 + PK`(trade_date,ts_code)` + `idx_reference_plans_code`),**0 行**(与 §四「现 0 行,今晚首次生成」相符)。
- `llm_judgments`:`search_engine` 列已迁移(`PRAGMA table_info` 第 11 列,TEXT 可空);近 5 个交易日各 10 行、`degraded=0`、`search_engine` 全 NULL(老行不回填,正确)。
- 今晚先决条件逐项:`app_settings` LLM = `glm` 且 key 已配置(只查布尔);`trade_cal` 覆盖至 20261231、下一交易日 20260731 在表(`_resolve_next_day_limit_prices` 不会因日历断档批量 `no_limit`);现役 `v1.3.3` `stop_pct=0.05`;`neckline-report.service`:`Type=oneshot`、`TimeoutStartUSec=infinity`、`RuntimeMaxUSec=infinity`、`MemoryMax=800M`(v1.5 未改),昨跑 `ExecMainStatus=0`。**未发现今晚必崩项。**
- 给今晚验收的一句提醒(非缺陷):`judgeSkipped` 命中数预计非零(见 §三-B 推演),属预算硬闸按设计止损;`reference_plans` 当日行数应 = 实际发起调用的候选数(跳过的不落行),别拿「不足 20 行」当异常。

---

## 四、复核建议

🟡-1 触碰 verdict 解析(判定线共用件),🟡-2 若走 `to_public_dict` 增键则触碰客户端契约——两条修复落地时建议主会话/契约线各复核一遍,取并集。本报告不改一行代码。
