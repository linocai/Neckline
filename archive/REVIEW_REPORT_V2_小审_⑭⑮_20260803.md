# REVIEW REPORT · V2 小审:⑭ 篮子日报/契约总装 + ⑮ 客户端双端改版(2026-08-03)

- **审计人**:@reviewer(契约/数据线,增量小审;①–⑬ 已清账不重审,判定线另有分工)。
- **对照件**:PROJECT_PLAN §五 V2-⑭/⑮ 规格与完工记录、`archive/V2_契约三方对拍_20260803.md`、CLAUDE.md。
- **方法**:从零读代码不信自述;⑭ 服务端(evening.py / basket_daily.py / render.py / pipeline / store / schemas / app / test_contract_crosscheck.py)逐文件亲读;⑮ 客户端以 file:line 级事实清单 + 承重点亲手复核(key 采集链、幂等键轮换、未知 kind 降级、B 类 DTO 解码面);对拍表与代码抽核;卡键覆盖用脚本逐键 diff。真实 db 与生产零接触。
- **基线复核**:本机实跑 `python -m pytest tests/ -q` = **2890 passed + 2 skipped**,与交办一致(挂钟脆弱用例本轮时段通过);Swift 179 过 + 12 skip 取完工记录 + 测试文件存在性证据,未在本环境亲跑 xcodebuild(既有环境限制)。

**分级统计:🔴 0 · 🟡 1 · 🔵 6 · 🟢 10**

---

## 🟡 重要(1)

### Y-1 · 重跑历史日期的报告会**不可逆销毁 V1 冻结快照**(`INSERT OR REPLACE` + 列清单缩水的合谋)

- **位置**:`neckline/report/store.py:57-80`(`save_report` 的 `INSERT OR REPLACE INTO reports`);`neckline/report/pipeline.py:313`(`candidates=[]` 恒传)。
- **机理**(三件事叠加,单看每件都"对"):
  1. ⑬-11 起 `watchlist_json` 不再出现在 `save_report` 的列清单里(git 证据:`1a318db` 删掉了该参数与该列)——而 **`INSERT OR REPLACE` 是整行先删后插**,不在列清单里的列被重置回 DDL 默认 `'[]'`;
  2. ⑭ 起 `build_report` 恒传 `candidates=[]`(⑬ 欠账登记过「列保留恒落 []」,但那说的是**新行**);
  3. `scripts/report.py <YYYYMMDD>` 重生成**任意历史日期**是 CLAUDE.md「跑法」一节的文档化工作流。
- **后果**:对任何 v1.0–v1.5.2 生产历史日期跑一次 `scripts/report.py` → 该日 `reports` 行的 `candidates_json`(20 只候选快照)与 `watchlist_json`(自选体检快照)**被覆写为空**、`markdown` 被 V2 版式覆盖 —— **V2 已删除候选管线,这些快照无法重算,一次误跑即永久丢失**。六张停写表的「行数不增」守门抓不到它(REPLACE 行数不变);「历史行供归因只读」的停写留档纪律被这条路径从侧面击穿。
- **为什么归 ⑭**:⑭ 是报告落库与契约总装的收口块,`basket_daily_json` 新列沿用了同一条 `INSERT OR REPLACE` 写法,列清单缩水的破坏向量在 ⑭ 定稿时应当被看见。
- **修法方向**:① `save_report` 改 `INSERT INTO … ON CONFLICT(trade_date) DO UPDATE SET <本次列>`(不在清单里的列天然保留,`watchlist_json` 立即得救);② `candidates_json` 对「已有非空历史值」的行不覆写为 `[]`(或对 V2 上线日之前的日期直接拒绝重生成 / 覆写前自动 `.backup`);③ 补一条守门:对造好的 V1 形状历史行重跑 `save_report` → 断言 `watchlist_json`/`candidates_json` 逐字节不变。
- **缓解面**:此刻纯本地、生产未部署;真实生产库尚未被此路径碰过。但 ⑯ 数据搬家后新机上这条枪就是上膛的,应在 ⑯ 之前修掉。
- ✅ **已修(2026-08-04,commit `a123fe9`)**:采纳修法方向 ①③ + ② 的第一种选项(`candidates_json`
  对「已有非空历史值」的行不覆写为 `[]`,不是拒绝重生成 / 自动 `.backup`)——
  `save_report` 改 `INSERT INTO … ON CONFLICT(trade_date) DO UPDATE SET <本次写入列>`;
  `watchlist_json` 不在写入列清单里,天然免疫;`candidates_json` 额外加 SQL `CASE`
  守卫(本次写 `[]` 且历史已非 `[]` 时保留旧值并记 WARNING,真的写非空 candidates
  时仍正常覆盖,不误伤合法路径)。**全仓同模式扫描结论**:除本处外零命中——
  `app_settings` 的停写列(`llm_provider`/`llm_api_key`/六个推送开关/
  `intel_watch_boards`)全部走「`INSERT OR IGNORE` 补首行 + 逐字段 `UPDATE…SET…
  WHERE id=1`」,天然安全;`inquiry_pool`/`watchlist`/`decision_log`/
  `reference_plans`/`llm_judgments`/`breathing_t_trades` 六表是**整表停写**
  (写函数已物理删除),没有残留写手能触发同类风险;`holding_eod_check` 的三列
  「D5 判一次定格」不是"停写留档"列而是活跃写路径,且 `save_holding_eod_checks`
  每次都显式带上这三列的值(由调用方 carry-forward,不是从列清单省略),不同险。
  守门单测 `tests/test_report_store.py::TestV1FrozenSnapshotSurvivesRerun`(3 例:
  两列逐字节不变 / 显式非空写入仍正常覆盖 / 历史本就是 `[]` 时重跑不误报)。

---

## 🔵 建议(6)

### B-1 · `ReportResponse` 残留六处硬解码,`sectors` 数组是最重的一处
`client/Neckline/Networking/APIClient.swift:143-150`:⑮ 修了 ⑭ 点名的三处硬失败,但同一个 `init(from:)` 里 `sectors = try c.decode([SectorSnapshot].self,…)`(`SectorSnapshot` 合成 Codable,内部非 Optional)与 `tradeDate`/`generatedAt`/`strategyVersion`/`degraded`/`reason` 五个标量仍是硬解码。现役契约恒发这些键(`sectors_json` NOT NULL),不构成活险;但它们与被修的三处同源同病,服务端日后动 `sectors` 形状 = 整份报告解不出。建议顺手拉平成 `decodeIfPresent + 诚实空态`,别等 CLAUDE.md 两步淘汰纪律来兜。

⛔ **本轮(2026-08-04 修复批)未处理**:现役契约恒发这些键、不构成活险,且改动面覆盖整个 `ReportResponse.init(from:)`,超出「便宜 🔵」的授权范围;留待下一轮 review 或专项收口。

### B-2 · ⑭-C 调用面提取的三个结构性盲区(今日干净,漂移时静默失守)
`tests/test_contract_crosscheck.py:30,34,47`:① 只扫 `APIClient.swift` 单文件——已亲手验证 client 其余文件零 `/api` 字面量(今日成立,依赖「网络层只此一家」的架构惯例),日后任何 View/Model 直接拼 URL 即绕过闭包断言且无人知晓;② 正则只认 `"/api/v1…"` 开头的字符串字面量,`basePath + "/x"` 类拼接不可见;③ 对拍的是路径形状、无 HTTP method 维度(POST 打到 GET-only 路径不报)。建议:glob 扩到 `client/**/*.swift`(成本一行)+ 给 client 侧提取补 method。

✅ **已修(2026-08-04,commit `a123fe9`)**:① `client_call_surface()` 改扫 `client/`
下全部 `.swift` 文件(含 `NecklineTests/`),不再只锚 `APIClient.swift` 单文件;
③ method 维度新增 `test_client_call_methods_match_server_route_methods_where_
determinable`——**已知不完整**,只覆盖路径字面量直接传给 `get/post/put/delete(...)`
的调用点(实测约 36/56),调用点若先 `let path = "..."` 再传变量则不纳入(正则不解析
变量绑定,宁可少覆盖、不产出假阳性)。② `basePath + "/x"` 类拼接的盲区**未处理**
(本仓库实测零此类拼接,且要可靠检测需要更深的解析能力,不是「一行成本」的范畴,
留作已知限制)。顺带发现并修:glob 扩到全部文件后 `client/NecklineTests/
URLGateTests.swift` 里字面量 `"42"`(而非插值)会被误判成路径不闭合,`_normalize`
补一条「裸数字路径段折叠成 `{}`」规则(已核实服务端真实路由没有固定的纯数字路径段,
折叠不会掩盖真实回归)。

### B-3 · 卡损坏与卡未生成共用 `card_not_ready`
`neckline/api/app.py:621-625` + `basket_store.load_basket_card`:`card_json` 解不出(损坏)时 `row["card"]=None` → 端点同样返 `card_not_ready`。「本篮的卡还没生成」会让一张**已生成但损坏**的冻结卡永远显示成"还没生成",数据损坏被降格成等待中。建议:store 解码失败时透传独立标记,端点区分 `card_corrupt`(或 500)与 `card_not_ready`。

⛔ **本轮(2026-08-04 修复批)未处理**:拆分 `card_not_ready` 涉及新增 reason 码,
需要 planner 先定新 reason 字符串与客户端 `mapReason` 接线口径,不属于施工侧可
自行拍板的「便宜 🔵」;留待 planner 裁定后再排期。

### B-4 · Provider key 草稿在提交失败路径不清空
`client/Neckline/App/AppModel.swift:906-910`:成功清(`:902`)、取消清(SettingsView:441)、**失败留**——明文 key 驻留 `@Observable` 内存直到用户下一步操作。属「保留输入好重试」的常见取舍,但与同文件 `:94-95`「只在本次填写期间存在」的注释有距离。建议 catch 分支提示后仅保留非 key 字段,或至少把注释改成与实现一致。

✅ **已修(2026-08-04,commit `a123fe9`)**:采纳「catch 分支仅保留非 key 字段」——
`submitProviderForm()` 的两个 `catch` 分支补 `providerForm.apiKey = ""`,失败重试
时表单其余字段(name/baseUrl/model/searchEngine/notes/enabled)原样保留、明文 key
草稿不残留。

### B-5 · 两处注释与事实不符(纯文档债)
① `client/Models.swift:2008`:声称 `scenarioReviewPending` 勾选「仍走既有 `POST /decisions/{id}/scenario-outcome`」——该端点服务端已删、客户端方法已删,会误导后人"把调用接回来";② `AppModel.swift:231` 幂等键「提交成功后作废」vs 实现「下次 `beginPositionEntryFlow()` 才换新」(风险为零:成功即 `dismissModal()`,下一笔必经 begin;且 AppModelTests:380 反向断言了同流程不换键),措辞应改一致。

✅ **已修(2026-08-04,commit `a123fe9`)**:两处均订正为与实现一致的措辞
(① 说明该端点/方法已物理删除,本字段现在纯只读展示;② 改为准确描述「提交成功
不主动作废,旧值留到下次 `beginPositionEntryFlow()` 才换新」,并引用
`AppModelTests` 的反向断言)。

### B-6 · ⑮ 更新 Provider 时 `searchEngine`/`notes` 的「留空」语义与 `apiKey` 不对称
`AppModel.swift:891` 更新路径把空串直接发出(= 清空服务端值),而 `apiKey` 是「留空 = 不传 = 不改」。语义各自成立,但同一张表单里两种「留空」含义不同,用户会按 key 的直觉误清 engine/notes。建议 UI 提示或统一为「留空不改 + 显式清除按钮」。

✅ **已修(2026-08-04,commit `a123fe9`)**:采纳「对齐 `apiKey`」一侧(未加「显式
清除按钮」——那是 UI 功能新增,超出本轮授权范围)——`submitProviderForm()` 的更新
分支对 `searchEngine`/`notes` 也做「空串 → `nil`」判断,与 `apiKey` 同一种「留空 =
不改」读法;代价与 `apiKey` 相同(一旦服务端已有值,不能再靠清空这个字段把它改回
空,要清除须删除 Provider 重建),已在代码注释与契约对拍表 §6.2-B1 写明。UI 侧
补充提示文案(如 `apiKey` 已有的 footer 说明)未做,留作后续观察项。

---

## 🟢 核过为好(10)

1. **五段保险丝独立性与链序**:`evening.py` 逐段 try/except、任一段 `failed` 链继续、**报告段唯一 re-raise**(CLI exit 3,systemd 可见);⑧ 位置「拉数后、扫描前」、`segments` 乱序传入按 `CHAIN_SEGMENTS` 重排、四态不合并、`dropped` 三态(`None`/`[]`/list)——与 Plan 定死语义逐条一致,`test_evening_chain.py` 15 例(含逐段炸链测试与「evening 是唯一批算调用方」守门)全绿。**⑯-D 跨进程分段不失效**:`SEG_BASKET` 单独跑时 `seed_set=None` → `aggregate_baskets` 自读预计算表重算种子(basket_store 亲核)。
2. **`build_report` 只读不算成立**:`pipeline.py`/`basket_daily.py` 都在 P0-23 在线清单(`test_scan_layer_guardrails.py:19-31`,⑭ 主动把 basket_daily 加进去),`evening.py` 刻意排除并写明理由;`basket_daily.py` 零写库、零批算、零 LLM,exec_hint 用 60 天轻量面板(P1-26 教训的正确姿势);pipeline 写入仅报告族快照(V1 既有体例)。
3. **③b 原因码渲染不可绕**:两码分桶 + 计数 + 表格逐行带码与文案、未知码单列不并桶;测试正向断言两句结论文案、**反向断言「未入选」不得出现**;零溢出节仍在、`dropped=None` 与 `[]` 讲不同的话;历史 `{}` 快照诚实标「生成于篮子日报上线之前」(`basket_daily_from_snapshot`),不冒充「那天没篮子」。
4. **`reports.basket_daily_json` 新列体例**:幂等迁移(`TEXT NOT NULL DEFAULT '{}'`)+ 写侧 `sort_keys` 可比 + 读侧容错;溢出篮唯一落点在报告快照的设计与 ⑥-b-C 一致。(该列自身干净;历史行破坏向量见 🟡 Y-1,是 OR REPLACE 写法的问题不是新列的问题。)
5. **契约面抽核全对上**:12 新路径 + 12 删路径机器断言在且过;`GET /baskets/{id}/card` 双 404(`basket_not_found`/`card_not_ready`)实现、注释、客户端 case、文案四层分得开;`droppedBaskets` 默认空数组 + `droppedBasketsAvailable` 位;`GET /alerts` 参数改回 `status`;`no_base_plan` 全新 reason 三方齐;幂等键语义文档(对拍表 §十一)把上轮 🔵 完整兑现。
6. **两个「债集合」真空 + 反向防线**:`PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15`/`PENDING_MAP_REASON_CASES_FOR_15` 均 `set()`,断言用 `==` 且配「清单里的债必须真的还在客户端」的反向守门;`SERVER_REASONS` 全量登记(app.py 是唯一 reason raise 面——deps.py 401 无 reason 键、inquiry.py 零 HTTPException,亲核);两条冻结 DTO 闸从 skip 转跑(9 过 + 0 skip 本机实跑)。
7. **card_json snake→camel 唯一转换点零漏键**(脚本逐键 diff):34 顶层键 = 32 显式映射 + `members`/`fingerprint` 专门处理;25 成员键 = 23 显式映射 + `role_conflict`/`is_primary` 专门处理。显式清单不递归的取舍正确(spec 条件键是语义标识符不该改名)。
8. **⑮ 解码面三态干净**:B 类 `BasketCard` 34/34、`BasketReview` 12/12 全字段 `decodeIfPresent` 且整个子树(Evidence/Member/PriceBand/Tag/Scripts/Fingerprint/NKJSON)容错;A 类篮子族 8 个 + `BasketDaily` 零漏网;`ReportResponse.basketDaily` 那一层是 `decodeIfPresent`(残留硬解码见 🔵 B-1);老快照缺键 fixture 有测试。`mapReason` 14 case + 409/422 接入 + `card_not_ready` 文案「本篮的卡还没生成」正确。
9. **Provider key 路径(重点审)总体安全**:`SecureField` 采集 → 内存草稿(不落 Keychain/UserDefaults/磁盘)→ trim 后 `key.isEmpty ? nil : key` 只发一次 → 成功即清草稿;编辑态显式 `apiKey: ""` 不回显;`Provider`/`SettingsProvider` DTO **根本没有 apiKey 属性**(服务端误回明文也无处落地);全 client 唯一 `print` 是 `#if DEBUG` 的 APNs device token;`ProviderUpdateRequest` 全 Optional 合成 Encodable = 「nil 键不出现 = 不改」,永不误发空串清 key;`SettingsLLMRequest` 与五个已删端点调用全域零残留(仅退役注释)。**⑮ 幂等键**:UUID 唯一铸造点 `beginPositionEntryFlow`、mock 500 重试复用同键有单测、`replayed=true` toast 如实「未重复开仓」、零业务量绑定、真实往返冒烟(同键二次不开仓)在。**推送动态渲染**:未知 level 自成组照显、未知 kind 横幅照显 + 不路由不崩不吞、零硬编 kind 清单、按 kind 不按 category 分支。
10. **版号与完工自述抽查**:`project.yml`(2 处)/pbxproj(app target 2 处)/`app.py::VERSION` 全 `2.0.0`,守门单测(`test_client_and_server_marketing_version_all_equal`)三条断言核过;⑮ 自述「12 Swift skip = 未起 dev 后端的联调冒烟」与 `IntegrationSmokeTests.swift` 的条件 skip 机制相符;语义红线 grep 11/11 命中全为否定句或禁令注释,三处用户可见文案均为「离场参考区间(**不是**止盈线)」式主动澄清。

---

## 附:审计覆盖声明

- 覆盖:⑭ 五段保险丝/③b/编排链语义/新列体例/build_report 只读性;⑭-B/C 契约(路径、DTO、双 404、reason 闭包、机器断言可绕性);⑮ 解码三态/两 B 类 DTO 逐字段/mapReason/key 路径/推送动态渲染/幂等键/版号;两块完工自述抽查(债集合、skip 转跑、测试基线)。
- 未亲跑:xcodebuild(环境限制,CLAUDE.md 已载);Swift 侧证据 = 测试文件与断言的静态核读 + 完工记录数字。
- 按登记口径放行:⑨ 先于报告的链序改判(planner 已改写 ⑯-D)、④ 双挂载(⑯-D 拆分时摘)、`VERSION` 归 ⑮ 三方同批(已兑现)。
