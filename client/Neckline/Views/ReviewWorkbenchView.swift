//
//  ReviewWorkbenchView.swift
//  Neckline — 周复盘工作台(macOS 独有,§五 阶段4D):拖入券商交割单 xlsx → 上传 →
//  展示对账周报(自由叙述材料 + 结构化表格)。对账逻辑全在后端 `neckline/review/`
//  (解析/FIFO闭合/三查/统计/强制复盘判定),本视图只负责拖文件、上传、纯展示
//  ——不在客户端重算任何判定(§3.8「同码不重写」精神的延伸)。
//
//  iOS 不做此板块(拖文件 + 阅读长材料是桌面场景,plan §五 阶段4D 明文)。
//

import SwiftUI
#if os(macOS)
import UniformTypeIdentifiers

struct ReviewWorkbenchView: View {
    @Bindable var model: AppModel
    @State private var isTargeted = false
    @State private var warningsExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                header
                dropZone
                warningsSection
                if model.reviewWeeks.isEmpty {
                    emptyState
                } else {
                    weekPicker
                    if let entry = model.selectedReviewEntry {
                        weekContent(entry)
                    }
                }
                // V2-⑫/⑨-C:画像两张账 + 评价校准报告(工作台的另外两块,与对账并列)。
                profilesSection
                evalSection
            }
            .padding(NKSpace.pagePad)
            .frame(maxWidth: 860)
        }
        .frame(maxWidth: .infinity)
        .background(NK.pageBg)
        .task { await model.loadWorkbenchExtras() }
    }

    // MARK: - V2-⑫ 画像(**两张账刻意分开**:偏好答「喜欢什么」、能力答「什么真有效」)

    @ViewBuilder
    private var profilesSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "画像")
            Text("偏好画像答「你喜欢什么」,能力画像答「什么在你手里真有效」—— ⛔ 两张账不合并。")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            ProfileCard(title: "偏好画像", profile: model.preferenceProfile)
            ProfileCard(title: "能力画像", profile: model.capabilityProfile)
        }
    }

    // MARK: - V2-⑨-C 评价校准报告(**长期统计,不是单日打分**)

    @ViewBuilder
    private var evalSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "评价校准报告")
            NKCard {
                if let ev = model.evalWeekly {
                    if ev.available {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("窗口 \(ev.weekStart) ~ \(ev.weekEnd)")
                                .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                            if !ev.markdown.isEmpty {
                                Text(ev.markdown).font(.system(size: 12))
                                    .foregroundStyle(NK.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else {
                                NKJSONTable(value: ev.result)
                            }
                        }
                    } else {
                        // ⛔ 不许拿半截样本给结论。
                        NKEmptyState(title: "本期评价尚不可用",
                                     subtitle: ev.unavailableReason ?? "样本窗未就绪 / 前向窗口还没走完",
                                     systemImage: "hourglass")
                    }
                } else {
                    NKEmptyState(title: "评价报告未取到", systemImage: "exclamationmark.icloud")
                }
            }
        }
    }

    // MARK: - 头部 + 拖入区

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("周复盘工作台").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            Text("拖入券商交割单 xlsx,对照当周计划与纪律章程生成违纪清单")
                .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
        }
    }

    private var dropZone: some View {
        RoundedRectangle(cornerRadius: NKRadius.card)
            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
            .foregroundStyle(isTargeted ? NK.accent : NK.hairline)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(isTargeted ? NK.accent.opacity(0.06) : NK.cardBg))
            .frame(height: 140)
            .overlay {
                VStack(spacing: 8) {
                    if model.reviewUploading {
                        ProgressView().controlSize(.small)
                        Text("解析对账中…").font(.system(size: 13, weight: .medium)).foregroundStyle(NK.textSecondary)
                    } else {
                        Image(systemName: "tray.and.arrow.down.fill").font(.system(size: 28))
                            .foregroundStyle(isTargeted ? NK.accent : NK.textTertiary)
                        Text("把交割单 .xlsx 拖到这里(可一次拖多份)")
                            .font(.system(size: 13, weight: .medium)).foregroundStyle(NK.textSecondary)
                    }
                }
            }
            .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
                handleDrop(providers)
            }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        let relevant = providers.filter { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }
        guard !relevant.isEmpty else { return false }
        Task {
            var loaded: [(filename: String, data: Data)] = []
            for provider in relevant {
                if let file = await Self.loadFile(from: provider) {
                    loaded.append(file)
                }
            }
            guard !loaded.isEmpty else {
                await MainActor.run { model.showToast("未能读取拖入的文件", isError: true) }
                return
            }
            await model.uploadReviewFiles(loaded)
        }
        return true
    }

    /// 把 `NSItemProvider` 的异步回调式 API 包成 `async`,读出(文件名,原始字节)。
    /// 读不到 URL / 读文件失败 → nil(调用方按"这份文件跳过"处理,不中断其它文件)。
    private static func loadFile(from provider: NSItemProvider) async -> (filename: String, data: Data)? {
        await withCheckedContinuation { continuation in
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url, let data = try? Data(contentsOf: url) else {
                    continuation.resume(returning: nil)
                    return
                }
                continuation.resume(returning: (url.lastPathComponent, data))
            }
        }
    }

    // MARK: - 警告(解析层面 + FIFO 数据完整性)

    @ViewBuilder
    private var warningsSection: some View {
        let all = model.reviewParseWarnings + model.reviewDataWarnings
        if !all.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) { warningsExpanded.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(NK.amber)
                            Text("解析提示 \(all.count) 条").font(.system(size: 12.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                            Spacer()
                            Image(systemName: warningsExpanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                        }
                    }
                    .buttonStyle(.plain)
                    if warningsExpanded {
                        ForEach(all, id: \.self) { w in
                            Text(w).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
            }
        }
    }

    private var emptyState: some View {
        NKCard {
            NKEmptyState(
                title: model.reviewHasUploaded ? "本次上传未解析出任何成交记录" : "还没有对账数据",
                subtitle: model.reviewHasUploaded
                    ? "请检查文件是否为支持的两种券商格式,或查看上方解析提示。"
                    : "把每周的券商交割单 .xlsx 拖到上面的区域,系统会自动生成对账周报。",
                systemImage: "tray.and.arrow.down"
            )
        }
    }

    // MARK: - 多周切换

    private var weekPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(model.reviewWeeks) { entry in
                    let selected = entry.week == (model.reviewSelectedWeek ?? model.reviewWeeks.first?.week)
                    Button { model.reviewSelectedWeek = entry.week } label: {
                        HStack(spacing: 5) {
                            Text(entry.week).font(.system(size: 12, weight: .semibold))
                            if entry.result.forcedReview {
                                Image(systemName: "exclamationmark.circle.fill").font(.system(size: 10))
                            }
                        }
                        .foregroundStyle(selected ? .white : NK.textSecondary)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(Capsule().fill(selected ? NK.accent : NK.chipNeutral))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // MARK: - 单周内容

    @ViewBuilder
    private func weekContent(_ entry: WeeklyReviewEntry) -> some View {
        let r = entry.result
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            if r.forcedReview {
                ForcedReviewBanner(reason: r.forcedReviewReason)
            }
            // v1.4-⑥-A(§七 P1-4):章程切换分段——`strategyVersion` 只是"周初标签",
            // 该周若发生过章程切换必须把分段讲清,不可再当"整周按这版判"展示。
            CharterVersionCard(result: r)
            if let stats = r.stats { ReviewStatsCard(stats: stats, weekStart: r.weekStart, weekEnd: r.weekEnd) }
            if !entry.material.isEmpty {
                NKCard {
                    VStack(alignment: .leading, spacing: 6) {
                        NKSectionHeader(title: "复盘材料")
                        Text(entry.material).font(.system(size: 13)).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            violationsCard(r.disciplineViolations)
            planChecksSection(r.planChecks)
            roundTripsSection(r.roundTrips)
            stopDisciplineSection(r.stopDiscipline)
        }
    }

    @ViewBuilder
    private func violationsCard(_ violations: [String]) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                NKSectionHeader(title: "违纪清单", trailing: "\(violations.count) 条")
                if violations.isEmpty {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.seal.fill").foregroundStyle(NK.up)
                        Text("本周未发现违纪").font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                    }
                } else {
                    ForEach(Array(violations.enumerated()), id: \.offset) { _, v in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 10))
                                .foregroundStyle(NK.down).padding(.top, 2)
                            Text(v).font(.system(size: 12)).foregroundStyle(NK.textPrimary)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func planChecksSection(_ checks: [ReviewPlanCheck]) -> some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "实际成交 vs 计划/台账", trailing: "\(checks.count) 笔买入")
            if checks.isEmpty {
                NKCard { NKEmptyState(title: "本周无买入记录", systemImage: "cart") }
            } else {
                ForEach(checks) { c in PlanCheckRow(check: c) }
            }
        }
    }

    @ViewBuilder
    private func roundTripsSection(_ trips: [ReviewRoundTrip]) -> some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "成交回合明细", trailing: "\(trips.count) 笔")
            if trips.isEmpty {
                NKCard { NKEmptyState(title: "本次数据范围内无回合", systemImage: "arrow.left.arrow.right") }
            } else {
                ForEach(trips) { rt in RoundTripRow(trip: rt) }
            }
        }
    }

    @ViewBuilder
    private func stopDisciplineSection(_ entries: [ReviewStopDisciplineEntry]) -> some View {
        if !entries.isEmpty {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                NKSectionHeader(title: "止损纪律核对", trailing: "\(entries.count) 回合")
                ForEach(entries) { e in StopDisciplineRow(entry: e) }
            }
        }
    }
}

// MARK: - 强制复盘横幅(§2.1 第4条,同 RetreatBrakeBanner 视觉权重但文案独立)

private struct ForcedReviewBanner: View {
    let reason: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.octagon.fill").font(.system(size: 16, weight: .bold))
            VStack(alignment: .leading, spacing: 3) {
                Text("触发强制复盘").font(.system(size: 13.5, weight: .bold))
                if !reason.isEmpty {
                    Text(reason).font(.system(size: 12)).opacity(0.9)
                }
            }
            Spacer()
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

// MARK: - v1.4-⑥-A 章程版本卡(§七 P1-4)。`strategyVersion` 只是"周初标签",本周若
// 发生过章程切换,`charterSwitches` 非空时把切换时刻 + 前后版本 + 分段计数讲清楚——
// 不可再让用户误以为"这周成交全按周初那版判"。

private struct CharterVersionCard: View {
    let result: ReviewWeeklyResult

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("本周章程").font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    Spacer()
                    Text(result.strategyVersion.isEmpty ? "未知(旧数据)" : "\(result.strategyVersion)(周初标签)")
                        .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                }
                if result.charterSwitches.isEmpty {
                    if !result.charterSegments.isEmpty {
                        Text("本周未发生章程切换,全周按 \(result.strategyVersion) 判定 \(result.charterSegments.first?.tradeCount ?? 0) 笔")
                            .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                    }
                } else {
                    ForEach(result.charterSwitches) { sw in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: "arrow.triangle.2.circlepath").font(.system(size: 10.5)).foregroundStyle(NK.amber)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("\(sw.at) 章程切换 \(sw.fromVersion) → \(sw.toVersion)")
                                    .font(.system(size: 11.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                                if !sw.note.isEmpty {
                                    Text(sw.note).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                                }
                            }
                        }
                    }
                    ForEach(result.charterSegments) { seg in
                        Text("· \(seg.version) 判定 \(seg.tradeCount) 笔(\(seg.start ?? "周初") 起)")
                            .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    }
                }
            }
        }
    }
}

// MARK: - 单周统计卡

private struct ReviewStatsCard: View {
    let stats: ReviewWeeklyStats
    let weekStart: String
    let weekEnd: String

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "本周统计", trailing: "\(weekStart)–\(weekEnd)")
                HStack(spacing: 0) {
                    stat("平仓回合", "\(stats.closedCount)")
                    stat("胜率", stats.closedCount > 0 ? NKFmt.pct(stats.winRate * 100) : "—")
                    stat("盈利因子", stats.profitFactor.map { String(format: "%.2f", $0) } ?? "∞")
                    stat("盈亏比", stats.profitLossRatio.map { String(format: "%.2f", $0) } ?? "∞")
                }
                HStack(spacing: 0) {
                    stat("净盈亏", NKFmt.signedMoney(stats.realizedPnl), tone: stats.realizedPnl >= 0 ? .good : .bad)
                    stat("实现亏损", NKFmt.signedMoney(stats.realizedLoss), tone: .bad)
                    stat("费用", String(format: "¥%.0f", stats.totalFees))
                    stat("未平仓", "\(stats.openCount)")
                }
            }
        }
    }

    private func stat(_ label: String, _ value: String, tone: NKAxisTone = .neutral) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
            Text(value).font(.system(size: 15, weight: .semibold).monospacedDigit())
                .foregroundStyle(tone == .neutral ? NK.textPrimary : tone.color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - 计划/台账核对行

private struct PlanCheckRow: View {
    let check: ReviewPlanCheck

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(check.name).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                            Text(check.tsCode).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        }
                        Text("\(check.tradeDate) · ¥\(String(format: "%.2f", check.price)) × \(check.qty) 股 = ¥\(String(format: "%.0f", check.amount))")
                            .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                }
                HStack(spacing: 6) {
                    NKChip(text: check.planStatus, tone: check.isOffPlan ? .bad : .good)
                    NKChip(text: check.ledgerStatus, tone: (check.isLedgerMissing || check.isLedgerMismatch) ? .warn : .good)
                }
            }
        }
    }
}

// MARK: - 回合行

private struct RoundTripRow: View {
    let trip: ReviewRoundTrip

    var body: some View {
        NKCard {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(trip.name).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                        Text(trip.tsCode).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    }
                    Text("买入 \(trip.buyDate) · ¥\(String(format: "%.2f", trip.buyPrice)) × \(trip.qty) 股")
                        .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                    if trip.closed, let sellDate = trip.sellDate, let sellPrice = trip.sellPrice {
                        Text("卖出 \(sellDate) · ¥\(String(format: "%.2f", sellPrice))")
                            .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                    } else {
                        Text("仍持仓(本次上传数据范围内未见卖出)").font(.system(size: 11.5)).foregroundStyle(NK.amber)
                    }
                }
                Spacer()
                if let pnl = trip.netPnl, let pct = trip.pnlPct {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(NKFmt.signedMoney(pnl)).font(.system(size: 13, weight: .semibold).monospacedDigit())
                            .foregroundStyle(pnl >= 0 ? NK.up : NK.down)
                        Text(NKFmt.signedPct(pct * 100)).font(.system(size: 11).monospacedDigit())
                            .foregroundStyle(pnl >= 0 ? NK.up : NK.down)
                    }
                }
            }
        }
    }
}

// MARK: - 止损纪律核对行

private struct StopDisciplineRow: View {
    let entry: ReviewStopDisciplineEntry

    var body: some View {
        NKCard {
            HStack(alignment: .top, spacing: 10) {
                if let kind = entry.kind {
                    NKChip(text: kind.label, tone: kind.tone, filled: true)
                }
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(entry.roundTrip.name).font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                        Text(entry.roundTrip.tsCode).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    }
                    Text(entry.note).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                }
                Spacer()
            }
        }
    }
}

// MARK: - V2-⑫ 画像卡(每行必带**样本量 / 时间范围 / 置信度**)

private struct ProfileCard: View {
    let title: String
    let profile: Profile?

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                guardBody
            }
        }
    }

    @ViewBuilder
    private var guardBody: some View {
        if let p = profile {
            if !p.available {
                NKEmptyState(title: "本期画像不可用",
                             subtitle: p.unavailableReason ?? "该期从未算过(不是「算出来是空的」)",
                             systemImage: "person.crop.circle.badge.questionmark")
            } else if p.items.isEmpty {
                Text("算过了 · 本期无可展示的分组").font(.system(size: 12))
                    .foregroundStyle(NK.textSecondary)
            } else {
                Text("截至 \(p.asOf.isEmpty ? "—" : p.asOf)")
                    .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                ForEach(p.items.map(ProfileRow.init(raw:))) { row in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text("\(row.dimension) · \(row.bucket)")
                                .font(.system(size: 12, weight: .medium)).foregroundStyle(NK.textPrimary)
                            Spacer()
                            NKChip(text: "样本 \(row.sampleN)",
                                   tone: row.isLowConfidence ? .warn : .neutral)
                        }
                        Text("窗口 \(row.windowStart) ~ \(row.windowEnd)")
                            .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                        if row.isLowConfidence {
                            // ⛔ 低置信度**不许当结论展示**(⑫ 验收条款)。
                            Text("样本不足,不给结论").font(.system(size: 10.5, weight: .semibold))
                                .foregroundStyle(NK.amber)
                        } else {
                            ForEach(row.metricKeys, id: \.self) { k in
                                HStack {
                                    Text(k).font(.system(size: 10.5).monospaced())
                                        .foregroundStyle(NK.textTertiary)
                                    Spacer()
                                    Text(row.raw[k]?.displayText ?? "—")
                                        .font(.system(size: 10.5).monospaced())
                                        .foregroundStyle(NK.textSecondary)
                                }
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        } else {
            NKEmptyState(title: "画像未取到", systemImage: "exclamationmark.icloud")
        }
    }
}

#endif
