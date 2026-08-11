# 独立审计报告 · 纪律章程与金额判定(v1.2-A / v1.2-A2 / v1.3-①)

- 审计员:独立 reviewer(未参与施工),2026-07-27
- 范围:三仓章程 + 历史洗白修复 / 熔断纪律 / 退出规则改革(含卖出费估算、推送白名单、切换器)
- 方法:从零读代码;施工自述当待验证声明;**每条纪律双向验**(该触发时触发 + 不该触发时不触发);能跑的全跑(全量 pytest、六年真回测独立复现、临时副本上实测切换器/洗白/熔断反例、生产 ECS 只读核查)。全程未改任何代码、未对生产做写操作。
- 生产实况(只读核查,2026-07-27):`/api/v1/health` 返 `v1.3`;`strategy_versions` 四行(K1 现役 `activated_at=2026-07-20T11:35:24Z`、K4/v1.2/v1.3 均 `is_active=0`);v1.3 行 config = 0.08/hold 5/profit 15/True/4 万/3 仓 ✓;K1 = 0.05/5/2 万/5 仓/0.6 ✓;open 持仓 0;`circuit_breaker` 0 行。**章程未激活属实,现在修正是最佳窗口。**

---

## 一、缺陷清单(按严重度)

### 🔴-1 两档时间退出:回测引擎「D5 一次性判向」 vs 实盘「逐日重判」——同一条章程两套行为,反向会静默瓦解退出纪律

- 位置:`neckline/sentinel/precall.py:259-279`(`classify_time_exit`,`d≥max_hold_days` 每日用最新净浮盈重判)vs `neckline/strategy/momentum.py:280-312`(`_time_exit_reason`,仅 `held==max_hold_days` 判一次,豁免后 `_eff_max` 一次性抬到 15、此后不再看净浮盈)。三个实盘消费点(16:35 `report/holding_k4_check.py:475`、9:25:30 precall `precall.py:465`、`GET /positions` `api/app.py:596`)全走逐日重判,且三处用的净浮盈数据源还不同(EOD close / 上一 EOD 快照 / 实时价)。
- 失败场景(v1.3 激活后,构造已复现):
  - **正向偏差**:D5 净浮盈 +50 → `profit_exempt`(豁免,客户端 D7/D15);D7 净浮盈跌到 −30(未破止损、回落 <8%)→ classify 返 `time_exit_next_day`,precall 推「D7 时间退出日(净浮盈≤0)」。**引擎/章程口径此时应继续持有到回落 8%/止损/D15**——实盘催早退,回测测的不是实盘执行的规则。
  - **反向纪律漏洞(更重)**:D5 净浮盈 −10 → 推「按计划离场」;用户没走,D6 收盘价小涨令净浮盈 +20 → D7 起 classify 改口 `profit_exempt`,**不再催、客户端徽标反转成 D7/D15、todayAction 显示「浮盈豁免…管到硬上限」**——章程明文「D5 非浮盈 → 次日退出」的违纪被系统事后合法化。且周复盘对账(`review/reconcile.py`)**没有任何 hold 天数/时间退出违纪检查**,事后也无人兜底。
- 证据:反例脚本 F1/F2 实测(`classify_time_exit(7, v13cfg, -30)→time_exit_next_day`;`classify_time_exit(5,·,-10)→time_exit_next_day` 后 `classify_time_exit(7,·,+20)→profit_exempt`)。根因是 plan 自相矛盾:§五 v1.3-①-B(引擎)写「D5 判一次、豁免管到 D15」,①-C(scan)写「d≥ + 当前净浮盈」三态——builder 两段都照抄了,§2.1 章程文本站在「D5 判一次」一边。
- 修法方向:激活前必须拍板一种语义。建议与章程/引擎对齐 = **豁免/退出判向在 D5 定格一次**:16:35 首次对 `d==max_hold_days` 算出的 state 持久化(`holding_eod_check` 已有该表),此后 classify/scan/PositionOut 一律读定格值(D≥15 硬上限除外),不再逐日重判;若用户有意要「逐日重判」语义,则须改引擎+补回测+改 §2.1 文字,并补「时间退出违纪」的周复盘审计项。

### 🟡-2 切换器目标闸是黑名单不是白名单:`--target K2 / K4` 一样能把死研究臂激活成现役章程

- 位置:`scripts/activate_charter.py:42`(`_DEPRECATED_TARGETS={"v1.2"}`)+ `:105`(核心值核对仅 `if target == "v1.3"`)。
- 失败场景:清仓后(闸 1 天然放行)`--target K2 --confirm`(或 K4,typo/复制错命令即可)→ 直接激活。**临时副本实测:exit=0、`is_active` 变 K2**。K2/K4 行 config 是 K1 旧值(回落 5%/2 万/5 仓),激活后 entry-suggestion、哨兵、周复盘全按废弃口径跑,且 `reviews.strategy_version` 会把周判归到 K2——静默且全链路生效。
- 证据:反例 A4(副本上真跑,K2 成现役);A1/A2/A3/A5 四道既有闸全部按设计工作。
- 修法方向:目标合法性改**白名单**(当前仅允许 `v1.3`,或要求目标 rule 带 `lineage` 且 `charter` 标记),对任何非白名单目标硬拒绝;顺手把闸 2b 的核心值核对改成「凡激活必核对」。

### 🟡-3 周末/北京时间凌晨激活章程,会把「刚结束的完整一周」交给新章程判——K1 时期违纪被整周洗白

- 位置:`neckline/strategy/brain.py:68-79`(`_activated_date` 取 UTC 时间戳的日期)+ `:181`(`candidates = activated_date <= ref_date`,ref = 周日 `week_end`);`review/reconcile.py:588`。
- 失败场景(副本实测复现):周内(如周三)有一笔 3 万买入(K1 下 >2 万违纪),用户周五清仓、**周六/周日激活 v1.3**(或北京周一 00:00–08:00 激活——UTC 时间戳落在周日)→ 该周 `week_end`(周日)≥ 激活日 → governing=v1.3(4 万上限)→ **3 万违纪消失**。实测 C1:`activated_at='2026-07-26T18:00:00+00:00'`(北京周一 02:00)时,07-20~07-26 周 governing=v1.3、违纪 0 条。plan「已知简化」接受周中激活,但其论证(「清仓后无跨边界成交」)不成立——该周一至周五的已平仓成交全在 K1 治下。周末恰是用户最可能跑切换器的时点。
- 证据:B1(正常时点激活,同一笔 3 万在激活前周被 K1 判 1 条违纪)vs C1(周末激活,0 条)。
- 修法方向:`_activated_date` 换算 Asia/Shanghai 日期只是半修;建议 governing 判据改「激活日 < week_start 才用新版本」(激活当周仍按旧章程判,与 staged「清仓后才切」的语义自洽),或至少在 `activate_charter.py` 打印一条「本周已有成交,请先上传本周交割单出周报再激活」的前置警告。

### 🟡-4 「熔断中:次日只减不加」的 9:25:30 盘前强提醒没有实现——plan 声称已加,代码里不存在(假安全)

- 位置:应在 `neckline/sentinel/precall.py::run_precall_tick` / `api/app.py::_sentinel_loop`(盘前分支);全仓库 grep `熔断中`/circuit 在 precall 路径零命中。plan v1.2-A2.5 明文要求「次日强提醒:折进 9:25:30 盘前校准 tick……仍锁定则汇总里带一句『熔断中:今日只减不加』」;v1.3-⑦-D 甚至写成完成时(「9:25:30 盘前 tick 加了『熔断中』提醒」)。
- 失败场景:熔断当晚触发推送一次后,若用户没打开 App(客户端横幅是拉取式),次日盘前收不到任何「今日只减不加」提醒——第 7 条纪律的「次日」这一半只剩纯自觉。熔断已随 v1.3 部署在生产生效(不等章程激活),此缺口现在就活着。
- 证据:grep 全库 + 读 `_sentinel_loop` 盘前分支(仅 precall 汇总 + D5 推送)。
- 修法方向:`run_precall_tick` 里查 `circuit.is_locked()`,锁定则在 9:26 汇总 body 前置一句「熔断中:今日只减不加」(汇总门槛 `summary_actionable` 需同时放行此情形),或单独走 `push_circuit_breaker` 复推一次(带 sentinel_events 防重)。

### 🔵-5 store 层/CLI 可落非法 `close_reason` 串 → 熔断既不算止损也不走价格兜底,静默失效

- 位置:`neckline/sentinel/positions.py:102-131`(`close_position` 不校验码,文档自认「CLI/内部调用方自负」);`sentinel/circuit.py:165-174`(`_is_stop_loss_close`:非空即信,非 `STOP_LOSS` 一律非止损)。CLI `scripts/positions.py:58` 不传 close_reason(→NULL,兜底可用),但任何脚本/手工 SQL 写入 `'stop_loss'`(小写)之类即触发。
- 证据:反例 D4 实测——三笔 −6% 卖出、`close_reason='stop_loss'` → 不触发;同价格 NULL 码(D5)→ 正常触发 consecutive_stops。
- 修法方向:`close_position` 对非 NULL 码做 `CLOSE_REASON_CODES` 白名单校验(非法即拒绝或降为 NULL 并告警),一行防线。

### 🔵-6 CLI 清仓不经熔断评估

- 位置:`scripts/positions.py::cmd_close` 只调 `close_position`,不调 `circuit.evaluate_after_close`(评估只挂在 API 端点 `api/app.py:688`)。用 CLI 补录的第 3 笔止损不会当场触发熔断,要等下一次 API 清仓才被尾链带出。主路径(客户端)不受影响;运维/应急用 CLI 时留意。修法:CLI close 后同样调一次 evaluate(尽力而为)。

### 🔵-7 `buy_fees` 缺失时净浮盈整体偏乐观(方向恒向「豁免续持」)

- 位置:`neckline/fees.py:117-127`(`estimate_net_float` 缺 buy_fees 按 0 计)+ `api/schemas.py:306`(服务端 `buyFees` 宽松可选,契约上客户端必填)。实测偏差 ≈ 实付买入费全额(样例 +18.92 元),只在盈亏平衡带翻向(明显亏损单不受影响,与 plan 风险登记一致);但翻向方向固定是「亏单被误判浮盈 → 误豁免续持」,与纪律保守方向相反。修法:缺 buy_fees 时按 `DEFAULT_COMMISSION_RATE` 估一笔买入费扣掉(与卖出费同为诚实估算),或至少在 `holding_eod_check`/客户端标注「买入费未录,判向偏乐观」。另:`infer_commission_rate` 把 `buy_fees=0.0` 当「没录」(falsy),免佣账户会被套默认万 2.5——影响几元,同带内。

### 🔵-8 `brain.save_version` 的 `INSERT OR REPLACE` 可把现役行覆盖成非现役;遗留脚本 `research/rule_v1.py:105` 仍 `activate=True`

- 位置:`neckline/strategy/brain.py:106-113`。对**当前现役版本**调 `save_version(..., activate=False)` 会把 `is_active` 抹成 0 → 全库无现役 → `active_config()={}` → 哨兵/报告/entry-suggestion 全线静默退回 `MomentumConfig` 字段默认(**max_hold_days=3、take_profit_retrace=None、单笔 2 万**),只有一条 warning 日志。现有调用点都安全(charter 脚本有来源护栏,b6_finalize 显式断言),但 `research/rule_v1.py` 若被重跑会新建 `v1` 行并直接激活。修法:`save_version` 加护栏「目标行当前 is_active=1 且 activate=False → 拒绝」;rule_v1.py 标注 legacy 或删 `activate=True`。

### 🔵-9 杂项(不改判定结果,但值得顺手修)

- `api/notify.py:149` `push_d5_exit` docstring 仍写「`__all__` 仍五入口、APNs category 仍五类」——已是六类,白名单文档漂移(白名单守护单测在,行为无恙)。
- `api/app.py:479-481` / `sentinel/circuit.py:162` / `precall.py:361`:`cfg.get("stop_pct") or fb.stop_pct` 之类 falsy 兜底——若未来章程显式设 `stop_pct=None`(不设止损),会被悄悄换回 0.05。现行 K1/v1.3 恒 0.05,暂无实害。
- 周复盘 `weekly_review_dict`(`review/reconcile.py:708`)不含 `strategy_version`——列已落 `reviews` 表,但 API 响应/客户端看不到「这周用哪版章程判的」。
- 熔断连续止损链**无时间窗**(横跨三个月的 3 笔也触发,D7 实测)且**解锁后链不重置**(解锁后第 1 笔新止损即再熔断,D6 实测)——两者都是保守方向、可辩护,但 plan 未写明,建议在 §2.1 第 7 条补一句口径说明,免得实盘触发时被当 bug。
- 周复盘对账没有「时间退出(hold 天数)违纪」检查项(§2.1 第 2 条纪律无周线审计兜底)——v1.3 两档语义定案后建议补。
- `scripts/report.py --notify` 手工重跑会对同一天的强警示重复推送(无 sentinel_events 防重;timer oneshot 正常路径无恙)。

---

## 二、已验证不变量(逐条,含阴性方向)

1. **K1 逐位不变** ✅ 独立复跑六年真回测(不经单测,直接 `lab.run_pf(MomentumConfig(**brain.active_config()))`,1.9G k3_panel):**N=1288 / total_return −0.20532084 / final_equity 95361.4988**,与声称 N=1288 / −20.53% / 95361.50 逐位吻合;新字段默认 None/False 时 `_time_exit_reason` 走原分支、`_eff_max`/`_fee_broker` 不被触碰(护栏单测 + 读码确认)。阳性方向:两档启用(0.08/True/15)六年回测跑通,250 笔硬上限退出、持有天数=16(D15 决策 T+1 成交),豁免分支真被走到。
2. **阈值单一源** ✅ 全库 grep:`3`/`4000` 熔断字面仅 `sentinel/circuit.py`(+db.py 注释);`40000/20000` 仅 charter 脚本(护栏用)与 `MomentumConfig` 字段默认;客户端 Swift 无硬编。stop/retrace/hold/single_cap 消费点(circuit、precall、app、reconcile、holding via engine)全经 `brain.active_config()`。费率常量 fees.py 与 backtest/broker.py 双处同值属 plan 明文设计(实盘估算 vs 引擎模型),fees.py 头注释已登记。
3. **单日亏损净口径 vs 周线 gross 口径** ✅ 双向实测:上午 −6000 + 下午 +3000(净 −3000)不触发;补一笔 −1000 凑满 −4000 整,边界触发 `daily_loss`(`_EPS` 容差,`≥4000` 语义正确)。`reconcile.realized_loss = Σmin(pnl,0)`(盈利不冲抵)与熔断互不引用、阈值互不共享;大赢单遮蔽由连续止损独立兜住(反例:早前大赢单在链外,3 笔尾链仍触发,单测已锁)。
4. **close_reason 兜底只在 NULL 生效** ✅ 双向实测:三笔 −20% 深亏但显式 `MANUAL` → 不计止损链不触发(信标注,不被价格二次猜);三笔 −6% NULL 码 → 价格兜底触发 `consecutive_stops`;触发后库内 `close_reason` 仍全 NULL(不回写、不臆造历史)。
5. **洗白修复** ✅ 双向实测(真库副本、真切换器激活 v1.3 后):激活前周 3 万买入 → governing=K1、报 1 条单笔违纪(不被 4 万洗白);激活后周 3 万 → governing=v1.3、0 条(不误伤);激活后周 4.5 万 → 1 条(新上限仍拦)。回填与兜底耦合核对:生产 K1 `activated_at` 已回填(2026-07-20T11:35:24Z),`_backfill_activated_at` 幂等只碰 `is_active=1 AND NULL`;纯 legacy 库退 `get_active()`(单测锁)。**例外:周末/UTC 边界见 🟡-3。**
6. **两档四消费点** ⚠ 单档兜底方向全对:K1 现役下 `scan_time_exits` 退回 `d==max_hold_days`(与 v1.1 `scan_d5_exits` 一致,单测锁);`profit_exempt` 在所有推送路径都推不出去(`_ACTIONABLE_TIME_EXIT` 过滤 + notify 无该分支 + `_today_action` 把它当持有态);无处残留「无条件 ==」误判豁免单。**但两档启用后的逐日重判 vs 引擎一次性豁免分叉 = 🔴-1。**
7. **卖出费估算** ✅ 回测不走 fees.py(`momentum._d5_net_float` 用 `Broker._sell_fees`,单测对拍);印花税万 5 已在模块头登记「待用户确认」并给出与千 1 的政策沿革;反推佣金带最低佣金地板、命中地板时偏保守方向已标注;误差只在盈亏平衡带翻向(明显盈/亏单金额级不可能被几十元翻转)。**缺 buy_fees 的偏乐观方向见 🔵-7。**
8. **推送白名单六处一致** ✅ `notify.__all__` 六入口 / `apns.py` 六 category / `app_settings` 六列(含幂等迁移)/ `GET/PUT /settings/push` 六字段必填(缺→422)/ `settings_store.set_push` 六参无默认值 / 客户端 `PushManager` 六个 `UNNotificationCategory` 且 identifier 字面与服务端一致。K4 推送门槛 `has_strong` = strong ∧ price_volume,题材类(constituent)弱证据被 `strong_price_volume_labels()` 过滤,不进 APNs。
9. **§3.8 铁律** ✅ 熔断/时间退出/派发警报全链路只落库 + 推送:`circuit.py` 纯读写 + 派生态;`close_position`/`open_position` 只记账;`holding_k4_check` 头注明并核实无交易副作用;全仓库无任何券商下单/撤单集成代码;服务端不拦 `POST /positions`(熔断纯提醒层,验证过 close 端点异常吞掉不阻断)。
10. **切换器三道闸** ✅ 副本实测:dry-run 不写库(exit 0、仍 K1);有 open 持仓 `--confirm` 拒绝(exit 1、打印清单);`--target v1.2 --confirm` 硬拒(exit 2);正常激活 v1.3 通过核心值核对(0.08/True/15)。**第四道闸缺口(K2/K4 可激活)= 🟡-2。**

## 三、没查/浅查的(诚实边界)

- v1.3-② K4 advisory polars 镜像与 DB `k4_advisory` 六节的**逐条语义对拍**只抽查了分级/推送门槛与 A3b/B1 分叉说明,未逐阈值对照 K4 行 evidence 原文。
- 客户端 SwiftUI 侧只核了契约字段、category 注册与推送开关往返代码,未起模拟器跑两档 D 徽标/费用录入 UI。
- `review/parse.py` 交割单解析、决策日志/呼吸台账两块(不在本维度)。
- APNs 活体推送(无真机/真 token,推送层沿用既有 MockTransport 测试基建)。

## 四、总体判断

熔断纪律、历史洗白修复、卖出费估算三块**主体工程质量高**:口径写死、双向都有单测、我构造的全部反例(净口径互抵、边界 −4000、显式码不二猜、兜底不回写、洗白双向)无一击穿;K1 逐位不变经我独立六年回测复现属实;生产现状与声称一致(v1.3 已部署、章程未激活、零持仓零熔断行)。**当前 K1 现役下的生产行为可以放心继续用。**

但 **v1.3 章程激活前必须先处理**:🔴-1(两档时间退出语义分叉——激活后第一只豁免单就会踩到,反向还会静默瓦解退出纪律)与 🟡-2(切换器白名单——就发生在激活这个动作本身)、🟡-3(激活时点选择——建议修复前至少遵守「只在周一至周五、当周交割单已出周报后激活」);🟡-4(盘前熔断提醒)不挡激活但已在生产裸奔,应尽快补。高危区惯例:本报告建议主会话对 🔴-1 的修复方案再复审一遍,取并集。
