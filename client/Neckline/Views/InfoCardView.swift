//
//  InfoCardView.swift
//  Neckline — 信息卡页(§五 v1.4-④,需求 8 第 3 点「考卷同构」):候选卡点进去展示
//  60 日 K 线 + RS 线 + 行业分歧线三图 + 快照七项 + 红黄牌 + 温和带 + 消息面 + 龙虎榜
//  + 市场语境。数据来自 `GET /report/{date}/info-card/{code}`(单只现算,不落库)。
//
//  **第〇原则(考卷同构)**:数据不可得如实缺省,禁止硬凑——每一路数据源独立
//  `*Available`/`*UnavailableReason`,一路缺失不连带其余各路"看起来也不可用"、
//  不画空图、不画 0 值线。
//
//  **execHints 待核对假设(⑤留,已核对)**:信息卡页复用候选对象自带的
//  `Candidate.execHints` 展示「执行提示」,不为它单独请求、不给 `InfoCardOut` 加字段
//  ——本版信息卡入口只有候选卡这一条路(`TodayPlanView.CandidateRow`),假设成立。
//
//  **图表实现**:项目部署目标 iOS 26 / macOS 26(远高于 Swift Charts 最低要求
//  iOS16/macOS13),侦察后直接用 Swift Charts,不必手绘 Path 兜底。
//
//  **性能**:承项目 CLAUDE.md「SwiftUI 动画性能三禁」——图表区不做阴影动画、不与
//  其它含材质大视图树交叉淡化,本页整体走 sheet 呈现/消失(系统转场),内部无自定义
//  隐式动画。
//

import Charts
import SwiftUI

struct InfoCardPageView: View {
    @Bindable var model: AppModel
    let request: AppModel.InfoCardRequest

    var body: some View {
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(platformBg)
            .navigationTitle(request.candidate.name)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.dismissInfoCard() }
                }
            }
        }
        #if os(macOS)
        .frame(width: 640, height: 760)
        #endif
    }

    private var platformBg: Color {
        #if os(iOS)
        NK.pageBgIOS
        #else
        NK.pageBg
        #endif
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            header
            if model.infoCardLoading && model.infoCard == nil {
                NKCard {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("加载信息卡…").font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                }
            } else if let err = model.infoCardError {
                NKCard {
                    NKEmptyState(title: "信息卡加载失败", subtitle: err, systemImage: "exclamationmark.triangle")
                }
            } else if let card = model.infoCard {
                execHintsCard
                klineCard(card)
                rsLineCard(card)
                industryDivergenceCard(card)
                snapshotCard(card)
                k4FlagsCard(card)
                newsCard(card)
                topListCard(card)
                marketCard(card)
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            #if os(macOS)
            HStack(spacing: 6) {
                Text(request.candidate.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                Text(request.candidate.code).font(.system(size: 12)).foregroundStyle(NK.textTertiary)
            }
            #endif
            Text("交易日 \(model.calendar.displayString(request.tradeDate)) · \(request.candidate.boardLabel)")
                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
        }
    }

    // MARK: - 执行提示(复用候选对象,不为本页单独请求)

    @ViewBuilder
    private var execHintsCard: some View {
        if !request.candidate.execHints.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    NKSectionHeader(title: "执行提示")
                    ForEach(request.candidate.execHints) { hint in
                        Text(hint.text).font(.system(size: 12.5)).foregroundStyle(NK.textPrimary)
                    }
                }
            }
        }
    }

    // MARK: - ① K 线(60 日,蜡烛 + 量柱 + MA20/MA250)

    @ViewBuilder
    private func klineCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "K 线(60 日,前复权)")
                if !card.klineAvailable {
                    unavailableRow(card.klineUnavailableReason ?? "无数据")
                } else if card.kline.isEmpty {
                    unavailableRow("无数据")
                } else {
                    KLineChartView(bars: card.kline)
                }
            }
        }
    }

    // MARK: - ② RS 线(相对大盘,60 日,起点归一 100)

    @ViewBuilder
    private func rsLineCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    NKSectionHeader(title: "RS 线(相对大盘)")
                    Spacer()
                    Text("基准 \(card.rsBenchmark)").font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                }
                if !card.rsAvailable || card.rsLine.isEmpty {
                    unavailableRow(card.rsUnavailableReason ?? "无数据")
                } else {
                    IndexLineChartView(points: card.rsLine, color: NK.accent)
                }
            }
        }
    }

    // MARK: - ③ 行业分歧线(个股/行业成员中位数合成,60 日)

    @ViewBuilder
    private func industryDivergenceCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    NKSectionHeader(title: "行业分歧线")
                    Spacer()
                    if !card.industry.isEmpty {
                        Text(card.industry).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                    }
                }
                if !card.industryDivergenceAvailable || card.industryDivergenceLine.isEmpty {
                    unavailableRow(card.industryDivergenceUnavailableReason ?? "无数据")
                } else {
                    // 用 amber 与 RS 线的 accent 区分开(两图分属不同卡片,颜色只为
                    // "同一屏里两条线不撞色",不承载额外语义)。
                    IndexLineChartView(points: card.industryDivergenceLine, color: NK.amber)
                }
                Text(card.industryDivergenceNote).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
            }
        }
    }

    // MARK: - ④ 快照七项

    @ViewBuilder
    private func snapshotCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "快照")
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    metric("量比(5日)", card.snapshot.volRatio5.map { String(format: "%.2f", $0) } ?? "—")
                    metric("换手率", card.snapshot.turnoverRate.map { NKFmt.pct($0) } ?? "—")
                    metric("行业强度排名", card.snapshot.industryRank.map { "#\($0)" } ?? "未参与排名")
                    // v1.4-⑩-E:`nil` = 行业强度表当日无数据(「没看」),显示「数据未就绪」
                    // 而不是「0 天」(「0 天」是「看了,不是强度日」,两者不能混)。
                    metric("行业强度持续天数", card.snapshot.industryPersistDays.map { "\($0) 天" } ?? "数据未就绪")
                    metric("年线位置", yearLineText(card.snapshot))
                    metric("距 20 日高点", card.snapshot.distFromHigh20dPct.map { NKFmt.signedPct($0 * 100) } ?? "—")
                    metric("连续涨停天数", "\(card.snapshot.consecLimitUpDays) 天")
                }
                if card.mildBand {
                    NKChip(text: "温和带(低方差核心带,≈0 期望、非正 alpha)", tone: .warn)
                }
            }
        }
    }

    private func yearLineText(_ s: InfoCardSnapshot) -> String {
        guard let above = s.aboveMa250 else { return "未就绪(历史不足250日)" }
        let distText = s.distFromMa250Pct.map { NKFmt.signedPct($0 * 100) } ?? ""
        return (above ? "年线上方 " : "年线下方 ") + distText
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
            Text(value).font(.system(size: 13, weight: .semibold).monospacedDigit()).foregroundStyle(NK.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - ⑤ 红黄牌

    @ViewBuilder
    private func k4FlagsCard(_ card: InfoCard) -> some View {
        if !card.k4Flags.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "红黄牌")
                    ForEach(card.k4Flags) { flag in
                        HStack(alignment: .top, spacing: 8) {
                            NKChip(text: flag.sectionLabel, tone: flag.sectionTone, filled: true)
                            VStack(alignment: .leading, spacing: 1) {
                                HStack(spacing: 5) {
                                    Text(flag.label).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                                    Text(flag.level == "strong" ? "强" : "普通").font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                                    if flag.evidenceStrength == "constituent" {
                                        Text("参考").font(.system(size: 9)).foregroundStyle(NK.textTertiary)
                                    }
                                }
                                if !flag.evidence.isEmpty {
                                    Text(flag.evidence).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - ⑥ 消息面

    @ViewBuilder
    private func newsCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "消息面")
                if !card.news.scanned {
                    unavailableRow(card.news.unavailableReason ?? "本次未扫描")
                } else if card.news.items.isEmpty {
                    Text("已扫描,当前无命中 = 确认无消息").font(.system(size: 12)).foregroundStyle(NK.up)
                } else {
                    ForEach(card.news.items) { item in
                        HStack(alignment: .top, spacing: 6) {
                            NKChip(text: item.categoryLabel, tone: .bad)
                            Text(item.summary).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - ⑦ 龙虎榜

    @ViewBuilder
    private func topListCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    NKSectionHeader(title: "龙虎榜")
                    Spacer()
                    Text("近 5 日查到 \(card.topList.lookbackDaysCovered) 天,命中 \(card.topList.lookbackHitDays) 天")
                        .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                }
                if card.topList.onListToday {
                    HStack(spacing: 10) {
                        NKChip(text: "今日上榜", tone: .warn, filled: true)
                        if let net = card.topList.netAmount {
                            Text("净额 \(NKFmt.signedMoney(net))").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        }
                        if let rate = card.topList.netRate {
                            Text("净占比 \(NKFmt.signedPct(rate * 100))").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        }
                    }
                } else {
                    Text(card.topList.reason ?? "今日未上榜").font(.system(size: 12)).foregroundStyle(NK.textTertiary)
                }
            }
        }
    }

    // MARK: - ⑧ 市场语境

    @ViewBuilder
    private func marketCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "市场语境", trailing: card.market.indexCode)
                if card.market.indexLine.isEmpty {
                    unavailableRow("无数据")
                } else {
                    IndexLineChartView(points: card.market.indexLine, color: NK.textSecondary, height: 90)
                }
                HStack(spacing: 16) {
                    metric("涨停家数", "\(card.market.limitUpCount)")
                    metric("跌停家数", "\(card.market.limitDownCount)")
                    metric("大盘 MA20", card.market.aboveMa20.map { $0 ? "上方" : "下方" } ?? "—")
                }
            }
        }
    }

    // MARK: - 共用:诚实缺省行(不画空图、不画 0 值线)

    private func unavailableRow(_ reason: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "info.circle").font(.system(size: 11)).foregroundStyle(NK.textTertiary)
            Text(reason).font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
        }
    }
}

// MARK: - K 线图(RuleMark 影线 + RectangleMark 实体 + LineMark MA20/MA250)

private struct KLineChartView: View {
    let bars: [InfoCardKlineBar]

    private struct Point: Identifiable {
        let id: String
        let date: Date
        let bar: InfoCardKlineBar
    }

    private var points: [Point] {
        let cal = StaticTradingCalendar.shared
        return bars.compactMap { b in
            guard let d = cal.parseDate(b.tradeDate) else { return nil }
            return Point(id: b.tradeDate, date: d, bar: b)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Chart {
                ForEach(points) { p in
                    RuleMark(x: .value("日期", p.date), yStart: .value("低", p.bar.low), yEnd: .value("高", p.bar.high))
                        .foregroundStyle(p.bar.isUp ? NK.up : NK.down)
                        .lineStyle(StrokeStyle(lineWidth: 1))
                    RectangleMark(x: .value("日期", p.date), yStart: .value("开", p.bar.open),
                                 yEnd: .value("收", p.bar.close), width: .ratio(0.62))
                        .foregroundStyle(p.bar.isUp ? NK.up : NK.down)
                }
                ForEach(points.filter { $0.bar.ma20 != nil }) { p in
                    LineMark(x: .value("日期", p.date), y: .value("MA20", p.bar.ma20 ?? 0))
                        .foregroundStyle(NK.accent)
                        .lineStyle(StrokeStyle(lineWidth: 1))
                        .interpolationMethod(.linear)
                }
                ForEach(points.filter { $0.bar.ma250 != nil }) { p in
                    LineMark(x: .value("日期", p.date), y: .value("MA250", p.bar.ma250 ?? 0))
                        .foregroundStyle(NK.amber)
                        .lineStyle(StrokeStyle(lineWidth: 1))
                        .interpolationMethod(.linear)
                }
            }
            .chartYScale(domain: .automatic(includesZero: false))
            .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) }
            .frame(height: 190)

            Chart(points) { p in
                BarMark(x: .value("日期", p.date), y: .value("量", p.bar.vol), width: .ratio(0.62))
                    .foregroundStyle((p.bar.isUp ? NK.up : NK.down).opacity(0.55))
            }
            .chartXAxis(.hidden)
            .frame(height: 46)

            HStack(spacing: 12) {
                legendDot(NK.accent, "MA20")
                legendDot(NK.amber, "MA250")
            }
        }
    }

    private func legendDot(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 3) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label).font(.system(size: 9.5)).foregroundStyle(NK.textTertiary)
        }
    }
}

// MARK: - 指数化折线图(RS 线 / 行业分歧线 / 市场语境指数线共用,起点归一 100)

private struct IndexLineChartView: View {
    let points: [InfoCardIndexPoint]
    var color: Color = NK.accent
    var height: CGFloat = 130

    private struct DPoint: Identifiable {
        let id: String
        let date: Date
        let value: Double
    }

    private var dpoints: [DPoint] {
        let cal = StaticTradingCalendar.shared
        return points.compactMap { p in
            guard let d = cal.parseDate(p.tradeDate) else { return nil }
            return DPoint(id: p.tradeDate, date: d, value: p.value)
        }
    }

    var body: some View {
        Chart {
            ForEach(dpoints) { p in
                LineMark(x: .value("日期", p.date), y: .value("值", p.value))
                    .foregroundStyle(color)
                    .interpolationMethod(.linear)
            }
            RuleMark(y: .value("基准 100", 100))
                .foregroundStyle(NK.textTertiary.opacity(0.5))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
        }
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) }
        .frame(height: height)
    }
}
