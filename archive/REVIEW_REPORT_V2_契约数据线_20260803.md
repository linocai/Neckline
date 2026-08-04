# REVIEW REPORT · V2.0.0 ①–⑬ 契约/数据线独立审计(2026-08-03)

- **审计人**:@reviewer(契约/数据线;判定线由另一位 reviewer 并行,第〇原则四锁 / 选股判定语义 / 哨兵纪律边界不在本报告范围)。
- **对照件**:PROJECT_PLAN.md §五 V2-①~⑬ 规格与完工记录、跨块裁定(篮子四表落库次序)、D1–D8、§五铁律;项目 CLAUDE.md。
- **方法**:从零读代码不信完工自述;21 张新表逐张对 DDL 与写入路径;守门单测逐条试绕(含一个真实复现脚本,见附录);全量路由/404 reason/DTO 逐个盘点;⑬ 十三项逐项残留取证 + 被删测试的防线陪葬排查;真实 `data/neckline.db` 与生产零接触,验证全部走 scratchpad 临时库。
- **基线复核**:本机实跑 `python -m pytest tests/ -q` = **2706 passed + 2 skipped + 0 failed**,与交办基线一致。

**分级统计:🔴 1 · 🟡 7 · 🔵 8 · 🟢 10**

> **销项状态(@builder-pro 修复批次,2026-08-03)**:🔴 R1 ✅ · 🟡 Y1/Y2/Y3/Y4/Y7 ✅ ·
> 🔵 B1/B3 ✅ · Y6 ✅(对照表已补档)。**未修(本批次范围外,已登记)**:🟡 Y5(⑮ 硬清单)、
> 🔵 B2/B4/B5/B6/B7/B8。逐条标注见各条目下的 `✅ 已修` 行。
>
> **✅ 第二批销项(@builder-pro,2026-08-04 A 组,⑰ 割接前的次级修理批次)**:
> **🔵 B2 ✅**(`8c3e650`)· **B4 ✅ / B5 ✅ / B8 ✅**(`4c00976`)· **B6 ✅**(`65abbf2`)。
> **🟡 Y5 已由 ⑮ 兑现**(planner `199521f` 的 A10 账本核验实查:五处活调用现已全部清零,
> 只剩注释复述;本报告原文是审计当时的事实)。**🔵 B7 未修,维持开放** —— A 组九项清单
> 没列它;其中三小项 planner 已核实**事实上已修**(`status` 参数名两侧一致 / 双 404 case
> 已落 / 幂等键语义已写进客户端注释),剩下的是 `PositionOpenIn|Out` 蛇驼混排(自认既有)
> 与 `max_chase_required` 死码两条纯整洁项,建议下批顺手。

> **✅ 复核销项(@reviewer 契约/数据线,2026-08-03 第二轮,独立重放不信修复自述)**:
> **全部已修项复核通过,零打回**。逐条判据:
> - **R1 ✅ 验证通过**:原报告附录 A 复现路径重放(隔离临时库)——重跑扩成员 / 重跑换成员
>   两向 `members_inserted=0`、冻结成员集逐位原样;独立入口 `save_baskets` 同一路径同样不写
>   且 stats 带 `frozen_conflicts`(B1 一并验证);披露文案「未采纳 + 冻结成员集原样保留」
>   与事实一致,不再撒谎。diff 审读:`basket_is_new` 分支干净,「父行冻结 + 子行另表」的
>   通用规则写进了模块头。
> - **Y1 ✅ 探针通过(9/9)**:亲手造九种违规写法喂给三套守门(monkeypatch 扫描域,探针不
>   入仓)——冻结表 `INSERT OR REPLACE`/小写 `replace into`、追加表小写 `UPDATE`/`DELETE`/
>   `OR REPLACE`、停写表 `OR IGNORE`/小写 `or replace`/裸 `REPLACE INTO`/`UPDATE`,**全部命中**。
>   builder 自发现的第 4 洞(禁止串小写 vs `sql.upper()` 永不相等 = 追加表守门从上线起零命中)
>   属实且已修——这条印证了本报告 Y1 的原判:全绿从来不是「守住了」的证据。
> - **Y2 ✅ 重放通过**:同一时刻 `+00:00`/`+08:00`/naive 三种写法落库后归一为北京时间、
>   `list_actions` 排序与 `since/until` 窗口按真实时间轴走;非法输入 `ValueError` fail loud。
>   「occurred_at 北京 / created_at UTC 两列刻意不同轴」的定案与 v1.4-⑥ 时间轴纪律一致,认可。
> - **Y3 ✅ 重放通过**:同期重算后消失的取值真消失、share 归一;空批次整期清空。按 dimension
>   替换的粒度选择(share 是维度内归一)比本报告原建议的整期替换更准,认可。
> - **Y4 ✅**:DTO/`__all__`/`IntelRankOut` 字段/客户端卡与 struct 全净,守门两侧齐;删键前
>   查过客户端是 `decodeIfPresent`(CLAUDE.md 两步淘汰纪律的正确豁免:非硬解码键可直删)。
> - **Y6 ✅**:`archive/V2-⑬_删除前后对照表_20260803.md` 已补,端点面 11 条逐路由(纠正完工
>   记录的 10 条计数)、带出处 commit、末节列 ⑭ 对拍注意项——比验收原文要求的更完整。
> - **Y7 ✅(增量审计通过)**:三写单事务 + `IntegrityError` 只在幂等键真命中时转重放、否则
>   照抛(不吞真写坏);重放只读冻结行不现查来源、`planDeviationNotice` 不重放的理由成立;
>   部分唯一索引(`WHERE idempotency_key IS NOT NULL`)不伤 CLI/历史补录;
>   `_POST_MIGRATION_INDEXES` 的取舍正确——依赖迁移列的索引进 `_SCHEMA` 会在老库上
>   "no such column",逐条 try/except `IntegrityError` 降级(WARNING + 继续启动)与 P0-23
>   「降级=不拦+显式披露」同向,⛔ 开机脚本无权替用户清数据这条边界划得对。
> - **B1/B3 ✅**:B1 已随 R1 验证;B3 库级部分唯一索引 + 读侧多行现役 WARNING + 确定性
>   tie-break(`created_at DESC, pack_version DESC`)齐。
> - **Y5 ⏸ 归档合理**:⑮ 硬清单已逐处列(PROJECT_PLAN:2512,含 `SettingsLLMRequest` 整体
>   退役)+ ⑭-C 机器断言「客户端调用面 ⊆ 服务端路由面」(:2515)防复发——归属与防线都在,认可。
> - **基线复核**:全量 `pytest tests/ -q` = **2801 passed + 2 skipped + 1 failed**,唯一红是
>   `test_sentinel_custom.py::test_cooldown_blocks_second_hit_and_expires`(已知挂钟脆弱:用例取
>   `now+700s` 越过 15:00 收盘,本次跑在 14:5x;已另行挂账),与销项批次无关。
>
> **复核期增量发现(三条观察,均不构成打回)**:
> - 🔵 **Y3 残留一角**:按 dimension 替换意味着「重算时某维度恰好产出 0 行(而其他维度有行)」
>   时,该维度旧行仍留在当期。数据面上 `user_actions`/`positions` 只增不减、同期重算样本单调
>   增长,该情形现实中几乎打不出来;但若日后画像口径改版(某维度整体停算),记得先清历史期
>   该维度行。建议在 `_clear_period` docstring 加一句边界说明即可,不需要代码改动。
> - 🔵 **`_POST_MIGRATION_INDEXES` 的降级窗口**:脏库上索引建不上时(WARNING + 跳过),幂等键
>   /单现役两条约束在该库上**持续不设防**直到人工清理——WARNING 只在 `init_schema` 时打,
>   常驻服务只在启动时看得见一次。可接受(方向对、有披露),但 ⑯ 迁移检查清单里应加一条
>   「新机首次 `init_schema` 后 grep 日志确认零条『唯一索引建不上』」。
> - 🔵 **幂等键的语义边界未写进契约注释**:同键不同 payload(客户端 bug 复用键)会静默重放
>   原仓且丢弃新参数——`replayed=true` 有透出,属标准幂等语义,但 ⑭-B 契约文档应写明
>   「键必须每笔新交易唯一生成,⛔ 不许复用」,客户端生成规则(如 UUID per 提交)归 ⑮。

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
- **✅ 已修**(commit `1052622`):照上述正解。另两处顺带收口:成员集比对不再要求 `frozen_members` 非空(冻结篮零成员 vs 本次算出非空同样是冲突);冲突文案补「本次结果未采纳,冻结成员集原样保留」。回归三条(重跑扩成员 / 重跑换成员 / 独立入口披露),复现路径照本报告附录 A。

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
- **✅ 已修**(commit `fdf604c`):三洞照正解全修,扫描域含 `scripts/oneoff/`(不需要豁免清单——`scripts/` 仅有的两条命中是 `INSERT INTO entry_snapshots`,冻结表的合法建行)。报告点名的那条 DELETE 按上下文改写:`smoke_basket_verify.py` 是活脚本,「有篮子无卡」改成**压根不发卡**(`_seed_basket(with_card=False)`),那本来就是该中间态的真实成因。
- **⚠ 报告未列的第 4 洞(施工期反向证伪打出来)**:追加表 AST 守门的禁止串是**小写表名**(`UPDATE user_actions`)、右边却是 `sql.upper()` —— `forbidden in upper` 两边永不相等,**这条守门从上线起一次都没可能命中**,是彻头彻尾的空转。已修(`forbidden.upper() in upper`)。教训与 🟡-5 锁空靶同类:守门写完必须反向造一条真违规验证它会红,「全绿」本身不构成「守住了」的证据。

### Y2 · `user_actions.occurred_at` 时区口径与 DDL 相悖,混时区字符串会破坏排序与过滤

- **位置**:`neckline/db.py:847`(DDL 注释「ISO8601 北京时间」)vs `neckline/user_actions.py:44,56`(`record()` 缺省落 **UTC** `+00:00`);`user_actions.py:97-105`(`since`/`until` 与 `ORDER BY occurred_at` 都是**字符串比较**)。
- **问题**:当前全部写入方(buy/sell/label/voice_note/alert)都走 UTC 缺省,暂时同质;但契约注释承诺北京时间,⑮ 客户端上报 `view` 事件几乎必然按北京时间传 `occurred_at`——同一时刻的 `…T01:00:00+00:00` 与 `…T09:00:00+08:00` 字符串排序不等价,`list_actions` 的时间序与窗口过滤在时间轴上错乱,⑫ 画像若日后引用 occurred_at 窗口同样中招。
- **修法**:`record()` 缺省改 `datetime.now(CN_TZ)`(与 `basket_verify_store.observed_at_now` 同源),并对显式传入值归一化到同一时区后再落库;或改 DDL 注释统一 UTC——两者选一,**不能一边承诺一边另落**。
- **✅ 已修**(commit `040bd27`):取「向 DDL 承诺看齐」这一路。`occurred_at` 缺省 = `datetime.now(CN_TZ)`(唯一源 `calendar.CN_TZ`);显式传入经新 `normalize_occurred_at()` 归一(带时区→换算 / naive→按北京时间读 / 解析不了→`ValueError` fail loud,⛔ 不原样落库);`list_actions` 的 `since`/`until` 走同一函数(窗口过滤是字符串比较,递进来 UTC 串会静默筛错时段)。`created_at` **保持 UTC**(审计戳,全仓 store 惯例)——两列不同轴是刻意的,DDL 与模块头两处都写死「别统一」。V2 表 0 行,直接改写侧、无数据迁移。

### Y3 · 画像「每期一版」可退化成两次计算的嵌合体

- **位置**:`neckline/profile/store.py:40-49`(preference)/ `:82-95`(capability)。
- **问题**:UPSERT(`ON CONFLICT DO UPDATE`)只覆盖**同键**行;同一 `as_of_date` 重算时,凡是新一轮不再产出的 `(dimension, value)` 旧行**原样残留**,与新行混在同一期里(`computed_at` 不同但 `load_*` 不区分)。「每期一版」变成「每期 = 多次运行的并集」——例:上午跑画像后用户又补录两笔,傍晚重跑,某个占比归零的题材值仍以旧 share 挂在当期里,share 合计 > 1。
- **修法**:两表非冻结件(plan 三律=「每期一版」),`save_*` 在同一事务里先 `DELETE FROM … WHERE as_of_date=?`(或按 dimension)再插;或读侧只取该期 `computed_at` 最大的一批。前者更贴「每期一版」语义。
- **✅ 已修**(commit `040bd27`):取**按 dimension** 那一路(`share` 是维度内归一,一致性单位就是维度;也不会顺手抹掉别的维度的当期结果)。空批次 = 这期真没算出东西 → 整期清空。回归四条(消失的取值真消失 + share 合计 ≤1 / 单维重算不牵连他维 / 空批清期 / 旧期不受牵连)。

### Y4 · ⑬-1 契约层与客户端仍有五常驻残留(含一块「永远等不到」的僵尸卡)

- **位置**:`neckline/api/schemas.py:30`(`PermanentBoardStatusOut` 整个 DTO)、`:65`(`IntelRankOut.permanentBoardStatus`)、`:1001`(仍在 `__all__`);`client/Neckline/Views/IntelSectionView.swift:29-63`(`permanentBoardsCard` 仍挂在情报节 body 上)。
- **问题**:⑬-1 验收是「十三项逐项 grep 零残余」。实现层删干净了,但契约 DTO 与客户端渲染件整套还在;⑬-1 后新报告 `candidates` 恒空,该卡片将**稳定**显示「暂无候选可显示常驻板块状态(今晚 16:35 报告后可见)」——一句永远兑现不了的承诺,另一分支文案还指向已删除的 `/settings/intel-boards`。守门(`test_v1_retirement_guard.py:104-111`)只断言了 settings_store 符号与端点,没罩 DTO 与客户端卡片,所以全绿。
- **修法**:若按 D2=A 路留给 ⑮ 一次性换血,也应现在就把这两处写进 ⑭-B/⑮ 的必办清单并加守门(断言 `PermanentBoardStatusOut` 不在 schemas、`permanentBoardsCard` 不在 client);最起码先把僵尸文案改成如实的「该栏目已随 V1 候选榜退役」。
- **✅ 已修**(commit `d9b41a1`):不留到 ⑮,**现在就删干净**——服务端删 DTO + `IntelRankOut.permanentBoardStatus` + `__all__` 条目;客户端拆整块 `permanentBoardsCard` + `PermanentBoardStatus` struct + `IntelRank` 四处接线。守门按建议加(契约 + 客户端两侧)。**删键安全性实查过**(CLAUDE.md「删键前先查客户端是不是硬解码」):该字段是 `decodeIfPresent ?? []`,不是 `try c.decode`,停发不会让老 App 解不出整份报告。老快照兼容两条(服务端库里存着该键也不再透出 / 客户端遇未知键照常解码)。双端 build + iOS Simulator 154 例通过。

### Y5 · 客户端仍有五个已删端点的活调用(含向已删端点发送明文 apiKey 的请求体)

> **✅ 2026-08-03 planner 已归档排期**:五处逐个列进 **§五 ⑮ 的硬清单**(`SettingsLLMRequest` 整个退役、换 `/settings/providers*`),并按本条修法把 **「客户端调用面 ⊆ 服务端路由面」做成 ⑭-C 三方对拍的一条机器断言**防复发。Plan 里额外点明了本条最糟的那一面:**采集了用户 key、发到不存在的端点、界面还是一副成功的样子 = 假成功面 + 明文密钥打进空洞**。

- **位置**:`client/Neckline/Networking/APIClient.swift:535`(`POST /decisions/{id}/link`)、`:542`(`cancel`)、`:560`(`revise`)、`:569`(`scenario-outcome`)、`:616`(`PUT /settings/llm`,请求体 `SettingsLLMRequest` 含明文 `apiKey`,定义 `:210`,调用点 `client/Neckline/App/AppModel.swift:857`)。
- **定性**:D2=A 路裁定下「老 App 打老机」是合法过渡态,⑮ 才换客户端——**但当前仓库构建出的客户端对 V2 服务端是五处 404/静默失败**,其中 LLM 设置路径还是「采集了 key、发到不存在的端点」的假成功面。⑬-5 只删了表单必填分支,没有处置这些调用面。
- **修法**:登记进 ⑮ 的硬清单(逐个删调用点 + `SettingsLLMRequest` 一并退役,换 `/settings/providers*`);⑭-B 三方对拍时把「客户端调用面 ⊆ 服务端路由面」做成对拍脚本的一条断言,防再漂。
- **⏸ 未修(本批次范围外)**:交办明示 Y5 归 ⑮ 硬清单,本次不动。

### Y6 · ⑬ 验收交付物缺一件:「报告与端点删除前/删除后对照表」没有落 archive/

> **✅ 2026-08-03 planner 已归档排期**:在 **§五 ⑬ 验收之前**加了一条欠交付告示 —— **排进本轮 review 修复批次,或最迟 ⑭ 开工前补**;并写明 ⛔ **不许并进 ⑭ 自己的工作量**(那时基准已不好重建)。采纳本条「趁删除提交还新鲜」的理由。

- **证据**:⑬ 验收原文「报告与端点『删除前 / 删除后』对照表落 `archive/`(⑭ 的三方对拍要用)」;`archive/` 目录最新文件停在 20260802,无任何 ⑬ 对照表;⑬ 完工记录也未提及此件。
- **影响**:⑭ 三方对拍(老报告 vs 新报告 vs 契约)的基准输入缺席,届时只能靠 git 考古重建删除前形状。
- **修法**:趁 ⑬ 的删除提交还新鲜(`1161441`/`1a318db`/`01d04c2`),补一份对照表归档;成本远低于 ⑭ 开工时再考古。
- **✅ 已补**:`archive/V2-⑬_删除前后对照表_20260803.md`(端点 10 条 + 报告字段/节 + 停写六表 + 客户端渲染件,逐条「删除前 → 删除后」+ 出处 commit)。

### Y7 · `record_buy` 三段核心写入无事务、`POST /positions` 无幂等键:失败重试会开出第二笔仓

- **位置**:`neckline/positions_entry.py:517-532`(`open_position` → `freeze_entry_snapshot` → `create_position_plan_v1` 三个独立连接、无共同事务);`neckline/api/app.py:962-1000`。
- **问题**:保险丝只包了「快照内容丰富度」子项;三个**核心写入本身**是串行独立提交。`open_position` 成功后任何一步抛异常 → API 500,但持仓已落库;客户端按 500 重试 = **重复开仓**(POST 无幂等键,positions 表也没有防重约束)。同时留下「持仓无 entry_snapshot / 无 plan v1」中间态,与 ⑩ 验收「开仓即有冻结行」的隐含预期不符,`create_position_plan_version` 见到无 v1 会直接 ValueError。
- **修法**:三写并入同一个 `with connection()`(`freeze_entry_snapshot`/`create_position_plan_v1` 照 `basket_store` 体例加可选 `conn=`);`user_actions` 记账留在事务外维持 best-effort。
- **✅ 已修**(commit `50ff5b2`):事务照正解(`open_position` 也加 `conn=`;复用连接时不再各自 `init_schema` —— 那会另开一条连接、在对方事务开着时抢写锁并自行提交 DDL)。幂等键另做:`PositionOpenIn.idempotencyKey` → `positions.idempotency_key` 迁移列 + **部分唯一索引**,`PositionOpenOut.replayed` 如实透出;两道闸(应用层查 = 快路径,库级约束 = 真闸,并发撞车时 `IntegrityError` 转重放而不是 500)。重放只读开仓当时冻结的 `entry_snapshots` + `position_plans` v1,⛔ 不现查来源篮子(那等于给同一笔仓编第二套来源);`planDeviationNotice` 刻意不重放(本次没有新成交)。老库迁移在模拟老库上实测过。

---

## 🔵 建议(8)

### B1 · `save_baskets` / `save_tier_history` 独立入口静默丢弃 `frozen_conflicts`
`neckline/selection/basket_store.py:199-210, 300-306`:两个独立入口把 `_conflicts` 直接丢弃(成员集冲突在该路径下连 WARNING 都没有,只有「幂等跳过」这条泛化警告);只有 `save_tier_decision` 披露。docstring 已标「正常路径应走 save_tier_decision」,但独立入口是导出的公开函数。建议:独立路径也把 conflicts 打 WARNING 并放进返回值。

**✅ 已修**(commit `1052622`):三个入口共用新的 `_warn_conflicts()`,返回 stats 统一带 `frozen_conflicts`;⑤ 两条断言 stats 全等的老用例补上该键。

### B2 · 存拍 `cum_volume/cum_amount` 用 `or 0.0` 把「源没给」写成 0
`neckline/sentinel/capture.py:168-169`:`cum_v = _f(...) or 0.0`——源缺累计量(None)时落 0.0,与模块自己「累计值原样落」的承诺及「没有 ≠ 没看」纪律相悖,还把下一拍的增量基线焊死在 0。增量列(d_v/d_a)的 null 纪律做对了,累计列漏了同一课。建议:None 原样落 null,且该码不进 `last_cum` 基线。

**✅ 已修**(commit `8c3e650`,2026-08-04 A 组 A5):照建议。新 `_delta()` 把三种"算不出"收口成一处(这一拍没拿到 / 上一拍没拿到〔基线失效,含当日首次观测〕/ 累计值回退),`last_cum` 改成**两列各自记基线**(`Tuple[Optional[float], Optional[float]]`,缺就记 `None` = 该列基线失效),`res.codes = len(last_cum)` 的既有语义(当日观测到过几只票)不变。回归三条:「没有≠0」/「缺这拍不当下一拍基线」(锁的正是本条点名的那个害处 —— 否则下一拍会算出一个等于当日全部累计量的假增量)/「只缺一列不牵连另一列」。

### B3 · `selection_packs` 单现役无 DB 级约束,多现役时读侧静默择新
无 `is_active=1` 的 partial unique index;`pack.py:398-401` 的 `get_active_pack` 在(仅可能由手工 SQL 造成的)多现役行下按 `created_at DESC` 静默取一行、不告警,`activate_pack:480-489` 也只 deactivate 一行。建议:`_SCHEMA` 补 `CREATE UNIQUE INDEX IF NOT EXISTS … ON selection_packs(is_active) WHERE is_active=1`,读侧遇 >1 行打 WARNING。

**✅ 已修**(commit `d61b0b8`),但**索引没放进 `_SCHEMA`**:那样老库上若已有两行现役,`executescript` 会在这条上抛 `IntegrityError` 并**中断整个建表脚本**,而 `init_schema` 是所有入口的开机路径 —— 一个「防新脏数据」的约束会把已有脏数据的库锁死开不了机,还只给一句 `UNIQUE constraint failed`。改放新的 `_POST_MIGRATION_INDEXES` 并逐条 try/except:吵得足够响 + 说清怎么修 + 继续启动(⛔ 开机脚本无权替用户清理数据)。读侧告警照建议加,排序补 `pack_version DESC` 作确定性 tie-break。

### B4 · `scripts/oneoff/` 两个留档脚本已断 import(运行即 ImportError)
`compare_intel_sort_key_switch.py:39-40`(import 已删的 `intel_candidates`/`report.candidates`)、`compare_a2b3_industry_switch.py:26`。留档定位没问题,但「留档审计用」的脚本如今跑不起来。建议:文件头补一行「⑬ 后依赖已删,仅供阅读」,或挪 `archive/`。

**✅ 已修**(commit `4c00976`,2026-08-04 A 组 A9-①):取"补文件头"这一路(**不挪 `archive/`** —— `scripts/oneoff/` 本就是"已执行完毕的一次性脚本"留档区,项目 CLAUDE.md「跑法」节有明文定位,挪走反而破坏那条约定)。两个文件头各补一段:⛔ 已跑不起来仅供阅读 + 断的是哪个 import + **刻意不删不改回去的理由**(留的是方法学证据)+ 指向它当年产出的 `archive/` 对照表。

### B5 · `exec_hints_for()` 零生产调用方,守门只查存在性不查接线
`neckline/report/exec_hint.py:224`:C1–C4 四条判定完好、11 条单测供养,但 `neckline/` 与 `scripts/` 内零调用(⑬ 完工记录已如实登记留 ⑭-A)。风险在于 `test_v1_retirement_guard.py:172-179` 只断言 `hasattr`,⑭ 若忘接线不会有任何测试变红。建议:⑭-A 完工判据里点名「exec_hints_for 有生产调用点」并加一条接线守门。

**✅ 已修**(commit `4c00976`,2026-08-04 A 组 A9-③)。**先核实再动手**(交办要求"报告与完工记录矛盾,以代码为准"):⑭-A **确实接了** —— `neckline/report/basket_daily.py:414-427`(函数内延迟 import `_load_k4_exec_hint_texts`/`exec_hints_for`,按篮子成员逐票调,整段包保险丝)。本条报告的"零调用方"是**审计当时**的事实,不是遗漏。故按建议补守门:新增 `test_13_4_exec_hints_for_has_a_real_production_call_site`,判据取 **AST 调用点**而不是 import —— 那是延迟 import,`_import_hits` 这类模块级扫描看不见它(照抄 import 判据会写出一条恒绿的空守门,正是本报告 Y1 与判定线 🟡-5 反复点名的那种"锁空靶")。

### B6 · 两处测试防线随 ⑬ 删除而变薄(功能没丢,断言丢了)
① 原 `test_candidates.py::test_invalidation_spec_and_text_consistent`(spec 与人话文案一致性)未随 spec 搬进 `sentinel/invalidation.py` 重建——阈值行为在 `test_sentinel_invalidation.py` 有边界锚(0.6<0.8 触发等,构造走真 `invalidation_spec()`,数值仍被间接钉住),但 spec 形状/文案一致性无断言。② `discipline_checks` 的行为覆盖从原 `test_watchlist_check.py` 约 11 例正负分支塌缩到「*ST 进 risk_flags」一例 + 两条函数同一性守门(`tests/test_api_inquiry.py:84-88,599-619`)。建议:补一个小型 `test_discipline_checks.py`(每条硬线正负各一)。

**✅ 两条都已补**(commit `65abbf2`,2026-08-04 A 组 A6):

- **①** 按交办口径把对象换成 **V2 的双份条款**(`verification_rules` + 卡上结构化/人话两半),语义等价。⚠ 人话半份是 **LLM 写的**、机器验不了,所以锁的是**喂给它的那份机械人读阈值块** `basket_card.spec_threshold_text()` —— plan 验收点名的通路,prompt 里明令「人话条款必须与机械阈值同频」;它与 spec 讲不一样的话 = LLM 手上的条款和盘中判定用的条款不是一回事。三例:每条条件描述 / 每个非空阈值 / 两侧门槛 / 两个 `spec_version` 都在;**spec 是 `null` 的那条必须说「不判」且全文无 `0.00`**(⛔ 不把 null 翻译成一个具体价位);止损线原样取 spec 的数(用 8.37 这种不可能巧合的价位证明渲染层没自己乘 0.95)。**反向验证过**:两处变异(null→`"0.00"`、漏一条条件描述)都被打红。
- **②** 新建 `tests/test_discipline_checks.py`(**19 例**):干净行零命中(负例的公共前提)/ 选股域五个组件各一正例 + 边界含等号 / 三条可配禁买正负各一 + `config=None` 时**根本不进清单**(与"进了但恒 False"分开)/ 原因文案带现役阈值 / 拆墙后高弹墙关闭但回退即回来 / 多条同时命中要逐条报。阈值一律从 `MomentumConfig` 与 `signals` 默认值取,⛔ 测试里不抄字面量(抄了就变成"测试锁死测试自己写的数")。

### B7 · ⑭-B 契约总装清单(小口径杂项打包)
- `GET /alerts` 查询参数名是 `status_filter`(Python 形参名直接漏成契约键,`app.py:1413`),与全仓 camelCase 取向不合;
- 客户端 `mapReason` 的 `max_chase_required` case 已成死码(服务端零 raise 点,`APIClient.swift:803`),`reasonString` 的 `unresolved` 拼接机制空转(`:814-816`);
- `PositionOpenOut` 蛇驼混排(`schemas.py:425-442`,自认既有);`PositionOpenIn`/`PositionCloseIn` 同病;
- `card_not_ready`/`basket_not_found` 两个 reason 目前只活在注释与 200 内嵌字段里,⑭-B 建 `/baskets/{id}/card` 时客户端 `mapReason` 必须加 case(404 fallback 是 `.notHolding`,新 reason 不加 case 会显示成「持仓已清」——CLAUDE.md 已有案底)。

### B8 · 两处措辞/注释与实现出入(行为本身无错)
- `neckline/sentinel/__init__.py:17` 仍写「entry.py …… 四哨兵判定」,该文件已删;
- ⑬ 完工记录称 `missedEntryHint`「保留但恒空」——准确口径是「**新数据下恒空,历史日期回放仍会非空**」(`pipeline.py:95` 读 `sentinel_events` 历史 `entry` 行;与 `ReportOut.candidates` 的历史回放语义同类,合理,但措辞该改)。

**✅ 两处均已改**(commit `4c00976`,2026-08-04 A 组 A9-②):

- 第一处**不止改那一行**:该模块头整段还停在 V1 口径(「四哨兵 = 买点/退潮/持仓/证伪」、关注池是"候选 + 持仓 + 昨日涨停"、模块地图缺 `mainline`/`precall`/`basket_verify`/`capture`/`circuit`)。改行不改段等于把一句假话换成一段假话,故按 V2 现役事实重写模块头,并逐条标出换血点(判定对象 = T1/T2 篮子成员、主线样本取 ④ 机械种子、篮子 `falsified` ⛔ 不接持仓动作/不进推送)。
- 第二处照建议逐字改口,并写明**为什么"恒空"这个说法有害**:它会让后人以为可以直接把 `compute_missed_entry_hint` 那条读路径删掉。已核实代码属实(`pipeline.py:97-105` 数 `sentinel_events` 的 `entry` 行,V1 时期真跑出来的行还在库里)。

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
