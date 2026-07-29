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

/// 问询台描述性标注徽标(§2.5:已知两值「已分析」/「已分析·有风险提示」;
/// `.unknown` 仅为契约漂移兜底展示,不代表第三态)。
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

// MARK: - v1.5-①-F/⑤-B 参考三件套展开区(候选卡专属,替换 `FourPieceDisclosure` 在
// `CandidateRow` 的位置;`WatchlistRow` 自选体检卡的四件套**不受影响**,仍用
// `FourPieceDisclosure`,§五 v1.5-⑤-C「自选体检卡的四件套照旧展示,不动」)。
//
// 四态诚实展示(`reference_plan.py` ①-D 状态机,不许合并,「没有」vs「没看」,同
// `render.py::_render_reference_plan` 服务端 markdown 渲染口径逐句对齐):
//   · `plan == nil` —— 老报告快照(建于本字段前)或本次生成整体异常;`judgeSkipped`
//     时换一句更具体的「预算耗尽未发起」理由。**直接展示,不折叠**(内容短,读者
//     需要马上知道"为什么这张卡没有参考件")。
//   · `status == "vetoed"` —— LLM 判风险大,三件套全不展示,只给不买理由;票与
//     信息卡仍照留(§2.0 第 3 条「机器不禁、人可复核」)。**直接展示,不折叠**。
//   · `status == "unavailable"` —— 生成过、本次没看清楚,不是"确认无参考"。**直接
//     展示,不折叠**。
//   · `status == "ok"` —— 逐件展示(买入/离场/剧本 + LLM 评语 + disclaimer),**折叠
//     在展开区**(同 `FourPieceDisclosure` 交互先例,内容体量大,20 只候选列表需要
//     可扫读);某一件被夹逼拦下或本就没给时,不画空区间、不写 0,如实给出未展示
//     原因。
struct ReferencePlanSection: View {
    let plan: ReferencePlan?
    var judgeSkipped: Bool = false
    var llmJudgment: LLMJudgment? = nil
    @State private var expanded = false

    var body: some View {
        Group {
            switch plan?.status {
            case "vetoed": vetoedText
            case "unavailable": unavailableText
            case "ok": okDisclosure
            default: if plan == nil { absentText }
            }
        }
    }

    /// 空字符串等同"没给",一律换成 `fallback`(承服务端 `rp.get(...) or fallback` 口径)。
    private static func orFallback(_ s: String?, _ fallback: String) -> String {
        guard let s, !s.isEmpty else { return fallback }
        return s
    }

    private var absentText: some View {
        Text(judgeSkipped
             ? "参考件:本次预算耗尽未发起审判,因此没有参考件(非异常状态,详见下方 LLM 审判结论)。"
             : "参考件:本报告未生成参考三件套(老报告快照建于本功能上线前,或本次生成异常;不代表已确认无参考)。")
            .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
    }

    private var vetoedText: some View {
        Text("参考件:LLM 判风险大,本次不给参考件;不买理由:\(Self.orFallback(plan?.vetoReason, "见下方审判叙述"))")
            .font(.system(size: 12)).foregroundStyle(NK.amber)
    }

    private var unavailableText: some View {
        Text("参考件:本次未生成(\(Self.orFallback(plan?.unavailableReason, "原因未知")))——不代表确认无参考,仅本次没看清楚。")
            .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
    }

    @ViewBuilder
    private var okDisclosure: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                HStack {
                    Text(expanded ? "收起参考件" : "参考买入 / 离场区间 · 明早剧本")
                        .font(.system(size: 12, weight: .medium))
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 10))
                }
                .foregroundStyle(NK.accent)
            }
            .buttonStyle(.plain)
            if expanded, let p = plan {
                VStack(alignment: .leading, spacing: 8) {
                    buyPiece(p)
                    exitPiece(p)
                    scriptPiece(p)
                    if let j = llmJudgment {
                        Divider().overlay(NK.hairline)
                        Text(j.narrative).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                    }
                    if !p.disclaimer.isEmpty {
                        Text(p.disclaimer).font(.system(size: 10.5)).italic().foregroundStyle(NK.textTertiary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func buyPiece(_ p: ReferencePlan) -> some View {
        if let buy = p.buy {
            piece("参考买入区间(参考,非指令)",
                  "\(NKFmt.price(buy.low))~\(NKFmt.price(buy.high));止损参考约 "
                  + "\(buy.stopPrice.map(NKFmt.price) ?? "未知")(章程 −5%,以实际成交价为准)。"
                  + (buy.why.isEmpty ? "" : " \(buy.why)"))
        } else {
            Text("参考买入区间:本次未展示(\(Self.orFallback(p.buyUnavailableReason, "原因未知")))。")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
        }
    }

    @ViewBuilder
    private func exitPiece(_ p: ReferencePlan) -> some View {
        if let exit = p.exit {
            piece("参考离场区间(参考,非止盈线)",
                  "\(NKFmt.price(exit.low))~\(NKFmt.price(exit.high))。"
                  + (exit.why.isEmpty ? "" : "\(exit.why) ") + "—— 纪律仍以回落止盈 8% 兜底。")
        } else {
            Text("参考离场区间:本次未展示(\(Self.orFallback(p.exitUnavailableReason, "原因未知")))。")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
        }
    }

    @ViewBuilder
    private func scriptPiece(_ p: ReferencePlan) -> some View {
        if let script = p.script, !script.isEmpty {
            piece("明早证伪剧本(参考,非指令)", script)
        } else {
            piece("明早证伪剧本", "本次未生成。")
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

// MARK: - v1.4-①-C 板块数据过期告警(§七 P0-3:报告顶部醒目告警,不静默把过期数据
// 当正常结果展示)。

/// 数据新鲜度告警(v1.4-①-C 板块 + v1.4-⑩-F 行业强度)。**两件独立故障各占一行**,
/// 不合并成一句 —— 合并读者就分不清哪个坏了(服务端契约同样是两组独立键)。
struct DataFreshnessBanner: View {
    let freshness: DataFreshness
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 14, weight: .semibold))
            VStack(alignment: .leading, spacing: 6) {
                if freshness.stale {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("板块数据已过期").font(.system(size: 13, weight: .bold))
                        Text(sectorText).font(.system(size: 12)).opacity(0.9)
                        Text("「当日暴起板块」与「题材持续天数」本日不可信").font(.system(size: 11)).opacity(0.85)
                    }
                }
                if freshness.industryStrengthStale == true {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("行业强度数据未就绪").font(.system(size: 13, weight: .bold))
                        Text(industryText).font(.system(size: 12)).opacity(0.9)
                        Text("今日候选排序缺行业维度、题材持续天数不可用").font(.system(size: 11)).opacity(0.85)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }

    private var sectorText: String {
        let dateText = freshness.sectorDataDate.map { "最新至 \($0)" } ?? "完全缺失"
        return "板块数据\(dateText),落后 \(freshness.sectorLagDays) 个交易日"
    }

    /// `industryStrengthLagDays == -1` 是哨兵值(完全无数据),**不是"落后 -1 天"**,
    /// 故单独成句;有数据时才报落后天数。
    private var industryText: String {
        guard let date = freshness.industryStrengthDate,
              let lag = freshness.industryStrengthLagDays, lag >= 0 else {
            return "行业强度数据完全缺失(预计算表无任何数据)"
        }
        return "行业强度数据最新至 \(date),落后 \(lag) 个交易日"
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
