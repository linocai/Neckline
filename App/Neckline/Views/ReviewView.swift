//
//  ReviewView.swift
//  Neckline — 🔴 **复盘板块**(V2.1 三板块之一,双端共用;V2.1-⑦ 新建)。
//
//  **五页答五个不同的问题,⛔ 不合并**(`ReviewPage`;V2.2-④ 从三页扩到五页):
//    · **每日** —— 昨天那批篮子后来怎么样了(④ 昨日篮子复盘,自选股板块迁入;
//      数据源仍是 `model.basketDaily.reviews`,**随报告冻结、零新增网络调用、服务端零改动**)。
//    · **选股钟**(V2.2-④-A)—— 这批票**选**得对不对。🔴 样本 = D0 **全部** T1/T2,
//      **与用户买没买无关**(K8 §十四 第 3 条),D1 收盘验证一次即结案。
//    · **交易钟**(V2.2-④-B)—— 这笔买卖**做**得怎么样。**启动的唯一条件 = 实际买入**;
//      逐仓明细带写入口在「持仓」板块的持仓卡里,本页只给周度侧归因。
//      ⚠ 两个时钟**刻意分成两页**:样本域根本不同,并排一页会让人以为
//      「选股时钟里的篮子 = 我买过的票」—— 那正是 K8 反复强调的、最容易讲小的那个域。
//    · **累计** —— 这套选股长期成绩如何(`GET /review/overview` 八段 + 校准移交件出口)。
//    · **对账** —— 我实际的成交与计划 / 章程对不对得上(macOS 上传交割单;iOS 只读)。
//
//  **诚实披露纪律(本页每一段都受它约束,⛔ 不许退化)**:
//   1. 「**没有**」与「**没看**」永远分开渲染 —— 画像段缺席 = 系统那一步没跑(`available=false`);
//      对账段缺席 = 输入只能由用户给、系统查过确实没有(`available=true` + `found=false`)。
//      两者给用户的动作完全不同(等系统 vs 去上传),⛔ **别"统一"成一句「暂无数据」**。
//   2. `confidence == "low"` 的画像行**必须**显式写「样本不足,不给结论」,
//      ⛔ 不许把低置信度的数字当结论展示。
//   3. 观察项是**等证据的策略问题清单**,不是待办 —— ⛔ 不给"建议"、不改写 status。
//   4. 复盘板块**只呈现证据,⛔ 不做任何自动反馈回写选股**(用户裁定 #3:改包唯一通道
//      仍是「攒够样本 → 用户带材料去策略台 → 新 K 包 → 用户过门 → 四道闸激活」)。
//   5. 🔴 **四分类的 `klass == nil` + `klassStatus == "thresholds_undecided"` 是设计中的
//      状态**(K8 §十七 没给「多少样本算够」「差多少算失效」这两个数)—— 界面必须写
//      「分界线未定 · 待你拍板」并说清缺哪两个数,⛔ 不许显示成「暂无建议」或空白
//      (那会把「还没决定」讲成「没问题」),⛔ 也不许渲染成「观察」。
//

import SwiftUI
#if os(macOS)
import AppKit
#endif

struct ReviewView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(AppTab.review.title)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    // 🔴 V2.4.0 P3.6:显式刷新走 `refresh(for:)`(不受首访门控,真的重拉)。
                    Button { Task { await model.refresh(for: .review) } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
        }
        // 首次进入「复盘」Tab 才拉(`AppModel.ensureLoaded` 按板块门控,与 `RootView`
        // 的 Tab 切换钩子共用同一份"已加载过就不重拉"逻辑,不在本页另写一份)。
        .task { await model.ensureLoaded(.review) }
        #else
        // ⚠ **V2.3:macOS 改「列表栏 五页 + 详情栏」** —— 五页横排在段控里挤成五个
        // 两字词,正是用户说的「五个页签分不清」;列表栏一页一行、每行下面跟着
        // **「这一页答什么」**,才把选股钟 / 交易钟那两个不同的样本域讲清楚。
        NKSplitLayout {
            pageListColumn
        } detail: {
            currentPage
        }
        .task { await model.ensureLoaded(.review) }
        #endif
    }

    #if os(macOS)
    private var pageListColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            // 标题区 = 原型 1268–1271 `padding:18px 16px 12px`(纵 18 由 `NKSplitLayout`
            // 的 `listPadTop` 给,横向在栏内边距 10 之上补 6 = 16,底 12)。
            VStack(alignment: .leading, spacing: 2) {          // 原型 1270 margin-top:2
                HStack(spacing: 8) {
                    Text(AppTab.review.title).font(NKFont.title2).tracking(-0.3)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 0)
                    // 🔴 V2.4.0 P3.6:复盘板块自己的刷新走 `model.refresh(for: .review)`
                    // ——五段 / 双时钟 / 选股时钟结案表已收口进 `AppModel.refreshReview()`,
                    // ⛔ 不在本页再另写一份"拉哪几样"。
                    Button { Task { await model.refresh(for: .review) } } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                }
                Text("五页答五个不同的问题 · 只呈现证据,改选股包永远走人工门禁")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .lineSpacing(3)                            // 原型 1270 line-height:1.5
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH).padding(.bottom, 12)

            ForEach(ReviewPage.allCases) { p in
                NKListRow(selected: model.reviewPage == p) {
                    model.reviewPage = p
                } content: {
                    // 原型 1274–1332:**没有行图标**,两行都顶格 —— 图标 + 缩进会把
                    // 「这一页答什么」推成附属说明,而它是这一栏最该被读到的那句话。
                    VStack(alignment: .leading, spacing: 3) {  // 原型 1283 margin-top:3
                        HStack(spacing: 8) {
                            Text(p.title).font(NKFont.body).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            Spacer(minLength: 0)
                            pageBadge(p)
                        }
                        // 🔴 「这一页答什么」——⛔ 别省:五个两字词分不清是原病。
                        Text(p.question).font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                            .lineSpacing(3)                    // 原型 line-height:1.45
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            // 对账页选中时,列表栏多两块(原型 `Neckline 信息卡与对账.dc.html` 321–336):
            // 「已上传的周」+ 底部「再拖入交割单」虚线块。
            if model.reviewPage == .reconcile { uploadedWeeksSection }

            // 翻周器住列表栏(原型 1330–1334),**⛔ 不在累计页详情里再画一遍**。
            windowCard.padding(.horizontal, 2).padding(.top, 18)
        }
    }

    // ⚠ **`reloadBoard()` 已删**(V2.4.0 P3.6):五段 + 选股时钟结案件的拉取逻辑
    // 收口进 `AppModel.refreshReview()`,本页两处调用点(iOS 工具栏 / macOS 列表栏)
    // 都直接走 `model.refresh(for: .review)`,不在视图层另存一份"拉哪几样"。

    /// 页签右端的读数。**只在数得出来时才给** —— ⛔ 拿 0 冒充「算过了没有」。
    /// ⚠ 原型五行**刻意是三种形状**(徽标 / 纯文字 / 一个点):徽标是「要你处理的」、
    /// 纯文字是「读数」、点是「有东西变了」。⛔ 别为了整齐统一成徽标。
    @ViewBuilder
    private func pageBadge(_ p: ReviewPage) -> some View {
        switch p {
        case .daily:
            // 原型 1279–1281:**两枚**计数(已验证绿 / 被证伪红),⛔ 不是一个总数。
            let c = dailyCounts
            if c.verified + c.falsified > 0 {
                HStack(spacing: 3) {                           // 原型 1279 gap:3
                    if c.verified > 0 { NKChip(text: "\(c.verified)", tone: .good) }
                    if c.falsified > 0 { NKChip(text: "\(c.falsified)", tone: .bad) }
                }
            }
        case .selectionClock:
            if !model.selectionClocks.isEmpty {
                Text("\(model.selectionClocks.count) 结案")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
            }
        case .tradeClock:
            if let n = model.reviewOverview?.tradeClock.detail["running"]?.intValue, n > 0 {
                Text("\(n) 在跑").font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textTertiary)
            }
        case .cumulative:
            // 原型 1315 那颗 6px 琥珀点 = 「这一页有等你拍板的东西」。
            if hasUndecidedThresholds {
                Circle().fill(NK.amber).frame(width: 6, height: 6)
            }
        case .reconcile:
            if let n = reconcileViolationCount {
                NKChip(text: "\(n) 条违纪", tone: .bad)
            } else if !reviewFound {
                NKChip(text: "未上传", tone: .warn)
            }
        }
    }

    private var dailyCounts: (verified: Int, falsified: Int) {
        let rs = model.reviewDailyBasket.reviews
        return (rs.filter { $0.verification?["state"]?.stringValue == "verified" }.count,
                rs.filter { $0.verification?["state"]?.stringValue == "falsified" }.count)
    }

    /// 本周对账**在服务端有没有那一行**(≠「本次会话有没有上传过」)。
    private var reviewFound: Bool { model.reviewOverview?.reconcile.found == true }

    private var reconcileViolationCount: Int? {
        guard let e = model.reviewOverview?.reconcile.weeklyEntry else { return nil }
        let n = e.result.disciplineViolations.count
        return n > 0 ? n : nil
    }

    private var hasUndecidedThresholds: Bool {
        (model.reviewOverview?.iterationSuggestions.items ?? [])
            .contains { $0["klassStatus"]?.stringValue == "thresholds_undecided" }
    }

    /// 「已上传的周」+「再拖入交割单」(原型 `信息卡与对账` 321–336)。
    /// ⚠ 只列**已经知道的那些周** —— 契约没有「列出全部已上传周」的端点,
    /// ⛔ 不去猜、不拿空清单冒充「只有这几周」。
    @ViewBuilder
    private var uploadedWeeksSection: some View {
        if !model.reviewWeeks.isEmpty {
            VStack(alignment: .leading, spacing: NKSpace.rowGap) {
                Text("已上传的周").nkLabel().foregroundStyle(NK.textTertiary)
                    .padding(.horizontal, 4).padding(.bottom, 4)
                ForEach(model.reviewWeeks) { entry in
                    let on = entry.week == (model.reviewSelectedWeek ?? model.reviewWeeks.first?.week)
                    Button { model.reviewSelectedWeek = entry.week } label: {
                        HStack(spacing: 7) {                   // 原型 325 gap:7
                            Text(entry.week).font(NKFont.callout.monospacedDigit())
                                .fontWeight(on ? .semibold : .regular)
                                .foregroundStyle(on ? NK.textPrimary : NK.textSecondary)
                            Spacer(minLength: 0)
                            if entry.result.forcedReview {
                                Image(systemName: "exclamationmark.circle")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(NK.down)
                            }
                        }
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: NKRadius.control)
                            .fill(on ? NK.accent.opacity(0.10) : .clear))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 16)
        }
    }

    /// **本期窗口翻周器**(原型 1330–1334,住列表栏底部)。
    /// ⚠ 翻周不是装饰:周度校准产物是**周六离线作业**落的,周一到周五看"本周"永远是
    /// 「尚未生成」—— 没有这两个箭头,用户就永远看不到上周那份已经算好的成绩单。
    private var windowCard: some View {
        VStack(spacing: 6) {                                    // 原型 1334 margin-top:6
            HStack(spacing: 10) {                               // 原型 1331 gap:10
                arrowButton("chevron.left") { Task { await model.shiftReviewWeek(-1) } }
                VStack(spacing: 1) {
                    Text(model.reviewWeekAnchor == nil ? "本期窗口" : "所选窗口")
                        .nkLabel().foregroundStyle(NK.textTertiary)
                    Text(windowText).font(NKFont.callout.monospacedDigit())
                        .fontWeight(.semibold).foregroundStyle(NK.textPrimary)
                }
                .frame(maxWidth: .infinity)
                arrowButton("chevron.right") { Task { await model.shiftReviewWeek(1) } }
            }
            Text("周度校准是周六离线作业 —— 周一到周五看「本周」永远是尚未生成,用箭头翻到上周")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .multilineTextAlignment(.center).lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            if model.reviewWeekAnchor != nil {
                Button { Task { await model.shiftReviewWeek(nil) } } label: {
                    Text("回本周").font(NKFont.caption).fontWeight(.semibold)
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
            }
        }
        .padding(.horizontal, 13).padding(.vertical, 11)        // 原型 1330
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
            .stroke(NK.hairline, lineWidth: 0.5))
    }

    private var windowText: String {
        guard let ov = model.reviewOverview else { return "—" }
        if ov.weekStart.isEmpty && ov.weekEnd.isEmpty { return "该周没有交易日" }
        return "\(ov.weekStart) ~ \(ov.weekEnd)"
    }

    private func arrowButton(_ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 11, weight: .bold))
                .foregroundStyle(NK.accent)
                .frame(width: 22, height: 22)                   // 原型 1332 22×22 / radius 6
                .contentShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
    }
    #endif

    @ViewBuilder
    private var currentPage: some View {
        switch model.reviewPage {
        case .daily: dailyPage
        case .selectionClock: selectionClockPage
        case .tradeClock: tradeClockPage
        case .cumulative: cumulativePage
        case .reconcile: reconcilePage
        }
    }

    // ⚠ **`loadIfNeeded()` 已删**(V2.4.0 P3.6):「只在还没有时拉」这条纪律现在由
    // `AppModel.ensureLoaded(.review)` 的 `loadedBoards` 门控统一承担(与其余三板块
    // 同一套机制),⛔ 不在本页再判一次 `reviewOverview == nil`。

    /// iOS:段控 + **「这一页答什么」**一行(⛔ 别省那一行 —— 五个两字词分不清是原病)。
    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            Text("复盘只呈现证据 · 改选股包永远走人工门禁(系统不自动回写策略)")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            pagePicker
            Text(model.reviewPage.question)
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            currentPage
        }
    }

    /// 五枚**胶囊页签**(iOS 原型 564–570 行:`radius:999; padding:6px 14px; 13px`,
    /// 选中 = `#0B6BCB` 实底白字 + 600、未选 = `rgba(60,60,67,.07)` 底 `.75` 字)。
    ///
    /// ⚠ **⛔ 不用系统 `Picker(.segmented)`**:它的选中态是"白底浮起"、整条还有一层灰槽,
    /// 与原型的"蓝实底胶囊"是两种视觉语言;而且五个两字词在 402pt 上会被系统均分成
    /// 五个等宽格,长短不一的页签(「每日」vs「选股钟」)看起来像排版没对齐。
    /// 横向可滚:五枚排不下时能划出来,⛔ 不缩字号。
    private var pagePicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {                       // 原型 564 行 gap:6
                ForEach(ReviewPage.allCases) { p in
                    let on = model.reviewPage == p
                    Button { model.reviewPage = p } label: {
                        Text(p.title).font(NKFont.body)
                            .fontWeight(on ? .semibold : .regular)
                            .foregroundStyle(on ? Color.white : NK.textPrimary.opacity(0.75))
                            .padding(.horizontal, 14).padding(.vertical, 6)
                            .background(Capsule().fill(on ? NK.accent : NK.chipNeutral))
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 2)
        }
    }

    // MARK: - 每日:④ 昨日篮子复盘(自选股板块整段迁入;**三态逐字保留**)
    //
    // 🔴 段名「④ 昨日篮子复盘」**一字不动**:它与服务端 markdown 报告同构,是审计锚。
    // 换的只是挂载点(选股板块 → 复盘板块),数据、三态文案、行视图全部逐字照搬。

    @ViewBuilder
    private var dailyPage: some View {
        // 🔴 **读的是「回看那一天」的快照**(V2.3 结 P3-47):没选日期时它就是今天那份。
        let daily = model.reviewDailyBasket
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            // 原型 1339–1341:26px 大标题 + 12px 副标题(⛔ 不是 15px 段头)。
            ReviewPageTitle("④ 昨日篮子复盘", subtitle: dailySubtitle(daily))
            dailyDatePicker
            if let err = model.reviewDailyError {
                NKCard {
                    NKEmptyState(title: "这一天没有可回看的复盘", subtitle: err,
                                 systemImage: "calendar.badge.exclamationmark")
                }
            } else if model.reviewDailyLoading {
                NKCard { NKEmptyState(title: "正在取那一天的报告…", systemImage: "hourglass") }
            } else if model.reviewDailyDate == nil && !model.hasReportData {
                NKCard {
                    NKEmptyState(title: "今日报告尚未生成",
                                 subtitle: "昨日复盘随每日报告一起冻结;今晚 16:35 出报告后自动显示。",
                                 systemImage: "moon.zzz")
                }
            } else if !daily.reviewsAvailable {
                NKCard {
                    NKEmptyState(title: "本次没跑复盘",
                                 subtitle: daily.reviewsUnavailableReason.map { "原因:\($0)" }
                                     ?? "这一段本次未取到(不是「昨日无篮子可复盘」)",
                                 systemImage: "exclamationmark.icloud")
                }
            } else if daily.reviews.isEmpty {
                NKCard {
                    HStack(spacing: 8) {
                        Image(systemName: "calendar.badge.clock").foregroundStyle(NK.textTertiary)
                        Text("昨日无篮子可复盘").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        Spacer()
                    }
                }
            } else {
                ForEach(daily.reviews) { r in
                    BasketReviewRow(review: r)
                }
            }
        }
    }

    /// **回看某一天**(§七 P3-47 在 V2.3 结案)。
    ///
    /// 🔴 **复用 `GET /report?date=`,读的是那天冻结的报告快照** —— ⛔ 不新建
    /// 「复盘历史」端点去现连 `basket_review_daily`:现连会绕开冻结件,让回看到的复盘
    /// 与**当时那份报告**讲不同的话。
    /// ⚠ 换日期**只影响这一页**;选股板块仍然是今天(⛔ 别把全 App 的"今天"换掉)。
    private func dailySubtitle(_ daily: BasketDaily) -> String {
        let d0 = daily.reviewD0.map { "D0 \($0)" } ?? "D0 未登记"
        return "\(d0) · \(daily.reviews.count) 篮 · 随每日报告冻结"
    }

    private var dailyDatePicker: some View {
        NKCard(padding: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: "calendar").font(.system(size: 12))
                        .foregroundStyle(NK.accent)
                    Text("回看").nkLabel().foregroundStyle(NK.textTertiary)
                    DatePicker("", selection: Binding(
                        get: {
                            model.reviewDailyDate.flatMap(model.calendar.parseDate)
                                ?? model.calendar.parseDate(model.report.tradeDate)
                                ?? Date()
                        },
                        set: { d in
                            Task { await model.loadReviewDaily(date: model.calendar.compactString(d)) }
                        }), in: ...Date(), displayedComponents: .date)
                        .labelsHidden()
                        #if os(macOS)
                        .datePickerStyle(.field)
                        #else
                        .datePickerStyle(.compact)
                        #endif
                    if model.reviewDailyDate != nil {
                        Button { Task { await model.loadReviewDaily(date: nil) } } label: {
                            Text("回到今天").font(NKFont.caption).fontWeight(.semibold)
                        }
                        .buttonStyle(.plain).foregroundStyle(NK.accent)
                    }
                    Spacer(minLength: 0)
                }
                // 🔴 **两个分支各自写成字面量**(§五 〇d 第 7 条):三元表达式的结果是
                // 一个 `String`,`Text(String)` **不解析 Markdown** → `**冻结报告**` 的
                // 星号会原样印在界面上。⛔ 别再合回一个三元。
                Group {
                    if model.reviewDailyDate == nil {
                        Text("看的是最新那份报告(交易日 \(model.calendar.displayString(model.reviewDailyTradeDate)))")
                    } else {
                        Text("看的是 \(model.calendar.displayString(model.reviewDailyTradeDate)) 那份**冻结报告**里的复盘 —— 与当时看到的逐字相同")
                    }
                }
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - 选股时钟(V2.2-④-A):**D0 全部 T1/T2 的结案件**
    //
    // 🔴 **样本口径必须当面讲清**:覆盖 D0 **全部** T1/T2,**与你买没买无关**
    // (K8 §十四 第 3 条)。⛔ 文案不许写成「你关注的篮子」之类 —— 那会把覆盖域讲小,
    // 而这条覆盖域正是它能当选股正确率样本的前提。

    @ViewBuilder
    private var selectionClockPage: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            // 原型 1387–1389。⚠ 副标题带 `**加粗**` → **必须是字面量**(§五 〇d 第 7 条)。
            VStack(alignment: .leading, spacing: 3) {
                Text("选股时钟").font(NKFont.title1).tracking(-0.4)
                    .foregroundStyle(NK.textPrimary)
                Text("样本 = D0 当天全部 T1/T2 篮子,**与你买没买无关** · D1 收盘验证一次即结案,之后不再改")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }
            if model.selectionClocksLoading && model.selectionClocks.isEmpty {
                NKCard { NKEmptyState(title: "正在取选股时钟…", systemImage: "hourglass") }
            } else if model.selectionClocks.isEmpty {
                NKCard {
                    // ⚠ 端点恒 200 → 空列表是**合法态**。⛔ 别写成「读取失败」,
                    // 也 ⛔ 别写成「系统没跑」(那要看每日复盘与段状态,是另一件事)。
                    NKEmptyState(title: "这段时间还没有结案样本",
                                 subtitle: "选股时钟在 D1 收盘验证后才结案;⛔ 这不等于「系统没跑」——那要看「每日」页的复盘段。",
                                 systemImage: "stopwatch")
                }
            } else {
                stateDistributionCard
                clockTableCard
                NKNoteBlock(text: "「D0 无判定行」= 那天没有行情状态记录,缺行 = 不知道,不猜。四态原样保留,折成正确率是周度侧的事,这一页一个数都不折。")
                NKAuditSection(contains: "逐篮 D1 九项验证、结案时刻、骨架 / 条件集版本(只读冻结件)") {
                    ForEach(model.selectionClocks) { c in
                        NKAuditGroup(title: "篮子 #\(c.basketId) · D0 \(c.d0Date) → D1 \(c.d1Date)") {
                            Text("结案于 \(c.closedAt) · 骨架 \(c.skeletonVersion) · 验证条件集 \(c.verificationRulesetVersion)")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                            NKJSONTable(value: c.mech)
                        }
                    }
                }
            }
            // 周度侧的正确率归因(读周度落盘产物,与上面的逐篮结案件是两条数据源)。
            if let ov = model.reviewOverview {
                ClockScoreboardCard(segment: ov.selectionClock)
            }
        }
    }

    /// 四态分布条(原型 1393–1405)。**四态原样,⛔ 不折成对错**。
    private var stateDistributionCard: some View {
        let clocks = model.selectionClocks
        let segs: [(String, Int, Color)] = [
            ("verified", clocks.filter { $0.tierAccuracy == "verified" }.count, NK.up),
            ("partial", clocks.filter { $0.tierAccuracy == "partial" }.count, NK.up.opacity(0.45)),
            ("unclear", clocks.filter { $0.tierAccuracy == "unclear" }.count,
             NK.textTertiary.opacity(0.7)),
            ("falsified", clocks.filter { $0.tierAccuracy == "falsified" }.count, NK.down),
        ]
        // 「没给分层准确性判定」是**第五种**,⛔ 不并进 unclear —— 一个是判过了说不清、
        // 一个是压根没判。
        let unjudged = clocks.count - segs.reduce(0) { $0 + $1.1 }
        let total = max(segs.reduce(0) { $0 + $1.1 }, 1)
        return NKCard {
            VStack(alignment: .leading, spacing: 10) {
                Text("本期 \(clocks.count) 例结案 · 四态原样,不折成对错")
                    .nkLabel().foregroundStyle(NK.textTertiary)
                GeometryReader { geo in
                    HStack(spacing: 0) {
                        ForEach(segs, id: \.0) { s in
                            if s.1 > 0 {
                                ZStack {
                                    s.2
                                    Text("\(s.1)").font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(.white)
                                }
                                .frame(width: geo.size.width * CGFloat(s.1) / CGFloat(total))
                            }
                        }
                    }
                }
                .frame(height: 26)                              // 原型 1394 height:26
                .clipShape(RoundedRectangle(cornerRadius: 6))
                NKWrapRow(spacing: 16, lineSpacing: 6) {        // 原型 1400 gap:16
                    ForEach(segs, id: \.0) { s in
                        HStack(spacing: 5) {
                            RoundedRectangle(cornerRadius: 2).fill(s.2)
                                .frame(width: 8, height: 8)
                            // 🔴 图例走**中文**:原型印的是 `verified` / `falsified` 这类
                            // 服务端枚举码(mock 如此),而本项目一贯不把机器码上屏
                            // (`nkBoardLabel` 先例 / §〇c 硬伤 2)。
                            Text("\(nkVerificationStateLabel(s.0)) \(s.1)")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
                if unjudged > 0 {
                    Text("另有 \(unjudged) 篮未给分层准确性判定 —— 「没判」不进上面四态的任何一档")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 结案表(原型 1407–1435:`60px 1fr 130px 100px 90px` 五列)。
    private var clockTableCard: some View {
        NKRowsCard {
            NKRowsHeader {
                Text("档位").frame(width: 60, alignment: .leading)
                Text("篮子").frame(maxWidth: .infinity, alignment: .leading)
                Text("D0 行情状态").frame(width: 130, alignment: .leading)
                Text("引擎").frame(width: 100, alignment: .leading)
                Text("D1 判定").frame(width: 90, alignment: .leading)
            }
            ForEach(Array(model.selectionClocks.enumerated()), id: \.element.id) { idx, c in
                if idx > 0 { Divider().overlay(NK.hairline) }
                HStack(spacing: 0) {
                    NKChip(text: "T\(c.coveredTier)",
                           tone: c.coveredTier == 1 ? .good : .warn, filled: true)
                        .frame(width: 60, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("篮子 #\(c.basketId)").font(NKFont.callout)
                            .foregroundStyle(NK.textPrimary)
                        // K8 验证内容⑧:未触发原因。⛔ 别省 —— 「没触发」与「触发了但判错」
                        // 是两件事,表里只剩一个判定列的话就分不出来了。
                        if let why = c.untriggeredReason, !why.isEmpty {
                            Text("未触发:\(why)").font(NKFont.caption)
                                .foregroundStyle(NK.amber)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    // 🔴 `regimeAtD0` 是**裸英文码**(服务端这条不发 label)→ 展示层换算;
                    // `nil` = D0 当天没有行情状态判定行(缺行 = 不知道,不猜)。
                    Group {
                        if let r = c.regimeAtD0, !r.isEmpty {
                            Text(nkRegimeLabel(r)).foregroundStyle(NK.textSecondary)
                        } else {
                            Text("D0 无判定行").foregroundStyle(NK.textTertiary)
                        }
                    }
                    .font(NKFont.caption).frame(width: 130, alignment: .leading)
                    Text(engineText(c)).font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                        .frame(width: 100, alignment: .leading)
                    Text(c.tierAccuracyLabel).font(NKFont.caption).fontWeight(.semibold)
                        .foregroundStyle(c.tierAccuracyTone.color)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(width: 90, alignment: .leading)
                }
                .padding(.horizontal, 18).padding(.vertical, 11)  // 原型 1414
            }
        }
    }

    /// 「C1 v1.0」。⚠ `engineVersion` 服务端发的就是 `"C1 v1.0"`(**已含引擎码**)——
    /// V2.3.0 写成 `引擎 \(code) \(version)` 打出了「引擎 C1 C1 v1.0」。
    private func engineText(_ c: SelectionClock) -> String {
        if let v = c.engineVersion, !v.isEmpty { return v }
        return c.engineCode ?? "—"
    }

    // MARK: - 交易时钟(V2.2-④-B):**只在实际买入后存在**
    //
    // ⚠ 逐仓明细在「持仓」板块每张持仓卡里(`TradeClockSection`,带写入口);
    // 本页给的是**周度侧的六项归因** + 一行指路。⛔ 不在两处各画一遍逐仓流水。

    @ViewBuilder
    private var tradeClockPage: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            // 原型 1440–1442。⚠ 副标题带 `**加粗**` → 必须是字面量(§五 〇d 第 7 条)。
            VStack(alignment: .leading, spacing: 3) {
                Text("交易时钟").font(NKFont.title1).tracking(-0.4)
                    .foregroundStyle(NK.textPrimary)
                Text("启动的唯一条件是**你真的买了** · 与选股时钟的样本域不同:那边看「选得对不对」,这边看「做得怎么样」")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }
            // 原型 1443–1450:指路卡 `padding:14px 18px; gap:11`。
            NKCard(padding: nil) {
                Button { model.view = .positions } label: {
                    HStack(spacing: 11) {
                        Image(systemName: "stopwatch").font(.system(size: 16))
                            .foregroundStyle(NK.accent)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("逐笔时钟与「补一条主观说明」在持仓卡里")
                                .font(NKFont.body).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            Text("每张持仓卡底部即可 · 系统不会替你写那句话")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        }
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(NK.textTertiary)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if let ov = model.reviewOverview {
                tradeAttributionCard(ov.tradeClock)
                ClockScoreboardCard(segment: ov.tradeClock)
            } else {
                NKCard { NKEmptyState(title: "本次没取到周度归因", systemImage: "exclamationmark.icloud") }
            }
        }
    }

    /// 交易侧**六项归因**(原型 1451–1461 的三列网格)。
    ///
    /// 🔴 **项目名按契约来,⛔ 不照抄原型那六格**:原型画的是「买点偏离中位 / 持有天数
    /// 中位 / 止损执行率 / 离场早于参考 / 违纪笔数 / 主观说明覆盖」—— 那六个量在
    /// `eval/iteration.py::TRADE_ITEMS` 里**一个都不存在**(理想化 mock)。真的六项见
    /// `nkTradeClockItemLabel`。在客户端算出原型那六个数 = 造服务端没给的判定。
    @ViewBuilder
    private func tradeAttributionCard(_ seg: ReviewSegment) -> some View {
        if seg.available {
            let d = seg.detail
            NKCard {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 8) {
                        Text("本期六项归因 · 读周度落盘产物").nkLabel()
                            .foregroundStyle(NK.textTertiary)
                        Spacer(minLength: 0)
                        Text("成交 \(d["trades"]?.intValue ?? 0) 笔 · 在跑 \(d["running"]?.intValue ?? 0) · 已结案 \(d["closed"]?.intValue ?? 0)")
                            .font(NKFont.caption.monospacedDigit())
                            .foregroundStyle(NK.textTertiary)
                    }
                    NKStatGrid {
                        ForEach(tradeItems(d), id: \.key) { it in
                            NKStatCell(title: it.title, value: it.value,
                                       tone: it.tone, footnote: it.footnote)
                        }
                    }
                    if let cov = d["note_coverage"], cov["available"]?.boolValue == true {
                        Divider().overlay(NK.hairline)
                        Text("主观说明覆盖 \(cov["with_note"]?.intValue ?? 0) / \(cov["trades"]?.intValue ?? 0) 笔 —— K8 §十五 的主观说明是唯一的人工输入,覆盖率低只作披露,⛔ 系统不代猜")
                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private struct TradeItem { let key, title, value: String; let tone: NKAxisTone
                               let footnote: String? }

    /// 六项各自的读数。**只读数,⛔ 无阈值、无「好 / 不好」结论**(服务端本就不判)。
    private func tradeItems(_ d: NKJSON) -> [TradeItem] {
        func pct(_ v: Double?) -> String { v.map { NKFmt.pct($0 * 100) } ?? "—" }
        let trades = d["trades"]?.intValue ?? 0
        let thesis = d["thesis_accuracy"], plan = d["plan_consistency"]
        let onThesis = d["exit_quality_on_thesis"], onDecay = d["exit_quality_on_decay"]
        let stop = d["stop_quality_on_failure"]
        let reasons = stop?["by_close_reason"]?.objectValue ?? [:]
        let reasonText = reasons.keys.sorted()
            .map { "\(nkCloseReasonLabel($0)) \(reasons[$0]?.intValue ?? 0)" }
            .joined(separator: " · ")
        return [
            TradeItem(key: "thesis_accuracy", title: nkTradeClockItemLabel("thesis_accuracy"),
                      value: "\(thesis?["with_source_basket"]?.intValue ?? 0) / \(trades)",
                      tone: .neutral, footnote: "有来源篮的笔数 · 对错在选股侧四态"),
            TradeItem(key: "plan_consistency", title: nkTradeClockItemLabel("plan_consistency"),
                      value: pct(plan?["rate"]?.doubleValue), tone: .neutral,
                      footnote: "在建仓区 \(plan?["in_entry_zone"]?.intValue ?? 0)/\(plan?["judged"]?.intValue ?? 0) · 超追价上限 \(plan?["above_max_chase"]?.intValue ?? 0)"),
            TradeItem(key: "exit_quality_on_thesis",
                      title: nkTradeClockItemLabel("exit_quality_on_thesis"),
                      value: pct(onThesis?["rate"]?.doubleValue), tone: .neutral,
                      footnote: "到过参考区间 \(onThesis?["reached_target"]?.intValue ?? 0)/\(onThesis?["judged"]?.intValue ?? 0)"),
            TradeItem(key: "exit_quality_on_decay",
                      title: nkTradeClockItemLabel("exit_quality_on_decay"),
                      value: onDecay?["ratio_median"]?.doubleValue
                          .map { String(format: "%.2f", $0) } ?? "—",
                      tone: .neutral,
                      footnote: "上涨效率中位 · 读数 \(onDecay?["with_efficiency_reading"]?.intValue ?? 0) 笔 · 无阈值"),
            TradeItem(key: "stop_quality_on_failure",
                      title: nkTradeClockItemLabel("stop_quality_on_failure"),
                      value: "\(reasons.values.compactMap { $0.intValue }.reduce(0, +))",
                      tone: .neutral,
                      footnote: reasonText.isEmpty ? "本期无已登记离场原因" : reasonText),
            TradeItem(key: "user_pick_vs_all", title: nkTradeClockItemLabel("user_pick_vs_all"),
                      value: "—", tone: .neutral,
                      footnote: "本期产物未给读数 · 对照走既有 selected_vs_not,⛔ 不另建一份"),
        ]
    }

    // MARK: - 累计:五段 + **四分类建议** + 校准移交件出口

    @ViewBuilder
    private var cumulativePage: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if let ov = model.reviewOverview {
                // 原型 1468–1470。⚠ 翻周器**住列表栏**(原型 1330),⛔ 不在这里再画一遍。
                ReviewPageTitle("累计", subtitle: cumulativeSubtitle(ov))
                CalibrationSegmentCard(segment: ov.calibration)
                // V2.2-④-D 修改建议四分类(K8 §十七)。**只给建议、⛔ 零写回。**
                // ⚠ 紧跟校准段是刻意的:两段吃的是**同一份**周度落盘产物(校准产物的
                // `iteration` 段),四分类是校准结论的直接延伸;画像 / 对账是另外两件
                // 互不相干的事,隔开更不容易被读成"这些数都出自同一处"。
                IterationSegmentCard(segment: ov.iterationSuggestions)
                ProfileSegmentCard(segment: ov.preference)
                ProfileSegmentCard(segment: ov.capability)
                // ⚠ **对账段在这里撤掉**(V2.3.1 批 4):原型的累计页没有它,而「对账」
                // 本来就是自己一页(macOS 是整个工作台)—— 同一份数据画两遍只会让用户
                // 在两处看到可能不同步的两个版本(同 ② 持仓体检、同 ⑨ 逐篮明细那两条)。
                ObservationSegmentCard(segment: ov.observations)
                handoffSection
            } else if model.reviewOverviewLoading {
                NKCard { NKEmptyState(title: "正在取累计复盘…", systemImage: "hourglass") }
            } else {
                // ⚠ 端点恒 200 → 走到这里只可能是**网络 / 鉴权**没通,
                // ⛔ 不是「五段都没有」。
                NKCard {
                    NKEmptyState(title: "本次没取到累计复盘",
                                 subtitle: "这不是「没有累计数据」——是这次没连上服务端。",
                                 systemImage: "exclamationmark.icloud")
                }
            }
        }
    }

    /// 累计页副标题(原型 1470 `2026-08-03 ~ 08-07 · 5 个交易日 / 14 个篮子 / 分层 2 层`)。
    /// **缺哪个不写哪个**,⛔ 不拿 0 冒充「算过了是 0」。
    private func cumulativeSubtitle(_ ov: ReviewOverview) -> String {
        var parts: [String] = []
        if !(ov.weekStart.isEmpty && ov.weekEnd.isEmpty) {
            parts.append("\(ov.weekStart) ~ \(ov.weekEnd)")
        }
        if !ov.weekKey.isEmpty { parts.append(ov.weekKey) }
        let d = ov.calibration.detail
        if ov.calibration.available {
            parts.append("\(d["nTradingDays"]?.intValue ?? 0) 个交易日 / "
                         + "\(d["nBaskets"]?.intValue ?? 0) 个篮子 / "
                         + "分层 \((d["strata"]?.arrayValue ?? []).count) 层")
        }
        return parts.joined(separator: " · ")
    }

    // —— 校准移交件出口(按需拉;⛔ 不随页面自动拉,它要读产物 + 拼 markdown)——

    @ViewBuilder
    private var handoffSection: some View {
        // 原型 1538–1543:**一张可点的白卡**(17px 图标 + 13.5/600 标题 + 11.5 .55 说明),
        // ⛔ 不再是「段头 + 卡 + 一个蓝字链接」三层。
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKCard(padding: nil) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {                        // 原型 1538 gap:12
                        Image(systemName: "square.and.arrow.up").font(.system(size: 17))
                            .foregroundStyle(NK.accent)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("导出校准移交件(.md)").font(NKFont.body).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            Text("窗口与样本量 / 校准报告全文 / 画像两表 / 观察项清单 / 免责 —— 攒够样本后由你带去策略台改包,系统不会自己改。")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .lineSpacing(3).fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 8)
                    }
                    if let h = model.reviewHandoff {
                        handoffBody(h)
                    } else if model.reviewHandoffLoading {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("正在装配移交件…").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        }
                    } else {
                        // 原型 1560 那枚蓝实底主按钮的同一档形状(批 2 动作行先例)。
                        Button { Task { await model.loadReviewHandoff() } } label: {
                            Text("装配移交件").font(NKFont.callout).fontWeight(.semibold)
                                .foregroundStyle(.white)
                                .padding(.horizontal, 14).padding(.vertical, 7)
                                .background(RoundedRectangle(cornerRadius: NKRadius.control)
                                    .fill(NK.accent))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func handoffBody(_ h: ReviewHandoff) -> some View {
        if h.available {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    NKChip(text: h.windowLabel)
                    if !h.generatedAt.isEmpty {
                        Text("生成于 \(h.generatedAt)").font(NKFont.caption)
                            .foregroundStyle(NK.textTertiary)
                    }
                    Spacer()
                }
                // 「这一份带多少样本」先摆出来 —— 样本量是这份材料能不能当依据的前提。
                if !h.sampleN.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(h.sampleN.keys.sorted(), id: \.self) { k in
                            NKChip(text: "\(k) \(h.sampleN[k] ?? 0)")
                        }
                        Spacer()
                    }
                }
                HStack(spacing: 12) {
                    ShareLink(item: h.markdown,
                              preview: SharePreview(h.suggestedFilename)) {
                        HStack(spacing: 4) {
                            Image(systemName: "square.and.arrow.up").font(.system(size: 11))
                            Text("分享").font(NKFont.callout).fontWeight(.semibold)
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    #if os(macOS)
                    Button { saveHandoff(h) } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "arrow.down.doc").font(.system(size: 11))
                            Text("存为 .md").font(NKFont.callout).fontWeight(.semibold)
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    #endif
                    Spacer()
                }
                DisclosureGroup("预览全文(\(h.markdown.count) 字)") {
                    Text(h.markdown).font(NKFont.monoKey)
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                .font(NKFont.caption)
                .foregroundStyle(NK.textSecondary)
            }
        } else {
            // ⛔ 服务端已把两种成因(还没生成 = 会自愈 / 读不出 = 不会自愈)写进
            // `unavailableReason`,这里**原样展示那句话**,别合并成一句「暂不可用」。
            NKReasonEmptyState(title: "本期移交件不可用",
                               reason: h.unavailableReason ?? "未取得原因(服务端未给)",
                               systemImage: "doc.badge.clock")
        }
    }

    #if os(macOS)
    /// 存盘用 `NSSavePanel`(用户自己选位置),⛔ 不往固定目录偷偷写文件。
    private func saveHandoff(_ h: ReviewHandoff) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = h.suggestedFilename
        panel.allowedContentTypes = [.plainText]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try h.markdown.write(to: url, atomically: true, encoding: .utf8)
            model.showToast("已存到 \(url.lastPathComponent)")
        } catch {
            model.showToast("存盘失败:\(error.localizedDescription)", isError: true)
        }
    }
    #endif

    // MARK: - 对账:macOS = 完整工作台(拖入 / 周切换 / 违纪清单);iOS = 只读

    @ViewBuilder
    private var reconcilePage: some View {
        #if os(macOS)
        // ⛔ 不重写:整块复用既有工作台(解析 / FIFO 闭合 / 三查 / 统计全在后端)。
        ReviewWorkbenchView(model: model)
        #else
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            if let ov = model.reviewOverview {
                ReconcileSegmentCard(segment: ov.reconcile, showUploadHint: true)
            } else if model.reviewOverviewLoading {
                NKCard { NKEmptyState(title: "正在取本周对账…", systemImage: "hourglass") }
            } else {
                NKCard {
                    NKEmptyState(title: "本次没取到对账",
                                 subtitle: "这不是「本周没对账」——是这次没连上服务端。",
                                 systemImage: "exclamationmark.icloud")
                }
            }
        }
        #endif
    }
}

/// **带服务端原因的空态**(与 `NKEmptyState` 只差一件事:副文案走 `nkMarkdown`)。
///
/// 🔴 服务端那几句「为什么没取到」是**按 markdown 写的**(`**会自愈**` / `**不补算**` /
/// `**读不出**`),而 `Text(String)` 不解析 Markdown —— 直接喂 `NKEmptyState(subtitle:)`
/// 会把星号原样印在界面上(§五 〇d 第 7 条,V2.3.1 批 4 实拍逮到两处)。
/// ⛔ 不去"清洗"星号:那是删信息;渲染成加粗才是它本来的意思。
struct NKReasonEmptyState: View {
    let title: String
    let reason: String
    var systemImage: String = "questionmark.circle"

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage).font(.system(size: 32))
                .foregroundStyle(NK.textTertiary)
            Text(title).font(NKFont.body).fontWeight(.medium).foregroundStyle(NK.textSecondary)
            Text(nkMarkdown(reason)).font(NKFont.callout).foregroundStyle(NK.textTertiary)
                .multilineTextAlignment(.center).lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }
}

// MARK: - 详情栏页标题 + 行表卡(复盘 / 对账两页共用;**⛔ 不进 SharedUI**:它们只服务
// 这两页,放共享件里两个并行施工的批次会撞同名符号)

/// 详情栏页标题块(原型 1339 / 1387 / 1440 / 1468 / 1548 逐字相同:
/// `26px/700 letter-spacing:-.4` 大标题 + `12px .55 margin-top:3` 副标题)。
/// ⚠ 副标题带 `**加粗**` 的调用点**必须自己写字面量 `Text`**,别走这个 `String` 参数
/// —— `Text(String)` 不解析 Markdown(§五 〇d 第 7 条)。
struct ReviewPageTitle: View {
    let title: String
    var subtitle: String = ""

    init(_ title: String, subtitle: String = "") {
        self.title = title; self.subtitle = subtitle
    }

    var body: some View {
        #if os(iOS)
        // 🔴 **iOS 上收成一行**(V2.3.1 批 7 结转的已知欠账 ①):手机上这一页顶上已经有
        // **系统大标题「复盘」+ 五枚页签 + 一句「这一页答什么」**,再来一个 26px 的页标题
        // 就是**第三层标题**,一屏之内三级标题连着出现。iOS 原型 576–579 行给的是
        // 「`15/700` 段头 + 右端 `11 .40` 的口径」**一行**。
        // ⛔ macOS 不跟着改:那边列表栏与详情栏是两个面,26px 是详情栏的**唯一**标题。
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title).font(NKFont.headline).foregroundStyle(NK.textPrimary)
            Spacer(minLength: 8)
            if !subtitle.isEmpty {
                Text(subtitle).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .multilineTextAlignment(.trailing)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 2)
        #else
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(NKFont.title1).tracking(-0.4).foregroundStyle(NK.textPrimary)
            if !subtitle.isEmpty {
                Text(subtitle).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }
        }
        #endif
    }
}

/// **行表卡**(原型的 `overflow:hidden` 白卡:表头 + 若干行 + `.5px` 行分隔,
/// 行自己贴边到卡沿 —— 1407 选股钟表 / 1500 修改建议表 / 信息卡与对账 419 成交表)。
/// ⚠ 与 `NKCard` 刻意不同:`NKCard` 有 `16/18` 内边距,行表要的是**行自己控制内边距**。
struct NKRowsCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
            .clipShape(RoundedRectangle(cornerRadius: NKRadius.card))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card)
                .stroke(NK.hairline, lineWidth: 0.5))
    }
}

/// 行表表头(原型 1408 `padding:10px 18px 8px; font-size:10.5px .40` + 底 `.5px`)。
struct NKRowsHeader<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) { content }
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .padding(.horizontal, 18).padding(.top, 10).padding(.bottom, 8)
            Divider().overlay(NK.hairline)
        }
    }
}

// MARK: - 昨日复盘一行(原型 1344–1381)

struct BasketReviewRow: View {
    let review: BasketReview

    /// ⚠ 「已验证 / 被证伪」的中文**服务端已经给了**(`verification.label`),缺席才
    /// 用客户端换算 —— ⛔ 不在客户端另建一份枚举中文表。
    private var verificationLabel: String? {
        guard let v = review.verification else { return nil }
        if let l = v["label"]?.stringValue, !l.isEmpty { return l }
        return v["state"]?.stringValue.map(nkVerificationStateLabel)
    }

    private var verificationTone: NKAxisTone {
        switch review.verification?["state"]?.stringValue {
        case "verified": return .good
        case "partial": return .warn
        case "falsified": return .bad
        default: return .neutral
        }
    }

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {   // 原型 1345 margin-bottom:10
                HStack(alignment: .top, spacing: 10) {
                    // 原型 1347 gap:7 —— 名称 15/600 + Tier 实心徽标 + 判定徽标。
                    NKWrapRow(spacing: 7, lineSpacing: 5) {
                        Text(review.name.isEmpty ? review.basketKey : review.name)
                            .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                        if let t = review.tier {
                            NKChip(text: "T\(t)", tone: t == 1 ? .good : .warn, filled: true)
                        }
                        if let l = verificationLabel {
                            NKChip(text: l, tone: verificationTone)
                        }
                    }
                    Spacer(minLength: 8)
                    Text(review.depthLabel).font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary).fixedSize()
                }
                if let text = review.llmText, !text.isEmpty {
                    // §2.7:LLM 叙述**原文整段呈现**,⛔ 不拆解塞回枚举卡片。
                    Text(text).font(NKFont.body).lineSpacing(6)   // 原型 1353 line-height:1.7
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    // 原型 1354–1357:收起成一行「披露 · 参考、非指令」。
                    // ⛔ 「四不」整句仍在展开区里,一个字没删(§2.8 红线)。
                    NKDisclosure(summary: "参考、非指令") { NKReferenceNote() }
                } else if degradedText != nil {
                    // **未生成**(预算耗尽 / 降级)—— ⛔ 不拿空串冒充「生成了但没内容」。
                    Text(degradedText ?? "").font(NKFont.callout)
                        .foregroundStyle(NK.amber).lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if review.llmText?.isEmpty == false, review.degraded {
                    Text("本次复盘降级:人话半份缺席,机械判照出")
                        .font(NKFont.callout).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let obj = review.mech.objectValue, !obj.isEmpty {
                    // 原型 1359 那枚虚线按钮。⚠ 原型只在第三张卡上画了它(mock 如此),
                    // 落地**三张都给** —— 审计入口是资产(同批 2 ③b 行的处置)。
                    NKAuditSection(contains: "展开机械判九项") { NKJSONTable(value: review.mech) }
                }
            }
        }
    }

    /// 未生成 + 降级**合成一句**(原型 1377 就是一句话)。⛔ 两个事实一个都不许省。
    private var degradedText: String? {
        let skip = (review.llmSkipReason ?? "").trimmingCharacters(in: .whitespaces)
        if !skip.isEmpty {
            return "本篮未生成人话复盘:\(skip)"
                + (review.degraded ? " —— 机械判照出,人话半份缺席。" : "")
        }
        return review.degraded ? "本次复盘降级:人话半份缺席,机械判照出" : nil
    }
}

// MARK: - 段壳:三态外壳(**有 / 没有 / 没取到** —— ⛔ 任何一段都不许省掉这一层)

private struct SegmentShell<Content: View>: View {
    let segment: ReviewSegment
    /// `available == false` 时的兜底标题(服务端没给 `label` 时用)。
    var fallbackTitle: String
    var emptyIcon: String = "questionmark.circle"
    /// **内容自己带标题时关掉这一行**(V2.3.1 批 4:原型的卡把段名写在**卡头**里,
    /// 卡外再来一个段头 = 同一个名字连着出现两次)。⚠ `available == false` 时**恒画** ——
    /// 那一屏只剩空态卡,没有标题就不知道是哪一段没取到。
    var showHeader: Bool = true
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            if showHeader || !segment.available {
                NKSectionHeader(title: segment.label.isEmpty ? fallbackTitle : segment.label,
                                trailing: segment.asOf.isEmpty ? nil : segment.asOf)
            }
            if segment.available {
                content
            } else {
                NKCard {
                    // 🔴 **「没取到」的原因原样展示**:服务端已经把"还没生成"(会自愈)与
                    // "读不出"(不会自愈)分成两句话写在这里,⛔ 客户端不合并、不改写。
                    // ⚠ 那两句话是**按 markdown 写的**(`**不补算**` / `**会自愈**`),
                    // 走 `NKEmptyState(subtitle:)` 会把星号原样印上屏(V2.3.1 批 4 实拍
                    // 逮到)→ 这里自绘一版走 `nkMarkdown`。
                    NKReasonEmptyState(title: "本段本次没取到",
                                       reason: segment.unavailableReason ?? "服务端未给原因",
                                       systemImage: emptyIcon)
                }
            }
        }
    }
}

// MARK: - 包成绩单 · 周度校准(读**离线落盘产物**,⛔ 在线不补算)

private struct CalibrationSegmentCard: View {
    let segment: ReviewSegment

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "包成绩单 · 周度校准",
                     emptyIcon: "doc.badge.clock", showHeader: false) {
            let d = segment.detail
            let strata = d["strata"]?.arrayValue ?? []
            if strata.isEmpty {
                NKCard {
                    Text("算过了 · 本期无分层数据(窗口内没有可评的篮子)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(Array(strata.enumerated()), id: \.offset) { _, s in
                    StratumCard(stratum: s, arms: d["placebo"]?.arrayValue ?? [],
                                notes: d["notes"]?.arrayValue ?? [])
                }
            }
            NKAuditSection(contains: "周度校准产物全文(只读冻结件)") {
                NKJSONTable(value: segment.detail)
            }
        }
    }
}

/// 安慰剂对照臂的**一条基准线**(原型 1487–1489:`72px` 名 + `6px` 轨道 + `56px` 右值)。
/// ⛔ 不给"跑赢了就是有效"这类结论 —— 结论那句话由产物自己的 `note` 说。
private struct PlaceboArmBar: View {
    let label: String
    let arm: NKJSON?
    /// 三条共用的满刻度(取三臂中位数绝对值的最大值),没有正数刻度时整条轨道留空。
    let scale: Double
    let primary: Bool

    private var median: Double? { arm?["median"]?.doubleValue }

    var body: some View {
        HStack(spacing: 10) {                                  // 原型 1487 gap:10
            Text(label).font(NKFont.caption)
                .fontWeight(primary ? .semibold : .regular)
                .foregroundStyle(primary ? NK.textPrimary : NK.textSecondary)
                .frame(width: 72, alignment: .leading)         // 原型 width:72
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(NK.textTertiary.opacity(0.15))
                    if let m = median, m > 0, scale > 0 {
                        Capsule().fill(primary ? NK.up : NK.textTertiary.opacity(0.75))
                            .frame(width: geo.size.width * CGFloat(min(m / scale, 1)))
                    }
                }
            }
            .frame(height: 6)                                   // 原型 height:6
            Text(nkPctText(median)).font(NKFont.callout.monospacedDigit())
                .fontWeight(primary ? .semibold : .regular)
                .foregroundStyle(primary ? NK.textPrimary : NK.textSecondary)
                .frame(width: 56, alignment: .trailing)         // 原型 width:56
        }
    }
}

/// 一层成绩单(`骨架 × 引擎 × 版本 × 条件集`)。**版式 = 原型 1473–1491 那张卡**:
/// 卡头(15/600 标题 + 包徽标 + 右端「离线落盘产物 · 在线不补算」)→ 三列读数 →
/// 分隔线 → 安慰剂对照臂三条 → 明细(Tier 单调性 / 四态分布 / 可交易收益口径)。
private struct StratumCard: View {
    let stratum: NKJSON
    var arms: [NKJSON] = []
    var notes: [NKJSON] = []

    private var verification: NKJSON? { stratum["verification"] }
    private var tradable: NKJSON? { stratum["tradable"] }

    /// 三臂共用满刻度(⛔ 不按各自最大值各画各的,那样三条长度不可比 —— 而这三条
    /// 存在的意义就是**互相比**)。**× 1.5 是刻意的**:满刻度取最大值本身的话,最长
    /// 那条会顶到轨道尽头,读起来像"到顶了 / 满分",而这三条只表示相对长短。
    /// (原型 1487–1489 的 66% / 24% / 33% 正好是这个比例。)
    private var armScale: Double {
        let a = arms.first
        let m = ["real", "randomArm", "buyAndHoldArm"]
            .compactMap { a?[$0]?["median"]?.doubleValue }.max() ?? 0
        return m * 1.5
    }

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 8) {                            // 原型 1474 gap:8
                    Text("包成绩单 · 周度校准").font(NKFont.headline)
                        .foregroundStyle(NK.textPrimary)
                    NKChip(text: "包 \(stratum["packVersion"]?.stringValue ?? "—")")
                    NKChip(text: "条件集 \(stratum["rulesetVersion"]?.stringValue ?? "—")")
                    Spacer(minLength: 6)
                    Text("离线落盘产物 · 在线不补算").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary).fixedSize()
                }
                // 原型 1480–1484:三列读数 + 底部一条 `.5px`。
                NKStatGrid {
                    NKStatCell(title: "已验证率",
                               value: nkRatioText(verification?["verified_rate"]?.doubleValue),
                               tone: .good)
                    NKStatCell(title: "被证伪率",
                               value: nkRatioText(verification?["falsified_rate"]?.doubleValue),
                               tone: .bad)
                    NKStatCell(title: "可交易收益中位",
                               value: nkPctText(tradable?["median"]?.doubleValue),
                               tone: (tradable?["median"]?.doubleValue ?? 0) < 0 ? .bad : .good,
                               footnote: "\(stratum["nDays"]?.intValue ?? 0) 日 / \(stratum["nBaskets"]?.intValue ?? 0) 篮")
                }
                Divider().overlay(NK.hairline)
                // 原型 1485–1490:安慰剂对照臂住同一张卡的下半部分。
                VStack(alignment: .leading, spacing: 7) {
                    Text("安慰剂对照臂 · 两条基准线").nkLabel().foregroundStyle(NK.textTertiary)
                    if let a = arms.first {
                        PlaceboArmBar(label: "真实臂", arm: a["real"], scale: armScale, primary: true)
                        PlaceboArmBar(label: "随机篮臂", arm: a["randomArm"], scale: armScale,
                                      primary: false)
                        PlaceboArmBar(label: "满仓持有臂", arm: a["buyAndHoldArm"], scale: armScale,
                                      primary: false)
                        Text("对照臂只是对照 —— 跑赢不等于有效,样本不足以下结论。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        if let note = a["note"]?.stringValue, !note.isEmpty {
                            // 🔴 服务端这句话里带 `**加粗**` —— `Text(String)` 不解析,
                            // V2.3.1 批 4 实拍逮到星号原样上屏。走 `nkMarkdown`。
                            Text(nkMarkdown(note)).font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    } else {
                        Text("本期无对照臂数据").font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                    }
                }
                Divider().overlay(NK.hairline)
                VStack(alignment: .leading, spacing: 8) {
                    tierRow
                    verificationRow
                    tradableRow
                    // ⚠ `NKJSON` 不是 `Hashable`(它能装任意结构),故按下标做 id ——
                    // 产物里的 notes 顺序是确定的,下标当 id 稳定。
                    ForEach(Array(notes.enumerated()), id: \.offset) { _, n in
                        Text("⚠ \(n.displayText)").font(NKFont.caption)
                            .foregroundStyle(NK.amber).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// 🔴 **Tier 单调性按本层数据里实际出现的档位渲染,⛔ 不写死 `(1,2,3)`**
    /// (V2.1-② 同一条教训:写死会在两档时代凭空多一行「T3 —(n=0)」,读起来像
    /// "T3 今天没样本",真相是 T3 已取消 —— 那正是把系统缺席讲成实质性结论;
    /// 而写死 `(1,2)` 又会让历史分层里真实存在的 T3 样本从成绩单上消失)。
    @ViewBuilder
    private var tierRow: some View {
        let t = stratum["tierMonotonicity"]
        let med = t?["median_outcome"]?.objectValue ?? [:]
        let obs = t?["observed"]?.objectValue ?? [:]
        let counts = t?["counts"]?.objectValue ?? [:]
        let tiers = Set(med.keys).union(obs.keys).union(counts.keys)
            .sorted { (Int($0) ?? 0) < (Int($1) ?? 0) }
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text("Tier 单调性").nkLabel().foregroundStyle(NK.textTertiary)
                Text(monotonicText(t?["monotonic"])).font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                Spacer()
            }
            if tiers.isEmpty {
                Text("本层无档位样本").font(NKFont.caption).foregroundStyle(NK.textSecondary)
            } else {
                ForEach(tiers, id: \.self) { k in
                    Text("T\(k) \(nkPctText(med[k]?.doubleValue))(n=\(obs[k]?.intValue ?? 0))")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
            }
            // §2.8-C 红线:Tier = 注意力优先级,不是收益预测 —— 单调性只是观察。
            Text("Tier = 注意力优先级,不是收益预测;单调性只是送进策略线的一个观察")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
    }

    private func monotonicText(_ v: NKJSON?) -> String {
        guard let b = v?.boolValue else { return "判不了(样本不够)" }
        return b ? "成立" : "不成立"
    }

    @ViewBuilder
    private var verificationRow: some View {
        let v = stratum["verification"]
        let dist = v?["distribution"]?.objectValue ?? [:]
        VStack(alignment: .leading, spacing: 2) {
            Text("四态分布").nkLabel().foregroundStyle(NK.textTertiary)
            if !dist.isEmpty {
                // 🔴 **四态码换中文**:V2.3.0 直接印 `falsified 3 · partial 3 · unclear 2`
                // (硬伤 2 同款,V2.3.1 批 4 实拍逮到)。⚠ 顺序按语义排,⛔ 不按字典序 ——
                // 字典序会把「被证伪」排到「已验证」前面。
                Text(["verified", "partial", "unclear", "falsified"]
                        .filter { dist[$0] != nil }
                        .map { "\(nkVerificationStateLabel($0)) \(dist[$0]?.intValue ?? 0)" }
                        .joined(separator: " · "))
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
            }
            // `not_evaluated` **不进分母**:「今天还没判过」不是「判了是 unclear」。
            Text("那一拍没跑过 \(v?["not_evaluated"]?.intValue ?? 0) 篮(不进分母 —— 「还没判过」不是「判了说不清」)")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var tradableRow: some View {
        let tr = stratum["tradable"]
        VStack(alignment: .leading, spacing: 2) {
            Text("可交易收益").nkLabel().foregroundStyle(NK.textTertiary)
            Text("中位 \(nkPctText(tr?["median"]?.doubleValue)) · "
                 + "均值 \(nkPctText(tr?["mean"]?.doubleValue)) · "
                 + "胜率 \(nkRatioText(tr?["win_rate"]?.doubleValue))")
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
            Text("成交 \(tr?["member_fills"]?.intValue ?? 0) 笔 · "
                 + "买不进 \(tr?["member_not_filled"]?.intValue ?? 0) 笔 · "
                 + "窗口未走完 \(tr?["member_unfinished"]?.intValue ?? 0) 笔(后两类不进均值)")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - 画像段(偏好 / 能力两张账形状相同;**缺席 = 「没看」**)

private struct ProfileSegmentCard: View {
    let segment: ReviewSegment

    private var rows: [ProfileRow] { segment.items.map(ProfileRow.init(raw:)) }

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "画像",
                     emptyIcon: "person.crop.circle.badge.questionmark") {
            NKCard {
                if rows.isEmpty {
                    // ⚠ 「算过了但本期没有分组」与上面的「没跑过」讲的是两回事。
                    Text("算过了 · 本期无可展示的分组").font(NKFont.callout)
                        .foregroundStyle(NK.textSecondary)
                } else {
                    VStack(alignment: .leading, spacing: 10) {   // 原型 1521 gap:10
                        ForEach(rows) { row in ProfileRowView(row: row) }
                        NKAuditSection(contains: "逐行原始度量(只读)") {
                            ForEach(rows) { row in
                                NKAuditGroup(title: row.title) {
                                    ForEach(row.metricKeys, id: \.self) { k in
                                        HStack(alignment: .top, spacing: 8) {
                                            Text(k).font(NKFont.monoKey)
                                                .foregroundStyle(NK.textTertiary)
                                            Spacer(minLength: 8)
                                            Text(row.raw[k]?.displayText ?? "—")
                                                .font(NKFont.monoKey)
                                                .foregroundStyle(NK.textSecondary)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private extension ProfileRow {
    /// 🔴 **维度码与分组值都是服务端英文码**(`role` / `leader` / `within_zone` / `tier`),
    /// V2.3.0 直接 `Text("\(row.dimension) · \(row.bucket)")` 印上屏(硬伤 2 同款,
    /// V2.3.1 批 4 实拍逮到)。展示层换算,**未识别值原样透传**。
    var title: String {
        nkProfileDimensionLabel(dimension) + " · "
            + nkProfileValueLabel(dimension: dimension, value: bucket)
    }

    /// 右端那一个数(原型 1524 `13/600 tabular width:60 右对齐`)。
    /// ⚠ **低置信度一律 `—`**:⑫ 验收条款「不把低置信度的数字当结论展示」。
    /// 优先级 = 相对同篮未选 → 胜率 → 占比;⛔ 都没有就 `—`,不硬凑一个数。
    var headlineValue: String {
        if isLowConfidence { return "—" }
        if let v = raw["vs_peer_delta"]?.doubleValue ?? raw["vsPeerDelta"]?.doubleValue {
            return nkPctText(v)
        }
        if let v = raw["win_rate"]?.doubleValue ?? raw["winRate"]?.doubleValue {
            return nkRatioText(v)
        }
        if let v = raw["share"]?.doubleValue { return nkRatioText(v) }
        return "—"
    }

    var headlineCaption: String {
        if isLowConfidence { return "" }
        if raw["vs_peer_delta"] != nil || raw["vsPeerDelta"] != nil { return "相对同篮未选" }
        if raw["win_rate"] != nil || raw["winRate"] != nil { return "胜率" }
        if raw["share"] != nil { return "占比" }
        return ""
    }

    var headlineTone: NKAxisTone {
        guard !isLowConfidence else { return .neutral }
        if let v = raw["vs_peer_delta"]?.doubleValue ?? raw["vsPeerDelta"]?.doubleValue {
            return v > 0 ? .good : (v < 0 ? .bad : .neutral)
        }
        return .neutral
    }
}

/// 画像一行(原型 1523–1529):**每行必带样本量 / 时间范围 / 置信度**;`low` 置信度
/// **只写「样本不足,不给结论」,⛔ 不把数字摆出来当结论**(⑫ 验收条款)。
private struct ProfileRowView: View {
    let row: ProfileRow

    var body: some View {
        HStack(alignment: .center, spacing: 12) {                // 原型 1523 gap:12
            VStack(alignment: .leading, spacing: 1) {
                Text(row.title).font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if row.isLowConfidence {
                    Text("样本不足,不给结论").font(NKFont.caption).fontWeight(.semibold)
                        .foregroundStyle(NK.amber)
                } else {
                    Text("窗口 \(row.windowStart) ~ \(row.windowEnd)")
                        .font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary)
                }
            }
            Spacer(minLength: 8)
            NKChip(text: "样本 \(row.sampleN)", tone: row.isLowConfidence ? .warn : .neutral)
            VStack(alignment: .trailing, spacing: 1) {
                Text(row.headlineValue).font(NKFont.body.monospacedDigit())
                    .fontWeight(.semibold)
                    .foregroundStyle(row.isLowConfidence ? NK.textTertiary
                                                         : row.headlineTone.color)
                if !row.headlineCaption.isEmpty {
                    Text(row.headlineCaption).font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
            }
            .frame(width: 72, alignment: .trailing)              // 原型 width:60(+ 说明行)
        }
    }
}

// MARK: - 对账段(**缺席 = 「没有」,不是「没看」** —— 与画像段刻意判得不一样)

private struct ReconcileSegmentCard: View {
    let segment: ReviewSegment
    /// iOS 对账页要多说一句「上传在 macOS 端做」;累计页里的这一段不必重复。
    let showUploadHint: Bool

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "交割单对账", emptyIcon: "exclamationmark.icloud") {
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    if segment.found == true {
                        foundBody
                    } else {
                        // 🔴 这是**「没有」**:系统查过 `reviews` 表、确实没有这一行 ——
                        // 必需输入(券商交割单)只能由用户给,系统补不出没上传的那一份。
                        // ⛔ 别把它渲染成故障 / 「没取到」。
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "tray").foregroundStyle(NK.textTertiary)
                            Text(segment.note ?? "本周尚未上传交割单")
                                .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                    if showUploadHint {
                        Text("交割单上传请在 macOS 端做(拖入 .xlsx → 自动对账);"
                             + "iPhone 上只做只读查看。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// 已有该周对账 → **只读**展示要点(违纪清单是这一页的主角)。
    /// ⛔ 客户端不重算任何判定(解析 / FIFO 闭合 / 三查 / 章程判定全在后端)。
    @ViewBuilder
    private var foundBody: some View {
        let result = segment.detail["result"]
        let violations = (result?["disciplineViolations"]?.arrayValue ?? [])
            .compactMap { $0.stringValue }
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text("第 \(segment.detail["week"]?.stringValue ?? segment.asOf) 周")
                    .font(NKFont.callout).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
                if result?["forcedReview"]?.boolValue == true {
                    NKChip(text: "触发强制复盘", tone: .bad, filled: true)
                }
                Spacer()
                if let g = segment.detail["generatedAt"]?.stringValue, !g.isEmpty {
                    Text(g).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
            }
            Text("违纪清单 \(violations.count) 条").font(NKFont.caption).fontWeight(.bold)
                .foregroundStyle(NK.textTertiary)
            if violations.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.seal.fill").foregroundStyle(NK.up)
                    Text("本周未发现违纪").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(Array(violations.enumerated()), id: \.offset) { _, v in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 10))
                            .foregroundStyle(NK.down).padding(.top, 2)
                        Text(v).font(NKFont.callout).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if let m = segment.detail["material"]?.stringValue, !m.isEmpty {
                DisclosureGroup("复盘材料") {
                    Text(m).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
        }
    }
}

// MARK: - 观察项段(**等证据的策略问题清单**,⛔ 不是待办、不给建议)

private struct ObservationSegmentCard: View {
    let segment: ReviewSegment

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "观察项 · 等证据的策略问题",
                     emptyIcon: "questionmark.circle") {
            if segment.items.isEmpty {
                NKCard {
                    Text("本期无登记的观察项").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(segment.items.map(ReviewObservation.init(raw:))) { o in
                    NKCard {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                NKChip(text: o.obsId)
                                Text(o.title).font(NKFont.callout).fontWeight(.semibold)
                                    .foregroundStyle(NK.textPrimary)
                                Spacer()
                            }
                            if !o.question.isEmpty {
                                Text(o.question).font(NKFont.caption)
                                    .foregroundStyle(NK.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !o.evidenceNeeded.isEmpty {
                                Text("需要的证据:\(o.evidenceNeeded)")
                                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !o.status.isEmpty {
                                Text("状态:\(o.status)").font(NKFont.caption)
                                    .foregroundStyle(NK.amber)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
        }
    }
}

// ⚠ **`SelectionClockRow`(每篮一张大卡)已随 V2.3.1 批 4 删除** —— 原型 1407–1435 给的是
// **一张五列表**(档位 / 篮子 / D0 行情状态 / 引擎 / D1 判定),三篮三张卡在桌面上一屏只看得
// 到两条。逐篮的九项验证冻结件、结案时刻、骨架 / 条件集版本**一条没丢**,全在
// `selectionClockPage` 那个 `NKAuditSection` 里(⛔ 审计件不许随版式一起消失)。

// MARK: - 双时钟的周度归因段(读**周度落盘产物**,⛔ 在线不补算)

private struct ClockScoreboardCard: View {
    let segment: ReviewSegment

    var body: some View {
        // ⚠ 段名就是这一页的标题(「选股时钟」/「交易时钟」),⛔ 不在页内再挂一个同名
        // 段头 —— 故 `showHeader: false`;段**没取到**时 `SegmentShell` 仍会画出标题,
        // 否则那张空态卡不知道是哪一段没取到。
        SegmentShell(segment: segment, fallbackTitle: "时钟归因",
                     emptyIcon: "doc.badge.clock", showHeader: false) {
            NKAuditSection(contains: "本期归因产物全文(只读冻结件)") {
                NKJSONTable(value: segment.detail)
            }
        }
    }
}

// MARK: - V2.2-④-D 修改建议四分类(K8 §十七)
//
// 🔴 **只给建议,⛔ 零写回**(V2.1 裁定 #3 一字不变):系统攒证据、**用户拍板改包**。
// 唯一通道 = 带着移交件去策略台 → 新引擎版本 → 四道闸激活。
//
// 🔴🔴 **`klass == nil` + `klassStatus == "thresholds_undecided"` 是设计中的状态**:
// K8 §十七 只给了四个类别的定性描述,**没有给「多少样本算够」「差多少算失效」这两个数**。
// ⛔ **绝不许把它渲染成「暂无建议」或空白** —— 那会把「还没决定」讲成「没问题」;
// ⛔ 也不许渲染成「观察」——「还没决定」与「样本不足」是两件不同的事。
// 界面必须当面说:**缺的是哪两个数、由谁定、定完怎么生效**。

private struct IterationSegmentCard: View {
    let segment: ReviewSegment

    private var rows: [IterationSuggestion] { segment.items.map(IterationSuggestion.init(raw:)) }
    private var undecided: Bool { rows.contains(where: { $0.thresholdsUndecided }) }
    private var thresholds: NKJSON? { segment.detail["thresholds"] }

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "修改建议 · 保留 / 观察 / 降权 / 淘汰",
                     emptyIcon: "slider.horizontal.3", showHeader: false) {
            // 原型 1495–1518:**一张琥珀描边卡**(头块琥珀淡底 + 表)。
            NKRowsCard {
                header
                if rows.isEmpty {
                    Text("算过了 · 本期没有可分类的因素(窗口内没有结案样本)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .padding(.horizontal, 18).padding(.vertical, 14)
                } else {
                    NKRowsHeader {
                        Text("因素").frame(maxWidth: .infinity, alignment: .leading)
                        Text("样本 n").frame(width: 90, alignment: .leading)
                        Text("命中率").frame(width: 90, alignment: .leading)
                        Text("建议分类").frame(width: 96, alignment: .leading)
                    }
                    ForEach(Array(rows.enumerated()), id: \.element.id) { idx, r in
                        if idx > 0 { Divider().overlay(NK.hairline) }
                        row(r)
                    }
                }
            }
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card)
                .stroke(NK.amber.opacity(0.30), lineWidth: 0.5))   // 原型 1495
            NKAuditSection(contains: "逐因素建议全文、本期分界线、免责") { auditBody }
        }
    }

    /// 🔴 卡头那一段**本身就是产品要说的话**,⛔ 不许省(原型 1496–1499)。
    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                if undecided {
                    // 🔴 **不是「暂无建议」**:统计量有,缺的是那两个数。
                    NKChip(text: "分界线未定 · 待你拍板", tone: .warn, filled: true)
                }
                Text("修改建议 · 保留 / 观察 / 降权 / 淘汰")
                    .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                Spacer(minLength: 0)
            }
            if undecided {
                Text("统计量**已经算出来了**(下面每行的 n / 命中率 / 相对基线 / 安慰剂对照都是真的),缺的是两个数:**多少样本算够**、**差多少算失效**。这两个数由你定,定完下次校准自动按它分类。⛔ 这不是「暂无建议」,也不是「观察」。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 3) {
                    numbered("min_n", "低于它一律判「观察:样本不足」——「多少样本算够」")
                    numbered("retire_min_n", "判「淘汰:持续失效」所需的最低样本量——「差多少算失效」")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18).padding(.vertical, 13)           // 原型 1496
        .background(NK.amber.opacity(undecided ? 0.07 : 0))
        .overlay(alignment: .bottom) {
            Rectangle().fill(NK.amber.opacity(undecided ? 0.20 : 0.10)).frame(height: 0.5)
        }
    }

    private func row(_ r: IterationSuggestion) -> some View {
        HStack(alignment: .top, spacing: 0) {
            VStack(alignment: .leading, spacing: 2) {
                // ⚠ `factor` 是**机器标识符**(`regime=trend_continuation`),原型同样用
                // 等宽体呈现 —— 等宽 = 「这是机器的名字」,⛔ 别翻译成中文假装是人话。
                Text(r.factor).font(NKFont.monoValue).foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(evidenceLine(r)).font(NKFont.caption)
                    .foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text("\(r.n)").font(NKFont.callout.monospacedDigit())
                .foregroundStyle(NK.textSecondary).frame(width: 90, alignment: .leading)
            Text(r.accuracy.map { NKFmt.pct($0 * 100) } ?? "—")
                .font(NKFont.callout.monospacedDigit())
                .foregroundStyle(NK.textSecondary).frame(width: 90, alignment: .leading)
            Group {
                if r.thresholdsUndecided {
                    Text("待你拍板").foregroundStyle(NK.amber)
                } else if let label = r.klassLabel {
                    Text(label).foregroundStyle(r.klassTone.color)
                } else {
                    Text("未给分类").foregroundStyle(NK.textTertiary)
                }
            }
            .font(NKFont.caption).fontWeight(.semibold)
            .fixedSize(horizontal: false, vertical: true)
            .frame(width: 96, alignment: .leading)
        }
        .padding(.horizontal, 18).padding(.vertical, 10)           // 原型 1506
    }

    /// 证据一行(相对基线 / 安慰剂对照 / 引擎)。⚠ **安慰剂判定是英文码**
    /// (`better` / `inconclusive` / `unavailable`)→ 展示层换算,⛔ 不上屏。
    private func evidenceLine(_ r: IterationSuggestion) -> String {
        var parts: [String] = []
        if let e = r.engineVersion ?? r.engineCode, !e.isEmpty { parts.append(e) }
        if let d = r.delta { parts.append(String(format: "相对基线 %+.3f", d)) }
        if let e = r.placeboEdge, !e.isEmpty { parts.append("安慰剂 " + nkPlaceboEdgeLabel(e)) }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var auditBody: some View {
        if let t = thresholds {
            NKAuditGroup(title: "本期分界线") {
                if t["available"]?.boolValue == false,
                   let why = t["unavailableReason"]?.stringValue, !why.isEmpty {
                    // 服务端整句写好了(带 markdown)—— **原样展示**。
                    Text(nkMarkdown(why)).font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    NKJSONTable(value: t)
                }
            }
        }
        ForEach(rows) { r in
            if !r.suggestion.isEmpty {
                NKAuditGroup(title: r.factor) {
                    // 服务端把「缺哪两个数、该怎么定」整句写好了 —— **原样展示**。
                    Text(nkMarkdown(r.suggestion)).font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        if let d = segment.detail["disclaimer"]?.stringValue, !d.isEmpty {
            NKAuditGroup(title: "免责") {
                Text(nkMarkdown(d)).font(NKFont.caption)
                    .foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        NKAuditGroup(title: "改包唯一通道") {
            Text("你带材料去策略台 —— 系统不会自己改选股包。")
                .font(NKFont.caption).fontWeight(.semibold).foregroundStyle(NK.textSecondary)
        }
    }

    private func numbered(_ key: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(key).font(NKFont.monoKey).fontWeight(.bold)
                .foregroundStyle(NK.accent)
            Text(text).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - 小工具:比率 / 百分数的缺席安全格式化

/// 比例(0.0123)→ 百分数文本;`nil` → `—`(**⛔ 不显示 0.00%**:那是个实质性判断)。
func nkPctText(_ v: Double?) -> String {
    guard let v else { return "—" }
    return NKFmt.signedPct(v * 100)
}

/// 比率(0.4)→ 百分比文本(无符号);`nil` → `—`。
func nkRatioText(_ v: Double?) -> String {
    guard let v else { return "—" }
    return NKFmt.pct(v * 100)
}
