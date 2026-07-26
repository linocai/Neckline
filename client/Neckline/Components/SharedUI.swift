//
//  SharedUI.swift
//  Neckline — 跨板块共用 UI 组件(卡片容器 / 徽标 / Toast / 着色映射)
//

import SwiftUI

// MARK: - 轴向着色映射(Models.swift 的 NKAxisTone → 实际颜色,保持 Models.swift 无 UI 依赖)

extension NKAxisTone {
    var color: Color {
        switch self {
        case .good: return NK.up
        case .warn: return NK.amber
        case .bad: return NK.down
        case .neutral: return NK.textSecondary
        }
    }
}

// MARK: - 卡片容器(不透明背景,Liquid Glass 只用于栏/浮层,§3.5)

struct NKCard<Content: View>: View {
    var padding: CGFloat = NKSpace.cardPad
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }
}

struct NKSectionHeader: View {
    let title: String
    var trailing: String? = nil

    var body: some View {
        HStack {
            Text(title).font(.system(size: 15, weight: .semibold)).foregroundStyle(NK.textPrimary)
            Spacer()
            if let t = trailing {
                Text(t).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
        }
    }
}

// MARK: - 徽标 / 标签

struct NKChip: View {
    let text: String
    var tone: NKAxisTone = .neutral
    var filled: Bool = false

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(filled ? Color.white : tone.color)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(filled ? tone.color : tone.color.opacity(0.12)))
    }
}

/// 仓位额度三态徽标(满额/半额/休息)。
struct QuotaBadge: View {
    let quota: PositionQuota
    var body: some View {
        NKChip(text: quota.label, tone: quota.tone, filled: true)
    }
}

/// 问询台裁决徽标(§2.5:只两值;`.unknown` 仅为契约漂移兜底展示,不代表第三态)。
struct VerdictBadge: View {
    let verdict: InquiryVerdict
    var body: some View {
        NKChip(text: verdict.label, tone: verdict.tone, filled: true)
    }
}

/// LLM 审判徽标(通过/否决/未激活)。
struct LLMJudgmentBadge: View {
    let judgment: LLMJudgment
    private var tone: NKAxisTone {
        switch judgment.verdict {
        case "通过": return .good
        case "否决": return .bad
        default: return .neutral   // "未激活"等降级占位
        }
    }
    var body: some View {
        NKChip(text: "LLM \(judgment.verdict)", tone: tone)
    }
}

// MARK: - 四件套展开区(§2.2/§2.3 买点/止损/目标/证伪条件;`CandidateRow`/自选体检
// `WatchlistRow` 共用布局,§五 v1.1-F.2「客户端可直接复用 CandidateRow 四件套布局」——
// 两处字段命名本就对齐,抽成一个组件避免两份视图各写一份、日后走样。)

struct FourPieceDisclosure: View {
    let buyPoint: String
    let stop: String
    let target: String
    let invalidation: String
    var llmJudgment: LLMJudgment? = nil
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                HStack {
                    Text(expanded ? "收起四件套" : "买点 / 止损 / 目标 / 证伪条件")
                        .font(.system(size: 12, weight: .medium))
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 10))
                }
                .foregroundStyle(NK.accent)
            }
            .buttonStyle(.plain)
            if expanded {
                VStack(alignment: .leading, spacing: 6) {
                    piece("买点", buyPoint)
                    piece("止损", stop)
                    piece("目标", target)
                    piece("证伪条件", invalidation)
                    if let j = llmJudgment {
                        Divider().overlay(NK.hairline)
                        Text(j.narrative).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                    }
                }
            }
        }
    }

    private func piece(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text(text).font(.system(size: 12.5)).foregroundStyle(NK.textPrimary)
        }
    }
}

// MARK: - 退潮红色刹车横幅(§2.4「今日计划作废、禁开新仓」,最高优先级视觉)

struct RetreatBrakeBanner: View {
    let reason: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 16, weight: .bold))
            VStack(alignment: .leading, spacing: 3) {
                Text("退潮红色刹车 · 今日计划作废、禁开新仓")
                    .font(.system(size: 13.5, weight: .bold))
                if !reason.isEmpty {
                    Text(reason).font(.system(size: 12)).opacity(0.9)
                }
            }
            Spacer()
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

// MARK: - 漏录兜底提示条(§五 v1.1-B.4/E.3:一句提示,非弹窗打扰,补录后自动消失)

struct MissedEntryHintBanner: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle.fill").font(.system(size: 14, weight: .semibold))
            Text(text).font(.system(size: 12.5)).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(NK.amber)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.amber.opacity(0.12)))
    }
}

// MARK: - Toast

struct ToastView: View {
    let toast: Toast
    var body: some View {
        Text(toast.message)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(Capsule().fill(toast.isError ? NK.down : Color.black.opacity(0.82)))
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .padding(.horizontal, 24)
    }
}

// MARK: - 空态 / 降级占位

struct NKEmptyState: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String = "tray"

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage).font(.system(size: 32)).foregroundStyle(NK.textTertiary)
            Text(title).font(.system(size: 14, weight: .medium)).foregroundStyle(NK.textSecondary)
            if let s = subtitle {
                Text(s).font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }
}

// MARK: - 品牌 Logo(◆ 简化几何标记,呼应"颈线"技术形态)

struct NKLogo: View {
    var size: CGFloat = 27
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.28).fill(NK.brand)
            // 一道颈线折线(head-and-shoulders neckline breakout 的极简抽象)
            Path { p in
                p.move(to: CGPoint(x: size * 0.2, y: size * 0.62))
                p.addLine(to: CGPoint(x: size * 0.42, y: size * 0.62))
                p.addLine(to: CGPoint(x: size * 0.58, y: size * 0.32))
                p.addLine(to: CGPoint(x: size * 0.8, y: size * 0.32))
            }
            .stroke(Color.white, style: StrokeStyle(lineWidth: max(1.4, size * 0.09), lineCap: .round, lineJoin: .round))
        }
        .frame(width: size, height: size)
    }
}

// MARK: - 数字格式化

enum NKFmt {
    static func price(_ v: Double) -> String { String(format: "%.2f", v) }
    static func pct(_ v: Double) -> String { String(format: "%.2f%%", v) }
    static func signedPct(_ v: Double) -> String {
        let sign = v > 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", v))%"
    }
    static func signedMoney(_ v: Double) -> String {
        let sign = v > 0 ? "+" : ""
        return "\(sign)¥\(String(format: "%.2f", v))"
    }
    /// 无符号、一位小数(v1.3-⑥「情报」板块的亿/万元量级数字,如大盘量能/板块资金流,
    /// 不需要 `price` 的两位小数精度)。
    static func money(_ v: Double) -> String { String(format: "%.1f", v) }
}
