//
//  NKFormKit.swift
//  Neckline — **弹层壳 + 分组字段卡**(V2.3.1 批 5 新增)。
//
//  🔴 **为什么单起一个文件、而不是继续用 `Form`**:四个弹层与设置四屏在原型里
//  (`Neckline 弹层.dc.html` 全文 + `Neckline macOS.dc.html` 1577–1887)是**同一套形状**
//  —— 白卡 + `.5px` 细线分隔的键值行 + 卡外一枚 `label` 档小标题;而 SwiftUI 的
//  `Form(.grouped)` 给的是**系统自己的**分组样式(圆角 / 页边距 / 分隔线 / 标题字号
//  全由系统定),逐项对不到原型的 inline style。⛔ 不是"嫌 `Form` 不好看",是它
//  的这些值**改不了**。
//
//  ⚠ **iOS 侧继续用 `Form`**(批 7 才做 iOS 逐屏比对):本文件的组件全部 `#if os(macOS)`
//  之外也能编,但**目前只有 macOS 分支在用**。⛔ 别顺手把 iOS 也切过来 —— 那属于批 7。
//

import SwiftUI

// MARK: - 服务端文案里的 Markdown

/// 🔴 **服务端的人读文案里是带 `**加粗**` 的**(`custom_alerts.py::QUOTE_DELAY_DISCLOSURE`
/// 原文:「行情来自免费实时源(新浪 / 腾讯),`**有延迟**`且非逐笔…」),而
/// `Text(String)` **不解析 Markdown** —— 直接插进去,星号会原样印在界面上
/// (V2.3.1 批 5 实拍在**七项确认卡第 ⑥ 行**逮到,§五 〇d 第 7 条的服务端版本)。
///
/// 把服务端字符串包成 `LocalizedStringKey` 才走 Markdown 解析。
/// ⚠ **只用在"服务端确实在写 Markdown"的那几处**(确认卡七项);⛔ 别无差别套到
/// 所有服务端字符串上 —— 那会让任何含 `*` / `[` 的正常文本被当成标记吃掉。
func nkServerMarkdown(_ s: String) -> LocalizedStringKey { LocalizedStringKey(s) }

// MARK: - 弹层壳(四个弹层共用:标题条 + 内容区)

/// 弹层的共同形状(`Neckline 弹层.dc.html` 22 行原文:「56px 标题条(取消 / 标题 /
/// 主操作)+ 内容区分组 + 底部说明」)。
///
/// ⚠ **高度取 52,不是 22 行那句话里的 56**:原型自己的 inline style 是
/// `height:52px`(31 / 76 / 133 / 190 行四个弹层逐字相同),而 22 行是**同一份原型的
/// 散文说明**。§五 〇a-A 定死「对不上时以原型的 **inline style** 为准」。
///
/// 取消 / 主操作两侧**固定宽 60**(原型 32 / 34 行 `width:60px`)—— 固定宽才能让中间
/// 标题真正落在**弹层的水平中线**上;用 `Spacer()` 会让标题随两侧文字长度左右漂。
struct NKSheetShell<Content: View>: View {
    let title: String
    /// 左上角。四个弹层里三个是「取消」,补充说明那个是「跳过」(那一步本来就可以不做)。
    var cancelTitle: String = "取消"
    /// 右上角主操作:「提交」/「记下」/「确认创建」/「保存」。
    let primaryTitle: String
    var primaryDisabled: Bool = false
    let onCancel: () -> Void
    let onPrimary: () -> Void
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            titleBar
            ScrollView {
                VStack(alignment: .leading, spacing: NKSheet.contentGap) {
                    content
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(NKSheet.contentPad)
            }
        }
        .background(NK.pageBg)
    }

    private var titleBar: some View {
        HStack(spacing: 0) {
            Button(action: onCancel) {
                Text(cancelTitle).font(NKFont.body).foregroundStyle(NK.accent)
                    .frame(width: 60, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Text(title).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                .frame(maxWidth: .infinity)
            Button(action: onPrimary) {
                Text(primaryTitle).font(NKFont.body).fontWeight(.semibold)
                    .foregroundStyle(primaryDisabled ? NK.textTertiary : NK.accent)
                    .frame(width: 60, alignment: .trailing)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(primaryDisabled)
        }
        .padding(.horizontal, 16)
        .frame(height: NKSheet.titleBarHeight)
        // 原型 `background:rgba(250,250,252,.94)` —— 比内容区(`#F6F6F8`)亮一档的
        // 半透明条。⛔ 不用材质:弹层里再叠一层玻璃在桌面密度下只会糊。
        .background(Color(hex: 0xFAFAFC))
        .overlay(alignment: .bottom) {
            Rectangle().fill(NK.hairline).frame(height: 0.5)
        }
    }
}

enum NKSheet {
    /// 原型 31 行 `height:52px`(⚠ 见 `NKSheetShell` 注释:不是散文里那个 56)。
    static let titleBarHeight: CGFloat = 52
    /// 内容区 `padding:16px`(原型 36 行)。
    static let contentPad: CGFloat = 16
    /// 内容区分组间距 `gap:14px`(同上)。
    static let contentGap: CGFloat = 14
}

// MARK: - 分组字段卡(白卡 + `.5px` 细线分隔的若干行)

/// 原型里每一处「一张白卡里若干行、行间一条 `.5px` 细线」的容器
/// (`background:#fff; border:.5px solid rgba(60,60,67,.12); border-radius:12px;
/// overflow:hidden`)。
///
/// ⚠ 分隔线**必须通栏**(`overflow:hidden` + 行自己带 `border-bottom`),⛔ 不是
/// SwiftUI `Divider()` 那种带内缩的线 —— 内缩会让卡看起来像列表而不是一张表。
struct NKFieldCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
            .clipShape(RoundedRectangle(cornerRadius: NKRadius.card))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card)
                .stroke(NK.hairline, lineWidth: 0.5))
    }
}

/// 卡内通栏细线(`border-top:.5px`)。⛔ 别换成 `Divider()`(它有内缩)。
struct NKFieldSeparator: View {
    var body: some View { Rectangle().fill(NK.hairline).frame(height: 0.5) }
}

/// 卡内一行的通用容器:只管**页边距**,内容自便。
///
/// 原型的行内边距不是一个值:设置屏 `14px 18px`(1620 行起)/ 版本行 `13px 18px` /
/// 路由行 `10px 18px` / 推送行 `11px 18px` / 弹层 `13px 15px` / 确认卡 `9px 15px`。
/// **刻意参数化** —— 它们表达的密度不同(键值行比开关行紧),⛔ 别"统一"成一个值。
struct NKFieldRow<Content: View>: View {
    var v: CGFloat = 13
    var h: CGFloat = 18
    var background: Color? = nil
    var alignment: VerticalAlignment = .center
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: alignment, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, v).padding(.horizontal, h)
            .background(background ?? Color.clear)
    }
}

/// 键值行的**左列**(定宽标签)。原型 `font-size:12.5–13.5px; color:rgba(60,60,67,.75)`
/// + `width:76/88/110px; flex:none`。
struct NKFieldLabel: View {
    let text: String
    var width: CGFloat = 110
    var body: some View {
        Text(text).font(NKFont.body)
            .foregroundStyle(NK.textPrimary.opacity(0.75))
            .frame(width: width, alignment: .leading)
    }
}

/// 卡**外**那一枚小标题(`font-size:11px; font-weight:700; letter-spacing:.5px;
/// color:rgba(60,60,67,.40)`)。就近对齐 `label` 档(10.5/700/+.5)。
struct NKGroupLabel: View {
    let text: String
    var body: some View {
        Text(text).nkLabel().foregroundStyle(NK.textTertiary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - 开关(原型是**绿色**药丸,不是系统蓝)

/// 原型 `width:34px; height:20px; border-radius:999px`,开 = `#0FA968`、
/// 关 = `rgba(60,60,67,.20)`,旋钮 `16px` 白圆 + `0 1px 2px rgba(0,0,0,.2)`。
/// Provider 弹层那一档更大:`42×25`,旋钮 `21px`(原型 219 / 221 行)。
///
/// ⚠ **为什么不用 `Toggle(.switch)`**:系统开关在 macOS 上是**强调色(蓝)**、尺寸也
/// 由系统定,与原型的绿色 34×20 对不上,且这两个数在原型里是**刻意分两档**的。
struct NKSwitch: View {
    @Binding var isOn: Bool
    var width: CGFloat = 34
    var height: CGFloat = 20

    private var knob: CGFloat { height - 4 }

    var body: some View {
        Button { isOn.toggle() } label: {
            ZStack(alignment: isOn ? .trailing : .leading) {
                Capsule().fill(isOn ? NK.up : NK.textTertiary.opacity(0.5))
                    .frame(width: width, height: height)
                Circle().fill(Color.white)
                    // 阴影恒定、不参与动画(全局三禁之一)。
                    .shadow(color: Color.black.opacity(0.2), radius: 1, y: 1)
                    .frame(width: knob, height: knob)
                    .padding(.horizontal, 2)
            }
            .frame(width: width, height: height)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 分段控件(环境切换)

/// 原型 1622–1625 行:外壳 `background:rgba(60,60,67,.06); border-radius:8px; padding:2px`,
/// 每段 `padding:5px 18px; border-radius:6px; font-size:12.5px`,选中 = **白底 + 600 +
/// `0 1px 2px rgba(0,0,0,.10)`**。
///
/// ⚠ 系统 `Picker(.segmented)` 的选中态是强调色填充,与"白底浮起"是两种视觉语言。
struct NKSegmented<T: Hashable>: View {
    let options: [(value: T, label: String)]
    @Binding var selection: T

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options, id: \.value) { opt in
                let on = opt.value == selection
                Button { selection = opt.value } label: {
                    Text(opt.label)
                        .font(NKFont.callout).fontWeight(on ? .semibold : .regular)
                        .foregroundStyle(on ? NK.textPrimary : NK.textPrimary.opacity(0.65))
                        .padding(.horizontal, 18).padding(.vertical, 5)
                        .background(
                            RoundedRectangle(cornerRadius: 6)
                                .fill(on ? NK.cardBg : Color.clear)
                                .shadow(color: on ? Color.black.opacity(0.10) : .clear,
                                        radius: 1, y: 1)
                        )
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(RoundedRectangle(cornerRadius: NKRadius.control)
            .fill(NK.textTertiary.opacity(0.15)))
    }
}

// MARK: - 可点标签(离场原因九码 / 补充说明七码)

/// 原型 55–65 行(离场原因)与 172–176 行(七枚标签的方块是另一种,见 `NKCheckSquare`)。
/// 未选 = `background:#fff; border:.5px rgba(60,60,67,.16); radius 7; padding:6px 12px`;
/// **已选按语义着色**(止损 = `NK.down` 实底白字)。
struct NKTagButton: View {
    let text: String
    var selected: Bool = false
    var selectedTone: NKAxisTone = .info
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(text)
                .font(NKFont.callout).fontWeight(selected ? .semibold : .regular)
                .foregroundStyle(selected ? Color.white : NK.textPrimary.opacity(0.75))
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 7)
                    .fill(selected ? selectedTone.color : NK.cardBg))
                .overlay(RoundedRectangle(cornerRadius: 7)
                    .stroke(selected ? Color.clear : NK.textTertiary.opacity(0.4), lineWidth: 0.5))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// 补充说明的**两列可点方块**(原型 133 行起):`padding:10px 12px; border-radius:9px`,
/// 选中 = `rgba(11,107,203,.08)` 底 + `.5px rgba(11,107,203,.35)` 描边 + 实心圆勾;
/// 未选 = 白底 + `.5px rgba(60,60,67,.14)` + 空心圆(`inset 0 0 0 1.3px`)。
struct NKCheckSquare: View {
    let text: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                ZStack {
                    if selected {
                        Circle().fill(NK.accent).frame(width: 15, height: 15)
                        Image(systemName: "checkmark").font(.system(size: 8, weight: .bold))
                            .foregroundStyle(Color.white)
                    } else {
                        Circle().stroke(NK.textTertiary.opacity(0.62), lineWidth: 1.3)
                            .frame(width: 15, height: 15)
                    }
                }
                .frame(width: 15, height: 15)
                Text(text).font(NKFont.body).fontWeight(selected ? .semibold : .regular)
                    .foregroundStyle(selected ? NK.textPrimary : NK.textPrimary.opacity(0.75))
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner)
                .fill(selected ? NK.accent.opacity(0.08) : NK.cardBg))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                .stroke(selected ? NK.accent.opacity(0.35) : NK.textTertiary.opacity(0.35),
                        lineWidth: 0.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 输入框(白底 / 淡底 + `.5px` 描边,⛔ 不用系统 `.textFieldStyle`)

/// 原型的输入框是 `padding:8–12px 11–15px; border-radius:8–11px; background:#FAFAFB 或 #fff;
/// border:.5px solid rgba(60,60,67,.14)`。系统 `.roundedBorder` 给的是另一套形状。
///
/// ⚠ **`bordered:false` 是键值行里那一档**:原型的卡内键值行(弹层 44 / 194 行)把值画成
/// **裸文字**、没有输入框外框 —— 再套一层框会让一张卡里出现"框中框",密度当场翻倍。
/// 有外框的只有真正的独立输入区(baseURL 覆盖 / token / 一句话说明)。
struct NKTextFieldBox: View {
    let placeholder: String
    @Binding var text: String
    var mono: Bool = false
    var secure: Bool = false
    var multiline: Bool = false
    var minHeight: CGFloat? = nil
    var filled: Bool = true      // true = `#FAFAFB` 淡底;false = 纯白
    var bordered: Bool = true
    /// 键值行里的值原型是 `15px/600 tabular`(弹层 45 行);默认走正文档。
    var emphasized: Bool = false

    private var font: Font {
        if emphasized { return NKFont.headline.monospacedDigit() }
        return mono ? NKFont.body.monospaced() : NKFont.body
    }

    var body: some View {
        Group {
            if secure {
                SecureField(placeholder, text: $text)
            } else if multiline {
                TextField(placeholder, text: $text, axis: .vertical).lineLimit(3...6)
            } else {
                TextField(placeholder, text: $text)
            }
        }
        .textFieldStyle(.plain)
        .font(font)
        .foregroundStyle(NK.textPrimary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(minHeight: minHeight, alignment: .topLeading)
        .padding(.horizontal, bordered ? 12 : 0).padding(.vertical, bordered ? 9 : 0)
        .background(RoundedRectangle(cornerRadius: NKRadius.control)
            .fill(bordered ? (filled ? NK.disclosureBg : NK.cardBg) : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
            .stroke(bordered ? NK.textTertiary.opacity(0.35) : Color.clear, lineWidth: 0.5))
    }
}

// MARK: - 描边按钮 / 虚线按钮(连接自检 / 新增 Provider / 解析成规则)

/// 蓝描边小按钮(原型 1661 行:`padding:8px 15px; radius 8; border:.5px rgba(11,107,203,.35)`)。
/// ⚠ **常态无底色**(原型那句 `.06` 淡蓝底是 `style-hover`,不是常态)。
struct NKOutlineButton: View {
    let title: String
    var systemImage: String? = nil
    var tone: Color = NK.accent
    var busy: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if busy {
                    ProgressView().controlSize(.small)
                } else if let s = systemImage {
                    Image(systemName: s).font(.system(size: 12, weight: .semibold))
                }
                Text(title).font(NKFont.callout).fontWeight(.semibold)
            }
            .foregroundStyle(tone)
            .padding(.horizontal, 15).padding(.vertical, 8)
            .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                .stroke(tone.opacity(0.35), lineWidth: 0.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// 行内下拉(任务路由的「哪个 Provider」)。
///
/// 🔴 **⛔ 不用 `Picker` + `.labelsHidden()`**:实拍逮到 —— 它在自绘的 `HStack` 行里
/// 会把**隐藏的 label 残留渲染到窗口最左侧**(设置 · Provider 屏的列表栏里凭空多出
/// 两枚 chevron)。`Menu` 自绘 label 没有这个副作用,且形状能对到原型的
/// 「值 + 极小 chevron」(1691 行右端只是一段 `12.5px .55` 的文字)。
struct NKInlineMenu: View {
    let options: [(value: String, label: String)]
    @Binding var selection: String

    private var currentLabel: String {
        options.first(where: { $0.value == selection })?.label ?? selection
    }

    var body: some View {
        Menu {
            ForEach(options, id: \.value) { o in
                Button(o.label) { selection = o.value }
            }
        } label: {
            HStack(spacing: 4) {
                Text(currentLabel).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.system(size: 8, weight: .semibold))
                    .foregroundStyle(NK.textTertiary)
            }
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

/// 虚线描边按钮(原型 1687 行「新增 Provider」/ 信息卡 275 行「审计视图」)。
struct NKDashedButton: View {
    let title: String
    var systemImage: String? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let s = systemImage {
                    Image(systemName: s).font(.system(size: 11, weight: .semibold))
                }
                Text(title).font(NKFont.callout).fontWeight(.semibold)
            }
            .foregroundStyle(NK.textPrimary.opacity(0.65))
            .padding(.horizontal, 15).padding(.vertical, 11)
            .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                .stroke(style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))
                .foregroundStyle(NK.textTertiary.opacity(0.55)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 提示块(琥珀 / 蓝 / 灰,原型三种底色的小说明块)

/// 原型里贴在某个字段**下方**的一句话:`font-size:11px; color:#E8910A`(琥珀警示)
/// 或 `rgba(60,60,67,.50)`(灰说明)。
///
/// 🔴 **文案类型是 `LocalizedStringKey` 不是 `String`**:这些句子里满是 `**加粗**`
/// (§五 〇d 第 7 条:`Text(String)` **不解析 Markdown**,星号会原样印上屏)。
struct NKInlineNote: View {
    let text: LocalizedStringKey
    var tone: NKAxisTone = .neutral

    var body: some View {
        Text(text)
            .font(NKFont.caption)
            .lineSpacing(3)
            .foregroundStyle(tone == .neutral ? NK.textTertiary : tone.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 带底色的说明块(原型 68 / 130 / 232 行:琥珀 `rgba(232,145,10,.06)` + `.20` 描边;
/// 蓝 `rgba(11,107,203,.05)` + `.18` 描边;灰 `rgba(60,60,67,.035)` + `.10` 描边)。
struct NKTintedNote: View {
    let text: LocalizedStringKey
    var tone: NKAxisTone = .neutral

    private var fill: Color {
        tone == .neutral ? NK.textTertiary.opacity(0.09) : tone.color.opacity(0.06)
    }
    private var stroke: Color {
        tone == .neutral ? NK.hairline : tone.color.opacity(0.20)
    }

    var body: some View {
        Text(text)
            .font(NKFont.caption)
            .lineSpacing(4)
            .foregroundStyle(NK.textPrimary.opacity(0.70))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 13).padding(.vertical, 11)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(fill))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.inner).stroke(stroke, lineWidth: 0.5))
    }
}
