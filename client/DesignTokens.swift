//
//  DesignTokens.swift
//  Neckline — A 股短线量化决策系统
//
//  设计令牌搬自 LinoN(整块复用,§五 阶段4C)。命名空间由 LN 改 NK 避免与 LinoN
//  代码库混淆(两者是完全独立的 App,无编译单元共享)。
//  涨跌色:绿涨红跌(国际惯例 / 用户明确选择,§3.5,勿改回 A 股本地红涨绿跌)。
//

import SwiftUI

// MARK: - Colors

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8)  & 0xff) / 255,
            blue:  Double( hex        & 0xff) / 255,
            opacity: alpha
        )
    }
}

enum NK {
    // 语义色
    static let up      = Color(hex: 0x0FA968)   // 涨 / 已进海选池 / 满额 / 盈利
    static let down    = Color(hex: 0xE5443B)   // 跌 / 不符合 / 破线 / 退潮刹车 / 休息
    static let amber   = Color(hex: 0xE8910A)   // 半额 / 警示 / 待办
    static let accent  = Color(hex: 0x0B6BCB)   // 交互蓝 / 主按钮 / 选中

    // 文本
    static let textPrimary   = Color(hex: 0x1D1D1F)
    static let textSecondary = Color(hex: 0x3C3C43, alpha: 0.55)   // rgba(60,60,67,.55)
    static let textTertiary  = Color(hex: 0x3C3C43, alpha: 0.40)
    static let hairline      = Color(hex: 0x3C3C43, alpha: 0.10)   // 分隔线 / 卡边

    // 背景
    static let cardBg     = Color.white
    static let pageBg     = Color(hex: 0xFBFBFD)
    static let pageBgIOS  = Color(hex: 0xF3F4F7)
    static let fieldBg    = Color(hex: 0xF7F8FA)
    static let chipNeutral = Color(hex: 0x3C3C43, alpha: 0.05)

    // 品牌渐变(◆ 头像 / Logo)
    static let brand = LinearGradient(
        colors: [Color(hex: 0x16A06A), Color(hex: 0x0B6BCB)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    // 退潮刹车 / 高危提醒渐变
    static let alertGrad = LinearGradient(
        colors: [Color(hex: 0xE5443B), Color(hex: 0xE8910A)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
}

// MARK: - Radius / Spacing

enum NKRadius {
    static let card: CGFloat   = 18    // 数据卡(macOS 16,iOS 18)
    static let hero: CGFloat   = 20
    static let field: CGFloat  = 12
    static let glassBar: CGFloat = 26  // 底部玻璃标签栏
    static let sheet: CGFloat  = 28    // bottom sheet 顶圆角
    static let pill: CGFloat   = 999
}

enum NKSpace {
    static let pagePad: CGFloat = 16
    static let cardPad: CGFloat = 18
    static let gap: CGFloat     = 12
}

// MARK: - Typography
// 数字务必加 .monospacedDigit()(对应 HTML tabular-nums)

enum NKFont {
    static let largeTitle = Font.system(size: 30, weight: .heavy)          // 大标题"今日计划/看板…"
    static let heroNumber = Font.system(size: 34, weight: .semibold).monospacedDigit()
    static let price      = Font.system(size: 25, weight: .semibold).monospacedDigit()
    static let priceMac   = Font.system(size: 30, weight: .semibold).monospacedDigit()
    static let stockName  = Font.system(size: 17, weight: .semibold)
    static let body       = Font.system(size: 13.5)
    static let caption    = Font.system(size: 11.5)
    static let chip       = Font.system(size: 11, weight: .bold)
}

// MARK: - Materials
// Liquid Glass 克制使用:仅栏/浮层/锁屏。数据卡用不透明 cardBg。
//   底部 TabBar / 侧栏 / 工具栏: .ultraThinMaterial + 描边 + 内高光
//   锁屏通知:               .regularMaterial(深色壁纸上)
