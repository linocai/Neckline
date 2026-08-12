# V2.3.3 独立复审报告(@reviewer,2026-08-12)

> ## ✅ 销项标注(@builder-pro,2026-08-12 整改完毕)
>
> **22 条全部有去处:修了 18 条 · 挂账 4 条 · 判定不成立 0 条。**
> 每条的标注就写在它自己那一段的末尾(「**✅ 已修**」/「**📌 已挂账**」/「**判定不成立**」)。
>
> **整改后读数**:Python **3986 passed / 3 skipped / 0 failed**(**连跑两次**同数,本报告基线
> 3971 → **+15 条新用例**)· 双端 `xcodebuild` **BUILD SUCCEEDED** · iOS Simulator
> **226 tests / 0 failures** · 冒烟 6 个全绿 · **三态实拍 iOS + macOS 各一张**。
>
> **⛔ 整改期同样守住的**:零新阈值(真需要的两处**挂 §七 等用户拍板,没给默认值**)·
> 只修报告点名的问题(⛔ 无 plan 外「优化」)· 不部署 / 不碰服务器 / 不激活 / 不换包 /
> 不 commit / 没动真实 `data/neckline.db`(md5 前后一致 `7ca02c7d…`)。
>
> **新挂 §七 3 条**:**P3-69**(历史回看窗口该多大)· **P3-70**(`rel_to_sector` 与
> `rel_to_index` 同源同值,「市场指数」要不要另定口径)· **P4-71**(竞价层三条已知行为)。

> **审查对象**:工作区未提交的 56 个文件(D1 集合竞价确认层 `neckline/auction/` + 卡 #6 换问题 + 契约 + 双端 + 事后复盘 + 版号)。
> **图纸**:`PROJECT_PLAN.md` §五 V2.3.3(650–1565 行)+ §3.13 + §七。**需求**:`~/Lino/whynotme/K8.md` §二十(574–745 行)。
> **立场**:审计员,未参与开发。**只报不改**,本次未修改任何仓内文件。
>
> **结论**:图纸 ①–⑦ 前半**全部落地,无漏做**;结构性守门(依赖方向 / 零交易动作 / 三道夹逼闸 / 三态契约 / 冻结件 DTO)真实有效。
> 逮到 **🔴 2 条 · 🟡 6 条 · 🔵 12 条**。两条 🔴 都属于本仓反复立过红线的那两类
> (「没判」被讲成「无异常」;定性需求被自行定量),**都在上产前可以低成本修掉**。

---

## 一、验证过、确认没问题的高风险点(这几处不必再查)

| # | 高风险点 | 我怎么验的 | 结论 |
|---|---|---|---|
| 1 | **T1 断链**(`upside_script` 是四件套第 1 件) | 读 `basket_card._upside_path_present()` + 跑 `test_trade_plan_first_piece_accepts_both_v4_and_v3_card_shapes` | ✅ 判据**真的是 OR**(新键优先 / 老 `scripts` 兜底),v4 新卡 / v3 老卡 / 两键全空 三例都有正面用例;`TRADE_PLAN_PIECES[0] == "upside_script"` 字符串未改 |
| 2 | **迟到 LLM 结论回写** | 通读 `pipeline.py` + `store.py`;`llm.explain()` **签名里根本没有 store 句柄**,worker 线程只写内存 `box` | ✅ **结构上写不进去**;`finalize_*` 的 `WHERE llm_stage='pending'` 是第二层;`test_slow_provider_...` 断言迟到调用跑完后两表逐位不变 |
| 3 | **硬截止保护哨兵主循环** | 窗口 `[9:26,9:29)` 左闭右开 + preopen 轮询 30s + `_is_preopen` 到 9:30 | ✅ 最坏阻塞 3 分钟,9:29:30 那一拍窗口已关、9:30 转 intraday;`daemon=True` 裸线程(`ThreadPoolExecutor` 不能设 daemon,这个取舍登记正确) |
| 4 | **夹逼闸按引擎线码判** | 库里 `baskets.engine_code='C'/'Z'/'Y'`、`engine_version='C1'` —— 施工图伪代码写的 `== "Z1"` **照抄就是闸 2/3 永不触发** | ✅ `llm.engine_line_of()` 按**线**判并已如实登记出入;`test_engine_line_is_read_from_the_line_code_not_the_version_string` 正面钉死 |
| 5 | **`verdict_raw != verdict ⇒ `clamped_by` 非空** | 读 `clamp_verdict` 全部返回路径 + 参数化不变量用例 | ✅ 成立。唯一 raw≠verdict 而 clamped_by 为空的情形是「模型压根没给码」(raw=None → `pending_explanation`),那**不是夹逼**,分得对 |
| 6 | **畸形 JSON 绕过闸** | `parse_auction_payload` 对 verdict 做白名单、对 bool 做 `isinstance(bool)`、`_codes` 区分「没给」与「空数组」 | ✅ 绕不过:不认识的码 → `pending_explanation`(⛔ 不猜中性);`null` 不当 `False`;闸 2 的「没给字段」有独立码 |
| 7 | **依赖环 / 竞价层碰交易** | `tests/test_v233_auction_guards.py` AST + SQL 双向扫,我另跑了一遍 | ✅ `sentinel/**`、`selection/**` 零 import auction;`auction/**` 零 import review、零 import 持仓/开仓/交易时钟、零写四张正式结论表 |
| 8 | **`qualified`/`wait`/`cancelled` 泄漏** | 守门按 AST 取非 docstring 字符串常量扫 | ✅ 零出现;三值枚举 == K8 原文 |
| 9 | **新端点鉴权 / 注入 / 三态** | 读端点 + 跑 `test_v233_auction_api.py` | ✅ `Depends(require_token)`(401/403 有用例);`date` 非 8 位数字一律退回今天、全部查询走绑定参数,**无注入面**;端点只 SELECT(三次调用后行数不变有用例);404/500/200 三态分得开、`baskets_covered=0` 是 200 |
| 10 | **客户端 DTO 冻结件纪律** | `test_contract_crosscheck.py` 的 `FROZEN_SNAPSHOT_DTOS` 加了 7 个新类型 | ✅ 7 个全部手写 `init(from:)` + 全字段 `decodeIfPresent`,名字两两不互为前缀;`mapReason` 两个新 case 都在 |
| 11 | **`NKJSON` Bool 排在 Double 之前** | `Models.swift:127-134` | ✅ 顺序正确,本版未动 |
| 12 | **服务端停发 `scripts` 键对已装客户端** | 老 `BasketCard.scripts` 是 `decodeIfPresent` | ✅ 停发安全,不触发「先查是不是硬解码」那条坑 |
| 13 | **选股时钟九项被污染** | `MECH_ITEM_KEYS` 断言恰 9 个 + 键名不变 + 第十项自带 `not_one_of_the_nine` | ✅ 干净;`CLOCK_MECH_SPEC_VERSION` 全仓**只写不读**,老行读回不会炸 |
| 14 | **测试写库泄漏到真实 `data/neckline.db`** | 按 CLAUDE.md A8 姿势写 pytest 插件 patch `sqlite3.connect`,**全量套件**跑一遍记命中 | ✅ **0 命中**(新 7 个 v233 文件单跑也是 0) |
| 15 | **墙钟炸弹 / 间歇红**(§七 P1-36) | 全量套件**独立跑 4 次**(含一次禁随机、一次带探针插件) | ✅ **3971 passed / 3 skipped / 0 failed ×4**,计数完全一致 |
| 16 | **iOS 编译** | `xcodebuild -destination 'platform=iOS Simulator,name=ICTW-v170-iPhone17Pro' build` | ✅ **BUILD SUCCEEDED** |
| 17 | **`sentinel='auction'` 台账行混进看板** | `api/app.py::board` 的 `if not e["ts_code"] and not is_yellow_retreat: continue` | ✅ 空 `ts_code` 天然被挡;有用例 |
| 18 | **周度步 5 撑破 unit 配额** | 读 `step_auction_eval` / `build_auction_section`:一次 `list_closures` 区间查 + 内存分组,**零 LLM** | ✅ 不会。`TimeoutStartSec=3000` / `MemoryMax=800M` 维持是对的(唯一浪费见 🔵-11) |

---

## 二、🔴 致命(上产前必须修)

### 🔴-1 「没判」被折成 `False`「没问题」——`hit_invalidation` / `gap_up_deviation`

**位置**:`[neckline/auction/mech.py:263-272]`

```python
if script is not None:
    stale = member_anchor_stale(script, q)
    reading.anchor_stale = bool(stale)
    if not stale:
        reading.hit_invalidation = judge_low_open_falsify(script, q) is not None
        reading.gap_up_deviation = judge_gap_up_invalidate(script, q) is not None
```

`precall` 的两个 judge 在**三种**情况下返回 `None`:① 真的没命中;② **卡上没有该价位**
(`script.stop_line is None` / `ref_close is None`);③ **`quote.open <= 0`**(行情源还没发开盘价)。
上面这两行把 ②③ 一起折成 `False`。

**这与四处文档直接矛盾**(全都写着 `None = 没判`):
`[neckline/auction/mech.py:91-92]` 字段注释 · `[neckline/api/schemas.py:1621-1623]`(`AuctionMemberRowOut` docstring)·
`[neckline/api/app.py:2160-2162]`(`_shape_auction_member`「⛔ 不许折成 `False`「没问题」」)·
`[client/Neckline/Networking/Models.swift]`(`AuctionMemberRow` docstring)。
**只有 ① 锚失效那一条真的产出了 `None`。**

**实测证据**(我直接跑 `build_member_reading`,四个用例):

| 输入 | `hit_invalidation` | `gap_up_deviation` |
|---|---|---|
| 卡上有 `stop_line` + `ref_close`,价 10.5(没命中) | `False` ✅ 对 | `True` ✅ 对 |
| **卡上没有 `stop_line`**(spec 漏了这只成员) | **`False` ❌ 应为 `None`** | `True` |
| **两个价位都没有** | **`False` ❌** | **`False` ❌** |
| **`quote.open == 0`**(源还没发开盘价) | **`False` ❌** | **`False` ❌** |

**失败场景**:D0 那张卡里某成员因为 `MemberMech.stop_price` 取不到而没进
`invalidation_spec.members`(`load_member_scripts` 仍会给它发一份 `stop_line=None` 的
`MemberScript`),或者 9:26 那一刻新浪/腾讯还没发 `open`(**`precall.judge_*` 里那句
`quote.open <= 0 → None` 就是为这件事写的**)→ 竞价小报告对这只票明确说
「未触发 D0 失效位、未高开偏离」,而真相是**一个字都没核对过**。
客户端 `statusBadges` 对 `false` 和 `nil` 一样不画徽标、也不出 `anchorStale` 那句提示 →
**用户与 LLM 都看不出这一格是空的**;system prompt 里那条「标了『没判』的项照实当作未知」
对这两项**永远不会生效**。

**修复方向**:把「判不判得了」的前置条件搬到调用方,别指望 `judge_*` 的 `None` 承载三种语义:

```python
if not stale:
    can_open = float(getattr(q, "open", 0) or 0) > 0
    reading.hit_invalidation = (
        (judge_low_open_falsify(script, q) is not None)
        if (script.stop_line is not None and can_open) else None)
    reading.gap_up_deviation = (
        (judge_gap_up_invalidate(script, q) is not None)
        if (script.ref_close is not None and can_open) else None)
```
并在 `_mechanical_risks` 照 `RISK_ANCHOR_STALE` 体例补一条「N 只这两项没判(卡上无冻结价位 / 开盘价未发布)」;
客户端逐票行照 `anchorStale` 那句补一句「本票失效位没判」。
**补两条用例**:卡上无 `stop_line` → `None`;`open==0` → 两项都 `None`。

> **✅ 已修**(按建议的方向,并把「原因码」这一层补全)。
> · **机械层**(`auction/mech.py`):前置条件搬到调用方,`judge_*` 的 `None` 只承载「真的没命中」;
>   每个 `None` **必配一个可查原因码** —— 新增 6 个常量 `UNDET_{NO_QUOTE,NO_MEMBER_SCRIPT,
>   ANCHOR_STALE,NO_STOP_LINE,NO_REF_CLOSE,NO_OPEN_PRICE}`(单一源 `auction/__init__.py`)。
>   ⚠ **两项各判各的**:卡上缺 `stop_line` 但 `ref_close` 还在 → 失效位没判、高开偏离照判。
> · **风险条**:新增 `RISK_INVALIDATION_UNDETERMINED`,**与 `anchor_stale` 并列、⛔ 不合并**
>   (那条是「锚失效所以不判」,这条是「判据本身缺」);⚠ 刻意排除两类免得同一件事报两遍:
>   锚失效(上面那条已逐票点名)、这只票压根没抓到(`data_missing` 已计入 + 逐票行本就标
>   「中性｜数据不足」)。
> · **短摘要**:`_undetermined_clause()` 逐票写出「失效位『没判』(原因)…这不是『无异常』」
>   —— system prompt 那条「标了『没判』的项照实当作未知」**此前对这两项永远不生效**,
>   现在有落点了。
> · **契约**:`AuctionMemberRowOut` 加 `hitInvalidationUndeterminedReason` /
>   `gapUpDeviationUndeterminedReason`,端点原样透传(老行缺键 → `None`)。
> · **客户端**:`AuctionMemberRow` 两个新字段 + `hasUndeterminedInvalidation` /
>   `undeterminedNote` + 展示层换算 `nkAuctionUndeterminedReasonLabel`(**未识别 / 缺码 →
>   「原因未记录」,⛔ 仍不说「无异常」**);逐票行画**第三态**:「有项没判」徽标
>   (⚠ 402pt 上只占**一枚**,哪一项 / 为什么写在下面那句里)+ 琥珀色一句原因。
> · **用例 +5**:缺 `stop_line` → `None` · `open==0` → 两项都 `None` + 风险条 + 短摘要都出现
>   「没判」· 有篮无卡 → `no_member_script` · **反向的 `False`**(一切齐全、真没命中 → `False`
>   且原因码为空 —— 没有这一条,前几条可以靠「全都返 None」作弊通过)· 锚失效带原因码。
> · **实拍**:iOS + macOS 各一张,同屏并列三态(判了没命中 / 没判 / 锚失效)。

---

### 🔴-2 本版自己拍了两个数(违反 〇b-1「零新阈值」红线,且都没挂 §七)

用户判据原文:「自己拍的就是 🔴」。全仓扫本版新增数字字面量后,**两处是 builder 自己定的**:

**(a) `[client/Neckline/Views/AuctionCardView.swift:513]`**

```swift
Text("自身历史竞价样本:\(days) 天可用" + (days <= 5 ? " · 样本很少时只看原始值,不做比较结论" : ""))
```

`days <= 5` **就是**「历史竞价样本不足」的门槛 —— 而这正是施工图 ⑨-A **第 1 行**
逐字禁止的那一个:「机械层只出 `history_days_available` 读数,『够不够』交 LLM 判,
**⛔ 不设天数门槛**」。
**失败场景**:`auction_snapshots` 从 2026-08-05 起攒,大约一个月后 `days` 稳定 > 5 →
那句「样本很少时只看原始值」**从界面上永久消失** = 系统在没人拍板的情况下,
用 6 天这个数宣布「历史样本已经够了」。K8 §二十 原文只说「样本不足时只展示原始值」,
从没给过「不足」的线。
**修复方向**:删掉那个条件 —— 要么这句话**恒显**(与 `proxySampleNote` 同族的诚实披露),
要么整句不出;⛔ 别换一个数。

> **✅ 已修**:`days <= 5` 那个条件**直接删掉**,`historyDaysAvailable` 恒显原始读数
> (几天就写几天),够不够交解释者与用户判。函数 docstring 里写明了「⛔ 这里不许有任何
> 天数门槛」以及原先那个 5 的失败场景,免得将来有人"顺手补回来"。
> ⚠ 顺带把 🔴-2(b) 的那句 `historyLookbackNote` 显示在同一处(见下条)。

**(b) `[neckline/auction/mech.py:69]` `_HISTORY_LOOKBACK_DAYS = 30`**

注释辩解「这不是判据阈值,只是往回翻几天 parquet」。但它**直接封顶了**喂给 LLM 的
`history_days_available`:30 自然日 ≈ 20 个交易日,半年后这个读数会永远停在 ~20,
而 LLM 正是拿这个数判「样本够不够」。**它是判据的输入,就是判据的一部分。**
**修复方向**:两条路选一 —— ① 按「全部可用分区」取(`auction_snapshots` 本身就小,
一年也就 200 天 × 数百行),读数变成真实值;② 保留窗口但**挂进 §七 backlog** 并在
产物里把「本次只回看了 30 个自然日」如实下发,让读者知道这个数是被截过的。

> **✅ 已修(路 ②)+ 📌 已挂账 §七 P3-69**。**窗口保留**(它是取数 I/O 边界:全史
> `scan_parquet` 跑在常驻 `neckline.service` 里正是 §七 P0-23 那类隐患 —— 路 ① 要先在 nk 上
> 隔离实测才敢做),但**四件事一起做齐,让它不再是个偷偷的判据**:
> ① 变量名与注释:`_HISTORY_LOOKBACK_DAYS` → **`_HISTORY_LOOKBACK_CALENDAR_DAYS`**,
>    注释开门见山写「这是**取数窗口**,不是判据阈值」+ 指向 P3-69;
> ② 契约**同时下发** `historyDaysAvailable` **与** `historyLookbackDays`(+ `historyLookbackUnit`
>    + 服务端文案单一源 `historyLookbackNote`,⛔ 客户端不许自己写那句);
> ③ 喂 LLM 的短摘要逐字写「回看窗口 N 个**自然日**,不是全史;够不够由你判」;
> ④ 双端界面把那句话显示在天数下面(实拍可见)。
> ⚠ **单位是自然日不是交易日**,如实照写(30 自然日 ≈ 20 个交易日)——⛔ 没有把它说成
> 「20 个交易日」去凑一个好听的数。
> 🔴 **「这个窗口该多大」= 一个 K8 没给的数 → 挂 §七 P3-69 等用户拍板,⛔ 没换一个数。**

**顺带(同族但影响小,归 🔵-3/🔵-4)**:`mech.py:565` 喂 LLM 的缺失代码 `[:20]` 截断、
`AuctionCardView.swift:236` 的 `prefix(12)`、`:285` 的 `prefix(24)`(锚点列表**静默截断且不写"还有 N 个"**)。

---

## 三、🟡 重要

### 🟡-1 `anchors_note` 让模型写了、解析了,然后整条丢掉 —— 契约字段永远是 `nil`

**位置**:`[neckline/auction/llm.py:270]`(解析)· `[neckline/auction/pipeline.py:276-317]`(`_finalize_with_llm` 里**没有任何地方用 `out.anchors_note`**)·
`[neckline/db.py:1437-1478]`(`auction_reports` **没有这一列**)· `[neckline/api/app.py:2258-2268]`(构造 `AuctionMarketOverviewOut` 时**没传 `anchorsNote`**)。

契约与客户端都留着这个字段(`[neckline/api/schemas.py:1601]` / `AuctionMarketOverview.anchorsNote`),
界面上还专门为它写了一个 `if let n = ... { Text(n) }` 分支(`[AuctionCardView.swift:289-292]`)。

**失败场景**:施工图 ③-B 把 `market.anchors_note` 列为 LLM **必须**输出的键,prompt 里也逐字要求了;
每次调用都白花 token 生成它,然后在编排层被丢弃 → 界面那个分支**永远不执行**,
用户永远看不到「这批竞价强势股怎么看」的那句话,而**没有任何东西会报错**。

**修复方向**:两条路选一 —— ① 给 `auction_reports` 加一列 `anchors_note`(它属 LLM 段白名单)
并在 `finalize_report` / 端点接上;② 若决定不做,就**同时**从 prompt、`AuctionLLMResult`、
`schemas.AuctionMarketOverviewOut`、客户端 DTO 与视图分支里一起删掉,并挂 §七。
⛔ 别留现在这种「三处都在、中间断了一节」的状态。

> **✅ 已修(选路 ①:落库 + 下发)**。`auction_reports` 加一列 `anchors_note`(DDL 带整段
> 注释说明它属 LLM 段)+ 进 `LLM_UPDATABLE_REPORT_COLUMNS` + `finalize_report(anchors_note=…)`
> 用 `COALESCE(?, anchors_note)` 静态字面量写(⛔ 不动态拼,否则 🟡-3 那条闸门当场失明)+
> `pipeline._finalize_with_llm` 传 `out.anchors_note` + 端点 `anchorsNote=row.get(...)`。
> **选 ① 而不是 ② 的理由**(按"哪个信息更有用"定):
> · 它是**施工图 ③-B 契约里 LLM 必须输出的键**,prompt 逐字要求了 —— 删它等于把 K8 §二十
>   「市场锚点只解释资金方向、不取得交易资格」那句解释从产物里拿掉,而那正是**用户唯一
>   能读到的、关于这批高开票该怎么看**的一句话;
> · 界面分支、契约字段、客户端 DTO **三处已经都在**,选 ② 要动 5 处删干净,反而更大;
> · **两张表从未上产** → 加一列就是一次 DDL 直改,**零迁移成本**(上产后再加就要走迁移了,
>   这一点已写进 §四 回滚绳)。
> 实拍已见:第 2 块「市场与主线概览」里那句锚点解释真的出现了。

### 🟡-2 `captured_at` 存的是**轮询那一拍的时刻**,不是**真正拉价的时刻** —— 而 `data_quality` 的「在不在窗内」就靠它

**位置**:`[neckline/api/app.py:294]`(`now = datetime.now()`,循环顶部取一次)→
`[neckline/api/app.py:339-344]`(把这个 `now` 传给 `run_auction_pipeline`)→
`[neckline/auction/collect.py:277]`(`captured_at=now`)→
`[neckline/auction/collect.py:131-134]`(`captured_in_window`)→ `[collect.py:159]`(`ok` 的必要条件)。

`now` 是在 **`run_precall_tick` 与 `run_auction_capture` 之前**取的;这两者各自要对整个关注池
(持仓 + T1/T2 成员 + 指数 + 昨日涨停,上限 200)批量拉一次价、还带主源失败降备源的重试。

**失败场景**:某个早晨新浪超时、precall + capture 合计耗时 3 分半 →
循环 09:26:00 进分支,竞价层**实际在 09:30 之后**才拉价、拿到的是**开盘后的价格**;
可库里写下的是 `captured_at=09:26:00`、`captured_in_window=True` → 市场级 `data_quality='ok'`
→ **闸 1 不夹逼**,一份「9:26 冻结」的报告堂而皇之地用开盘后价格下 `confirm`。
这正是 K8 §二十「不持续观察 9:30 以后的价格」与 〇b-4「事后不许补跑」要防的事,
而现在**没有任何检测**(`remaining` 用真实墙钟算,只会让 LLM 被跳过,机械报告照样落库、照样 200)。

**修复方向**:`collect_auction_snapshot` 加 `now_fn`(默认 `datetime.now`),
**在 `fetch(requested)` 返回之后**取时刻当 `captured_at`;并在拉价前再判一次
`AUCTION_WINDOW_START <= t < AUCTION_WINDOW_END`,越窗就 `skipped_reason` + 零落库
(与 〇b-4 同一条纪律)。

> **✅ 已修(两层都做了)**。
> · `collect_auction_snapshot(…, now_fn=None)`,缺省 `datetime.now`(**真实时钟**);
>   `pipeline` 把它自己那个 `clock`(硬截止余量用的同一个)一路传下去 —— 生产路径因此
>   **恒是真实时钟**,回放 / 单测显式注入(同 `precall.run_precall_tick(now=…)` 体例)。
> · **第一层(拉价前)**:`fetch_started_at = clock()`;不在 `[9:26, 9:29)` → **一条价都不拉**,
>   `fetch_skipped_reason='window_closed_before_fetch'` → 编排层 **零落库、零 LLM、
>   连「当日已跑」标记都不落**(今天压根没跑成,下一拍若还在窗口内应当能干净重跑)。
>   🔴 **新码与 `not_auction_window` 刻意分开**:那条是「排程就没进窗口」,这条是「慢了」——
>   混成一个码,部署次日查 journal 分不出是哪种,而处置完全不同。
> · **第二层(拉价后)**:`captured_at = clock()`(**真正拉完价那一刻**);跨过 9:29 →
>   报告**照落**(机械事实与失效警报不能丢)但 `captured_in_window=False` → `data_quality`
>   降级 → **闸 1 夹得住**。另落 `fetch_elapsed_sec`(拉价耗时可查,⛔ 不只留一个点)。
> · **用例 +3**:`captured_at` 是拉完那一刻而非那一拍(含 `fetch_elapsed_sec==130.0` 与
>   `DQ_DEGRADED`)· 正常一拍仍 `DQ_OK` · 窗口已关 → `quotes_fn` **一次都没被调用** +
>   两表零行 + dedup 标记未落。
> ⚠ 顺带订正了一条老用例的前提:`test_deadline_already_past_skips_the_call_entirely` 原先拿
> **9:29:00** 当时钟,而那一刻按新规则属于「窗口已关」(另一条路径)—— 改成 9:28:55 + 注入
> 更早的 `deadline`,它要钉的「余量 ≤ 0 → 压根不发起调用」这件事逐字未变。

### 🟡-3 「机械列永不 UPDATE」这条**代偿闸门**有两个可以走过去的洞

**位置**:`[tests/test_v233_auction_guards.py:175-227]`(`_sql_literals` / `_flatten_str`)。

`auction_reports` / `auction_verdicts` **刻意不进** `_APPEND_ONLY_TABLES`,
`db.py:1427-1430` 与 `store.py:12-14` 都写着「代偿闸门就是这条列白名单守门 —— 缺了它这一步就是个后门」。
我拿六个构造用例喂了守门的解析器:

| 写法 | 守门反应 |
|---|---|
| `conn.execute("UPDATE auction_reports SET data_quality=? WHERE …")` | ✅ 逮到,报红 |
| `conn.execute(f"UPDATE auction_verdicts SET {sets} WHERE …")` | ✅ 逮到(解析不出列 → `assert cols` 红) |
| `cur = conn.cursor(); cur.execute("UPDATE auction_reports SET captured_at=? …")` | ✅ 逮到 |
| **`sql = "UPDATE auction_reports SET " + ", ".join(...); conn.execute(sql, args)`** | ❌ **完全失明**(`_sql_literals` 返回 `[]`) |
| **`stmt = "UPDATE auction_verdicts SET members_json=? …"; conn.executemany(stmt, rows)`** | ❌ **完全失明** |

另外两点:① 守门**只扫 `neckline/**`** —— `scripts/` 下任何脚本改机械列都不会红;
② **`DELETE FROM auction_reports` 没有任何守门**(这两张表不在 append-only 名单里,
而新守门只管 UPDATE 的列集合)。

**失败场景**:半年后有人写一个「修数据」脚本或一个批量回填函数,把 SQL 先赋给变量再 `execute`,
或者写在 `scripts/oneoff/` 里 —— 机械段那份 9:26 的冻结事实被就地改写 / 删除,
**全量套件依然全绿**,而这两张表是竞价复盘唯一的原始证据。

**修复方向**:① `_sql_literals` 增加一条「模块内 `Assign` 到 `Name` 的字符串常量」的回溯,
或更简单 —— 对**整份文件文本**做一次 `UPDATE\s+auction_(reports|verdicts)` 的正则预扫,
命中行数与 AST 找到的语句数不相等就报红("有 UPDATE 但我解析不到 = 失明,⛔ 不许");
② 扫描域从 `neckline/**` 扩到 `scripts/**`;③ 补一条 `DELETE FROM auction_(reports|verdicts)` 全仓禁令。

> **✅ 已修(取并集,四条一起上)**。
> ① **源头最严的一条**(新用例 `test_the_store_module_only_ever_executes_static_sql_literals`):
>    `neckline/auction/store.py` 里**任何非字面量 `execute*()` 第一实参直接红** —— 它是两张表的
>    读写唯一通道,与其事后靠正则去追,不如在源头要求这个模块一条动态 SQL 都不许有;
> ② **失明自检**(按建议的正则预扫):对**整份文件文本**(已剥 docstring 与 `#` 注释)数
>    `UPDATE auction_*` 的出现次数,与 AST 取到的静态 SQL 实参数比对 ——
>    **文本里有、AST 里没有 = 我解析不到 = 失明 → 红**;
> ③ 扫描域 `neckline/**` → **`neckline/**` + `scripts/**`**(`_SQL_SCAN_ROOTS`);
> ④ 新增 `test_nothing_anywhere_deletes_from_the_two_auction_tables`(两张表各一条参数化)。
> **实测**:拿报告里那 6 个写法逐个注入真文件跑守门 —— **6/6 全部报红**
> (变量 SQL + `execute` / 变量 SQL + `executemany` / `DELETE` / 直接改机械列 / f-string 动态
> `SET` / `scripts/oneoff/` 下的修数据脚本)。探针脚本跑在 scratchpad、**不入仓**,
> 跑完 `git status` 与守门复跑均确认**零残留**。

### 🟡-4 周度四维交叉表**丢掉了骨架版本** —— 升 `K8-V0.7` 的那条理由没有兑现

**位置**:`[neckline/eval/auction_eval.py:98-109]`

```python
_skeleton, engine_code, engine_version, _ruleset = stratum_of(closure)
return (regime, f"T{tier}", engine_code, engine_version)
```

施工图 ⑦-2 给「什么参数都没改为什么还要升骨架版本」的唯一理由是:
> `pack_version` 是**归因分层键**(`eval/iteration.stratum_of()` 第一位)——
> 竞价层上线**前后**的选股时钟样本必须分得开,否则**周度按版本归因会把两个不同的系统混成一层**。

而竞价段自己的 `cell_key_of` 恰恰把 `stratum_of()` 的第一位丢掉了。

**失败场景**:上产两周后跑周报 —— `K8-V0.6` 时代(没有竞价层)与 `K8-V0.7` 时代
(有竞价层)的同一 `(行情状态, T1, C, C1)` 样本落进**同一个单元格**、共用一个 `n`、
共用一条 30/80 样本量闸。`withoutAuctionItem` 只是一个**全局**计数,不进单元格,
分不开哪一层被稀释了。届时给出的「保留 / 降权 / 淘汰」建议,分母是两个系统的混合。

**修复方向**:`cellKey` 加一维 `skeletonVersion`(K8 §二十「引擎和**版本**」本来就可以这么读),
或把 `engineVersion` 换成 `f"{skeleton}/{engine_version}"`;`byCell` 的表头与
`render_auction_section` 的表格同步加一列。⛔ 别只在 `overall` 里补。

> **✅ 已修(加一维,不是拼串)**。`cell_key_of()` 由四元组改**五元组**
> `(regime, tier, skeletonVersion, engineCode, engineVersion)` —— 骨架维直接取
> `stratum_of()` 的**第一位**(老样本落 `LEGACY` 的退回逻辑因此与迭代段仍逐字一致);
> `cellKey` 声明、`byCell` 的 dict、`render_auction_section` 的 markdown 表头与每一行**同步加一列**。
> **选"加一维"而不是"拼成 `骨架/版本`"**:拼串会让 `engineVersion` 这一列的语义悄悄变成
> 两个量,读表的人和后续代码都得去拆字符串。
> **用例 +2**:`cellKey` 五维断言 · **两个骨架版本 ⛔ 不许落进同一格**(同一
> `(trend_continuation, T1, Z, Z1)` 样本,`K8-V0.6` 与 `K8-V0.7` 必须是两格、各自 `n=1`,
> 且 markdown 表里两个版本都看得见 —— 正面钉死复审给的那个失败场景)。
> 顺带修了 🔵-13(等级维哨兵借用了引擎维的 `LEGACY` → 改 `TIER_UNKNOWN = "T?"`)。

### 🟡-5 `upside_path_present` 这个键名会说谎(而且冻进了 B 类快照)

**位置**:`[neckline/review/basket_review.py:339-354]`

```python
upside = str((card or {}).get("upside_path") or "").strip() ...
text = upside or ((scripts.get(branch) or None) if branch else None)
...
"script_present": bool(text),
"upside_path_present": bool(text),      # ← 与上一行同值
```

对一张 **v3 老卡**(只有 `scripts` 三格、没有 `upside_path`),只要当天的档位上有原文,
`upside_path_present` 就是 `True`。

**失败场景**:`basket_review_daily.mech_json` 是**写入当时冻住**的 B 类快照;
将来做「V2.3.3 前后有多少篮子真的有预期上涨路径」这类归因时,按这个键统计会把
老卡的三剧本一起算进「有 upside_path」,**而快照不可回改**。
`[neckline/report/render.py:786]` 的 markdown 也已经把它当同一件事讲。

**修复方向**:两个键讲两件事 —— `script_present`(有没有取到原文,保留现语义)
与 `upside_path_present = bool(upside)`(**只看新键**);或者干脆把新键改名
`plan_text_present`,别用一个具体键名冒充。改之前先确认 `mech_json` 的消费方
(客户端复盘 DTO / `render.py`)对多一个键是容错的(它们是 `decodeIfPresent`,是容错的)。

> **✅ 已修(选"两个键讲两件事")**。`script_present = bool(text)`(取到原文没有,**现语义
> 保留** —— `render.py:786` 读的就是它,不动)· `upside_path_present = bool(upside)`
> (**只看新键**)· 另加 `script_text_source`(`upside_path` / `legacy_scripts` / `None`),
> 归因时不必再靠两个布尔互相推断。
> **消费方已核**:客户端复盘 DTO 是手写 `init(from:)` + `decodeIfPresent`(多一个键容错);
> 全仓 grep 确认只有 `render.py::_mech_item_summary` 一处读这两个键,已改成读
> `script_present` 并在 `script_text_source == "legacy_scripts"` 时补一句「(v3 老卡的三剧本)」。
> **用例 +1(改既有那条)**:v3 老卡落 `flat` 档取到原文 → `script_present is True` **但**
> `upside_path_present is False` + `script_text_source == "legacy_scripts"`。

### 🟡-6 `rel_to_index` 用了三支指数的**算术平均**,这是一个图纸没给的新口径

**位置**:`[neckline/auction/mech.py:257-261]`

```python
market_gaps = [g for g in (snap.gap_of(c) for c in MARKET_INDEX_CODES) if g is not None]
reading.rel_to_index = reading.gap_pct - (sum(market_gaps) / len(market_gaps))
```

施工图 ②-D 那张表写的是「`该票 gap_pct − 市场指数 gap_pct`」(单数),
K8 §二十 说的是「看上证、深证、创业板竞价涨跌,再看候选相对强弱」。
把三支指数**等权平均**成一个合成指数,是本版发明的一个统计口径
(上证综指与创业板指的波动幅度差一档,等权平均没有依据)。

**失败场景**:创业板早盘 +1.8%、上证 +0.1%、深成 +0.4% 的分化日,
一只**主板**票 +0.6% 的 `rel_to_index` 被算成 `0.6% − 0.77% = −0.17%`(看起来跑输),
而它其实跑赢了自己的市场基准。这个读数直接进 prompt 与界面,
LLM 的「候选是否掉队」判断就建立在这个合成数上。

**修复方向**:① 逐指数出三个差值(`rel_to_index_by_code: {code: diff}`,零聚合、最诚实),
或 ② 用该票**自己所属板块**的市场指数(`benchmark_of` 已经算出来了)。
无论哪条,都别留一个没人拍板的等权平均。

> **✅ 已修(选路 ②,照图纸的"单数")+ 📌 一处如实登记 → §七 P3-70**。
> `rel_to_index` 改成 `gap_pct − snap.gap_of(benchmark_of[code])`(唯一源
> `universe.BOARD_BENCHMARK_INDEX`,主板按交易所落到上证 / 深证)。
> **为什么不保留等权**:施工图 ②-D 写的是**单数**,而等权是本版发明的口径且在分化日会
> **说谎**(那个 `+0.6% → −0.17%` 的例子);「照图纸的单数可辩护、自己发明的合成指数不可」。
> ⚠ **一处必须如实说**:现行映射下每只票**只有一支对照指数** → `rel_to_index` 与
> `rel_to_sector` **同源同值**。这是数据现实(系统里没有第二支指数可减),不是抄错;
> 已在代码注释里写明,并**补强可追溯性**:篮级 `rel_strength.per_member[].benchmark_code`
> 落下「减的是哪一支」(走 `relStrength` 的 NKJSON 透传,**零新客户端契约面**)。
> 🔴 **「市场指数要不要另定一个与板块基准不同的口径」= 一个图纸没给的规则 →
> 挂 §七 P3-70 等用户拍板,⛔ 工程侧不自己定。**
> **用例 +2**:分化日主板票的 `rel_to_index` 必须 **> 0**(等权会算成负的)· 创业板票的
> `benchmark_code == "399006.SZ"`。

---

## 四、🔵 建议

1. **`_deadline_passed` 标志位不存在**,却被三处文档当成「双保险」的第一层写着:
   `[neckline/auction/pipeline.py:29]` · `[neckline/auction/store.py:20]` · `[tests/test_v233_auction_guards.py:232]`。
   实际机制是「主线程不等 + `WHERE llm_stage='pending'` + LLM 层拿不到 store 句柄」(CLAUDE.md 那条写对了)。
   **改文档,别加标志位** —— 现有设计更强。
2. **`tests/test_v233_auction_store.py` 这个文件不存在**,却被 `[neckline/db.py:1421-1422]` 与
   `[neckline/auction/store.py:9-10]` 当成守门出处引用。守门实际在 `tests/test_v233_auction_guards.py`。
3. **喂 LLM 的缺失代码列表截断 `[:20]`**(`[neckline/auction/mech.py:565-566]`)——
   有「…」提示,可接受;但那个 20 也是本版自己定的,顺手记一笔。
4. **客户端两处静默截断**:`[AuctionCardView.swift:236]` 缺失代码 `prefix(12)`(有"等 N 个",✅)、
   `[AuctionCardView.swift:285]` 锚点 `prefix(24)`(**没有"还有 N 个"**)。后者建议补一句。
5. **`Text("a" + "b")` 还剩两处**:`[AuctionCardView.swift:236]` 与 `[:513]`。
   这两处正文里没有 `**`,所以新立的守门(只逮带 Markdown 的)不会红,也没有可见 bug ——
   但同一批刚刚把 `NKMemberCard` 的同款写法改掉并立了规矩,体例不一致。
6. **`Text(三元表达式)` 里带 Markdown**:`[AuctionCardView.swift:243-245]` 的
   「这一项**结构性恒空**」。两个分支都是字面量,Swift **应当**推断成 `LocalizedStringKey`(能加粗),
   但这正是 CLAUDE.md 那条坑的灰色地带 —— **出图时顺手确认这四个字是粗体**,不是四个星号。
7. **历史报告重渲染会把老卡的三剧本讲成「未生成」**:`[neckline/report/render.py:614-622]` +
   `[neckline/report/basket_daily.py:129-130]` 已不再映射 `scripts` → 回放 V2.3.3 之前的某天,
   markdown 会写「预期上涨路径:未生成(原因未记录)」,而那天其实是**生成了的、只是形状不同**。
   建议在那句 `未生成` 的分支里加一句「(v3 老卡的三剧本不再进本节)」。
8. **`packs/K8-skeleton.json` 的 `manifest.date` 没改**(仍是 `2026-08-11`),
   而施工图 ⑦-2 明写「`manifest.pack_version` + **`manifest.date`** + `notes` 追加一条」。
   `notes` 追加了、版本改了,日期漏了。激活前顺手改。
9. **D0 零篮子的早晨仍然会打一次 LLM**(`[neckline/auction/pipeline.py:205-247]`):
   `known_keys=[]`,模型给的所有篮子条目都会被丢弃,只留市场段 `overview`。
   不算 bug(市场段有价值),但值得知道「没有篮子的日子也花一次调用」。
10. **窗口内的中途异常会导致重复 LLM 调用**:`_record_tick` 在 `finalize` **之后**才落
    (`[pipeline.py:248]`)—— 这是图纸 ④-A 有意的「干净重跑」。代价是:若 `finalize_verdict`
    连续抛异常(如 DB 忙),9:26–9:29 的 6 拍最多能打 6 次 LLM,而每拍新建一本 `BudgetLedger`
    所以预算账拦不住。部署次日查 journal 时留意一下。
11. **`build_auction_section` 在一次周度作业里跑了两遍**(`calibration.build_report` 里一次、
    `scripts/weekly.py` 步 5 一次),`[scripts/weekly.py:203-210]` 的 docstring 已如实承认。
    纯读、量级小,但两次 `list_closures` 全区间查是白花的。
12. **`load_history` 逐篮各扫一次 parquet**(`[neckline/auction/mech.py:489]`)——
    N 个篮子 = N 次 `scan_table_range("auction_snapshots", …)`,同一个区间。
    量级很小(该表一天几百行),但这是**跑在常驻 `neckline.service` 里**的路径(P0-23 语境),
    合并成一次扫描 + 按 code 分组更稳妥。
13. **`cell_key_of` 用 `LEGACY_ENGINE` 当「等级缺失」的哨兵**(`[auction_eval.py:108]`)——
    引擎维的哨兵串跑到了等级维,读表的人会困惑。建议另给一个 `"T?"`。
14. **`test_slow_provider_...` 用真 sleep + `< 1.0s` 断言**(`[tests/test_v233_auction_pipeline.py:179]`)——
    余量 0.8s,并行/负载高时理论上可能抖。我 4 次全量跑都没红,先记着。

---

> ## 🔵 逐条销项(修 10 · 挂账 3 · 判定不成立 0)
>
> | # | 处置 | 怎么做的 |
> |---|---|---|
> | 1 | **✅ 已修(改文档)** | 三处文档里的 `_deadline_passed` 标志位**并不存在**,而现有机制**更强**:`llm.explain()` 的签名里根本没有 store 句柄 → 迟到结论**够不着**库,不是"来得及拦住"。三处(`pipeline.py` / `store.py` / 守门 docstring)改成如实描述,并各写一句 **⛔ 别为了"对齐施工图字面"去补那个布尔** |
> | 2 | **✅ 已修** | `db.py` 与 `store.py` 里指向不存在的 `tests/test_v233_auction_store.py` → 改指 `tests/test_v233_auction_guards.py`,并顺带写明它现在还管**非字面量 SQL** 与 `DELETE`、扫描域含 `scripts/**` |
> | 3 | **✅ 已修(去掉截断,零发明数字)** | 喂 LLM 的缺失代码 `[:20]` **整条去掉** —— 理由与 ⑨-A 第 5 行对竞价强势股的既定理由同款:截断需要一个 K8 没给的数,而「模型看到的就是系统看到的全部」更诚实;量级上限就是抓取清单本身(`DEFAULT_BREADTH_CAP`),不会失控 |
> | 4 | **✅ 已修** | 锚点 `prefix(24)` 补一句「以上是全部 N 只里的前 M 只(按竞价涨幅降序)」;那个 24 提成命名常量 `_anchorChipCap` 并注明**是纯展示层排版上限、不是判据**(喂 LLM 的那份不截断) |
> | 5 | **✅ 已修** | 两处 `Text("a" + "b")` 拼成**一整条插值字面量**(新增小工具 `nkJoinCapped`,返回值供插值);另一处随 🔴-2(a) 重写时一并消失 |
> | 6 | **✅ 已修(消灭灰色地带,不靠"出图时确认")** | `Text(三元表达式)` 里带 `**加粗**` 的那处**拆成 if/else 两个独立 `Text` 字面量** —— 与其出图时肉眼确认一次,不如让 Swift 的类型推断**没有第二种可能** |
> | 7 | **✅ 已修** | 「预期上涨路径:未生成」那一支加一句「(这是 V2.3.3 之前的 `<specVersion>` 老卡:那一版问的是三剧本…)」。⚠ 判据取卡自己的 **`specVersion`**,**不看已停发的 `scripts` 键**(它自 V2.3.3 起就不在 `card_to_public_dict` 的映射里了)、也**不猜日期** |
> | 8 | **✅ 已修** | `packs/K8-skeleton.json` 的 `manifest.date` → `2026-08-12`(`pack_version` / `notes` / `config` 段**一个字没动**) |
> | 9 | **📌 已挂账 §七 P4-71(a)** | 「D0 零篮子的早晨也打一次 LLM」—— 市场段(指数环境 + 锚点)本身有价值,**不是浪费**,但值得知道;部署次日查 journal 时留意 |
> | 10 | **📌 已挂账 §七 P4-71(b)** | 「中途异常最多 6 次调用」是图纸 ④-A 有意的「干净重跑」;天花板仍是 9:29 硬截止(最多 6 次、都在 3 分钟内)。⛔ **不把标记提到 `finalize` 之前** —— 那会让中途异常变成「今天再也不跑了」,代价更大 |
> | 11 | **📌 已挂账 §七 P4-71(c)** | `build_auction_section` 跑两遍。**不合并的理由**:步 5 单独跑的意义就是有**独立的退出码信号**与一句可核的 journal 行,让它改读步 2 的产物 = 步 2 一失败步 5 连"没跑成"都报不出来;而这一段是纯读 SQLite + 内存分组,量级几百行。真要动,先想清楚那个独立信号怎么保 |
> | 12 | **✅ 已修** | `load_history` 逐篮各扫一次 → 拆成 `scan_history_index()`(**一次扫描服务全部篮子**)+ `history_of()`(按 code 切片,`history_days_available` 仍按**本篮**的可得日期数算,读数逐位不变)。`load_history()` 保留为单篮入口。**用例 +1**:三个篮子 → `scan_history_index` **只被调用 1 次** |
> | 13 | **✅ 已修** | 等级维哨兵由 `LEGACY_ENGINE` 改 **`TIER_UNKNOWN = "T?"`**(引擎维的串跑到等级维,读表的人只会以为那一格在讲引擎)。**用例 +1** 正面钉死 `!= "LEGACY"` |
> | 14 | **✅ 已修** | 断言由 `< 1.0`(硬编,余量 0.8s)改成 `< slow.sleep_sec`(假 provider 新增只读属性)—— 判据回到它真正要钉的那件事:**没等那 1.5s 睡完**,而不是墙钟精度 |

---

## 五、图纸完整性对照(①–⑦ 逐条)

| 批 | 图纸要求 | 落地 | 判定 |
|---|---|---|---|
| ① | 卡 #6 换问题 + 8 处 `basket_card.py` 改动 + 6 个消费方 + 双端 | 全部落地;`CARD_SPEC_VERSION`→v4、`VERIFY/INVALIDATE_SPEC_VERSION` 逐字未动、`TRADE_PLAN_PIECES` 未动、老卡 OR 有正反用例 | ✅ 无漏做;🟡-5 是键名问题 |
| ② | 新包 5 模块 + 两张表 + 输入五组 + 机械读数逐项 + `data_quality` 三态 + `members_json` 键表 | 全部落地;`gap_pct_of` 与 `capture.py` 有逐位对拍用例;三支指数显式并入、`_related_index_codes` 未动 | ✅;🔴-1 在这里 |
| ③ | `TASK_AUCTION` 进两个元组 + 输出契约 + 三道闸 + 小纸条常量 | 全部落地;`ALL_TASKS==9`、`use_streaming`/`read_timeout` 同路有正面断言;`provider.chat` 调用点恰 1 个有 AST 守门 | ✅;🟡-1 是 `anchors_note` |
| ④ | 窗口 / 防重 / 硬截止 / `_sentinel_loop` 一个 elif / `KIND_PRECALL` 推送 / `should_push` 三条 | 全部落地;`_SENTINEL_PREOPEN_POLL_SEC` 未改、现有两条旁路未动、零新 unit(守门断言 10 个) | ✅;🟡-2 在这里 |
| ⑤ | 端点三态 + 契约 DTO + 客户端竞价卡 + 五个换算函数 + QA 钩子 | 全部落地,**多做**了第六个换算函数 `nkAuctionLlmStageLabel` 与 `volumeNote` 字段(都是补强,不是越界) | ✅ |
| ⑥ | 第十项 `auction_review` + 六标签 + 三分因 + 周度四维聚合 + 步 5 + `--skip-auction-eval` | 全部落地;九项键名未动、`tier_accuracy` 复用、零新 LLM(有 AST 守门) | ✅;🟡-4 是四维少一维 |
| ⑦ 前半 | 版号三处同升 + 骨架包出文件不激活 | `app.py::VERSION` / `project.yml`(顶层 + target)/ pbxproj **四处**全 2.3.3;`config` 段逐字节未变;未激活 | ✅;🔵-8 `manifest.date` 漏改 |
| ⑦ 后半 | 部署 / 配额实测 / 实拍 / 换包 | **本次不在审查范围**(未部署) | — |

**多做的**:`nkAuctionLlmStageLabel`(第 6 个换算函数)· `AuctionMemberRowOut.volumeNote`(②-F 键表里没有)·
`AuctionRunResult.deadline_hit`。三者都是补强,**不构成越界**。
**明确不动清单**(六关 / `tier.py` / `gates.py` / OUT 全链 / `verification_rules.py` / 章程 /
引擎包三份 / `precall.py` 四类判定 / `capture.py` / systemd unit)—— 我逐个 `git diff` 确认过:**一行未动**。

**builder 自报的偏离,属实且登记正确**:
① `ThreadPoolExecutor` → 裸 `threading.Thread`(`concurrent.futures` 退出时会 join 工作线程,与「进程退出不阻塞」相反)——**属实,取舍对**;
② 施工图 ③-C 的 `engine_code == "Z1"` → `engine_line_of()` 按线码判 ——**属实,而且是本版最关键的一次纠错**;
③ macOS 竞价卡摆在「今日概览」标题之后而不是之前 ——**属实,理由成立**。
**没报的偏离**:🟡-1(`anchors_note` 未落地)· 🟡-4(四维少骨架版本)· 🟡-6(`rel_to_index` 等权平均)· 🔵-8(`manifest.date`)。

---

## 六、建议主会话再复审一遍的高危区

1. **`GET /api/v1/auction`(新端点 + 鉴权)** —— 我已验鉴权/注入/三态/只读,但这是本版唯一新增的对外面。
2. **`_sentinel_loop` 里那条新执行路径** —— 它跑在**常驻 `neckline.service`**、与盘中哨兵同进程,
   且是本项目第一次在这个进程里发起 LLM 调用。🟡-2 就在这条路径上。
3. **`scripts/weekly.py` 步 5 + `packs/K8-skeleton.json` 发版**(发版脚本类)——
   激活 `K8-V0.7` 要走四道闸,🔵-8 的 `manifest.date` 建议在演练 diff 时一起看。
4. **两张新表的写路径**(数据迁移类)—— 🟡-3 那条代偿闸门的两个洞,建议取并集。

---

## 七、修复优先级(我的建议顺序)

1. **🔴-1**(`hit_invalidation` 三态)—— 改动最小、错得最深,而且**上产后写进库的行不可回改**。
2. **🔴-2(a)**(客户端 `days <= 5`)—— 删一个条件的事。
3. **🟡-2**(`captured_at` 取真正拉价时刻 + 拉价前复判窗口)—— 十几行,防的是「一份假装 9:26 的报告」。
4. 其余按 🟡-1 → 🟡-3 → 🟡-4 → 🟡-5 → 🟡-6 → 🔴-2(b) 收。

> **复审方式说明**:全量 `pytest tests/ -q` 独立跑 **4 次**(其中一次 `-p no:randomly`、一次挂
> `sqlite3.connect` 探针插件),均 **3971 passed / 3 skipped / 0 failed**;
> `xcodebuild` iOS Simulator **BUILD SUCCEEDED**;
> 另用三段一次性探针脚本(不入仓)分别验证了「UPDATE 守门的失明面」「`hit_invalidation` 的四种输入」
> 「真实 `data/neckline.db` 是否被测试打开」。**本次审查未修改仓内任何文件、未部署、未碰服务器、未 commit。**

---
---

# 附:P3-69 / P3-70 裁定落地 · 定向复审(@reviewer,2026-08-12 第二轮)

> **审查对象**:只审 2026-08-12 用户对 §七 **P3-69 / P3-70** 两条挂账的裁定落地那一棒
> (今日 05:00 后改动的 12 个文件:`neckline/auction/{__init__,collect,mech,llm}.py` ·
> `neckline/api/{schemas,app}.py` · `client/Neckline/{Networking/Models.swift,Views/AuctionCardView.swift}` ·
> `client/NecklineTests/DTODecodeTests.swift` · `tests/test_v233_auction_{mech,api}.py` ·
> `scripts/smoke_auction.py`)。⛔ **未重审 V2.3.3 全版**(上一轮已审并整改完毕)。
>
> **判据**:用户裁定原文(20 / 60 / 15 / 3 四个数 + 五条 `rel_to_index` 分支 + 四条 `rel_to_sector` 路径)。
>
> **结论**:**四个数逐字照做、没有第五个数**;`rel_to_index` 五条分支我逐条实测正确;
> 「禁止市场指数代替板块基准」是**真的结构性保证**(我把指数码硬塞进取样域也被挡住);
> 「当日不进基线」我造了含当日分区的库实测,三重过滤都在,回放路径也验了。
> 逮到 **🔴 1 条 · 🟡 3 条 · 🔵 9 条**。唯一那条 🔴 **现在打不着**(生产 `auction_snapshots`
> 只有 ~5 天分区),但它会在**约 2026-08-26**(样本攒到 15 天那天)自己醒过来。

## A、验证过、确认没问题的高风险点(不必再查)

1. **四个数逐字 = 裁定值,全仓无第五个数**。AST 扫 `neckline/auction/**` 的全部数字字面量:
   除 `20`(mech.py:119)/`60`(:123)/`15`(:129)/`3`(`__init__.py`:110)与 `_EPS=1e-9`、
   窗口时刻 `9,26`/`9,29`、`1000`(毫秒换算)、0/1 索引外**一个新数都没有**;
   客户端同扫,零硬编阈值(上一轮删掉的 `days <= 5` 没有复活)。
2. **窗口口径真的是交易日,60 是上界不是计数**。拿**真实 `trade_cal`**(副本,coverage
   2015-01-01→2026-12-31)遍历 2025–2026 全部交易日实跑 `history_window_days()`:
   **每一天都恰好 20 个交易日**,回溯跨度 28–38 自然日(春节 32 天 / 国庆 38 天),
   `window[0] >= D1-60` 恒成立,60 那条上界**从未真正夹住过**(= 它就是"补齐上界"该有的样子)。
   窗口是 `trade_date` + `trade_cal` 的纯函数 → **跨进程可复现**。
3. **「当日竞价不进入自身历史基线」三重保护,实测有效**。自己造了一个**含当日分区**的
   parquet 库(`w[-4:] + [D1]`)跑 `load_history(D1)`:返回的 4 天全是 D1 之前的,当日那行
   **没进去**;再把 `trade_date` 往回拨到窗口中间做**回放**,拿到的行也全部 `< trade_date`。
   三道:窗口按构造 `d < trade_date` · `pl.col("trade_date").is_in(window)` ·
   `!= trade_date`(mech.py:641-643 / 683-688)。
4. **`rel_to_index` 五条分支逐条实测正确**:`(MAIN, *.SH)→000001.SH` · `(MAIN, *.SZ)→399001.SZ` ·
   `GEM→399006.SZ` · `BSE→899050.BJ` · `STAR→(None,'board_excluded')` · `board=None→(None,'no_board_meta')`。
   **科创板零 fallback 属实**(短路在板块判定之前,`BOARD_BENCHMARK_INDEX[STAR]=000688.SH` 拿不到);
   码全部取自 `sentinel/universe.py`,本包**没有第二份**。
5. **裁定 ④「禁止市场指数代替板块基准」是结构性的,不是自觉**。两层:① 取样域
   `snap.industry_of` 由 `load_industry_map()`(读 `stock_basic`)派生 → 指数根本不在里面;
   ② `sector_benchmark_of` 再叠 `set(snap.index_codes)` 排除。**我构造了一个把
   `000001.SH`/`399001.SZ` 硬塞进 `industry_of` 的快照实测**:两支指数被第 ② 层挡下,
   剩 2 只对照股 → 如实返回 `data_insufficient`,**没有偷偷用指数把中位数凑够**。
6. **中位数与边界**:偶数样本取两个中值的平均(`[1,2,3,4]→2.5`)、奇数取中间;
   `len(peers) < 3` → 不足,**恰好 3 只就允许**(裁定「至少 3 只」)。这两处都是整数比较,
   不需要 `_EPS`;`gap_of` 那侧只判 `is not None`,也不涉及浮点阈值。
7. **`no_industry` 与 `data_insufficient` 真的分得开**:前者在 `industry` 取不到时**提前
   返回**(mech.py:392-393),后者在数过对照股之后返回(:397-400),两条路互不可达。
   我认可这个拆分。
8. **双端第三态没被折成 0**:`relToSectorText` / `relToIndexText` 在 `nil` 时返回
   「相对板块 未取得 —— <原因>(不是「持平」)」并画琥珀色(`AuctionCardView.swift:653-663`),
   ⛔ 没有 0、没有空白;DTO 全部手写 `init(from:)` + `decodeIfPresent`;
   `NKJSON` 的 Bool-先于-Double 顺序仍在(`Models.swift:128-131`)→
   `history_sample_sufficient` 不会悄悄变成数字。
9. **服务端下发文案零 Markdown**:`HISTORY_LOOKBACK_NOTE` / `HISTORY_SAMPLE_INSUFFICIENT_NOTE` /
   `SECTOR_PEER_POOL_NOTE` / `_REL_UNDET_TEXT` 六条全用「」强调,一个 `*` 都没有;
   客户端两条整句都是拼成一个 `String` 再进 `Text`,也没有 `*`。
10. **读数复现**:我独立跑了一次全量 `pytest tests/ -q` = **4007 passed / 3 skipped / 0 failed**
    (与 builder 自述同数);`scripts/smoke_auction.py` 绿;**真实 `data/neckline.db` md5 全程
    `7ca02c7d99a01f4226442a3edb085c9b` 不变**(本次审查零写库、零部署、零 commit)。

## B、🔴 致命

### 🔴-1 `n ≥ 15` 这道刚拍板的闸,被「篮级并集」开了一个后门 —— 一只只有 2 天历史的票会被讲成「允许形成历史比较」

**[neckline/auction/mech.py:712-718]**(`history_of`)

```python
for code in dict.fromkeys(codes):
    rows = list(index.per_member.get(code) or ())
    if rows:
        per[code] = rows
        days.update(str(r["trade_date"]) for r in rows)   # ← 全篮**并集**
n = len(days)
sufficient = n >= HISTORY_MIN_SAMPLE_FOR_COMPARISON
```

`n` 是**整篮成员的日期并集**,不是每只票**自己**的样本数。而这个字段叫
「**自身**历史竞价样本」、裁定管的也是「**当期有效样本**」——K8 §二十 要对比的是
「**当前竞价量、额** 与历史竞价快照」,那是**逐票**的量。

**失败场景(我已实测复现)**:一个篮子里有 `600519.SH`(老面孔,窗口内 20 天全有)
和 `600000.SH`(今天才第一次进关注池,只有 2 天)——

```
篮级 n = 20   sufficient = True
逐票天数    = {'600519.SH': 20, '600000.SH': 2}
```

→ 短摘要写「样本 ≥ 15 天 → **允许形成历史比较**」(mech.py:1052-1054),
→ system prompt 那条「标了样本不足的只描述原始值」**对这个篮子整体失效**,
→ 模型于是可以对**只有 2 天历史**的 `600000.SH` 说「明显放量」——
**这正是这道闸被拍板出来要挡的那句话**。而短摘要里**没有逐票天数**,人和模型都看不出来。

⚠ 这个组合**不是边角料**:篮子成员每天在换,老成员(或持仓、昨日涨停留下来的票)
与新成员同篮是常态;并集 = **取最长的那一只**,所以只要篮里有一只老面孔,整篮就"够"了。

**🔴 什么时候会醒**:生产 `auction_snapshots` 从 **2026-08-05** 才开始存,到今天只有
**~5 个交易日** → 现在所有篮子恒 `sufficient=False`,这个洞**打不着**。攒满 15 天大约在
**2026-08-26**。**所以它不拦今天的部署,但必须在那之前修掉。**

**修复方向**(零新数字):`history_of` 逐票算 `n_i`,`per_member` 每条带上自己的
`days_available` 与 `sample_sufficient`;篮级 `history_days_available` /
`history_sample_sufficient` 取**逐票的最小值**(或干脆只发逐票、篮级不再发一个会被误读的总数);
短摘要那两行改成**逐票**写(样本不足的票单独点名),客户端「历史样本不足」徽标同理下沉到票级。
守门补一条:构造「20 天 + 2 天」同篮,断言篮级 `sufficient is False` 且 2 天那只被点名。

## C、🟡 重要

### 🟡-1 模型被告知「允许形成历史比较」,却一个历史数字都没拿到 —— 只能靠编

**[neckline/auction/mech.py:1041-1059]** + **[neckline/auction/llm.py:168-183]**

喂 LLM 的 user 消息 = `date_anchor_line()` + `short_summary(mech)`,**再无别的**。
而 `short_summary` 关于历史只写两行:一个**天数**和一句「允许比较 / 样本不足」——
`history["per_member"]` 里那些**逐日 `auction_volume` / `auction_amount` / `gap_pct` 原始值
一条都没进 prompt**。

**失败场景**:`n ≥ 15` 的那一天,模型读到「样本 ≥ 15 天 → **允许形成历史比较**」,
手里却没有任何历史数字 → 要么沉默(那这条闸白开),要么**凭印象编一句「较近期明显放量」**。
上一轮 🔴-1 刚立过的纪律是「标了没判的项照实当作未知」;这里是**反过来的同一个病**:
**给了一张许可证,却没给能据以行使许可的证据**。

**修复方向**:要么(a)`short_summary` 在 `sufficient` 时把每票的历史 `auction_volume` /
`gap_pct` 逐日(或 min/median/max 三个数)写进去 —— 那才叫"可以比较";
要么(b)把那句话从「**允许**形成历史比较」改成「样本量够,但**本次未随资料下发历史明细**,
⛔ 不得做比较结论」。**⛔ 别留现在这个中间态。**(与 🔴-1 同一天醒。)

### 🟡-2 同一份 prompt 里,把上证综指叫「板块基准指数」—— 与裁定 ④ 当面打架

**[neckline/auction/mech.py:856]**(`bench_codes = [snap.benchmark_of[c] …]`)
+ **[:546-569]**(`sector_sync_of` 的 `benchmarks`)+ **[:1029-1033]**(短摘要那一行)

`benchmark_of` 对主板票落的就是 `000001.SH` / `399001.SZ`,于是喂给模型的资料里出现:

```
   板块协同:同向上涨 3 / 下跌 0 / 平 0(有读数 3/3);板块基准指数 000001.SH +0.10%
   相对强弱中位:vs 板块 +2.00%(取到 3/3 只)、vs 市场 +4.90%(取到 3/3 只);两者是两个不同的基准。
```

**上一行管市场指数叫「板块基准指数」,下一行说这两个基准不是一回事。**
而裁定 ④ 的原话是「**禁止使用市场指数代替板块基准**」——`rel_to_sector` 的**计算**确实
没用它(这点我验过),但**在唯一真正读这些字的那个消费者(LLM)眼里,系统仍然把市场指数
标成了板块基准**。判断顺序第 3、6 步(「结合板块强度」「比较候选相对板块…的强弱」)
正好会去读这一行。

builder 把范围裁到「裁定 ④ 的字面对象是 `rel_to_sector`」——**计算不动是站得住的**
(换 `sector_sync` 的口径确实要新裁定),**但保留这个标签站不住**:那不是口径问题,是**命名**问题。
⚠ 另:`sectorSync` 客户端**根本没渲染**(`AuctionCardView.swift` 只用 `relStrength`),
所以界面上**不会**出现自相矛盾 —— 这个洞**只在 prompt 里**。

**修复方向**(零裁定、零口径变更):把 `sector_sync_of` 产物里那个键与短摘要里那句话
改成它真实的身份,例如「该篮成员**所属板块的对照指数**(主板票即市场指数;**这不是本次的板块基准**,
板块基准见下一行的『相对板块』)」。一句文案的事。

### 🟡-3 §五 施工图里三处已被裁定作废的原文没改 —— 下一个 builder 照着它就会把 15 删掉

**[PROJECT_PLAN.md:855]** `history_json` 行:「🔴 **「样本够不够」交 LLM 判**……
⛔ **不设"够用"的天数门槛**,那是一个 K8 没给的数」
**[PROJECT_PLAN.md:1489]** ⑨-A 第 1 行:「机械层只出 `history_days_available` 这个**读数**,
「够不够」写进 prompt 交 LLM 判……**⛔ 不设天数门槛**」
**[PROJECT_PLAN.md:847]** ②-D `rel_to_sector` 行仍写「该票 `gap_pct` − **板块基准指数** `gap_pct`」
**[PROJECT_PLAN.md:1887]** §七 **P4-67** 的「处置」段仍写「⛔ 不设「够用」的天数门槛」

§四 与 §七 P3-69/P3-70 都改对了,**但 §五 是本项目自己规定的「当前版本施工口径」**。
现在同一份 PROJECT_PLAN 里,§五 说「⛔ 不设天数门槛」、§七 说「15 是用户拍板的机械判据」——
**失败场景**:下一棒 builder(或下一个 reviewer)按 §五 ⑨-A 第 1 行判定
`HISTORY_MIN_SAMPLE_FOR_COMPARISON=15` 违反 〇b-1「零新阈值」红线,**把它删掉**,
而且他有白纸黑字的依据。同理 ②-D 那行会让人把 `rel_to_sector` 改回板块基准指数。

**修复方向**:§五 ②-D 两行、⑨-A 第 1 行、§七 P4-67 处置段各加一句
「**已被 2026-08-12 用户裁定取代 → 见 §七 P3-69 / P3-70**」(原文按项目体例保留、划掉即可),
⛔ 别原地重写成新口径(那会造出第二份权威)。**改文档不改代码,零风险,建议上产前就做。**

## D、🔵 建议

- **🔵-1 客户端硬编了那个 `3`** —— **[client/Neckline/Networking/Models.swift:4518]**
  `case "data_insufficient": return "有效板块对照股不足 3 只"`。服务端**已经**在
  `rel_strength.sector_peer_min` 里下发了这个数(mech.py:605),客户端却又抄了一份字面量。
  裁定值改一次就会两边打架,而且**没有守门**。建议改成读 `sector_peer_min` 拼串,
  取不到再退回不带数字的说法。
- **🔵-2 老行会渲染出自相矛盾的一句话** —— **[Models.swift:4772-4778]** + **[api/app.py:2174]**:
  整改前冻的 `members_json` 有 `rel_to_sector` 的值、没有 `rel_to_sector_source`
  → 服务端补 `"unavailable"` → 客户端走 else 分支 → 印出
  「相对板块 +1.42%(对照:**未取得** 未记录)」= 有值却说没取到。
  两张表尚未上产,只影响本地演示库,故只列 🔵。建议:有值但 `source == "unavailable"` 时
  改印「(对照:口径已变更,该值来自旧口径)」或干脆当第三态处理。
- **🔵-3 「只展示原始值」这后半句在界面上没落地** —— **[AuctionCardView.swift:548-575]**:
  `history.per_member` 的逐日原始值**下发了但一个都不画**,界面只有天数 + 三句话。
  裁定原文是「标记『历史样本不足』,**只展示原始值**」——现在是「既不比较、也不展示」。
  不算说谎,但那半句需求没实现。
- **🔵-4 「三支指数等权平均正式停用」的反向守门是 token 黑名单,换个写法就逮不到** ——
  **[tests/test_v233_auction_mech.py:986-1010]** 禁的是 `"sum(idx_gaps"` / `"mean(index_gaps"` /
  `"/ 3.0"` / `"/ 3)"` 这几个**字面片段**。有人写
  `avg = sum(vals) / len(vals)` 重新引入等权平均,这条守门**全绿**。
  建议改成语义判据:断言 `MemberReading.rel_to_index` 的产出恒等于
  `gap_pct − snap.gap_of(index_benchmark_code)`(逐票对拍),那才是真正锁死"单指数"。
- **🔵-5 窗口那条用例钉不死「恰好 20 个交易日」** —— **[tests/test_v233_auction_mech.py:730-741]**:
  ① 跑在 `isolated_env` 上,`trade_cal` 是**空表** → 走的是静态表/工作日近似,
  **没验到 DB 日历那条路**;② `assert all(is_trading_day(d) …)` 用**建窗口的同一个函数**自证,
  循环论证;③ 只断言 `0 < len(window) <= 20` —— 实现哪天退化成只返回 10 天,**这条照样绿**。
  建议:往 `fake_settings.db_path` 的 `trade_cal` 塞一段真日历,断言 `len(window) == 20`
  且 `window == 已知的那 20 个日期`。(我用真日历替它验过了:2025–2026 每个交易日恒 20。)
- **🔵-6 跨年那天窗口会悄悄退化** —— `trade_cal` 覆盖到 **2026-12-31**;`trade_date` 一旦进
  2027,`trading_days_between` 两端不在覆盖内 → 逐日回退 `_static_is_trading_day`,而
  `STATIC_YEARS=(2025, 2026)` → **2027-01-01(周五)会被当成交易日**占掉窗口一格。
  我实跑 `history_window_days(2027-01-05)` 复现了那两条 warning。这是日历的既有局限、
  不是本棒引入的,但本棒**新增了一个依赖它的判据**。建议把「跨年前跑
  `scripts/init_calendar.py` 延长 `trade_cal`」写进部署/运维清单。
- **🔵-7 `no_industry` 里折了第三种成因** —— **[neckline/auction/collect.py:336-345]**:
  `load_industry_map` 整个抛异常时 `industry_of = {}` → **全篮每只票**都拿到 `no_industry`,
  与「这一只票在 `stock_basic` 里真的没登记行业」发同一个码。差别只在报告级 `notes`
  里那条 `industry_map_unavailable`。按本仓 P0-39 的纪律(系统缺席 ≠ 实质判断),
  建议另给一个 `industry_map_unavailable` 码。
- **🔵-8 那句「板块对照股取样域」的诚实披露挂错了地方** —— **[AuctionCardView.swift:549]**:
  `sectorPeerPoolNote` 画在 `if let days = verdict.historyDaysAvailable { … }` 里面,
  历史那个键一旦缺(老行 / 该段读不出),**板块对照股的披露也一起消失**。
  两件事没有从属关系,建议拆成两个独立块。
- **🔵-9 对照股池本身是"偏强样本",披露没说到这一层** —— 关注池 = 持仓 + T1/T2 成员 +
  板块基准指数 + **昨日涨停**。用它取同行业中位数,分母天然偏向当天最强的一批票 →
  中位数系统性偏高 → `rel_to_sector` 系统性偏低(「跑输板块」会比真实情况多)。
  `SECTOR_PEER_POOL_NOTE` 列了池的来源,但没点破这层**选择性偏差**。建议在那句话里加半句。

## E′、逐条销项(@builder-pro,2026-08-12 当日整改;⛔ 未部署 / 未 commit / 没动真库)

> **口径**:每条只有三种去处 —— **已修** / **已挂账(§七)** / **判定不成立**。
> **零新阈值**:整改全程可用的数只有裁定给的 `20 / 60 / 15 / 3`,**一个新数都没发明**
> (对照三数 min/中位/max 是同一批原始值的**描述统计**,不是门槛;逐日原始值**不截断**,
> 上界就是裁定的 20 天窗口本身)。

| 条目 | 去处 | 落点 / 判据 |
|---|---|---|
| 🔴-1 篮级并集开后门 | **已修** | `mech.history_of()` 逐票算 `n_i` + `sample_sufficient_i`;篮级**取 min** 并自曝 `history_days_available_basis="min_per_member"`;短摘要逐票写、点名不够的那几只;双端下沉到票级。**正面守门** `test_history_sample_is_counted_per_member_not_as_a_basket_wide_union`(15 天 + 2 天同篮,断言 2 天那只自己 `insufficient`、摘要里点名、够的那只**不被连坐**)+ 客户端 `testAuctionHistoryIsPerMemberAndNamesTheShortOne`。**选的是「逐票 + 篮级取 min」**(不是只发逐票):篮级那个数已经进了契约与界面,留着并改成 min 比删掉更不容易被误读,而 min 的语义恰好等于「每一只都够」 |
| 🟡-1 有许可证没证据 | **已修** | 够样本 → `comparison_readings`(窗口内 量/额/涨跌 各自 最低/中位/最高 + `observed`)进短摘要;不够 → **逐日原始值**进短摘要 + 逐字「⛔ 不得据此做比较结论」;`0 天` 另说一句「一条都没有,不是『跟平时一样』」。system prompt 第 3 条改写成逐票口径。**token 实测**见下方读数,**没压过预算 → 没挂账、也没拍截断 N** |
| 🟡-2 把上证叫「板块基准指数」 | **已修** | `sector_sync` 键改名 `listing_board_benchmarks` + `listing_board_benchmarks_note`(服务端文案单一源);短摘要那一行改成「所属上市板块对照指数(主板票即市场指数本身,不是本次的板块基准)」;system prompt 补一条把它与「相对板块」分开。**计算零改动**。守门 `test_prompt_never_calls_a_market_index_the_sector_benchmark`(断言旧键名不再存在 + 摘要里不出现「板块基准指数 000001.SH」) |
| 🟡-3 §五/§七 作废原文 | **已修** | §五 ②-D 两格(`rel_to_sector`/`rel_to_index` 与 `history_json`)· ⑨-A 第 1 行 · ⑨-B 第 8 条 · §七 P4-67 处置段 —— **原文保留 + 删除线 + 「⚑ 已被 2026-08-12 裁定取代 → 见 §七 P3-69/P3-70」+ 一句「⛔ 别照这句去删那个 15」**;P3-69 / P3-70 两条各追加本轮修正记录 |
| 🔵-1 客户端硬编 3 | **已修** | `nkAuctionRelReasonLabel(_:sectorPeerMin:)` + `relToSectorText(sectorPeerMin:)`,值取服务端 `relStrength.sector_peer_min`;**取不到就说不带数字的话**(⛔ 不猜)。守门 `testAuctionSectorPeerMinComesFromTheServer` |
| 🔵-2 老行自相矛盾 | **已修** | 有值 + `source=="unavailable"` → 印「(这条来自旧口径,当时未记录对照来源;新口径的板块基准是同行业对照股中位)」,⛔ 不再印「未取得」。守门 `testAuctionOldRowWithValueButNoSourceIsNotSelfContradictory` |
| 🔵-3 「只展示原始值」没落地 | **已修** | 逐票行下面新增一句 `AuctionMemberHistory.noteText`:不够 → **逐日原始值**;够 → 对照读数。⚠ 它住在**收起的**「逐票读数」展开里 → 不把首屏推下去 |
| 🔵-4 等权平均守门是 token 黑名单 | **已修** | 新增**语义**守门 `test_rel_to_index_is_exactly_its_own_index_gap_subtracted`:三支指数**取值互不相同**,逐票断言 `rel_to_index == gap_pct − gap_of(该票自己那支)` 且三只票各减各的。原 token 黑名单**保留**(多一层不亏,但它不再是唯一判据) |
| 🔵-5 窗口用例钉不死 20 | **已修** | 新增 `test_history_window_is_exactly_twenty_days_of_a_real_calendar`:往 `trade_cal` 塞一段日历 + `reset_cache()`,断言窗口**恰好 20 个**且**逐位等于**独立构造的期望列表(⛔ 不再用被测函数自证)。原用例保留 |
| 🔵-6 跨年窗口退化 | **已挂账** | → §七 **P4-72**(运维动作:跨年前跑 `scripts/init_calendar.py` 延 `trade_cal`;判据 `MAX(cal_date) ≥ 次年年末`)。**不改代码**:自动近似 = 拿工作日冒充交易日历 |
| 🔵-7 `no_industry` 折了第三种成因 | **已修** | 新码 `REL_UNDET_INDUSTRY_MAP_UNAVAILABLE`(单一源 `auction/__init__.py`),判据 = 报告级 note `NOTE_INDUSTRY_MAP_UNAVAILABLE`(collect 与 mech 共用同一个常量);服务端文案 + 客户端 label 各一句。守门 `test_industry_map_unavailable_is_not_folded_into_no_industry` + `testAuctionIndustryMapUnavailableIsItsOwnReason` |
| 🔵-8 披露挂错地方 | **已修** | `sectorPeerPoolNote` 从 `if let days = historyDaysAvailable {…}` 里**拆出来**,独立块 |
| 🔵-9 对照股池是偏强样本 | **已修** | `SECTOR_PEER_POOL_NOTE` 补半句:池子偏向当天最强的一批票 → 中位数系统性偏高 →「相对板块」系统性偏低,读到「跑输板块」时要记得分母是一批强票 |

**本轮读数**:Python **4014 passed / 3 skipped / 0 failed**(连跑两次同数;本轮基线 4007 → **+7**)·
双端 `xcodebuild` **BUILD SUCCEEDED** · iOS `NecklineTests` **237 tests / 0 failures**(232 → +5)·
冒烟 6 个全绿。
**🟡-1 的 token 实测**(一次性探针,不入仓;6 只成员/篮 = 真实上限 `MAX_MEMBERS=3` 的两倍):
历史段 **旧 ~106–152 字 → 新 1305–4326 字**;user 消息 **2272→3471**(全篮满样本)/
**2316→4217**(全篮 5 天 = **今天生产的真实形态**)/ **2318→6492**(全篮 14 天 = **最坏情形**,
差一天就够 15)。按真实上限(`TIER_CAPACITY` 合计 **7 篮** × `MAX_MEMBERS` **3 只**)折算,
最坏约 **+15k 字符**、整份 prompt 量级 ~2 万字符 —— **没有压过任何预算**
(本项目的 LLM 预算是**墙钟秒数**不是 token,真天花板是 9:29 硬截止),故**没挂账、也没拍截断 N**。
上界是**结构性**的:窗口 20 天 × 每篮 ≤3 只 × ≤7 篮,全部来自既有裁定值。

---

## E、结论:能不能上产

**能上产 —— 但带两条硬附加条件。**

1. **🔴-1 不拦今天的部署**:生产 `auction_snapshots` 自 2026-08-05 起只有 ~5 个交易日分区,
   `history_sample_sufficient` 现在**恒 `False`**,那个后门打不着。
   🔴 **但它会在样本攒到 15 天那天(约 2026-08-26)自己醒过来,且醒来时没有任何告警**——
   **必须在那之前修掉 🔴-1 与 🟡-1**(两者同一天生效,建议一起改)。
2. **🟡-2(把上证叫「板块基准指数」)与 🟡-3(§五 三处作废原文)建议上产前顺手做**:
   前者是一行文案、后者是纯文档,零代码风险;而 🟡-2 **每天早上都在 prompt 里生效**,
   拖着它上产等于让裁定 ④ 在最要紧的那个消费者面前失效。

**其余 9 条 🔵 都不拦部署**,可按方便程度排。

> **本轮复审方式**:全量 `pytest tests/ -q` 独立跑一次(**4007 passed / 3 skipped / 0 failed**,
> 与 builder 自述同数)· `scripts/smoke_auction.py` 绿 · 另用 4 段一次性探针(**不入仓**,
> 写在会话 scratchpad)分别实测了「真日历下 2025–2026 每个交易日的窗口长度与回溯跨度」
> 「含当日分区的库 + 回放路径的基线排除」「`market_index_of` 六种输入」
> 「把指数码硬塞进 `industry_of` 能否顶替板块基准」「篮级并集 vs 逐票天数」。
> **本次审查未修改仓内任何代码文件、未部署、未碰服务器、未 commit;
> 真实 `data/neckline.db` md5 前后一致(`7ca02c7d…`)。**
