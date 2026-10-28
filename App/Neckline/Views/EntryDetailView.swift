// 一条晨报的详情。四个问题、风险、各方观点，来源推进原文。
// 旧晨报没有评估内容时，只显示标题、结论和原文。

import SwiftUI

struct EntryDetailView: View {
    let entry: MorningEntry
    var model: MorningModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        scrollContainer
            .morningScreen()
            #if os(iOS)
            .navigationTitle(entry.companyName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.visible, for: .navigationBar)
            #endif
    }

    @ViewBuilder private var scrollContainer: some View {
        #if DEBUG
        if ProcessInfo.processInfo.environment["NK_QA_DETAIL_ANCHOR"] == "risk" {
            ScrollViewReader { proxy in
                scrollView
                    .onAppear { scheduleRiskScroll(proxy) }
            }
        } else {
            scrollView
        }
        #else
        scrollView
        #endif
    }

    private var scrollView: some View {
        ScrollView {
            detail
                .padding(pagePadding)
                .frame(maxWidth: 720, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var detail: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(entry.companyName)
                        .nkScaledFont(.detailName)
                        .foregroundStyle(NKMorning.textPrimary)
                    Text(entry.companyCode)
                        .nkScaledFont(.titleLine)
                        .monospacedDigit()
                        .foregroundStyle(NKMorning.textSecondary)
                }
                meta
                    .nkScaledFont(.caption)
                    .fixedSize(horizontal: false, vertical: true)
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
            if !questionRows.isEmpty {
                sectionTitle(MorningCopy.questionsHeading)
                questionsCard
            }
            if let risk = trimmed(entry.risk) {
                riskBlock(risk)
                    .padding(.top, 14)
                    #if DEBUG
                    .id("nk-qa-risk")
                    #endif
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

    private var meta: Text {
        let type = Text(entry.type).foregroundStyle(NKMorning.textSecondary)
        guard let raw = entry.disclosedRaw else { return type }
        let gap = Text(" · ").foregroundStyle(NKMorning.textTertiary)
        let disclosed = Text(MorningCopy.firstDisclosedLine(raw))
            .foregroundStyle(NKMorning.textSecondary)
            .monospacedDigit()
        return Text("\(type)\(gap)\(disclosed)")
    }

    private var questionRows: [(String, String)] {
        guard let questions = entry.questions else { return [] }
        return [
            (MorningCopy.questionValue, questions.value ?? ""),
            (MorningCopy.questionPriced, questions.priced ?? ""),
            (MorningCopy.questionMoney, questions.money ?? ""),
            (MorningCopy.questionPrice, questions.price ?? ""),
        ].compactMap { label, text in
            guard let text = trimmed(text) else { return nil }
            return (label, text)
        }
    }

    private var questionsCard: some View {
        VStack(spacing: 0) {
            ForEach(Array(questionRows.enumerated()), id: \.offset) { index, row in
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
                if index != questionRows.count - 1 {
                    Rectangle().fill(NKMorning.hairline).frame(height: 1)
                }
            }
        }
        .padding(.horizontal, NKMorningSpace.rowH)
        .morningCard()
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
        (entry.views ?? []).filter { trimmed($0.text) != nil }
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

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(NKMorningFont.caption.weight(.medium))
            .foregroundStyle(NKMorning.textTertiary)
            .padding(.top, 16)
            .padding(.bottom, 6)
    }

    private func sourceRow(_ source: MorningSource) -> some View {
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
            Image(systemName: "chevron.right")
                .font(NKMorningFont.disclosure)
                .foregroundStyle(NKMorning.textTertiary)
        }
        .padding(.horizontal, NKMorningSpace.rowH)
        .padding(.vertical, NKMorningSpace.rowV)
        .contentShape(Rectangle())
    }

    private func trimmed(_ value: String?) -> String? {
        let text = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return text.isEmpty ? nil : text
    }

    #if DEBUG
    private func scheduleRiskScroll(_ proxy: ScrollViewProxy) {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 450_000_000)
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                proxy.scrollTo("nk-qa-risk", anchor: .top)
            }
        }
    }
    #endif

    private var pagePadding: CGFloat {
        #if os(macOS)
        NKMorningSpace.detail
        #else
        NKMorningSpace.page
        #endif
    }
}
