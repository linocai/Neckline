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

    /// macOS 列表栏的第三种选中态:**昨日回执**(原型 827–852 行是详情栏的一屏,
    /// ⛔ 不是"点了就跳去复盘板块"）。⚠ 篮子选中仍然住在 `model.openedBasketId`
    /// (推送 / 深链会从外面设它),所以这里只在**没选篮子**时才生效。
    #if os(macOS)
    /// ⚠ 初值来自 QA 钩子(缺环境变量时恒 `false`),**只改初值**,用户照常可点。
    @State private var receiptSelected = NKQA.initialReceipt
    #endif

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
                    // 🔴 **V2.3.3-⑤ 竞价卡是这一页的第一张卡**(D1 早晨 9:26 之后才有内容;
                    // 没有就整张不画,⛔ 不画空卡)。**刻意放在 `degraded` 分支之外**:
                    // 竞价报告是 **D1 早晨**的独立产物,与 D0 晚上那份盘后报告在不在**无关**
                    // ——把它塞进 else 分支会让"昨晚报告没跑"顺手把今早的竞价确认也藏掉。
                    AuctionSummaryCard(model: model)
                    if model.report.degraded {
                        reportNotReadyCard
                    } else {
                        compactOverviewCard
                        reviewPointer
                        // 🔴 **③ 今日篮子排在 ② 与情报之前**(iOS 原型 349–441 行的顺序:
                        // 行情状态 + 情绪 → 昨日回执 → ③ 篮子 → 篮子卡)。V2.3.0 把
                        // ② 持仓体检入口与整块「情报」塞在概览卡里、压在篮子上面 ——
                        // iPhone 上要**划过两屏**才看得到今天的篮子,而它才是这一页的主角。
                        // ⛔ 两段一个字没删,只是挪到篮子后面(macOS 侧另有安排,别跟着改)。
                        basketsSection
                        droppedSection
                        outSection
                        holdingCheckupPointer
                        IntelPackageView(report: model.report)
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
                // iOS 原型 316–319 行:右上是**蓝底胶囊 + 上次刷新时刻**,⛔ 不是裸箭头。
                ToolbarItem(placement: .primaryAction) { NKRefreshPill(model: model) }
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
        // V2.3.3-⑤ 竞价小报告五块。⚠ `auction == nil` 时**开不起来**(卡都不画),
        // 这里再兜一层:没有 payload 就什么都不呈现(⛔ 不弹一个空壳)。
        .sheet(isPresented: $model.showAuctionSheet) {
            if let a = model.auction { AuctionReportPage(model: model, payload: a) }
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
            // 原型 `Neckline 信息卡与对账.dc.html` 36 行:信息卡是 **1200×820** 的整页
            // (它是全 App 唯一放图的地方 —— 三张图 + 三组并排卡挤进 660 宽会全部塌成一列)。
            // ⚠ sheet 受父窗口内缩,故取 1120:与 1200 画布同量级,两列排得开。
            InfoCardPageView(model: model, request: req)
                .frame(minWidth: 1120, maxWidth: 1120, minHeight: 700, maxHeight: .infinity)
        }
        // V2.3.3-⑤ 竞价小报告五块(macOS 也走 sheet:五块是**一次读完**的东西,
        // 塞进详情栏会把「今日概览」挤没)。
        .sheet(isPresented: $model.showAuctionSheet) {
            if let a = model.auction {
                AuctionReportPage(model: model, payload: a)
                    .frame(minWidth: 760, maxWidth: 860, minHeight: 640, maxHeight: .infinity)
            }
        }
    }

    // —— 列表栏 ——

    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            // 标题区 = 原型 82 行 `padding:18px 16px 10px`(纵向由 `NKSplitLayout` 的
            // `listPadTop` 给 18,横向在栏内边距 10 之上再补 6 = 16,底 10)。
            VStack(alignment: .leading, spacing: 2) {   // 原型 84 行 margin-top:2
                Text(AppTab.baskets.title).font(NKFont.title2).tracking(-0.3)
                    .foregroundStyle(NK.textPrimary)
                // 退潮刹车时这一句翻**红 + 600**(状态原型 83 行 `11.5px #E5443B; 600`)——
                // 「计划作废」是这一栏此刻最要紧的一句,灰字会被当成普通计数说明扫过去。
                Text(listSubtitle).font(NKFont.caption)
                    .fontWeight(model.board.retreatBrake.active ? .semibold : .regular)
                    .foregroundStyle(model.board.retreatBrake.active ? NK.down : NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH).padding(.bottom, 10)

            overviewRow
            if model.report.degraded {
                // 报告未生成:列表栏没有篮子可列 —— 原型(`Neckline 状态.dc.html` 240–246)
                // 给的是一块**居中的空态**,⛔ 不是一片白。
                notReadyListPlaceholder
            } else {
                reviewReceiptRow
                basketsListSection
                droppedListSection
                outListSection
            }
        }
    }

    private var listSubtitle: String {
        // 退潮刹车优先(状态原型 83 行)——「篮子仍然列出、点得开」是这一屏必须当面说的
        // 那句话:作废的是计划,不是数据。
        if model.board.retreatBrake.active { return "今日计划已作废 · 篮子仍然列出,点得开" }
        // 报告未生成时原型副标题是「今晚 16:35 出计划」(状态原型 231 行)。
        if model.report.degraded { return "今晚 16:35 出计划" }
        return "今日 \(daily.baskets.count) 篮定档 · \(daily.droppedBaskets.count) 篮未定档 · \(daily.outCandidates.count) 只 OUT"
    }

    /// 「今日概览」入口行(选中 = 详情栏显示概览)。**两行**(原型 87–95):
    /// 首行 = 图标 + 标题 + 右端段名索引;次行 = 本份报告的降级摘要,缩进到标题下沿。
    private var overviewRow: some View {
        NKListRow(selected: model.openedBasketId == nil && !receiptSelected) {
            model.dismissBasket()
            receiptSelected = false
        } content: {
            VStack(alignment: .leading, spacing: 3) {   // 原型 95 行 margin-top:3
                HStack(spacing: 8) {
                    Image(systemName: "waveform.path.ecg").font(.system(size: 13))
                        .foregroundStyle(NK.textSecondary)
                        .frame(width: 13)
                    Text("今日概览").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 6)
                    if model.report.degraded {
                        // 原型状态屏 234 行:报告未生成时右端是一颗琥珀点,不是段名索引。
                        Circle().fill(NK.amber).frame(width: 6, height: 6)
                    } else {
                        Text("①·情报·⑤").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                }
                Text(overviewRowCaption).font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 21)              // 原型 95 行 padding-left:21(13 图标 + 8 gap)
            }
        }
    }

    /// 次行摘要。🔴 **只由本份报告里已有的字段拼**(情绪额度 / 三处新鲜度),
    /// ⛔ 一个数都不新造;都没有就如实说这一段能看什么。
    private var overviewRowCaption: String {
        if model.report.degraded { return "报告未生成 · ⑤ 数据新鲜度可看" }
        var parts: [String] = []
        if let s = model.report.sentiment {
            parts.append("情绪\(PositionQuota(s.positionQuota).label)")
        }
        if let f = model.report.dataFreshness {
            if f.stale { parts.append("板块数据落后 \(f.sectorLagDays) 日") }
            if f.industryStrengthStale == true { parts.append("行业强度未就绪") }
            if f.scanLayerStale == true { parts.append("扫描层未就绪") }
        } else {
            parts.append("新鲜度没查到")
        }
        return parts.isEmpty ? "① 情绪 · 情报 · ⑤ 数据新鲜度" : parts.joined(separator: " · ")
    }

    /// 「昨日回执」行 —— 选中 = 详情栏显示回执摘要屏(原型 827–852)。
    /// ⛔ 只给回执,逐篮明细在复盘板块(不在两处各画一遍)。
    private var reviewReceiptRow: some View {
        NKListRow(selected: receiptSelected && model.openedBasketId == nil) {
            model.dismissBasket()
            receiptSelected = true
        } content: {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Image(systemName: "clock").font(.system(size: 13))
                        .foregroundStyle(NK.textSecondary)
                        .frame(width: 13)
                    Text("昨日回执").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 6)
                    // 原型 103–106 行:右端两枚计数徽标(验证 / 证伪),gap 3。
                    HStack(spacing: 3) {
                        if reviewCounts.verified > 0 {
                            NKChip(text: "\(reviewCounts.verified) 验证", tone: .good)
                        }
                        if reviewCounts.falsified > 0 {
                            NKChip(text: "\(reviewCounts.falsified) 证伪", tone: .bad)
                        }
                    }
                }
                Text(reviewPointerCaption).font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 21)
            }
        }
    }

    /// 昨日回执两枚计数徽标的读数。**三态各自计数**(⑧ 的三路读法):
    /// `notEvaluated` = **今天还没判过**,⛔ 不并进「未明」。
    private var reviewCounts: (verified: Int, falsified: Int, notEvaluated: Int) {
        var v = 0, f = 0, n = 0
        for r in daily.reviews {
            if r.verification?["notEvaluated"]?.boolValue == true { n += 1; continue }
            switch r.verification?["state"]?.stringValue {
            case "verified": v += 1
            case "falsified": f += 1
            default: break
            }
        }
        return (v, f, n)
    }

    /// 报告未生成时列表栏的空态(状态原型 240–246 行:月亮图标 + 两行 12px 说明,居中)。
    private var notReadyListPlaceholder: some View {
        VStack(spacing: 10) {
            // 原型 stroke `rgba(60,60,67,.22)`;`textTertiary` 是同一灰的 .40 → 再压 .55。
            Image(systemName: "moon.zzz").font(.system(size: 30, weight: .light))
                .foregroundStyle(NK.textTertiary.opacity(0.55))
            Text("今晚出报告后\n篮子会出现在这里")
                .font(NKFont.callout).foregroundStyle(NK.textTertiary)
                .multilineTextAlignment(.center).lineSpacing(3)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24).padding(.vertical, 90)
    }

    @ViewBuilder
    private var basketsListSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            // ⚠ **③ 的段头与那句 Tier 红线在原型的列表栏里没有**(它们在篮子卡的
            // ⑩ 披露区里,macOS 原型 685 行逐字保留)—— 列表栏直接是 T1 / T2 分组头。
            // ⛔ 别当成"漏了"再加回来:同一句话在一屏里说两遍,反而没人读。
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
            // 原型 113–117 行:`padding:16px 16px 7px`,Tier 实心徽标 + 弱标 + 一条细线。
            NKGroupHeader(text: tierCaption(tier)) {
                NKChip(text: "T\(tier)", tone: tier == 1 ? .good : (tier == 2 ? .warn : .neutral),
                       filled: true)
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH)
            .padding(.top, 16).padding(.bottom, 7)

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
            // 原型 233 行:与 T1 / T2 同一种分组头(⛔ 不是 `title3` 那一档 —— 段名在
            // 原型里是**弱标**,17px 会把它抢成页面标题)。
            NKGroupHeader("③b 档位已满 · 未定档 \(daily.droppedBaskets.count)")
                .padding(.horizontal, NKSpace.listHeaderExtraH)
                .padding(.top, 16).padding(.bottom, 7)
            droppedRows
        }
    }

    @ViewBuilder
    private var outListSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            NKGroupHeader("③b-2 今日 OUT \(daily.outCandidates.count)")
                .padding(.horizontal, NKSpace.listHeaderExtraH)
                .padding(.top, 16).padding(.bottom, 7)
            outRows
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
        } else if receiptSelected {
            reviewReceiptDetail
        } else {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                Text("今日概览").font(NKFont.title1).tracking(-0.4)
                    .foregroundStyle(NK.textPrimary)
                // 🔴 **V2.3.3-⑤ 竞价卡 = 概览里的第一张卡**(施工图写「今日概览之前」,
                // 落地取"标题之后的第一张卡" —— 把一张卡摆到屏标题上面在 macOS 详情栏
                // 里读起来像界面坏了;位置语义"最靠前"一样成立)。没有报告就整张不画。
                AuctionSummaryCard(model: model)
                // 🔴 V2.2-② 行情状态条。**纯展示、零动作**;`available=false` 时如实说
                // 「本段未取得」,⛔ 不静默省略。⚠ 它**不是**「① 情绪与市场语境」的替代 ——
                // 两者一个讲市场结构、一个讲情绪读数,段名各自保留(段名是审计锚)。
                MarketRegimeStrip(regime: model.marketRegime)
                if !model.report.missedEntryHint.isEmpty {
                    MissedEntryHintBanner(text: model.report.missedEntryHint)
                }
                // 顺序 = 原型 711 / 736 / 776 / 800 行:行情状态 → ① → ⑤ → 情报。
                // ⚠ **② 持仓体检的入口卡在原型的今日概览里没有**(列表栏那一行右端写死
                // 「①·情报·⑤」三段)—— 工具栏上就有「持仓」胶囊,一屏之内不再重复一个
                // 跳板;⛔ 别当成漏了。② 不在本页重画这条既有纪律不变。
                sentimentSection          // ① 情绪与市场语境
                freshnessSection          // ⑤ 数据新鲜度与降级披露(**恒在**)
                IntelPackageView(report: model.report)
            }
        }
    }

    /// 「昨日回执」详情屏(原型 827–852)。🔴 **只给回执摘要**:逐篮明细在复盘板块的
    /// 「每日」页 —— 同一份数据不在两处各画一遍,免得看到两个可能不同步的版本。
    private var reviewReceiptDetail: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            VStack(alignment: .leading, spacing: 3) {
                Text("昨日回执").font(NKFont.title1).tracking(-0.4)
                    .foregroundStyle(NK.textPrimary)
                Text(receiptSubtitle).font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
            NKCard {
                HStack(alignment: .center, spacing: 28) {   // 原型 831 行 gap:28
                    receiptMetric(reviewCounts.verified, "已验证", NK.up)
                    receiptMetric(reviewCounts.falsified, "被证伪", NK.down)
                    // ⚠ 「今天还没判过」是**第三态**(⑧ 三路读法),⛔ 不并进「被证伪」。
                    receiptMetric(reviewCounts.notEvaluated, "今天还没判过", NK.textSecondary)
                    Spacer(minLength: 8)
                    Button {
                        model.view = .review
                        model.reviewPage = .daily
                    } label: {
                        HStack(spacing: 5) {
                            Text("去「复盘 · 每日」看逐篮明细")
                                .font(NKFont.callout).fontWeight(.semibold)
                            Image(systemName: "chevron.right")
                                .font(.system(size: 9, weight: .semibold))
                        }
                        .foregroundStyle(NK.accent)
                        .padding(.horizontal, 13).padding(.vertical, 7)
                        .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                            .stroke(NK.accent.opacity(0.35), lineWidth: 0.5))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            Text("这里只给回执摘要。逐篮的人话复盘与机械判九项在复盘板块的「每日」页 —— "
                 + "同一份数据不在两处各画一遍,免得你看到两个可能不同步的版本。")
                .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16).padding(.vertical, 14)
                .background(RoundedRectangle(cornerRadius: NKRadius.memberCard)
                    .fill(NK.chipNeutral.opacity(0.7)))
                .overlay(RoundedRectangle(cornerRadius: NKRadius.memberCard)
                    .stroke(NK.hairline, lineWidth: 0.5))
            // ⚠ 三态之外还有「本次没跑复盘」——它不是 0 篮,必须另说一句。
            if !daily.reviewsAvailable {
                Text(daily.reviewsUnavailableReason.map { "本次没跑复盘 · 原因:\($0)" }
                     ?? "本次没跑复盘(⛔ 不等于昨日无篮子可复盘)")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var receiptSubtitle: String {
        var parts = ["④ 昨日篮子复盘"]
        if let d0 = daily.reviewD0, !d0.isEmpty { parts.append("D0 \(d0)") }
        parts.append(daily.reviewsAvailable ? "\(daily.reviews.count) 篮已复盘" : "本次没跑复盘")
        return parts.joined(separator: " · ")
    }

    private func receiptMetric(_ n: Int, _ label: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            // 原型 832 行 `26px/600`:字号取 `title1`(26),字重压回 600(它是**读数**
            // 不是标题,700 在三个数并排时太重)。
            Text("\(n)").font(NKFont.title1.monospacedDigit()).fontWeight(.semibold)
                .tracking(-0.5).foregroundStyle(color)
            Text(label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
        }
    }

    /// 报告未生成时的详情栏。**把「还没跑」和「跑了、今天真没有」分开**:
    /// 这一屏说的是「还没跑到那一步」,后者会给一份完整报告、里面写着「今日 T1 为空」。
    private var reportNotReadyDetail: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            Text("今日概览").font(NKFont.title1).tracking(-0.4)
                .foregroundStyle(NK.textPrimary)
            // 竞价报告是 **D1 早晨**的独立产物 —— 昨晚那份盘后报告没跑,不该顺手把今早
            // 的竞价确认也藏掉(同 iOS 侧把它放在 `degraded` 分支之外的理由)。
            AuctionSummaryCard(model: model)
            notReadyCardMac
            freshnessSection
        }
    }

    /// 状态原型 253–284 行:**一张卡**(⛔ 不是"空态卡 + 流水线卡"两张)——
    /// 图标块 + 标题 + 分隔 + 横向时间轴 + 三段串行 + 底部那句"两回事"。
    private var notReadyCardMac: some View {
        NKCard(padding: 26) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 14) {
                    RoundedRectangle(cornerRadius: NKRadius.field, style: .continuous)
                        .fill(NK.accent.opacity(0.08))
                        .frame(width: 44, height: 44)
                        .overlay(Image(systemName: "moon.zzz")
                            .font(.system(size: 21, weight: .light)).foregroundStyle(NK.accent))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(emptyTitle(model.report.reason))
                            .font(NKFont.metric).fontWeight(.semibold).tracking(-0.3)
                            .foregroundStyle(NK.textPrimary)
                        Text("策略引擎已在跑,今晚 16:35 出计划后自动显示。")
                            .font(NKFont.body).foregroundStyle(NK.textSecondary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.top, 4)

                Divider().overlay(NK.hairline).padding(.top, 24)

                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("今晚的流水线").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 0)
                    // 🔴 §五 〇-2:这是**排程表**,不是实时状态 —— 这一句必须当面标出来。
                    Text("排程表 · 非实时状态 · 16:35 是晚间链启动时刻,不是报告落地时刻")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                }
                .padding(.top, 22)

                pipelineTimeline.padding(.top, 16)
                pipelineSegments.padding(.top, 16)

                Text("这一屏说的是「还没跑到那一步」—— 与「跑了、今天真没有篮子」是两回事。"
                     + "后者会给你一份完整报告,里面写着「今日 T1 为空(算过了,今天没有"
                     + "达到该档标准的篮子)」。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .background(RoundedRectangle(cornerRadius: NKRadius.inner)
                        .fill(NK.chipNeutral.opacity(0.7)))
                    .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                        .stroke(NK.hairline, lineWidth: 0.5))
                    .padding(.top, 22)

                NKDisclosure(summary: "这条流水线是排程表,不是进度条", tone: .warn) {
                    Text("上面是 systemd timer 的固定时刻表 —— 它说的是「按计划几点会跑」,"
                         + "⛔ 不是「现在跑到哪了」。客户端目前没有观察批算进度的通道。")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("16:35 是晚间链**启动**时刻,不是报告落地时刻。")
                    // 🔴 原型把 16:05 那一格画成了「日更进行中」(琥珀 + 光晕)——
                    // ⛔ **本版刻意不画那个「当前在跑」的标记**:客户端没有任何通道能
                    // 观察到批算跑到哪了,画出来就是把「没看」讲成「看到了」(§五 〇-2)。
                    Text("四个时刻**同样着色**是刻意的:哪一段正在跑,客户端此刻并不知道。")
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 14)
            }
        }
    }

    /// 横向时间轴(状态原型 268–276 行):四个节点 + 中间连线。
    private var pipelineTimeline: some View {
        // ⚠ `alignment: .top` **不能省**:16:05 那格的说明是两行,HStack 默认居中对齐会
        // 把它整格往上顶 —— 四颗圆点就落不到同一条水平线上(实拍逮到)。
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(Self.pipelineSteps.enumerated()), id: \.offset) { idx, step in
                if idx > 0 {
                    // 连线要落在圆点的**中线**上(圆点 11 高 → 顶端偏移 5)。
                    Rectangle().fill(NK.hairline).frame(height: 2).padding(.top, 5)
                }
                VStack(spacing: 7) {
                    Circle().fill(NK.textTertiary.opacity(0.45)).frame(width: 11, height: 11)
                    Text(step.0).font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Text(step.1).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .multilineTextAlignment(.center).lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(width: 96)                      // 原型 268 行 width:88(SF 中文稍宽)
            }
        }
        .frame(maxWidth: .infinity)
    }

    /// 「16:35 之后三段串行」(状态原型 277–283 行)。
    private var pipelineSegments: some View {
        HStack(spacing: 8) {
            Text("16:35 之后三段串行").nkLabel().foregroundStyle(NK.textTertiary)
                .fixedSize()
            ForEach(Array(Self.pipelineSegmentNames.enumerated()), id: \.offset) { idx, name in
                if idx > 0 {
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(NK.textTertiary)
                }
                Text(name).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, 4).padding(.vertical, 6)
                    .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.cardBg))
                    .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                        .stroke(NK.hairline, lineWidth: 0.5))
            }
        }
        .padding(.horizontal, 13).padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: NKRadius.inner)
            .fill(NK.chipNeutral.opacity(0.7)))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
            .stroke(NK.hairline, lineWidth: 0.5))
    }
    #endif

    private var reportNotReadyCard: some View {
        NKCard {
            NKEmptyState(title: emptyTitle(model.report.reason),
                         subtitle: "策略引擎已在跑,今晚 16:35 出计划后自动显示。",
                         systemImage: "moon.zzz")
        }
    }

    // ⚠ **原来那张独立的「今晚的流水线」卡已并进 `notReadyCardMac`**(状态原型 253–284
    // 行是**一张**卡:图标块 + 时间轴 + 三段串行 + 那句"两回事")—— 文案一句没少,
    // 包括「排程表 · 非实时状态」与折叠区里那三条。⛔ 别再拆回两张卡。

    private static let pipelineSteps: [(String, String)] = [
        ("15:00", "收盘"),
        ("15:05", "盘中存拍"),
        ("16:05", "日更\nEOD → Parquet"),
        ("16:35", "晚间链启动"),
    ]

    /// 16:35 之后的三段串行(名字与服务端 `neckline-*.service` 的段序一致)。
    private static let pipelineSegmentNames: [String] = [
        "seg1 扫描层批算", "seg2 篮子 · LLM", "seg3 复盘 + 报告 + 推送",
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
            // ⚠ ② 持仓体检与「情报」**已挪到篮子后面**(见 `iosBody`),⛔ 别搬回来。
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
        #if os(macOS)
        // 🔴 **段名进卡头**(原型 736–739 行:卡头就是「① 情绪与市场语境」+ 右端仓位额度
        // 徽标)—— V2.3.0 是"卡外一个 ① 段头 + 卡内再写一次「情绪仪表盘」",同一张卡上
        // 两层标题。⚠ 段名一字未动(它是审计锚),动的只是它住在哪。
        if let s = model.report.sentiment {
            SentimentCard(sentiment: s, sectors: model.report.sectors)
        } else {
            NKCard {
                VStack(alignment: .leading, spacing: 12) {
                    Text("① 情绪与市场语境").font(NKFont.headline)
                        .foregroundStyle(NK.textPrimary)
                    NKEmptyState(title: "本次没有情绪仪表盘数据", systemImage: "gauge")
                }
            }
        }
        #else
        // 🔴 **iOS 上⛔ 不再另起一个段头**(V2.3.1 批 7 实拍逮到):批 2 把段名「① 情绪与
        // 市场语境」搬进了 `SentimentCard` 的卡头(macOS 原型 736–739),而 iOS 这个调用点
        // 仍然在卡外面写了一遍 —— 手机上同一张卡**连着出现两个一模一样的标题**。
        // ⚠ 这正是「双端共用件改了却只在一端核对过」的典型:编译不报错、单测也测不出。
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            if let s = model.report.sentiment {
                SentimentCard(sentiment: s, sectors: [])
            } else {
                NKCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("① 情绪与市场语境").font(NKFont.headline)
                            .foregroundStyle(NK.textPrimary)
                        NKEmptyState(title: "本次没有情绪仪表盘数据", systemImage: "gauge")
                    }
                }
            }
            if !model.report.sectors.isEmpty {
                SectorChipsRow(sectors: model.report.sectors)
            }
        }
        #endif
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
    //
    // 🔴 V2.3.2-②-A:③b 自此装**两类行,⛔ 不许合并**——
    //   · 本节 = **未定档**(`capacity_overflow`):关口全过了、只是位置装不下。
    //     **它不是 OUT**(K8 §八 的 OUT 适用状态里没有"位置满")。
    //   · 下一节 = **OUT**(股票级):在六道关口上被判出局的票。
    // 两者混成一张表,用户就分不清"今天机会太多"和"这票没过关"了。

    @ViewBuilder
    private var droppedSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKSectionHeader(title: "③b 档位已满 · 未定档 \(daily.droppedBaskets.count)")
            droppedRows
        }
    }

    // MARK: - ③b-2 今日 OUT 清单(股票级;V2.3.2-②-B)

    @ViewBuilder
    private var outSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            NKSectionHeader(title: "③b-2 今日 OUT \(daily.outCandidates.count)")
            outRows
        }
    }

    @ViewBuilder
    private var outRows: some View {
        if !daily.outCandidatesAvailable {
            unavailableRow(title: "本次没跑 OUT 清单",
                           detail: daily.outCandidatesUnavailableReason.map { "原因:\($0)" }
                               ?? "这一段本次未取到(不是「今天没有 OUT」)")
        } else if daily.outCandidates.isEmpty {
            unavailableRow(title: "算过了 · 今日无 OUT 候选", detail: nil, tone: .neutral)
        } else {
            ForEach(daily.outCandidates) { o in
                OutCandidateRow(item: o)
            }
        }
    }

    @ViewBuilder
    private var droppedRows: some View {
        if !daily.droppedBasketsAvailable {
            unavailableRow(title: "本次没跑未定档统计",
                           detail: daily.droppedBasketsUnavailableReason.map { "原因:\($0)" }
                               ?? "这一段本次未取到(不是「今天零溢出」)")
        } else if daily.droppedBaskets.isEmpty {
            // ⚠ 服务端自 V2.3.2-②-A 起只把**非 OUT** 的未定档行放进这一段 → 空 =
            // 「没有装不下的」,**不等于**「今天没有票被判 OUT」(那要看 ③b-2)。
            // ⛔ 别把这行文案写成「今天没有票被刷掉」。
            unavailableRow(title: "算过了 · 今日没有装不下的篮子",
                           detail: "关口出局的票看下一节 ③b-2", tone: .neutral)
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
        #if os(macOS)
        // 🔴 **一张卡把这一段说完**(原型 776–798 行):卡头 + 三行读数 + 琥珀提示块 +
        // 披露行。⛔ 不再在卡上面另挂一条通栏红橙横幅 —— 原型里没有那一条,而它在
        // 详情栏里的视觉重量比刹车条还大(刹车条才是那个颜色该留给的东西)。
        // ⚠ 告警**没有被藏起来**:过期项自己在行里标红、外加卡内那块琥珀提示,
        // 工具栏右上角还有一枚「降级 N 项」——三处都说得出口。
        NKCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Text("⑤ 数据新鲜度与降级披露").font(NKFont.headline)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 0)
                    // 报告没出的时候右端不能写「EOD 硬数据」—— 这一段此刻一个 EOD 读数
                    // 都没有(状态原型 779 行同款措辞)。
                    if model.report.dataFreshness == nil {
                        NKChip(text: "这一段恒在 · 但此刻只说得出一句")
                    } else {
                        Text("EOD 硬数据").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                }
                if let f = model.report.dataFreshness {
                    DataFreshnessDetail(freshness: f)
                    // 「扫描层过期」与「扫描层这一组键整体缺席」都要说这句(后者 =
                    // 连它新不新鲜都没查到,更该说)。判据与 `DataFreshnessDetail`
                    // 那一行的 `present` 逐字相同。
                    let scanMissing = f.scanLayerLagDays == nil && f.scanLayerDate == nil
                        && f.scanLayerStale == nil
                    if f.scanLayerStale == true || scanMissing {
                        // 扫描层没跑 → 今日无种子 → 今日无篮子;「今天没有篮子」与
                        // 「今天没看」必须能分开,这一句就是把它们分开的那句话。
                        noticeBlock("今日篮子若为空,可能是**没看**而不是**今天真没有** —— 扫描层三张预计算表本次没查到。")
                    }
                } else {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: "questionmark.circle")
                            .font(.system(size: 20, weight: .light))
                            .foregroundStyle(NK.textTertiary).padding(.top, 1)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("本次连数据新鲜度都没查到").font(NKFont.headline)
                                .foregroundStyle(NK.textSecondary)
                            Text("⛔ 这不等于「数据新鲜」—— 该报告没有新鲜度记录。")
                                .font(NKFont.callout).foregroundStyle(NK.amber)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 8)
                    // 状态原型 786 行:说清**为什么**此刻只说得出这一句 ——
                    // `dataFreshness` 挂在报告快照上,没有报告就没有这个字段。
                    Divider().overlay(NK.hairline)
                    // ⚠ 一整条字面量,⛔ 别拆成 `+` 拼接:拼出来的是 `String`,
                    // `Text(String)` 不解析 Markdown,`**恒在**` 会把星号原样印出来。
                    Text("这一段**恒在**(它在 degraded 分支之外),但报告没出的时候它能说的只有这一句 —— dataFreshness 挂在报告快照上,没有报告就没有这个字段。")
                        .font(NKFont.caption).lineSpacing(3).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !daily.notes.isEmpty {
                    NKDisclosure(summary: "本次生成备注 \(daily.notes.count) 条", tone: .warn) {
                        ForEach(daily.notes, id: \.self) { n in
                            Text("· \(n)").fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
        #else
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
        #endif
    }

    #if os(macOS)
    /// 卡内琥珀提示块(原型 793 行 `padding:11px 13px; radius:8;
    /// background:rgba(232,145,10,.06); border:.5px rgba(232,145,10,.20)`)。
    /// ⚠ 参数类型是 `LocalizedStringKey` **不是 `String`**:`Text(String)` 不解析
    /// Markdown,`**没看**` 会原样把星号印在界面上(实拍逮到)。
    private func noticeBlock(_ text: LocalizedStringKey) -> some View {
        Text(text).font(NKFont.callout).lineSpacing(3).foregroundStyle(NK.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 13).padding(.vertical, 11)
            .background(RoundedRectangle(cornerRadius: NKRadius.control)
                .fill(NK.amber.opacity(0.06)))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                .stroke(NK.amber.opacity(0.20), lineWidth: 0.5))
    }
    #endif
}

// MARK: - 列表行外壳(选中态 = 白底 + accent 描边;⛔ 靠选中态分隔,不靠留白)

struct NKListRow<Content: View>: View {
    let selected: Bool
    let action: () -> Void
    @ViewBuilder var content: Content

    var body: some View {
        Button(action: action) {
            content
                // 原型 `pick()`(macOS 原型 1783 行):`padding:11px 12px; border-radius:9px`。
                .padding(.horizontal, 12).padding(.vertical, 11)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: NKRadius.inner)
                        .fill(selected ? NK.cardBg : Color.clear)
                        // 🔴 选中态是**白底 + 1.5px 实蓝描边 + 一层极轻投影**,
                        // ⛔ 不是 1px 半透明蓝(V2.3.0 的 `accent.opacity(0.55)` 在
                        // `#FCFCFD` 列表栏底上几乎看不出选中)。原型
                        // `box-shadow:0 0 0 1.5px #0B6BCB, 0 1px 3px rgba(0,0,0,.06)`。
                        // ⚠ 阴影**恒定不参与动画**(全局三禁之一):选中态切换只换颜色。
                        .shadow(color: selected ? Color.black.opacity(0.06) : .clear,
                                radius: 1.5, y: 1)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: NKRadius.inner)
                        .stroke(selected ? NK.accent : Color.clear, lineWidth: 1.5)
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
        VStack(alignment: .leading, spacing: 0) {
            NKListRow(selected: selected) {
                model.openBasket(id: basket.basketId)
            } content: {
                VStack(alignment: .leading, spacing: 9) {   // 原型 128 行 margin-top:9
                    HStack(alignment: .top, spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            // 原型 122 行 `13px/600` + **单行省略**(⛔ 不折行:折行会把
                            // 一整栏的行高搅成参差不齐,列表就没法一眼扫)。
                            Text(basket.name.isEmpty ? basket.basketKey : basket.name)
                                .font(NKFont.body).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                                .lineLimit(1).truncationMode(.tail)
                            if let card = basket.card, !card.driver.isEmpty {
                                Text(card.driver)
                                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                    .lineLimit(1).truncationMode(.tail)
                            }
                        }
                        Spacer(minLength: 6)
                        VStack(alignment: .trailing, spacing: 3) {
                            if let p = basket.scoreDisplayPercent {
                                // 原型 126 行 `16px/600 tabular`;字阶就近取 `headline`(15/600)
                                // —— ⛔ 视图里不写裸 `.system(size:)`(CLAUDE.md 字阶纪律)。
                                Text(String(format: "%.1f", p))
                                    .font(NKFont.headline.monospacedDigit())
                                    .foregroundStyle(NK.textPrimary)
                            }
                            if let r = basket.card?.rankInTier, let t = basket.tier {
                                Text("T\(t) 第 \(r) 位").font(NKFont.caption)
                                    .foregroundStyle(NK.textTertiary).lineLimit(1)
                            }
                        }
                        .fixedSize()
                    }
                    HStack(spacing: 8) {
                        // 🔴 六关灯条(机械关方角 + 外环 / 证据关圆点)+ 一句摘要。
                        GateLightBar(gates: basket.gates)
                        Spacer(minLength: 4)
                        // 验证状态实时角标(四态;**「今天还没判过」与「判了是 unclear」讲不同的话**)。
                        VerificationBadge(model: model, basketId: basket.basketId)
                        if let n = memberCount {
                            Text("\(n) 只").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                    // 退潮刹车时每一行都带这一句(状态原型 133 行:`padding:6px 9px;
                    // radius:7; background:rgba(229,68,59,.08); 10.5/600 #E5443B`)。
                    // 🔴 **作废的是计划,不是数据** —— 卡、六关、逐只判定照常可读,
                    // ⛔ 别把行灰掉、更别把它从列表里拿掉。
                    if model.board.retreatBrake.active {
                        Text("今日计划作废 —— 这篮的卡与判定照常可读")
                            .font(NKFont.badge).foregroundStyle(NK.down)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 9).padding(.vertical, 6)
                            .background(RoundedRectangle(cornerRadius: NKRadius.control)
                                .fill(NK.down.opacity(0.08)))
                    }
                }
            }
            // 🔴 **成员区在选中框外面**(原型 148 行:它是 `pick()` 那个 div 的**兄弟**,
            // `padding:3px 4px 6px 10px`)—— 放进框里会让选中态的白底把成员一起圈进去,
            // 而成员行自己另有一套选中底色。
            membersStrip
                .padding(.leading, 10).padding(.trailing, 4)
                .padding(.top, 3).padding(.bottom, 6)
        }
        // 🔴 **iOS 上一篮就是一张白卡**(iOS 原型 364 行 `padding:15; radius:18;
        // background:#fff; border:.5px rgba(60,60,67,.10)`)——手机上没有 macOS 那条
        // 「列表栏 vs 详情栏」的分栏,裸行摞在页底色上分不出哪几行属于同一篮。
        // ⛔ macOS 侧不跟着改:那边靠 `pick()` 的选中态分隔,加卡会让整栏变成一摞盒子。
        #if os(iOS)
        .padding(15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 18).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(NK.hairline, lineWidth: 0.5))
        #endif
    }

    private var memberCount: Int? {
        if let m = basket.card?.members, !m.isEmpty { return m.count }
        return basket.memberCodes.isEmpty ? nil : basket.memberCodes.count
    }

    /// **成员直接列在行下面**。卡就绪 → 状态点 + 名称 + 角色 + RS;卡未就绪 → 只有代码
    /// (原型 210–215 行:**代码横排一行**),并如实说「名称与逐只判定随卡一起来」
    /// (⛔ 不等于这几只票没通过)。
    @ViewBuilder
    private var membersStrip: some View {
        if let members = basket.card?.members, !members.isEmpty {
            VStack(alignment: .leading, spacing: 1) {   // 原型 148 行 gap:1
                ForEach(members) { m in
                    HStack(spacing: 7) {                // 原型 `memRow()` gap:7
                        // 5px 状态点 = **位置关 / 核心关两关取最差**;两关都没判 → 灰
                        // (原型 150/156/162/168 行四行分别是绿 / 红 / 绿 / 灰)。
                        // ⚠ 纯展示层聚合,读的就是同一行右边那两枚判定 —— ⛔ 不另算判据。
                        Circle().fill(memberDotColor(m)).frame(width: 5, height: 5)
                        Text(m.name.isEmpty ? m.tsCode : m.name)
                            .font(NKFont.caption).fontWeight(.medium)
                            .foregroundStyle(NK.textPrimary).lineLimit(1)
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
                    .padding(.horizontal, 8).padding(.vertical, 4)   // 原型 `memRow()`
                }
            }
        } else if !basket.memberCodes.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 7) {
                    Circle().fill(NK.textTertiary.opacity(0.5)).frame(width: 5, height: 5)
                    ForEach(basket.memberCodes, id: \.self) { c in
                        Text(c).font(NKFont.caption.monospacedDigit())
                            .foregroundStyle(NK.textSecondary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 8).padding(.vertical, 4)
                // ⚠ 前半句取 `cardUnavailableText`(它把 `card_not_ready` 与
                // **`card_corrupt`** 分开说 —— 后者是数据事故、不会自己好,
                // ⛔ 不许合成「还没生成」)。
                Text("\(basket.cardUnavailableText ?? "卡还没生成") —— 只有成员代码,名称与逐只判定随卡一起来")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 8)
            }
        }
    }

    /// 两关取最差:任一 `bad` → 红;任一 `warn` → 琥珀;两关都 `good` → 绿;
    /// **一关都没判出来 → 灰**(⛔ 不默认成绿:那是把"没判"讲成"没问题")。
    private func memberDotColor(_ m: BasketMember) -> Color {
        let tones = [m.positionVerdictLabel.map { _ in m.positionVerdictTone },
                     m.coreVerdictLabel.map { _ in m.coreVerdictTone }].compactMap { $0 }
        if tones.isEmpty { return NK.textTertiary.opacity(0.7) }
        if tones.contains(.bad) { return NK.down }
        if tones.contains(.warn) { return NK.amber }
        if tones.contains(.good) { return NK.up }
        return NK.textTertiary.opacity(0.7)
    }
}

// MARK: - ③b 一行

private struct DroppedBasketRow: View {
    let dropped: DroppedBasket

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // 原型 238–243 行:**一行**,名 / 原因 / 分数,⛔ 不是一张白卡 ——
            // ③b 是"没进正选的",在列表栏里给它跟正选一样的卡片重量就喧宾夺主了。
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(dropped.name).font(NKFont.callout)
                    .foregroundStyle(NK.textSecondary)
                    .lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 6)
                // 🔴 `bad` = **系统自己缺席**(不是市场结论),给一枚实心徽标
                // 把它从"今天没好票"里拎出来。
                if dropped.reasonTone == .bad {
                    NKChip(text: "系统缺席", tone: .bad, filled: true)
                }
                Text(dropped.reasonHeadline).font(NKFont.caption).fontWeight(.semibold)
                    .foregroundStyle(dropped.reasonTone.color)
                    .lineLimit(1).fixedSize()
                if let s = dropped.mechScore {
                    Text(String(format: "%.1f", s))
                        .font(NKFont.callout.monospacedDigit())
                        .foregroundStyle(NK.textTertiary)
                }
            }
            // ⚠ **补充信息收成一行**(原型 ③b 一行只有名 / 原因 / 分):
            // V2.2-③ 的「卡在哪一关 + 那一关是哪一类」与原因补充句同属"再说细一点",
            // 分三行摊开会让 ③b 比正选的篮子行还高。老快照没有这两键 → 不写(⛔ 不写
            // 「无」,那会看起来像"没卡在任何关")。
            if let sub = droppedSubline {
                Text(sub).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 服务端的机器原因码串(数值内嵌)。**原样展示,⛔ 不改写** —— 下沉审计视图。
            // ⚠ 列表栏用 `compact`(一行弱标),⛔ 不用详情栏那枚虚线大按钮:
            // 376pt 宽的栏里那枚按钮会盖过它上面那一行真正的内容。
            if let detail = dropped.gateDetail, !detail.isEmpty {
                NKAuditSection(contains: "机械读数原始件", compact: true) {
                    Text(detail).font(NKFont.monoKey).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)    // 原型 238 行 `padding:9px 12px`
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// 「卡在 X 关(证据关 · 只降级)· 原因补充句」——三段有哪段写哪段。
    private var droppedSubline: String? {
        var parts: [String] = []
        if let g = dropped.gateLabel {
            // 🔴 同 `OutCandidateRow` 那条:走 `nkGateEnforcementNote`,⛔ 不用
            // `dropped.gateKind.label` —— 市场关 / 板块关的 `*_unfit` 印成「硬否决」是说反。
            let kind = nkGateEnforcementNote(gate: dropped.gate, reason: dropped.reason)
                .map { "(\($0))" } ?? ""
            parts.append("卡在 \(g)\(kind)")
        }
        if let d = dropped.reasonDetail, !d.isEmpty { parts.append(d) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// ③b-2 的一行:**股票级 OUT**(V2.3.2-②-B;K8 §十-11 四项 = 股票 / 主引擎+版本 /
/// 出局关口 / 理由)。
///
/// ⚠ **刻意分两行**(CLAUDE.md 402pt 那条坑):iPhone 上「名称+代码 + 角色 + 引擎 +
/// 关口 + 原因」挤一行会把名称压成两行、把中文徽标压成竖排单字。首行只放
/// **票 + 出局结论**,其余全部收进次行。
private struct OutCandidateRow: View {
    let item: OutCandidate

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(item.display).font(NKFont.callout)
                    .foregroundStyle(NK.textSecondary)
                    .lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 6)
                // 🔴 `bad` = **系统自己缺席**(引擎没跑 / 归属解析失败),不是市场结论。
                if item.reasonTone == .bad {
                    NKChip(text: "系统缺席", tone: .bad, filled: true)
                }
                Text(item.reasonHeadline).font(NKFont.caption).fontWeight(.semibold)
                    .foregroundStyle(item.reasonTone.color)
                    .lineLimit(1).fixedSize()
            }
            if let sub = subline {
                Text(sub).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 服务端的原因码串 / 模型理由。**原样展示,⛔ 不改写** —— 下沉审计视图。
            if let detail = item.outDetail, !detail.isEmpty {
                NKAuditSection(contains: "出局判定原始件", compact: true) {
                    Text(detail).font(NKFont.monoKey).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// 「角色 · 主引擎 C1 · 卡在 板块关(机械关 · 硬否决)」——有哪段写哪段。
    /// ⛔ 缺的段一律不写(⛔ 不写「无」,那会看起来像"确实没有引擎/关口")。
    private var subline: String? {
        var parts: [String] = []
        if let r = item.role, !r.isEmpty { parts.append(nkRoleLabel(r)) }
        if let e = item.engineLabel { parts.append("主引擎 \(e)") }
        if let g = item.gateLabel {
            // 🔴 走 `nkGateEnforcementNote`,⛔ 不直接印 `nkGateKind(...).label`:
            // 市场关 / 板块关自 V2.3.2-① 起是半机械半证据的,只看关别会把
            // `market_unfit` / `sector_unfit` 讲成「硬否决」——正好说反(实拍逮到过)。
            let kind = nkGateEnforcementNote(gate: item.outGate, reason: item.outReason)
                .map { "(\($0))" } ?? ""
            parts.append("卡在 \(g)\(kind)")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
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
    /// 机械分原值(原型 267 行「/ 100 · 机械分 7.4」)。nil = 这份快照没有 → 只写「/ 100」。
    var mechScore: Double? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 4 : 7) {   // 原型 268 行 gap:7
            if let p = percent {
                // 原型 265–269 行:主数 `32/600 tabular ls -1` + 「/ 100 · 机械分 7.4」
                // (`12px .40`)+ 右端「纯展示 · 不进排序」(`11px .40`)。
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(String(format: "%.1f", p))
                        .font(compact ? NKFont.metric : NKFont.heroNumber)
                        .tracking(compact ? 0 : -1)
                        .foregroundStyle(NK.textPrimary)
                    Text(scaleText).font(NKFont.callout).foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    Text("纯展示 · 不进排序").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
                .padding(.bottom, compact ? 0 : 5)
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

    /// 「/ 100 · 机械分 7.4」。⚠ 机械分**没给就不写**(⛔ 不拿百分数 ÷10 反推一个
    /// 看起来像原值的数 —— 那是客户端造第二份换算)。
    private var scaleText: String {
        guard let m = mechScore else { return "/ 100" }
        return "/ 100 · 机械分 " + String(format: "%.1f", m)
    }

    private var neutralCount: Int { contributions.filter(\.neutralFilled).count }

    private var disclosureSummary: String {
        neutralCount > 0 ? "\(neutralCount) 项按中性分计入" : "百分制是等价换算 · 纯展示"
    }

    private var disclosureTone: NKAxisTone { neutralCount > 0 ? .warn : .neutral }

    @ViewBuilder
    private func contributionRow(_ c: ScoreContribution) -> some View {
        // 原型 269 行:列宽 `76 / flex / 42 / 34`,条 `height:5 radius 999`,行间 gap 10。
        HStack(spacing: 10) {
            // ⚠ `*` 角标只挂在**数值**上(与服务端 markdown 那一行
            // 「龙头清晰度 12.5*」逐位同形),⛔ 不在标签上再挂一个。
            Text(c.displayLabel)
                .font(NKFont.caption)
                .foregroundStyle(c.neutralFilled ? NK.amber : NK.textSecondary)
                .frame(width: compact ? 76 : 82, alignment: .leading)
                .lineLimit(1).minimumScaleFactor(0.85)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(NK.chipNeutral).frame(height: 5)
                    // ⚠ **缺数那一维原型是斜纹**(`repeating-linear-gradient`)——
                    // SwiftUI 没有等价的可平铺图案填充,改用**半透明琥珀 + 虚线描边**:
                    // 同样一眼看出"这一条与别的不是一回事",判定「刻意不同 · 平台差异」。
                    Capsule()
                        .fill(c.neutralFilled ? NK.amber.opacity(0.35) : NK.accent)
                        .frame(width: geo.size.width * barFraction(c), height: 5)
                        .overlay(alignment: .leading) {
                            if c.neutralFilled {
                                Capsule()
                                    .strokeBorder(NK.amber.opacity(0.75),
                                                  style: StrokeStyle(lineWidth: 1, dash: [3, 2]))
                                    .frame(width: geo.size.width * barFraction(c), height: 5)
                            }
                        }
                }
                .frame(height: geo.size.height, alignment: .center)
            }
            .frame(height: 10)
            Text(contribText(c))
                .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                .foregroundStyle(c.neutralFilled ? NK.amber : NK.textPrimary)
                .frame(width: 42, alignment: .trailing)
            // 「占比」= 该维在机械分里的**归一化权重**(契约字段 `weight`,服务端给)。
            // ⛔ 客户端不自己求和、不自己归一化 —— 那就成了第二份换算。
            // ⚠ 原型这一列只有一个百分数(宽 34),「权重」二字省掉 —— 列头位置
            // 已经由「/ 100 · 机械分」那一行交代过这是打分卡。
            Text(weightText(c))
                .font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textTertiary)
                .frame(width: 38, alignment: .trailing)
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
        guard let w = c.weight else { return "—" }
        return NKFmt.ratioPct(w)
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
    /// macOS:强势板块 chips 画在**这张卡里**(原型 771–775);iOS 传空数组、
    /// 由 `SectorChipsRow` 另画一行(批 7 再核)。
    var sectors: [SectorSnapshot] = []

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    // ⚠ 段名住卡头(原型 737 行 `15px/600`),⛔ 不再另起一个卡外段头。
                    Text("① 情绪与市场语境").font(NKFont.headline)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 0)
                    NKChip(text: "仓位额度 \(PositionQuota(sentiment.positionQuota).label)",
                           tone: PositionQuota(sentiment.positionQuota).tone, filled: true)
                }
                // 原型 740 行 `repeat(3,1fr); gap:18px 24px`。
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 24,
                                                            alignment: .topLeading), count: 3),
                          alignment: .leading, spacing: 18) {
                    metric("涨停", "\(sentiment.limitUpCount)", "家")
                    metric("跌停", "\(sentiment.limitDownCount)", "家")
                    zabanMetric
                    metric("最高连板", "\(sentiment.maxConsecLimitUp)", "板")
                    metric("昨涨停今溢价", premiumValue, premiumUnit,
                           tone: (sentiment.prevLimitUpPremiumAvg ?? 0) < 0 ? NK.down : nil)
                    metric("样本", "\(sentiment.prevLimitUpSample)", "只")
                }
                VStack(alignment: .leading, spacing: 12) {
                    Divider().overlay(NK.hairline)
                    Text(sentiment.quotaReason)
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if !sectors.isEmpty {
                        NKWrapRow(spacing: 6, lineSpacing: 6) {
                            ForEach(sectors) { s in
                                NKChip(text: "\(s.name) · 第\(s.boardAge)天 · "
                                       + NKFmt.signedPct(s.ret20d * 100),
                                       tone: s.bonus > 0 ? .good : .neutral)
                            }
                        }
                    }
                }
            }
        }
    }

    /// `nil` = 昨日无涨停股或数据缺失,**非"溢价为 0"**(服务端 docstring 原话)。
    private var premiumValue: String {
        guard let v = sentiment.prevLimitUpPremiumAvg else { return "—" }
        return String(format: "%+.2f", v * 100)
    }
    private var premiumUnit: String { sentiment.prevLimitUpPremiumAvg == nil ? "" : "%" }

    /// 炸板率多一条迷你条 + 章程阈值竖线(原型 745 行)。
    /// 🔴 **阈值那根竖线画在 35%**:它是**章程口径**(`quotaReason` 里那句话的来源),
    /// ⛔ 不是客户端自定的判据 —— 这里只画,不参与任何判定。
    private var zabanMetric: some View {
        VStack(alignment: .leading, spacing: 0) {
            valueLine(NKFmt.pct(sentiment.zabanRate * 100).replacingOccurrences(of: "%", with: ""),
                      "%", tone: sentiment.zabanRate > 0.35 ? NK.amber : nil)
            Text("炸板率").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                .padding(.top, 2)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(NK.chipNeutral).frame(height: 4)
                    Capsule().fill(NK.amber)
                        .frame(width: geo.size.width * min(1, max(0, sentiment.zabanRate)),
                               height: 4)
                    Rectangle().fill(NK.textSecondary)
                        .frame(width: 1.5, height: 8)
                        .offset(x: geo.size.width * 0.35)
                }
                .frame(height: geo.size.height, alignment: .center)
            }
            .frame(height: 8).padding(.top, 6)
            Text("竖线 = 章程阈值 35%").font(NKFont.caption)
                .foregroundStyle(NK.textTertiary).padding(.top, 3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// 原型 741–743 行:**读数在上、名字在下**(V2.3.0 是名字在上),
    /// 且数值与单位分成两档字号(`22/600` + `11.5 .40`)。
    private func metric(_ label: String, _ value: String, _ unit: String,
                        tone: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            valueLine(value, unit, tone: tone)
            Text(label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func valueLine(_ value: String, _ unit: String, tone: Color?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(value).font(NKFont.metric).tracking(-0.4)
                .foregroundStyle(tone ?? NK.textPrimary)
            if !unit.isEmpty {
                Text(unit).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
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
