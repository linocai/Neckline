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
                reviewPointer             // ④(昨日复盘已迁往复盘板块,这里只留入口)
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
                Text(AppTab.baskets.title).font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
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
                // 🔴 档位清单 = **现役两档 ∪ 本份快照实际出现的档位**(V2.1-② 移交 ⑦ 的
                // 硬约束,判据与理由见 `BasketDaily.displayTiers`)——⛔ 既不许写死
                // `[1,2]`(历史 T3 回放会在客户端消失)、也不许写死 `[1,2,3]`(新报告
                // 凭空多一个恒空 T3 分组,把系统缺席讲成市场结论)。
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
                        Text("④ 昨日篮子复盘").font(.system(size: 13.5, weight: .semibold))
                            .foregroundStyle(NK.textPrimary)
                        Text(reviewPointerCaption)
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

    /// ⚠ 入口这一行也得**三态分开说**(⛔ 不许统一成「\(n) 篮」):本次没跑复盘 /
    /// 昨日无篮子可复盘 / 有 —— 详细三态在复盘板块每日页逐字保留,这里是它的缩写。
    private var reviewPointerCaption: String {
        let daily = model.basketDaily
        if !daily.reviewsAvailable { return "本次没跑复盘 · 在「复盘 · 每日」查看原因" }
        if daily.reviews.isEmpty { return "昨日无篮子可复盘 · 在「复盘 · 每日」查看" }
        return "\(daily.reviews.count) 篮已复盘 · 在「复盘 · 每日」查看"
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
                    // V2.2-③-E 引擎徽标(裁定 #9:单篮子单引擎)—— 卡头一眼看出这篮
                    // 走的哪条引擎;老数据缺这三键是常态,不显示不代表异常。
                    if let ev = basket.engineVersionDisplay {
                        NKChip(text: "选股引擎 \(ev)", tone: .neutral)
                    }
                    if let s = basket.card?.mechScore {
                        NKChip(text: String(format: "机械分 %.1f", s))
                    }
                    if let r = basket.card?.rankInTier, let t = basket.tier {
                        NKChip(text: "T\(t) 第 \(r) 位")
                    }
                    Spacer()
                }
                // V2.1-④ 百分制打分卡:总分 + 五维贡献条(**纯展示**)。
                BasketScoreCard(percent: basket.scoreDisplayPercent,
                                contributions: basket.scoreDisplayContributions)
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
                    Text("总分").font(.system(size: compact ? 11 : 12, weight: .bold))
                        .foregroundStyle(NK.textTertiary)
                    Text(String(format: "%.1f", p))
                        .font(.system(size: compact ? 17 : 20, weight: .semibold).monospacedDigit())
                        .foregroundStyle(NK.textPrimary)
                    Text("/ 100").font(.system(size: compact ? 11 : 12))
                        .foregroundStyle(NK.textTertiary)
                    Spacer()
                }
                ForEach(contributions) { c in
                    contributionRow(c)
                }
                if contributions.contains(where: { $0.neutralFilled }) {
                    // 一个数字后面跟着"这个数是猜的"必须当场说清,⛔ 不能只放在结构化字段里。
                    Text("`*` = 该维今天没算出来、按中性分 0.5 计入(**不是表现好**)")
                        .font(.system(size: 9.5)).foregroundStyle(NK.amber)
                }
                Text("百分制 = 机械分 ×100 的等价换算 · 纯展示:不进排序、不进哨兵、不改去留")
                    .font(.system(size: 9.5)).foregroundStyle(NK.textTertiary)
            } else {
                Text("本报告版本无打分(⛔ 不是 0 分)")
                    .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
            }
        }
    }

    @ViewBuilder
    private func contributionRow(_ c: ScoreContribution) -> some View {
        HStack(spacing: 6) {
            // ⚠ `*` 角标只挂在**数值**上(与服务端 markdown 那一行
            // 「龙头清晰度 12.5*」逐位同形),⛔ 不在标签上再挂一个。
            Text(c.displayLabel)
                .font(.system(size: compact ? 10.5 : 11.5))
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
                .font(.system(size: compact ? 10.5 : 11.5).monospacedDigit())
                .foregroundStyle(c.neutralFilled ? NK.amber : NK.textSecondary)
                .frame(width: 40, alignment: .trailing)
            // 「占比」= 该维在机械分里的**归一化权重**(契约字段 `weight`,服务端给)。
            // ⛔ 客户端不自己求和、不自己归一化 —— 那就成了第二份换算。
            Text(weightText(c))
                .font(.system(size: 9.5).monospacedDigit())
                .foregroundStyle(NK.textTertiary)
                .frame(width: 46, alignment: .trailing)
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
