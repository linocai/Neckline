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
                    Text(MorningCopy.listMeta(report: report))
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
    var selected = false
    #if os(iOS)
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #endif

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            nameCodeAndTag
            if !entry.displayTitle.isEmpty {
                Text(entry.displayTitle)
                    .font(NKMorningFont.titleLine)
                    .foregroundStyle(NKMorning.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            if !entry.displayConclusion.isEmpty {
                Text(entry.displayConclusion)
                    .font(NKMorningFont.caption)
                    .foregroundStyle(NKMorning.textSecondary)
                    .lineLimit(2)
                    .truncationMode(.tail)
            }
        }
        .padding(.horizontal, NKMorningSpace.rowH)
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
                        .foregroundStyle(NKMorning.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(entry.companyCode)
                        .font(NKMorningFont.caption)
                        .monospacedDigit()
                        .foregroundStyle(NKMorning.textSecondary)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                        .layoutPriority(1)
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    MorningTag(type: entry.type)
                    Spacer(minLength: 8)
                    timeLabel
                }
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
                .foregroundStyle(NKMorning.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
            Text(entry.companyCode)
                .font(NKMorningFont.caption)
                .monospacedDigit()
                .foregroundStyle(NKMorning.textSecondary)
                .lineLimit(1)
                .layoutPriority(1)
            MorningTag(type: entry.type)
                .layoutPriority(1)
            Spacer(minLength: 8)
            timeLabel
                .layoutPriority(2)
        }
    }

    @ViewBuilder private var timeLabel: some View {
        if let raw = entry.disclosedRaw {
            Text(MorningCopy.listStamp(raw))
                .font(NKMorningFont.caption)
                .monospacedDigit()
                .foregroundStyle(NKMorning.textTertiary)
                .lineLimit(1)
        }
    }
}

struct MorningTag: View {
    let type: String

    var body: some View {
        Text(type)
            .font(NKMorningFont.tag)
            .foregroundStyle(NKMorning.textSecondary)
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
            if message.showsIcon {
                Label {
                    Text(message.title)
                        .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textPrimary)
                } icon: {
                    Image(systemName: message.icon)
                        .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textSecondary)
                }
            } else {
                Text(message.title)
                    .foregroundStyle(message.problem ? NKMorning.problem : NKMorning.textPrimary)
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

#if DEBUG
private struct NKQAScrollBottomKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var nkQAScrollBottom: Bool {
        get { self[NKQAScrollBottomKey.self] }
        set { self[NKQAScrollBottomKey.self] = newValue }
    }
}
#endif

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
