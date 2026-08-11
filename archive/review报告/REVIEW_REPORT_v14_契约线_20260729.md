# REVIEW_REPORT · v1.4 契约线独立审计(P2-9 两路 + v1.4 新契约三方对拍)

- 审计日:2026-07-29 · 审计员:@reviewer(契约线;与判定线审计员并行分工,⑥-A/⑩/P2-10 不在本报告范围)
- 方式:**纯审计,零代码修改、零生产访问**。本地库 `data/neckline.db` 只读取证;测试只跑不改。
- 测试证据:Python 全量 `pytest tests/ -q` → **1677 passed + 2 skipped**(37.9s,与基线一致);
  Swift `xcodebuild test`(iOS Simulator,LinoJ-iPhone16Pro)→ **168 tests / 13 skipped / 0 failures, TEST SUCCEEDED**。
  macOS 侧 `xcodebuild test` 因 `TEST_HOST` 只适配 iOS 布局跑不了——与 §九 2026-07-29 既有记录一致,非本次新问题。
- 输入材料:`archive/对照表/v1.4_A2B3口径切换对拍_20260724.md`、`archive/对照表/v1.4_排序键切换对照_20260724.md`(已抽查,见 §四)。

## 〇 结论一句话

**未发现 🔴 级缺陷**。K4 安检三处消费点确实收敛到同一份判据(v1.4-② 宣称属实);「诚实字段」四段链路全部走通;v1.4 契约清单 14 条三方对拍全部成立;404/400 reason 与客户端 `mapReason` 互为闭包。发现 **3 条 🟡**(六节之一 `intel_order` 零消费、exec_hint C3 无前视截断的 UTC/北京时区缝、`inquiryId` 客户端解码断链)与 4 条 🟢 观察。

---

## 一、🟡 重要

### 🟡-1 DB `k4_advisory.intel_order` 节从未被任何代码消费(「六节用足」声明与实现有缺口)

- **证据**:真库 K4 行 `rule_json["k4_advisory"]["intel_order"]` = `["B2双金叉","A1换手","B4追强","B3题材23","B1堆积","A3年线下涨停","A2题材≥4天"]`;
  `grep -rn "intel_order" neckline/ scripts/ tests/` **零命中**。K4 标注的实际展示顺序 = `_evaluate_hits` 的发射顺序
  (A1→A3→A3b→B1→B2→B4→A2/B3,[`neckline/report/holding_k4_check.py:465-484`]),与 DB 声明的展示优先级**不同**。
- **为什么算缺口**:需求 3 补充([`archive/交接与日志/交接_系统线升级需求_20260725.md:72-76`])明写六节含 `intel_order`(展示排序)且
  「实现情报包直接读这条 DB 记录」;§七 📌-22 明写「v1.4 是**把这六节用足**」。六节里五节都有消费方
  (hard_cut/avoid_flag → `_evaluate_hits`+`load_k4_sections`;exec_hint → `report/exec_hint.py`;circuit_breaker →
  `sentinel/circuit.py` 常量镜像;note = 纯档案),唯独 `intel_order` 落空。
- **修复方向**:二选一——(a) 候选卡 `k4_flags` / 信息卡 `k4Flags` / 持仓 `k4Advisory` 的展示序按 DB `intel_order` 排
  (读 DB、不抄常量,同 evidence 文字姿势);(b) 在 PROJECT_PLAN 显式登记「intel_order 不消费」的裁定理由(如 v1.4-③
  三级排序键已覆盖其意图),不许悬空。

### 🟡-2 exec_hint C3 的「无前视截断」有一条 UTC/北京时区缝

- **证据**:`decision_log.created_at` 用 UTC ISO 落库([`neckline/decision_log.py:103-104`]);`list_decisions` 的
  `date_to` 过滤按 `substr(created_at,1,10)`(= **UTC 日期**)比较([`neckline/decision_log.py:193-195`]);
  `exec_hint._latest_decision` 以 `date_to=trade_date` 做无前视截断并在 docstring 宣称「无前视偏差铁律的落地,
  不是摆设参数」([`neckline/report/exec_hint.py:196-215`])。北京时间 **T+1 00:00–07:59** 创建的决策,UTC 日期仍是 T
  → 历史回放 T 日报告时 C3 会读到「T 日当时并不存在」的决策(盘前 7 点预注册是完全现实的使用场景)。
- **影响界定**:C3 只进展示层执行提示(不进排序键、不进任何判定),16:35 实时生成不受影响(彼时未来行尚不存在);
  受影响的是**历史回放的可复现性**与该模块自己立下的铁律声明。同一缝隙也影响 `GET /decisions` 的 `from`/`to` 过滤语义
  (v1.2 起既有行为)。另注意系统内时区口径已分叉:周复盘章程切换时刻明确用**北京时间**(plan §五 v1.4-⑥-A),
  决策日志用 UTC。
- **修复方向**:比较前把 `created_at` 换算为北京日期(或落库即用北京时间,与 ⑥-A 口径统一);补一条
  「北京 T+1 凌晨创建的决策不得出现在 T 日回放」的单测(现有
  `test_attach_exec_hints_c3_does_not_look_ahead_past_trade_date` 只锁到日粒度,锁不住时区缝)。

### 🟡-3 `POST /inquiry` 响应的 `inquiryId` 在 Swift 解码段断链

- **证据**:契约清单明确登记该字段([`PROJECT_PLAN.md:674`]:「`POST /inquiry` 响应加 `inquiryId:Int?`」),
  `APIClient.swift:32` 头注释也写着「v1.4-⑦-B 带 inquiryId」;但私有 DTO `InquiryResponse`
  ([`client/Neckline/Networking/APIClient.swift:200-207`])与展示模型 `InquiryResult`
  ([`client/Models.swift:1320-1326`])**都没有该字段**——服务端→线上 JSON 三段都在,第四段(Swift 解码)把它丢了。
- **影响界定**:不崩(Codable 忽略未知键);历史列表走 `GET /inquiries` 不依赖它。但「问完即关联/跳转本次档案」类
  功能没有原料,且这正是本项目三次栽过的「链路末段漏字段」复发型形态(只是方向反过来:这次漏在客户端)。
- **修复方向**:`InquiryResponse`/`InquiryResult` 补 `inquiryId: Int?`(⑧-G 展示随后接);或在契约清单该条显式登记
  「客户端本版刻意不解码」。同时修正 `APIClient.swift:32` 注释与实现不符。

---

## 二、🟢 观察(放行,登记备查)

### 🟢-4 `A4_base_hygiene` 永不产命中 × forced 豁免叠加:forced 票的卫生线违规无 K4 标注

`_evaluate_hits` 不产 A4 命中(`load_k4_sections` docstring 已声明,候选侧由 ② 卫生线前置强制满足,
[`neckline/report/holding_k4_check.py:400-405`]);但 forced(问询强制)票**同时豁免 ② 和 hard_cut**
([`neckline/report/intel_candidates.py:467-475`]),于是一只 ST/低流动性 forced 票上榜时不会带任何
「未过卫生线」的 K4 标注(k4_flags 里没有 A4 这一码)。缓解:v1.3.3 起问询台不再自动写 `inquiry_pool`,
本地库实测 0 行待消费(forced 路径休眠);且该票在问询当时已经由 `discipline_checks` 披露过风险。
**若未来恢复海选池写入方,建议给 forced 票补一条卫生线标注**。

### 🟢-5 `_evaluate_hits` 对「无 EOD 行」的三消费点姿势不完全一致

持仓体检 `has_data=False` → 整份跳过(连题材类 A2/B3 也不判,[`holding_k4_check.py:599-603`]);
问询 `_k4_flags` 与候选管线在 `row=None` 时仍会判 A2/B3。实测影响≈0:问询侧该分支实际不可达
(研究面板查无该票时已提前 return,[`neckline/api/inquiry.py:247-253`]);候选侧命中被随后的
`if row is None: continue` 吞掉,仅 `hard_cut_codes` 诊断计数在极端数据不一致时可能偏差一票。放行,登记体例分叉。

### 🟢-6 Swift 完整信息卡 `InfoCard` 用合成 Codable(非 Optional 字段缺键即整卡解码失败)

依赖「`GET /report/{date}/info-card/{code}` 每次现算 + pydantic 全量序列化」这一分类(同 `DataFreshness` 注释
里的分类法),分类判断成立(端点确实现算不读冻结存档,[`neckline/api/app.py:471-499`]),放行。
**若未来该端点改读冻结快照,必须换手写容错 init(from:)**——同 `ReviewWeeklyResult` 的教训。

### 🟢-7 渲染层两处 `search_hits` 类型分叉(列表 vs 条数)有护栏

候选审判存全文列表 → `len(jr.search_hits or [])`([`render.py:218`]);自选体检只存条数 → 直接传
([`render.py:298`],`watchlist_check.py:365` 落的就是 `len(...)`)。`search_coverage_line` 内部
`int(hit_count or 0)` 强转兜底([`llm/base.py:70`])。放行。

---

## 三、A 路:K4 安检 vs DB `k4_advisory` 六节逐条对拍(查过什么、为什么放行)

真库 K4 行(`is_active=0`)六节全文已 dump 取证。逐条对拍结果:

| DB 节/码 | DB 规格档原文(expr/text) | 可执行镜像 | 判定 |
|---|---|---|---|
| hard_cut·A1 | `turnover_rate > 10` | `pl.col("turnover_rate") > _A1_TURNOVER_HI(=10.0)`,严格 `>` 对齐 [`holding_k4_check.py:104,248`] | ✅ 一致 |
| hard_cut·A2 | 行业强度(top20%中位数)连续≥4天成员 | `industry_strength.stock_persist_days ≥ _A2_PERSIST_MIN(=4)`;v1.4-② 起行业口径(一票一行业),board_age 代理已废 | ✅ 一致(evidence_strength=constituent,strong 但不推 APNs,守 §2.4) |
| hard_cut·A3 | `TREND_BELOW(ma250非空 & ~(close>ma250 & ma250_slope_up)) & is_limit_up` | `_trend_below_expr() & is_limit_up` [`holding_k4_check.py:204-209,249`] | ✅ 逐字一致 |
| hard_cut·A4 | `base_universe_expr() & days_since_listing≥120` | 不产命中;候选侧由 ② 卫生线前置强制满足(`base_universe_expr` + `forbid_new_stock(120)`,[`intel_candidates.py:398-404`]) | ✅ 设计如此,已在 docstring 声明(forced 叠加缝见 🟢-4) |
| avoid_flag·B1 | `cnt3≥2 & ret_1d≥5% & vol>vol_ma20×1.5` | `_big_red_expr() & ~trend_below & ~一字` [`holding_k4_check.py:229-238,251`];`≥5%`/`×1.5`/`cnt3≥2` 逐项对齐 | ✅(加 ~年线下/~一字两处刻意分叉,均有成段注释声明理由) |
| avoid_flag·B2 | MACD多头(DIF>DEA) & KDJ多头(K>D) | `state4=="①双金叉态"`,`_add_macd_kdj` 逐字镜像 research(12/26/9、9/3/3、warmup 34)[`holding_k4_check.py:259-291`] | ✅ 一致 |
| avoid_flag·B3 | 行业强度连续2-3天成员 | `2 ≤ persist_days ≤ 3` [`holding_k4_check.py:119,482`] | ✅ 一致 |
| avoid_flag·B4 | `close>ma20 & ret_1d>5%` | `(close>ma20) & (ret_1d > 0.05)`,严格 `>` 对齐 DB 的「>5%」[`holding_k4_check.py:120,253`] | ✅ 一致 |
| (合成)A3b | **不在 DB**;证据源=雷区地图 3-⑤(ret1d≥5%×量比≥2) | `_trend_below & _dispatch_bigred(vol_ratio_5≥2.0, 非一字)`;归 `_DEFAULT_SECTION="avoid_flag"`,**不计黄牌数**,evidence 用专用 `_A3B_EVIDENCE` 不落 DB 兜底 | ✅ 三处声明(docstring/常量注释/`describe_hits`)自洽;A3b(2.0) vs B1(1.5) 分叉有单测锁(`test_a3b_volume_ratio_threshold_2x_not_1p5`) |
| exec_hint·C1-C4 | 扁平 `{code:text}` 四条 | `_load_k4_exec_hint_texts` 读 DB、逐码 fallback + `source: db|fallback` 诚实标注;C2 复用 `info_card.MILD_BAND_RANGE=(0.02,0.03)` 单一源(DB 文字「2-3%」一致);C4 与 B4 数值巧合独立声明不复用 `_hit_B4` | ✅ 一致(C3 时区缝见 🟡-2) |
| circuit_breaker | 3 连止损 / 单日 -4000 | `sentinel/circuit.py::CIRCUIT_CONSECUTIVE_STOPS=3` / `CIRCUIT_DAILY_LOSS_YUAN=4000.0`(常量镜像 + docstring 声明「改须同改两处」) | ✅ 一致 |
| intel_order | 展示排序七码序列 | **零消费方** | 🟡-1 |
| note | 纯档案文字 | 无需消费 | ✅ |

**红牌真拦 / 黄牌只标**:候选管线 ③ `hard` 命中且非 forced → `continue` 出池([`intel_candidates.py:471-472`]);
avoid_flag 命中只进 `k4_flags` 打标保留。`yellow_card_count` **严格**只数 DB 显式登记为 avoid_flag 的码
(`.get(h.code)` 不带默认值,与拦截判定的 `.get(..., _DEFAULT_SECTION)` 刻意不同,注释成段声明,
[`intel_candidates.py:477-483`])——与 plan §五 v1.4-③-A「不数 hard_cut、不数合成码」逐字一致。

**三处单一源宣称(v1.4-②)验证属实**:持仓体检([`holding_k4_check.py:535-613`])、候选管线
([`intel_candidates.py:90-95,414-423,466`] 直接 import `_evaluate_hits`/`_build_holding_feature_panel`/
`load_k4_sections`/`_load_k4_evidence`,仅注入 bulk loader,一致性有 `test_bulk_and_percode_loaders_agree` 对拍)、
问询台([`api/inquiry.py:164-200`] 同四件套 import)——判据、阈值、evidence 文字、分区归属全部同一份;
题材持续天数三处统一 `industry_strength.stock_persist_days`(0=无/不达标,保守;rank None=未参与,`_sort_key`
None→+inf 排最后不当 0,有专测 `test_sort_key_no_industry_rank_sorts_last_not_zero`)。

**⑤ exec_hint 读 DB 路径 / 缺省兜底 / 两处同步声明**:`_load_k4_exec_hint_texts` 容忍 K4 行缺失/节缺失/类型异常
→ 空 dict → 逐码 `_FALLBACK_HINT_TEXT`(允许部分 DB 部分兜底,`source` 字段诚实标注);「改阈值须同改两处」的
镜像常量 `_C1_STRONG_RET`/`_C4_BIGRED_UP` 住模块头对照表,C2 阈值单一源在 `info_card.py`(有单测
`test_reuses_info_card_mild_band_range_not_reopened` 锁)。`test_output_text_changes_when_db_text_changes`
证明文字真读 DB 不抄常量。属实。

## 四、B 路:「诚实字段」四段链路(领域层 → schemas → _shape_report → Swift)

| 字段 | 领域层 | schemas.py | app.py 转发 | Swift 解码 | 判定 |
|---|---|---|---|---|---|
| `newsAlertsScan.codesSkipped/codesFailed/codesNoSearch` | `news_alerts.py:249-255` to_public_dict | `schemas.py:225-253` 逐一声明 | `_shape_report` 逐键显式读 [`app.py:409-424`] | `NewsAlertScanStatus` 手写容错 init [`Models.swift:625-682`] + DTO 单测 | ✅ |
| `rotationGroup/codesRotationDeferred`(⑥-B) | 同上(`**rot`) | 同上 | 同上 | 同上(fixture 断言 "A"/8) | ✅ |
| 搜索 0 命中脚注 | `search_coverage_line`(0 条显式说)[`llm/base.py:59-73`] | — | 报告 markdown 两处脚注 [`render.py:218,298`];问询 evidence [`inquiry.py:511`];计数走 codesNoSearch | evidence 列表原样展示 | ✅ |
| 板块资金流 `unavailableReason` | `sector_moneyflow.py:85-92`(降级路径 `empty_sector_moneyflow_report` 恒带 reason,候选管线保险丝亦然 [`intel_candidates.py:435-447`]) | `ReportOut.sectorMoneyflow` Dict 透传 | `rep.get("sector_moneyflow")` | `SectorMoneyflowSection` 七字段逐一吻合(空对象 `{}` 由 `try?` 归一 nil) | ✅ |
| 行业闸被挡票留痕 | `_blocked_no_industry` INFO 日志 + 漏斗五数 `_permanent_board_status` [`intel_candidates.py:372-385,577-631`] | `PermanentBoardStatusOut` | 经 `intelRank.permanentBoardStatus` 随快照 | `PermanentBoardStatus` 全字段 | ✅(暴起板块无逐板诊断——plan 只要求常驻板块,放行) |
| `dataFreshness`(板块三键 + 行业三键) | `sectors.py:68-69` + `industry_strength_store.py:122-126`,pipeline 合并落库 [`pipeline.py:379-382`],**两故障并列不合并** | `ReportOut.dataFreshness` Dict 透传(随报告冻住) | `rep.get("data_freshness")` | `DataFreshness`:板块键必填、行业键 Optional(老快照缺键→nil),`needsBanner` 任一成立;老报告 `{}` → `try?` → nil「该版本无新鲜度概念」不当新鲜 | ✅ |

## 五、C 路:v1.4 新契约三方对拍(契约清单 14 条逐条)

全部按「schemas 声明 → app.py 转发 → Models.swift/APIClient.swift 解码」三段走过:

1. **①-A `buyDate` + 400**:`_parse_buy_date_or_400` 校验顺序(格式→未来日→交易日)防 reason 说谎;缺省=今天逐位兼容;客户端 `.notTradingDay`/`.futureBuyDate` 独立 case。✅
2. **①-B `priceStale` / `suspended_hold`**:`price_stale.to_public_dict` 三键与 Swift `PriceStale` 吻合;`timeExitState` 第五态 Swift 枚举已识别(未识别兜 `.holding` 不误报离场);`k4DataUnavailable` 三值(true/false/null)从 `holding_store` 的 `data_unavailable` 列一路透传,null=老快照如实不知道。api 测试 8 条覆盖。✅
3. **①-C + ⑩-F `dataFreshness` 六键**:见 §四。✅
4. **③-E `IntelRankOut` 三新键**:`industryRank:Int?`(None 不当 0,两端注释+单测)/`industryPersistDays`(与 `themePersistDays` 同源同值双字段并存=刻意兼容)/`yellowCardCount`;Swift `IntelRank` 手写容错 init。✅
5. **④-B 信息卡**:`CandidateOut.infoCard` 摘要位 None=「暂不可用」不冒充空;`InfoCardOut` 全树逐字段与 Swift `InfoCard` 吻合(21 个字段逐一比过);`snapshot.industryPersistDays` Optional 化后两端一致(**null=没看 / 0=看了没有**,server `info_card.py:140-143` 与 Swift `Models.swift:262-264` 注释同语义,DTO 测试 `industryPersistDays null vs 0` 两向断言);404 双 reason 两端闭合。✅
6. **⑤-A `execHints`**:`ExecHintOut{code,text,source}` ↔ `attach_exec_hints` public_dict ↔ Swift `ExecHint`;老快照缺键 → `decodeIfPresent ?? []`。✅
7. **⑤-B `maxChasePct`**:服务端 `model_fields_set` 区分「缺键 400」vs「显式 null 合法」[`app.py:989-1005`];Swift 两个请求体手写 `encode(to:)`,唯独该键用 `encode`(nil→JSON null,**键永远出现**)、其余 Optional 仍 `encodeIfPresent` 保持原形状 [`APIClient.swift:290-343`]——两端语义互锁;create/revise 同纪律;`DecisionOut.maxChasePct` 回读;6 条 API 测试。✅
8. **⑥-C `timeExitLockedDay/LateDays`**:纯派生展示位,不参与判向(`resolve_time_exit` 先算完判向,标注后算,[`app.py:776-786`]);`locked_day` 由 `positions.d_count` 单一源换算,垃圾日期→None 不拿今天冒充;`late = max(0, day−maxHoldDays)`;api 测试 5 条(含断跑 lag、复牌晚定格)。✅
9. **⑥-A `charterSegments/charterSwitches`**:`weekly_review_dict` 键齐(`version/start(null=周初)/tradeCount`、`at/fromVersion/toVersion/note`)[`reconcile.py:1091-1107`];Swift `ReviewWeeklyResult` **手写容错 init**(冻结快照类,老周报缺三键→默认,分类判断正确)。判定语义本身归判定线审计,此处只审契约形状。✅
10. **⑥-B 轮扫披露**:见 §四。✅
11. **⑦-A `DecisionTrackOut`**:`status/planPrice/rows[{tradeDate,dOffset,close,retFromPlan?}]` 三段吻合;404 仅 decision 不存在,`rows=[]` 合法 200(「没攒到」≠「没这条」),`retFromPlan=null`=未设 plannedPrice 不臆造;DTO 测试 3 条含 404 case 复用断言。✅
12. **⑦-B `InquiryLogOut`**:`_row_to_inquiry_log` 键与 pydantic 模型逐一吻合;Swift `InquiryLogEntry` 刻意不解码 `materials/searchHits`(UI 不渲染,未知键天然跳过,已声明);`positionId/decisionId` 恒 null 契约清单已如实登记。**`inquiryId` 客户端断链见 🟡-3**。
13. **404/400 reason 全集互为闭包**:服务端会返的 reason 全集 = {`not_holding`, `not_found`(×9 端点), `report_not_found`, `code_not_in_report`, `not_trading_day`, `future_buy_date`, `max_chase_required`} → `mapReason` 每个都有独立 case;客户端每个 case 服务端都真会返(无死 case,v1.3.3 死 case `.reject/.pass` 已在 ⑦-C 清理);422 族(`watchlist_full`/`invalid_code`/`board_not_found`+unresolved 拼接/`scenario_index_out_of_range`)统一走 `.validation(reasonString)`,`board_not_found` 的 unresolved 拼接是纯附加不破既有。降级 200 reason(`no_report`/`bad_date`)走 `degraded` 契约非异常。✅
14. **撞名坑复查**:`distToStopPctServer = "distToStopPct"` CodingKeys 改名仍在 [`Models.swift:917`],旧计算属性语义未动。✅

**「快照 vs 现算」容错分类纪律抽查**:冻结快照类(`ReviewWeeklyResult`)手写容错 ✅;多次演进的响应类
(`IntelRank`/`NewsAlertScanStatus`/`Candidate`/`Position`)手写容错 ✅;每次现算全量序列化类
(`InfoCard`/`DecisionTrack`/`InquiryLogEntry`/`InfoCardSummary` 内层)合成 Codable ✅——分类判断均成立(见 🟢-6 的前提条件)。

**两份施工对拍报告抽查**:A2/B3 口径切换(旧 375→新 11,行业 vs 概念板块口径)与代码现状一致
(`stock_persist_days` 一票一行业、无 max-over-boards);排序键切换对照的方法声明(monkeypatch `_sort_key`、
universe/hard_cut 两次调用必然一致、375→11 是 ② 的效应不记入 ③)与 `intel_candidates.py` 结构吻合,
未发现结论与代码矛盾。未复跑数值(需 2026-07-24 面板全量重算,与抽查目的不成比例)。

## 六、建议处置顺序

1. 🟡-2(时区缝)——修法小(截断处转北京日期 + 一条单测),且顺手统一系统内时区口径,建议随 ⑩ 后的下一个快修批。
2. 🟡-3(inquiryId)——客户端两行 + 注释修正,归 ⑧-G 收尾。
3. 🟡-1(intel_order)——先在 plan 拍板「消费 or 登记不消费」,再定是否施工。
4. 🟢-4 —— 只在未来恢复海选池写入方时处理,现在只需记住。

> 高危区提醒:本报告范围不含 ⑥-A 纪律判定 / ⑩ 递推正确性 / P2-10 已发布版本(判定线审计员负责)。两份报告出齐后建议主会话取并集再定 ⑨ 上云。
