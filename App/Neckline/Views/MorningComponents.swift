// 晨报共用的行、页眉和空状态。颜色与字号只走 NKMorning。

import SwiftUI

struct MorningPageHeader: View {
    let report: MorningReport

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(MorningCopy.header(tradeDate: report.tradeDate))
                .font(NKMorningFont.pageTitle)
                .foregroundStyle(NKMorning.textPrimary)
            Text(MorningCopy.windowLine(report: report))
                .font(NKMorningFont.caption)
                .foregroundStyle(NKMorning.textSecondary)
                .monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
            if let gap = gapText {
                Text(gap)
                    .font(NKMorningFont.caption)
                    .foregroundStyle(NKMorning.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var gapText: String? {
        let raw = report.gapSummary ?? report.content?.gapSummary
        let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct MorningEntryRow: View {
    let entry: MorningEntry
    var quiet = false
    var selected = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(entry.companyName)
                    .font(NKMorningFont.company)
                    .foregroundStyle(quiet ? NKMorning.textSecondary : NKMorning.textPrimary)
                    .lineLimit(1)
                Text(entry.companyCode)
                    .font(NKMorningFont.company)
                    .monospacedDigit()
                    .foregroundStyle(quiet ? NKMorning.textTertiary : NKMorning.textSecondary)
                    .lineLimit(1)
                    .layoutPriority(1)
                Spacer(minLength: 8)
                MorningTag(type: entry.type, importance: entry.importance)
            }
            Text(entry.headline)
                .font(NKMorningFont.titleLine)
                .foregroundStyle(quiet ? NKMorning.textTertiary : NKMorning.textSecondary)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .padding(.horizontal, horizontalInset)
        .padding(.vertical, NKMorningSpace.rowV)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(selected ? NKMorning.selection : Color.clear)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var horizontalInset: CGFloat {
        #if os(macOS)
        NKMorningSpace.page
        #else
        NKMorningSpace.rowH
        #endif
    }
}

struct MorningTag: View {
    let type: String
    let importance: MorningImportance

    var body: some View {
        HStack(spacing: 0) {
            Text(type)
                .foregroundStyle(NKMorning.textSecondary)
            Text(" · ")
                .foregroundStyle(NKMorning.textTertiary)
            Text(importance.label)
                .foregroundStyle(importance == .high ? NKMorning.accent : NKMorning.textSecondary)
        }
        .font(NKMorningFont.tag)
        .lineLimit(1)
        .fixedSize()
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(NKMorning.tagFill, in: RoundedRectangle(cornerRadius: NKMorningRadius.tag, style: .continuous))
    }
}

struct MorningStatusView: View {
    let message: MorningMessage
    var retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Text(message.title)
                .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textPrimary)
        } description: {
            if let detail = message.detail, !detail.isEmpty {
                Text(detail)
                    .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textSecondary)
                    .multilineTextAlignment(.center)
            }
        } actions: {
            if message.retry {
                Button("重试", action: retry)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 280)
    }
}

struct MorningLoadingView: View {
    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text(MorningCopy.reading)
                .font(NKMorningFont.caption)
                .foregroundStyle(NKMorning.textSecondary)
        }
        .frame(maxWidth: .infinity, minHeight: 280)
    }
}

struct MorningFold<Content: View>: View {
    let title: String
    @ViewBuilder var content: () -> Content
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: NKMorningSpace.expand)) { open.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Text(title)
                        .font(NKMorningFont.caption.weight(.semibold))
                        .foregroundStyle(NKMorning.textSecondary)
                    Spacer(minLength: 8)
                    Image(systemName: open ? "chevron.down" : "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(NKMorning.textTertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                content()
                    .transition(.opacity.combined(with: .offset(y: -6)).combined(with: .scale(scale: 0.98, anchor: .top)))
            }
        }
    }
}

extension MorningReview {
    var listedGroups: [MorningReviewGroup] {
        [
            MorningReviewGroup(title: "类型不在收录清单", entries: types),
            MorningReviewGroup(title: "受益方", entries: relation),
            MorningReviewGroup(title: "看不出是否这段时间首次公开", entries: fresh),
            MorningReviewGroup(title: "判为旧事", entries: old),
        ].filter { !$0.entries.isEmpty }
    }
}

struct MorningReviewGroup: Identifiable {
    let title: String
    let entries: [MorningEntry]
    var id: String { title }
}

extension View {
    func morningCard() -> some View {
        background(NKMorning.card, in: RoundedRectangle(cornerRadius: NKMorningRadius.card, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: NKMorningRadius.card, style: .continuous)
                    .stroke(NKMorning.hairline, lineWidth: 1)
            )
    }

    func morningScreen() -> some View {
        frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(NKMorning.page.ignoresSafeArea())
    }
}
