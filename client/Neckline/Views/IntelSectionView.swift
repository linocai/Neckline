//
//  IntelSectionView.swift
//  Neckline — 「情报」板块(§五 v1.3-⑥-F):C1 复盘情报件 + C2 板块资金流 + C4 消息面。
//
//  挂在「今日计划」候选之后展示(iOS/macOS 通用,§五 v1.3-⑥ 硬约束「不新增 tab」)。
//  五常驻板块诊断漏斗(`intelRank.permanentBoardStatus`)也在这里——它是「报告级构件」
//  (`report/intel_candidates.py::_permanent_board_status` 每只候选携同一份完整列表),
//  取任一候选(这里取第一只)读出即可,不必逐票重复展示。
//
//  **定位铁律(硬要求,写进 UI 文案,不只是代码注释)**:
//   · 候选 = 「过完安检、值得关注的票」,不是「会涨的票」(候选卡自身文案见
//     `TodayPlanView.CandidateRow`,本节只管情报维度展示)。
//   · 板块资金流(C2)= 拥挤情报,非选股信号(STRATEGY_LAB K2 判决板块层有效但无
//     次日领先性)——文案不得暗示"买入依据"。
//   · 消息面必须先展示扫描状态再展示命中,「本次未扫描」/「N 只未及扫描」绝不能被
//     误渲染成「确认无消息」(§硬要求「没扫到 vs 扫了没有必须能区分」)。
//   · 五常驻板块 0 只/不足 2 只时必须展示"为什么"(`note` 服务端已写好文案),
//     **静默空白是禁止的**——这是本版一路守的「『没有』和『没看』必须分开」。
//

import SwiftUI

struct IntelPackageView: View {
    let report: ReportSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "情报")
            permanentBoardsCard
            intelC1Card
            if let mf = report.sectorMoneyflow {
                sectorMoneyflowCard(mf)
            }
            NewsAlertsCard(alerts: report.newsAlerts, scanStatuses: report.newsAlertsScan)
        }
    }

    // MARK: - 五常驻板块诊断漏斗(用户 2026-07-26 拍板:0 只/不足 2 只必须带「为什么」)

    private var permanentBoardsCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("五常驻板块").font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
                let statuses = report.candidates.first?.intelRank.permanentBoardStatus ?? []
                if statuses.isEmpty {
                    Text(report.candidates.isEmpty
                         ? "暂无候选可显示常驻板块状态(今晚 16:35 报告后可见)"
                         : "常驻板块未配置(设置屏「候选情报 · 五常驻板块」可配)")
                        .font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                } else {
                    ForEach(statuses) { s in
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: s.quotaFilled > 0 ? "checkmark.circle" : "exclamationmark.circle")
                                .font(.system(size: 12))
                                .foregroundStyle(s.quotaFilled > 0 ? NK.up : NK.amber)
                            Text(s.note).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    // MARK: - C1 复盘情报件

    @ViewBuilder
    private var intelC1Card: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("复盘情报件").font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    Spacer()
                    Text("EOD 硬数据 · 强证据").font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                }
                if let intel = report.intel, intel.hasContent {
                    if let mv = intel.marketVolume {
                        metricRow("大盘量能(沪深合计)",
                                  "\(NKFmt.money(mv.totalAmountYi))亿 · 5日均\(NKFmt.money(mv.ma5AmountYi))亿"
                                  + (mv.sampleDays < 5 ? "(样本仅\(mv.sampleDays)日)" : ""))
                    }
                    if !intel.limitUpLadder.isEmpty {
                        ladderRow(intel.limitUpLadder)
                    }
                    metricRow("跌停", "\(intel.limitDownTotalCount) 只")
                    moversRow("涨幅榜", intel.gainers, tone: .good)
                    moversRow("跌幅榜", intel.losers, tone: .bad)
                    if !intel.topThemes.isEmpty {
                        themesBlock(intel.topThemes)
                    }
                    if !intel.themePersistenceDistribution.isEmpty {
                        distributionRow("题材持续天数分布", intel.themePersistenceDistribution)
                    }
                    if !intel.mvPreference.isEmpty {
                        bucketRow("市值偏好", intel.mvPreference)
                    }
                    if !intel.limitRegimePreference.isEmpty {
                        bucketRow("涨跌停制度偏好", intel.limitRegimePreference)
                    }
                    if !intel.excludedBoardsNote.isEmpty {
                        Text(intel.excludedBoardsNote).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                    }
                    if !intel.evidenceNote.isEmpty {
                        Text(intel.evidenceNote).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                    }
                } else {
                    Text("复盘情报件暂无数据(旧报告 / 今晚 16:35 报告后自动出现)")
                        .font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                }
            }
        }
    }

    private func moversRow(_ title: String, _ items: [IntelMover], tone: NKAxisTone) -> some View {
        Group {
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(title)(\(items.count))").font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(items.prefix(10)) { m in
                                NKChip(text: "\(m.name) \(NKFmt.signedPct(m.pctChg))", tone: tone)
                            }
                        }
                    }
                }
            }
        }
    }

    private func ladderRow(_ ladder: [IntelLimitLadderRung]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("涨停梯队").font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(ladder) { r in
                        NKChip(text: "\(r.consecDays)连板×\(r.count)", tone: .warn, filled: r.consecDays >= 4)
                    }
                }
            }
        }
    }

    private func themesBlock(_ themes: [IntelThemeItem]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("最强题材").font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
            ForEach(themes.prefix(5)) { t in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(t.name).font(.system(size: 12, weight: .medium)).foregroundStyle(NK.textPrimary)
                        Text(t.persistenceLabel).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                        if t.evidenceStrength == "constituent" {
                            Text("参考").font(.system(size: 9)).foregroundStyle(NK.textTertiary)
                        }
                    }
                    if !t.leaders.isEmpty {
                        Text(t.leaders.map { "\($0.name)\(NKFmt.signedPct($0.pctChg))" }.joined(separator: " · "))
                            .font(.system(size: 10.5)).foregroundStyle(NK.textSecondary)
                    }
                }
            }
        }
    }

    private func distributionRow(_ title: String, _ dist: [String: Int]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(dist.sorted(by: { $0.key < $1.key }), id: \.key) { k, v in
                        NKChip(text: "\(k) \(v)")
                    }
                }
            }
        }
    }

    private func bucketRow(_ title: String, _ buckets: [IntelBucketCount]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(buckets) { b in
                        NKChip(text: "\(b.label) \(b.count)(\(NKFmt.pct(b.pctOfTotal * 100)))")
                    }
                }
            }
        }
    }

    private func metricRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
            Spacer()
            Text(value).font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundStyle(NK.textPrimary)
        }
    }

    // MARK: - C2 板块资金流(**拥挤情报,非选股信号**)

    private func sectorMoneyflowCard(_ mf: SectorMoneyflowSection) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("板块资金流").font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    Spacer()
                    Text("拥挤情报 · 非选股信号").font(.system(size: 10)).foregroundStyle(NK.amber)
                }
                if !mf.available {
                    Text(mf.unavailableReason.isEmpty ? "本日板块资金流数据不可用" : mf.unavailableReason)
                        .font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                } else {
                    if !mf.topInflow.isEmpty { flowGroup("净流入 Top", mf.topInflow, tone: .good) }
                    if !mf.topOutflow.isEmpty { flowGroup("净流出 Top", mf.topOutflow, tone: .bad) }
                    if mf.topInflow.isEmpty && mf.topOutflow.isEmpty {
                        Text("本日无板块资金流榜单数据").font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                    }
                }
            }
        }
    }

    private func flowGroup(_ title: String, _ items: [SectorMoneyflowItem], tone: NKAxisTone) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
            ForEach(items.prefix(5)) { i in
                HStack(spacing: 6) {
                    Text(i.name).font(.system(size: 12)).foregroundStyle(NK.textPrimary)
                    if i.evidenceStrength == "constituent" {
                        Text("参考").font(.system(size: 9)).foregroundStyle(NK.textTertiary)
                    }
                    Spacer()
                    Text("\(NKFmt.signedMoney(i.netInflowWan))万")
                        .font(.system(size: 12).monospacedDigit()).foregroundStyle(tone.color)
                }
            }
        }
    }
}

// MARK: - C4 消息面(减持/立案/暴雷/监管;§硬要求「没扫到 vs 扫了没有必须能区分」)

struct NewsAlertsCard: View {
    let alerts: [NewsAlert]
    let scanStatuses: [NewsAlertScanStatus]

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                Text("消息面 · 减持 / 立案 / 暴雷 / 监管").font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
                if scanStatuses.isEmpty {
                    Text("本次报告未包含消息面扫描状态(旧报告 / 尚未生成)")
                        .font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                } else {
                    // 硬要求:**必须先展示扫描状态,再展示命中条目**——"本次未扫描" /
                    // "N 只未及扫描" 绝不能被渲染成"确认无消息"。
                    ForEach(scanStatuses) { s in scanStatusRow(s) }
                    Divider().overlay(NK.hairline)
                    if alerts.isEmpty {
                        if scanStatuses.allSatisfy({ $0.scanned }) {
                            Text("以上来源均已扫描,当前无命中 = 确认无消息")
                                .font(.system(size: 12)).foregroundStyle(NK.up)
                        } else {
                            Text("部分来源未扫描,当前无命中不代表「确认无消息」——见上方扫描状态")
                                .font(.system(size: 12)).foregroundStyle(NK.amber)
                        }
                    } else {
                        ForEach(alerts) { a in alertRow(a) }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func scanStatusRow(_ s: NewsAlertScanStatus) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: s.scanned ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .font(.system(size: 13)).foregroundStyle(s.scanned ? NK.up : NK.amber)
            VStack(alignment: .leading, spacing: 2) {
                Text(s.sourceLabel).font(.system(size: 12, weight: .semibold)).foregroundStyle(NK.textPrimary)
                Text(scanStatusText(s)).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                // v1.4-⑥-B:自选隔日轮扫披露 + v1.3.4 命中率诚实标注——**四个计数
                // (codesFailed/codesSkipped/codesNoSearch/codesRotationDeferred)语义
                // 各不相同,分开展示,不许合并成一个"没扫到"数字**。
                if !rotationAndNoSearchText(s).isEmpty {
                    Text(rotationAndNoSearchText(s)).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
            }
        }
    }

    private func scanStatusText(_ s: NewsAlertScanStatus) -> String {
        if !s.scanned {
            return s.reason.isEmpty ? "本次未扫描" : "本次未扫描 · \(s.reason)"
        }
        var parts: [String] = ["已扫描"]
        if s.codesTotal > 0 { parts.append("应扫 \(s.codesTotal) 只") }
        // codesFailed(调用了但失败)与 codesSkipped(墙钟预算耗尽、根本没发起调用就跳过)
        // 语义不同,两者都要展示、不能合并成一个数字。
        if s.codesFailed > 0 { parts.append("\(s.codesFailed) 只调用失败") }
        if s.codesSkipped > 0 { parts.append("\(s.codesSkipped) 只因预算未及扫描(持仓已优先扫完)") }
        return parts.joined(separator: " · ")
    }

    /// v1.4-⑥-B 自选隔日轮扫(`rotationGroup`/`codesRotationDeferred`)+ v1.3.4
    /// 命中诚实标注(`codesNoSearch`,调用成功但联网搜索命中 0 条,结论未经搜索证实)。
    /// 独立一行,不与上面 `scanStatusText` 的 codesFailed/codesSkipped 合并。
    private func rotationAndNoSearchText(_ s: NewsAlertScanStatus) -> String {
        var parts: [String] = []
        if !s.rotationGroup.isEmpty {
            parts.append("本次扫自选 \(s.rotationGroup) 组")
        }
        if s.codesRotationDeferred > 0 {
            parts.append("\(s.codesRotationDeferred) 只自选本日轮空(隔日再扫)")
        }
        if s.codesNoSearch > 0 {
            parts.append("\(s.codesNoSearch) 只搜索命中 0 条(结论未经搜索证实)")
        }
        return parts.joined(separator: " · ")
    }

    private func alertRow(_ a: NewsAlert) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(a.name).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                Text(a.code).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                NKChip(text: a.categoryLabel, tone: .bad)
            }
            Text(a.summary).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
        }
        .padding(.vertical, 2)
    }
}
