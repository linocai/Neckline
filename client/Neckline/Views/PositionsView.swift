//
//  PositionsView.swift
//  Neckline — **持仓**(D8 四板块之一,V2-⑮):极简台账 + 同题材合并敞口 +
//  计划继承展示 + 盘中动态 + 临时提醒入口。
//
//  **⑩ 的产品意图**:买卖录入控制在数秒内、不再要求长表单;**系统自动记录,人只补充
//  机器不知道的**。故:
//   · 开仓 = **票 + 价 + 量**(+ 日期),其余(来源篮子 / Tier / 角色 / 市场快照)自动关联;
//   · 卖出 = 价 + **可选**快捷标签(九码);
//   · 「用户可选补充」(七枚标签 + 一句说明)是**独立的、可跳过的**动作,⛔ 不做前置阻断。
//
//  ⚠ **V2.3 视觉升级**:
//    · macOS 改「列表栏 376 + 详情栏」——左边是仓位一览,右边是选中那一笔的全部内容;
//      iOS 改**推入式详情**(列表 → 详情页),动作按钮吸底、盘中单手够得到。
//    · **止损刻度尺 `NKStopScale` 是这一屏的主角**:成本 / 现价 / 止损线三点一线,
//      不看数字也知道离线多远;破线那张卡整圈描红。
//    · 「已在券商挂 -5% 条件单」勾选**改为按 `positionId` 落 UserDefaults**
//      (规范 §08 第 1 条;⛔ 不写服务端,见 `NKStopOrderLedger`)。
//    · 退潮刹车条**已上移到壳**(`RootView`),⛔ 本页不再画一条。
//
//  🔴 **「占总仓 %」本页一律不显示 —— 契约里没有这个分母**(V2.3 施工期核实):
//  原型画了「占总仓 61.2% / 35.3%」,但「总仓」分母的唯一源是**服务端**
//  `Settings.total_capital`(默认 12 万,`.env` 的 `TOTAL_CAPITAL` 可覆盖),
//  而它**从未下发到客户端**(`schemas.py` / `app.py` 零出现)。在客户端写死 12 万 =
//  给一个钉死的领域常量造第二份事实源,用户改了 `.env` 之后界面会一直说谎。
//  故本页只显示**金额**(成本额 / 浮盈额),⛔ 不显示占比。要占比得服务端先发这个数。
//
//  **本页硬纪律**:
//   · 计划 vs 实际偏离 → 提示「原盈亏结构已变」,**不质问、不阻断、不进任何判定**。
//   · 同题材合并敞口(蓝图 6.2):同一来源篮子的多笔仓**不得视为完全分散的两笔仓位**。
//   · ⑪-D-D per-position「不提醒」开关:关的是**这一票的触达提醒**,⛔ 不连坐其它持仓。
//   · 系统**永不代下单**:补录是记账动作,退潮刹车只醒目警示、不硬拦(硬拦 = 帮用户瞒报)。
//   · ⚠ **熔断整套已于 V2.2-⑤-B 退役**(裁定 #8):横幅 / 开仓灰化 / 解锁弹层全删,
//     留下的只有一条纯告知推送与一条看板事件。⛔ 不许在本文件里加回任何锁定态。
//

import SwiftUI

struct PositionsView: View {
    @Bindable var model: AppModel

    private var selected: Position? {
        model.selectedPositionId.flatMap { model.position(byID: $0) }
    }

    var body: some View {
        platformBody
            .sheet(item: sheetBinding) { item in
                #if os(iOS)
                sheetContent(item.kind)
                #else
                sheetContent(item.kind).frame(width: 460)
                #endif
            }
            .sheet(isPresented: $model.showAlertComposer) {
                #if os(iOS)
                AlertComposerSheet(model: model)
                #else
                AlertComposerSheet(model: model).frame(width: 500, height: 660)
                #endif
            }
    }

    // MARK: - iOS:列表 → 推入式详情

    #if os(iOS)
    private var platformBody: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    summaryStrip
                    MarketRegimeStrip(regime: model.marketRegime, compact: true)
                    mergedExposureSection
                    positionsListSection
                    alertsSection
                    BoardSection(model: model)
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(AppTab.positions.title)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await model.refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .refreshable { await model.refresh() }
            .navigationDestination(for: Int.self) { pid in
                if let p = model.position(byID: pid) {
                    PositionDetailPage(model: model, position: p)
                } else {
                    // 该仓已不在列表里(刚补录清仓)——如实说,⛔ 不留白。
                    NKEmptyState(title: "这笔仓已不在持仓列表里",
                                 subtitle: "多半是刚补录过清仓;回上一页看看。",
                                 systemImage: "tray")
                }
            }
        }
    }
    #endif

    // MARK: - macOS:列表栏 376 + 详情栏

    #if os(macOS)
    private var platformBody: some View {
        NKSplitLayout {
            listColumn
        } detail: {
            if let p = selected {
                PositionDetailPage(model: model, position: p)
            } else {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    NKDetailPlaceholder(
                        title: model.positions.isEmpty ? "暂无持仓" : "选一笔仓看详情",
                        subtitle: model.positions.isEmpty
                            ? "补录你已在券商完成的买入之后,这里会出现纪律位置、计划继承与交易时钟。"
                            : "左边点一笔,这里显示止损刻度尺、纪律位置、持仓计划与交易时钟。",
                        systemImage: "chart.line.uptrend.xyaxis")
                    BoardSection(model: model)
                }
            }
        }
    }

    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            HStack {
                Text(AppTab.positions.title).font(NKFont.title2).foregroundStyle(NK.textPrimary)
                Spacer()
                addPositionButton
            }
            .padding(.horizontal, 6).padding(.bottom, 6)

            summaryStrip.padding(.horizontal, 4).padding(.bottom, 8)
            MarketRegimeStrip(regime: model.marketRegime, compact: true)
                .padding(.horizontal, 4).padding(.bottom, 8)
            mergedExposureSection.padding(.horizontal, 4)

            Text("持仓 \(model.positions.count) · 先管住手里的").font(NKFont.title3)
                .foregroundStyle(NK.textPrimary)
                .padding(.horizontal, 6).padding(.top, 10).padding(.bottom, 4)

            if model.positions.isEmpty {
                NKCard { NKEmptyState(title: "暂无持仓", systemImage: "tray") }
            } else {
                ForEach(model.positions) { p in
                    PositionListRow(model: model, position: p,
                                    selected: model.selectedPositionId == p.id)
                }
            }

            alertsSection.padding(.horizontal, 4).padding(.top, 12)
        }
    }
    #endif

    // MARK: - 概要条(**金额,⛔ 无占比** —— 分母不在契约里,见文件头)

    private var summaryStrip: some View {
        HStack(spacing: NKSpace.blockGap) {
            summaryItem("合计成本", "¥" + NKFmt.price(totalCost), NK.textPrimary)
            summaryItem("合计浮盈", NKFmt.signedMoney(totalPnl),
                        totalPnl >= 0 ? NK.up : NK.down)
            summaryItem("待处理", "\(pendingCount) 件",
                        pendingCount > 0 ? NK.amber : NK.textSecondary)
            Spacer(minLength: 0)
        }
    }

    private func summaryItem(_ title: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            Text(value).font(NKFont.metric).foregroundStyle(color)
        }
    }

    private var totalCost: Double {
        model.positions.reduce(0) { $0 + $1.buyPrice * Double($1.qty) }
    }

    /// ⚠ **只把拿得到实时价的那几笔算进浮盈**(停牌 / 拉不到行情的那笔 `price == 0`,
    /// 拿买入价顶上会算出一个恒等于 0 的假浮盈)。
    private var totalPnl: Double {
        model.positions.filter(\.hasLivePrice).reduce(0) { $0 + $1.pnlAmount }
    }

    /// 「待处理」= 已破止损线 + 今日应离场。⛔ 这是**提醒计数**,不是待办清单,
    /// 更不是"系统建议你做这几件事"。
    private var pendingCount: Int {
        model.positions.filter { $0.hasBrokenStop || $0.isExitDay }.count
    }

    private var addPositionButton: some View {
        // 🔴 **「熔断中灰化开仓」已删**(V2.2-⑤-B / 〇b-7):补录本就是记账动作,
        // 而这个按钮此前是全系统唯一一处「程序替用户做决定」的自律灰化。
        // ⛔ 不许以任何条件把它再灰掉(**退潮刹车激活时同样不灰**)。
        Button { model.beginPositionEntryFlow() } label: {
            Label("补录开仓", systemImage: "plus.circle.fill")
                .font(NKFont.callout).fontWeight(.semibold)
        }
        .buttonStyle(.plain).foregroundStyle(NK.accent)
    }

    // MARK: - 同题材合并敞口(蓝图 6.2)

    @ViewBuilder
    private var mergedExposureSection: some View {
        let merged = model.mergedExposures
        if !merged.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("同题材合并敞口 \(merged.count) 组").nkLabel().foregroundStyle(NK.textTertiary)
                ForEach(merged) { m in
                    NKCard(padding: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                Text(m.basketName).font(NKFont.body).fontWeight(.semibold)
                                    .foregroundStyle(NK.textPrimary)
                                NKChip(text: "\(m.codes.count) 只", tone: .warn)
                                Spacer()
                                Text("成本 ¥\(NKFmt.price(m.costAmount))")
                                    .font(NKFont.callout.monospacedDigit())
                                    .foregroundStyle(NK.textSecondary)
                            }
                            Text("它们一起涨、一起跌,**不是**完全分散的两笔仓位。")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(m.codes.joined(separator: "、"))
                                .font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - iOS 持仓列表

    #if os(iOS)
    private var positionsListSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            HStack {
                NKSectionHeader(title: "持仓 \(model.positions.count)")
                Spacer()
                addPositionButton
            }
            Text("先管住手里的").font(NKFont.caption).foregroundStyle(NK.textTertiary)
            if model.positions.isEmpty {
                NKCard { NKEmptyState(title: "暂无持仓", systemImage: "tray") }
            } else {
                ForEach(model.positions) { p in
                    NavigationLink(value: p.id) {
                        PositionListRow(model: model, position: p, selected: false,
                                        interactive: false)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
    #endif

    // MARK: - 临时提醒(⑪-C,**只通知,永不交易**)

    private var alertsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            HStack {
                NKSectionHeader(title: "临时提醒 \(model.alerts.count)")
                Spacer()
                Button { model.beginAlertComposer() } label: {
                    Label("新建提醒", systemImage: "bell.badge")
                        .font(NKFont.callout).fontWeight(.semibold)
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
            }
            Text("用一句话描述条件,系统只在命中时通知你 —— **永不自动交易**")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            if model.alerts.isEmpty {
                NKCard { NKEmptyState(title: "还没有临时提醒", systemImage: "bell.slash") }
            } else {
                ForEach(model.alerts) { a in
                    AlertRow(model: model, alert: a)
                }
            }
        }
        .task { await model.loadAlerts() }
    }

    // MARK: - 弹层

    private var sheetBinding: Binding<SheetItem?> {
        Binding(get: { model.modal.map(SheetItem.init) },
                set: { if $0 == nil { model.dismissModal() } })
    }

    /// `.sheet(item:)` 需要 Identifiable;`PositionModal` 本身只 Equatable,包一层。
    private struct SheetItem: Identifiable, Equatable {
        let kind: PositionModal
        var id: String {
            switch kind {
            case .open: return "open"
            case .close(let code): return "close-\(code)"
            case .note: return "note"
            case .tradeNote(let positionId): return "tradeNote-\(positionId)"
            }
        }
    }

    @ViewBuilder
    private func sheetContent(_ kind: PositionModal) -> some View {
        switch kind {
        case .open: OpenPositionSheet(model: model)
        case .close(let code): ClosePositionSheet(model: model, code: code)
        case .note: NoteSheet(model: model)
        case .tradeNote(let positionId): TradeClockNoteSheet(model: model, positionId: positionId)
        }
    }
}

// MARK: - 仓位一行(列表态:名称 / D 徽标 / 代码·买价×量 / 现价 / 涨跌 / 止损线 / 距止损)

struct PositionListRow: View {
    @Bindable var model: AppModel
    let position: Position
    let selected: Bool
    /// macOS 列表里这一行**自己是按钮**;iOS 上外面套着 `NavigationLink`,
    /// 再套一层 `Button` 会吃掉点击 —— 故 iOS 传 `false`。
    var interactive: Bool = true

    var body: some View {
        Group {
            if interactive {
                NKListRow(selected: selected) {
                    model.selectedPositionId = position.id
                } content: { rowBody }
            } else {
                rowBody
                    .padding(.horizontal, 10).padding(.vertical, 9)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
                    .overlay(
                        RoundedRectangle(cornerRadius: NKRadius.card)
                            .stroke(position.hasBrokenStop ? NK.down : NK.hairline,
                                    lineWidth: position.hasBrokenStop ? 1 : 0.5)
                    )
            }
        }
    }

    private var rowBody: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .top, spacing: 6) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Text(position.name).font(NKFont.title3).foregroundStyle(NK.textPrimary)
                        // 两档 D 徽标(服务端按 D5 净浮盈判好下发,客户端不重算)。
                        // ⚠ V2.2-⑤ 起章程可能**没有时间退出条款** → 徽标只报 D 计数
                        // (`dBadgeText` 单点判断),⛔ 不拿 D5 顶上冒充有上限。
                        NKChip(text: position.dBadgeText, tone: dBadgeTone,
                               filled: dBadgeTone != .neutral)
                    }
                    Text("\(position.code) · ¥\(NKFmt.price(position.buyPrice)) × \(position.qty)")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 1) {
                    Text(position.hasLivePrice ? NKFmt.price(position.price) : "—")
                        .font(NKFont.metric).foregroundStyle(NK.textPrimary)
                    if position.hasLivePrice {
                        Text(NKFmt.signedPct(position.pnlPct))
                            .font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
                            .foregroundStyle(position.pnlPct >= 0 ? NK.up : NK.down)
                    }
                }
            }
            HStack(spacing: 8) {
                if position.hasBrokenStop {
                    Text("已破止损线 ¥\(NKFmt.price(position.stopLine))")
                        .font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
                        .foregroundStyle(NK.down)
                } else {
                    Text("止损 \(NKFmt.price(position.stopLine))")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
                if let dist = position.distToStopPctServer {
                    Text("距止损 \(NKFmt.signedPct(dist * 100))")
                        .font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(dist <= 0 ? NK.down : (dist <= 0.02 ? NK.amber : NK.textTertiary))
                }
                Spacer(minLength: 0)
            }
            // 停牌 / 无数据显式标注 —— **绝不静默把老价当今日价**。
            if let stale = position.priceStale {
                Text("停牌 \(stale.staleDays) 日 · 价格为 \(model.calendar.displayString(stale.lastCloseDate)) 最后成交价")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// D 徽标色调:到期(离场提示)→ 红底;浮盈豁免(持有态)→ 绿底;判向挂起 → 黄底。
    private var dBadgeTone: NKAxisTone {
        if position.isExitDay { return .bad }
        if position.timeExitKind == .profitExempt { return .good }
        if position.timeExitKind == .suspendedHold { return .warn }
        return .neutral
    }
}

// MARK: - 仓位详情(macOS 详情栏 / iOS 推入页)

struct PositionDetailPage: View {
    @Bindable var model: AppModel
    let position: Position

    /// 「已在券商挂 -5% 条件单」——**本机记录**,按 `positionId` 落 UserDefaults。
    /// ⚠ 初值从账本读(V2.3 之前是 `@State false`,刷新即丢)。
    @State private var stopOrderChecked: Bool = false
    @State private var stopOrderLabel: String? = nil

    /// 服务端 K4 命中里「该置顶醒目」的子集(level=strong ∧ evidenceStrength=price_volume;
    /// 弱证据即便标了 strong 也只降级展示,守 §2.4 铁律「证伪只用价量结构」)。
    private var topBillboardK4: [K4Advisory] { position.k4Advisory.filter { $0.isTopBillboard } }
    private var listK4: [K4Advisory] { position.k4Advisory.filter { !$0.isTopBillboard } }

    var body: some View {
        content
            .onAppear(perform: syncStopOrder)
            .onChange(of: position.id) { _, _ in syncStopOrder() }
            #if os(iOS)
            .navigationTitle(position.name)
            .navigationBarTitleDisplayMode(.inline)
            // 详情页动作按钮**吸底**,盘中单手够得到。
            .safeAreaInset(edge: .bottom) { actionBar }
            #endif
    }

    @ViewBuilder
    private var content: some View {
        #if os(iOS)
        ScrollView { detailStack.padding(NKSpace.pagePad) }
            .background(NK.pageBgIOS)
        #else
        detailStack
        #endif
    }

    private var detailStack: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if position.todayActionTone == .bad {
                TodayActionBanner(text: position.todayAction)
            }
            ForEach(topBillboardK4) { hit in K4AdvisoryBanner(hit: hit) }
            headerBlock
            stopScaleCard              // 🔴 纪律位置(刻度尺是主角)
            brokerOrderCard            // 已在券商挂 -5% 条件单(本机记录)
            k4Section
            // ⑩-B 计划继承卡 + ⑪-D-D per-position 触达提醒开关。
            NKCard { PositionPlanSection(model: model, position: position) }
            // V2.2-④-B 交易时钟(只读跟踪 + 「补一条主观说明」写入口)。
            NKCard { TradeClockSection(model: model, position: position) }
            #if os(macOS)
            macActionRow
            #endif
        }
    }

    // MARK: - 头部

    private var headerBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    #if os(macOS)
                    HStack(spacing: 6) {
                        Text(position.name).font(NKFont.title1).foregroundStyle(NK.textPrimary)
                        NKChip(text: position.dBadgeText, tone: dBadgeTone,
                               filled: dBadgeTone != .neutral)
                        if position.timeExitKind == .suspendedHold {
                            NKChip(text: "判向挂起 · 复牌当日重判", tone: .warn)
                        }
                    }
                    #endif
                    Text("\(position.code) · 买入 ¥\(NKFmt.price(position.buyPrice)) × \(position.qty) · \(model.calendar.displayString(position.buyDate))")
                        .font(NKFont.callout.monospacedDigit()).foregroundStyle(NK.textSecondary)
                    if !position.entryReason.isEmpty {
                        Text(position.entryReason).font(NKFont.body)
                            .foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 6)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(position.hasLivePrice ? NKFmt.price(position.price) : "—")
                        .font(NKFont.heroNumber).foregroundStyle(NK.textPrimary)
                    if position.hasLivePrice {
                        Text("\(NKFmt.signedPct(position.pnlPct)) · \(NKFmt.signedMoney(position.pnlAmount))")
                            .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                            .foregroundStyle(position.pnlPct >= 0 ? NK.up : NK.down)
                    }
                }
            }
            if position.todayActionTone != .bad && !position.todayAction.isEmpty {
                Text(position.todayAction)
                    .font(NKFont.body)
                    .fontWeight(position.todayActionTone == .warn ? .semibold : .regular)
                    .foregroundStyle(position.todayActionTone == .warn ? NK.amber
                                    : (position.todayActionTone == .good ? NK.up : NK.textTertiary))
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 停牌 / 无数据显式标注 —— **绝不静默把老价当今日价**。
            if let stale = position.priceStale {
                Text("停牌/无数据 \(stale.staleDays) 个交易日,价格为 \(model.calendar.displayString(stale.lastCloseDate)) 最后成交价(\(stale.reasonLabel))")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 定格日 ≠ D5 显式标注,只提示、不改判定逻辑,**只在晚于 D{maxHoldDays} 时展示**。
            if position.timeExitLockedLateDays > 0, let lockedDay = position.timeExitLockedDay,
               let cap = position.maxHoldDays {
                Text("定格于 D\(lockedDay),晚于 D\(cap) \(position.timeExitLockedLateDays) 天")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
            // 🔴 V2.2-⑤:本版章程无时间退出条款时,**把这件事说出口**——
            // 否则用户看到一个光秃秃的「D3」会以为系统忘了算上限。
            if let disclosure = position.timeExitDisclosure {
                Text(disclosure).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - 🔴 纪律位置(止损刻度尺)

    private var stopScaleCard: some View {
        NKStopScaleCard(stop: position.stopLine, cost: position.buyPrice,
                        price: position.hasLivePrice ? position.price : 0,
                        peak: position.retraceState?.peak) {
            VStack(alignment: .leading, spacing: 6) {
                Divider().overlay(NK.hairline)
                HStack(alignment: .top, spacing: 18) {
                    if let dist = position.distToStopPctServer {
                        statItem("距止损线", NKFmt.signedPct(dist * 100),
                                 dist <= 0 ? NK.down : (dist <= 0.02 ? NK.amber : NK.textPrimary))
                    }
                    if let rs = position.retraceState {
                        statItem("自峰值回落", NKFmt.pct(rs.retracePct * 100),
                                 rs.triggered ? NK.down : NK.textPrimary)
                    }
                    Spacer(minLength: 0)
                }
                if let rs = position.retraceState, rs.triggered {
                    Text("回落止盈已触发 —— 系统只提醒,不代下单。")
                        .font(NKFont.caption).foregroundStyle(NK.down)
                }
                // ⚠ 刻度尺只是版式:位置按价格线性映射,⛔ 不代表任何概率或建议。
                NKDisclosure(summary: "刻度尺是版式,不是判断") {
                    Text("四个刻度按价格**线性映射**到轨道上 —— 它只回答「离止损线还有多远」,"
                         + "⛔ 不代表任何概率、不构成任何建议。")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("止损线由服务端按现役章程派生(客户端不重算);峰值只在有回落止盈态时才画。")
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func statItem(_ title: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            Text(value).font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                .foregroundStyle(color)
        }
    }

    // MARK: - 已在券商挂 -5% 条件单(**本机记录**,⛔ 不写服务端)

    private var brokerOrderCard: some View {
        NKCard(padding: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Button {
                    stopOrderChecked.toggle()
                    NKStopOrderLedger.setChecked(stopOrderChecked, positionId: position.id)
                    stopOrderLabel = NKStopOrderLedger.checkedLabel(positionId: position.id)
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: stopOrderChecked ? "checkmark.square.fill" : "square")
                            .foregroundStyle(stopOrderChecked ? NK.up : NK.textTertiary)
                        Text("已在券商挂 -5% 条件单")
                            .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if let label = stopOrderLabel {
                    Text("\(label) · 本机记录,换机不同步")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                } else if position.hasBrokenStop {
                    // 没勾 + 已破线 = 这一格此刻最该被看见。
                    Text("还没勾 —— 这一票已破线").font(NKFont.caption)
                        .foregroundStyle(NK.amber)
                } else {
                    Text("本机记录,换机不同步 · 勾不勾**不改变任何判定**")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
            }
        }
    }

    private func syncStopOrder() {
        stopOrderChecked = NKStopOrderLedger.isChecked(positionId: position.id)
        stopOrderLabel = NKStopOrderLedger.checkedLabel(positionId: position.id)
    }

    // MARK: - K4 持仓牌

    @ViewBuilder
    private var k4Section: some View {
        if position.k4DataUnavailable == true || !listK4.isEmpty {
            NKCard(padding: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("K4 持仓牌").nkLabel().foregroundStyle(NK.textTertiary)
                    // K4 体检因无 EOD 行整份跳过 → 显式"今日未体检",不静默留空
                    // (空白 = 「体检过了没问题」,两者必须能分开)。
                    if position.k4DataUnavailable == true {
                        Text("K4 今日未体检 —— 无 EOD 行,整份跳过,**不是**「体检过了没问题」。")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !listK4.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 6) {
                                ForEach(listK4) { hit in
                                    NKChip(text: hit.evidenceStrength == "constituent"
                                           ? "\(hit.label) · 参考" : hit.label,
                                           tone: hit.isStrong ? .warn : .neutral)
                                }
                            }
                        }
                    }
                    if position.buyFees != nil || position.sellFees != nil {
                        feesRow
                    }
                }
            }
        } else if position.buyFees != nil || position.sellFees != nil {
            NKCard(padding: 12) { feesRow }
        }
    }

    @ViewBuilder
    private var feesRow: some View {
        HStack(spacing: 10) {
            if let bf = position.buyFees {
                Text("买入费 ¥\(NKFmt.price(bf))").font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textTertiary)
            }
            if let sf = position.sellFees {
                Text("卖出费 ¥\(NKFmt.price(sf))").font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textTertiary)
            }
            Text("实付,供周复盘对账用真数").font(NKFont.caption).italic()
                .foregroundStyle(NK.textTertiary)
            Spacer(minLength: 0)
        }
    }

    // MARK: - 动作

    #if os(iOS)
    /// 吸底动作条(盘中单手够得到)。
    private var actionBar: some View {
        HStack(spacing: 10) {
            Button { model.beginNote(code: position.code, positionId: position.id) } label: {
                Text("补充说明").font(NKFont.body).fontWeight(.semibold)
                    .frame(maxWidth: .infinity).padding(.vertical, 11)
                    .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.chipNeutral))
            }
            .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
            Button { model.openCloseSheet(code: position.code) } label: {
                Text("补录清仓").font(NKFont.body).fontWeight(.semibold)
                    .frame(maxWidth: .infinity).padding(.vertical, 11)
                    .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.down.opacity(0.12)))
            }
            .buttonStyle(.plain).foregroundStyle(NK.down)
        }
        .padding(.horizontal, NKSpace.pagePad).padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }
    #endif

    #if os(macOS)
    private var macActionRow: some View {
        HStack(spacing: 16) {
            Spacer()
            Button { model.beginNote(code: position.code, positionId: position.id) } label: {
                Text("补充说明").font(NKFont.callout).fontWeight(.semibold)
            }
            .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
            Button { model.openCloseSheet(code: position.code) } label: {
                Text("补录清仓").font(NKFont.callout).fontWeight(.semibold)
            }
            .buttonStyle(.plain).foregroundStyle(NK.down)
        }
    }
    #endif

    private var dBadgeTone: NKAxisTone {
        if position.isExitDay { return .bad }
        if position.timeExitKind == .profitExempt { return .good }
        if position.timeExitKind == .suspendedHold { return .warn }
        return .neutral
    }
}

/// D5/时间退出等最高优先级今日动作横幅。文案恒来自服务端 `todayAction`。
private struct TodayActionBanner: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").font(.system(size: 15, weight: .bold))
            Text(text).font(NKFont.body).fontWeight(.bold)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.alertGrad))
    }
}

/// K4 持仓牌强警示置顶横幅(level=strong ∧ evidenceStrength=price_volume 才走到这里)。
private struct K4AdvisoryBanner: View {
    let hit: K4Advisory
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "flag.fill").font(.system(size: 14, weight: .bold))
            VStack(alignment: .leading, spacing: 3) {
                Text(hit.label).font(NKFont.body).fontWeight(.bold)
                if !hit.evidence.isEmpty {
                    Text(hit.evidence).font(NKFont.caption).opacity(0.92)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.alertGrad))
    }
}
// MARK: - 临时提醒一行

private struct AlertRow: View {
    @Bindable var model: AppModel
    let alert: CustomAlert

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text(alert.subjectLabel).font(NKFont.body).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    NKChip(text: alert.statusLabel, tone: alert.statusTone)
                    Spacer()
                    if alert.maxFires > 0 {
                        Text("已触发 \(alert.firedCount)/\(alert.maxFires)")
                            .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                    }
                }
                if !alert.condition.isEmpty {
                    Text(alert.condition).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !alert.nlText.isEmpty {
                    Text("你的原话:\(alert.nlText)").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
                HStack(spacing: 12) {
                    if alert.firedCount > 0 {
                        Button { Task { await model.updateAlert(id: alert.id, resetFired: true) } } label: {
                            Text("重置触发计数").font(NKFont.caption)
                        }
                        .buttonStyle(.plain).foregroundStyle(NK.accent)
                    }
                    Spacer()
                    Button(role: .destructive) { Task { await model.deleteAlert(id: alert.id) } } label: {
                        Text("停用").font(NKFont.caption)
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.down)
                }
            }
        }
    }
}

// MARK: - 开仓 / 清仓表单(审计台账,补记用户已在券商完成的操作)

/// 两个表单共用的壳(标题 / 取消 / 提交)。
struct PositionFormShell<Content: View>: View {
    let title: String
    let onCancel: () -> Void
    let onSubmit: () -> Void
    @ViewBuilder var content: Content

    var body: some View {
        NavigationStack {
            Form { content }
                .formStyle(.grouped)
                .navigationTitle(title)
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消", action: onCancel) }
                    ToolbarItem(placement: .confirmationAction) { Button("提交", action: onSubmit) }
                }
        }
    }
}

struct OpenPositionSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        PositionFormShell(title: "补录开仓", onCancel: { model.dismissModal() },
                          onSubmit: { Task { await model.submitOpenPosition() } }) {
            Section {
                TextField("代码,如 600519.SH", text: $model.entryForm.code)
                TextField("买入价", text: $model.entryForm.price)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
                TextField("数量(股)", text: $model.entryForm.qty)
                    #if os(iOS)
                    .keyboardType(.numberPad)
                    #endif
                DatePicker("买入日", selection: $model.entryForm.buyDate, in: ...Date(),
                           displayedComponents: .date)
            } header: {
                Text("三字段即可提交")
            } footer: {
                Text("此处只记录你已在券商完成的真实操作;来源篮子 / Tier / 角色 / 市场快照由系统自动关联,止损线由服务端按现役章程派生返回。系统不代下单。")
            }
            Section {
                TextField("名称(可选)", text: $model.entryForm.name)
                TextField("进场理由(可选)", text: $model.entryForm.reason)
                TextField("实付买入费用(可选,含佣金/过户费等)", text: $model.entryForm.buyFees)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
            } header: {
                Text("可选补充")
            } footer: {
                if let range = model.entrySuggestionRange {
                    Text("参考手数区间 \(range.qtyLow)–\(range.qtyHigh) 股(¥\(NKFmt.price(range.capFloor))–¥\(NKFmt.price(range.capCeil)),上限 = 违纪判定线、**非推荐值**),预计止损价 ¥\(NKFmt.price(range.stopLine))(按现役配置,提交后以实际返回值为准)。")
                } else {
                    Text("实付费用留空时,D5 净浮盈判向走默认佣金率估算并诚实标注为估算;周复盘对账建议回填真数。")
                }
            }
        }
    }
}

struct ClosePositionSheet: View {
    @Bindable var model: AppModel
    let code: String

    var body: some View {
        PositionFormShell(title: "补录清仓", onCancel: { model.dismissModal() },
                          onSubmit: { Task { await model.submitClosePosition() } }) {
            Section {
                TextField("卖出价", text: $model.closeSellPrice)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
                TextField("实付卖出费用(可选,回填用真数)", text: $model.closeSellFees)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
                // 快捷标签(九码,可选;不选 → 服务端 NULL + 价格兜底判止损)。
                // ⚠ `TARGET_ZONE_REACHED` 文案是「达到参考区间」,⛔ 不是「止盈」。
                Picker("离场原因(可选)", selection: $model.closeReasonDraft) {
                    Text("不选(按价格兜底判定)").tag(CloseReasonCode?.none)
                    ForEach(CloseReasonCode.allCases) { reason in
                        Text(reason.label).tag(CloseReasonCode?.some(reason))
                    }
                }
            } footer: {
                Text("卖出时间缺省为今日;此处只记录真实成交,系统不代下单。离场原因用于周复盘归因与「连续 3 笔止损」提醒的计数,不选时系统按 -5% 价格近似兜底判止损。")
            }
        }
    }
}
