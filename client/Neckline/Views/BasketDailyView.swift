//
//  BasketDailyView.swift
//  Neckline — 🔴 **本文件 = 选股板块**(V2.1 三板块之一;板块名由
//  `AppTab.baskets.title` 单点定义)。⚠ **文件名刻意不改**(改名要同步 `project.yml`
//  + `pbxproj`,收益低风险高)—— 文件叫 `BasketDaily`,板块叫「选股」,不冲突。
//
//  渲染 `GET /report/latest` 的**篮子日报**:
//    ① 情绪与市场语境 → ②(持仓体检见「持仓」板块)→ ③ 今日篮子(每篮一张卡)
//    → ③b 未定档篮子 → ⑤ 数据新鲜度与降级披露
//
//  ⚠ **④ 昨日篮子复盘已迁往「复盘」板块的「每日」页**(V2.1-⑦):数据源仍是同一份
//  `model.basketDaily.reviews`(**零新增网络调用、服务端零改动**),只是换了挂载点 ——
//  ⛔ 不是删掉,更不许在两处各画一遍。
//
//  🔴 **段号与段名一字不动**(「① 情绪与市场语境」「③ 今日篮子」「③b 未定档篮子」
//  「⑤ 数据新鲜度与降级披露」):它们与**服务端 markdown 报告同构**,是审计锚,改了
//  客户端就与历史报告对不上。**板块名是导航语义,段名是报告结构,两回事。**
//
//  ⚠ **V2.3 视觉升级(规范 §01 决定 01 / 04 / 05 / 06)**:
//    · macOS 改「**列表栏 376 + 详情栏自适应**」——左边列篮子(**行下面直接列成员**),
//      右边是选中那一篮的**篮子卡**(⛔ 不再是 700×780 的 `.sheet`);没选时右边是
//      **今日概览**(行情状态 / ① / 情报 / ⑤)。iOS 保留 sheet(手机并排放不下)。
//    · 披露文案收进 `NKDisclosure`,**一字未改**;原始件下沉 `NKAuditSection`。
//  ⛔ **内容不再硬性居中在 900px** —— 那正是这次要改掉的东西。
//
//  **展示纪律(⑭-C 对拍表 §六.5,逐条守)**:
//   E1 空档位如实显示「今日 T1 为空」,⛔ 不隐藏。
//   E2 ③b 两个原因码**分开展示**,⛔ 不合并成「未入选」;零溢出时**这一节仍在**。
//   E3 每个 `*Available=false` 与「空数组 + available=true」**讲不同的话**。
//   E4 参考件每处带「参考、非指令」;**离场参考区间不许写成止盈线**。
//   E5 角色对拍分歧**两说并存**,⛔ 不挑一个当正确答案。
//   E7 `board` 是英文枚举码,中文展示走客户端 `nkBoardLabel` 纯展示层换算。
//  **语义红线(§2.8-C)**:Tier / 排序 = **注意力优先级,不是收益预测**;T1 ≠ 最会涨;
//  终选权在用户。⛔ 禁「推荐买入 / 建议买入 / 看好 / 值得买」类表述。
//
//  ⚠ **② 持仓体检刻意不在本页重画**:D8 把持仓拆成了独立板块,同一份数据画两遍只会
//  让用户在两处看到可能不同步的两个版本。本页给一行入口指过去。
//

import SwiftUI

struct BasketDailyView: View {
    @Bindable var model: AppModel

    private var daily: BasketDaily { model.basketDaily }
    private var selectedBasket: Basket? { model.openedBasketId.flatMap { model.basket(byID: $0) } }

    var body: some View {
        #if os(iOS)
        iosBody
        #else
        macBody
        #endif
    }

    // MARK: - iOS:单列滚动(概览压成一张卡);篮子卡走 sheet

    #if os(iOS)
    private var iosBody: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    if model.report.degraded {
                        reportNotReadyCard
                    } else {
                        compactOverviewCard
                        reviewPointer
                        basketsSection
                        droppedSection
                    }
                    freshnessSection      // ⑤ **恒在**(在 degraded 分支之外)
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            // 页面标题跟着板块名走(单点定义在 `AppTab.baskets.title`)——
            // ⛔ 别在这里再写一个字面量,同屏两个名字是改名最容易漏的那一处。
            .navigationTitle(AppTab.baskets.title)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await model.refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .refreshable { await model.refresh() }
        }
        .sheet(item: Binding(get: { selectedBasket },
                             set: { if $0 == nil { model.dismissBasket() } })) { basket in
            BasketCardPage(model: model, basket: basket)
        }
        .sheet(item: Binding(get: { model.infoCardRequest },
                             set: { if $0 == nil { model.dismissInfoCard() } })) { req in
            InfoCardPageView(model: model, request: req)
        }
    }
    #endif

    // MARK: - macOS:列表栏 376 + 详情栏自适应

    #if os(macOS)
    private var macBody: some View {
        NKSplitLayout {
            listColumn
        } detail: {
            detailColumn
        }
        .sheet(item: Binding(get: { model.infoCardRequest },
                             set: { if $0 == nil { model.dismissInfoCard() } })) { req in
            InfoCardPageView(model: model, request: req).frame(width: 660, height: 740)
        }
    }

    // —— 列表栏 ——

    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            Text(AppTab.baskets.title).font(NKFont.title2).foregroundStyle(NK.textPrimary)
                .padding(.horizontal, 6).padding(.bottom, 4)
            Text(listSubtitle).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                .padding(.horizontal, 6).padding(.bottom, 8)

            overviewRow
            if !model.report.degraded {
                reviewReceiptRow
                Spacer().frame(height: 10)
                basketsListSection
                Spacer().frame(height: 10)
                droppedListSection
            }
        }
    }

    private var listSubtitle: String {
        if model.report.degraded { return emptyTitle(model.report.reason) }
        return "今日 \(daily.baskets.count) 篮定档 · \(daily.droppedBaskets.count) 篮未定档"
    }

    /// 「今日概览」入口行(选中 = 详情栏显示概览)。
    private var overviewRow: some View {
        NKListRow(selected: model.openedBasketId == nil) {
            model.dismissBasket()
        } content: {
            HStack(spacing: 8) {
                Image(systemName: "square.grid.2x2").font(.system(size: 12))
                    .foregroundStyle(NK.accent)
                Text("今日概览").font(NKFont.body).fontWeight(.semibold)
                    .foregroundStyle(NK.textPrimary)
                Spacer()
                Text("①·情报·⑤").font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }

    /// 「昨日回执」摘要行 —— ⛔ 只给回执,逐篮明细在复盘板块(不在两处各画一遍)。
    private var reviewReceiptRow: some View {
        NKListRow(selected: false) {
            model.view = .review
            model.reviewPage = .daily
        } content: {
            HStack(spacing: 8) {
                Image(systemName: AppTab.review.systemImage).font(.system(size: 12))
                    .foregroundStyle(NK.accent)
                VStack(alignment: .leading, spacing: 1) {
                    Text("昨日回执").font(NKFont.body).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Text(reviewPointerCaption).font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 10))
                    .foregroundStyle(NK.textTertiary)
            }
        }
    }

    @ViewBuilder
    private var basketsListSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            HStack(spacing: 6) {
                Text("③ 今日篮子 \(daily.baskets.count)").font(NKFont.title3)
                    .foregroundStyle(NK.textPrimary)
                Spacer()
            }
            .padding(.horizontal, 6)
            // 语义红线句(§2.8-C):**排序不是收益预测**。
            Text("Tier / 档内次序 = 注意力优先级,不是收益预测 · T1 ≠ 最会涨 · 终选权在你")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .padding(.horizontal, 6).padding(.bottom, 6)
                .fixedSize(horizontal: false, vertical: true)

            if !daily.basketsAvailable {
                // E3:**「本次没取到」与「今天真没有」讲不同的话**。
                unavailableRow(title: "本次没取到今日篮子",
                               detail: daily.basketsUnavailableReason.map { "原因:\($0)" }
                                   ?? "这一段本次未取到(不是「今天没有篮子」)")
            } else {
                // 🔴 档位清单 = **现役两档 ∪ 本份快照实际出现的档位**(V2.1-② 移交 ⑦ 的
                // 硬约束,判据与理由见 `BasketDaily.displayTiers`)——⛔ 既不许写死
                // `[1,2]`(历史 T3 回放会在客户端消失)、也不许写死 `[1,2,3]`(新报告
                // 凭空多一个恒空 T3 分组,把系统缺席讲成市场结论)。
                ForEach(daily.displayTiers, id: \.self) { tier in
                    tierListBlock(tier: tier, baskets: daily.baskets(tier: tier))
                }
            }
        }
    }

    @ViewBuilder
    private func tierListBlock(tier: Int, baskets: [Basket]) -> some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            HStack(spacing: 6) {
                NKChip(text: "T\(tier)", tone: tier == 1 ? .good : (tier == 2 ? .warn : .neutral),
                       filled: true)
                Text(tierCaption(tier)).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                Spacer()
            }
            .padding(.horizontal, 6).padding(.top, 6).padding(.bottom, 2)

            if baskets.isEmpty {
                // E1:**空档位如实显示,⛔ 不隐藏**。
                unavailableRow(title: "今日 T\(tier) 为空",
                               detail: "算过了,今天没有达到该档标准的篮子", tone: .neutral)
            } else {
                ForEach(baskets) { b in
                    BasketListRow(model: model, basket: b,
                                  selected: model.openedBasketId == b.basketId)
                }
            }
        }
    }

    @ViewBuilder
    private var droppedListSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            Text("③b 今日未定档篮子 \(daily.droppedBaskets.count)").font(NKFont.title3)
                .foregroundStyle(NK.textPrimary)
                .padding(.horizontal, 6).padding(.bottom, 4)
            droppedRows
        }
    }
    #endif

    // MARK: - 详情栏 / iOS 概览(两端共用的「今日概览」内容)

    #if os(macOS)
    @ViewBuilder
    private var detailColumn: some View {
        if let b = selectedBasket {
            BasketCardPage(model: model, basket: b)
        } else if model.report.degraded {
            reportNotReadyDetail
        } else {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                Text("今日概览").font(NKFont.title1).foregroundStyle(NK.textPrimary)
                // 🔴 V2.2-② 行情状态条。**纯展示、零动作**;`available=false` 时如实说
                // 「本段未取得」,⛔ 不静默省略。⚠ 它**不是**「① 情绪与市场语境」的替代 ——
                // 两者一个讲市场结构、一个讲情绪读数,段名各自保留(段名是审计锚)。
                MarketRegimeStrip(regime: model.marketRegime)
                if !model.report.missedEntryHint.isEmpty {
                    MissedEntryHintBanner(text: model.report.missedEntryHint)
                }
                sentimentSection          // ① 情绪与市场语境
                holdingCheckupPointer     // ②(指向持仓板块)
                IntelPackageView(report: model.report)
                freshnessSection          // ⑤ 数据新鲜度与降级披露(**恒在**)
            }
        }
    }

    /// 报告未生成时的详情栏。**把「还没跑」和「跑了、今天真没有」分开**:
    /// 这一屏说的是「还没跑到那一步」,后者会给一份完整报告、里面写着「今日 T1 为空」。
    private var reportNotReadyDetail: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            Text("今日概览").font(NKFont.title1).foregroundStyle(NK.textPrimary)
            reportNotReadyCard
            pipelineScheduleCard
            freshnessSection
        }
    }
    #endif

    private var reportNotReadyCard: some View {
        NKCard {
            NKEmptyState(title: emptyTitle(model.report.reason),
                         subtitle: "策略引擎已在跑,今晚 16:35 出计划后自动显示。",
                         systemImage: "moon.zzz")
        }
    }

    /// 今晚的流水线。🔴 **这是排程表(timer 的固定时刻),⛔ 不是实时进度** ——
    /// 客户端目前没有任何通道能观察到批算跑到哪了(规范 §08 第 2 条:那需要一个
    /// **不依赖报告快照**的批算状态端点)。⛔ 在拿到那个端点之前,这里画进度条
    /// 就是把「没看」讲成「看到了」。
    private var pipelineScheduleCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Text("今晚的流水线").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    Spacer()
                    Text("排程表 · 非实时状态").font(NKFont.caption).foregroundStyle(NK.amber)
                }
                ForEach(Self.pipelineSteps, id: \.0) { step in
                    HStack(alignment: .top, spacing: 10) {
                        Text(step.0).font(NKFont.callout.monospacedDigit())
                            .foregroundStyle(NK.textTertiary).frame(width: 44, alignment: .leading)
                        Text(step.1).font(NKFont.body).foregroundStyle(NK.textSecondary)
                        Spacer(minLength: 0)
                    }
                }
                NKDisclosure(summary: "这条流水线是排程表,不是进度条", tone: .warn) {
                    Text("上面是 systemd timer 的固定时刻表 —— 它说的是「按计划几点会跑」,"
                         + "⛔ 不是「现在跑到哪了」。客户端目前没有观察批算进度的通道。")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("16:35 是晚间链**启动**时刻,不是报告落地时刻。")
                    Text("这一屏说的是「还没跑到那一步」—— 与「跑了、今天真没有篮子」是两回事:"
                         + "后者会给你一份完整报告,里面写着「今日 T1 为空(算过了,今天没有达到该档标准的篮子)」。")
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private static let pipelineSteps: [(String, String)] = [
        ("15:00", "收盘"),
        ("15:05", "盘中存拍"),
        ("16:05", "日更:EOD → Parquet"),
        ("16:35", "晚间链启动 · 三段串行(扫描层批算 → 篮子 · LLM → 复盘 + 报告 + 推送)"),
    ]

    private func emptyTitle(_ reason: String) -> String {
        switch reason {
        case "no_report": return "今日报告尚未生成"
        case "bad_date", "not_loaded": return "暂无数据"
        default: return "暂无数据(\(reason))"
        }
    }

    // MARK: - iOS 概览卡(桌面版「今日概览」在手机上压成一张卡)

    #if os(iOS)
    @ViewBuilder
    private var compactOverviewCard: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if !model.report.tradeDate.isEmpty {
                Text(metaLine).font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textSecondary)
            }
            MarketRegimeStrip(regime: model.marketRegime, compact: true)
            if !model.report.missedEntryHint.isEmpty {
                MissedEntryHintBanner(text: model.report.missedEntryHint)
            }
            sentimentSection
            holdingCheckupPointer
            IntelPackageView(report: model.report)
        }
    }

    private var metaLine: String {
        "交易日 \(model.calendar.displayString(model.report.tradeDate)) · 章程 \(model.report.strategyVersion)"
            + (daily.packVersion.map { " · 选股包 \($0)" } ?? "")
    }
    #endif

    // MARK: - ① 情绪与市场语境

    @ViewBuilder
    private var sentimentSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKSectionHeader(title: "① 情绪与市场语境")
            if let s = model.report.sentiment {
                SentimentCard(sentiment: s)
            } else {
                NKCard { NKEmptyState(title: "本次没有情绪仪表盘数据", systemImage: "gauge") }
            }
            if !model.report.sectors.isEmpty {
                SectorChipsRow(sectors: model.report.sectors)
            }
        }
    }

    // MARK: - ②(持仓体检:指过去,不重画)

    private var holdingCheckupPointer: some View {
        NKCard {
            Button { model.view = .positions } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chart.line.uptrend.xyaxis").foregroundStyle(NK.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("② 持仓体检").font(NKFont.body).fontWeight(.semibold)
                            .foregroundStyle(NK.textPrimary)
                        Text("持仓 \(model.positions.count) 笔 · 在「持仓」板块查看(先管住手里的)")
                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 11))
                        .foregroundStyle(NK.textTertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - ③ 今日篮子(iOS:卡片流)

    #if os(iOS)
    @ViewBuilder
    private var basketsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKSectionHeader(title: "③ 今日篮子 \(daily.baskets.count)",
                            trailing: "Tier = 注意力优先级")
            Text("Tier / 档内次序 = 注意力优先级,不是收益预测 · T1 ≠ 最会涨 · 终选权在你")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            if !daily.basketsAvailable {
                NKCard {
                    NKEmptyState(title: "本次没取到今日篮子",
                                 subtitle: daily.basketsUnavailableReason.map { "原因:\($0)" }
                                     ?? "这一段本次未取到(不是「今天没有篮子」)",
                                 systemImage: "exclamationmark.icloud")
                }
            } else {
                ForEach(daily.displayTiers, id: \.self) { tier in
                    tierBlock(tier: tier, baskets: daily.baskets(tier: tier))
                }
            }
        }
    }

    @ViewBuilder
    private func tierBlock(tier: Int, baskets: [Basket]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                NKChip(text: "T\(tier)", tone: tier == 1 ? .good : (tier == 2 ? .warn : .neutral),
                       filled: true)
                Text(tierCaption(tier)).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                Spacer()
            }
            if baskets.isEmpty {
                NKCard {
                    HStack(spacing: 8) {
                        Image(systemName: "tray").foregroundStyle(NK.textTertiary)
                        Text("今日 T\(tier) 为空(算过了,今天没有达到该档标准的篮子)")
                            .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        Spacer()
                    }
                }
            } else {
                ForEach(baskets) { b in
                    BasketListRow(model: model, basket: b, selected: false)
                }
            }
        }
    }
    #endif

    private func tierCaption(_ tier: Int) -> String {
        switch tier {
        case 1: return "最先看的一档"
        case 2: return "次一档"
        // ⚠ T3 **只可能出现在历史报告回放里**(V2.1-② 起引擎不再产生第三档;
        // `displayTiers` 的并集只在快照真有该档篮子时才把它加进来,所以这一分组
        // **永远不会以"空档"的样子出现**)。如实标明,免得看老报告的人以为它还活着。
        case 3: return "留作对照的一档 · V2.1 起已取消(历史报告回放)"
        default: return "历史档位(现役引擎不产生)"
        }
    }

    // MARK: - ③b 未定档篮子(**零溢出时这一节仍在**)

    @ViewBuilder
    private var droppedSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKSectionHeader(title: "③b 今日未定档篮子 \(daily.droppedBaskets.count)")
            droppedRows
        }
    }

    @ViewBuilder
    private var droppedRows: some View {
        if !daily.droppedBasketsAvailable {
            unavailableRow(title: "本次没跑未定档统计",
                           detail: daily.droppedBasketsUnavailableReason.map { "原因:\($0)" }
                               ?? "这一段本次未取到(不是「今天零溢出」)")
        } else if daily.droppedBaskets.isEmpty {
            unavailableRow(title: "算过了 · 今日零未定档篮子", detail: nil, tone: .neutral)
        } else {
            // E2:**每个原因码语义不同,分开展示**(V2.2-③ 起共 9 码,
            // 「位置不合适」「无引擎线」「档位已满」讲的是完全不同的三件事)。
            ForEach(daily.droppedBaskets) { d in
                DroppedBasketRow(dropped: d)
            }
        }
    }

    /// 列表栏 / 卡片流通用的「这一段没有内容」一行。
    private func unavailableRow(title: String, detail: String?,
                                tone: NKAxisTone = .warn) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: tone == .warn ? "exclamationmark.icloud" : "checkmark.seal")
                .font(.system(size: 11)).foregroundStyle(tone == .warn ? NK.amber : NK.textTertiary)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                if let d = detail {
                    Text(d).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.inner).stroke(NK.hairline, lineWidth: 0.5))
    }

    // MARK: - ④(昨日篮子复盘已迁往复盘板块 · 每日页;这里只留一行入口,⛔ 不重画)
    //
    // 同 ② 持仓体检那条:**同一份数据画两遍只会让用户在两处看到可能不同步的两个版本**。
    // 数据源没变(仍是 `model.basketDaily.reviews`,随报告冻结),换的是挂载点。

    private var reviewPointer: some View {
        NKCard {
            Button {
                model.view = .review
                model.reviewPage = .daily
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: AppTab.review.systemImage).foregroundStyle(NK.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("④ 昨日篮子复盘").font(NKFont.body).fontWeight(.semibold)
                            .foregroundStyle(NK.textPrimary)
                        Text(reviewPointerCaption)
                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 11))
                        .foregroundStyle(NK.textTertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    /// ⚠ 入口这一行也得**三态分开说**(⛔ 不许统一成「\(n) 篮」):本次没跑复盘 /
    /// 昨日无篮子可复盘 / 有 —— 详细三态在复盘板块每日页逐字保留,这里是它的缩写。
    private var reviewPointerCaption: String {
        if !daily.reviewsAvailable { return "本次没跑复盘 · 在「复盘 · 每日」查看原因" }
        if daily.reviews.isEmpty { return "昨日无篮子可复盘 · 在「复盘 · 每日」查看" }
        return "\(daily.reviews.count) 篮已复盘 · 在「复盘 · 每日」查看"
    }

    // MARK: - ⑤ 数据新鲜度与降级披露(**恒在**:降级必须诚实披露)

    @ViewBuilder
    private var freshnessSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKSectionHeader(title: "⑤ 数据新鲜度与降级披露")
            if let f = model.report.dataFreshness {
                if f.needsBanner { DataFreshnessBanner(freshness: f) }
                NKCard { DataFreshnessDetail(freshness: f) }
            } else {
                NKCard {
                    NKEmptyState(title: "本次连数据新鲜度都没查到",
                                 subtitle: "⛔ 这不等于「数据新鲜」——该报告没有新鲜度记录。",
                                 systemImage: "questionmark.circle")
                }
            }
            if !daily.notes.isEmpty {
                NKCard {
                    NKDisclosure(summary: "本次生成备注 \(daily.notes.count) 条", tone: .warn) {
                        ForEach(daily.notes, id: \.self) { n in
                            Text("· \(n)").fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - 列表行外壳(选中态 = 白底 + accent 描边;⛔ 靠选中态分隔,不靠留白)

struct NKListRow<Content: View>: View {
    let selected: Bool
    let action: () -> Void
    @ViewBuilder var content: Content

    var body: some View {
        Button(action: action) {
            content
                .padding(.horizontal, 10).padding(.vertical, 9)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: NKRadius.inner)
                        .fill(selected ? NK.cardBg : Color.clear)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: NKRadius.inner)
                        .stroke(selected ? NK.accent.opacity(0.55) : Color.clear, lineWidth: 1)
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 篮子一行(**行下面直接列成员** —— 规范 §01 决定 05:个股是一等对象)

private struct BasketListRow: View {
    @Bindable var model: AppModel
    let basket: Basket
    let selected: Bool

    var body: some View {
        NKListRow(selected: selected) {
            model.openBasket(id: basket.basketId)
        } content: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(basket.name.isEmpty ? basket.basketKey : basket.name)
                            .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        if let card = basket.card, !card.driver.isEmpty {
                            Text(card.driver)
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 6)
                    VStack(alignment: .trailing, spacing: 2) {
                        if let p = basket.scoreDisplayPercent {
                            Text(String(format: "%.1f", p)).font(NKFont.metric)
                                .foregroundStyle(NK.textPrimary)
                        }
                        if let r = basket.card?.rankInTier, let t = basket.tier {
                            Text("T\(t) 第 \(r) 位").font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                        }
                    }
                }
                HStack(spacing: 6) {
                    // 🔴 六关灯条(机械关方角 + 外环 / 证据关圆点)。
                    GateLightBar(gates: basket.gates)
                    Spacer(minLength: 0)
                    // 验证状态实时角标(四态;**「今天还没判过」与「判了是 unclear」讲不同的话**)。
                    VerificationBadge(model: model, basketId: basket.basketId)
                }
                membersStrip
                if let note = basket.cardUnavailableText {
                    // ⛔ 「本篮的卡还没生成」**不是**「篮子不存在」。
                    Text(note).font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// **成员直接列在行下面**。卡就绪 → 名称 + 角色 + RS;卡未就绪 → 只有代码,
    /// 并如实说「名称与逐只判定随卡一起来」(⛔ 不等于这几只票没通过)。
    @ViewBuilder
    private var membersStrip: some View {
        if let members = basket.card?.members, !members.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(members) { m in
                    HStack(spacing: 5) {
                        Text(m.name.isEmpty ? m.tsCode : m.name)
                            .font(NKFont.callout).foregroundStyle(NK.textPrimary)
                        if m.roleConflict {
                            NKChip(text: "角色两说", tone: .warn)
                        } else if !m.roleDisplay.isEmpty {
                            NKChip(text: m.roleDisplay)
                        }
                        Spacer(minLength: 0)
                        if let rs = m.rsRank {
                            Text("RS #\(rs)").font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                    }
                    .frame(minHeight: NKSpace.denseRowHeight)
                }
            }
        } else if !basket.memberCodes.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(basket.memberCodes, id: \.self) { c in
                    Text(c).font(NKFont.callout.monospacedDigit())
                        .foregroundStyle(NK.textSecondary)
                }
                Text("卡还没生成 —— 只有成员代码,名称与逐只判定随卡一起来")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - ③b 一行

private struct DroppedBasketRow: View {
    let dropped: DroppedBasket

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(dropped.name).font(NKFont.body).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    // 主句着原因色、补充句降一级 —— 九种原因同色同字号挤成一坨时,
                    // 「系统缺席」那两码(最该被看见的)会淹在里面。
                    HStack(spacing: 5) {
                        // 🔴 `bad` = **系统自己缺席**(不是市场结论),给一枚实心徽标
                        // 把它从"今天没好票"里拎出来。
                        if dropped.reasonTone == .bad {
                            NKChip(text: "系统缺席", tone: .bad, filled: true)
                        }
                        Text(dropped.reasonHeadline).font(NKFont.callout).fontWeight(.semibold)
                            .foregroundStyle(dropped.reasonTone.color)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let d = dropped.reasonDetail {
                        Text(d).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 6)
                if let s = dropped.mechScore {
                    Text(String(format: "%.1f", s))
                        .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                        .foregroundStyle(NK.textSecondary)
                }
            }
            // V2.2-③ 新增:卡在哪一关 + 差多少。老快照没有这两键 →
            // 整行不显示(⛔ 不写「无」,那会看起来像"没卡在任何关")。
            if let g = dropped.gateLabel {
                HStack(spacing: 6) {
                    NKChip(text: "卡在 \(g)",
                           tone: dropped.gateKind == .mechanical ? .bad : .warn)
                    if let kind = dropped.gateKind {
                        Text(kind.label).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                    Spacer(minLength: 0)
                }
            }
            // 服务端的机器原因码串(数值内嵌)。**原样展示,⛔ 不改写** —— 下沉审计视图。
            if let detail = dropped.gateDetail, !detail.isEmpty {
                NKAuditSection(contains: "机械读数原始件") {
                    Text(detail).font(NKFont.monoKey).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.inner).stroke(NK.hairline, lineWidth: 0.5))
    }
}

// MARK: - V2.1-④ 百分制打分卡(总分 + 五维贡献条)
//
// **数据全部由服务端 `report/score_display.py` 算好下发**(唯一换算实现):
// 本视图只做三件事 —— 格式化成一位小数、按贡献画条、把 `neutralFilled` 那句话说出口。
// ⛔ 不重算分数、⛔ 不另建中文标签表(`label` 服务端给)、⛔ 不把百分分数塞进任何
// 排序/筛选逻辑(§五 V2.1-④:该分数不进任何判定路径)。
//
// 🔴 **`percent == nil` 时如实说「本报告版本无打分」,⛔ 绝不渲染成 0 分** ——
// 0 分是一个极差的实质性判断,拿它冒充"没这个数"是本项目反复禁止的那类谎。

struct BasketScoreCard: View {
    let percent: Double?
    let contributions: [ScoreContribution]
    /// 卡详情页给更大的字号;列表行用紧凑档。
    var compact: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 4 : 6) {
            if let p = percent {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(String(format: "%.1f", p))
                        .font(compact ? NKFont.metric : NKFont.heroNumber)
                        .foregroundStyle(NK.textPrimary)
                    Text("/ 100").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    Text("纯展示 · 不进排序").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    Spacer()
                }
                ForEach(contributions) { c in
                    contributionRow(c)
                }
                // 一个数字后面跟着"这个数是猜的"必须当场说清,⛔ 不能只放在结构化字段里。
                // ⚠ 折叠的是版式不是内容:收起态那一行**点名了有几项按中性分计入**。
                NKDisclosure(summary: disclosureSummary, tone: disclosureTone) {
                    if contributions.contains(where: { $0.neutralFilled }) {
                        Text("`*` = 该维今天没算出来、按中性分 0.5 计入(**不是表现好**)")
                            .foregroundStyle(NK.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("百分制 = 机械分 ×100 的等价换算 · 纯展示:不进排序、不进哨兵、不改去留")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("条长按本篮内最大贡献归一,只是版式,不代表任何比例判断")
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("本报告版本无打分(⛔ 不是 0 分)")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }

    private var neutralCount: Int { contributions.filter(\.neutralFilled).count }

    private var disclosureSummary: String {
        neutralCount > 0 ? "\(neutralCount) 项按中性分计入" : "百分制是等价换算 · 纯展示"
    }

    private var disclosureTone: NKAxisTone { neutralCount > 0 ? .warn : .neutral }

    @ViewBuilder
    private func contributionRow(_ c: ScoreContribution) -> some View {
        HStack(spacing: 6) {
            // ⚠ `*` 角标只挂在**数值**上(与服务端 markdown 那一行
            // 「龙头清晰度 12.5*」逐位同形),⛔ 不在标签上再挂一个。
            Text(c.displayLabel)
                .font(NKFont.caption)
                .foregroundStyle(c.neutralFilled ? NK.amber : NK.textSecondary)
                .frame(width: compact ? 88 : 104, alignment: .leading)
                .lineLimit(1)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(NK.chipNeutral).frame(height: 6)
                    Capsule()
                        .fill(c.neutralFilled ? NK.amber.opacity(0.55) : NK.accent)
                        .frame(width: geo.size.width * barFraction(c), height: 6)
                }
                .frame(height: geo.size.height, alignment: .center)
            }
            .frame(height: 10)
            Text(contribText(c))
                .font(NKFont.caption.monospacedDigit())
                .foregroundStyle(c.neutralFilled ? NK.amber : NK.textSecondary)
                .frame(width: 40, alignment: .trailing)
            // 「占比」= 该维在机械分里的**归一化权重**(契约字段 `weight`,服务端给)。
            // ⛔ 客户端不自己求和、不自己归一化 —— 那就成了第二份换算。
            Text(weightText(c))
                .font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textTertiary)
                .frame(width: 56, alignment: .trailing)
        }
    }

    /// 条长只是**版式**:按本篮内最大贡献归一,⛔ 不代表任何比例判断。
    private func barFraction(_ c: ScoreContribution) -> CGFloat {
        let maxContrib = contributions.compactMap(\.contribPercent).max() ?? 0
        guard let v = c.contribPercent, maxContrib > 0, v > 0 else { return 0 }
        return CGFloat(min(1.0, v / maxContrib))
    }

    /// 契约里是 4 位小数(精度住契约),展示统一 1 位(位数住展示)。
    /// 算不出的项显示 `—`,**⛔ 不显示 0.0**。
    private func contribText(_ c: ScoreContribution) -> String {
        guard let v = c.contribPercent else { return "—" }
        return String(format: "%.1f", v) + (c.neutralFilled ? "*" : "")
    }

    private func weightText(_ c: ScoreContribution) -> String {
        guard let w = c.weight else { return "权重 —" }
        return "权重 " + NKFmt.ratioPct(w)
    }
}

/// 验证状态角标。数据是**实时**的(与卡上的 D0 冻结件不是一回事),按需懒加载;
/// 拉不到就**什么都不显示**,⛔ 不假装"已验证"。
struct VerificationBadge: View {
    @Bindable var model: AppModel
    let basketId: Int

    var body: some View {
        Group {
            if let v = model.basketVerifications[basketId] {
                NKChip(text: v.badgeText, tone: v.badgeTone, filled: v.badgeTone != .neutral)
            } else {
                EmptyView()
            }
        }
        .task(id: basketId) { await model.loadBasketVerification(id: basketId) }
    }
}

// ⚠ **`BasketReviewRow` 已整块搬到 `ReviewView.swift`**(V2.1-⑦ ④ 节迁移;
// **一字未改**)—— 它现在是复盘板块「每日」页的行视图。⛔ 别在这里再留一份。

// MARK: - 情绪仪表盘卡

private struct SentimentCard: View {
    let sentiment: SentimentSnapshot

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("情绪仪表盘").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    Spacer()
                    QuotaBadge(quota: PositionQuota(sentiment.positionQuota))
                }
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    metric("涨停", "\(sentiment.limitUpCount)家")
                    metric("跌停", "\(sentiment.limitDownCount)家")
                    metric("炸板率", NKFmt.pct(sentiment.zabanRate * 100))
                    metric("最高连板", "\(sentiment.maxConsecLimitUp)板")
                    metric("昨涨停今溢价", premiumText)
                    metric("样本", "\(sentiment.prevLimitUpSample)只")
                }
                Text(sentiment.quotaReason)
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// `nil` = 昨日无涨停股或数据缺失,**非"溢价为 0"**(服务端 docstring 原话)。
    private var premiumText: String {
        guard let v = sentiment.prevLimitUpPremiumAvg else { return "—" }
        return NKFmt.signedPct(v * 100)
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).nkLabel().foregroundStyle(NK.textTertiary)
            Text(value).font(NKFont.metric).foregroundStyle(NK.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - 强势板块

private struct SectorChipsRow: View {
    let sectors: [SectorSnapshot]
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(sectors) { s in
                    NKChip(text: "\(s.name) · 第\(s.boardAge)天 · \(NKFmt.signedPct(s.ret20d * 100))",
                           tone: s.bonus > 0 ? .good : .neutral)
                }
            }
        }
    }
}
