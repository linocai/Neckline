// 一条利好的详情。来源推进原文；others 只展示。

import SwiftUI

struct EntryDetailView: View {
    let entry: MorningEntry
    var model: MorningModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(entry.companyName)
                        .font(NKMorningFont.company)
                        .foregroundStyle(NKMorning.textPrimary)
                    Text(entry.companyCode)
                        .font(NKMorningFont.company)
                        .monospacedDigit()
                        .foregroundStyle(NKMorning.textSecondary)
                }
                meta
                    .font(NKMorningFont.caption)
                    .fixedSize(horizontal: false, vertical: true)
                Text(entry.headline)
                    .font(NKMorningFont.detailTitle)
                    .foregroundStyle(NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(entry.reason)
                    .font(NKMorningFont.reason)
                    .foregroundStyle(NKMorning.textPrimary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
                if !entry.sources.isEmpty {
                    sectionTitle("来源")
                    VStack(spacing: 0) {
                        ForEach(entry.sources, id: \.documentId) { source in
                            NavigationLink {
                                DocumentView(documentID: source.documentId, model: model)
                            } label: {
                                sourceRow(source)
                            }
                            .buttonStyle(.plain)
                            if source.documentId != entry.sources.last?.documentId {
                                Rectangle().fill(NKMorning.hairline).frame(height: 1)
                            }
                        }
                    }
                    .morningCard()
                }
                if !entry.others.isEmpty {
                    sectionTitle("其他")
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(Array(entry.others.enumerated()), id: \.offset) { _, other in
                            Text("\(other.type) · \(other.headline)")
                                .font(NKMorningFont.titleLine)
                                .foregroundStyle(NKMorning.textSecondary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, NKMorningSpace.rowH)
                    .padding(.vertical, NKMorningSpace.rowV)
                    .morningCard()
                }
            }
            .padding(pagePadding)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .morningScreen()
        #if os(iOS)
        .navigationTitle(entry.companyName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.visible, for: .navigationBar)
        #endif
    }

    private var meta: Text {
        let type = Text(entry.type).foregroundStyle(NKMorning.textSecondary)
        let gap = Text(" · ").foregroundStyle(NKMorning.textTertiary)
        let importance = Text(entry.importance.label)
            .foregroundStyle(entry.importance == .high ? NKMorning.accent : NKMorning.textSecondary)
        guard let first = entry.firstDisclosed?.trimmingCharacters(in: .whitespacesAndNewlines), !first.isEmpty else {
            return Text("\(type)\(gap)\(importance)")
        }
        let disclosed = Text(MorningCopy.historyTitle(tradeDate: first))
            .foregroundStyle(NKMorning.textSecondary)
            .monospacedDigit()
        return Text("\(type)\(gap)\(importance)\(gap)\(disclosed)")
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(NKMorningFont.caption.weight(.medium))
            .foregroundStyle(NKMorning.textTertiary)
    }

    private func sourceRow(_ source: MorningSource) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("\(source.sourceLabel) · \(MorningCopy.beijingClock(source.publishedAt))")
                    .font(NKMorningFont.caption)
                    .foregroundStyle(NKMorning.textSecondary)
                    .monospacedDigit()
                    .lineLimit(1)
                if let title = source.title?.trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty {
                    Text(title)
                        .font(NKMorningFont.titleLine)
                        .foregroundStyle(NKMorning.textPrimary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(NKMorning.textTertiary)
        }
        .padding(.horizontal, NKMorningSpace.rowH)
        .padding(.vertical, NKMorningSpace.rowV)
        .contentShape(Rectangle())
    }

    private var pagePadding: CGFloat {
        #if os(macOS)
        NKMorningSpace.detail
        #else
        NKMorningSpace.page
        #endif
    }
}
