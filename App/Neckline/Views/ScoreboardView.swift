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
//  ⚠ **① 现在只有壳、没有数,这是计划中的顺序不是漏做**:五指标的结算是 **S17**
//  (排在参数标定完成之后),依赖 D+1~D+4 的行情回填与 10:00 结算终值。
//  服务端因此还**没有** `GET /scoreboard/listing` 这条路由 —— 客户端⛔ 不去调一个
//  不存在的端点(契约对拍会当场红),界面如实说「这条线还没开始结算」。
//

import SwiftUI

struct ScoreboardView: View {
    @Bindable var model: AppModel
    #if os(macOS)
    @State private var section: ScoreboardSection = .listing
    #endif

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
        .task { await model.ensureLoaded(.scoreboard) }
        #endif
    }

    // MARK: - ① 清单成绩五指标(壳 + 两栏)

    private var listingScorecardCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 12) {
                NKSectionHeader(title: "清单成绩 · 五指标", trailing: "K9 §八")
                // 🔴 **两栏永远分开**:这两块并排、各占一半,⛔ 中间不出现任何合计。
                HStack(alignment: .top, spacing: 16) {
                    splitColumn(NKListingScorecard.splitPair.industry, "方向对不对")
                    Divider().frame(width: 0.5).overlay(NK.hairline)
                    splitColumn(NKListingScorecard.splitPair.pick, "票挑得好不好")
                }
                Text("🔴 **行业分与选票分分开计,⛔ 不给合计**:行业分低是**方向层**的问题,"
                     + "行业分高而选票分低是**选票参数**的问题 —— 两者吃的药完全不同。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Divider().overlay(NK.hairline)
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(NKListingScorecard.metrics.filter {
                        $0.name != NKListingScorecard.splitPair.industry
                            && $0.name != NKListingScorecard.splitPair.pick
                    }, id: \.name) { m in
                        HStack(alignment: .top, spacing: 8) {
                            Text(m.name).font(NKFont.callout).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary).frame(width: 52, alignment: .leading)
                            Text(m.question).font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 6)
                            Text("尚未结算").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
                NKNoteBlock(text: LocalizedStringKey(NKListingScorecard.notSettledNote))
            }
        }
    }

    private func splitColumn(_ title: String, _ question: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(NKFont.callout).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
            Text(question).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            // ⛔ 尚未结算 → 写「尚未结算」,**⛔ 不写 0**、⛔ 不写「—」蒙混。
            Text("尚未结算").font(NKFont.metric).foregroundStyle(NK.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - ② 10:00 结算拍的三分支终值

    private var verdictsCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "10:00 结算 · 三分支终值",
                                trailing: model.verdicts.tradeDate)
                Text("🔴 **三分支判定的唯一权威是这一拍**(裁定 10)。9:29 那张核对表只提前告知"
                     + "「哪几只已经死了」,它**不产生「成立」**;⛔ 也不许把「待开盘后观察」"
                     + "当成任何一个分支的结论。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                if model.verdicts.verdicts.isEmpty {
                    Text("这一天还没有结算记录 —— 结算拍在 D1 的 10:00–10:05 跑,**事后不补跑**"
                         + "(补跑会拿 10:30 的价格冒充 10:00 那一刻)。")
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
                        Text("交易日 \(d.tradeDate)")
                        if let v = d.packVersion, !v.isEmpty { Text("事实包 \(v)") }
                        if let lt = d.listingTradeDate, !lt.isEmpty { Text("对比清单 \(lt)") }
                    }
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                    Text("⚠ **NULL 不是 0**:「尚不可得」= 昨天还没有清单(上线首日 / 参数未配置的"
                         + "日子),⛔ 不是「一只都没覆盖到」。")
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
                NKListRow(selected: section == s) { section = s } content: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Image(systemName: s.systemImage).font(.system(size: 11))
                                .foregroundStyle(section == s ? NK.accent : NK.textTertiary)
                                .frame(width: 16)
                            Text(s.title).font(NKFont.body)
                                .fontWeight(section == s ? .semibold : .regular)
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
            switch section {
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
