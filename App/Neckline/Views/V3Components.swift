import SwiftUI

struct V3Card<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content.padding(NKSpace.cardPad)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 1))
            .shadow(color: Color(hex: 0x172B4D, alpha: 0.035), radius: 10, x: 0, y: 4)
    }
}

struct V3Pill: View {
    let text: String
    private var display: String { k10StatusText(text) }
    private var tone: Color {
        if display.contains("撤回") || display.contains("失败") || display.contains("风险") { return NK.down }
        if display.contains("待") || display.contains("缺") || display.contains("迟到") || display.contains("重叠") { return NK.amber }
        if display.contains("完成") || display.contains("封板") { return NK.up }
        return NK.accent
    }
    var body: some View {
        Text(display).font(NKFont.badge).foregroundStyle(tone)
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(tone.opacity(0.065), in: RoundedRectangle(cornerRadius: NKRadius.badge))
            .fixedSize(horizontal: false, vertical: true)
    }
}

struct V3PageHeader: View {
    let title: String
    var subtitle: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(NKFont.title1).foregroundStyle(NK.textPrimary)
            if let subtitle, !subtitle.isEmpty {
                Text(subtitle).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct V3SectionTitle: View {
    let title: String
    var icon: String? = nil
    var body: some View {
        HStack(spacing: 8) {
            if let icon { Image(systemName: icon).foregroundStyle(NK.accent).font(NKFont.headline) }
            Text(title).font(NKFont.headline).foregroundStyle(NK.textPrimary)
        }
    }
}

struct V3CompanyMark: View {
    let code: String
    var size: CGFloat = 44
    var body: some View {
        Image(systemName: "building.2.crop.circle")
            .font(.system(size: size * 0.54, weight: .regular))
            .foregroundStyle(NK.accent)
            .frame(width: size, height: size)
            .background(NK.accent.opacity(0.07), in: RoundedRectangle(cornerRadius: size * 0.24))
            .accessibilityHidden(true)
    }
}

struct V3PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(NKFont.headline)
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.horizontal, 12)
            .foregroundStyle(.white)
            .background(NK.accent.opacity(configuration.isPressed ? 0.82 : 1), in: RoundedRectangle(cornerRadius: NKRadius.control))
            .contentShape(RoundedRectangle(cornerRadius: NKRadius.control))
            .opacity(isEnabled ? 1 : 0.45)
    }
}

struct V3SecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(NKFont.headline)
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.horizontal, 12)
            .foregroundStyle(NK.accent)
            .background(configuration.isPressed ? NK.accent.opacity(0.06) : Color.white, in: RoundedRectangle(cornerRadius: NKRadius.control))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.control).stroke(NK.accent.opacity(0.5), lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: NKRadius.control))
            .opacity(isEnabled ? 1 : 0.45)
    }
}

struct V3EmptyState: View {
    let icon: String
    let title: String
    let message: String
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: icon).font(.system(size: 28, weight: .light))
                .foregroundStyle(NK.accent)
                .frame(width: 64, height: 64)
                .background(NK.accent.opacity(0.055), in: RoundedRectangle(cornerRadius: 18))
            Text(title).font(NKFont.title3).foregroundStyle(NK.textPrimary)
            Text(message).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                .multilineTextAlignment(.center).frame(maxWidth: 340)
        }.frame(maxWidth: .infinity).padding(.horizontal, 24).padding(.vertical, 36)
    }
}

struct K10Loading: View {
    let state: K10LoadState
    var body: some View {
        switch state {
        case .idle, .ready: EmptyView()
        case .loading:
            VStack(spacing: 18) {
                ProgressView().tint(NK.accent)
                Text("正在整理机会与资料").font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
        case .offline(let text), .unavailable(let text), .failed(let text):
            V3EmptyState(icon: "wifi.exclamationmark", title: "暂时无法更新", message: text)
                .frame(maxHeight: .infinity)
        }
    }
}
