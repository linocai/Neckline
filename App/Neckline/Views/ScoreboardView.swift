//
//  ScoreboardView.swift
//  Neckline — 🔴 **成绩板块**(三板块之一,裁定 11:成绩线升为板块)。
//
//  三块内容,**各自独立、⛔ 互不合并**:
//    ① **清单成绩五指标**(K9 §八)—— 🔴 **行业分与选票分分两栏,⛔ 无任何合计数字**;
//    ② **10:00 结算拍的三分支终值**(裁定 10)—— 成立率的**明细**,
//       ⛔ 它只出现在这里、不进选股首屏;
//    ③ **覆盖率 + 漏检归因**(架构 §5.2)—— 这条线**不依赖任何待标定数字**。
//
//  🔴 **行业分与选票分为什么必须分开**(K9 §八 口径原文):
//  行业分低是**方向层**的问题;行业分高而选票分低是**选票参数**的问题 ——
//  **两者吃的药完全不同**。服务端 `scorecard` 存储层刻意没有 `total` / `combined`
//  一类字段(守门单测锁死),本页同理:**⛔ 全页不出现把两者相加的任何数字**。
//
//  🔴 **NULL 不是 0**:`coverageAll == nil` = 昨天还没有清单;
//  `coverageInPool == nil` = 边界参数缺失。⛔ 一律渲染成「尚不可得」。
//

import SwiftUI

struct ScoreboardView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    listingScorecardCard
                    verdictsCard
                    coverageCard
                    missesCard
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(AppTab.scoreboard.title)
            .toolbar { ToolbarItem(placement: .primaryAction) { NKRefreshPill(model: model) } }
        }
        .task { await model.ensureLoaded(.scoreboard) }
        #else
        NKSplitLayout {
            listColumn
        } detail: {
            detailColumn
        }
        .task {
            await model.ensureLoaded(.scoreboard)
            model.revealSettlementIfAvailable()
        }
        #endif
    }

    // MARK: - ① 清单成绩五指标

    private var listingScorecardCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 12) {
                NKSectionHeader(title: "K9-v2 清单成绩 · D2 结算",
                                trailing: "观察队列 \(model.listingScorecard.activeQueueCount)"
                                    + "/\(model.listingScorecard.activeQueueLimit)"
                                    + " · 已结算 \(model.listingScorecard.settledDays) 批")
                let score = model.listingScorecard.overall
                metricRow("D1—D2 上涨触达", score.touchRate.map(NKFmt.ratioPct),
                          "\(score.touchNumerator)/\(score.touchDenominator)")
                metricRow("D2 收盘胜率", score.d2CloseWinRate.map(NKFmt.ratioPct),
                          "\(score.d2CloseWinNumerator)/\(score.d2CloseWinDenominator)")
                metricRow("D2 行业超额", score.averageIndustryExcess.map(NKFmt.signedPct),
                          "个股收益减冻结行业同期收益")
                metricRow("D1—D2 最大回撤", score.averageMaxDrawdown.map(NKFmt.signedPct),
                          "统一以 D0 收盘为起点")
                VStack(alignment: .leading, spacing: 4) {
                    Text("最终清单提升").font(NKFont.callout).fontWeight(.semibold)
                    HStack(spacing: 12) {
                        Text("对严格召回 " + (score.finalListingLift.vsStrictRecall
                            .map(NKFmt.signedPct) ?? "尚不可得"))
                        Text("对匹配基准 " + (score.finalListingLift.vsMatchedBaseline
                            .map(NKFmt.signedPct) ?? "尚不可得"))
                    }
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
                Divider().overlay(NK.hairline)
                Text("按主形态拆分").font(NKFont.callout).fontWeight(.semibold)
                ForEach(["p1", "p2", "p3", "p4"], id: \.self) { pattern in
                    patternRow(pattern, model.listingScorecard.byPattern[pattern])
                }
                Divider().overlay(NK.hairline)
                Text("D1 预案辅助账").font(NKFont.callout).fontWeight(.semibold)
                HStack(spacing: 12) {
                    d1Aux("成立率", key: "confirmationRate")
                    d1Aux("成立后触达", key: "confirmed")
                    d1Aux("放弃后触达", key: "rejected")
                    d1Aux("观察分支", key: "observed")
                }
                NKNoteBlock(text: LocalizedStringKey(
                    "全部正式清单都从 D0 收盘跟踪到 D2 收盘；D1 的成立、观察、放弃只作辅助拆分，"
                    + "不会把放弃票或未成交票从主成绩里删掉。D1 辅助触达统一使用参数包冻结的开盘参考价；"
                    + "同一日同时触及上涨线和风险线时标记路径不明。"
                ))
            }
        }
    }

    private func metricRow(_ title: String, _ value: String?, _ note: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title).font(NKFont.callout).fontWeight(.semibold)
            Spacer(minLength: 6)
            Text(value ?? "尚不可得").font(NKFont.callout.monospacedDigit())
                .fontWeight(.semibold).foregroundStyle(value == nil ? NK.textTertiary : NK.textPrimary)
            Text(note).font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
    }

    private func patternRow(_ pattern: String, _ group: ListingMetricGroup?) -> some View {
        HStack(spacing: 10) {
            Text(nkPatternLabel(pattern)).font(NKFont.caption).frame(width: 84, alignment: .leading)
            Text("触达 " + (group?.touchRate.map(NKFmt.ratioPct) ?? "—"))
            Text("胜率 " + (group?.d2CloseWinRate.map(NKFmt.ratioPct) ?? "—"))
            Text("超额 " + (group?.averageIndustryExcess.map(NKFmt.signedPct) ?? "—"))
            Text("回撤 " + (group?.averageMaxDrawdown.map(NKFmt.signedPct) ?? "—"))
        }
        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
    }

    private func d1Aux(_ title: String, key: String) -> some View {
        let value = model.listingScorecard.d1Aux[key]
        return VStack(alignment: .leading, spacing: 3) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value?.touchRate.map(NKFmt.ratioPct) ?? "尚不可得")
                .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - ② 10:00 结算拍的三分支终值

    private var verdictsCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "10:00 结算 · 三分支终值",
                                trailing: NKFmt.reportDate(model.verdicts.tradeDate))
                Text("这里显示开盘后的最终核对结果；早间页面只提供提前观察。"
                     + "提前放弃并不等同于成立；仍待观察的项目也不会提前计入结论。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                if model.verdicts.verdicts.isEmpty {
                    Text("这一天还没有开盘后的结算记录。补跑不会用更晚的价格替代当时的记录。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    NKStatGrid(columns: 4) {
                        NKStatCell(title: "成立", value: String(model.verdicts.count(.confirmed)),
                                   tone: .good)
                        NKStatCell(title: "放弃", value: String(model.verdicts.count(.rejected)),
                                   tone: .bad)
                        NKStatCell(title: "观察", value: String(model.verdicts.count(.observed)),
                                   footnote: "⛔ 不进三个比率的分子分母")
                        NKStatCell(title: "还没定案",
                                   value: String(model.verdicts.undecidedCount),
                                   footnote: "⚠ 与「观察」不是一回事")
                    }
                    Divider().overlay(NK.hairline)
                    ForEach(model.verdicts.verdicts) { row in VerdictRowView(row: row) }
                }
            }
        }
    }

    // MARK: - ③ 覆盖率

    private var coverageCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 12) {
                NKSectionHeader(title: "覆盖率", trailing: "口径 = 涨停 · 不依赖任何待标定数字")
                if let d = model.coverage.latest {
                    NKStatGrid {
                        NKStatCell(title: "覆盖率(头条)", value: pctText(d.coverageAll),
                                   tone: d.coverageAll == nil ? .neutral : .good,
                                   footnote: coverageFootnote(d))
                        NKStatCell(title: "池内覆盖率", value: pctText(d.coverageInPool),
                                   footnote: d.coverageInPool == nil
                                       ? "尚不可得 —— 边界参数缺失时服务端写 NULL"
                                       : "分母 = D−1 未被硬边界排除的涨停票")
                        NKStatCell(title: "当日涨停", value: String(d.limitUpCount),
                                   footnote: "连板高度 \(d.maxConsecDays.map(String.init) ?? "—")")
                    }
                    HStack(spacing: 10) {
                        Text("交易日 \(NKFmt.reportDate(d.tradeDate))")
                        if let v = d.packVersion, !v.isEmpty { Text("事实包 \(v)") }
                        if let lt = d.listingTradeDate, !lt.isEmpty { Text("对比清单 \(NKFmt.reportDate(lt))") }
                    }
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                    Text("“尚不可得”表示还没有可用于对比的前一日清单，不是 0%，也不是一只都没覆盖到。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("还没有覆盖率读数 —— 它在每天 16:05 的日更里、紧随事实包冻结之后产出。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func coverageFootnote(_ d: CoverageDay) -> String {
        guard d.coverageAll != nil, let covered = d.coveredCount else {
            return "尚不可得 —— 昨天还没有清单"
        }
        return "\(covered) / \(d.limitUpCount) 只涨停出现在昨天的清单里"
    }

    private func pctText(_ v: Double?) -> String {
        guard let v else { return "尚不可得" }   // 🔴 ⛔ 绝不显示成 0%
        return NKFmt.ratioPct(v)
    }

    /// 漏检归因。⚠ 它不是在指责系统 —— 它把「昨天为什么没选中这只涨停票」
    /// 变成一次**查表**而不是一次考古(§5.4.8)。
    @ViewBuilder
    private var missesCard: some View {
        if !model.coverage.latestMisses.isEmpty || !model.coverage.missReasonCounts.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 10) {
                    NKSectionHeader(title: "漏检归因",
                                    trailing: "最新一天 · \(model.coverage.latestMisses.count) 只")
                    if !model.coverage.missReasonCounts.isEmpty {
                        NKWrapRow(spacing: 5, lineSpacing: 5) {
                            ForEach(model.coverage.missReasonCounts.keys.sorted(), id: \.self) { k in
                                NKChip(text: "\(nkMissReasonLabel(k)) \(model.coverage.missReasonCounts[k] ?? 0)",
                                       tone: .neutral)
                            }
                        }
                    }
                    ForEach(model.coverage.latestMisses) { m in
                        HStack(alignment: .top, spacing: 8) {
                            Text(m.displayName).font(NKFont.body).foregroundStyle(NK.textPrimary)
                                .frame(width: 76, alignment: .leading)
                            Text(m.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                            Spacer(minLength: 6)
                            if let l2 = m.l2Name, !l2.isEmpty {
                                Text(l2).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            }
                            NKChip(text: m.reasonLabel, tone: .warn)
                        }
                    }
                }
            }
        }
    }

    // MARK: - macOS 两栏

    #if os(macOS)
    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            VStack(alignment: .leading, spacing: 2) {
                Text(AppTab.scoreboard.title).font(NKFont.title2).tracking(-0.3)
                    .foregroundStyle(NK.textPrimary)
                Text("三条成绩线分开存放 · ⛔ 互不进入对方的分子分母")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH).padding(.bottom, 12)

            ForEach(ScoreboardSection.allCases) { s in
                NKListRow(selected: model.scoreboardSection == s) {
                    model.scoreboardSection = s
                } content: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Image(systemName: s.systemImage).font(.system(size: 11))
                                .foregroundStyle(model.scoreboardSection == s ? NK.accent : NK.textTertiary)
                                .frame(width: 16)
                            Text(s.title).font(NKFont.body)
                                .fontWeight(model.scoreboardSection == s ? .semibold : .regular)
                                .foregroundStyle(NK.textPrimary)
                        }
                        // 🔴 **「这一块答什么」** —— 三块的域各不相同,一行字把它们分开。
                        Text(s.question).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.leading, 24)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var detailColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            switch model.scoreboardSection {
            case .listing: listingScorecardCard
            case .verdicts: verdictsCard
            case .coverage:
                coverageCard
                missesCard
            }
        }
    }
    #endif
}

#if os(macOS)
enum ScoreboardSection: String, CaseIterable, Identifiable {
    case listing, verdicts, coverage
    var id: String { rawValue }
    var title: String {
        switch self {
        case .listing: return "清单成绩"
        case .verdicts: return "三分支终值"
        case .coverage: return "覆盖率"
        }
    }
    var question: String {
        switch self {
        case .listing: return "五指标 · 行业分与选票分分两栏"
        case .verdicts: return "10:00 结算那一拍判出了什么"
        case .coverage: return "当日走强的票昨天在不在清单里"
        }
    }
    var systemImage: String {
        switch self {
        case .listing: return "list.star"
        case .verdicts: return "arrow.triangle.branch"
        case .coverage: return "scope"
        }
    }
}
#endif

// MARK: - 一条终值

struct VerdictRowView: View {
    let row: K9VerdictRow

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(row.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textPrimary)
                .frame(width: 84, alignment: .leading)
            NKChip(text: nkPatternLabel(row.pattern), tone: .neutral)
            Spacer(minLength: 6)
            if let v = row.verdict {
                NKChip(text: v.label, tone: v.tone, filled: true)
                Text(nkDecidedStageLabel(row.decidedStage))
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            } else {
                // 🔴 「还没定案」⛔ 不是「观察」——「观察」是 10:00 真看过之后的结论。
                NKChip(text: "还没定案", tone: .neutral)
            }
        }
    }
}
