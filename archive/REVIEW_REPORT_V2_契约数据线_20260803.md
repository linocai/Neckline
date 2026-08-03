# REVIEW REPORT · V2.0.0 ①–⑬ 契约/数据线独立审计(2026-08-03)

- **审计人**:@reviewer(契约/数据线;判定线由另一位 reviewer 并行,第〇原则四锁 / 选股判定语义 / 哨兵纪律边界不在本报告范围)。
- **对照件**:PROJECT_PLAN.md §五 V2-①~⑬ 规格与完工记录、跨块裁定(篮子四表落库次序)、D1–D8、§五铁律;项目 CLAUDE.md。
- **方法**:从零读代码不信完工自述;21 张新表逐张对 DDL 与写入路径;守门单测逐条试绕(含一个真实复现脚本,见附录);全量路由/404 reason/DTO 逐个盘点;⑬ 十三项逐项残留取证 + 被删测试的防线陪葬排查;真实 `data/neckline.db` 与生产零接触,验证全部走 scratchpad 临时库。
- **基线复核**:本机实跑 `python -m pytest tests/ -q` = **2706 passed + 2 skipped + 0 failed**,与交办基线一致。

**分级统计:🔴 1 · 🟡 7 · 🔵 8 · 🟢 10**

---

## 🔴 致命(1)

### R1 · `basket_members` 冻结泄漏:同日重跑成员集变化时,新成员被**实际写入**冻结篮,而披露文案说「本次重算结果未采纳」

- **位置**:`neckline/selection/basket_store.py:146-162`(`_save_baskets_on_conn` 成员写入循环)。
- **机理**:重跑时 `baskets` 行 `INSERT OR IGNORE` 命中既有行(rowcount=0,幂等跳过 ✅),但随后的成员循环**不区分「篮子是新插的」还是「篮子早已冻结」**,对每个成员照走 `INSERT OR IGNORE INTO basket_members`——既有成员被 UNIQUE(basket_id, ts_code) 挡住,**新出现的成员码直接插进冻结篮**。`frozen_conflicts` 只是事后比对留痕,拦不住写入。
- **复现**(隔离临时库实测,脚本见附录 A):首跑成员 `{600001, 600002}` → 重跑成员 `{600001, 600002, 600003}` → stats 显示 `members_inserted=1`,库里冻结篮成员变成三只;同时 WARNING 打出「今日已有冻结篮子,本次重算结果**未采纳**(差异 1 处)」——**披露与事实相反**(采纳了一半)。
- **触发条件现实存在**:同一 D0 重跑 + LLM 实调时成员选择非确定(同名篮子 → 同 `driver_slug` → 同 `basket_key`,成员集却常不同),这正是 ④ 种子顺序快修之后仍然存在的非确定源。
- **影响面**:`basket_members` 是 D0 冻结件,五路下游直接消费——⑧ 关注池(`load_baskets_for_date.member_codes`)、⑨ 复盘面板(`eval/metrics.load_basket_panel`)、⑫ 能力画像「同篮未选成员」对照、⑬-N 信息卡 `build_basket_context`、⑩ `find_source_basket_member`。泄漏成员会以「D0 判断的一员」身份进入盘中关注、复盘归因、画像对照与开仓来源关联,而它从未被 D0 定档时采纳;卡上的成员节(冻结 card_json)与表里的成员集从此不一致。
- **守门为何没抓到**:`tests/test_selection_tier.py` 的重跑单测只断言 `baskets.tier` 不变 + `frozen_conflicts` 非空,**没有断言成员集逐位不变**;三律守门(`test_v2_schema_guard.py`)只盯 UPDATE/DELETE,不盯「向冻结聚合追加子行」。
- **修法**:`_save_baskets_on_conn` 里,当 `baskets` 行已存在(`cur.rowcount == 0`)时**整段跳过该篮成员 INSERT**,只做比对入 `frozen_conflicts`;补守门单测「重跑成员集扩/换 → 库里成员集逐位不变 + 冲突如实披露」。修完把披露文案里的「未采纳」才真正成立。

---

## 🟡 重要(7)

### Y1 · 三律/停写守门的写法盲区成套:`INSERT OR REPLACE` 全线漏网 + `scripts/` 不在扫描域

- **位置**:`tests/test_v2_schema_guard.py:132-137`(冻结四短语)与 `:154-213`(追加 AST 扫描);`tests/test_v1_retirement_guard.py:74`(停写六表 forbidden 前缀)。
- **三个洞**:
  1. 冻结守门是四条纯文本短语(`UPDATE basket_cards` / `UPDATE entry_snapshots` / `DELETE FROM basket_cards` / `DELETE FROM entry_snapshots`)。**`INSERT OR REPLACE INTO basket_cards`(= 先删后插,绕 UNIQUE 冻结的第一写法)不在短语集内**,大小写变体也不拦。今天仓里没有这种写法,但仓里现役 `INSERT OR REPLACE` 手法有十余处(scan/stage/retreat 等合法处),抄错一个表名守门全程沉默。
  2. 停写六表守门 `_write_sql_hits` 的 forbidden 只有 `INSERT INTO {t}` / `UPDATE {t}` / `DELETE FROM {t}` 三前缀——**`INSERT OR REPLACE INTO <停写表>` / `INSERT OR IGNORE INTO <停写表>` 都不命中**(字符串是 `INSERT OR REPLACE INTO t`,不含子串 `INSERT INTO t`)。
  3. 两套扫描都只罩 `neckline/`,不罩 `scripts/`——`scripts/smoke_basket_verify.py:180` 已真实存在一条 `DELETE FROM basket_cards`(冒烟脚本、隔离库,但它证明该写法在扫描域外自由生长)。
- **现存兜底**:`test_v1_retirement_guard.py:410-431` 跑完整管线后断言六张停写表行数不增——这条能抓动态 SQL 与 OR REPLACE,但只覆盖「管线会路过的路径」。
- **修法**:① 冻结短语集补 `INSERT OR REPLACE INTO` / `REPLACE INTO` 两个前缀(对两张冻结表);② `_write_sql_hits` 的 forbidden 对停写表改成「任何 INSERT 变体 + UPDATE + DELETE」;③ 把 `scripts/`(至少非 oneoff)纳入两套扫描,smoke 脚本如需破例用显式豁免清单登记。

### Y2 · `user_actions.occurred_at` 时区口径与 DDL 相悖,混时区字符串会破坏排序与过滤

- **位置**:`neckline/db.py:847`(DDL 注释「ISO8601 北京时间」)vs `neckline/user_actions.py:44,56`(`record()` 缺省落 **UTC** `+00:00`);`user_actions.py:97-105`(`since`/`until` 与 `ORDER BY occurred_at` 都是**字符串比较**)。
- **问题**:当前全部写入方(buy/sell/label/voice_note/alert)都走 UTC 缺省,暂时同质;但契约注释承诺北京时间,⑮ 客户端上报 `view` 事件几乎必然按北京时间传 `occurred_at`——同一时刻的 `…T01:00:00+00:00` 与 `…T09:00:00+08:00` 字符串排序不等价,`list_actions` 的时间序与窗口过滤在时间轴上错乱,⑫ 画像若日后引用 occurred_at 窗口同样中招。
- **修法**:`record()` 缺省改 `datetime.now(CN_TZ)`(与 `basket_verify_store.observed_at_now` 同源),并对显式传入值归一化到同一时区后再落库;或改 DDL 注释统一 UTC——两者选一,**不能一边承诺一边另落**。

### Y3 · 画像「每期一版」可退化成两次计算的嵌合体

- **位置**:`neckline/profile/store.py:40-49`(preference)/ `:82-95`(capability)。
- **问题**:UPSERT(`ON CONFLICT DO UPDATE`)只覆盖**同键**行;同一 `as_of_date` 重算时,凡是新一轮不再产出的 `(dimension, value)` 旧行**原样残留**,与新行混在同一期里(`computed_at` 不同但 `load_*` 不区分)。「每期一版」变成「每期 = 多次运行的并集」——例:上午跑画像后用户又补录两笔,傍晚重跑,某个占比归零的题材值仍以旧 share 挂在当期里,share 合计 > 1。
- **修法**:两表非冻结件(plan 三律=「每期一版」),`save_*` 在同一事务里先 `DELETE FROM … WHERE as_of_date=?`(或按 dimension)再插;或读侧只取该期 `computed_at` 最大的一批。前者更贴「每期一版」语义。

### Y4 · ⑬-1 契约层与客户端仍有五常驻残留(含一块「永远等不到」的僵尸卡)

- **位置**:`neckline/api/schemas.py:30`(`PermanentBoardStatusOut` 整个 DTO)、`:65`(`IntelRankOut.permanentBoardStatus`)、`:1001`(仍在 `__all__`);`client/Neckline/Views/IntelSectionView.swift:29-63`(`permanentBoardsCard` 仍挂在情报节 body 上)。
- **问题**:⑬-1 验收是「十三项逐项 grep 零残余」。实现层删干净了,但契约 DTO 与客户端渲染件整套还在;⑬-1 后新报告 `candidates` 恒空,该卡片将**稳定**显示「暂无候选可显示常驻板块状态(今晚 16:35 报告后可见)」——一句永远兑现不了的承诺,另一分支文案还指向已删除的 `/settings/intel-boards`。守门(`test_v1_retirement_guard.py:104-111`)只断言了 settings_store 符号与端点,没罩 DTO 与客户端卡片,所以全绿。
- **修法**:若按 D2=A 路留给 ⑮ 一次性换血,也应现在就把这两处写进 ⑭-B/⑮ 的必办清单并加守门(断言 `PermanentBoardStatusOut` 不在 schemas、`permanentBoardsCard` 不在 client);最起码先把僵尸文案改成如实的「该栏目已随 V1 候选榜退役」。

### Y5 · 客户端仍有五个已删端点的活调用(含向已删端点发送明文 apiKey 的请求体)

> **✅ 2026-08-03 planner 已归档排期**:五处逐个列进 **§五 ⑮ 的硬清单**(`SettingsLLMRequest` 整个退役、换 `/settings/providers*`),并按本条修法把 **「客户端调用面 ⊆ 服务端路由面」做成 ⑭-C 三方对拍的一条机器断言**防复发。Plan 里额外点明了本条最糟的那一面:**采集了用户 key、发到不存在的端点、界面还是一副成功的样子 = 假成功面 + 明文密钥打进空洞**。

- **位置**:`client/Neckline/Networking/APIClient.swift:535`(`POST /decisions/{id}/link`)、`:542`(`cancel`)、`:560`(`revise`)、`:569`(`scenario-outcome`)、`:616`(`PUT /settings/llm`,请求体 `SettingsLLMRequest` 含明文 `apiKey`,定义 `:210`,调用点 `client/Neckline/App/AppModel.swift:857`)。
- **定性**:D2=A 路裁定下「老 App 打老机」是合法过渡态,⑮ 才换客户端——**但当前仓库构建出的客户端对 V2 服务端是五处 404/静默失败**,其中 LLM 设置路径还是「采集了 key、发到不存在的端点」的假成功面。⑬-5 只删了表单必填分支,没有处置这些调用面。
- **修法**:登记进 ⑮ 的硬清单(逐个删调用点 + `SettingsLLMRequest` 一并退役,换 `/settings/providers*`);⑭-B 三方对拍时把「客户端调用面 ⊆ 服务端路由面」做成对拍脚本的一条断言,防再漂。

### Y6 · ⑬ 验收交付物缺一件:「报告与端点删除前/删除后对照表」没有落 archive/

> **✅ 2026-08-03 planner 已归档排期**:在 **§五 ⑬ 验收之前**加了一条欠交付告示 —— **排进本轮 review 修复批次,或最迟 ⑭ 开工前补**;并写明 ⛔ **不许并进 ⑭ 自己的工作量**(那时基准已不好重建)。采纳本条「趁删除提交还新鲜」的理由。

- **证据**:⑬ 验收原文「报告与端点『删除前 / 删除后』对照表落 `archive/`(⑭ 的三方对拍要用)」;`archive/` 目录最新文件停在 20260802,无任何 ⑬ 对照表;⑬ 完工记录也未提及此件。
- **影响**:⑭ 三方对拍(老报告 vs 新报告 vs 契约)的基准输入缺席,届时只能靠 git 考古重建删除前形状。
- **修法**:趁 ⑬ 的删除提交还新鲜(`1161441`/`1a318db`/`01d04c2`),补一份对照表归档;成本远低于 ⑭ 开工时再考古。

### Y7 · `record_buy` 三段核心写入无事务、`POST /positions` 无幂等键:失败重试会开出第二笔仓

- **位置**:`neckline/positions_entry.py:517-532`(`open_position` → `freeze_entry_snapshot` → `create_position_plan_v1` 三个独立连接、无共同事务);`neckline/api/app.py:962-1000`。
- **问题**:保险丝只包了「快照内容丰富度」子项;三个**核心写入本身**是串行独立提交。`open_position` 成功后任何一步抛异常 → API 500,但持仓已落库;客户端按 500 重试 = **重复开仓**(POST 无幂等键,positions 表也没有防重约束)。同时留下「持仓无 entry_snapshot / 无 plan v1」中间态,与 ⑩ 验收「开仓即有冻结行」的隐含预期不符,`create_position_plan_version` 见到无 v1 会直接 ValueError。
- **修法**:三写并入同一个 `with connection()`(`freeze_entry_snapshot`/`create_position_plan_v1` 照 `basket_store` 体例加可选 `conn=`);`user_actions` 记账留在事务外维持 best-effort。

---

## 🔵 建议(8)

### B1 · `save_baskets` / `save_tier_history` 独立入口静默丢弃 `frozen_conflicts`
`neckline/selection/basket_store.py:199-210, 300-306`:两个独立入口把 `_conflicts` 直接丢弃(成员集冲突在该路径下连 WARNING 都没有,只有「幂等跳过」这条泛化警告);只有 `save_tier_decision` 披露。docstring 已标「正常路径应走 save_tier_decision」,但独立入口是导出的公开函数。建议:独立路径也把 conflicts 打 WARNING 并放进返回值。

### B2 · 存拍 `cum_volume/cum_amount` 用 `or 0.0` 把「源没给」写成 0
`neckline/sentinel/capture.py:168-169`:`cum_v = _f(...) or 0.0`——源缺累计量(None)时落 0.0,与模块自己「累计值原样落」的承诺及「没有 ≠ 没看」纪律相悖,还把下一拍的增量基线焊死在 0。增量列(d_v/d_a)的 null 纪律做对了,累计列漏了同一课。建议:None 原样落 null,且该码不进 `last_cum` 基线。

### B3 · `selection_packs` 单现役无 DB 级约束,多现役时读侧静默择新
无 `is_active=1` 的 partial unique index;`pack.py:398-401` 的 `get_active_pack` 在(仅可能由手工 SQL 造成的)多现役行下按 `created_at DESC` 静默取一行、不告警,`activate_pack:480-489` 也只 deactivate 一行。建议:`_SCHEMA` 补 `CREATE UNIQUE INDEX IF NOT EXISTS … ON selection_packs(is_active) WHERE is_active=1`,读侧遇 >1 行打 WARNING。

### B4 · `scripts/oneoff/` 两个留档脚本已断 import(运行即 ImportError)
`compare_intel_sort_key_switch.py:39-40`(import 已删的 `intel_candidates`/`report.candidates`)、`compare_a2b3_industry_switch.py:26`。留档定位没问题,但「留档审计用」的脚本如今跑不起来。建议:文件头补一行「⑬ 后依赖已删,仅供阅读」,或挪 `archive/`。

### B5 · `exec_hints_for()` 零生产调用方,守门只查存在性不查接线
`neckline/report/exec_hint.py:224`:C1–C4 四条判定完好、11 条单测供养,但 `neckline/` 与 `scripts/` 内零调用(⑬ 完工记录已如实登记留 ⑭-A)。风险在于 `test_v1_retirement_guard.py:172-179` 只断言 `hasattr`,⑭ 若忘接线不会有任何测试变红。建议:⑭-A 完工判据里点名「exec_hints_for 有生产调用点」并加一条接线守门。

### B6 · 两处测试防线随 ⑬ 删除而变薄(功能没丢,断言丢了)
① 原 `test_candidates.py::test_invalidation_spec_and_text_consistent`(spec 与人话文案一致性)未随 spec 搬进 `sentinel/invalidation.py` 重建——阈值行为在 `test_sentinel_invalidation.py` 有边界锚(0.6<0.8 触发等,构造走真 `invalidation_spec()`,数值仍被间接钉住),但 spec 形状/文案一致性无断言。② `discipline_checks` 的行为覆盖从原 `test_watchlist_check.py` 约 11 例正负分支塌缩到「*ST 进 risk_flags」一例 + 两条函数同一性守门(`tests/test_api_inquiry.py:84-88,599-619`)。建议:补一个小型 `test_discipline_checks.py`(每条硬线正负各一)。

### B7 · ⑭-B 契约总装清单(小口径杂项打包)
- `GET /alerts` 查询参数名是 `status_filter`(Python 形参名直接漏成契约键,`app.py:1413`),与全仓 camelCase 取向不合;
- 客户端 `mapReason` 的 `max_chase_required` case 已成死码(服务端零 raise 点,`APIClient.swift:803`),`reasonString` 的 `unresolved` 拼接机制空转(`:814-816`);
- `PositionOpenOut` 蛇驼混排(`schemas.py:425-442`,自认既有);`PositionOpenIn`/`PositionCloseIn` 同病;
- `card_not_ready`/`basket_not_found` 两个 reason 目前只活在注释与 200 内嵌字段里,⑭-B 建 `/baskets/{id}/card` 时客户端 `mapReason` 必须加 case(404 fallback 是 `.notHolding`,新 reason 不加 case 会显示成「持仓已清」——CLAUDE.md 已有案底)。

### B8 · 两处措辞/注释与实现出入(行为本身无错)
- `neckline/sentinel/__init__.py:17` 仍写「entry.py …… 四哨兵判定」,该文件已删;
- ⑬ 完工记录称 `missedEntryHint`「保留但恒空」——准确口径是「**新数据下恒空,历史日期回放仍会非空**」(`pipeline.py:95` 读 `sentinel_events` 历史 `entry` 行;与 `ReportOut.candidates` 的历史回放语义同类,合理,但措辞该改)。

---

## 🟢 核过为好(10)

1. **21 张表 DDL 与 plan 逐字一致**:`db.py::_SCHEMA` 的 18+1 张 SQLite 新表列名/约束/注释与 §V2-① 及 ④b 的 DDL 定稿一致;`app_settings` 两新列走 `_COLUMN_MIGRATIONS`;各 store 的 INSERT 列清单与 DDL 逐列对上(`_BASKET_COLUMNS` 12 列等)。
2. **parquet 声明齐**:`intraday_ticks`/`auction_snapshots` 进 `_VALID_TABLES` + `TABLE_FLOAT_COLS`(`market_data.py:50-51,125-126`),写读往返 dtype + 全空列不漂 String 守门在(`test_v2_schema_guard.py:358-429`);`trade_date` 落 `pl.Date`(⑧ 更正后口径)。
3. **事务 1 回滚成立**:`connection()`(`db.py:1210-1217`)只在正常退出 commit,异常路径 close 即弃;`save_tier_decision` 三表同 `with`,单测演练过 tier_history 失败三表零行。
4. **事务 2 与「有篮子无卡」中间态**:卡生成在 LLM 之后、独立事务;读侧(`positions_entry` 内嵌 `card_not_ready`、`basket_store.load_basket_card` 返 None、⑧ 落 `unclear+no_card`)都能表达;`GET /baskets/{id}/card` 尚未建与 plan(⑭-B)一致,不是漏。
5. **`basket_verification` append-only 全套与 ⑧-C2 逐条相符**:store 只有 INSERT;`falsified` 当日定格 + EOD 必落 + `latched_over` 审计留痕 + 「当前状态」三路读法唯一实现(`basket_verify_store.py:141-206`)。
6. **`selection_packs` append-only + 激活闸**:内容篡改拒绝(逐字节比对)先于「已现役」快捷退出;activation_log 纯 INSERT;演练零写库、切换两事件/首激活一事件均有单测。`llm_judgments` 等停写六表:`neckline/` 与 `scripts/` 全域零写入方,六表 DDL 停写注释齐,整管线行数不增兜底测试在(`test_v1_retirement_guard.py:410`)。
7. **api_key 零泄露面**:`ProviderOut`/`SettingsProviderOut` 只有 `keySet`;三条构造路径逐字段手写,`api/` 目录零 `model_dump`/`.dict()`/`__dict__` 透传;入参方向的明文 `apiKey` 属正常写入。
8. **`model_fields_set` 两处使用姿势正确**(`app.py:1300` PUT provider、`:1463` PUT alert,均为「键缺失 vs 显式 null」判别,合 v1.4-⑤ 体例)。
9. **十个删除端点服务端零残留**(watchlist×5、breathing×3、intel-boards、PUT /settings/llm、decisions 四写端点),路由面唯一(无第二个 router),守门断言在;404 reason 面与登记一致(info-card 复用 `code_not_in_report` 系 docstring 明示的刻意决定)。
10. **⑬ 防线迁移四件到位**:`discipline_checks` 搬家且与问询台同一函数对象;`json_block.py` 存活且五方消费;`board_pool.py` 保留 + 五消费方 + 反向守门(plan 笔误已由守门单测自纠);`sentinel/invalidation.py` 三常量与 spec 搬家、universe/engine 两消费方在。画像方向性守门(`selection/`/`scan/` 零 `profile` 引用)在。

---

## 附录 A · R1 复现脚本(scratchpad 已实跑,输出节选)

```python
# 隔离临时库;BasketCandidate 同 key 两次落库,第二次成员集多一只 600003.SH
s1 = bs.save_tier_decision(result([mem('600001.SH'), mem('600002.SH')]), ...)
# run1 {'baskets_inserted': 1, 'members_inserted': 2}
s2 = bs.save_tier_decision(result([mem('600001.SH'), mem('600002.SH'), mem('600003.SH')]), ...)
# WARNING: 今日已有冻结篮子,本次重算结果**未采纳**(差异 1 处)…
# run2 {'baskets_inserted': 0, 'baskets_existing': 1, 'members_inserted': 1}   ← 实际采纳了
# SELECT ts_code FROM basket_members → ['600001.SH', '600002.SH', '600003.SH']
```

## 附录 B · 审计覆盖范围声明

- 覆盖:三律与守门可绕性(21 表)、落库事务与次序(事务 1/2、frozen_conflicts、单现役、画像每期一版)、API 契约面(路由全量、404 reason、camel/snake、model_fields_set、key 泄露)、⑬ 拆除完整性(残留 import、停写表写入方、防线迁移、被删测试陪葬)、schema 与声明一致性(DDL vs 写入列、parquet 声明、包校验盲区)。
- 不覆盖(判定线 reviewer 负责):第〇原则四锁、选股判定语义(五维分/质量线/种子判据)、哨兵纪律边界、验证条件集的策略正确性。
- 已按登记口径放行、不计问题的项:包校验对 `dims` 拼写错误只在运行期 fail loud(③-K7 登记)、`quality_lines` 单调性只比字面键(⑥-b 登记)、`news_scan.py` 缺 `prompt_context` 豁免名单(V2-② 挂账)、`evidence_status` 编排住 ⑤ 不住 `llm/`(V2-② 登记)。
