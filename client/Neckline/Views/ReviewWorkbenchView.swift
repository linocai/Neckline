//
//  ReviewWorkbenchView.swift
//  Neckline — **复盘板块 · 对账页**(macOS 独有;V2.1-⑦ 起由 `ReviewView` 嵌入,
//  不再是一个独立 tab):拖入券商交割单 xlsx → 上传 → 展示对账周报(自由叙述材料 +
//  结构化表格)。对账逻辑全在后端 `neckline/review/`(解析/FIFO闭合/三查/统计/
//  强制复盘判定),本视图只负责拖文件、上传、纯展示 —— 不在客户端重算任何判定
//  (§3.8「同码不重写」精神的延伸)。
//
//  iOS 侧只做**只读**展示(见 `ReviewView.reconcilePage`):拖文件 + 阅读长材料是桌面场景。
//
//  ⚠ **V2.1-⑦ 移出的两节**:「画像」与「评价校准报告」已迁往复盘板块的**累计页**
//  (数据源换成 ⑤ 的 `GET /review/overview` 聚合读)。⛔ 不在两处各画一遍 —— 同一份
//  数据画两遍只会让用户在两处看到可能不同步的两个版本(同 ② 持仓体检那条)。
//  ⚠ 顺带的口径变化(如实登记,不是漏做):累计页的校准段读的是**离线落盘产物**,
//  而本页原先那张卡走的是 `GET /eval/weekly`(**在线现算**,§七 P4-46 已挂账)——
//  周度作业(⑥,批 3)上线前,累计页会如实说「本窗口的周度校准产物尚未生成」。
//  那是 plan §五⑤ 明写的合法中间态,**不是缺陷**。
//

import SwiftUI
#if os(macOS)
import AppKit
import UniformTypeIdentifiers

struct ReviewWorkbenchView: View {
    @Bindable var model: AppModel
    @State private var isTargeted = false

    /// ⚠ **本视图不再自带 `ScrollView` / 背景 / 页边距**:V2.1-⑦ 起它被 `ReviewView`
    /// 的滚动容器嵌入(嵌套 ScrollView 会让内层高度无界、滚动手势打架)——
    /// 容器的事归容器,⛔ 别在这里再套一层。
    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if let entry = model.selectedReviewEntry {
                // —— 已上传态(原型 `Neckline 信息卡与对账.dc.html` 337–450)——
                uploadedHeader(entry)
                warningsSection
                weekContent(entry)
            } else {
                // —— 未上传态(原型 `Neckline macOS.dc.html` 1546–1571)——
                ReviewPageTitle("对账", subtitle: emptySubtitle)
                dropZone
                warningsSection
                emptyState
            }
        }
    }

    private var emptySubtitle: String {
        let win = model.reviewOverview.map { "\($0.weekStart) ~ \($0.weekEnd)" } ?? ""
        return "我实际的成交与计划 / 章程对不对得上" + (win.isEmpty ? "" : " · " + win)
    }

    /// 已上传态的标题块(原型 340–347)。
    private func uploadedHeader(_ entry: WeeklyReviewEntry) -> some View {
        HStack(alignment: .bottom, spacing: 12) {
            ReviewPageTitle("\(entry.week) 对账", subtitle: headerSubtitle(entry))
            Spacer(minLength: 8)
            Text("解析 / FIFO 闭合 / 三查 / 章程判定全在后端")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary).fixedSize()
        }
    }

    private func headerSubtitle(_ entry: WeeklyReviewEntry) -> String {
        let r = entry.result
        var parts = ["\(r.weekStart) ~ \(r.weekEnd)"]
        parts.append("\(r.roundTrips.count) 笔回合")
        if let g = model.reviewOverview?.reconcile.detail["generatedAt"]?.stringValue, !g.isEmpty {
            parts.append("生成于 \(g)")
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - 拖入区(未上传态)

    /// 原型 1552–1562:`1.5px dashed rgba(11,107,203,.35); radius:14; padding:38px 24px;
    /// text-align:center; background:rgba(11,107,203,.03)`。
    /// 🔴 中间那两句**诚实披露一个字都不许省**:「系统查过了 —— 本周确实没有上传记录」
    /// 与「这不是故障,是必需输入只能由你给」讲的是**「没有」不是「没看」**。
    private var dropZone: some View { dropZoneView(alreadyUploaded: model.selectedReviewEntry != nil) }

    private func dropZoneView(alreadyUploaded: Bool) -> some View {
        VStack(spacing: 0) {
            if model.reviewUploading {
                ProgressView().controlSize(.small)
                Text("解析对账中…").font(NKFont.headline).foregroundStyle(NK.textSecondary)
                    .padding(.top, 12)
            } else {
                Image(systemName: "square.and.arrow.up").font(.system(size: 34, weight: .light))
                    .foregroundStyle(NK.accent.opacity(0.7))
                Text(alreadyUploaded ? "再拖入交割单 .xlsx" : "把券商交割单 .xlsx 拖到这里")
                    .font(NKFont.headline)
                    .foregroundStyle(NK.textPrimary).padding(.top, 12)
                Text(alreadyUploaded
                     ? "可一次拖多份 · 同一周再传会用新的那一份覆盖上面这份对账"
                     : notFoundText)
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .multilineTextAlignment(.center).lineSpacing(4).padding(.top, 5)
                    .fixedSize(horizontal: false, vertical: true)
                Button { chooseFiles() } label: {
                    Text("选择文件").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16).padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: NKRadius.control)
                            .fill(NK.accent))
                }
                .buttonStyle(.plain).padding(.top, 16)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24).padding(.vertical, 38)
        .background(RoundedRectangle(cornerRadius: 14)
            .fill(NK.accent.opacity(isTargeted ? 0.08 : 0.03)))
        .overlay(RoundedRectangle(cornerRadius: 14)
            .strokeBorder(NK.accent.opacity(isTargeted ? 0.7 : 0.35),
                          style: StrokeStyle(lineWidth: 1.5, dash: [6, 5])))
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            handleDrop(providers)
        }
    }

    /// ⚠ 「查过了确实没有」与「这次没查成」是两句话:前者服务端已经写好
    /// (`reconcile.note`),⛔ 客户端不改写;后者(段没取到)照实说。
    private var notFoundText: String {
        guard let seg = model.reviewOverview?.reconcile else {
            return "本周对账本次没查成 —— 这不是「本周没上传」,是这次没连上服务端。"
        }
        if !seg.available {
            return seg.unavailableReason ?? "本周对账本次没查成(服务端未给原因)。"
        }
        return seg.note ?? "系统查过了 —— 本周确实没有上传记录。这不是故障,是必需输入只能由你给:系统补不出没上传的那一份。"
    }

    /// 「选择文件」(原型 1558)。⛔ 不往固定目录偷偷读写,由用户自己选。
    private func chooseFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        if let xlsx = UTType(filenameExtension: "xlsx") {
            panel.allowedContentTypes = [xlsx]
        }
        guard panel.runModal() == .OK else { return }
        let files: [(filename: String, data: Data)] = panel.urls.compactMap { url in
            guard let data = try? Data(contentsOf: url) else { return nil }
            return (url.lastPathComponent, data)
        }
        guard !files.isEmpty else {
            model.showToast("未能读取所选文件", isError: true); return
        }
        Task { await model.uploadReviewFiles(files) }
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
                NKAuditSection(contains: "解析提示原文 \(all.count) 条") {
                    ForEach(all, id: \.self) { w in
                        Text(w).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        if model.reviewHasUploaded {
            NKCard {
                NKEmptyState(title: "本次上传未解析出任何成交记录",
                             subtitle: "请检查文件是否为支持的两种券商格式,或查看上面的解析提示。",
                             systemImage: "doc.questionmark")
            }
        }
        // ⚠ **没上传过时这里刻意什么都不画**:该说的话已经在拖入区里说完了
        // (「系统查过了 —— 本周确实没有上传记录」)。再来一张「还没有对账数据」的
        // 空态卡 = 同一件事说两遍,而且第二遍的措辞还更像故障。
        // 🔴 原型 1563–1570 那张「上一份对账 · 07-27 ~ 07-31 · 查看 →」的卡**画不出来**:
        // 契约没有「列出已上传的周」这条端点(`GET /review?week=` 只按周查单条),
        // 客户端**无从知道上一份是哪一周** —— 判「刻意不同 · 契约支撑不了」,
        // ⛔ 不许拿"往前猜几周"去凑(那是替服务端编事实)。
    }

    // MARK: - 单周内容

    @ViewBuilder
    private func weekContent(_ entry: WeeklyReviewEntry) -> some View {
        let r = entry.result
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if r.forcedReview {
                ForcedReviewBanner(reason: r.forcedReviewReason)
            }
            violationsCard(r.disciplineViolations)
            // v1.4-⑥-A(§七 P1-4):章程切换分段——`strategyVersion` 只是"周初标签",
            // 该周若发生过章程切换必须把分段讲清,不可再当"整周按这版判"展示。
            CharterVersionCard(result: r)
            if let stats = r.stats { ReviewStatsCard(stats: stats, weekStart: r.weekStart, weekEnd: r.weekEnd) }
            planChecksSection(r.planChecks)
            if !entry.material.isEmpty {
                NKCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("复盘材料").nkLabel().foregroundStyle(NK.textTertiary)
                        Text(entry.material).font(NKFont.body).lineSpacing(5)
                            .foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            // 原型 447–450:整块收进一枚虚线「审计视图」按钮。
            NKAuditSection(contains: "成交回合明细、止损纪律核对") {
                NKAuditGroup(title: "成交回合明细 \(r.roundTrips.count) 笔") {
                    ForEach(r.roundTrips) { rt in RoundTripRow(trip: rt) }
                }
                if !r.stopDiscipline.isEmpty {
                    NKAuditGroup(title: "止损纪律核对 \(r.stopDiscipline.count) 回合") {
                        ForEach(r.stopDiscipline) { e in StopDisciplineRow(entry: e) }
                    }
                }
            }
            // 再拖一份(已上传态仍要能补传;原型把它放在列表栏底部的虚线块里)。
            // ⚠ **文案换一套**:已上传的周再画「系统查过了 —— 本周确实没有上传记录」
            // 就是**假话**(V2.3.1 批 4 实拍逮到)。
            dropZone
        }
    }

    /// 违纪清单(原型 373–398:白卡 + 逐条「类型徽标 + 一句话 + 锚」)。
    @ViewBuilder
    private func violationsCard(_ violations: [String]) -> some View {
        NKRowsCard {
            HStack(spacing: 8) {
                Text("违纪清单").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                Spacer(minLength: 0)
                if violations.isEmpty {
                    NKChip(text: "0 条", tone: .good, filled: true)
                } else {
                    NKChip(text: "\(violations.count) 条", tone: .bad, filled: true)
                }
            }
            .padding(.horizontal, 18).padding(.top, 14).padding(.bottom, 11)
            if violations.isEmpty {
                Divider().overlay(NK.hairline)
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.seal.fill").font(.system(size: 15))
                        .foregroundStyle(NK.up)
                    Text("本周未发现违纪").font(NKFont.body).foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 18).padding(.vertical, 12)
            } else {
                ForEach(Array(violations.enumerated()), id: \.offset) { _, v in
                    Divider().overlay(NK.hairline)
                    HStack(alignment: .top, spacing: 11) {       // 原型 379 gap:11
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 11)).foregroundStyle(NK.down).padding(.top, 3)
                        // ⚠ 违纪条目是**服务端拼好的整句**(带金额 / 章程版本 / 锚点),
                        // ⛔ 客户端不拆、不重排、不重算。
                        Text(v).font(NKFont.body).lineSpacing(4)
                            .foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 18).padding(.vertical, 12)
                }
            }
        }
    }

    /// 实际成交 vs 计划 / 台账(原型 411–435 的五列表)。
    @ViewBuilder
    private func planChecksSection(_ checks: [ReviewPlanCheck]) -> some View {
        NKRowsCard {
            HStack(spacing: 8) {
                Text("实际成交 vs 计划 / 台账").font(NKFont.headline)
                    .foregroundStyle(NK.textPrimary)
                Spacer(minLength: 0)
                Text("\(checks.count) 笔买入").font(NKFont.caption)
                    .foregroundStyle(NK.textTertiary)
            }
            .padding(.horizontal, 18).padding(.top, 14).padding(.bottom, 11)
            if checks.isEmpty {
                Divider().overlay(NK.hairline)
                Text("本周无买入记录").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .padding(.horizontal, 18).padding(.vertical, 12)
            } else {
                // 🔴 原型 413 是**五列**(标的 / 实际买价 / **计划区间** / **偏离** / 台账),
                // 落地只有四列:`ReviewPlanCheck` 契约里**没有** `planLow`/`planHigh`/
                // `deviationPct` 三个字段 —— 服务端只发一句 `plan_status`(「计划内(当日
                // 报告候选)」/「计划外(未经系统候选…)」)。⛔ 不许在客户端反推区间与
                // 偏离,那是造服务端没给的数(判「刻意不同 · 契约支撑不了」)。
                NKRowsHeader {
                    Text("标的 · 日期").frame(maxWidth: .infinity, alignment: .leading)
                    Text("成交金额").frame(width: 100, alignment: .trailing)
                    Text("计划核对").frame(width: 168, alignment: .trailing)
                    Text("台账").frame(width: 168, alignment: .trailing)
                }
                ForEach(Array(checks.enumerated()), id: \.element.id) { idx, c in
                    if idx > 0 { Divider().overlay(NK.hairline) }
                    PlanCheckRow(check: c)
                }
                Text("交割单里有、台账里没有的那几笔 —— 系统能看出漏录,但补不出你当时的想法。提示而已,⛔ 不阻断、不进任何判定。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18).padding(.top, 11).padding(.bottom, 14)
                    .background(NK.amber.opacity(0.04))          // 原型 434
            }
        }
    }
}

// MARK: - 强制复盘横幅(§2.1 第4条,同 RetreatBrakeBanner 视觉权重但文案独立)

private struct ForcedReviewBanner: View {
    let reason: String
    var body: some View {
        HStack(alignment: .center, spacing: 13) {                // 原型 360 gap:13
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 21))
            VStack(alignment: .leading, spacing: 2) {
                Text("触发强制复盘").font(NKFont.headline).fontWeight(.bold)
                if !reason.isEmpty {
                    Text(reason).font(NKFont.callout).opacity(0.92).lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 17).padding(.vertical, 15)         // 原型 359
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.alertGrad))
    }
}

// MARK: - v1.4-⑥-A 章程版本卡(§七 P1-4)。`strategyVersion` 只是"周初标签",本周若
// 发生过章程切换,`charterSwitches` 非空时把切换时刻 + 前后版本 + 分段计数讲清楚——
// 不可再让用户误以为"这周成交全按周初那版判"。

private struct CharterVersionCard: View {
    let result: ReviewWeeklyResult

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 13) {            // 原型 401 margin-bottom:13
                HStack(spacing: 8) {
                    Text("本周章程").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 0)
                    Text(result.strategyVersion.isEmpty ? "未知(旧数据)"
                                                        : "\(result.strategyVersion)(周初标签)")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
                if result.charterSegments.isEmpty {
                    Text(result.charterSwitches.isEmpty
                         ? "本周未发生章程切换" : "本周发生过章程切换,但没有落下分段计数")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                } else {
                    // 原型 405–408:两段**并排**、中间一条 26×2 的连接线。
                    HStack(spacing: 0) {
                        ForEach(Array(result.charterSegments.enumerated()), id: \.offset) { idx, seg in
                            if idx > 0 {
                                Rectangle().fill(NK.textTertiary.opacity(0.5))
                                    .frame(width: 26, height: 2)
                            }
                            segmentCell(seg, active: idx == result.charterSegments.count - 1)
                        }
                    }
                }
                ForEach(result.charterSwitches) { sw in
                    // ⚠ 这句话带 `**加粗**`,是**字面量**才解析得了(§五 〇d 第 7 条)。
                    Text("⚠ 本周**发生过章程切换**(\(sw.at) \(sw.fromVersion) → \(sw.toVersion))。逐笔按成交时刻各归各的版本判 —— ⛔ 不是「整周按周初那版判」。")
                        .font(NKFont.caption).foregroundStyle(NK.amber).lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func segmentCell(_ seg: ReviewCharterSegment, active: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(seg.version).font(NKFont.callout).fontWeight(.semibold)
                .foregroundStyle(NK.textPrimary)
            Text((seg.start.map { "\($0) 起" } ?? "自周初") + " · \(seg.tradeCount) 笔")
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 10)         // 原型 405
        .background(RoundedRectangle(cornerRadius: NKRadius.control)
            .fill(active ? NK.accent.opacity(0.06) : NK.textTertiary.opacity(0.05)))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
            .stroke(active ? NK.accent.opacity(0.22) : NK.hairline, lineWidth: 0.5))
    }
}

// MARK: - 单周统计卡

private struct ReviewStatsCard: View {
    let stats: ReviewWeeklyStats
    let weekStart: String
    let weekEnd: String

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 14) {            // 原型 415 margin-bottom:14
                HStack(spacing: 8) {
                    Text("本周统计").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 0)
                    Text("\(weekStart) ~ \(weekEnd)")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                }
                NKStatGrid(columns: 4) {                          // 原型 416 repeat(4,1fr)
                    NKStatCell(title: "平仓回合", value: "\(stats.closedCount)")
                    NKStatCell(title: "未平仓", value: "\(stats.openCount)")
                    // 🔴 金额一律走 `NKFmt`(批 1 全局钉子):千分位、符号在 ¥ 外面。
                    NKStatCell(title: "净盈亏(含费)",
                               value: NKFmt.signedAmount(stats.realizedPnl),
                               tone: stats.realizedPnl >= 0 ? .good : .bad)
                    NKStatCell(title: "实付费用合计", value: "¥" + NKFmt.amount(stats.totalFees))
                }
                Divider().overlay(NK.hairline)
                NKStatGrid(columns: 4) {
                    NKStatCell(title: "胜率",
                               value: stats.closedCount > 0 ? NKFmt.pct(stats.winRate * 100) : "—")
                    NKStatCell(title: "盈利因子",
                               value: stats.profitFactor.map { String(format: "%.2f", $0) } ?? "∞")
                    NKStatCell(title: "盈亏比",
                               value: stats.profitLossRatio.map { String(format: "%.2f", $0) } ?? "∞")
                    NKStatCell(title: "实现亏损",
                               value: NKFmt.signedAmount(stats.realizedLoss), tone: .bad)
                }
            }
        }
    }
}

// MARK: - 计划/台账核对行

private struct PlanCheckRow: View {
    let check: ReviewPlanCheck

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(check.name).font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Text(check.tradeDate).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary)
                }
                Text("\(check.tsCode) · ¥\(NKFmt.price(check.price)) × \(check.qty) 股")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text("¥" + NKFmt.amount(check.amount)).font(NKFont.callout.monospacedDigit())
                .foregroundStyle(NK.textPrimary).frame(width: 100, alignment: .trailing)
            // ⚠ 服务端把「计划内 / 计划外 / 无报告数据」整句写好了(`plan_status`),
            // ⛔ 客户端不拆、不改写、不另判。
            Text(check.planStatus).font(NKFont.caption)
                .fontWeight(check.isOffPlan ? .semibold : .regular)
                .foregroundStyle(check.isOffPlan ? NK.amber : NK.textSecondary)
                .multilineTextAlignment(.trailing)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: 168, alignment: .trailing)
            Text(check.ledgerStatus).font(NKFont.caption)
                .fontWeight((check.isLedgerMissing || check.isLedgerMismatch) ? .semibold : .regular)
                .foregroundStyle((check.isLedgerMissing || check.isLedgerMismatch) ? NK.down : NK.up)
                .multilineTextAlignment(.trailing)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: 168, alignment: .trailing)
        }
        .padding(.horizontal, 18).padding(.vertical, 11)          // 原型 423
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
                        Text(trip.name).font(NKFont.body).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
                        Text(trip.tsCode).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                    Text("买入 \(trip.buyDate) · ¥\(String(format: "%.2f", trip.buyPrice)) × \(trip.qty) 股")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    if trip.closed, let sellDate = trip.sellDate, let sellPrice = trip.sellPrice {
                        Text("卖出 \(sellDate) · ¥\(String(format: "%.2f", sellPrice))")
                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    } else {
                        Text("仍持仓(本次上传数据范围内未见卖出)").font(NKFont.caption).foregroundStyle(NK.amber)
                    }
                }
                Spacer()
                if let pnl = trip.netPnl, let pct = trip.pnlPct {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(NKFmt.signedAmount(pnl)).font(NKFont.body.monospacedDigit()).fontWeight(.semibold)
                            .foregroundStyle(pnl >= 0 ? NK.up : NK.down)
                        Text(NKFmt.signedPct(pct * 100)).font(NKFont.caption.monospacedDigit())
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
                        Text(entry.roundTrip.name).font(NKFont.body).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
                        Text(entry.roundTrip.tsCode).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                    Text(entry.note).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                }
                Spacer()
            }
        }
    }
}

// ⚠ **`ProfileCard` 已随「画像」一节迁往复盘板块累计页**(V2.1-⑦):那边的
// `ProfileSegmentCard` / `ProfileRowView` 读的是 `GET /review/overview` 的画像段
// (与 `/profile/*` 同码同源,服务端直接复用那两个端点函数),双端共用、不再 macOS 独有。
// ⛔ 别在这里再留一份 —— 两份画像卡迟早会讲不同的话。

#endif
