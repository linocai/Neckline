// iOS 历史标签。点进某一期复用名单页。

import SwiftUI

struct HistoryView: View {
    var model: MorningModel

    var body: some View {
        Group {
            if !model.historyLoaded && model.history.isEmpty && model.historyError == nil {
                MorningLoadingView()
            } else if model.history.isEmpty, let error = model.historyError {
                MorningStatusView(message: MorningMessage(title: MorningCopy.text(for: error), detail: MorningCopy.detail(for: error), problem: false, retry: true)) {
                    Task { await model.reloadHistory() }
                }
            } else if model.history.isEmpty {
                MorningStatusView(message: MorningMessage(title: MorningCopy.noReport, detail: MorningCopy.noReportDetail, problem: false, retry: true)) {
                    Task { await model.reloadHistory() }
                }
            } else {
                list
            }
        }
        .morningScreen()
        .navigationTitle("历史")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        #endif
        .onAppear { Task { await model.reloadHistory() } }
    }

    private var list: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("历史")
                    .font(NKMorningFont.pageTitle)
                    .foregroundStyle(NKMorning.textPrimary)
                VStack(spacing: 0) {
                    ForEach(model.history) { item in
                        NavigationLink {
                            ReportView(slot: .date(item.tradeDate), model: model)
                        } label: {
                            historyRow(item)
                        }
                        .buttonStyle(.plain)
                        if item.tradeDate != model.history.last?.tradeDate {
                            Rectangle().fill(NKMorning.hairline).frame(height: 1).padding(.leading, NKMorningSpace.rowH)
                        }
                    }
                }
                .morningCard()
            }
            .padding(NKMorningSpace.page)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .refreshable { await model.reloadHistory() }
    }

    private func historyRow(_ item: MorningHistoryItem) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(MorningCopy.historyTitle(tradeDate: item.tradeDate))
                    .font(NKMorningFont.company)
                    .foregroundStyle(NKMorning.textPrimary)
                    .monospacedDigit()
                Text(MorningCopy.historySubtitle(status: item.status, entryCount: item.entryCount))
                    .font(NKMorningFont.caption)
                    .foregroundStyle(NKMorning.textSecondary)
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
}
