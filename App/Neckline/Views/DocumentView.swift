// 一篇原文。有链接时用系统浏览器打开。

import SwiftUI

struct DocumentView: View {
    let documentID: String
    var model: MorningModel
    @State private var phase: Phase = .loading

    var body: some View {
        Group {
            switch phase {
            case .loading:
                MorningLoadingView()
            case .failed(let error):
                MorningStatusView(message: failureMessage(error), retry: { Task { await load() } }, openSettings: { MorningChrome.openSettings(model) })
            case .loaded(let document):
                article(document)
            }
        }
        .morningScreen()
        .navigationTitle(navigationHeading)
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.visible, for: .navigationBar)
        #endif
        .task(id: documentID) { await load() }
    }

    private func article(_ document: MorningDocument) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(MorningCopy.documentHeading(title: document.title, text: document.text, sourceLabel: document.sourceLabel))
                    .nkScaledFont(.pageTitle)
                    .foregroundStyle(NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(sourceLine(document))
                    .nkScaledFont(.caption)
                    .foregroundStyle(NKMorning.textSecondary)
                    .monospacedDigit()
                Text(document.text)
                    .font(NKMorningFont.document)
                    .foregroundStyle(NKMorning.textPrimary)
                    .lineSpacing(8)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                if let url = externalURL(document.url) {
                    Link("原始链接", destination: url)
                        .buttonStyle(.bordered)
                }
            }
            .padding(pagePadding)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func load() async {
        phase = .loading
        do {
            let document = try await model.fetchDocument(id: documentID)
            guard !Task.isCancelled else { return }
            phase = .loaded(document)
        } catch is CancellationError {
            return
        } catch let error as MorningClientError {
            guard !Task.isCancelled else { return }
            phase = .failed(error)
        } catch {
            guard !Task.isCancelled else { return }
            phase = .failed(.unreadable)
        }
    }

    private var navigationHeading: String {
        if case .loaded(let document) = phase {
            return MorningCopy.sourceName(document.sourceLabel)
        }
        return "原文"
    }

    private func failureMessage(_ error: MorningClientError) -> MorningMessage {
        .forClientError(error)
    }

    private func sourceLine(_ document: MorningDocument) -> String {
        let stamp = document.publishedAt ?? document.fetchedAt
        guard let stamp, !stamp.isEmpty else { return document.sourceLabel }
        return "\(document.sourceLabel) · \(MorningCopy.beijingClock(stamp))"
    }

    private func externalURL(_ raw: String?) -> URL? {
        guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, let url = URL(string: raw) else { return nil }
        return url
    }

    private var pagePadding: CGFloat {
        #if os(macOS)
        NKMorningSpace.detail
        #else
        NKMorningSpace.page
        #endif
    }
}

extension DocumentView {
    enum Phase {
        case loading
        case loaded(MorningDocument)
        case failed(MorningClientError)
    }
}
