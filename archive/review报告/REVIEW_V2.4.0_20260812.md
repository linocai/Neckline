# V2.4.0 独立复审报告(`0cb5d00..659a42b`,五段 P0–P4)

> 审计员:独立 reviewer(未参与施工)· 日期:2026-08-13
> 对象:`git diff 0cb5d00..HEAD`(5 个 commit)· 基线 = 生产现役 `v2.3.3`(tag `v2.3.3` → `0cb5d00`)
> 依据:`V2.4.0_AUDIT_REMEDIATION.md`(需求唯一权威,含最终 DoD 20 条 + 施工纪律 7 条)·
> `PROJECT_PLAN.md` §五 V2.4.0 施工图 · `CLAUDE.md`(整份)· `~/Lino/whynotme/K8.md` ·
> 用户裁定 #1(题材域 / `ths_member`)与 #2(竞价时间戳零容差)
> 审计方式:读 diff + AST/正则扫描 + **构造输入直接调函数实测** + **注入逃逸探针** + 双端 build/test。
> ⛔ 全程零部署、零服务器动作、零 git 写操作;`data/neckline.db` md5 全程 `7ca02c7d99a01f4226442a3edb085c9b` 未变;
> 工作区收尾 `git status` clean。

---

## 〇、结论(先看这段)

**级别分布:🔴 2 条 · 🟡 8 条 · 🔵 15 条。**

**能不能上产:不建议原样上产 —— 先修两条 🔴 与一条 🟡(合计改动很小,都在一两个函数内),
其余可上产后再修。** 两条 🔴 都不是"算错了",而是**这一版立项要根治的那一类病在别处复发**:

- 🔴-1 删聚合页面时**没有清点它实际承载的全部事件流** → 四类仍在写的持仓/关注提醒失去 App 内唯一落点;
- 🔴-2 客户端把「从未交叉核验」讲成「两源已交叉核验」—— 正是审计规格 P2 目标里点名的病 ③,在展示层复发。

**主干判断全部经得起查**:退役是真的撤销判断权(不是改文案/调阈值/加状态)· 三处「同名不同物」一处没切歪 ·
成员级 OUT 没有"凭空消失" · 裁定 #1/#2 逐字落地 · **交易语义阈值一个都没发明** ·
DB 迁移与四线原子激活**我独立实测通过**(下面有读数)。

---

## 一、🔴 致命(2 条)

### 🔴-1 删「盘中动态页」时漏清点:**四类仍在写的提醒失去了 App 内唯一落点**

`[neckline/api/app.py:1093-1122]`(`_today_position_alerts`,过滤条件 `sentinel == "holding"`)
`[PROJECT_PLAN.md:940-948]`(P0.5+ 规格本身就只写了 `holding`)

**事实**。老 `GET /board` 的事件列表吃的是当日 `sentinel_events` 里**全部**非空 `ts_code` 行
(`git show 0cb5d00:neckline/api/app.py:790-807`)。新通道只取 `holding`。逐条清点老页面承载的东西:

| 老 BoardSection 上的事件 | 今天还在不在写 | 新落点 | 结论 |
|---|---|---|---|
| `retreat` / `invalidation` | 已停写 | —— | ✅ 本来就该删 |
| `holding`(stop_approach / take_profit / sector_dive / exit_reference) | 在写 | `PositionOut.alerts` | ✅ 实测通 |
| `precall` 篮子成员段(gap_up / low_open / auction_vol / member_ex_rights) | 在写 | 竞价报告(同一批纯函数与阈值) | ✅ 功能等价 |
| `custom_alert` | 在写 | `/alerts` 独立入口 | ✅ |
| `entry` / `d5exit` | **已无写入方** / `max_hold_days=None` 恒不触发 | —— | ✅ 无影响 |
| **`circuit` / `consecutive_stops`**(连续 3 笔止损提醒) | **在写**(`positions_entry.py:934-939`) | **无** | 🔴 |
| **`attention` / `basket_peers_weak`**(同篮成员集体转弱,`scope=持仓代码`) | **在写**(`engine.py:423-433`,每拍) | **无** | 🔴 |
| **`attention` / `holding_decoupled`**(持仓转独立弱势,`scope=持仓代码`) | **在写**(同上) | **无** | 🔴 |
| **`precall` / `position_low_open`**(持仓竞价大幅低开逼近/跌破**亏损警戒线**) | **在写**(`precall.py:736-739`) | **无** | 🔴(有部分兜底) |

**为什么这不是"规格没列到"就能放过**:

1. `[neckline/sentinel/engine.py:421-422]` 把设计不变量写死了 ——「台账**无论推没推出去都落**
   (开关关掉 = 不打扰,不等于这件事没发生;**看板照样要看得见**)」。页面删掉之后这句话的**后半句当场不成立**。
2. `[neckline/api/app.py:1451-1454]` 白纸黑字:`GET /circuit` **「没有替代端点」—— 提醒走推送与看板事件**。
   两条腿,现在断了一条。
3. 审计规格 P0.3 末段 + `[PROJECT_PLAN.md:884]`:「**不得因为删除聚合页面而误删仍然有效的持仓提醒**」;
   审计规格 P0.6 第 4 条:「**确认无其他职责后**删除文件」—— 这次清点只覆盖了规格逐字点名的三类。
4. `app.py:1097-1100` 的 docstring 写「`attention` / `custom_alert` 各有自己的入口(⑪-A 四监测走 APNs)」
   —— **把「推送」当成了「入口」**。APNs 是一次性打扰、可被 kind 开关掐掉、划走就没了;
   `/alerts` 是可查列表。两者不是同一种东西。

**失败场景(具体)**:某日盘中 `holding_decoupled` 命中(持仓从跟随板块转为独立弱势),
用户在开会没看锁屏;晚上打开 App 复盘「今天这只票出过什么信号」→ 持仓详情「今日提醒」只有 `holding` 那几条,
那条判决**在界面上完全不存在**,而它躺在 `sentinel_events` 里。用户会得出「今天没出过信号」的错误结论。
`push_*` 开关关掉后(`DEFAULT_ENABLED=True`,但用户可关)该类事件从此**在任何界面上都不存在**。
`precall/position_low_open` 更硬:它**根本没有推送 kind**(`precall.py:688-692` 的 `_record` 只落库不推),
9:26 那条「集合竞价开盘 X 已跌破亏损警戒线 Y」现在**无处可见**,只能等 9:30 后第一拍的
`holding/stop_approach` 补一条 —— 丢的正是"开盘前那几分钟"这个它存在的全部理由。

**修复方向**(需要一次拍板,因为它改的是 P0.5+ 的取数口径):
`_today_position_alerts` 的过滤从「`sentinel == 'holding'`」改成**白名单**
`{holding, attention, circuit, precall}`,**继续按 `ts_code == 该持仓` 匹配**
(市场级行 `ts_code=''` 已被 `if not code: continue` 天然排除)。同步补
`_POSITION_ALERT_LEVEL` 与客户端 `nkPositionAlertLabel`(`PositionModels.swift:134-142`)的
event_key 映射(`decoupled` / `basket<id>` / `consecutive_stops` / `position_low_open`)。
🔴 **退役的 `retreat` / `invalidation` 必须继续排除 —— 用白名单,⛔ 不用黑名单。**
⚠ `attention/sector_bid_fade` 的 `scope` 是指数码、匹配不到任何持仓,那一条要么另想落点、要么如实登记。

---

### 🔴-2 客户端把「从未交叉核验」讲成「两源已交叉核验」

`[client/Neckline/Views/AuctionCardView.swift:307-310]`

```swift
if payload.dataStatus.conflictCodes.isEmpty {
    Text("跨源冲突:本次为空(两源已交叉核验)。")
```

P2 **替换掉**了老版本的诚实披露(`git show 0cb5d00:…AuctionCardView.swift:262`:
「…这一项**结构性恒空**,不等于「已核对无冲突」」),换成一句**无条件的正面断言**。

**失败场景**:
- **备源整体失败的早晨**。`resolve_dual` 只在两侧都存在**且都过七项校验**时才调 `detect_conflict`;
  单源时 `detect_conflict` 返回 `None`。腾讯 404 / 限流的早晨 → `conflictCodes == []`、
  `sourceDegraded == false`、`freshness == fresh` → 逐票双源核验区 `worthShowing == false` 什么都不画
  → **屏幕上唯一那句话说"两源已交叉核验",而实际上一个数都没对拍过。**
- **单只票只有一源有效**时同理:全局那句话覆盖了所有代码。

Python 侧在**五处**说的是相反的话(`quality.py:358` / `mech.py:1128` / `collect.py:355` / `pipeline.py:172`
+ 守门 `tests/test_v240_p2_auction.py:444::test_single_source_is_never_reported_as_no_conflict`)——
**守门停在了屏幕前一层**。这正是审计规格 P2 目标里点名的病 ③「『跨源冲突为空』实际上没有进行过交叉核验」,
在展示层复发,也正是上一版复审 🔴-1「把『没判』折成『没问题』」的同款。

**修复方向**:判别式就在同一个结构体里 —— 只对
`payload.qualityDetails.filter { $0.checks.count >= 2 }` 那一部分声称"核验过",
其余如实说「本次只有一个源有有效读数,没有可对拍的第二个数」;
老报告(`qualityDetails.isEmpty` / `!hasDomainSplit`)说「这份报告生成于双源核验上线之前」。
然后把那条 Python 守门扩到扫这段 Swift 字面量。

---

## 二、🟡 重要(8 条)

### 🟡-1 `bootstrap_dev_db.py` **不幂等**:重跑第二次留下**两行 `is_active=1` 的章程**

`[scripts/bootstrap_dev_db.py:108-155]`(`_copy_reference_tables` 用 `INSERT OR REPLACE` 把
`strategy_versions` 连 `is_active` 列一起拷)+ `[scripts/bootstrap_dev_db.py:233]` +
`[scripts/activate_charter.py:304-306]`(已现役就早退)+ `[neckline/strategy/brain.py:355-361]`
(`get_active` 用 `ORDER BY created_at DESC LIMIT 1`,把问题遮住)。

**我独立复现了**(命令 = `PROJECT_PLAN` §五 P4.5 ⑤ 那条文档命令本身,`--reference-db data/neckline.db`,
而真库现役章程正是 `K1`):

```
run 1: K1=0 K2=0 K4=0 v1.2=0 v1.3=0 v1.3.3=0 v2.2-k8=0 v2.3-k8=1    ✅ 一行现役
run 2: K1=1 K2=0 K4=0 v1.2=0 v1.3=0 v1.3.3=0 v2.2-k8=0 v2.3-k8=1    ❌ 两行现役
run 3: 同 run 2
```

审计规格 P4.2 逐字要求「**可以重复运行且结果幂等**」——**这一条不成立**。
守门 `[tests/test_bootstrap_dev_db.py:185-206]` 只断言 `get_active().version` 与事件数,
**恰好是被 `ORDER BY … LIMIT 1` 遮住的那两样**;`strategy_versions` 也没有
`selection_packs` 那种部分唯一索引(`neckline/db.py:1787-1806` 只覆盖 `selection_packs`),库层静默接受。
**影响面仅开发/临时库**(生产护栏是好的),但结果正是「今天用的是哪版章程 = 看 `created_at` 谁大」。

**修复方向**:`_copy_reference_tables` 之后归一一次
(`UPDATE strategy_versions SET is_active=0 WHERE version<>目标` → `SET is_active=1 WHERE version=目标`);
`bootstrap()` 末尾加一条**响亮自检** `SELECT COUNT(*) FROM strategy_versions WHERE is_active=1` 必须为 1,
否则非零退出;守门断言改成那个**裸计数**。

### 🟡-2 一条负例测试对**权威库实弹打靶**

`[tests/test_bootstrap_dev_db.py:75-78]`

```python
def test_refuses_the_authoritative_db(self, capsys):
    rc = bootstrap_dev_db.bootstrap(settings.db_path, None)
```

把 bootstrap **真的瞄准** `data/neckline.db`,靠**被测的那道护栏**自己拦住。
护栏一旦被削弱或调序,跑一次 `pytest` 就会在权威库上 `init_schema` +
`activate_pack_set(K8-V0.8, C2, Z2, Y2)` + 激活章程 `v2.3-k8` —— **正是本次发版明令推迟的那件事**。
(旁证:该库现在 `selection_packs` 0 行、现役章程 `K1`,所以那会是一次**静默的真激活**。)

**修复方向**:改断纯判据 `assert bootstrap_dev_db._is_protected_db(settings.db_path) is not None`;
真要跑 `bootstrap()` 就 monkeypatch `settings.db_path` 指到 `tmp_path`。
(`test_refuses_paths_under_repo_data_dir` / `test_refuses_production_deploy_dir` 无此问题 —— 那两个路径不存在。)

### 🟡-3 回滚绳 ①「只回滚策略包」**比文档说的弱** —— 已拍板 #8 与实现不符

`[PROJECT_PLAN.md:786]`(已拍板 #8:「旧 C1/Z1/Y1 **行为逐位不变**以保回滚可复现」)·
`[PROJECT_PLAN.md:1606]`(P4.7-实测 ①:「产品代码可暂留 v2.4.0(旧包兼容行为是 P1.6 的硬要求)」)·
`[V2.4.0_AUDIT_REMEDIATION.md:492]`(P1 验收 10:「C1/Z1/Y1 旧包在兼容测试中保持 v2.3.3 行为」)

**实现**:P1.1(漏答 = unknown 不计降级)、P1.4(成员级 OUT 不再整篮连坐)、P1.5(unfit 四条件夹逼)
都是**代码语义、对所有包生效**;按包分叉的**只有** `tier_evidence.t2.formal_policy` 一条。
`[neckline/selection/gates.py:476-484]` 的 `t1_eligible` 已由 `any_unfit` 改读 `basket_level_unfit` +
`kept_member_codes`,对 C1 同样生效。`[tests/test_v240_p1_selection.py:371-390]`
(`test_old_pack_drop_reason_is_the_real_one_not_the_removal_marker`)**自己就演示了这一点**:
旧包下 `core_unfit` 成员被移除、篮子存活、出局原因换成了 `evidence_degraded_out`。

所以 **`activate_pack_set.py --file K8-V0.7 --file C1 --file Z1 --file Y1` 只回滚了
`max_evidence_degrades` 这一条硬判据,并没有回到 v2.3.3 的选股行为**。测试类名
`TestP1OldPacksUnchanged`(`[tests/test_v240_p1_selection.py:481]`,标题「旧包逐位行为不变」)
锁的其实只有那一条。

⚠ 这不一定是错的取舍(builder 在 `CLAUDE.md` 写了理由:「⛔ 别把成员级 OUT 也做成"新包才有" ——
那是把 bug 修复挂在版本号上」,这个理由站得住),**但它把一条已拍板结论重新解释了,而没有回到用户面前**。

**修复方向(不改代码)**:改文档 —— §四 回滚绳与 §五 P4.7-实测 ① 必须写明
「只回滚包 = 只回到旧的 T2 降级上限;**P1.1/P1.4/P1.5 是代码级、退不掉**;要真正回到 v2.3.3 选股口径,
必须走绳 ②(`git checkout v2.3.3`)」;已拍板 #8 的措辞相应收窄,并**当面跟用户确认这个收窄**。
测试类改名 / 补一条注释,别让下一个人以为它锁住了全部旧行为。

### 🟡-4 篮子卡上仍**宣传两条在 `v2.3-k8` 下不存在的机械纪律**

`[client/Neckline/Views/BasketCardView.swift:670]`

```swift
Text("失效说的是「这个驱动假设不成立了」,**不是**「手里的仓该卖了」——该不该走由持仓纪律(止损 / 回落止盈 / 时间退出)管。")
```

`v2.3-k8` 的 `take_profit_retrace=None` / `max_hold_days=None` —— **回落止盈与时间退出都不存在**,
−5% 那条线也已改叫「亏损警戒线」。这句话告诉用户有三条机械纪律在管他的仓位,其中两条是空的。
最终 DoD 第 15 条「**当前 K8 路径不再宣传机械回落止盈和机械时间退出**」**未满足**。
该行自 `0cb5d00` 一字未动(旧文件第 504 行),P3 的禁词守门
`[tests/test_v240_p3_frontend.py:118-124]` 只扫**规格逐字点名的四句**,扫不到它。

**同族第二处**:`[neckline/selection/basket_card.py:280]` `stop = f"章程止损 −{stop_pct:.1%}"`
—— `discipline_labels()` 不接受 `advisory`,K8 下卡上仍印「章程止损 −5.0%」,而同一屏别处叫「亏损警戒线」。
⚠ **这一处更急**:`discipline_labels` 的输出进 `to_card_json()`(`basket_card.py:1055`),
冻结卡 `INSERT OR IGNORE` **永不回填** —— v2.4.0 上产后每一天冻的卡都会把这个旧称呼**永久写死**,
事后修不回来。修它很便宜:`[basket_card.py:1279]` 已经在同一处解析了 `lw_action`,顺手传进去即可。

### 🟡-5 P0.7 判据 #1 与 #5 的守门**扫描域窄于它的名字**(注入实证两条都静默通过)

`[tests/test_v240_p0_retirement_guard.py:106-110]`(判据 #1)· `[:327-331]`(判据 #5)

- 判据 #1「生产判断删除 100%」的 `_BANNED_CALLS` 只 AST 扫 `neckline/sentinel/engine.py` **一个文件**。
  在 `neckline/api/app.py` 的 `board()` 里插一行**真调用** `evaluate_retreat(...)` → **38 passed**。
  ⚠ 同文件的判据 #3(`push_retreat_brake`)**是**全 `neckline/**`+`scripts/**` 扫的(`:270-282`)—— 两条口径不一致。
- 判据 #5「专用轮询删除 100%」只 grep 纳秒字面量 `60_000_000_000`。插
  `while true { try? await Task.sleep(for: .seconds(60)) }` → **38 passed**。

**不是当前 bug**(实际状态确实干净,我独立复算 P0.1 表 11 行 = 11/11 全部撤销),
是**守门的耐久性**问题 —— 而 P0.7 这七条恰恰是「P0 算不算做完」的合同本身。

**修复方向**:① #1 的扫描域扩到 `neckline/**`+`scripts/**`,豁免四个退役件自身(与 #3 对齐);
② #5 改扫**语义** —— 剥注释后对每份 Swift 正则找 `Task.sleep` / `Timer.scheduledTimer` 的任何出现,
建一份带理由的白名单(当前只有 `AppModel.swift:1364` 那条 2.4 秒 Toast),名单外命中直接红。

### 🟡-6 P0.5+ 新通道**零行为测试** —— 迁移的落点本身没被端到端验过

全仓找不到一条测试真的打 `/positions` 看 alerts 出不出来:
`[tests/test_contract_crosscheck.py:685-709]` 只断言"服务端声明了 `alerts:`"「客户端有 `struct PositionAlert`」
「四个键两边都有」「Swift 手写了 `init(from:)`」;`[tests/test_v240_p0_retirement_guard.py:431-437]`
只断言 `model_fields` 里有那几个键。`tests/test_api_positions.py` 里**一次 `record_pushed` 都没有**;
唯一「seed 事件再打端点」的是 `tests/test_api_report_board.py`,而它测的是**老 `/board`**。

**后果**:`_today_position_alerts` 的过滤条件、`_POSITION_ALERT_LEVEL` 的映射、`app.py:1218` 的挂载
—— 任何一处改坏,4251 条测试照样全绿,而用户看到的是「今天没有提醒」。
P0.8 用例 9/10(5% 亏损警戒仍出现 / 离场参考仍可到达)目前只被**引擎层**覆盖(证明事件写进去了),
**投递层没有**。(探针实测通道今天是好的:`['stop_approach','exit_reference']`,
level 映射正确,预置的 retreat/invalidation 行未泄漏。)
**修复方向**:随 🔴-1 改过滤器时把这三条落成正式用例。

### 🟡-7 `QuoteQuality.status` 在**一个读数都没有**时伪造 `timestamp_unparseable`

`[neckline/auction/quality.py:402-407]` `return self.checks[0].status if self.checks else QS_TIMESTAMP_UNPARSEABLE`

两源都没返回时 `checks` 为空,`status` 却报一个**证明没发生过**的具体校验失败(压根没有时间戳可解析)。
实测 `resolve_dual("999999.SZ", DualQuote(code=…))` → `freshness=insufficient, status='timestamp_unparseable', errors=()`。
它顺着 `[mech.py:593]` 进 `members_json`(冻结审计行,`INSERT OR IGNORE`,永不重写)→
`AuctionMemberOut.quoteStatus` → 客户端。**与本模块自己两函数之上的教条直接冲突**
(`quality.py:213`:「「没拉到」与「拉到了但不合格」是两件事…⛔ 不许折平」),
也与 `mech.py:218-221` 刻意保留的 `UNDET_NO_QUOTE` vs `UNDET_QUOTE_INVALID` 之分矛盾。
今天没有视图画 `quoteStatus`,所以屏幕上不撒谎,**但落库的审计行在撒谎**。
**修复方向**:空 `checks` 时给 `QS_NO_QUOTE`(或返回 `""` = 「本次没记这一位」,客户端已有正确标签)。

### 🟡-8 `captured_at` 一旦是 aware datetime,竞价层**整层静默零落库**

`[neckline/auction/quality.py:288]`(裸 `src > captured_at`,两侧都假定 naive)·
`[neckline/auction/collect.py:369]`(`clock = now_fn or datetime.now`)

生产路径 `[neckline/api/app.py:305]` 走 `datetime.now()`(naive),今天没问题。
但同一个 `app.py` 在三处(`:1732/:1772/:1872`)用的是 `datetime.now(CN_TZ)`(aware)——
本仓房规就是那一套。实测传 aware `captured_at` 会在 `validate_quote` 里
`TypeError: can't compare offset-naive and offset-aware datetimes`;
`collect_auction_snapshot` 只把 *fetch* 包了 try/except,`resolve_dual` 循环**没包**,
异常会一路逃到 lifespan 的兜底 `except` → 「竞价确认层异常(已吞)」→ **每天早晨静默零落库**。
**修复方向**:边界处归一(`if captured_at.tzinfo: captured_at = captured_at.replace(tzinfo=None)`)+ 一条守门。

---

## 三、🔵 建议(15 条,不阻断上产)

1. **`[PROJECT_PLAN.md:2325]` 的「零新阈值」自证不完整**。交易语义阈值确实一个没发明(已逐条核验),
   但 P3 引入了 **8 个没有来源的 Swift 版式常量**:`BasketDailyView.swift:202` 的 `620/720/420`
   (相邻两个 sheet 用的是 `1120/1120/700` 与 `760/860/640`,一个都没复用)、
   `BasketCardView.swift:823` 的 `0.12`(同族条用 `0.14`/`0.22`)、`:839` 的 `opacity(0.32)`
   (同款段落是 `0.30`)、`:848/:852` 的 `height: 11 / 9`、`:837/:840/:845/:849` 的 `offset(y:)`、
   `NKStopScale.swift:71` 的 `5.5`。**都是纯版式、不进任何判据**,但那句自证只提了 `labelHalf` 一处。
   建议把这句话改成「交易语义阈值零新增;版式常量新增 N 个,清单如下」。
2. **`design_handoff_p3_frontend_reduction/`(3800+ 行)是自供来源**:它在 builder 自己的 P3 commit
   `731770d` 里进仓,被 §九 描述为「用户提供的设计交接包」,却**从未出现在 planner 写的 §五 施工图或
   `V2.4.0_AUDIT_REMEDIATION.md` 里**。仓内无法证实其出处。**建议用户确认一句**:这包是不是你给的?
   (若不是,那就是"自己写一份规格再照着它施工"——本项目 `CLAUDE.md` 明令防的那个模式。)
   ⚠ 好消息:所有能追到它的数值**独立地**在 `0cb5d00` 就存在,结论不依赖它。
3. **零容差守门可被绕开**(`[tests/test_v240_p2_auction.py:621-641]`):只扫
   `neckline/auction/quality.py` 一个文件、只禁四个字面名。嵌一层 `if (src-captured_at).total_seconds() > 3:`
   守门全绿;更要紧的是它**不看调用方** —— `collect.py:459` 传 `captured_at + timedelta(seconds=3)` 也逮不到。
   裁定 #2 明写「施工 Agent 不得自行设定」,守门该覆盖调用点。
4. **`pack.py:1216` 的 `uuid4().hex[:12]`**:`12` 无出处(仓内 `0cb5d00` 零 `uuid` 使用),也没有守门锁格式。无害,但登记一下。
5. **`MarketMech.critical_quality` / `context_quality` 算了但没人读、也不落库**(`[mech.py:352-353,1013-1031]`)
   —— API 侧另有一份 `_worst_quality`(`app.py:2449-2450`)。同一件事两份实现,其中一份是死的。
6. **`auction_reports.fetched_codes` 与市场级 `data_quality` 语义变了但没有判别列**:
   `snap.quotes` 自 P2 起只装**可用**读数,于是 `fetched_codes` 从「抓到几个」变成「几个能用」。
   verdict 级有 `critical_data_quality IS NULL` 当版本判别,这两个没有。建议在 DDL 注释里点明
   「`quote_quality_json IS NULL` = v2.3.3 口径」。
7. **「有界」双源是在语义层限界、不在取数层**:`collect.py:435` 对**整份关注池**调 `fetch_dual`。
   今天关注池上界 200 < `_CHUNK_SIZE=400` → 净 +1 请求(实测:1–400 码 = 2 次、401 码 = 4 次、900 码 = 6 次)。
   哪天关注池过 400,「每早 +1 次」会**静默变成 +N**。建议加一条 `assert len(requested) <= _CHUNK_SIZE`。
8. **`aggregate.py:2308-2310` 的注释夸大了保证**:说「整段异常也带着 seed_count 返回」,
   而 `:2428` 的兜底 `return` 并不带。行为本身是诚实的(`None` = 当时没记),改注释即可。
9. **`client/project.yml:83` 的注释指向不存在的文件** `tests/test_client_test_host.py`(真守门在
   `tests/test_v240_p4_release.py:47-72`)。下一个人按名字 grep 会以为没有守门。
10. **`tests/test_v240_p4_release.py:40` 把 `"2.4.0"` 写死**:以后每次升版都得改守门测试,
    而"为变绿改守门"正是 P4.5 禁止的习惯。建议从 `app.py::VERSION` 反推,只留一致性断言。
11. **`test_every_marketing_version_in_pbxproj_is_the_same`(`:97-104`)只断言集合、不断言个数**:
    某次生成把 project 级那处整个丢掉,它仍绿 —— 正是 P4.4 要堵的盲点换了个形状。补 `assert len(...) == 4`。
12. **`git diff --check` 守门漏 `--cached`**(`[tests/test_v240_p4_release.py:246-253]`),
    且会因开发者本地任何无关空白改动变红。
13. **`bootstrap_dev_db.py:222` 绕过四道闸**直调 `pack.activate_pack_set()`:
    闸 1 的 `check_threshold_governance` 对账与闸 2 的 `engine_api.is_compatible` 都不跑,
    「能 bootstrap 出来的 dev 基线」可能是「`activate_pack_set.py` 会拒绝的组合」。今天四个包都过闸,属潜伏项。
14. **`models_text()` 的扫描域只有 `Networking/Models/`**:DTO 若落在 `Networking/` 根或别处,
    35 处用 `models_text()` 的守门看不见它(`networking_swift_text()` 能看见)。
    `CLAUDE.md` 已写规矩但没有机器判据 —— 建议加一条「`Networking/` 根上不许有 `Decodable`」。
15. **P0.2 小提示双端落点不同**(`BasketDailyView.swift:101` iOS vs `:243` macOS):
    两处都合规(各自平台恰好一次),只是邻居不同,截图对账时容易起疑。
16. **`activate_*.py` 用 `--db`、`bootstrap_dev_db.py` 用 `--db-path`**,而 §五 P4.5 把它们排在一起。
    打错会被 argparse 当场拦下(安全),仅提一句。

---

## 四、✅ 实测确认没问题的高风险点(这些同样是结论)

**这一节是我自己跑出来的读数,不是复述 builder 的自证。**

### 数据库迁移(高危区)—— 独立实测通过

用 `0cb5d00` 那份 `_SCHEMA` 造真老库 → 塞历史行 → 跑 HEAD 的 `init_schema` **三次**:

```
auction_reports / auction_verdicts / basket_stage_handoff / selection_pack_activation_log
  行数 1→1,老列逐位相同: True(四张表全部)
7 个新列: 全部 notnull=0 / default=None / 老行值 None
PRAGMA integrity_check: ok
```
全库 59 表 `PRAGMA table_info` 逐元组对拍:**added=7 / dropped=0 / changed=0,表数 59→59**。
无回填(`_migrate_columns` 只 `ALTER TABLE … ADD COLUMN`,靠 `PRAGMA table_info` 先探测 → 天然幂等)。
老行经 store → API → 客户端全链读回:`None` / `""` 三态一路保住,`_worst_quality` 把 `None` **丢弃**
而不是当 `ok` 排序。四个新机械列**不在**两张 LLM UPDATE 白名单里,`finalize_*` 仍是**静态 SQL 字面量**
(AST 守门没失明)。

### 四线原子激活(高危区)—— 四种失败注入全部完整回滚

用 `git show 0cb5d00:packs/K8-skeleton.json` 取回 `K8-V0.7`,建与生产同构的临时库,注入四种**独立**失败:

| 注入 | 结果 |
|---|---|
| 最后一条线(Y)真实 append-only 冲突(**无 monkeypatch**) | 抛 `ValueError`;现役集合原值一字不差;`activation_log` 零新增;**前面几包的 INSERT 也一并回滚** |
| 第一条线(V)失败 | 四线原值,零事件,`C2/Z2/Y2` 一行没出现 |
| 中途 `sqlite3.OperationalError` | 异常透传,库三项全不变 |
| **`KeyboardInterrupt`(BaseException)** | **同样完整回滚** —— 这条最关键 |

机制确认:回滚**不是** `with conn:`,而是 `[neckline/db.py:1923-1930]` 的 `connection()` 上下文管理器
(`commit()` 只在成功路径,异常走 `finally: close()`,SQLite 关连接即回滚)。
`_activate_one` 确认**不 commit / 不清缓存 / 不 `init_schema`**;`activate_pack_set` 在 `:1218` 先
`init_schema`(独立连接)、`:1229` 才开事务 → **DDL 隐式提交的隐患不存在**。
**四道闸一道没放宽**:`check_threshold_governance` 在区间内**逐字节相同**,
`engine_api.py` / `primitives.py` / `scripts/activate_pack.py` **零改动**,唯一校验变更是 P1.6 加了个可选键(是加不是放宽)。
**闸 1 对称性四向都拒**(新骨架单独 / 新骨架+旧引擎 / 旧骨架+新引擎 / 半批),全部 `rc=2` 且库字节不变。
**dry-run 真的零写**(md5 逐字节相同、不消耗 batch_id);`--confirm` 是硬要求;
批量前后 `strategy_versions` **整表逐行逐列字节相同**(章程确实不参与该事务)。
**P4.7-实测 ① 那条回滚命令逐字可跑**:起真子进程跑正向 + 反向 → `rc=0`,现役回到 `K8-V0.7/C1/Z1/Y1`,
事件 **+8 追加**(12→20),历史一行没抹。

### 用户裁定

- **裁定 #1(题材域 / `ths_member`)逐字落地**。`[core_metrics.py:254-264]` ② 层整层跳过,
  `theme_domain_not_implemented` 真落库;全仓 `ths_member` 的 10 个引用**没有一个**在判定路径上
  (板块展示 / 情报 / 资金流 / 数据层),`resolve_comparison_domain` 是唯一实现;
  比较域 ① 取的是**合并前各 seed 的 presented member universe 并集**(`aggregate.py:2014`),
  不是最终入篮的一到三只 —— K8 §五-4 明令防的自证循环没有发生。
  回退触发条件是「除自己以外同域成员数 = 0」(定义,不是阈值),`[core_metrics.py:154-156]` 明确拒绝 `peer_count ≥ N` 门槛。
- **裁定 #2(零容差)逐字落地**。`[quality.py:288]` 就是裸 `if src > captured_at:`;
  全 `neckline/auction/**` + `sentinel/quotes.py` 搜 `TOLERANCE|SKEW|GRACE|timedelta(seconds=` **零命中**;
  `_EPS=1e-9` 只用在价/量比较,从不碰 datetime。**「源时间早于抓取时刻」被正确接受**(实测 −1s / −60s / 相等 → `fresh`,+1s → `future_timestamp`)。
  时间窗用 K8 自己的 9:25 边界,且 `AUCTION_RESULT_TIME_START` **is** `sentinel/capture.AUCTION_CAPTURE_START`(同一个对象,没有第二份字面量)。
  误判可被发现:`invalidCodes` + `RISK_QUOTE_INVALID` + 逐票 `validation_errors` 三处都说得出「为什么这一格不算数」。

### P1 语义

- **成员级 OUT 没有「凭空消失」**:`[basket_store.py:887-909]` 两个来源都在,生产调用方
  `[report/evening.py:526-529]` 传的是**对拍前**的 `pre_gate_by_key` + `engine_by_key=gate_out.summaries`(两个前提都对);
  篮子里查不到该成员时**如实写一行裸码 + WARNING**,⛔ 不静默丢。
- **五态分得开**:漏答 / 模型明说 `unknown` / 枚举外取值 → 统一落 `PASS + available=False + blocks_t1=True`,
  **不进 `degraded_gates`**(`[gates.py:1341-1349]` + `[:1651-1652]`);落库枚举仍只有 `ok|weak|unfit|""`,
  ⛔ 没有扩外部枚举。`has_unavailable` 是 T1 的独立必要条件。
- **`unfit` 四条件夹逼只管市场关/板块关**,成员两关不受夹(`[gates.py:938-983]` + 正面用例 `:466`);
  夹逼留痕齐(`unfit_clamped_to_weak` + `*_verdict_before_clamp`)。
- **被移除的成员不再影响留下那些成员的档位**:`_counts(c)` 三处口径一致(降级关 / 挡 T1 理由 / 判不出的关)。
- **包文件零数值漂移**:`K8-V0.7 → V0.8` 的 `config` 只有 11 条 `threshold_governance` 键前缀 `C1→C2` 等,
  30 个数值叶子**逐位相同**;`C2/Z2/Y2` 对 `C1/Z1/Y1` **只多一个字符串键** `formal_policy`,零数值差、零删键;
  `engine_code` 仍是线码。`ENGINE_API_VERSION` 保持 2。
- **`CARD_SPEC_VERSION` = `basket_card_v5`**,老 v4 卡按 OR 兼容读回;
  客户端 `BasketGates.gateAvailable` 是真三态(`Bool?`:`nil`=未记录 / `false`=判不出 / `true`=判得出),
  「判不出」优先于 verdict,⛔ 没有照 `pass` 画成「过」。

### P2 两域拆分

构造 `AuctionSnapshot` 直接调 `mech.basket_quality_domains` + `llm.clamp_verdict`:
无关指数缺失 → `critical=ok`、**不夹**;成员自己的指数缺失 → `critical=degraded`、confirm 与 veto **都夹成 neutral**;
喂一个「有 `data_quality='ok'` 但没有 `critical_quality` 属性」的对象 → **仍然夹**(默认拒,不回退);
科创板成员 `market_index_of → (None,'board_excluded')` → **不算缺失**(三支指数全缺也不夹);
「对照不足」结构性进不了关键域(P1-78 那条连带不会把整篮拖成中性)。
**闸 2 / 闸 3 逐字节未动**(`llm.py` 整个 delta 只有 4 个 hunk:docstring、两段 prompt、闸 1 那一个 `if`)。
`get_quotes()` **逐字节未动**(`quotes.py` 的 diff 只删了 `__all__` 一行);
跨源冲突判据**零百分比阈值**(实测 0.00001% 反向 → `direction_opposite`;平盘 vs 上涨 → 不算冲突;
三态钩子要求两边都非 `None` 才比);**两源原始读数都留痕**(不合格时也留)。

### P3 前端

- **`Models.swift` 拆分守门我做了注入实测**:删掉一份 → 报红;改名一份 → 报红;
  哨兵机制 `_assert_sentinels()` 真的能逮住「扫描域缺块」。
- **零服务端字段删除**:schemas.py 逐类逐字段对拍 —— **removed = 0,added = 46**。
- 服务端新增文案里**无 Markdown**(逐行扫 added 字符串字面量,命中全是 docstring/注释)。
- 章程派生文案单一源 `charter_copy.py` 已被 `holding.py` / `precall.py` / `app.py` / `basket_card.py` 四处消费;
  advisory 分支的**线名本身**已改(不只是前缀),刻度尺 `stopLabel`、`scaleExplainLine` 都随章程走;
  **老章程仍说老话**(`discipline_labels(0.05, 0.08) == ["章程止损 −5.0%","回落止盈 8.0%"]`)。

### 测试与版本

- **全量 `pytest` 我跑了两次:两次都 `4251 passed / 3 skipped / 0 failed`**(118.10s / 116.31s);
  五个新测试文件 + 两个新脚本测试**连跑三次** `231 passed`,零间歇。
- **`data/neckline.db` md5 全程未变**;新测试**无墙钟断言**(`datetime.now()`/`date.today()`/`time.time()` 零命中)、
  **无 db 泄漏**(34 处显式 `db_path=`)。
- 🔴 **macOS `xcodebuild test` 我自己跑了:`** TEST SUCCEEDED **`,`Executed 228 tests, 10 skipped, 0 failures`**
  —— P4.1 的核心主张属实(此前恒报 `Could not find test host`)。
  修法正确:`project.yml` 用 `[sdk=macosx*]` 条件覆盖,iOS 那条未动;
  把 `client/` 拷出去重跑 `xcodegen generate`,产物与仓库 pbxproj **除 UUID 外逐字节相同** → 重生成不会丢这个修复。
- **版号四处一致**:`app.py:227 = "v2.4.0"` · `project.yml:12 / :47 = 2.4.0` · pbxproj **四处**(含 project 级)= `2.4.0`。
- **三个 tag 全是 annotated**:`v2.4.0`→`659a42b`(=HEAD)· `v2.3.3`→`0cb5d00` · `v2.0.0`→`2e0f611`(**未动**)。工作区 clean。
- **`bootstrap_dev_db.py` 三条护栏实测都拦得住**(`settings.db_path` / 仓库 `data/` 绝对与相对 / `/opt/neckline`,
  全部 `rc=2` 且**拒绝发生在创建文件之前**);参考库走**独立只读 URI 连接**(⛔ 无 `ATTACH`);
  白名单四张表,对生成的 dev 库做**全文件字节扫描** —— 假 `api_key`(`llm_providers` 与 `app_settings` 各一)
  与假持仓**一个都没进去**,而 `trade_cal`/`namechange` 确实拷到了(排除"什么都没拷"的假绿);
  章程派生**真的会 fail loud**(喂伪造 K1 config → 当场 `rc=2` 报「期望 K1 基线 20000.0,实际 0.5」),源码零章程字面量。

### P0 退役本体

- **三处「同名不同物」零误伤**:P0 提交对 `data/board.py` / `auction/mech.py` / `selection/basket_card.py` /
  `sentinel/{holding,precall,capture}.py` 的 diff **逐个为 0 行**;`invalidation_spec`(13 处)、
  `hit_invalidation`(29 处)、`decision_log.invalidation`、`Board` 枚举、`retraceState` / `take_profit_retrace` 全部完好。
- **`retreat_metrics` 只停写不 DROP**,预置历史行「既不被读成状态、也不被删掉」。
- **`TickResult` 五个退役观测位物理删除**,守门按 `__dataclass_fields__` **反向断言**「一个都不许长回来」。
- **`RETIRED_KINDS` 是与 `ALL_KINDS` 并列的第二张表**,`ALL_KINDS` 仍含 `KIND_RETREAT`(长度仍 14)→
  旧客户端 `PUT /settings/push` 不会 422;闸门在**唯一扇出路径** `push_event` 上、且**排在开关闸之前**。
  客户端隐藏开关走服务端下发的 `retired` 位,⛔ 无硬编码黑名单。
- **P0.1 表 11 行我独立复算 = 11/11 全部撤销**(七个被禁函数在整个生产链的真实调用点 = 0;
  「剔除勿进」「今日计划作废」「禁止开新仓」「退潮红色刹车已触发」在三棵树里各 0 命中;
  `BoardSection.swift` 整文件删;`fetchBoard()` 零调用方)。**七种禁止变形全客户端剥注释扫描,一条都没有。**
- **P0.2 小提示**文案单一源 `NKCopy.intradaySelfObserve`,全客户端字面量恰好 1 处,
  两个渲染点平台互斥(同一平台恰好一次),形态低强调且守门逐个禁 `Button`/`onTapGesture`/`NKChip`/警示色/`Task`。

---

## 五、最终 DoD 20 条逐条复核

| # | DoD 条目 | 判定 | 依据 |
|---|---|---|---|
| 1 | 非持仓 T1/T2 不再运行通用盘中证伪 | ✅ | `run_tick` 两段整删 + 6 拍后 `sentinel_events` 行数差 = 0(喂的行情**刻意仍命中旧阈值**) |
| 2 | 代理池不再生成退潮 / 全局刹车 / 禁开仓 | ✅ | 同上 + `retreat_metrics` 停写 + 三个词全树 0 命中 |
| 3 | 客户端无盘中动态页 / 红条 / 徽标 / 绿灯 / `/board` 轮询 | ✅ | `BoardSection.swift` 整文件删;`fetchBoard()` 零调用方;60s 轮询全客户端仅剩一处 2.4s Toast(非轮询)。⚠ 守门口径窄见 🟡-5 |
| 4 | 今日篮子只保留一次低强调静态提示 | ✅ | 字面量恰好 1 处 + 双端各一个平台互斥渲染点 |
| 5 | D0 预案 / D1 竞价 / 亏损警戒 / 离场参考 / 交易时钟 / 临时提醒保持有效 | ⚠ **部分** | 五项 ✅ 实测通;**「持仓提醒」这一类有四种失去 App 内落点** → 🔴-1 |
| 6 | 一个不合格备选不再拖死有效篮子 | ✅ | 成员级 OUT 实装 + `t1/t2_eligible` 只看篮子级 unfit |
| 7 | leader/core/elastic 用不同核心判据 | ✅ | `K8_CORE_CRITERIA` 三分,并明写「不要因为不是龙头就判 core/elastic `unfit`」 |
| 8 | 比较域符合 driver → theme → industry | ✅ **按裁定 A** | ② 层永不产出 + `theme_domain_not_implemented` 落库(裁定 #1) |
| 9 | 缺失与漏答不再通过降级计数导致 OUT | ✅ | 第三态落 `PASS+available=False`,不进 `degraded_gates` |
| 10 | 未校准的 T2 降级上限只进影子账本 | ✅ | `formal_policy=no_hard_fail` + `T2_SHADOW_THRESHOLD_KEY` 影子行(旧包上仍是活判据 → 不出影子行) |
| 11 | 竞价行情验证源交易日与时间戳 | ✅ | 七项校验 + 四态 freshness;零容差 |
| 12 | 无关指数缺失不再强制整篮中性 | ✅ | 构造输入实测 |
| 13 | 关键股票完成有界双源核验 | ✅ | +1 请求/早晨(实测计数);⚠ 界在语义层不在取数层 → 🔵-7 |
| 14 | 晚间 LLM 故障不再显示成「今天没有篮子」 | ✅ | `selectionStage` 四字段 + 「选股解释未完成」;未解释 seed 进不了 baskets/OUT/gates |
| 15 | 当前 K8 路径不再宣传机械回落止盈与时间退出 | ❌ | `BasketCardView.swift:670` + `basket_card.py:280` → 🟡-4 |
| 16 | 选股首屏以 T1/T2 与交易预案为主 | ✅(凭实拍) | 11 张实拍 + 结构守门;⚠ 大字体一项未拍,已挂 §七 P3-79 |
| 17 | macOS 客户端测试真正执行 | ✅ **我自己跑过** | `TEST SUCCEEDED`,228 executed / 0 failed |
| 18 | 本地可用临时 DB 重建现役版本集合 | ⚠ **部分** | 首次运行 ✅;**第二次运行留下两行现役章程** → 🟡-1 |
| 19 | v2.3.3 历史记录 / 冻结卡 / 复盘结果完全不变 | ✅ **我自己跑过** | 老库迁移演练:四表老列逐位相同、7 新列全 NULL、`integrity_check=ok` |
| 20 | 版本 / tag / 部署产物 / 运行中 `/health` 全为 v2.4.0 | ⚠ **未做** | 前三项 ✅;`/health` 仍返 `v2.3.3`(未部署,任务边界内的开放项) |

**结论:18 条满足(其中第 8 条按裁定 A)· 第 5 / 18 条部分满足 · 第 15 条不满足 · 第 20 条部署侧未做。**
builder 自报的「18 满足 + 第 8 条按裁定 A + 第 20 条部署侧未做」**略微高估**:漏了第 15 条与第 5 / 18 条的缺口。

---

## 六、上产建议

**先修**(都很小,加起来一次施工):
1. 🔴-1 `_today_position_alerts` 白名单 + 客户端 label 映射(**需要你先拍一下白名单范围**)。
2. 🔴-2 `AuctionCardView.swift:307` 那一句改成条件式披露。
3. 🟡-2 `test_refuses_the_authoritative_db` 改成纯判据(这颗地雷跟改不改代码无关,越早拆越好)。
4. 🟡-4 的第二处(`basket_card.discipline_labels`)—— **上产后就冻进卡里,事后修不回来**,建议一起做。

**可以上产后再修**:🟡-1(dev 库幂等)· 🟡-3(改文档 + 找你确认已拍板 #8 的收窄)· 🟡-5 ~ 🟡-8 · 全部 🔵。

**建议主会话再复审一遍取并集的三块**(我这轮已实测通过,但都是高危区):
- **发版脚本**:`scripts/activate_pack_set.py` + `pack.activate_pack_set()`(四种失败注入全过,但这是第一次上产用)。
- **数据库迁移**:7 个可空列(独立演练通过;生产库比开发库大得多,建议 P4.6 第 4 步的副本演练照跑不跳)。
- **推送链**:`RETIRED_KINDS` 闸门本身没问题,但 🔴-1 一旦改了 alert 白名单,**改完要再审一次**
  ——「哪些事件算持仓提醒」是产品语义,不是工程可自决的。

---

## 七、逐条销项(**2026-08-13 整改后由 @builder-pro 填**)

> 🔴 **每条都有去处**(已修 / 已挂账 / 判定不成立 + 理由),⛔ 无一条无声跳过。
> **用户 2026-08-12 两条拍板**:**裁定 A** = 🔴-1 白名单四类全收(`{holding, attention, circuit, precall}`,
> 且必须是**正面白名单** + 守门双向锁);**裁定 B** = 🟡-3 接受收窄、改文档(原文保留 + 标注被谁取代)。
>
> **读数**:Python **4321 passed / 3 skipped / 0 failed**(连跑两次,复审基线 4251 → **+70**)·
> macOS `TEST SUCCEEDED` **228 / 0** · iOS `TEST SUCCEEDED` **239 / 0** · **实拍 6 张** ·
> `data/neckline.db` md5 全程 `7ca02c7d99a01f4226442a3edb085c9b` 未变 · 工作区收尾 clean。

### 🔴(2 条)

| 条 | 处置 | 落点 |
|---|---|---|
| 🔴-1 四类提醒失去 App 内落点 | ✅ **已修**(裁定 A) | `api/app.py::_POSITION_ALERT_SENTINELS`(正面白名单)+ `_RETIRED_ALERT_SENTINELS`(**只做反向断言**)+ `_position_alert_level()`(含 `basket<id>` **前缀分派**)+ `PositionModels.swift::nkPositionAlertLabel` 四条;守门 `tests/test_v240_review_remediation.py::TestPositionAlertWhitelist`(四类都在 ∧ 与退役两类交集为空 ∧ **过滤器源码必须是 `in 白名单`**)。**`precall/position_low_open` 在 App 里的位置**:持仓卡收起行「今日有 N 条提醒」→ 持仓详情「今日提醒」卡首条(红色实心徽标「竞价低开逼近/跌破亏损警戒线」+ 09:26 + 落库原话),**iOS + macOS 各一张实拍** |
| 🔴-2 「从未交叉核验」被讲成「已交叉核验」 | ✅ **已修** | 判别式**单一源** `auction/quality.py::_is_cross_verified`(`resolve_dual` 里触发 `detect_conflict` 的那个 `if` **直接调它**;守门反向禁 `cp.ok and cb.ok` 抄第二份)→ `QuoteQuality.cross_verified` → `to_dict()` 落库 → `AuctionQualityDetailOut.crossVerified` → 客户端 `crossVerifiedCount` **只数不重推**。文案改**四态**:有冲突 / 老报告(那时结构性恒空)/ **一只都没对拍成**(说「是『没得比』,⛔ 不是『已核对无冲突』」)/ 对拍过 `N/M`。守门 `TestCrossVerifiedIsNotFaked` + `TestCrossSourceCopyIsConditional`(含**禁那句无条件断言复活**);**两种状态双端各一张实拍** |

### 🟡(8 条:7 修 + 1 改文档)

| 条 | 处置 | 落点 / 备注 |
|---|---|---|
| 🟡-1 bootstrap 不幂等 | ✅ 已修 | `_restore_single_active_charter()`(`is_active` 是本库状态、⛔ 不是参考数据)+ 末尾**裸计数**自检(rc=3),且**排在 `active_charter.version` 那条之前**;守门连跑三次断裸计数 + **反向探针**(掐掉两道自愈后自检必红) |
| 🟡-2 负例对权威库实弹 | ✅ 已修 | 改断纯判据 `_is_protected_db(settings.db_path)`;另立一条用**替身 `settings`** 指 tmp 下不存在的路径真走 `bootstrap()` 拒绝分支 —— 万一护栏失效,写坏的也只是 tmp |
| 🟡-3 回滚绳 ① / 前提 #8 | ✅ **改文档**(裁定 B) | §五 前提 #8 **原文保留 + `~~删除线~~` + ⚑ 标注被谁取代** + 新增「收窄」说明段;P4.7 正文与 P4.7-实测 ① 行写明「**只回滚包 ≠ 回到 v2.3.3 选股行为,要真回去必须走绳 ②**」;`TestP1OldPacksUnchanged` docstring 点明「类名会骗人,它锁的只有那一条 pack 开关」。⚠ **代码一行未改**(采纳 builder 原理由:⛔ 不把 bug 修复挂在版本号上) |
| 🟡-4 冻结卡旧称呼 + 卡上宣传两条不存在的纪律 | ✅ 已修(两处) | `discipline_labels(..., advisory=)` 线名改由**单一源** `charter_copy.stop_line_label` 派生;`to_card_json` 用 `brain.stop_is_advisory(self.charter_version, {...loss_warning_action})` = **这张卡当时那版**章程(⛔ 不是"现役");`BasketCardView.swift` 那句不再点名「止损 / 回落止盈 / 时间退出」,改指向卡自己冻结的「纪律标签」。🔴 **整改时另逮到第三处**(本报告只点名了两处):`PositionsView` 停牌那一支写着「**时间退出判向挂起**」—— `v2.3-k8` 没有这项纪律,也就没有「判向」可挂起;⛔ 不是删掉,而是按**既有唯一判据** `hasTimeExitRule` 分档。**最终 DoD 第 15 条自此满足**;⚠ 但这也说明本报告那份清点**不是穷举的**。⚠ 老章程下线名由「章程止损」→「章程止损线」(单一源没有不带「线」的形式),**5 条旧断言已在原处写明被 🟡-4 取代** |
| 🟡-5 P0.7 #1/#5 守门扫描域 | ✅ 已修(两条) | #1 扩到 `neckline/**` + `scripts/**`(与 #3 对齐),豁免仅限**四个退役件自身**且有反向校验;#5 改扫**语义**(剥注释后禁一切"等时间"写法,白名单 2 条各带理由 + 反向校验名单不许过期)。🔴 **复审给的两个逃逸探针逐个注入实测:两条都当场红**(`evaluate_retreat` 插进 `app.py` / `Task.sleep(for: .seconds(60))` 插进 `RootView.swift`) |
| 🟡-6 P0.5+ 零行为测试 | ✅ 已修 | `TestPositionAlertsEndToEnd` 5 条(四类全到 / **退役两类不泄漏** / 市场级与指数级匹配不到任何持仓 / 只画自己那笔 / 空数组⛔ 不合成「暂无异常」)—— 全仓**第一次**有测试真的 seed 事件再打 `/positions` |
| 🟡-7 `status` 伪造 `timestamp_unparseable` | ✅ 已修 | 空 `checks` → `""` =「本次没记这一位」(客户端已有标签「本次未记录」,⛔ 不扩枚举);反向用例锁住「真解不出时间戳时那个码照旧要报」 |
| 🟡-8 aware `captured_at` 整层零落库 | ✅ 已修 | `_as_market_naive()` 在 `validate_quote` 边界归一,🔴 **按 `CN_TZ` 换算再剥时区**(⛔ 不是裸 `replace(tzinfo=None)` —— 那会把 UTC 当北京时刻用、整份快照全判 `future_timestamp`)。四条用例:不再抛 / aware 与 naive 逐位相同 / UTC 也对 / **零容差没被顺手放宽** |

### 🔵(16 条:修 11 · 挂账 4 · 判定不成立 1)

| # | 处置 | 说明 |
|---|---|---|
| 1 「零新阈值」自证不完整 | ✅ 已修(文档) | §四 新增「🔵-1 订正」小节:以后**一律写三句** —— ① 交易语义阈值零新增(`test_no_unwhitelisted_module_level_numeric_thresholds` 常年兜着)· ② 纯版式常量 **8 个**(复审点名,不进任何判据)· ③ 工程标识量 1 个 |
| 2 设计交接包自供来源 | 🟠 **挂账** → §七 **P2-84** | 出处问题不是技术问题,**⛔ build 不自决**;已把「要用户回答的只有一句」写清。✅ 复审已核实结论不依赖它 |
| 3 零容差守门可绕开 | ✅ 已修 | 扫描域扩到全 `neckline/auction/**` 的**真代码** + **盯调用点**(传给 `validate_quote`/`resolve_dual` 的 `captured_at=` 必须是裸名字);⚠ 只盯这两个入口 —— `MarketMech(captured_at=…isoformat())` 是格式化落库,一把梭会误判。**注入 `captured_at + timedelta(seconds=3)` 实测当场红** |
| 4 `uuid4().hex[:12]` 无出处 | ✅ 已修 | 提成 `_BATCH_ID_HEX_LEN` + `_BATCH_ID_RE` 锁格式 + 白名单登记理由(它触发了 `neckline/selection/**` 那条常年守门,**正是那条守门规定的处置路径**) |
| 5 市场级两位算了没人读 | 🟠 **挂账** → §七 **P1-82** | 删一个"看起来没人用"的字段属**报告之外的改动**;修法二选一已写清(⛔ 别两份都留着) |
| 6 `fetched_codes` / `data_quality` 语义变了没判别列 | ✅ 已修 | `db.py` DDL 注释点明语义在 P2 变过 + **判别列 = `quote_quality_json IS NULL`**(= v2.3.3 口径),⛔ 别拿两个版本的数直接比 |
| 7 「有界」在语义层不在取数层 | ✅ 已修(改法不同) | 关注池过 `_CHUNK_SIZE` 时**响一句 WARNING** 说明「+1 已变成 +N」;🔴 **刻意不 `assert`** —— 那一段的异常会一路逃到 lifespan 兜底 =「竞价确认层异常(已吞)」= 每天早晨静默零落库,**正是 🟡-8 那个出口** |
| 8 `aggregate.py` 注释夸大 | ✅ 已修 | 订正为「**种子拿到之后**的每一条返回路径都带着它」;并写明兜底 `return` 确实不带,`None` = 当时没记(诚实的三态,⛔ 不是 0) |
| 9 `project.yml` 注释指向不存在的文件 | ✅ 已修 | 改指真守门 `tests/test_v240_p4_release.py` A 组,并写明订正原因 |
| 10 版号写死在守门里 | ✅ 已修 | `_expected_marketing_version()` 从 `app.py::VERSION` 反推,只留一致性断言 |
| 11 只断集合不断个数 | ✅ 已修 | 补 `assert len(versions) == 4`(project 级 2 + app target 2);另在整改守门里再锁一遍 |
| 12 `git diff --check` 漏 `--cached` | ✅ 已修 | 参数化两种都跑;§五 P4.5 ⑦ 同步加了一句 |
| 13 bootstrap 绕过四道闸 | 🟠 **挂账** → §七 **P1-83** | 让 bootstrap 走脚本 = 改它的入口形状(要 `--confirm`、要 dry-run),属报告之外的改动;修法(把四道闸抽成可复用函数,两个入口都调)已写清 |
| 14 `models_text()` 扫描域 | ✅ 已修(判据不同) | 🔴 复审建议的「`Networking/` 根上不许有 `Decodable`」**直译过来是错的** —— `APIClient.swift` 里十几个 `private struct XxxResponse: Decodable` 是**信封类型**,与解它的客户端同处一文件是刻意的。改成「根上的 `.swift` 必须在带理由的名单里」+「`Models/` 每份都要有哨兵」两条 |
| 15 P0.2 双端落点不同 | ⚪ **判定不成立** | 复审自己写了「两处都合规(各自平台恰好一次)」,守门已按平台互斥锁死 —— **没有要改的东西**。如实记一笔,免得下次看见两个行号又起疑 |
| 16 `--db` vs `--db-path` | ✅ 已修(文档) | §五 P4.5 ⑤⑥ 之间加了一句提醒(打错会被 argparse 当场拦下 = 安全,但两条排一起容易顺手抄错) |

### ⚠ 整改自己认下的缺口

1. **🔴-2 的四个分支只实拍到两个**:「有冲突」与「老报告」两支**由守门单测按字面量锁死**,没有实拍
   (任务书要的是「两种核验状态各一张」= 已满足)。
2. **`_POSITION_ALERT_LEVEL` 新增那四条的档位是 builder 按已有同族项定的**(⛔ 不是 K8 或用户给的数)——
   它是**展示强调档**,不是交易判据;前四条 P0.5+ 已发布的**一个字节没动**。理由逐条写在源码注释里。
3. **老章程下 `discipline_labels` 的线名多了一个「线」字**(见 🟡-4 行末)。
4. **DoD 15 的第三处是整改时自己逮到的、不在本报告清单上** —— 已加守门钉住那一处,但 ⚠ **⛔ 别把「三处都修了」读成「全仓已穷举」**:今天的判据仍是「逐句读 + 五句禁词扫描」,不是一条能覆盖全部措辞的机器判据。
