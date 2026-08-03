//
//  TodayPlanView.swift
//  Neckline — 今日计划(§五 阶段4C.1):昨晚候选 20 + 四件套 + 情绪仪表盘 + 仓位额度 +
//  强势板块 + 持仓列表(派生止损线 + 条件单自证提醒)。数据来自 `GET /report/latest`
//  + `GET /positions`(+ `GET /board` 供退潮警示,见 AppModel.refresh 注释)。
//

import SwiftUI

struct TodayPlanView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle("今日计划")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await model.refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .refreshable { await model.refresh() }
        }
        .sheet(item: Binding(get: { model.modal.map(SheetItem.init) },
                             set: { if $0 == nil { model.dismissModal() } })) { item in
            sheetContent(item.kind)
        }
        .sheet(item: Binding(get: { model.infoCardRequest },
                             set: { if $0 == nil { model.dismissInfoCard() } })) { req in
            InfoCardPageView(model: model, request: req)
        }
        #else
        ScrollView {
            content.padding(NKSpace.pagePad).frame(maxWidth: 860)
        }
        .frame(maxWidth: .infinity)
        .background(NK.pageBg)
        .toolbar {
            ToolbarItem { Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") } }
        }
        .sheet(item: Binding(get: { model.modal.map(SheetItem.init) },
                             set: { if $0 == nil { model.dismissModal() } })) { item in
            sheetContent(item.kind).frame(width: 420)
        }
        .sheet(item: Binding(get: { model.infoCardRequest },
                             set: { if $0 == nil { model.dismissInfoCard() } })) { req in
            InfoCardPageView(model: model, request: req).frame(width: 640, height: 720)
        }
        #endif
    }

    /// `.sheet(item:)` 需要 Identifiable;`PositionModal` 本身只 Equatable,包一层。
    private struct SheetItem: Identifiable, Equatable {
        let kind: PositionModal
        var id: String {
            switch kind {
            case .decisionLog: return "decisionLog"
            case .open: return "open"
            case .close(let code): return "close-\(code)"
            case .circuitReview: return "circuitReview"
            }
        }
    }

    @ViewBuilder
    private func sheetContent(_ kind: PositionModal) -> some View {
        switch kind {
        case .decisionLog: DecisionLogSheet(model: model)
        case .open: OpenPositionSheet(model: model)
        case .close(let code): ClosePositionSheet(model: model, code: code)
        case .circuitReview: CircuitReviewSheet(model: model)
        }
    }

    // MARK: - 共用内容

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            header
            // v1.2-E.3:熔断横幅置顶(比退潮刹车更靠前——这是用户自身纪律被触发,
            // §2.1 第 7 条),文案用服务端 episode.note/basisTradesCount,客户端不重算判定。
            if model.circuit.locked {
                CircuitLockBanner(model: model)
            }
            if let warning = model.retreatWarning {
                RetreatBrakeBanner(reason: warning)
            }
            // v1.1-E.3:漏录兜底提示条(报告 `missedEntryHint` 有值才显示,非弹窗打扰)。
            if !model.report.missedEntryHint.isEmpty {
                MissedEntryHintBanner(text: model.report.missedEntryHint)
            }
            // v1.4-①-C(§七 P0-3)板块数据过期 + v1.4-⑩-F(§七 P0-23)行业强度未就绪:
            // 顶部醒目告警——不可信时不静默把它们当正常结果展示。**两件独立故障,任一
            // 成立即展示,横幅内各占一行**(见 `DataFreshnessBanner`)。
            if let freshness = model.report.dataFreshness, freshness.needsBanner {
                DataFreshnessBanner(freshness: freshness)
            }
            // v1.1-E.1:持仓区置顶到候选之上(持仓管理优先于选新票)。
            positionsSection
            if model.report.degraded {
                NKCard {
                    NKEmptyState(title: emptyTitle(model.report.reason),
                                subtitle: "策略引擎已在跑,今晚 16:35 出计划后自动显示。",
                                systemImage: "moon.zzz")
                }
            } else {
                if let s = model.report.sentiment {
                    SentimentCard(sentiment: s)
                }
                if !model.report.sectors.isEmpty {
                    SectorChipsRow(sectors: model.report.sectors)
                }
                candidatesSection
                // v1.3-⑥-F:情报包(C1 复盘情报件 + C2 板块资金流 + C4 消息面),iOS/macOS
                // 通用,不新增 tab——挂在「今日计划」候选之后(§五 v1.3-⑥ 硬约束「不新增 tab」)。
                IntelPackageView(report: model.report)
            }
        }
    }

    private func emptyTitle(_ reason: String) -> String {
        switch reason {
        case "no_report": return "今日报告尚未生成"
        case "bad_date", "not_loaded": return "暂无数据"
        default: return "暂无数据(\(reason))"
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            // iOS 由 navigationTitle 渲染大标题,此处再画会双标题(实机反馈修复);macOS 无大标题才自画
            #if os(macOS)
            HStack(spacing: 8) {
                NKLogo(size: 24)
                Text("今日计划").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            }
            #endif
            if !model.report.tradeDate.isEmpty {
                Text("交易日 \(model.calendar.displayString(model.report.tradeDate)) · 策略版本 \(model.report.strategyVersion)")
                    .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
        }
    }

    // v1.5-⑤-A(需求 9):今日计划拆两块——① 持仓股(见 `positionsSection`,先管住手里的)
    // / ② 候选列表(本函数,每日 20 只)。顺序不变(持仓在上,承 v1.1-E.1);情绪仪表盘 /
    // 板块 chips / 情报包等既有构件归属不变,挂在候选块之下(见 `content`)。不新增 tab。
    private var candidatesSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            // v1.5-②-A:「前10只过 LLM 审判」的旧分档已退役(20 只全覆盖,每票或出参考件
            // 或出不买理由),trailing 文案同步更新,不留过时描述。
            NKSectionHeader(title: "② 候选列表 \(model.report.candidates.count)", trailing: "20 只全覆盖 · 或参考或说明")
            // v1.3-③-C3/⑥ + v1.4-③ 语义红线(需求 8):候选=「过完安检、值得关注的票」,
            // 非「系统认为会涨的票」;排序 = 注意力优先级,不是收益预测,排第一 ≠ 最会涨,
            // 终选权在你。文案必须跟上,不能让人以为这是买入信号。这句同时是本块的
            // 「一句定位文案」(v1.5-⑤-A「候选沿用既有语义红线句」)。
            Text("过完安检、值得花注意力的票 · 排序 = 注意力优先级,不是收益预测 · 排第一 ≠ 最会涨 · 终选权在你")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            ForEach(model.report.candidates) { c in
                CandidateRow(model: model, candidate: c)
            }
        }
    }

    private var positionsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            HStack {
                NKSectionHeader(title: "① 持仓股 \(model.positions.count)")
                Spacer()
                // v1.2-E.3:熔断锁定时灰化「开新仓」入口(客户端自律,服务端不拦,§3.8)。
                Button { model.beginPositionEntryFlow() } label: {
                    Label(model.circuit.locked ? "熔断中 · 暂停开仓" : "补录开仓",
                          systemImage: model.circuit.locked ? "lock.fill" : "plus.circle.fill")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(model.circuit.locked ? NK.textTertiary : NK.accent)
                .disabled(model.circuit.locked)
            }
            // v1.5-⑤-A:本块「一句定位文案」——持仓管理优先于选新票(承 v1.1-E.1)。
            Text("先管住手里的").font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            if model.positions.isEmpty {
                NKCard { NKEmptyState(title: "暂无持仓", systemImage: "tray") }
            } else {
                ForEach(model.positions) { p in
                    PositionCard(model: model, position: p)
                }
            }
        }
    }
}

// MARK: - 情绪仪表盘卡

private struct SentimentCard: View {
    let sentiment: SentimentSnapshot

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("情绪仪表盘").font(.system(size: 14, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    Spacer()
                    QuotaBadge(quota: PositionQuota(sentiment.positionQuota))
                }
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    metric("涨停", "\(sentiment.limitUpCount)家")
                    metric("跌停", "\(sentiment.limitDownCount)家")
                    metric("炸板率", NKFmt.pct(sentiment.zabanRate * 100))
                    metric("最高连板", "\(sentiment.maxConsecLimitUp)板")
                    metric("昨涨停今溢价", premiumText)
                    metric("样本", "\(sentiment.prevLimitUpSample)只")
                }
                Text(sentiment.quotaReason)
                    .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            }
        }
    }

    private var premiumText: String {
        guard let v = sentiment.prevLimitUpPremiumAvg else { return "—" }
        return NKFmt.signedPct(v * 100)
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
            Text(value).font(.system(size: 14, weight: .semibold).monospacedDigit()).foregroundStyle(NK.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - 强势板块

private struct SectorChipsRow: View {
    let sectors: [SectorSnapshot]
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(sectors) { s in
                    NKChip(text: "\(s.name) · 第\(s.boardAge)天 · \(NKFmt.signedPct(s.ret20d * 100))",
                          tone: s.bonus > 0 ? .good : .neutral)
                }
            }
        }
    }
}

// MARK: - 候选行(四件套展开 + v1.1-E.2「已按计划买入」一键补录 + v1.1-F.3「+自选」)

private struct CandidateRow: View {
    @Bindable var model: AppModel
    let candidate: Candidate

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    Text("#\(candidate.rank)")
                        .font(.system(size: 12, weight: .bold)).foregroundStyle(NK.textTertiary)
                        .frame(width: 24, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(candidate.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                            Text(candidate.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        }
                        Text(candidate.boardLabel).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(String(format: "%.1f 分", candidate.score))
                            .font(.system(size: 13, weight: .semibold).monospacedDigit())
                        if let j = candidate.llmJudgment {
                            LLMJudgmentBadge(judgment: j)
                        } else if candidate.judgeSkipped {
                            // v1.5-②-B/⑤-B:预算耗尽未发起(与下方 degraded「发起了但失败」
                            // 语义不同,不许合并成一个"没审",契约见 `Candidate.judgeSkipped`。
                            NKChip(text: "预算耗尽未审")
                        }
                    }
                }
                if !candidate.formTags.isEmpty || !candidate.hotSectors.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(candidate.formTags, id: \.self) { NKChip(text: $0) }
                            ForEach(candidate.hotSectors, id: \.self) { NKChip(text: $0, tone: .good) }
                        }
                    }
                }
                // v1.3-③-C3/⑥:K4 安检标(avoid_flag 打标保留展示;hard_cut 已在服务端拦截、
                // 不会出现在候选里)+ 情报排序理由(来源/资金流强度/题材天数/高弹/行业)。
                if !candidate.k4Flags.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(candidate.k4Flags, id: \.self) { NKChip(text: $0, tone: .warn) }
                        }
                    }
                }
                intelRankRow(candidate.intelRank)
                // ⚠ **V2-⑬-1/3/4/6 过渡态**:老四件套展开区(⑬-6)、参考三件套展开区
                // (⑬-3)、执行提示区(⑬-4)三块 UI 随服务端键一并删除;而**候选卡本身
                // 由 ⑮ 换成篮子卡(11 项)**,不在 ⑬ 范围。此刻这张卡只剩「码/名/分/板块/
                // K4 牌/情报排序理由/LLM 审判徽标」—— 中间状态,不是最终形态。
                if let j = candidate.llmJudgment { LLMJudgmentBadge(judgment: j) }
                Divider().overlay(NK.hairline)
                HStack(spacing: 14) {
                    // v1.4-④-B:信息卡入口(60 日 K 线/RS 线/行业分歧线 + 快照/红黄牌/温和带/
                    // 消息面/龙虎榜/市场语境)。候选专属,本版先只接候选。
                    Button {
                        model.openInfoCard(tradeDate: model.report.tradeDate, candidate: candidate)
                    } label: {
                        Label("信息卡", systemImage: "chart.xyaxis.line").font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
                    Spacer()
                    // 动作按钮,不是状态——绿勾样式曾被误读为"已经买过"(实机反馈),
                    // 改为明确的动作措辞 + 编辑图标 + 强调色。v1.2-E.1 起先插入决策
                    // 日志录入(建计划→录八项→成交后关联);v1.2-E.3 熔断中灰化。
                    Button {
                        Task { await model.beginPositionEntryFlow(fromCandidate: candidate) }
                    } label: {
                        Label(model.circuit.locked ? "熔断中" : "买入补录",
                              systemImage: model.circuit.locked ? "lock.fill" : "square.and.pencil")
                            .font(.system(size: 12.5, weight: .semibold))
                    }
                    .buttonStyle(.plain).foregroundStyle(model.circuit.locked ? NK.textTertiary : NK.accent)
                    .disabled(model.circuit.locked)
                }
            }
        }
    }

    /// 情报排序理由(v1.3-③-C3/⑥ + v1.4-③ 需求 8 排序键三级):来源(常驻保底/情报竞争/
    /// 问询强制)/ 行业排名(①)/ 行业持续天数(②)/ 黄牌数(③)/ 板块资金流(并列展示,
    /// 不参与排序)/ 高弹标注 / 行业(说清"凭什么在这个板块栏")。全空(旧报告,
    /// `intelRank` 默认值)时不画任何东西,不是硬凑一行空 chip。
    @ViewBuilder
    private func intelRankRow(_ rank: IntelRank) -> some View {
        let hasContent = !rank.source.isEmpty || rank.sectorFlow != nil || rank.industryPersistDays > 0
            || rank.industryRank != nil || rank.yellowCardCount > 0 || rank.highElasticity || !rank.industry.isEmpty
        if hasContent {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    if !rank.source.isEmpty {
                        NKChip(text: nkIntelSourceLabel(rank.source))
                    }
                    // 排序键①:行业强度排名。nil = 未参与排名,**不当 0**(0 会误读成"最强")。
                    if let ir = rank.industryRank {
                        NKChip(text: "行业排名 #\(ir)", tone: ir <= 3 ? .good : .neutral)
                    } else if !rank.industry.isEmpty {
                        NKChip(text: "行业未参与排名")
                    }
                    // 排序键②:行业强度持续天数(升序,第1天最新鲜;H6 单调证据反用)。
                    if rank.industryPersistDays > 0 {
                        NKChip(text: "行业强度第\(rank.industryPersistDays)天",
                              tone: rank.industryPersistDays >= 2 ? .warn : .good)
                    }
                    // 排序键③:K4 黄牌命中数(升序,无牌靠前)。「无牌靠前」只是风险优先排序,
                    // 无牌 ≠ 会涨。
                    if rank.yellowCardCount > 0 {
                        NKChip(text: "黄牌×\(rank.yellowCardCount)", tone: .warn)
                    }
                    // v1.4-③ 起板块资金流退出排序键,只作并列展示。
                    if let flow = rank.sectorFlow {
                        NKChip(text: "板块资金流\(NKFmt.signedMoney(flow))万(并列参考,不参与排序)",
                              tone: flow >= 0 ? .good : .bad)
                    }
                    if rank.highElasticity {
                        NKChip(text: "高弹性", tone: .warn)
                    }
                    if !rank.industry.isEmpty {
                        NKChip(text: rank.industry)
                    }
                }
            }
        }
    }

}

// MARK: - 持仓卡

private struct PositionCard: View {
    @Bindable var model: AppModel
    let position: Position
    /// 本地会话态(§五 阶段4C「自证 checklist」):后端 4A 尚无持久化字段
    /// (`stopOrderChecked` 恒 false),此勾选仅本机本次会话记忆,不同步、不落库。
    @State private var checkedLocally = false

    /// 服务端 K4 命中里「该置顶醒目」的子集(§五 v1.3-⑥-C:level=strong ∧
    /// evidenceStrength=price_volume 才置顶——弱证据/普通级一律降级为下方 chips)。
    private var topBillboardK4: [K4Advisory] { position.k4Advisory.filter { $0.isTopBillboard } }
    private var listK4: [K4Advisory] { position.k4Advisory.filter { !$0.isTopBillboard } }

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                // v1.1-E.1:今日动作提示——D5/时间退出等高优先级动作用醒目横幅置顶展示
                // (`todayActionTone` 纯展示层派生,文案来自服务端 `todayAction`)。
                if position.todayActionTone == .bad {
                    TodayActionBanner(text: position.todayAction)
                }
                // v1.3-②/⑥-C:K4 强警示置顶(疑似派发/换手异常等价量证据;题材类弱证据
                // 不会出现在这里,已被 `topBillboardK4` 挡下)。
                ForEach(topBillboardK4) { hit in
                    K4AdvisoryBanner(hit: hit)
                }
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(position.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                            Text(position.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                            // v1.3-①/⑥-A:两档 D 徽标——非浮盈 D{n}/D5,浮盈豁免 D{n}/D15
                            // (`maxHoldDaysEffective` 服务端按 D5 净浮盈判好下发,不客户端重算)。
                            // 到期(isExitDay)红底醒目;浮盈豁免(D15 档)绿底提示"续持中";其余中性。
                            NKChip(text: "D\(position.dCount)/D\(position.maxHoldDaysEffective)",
                                  tone: dBadgeTone, filled: dBadgeTone != .neutral)
                            // v1.4-①-B:判向挂起短标(主句在下方 todayAction 里,服务端原文
                            // 已含「判向挂起,复牌当日再定格」的完整语义,这里只加一个显眼短
                            // 徽标,不是替代服务端文案)。
                            if position.timeExitKind == .suspendedHold {
                                NKChip(text: "判向挂起 · 复牌当日重判", tone: .warn)
                            }
                        }
                        Text("买入 ¥\(NKFmt.price(position.buyPrice)) × \(position.qty) · \(model.calendar.displayString(position.buyDate))")
                            .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        if !position.entryReason.isEmpty {
                            Text(position.entryReason).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        }
                        // v1.4-①-B(§七 P0-2):停牌/无数据显式标注——绝不静默把老价当今日价。
                        if let stale = position.priceStale {
                            Text("停牌/无数据 \(stale.staleDays) 个交易日,价格为 \(model.calendar.displayString(stale.lastCloseDate)) 最后成交价(\(stale.reasonLabel))")
                                .font(.system(size: 11)).foregroundStyle(NK.amber)
                        }
                        // v1.4-⑥-C(§七 P1-6):定格日 ≠ D5 显式标注,只提示、不改判定逻辑,
                        // **只在晚于 D{maxHoldDays} 时展示**。
                        if position.timeExitLockedLateDays > 0, let lockedDay = position.timeExitLockedDay {
                            Text("定格于 D\(lockedDay),晚于 D\(position.maxHoldDays) \(position.timeExitLockedLateDays) 天")
                                .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                        }
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 3) {
                        Text(position.hasLivePrice ? "¥\(NKFmt.price(position.price))" : "—")
                            .font(NKFont.price).foregroundStyle(NK.textPrimary)
                        if position.hasLivePrice {
                            Text(NKFmt.signedPct(position.pnlPct))
                                .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                                .foregroundStyle(position.pnlPct >= 0 ? NK.up : NK.down)
                        }
                    }
                }
                HStack {
                    NKChip(text: "止损线 ¥\(NKFmt.price(position.stopLine))(-5%)",
                          tone: position.hasBrokenStop ? .bad : .neutral)
                    if position.hasBrokenStop {
                        NKChip(text: "已破止损线", tone: .bad, filled: true)
                    }
                    // v1.1-E.1:距止损线(服务端下发,不重算);无实时价 → 不展示(不误显 0%)。
                    if let dist = position.distToStopPctServer {
                        NKChip(text: "距止损线 \(NKFmt.signedPct(dist * 100))",
                              tone: dist <= 0 ? .bad : (dist <= 0.02 ? .warn : .neutral))
                    }
                    // v1.1-E.1:回落止盈状态(判定复用服务端 `check_take_profit`,客户端只展示)。
                    if let rs = position.retraceState {
                        NKChip(text: rs.triggered ? "回落止盈已触发"
                                    : "峰值¥\(NKFmt.price(rs.peak)) 回落\(NKFmt.pct(rs.retracePct * 100))",
                              tone: rs.triggered ? .bad : .neutral)
                    }
                    Spacer()
                    Button { model.openCloseSheet(code: position.code) } label: {
                        Text("补录清仓").font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(NK.down)
                }
                if position.todayActionTone != .bad && !position.todayAction.isEmpty {
                    Text(position.todayAction)
                        .font(.system(size: 11.5, weight: position.todayActionTone == .warn ? .semibold : .regular))
                        .foregroundStyle(position.todayActionTone == .warn ? NK.amber
                                        : (position.todayActionTone == .good ? NK.up : NK.textTertiary))
                }
                // v1.4-①-B(§七 P0-2):K4 体检因无 EOD 行整份跳过 → 显式"今日未体检",
                // 不静默留空(空白 = 「体检过了没问题」,两者必须能分开)。
                if position.k4DataUnavailable == true {
                    NKChip(text: "K4 今日未体检(停牌/无数据)", tone: .neutral)
                }
                // v1.3-②/⑥-C:普通/成分参考类 K4 命中降级为 chips(题材类标「参考」,不当硬判据)。
                if !listK4.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(listK4) { hit in
                                NKChip(text: hit.evidenceStrength == "constituent" ? "\(hit.label) · 参考" : hit.label,
                                      tone: hit.isStrong ? .warn : .neutral)
                            }
                        }
                    }
                }
                // v1.3-①/⑥-B:实付费用回显(供周复盘对账用真数,不是估算)。
                if position.buyFees != nil || position.sellFees != nil {
                    HStack(spacing: 10) {
                        if let bf = position.buyFees {
                            Text("买入费 ¥\(NKFmt.price(bf))").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                        }
                        if let sf = position.sellFees {
                            Text("卖出费 ¥\(NKFmt.price(sf))").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                        }
                        Text("实付,供周复盘对账用真数").font(.system(size: 9.5)).italic().foregroundStyle(NK.textTertiary)
                    }
                }
                Divider().overlay(NK.hairline)
                Button { checkedLocally.toggle() } label: {
                    HStack(spacing: 6) {
                        Image(systemName: checkedLocally ? "checkmark.square.fill" : "square")
                            .foregroundStyle(checkedLocally ? NK.up : NK.textTertiary)
                        Text("已在券商挂 -5% 条件单")
                            .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        Spacer()
                        Text("仅本机本次会话记忆").font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                    }
                }
                .buttonStyle(.plain)
                Divider().overlay(NK.hairline)
                // v1.2-E.1/E.4:决策日志回显(含情景兑现勾选)+ 呼吸台账入口。
                PositionDecisionSection(model: model, position: position)
            }
        }
    }

    /// D 徽标色调(§五 v1.3-⑥-A):到期(离场提示,两态之一)→ 红底;浮盈豁免(D15 档,
    /// 持有态)→ 绿底(区别于"该走了",视觉上标出"这单在赚、续持中");判向挂起(v1.4-
    /// ①-B)→ 黄底(数据陈旧,提醒但非紧急);其余中性灰。
    private var dBadgeTone: NKAxisTone {
        if position.isExitDay { return .bad }
        if position.timeExitKind == .profitExempt { return .good }
        if position.timeExitKind == .suspendedHold { return .warn }
        return .neutral
    }
}

/// D5/时间退出等最高优先级今日动作横幅(§五 v1.1-E.1「todayAction 文案最高优先醒目」)。
/// 文案本身恒来自服务端 `todayAction`,本组件只负责视觉呈现,同 `RetreatBrakeBanner` 视觉权重。
private struct TodayActionBanner: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").font(.system(size: 16, weight: .bold))
            Text(text).font(.system(size: 13, weight: .bold)).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

/// K4 持仓牌强警示置顶横幅(§五 v1.3-⑥-C:level=strong ∧ evidenceStrength=price_volume
/// 才会走到这里——年线下涨停/放量大阳疑似派发、换手异常等纯价量结构证据)。文案
/// (`label`/`evidence`)恒来自服务端 advisory 原文,本组件只负责视觉呈现。
private struct K4AdvisoryBanner: View {
    let hit: K4Advisory
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "flag.fill").font(.system(size: 15, weight: .bold))
            VStack(alignment: .leading, spacing: 3) {
                Text(hit.label).font(.system(size: 13, weight: .bold))
                if !hit.evidence.isEmpty {
                    Text(hit.evidence).font(.system(size: 11.5)).opacity(0.9)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

// MARK: - 开仓 / 清仓表单(审计台账,补记用户已在券商完成的操作)

/// 两个表单共用的壳(标题 / 取消 / 提交),避免同一段 `NavigationStack`+`Form`+
/// 工具栏样板在两个 sheet 里重复一份。
private struct PositionFormShell<Content: View>: View {
    let title: String
    let onCancel: () -> Void
    let onSubmit: () -> Void
    @ViewBuilder var content: Content

    var body: some View {
        NavigationStack {
            Form { content }
                .formStyle(.grouped)
                .navigationTitle(title)
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("取消", action: onCancel)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("提交", action: onSubmit)
                    }
                }
        }
    }
}

struct OpenPositionSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        PositionFormShell(title: "补录开仓", onCancel: { model.dismissModal() },
                          onSubmit: { Task { await model.submitOpenPosition() } }) {
            Section {
                TextField("代码,如 600519.SH", text: $model.entryForm.code)
                TextField("名称(可选)", text: $model.entryForm.name)
                TextField("买入价", text: $model.entryForm.price)
                TextField("数量(股)", text: $model.entryForm.qty)
                TextField("进场理由", text: $model.entryForm.reason)
                // v1.4-①-A(§七 P0-1):真实买入日,默认今天、不可选未来(服务端还会校验
                // 是否交易日,非交易日 400 拒绝并给出友好提示,不在客户端预判日历)。
                DatePicker("买入日", selection: $model.entryForm.buyDate, in: ...Date(), displayedComponents: .date)
                // v1.3-①/⑥-B:实付买入费用(UI 强制必填,服务端宽松;供 D5 净浮盈判向 +
                // 周复盘对账用真数,不是估算)。
                TextField("实付买入费用(必填,含佣金/过户费等)", text: $model.entryForm.buyFees)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
            } footer: {
                // v1.2-E.5:一键补录预填改区间双档(GET /positions/entry-suggestion,
                // 仅预览——实际提交后以服务端按真实买入价返回的 stopLine 为准,见提交
                // 成功后的 toast)。客户端只展示两档,不替用户拍单笔金额。
                if let range = model.entrySuggestionRange {
                    Text("此处只记录你已在券商完成的真实操作;参考手数区间 \(range.qtyLow)–\(range.qtyHigh) 股(¥\(NKFmt.price(range.capFloor))–¥\(NKFmt.price(range.capCeil)),上限 = 违纪判定线、非推荐值),预计止损价 ¥\(NKFmt.price(range.stopLine))(按现役配置,提交后以实际返回值为准),系统不代下单。实付费用供周复盘对账用真数。")
                } else {
                    Text("此处只记录你已在券商完成的真实操作;止损线由服务端按 -5% 派生返回,系统不代下单。实付费用供周复盘对账用真数。")
                }
            }
        }
    }
}

struct ClosePositionSheet: View {
    @Bindable var model: AppModel
    let code: String

    var body: some View {
        PositionFormShell(title: "补录清仓", onCancel: { model.dismissModal() },
                          onSubmit: { Task { await model.submitClosePosition() } }) {
            Section {
                TextField("卖出价", text: $model.closeSellPrice)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
                // v1.3-①/⑥-B:实付卖出费用真数(可选,成交后回填)——周复盘对账用真数、
                // 不用估数;留空时服务端仍能按公式估算(诚实标注为估算)。
                TextField("实付卖出费用(可选,回填用真数)", text: $model.closeSellFees)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
                // v1.2-A2:离场原因 picker(可选;不选 → 服务端 NULL + 价格兜底判止损)。
                Picker("离场原因(可选)", selection: $model.closeReasonDraft) {
                    Text("不选(按价格兜底判定)").tag(CloseReasonCode?.none)
                    ForEach(CloseReasonCode.allCases) { reason in
                        Text(reason.label).tag(CloseReasonCode?.some(reason))
                    }
                }
            } footer: {
                Text("卖出时间缺省为今日;此处只记录真实成交,系统不代下单。离场原因用于熔断纪律统计(§2.1 第 7 条),不选时系统按 -5% 价格近似兜底判止损。实付卖出费用供周复盘对账用真数,可成交后再补填。")
            }
        }
    }
}
