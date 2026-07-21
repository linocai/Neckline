//
//  TodayPlanView.swift
//  Neckline — 今日计划(§五 阶段4C.1):昨晚候选 20 + 四件套 + 情绪仪表盘 + 仓位额度 +
//  强势板块 + 持仓列表(派生止损线 + 条件单自证提醒)。数据来自 `GET /report/latest`
//  + `GET /positions`(+ `GET /board` 供退潮警示,见 AppModel.refresh 注释)。
//

import SwiftUI

struct TodayPlanView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle("今日计划")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await model.refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .refreshable { await model.refresh() }
        }
        .sheet(item: Binding(get: { model.modal.map(SheetItem.init) },
                             set: { if $0 == nil { model.dismissModal() } })) { item in
            sheetContent(item.kind)
        }
        #else
        ScrollView {
            content.padding(NKSpace.pagePad).frame(maxWidth: 860)
        }
        .frame(maxWidth: .infinity)
        .background(NK.pageBg)
        .toolbar {
            ToolbarItem { Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") } }
        }
        .sheet(item: Binding(get: { model.modal.map(SheetItem.init) },
                             set: { if $0 == nil { model.dismissModal() } })) { item in
            sheetContent(item.kind).frame(width: 420)
        }
        #endif
    }

    /// `.sheet(item:)` 需要 Identifiable;`PositionModal` 本身只 Equatable,包一层。
    private struct SheetItem: Identifiable, Equatable {
        let kind: PositionModal
        var id: String {
            switch kind {
            case .open: return "open"
            case .close(let code): return "close-\(code)"
            }
        }
    }

    @ViewBuilder
    private func sheetContent(_ kind: PositionModal) -> some View {
        switch kind {
        case .open: OpenPositionSheet(model: model)
        case .close(let code): ClosePositionSheet(model: model, code: code)
        }
    }

    // MARK: - 共用内容

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            header
            if let warning = model.retreatWarning {
                RetreatBrakeBanner(reason: warning)
            }
            if model.report.degraded {
                NKCard {
                    NKEmptyState(title: emptyTitle(model.report.reason),
                                subtitle: "策略引擎已在跑,今晚 16:35 出计划后自动显示。",
                                systemImage: "moon.zzz")
                }
            } else {
                if let s = model.report.sentiment {
                    SentimentCard(sentiment: s)
                }
                if !model.report.sectors.isEmpty {
                    SectorChipsRow(sectors: model.report.sectors)
                }
                candidatesSection
            }
            positionsSection
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
            // iOS 由 navigationTitle 渲染大标题,此处再画会双标题(实机反馈修复);macOS 无大标题才自画
            #if os(macOS)
            HStack(spacing: 8) {
                NKLogo(size: 24)
                Text("今日计划").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            }
            #endif
            if !model.report.tradeDate.isEmpty {
                Text("交易日 \(model.calendar.displayString(model.report.tradeDate)) · 策略版本 \(model.report.strategyVersion)")
                    .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
        }
    }

    private var candidatesSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "候选 \(model.report.candidates.count)", trailing: "前10只过 LLM 审判")
            ForEach(model.report.candidates) { c in
                CandidateRow(candidate: c)
            }
        }
    }

    private var positionsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            HStack {
                NKSectionHeader(title: "持仓 \(model.positions.count)")
                Spacer()
                Button { model.openEntrySheet() } label: {
                    Label("补录开仓", systemImage: "plus.circle.fill")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(NK.accent)
            }
            if model.positions.isEmpty {
                NKCard { NKEmptyState(title: "暂无持仓", systemImage: "tray") }
            } else {
                ForEach(model.positions) { p in
                    PositionCard(model: model, position: p)
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

// MARK: - 候选行(四件套展开)

private struct CandidateRow: View {
    let candidate: Candidate
    @State private var expanded = false

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    Text("#\(candidate.rank)")
                        .font(.system(size: 12, weight: .bold)).foregroundStyle(NK.textTertiary)
                        .frame(width: 24, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(candidate.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                            Text(candidate.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        }
                        Text(candidate.boardLabel).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(String(format: "%.1f 分", candidate.score))
                            .font(.system(size: 13, weight: .semibold).monospacedDigit())
                        if let j = candidate.llmJudgment { LLMJudgmentBadge(judgment: j) }
                    }
                }
                if !candidate.formTags.isEmpty || !candidate.hotSectors.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(candidate.formTags, id: \.self) { NKChip(text: $0) }
                            ForEach(candidate.hotSectors, id: \.self) { NKChip(text: $0, tone: .good) }
                        }
                    }
                }
                Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                    HStack {
                        Text(expanded ? "收起四件套" : "买点 / 止损 / 目标 / 证伪条件")
                            .font(.system(size: 12, weight: .medium))
                        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 10))
                    }
                    .foregroundStyle(NK.accent)
                }
                .buttonStyle(.plain)
                if expanded {
                    VStack(alignment: .leading, spacing: 6) {
                        fourPiece("买点", candidate.buyPoint)
                        fourPiece("止损", candidate.stop)
                        fourPiece("目标", candidate.target)
                        fourPiece("证伪条件", candidate.invalidation)
                        if let j = candidate.llmJudgment {
                            Divider().overlay(NK.hairline)
                            Text(j.narrative).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                        }
                    }
                    .padding(.top, 2)
                }
            }
        }
    }

    private func fourPiece(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text(text).font(.system(size: 12.5)).foregroundStyle(NK.textPrimary)
        }
    }
}

// MARK: - 持仓卡

private struct PositionCard: View {
    @Bindable var model: AppModel
    let position: Position
    /// 本地会话态(§五 阶段4C「自证 checklist」):后端 4A 尚无持久化字段
    /// (`stopOrderChecked` 恒 false),此勾选仅本机本次会话记忆,不同步、不落库。
    @State private var checkedLocally = false

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(position.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                            Text(position.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        }
                        Text("买入 ¥\(NKFmt.price(position.buyPrice)) × \(position.qty) · \(model.calendar.displayString(position.buyDate))")
                            .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        if !position.entryReason.isEmpty {
                            Text(position.entryReason).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
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
                HStack {
                    NKChip(text: "止损线 ¥\(NKFmt.price(position.stopLine))(-5%)",
                          tone: position.hasBrokenStop ? .bad : .neutral)
                    if position.hasBrokenStop {
                        NKChip(text: "已破止损线", tone: .bad, filled: true)
                    }
                    Spacer()
                    Button { model.openCloseSheet(code: position.code) } label: {
                        Text("补录清仓").font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(NK.down)
                }
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
            }
        }
    }
}

// MARK: - 开仓 / 清仓表单(审计台账,补记用户已在券商完成的操作)

/// 两个表单共用的壳(标题 / 取消 / 提交),避免同一段 `NavigationStack`+`Form`+
/// 工具栏样板在两个 sheet 里重复一份。
private struct PositionFormShell<Content: View>: View {
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
                    ToolbarItem(placement: .cancellationAction) {
                        Button("取消", action: onCancel)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("提交", action: onSubmit)
                    }
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
                TextField("名称(可选)", text: $model.entryForm.name)
                TextField("买入价", text: $model.entryForm.price)
                TextField("数量(股)", text: $model.entryForm.qty)
                TextField("进场理由", text: $model.entryForm.reason)
            } footer: {
                Text("此处只记录你已在券商完成的真实操作;止损线由服务端按 -5% 派生返回,系统不代下单。")
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
            } footer: {
                Text("卖出时间缺省为今日;此处只记录真实成交,系统不代下单。")
            }
        }
    }
}
