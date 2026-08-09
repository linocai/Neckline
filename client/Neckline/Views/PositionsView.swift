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

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView { content.padding(NKSpace.pagePad) }
                .background(NK.pageBgIOS)
                .navigationTitle("持仓")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { Task { await model.refresh() } } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                }
                .refreshable { await model.refresh() }
        }
        .sheet(item: sheetBinding) { item in sheetContent(item.kind) }
        .sheet(isPresented: $model.showAlertComposer) { AlertComposerSheet(model: model) }
        #else
        ScrollView { content.padding(NKSpace.pagePad).frame(maxWidth: 900) }
            .frame(maxWidth: .infinity)
            .background(NK.pageBg)
            .toolbar {
                ToolbarItem { Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") } }
            }
            .sheet(item: sheetBinding) { item in sheetContent(item.kind).frame(width: 440) }
            .sheet(isPresented: $model.showAlertComposer) {
                AlertComposerSheet(model: model).frame(width: 480, height: 640)
            }
        #endif
    }

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

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            #if os(macOS)
            HStack(spacing: 8) {
                NKLogo(size: 24)
                Text("持仓").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            }
            #endif
            // 🔴 **熔断横幅已整体删除**(V2.2-⑤-B,用户裁定 #8):「锁定态 / 次日只减
            // 不加 / 强制复盘解锁」三件机制在产品面消失。用户原话:「我不需要你替我
            // 做决定;这个程序永远是提醒」。连续 3 笔止损仍会推一条**纯告知**推送 +
            // 一条看板事件(见下面 `BoardSection`),**零状态、零锁、零行为改变**。
            // ⛔ 不许以任何形式在这里加回一个「建议今天别开仓」的自动状态位(〇b-7)。
            if let warning = model.retreatWarning { RetreatBrakeBanner(reason: warning) }
            // V2.2-② 行情状态:**纯展示、⛔ 无动作**(灰色地带提示,K8 §十三)。
            MarketRegimeStrip(regime: model.marketRegime, compact: true)
            mergedExposureSection
            positionsSection
            alertsSection
            BoardSection(model: model)
        }
    }

    // MARK: - 同题材合并敞口(蓝图 6.2)

    @ViewBuilder
    private var mergedExposureSection: some View {
        let merged = model.mergedExposures
        if !merged.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "同题材合并敞口")
                Text("同一来源篮子的多笔仓**不是**完全分散的两笔仓位 —— 它们一起涨、一起跌。")
                    .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                ForEach(merged) { m in
                    NKCard {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                Text(m.basketName).font(.system(size: 13, weight: .semibold))
                                    .foregroundStyle(NK.textPrimary)
                                NKChip(text: "\(m.codes.count) 只", tone: .warn)
                                Spacer()
                                Text("成本 ¥\(NKFmt.price(m.costAmount))")
                                    .font(.system(size: 11.5).monospacedDigit())
                                    .foregroundStyle(NK.textSecondary)
                            }
                            Text(m.codes.joined(separator: "、"))
                                .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - 持仓列表

    private var positionsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            HStack {
                NKSectionHeader(title: "持仓 \(model.positions.count)")
                Spacer()
                // 🔴 **「熔断中灰化开仓」已删**(V2.2-⑤-B / 〇b-7):补录本就是记账动作,
                // 而这个按钮此前是全系统唯一一处「程序替用户做决定」的自律灰化。
                // ⛔ 不许以任何条件把它再灰掉。
                Button { model.beginPositionEntryFlow() } label: {
                    Label("补录开仓", systemImage: "plus.circle.fill")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(NK.accent)
            }
            Text("先管住手里的").font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            if model.positions.isEmpty {
                NKCard { NKEmptyState(title: "暂无持仓", systemImage: "tray") }
            } else {
                ForEach(model.positions) { p in
                    PositionCard(model: model, position: p)
                }
            }
        }
    }

    // MARK: - 临时提醒(⑪-C,**只通知,永不交易**)

    private var alertsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            HStack {
                NKSectionHeader(title: "临时提醒 \(model.alerts.count)")
                Spacer()
                Button { model.beginAlertComposer() } label: {
                    Label("新建提醒", systemImage: "bell.badge")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
            }
            Text("用一句话描述条件,系统只在命中时通知你 —— **永不自动交易**")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
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
}

// MARK: - 持仓卡

private struct PositionCard: View {
    @Bindable var model: AppModel
    let position: Position
    /// 本地会话态:后端无持久化字段(`stopOrderChecked` 恒 false),此勾选仅本机本次会话记忆。
    @State private var checkedLocally = false

    /// 服务端 K4 命中里「该置顶醒目」的子集(level=strong ∧ evidenceStrength=price_volume;
    /// 弱证据即便标了 strong 也只降级展示,守 §2.4 铁律「证伪只用价量结构」)。
    private var topBillboardK4: [K4Advisory] { position.k4Advisory.filter { $0.isTopBillboard } }
    private var listK4: [K4Advisory] { position.k4Advisory.filter { !$0.isTopBillboard } }

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                if position.todayActionTone == .bad {
                    TodayActionBanner(text: position.todayAction)
                }
                ForEach(topBillboardK4) { hit in K4AdvisoryBanner(hit: hit) }
                headerRow
                metricsRow
                if position.todayActionTone != .bad && !position.todayAction.isEmpty {
                    Text(position.todayAction)
                        .font(.system(size: 11.5, weight: position.todayActionTone == .warn ? .semibold : .regular))
                        .foregroundStyle(position.todayActionTone == .warn ? NK.amber
                                        : (position.todayActionTone == .good ? NK.up : NK.textTertiary))
                }
                // K4 体检因无 EOD 行整份跳过 → 显式"今日未体检",不静默留空
                // (空白 = 「体检过了没问题」,两者必须能分开)。
                if position.k4DataUnavailable == true {
                    NKChip(text: "K4 今日未体检(停牌/无数据)", tone: .neutral)
                }
                if !listK4.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(listK4) { hit in
                                NKChip(text: hit.evidenceStrength == "constituent" ? "\(hit.label) · 参考" : hit.label,
                                       tone: hit.isStrong ? .warn : .neutral)
                            }
                        }
                    }
                }
                feesRow
                Divider().overlay(NK.hairline)
                Button { checkedLocally.toggle() } label: {
                    HStack(spacing: 6) {
                        Image(systemName: checkedLocally ? "checkmark.square.fill" : "square")
                            .foregroundStyle(checkedLocally ? NK.up : NK.textTertiary)
                        Text("已在券商挂 -5% 条件单")
                            .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        Spacer()
                        Text("仅本机本次会话记忆").font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                    }
                }
                .buttonStyle(.plain)
                Divider().overlay(NK.hairline)
                // ⑩-B 计划继承卡 + ⑪-D-D per-position 触达提醒开关。
                PositionPlanSection(model: model, position: position)
                Divider().overlay(NK.hairline)
                // V2.2-④-B 交易时钟(只读跟踪 + 「补一条主观说明」写入口)。
                TradeClockSection(model: model, position: position)
            }
        }
    }

    private var headerRow: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(position.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                    Text(position.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    // 两档 D 徽标(服务端按 D5 净浮盈判好下发,客户端不重算)。
                    // ⚠ V2.2-⑤ 起章程可能**没有时间退出条款** → 徽标只报 D 计数
                    // (`dBadgeText` 单点判断),⛔ 不拿 D5 顶上冒充有上限。
                    NKChip(text: position.dBadgeText,
                           tone: dBadgeTone, filled: dBadgeTone != .neutral)
                    if position.timeExitKind == .suspendedHold {
                        NKChip(text: "判向挂起 · 复牌当日重判", tone: .warn)
                    }
                }
                Text("买入 ¥\(NKFmt.price(position.buyPrice)) × \(position.qty) · \(model.calendar.displayString(position.buyDate))")
                    .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                if !position.entryReason.isEmpty {
                    Text(position.entryReason).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                }
                // 停牌/无数据显式标注 —— **绝不静默把老价当今日价**。
                if let stale = position.priceStale {
                    Text("停牌/无数据 \(stale.staleDays) 个交易日,价格为 \(model.calendar.displayString(stale.lastCloseDate)) 最后成交价(\(stale.reasonLabel))")
                        .font(.system(size: 11)).foregroundStyle(NK.amber)
                }
                // 定格日 ≠ D5 显式标注,只提示、不改判定逻辑,**只在晚于 D{maxHoldDays} 时展示**。
                if position.timeExitLockedLateDays > 0, let lockedDay = position.timeExitLockedDay,
                   let cap = position.maxHoldDays {
                    Text("定格于 D\(lockedDay),晚于 D\(cap) \(position.timeExitLockedLateDays) 天")
                        .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
                // 🔴 V2.2-⑤:本版章程无时间退出条款时,**把这件事说出口**——
                // 否则用户看到一个光秃秃的「D3」会以为系统忘了算上限。
                if let disclosure = position.timeExitDisclosure {
                    Text(disclosure).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(position.hasLivePrice ? "¥\(NKFmt.price(position.price))" : "—")
                    .font(NKFont.price).foregroundStyle(NK.textPrimary)
                if position.hasLivePrice {
                    Text(NKFmt.signedPct(position.pnlPct))
                        .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                        .foregroundStyle(position.pnlPct >= 0 ? NK.up : NK.down)
                }
            }
        }
    }

    /// 纪律位 chips 与动作按钮**分两行**:iPhone 宽度下挤在一行会把每个 chip 压成
    /// 竖排断字(实机截图核对时踩到),chips 走横向滚动、按钮独占一行。
    private var metricsRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    NKChip(text: "止损线 ¥\(NKFmt.price(position.stopLine))",
                           tone: position.hasBrokenStop ? .bad : .neutral)
                    if position.hasBrokenStop {
                        NKChip(text: "已破止损线", tone: .bad, filled: true)
                    }
                    if let dist = position.distToStopPctServer {
                        NKChip(text: "距止损线 \(NKFmt.signedPct(dist * 100))",
                               tone: dist <= 0 ? .bad : (dist <= 0.02 ? .warn : .neutral))
                    }
                    if let rs = position.retraceState {
                        NKChip(text: rs.triggered ? "回落止盈已触发"
                                    : "峰值 ¥\(NKFmt.price(rs.peak)) · 回落 \(NKFmt.pct(rs.retracePct * 100))",
                               tone: rs.triggered ? .bad : .neutral)
                    }
                }
            }
            HStack(spacing: 16) {
                Spacer()
                Button { model.beginNote(code: position.code, positionId: position.id) } label: {
                    Text("补充说明").font(.system(size: 12, weight: .semibold))
                }
                .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
                Button { model.openCloseSheet(code: position.code) } label: {
                    Text("补录清仓").font(.system(size: 12, weight: .semibold))
                }
                .buttonStyle(.plain).foregroundStyle(NK.down)
            }
        }
    }

    @ViewBuilder
    private var feesRow: some View {
        if position.buyFees != nil || position.sellFees != nil {
            HStack(spacing: 10) {
                if let bf = position.buyFees {
                    Text("买入费 ¥\(NKFmt.price(bf))").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
                if let sf = position.sellFees {
                    Text("卖出费 ¥\(NKFmt.price(sf))").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
                Text("实付,供周复盘对账用真数").font(.system(size: 9.5)).italic().foregroundStyle(NK.textTertiary)
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

/// D5/时间退出等最高优先级今日动作横幅。文案恒来自服务端 `todayAction`。
private struct TodayActionBanner: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").font(.system(size: 16, weight: .bold))
            Text(text).font(.system(size: 13, weight: .bold)).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

/// K4 持仓牌强警示置顶横幅(level=strong ∧ evidenceStrength=price_volume 才走到这里)。
private struct K4AdvisoryBanner: View {
    let hit: K4Advisory
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "flag.fill").font(.system(size: 15, weight: .bold))
            VStack(alignment: .leading, spacing: 3) {
                Text(hit.label).font(.system(size: 13, weight: .bold))
                if !hit.evidence.isEmpty {
                    Text(hit.evidence).font(.system(size: 11.5)).opacity(0.9)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
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
                    Text(alert.subjectLabel).font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(NK.textPrimary)
                    NKChip(text: alert.statusLabel, tone: alert.statusTone)
                    Spacer()
                    if alert.maxFires > 0 {
                        Text("已触发 \(alert.firedCount)/\(alert.maxFires)")
                            .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(NK.textTertiary)
                    }
                }
                if !alert.condition.isEmpty {
                    Text(alert.condition).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !alert.nlText.isEmpty {
                    Text("你的原话:\(alert.nlText)").font(.system(size: 10.5))
                        .foregroundStyle(NK.textTertiary)
                }
                HStack(spacing: 12) {
                    if alert.firedCount > 0 {
                        Button { Task { await model.updateAlert(id: alert.id, resetFired: true) } } label: {
                            Text("重置触发计数").font(.system(size: 11.5))
                        }
                        .buttonStyle(.plain).foregroundStyle(NK.accent)
                    }
                    Spacer()
                    Button(role: .destructive) { Task { await model.deleteAlert(id: alert.id) } } label: {
                        Text("停用").font(.system(size: 11.5))
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
