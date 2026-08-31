import SwiftUI

struct CheckListView: View {
    @Bindable var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if model.checklistLoading { ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40) }
            else if let checklist = model.checklist { checklistBody(checklist) }
            else { NKEmptyState(title: "尚无次日核对表", subtitle: model.checklistMissing ?? "等待对应 K9-v3 成绩包的 9:29 核对。", systemImage: "checklist") }
        }
    }

    private func checklistBody(_ checklist: Checklist) -> some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            NKCard { VStack(alignment: .leading, spacing: 7) {
                HStack { NKSectionHeader(title: "次日核对表"); Spacer(); NKChip(text: "K9-v3", tone: .info) }
                Text("选股日 \(NKFmt.reportDate(checklist.selectionDate)) · 信号交易日 \(NKFmt.reportDate(checklist.signalTradeDate))")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                Text(checklist.footnote).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }}
            ForEach(checklist.segments) { segment in
                NKCard { VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: segment.label)
                    if segment.rows.isEmpty { Text("暂无标的").font(NKFont.callout).foregroundStyle(NK.textTertiary) }
                    ForEach(segment.rows) { row in checklistRow(row) }
                }}
            }
        }
    }

    private func checklistRow(_ row: ChecklistRow) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack { Text(row.displayName).font(NKFont.headline).foregroundStyle(NK.textPrimary); Text(row.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary); Spacer(); NKChip(text: row.playbookLabel, tone: .neutral) }
            NKWrapRow(spacing: 5, lineSpacing: 5) {
                ForEach(row.channels, id: \.self) { channel in NKChip(text: "\(nkChannelLabel(channel)) · 第 \(row.channelRanks[channel] ?? 0)", tone: .info) }
            }
            if !row.readings.isNull { NKJSONTable(value: row.readings) }
        }
    }
}

#if os(macOS)
struct CheckListRail: View {
    @Bindable var model: AppModel
    var body: some View {
        if let checklist = model.checklist {
            ForEach(checklist.segments) { segment in
                Text(segment.label).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                ForEach(segment.rows) { row in Text(row.displayName).font(NKFont.callout).foregroundStyle(NK.textPrimary) }
            }
        } else { Text(model.checklistMissing ?? "等待核对表").font(NKFont.callout).foregroundStyle(NK.textTertiary) }
    }
}
#else
struct CheckListRail: View { @Bindable var model: AppModel; var body: some View { EmptyView() } }
#endif
