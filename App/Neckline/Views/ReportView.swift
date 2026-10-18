// 当期或某一期的名单。macOS 左栏与 iOS 晨报、历史详情共用。

import SwiftUI

struct ReportView: View {
    var slot: MorningSlot
    var model: MorningModel
    var showsDateMenu = false
    #if DEBUG
    @Environment(\.nkQAReviewOpen) private var qaReviewOpen
    @Environment(\.nkQAScrollBottom) private var qaScrollBottom
    #endif

    var body: some View {
        page
            .morningScreen()
            #if os(iOS)
            .navigationTitle(navigationHeading)
            .navigationBarTitleDisplayMode(.large)
            .toolbarTitleDisplayMode(.large)
            .modifier(MorningWindowSubtitle(text: navigationSubheading))
            #else
            .navigationTitle("晨报")
            #endif
            .onAppear {
                Task {
                    await model.reload(slot)
                    if showsDateMenu { await model.reloadHistory() }
                }
            }
    }

    /// 日期菜单作为顶栏：列表滚到它下面时由系统做滚动边缘效果，不再被硬切在菜单正下方。
    @ViewBuilder private var page: some View {
        if showsDateMenu {
            scrollingList
                .safeAreaBar(edge: .top) { dateMenu }
                .scrollEdgeEffectStyle(.hard, for: .top)
        } else {
            VStack(spacing: 0) {
                scrollingList
            }
        }
    }

    @ViewBuilder private var scrollingList: some View {
        #if DEBUG
        if qaReviewOpen || qaScrollBottom {
            ScrollViewReader { proxy in
                reportScroll
                    .onAppear { scheduleReviewScroll(proxy) }
                    .onChange(of: model.state(for: slot)) { _, _ in scheduleReviewScroll(proxy) }
            }
        } else {
            reportScroll
        }
        #else
        reportScroll
        #endif
    }

    private var reportScroll: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let report = model.report(for: slot) {
                    MorningPageHeader(report: report, showsIdentity: showsPageIdentity)
                        .padding(.horizontal, usesCards ? 0 : NKMorningSpace.page)
                }
                listBody
            }
            .padding(.horizontal, usesCards ? NKMorningSpace.page : 0)
            .padding(.top, showsDateMenu ? 4 : 12)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .refreshable { await refresh() }
    }

    #if DEBUG
    private func scheduleReviewScroll(_ proxy: ScrollViewProxy) {
        guard qaReviewOpen || qaScrollBottom, case .ready = model.state(for: slot) else { return }
        let anchor: UnitPoint = qaScrollBottom ? .bottom : .top
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 450_000_000)
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                proxy.scrollTo("nk-qa-review", anchor: anchor)
            }
        }
    }
    #endif

    @ViewBuilder private var listBody: some View {
        switch model.state(for: slot) {
        case .loading:
            MorningLoadingView()
        case .ready(let report):
            entryList(report.content?.entries ?? [], quiet: false)
            reviewAnchor(reviewFold(report))
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

    @ViewBuilder private func reviewAnchor<V: View>(_ view: V) -> some View {
        #if DEBUG
        view.id("nk-qa-review")
        #else
        view
        #endif
    }

    @ViewBuilder private func reviewFold(_ report: MorningReport) -> some View {
        let groups = report.content?.review.listedGroups ?? []
        if !groups.isEmpty {
            MorningFold(title: "待定与旧事", headerInset: foldHeaderInset) {
                VStack(alignment: .leading, spacing: 16) {
                    ForEach(groups) { group in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(group.title)
                                .nkScaledFont(.captionMedium)
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

    private var showsPageIdentity: Bool {
        #if os(iOS)
        false
        #else
        true
        #endif
    }

    private var navigationHeading: String {
        guard let report = model.report(for: slot) else { return "晨报" }
        return MorningCopy.header(tradeDate: report.tradeDate)
    }

    private var navigationSubheading: String {
        guard let report = model.report(for: slot) else { return "" }
        return MorningCopy.windowLine(report: report)
    }

    private var foldHeaderInset: CGFloat {
        #if os(macOS)
        NKMorningSpace.page
        #else
        0
        #endif
    }

    private func status(_ message: MorningMessage) -> some View {
        MorningStatusView(message: message, retry: { Task { await refresh() } }, openSettings: { MorningChrome.openSettings(model) })
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
        .menuIndicator(.hidden)
        #endif
        .fixedSize()
        .padding(.horizontal, NKMorningSpace.page)
        .padding(.top, 12)
        // 列表顶部相应少留 8，没滚动时位置不变；滚动后分隔线不贴着菜单文字。
        .padding(.bottom, 8)
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

private struct MorningWindowSubtitle: ViewModifier {
    var text: String

    func body(content: Content) -> some View {
        #if os(iOS)
        if text.isEmpty {
            content
        } else {
            content.navigationSubtitle(text)
        }
        #else
        content
        #endif
    }
}
