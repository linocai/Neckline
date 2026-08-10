//
//  NKDisclosure.swift
//  Neckline — V2.3 视觉升级:披露折叠 + 审计视图容器
//
//  🔴 **折叠的是版式,⛔ 不是内容**(规范 §01 决定 03,用户裁定):「没看≠没有」
//  「参考、非指令」「⛔ 不是 0 分」这类**诚实披露文案一字不改**,只是从平铺在卡面上
//  改成收进卡片底部的 ⓘ 区,正常态留一行标记。
//
//  ⛔ **别把折叠当降级理由**:V2.3 之前这些话大量挂在 9.5px —— 既看不清也压不住信息量。
//  折叠**本身**已经完成了降级,所以展开区内容一律走 `caption`(11),⛔ 不许再缩字号。
//
//  ⚠ **收起态那一行必须说清里面有几条、是什么性质** —— 一个只写「披露」两个字的
//  折叠等于把话藏了;而藏起来的诚实披露,与没写过没有区别。
//

import SwiftUI

/// 披露折叠区。前缀恒为「披露 · 」,由组件加,调用点只给后半句。
///
/// ```swift
/// NKDisclosure("参考、非指令") { NKReferenceNote() }              // 正常态:灰圈
/// NKDisclosure("2 维判定输入未取得", tone: .warn) { ... }          // 异常态:琥珀圈
/// ```
struct NKDisclosure<Content: View>: View {
    /// 收起态那一行「披露 · 」后面的话。**说清有几条、是什么性质。**
    let summary: String
    /// 🔴 **异常态(有降级 / 缺数)圈变琥珀,正常态灰圈** —— 让用户一眼看出
    /// 这次的披露要不要点开。⛔ 别恒定用一种颜色:那等于每次都要点开才知道。
    var tone: NKAxisTone = .neutral
    @ViewBuilder var content: Content

    /// ⚠ 初值来自 QA 钩子(`NKQA.expandDisclosures`,缺环境变量时恒 `false`):
    /// 本环境点不动模拟器,折叠区内容只能靠它出截图。**只改初值**,用户照常可收放。
    /// ⛔ 不许写成 `isExpanded: .constant(...)` —— 那会把用户的点击一起夺走。
    @State private var expanded: Bool = NKQA.expandDisclosures

    private var circleColor: Color {
        tone == .neutral ? NK.textTertiary : tone.color
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    // 13px 圆圈 i。异常态整圈变琥珀(底 + 描边 + 字)。
                    ZStack {
                        Circle().fill(circleColor.opacity(0.12))
                        Circle().stroke(circleColor.opacity(0.45), lineWidth: 0.5)
                        Text("i").font(.system(size: 8, weight: .bold)).foregroundStyle(circleColor)
                    }
                    .frame(width: 13, height: 13)

                    Text("披露 · \(summary)")
                        .font(NKFont.caption)
                        .foregroundStyle(tone == .neutral ? NK.textTertiary : tone.color)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)

                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 8, weight: .semibold))
                        .foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                VStack(alignment: .leading, spacing: 5) { content }
                    // 展开区内容统一 11px;子视图自己设了字号的照旧覆盖。
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(
                        RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.disclosureBg)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: NKRadius.control)
                            .stroke(NK.hairline, lineWidth: 0.5)
                    )
            }
        }
    }
}

/// **审计视图**(规范 §01 决定 04):`NKJSONTable` 那类等宽键值表不再摊在主界面,
/// 收进这个入口。主界面只留**结论 + 少量关键读数**。
///
/// ⚠ 与 `NKDisclosure` 是两件事,⛔ 别合并:
///   · `NKDisclosure` 收的是**诚实披露文案**(说人话的限制条件),正常态也该被看见一行;
///   · `NKAuditSection` 收的是**机器原始件**(口径指纹 / 验证条件集 / 机械读数),
///     只有要核对的时候才点开 —— 它答的是「这个数是怎么来的」。
struct NKAuditSection<Content: View>: View {
    /// 里面装了什么,逐项列清(如「口径指纹、验证条件集、机械读数原始件」)。
    let contains: String
    @ViewBuilder var content: Content

    @State private var expanded: Bool = NKQA.expandDisclosures

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "wrench.and.screwdriver")
                        .font(.system(size: 10, weight: .medium))
                    Text("审计视图").nkLabel()
                    Text("· \(contains)")
                        .font(NKFont.caption)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 8, weight: .semibold))
                    Spacer(minLength: 0)
                }
                .foregroundStyle(NK.textTertiary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                VStack(alignment: .leading, spacing: NKSpace.denseGap) { content }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(
                        RoundedRectangle(cornerRadius: NKRadius.control).fill(NK.disclosureBg)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: NKRadius.control)
                            .stroke(NK.hairline, lineWidth: 0.5)
                    )
            }
        }
    }
}

/// 审计视图里的一小节(标题 + 内容),让「口径指纹」「验证条件集」「机械读数」
/// 在同一个折叠区内仍然分得开。
struct NKAuditGroup<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            content
        }
    }
}
