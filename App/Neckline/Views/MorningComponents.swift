// 晨报共用的行、页眉和空状态。颜色与字号只走 NKMorning。

import SwiftUI

struct MorningPageHeader: View {
    let report: MorningReport
    /// iOS 把标题和窗口行放进导航栏，这里只留缺口摘要。
    var showsIdentity = true

    var body: some View {
        if showsIdentity || gapText != nil {
            VStack(alignment: .leading, spacing: 4) {
                if showsIdentity {
                    Text(MorningCopy.header(tradeDate: report.tradeDate))
                        .nkScaledFont(.pageTitle)
                        .foregroundStyle(NKMorning.textPrimary)
                    Text(MorningCopy.windowLine(report: report))
                        .nkScaledFont(.caption)
                        .foregroundStyle(NKMorning.textSecondary)
                        .monospacedDigit()
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let gap = gapText {
                    Text(gap)
                        .nkScaledFont(.caption)
                        .foregroundStyle(NKMorning.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
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
    #if os(iOS)
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #endif

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            nameCodeAndTag
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

    @ViewBuilder private var nameCodeAndTag: some View {
        #if os(iOS)
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(entry.companyName)
                        .font(NKMorningFont.company)
                        .foregroundStyle(quiet ? NKMorning.textSecondary : NKMorning.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(entry.companyCode)
                        .font(NKMorningFont.company)
                        .monospacedDigit()
                        .foregroundStyle(quiet ? NKMorning.textTertiary : NKMorning.textSecondary)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                        .layoutPriority(1)
                }
                MorningTag(type: entry.type, importance: entry.importance)
            }
        } else {
            standardNameCodeAndTag
        }
        #else
        standardNameCodeAndTag
        #endif
    }

    private var standardNameCodeAndTag: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(entry.companyName)
                .font(NKMorningFont.company)
                .foregroundStyle(quiet ? NKMorning.textSecondary : NKMorning.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
            Text(entry.companyCode)
                .font(NKMorningFont.company)
                .monospacedDigit()
                .foregroundStyle(quiet ? NKMorning.textTertiary : NKMorning.textSecondary)
                .lineLimit(1)
                .layoutPriority(1)
            Spacer(minLength: 8)
            MorningTag(type: entry.type, importance: entry.importance)
                .layoutPriority(1)
        }
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
    var openSettings: () -> Void = {}

    var body: some View {
        ContentUnavailableView {
            Label {
                Text(message.title)
                    .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textPrimary)
            } icon: {
                Image(systemName: message.icon)
                    .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textSecondary)
            }
        } description: {
            if let detail = message.detail, !detail.isEmpty {
                Text(detail)
                    .foregroundStyle(NKMorning.textSecondary)
                    .multilineTextAlignment(.center)
            }
        } actions: {
            if message.retry {
                Button("重试", action: retry)
            }
            if message.settings {
                Button("去设置", action: openSettings)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 280)
    }
}

enum MorningChrome {
    @MainActor
    static func openSettings(_ model: MorningModel) {
        #if os(iOS)
        model.tab = .settings
        #else
        NSApp.activate(ignoringOtherApps: true)
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        #endif
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
    var headerInset: CGFloat = 0
    @ViewBuilder var content: () -> Content
    @State private var open = false
    #if DEBUG
    @Environment(\.nkQAReviewOpen) private var qaReviewOpen
    #endif

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
                        .font(NKMorningFont.disclosure)
                        .foregroundStyle(NKMorning.textTertiary)
                }
                .padding(.horizontal, headerInset)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                content()
                    .transition(.opacity.combined(with: .offset(y: -6)).combined(with: .scale(scale: 0.98, anchor: .top)))
            }
        }
        #if DEBUG
        .onAppear {
            guard qaReviewOpen, !open else { return }
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) { open = true }
        }
        #endif
    }
}

#if DEBUG
private struct NKQAReviewOpenKey: EnvironmentKey {
    static let defaultValue = false
}

private struct NKQAScrollBottomKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var nkQAReviewOpen: Bool {
        get { self[NKQAReviewOpenKey.self] }
        set { self[NKQAReviewOpenKey.self] = newValue }
    }

    var nkQAScrollBottom: Bool {
        get { self[NKQAScrollBottomKey.self] }
        set { self[NKQAScrollBottomKey.self] = newValue }
    }
}
#endif

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
        modifier(MorningScreenModifier())
    }
}

private struct MorningScreenModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            #if os(iOS)
            // 底色交给导航容器，才能铺到导航栏与悬浮标签栏后面；视图自己的 background 只盖安全区内，栏后会露白。
            .containerBackground(NKMorning.page, for: .navigation)
            // 默认在导航栏下沿是一道硬边白底；柔和边缘让滚上去的内容渐隐在同一底色里。
            .scrollEdgeEffectStyle(.soft, for: .top)
            #else
            .background { NKMorning.page }
            #endif
    }
}
