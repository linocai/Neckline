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

// ⚠ **`LLMJudgmentBadge` 已随候选族 DTO 整族退役**(V2-⑮):`ReportOut.candidates` 键
// 已删,LLM 的产出改由**篮子卡**承载(叙述 / 剧本 / 三个参考件),每处带下面这条标注。

/// **「参考、非指令」标注**(§2.8 红线:参考件每处出现都要带)。
///
/// **四不**:不进排序 / 不进哨兵 / 不改去留 / 不加分。⛔ 不许省略、不许改写成
/// 「建议」「推荐」之类的指令口吻 —— 这句话是 LLM 产出与硬纪律之间的那条线。
struct NKReferenceNote: View {
    var text: String = "参考、非指令 · 不进排序、不进哨兵、不改去留、不加分"
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "info.circle").font(.system(size: 9))
            Text(text).font(.system(size: 9.5))
        }
        .foregroundStyle(NK.textTertiary)
    }
}

/// 自由结构字段(`mech` / `tierBreakdown` / `verificationSpec` / `manualForm` …)的
/// 键值表。**只展示、不解释**:这些键是服务端的语义标识符(维度名 / 条件名),
/// ⛔ 客户端不改名、不重算、不猜含义。
struct NKJSONTable: View {
    let value: NKJSON

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let obj = value.objectValue, !obj.isEmpty {
                // 按字典序,**确定性** —— 顺序不能每次刷新都跳。
                ForEach(value.sortedKeys, id: \.self) { k in
                    HStack(alignment: .top, spacing: 8) {
                        Text(k).font(.system(size: 10.5).monospaced())
                            .foregroundStyle(NK.textTertiary)
                        Spacer(minLength: 8)
                        Text(obj[k]?.displayText ?? "—")
                            .font(.system(size: 10.5).monospaced())
                            .foregroundStyle(NK.textSecondary)
                            .multilineTextAlignment(.trailing)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } else if let arr = value.arrayValue {
                ForEach(Array(arr.enumerated()), id: \.offset) { _, item in
                    Text("· \(item.displayText)").font(.system(size: 10.5).monospaced())
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text(value.displayText).font(.system(size: 10.5).monospaced())
                    .foregroundStyle(NK.textSecondary)
            }
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

/// 数据新鲜度告警(板块 + 行业强度 + **V2-⑭-A 市场扫描层**)。
/// **三件独立故障各占一行,⛔ 不合并成一句** —— 合并读者就分不清哪个坏了
/// (服务端契约同样是三组独立键)。
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
                        Text("排序缺行业维度、题材持续天数不可用").font(.system(size: 11)).opacity(0.85)
                    }
                }
                if freshness.scanLayerStale == true {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("市场扫描层未就绪").font(.system(size: 13, weight: .bold))
                        Text(scanText).font(.system(size: 12)).opacity(0.9)
                        // 扫描层没跑 → 今日无种子 → 今日无篮子;而「今天没有篮子」与
                        // 「今天没看」必须能分开,这一行就是把它们分开的那句话。
                        Text("今日篮子若为空,可能是**没看**而不是**今天真没有**")
                            .font(.system(size: 11)).opacity(0.85)
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

    /// `-1` 是哨兵值(完全无数据),**不是"落后 -1 天"**,故单独成句。
    private var industryText: String {
        guard let date = freshness.industryStrengthDate,
              let lag = freshness.industryStrengthLagDays, lag >= 0 else {
            return "行业强度数据完全缺失(预计算表无任何数据)"
        }
        return "行业强度数据最新至 \(date),落后 \(lag) 个交易日"
    }

    private var scanText: String {
        guard let date = freshness.scanLayerDate,
              let lag = freshness.scanLayerLagDays, lag >= 0 else {
            return "扫描层三张预计算表完全缺失"
        }
        return "扫描层最新至 \(date),落后 \(lag) 个交易日"
    }
}

/// ⑤ 数据新鲜度明细(三组各自一行,**该组三键整体缺席 = 本次连新鲜度都没查到**,
/// ⛔ 不是"新鲜")。
struct DataFreshnessDetail: View {
    let freshness: DataFreshness

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            row(title: "概念板块日更",
                date: freshness.sectorDataDate, lag: freshness.sectorLagDays,
                stale: freshness.stale, present: true)
            row(title: "行业强度日更",
                date: freshness.industryStrengthDate, lag: freshness.industryStrengthLagDays,
                stale: freshness.industryStrengthStale,
                present: freshness.industryStrengthLagDays != nil || freshness.industryStrengthDate != nil
                    || freshness.industryStrengthStale != nil)
            row(title: "市场扫描层批算",
                date: freshness.scanLayerDate, lag: freshness.scanLayerLagDays,
                stale: freshness.scanLayerStale,
                present: freshness.scanLayerLagDays != nil || freshness.scanLayerDate != nil
                    || freshness.scanLayerStale != nil)
        }
    }

    @ViewBuilder
    private func row(title: String, date: String?, lag: Int?, stale: Bool?, present: Bool) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(title).font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
            Spacer(minLength: 8)
            Text(text(date: date, lag: lag, stale: stale, present: present))
                .font(.system(size: 11)).multilineTextAlignment(.trailing)
                .foregroundStyle(tone(stale: stale, present: present).color)
        }
    }

    private func text(date: String?, lag: Int?, stale: Bool?, present: Bool) -> String {
        guard present else { return "本次没查到(⛔ 不等于新鲜)" }
        guard let l = lag else { return date.map { "最新至 \($0)" } ?? "无数据" }
        if l < 0 { return "完全缺失(哨兵值 -1)" }
        let base = date.map { "最新至 \($0)" } ?? "无日期"
        return "\(base) · 落后 \(l) 个交易日" + (stale == true ? " · 已过期" : "")
    }

    private func tone(stale: Bool?, present: Bool) -> NKAxisTone {
        guard present else { return .warn }
        return stale == true ? .bad : .good
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
    /// **比例**(0.05)→ 展示百分数("5%");非整百分点保留小数("5.5%"),不四舍五入
    /// 成 "6%" 骗人。章程口径指纹(止损比例 / 回落止盈比例)专用——与 `pct(_:)`
    /// (入参已经是百分数)**不是一回事**,别混用。服务端同款实现见
    /// `report/render.py::_ratio_pct_txt`(两端各自格式化,不下发拼好的文案)。
    static func ratioPct(_ v: Double) -> String {
        var s = String(format: "%.2f", v * 100)
        if s.contains(".") {
            while s.hasSuffix("0") { s.removeLast() }
            if s.hasSuffix(".") { s.removeLast() }
        }
        return s + "%"
    }
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
