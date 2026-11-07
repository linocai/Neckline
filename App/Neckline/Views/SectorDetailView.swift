// 一条板块热点的详情。①②③各一段；④是承接说明和承接股。

import SwiftUI

struct SectorDetailView: View {
    let entry: MorningSectorEntry
    var model: MorningModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        ScrollView {
            detail
                .padding(pagePadding)
                .frame(maxWidth: 720, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .morningScreen()
        #if os(iOS)
        .navigationTitle(entry.sector.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.visible, for: .navigationBar)
        #endif
    }

    private var detail: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.sector.name)
                    .nkScaledFont(.detailName)
                    .foregroundStyle(NKMorning.textPrimary)
                if !entry.detailMeta.isEmpty {
                    Text(entry.detailMeta)
                        .nkScaledFont(.caption)
                        .foregroundStyle(NKMorning.textSecondary)
                        .monospacedDigit()
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if !entry.displayTitle.isEmpty {
                Text(entry.displayTitle)
                    .nkScaledFont(.detailTitle)
                    .foregroundStyle(NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
            }
            if !entry.displayConclusion.isEmpty {
                Text(entry.displayConclusion)
                    .font(NKMorningFont.reason)
                    .foregroundStyle(NKMorning.textPrimary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)
            }
            if showsQuestions {
                sectionTitle(MorningCopy.questionsHeading)
                questionsCard
            }
            if let risk = trimmed(entry.risk) {
                riskBlock(risk)
                    .padding(.top, 14)
            }
            if !viewItems.isEmpty {
                sectionTitle(MorningCopy.viewsHeading)
                viewsBlock
            }
            if !entry.sources.isEmpty {
                sectionTitle(MorningCopy.sourcesHeading)
                sourcesCard
            }
        }
    }

    private var textQuestions: [(String, String)] {
        [
            (MorningCopy.sectorQuestionFresh, entry.questions.fresh),
            (MorningCopy.sectorQuestionWeight, entry.questions.weight),
            (MorningCopy.sectorQuestionPosition, entry.questions.position),
        ].compactMap { label, text in
            guard let text = trimmed(text) else { return nil }
            return (label, text)
        }
    }

    private var showsCarrierBlock: Bool {
        trimmed(entry.questions.carriers) != nil || !entry.leaders.isEmpty
    }

    private var showsQuestions: Bool {
        !textQuestions.isEmpty || showsCarrierBlock
    }

    private var questionsCard: some View {
        let rows = textQuestions
        let showCarriers = showsCarrierBlock
        return VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.0)
                        .font(NKMorningFont.caption.weight(.semibold))
                        .foregroundStyle(NKMorning.textSecondary)
                    Text(row.1)
                        .font(NKMorningFont.titleLine)
                        .foregroundStyle(NKMorning.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 9)
                if index != rows.count - 1 || showCarriers {
                    Rectangle().fill(NKMorning.hairline).frame(height: 1)
                }
            }
            if showCarriers {
                carriersBlock
            }
        }
        .padding(.horizontal, NKMorningSpace.rowH)
        .morningCard()
    }

    private var carriersBlock: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(MorningCopy.sectorQuestionCarriers)
                .font(NKMorningFont.caption.weight(.semibold))
                .foregroundStyle(NKMorning.textSecondary)
            if let carriers = trimmed(entry.questions.carriers) {
                Text(carriers)
                    .font(NKMorningFont.titleLine)
                    .foregroundStyle(NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(entry.leaders.enumerated()), id: \.offset) { index, leader in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(leader.companyName)
                            .font(NKMorningFont.titleLine.weight(.semibold))
                            .foregroundStyle(NKMorning.textPrimary)
                            .lineLimit(1)
                        Text(leader.companyCode)
                            .font(NKMorningFont.caption)
                            .monospacedDigit()
                            .foregroundStyle(NKMorning.textSecondary)
                            .lineLimit(1)
                            .layoutPriority(1)
                    }
                    if let why = trimmed(leader.why) {
                        Text(why)
                            .font(NKMorningFont.titleLine)
                            .foregroundStyle(NKMorning.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let price = trimmed(leader.price) {
                        Text("价格：\(price)")
                            .font(NKMorningFont.titleLine)
                            .foregroundStyle(NKMorning.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6)
                .padding(.bottom, 4)
                if index != entry.leaders.count - 1 {
                    Rectangle().fill(NKMorning.hairline).frame(height: 1)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 9)
    }

    private func riskBlock(_ risk: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("!")
                .font(NKMorningFont.titleLine.weight(.bold))
                .accessibilityHidden(true)
            Text(MorningCopy.riskLine(risk))
                .font(NKMorningFont.titleLine)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(NKMorning.risk)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NKMorning.riskFill, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var viewItems: [MorningView] {
        entry.views.filter { trimmed($0.text) != nil }
    }

    private var viewsBlock: some View {
        VStack(spacing: 0) {
            ForEach(Array(viewItems.enumerated()), id: \.offset) { index, item in
                viewRow(item)
                if index != viewItems.count - 1 {
                    Rectangle().fill(NKMorning.hairline.opacity(0.45)).frame(height: 0.5)
                }
            }
        }
        .padding(.horizontal, 14)
        .background(NKMorning.viewFill, in: RoundedRectangle(cornerRadius: NKMorningRadius.card, style: .continuous))
    }

    private func viewRow(_ item: MorningView) -> some View {
        let body = VStack(alignment: .leading, spacing: 2) {
            Text(MorningCopy.viewAttribution(by: item.by.label, name: item.name))
                .font(NKMorningFont.tag)
                .foregroundStyle(NKMorning.view.opacity(0.85))
            Text(item.text)
                .font(NKMorningFont.titleLine)
                .foregroundStyle(NKMorning.view)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)

        return Group {
            if let url = item.linkURL {
                Button { openURL(url) } label: { body }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(.isLink)
            } else {
                body
            }
        }
    }

    private var sourcesCard: some View {
        VStack(spacing: 0) {
            ForEach(Array(entry.sources.enumerated()), id: \.offset) { index, source in
                if let documentID = source.documentID {
                    NavigationLink {
                        DocumentView(documentID: documentID, model: model)
                    } label: {
                        sourceRow(source, linked: true)
                    }
                    .buttonStyle(.plain)
                } else {
                    sourceRow(source, linked: false)
                }
                if index != entry.sources.count - 1 {
                    Rectangle().fill(NKMorning.hairline).frame(height: 1)
                }
            }
        }
        .morningCard()
    }

    private func sourceRow(_ source: MorningSectorSource, linked: Bool) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("\(source.sourceLabel) · \(MorningCopy.beijingClock(source.publishedAt))")
                    .font(NKMorningFont.caption)
                    .foregroundStyle(NKMorning.textSecondary)
                    .monospacedDigit()
                    .lineLimit(1)
                if let title = trimmed(source.title) {
                    Text(title)
                        .nkScaledFont(.titleLine)
                        .foregroundStyle(NKMorning.textPrimary)
                        .lineLimit(2)
                        .truncationMode(.tail)
                }
            }
            Spacer(minLength: 8)
            if linked {
                Image(systemName: "chevron.right")
                    .font(NKMorningFont.disclosure)
                    .foregroundStyle(NKMorning.textTertiary)
            }
        }
        .padding(.horizontal, NKMorningSpace.rowH)
        .padding(.vertical, NKMorningSpace.rowV)
        .contentShape(Rectangle())
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(NKMorningFont.caption.weight(.medium))
            .foregroundStyle(NKMorning.textTertiary)
            .padding(.top, 16)
            .padding(.bottom, 6)
    }

    private func trimmed(_ value: String?) -> String? {
        let text = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return text.isEmpty ? nil : text
    }

    private var pagePadding: CGFloat {
        #if os(macOS)
        NKMorningSpace.detail
        #else
        NKMorningSpace.page
        #endif
    }
}
