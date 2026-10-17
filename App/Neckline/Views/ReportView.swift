// 当期或某一期的名单。macOS 左栏与 iOS 晨报、历史详情共用。

import SwiftUI

struct ReportView: View {
    var slot: MorningSlot
    var model: MorningModel
    var showsDateMenu = false
    var hidesNavigationBar = false

    var body: some View {
        VStack(spacing: 0) {
            if showsDateMenu { dateMenu }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let report = model.report(for: slot) {
                        MorningPageHeader(report: report)
                            .padding(.horizontal, usesCards ? 0 : NKMorningSpace.page)
                    }
                    listBody
                }
                .padding(.horizontal, usesCards ? NKMorningSpace.page : 0)
                .padding(.top, 12)
                .padding(.bottom, 28)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .refreshable { await refresh() }
        }
        .morningScreen()
        .navigationTitle("晨报")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(hidesNavigationBar ? .hidden : .visible, for: .navigationBar)
        #endif
        .onAppear {
            Task {
                await model.reload(slot)
                if showsDateMenu { await model.reloadHistory() }
            }
        }
    }

    @ViewBuilder private var listBody: some View {
        switch model.state(for: slot) {
        case .loading:
            MorningLoadingView()
        case .ready(let report):
            entryList(report.content?.entries ?? [], quiet: false)
            reviewFold(report)
        case .empty(let report):
            if let message = model.message(for: .empty(report)) {
                status(message)
            }
            reviewFold(report)
        case .failed, .running, .error:
            if let message = model.message(for: model.state(for: slot)) {
                status(message)
            }
        }
    }

    @ViewBuilder private func reviewFold(_ report: MorningReport) -> some View {
        let groups = report.content?.review.listedGroups ?? []
        if !groups.isEmpty {
            MorningFold(title: "待定与旧事") {
                VStack(alignment: .leading, spacing: 16) {
                    ForEach(groups) { group in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(group.title)
                                .font(NKMorningFont.caption.weight(.medium))
                                .foregroundStyle(NKMorning.textTertiary)
                                .padding(.horizontal, groupInset)
                            entryList(group.entries, quiet: true)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private func entryList(_ entries: [MorningEntry], quiet: Bool) -> some View {
        VStack(spacing: usesCards ? 10 : 0) {
            ForEach(entries, id: \.listID) { entry in
                entryControl(entry, quiet: quiet)
                if !usesCards, entry.listID != entries.last?.listID {
                    Rectangle().fill(NKMorning.hairline).frame(height: 1)
                }
            }
        }
    }

    @ViewBuilder private func entryControl(_ entry: MorningEntry, quiet: Bool) -> some View {
        #if os(macOS)
        if showsDateMenu {
            Button { model.select(entry) } label: {
                MorningEntryRow(entry: entry, quiet: quiet, selected: model.selectedEntryKey == entry.listID)
            }
            .buttonStyle(.plain)
        } else {
            NavigationLink { EntryDetailView(entry: entry, model: model) } label: {
                MorningEntryRow(entry: entry, quiet: quiet)
            }
            .buttonStyle(.plain)
        }
        #else
        NavigationLink { EntryDetailView(entry: entry, model: model) } label: {
            MorningEntryRow(entry: entry, quiet: quiet)
                .morningCard()
        }
        .buttonStyle(.plain)
        #endif
    }

    private var usesCards: Bool {
        #if os(macOS)
        false
        #else
        true
        #endif
    }

    private var groupInset: CGFloat {
        #if os(macOS)
        NKMorningSpace.page
        #else
        NKMorningSpace.rowH
        #endif
    }

    private func status(_ message: MorningMessage) -> some View {
        MorningStatusView(message: message) { Task { await refresh() } }
    }

    private func refresh() async {
        await model.reload(slot)
        if showsDateMenu { await model.reloadHistory() }
    }

    private var dateMenu: some View {
        Menu {
            Button {
                Task { await model.open(.latest) }
            } label: {
                menuLabel("最新", chosen: model.focus == .latest)
            }
            ForEach(model.history) { item in
                Button {
                    Task { await model.open(.date(item.tradeDate)) }
                } label: {
                    menuLabel(MorningCopy.historyTitle(tradeDate: item.tradeDate), chosen: model.focus == .date(item.tradeDate))
                }
            }
        } label: {
            HStack(spacing: 4) {
                Text(menuTitle)
                    .font(NKMorningFont.caption.weight(.medium))
                Image(systemName: "chevron.down")
                    .font(.system(size: 10, weight: .semibold))
            }
            .foregroundStyle(NKMorning.accent)
        }
        #if os(macOS)
        .menuStyle(.borderlessButton)
        #endif
        .fixedSize()
        .padding(.horizontal, NKMorningSpace.page)
        .padding(.top, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var menuTitle: String {
        if case .date(let tradeDate) = model.focus {
            return MorningCopy.historyTitle(tradeDate: tradeDate)
        }
        return "最新"
    }

    @ViewBuilder private func menuLabel(_ title: String, chosen: Bool) -> some View {
        if chosen {
            Label(title, systemImage: "checkmark")
        } else {
            Text(title)
        }
    }
}
