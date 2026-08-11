# REVIEW_REPORT · v1.5 契约线独立审计(部署后 review)

- 审计日:2026-07-30 · 审计员:@reviewer(契约线;与判定线审计员并行分工,参考件解析/预算闸/判据同源不在本报告范围)
- 方式:**纯审计,零代码修改;生产只读**(公网 `/health` 免鉴权 + `/report/latest` 用服务器本地 token 在服务器上 curl,token 未落任何输出)。
- 范围:v1.5 全部提交 `0c33367`(v1.5-① 本身)~ `510bf20`(含 ①②③④⑤⑥⑦)。
- 测试证据:
  - Python 全量 `pytest tests/ -q` → **1942 passed + 2 skipped**(42.06s,与 §四 宣称基线逐字一致,零回归);
  - **A4 实证**:跑完全量后开发库 `data/neckline.db` mtime 仍为 2026-07-29 23:17(测试于 07-30 10:02–10:03 执行)——P4-25「测试套件写开发库」确已堵住;
  - Swift `xcodebuild test`(iOS Simulator,LinoJ-iPhone16Pro)→ **174 tests / 13 skipped / 0 failures,TEST SUCCEEDED**。
- 生产活体(只读):`/health` 返 `{"status":"ok","version":"v1.5.0"}`;`/report/latest` = 20260729 老快照,20 只候选四个老键**全部统一**过渡文案、`referencePlan=null`、`judgeSkipped=false`,16 条自选 `dispatchAlerts=[]`(键在、空列表)。

## 〇 结论一句话

**未发现 🔴 级缺陷,也未发现会让已装 v1.5 macOS App 崩溃或错显的活缺陷**。三方契约(schemas → app.py 转发 → Swift 解码)逐字段对拍全部成立;老四件套过渡文案「无条件覆盖」的宣称经代码、单测、生产活体三路证实;版本号治理三方恒等守门真实有效。发现 **1 条 🟡**(同日重跑时 `judgeSkipped` 与陈旧 `llm_judgments` 行的自相矛盾展示——今晚 16:35 首战若预算耗尽 + 人工补跑,正是会踩到的形态)与 4 条 🔵。

---

## 一、🟡 重要

### 🟡-1 同日重跑报告时,`judgeSkipped=true` 会与上一跑遗留的 `llmJudgment` 同时出现(诚实字段自相矛盾)

- **证据**:`/report` 端点把审判**从表里现连**而非读快照:[`neckline/api/app.py:476-477`]
  `judgments = {j["ts_code"]: j for j in report_store.load_llm_judgments(d, ...)}`;
  `_judge_candidates_with_budget` 预算耗尽时只在候选对象上打 `judge_skipped=True`,
  **不清理**当日 `llm_judgments` 里这些码的既有行([`neckline/report/pipeline.py:196-212`]);
  `save_llm_judgment` 是 `INSERT OR REPLACE`、只触碰本跑实际审过的码([`neckline/report/store.py:203-224`])。
- **触发场景(现实,非臆造)**:16:35 第一跑审完 20 只 → 人工补跑(本项目有先例,07-28 补跑 20260727)第二跑预算在第 10 只耗尽 → `reports` 快照被整体替换、11~20 号标 `judge_skipped=True`,但它们第一跑的 `llm_judgments` 行还在 → API 对同一只候选**同时**返回「审判结论(陈旧)」+「本次预算耗尽未发起」。客户端 `CandidateRow` 会显示审判 badge(judgment 优先),而同卡 `ReferencePlanSection` 因 `referencePlan=nil ∧ judgeSkipped=true` 显示「预算耗尽未发起审判」——两句话互相打脸。markdown 侧不受影响(用内存 `judged` 字典,自洽)。`reference_plans` 表有同样的陈旧行残留,但报告 API 读的是快照里的 `reference_plan`,只影响审计表一致性、不影响展示。
- **界定**:纯展示矛盾,不进任何判据/推送/排序;但 `judgeSkipped` 的全部价值就是「诚实分辨没审的原因」,矛盾出现时它恰好在撒谎。**今晚 v1.5 首战预算硬闸是第一看点,若真耗尽且随后补跑,这个形态就会出现**——建议主会话在首战验收时顺带留意。
- **修复方向**:写侧收口——`_judge_candidates_with_budget`(`save=True` 时)标记 skipped 的同时 `DELETE FROM llm_judgments WHERE trade_date=? AND ts_code=?`(`reference_plans` 同理);或 `build_report` 落库时把当日 `llm_judgments` 对齐到本跑 `judged` 键集。**不要**在 `_shape_candidate` 读侧遮蔽(藏真数据不是诚实)。

---

## 二、🔵 建议

### 🔵-2 Swift `ReferencePlanSection` 对未知 `status` 整节静默消失

[`client/Neckline/Components/SharedUI.swift:168-176`] 的 `switch plan?.status` 只列 `vetoed`/`unavailable`/`ok` 三 case,`default` 分支仅处理 `plan == nil`——若 `plan` 非 nil 而 `status` 是未知值(未来服务端加第四态、或快照损坏),**参考件区块渲染为空**,违背本类型 docstring 自己立的「UI 不得静默消失、须展示未展示原因」。markdown 侧同场景落入 ok 分支、仍给出诚实的逐件缺省文案([`neckline/report/render.py:283-310`]),两路行为不一致。现役三态由状态机锁死、触发概率低,故 🔵。修法:`default` 对 `plan != nil` 渲染一条兜底文案(带原始 status 字符串),与 unavailableText 同款式。

### 🔵-3 「章程 −5%」「回落止盈 8%」是硬编码文案,数字却跟现役 config 走

`stopPrice` 数值随现役 `stop_pct` 变(①-验收 ⑥ 有单测锁死不硬编 0.05),但两处展示文案把百分比写死:[`neckline/report/render.py:295-306`]「(章程 −5%…)」「回落止盈 8% 兜底」与 [`client/Neckline/Components/SharedUI.swift:237-252`] 同款。若日后章程改 `stop_pct`/回落止盈比例,会出现「数字对、标签错」。**plan §五 ③-A 逐字指定了这两句文案,实现与 plan 一致**,故只登记为 plan 层潜在漂移:修法是文案里的百分比从 `stop_pct` 口径指纹推导(`reference_plans` 表已存 `stop_pct`,契约需加一个字段),或在 §2.1 附注登记「章程改止损/止盈比例时须同步改这两句文案(共 4 处)」。

### 🔵-4 PROJECT_PLAN §四 活体验收记录提到不存在的字段 `judgeSkipReason`

[`PROJECT_PLAN.md:280`]「`judgeSkipped=false` 默认、`judgeSkipReason=null`」——全仓 grep(`neckline/`、`client/`)**零命中** `judgeSkipReason`,契约里从未有过这个字段。验收记录写了一个不存在的键,修法:§四 快照下次替换时删掉这半句。

### 🔵-5 `WatchlistCheckOut` docstring 的「供客户端复用候选卡的四件套布局」已过时

[`neckline/api/schemas.py:255-258`]:v1.5-⑤-B 起候选卡已改 `ReferencePlanSection`、不再用 `FourPieceDisclosure`,该 docstring 的存在理由(与候选卡布局对齐)只剩历史意义,易误导后来者以为两边仍共享布局。修法:docstring 补一句「v1.5.0 起候选卡已换参考三件套,本对齐仅剩自选体检自用 + 历史兼容」。

---

## 三、放行项(逐条依据)

### A. v1.5 新契约三方对拍(全部成立)

1. **`referencePlan` 全树**:`ReferencePlanOut/BuyOut/ExitOut`([`schemas.py:146-186`])↔ `_shape_reference_plan`([`app.py:355-373`])↔ Swift `ReferencePlan/ReferencePlanBuy/ReferencePlanExit`([`Models.swift:320-368`])逐字段名称/类型/可空性一致;`to_public_dict()`([`reference_plan.py:484-513`])产出的键名(camelCase,含嵌套 `buy.stopPrice`)与 shape 函数读的键逐一吻合。`stopPrice: Optional[float]` ↔ `Double?` 一致;`buy`/`exit` 只在 `clamp=ok` 时非 null,而 clamp=ok 由 `_clamp_buy`/`_clamp_exit`([`reference_plan.py:380-416`])保证 low/high 为有限浮点 → `ReferencePlanBuyOut.low: float` 非可选约束不可能在合法快照上 500。status 三态、`disclaimer` 单一源常量原样透传、`degraded` 独立——三方注释口径一致。
2. **「现拼 vs 冻结」分类(任务 B)判断正确**:`referencePlan` 挂 `_shape_candidate`,每次响应由 pydantic 重新构造(键恒全)→ Swift 用普通合成 Codable 合理(`Models.swift:320` 头注释的分类论证成立);`WatchlistCheckItem` 加 `dispatchAlerts` 后改手写 `init(from:)` + `decodeIfPresent` 兜底([`Models.swift:1510-1560`]),其余字段的 decode 严格性与合成时代逐位保持(对照通过);`reviews.result_json`(真正的冻结快照类)**本版零改动**——v1.5 全 diff 未触碰任何周复盘 DTO,无误伤。
3. **`judgeSkipped`**:`Candidate.judge_skipped`(dataclass 默认 False,[`candidates.py:117`])→ `asdict` 落快照 → `_shape_candidate` `bool(c.get("judge_skipped", False))` → Swift `decodeIfPresent ?? false`。老快照缺键默认 False 的语义论证(schemas 注释 + 单测 `test_report_candidate_judge_skipped_defaults_false_for_old_snapshot`)成立。与 `degraded` 不合并的纪律在 pipeline/render/client 三处注释与行为一致(🟡-1 的重跑缝除外)。
4. **`dispatchAlerts`**:`HoldingK4Hit`(5 字段含 `level`)→ `_shape_watchlist_check` 刻意丢 `level`([`app.py:446-455`])→ `DispatchAlertOut` 4 字段 ↔ Swift `DispatchAlert` 4 字段;往返单测(`test_api_watchlist.py` 两条:有数据 round-trip 断言 level 不透传 + 老快照缺键默认空)+ Swift 解码单测(`testDecodeWatchlistCheckDispatchAlerts` + 老样例断言 `== []`)齐备。复用 `_build_holding_feature_panel`/`describe_hits` 同一镜像、只读 `_hit_A3`/`_hit_A3b` 列不重写表达式,保险丝在 `pipeline.py:369-373`。不推 APNs(`notify.__all__` 仍六类,守门未改断言)。
5. **`APIClient.health()` 改 `(ok, version)` 元组**:全仓调用点 4 处全部迁移——`AppModel.loadServerVersion`([`AppModel.swift:915-923`])、`SettingsView.runSelfCheck`([`SettingsView.swift:313-316`],顺带回填版本)、`IntegrationSmokeTests` 两处(`.ok` / `XCTAssertNotNil(version)`)、`DTODecodeTests` 两条新测(200 与 503 路径)。设置屏数据流:`.task` 拉取 + 自检刷新 + `versionMismatchNote` 仅在 serverVersion 非 nil 且不等时提示(「沉默 ≠ 已确认一致」的注释与实现一致),只提示不拦功能。
6. **老四件套过渡文案**:`LEGACY_FOURPIECE_NOTICE` 单一源、`_shape_candidate` 四键无条件下发不读快照([`app.py:388-391`]);「老/新快照行为一致」的宣称有专测(`test_report_latest_legacy_fourpiece_keys_always_notice` 同时喂空串新快照与真文本老快照,断言四键恒等于常量)且**生产活体证实**(20260729 v1.4.1 期真快照,20 只四键全部返过渡文案)。老客户端硬解码回归真锁:`Candidate.init(from:)` 四键仍 `try c.decode(String.self)`([`Models.swift:451-454`],本版未松动),`testDecodeCandidateReferencePlanThreeStatesAndJudgeSkipped` 兼作 ⑤-G 机器证据(断言四键非空)。
7. **版本号治理三方恒等**:`tests/test_client_version_governance.py` 读 `app.py::VERSION`(去 v)/ `project.yml` / pbxproj **app target** 两处,断言恰 2 处且三方全等;「app target 判据 = 块内含 `PRODUCT_NAME = Neckline;`」在实际 pbxproj 上核实:`:383`/`:568` 两块(1.5.0,`PRODUCT_NAME = Neckline;`)被抓、`:467`/`:536` 两块(1.0.0,`PRODUCT_NAME = "$(TARGET_NAME)"`,NecklineTests 继承的 project 级配置)被正确排除;正则的「buildSettings 块内无嵌套 `{}`」前提在本文件成立。生产 `/health` 返 `v1.5.0` 三方闭环。
8. **无新增 404 reason**:v1.5 未新增端点、未新增 reason 字符串(app.py diff 核对),`mapReason` 无需加 case——符合项目 CLAUDE.md「只有新字符串才需要新 case」。
9. **`search_engine` 链路**(仅落库侧,不进客户端契约):`glm._SEARCH_ENGINE` 单一源 → `_search_tools` 与 `_search_engine_value()` 同读 → `LLMResult.search_engine` 只在成功且开搜索时填 → `JudgeResult` 透传 → `save_llm_judgment` 落列;老行 NULL 不回填。列走 `_COLUMN_MIGRATIONS` 幂等,生产迁移记录(hz_info)已证。

### C. 渲染与语义红线(全部通过)

- **禁语扫描**(推荐买入/建议买入/看好/值得买/推荐买点/目标价/止盈线,负句除外):全仓(`neckline/` + `client/`,含 LLM system prompt)命中逐条人读——全部为①否定/禁止表述(如 `reference_plan.py:128` prompt 明令禁用、`render.py:301`「非止盈线」、`candidates.py:345`「不设固定止盈线」)或②`decision_log` 用户自填「目标价」不同概念(`db.py:332`、`DecisionLogSheet.swift`)。**零真实违规**,与 ③-D 宣称一致。
- **持仓体检节**:排候选之前、空持仓出「今日无持仓」不省节、`has_data=False` 如实标跳过且 D 计数照常、时间退出五态标签含 `SUSPENDED_HOLD`、状态码从 `sentinel.precall` 借常量不硬编字面量、定格日只展示原始日期不在纯函数模块里算交易日历派生值(与 §九 登记的规格偏差说明一致)。
- **三件套渲染四态**与 Swift `ReferencePlanSection` 逐句对齐(nil+judgeSkipped 分叉、vetoed 只给理由、unavailable 不冒充无参考、ok 态被拦子项给 reason 不画空区间不写 0);`judgeSkipped` 与「真异常」的 else 分支刻意分开([`render.py:382-390`])。22 条新 render 单测覆盖各态(含「0.00~0.00 不出现」「未执行 not in md」等反向断言)。
- **markdown 与 API 数字一致性**:止损参考价、买入/离场区间两路同源(同一份 `reference_plan` 快照,`stopPrice` 均直读、无二次计算),无口径分叉。

### D. 生产活体抽查(只读)

- `/health`(公网,免鉴权)= `v1.5.0`;
- `/report/latest`(服务器本地 token,token 未离开服务器):`tradeDate=20260729` 老快照——20 只候选 `(四键相等=True, 值=过渡文案, referencePlan=None, judgeSkipped=False)` **集合唯一**(无一例外);`watchlistCheck` 16 条、`dispatchAlerts` 非空计数 0;`referencePlan`/`judgeSkipped`/`execHints`/`infoCard` 键均在。
- **未发现会让已装 v1.5 macOS App 崩溃或错显的活缺陷**(四键非空满足严格解码;`referencePlan=null` 走 `decodeIfPresent`;`dispatchAlerts` 由 shape 层恒补键)。今晚 16:35 新快照首次出真 `referencePlan` 时,ok 态各必填键(`status`/`disclaimer`/`degraded`/`buy.low/high/why`)均由 pydantic 构造保证恒在,Swift 合成解码不会因缺键崩——契约侧无雷;生成侧质量(三态分布/预算闸行为)归判定线与今晚首战验收。

### 审计边界

- 参考件 LLM 解析、夹逼状态机语义、预算闸计时正确性、A2/B3 判据同源:判定线审计员范围,本报告只核契约形状与两端一致性。
- iOS 真机未换包(§四 已如实登记),不构成本线缺陷。

---

## 四、复核(2026-07-30,v1.5.1 待部署态 `c26c2b6`;纯审计零改动)

- 复核证据:Python 全量 **1969 passed + 2 skipped**(68.3s,与宣称基线逐字一致);Swift iOS Simulator **178 tests / 165 passed / 13 skipped / 0 failures,TEST SUCCEEDED**(xcresult 计数为证)。本节生产零访问(部署前无需再打)。

### ✅ `1783090` —— 🟡-1 写侧收口,判据满足

- **矛盾不再出现且不在读侧遮蔽**:✅。修在 `_judge_candidates_with_budget` 预算耗尽分支(`save=True` 时对被跳过那批码批量 `DELETE` 当日 `llm_judgments` + `reference_plans` 行,[`pipeline.py:213-222`]);`_shape_candidate`/`load_llm_judgments` 读侧一字未动(该 commit 未触碰 `app.py`),与本报告 🟡-1 给的修复方向逐字吻合。新函数走各自表的读写单一通道(`store.delete_llm_judgments` / `reference_plan_store.delete_reference_plans`),单条参数化 `DELETE ... IN (...)` = 单事务,名单去重滤空(`["", None]` 不误伤,有专测)。
- **单测代表性**:✅。pipeline 层三条(第一跑审完→补跑耗尽→跳过票两表零残留、本跑真审过的照留;首跑幂等 no-op;`save=False` 既不写也不删)+ store 层两条(只删指名码、别日不受伤)——正反两向、幂等、只读路径全覆盖,且第一条断言了「skipped 与 judged 都非空」防白测。
- 🟢 两条登记不设卡:① 删行与 `reports` 快照落库**不是原子**——补跑中途崩溃(或生成进行中的秒级窗口)会短暂出现反向组合「行已删、快照还是旧的」(老快照该票显示"审判未执行"),瞬态且与既有"重跑本就非原子"同类,16:35 单进程场景无并发读风险放大;② `1783090` 提交讯息称"新增 `delete_reference_plans`",该函数实际随 `3f0a5a2` 入库(提交切分小瑕疵,零功能影响)。

### ✅ `3f0a5a2` —— 契约增量三方对拍成立

- **`buy.stopPct` / `exit.takeProfitRetrace`**:schemas 声明(`Optional[float] = None`,[`schemas.py:151-175`])→ `to_public_dict` 增键(只嵌在 clamp=ok 的 `buy`/`exit` 对象里,与 `stopPrice` 同待遇,[`reference_plan.py:526-550`])→ `_shape_reference_plan` **零改动即正确透传**(`ReferencePlanBuyOut(**d["buy"])` + pydantic 字段声明;老 v1.5.0 快照缺键 → 默认 `None`,专测断言是 `None` 不是 0——"0% 止损"这种危险冒充被明确防住)→ Swift `Double? = nil`(合成 decodeIfPresent)。API 往返测(`test_report_candidate_reference_plan_carries_charter_fingerprints` + 老快照 None 断言)与 Swift 解码测各就位。
- **两端动态标签数字一致性**:✅。`render._ratio_pct_txt`(`f"{v*100:.2f}".rstrip("0").rstrip(".")`)与 `NKFmt.ratioPct`(`%.2f` + 循环去尾零)逐字符同频:0.05→"5%"、0.055→"5.5%"(不四舍五入)、0.1→"10%"("10.00" 的小数点挡住误剥,两端同理,已人工推演 + 双端单测锁值);`null` 退化文案两端**逐字相同**(「章程止损」/「纪律仍以章程的回落止盈兜底」)。
- **指纹来源真实**:✅(复核时专查过——字段名拍错会让功能永远静默退化)。`_resolve_charter_pcts` 读 `active_config()["take_profit_retrace"]`,键名与 `strategy/momentum.py:71`(`MomentumConfig.take_profit_retrace`)、`brain.py:487` 同源;本地库实测可跑通(K1 现役返 0.05;生产现役 v1.3.3 将返 0.08)。一次 `active_config` 取两值,不多开连接。
- **迁移登记**:✅。`_COLUMN_MIGRATIONS` 增 `("reference_plans", "take_profit_retrace", "REAL")`([`db.py:690-695`]),CREATE TABLE **刻意不含该列**(新老库同走补列,同 `llm_judgments.search_engine` 惯例,注释已写明);部署清单三处齐:§四「🗄 迁移增量」独立一行 + 部署作业单第④步「`take_profit_retrace` 列在」+ §五 **⑧-D**(v1.5.1 归 ⑧ 块;⑥ 是 v1.5.0 的部署块,其 ⑥-B 清单如实停在 v1.5.0 两项,不算漏)。
- **版号三方 v1.5.1**:✅(`app.py:189` / `project.yml` / pbxproj `:383`/`:568`,NecklineTests 两处 1.0.0 照旧被守门判据排除;守门测在 1969 里绿)。

### ✅ 便宜项三件

- **🔵-2**:展示态判定抽成纯函数 `ReferencePlanSection.displayState()`(SwiftUI body 测不了,抽出来正是为可测性),`.unknown(原始status)` 走诚实兜底文案(带原始 status 字符串 + 「不代表确认无参考,请更新 App」);单测覆盖 nil/三态/`future_state`/空串六分支。与 markdown 侧"不静默"目标对齐(markdown 未知态落 ok 分支仍渲染诚实缺省,两端策略不同但都不消失——可接受的实现差异)。
- **🔵-4**:§四 里不存在的 `judgeSkipReason` 已随快照替换清掉(全文现仅剩 §四 描述"已清掉"动作的那一行,代码全仓本就零命中)。
- **🔵-5**:`WatchlistCheckOut` docstring 已更正且措辞准确(「历史成因…v1.5.0 起该对齐只剩历史意义…改动它不再牵动候选卡」)。

### ✅ 老客户端影响:无破坏

- **v1.5.0 macOS App(现装)**:`ReferencePlanBuy/Exit` 合成 Decodable 对 JSON 里的未知键(keyed container 语义)天然忽略——新 payload 的 `stopPct`/`takeProfitRetrace` 两键被静默丢弃,解码零影响;其标签仍显旧硬编「−5%/8%」= 恰是本次换包 1.5.1 的动机,非破坏。设置屏版本不一致提示(v1.5.0 已有)会如实提示「服务端已是 v1.5.1,请换包」。
- **v1.4.1 iPhone(未换包)**:整个 `referencePlan` 本就不在其解码集,四个老键仍恒非空过渡文案,零影响。
- 顺带核过 `d80f57c`(判定线修复)契约面零触碰:只动 `llm/judge.py` + `report/reference_plan.py`,`parsed_attachment` 在 `neckline/api/` 与 `client/` 全仓零命中 = 「不落库不进契约」宣称属实。

**复核结论:三张卡的修复全部按正解落地,契约增量三方对拍干净,15:05 部署无契约侧阻塞项。**部署后照作业单以 `/health = v1.5.1` + `take_profit_retrace` 列在 + 老快照读取不崩三件为到位判据即可;今晚 16:35 首战四项待验清单(§四)不变。
