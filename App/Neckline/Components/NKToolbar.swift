//
//  NKToolbar.swift
//  Neckline — V2.3 视觉升级:macOS 统一工具栏(规范 §05 / §06)
//
//  🔴 **左侧 240px 导航栏整个去掉**(规范 §01 决定 01):板块进工具栏,
//  窗口宽度全部还给内容。⛔ 别把侧栏加回来 —— 它与胶囊是同一组导航的两种形态。
//
//  🔴 **齿轮不做成第四个胶囊** —— 设置**是入口不是板块**(裁定 11 沿用 V2.1 裁定 #2,
//  「设置在产品语义上不算板块」)。它沉在右端,与三个胶囊之间隔开。
//

import SwiftUI

#if os(macOS)

struct NKToolbar: View {
    @Bindable var model: AppModel
    /// 现役板块胶囊(设置不在内 —— 它是右端那个齿轮)。
    /// 🔴 **三板块 = 选股 / 成绩 / 复盘**(裁定 11)。⛔ 别把持仓加回来。
    private let tabs: [AppTab] = [.selection, .scoreboard, .review]

    /// 工具栏高度(macOS 原型 23 行 `height:50px`)。红绿灯要在这个高度里居中。
    static let barHeight: CGFloat = 50
    /// 工具栏左右内边距(原型 23 行 `padding:0 14px`)。红绿灯的左起点也用它。
    static let barPadH: CGFloat = 14

    var body: some View {
        HStack(spacing: 10) {
            // 左:红绿灯 → Logo + 字标 → 分隔线 → 三个板块胶囊
            //
            // ⚠ **V2.3.1**:窗口已 `.hiddenTitleBar`(§〇c 硬伤 1),红绿灯是**系统按钮
            // 浮在这一条栏上**,所以这里仍然只能占位;`NKTrafficLightAligner` 负责把系统
            // 那三颗挪到 50pt 栏的垂直中线、左起点对齐 `barPadH`。
            // **宽度 60 = 系统三颗按钮的真实跨度**(14pt 框 × 3,节距 23 → 14…74)。
            // 配合外层 HStack 的 10pt spacing,Logo 落在 x=84 —— 与原型逐点相同
            // (原型 23/26 行:padding 14 + 圆点组 52 + flex gap 10 + margin-left 8 = 84)。
            Color.clear.frame(width: 60, height: 1)
                .background(NKTrafficLightAligner(barHeight: Self.barHeight))
                #if DEBUG
                // QA/截图钩子(`NKDevCapture`,Release 里整份文件不存在)。挂在这里只因为
                // 它需要一个能拿到 `NSWindow` 的落点,**与工具栏本身无关**;缺环境变量时
                // 什么都不做。
                .background(NKDevCaptureHook().frame(width: 0, height: 0))
                #endif
            HStack(spacing: 7) {
                NKLogo(size: 20)
                Text("Neckline").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textPrimary)
            }
            Divider().frame(height: 20).overlay(NK.hairline)   // 原型 35 行 height:20
            HStack(spacing: 2) { ForEach(tabs) { tabPill($0) } }  // 原型 37 行 gap:2

            Spacer(minLength: 12)

            // 右端只保留全局操作；选股状态属于选股工作台，不在工具栏重复。
            refreshButton
            gearButton
        }
        .padding(.horizontal, Self.barPadH)
        .frame(height: Self.barHeight)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) { Divider().overlay(NK.hairline) }
        // 🔴 **拖窗只挂在这一条栏上**(§〇c 硬伤 1 的第 ① 件事)。
        // ⛔ **不许改用 `NSWindow.isMovableByWindowBackground = true`** —— 那是整窗生效的
        // 开关,会让列表行、卡片、刻度尺**处处都能拖窗**,一次没点准的点选就把窗口拖跑了。
        // `.gesture`(非 `.simultaneousGesture`)让子视图的按钮优先:胶囊 / 刷新 / 齿轮
        // 照常可点,只有空白处起拖。
        .gesture(WindowDragGesture())
    }

    // MARK: - 板块胶囊(选中 = 白底 + 1px 投影 + .5px 描边)

    private func tabPill(_ tab: AppTab) -> some View {
        let active = model.view == tab
        return Button { model.view = tab } label: {
            HStack(spacing: 6) {                       // 原型 nav() 1789 行 gap:6
                // 🔴 图标与文字**着色不同**(原型 navIcon() 1798 行):选中图标是**蓝的**
                // (`#0B6BCB`),不是跟着文字变深 —— V2.3.0 把两者绑成同一个
                // `foregroundStyle`,选中态就少了这一记提示。
                Image(systemName: tab.systemImage).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(active ? NK.accent : NK.textTertiary)
                    .frame(width: 14)                  // 原型 navIcon() width:14
                Text(tab.title).font(NKFont.callout)
                    .fontWeight(active ? .semibold : .regular)
                    .foregroundStyle(active ? NK.textPrimary : NK.textSecondary)
                // ⛔ V2.4.0 P0:原先退潮刹车时持仓那枚带一颗红点,随退潮判级退役删除。
            }
            // 原型 nav() 1789 行 `padding:5px 11px 5px 9px`(左 9 / 右 11,刻意不对称:
            // 左边挨着图标、右边挨着文字或计数徽标)。
            .padding(.leading, 9).padding(.trailing, 11).padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: NKRadius.control)
                    .fill(active ? NK.cardBg : Color.clear)
                    .shadow(color: active ? Color.black.opacity(0.10) : .clear, radius: 1, y: 0.5)
            )
            .overlay(
                RoundedRectangle(cornerRadius: NKRadius.control)
                    .stroke(active ? NK.hairline : Color.clear, lineWidth: 0.5)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - 当前报告的身份(双日期 + 参数包版本)

    /// 报告日 · 交易日 · 参数包版本。**缺哪个就不写哪个**,⛔ 不用占位符冒充。
    ///
    /// 🔴 **双日期都写出来**(LRN-20260816-001):`reportDate` 管标题 / 推送 / 可见身份,
    /// `tradeDate` 管 EOD 读数 / 清单 / 审计键 —— 周日报告两者不同,只印一个会让人
    /// 以为周日那份用的是周日的行情。两者相同时收成一个,不啰嗦。
    @ViewBuilder
    private var metaLine: some View {
        if !metaParts.isEmpty {
            Text(metaParts.joined(separator: " · "))
                .font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textSecondary)
                .help("报告日 · 行情交易日 · 参数包版本")
        }
    }

    private var metaParts: [String] {
        var parts: [String] = []
        let report = NKToolbar.mmdd(model.selection.reportDate)
        let trade = NKToolbar.mmdd(model.selection.tradeDate)
        if !report.isEmpty && !trade.isEmpty && report != trade {
            parts.append("\(report) · 行情 \(trade)")
        } else if !report.isEmpty {
            parts.append(report)
        } else if !trade.isEmpty {
            parts.append(trade)
        }
        // 🔴 参数包版本:**没有就说没有**(那是「参数未配置」的日子,裁定 5)——
        // ⛔ 不留空、⛔ 不写一个占位版本号。
        if let v = model.selection.paramsPackageVersion, !v.isEmpty {
            parts.append(v)
        } else if model.selectionLoaded {
            parts.append("参数未配置")
        }
        return parts
    }

    static func mmdd(_ raw: String) -> String {
        guard raw.count == 8 else { return raw }
        return "\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    // MARK: - 刷新(按钮上直接显示上次更新时刻)+ 齿轮

    private var refreshButton: some View {
        // 🔴 V2.4.0 P3.6:`refresh(for: model.view)` 只刷**当前 Tab**——三个板块
        // 各自的工具栏按钮语义不同了,⛔ 不再是"刷新报告/持仓/盘中动态"一把梭。
        Button { Task { await model.refresh(for: model.view) } } label: {
            // 原型 67–70 行:`gap:5; padding:5px 10px; radius:7; background:#0B6BCB` +
            // 11px 图标 + `11.5/600 #fff`。⛔ 去胶囊。
            HStack(spacing: 5) {
                if model.isLoadingCurrentTab {
                    ProgressView().controlSize(.mini).tint(.white)
                } else {
                    Image(systemName: "arrow.clockwise").font(.system(size: 11, weight: .semibold))
                }
                // ⚠ 还没成功刷新过就不写时刻(⛔ 不拿"现在"冒充)。
                Text(model.lastRefreshedAt.map(NKToolbar.hhmm) ?? "刷新")
                    .font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.accent))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(model.isLoadingCurrentTab)
        .help("刷新当前板块;按钮上是本机上次成功刷新的时刻")
    }

    /// 🔴 **齿轮是入口不是板块** —— ⛔ 别把它做成第四个胶囊。
    private var gearButton: some View {
        // 原型 `gearBtn()`(1802 行):`28×28 / radius 7`,**选中时有白底 + 投影 + .5px 描边**
        // (与板块胶囊同一套选中语言,只是没有文字)。V2.3.0 是 24×24 且选中只变色,
        // 点开设置后看不出"我在这儿"。
        let active = model.view == .settings
        return Button { model.view = .settings } label: {
            Image(systemName: "gearshape")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(active ? NK.accent : NK.textSecondary)
                .frame(width: 28, height: 28)
                .background(
                    RoundedRectangle(cornerRadius: NKRadius.control)
                        .fill(active ? NK.cardBg : Color.clear)
                        .shadow(color: active ? Color.black.opacity(0.10) : .clear,
                                radius: 1, y: 0.5)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: NKRadius.control)
                        .stroke(active ? NK.hairline : Color.clear, lineWidth: 0.5)
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("设置")
    }

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f
    }()

    static func hhmm(_ d: Date) -> String { timeFormatter.string(from: d) }
}

// MARK: - 红绿灯垂直居中(§〇c 硬伤 1 的第 ② 件事)

/// 把三颗系统窗口按钮(红绿灯)在 **50pt 自建工具栏**里垂直居中。
///
/// 🔴 **为什么必须自己挪**:`.windowStyle(.hiddenTitleBar)` 只是把系统标题栏**变透明**,
/// 那三颗按钮仍然钉在**标准标题栏 28pt 的垂直中线**上(≈ 距窗口顶 14),而我们的工具栏
/// 是 50pt(中线 25)—— 不挪就是**偏上 11pt**,与 Logo / 板块胶囊不在一排,正是原型
/// 23–27 行要求的「同排」对不上的那一处。
///
/// **做法 = 逐颗改 `frame.origin`**(本机实测的真实几何,⛔ 别照搬网上那些"拉高
/// `NSTitlebarView`"的写法):`NSTitlebarView` 高 32、顶边与窗口顶重合,三颗按钮
/// 14×14 钉在它的**左下角** `(9, 9)` —— AppKit 坐标系不翻转,所以**把父视图拉高只会
/// 把按钮推得离顶更远**(实测 32→50 时按钮中线从距顶 16 掉到 34,方向正好反了)。
/// 直接算 `origin.y` 才对:`origin.y = 容器高 − 栏高/2 − 按钮高/2`
/// (32 − 25 − 7 = 0,落在容器内、不会被裁)。
/// ⚠ AppKit 在窗口尺寸变化 / 进出全屏后会重排,所以监听通知**重贴一次**。
struct NKTrafficLightAligner: NSViewRepresentable {
    let barHeight: CGFloat

    func makeNSView(context: Context) -> NSView { Aligner(barHeight: barHeight) }
    func updateNSView(_ nsView: NSView, context: Context) { (nsView as? Aligner)?.align() }

    final class Aligner: NSView {
        private let barHeight: CGFloat

        init(barHeight: CGFloat) {
            self.barHeight = barHeight
            super.init(frame: .zero)
        }
        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError("init(coder:) 不用") }

        deinit { NotificationCenter.default.removeObserver(self) }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let w = window else { return }
            align()
            // 窗口尺寸变化 / 进出全屏 / 重新成为主窗口后 AppKit 会重排,重贴一次。
            for name in [NSWindow.didResizeNotification,
                         NSWindow.didBecomeKeyNotification,
                         NSWindow.didExitFullScreenNotification] {
                NotificationCenter.default.addObserver(
                    self, selector: #selector(align), name: name, object: w)
            }
        }

        @objc func align() {
            guard let w = window else { return }
            let buttons = [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton]
                .compactMap { w.standardWindowButton($0) }
            guard let container = buttons.first?.superview else { return }
            // 垂直:三颗都落到 50pt 栏的中线(容器顶 = 窗口顶,坐标系不翻转)。
            // 水平:第一颗对齐工具栏左内边距 14(原型 23 行 `padding:0 14px`),
            // 颗间距沿用系统自己的节距 —— 三颗按钮是**系统控件**,尺寸不可改
            // (原型是 12px 圆点 / gap 8,系统是 14pt 框 / 节距 23,见对照表「刻意不同 · 平台差异」)。
            let pitch = buttons.count > 1
                ? buttons[1].frame.origin.x - buttons[0].frame.origin.x
                : 23
            for (i, b) in buttons.enumerated() {
                let targetY = container.bounds.height - barHeight / 2 - b.frame.height / 2
                let targetX = NKToolbar.barPadH + CGFloat(i) * pitch
                guard abs(b.frame.origin.y - targetY) > 0.5
                        || abs(b.frame.origin.x - targetX) > 0.5 else { continue }
                b.setFrameOrigin(CGPoint(x: targetX, y: targetY))
            }
        }
    }
}

#endif
