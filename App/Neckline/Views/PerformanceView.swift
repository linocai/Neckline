import Foundation
import SwiftUI

struct PerformanceView: View {
    @Bindable var model: AppModel
    @State private var filter: PerformanceFilter = .all

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                V3PageHeader(
                    title: "选股表现",
                    subtitle: "固定 D1 / D2 的行情事实与封板记录，不表示个人交易结果。"
                )

                if let results = model.results {
                    PerformanceDashboard(results: results)
                    PerformanceFilterBar(selection: $filter)

                    if let reason = results.reason {
                        PerformanceNotice(icon: "exclamationmark.triangle.fill", text: reason.message)
                    }

                    let records = filteredRecords(results.records)
                    if records.isEmpty {
                        V3EmptyState(
                            icon: "chart.bar.xaxis",
                            title: emptyTitle,
                            message: results.reason?.message ?? "正式发布后自动跟踪固定窗口；D2 收盘后会补充完整或缺口明确的记录。"
                        )
                    } else {
                        V3SectionTitle(title: "公司窗口", icon: "rectangle.stack")
                        LazyVStack(spacing: NKSpace.blockGap) {
                            ForEach(records) { record in
                                EvaluationCard(value: record, model: model)
                            }
                        }
                    }

                    if let cohorts = results.cohorts, !cohorts.isEmpty {
                        CohortResults(cohorts: cohorts)
                    }
                    if let groups = results.eventGroups, !groups.isEmpty {
                        EventGroupResults(groups: groups)
                    }
                } else {
                    V3EmptyState(
                        icon: "chart.bar.xaxis",
                        title: "尚未取得选股表现",
                        message: "连接服务后读取固定窗口的两日行情事实与评价状态。"
                    )
                }
            }
            .padding(.horizontal, NKSpace.pagePad)
            .padding(.top, NKSpace.pagePad)
            .padding(.bottom, NKSpace.pagePadBottom)
        }
        .background(NK.pageBg)
        .navigationTitle("选股表现")
    }

    private var emptyTitle: String {
        switch filter {
        case .overlap: return "尚无重叠窗口"
        case .all: return "尚无两日观察记录"
        default: return "该分组尚无观察记录"
        }
    }

    private func filteredRecords(_ records: [K10Evaluation]) -> [K10Evaluation] {
        records.filter { record in
            switch filter {
            case .all:
                return record.sampleClass == "primary"
            case .kept:
                return record.sampleClass == "primary" && record.selection?.state == "selected"
            case .skipped:
                return record.sampleClass == "primary" && record.selection?.state == "skipped"
            case .unhandled:
                return record.sampleClass == "primary" && record.selection?.state == "unhandled"
            case .overlap:
                return record.sampleClass == "overlap"
            }
        }
    }
}

private enum PerformanceFilter: String, CaseIterable, Identifiable {
    case all, kept, skipped, unhandled, overlap

    var id: String { rawValue }
    var title: String {
        ["all": "全部", "kept": "留下", "skipped": "明确略过", "unhandled": "未处理", "overlap": "重叠"][rawValue] ?? rawValue
    }
}

private struct PerformanceFilterBar: View {
    @Binding var selection: PerformanceFilter

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(PerformanceFilter.allCases) { item in
                    Button(item.title) { selection = item }
                        .font(NKFont.callout.weight(.semibold))
                        .foregroundStyle(selection == item ? Color.white : NK.textSecondary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(selection == item ? NK.accent : NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.control))
                        .overlay(RoundedRectangle(cornerRadius: NKRadius.control).stroke(selection == item ? NK.accent : NK.hairline, lineWidth: 0.5))
                        .buttonStyle(.plain)
                }
            }
        }
    }
}

private struct PerformanceDashboard: View {
    let results: K10Results

    private var primary: K10EvaluationMetrics { results.primary["all"] ?? results.overlap }
    private var rateText: String { primary.hitRate.map { String(format: "%.1f%%", $0 * 100) } ?? "待核" }

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("主样本两日收盘封板率").font(NKFont.headline)
                        Text(rateText).font(NKFont.heroNumber).foregroundStyle(NK.accent)
                        Text("可核 \(primary.eligibleCount) / 已发布主样本 \(primary.sampleCount)")
                            .font(NKFont.callout.monospacedDigit())
                            .foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    Image(systemName: "chart.line.uptrend.xyaxis")
                        .font(.title2)
                        .foregroundStyle(NK.accent)
                        .padding(11)
                        .background(NK.accent.opacity(0.10), in: RoundedRectangle(cornerRadius: NKRadius.inner))
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 132), spacing: 8)], spacing: 8) {
                    MetricTile(title: "两日收盘封板", value: "\(primary.hitCount)", note: "仅完整主样本")
                    MetricTile(title: "两日触板", value: primary.touchRate.map { String(format: "%.1f%%", $0 * 100) } ?? "待核", note: "分母同可核主样本")
                    MetricTile(title: "未到期／待核", value: "\(primary.pendingCount)", note: "不计为未命中")
                    MetricTile(title: "停牌", value: "\(primary.suspendedCount)", note: "单列，不计为未命中")
                    MetricTile(title: "行情缺数", value: "\(primary.dataGapCount)", note: "资料不完整的子集，不可相加")
                    MetricTile(title: "行情异常", value: "\(primary.anomalyCount)", note: "单列，不计为未命中")
                    MetricTile(title: "资料不完整", value: "\(primary.incompleteCount)", note: "含行情缺数子集，不可相加")
                    MetricTile(title: "评价未配置", value: primary.notConfiguredCount.map(String.init) ?? "未记录", note: "保留样本，暂不计成绩")
                    MetricTile(title: "未冻结", value: "\(primary.selectionPendingCount)", note: "尚未归入三组")
                }

                Text("主口径仅统计 D2 已到期且 D1 / D2 封板状态完整可核的主样本；重叠机会单列展示。")
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
            }
        }
    }
}

private struct MetricTile: View {
    let title: String
    let value: String
    let note: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value).font(NKFont.metric).foregroundStyle(NK.textPrimary)
            Text(note).font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
        .frame(maxWidth: .infinity, minHeight: 76, alignment: .leading)
        .padding(10)
        .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }
}

private struct PerformanceNotice: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: icon).foregroundStyle(NK.amber)
            Text(text).font(NKFont.callout).foregroundStyle(NK.textPrimary)
            Spacer(minLength: 0)
        }
        .padding(NKSpace.cardPad)
        .background(NK.amber.opacity(0.08), in: RoundedRectangle(cornerRadius: NKRadius.card))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.amber.opacity(0.28), lineWidth: 0.5))
    }
}

private struct CohortResults: View {
    let cohorts: [K10ResultsCohort]

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            V3SectionTitle(title: "同批次、同窗口比较", icon: "calendar")
            ForEach(cohorts) { cohort in
                V3Card {
                    VStack(alignment: .leading, spacing: 9) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("D1 \(k10DisplayTime(cohort.d1TradeDate)) · D2 \(k10DisplayTime(cohort.d2TradeDate))")
                                    .font(NKFont.headline)
                                Text("来源批次 \(cohort.sourceBatchText) · 公司 \(cohort.companySampleCount) · 事件 \(cohort.catalystEventCount)")
                                    .font(NKFont.caption)
                                    .foregroundStyle(NK.textSecondary)
                            }
                            Spacer()
                            if let version = cohort.evaluationVersion { V3Pill(text: version) }
                        }
                        CohortMetricRow(title: "全部主样本", metric: cohort.primary["all"])
                        CohortMetricRow(title: "留下", metric: cohort.primary["selected"])
                        CohortMetricRow(title: "明确略过", metric: cohort.primary["skipped"])
                        CohortMetricRow(title: "未处理", metric: cohort.primary["unhandled"])
                        Divider().overlay(NK.hairline)
                        CohortMetricRow(title: "重叠观察", metric: cohort.overlap, showsRate: false, observedOnly: true)
                    }
                }
            }
        }
    }
}

private extension K10ResultsCohort {
    var sourceBatchText: String {
        let values = batchIds ?? []
        return (values.isEmpty ? [batchId] : values).joined(separator: "、")
    }
}

private struct CohortMetricRow: View {
    let title: String
    let metric: K10EvaluationMetrics?
    var showsRate = true
    var observedOnly = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title).font(NKFont.callout).frame(width: 72, alignment: .leading)
            if let metric {
                VStack(alignment: .leading, spacing: 2) {
                    Text(observedOnly
                         ? "样本 \(metric.sampleCount) · 可核 \(metric.observedCompleteCount) · 收盘封板 \(metric.hitCount)"
                         : "样本 \(metric.sampleCount) · 可核 \(metric.eligibleCount) · 收盘封板 \(metric.hitCount)")
                    Text("评价未配置 \(metric.notConfiguredCount.map(String.init) ?? "未记录")")
                    Text("未到期／待核 \(metric.pendingCount) · 停牌 \(metric.suspendedCount) · 缺数 \(metric.dataGapCount) · 异常 \(metric.anomalyCount) · 不完整 \(metric.incompleteCount)（缺数为不完整子集）")
                }
                    .font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textSecondary)
                Spacer(minLength: 0)
                if showsRate { Text(metric.hitRate.map { String(format: "%.1f%%", $0 * 100) } ?? "待核").font(NKFont.callout.monospacedDigit()).foregroundStyle(NK.accent) }
            } else {
                Text("尚无可比记录").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                Spacer()
            }
        }
    }
}

private struct EventGroupResults: View {
    let groups: [K10ResultsEventGroup]

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            V3SectionTitle(title: "按共同事件追溯", icon: "point.3.connected.trianglepath.dotted")
            Text("事件分组用于查看关联公司与催化数量，不能相加为独立事件次数。")
                .font(NKFont.caption)
                .foregroundStyle(NK.textSecondary)
            ForEach(groups) { group in
                V3Card {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(group.headline ?? "共同事件").font(NKFont.headline)
                        Text("关联公司窗口 \(group.companySampleCount) · 机会 \(group.opportunityIds.count) · 催化记录 \(group.catalystCount)")
                            .font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                        CohortMetricRow(title: "主样本", metric: group.primary["all"])
                        Divider().overlay(NK.hairline)
                        if let overlap = group.overlap {
                            CohortMetricRow(title: "重叠观察", metric: overlap, showsRate: false, observedOnly: true)
                        } else {
                            Text("该事件的重叠成绩未记录")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }
}

private struct EvaluationCard: View {
    let value: K10Evaluation
    @Bindable var model: AppModel

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                HStack(alignment: .top, spacing: 10) {
                    V3CompanyMark(code: value.companyCode, size: 40)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(value.companyCode).font(NKFont.headline)
                        Text(selectionText).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        V3Pill(text: value.state)
                        V3Pill(text: value.sampleClass)
                    }
                }

                if value.evaluationConfigurationState == "not_configured" {
                    Label("该窗口的评价规则未配置，暂不参与成绩。已发布候选与原定观察窗口保留。", systemImage: "exclamationmark.triangle.fill")
                        .font(NKFont.callout).foregroundStyle(NK.amber)
                }

                marketDayColumns

                VStack(alignment: .leading, spacing: 4) {
                    Text("两日收盘封板：\(value.closeLimitHitAny == true ? "命中" : value.closeLimitHitAny == false ? "未命中" : "待核") · 首次触板：\(value.firstTouchDay.map(k10DisplayTime) ?? "待核")")
                        .font(NKFont.callout)
                    Text("D1 开盘跳空：\(percentage(value.d1OpenGap)) · \(k10ComparabilityText(value.comparability))")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                    PriceChangeSummary(value: value)
                }

                if !value.gaps.isEmpty {
                    Label(value.gaps.map(k10ReasonText).joined(separator: "、"), systemImage: "exclamationmark.triangle.fill")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.amber)
                }

                if !value.factRefs.isEmpty {
                    Divider().overlay(NK.hairline)
                    ForEach(value.factRefs) { SourceReferenceLine(source: $0, model: model) }
                }

                Text(value.revision > 0 ? "结果第 \(value.revision) 版 · 更新 \(k10DisplayTime(value.updatedAt))" : "尚未生成持久成绩 · 状态更新 \(k10DisplayTime(value.updatedAt))")
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textTertiary)
            }
        }
    }

    private var selectionText: String {
        guard let selection = value.selection else { return "选择尚未冻结，不归入三组" }
        return "开盘前冻结：\(k10StatusText(selection.state)) · \(k10DisplayTime(selection.frozenAt))"
    }

    private func percentage(_ number: Double?) -> String {
        number.map { String(format: "%.2f%%", $0 * 100) } ?? "待核"
    }

    @ViewBuilder private var marketDayColumns: some View {
        #if os(macOS)
        HStack(spacing: 8) {
            MarketDayBlock(label: "D1", day: value.d1, model: model)
            MarketDayBlock(label: "D2", day: value.d2, model: model)
        }
        #else
        VStack(spacing: 8) {
            MarketDayBlock(label: "D1", day: value.d1, model: model)
            MarketDayBlock(label: "D2", day: value.d2, model: model)
        }
        #endif
    }
}

private struct MarketDayBlock: View {
    let label: String
    let day: K10MarketDay?
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(label).nkLabel().foregroundStyle(NK.accent)
                Spacer()
                if let day { Text(k10DisplayTime(day.tradeDate)).font(NKFont.caption).foregroundStyle(NK.textSecondary) }
            }
            if let day {
                Text(k10StatusText(day.availability)).font(NKFont.callout.weight(.semibold))
                if day.availability == "available" {
                    Text("开 \(price(day.open)) · 高 \(price(day.high))")
                    Text("低 \(price(day.low)) · 收 \(price(day.close))")
                    Text(k10LimitStatusText(closeLimitUp: day.closeLimitUp, touchedLimitUp: day.touchedLimitUp, firstTouchedAt: day.firstTouchedAt))
                        .foregroundStyle(day.closeLimitUp == true ? NK.accent : NK.textSecondary)
                } else {
                    Text(dayMessage(day.availability)).foregroundStyle(NK.amber)
                }
                if let reason = day.anomalyReason, !reason.isEmpty {
                    Label("异常原因：\(k10AnomalyReasonText(reason))", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(NK.amber)
                }
                if let checks = day.fieldChecks, !checks.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("字段核验").font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.textSecondary)
                        ForEach(checks) { check in
                            Text("\(k10FieldNameText(check.field))：\(fieldCheckText(check))")
                                .foregroundStyle(check.state == "verified" ? NK.textSecondary : NK.amber)
                        }
                    }
                }
                if !day.sourceRefs.isEmpty {
                    ForEach(day.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                }
            } else {
                Text("尚无行情资料").foregroundStyle(NK.textTertiary)
            }
        }
        .font(NKFont.caption)
        .frame(maxWidth: .infinity, minHeight: 128, alignment: .topLeading)
        .padding(10)
        .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }

    private func price(_ value: Double?) -> String { value.map { String(format: "%.2f", $0) } ?? "–" }
    private func dayMessage(_ state: String) -> String {
        switch state {
        case "suspended": return "停牌，未作为未命中"
        case "anomaly": return "行情异常，等待核验"
        case "data_gap": return "行情缺数，等待补齐"
        default: return "状态待核"
        }
    }
    private func fieldCheckText(_ check: K10MarketFieldCheck) -> String {
        let sources = check.sourceValues.map { "\(k10MarketSourceText($0.source))=\(valueText($0.value))" }.joined(separator: "；")
        return "\(k10StatusText(check.state)) · \(k10ReasonText(check.reason))\(sources.isEmpty ? "" : "（\(sources)）")"
    }
    private func valueText(_ value: K10Value?) -> String {
        guard let value else { return "未记录" }
        switch value { case .string(let item): return item; case .number(let item): return String(item); case .bool(let item): return item ? "是" : "否"; case .null: return "未记录"; case .object, .array: return "复合记录" }
    }
}

private struct PriceChangeSummary: View {
    let value: K10Evaluation

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("相对 D1 开盘：D1 高/低/收 \(change(value.d1PriceChanges, "high")) / \(change(value.d1PriceChanges, "low")) / \(change(value.d1PriceChanges, "close"))")
            Text("D2 高/低/收 \(change(value.d2PriceChanges, "high")) / \(change(value.d2PriceChanges, "low")) / \(change(value.d2PriceChanges, "close"))")
            if value.windowPriceChanges != nil {
                Text("两日窗口高/低/收 \(change(value.windowPriceChanges, "high")) / \(change(value.windowPriceChanges, "low")) / \(change(value.windowPriceChanges, "close"))")
            }
        }
        .font(NKFont.caption)
        .foregroundStyle(NK.textSecondary)
    }

    private func change(_ values: [String: Double?]?, _ key: String) -> String {
        guard let value = values?[key] ?? nil else { return "待核" }
        return String(format: "%.2f%%", value * 100)
    }
}
