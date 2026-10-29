import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

extension Color {
    /// 浅色 / 暗色各一套，随系统外观切换。
    init(light lightHex: UInt, dark darkHex: UInt, alpha: Double = 1) {
        self.init(light: lightHex, lightAlpha: alpha, dark: darkHex, darkAlpha: alpha)
    }

    /// 浅色 / 暗色各一套，两套的透明度可以不同。
    init(light lightHex: UInt, lightAlpha: Double, dark darkHex: UInt, darkAlpha: Double) {
        #if os(macOS)
        self.init(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let hex = isDark ? darkHex : lightHex
            return NSColor(srgbRed: Self.channel(hex, shift: 16),
                            green: Self.channel(hex, shift: 8),
                            blue: Self.channel(hex, shift: 0),
                            alpha: isDark ? darkAlpha : lightAlpha)
        }))
        #else
        self.init(uiColor: UIColor { traits in
            let isDark = traits.userInterfaceStyle == .dark
            let hex = isDark ? darkHex : lightHex
            return UIColor(red: Self.channel(hex, shift: 16),
                            green: Self.channel(hex, shift: 8),
                            blue: Self.channel(hex, shift: 0),
                            alpha: isDark ? darkAlpha : lightAlpha)
        })
        #endif
    }

    private static func channel(_ hex: UInt, shift: UInt) -> CGFloat {
        CGFloat((hex >> shift) & 255) / 255
    }
}

/// 晨报界面。浅色与暗色都完整。
enum NKMorning {
    static let page = Color(light: 0xF5F6F8, dark: 0x1C1C1E)
    static let card = Color(light: 0xFFFFFF, dark: 0x2C2C2E)
    static let textPrimary = Color(light: 0x1C1C1E, dark: 0xF5F5F7)
    static let textSecondary = Color(light: 0x6E6E73, dark: 0x98989D)
    static let textTertiary = Color(light: 0x8E8E93, dark: 0x636366)
    static let hairline = Color(light: 0xE5E5EA, dark: 0x38383A)
    static let accent = Color(light: 0x0754E9, dark: 0x4C8DFF)
    static let problem = Color(light: 0xD70015, dark: 0xFF453A)
    static let selection = Color(light: 0x0754E9, dark: 0x4C8DFF, alpha: 0.12)
    static let tagFill = Color(light: 0xF2F2F7, dark: 0x3A3A3C)
    /// 风险正文。系统红，深浅色自动换。
    static let risk = Color.red
    /// 风险浅底。浅色约 8%，深色约 14%。
    static let riskFill = Color.adaptiveSystemFill(red: true, lightAlpha: 0.08, darkAlpha: 0.14)
    /// 各方观点正文。系统蓝，深浅色自动换。
    static let view = Color.blue
    /// 观点浅底。浅色约 7%，深色约 14%。
    static let viewFill = Color.adaptiveSystemFill(red: false, lightAlpha: 0.07, darkAlpha: 0.14)
}

extension Color {
    /// 系统红或系统蓝的浅底，两套外观透明度不同。
    static func adaptiveSystemFill(red: Bool, lightAlpha: Double, darkAlpha: Double) -> Color {
        #if os(macOS)
        Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let alpha = isDark ? darkAlpha : lightAlpha
            let base = red ? NSColor.systemRed : NSColor.systemBlue
            var resolved = base
            appearance.performAsCurrentDrawingAppearance {
                resolved = base.usingColorSpace(.sRGB) ?? base
            }
            return resolved.withAlphaComponent(alpha)
        }))
        #else
        Color(uiColor: UIColor { traits in
            let alpha = traits.userInterfaceStyle == .dark ? darkAlpha : lightAlpha
            let base: UIColor = red ? .systemRed : .systemBlue
            return base.resolvedColor(with: traits).withAlphaComponent(alpha)
        })
        #endif
    }
}

/// KX 看板。涨红跌绿平灰是 A 股惯例；今日、昨日推荐的底色和竖条用晨报的强调色加透明度。取值与样图一致。
enum NKKX {
    static let up = Color(light: 0xE0312B, dark: 0xFF5A4E)
    static let down = Color(light: 0x139A5B, dark: 0x30C67C)
    static let flat = Color(light: 0x8E8E93, dark: 0x8E8E93)
    static let todayFill = Color(light: 0x0754E9, lightAlpha: 0.10, dark: 0x4C8DFF, darkAlpha: 0.20)
    static let ydayFill = Color(light: 0x0754E9, lightAlpha: 0.04, dark: 0x4C8DFF, darkAlpha: 0.08)
    static let bar = NKMorning.accent
    static let ydayBarOpacity = 0.35
    /// 热度徽标第 5 天起的浅红底（浅色、深色同一个值，样图如此）；首日用强调色描边，第 2 到 4 天用 NKMorning.tagFill。
    static let heatHotFill = Color(light: 0xE0312B, lightAlpha: 0.12, dark: 0xE0312B, darkAlpha: 0.12)
    /// 涨跌幅色块上的字。
    static let pillText = Color.white
    /// 分段控件选中项的投影。
    static let segmentShadow = Color.black.opacity(0.12)
}

/// KX 看板的字号。看板一行要放下一整组数字，磅值固定，不随动态字体放大。
enum NKKXFont {
    static let title = Font.system(size: 28, weight: .semibold)
    static let meta = Font.system(size: 12)
    static let segment = Font.system(size: 13)
    static let segmentOn = Font.system(size: 13, weight: .semibold)
    static let columnHead = Font.system(size: 11)
    static let section = Font.system(size: 12, weight: .semibold)
    static let sectionCount = Font.system(size: 12)
    static let name = Font.system(size: 15, weight: .medium)
    static let nameToday = Font.system(size: 15, weight: .bold)
    static let code = Font.system(size: 11).monospacedDigit()
    static let cell = Font.system(size: 15).monospacedDigit()
    static let cellBold = Font.system(size: 15, weight: .bold).monospacedDigit()
    static let pill = Font.system(size: 13.5, weight: .semibold).monospacedDigit()
    static let ratio = Font.system(size: 12).monospacedDigit()
    static let rangeNote = Font.system(size: 10).monospacedDigit()
    static let foot = Font.system(size: 10.5)
    /// 热度徽标与阶段标签。
    static let badge = Font.system(size: 10.5, weight: .semibold).monospacedDigit()
    /// 手机第二行的题材。
    static let theme = Font.system(size: 12)
}

/// 多行文字的字号规格。只在这里写磅值、相对的文本样式和字重。
struct NKScaledFontSpec {
    var size: CGFloat
    var relativeTo: Font.TextStyle
    var weight: Font.Weight

    static let pageTitle = NKScaledFontSpec(size: 28, relativeTo: .title, weight: .semibold)
    static let caption = NKScaledFontSpec(size: 13, relativeTo: .footnote, weight: .regular)
    static let captionMedium = NKScaledFontSpec(size: 13, relativeTo: .footnote, weight: .medium)
    static let company = NKScaledFontSpec(size: 16, relativeTo: .callout, weight: .semibold)
    static let titleLine = NKScaledFontSpec(size: 15, relativeTo: .subheadline, weight: .regular)
    static let detailName = NKScaledFontSpec(size: 20, relativeTo: .title2, weight: .semibold)
    static let detailTitle = NKScaledFontSpec(size: 19, relativeTo: .headline, weight: .semibold)
}

enum NKMorningFont {
    #if os(iOS)
    // 系统文本样式在默认字号下与原先的固定磅值相同，并跟随系统字体大小。
    static let pageTitle = Font.title.weight(.semibold)
    static let caption = Font.footnote
    static let company = Font.callout.weight(.semibold)
    static let titleLine = Font.subheadline
    /// 详情页公司名。
    static let detailName = Font.title2.weight(.semibold)
    /// 详情页事件标题。
    static let detailTitle = Font.headline.weight(.semibold)
    static let tag = Font.caption2.weight(.medium)
    static let reason = Font.subheadline
    static let document = Font.callout
    static let disclosure = Font.caption.weight(.semibold)
    #else
    static let pageTitle = Font.system(size: NKScaledFontSpec.pageTitle.size, weight: NKScaledFontSpec.pageTitle.weight)
    static let caption = Font.system(size: NKScaledFontSpec.caption.size, weight: NKScaledFontSpec.caption.weight)
    static let company = Font.system(size: NKScaledFontSpec.company.size, weight: NKScaledFontSpec.company.weight)
    static let titleLine = Font.system(size: NKScaledFontSpec.titleLine.size, weight: NKScaledFontSpec.titleLine.weight)
    /// 详情页公司名。
    static let detailName = Font.system(size: NKScaledFontSpec.detailName.size, weight: NKScaledFontSpec.detailName.weight)
    /// 详情页事件标题。
    static let detailTitle = Font.system(size: NKScaledFontSpec.detailTitle.size, weight: NKScaledFontSpec.detailTitle.weight)
    static let tag = Font.system(size: 11, weight: .medium)
    static let reason = Font.system(size: 15)
    static let document = Font.system(size: 16)
    static let disclosure = Font.system(size: 12, weight: .semibold)
    #endif
}

extension View {
    /// 多行不用文本样式：样式自带额外行距和中文断行，默认字号下也会少排字、行距更大。
    func nkScaledFont(_ spec: NKScaledFontSpec) -> some View {
        modifier(NKScaledFont(spec: spec))
    }
}

private struct NKScaledFont: ViewModifier {
    var weight: Font.Weight
    #if os(iOS)
    @ScaledMetric private var size: CGFloat

    init(spec: NKScaledFontSpec) {
        weight = spec.weight
        _size = ScaledMetric(wrappedValue: spec.size, relativeTo: spec.relativeTo)
    }
    #else
    private var size: CGFloat

    init(spec: NKScaledFontSpec) {
        weight = spec.weight
        size = spec.size
    }
    #endif

    func body(content: Content) -> some View {
        content.font(.system(size: size, weight: weight))
    }
}

enum NKMorningSpace {
    static let page: CGFloat = 20
    static let detail: CGFloat = 28
    static let rowV: CGFloat = 10
    static let rowH: CGFloat = 16
    static let expand: TimeInterval = 0.2
}

enum NKMorningRadius {
    static let card: CGFloat = 12
    static let tag: CGFloat = 5
}
