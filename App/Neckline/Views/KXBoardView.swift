// iOS 的 KX 看板（底部小字 macOS 共用）：页眉与分段控件、固定的列头、三个区的行、底部小字。刷新时只换数据，不换视图，滚动位置和分组都保持。

import SwiftUI

#if os(iOS)
struct KXBoardView: View {
    @Bindable var kx: KXBoardModel
    var openSettings: () -> Void = {}

    var body: some View {
        VStack(spacing: 0) {
            header
            content
        }
        .frame(maxWidth: contentMaxWidth)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background { NKMorning.page.ignoresSafeArea() }
    }

    /// 截图验收可以用 NK_QA_KX_WIDTH 把看板收窄到最小的 iPhone 宽度（375），在较宽的模拟器上也能看窄屏的排版。
    private var contentMaxWidth: CGFloat {
        #if DEBUG
        if let raw = ProcessInfo.processInfo.environment["NK_QA_KX_WIDTH"], let width = Double(raw), width > 0 {
            return CGFloat(width)
        }
        #endif
        return .infinity
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("KX")
                    .font(NKKXFont.title)
                    .tracking(0.2)
                    .foregroundStyle(NKMorning.textPrimary)
                Spacer(minLength: 8)
                if let board = kx.board {
                    Text(KXCopy.meta(board: board, refreshFailed: kx.refreshFailed))
                        .font(NKKXFont.meta)
                        .foregroundStyle(NKMorning.textSecondary)
                        .monospacedDigit()
                }
            }
            if kx.board != nil {
                NKSegmentedControl(
                    options: KXColumnGroup.allCases.map { (value: $0, title: $0.title) },
                    selection: $kx.group
                )
                .padding(.top, 10)
            }
        }
        .padding(.horizontal, KXMetrics.inset)
        .padding(.top, 4)
        .padding(.bottom, 8)
    }

    @ViewBuilder private var content: some View {
        switch kx.state {
        case .loading:
            scrolling { MorningLoadingView() }
        case .error(let error):
            scrolling {
                MorningStatusView(message: KXCopy.message(for: error), retry: { Task { await kx.refresh() } }, openSettings: openSettings)
            }
        case .ready(let board):
            if board.rows.isEmpty {
                scrolling {
                    MorningStatusView(message: KXCopy.emptyPoolMessage, retry: { Task { await kx.refresh() } })
                }
            } else {
                VStack(spacing: 0) {
                    KXColumnHeader(group: kx.group)
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(board.sections, id: \.section) { part in
                                KXSectionHeader(section: part.section, count: part.rows.count)
                                ForEach(part.rows, id: \.code) { row in
                                    KXRowView(row: row, group: kx.group)
                                }
                            }
                        }
                    }
                    .refreshable { await kx.refresh() }
                    KXFootView(lines: KXCopy.footerLines(board: board))
                }
            }
        }
    }

    private func scrolling<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        ScrollView { content() }
            .refreshable { await kx.refresh() }
    }
}

/// 列头：与当前分组的数据列对齐，贴在列表上方。
struct KXColumnHeader: View {
    let group: KXColumnGroup

    var body: some View {
        HStack(spacing: 0) {
            Text("名称")
                .frame(maxWidth: .infinity, alignment: .leading)
            ForEach(group.columns, id: \.self) { column in
                Text(column.heading(in: group))
                    .lineLimit(1)
                    .fixedSize()
                    .frame(minWidth: column.minWidth, alignment: .trailing)
                    .fixedSize(horizontal: true, vertical: false)
                    .padding(.leading, column.leadingGap)
            }
        }
        .font(NKKXFont.columnHead)
        .foregroundStyle(NKMorning.textTertiary)
        .padding(.horizontal, KXMetrics.inset)
        .padding(.top, 6)
        .padding(.bottom, 4)
        .background(NKMorning.page)
        .overlay(alignment: .bottom) {
            Rectangle().fill(NKMorning.hairline).frame(height: KXMetrics.hairline)
        }
        .accessibilityHidden(true)
    }
}

/// 区头：区名在左，只数在右。「全部」另写一句排序依据。
struct KXSectionHeader: View {
    let section: KXSection
    let count: Int

    var body: some View {
        HStack {
            Text(section.title)
                .font(NKKXFont.section)
                .foregroundStyle(NKMorning.textSecondary)
            Spacer(minLength: 8)
            Text(countText)
                .font(NKKXFont.sectionCount)
                .foregroundStyle(NKMorning.textTertiary)
                .monospacedDigit()
        }
        .padding(.horizontal, KXMetrics.inset)
        .padding(.top, 10)
        .padding(.bottom, 4)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private var countText: String {
        section == .all ? "\(count) 只 · 按涨跌幅" : "\(count) 只"
    }
}
#endif

/// 底部小字：名单版本与核对时间、报价时间、最后收到推荐的时间。
struct KXFootView: View {
    let lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(lines.indices, id: \.self) { index in
                Text(lines[index])
            }
        }
        .font(NKKXFont.foot)
        .lineSpacing(3)
        .foregroundStyle(NKMorning.textTertiary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, KXMetrics.inset)
        .padding(.vertical, 8)
        .background(NKMorning.page)
        .overlay(alignment: .top) {
            Rectangle().fill(NKMorning.hairline).frame(height: KXMetrics.hairline)
        }
    }
}
