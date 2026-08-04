//
//  BasketDailyView.swift
//  Neckline — **今日篮子**(D8 四板块之一,V2-⑮)。渲染 `GET /report/latest` 的
//  **篮子日报五段**:
//    ① 情绪与市场语境 → ②(持仓体检见「持仓」板块)→ ③ 今日篮子(T1/T2/T3 每篮一张卡)
//    → ③b 未定档篮子 → ④ 昨日篮子复盘 → ⑤ 数据新鲜度与降级披露
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

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle("今日篮子")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await model.refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .refreshable { await model.refresh() }
        }
        .sheet(item: Binding(get: { model.openedBasketId.flatMap { model.basket(byID: $0) } },
                             set: { if $0 == nil { model.dismissBasket() } })) { basket in
            BasketCardPage(model: model, basket: basket)
        }
        .sheet(item: Binding(get: { model.infoCardRequest },
                             set: { if $0 == nil { model.dismissInfoCard() } })) { req in
            InfoCardPageView(model: model, request: req)
        }
        #else
        ScrollView {
            content.padding(NKSpace.pagePad).frame(maxWidth: 900)
        }
        .frame(maxWidth: .infinity)
        .background(NK.pageBg)
        .toolbar {
            ToolbarItem { Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") } }
        }
        .sheet(item: Binding(get: { model.openedBasketId.flatMap { model.basket(byID: $0) } },
                             set: { if $0 == nil { model.dismissBasket() } })) { basket in
            BasketCardPage(model: model, basket: basket).frame(width: 700, height: 780)
        }
        .sheet(item: Binding(get: { model.infoCardRequest },
                             set: { if $0 == nil { model.dismissInfoCard() } })) { req in
            InfoCardPageView(model: model, request: req).frame(width: 640, height: 720)
        }
        #endif
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            header
            if let warning = model.retreatWarning {
                RetreatBrakeBanner(reason: warning)
            }
            if !model.report.missedEntryHint.isEmpty {
                MissedEntryHintBanner(text: model.report.missedEntryHint)
            }
            if model.report.degraded {
                NKCard {
                    NKEmptyState(title: emptyTitle(model.report.reason),
                                 subtitle: "策略引擎已在跑,今晚 16:35 出计划后自动显示。",
                                 systemImage: "moon.zzz")
                }
            } else {
                sentimentSection          // ① 情绪与市场语境
                holdingCheckupPointer     // ②(指向持仓板块)
                basketsSection            // ③ 今日篮子
                droppedSection            // ③b 未定档
                reviewSection             // ④ 昨日复盘
                IntelPackageView(report: model.report)
            }
            freshnessSection              // ⑤ 数据新鲜度与降级披露(**恒在**)
        }
    }

    private func emptyTitle(_ reason: String) -> String {
        switch reason {
        case "no_report": return "今日报告尚未生成"
        case "bad_date", "not_loaded": return "暂无数据"
        default: return "暂无数据(\(reason))"
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            #if os(macOS)
            HStack(spacing: 8) {
                NKLogo(size: 24)
                Text("今日篮子").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            }
            #endif
            if !model.report.tradeDate.isEmpty {
                Text("交易日 \(model.calendar.displayString(model.report.tradeDate)) · 章程 \(model.report.strategyVersion)"
                     + (model.basketDaily.packVersion.map { " · 选股包 \($0)" } ?? ""))
                    .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
        }
    }

    // MARK: - ① 情绪与市场语境

    @ViewBuilder
    private var sentimentSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
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
                        Text("② 持仓体检").font(.system(size: 13.5, weight: .semibold))
                            .foregroundStyle(NK.textPrimary)
                        Text("持仓 \(model.positions.count) 笔 · 在「持仓」板块查看(先管住手里的)")
                            .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 12))
                        .foregroundStyle(NK.textTertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - ③ 今日篮子(T1/T2/T3 每篮一张卡)

    @ViewBuilder
    private var basketsSection: some View {
        let daily = model.basketDaily
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "③ 今日篮子 \(daily.baskets.count)",
                            trailing: "Tier = 注意力优先级")
            // 语义红线句(§2.8-C):**排序不是收益预测**。
            Text("Tier / 档内次序 = 注意力优先级,不是收益预测 · T1 ≠ 最会涨 · 终选权在你")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            if !daily.basketsAvailable {
                // E3:**「本次没取到」与「今天真没有」讲不同的话**。
                NKCard {
                    NKEmptyState(title: "本次没取到今日篮子",
                                 subtitle: daily.basketsUnavailableReason.map { "原因:\($0)" }
                                     ?? "这一段本次未取到(不是「今天没有篮子」)",
                                 systemImage: "exclamationmark.icloud")
                }
            } else {
                // E1:**空档位如实显示,⛔ 不隐藏**。
                ForEach([1, 2, 3], id: \.self) { tier in
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
                Text(tierCaption(tier)).font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                Spacer()
            }
            if baskets.isEmpty {
                NKCard {
                    HStack(spacing: 8) {
                        Image(systemName: "tray").foregroundStyle(NK.textTertiary)
                        Text("今日 T\(tier) 为空(算过了,今天没有达到该档标准的篮子)")
                            .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        Spacer()
                    }
                }
            } else {
                ForEach(baskets) { b in
                    BasketRow(model: model, basket: b)
                }
            }
        }
    }

    private func tierCaption(_ tier: Int) -> String {
        switch tier {
        case 1: return "最先看的一档"
        case 2: return "次一档"
        default: return "留作对照的一档"
        }
    }

    // MARK: - ③b 未定档篮子(**零溢出时这一节仍在**)

    @ViewBuilder
    private var droppedSection: some View {
        let daily = model.basketDaily
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "③b 今日未定档篮子 \(daily.droppedBaskets.count)")
            if !daily.droppedBasketsAvailable {
                NKCard {
                    NKEmptyState(title: "本次没跑未定档统计",
                                 subtitle: daily.droppedBasketsUnavailableReason.map { "原因:\($0)" }
                                     ?? "这一段本次未取到(不是「今天零溢出」)",
                                 systemImage: "exclamationmark.icloud")
                }
            } else if daily.droppedBaskets.isEmpty {
                NKCard {
                    HStack(spacing: 8) {
                        Image(systemName: "checkmark.seal").foregroundStyle(NK.textTertiary)
                        Text("算过了 · 今日零未定档篮子").font(.system(size: 12))
                            .foregroundStyle(NK.textSecondary)
                        Spacer()
                    }
                }
            } else {
                // E2:**两个原因码语义相反,分开展示**。
                ForEach(daily.droppedBaskets) { d in
                    NKCard {
                        HStack(alignment: .top, spacing: 10) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(d.name).font(.system(size: 13, weight: .semibold))
                                    .foregroundStyle(NK.textPrimary)
                                Text(d.reasonLabel).font(.system(size: 11.5))
                                    .foregroundStyle(d.reasonTone.color)
                            }
                            Spacer()
                            if let s = d.mechScore {
                                Text(String(format: "%.1f 分", s))
                                    .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                                    .foregroundStyle(NK.textSecondary)
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - ④ 昨日篮子复盘

    @ViewBuilder
    private var reviewSection: some View {
        let daily = model.basketDaily
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "④ 昨日篮子复盘 \(daily.reviews.count)",
                            trailing: daily.reviewD0.map { "D0 \($0)" })
            if !daily.reviewsAvailable {
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
                        Text("昨日无篮子可复盘").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
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

    // MARK: - ⑤ 数据新鲜度与降级披露(**恒在**:降级必须诚实披露)

    @ViewBuilder
    private var freshnessSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
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
            if !model.basketDaily.notes.isEmpty {
                NKCard {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("本次生成备注").font(.system(size: 11, weight: .bold))
                            .foregroundStyle(NK.textTertiary)
                        ForEach(model.basketDaily.notes, id: \.self) { n in
                            Text("· \(n)").font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - 篮子一行(列表态:名称 / 驱动 / 成员数 / 验证角标 / 卡未就绪如实说)

private struct BasketRow: View {
    @Bindable var model: AppModel
    let basket: Basket

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(basket.name.isEmpty ? basket.basketKey : basket.name)
                            .font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
                        if let card = basket.card, !card.driver.isEmpty {
                            Text("共同驱动:\(card.driver)")
                                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer()
                    // 验证状态实时角标(四态;**「今天还没判过」与「判了是 unclear」讲不同的话**)。
                    VerificationBadge(model: model, basketId: basket.basketId)
                }
                HStack(spacing: 6) {
                    NKChip(text: "成员 \(basket.memberCodes.count)")
                    if let s = basket.card?.mechScore {
                        NKChip(text: String(format: "机械分 %.1f", s))
                    }
                    if let r = basket.card?.rankInTier, let t = basket.tier {
                        NKChip(text: "T\(t) 第 \(r) 位")
                    }
                    Spacer()
                }
                if let note = basket.cardUnavailableText {
                    // ⛔ 「本篮的卡还没生成」**不是**「篮子不存在」。
                    Text(note).font(.system(size: 11.5)).foregroundStyle(NK.amber)
                } else {
                    Button { model.openBasket(id: basket.basketId) } label: {
                        HStack(spacing: 4) {
                            Text("查看篮子卡(11 项)").font(.system(size: 12.5, weight: .semibold))
                            Image(systemName: "chevron.right").font(.system(size: 10))
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                }
            }
        }
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

// MARK: - 昨日复盘一行

private struct BasketReviewRow: View {
    let review: BasketReview
    @State private var expanded = false

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text(review.name.isEmpty ? review.basketKey : review.name)
                        .font(.system(size: 13.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    if let t = review.tier { NKChip(text: "T\(t)") }
                    NKChip(text: review.depthLabel)
                    Spacer()
                    Text("D0 \(review.d0)").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
                if let text = review.llmText, !text.isEmpty {
                    // §2.7:LLM 叙述**原文整段呈现**,⛔ 不拆解塞回枚举卡片。
                    Text(text).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    NKReferenceNote()
                } else if let skip = review.llmSkipReason, !skip.isEmpty {
                    // **未生成**(预算耗尽 / 降级)—— ⛔ 不拿空串冒充「生成了但没内容」。
                    Text("本篮未生成人话复盘:\(skip)")
                        .font(.system(size: 11.5)).foregroundStyle(NK.amber)
                }
                if review.degraded {
                    Text("本次复盘降级:人话半份缺席,机械判照出")
                        .font(.system(size: 11)).foregroundStyle(NK.amber)
                }
                if let obj = review.mech.objectValue, !obj.isEmpty {
                    Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                        HStack(spacing: 4) {
                            Text(expanded ? "收起机械判" : "展开机械判(九项)")
                                .font(.system(size: 11.5, weight: .medium))
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 9))
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    if expanded { NKJSONTable(value: review.mech) }
                }
            }
        }
    }
}

// MARK: - 情绪仪表盘卡

private struct SentimentCard: View {
    let sentiment: SentimentSnapshot

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("情绪仪表盘").font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
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
                    .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
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
            Text(label).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
            Text(value).font(.system(size: 14, weight: .semibold).monospacedDigit()).foregroundStyle(NK.textPrimary)
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
