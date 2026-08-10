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

/// macOS 列表栏点开的**详情靶**(原型 875–893 行把「盘中动态」「临时提醒」画成与仓位
/// 平级的两行,选中后详情栏整屏换成它们)。
///
/// ⚠ **刻意住在这一屏的 `@State` 里、不进 `AppModel`**(同 `BasketDailyView.receiptSelected`
/// 先例):它是列表栏的本地选中态,不是跨板块的应用状态。
enum PositionsPane: Equatable {
    case position
    case board
    case alerts
}

struct PositionsView: View {
    @Bindable var model: AppModel

    /// ⚠ 初值走 QA 钩子(缺环境变量恒 `.position`,正常路径逐字节不变)—— 见 `NKQA`。
    @State private var pane: PositionsPane = {
        switch NKQA.initialPositionsPane {
        case "board": return .board
        case "alerts": return .alerts
        default: return .position
        }
    }()

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
            detailPane
        }
    }

    @ViewBuilder
    private var detailPane: some View {
        switch pane {
        case .board:
            BoardSection(model: model)
        case .alerts:
            alertsSection
        case .position:
            if let p = selected {
                PositionDetailPage(model: model, position: p)
            } else {
                NKDetailPlaceholder(
                    title: model.positions.isEmpty ? "暂无持仓" : "选一笔仓看详情",
                    subtitle: model.positions.isEmpty
                        ? "补录你已在券商完成的买入之后,这里会出现纪律位置、计划继承与交易时钟。"
                        : "左边点一笔,这里显示止损刻度尺、纪律位置、持仓计划与交易时钟。",
                    systemImage: "chart.line.uptrend.xyaxis")
            }
        }
    }

    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            // 标题区 = 原型 857 行 `padding:18px 16px 10px`(纵向由 `NKSplitLayout` 的
            // `listPadTop` 给 18,横向在栏内边距 10 之上再补 6 = 16,底 10)。
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    Text(AppTab.positions.title).font(NKFont.title2).tracking(-0.3)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 6)
                    addPositionButton
                }
                summaryStrip
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH).padding(.bottom, 10)

            boardRow
            alertsRow
            // ⚠ 行情状态条**原型的持仓栏里没有**(856–983 行全文),它是 V2.3.0 起的既有
            // 诚实披露资产 —— 本批只把 compact 分支收成与合并敞口块同一种"窄块"形状,
            // ⛔ 不删内容(缺维 / 未取得那两句是「没看≠没有」的落点)。
            MarketRegimeStrip(regime: model.marketRegime, compact: true)
                .padding(.horizontal, 2).padding(.top, 8)
            mergedExposureSection

            positionRowsSection
        }
    }

    /// 「盘中动态」入口行(原型 875–884)。两行:图标 + 标题 + 右端状态徽标;
    /// 次行是本次哨兵的实际读数,缩进到标题下沿。
    private var boardRow: some View {
        NKListRow(selected: pane == .board) {
            pane = .board
        } content: {
            VStack(alignment: .leading, spacing: 3) {   // 原型 883 行 margin-top:3
                HStack(spacing: 8) {
                    Image(systemName: "waveform.path.ecg").font(.system(size: 13))
                        .foregroundStyle(NK.textSecondary).frame(width: 13)
                    Text("盘中动态").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 6)
                    if model.board.retreatBrake.active {
                        NKChip(text: "退潮刹车", tone: .bad, filled: true)
                    } else {
                        NKChip(text: "运行正常", tone: .good)
                    }
                }
                Text(boardRowCaption).font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 21)          // 原型 883 行 padding-left:21
            }
        }
    }

    /// 次行摘要。🔴 **只由已经拉回来的看板字段拼**,⛔ 一个数都不新造。
    private var boardRowCaption: String {
        var parts = ["\(model.board.events.count) 条哨兵事件"]
        parts.append(model.board.retreatBrake.active ? "退潮刹车已触发" : "无退潮刹车")
        if !model.board.asof.isEmpty { parts.append("更新于 \(model.board.asof)") }
        return parts.joined(separator: " · ")
    }

    /// 「临时提醒」入口行(原型 886–893)。右端是**计数**,不是徽标。
    private var alertsRow: some View {
        NKListRow(selected: pane == .alerts) {
            pane = .alerts
        } content: {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Image(systemName: "bell").font(.system(size: 13))
                        .foregroundStyle(NK.textSecondary).frame(width: 13)
                    Text("临时提醒").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 6)
                    Text("\(model.alerts.count)")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                }
                Text("只在命中时通知你 · 永不自动交易").font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                    .padding(.leading, 21)
            }
        }
        .task { await model.loadAlerts() }
    }

    /// 仓位行(原型 910–982:`padding:0 10px 20px; gap:2`,**没有分组头** ——
    /// 「持仓」这个标题在栏顶已经说过一遍了)。
    @ViewBuilder
    private var positionRowsSection: some View {
        if model.positions.isEmpty {
            NKCard { NKEmptyState(title: "暂无持仓", systemImage: "tray") }
                .padding(.top, 8)
        } else {
            VStack(alignment: .leading, spacing: NKSpace.rowGap) {
                ForEach(model.positions) { p in
                    PositionListRow(model: model, position: p,
                                    selected: pane == .position && model.selectedPositionId == p.id) {
                        pane = .position
                    }
                }
            }
            .padding(.top, 8)
        }
    }
    #endif

    // MARK: - 概要条(**金额,⛔ 无占比** —— 分母不在契约里,见文件头)

    /// 原型 866–872 行:一条 `.5px` 细线 + `margin-top:7; padding-top:8`,
    /// 每格是**数在上、名在下**(`15/600` + `10 .40`),格间一根 `.5px × 24` 竖线,
    /// 右端另起一组「N 件待处理 / 是哪几件」。
    ///
    /// 🔴 原型第一格是「61.2% 占总仓」—— **本版一律换成金额**(§五 〇-4:总仓分母
    /// `Settings.total_capital` 从未下发,客户端写死 12 万 = 造第二份事实源)。
    private var summaryStrip: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            summaryItem("合计成本", "¥" + NKFmt.amount(totalCost), NK.textPrimary)
            Rectangle().fill(NK.hairline).frame(width: 0.5, height: 24)
                .alignmentGuide(.firstTextBaseline) { $0[.top] + 12 }
            summaryItem("合计浮盈", NKFmt.signedAmount(totalPnl),
                        totalPnl >= 0 ? NK.up : NK.down)
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 3) {
                Text("\(pendingCount) 件待处理")
                    .font(NKFont.caption).fontWeight(.semibold)
                    .foregroundStyle(pendingCount > 0 ? NK.down : NK.textSecondary)
                Text(pendingDetail).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
        .padding(.top, 8)
        .overlay(alignment: .top) { Rectangle().fill(NK.hairline).frame(height: 0.5) }
        .padding(.top, 7)
    }

    private func summaryItem(_ title: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {   // 原型 867 行 margin-top:3
            Text(value).font(NKFont.headline.monospacedDigit()).foregroundStyle(color)
            Text(title).font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
    }

    /// 「是哪几件」——⛔ 只数**已经在列表里的那两种醒目态**,不新造任何判定。
    private var pendingDetail: String {
        let exit = model.positions.filter(\.isExitDay).count
        let broken = model.positions.filter(\.hasBrokenStop).count
        var parts: [String] = []
        if exit > 0 { parts.append("今日离场 \(exit)") }
        if broken > 0 { parts.append("已破止损 \(broken)") }
        return parts.isEmpty ? "无待处理" : parts.joined(separator: " · ")
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
        // 原型 861–864 行:`padding:4px 9px; radius:7; background rgba(11,107,203,.10)`,
        // 里面是 10px 的「+」加 `11.5/600` 蓝字 —— 是一枚**淡蓝底的小胶囊按钮**,
        // ⛔ 不是一行裸文字链接。
        Button { model.beginPositionEntryFlow() } label: {
            HStack(spacing: 4) {
                Image(systemName: "plus").font(.system(size: 10, weight: .semibold))
                Text("补录开仓").font(NKFont.caption).fontWeight(.semibold)
            }
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: NKRadius.control)
                .fill(NK.accent.opacity(0.10)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).foregroundStyle(NK.accent)
    }

    // MARK: - 同题材合并敞口(蓝图 6.2)

    /// 原型 897–908 行:一整块**琥珀淡底**(`rgba(232,145,10,.07)` + `.5px` 同色描边,
    /// `radius 9 / padding 10px 12px`),⛔ 不是白卡 —— 它是提示,不是数据卡。
    /// 顶行 `11/700` 琥珀标题 + 右端合计金额;次行一句白话;展开后列代码。
    ///
    /// 🔴 原型展开行末尾是「占总仓 40.5%(总仓分母 ¥120,000)」—— **本版不画占比**
    /// (§五 〇-4),只列代码 + 合计成本额。
    @ViewBuilder
    private var mergedExposureSection: some View {
        let merged = model.mergedExposures
        if !merged.isEmpty {
            let total = merged.reduce(0.0) { $0 + $1.costAmount }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text("同题材合并敞口 \(merged.count) 组")
                        .font(NKFont.caption).fontWeight(.bold).foregroundStyle(NK.amber)
                    Spacer(minLength: 6)
                    Text("¥\(NKFmt.amount(total))")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
                Text("它们一起涨、一起跌,**不是**完全分散的两笔仓位。")
                    .font(NKFont.caption).lineSpacing(3).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(merged) { m in
                    Text("\(m.basketName) \(m.codes.count) 只 · \(m.codes.joined(separator: "、"))")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner)
                .fill(NK.amber.opacity(0.07)))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                .stroke(NK.amber.opacity(0.22), lineWidth: 0.5))
            .padding(.horizontal, 2).padding(.top, 16).padding(.bottom, 8)
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
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            #if os(macOS)
            // 原型 1231–1237 行:大标题 + 一句副标题,右端**实心蓝**主按钮。
            HStack(alignment: .bottom, spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("临时提醒").font(NKFont.title1).tracking(-0.4)
                        .foregroundStyle(NK.textPrimary)
                    Text("用一句话描述条件,系统只在命中时通知你 —— 永不自动交易")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Button { model.beginAlertComposer() } label: {
                    Text("新建提醒").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.accent))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            #else
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
            #endif
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
    /// 选中回调(macOS:同时把详情靶切回「仓位」)。
    var onSelect: (() -> Void)? = nil

    var body: some View {
        Group {
            if interactive {
                NKListRow(selected: selected) {
                    model.selectedPositionId = position.id
                    onSelect?()
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

    /// 原型 911–981 行:上半 = 名称 `13/600` + D 徽标 / 代码·买价×量 `11 .55`,
    /// 右端现价 `16/600` + 涨跌 `11.5/600`;下半 = **迷你刻度条** + 一行读数。
    /// 停牌那一笔(原型 962–980)**不画条**,改一句琥珀说明。
    private var rowBody: some View {
        VStack(alignment: .leading, spacing: 10) {   // 原型 921 行 margin-top:10
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(position.name).font(NKFont.body).fontWeight(.semibold)
                            .foregroundStyle(NK.textPrimary).lineLimit(1)
                        // 两档 D 徽标(服务端按 D5 净浮盈判好下发,客户端不重算)。
                        // ⚠ V2.2-⑤ 起章程可能**没有时间退出条款** → 徽标只报 D 计数
                        // (`dBadgeText` 单点判断),⛔ 不拿 D5 顶上冒充有上限。
                        NKChip(text: position.dBadgeText, tone: dBadgeTone,
                               filled: dBadgeTone == .bad)
                        if position.timeExitKind == .suspendedHold {
                            NKChip(text: "判向挂起", tone: .warn)
                        }
                    }
                    Text("\(position.code) · ¥\(NKFmt.price(position.buyPrice)) × \(position.qty)")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 3) {
                    Text(position.hasLivePrice ? "¥\(NKFmt.price(position.price))" : "—")
                        .font(NKFont.headline.monospacedDigit())
                        .foregroundStyle(position.hasLivePrice ? NK.textPrimary : NK.textTertiary)
                    if position.hasLivePrice {
                        Text(NKFmt.signedPct(position.pnlPct))
                            .font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
                            .foregroundStyle(position.pnlPct >= 0 ? NK.up : NK.down)
                    } else if let stale = position.priceStale {
                        Text("\(stale.reasonLabel) \(stale.staleDays) 日")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                    }
                }
            }
            // 停牌 / 无数据显式标注 —— **绝不静默把老价当今日价**。
            if let stale = position.priceStale {
                Text("价格为 \(model.calendar.displayString(stale.lastCloseDate)) 最后成交价(\(stale.reasonLabel))· 复牌当日重判")
                    .font(NKFont.caption).lineSpacing(3).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            } else if position.hasLivePrice {
                VStack(alignment: .leading, spacing: 4) {   // 原型 933 行 margin-top:4
                    NKStopMiniBar(stop: position.stopLine, cost: position.buyPrice,
                                  price: position.price)
                    HStack(spacing: 6) {
                        if position.hasBrokenStop {
                            Text("已破止损线 ¥\(NKFmt.price(position.stopLine))")
                                .font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
                                .foregroundStyle(NK.down)
                        } else {
                            Text("止损 \(NKFmt.price(position.stopLine))")
                                .font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                        Spacer(minLength: 4)
                        if let dist = position.distToStopPctServer {
                            Text(position.hasBrokenStop
                                 ? "距止损 \(NKFmt.signedPct(dist * 100))"
                                 : "现价 \(NKFmt.price(position.price)) · 距止损 \(NKFmt.signedPct(dist * 100))")
                                .font(NKFont.caption.monospacedDigit())
                                .fontWeight(position.hasBrokenStop ? .semibold : .regular)
                                .foregroundStyle(dist <= 0 ? NK.down
                                                 : (dist <= 0.02 ? NK.amber : NK.textTertiary))
                        }
                    }
                }
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
            if let stale = position.priceStale {
                // 原型 1183–1196 行:拉不到今日行情时,主位是一张**居中**的「—」卡
                // (说清最后成交日 + K4 有没有体检),后面跟一块常开的灰底说明。
                // ⛔ 这一屏**不画刻度尺** —— 用陈旧价画一条"离止损还有多远"就是假图。
                noQuoteCard(stale)
                NKNoteBlock(text: "时间退出判向挂起 = 停牌期间不推进 D 计数、不触发离场判定;复牌当日按当日收盘重判。⛔ 名单拉不到时如实标 unknown,不猜成停牌。")
            } else {
                stopScaleCard          // 🔴 纪律位置(刻度尺是主角)
            }
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

    /// 原型 987–999 行:**徽标一排在最上**(`gap:7; margin-bottom:6`)→ 名称 `26/700
    /// tracking -.4` → `代码 · 买入 ¥x × n · 日期` `12 .55`;右端现价 `34/600` +
    /// `涨跌% · 金额` `14/600`。⛔ V2.3.0 那种「名称与徽标挤一行」不是原型的样子。
    private var headerBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 3) {
                    #if os(macOS)
                    HStack(spacing: 7) {
                        if position.timeExitKind == .suspendedHold {
                            NKChip(text: "判向挂起 · 复牌当日重判", tone: .warn)
                        } else {
                            NKChip(text: position.dBadgeText, tone: dBadgeTone,
                                   filled: dBadgeTone == .bad)
                        }
                        // 来源篮子(原型 991 行「来自「固态电池 · 中试线落地」」)。
                        // ⚠ 只在**计划已经拉回来且有来源篮子**时画:⛔ 不拿 basketId 拼一个
                        // 「篮子 #412」冒充名字。
                        if let name = model.latestPlan(positionId: position.id)?.sourceBasketName,
                           !name.isEmpty {
                            NKChip(text: "来自「\(name)」")
                        }
                    }
                    .padding(.bottom, 3)
                    Text(position.name).font(NKFont.title1).tracking(-0.4)
                        .foregroundStyle(NK.textPrimary)
                    #endif
                    Text("\(position.code) · 买入 ¥\(NKFmt.price(position.buyPrice)) × \(position.qty) · \(model.calendar.displayString(position.buyDate))")
                        .font(NKFont.callout.monospacedDigit()).foregroundStyle(NK.textSecondary)
                    if !position.entryReason.isEmpty {
                        Text(position.entryReason).font(NKFont.callout)
                            .foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 6)
                // 🔴 拉不到行情时**右端整块不画**:那个大「—」在原型里住在下面那张居中卡上
                //(1183–1188 行),挂在标题右边会读成"现价是 0"。
                if position.hasLivePrice {
                    VStack(alignment: .trailing, spacing: 5) {
                        Text("¥\(NKFmt.price(position.price))")
                            .font(NKFont.heroNumber).tracking(-1).foregroundStyle(NK.textPrimary)
                        Text("\(NKFmt.signedPct(position.pnlPct)) · \(NKFmt.signedAmount(position.pnlAmount))")
                            .font(NKFont.headline.monospacedDigit())
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
            // ⚠ 停牌 / 无数据的那句话**已挪到下面那张居中卡**(原型 1183–1188 行),
            // ⛔ 别在这里再写一遍 —— 同一件事一屏说两遍,醒目的那条反而会被跳过。
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

    // MARK: - 拉不到今日行情(原型 1183–1188:居中「—」卡)

    /// 🔴 **「—」不是 0 元**:`price == 0` = 本次没有当日 EOD 行 / 拉不到实时价。
    /// 卡里把三件说清:陈旧几个交易日、最后成交日是哪天、K4 有没有体检 ——
    /// ⛔ 空白会被读成「体检过了没问题」。
    private func noQuoteCard(_ stale: PriceStale) -> some View {
        NKCard {
            VStack(spacing: 8) {
                Text("—").font(NKFont.heroNumber).foregroundStyle(NK.textTertiary.opacity(0.75))
                Text("\(stale.reasonLabel) \(stale.staleDays) 个交易日")
                    .font(NKFont.body).fontWeight(.semibold).foregroundStyle(NK.amber)
                Text(noQuoteDetail(stale))
                    .font(NKFont.callout).lineSpacing(4).foregroundStyle(NK.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
        }
    }

    private func noQuoteDetail(_ stale: PriceStale) -> String {
        let day = stale.lastCloseDate.isEmpty
            ? "回看窗口内找不到最后成交日(如实留空,不臆造)"
            : "价格为 \(model.calendar.displayString(stale.lastCloseDate)) 最后成交价"
        // ⚠ 三值:true=没体检 / false=体检过了 / nil=老快照未记录。⛔ nil 不冒充 false。
        switch position.k4DataUnavailable {
        case .some(true):
            return day + "。\nK4 今日未体检 —— 无 EOD 行,整份跳过,不是「体检过了没问题」。"
        case .some(false):
            return day + "。\nK4 今日照常体检过了。"
        case .none:
            return day + "。\nK4 体检有没有跑,这份快照里没记(不知道,不冒充「跑过了」)。"
        }
    }

    // MARK: - 🔴 纪律位置(止损刻度尺)

    private var stopScaleCard: some View {
        NKStopScaleCard(stop: position.stopLine, cost: position.buyPrice,
                        price: position.hasLivePrice ? position.price : 0,
                        peak: position.retraceState?.peak) {
            VStack(alignment: .leading, spacing: 6) {
                // 原型 1023 行:`margin-top:18; padding-top:14` + 一条 `.5px` 上边线,
                // 每格 `flex:1`(名 `11 .55` 在上、数 `16/600` 在下)。
                HStack(alignment: .top, spacing: 10) {
                    if let dist = position.distToStopPctServer {
                        statItem("距止损线", NKFmt.signedPct(dist * 100),
                                 dist <= 0 ? NK.down : (dist <= 0.02 ? NK.amber : NK.up))
                    }
                    if let rs = position.retraceState {
                        statItem("自峰值回落", NKFmt.pct(rs.retracePct * 100),
                                 rs.triggered ? NK.down : NK.textPrimary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.top, 14)
                .overlay(alignment: .top) {
                    Rectangle().fill(NK.hairline.opacity(0.8)).frame(height: 0.5)
                }
                .padding(.top, 18)
                if let rs = position.retraceState, rs.triggered {
                    Text("回落止盈已触发 —— 系统只提醒,不代下单。")
                        .font(NKFont.caption).foregroundStyle(NK.down)
                }
                // ⚠ 刻度尺只是版式:位置按价格线性映射,⛔ 不代表任何概率或建议。
                // 🔴 原型第四格「占总仓 35.3%」**本版不画**(§五 〇-4),这里把为什么
                // 说出口 —— ⛔ 少一格不能悄悄地少。
                NKDisclosure(summary: "刻度尺是版式,不是判断") {
                    // 🔴 `Text(String)` 不解析 Markdown(只有字面量解析)——`+` 拼接会把
                    // `**` 原样印上屏(§〇d 结转第 7 条)。整句必须是**一条字面量**。
                    Text("四个刻度按价格**线性映射**到轨道上 —— 它只回答「离止损线还有多远」,⛔ 不代表任何概率、不构成任何建议。")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("止损线由服务端按现役章程派生(客户端不重算);峰值只在有回落止盈态时才画。")
                        .fixedSize(horizontal: false, vertical: true)
                    Text("「回落止盈线」与「占总仓」两格**本次不画**:前者的比例、后者的总仓分母都**没有下发到客户端**,在这里写死一个数就是造第二份事实源。")
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// 原型 1024 行:名 `11px rgba(.55)` 在上,数 `16/600 tabular` 在下(`margin-top:2`)。
    private func statItem(_ title: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value).font(NKFont.headline.monospacedDigit()).foregroundStyle(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - 已在券商挂 -5% 条件单(**本机记录**,⛔ 不写服务端)

    private var brokerOrderCard: some View {
        // 原型 1031–1046 行:整卡可点,左边是一枚 **15×15 / radius 4** 的自绘勾选框
        // (勾上 = `#0FA968` 实底白勾;没勾 = 白底 + `inset 1.2px rgba(60,60,67,.28)`),
        // 右边 `13px` 文案 + `10.5 .40` 的勾选时刻。⛔ 不是 SF Symbols 那个 `checkmark.square`。
        NKCard {
            Button {
                stopOrderChecked.toggle()
                NKStopOrderLedger.setChecked(stopOrderChecked, positionId: position.id)
                stopOrderLabel = NKStopOrderLedger.checkedLabel(positionId: position.id)
            } label: {
                HStack(alignment: .center, spacing: 11) {
                    ZStack {
                        RoundedRectangle(cornerRadius: NKRadius.badge)
                            .fill(stopOrderChecked ? NK.up : NK.cardBg)
                        if !stopOrderChecked {
                            RoundedRectangle(cornerRadius: NKRadius.badge)
                                .strokeBorder(NK.textSecondary.opacity(0.5), lineWidth: 1.2)
                        }
                        if stopOrderChecked {
                            Image(systemName: "checkmark")
                                .font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                        }
                    }
                    .frame(width: 15, height: 15)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("已在券商挂 -5% 条件单")
                            .font(NKFont.body).foregroundStyle(NK.textPrimary)
                        if let label = stopOrderLabel {
                            Text("\(label) · 本机记录,换机不同步")
                                .font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        } else if position.hasBrokenStop {
                            // 没勾 + 已破线 = 这一格此刻最该被看见。
                            Text("还没勾 —— 这一票已破线").font(NKFont.caption)
                                .foregroundStyle(NK.amber)
                        } else {
                            Text("本机记录,换机不同步 · 勾不勾**不改变任何判定**")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    private func syncStopOrder() {
        stopOrderChecked = NKStopOrderLedger.isChecked(positionId: position.id)
        stopOrderLabel = NKStopOrderLedger.checkedLabel(positionId: position.id)
    }

    // MARK: - K4 持仓牌

    /// 原型 1164–1171 行:标题弱标 `margin-bottom:10` + 一排**自动换行**的标签
    /// (`gap:7`)。⛔ 不用横向 `ScrollView`:桌面上没有滚动提示,超宽的那几枚
    /// **看不见也不知道有**(`NKWrapRow` 的 docstring 记的就是这条)。
    @ViewBuilder
    private var k4Section: some View {
        if position.k4DataUnavailable == true || !listK4.isEmpty || showFeesInCard {
            NKCard {
                VStack(alignment: .leading, spacing: 10) {
                    if position.k4DataUnavailable == true || !listK4.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("K4 持仓牌").nkLabel().foregroundStyle(NK.textTertiary)
                            // K4 体检因无 EOD 行整份跳过 → 显式"今日未体检",不静默留空
                            // (空白 = 「体检过了没问题」,两者必须能分开)。
                            if position.k4DataUnavailable == true {
                                Text("K4 今日未体检 —— 无 EOD 行,整份跳过,**不是**「体检过了没问题」。")
                                    .font(NKFont.caption).foregroundStyle(NK.amber)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !listK4.isEmpty {
                                NKWrapRow(spacing: 7, lineSpacing: 7) {
                                    ForEach(listK4) { hit in
                                        NKChip(text: hit.evidenceStrength == "constituent"
                                               ? "\(hit.label) · 参考" : hit.label,
                                               tone: hit.isStrong ? .warn : .neutral)
                                    }
                                }
                            }
                        }
                    }
                    if showFeesInCard {
                        feesRow.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                // ⚠ 卡必须**吃满详情栏宽度**:不给这一句时 `NKCard` 会缩到内容宽,
                // 一屏卡片流里就会冒出一张比别人窄的卡(实拍逮到)。
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    /// 费用行的落点分平台:**macOS 归动作行右端**(原型 1105 行),iOS 仍留在卡里。
    private var showFeesInCard: Bool {
        #if os(macOS)
        return false
        #else
        return position.buyFees != nil || position.sellFees != nil
        #endif
    }

    /// 原型 1105 行:`买入费 ¥12.69 · 实付,供周复盘对账用真数`,一行 `10.5 .40`。
    /// ⚠ **不带 `Spacer`** —— 对齐交给调用方(macOS 贴右、iOS 贴左)。
    private var feesRow: some View {
        Text(feesText).font(NKFont.caption.monospacedDigit())
            .foregroundStyle(NK.textTertiary)
            .multilineTextAlignment(.trailing)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var feesText: String {
        var parts: [String] = []
        if let bf = position.buyFees { parts.append("买入费 ¥\(NKFmt.price(bf))") }
        if let sf = position.sellFees { parts.append("卖出费 ¥\(NKFmt.price(sf))") }
        parts.append("实付,供周复盘对账用真数")
        return parts.joined(separator: " · ")
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
    /// 原型 1101–1106 行:**左对齐**两枚按钮(补录清仓 = `#E5443B` 实底白字;补充说明 =
    /// `.5px` 描边)`padding:9px 16px; radius:8; 12.5/600`,右端把实付费用那句话贴边。
    /// ⛔ V2.3.0 是右对齐的两行裸文字 —— 在一屏卡片流的最后读起来像脚注,不像动作。
    private var macActionRow: some View {
        HStack(alignment: .center, spacing: 10) {
            Button { model.openCloseSheet(code: position.code) } label: {
                Text("补录清仓").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16).padding(.vertical, 9)
                    .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.down))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button { model.beginNote(code: position.code, positionId: position.id) } label: {
                Text("补充说明").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textSecondary)
                    .padding(.horizontal, 16).padding(.vertical, 9)
                    .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                        .stroke(NK.textTertiary.opacity(0.5), lineWidth: 0.5))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Spacer(minLength: 8)
            if position.buyFees != nil || position.sellFees != nil {
                feesRow.frame(maxWidth: 260, alignment: .trailing)
            }
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

/// D5/时间退出等最高优先级今日动作横幅(原型 1002–1008 / 1127–1133 行:
/// `padding:14px 16px; radius:12; 红橙渐变; gap:11`,`17px` 图标 + `14/700` 一句 +
/// `12` 次行)。**主句恒来自服务端 `todayAction`**;次行是那条**恒真的产品口径**
/// (系统只提醒、不代下单)—— ⛔ 不在这里编任何与这一票有关的数。
private struct TodayActionBanner: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: "exclamationmark.circle.fill")
                .font(.system(size: 17, weight: .semibold))
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(text).font(NKFont.headline).fontWeight(.bold).lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                Text("系统只提醒,不代下单、不硬拦。")
                    .font(NKFont.callout).opacity(0.9).lineSpacing(2)
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.alertGrad))
    }
}

/// K4 持仓牌强警示置顶横幅(level=strong ∧ evidenceStrength=price_volume 才走到这里)。
private struct K4AdvisoryBanner: View {
    let hit: K4Advisory
    var body: some View {
        // 与 `TodayActionBanner` **同一种形状**(原型 1002 行那一张):同为最高优先级
        // 的整卡渐变横幅,⛔ 别做成两种规格。
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: "flag.fill").font(.system(size: 17, weight: .semibold))
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(hit.label).font(NKFont.headline).fontWeight(.bold)
                if !hit.evidence.isEmpty {
                    Text(hit.evidence).font(NKFont.callout).opacity(0.9).lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.alertGrad))
    }
}
// MARK: - 临时提醒一行

private struct AlertRow: View {
    @Bindable var model: AppModel
    let alert: CustomAlert

    var body: some View {
        // 原型 1238–1246 行:`padding:15px 18px`;名 `13.5/600` + 状态徽标 + 右端计数
        // `11 .40`;条件 `12.5 .75`(`margin-top:6`);你的原话 `10.5 .40`(`margin-top:4`)。
        NKCard {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
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
                        .padding(.top, 2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !alert.nlText.isEmpty {
                    Text("你的原话:\(alert.nlText)").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
                // ⚠ 原型这张卡没有这一行(1238–1246)—— 但「重置计数 / 停用」是这一屏
                // **唯一**的写入口,删了就没法停用一条提醒。保留,压成一行弱按钮贴在卡底。
                HStack(spacing: 12) {
                    if alert.firedCount > 0 {
                        Button { Task { await model.updateAlert(id: alert.id, resetFired: true) } } label: {
                            Text("重置触发计数").font(NKFont.caption)
                        }
                        .buttonStyle(.plain).foregroundStyle(NK.accent)
                    }
                    Spacer(minLength: 8)
                    Button(role: .destructive) { Task { await model.deleteAlert(id: alert.id) } } label: {
                        Text("停用").font(NKFont.caption)
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.down)
                }
                .padding(.top, 2)
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
