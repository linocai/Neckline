//
//  ReviewView.swift
//  Neckline — 🔴 **复盘板块**(V2.1 三板块之一,双端共用;V2.1-⑦ 新建)。
//
//  **五页答五个不同的问题,⛔ 不合并**(`ReviewPage`;V2.2-④ 从三页扩到五页):
//    · **每日** —— 昨天那批篮子后来怎么样了(④ 昨日篮子复盘,自选股板块迁入;
//      数据源仍是 `model.basketDaily.reviews`,**随报告冻结、零新增网络调用、服务端零改动**)。
//    · **选股钟**(V2.2-④-A)—— 这批票**选**得对不对。🔴 样本 = D0 **全部** T1/T2,
//      **与用户买没买无关**(K8 §十四 第 3 条),D1 收盘验证一次即结案。
//    · **交易钟**(V2.2-④-B)—— 这笔买卖**做**得怎么样。**启动的唯一条件 = 实际买入**;
//      逐仓明细带写入口在「持仓」板块的持仓卡里,本页只给周度侧归因。
//      ⚠ 两个时钟**刻意分成两页**:样本域根本不同,并排一页会让人以为
//      「选股时钟里的篮子 = 我买过的票」—— 那正是 K8 反复强调的、最容易讲小的那个域。
//    · **累计** —— 这套选股长期成绩如何(`GET /review/overview` 八段 + 校准移交件出口)。
//    · **对账** —— 我实际的成交与计划 / 章程对不对得上(macOS 上传交割单;iOS 只读)。
//
//  **诚实披露纪律(本页每一段都受它约束,⛔ 不许退化)**:
//   1. 「**没有**」与「**没看**」永远分开渲染 —— 画像段缺席 = 系统那一步没跑(`available=false`);
//      对账段缺席 = 输入只能由用户给、系统查过确实没有(`available=true` + `found=false`)。
//      两者给用户的动作完全不同(等系统 vs 去上传),⛔ **别"统一"成一句「暂无数据」**。
//   2. `confidence == "low"` 的画像行**必须**显式写「样本不足,不给结论」,
//      ⛔ 不许把低置信度的数字当结论展示。
//   3. 观察项是**等证据的策略问题清单**,不是待办 —— ⛔ 不给"建议"、不改写 status。
//   4. 复盘板块**只呈现证据,⛔ 不做任何自动反馈回写选股**(用户裁定 #3:改包唯一通道
//      仍是「攒够样本 → 用户带材料去策略台 → 新 K 包 → 用户过门 → 四道闸激活」)。
//   5. 🔴 **四分类的 `klass == nil` + `klassStatus == "thresholds_undecided"` 是设计中的
//      状态**(K8 §十七 没给「多少样本算够」「差多少算失效」这两个数)—— 界面必须写
//      「分界线未定 · 待你拍板」并说清缺哪两个数,⛔ 不许显示成「暂无建议」或空白
//      (那会把「还没决定」讲成「没问题」),⛔ 也不许渲染成「观察」。
//

import SwiftUI
#if os(macOS)
import AppKit
#endif

struct ReviewView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(AppTab.review.title)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await model.loadReviewOverview() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
        }
        .task { await loadIfNeeded() }
        #else
        ScrollView {
            content.padding(NKSpace.pagePad).frame(maxWidth: 900)
        }
        .frame(maxWidth: .infinity)
        .background(NK.pageBg)
        .toolbar {
            ToolbarItem {
                Button { Task { await model.loadReviewOverview() } } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
        }
        .task { await loadIfNeeded() }
        #endif
    }

    /// 累计页 / 对账页(iOS)都吃这一份五段。**只在还没有时拉**——切页、切侧栏都不该
    /// 重打一次网络(⛔ 也别做自动轮询:这是回看件,不是盘中数据)。手动刷新在工具栏。
    private func loadIfNeeded() async {
        if model.reviewOverview == nil { await model.loadReviewOverview() }
        // V2.2-④-A 选股时钟结案列表:与五段是两条独立数据源(一条读周度落盘产物、
        // 一条读 `selection_clock` 结案行)—— ⛔ 别为了"少一次请求"把它并进 overview。
        if model.selectionClocks.isEmpty && !model.selectionClocksLoading {
            await model.loadSelectionClocks()
        }
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            header
            pagePicker
            switch model.reviewPage {
            case .daily: dailyPage
            case .selectionClock: selectionClockPage
            case .tradeClock: tradeClockPage
            case .cumulative: cumulativePage
            case .reconcile: reconcilePage
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            #if os(macOS)
            HStack(spacing: 8) {
                NKLogo(size: 24)
                Text(AppTab.review.title).font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            }
            #endif
            Text("复盘只呈现证据 · 改选股包永远走人工门禁(系统不自动回写策略)")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
        }
    }

    private var pagePicker: some View {
        Picker("", selection: Binding(get: { model.reviewPage },
                                      set: { model.reviewPage = $0 })) {
            ForEach(ReviewPage.allCases) { p in
                Text(p.title).tag(p)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }

    // MARK: - 每日:④ 昨日篮子复盘(自选股板块整段迁入;**三态逐字保留**)
    //
    // 🔴 段名「④ 昨日篮子复盘」**一字不动**:它与服务端 markdown 报告同构,是审计锚。
    // 换的只是挂载点(选股板块 → 复盘板块),数据、三态文案、行视图全部逐字照搬。

    @ViewBuilder
    private var dailyPage: some View {
        let daily = model.basketDaily
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "④ 昨日篮子复盘 \(daily.reviews.count)",
                            trailing: daily.reviewD0.map { "D0 \($0)" })
            if !model.hasReportData {
                NKCard {
                    NKEmptyState(title: "今日报告尚未生成",
                                 subtitle: "昨日复盘随每日报告一起冻结;今晚 16:35 出报告后自动显示。",
                                 systemImage: "moon.zzz")
                }
            } else if !daily.reviewsAvailable {
                NKCard {
                    NKEmptyState(title: "本次没跑复盘",
                                 subtitle: daily.reviewsUnavailableReason.map { "原因:\($0)" }
                                     ?? "这一段本次未取到(不是「昨日无篮子可复盘」)",
                                 systemImage: "exclamationmark.icloud")
                }
            } else if daily.reviews.isEmpty {
                NKCard {
                    HStack(spacing: 8) {
                        Image(systemName: "calendar.badge.clock").foregroundStyle(NK.textTertiary)
                        Text("昨日无篮子可复盘").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        Spacer()
                    }
                }
            } else {
                ForEach(daily.reviews) { r in
                    BasketReviewRow(review: r)
                }
            }
        }
    }

    // MARK: - 选股时钟(V2.2-④-A):**D0 全部 T1/T2 的结案件**
    //
    // 🔴 **样本口径必须当面讲清**:覆盖 D0 **全部** T1/T2,**与你买没买无关**
    // (K8 §十四 第 3 条)。⛔ 文案不许写成「你关注的篮子」之类 —— 那会把覆盖域讲小,
    // 而这条覆盖域正是它能当选股正确率样本的前提。

    @ViewBuilder
    private var selectionClockPage: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "选股时钟 · 已结案 \(model.selectionClocks.count)",
                            trailing: "D1 收盘验证一次即结案")
            Text("样本 = D0 当天全部 T1/T2 篮子,**与你买没买无关** · 结案 = 只结一次,之后不再改")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            if model.selectionClocksLoading && model.selectionClocks.isEmpty {
                NKCard { NKEmptyState(title: "正在取选股时钟…", systemImage: "hourglass") }
            } else if model.selectionClocks.isEmpty {
                NKCard {
                    // ⚠ 端点恒 200 → 空列表是**合法态**。⛔ 别写成「读取失败」,
                    // 也 ⛔ 别写成「系统没跑」(那要看每日复盘与段状态,是另一件事)。
                    NKEmptyState(title: "这段时间还没有结案样本",
                                 subtitle: "选股时钟在 D1 收盘验证后才结案;⛔ 这不等于「系统没跑」——那要看「每日」页的复盘段。",
                                 systemImage: "stopwatch")
                }
            } else {
                ForEach(model.selectionClocks) { c in
                    SelectionClockRow(clock: c)
                }
            }
            // 周度侧的正确率归因(读周度落盘产物,与上面的逐篮结案件是两条数据源)。
            if let ov = model.reviewOverview {
                ClockScoreboardCard(segment: ov.selectionClock)
            }
        }
    }

    // MARK: - 交易时钟(V2.2-④-B):**只在实际买入后存在**
    //
    // ⚠ 逐仓明细在「持仓」板块每张持仓卡里(`TradeClockSection`,带写入口);
    // 本页给的是**周度侧的六项归因** + 一行指路。⛔ 不在两处各画一遍逐仓流水。

    @ViewBuilder
    private var tradeClockPage: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "交易时钟 · 真实买入", trailing: "全部离场后结案")
            Text("启动的唯一条件是**你真的买了** · 与选股时钟的样本域不同:那边看「选得对不对」,这边看「做得怎么样」")
                .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            NKCard {
                Button { model.view = .positions } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "stopwatch").foregroundStyle(NK.accent)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("逐笔时钟与「补一条主观说明」在持仓卡里")
                                .font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                            Text("每张持仓卡底部展开即可 · 系统不会替你写那句话")
                                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.right").font(.system(size: 12))
                            .foregroundStyle(NK.textTertiary)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if let ov = model.reviewOverview {
                ClockScoreboardCard(segment: ov.tradeClock)
            } else {
                NKCard { NKEmptyState(title: "本次没取到周度归因", systemImage: "exclamationmark.icloud") }
            }
        }
    }

    // MARK: - 累计:五段 + **四分类建议** + 校准移交件出口

    @ViewBuilder
    private var cumulativePage: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            if let ov = model.reviewOverview {
                windowCard(ov)
                CalibrationSegmentCard(segment: ov.calibration)
                // V2.2-④-D 修改建议四分类(K8 §十七)。**只给建议、⛔ 零写回。**
                // ⚠ 紧跟校准段是刻意的:两段吃的是**同一份**周度落盘产物(校准产物的
                // `iteration` 段),四分类是校准结论的直接延伸;画像 / 对账是另外两件
                // 互不相干的事,隔开更不容易被读成"这些数都出自同一处"。
                IterationSegmentCard(segment: ov.iterationSuggestions)
                ProfileSegmentCard(segment: ov.preference)
                ProfileSegmentCard(segment: ov.capability)
                ReconcileSegmentCard(segment: ov.reconcile, showUploadHint: false)
                ObservationSegmentCard(segment: ov.observations)
                handoffSection
            } else if model.reviewOverviewLoading {
                NKCard { NKEmptyState(title: "正在取累计复盘…", systemImage: "hourglass") }
            } else {
                // ⚠ 端点恒 200 → 走到这里只可能是**网络 / 鉴权**没通,
                // ⛔ 不是「五段都没有」。
                NKCard {
                    NKEmptyState(title: "本次没取到累计复盘",
                                 subtitle: "这不是「没有累计数据」——是这次没连上服务端。",
                                 systemImage: "exclamationmark.icloud")
                }
            }
        }
    }

    /// 窗口卡 + **翻周**。⚠ 翻周不是装饰:周度校准产物是**周六离线作业**落的,
    /// 周一到周五看"本周"永远是「尚未生成」—— 没有这两个箭头,用户就永远看不到上周
    /// 那份已经算好的成绩单。
    private func windowCard(_ ov: ReviewOverview) -> some View {
        NKCard {
            HStack(spacing: 10) {
                Button { Task { await model.shiftReviewWeek(-1) } } label: {
                    Image(systemName: "chevron.left").font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.reviewWeekAnchor == nil ? "本期窗口" : "所选窗口")
                        .font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
                    Text(ov.weekStart.isEmpty && ov.weekEnd.isEmpty
                         ? "该周没有交易日" : "\(ov.weekStart) ~ \(ov.weekEnd)")
                        .font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                }
                Spacer()
                if !ov.weekKey.isEmpty { NKChip(text: ov.weekKey) }
                if model.reviewWeekAnchor != nil {
                    Button { Task { await model.shiftReviewWeek(nil) } } label: {
                        Text("回本周").font(.system(size: 11, weight: .semibold))
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                }
                Button { Task { await model.shiftReviewWeek(1) } } label: {
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
            }
        }
    }

    // —— 校准移交件出口(按需拉;⛔ 不随页面自动拉,它要读产物 + 拼 markdown)——

    @ViewBuilder
    private var handoffSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "校准移交件")
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    Text("一份能直接交给策略台的 markdown:窗口与样本量 / 校准报告全文 / "
                         + "画像两表 / 观察项清单 / 免责。攒够样本后由你带去策略台改包 —— "
                         + "系统不会自己改。")
                        .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let h = model.reviewHandoff {
                        handoffBody(h)
                    } else if model.reviewHandoffLoading {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("正在装配移交件…").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        }
                    } else {
                        Button { Task { await model.loadReviewHandoff() } } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "square.and.arrow.up").font(.system(size: 11))
                                Text("导出校准移交件").font(.system(size: 12.5, weight: .semibold))
                            }
                        }
                        .buttonStyle(.plain).foregroundStyle(NK.accent)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func handoffBody(_ h: ReviewHandoff) -> some View {
        if h.available {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    NKChip(text: h.windowLabel)
                    if !h.generatedAt.isEmpty {
                        Text("生成于 \(h.generatedAt)").font(.system(size: 10.5))
                            .foregroundStyle(NK.textTertiary)
                    }
                    Spacer()
                }
                // 「这一份带多少样本」先摆出来 —— 样本量是这份材料能不能当依据的前提。
                if !h.sampleN.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(h.sampleN.keys.sorted(), id: \.self) { k in
                            NKChip(text: "\(k) \(h.sampleN[k] ?? 0)")
                        }
                        Spacer()
                    }
                }
                HStack(spacing: 12) {
                    ShareLink(item: h.markdown,
                              preview: SharePreview(h.suggestedFilename)) {
                        HStack(spacing: 4) {
                            Image(systemName: "square.and.arrow.up").font(.system(size: 11))
                            Text("分享").font(.system(size: 12.5, weight: .semibold))
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    #if os(macOS)
                    Button { saveHandoff(h) } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "arrow.down.doc").font(.system(size: 11))
                            Text("存为 .md").font(.system(size: 12.5, weight: .semibold))
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    #endif
                    Spacer()
                }
                DisclosureGroup("预览全文(\(h.markdown.count) 字)") {
                    Text(h.markdown).font(.system(size: 11).monospaced())
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                .font(.system(size: 11.5))
                .foregroundStyle(NK.textSecondary)
            }
        } else {
            // ⛔ 服务端已把两种成因(还没生成 = 会自愈 / 读不出 = 不会自愈)写进
            // `unavailableReason`,这里**原样展示那句话**,别合并成一句「暂不可用」。
            NKEmptyState(title: "本期移交件不可用",
                         subtitle: h.unavailableReason ?? "未取得原因(服务端未给)",
                         systemImage: "doc.badge.clock")
        }
    }

    #if os(macOS)
    /// 存盘用 `NSSavePanel`(用户自己选位置),⛔ 不往固定目录偷偷写文件。
    private func saveHandoff(_ h: ReviewHandoff) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = h.suggestedFilename
        panel.allowedContentTypes = [.plainText]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try h.markdown.write(to: url, atomically: true, encoding: .utf8)
            model.showToast("已存到 \(url.lastPathComponent)")
        } catch {
            model.showToast("存盘失败:\(error.localizedDescription)", isError: true)
        }
    }
    #endif

    // MARK: - 对账:macOS = 完整工作台(拖入 / 周切换 / 违纪清单);iOS = 只读

    @ViewBuilder
    private var reconcilePage: some View {
        #if os(macOS)
        // ⛔ 不重写:整块复用既有工作台(解析 / FIFO 闭合 / 三查 / 统计全在后端)。
        ReviewWorkbenchView(model: model)
        #else
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            if let ov = model.reviewOverview {
                ReconcileSegmentCard(segment: ov.reconcile, showUploadHint: true)
            } else if model.reviewOverviewLoading {
                NKCard { NKEmptyState(title: "正在取本周对账…", systemImage: "hourglass") }
            } else {
                NKCard {
                    NKEmptyState(title: "本次没取到对账",
                                 subtitle: "这不是「本周没对账」——是这次没连上服务端。",
                                 systemImage: "exclamationmark.icloud")
                }
            }
        }
        #endif
    }
}

// MARK: - 昨日复盘一行(**整块自 `BasketDailyView.swift` 迁入,一字未改**)

struct BasketReviewRow: View {
    let review: BasketReview
    @State private var expanded = false

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text(review.name.isEmpty ? review.basketKey : review.name)
                        .font(.system(size: 13.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    if let t = review.tier { NKChip(text: "T\(t)") }
                    NKChip(text: review.depthLabel)
                    Spacer()
                    Text("D0 \(review.d0)").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
                if let text = review.llmText, !text.isEmpty {
                    // §2.7:LLM 叙述**原文整段呈现**,⛔ 不拆解塞回枚举卡片。
                    Text(text).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    NKReferenceNote()
                } else if let skip = review.llmSkipReason, !skip.isEmpty {
                    // **未生成**(预算耗尽 / 降级)—— ⛔ 不拿空串冒充「生成了但没内容」。
                    Text("本篮未生成人话复盘:\(skip)")
                        .font(.system(size: 11.5)).foregroundStyle(NK.amber)
                }
                if review.degraded {
                    Text("本次复盘降级:人话半份缺席,机械判照出")
                        .font(.system(size: 11)).foregroundStyle(NK.amber)
                }
                if let obj = review.mech.objectValue, !obj.isEmpty {
                    Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                        HStack(spacing: 4) {
                            Text(expanded ? "收起机械判" : "展开机械判(九项)")
                                .font(.system(size: 11.5, weight: .medium))
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 9))
                        }
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    if expanded { NKJSONTable(value: review.mech) }
                }
            }
        }
    }
}

// MARK: - 段壳:三态外壳(**有 / 没有 / 没取到** —— ⛔ 任何一段都不许省掉这一层)

private struct SegmentShell<Content: View>: View {
    let segment: ReviewSegment
    /// `available == false` 时的兜底标题(服务端没给 `label` 时用)。
    var fallbackTitle: String
    var emptyIcon: String = "questionmark.circle"
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: segment.label.isEmpty ? fallbackTitle : segment.label,
                            trailing: segment.asOf.isEmpty ? nil : segment.asOf)
            if segment.available {
                content
            } else {
                NKCard {
                    // 🔴 **「没取到」的原因原样展示**:服务端已经把"还没生成"(会自愈)与
                    // "读不出"(不会自愈)分成两句话写在这里,⛔ 客户端不合并、不改写。
                    NKEmptyState(title: "本段本次没取到",
                                 subtitle: segment.unavailableReason ?? "服务端未给原因",
                                 systemImage: emptyIcon)
                }
            }
        }
    }
}

// MARK: - 包成绩单 · 周度校准(读**离线落盘产物**,⛔ 在线不补算)

private struct CalibrationSegmentCard: View {
    let segment: ReviewSegment

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "包成绩单 · 周度校准",
                     emptyIcon: "doc.badge.clock") {
            let d = segment.detail
            let strata = d["strata"]?.arrayValue ?? []
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    Text("样本:\(d["nTradingDays"]?.intValue ?? 0) 个交易日 / "
                         + "\(d["nBaskets"]?.intValue ?? 0) 个篮子 · 分层 \(strata.count) 层")
                        .font(.system(size: 12, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    // ⚠ 拼接串(非字面量)**不走 SwiftUI 的 Markdown 解析** → 这里不放
                    // `**` / 反引号,否则界面上会原样出现那些符号。
                    Text("分层维度:pack_version × verification_ruleset_version —— "
                         + "换包会开新分层,这正是它能当归因分界线的原因。")
                        .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    // ⚠ `NKJSON` 不是 `Hashable`(它能装任意结构),故按下标做 id ——
                    // 产物里的 notes 顺序是确定的,下标当 id 稳定。
                    ForEach(Array((d["notes"]?.arrayValue ?? []).enumerated()), id: \.offset) { _, n in
                        Text("⚠ \(n.displayText)").font(.system(size: 10.5))
                            .foregroundStyle(NK.amber).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if strata.isEmpty {
                NKCard {
                    Text("算过了 · 本期无分层数据(窗口内没有可评的篮子)")
                        .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(Array(strata.enumerated()), id: \.offset) { _, s in
                    StratumCard(stratum: s)
                }
            }
            placeboCard(d["placebo"]?.arrayValue ?? [])
            NKCard {
                DisclosureGroup("展开校准产物全文(只读)") {
                    NKJSONTable(value: segment.detail)
                }
                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
            }
        }
    }

    /// 安慰剂对照臂:**两条基准线**(随机篮 / 满仓持有)。⛔ 不给"跑赢了就是有效"这类结论。
    @ViewBuilder
    private func placeboCard(_ arms: [NKJSON]) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                Text("安慰剂对照臂").font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(NK.textPrimary)
                if arms.isEmpty {
                    Text("本期无对照臂数据").font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                } else {
                    ForEach(Array(arms.enumerated()), id: \.offset) { _, a in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("包 \(a["packVersion"]?.stringValue ?? "—") · "
                                 + "\(a["nDays"]?.intValue ?? 0) 日 · 抽样 \(a["draws"]?.intValue ?? 0) 次")
                                .font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textPrimary)
                            armRow("真实臂", a["real"])
                            armRow("随机篮臂", a["randomArm"])
                            armRow("满仓持有臂", a["buyAndHoldArm"])
                            if let note = a["note"]?.stringValue, !note.isEmpty {
                                Text(note).font(.system(size: 10))
                                    .foregroundStyle(NK.textTertiary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func armRow(_ label: String, _ arm: NKJSON?) -> some View {
        HStack(spacing: 6) {
            Text(label).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                .frame(width: 76, alignment: .leading)
            Text("中位 " + nkPctText(arm?["median"]?.doubleValue))
                .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(NK.textSecondary)
            Text("· 均值 " + nkPctText(arm?["mean"]?.doubleValue))
                .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(NK.textSecondary)
            Text("· n=\(arm?["n"]?.intValue ?? 0)")
                .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(NK.textTertiary)
            Spacer()
        }
    }
}

/// 一层成绩单(`pack_version × verification_ruleset_version`)。
private struct StratumCard: View {
    let stratum: NKJSON

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text("包 \(stratum["packVersion"]?.stringValue ?? "—")")
                        .font(.system(size: 12.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    NKChip(text: "条件集 \(stratum["rulesetVersion"]?.stringValue ?? "—")")
                    Spacer()
                    NKChip(text: "\(stratum["nDays"]?.intValue ?? 0) 日 / \(stratum["nBaskets"]?.intValue ?? 0) 篮")
                }
                tierRow
                verificationRow
                tradableRow
            }
        }
    }

    /// 🔴 **Tier 单调性按本层数据里实际出现的档位渲染,⛔ 不写死 `(1,2,3)`**
    /// (V2.1-② 同一条教训:写死会在两档时代凭空多一行「T3 —(n=0)」,读起来像
    /// "T3 今天没样本",真相是 T3 已取消 —— 那正是把系统缺席讲成实质性结论;
    /// 而写死 `(1,2)` 又会让历史分层里真实存在的 T3 样本从成绩单上消失)。
    @ViewBuilder
    private var tierRow: some View {
        let t = stratum["tierMonotonicity"]
        let med = t?["median_outcome"]?.objectValue ?? [:]
        let obs = t?["observed"]?.objectValue ?? [:]
        let counts = t?["counts"]?.objectValue ?? [:]
        let tiers = Set(med.keys).union(obs.keys).union(counts.keys)
            .sorted { (Int($0) ?? 0) < (Int($1) ?? 0) }
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text("Tier 单调性").font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
                Text(monotonicText(t?["monotonic"])).font(.system(size: 11))
                    .foregroundStyle(NK.textSecondary)
                Spacer()
            }
            if tiers.isEmpty {
                Text("本层无档位样本").font(.system(size: 11)).foregroundStyle(NK.textSecondary)
            } else {
                ForEach(tiers, id: \.self) { k in
                    Text("T\(k) \(nkPctText(med[k]?.doubleValue))(n=\(obs[k]?.intValue ?? 0))")
                        .font(.system(size: 11).monospacedDigit()).foregroundStyle(NK.textSecondary)
                }
            }
            // §2.8-C 红线:Tier = 注意力优先级,不是收益预测 —— 单调性只是观察。
            Text("Tier = 注意力优先级,不是收益预测;单调性只是送进策略线的一个观察")
                .font(.system(size: 9.5)).foregroundStyle(NK.textTertiary)
        }
    }

    private func monotonicText(_ v: NKJSON?) -> String {
        guard let b = v?.boolValue else { return "判不了(样本不够)" }
        return b ? "成立" : "不成立"
    }

    @ViewBuilder
    private var verificationRow: some View {
        let v = stratum["verification"]
        let dist = v?["distribution"]?.objectValue ?? [:]
        VStack(alignment: .leading, spacing: 2) {
            Text("验证率").font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text("已验证 \(nkRatioText(v?["verified_rate"]?.doubleValue)) · "
                 + "被证伪 \(nkRatioText(v?["falsified_rate"]?.doubleValue))")
                .font(.system(size: 11).monospacedDigit()).foregroundStyle(NK.textSecondary)
            if !dist.isEmpty {
                Text("四态分布 " + dist.keys.sorted()
                        .map { "\($0) \(dist[$0]?.intValue ?? 0)" }.joined(separator: " · "))
                    .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(NK.textTertiary)
            }
            // `not_evaluated` **不进分母**:「今天还没判过」不是「判了是 unclear」。
            Text("not_evaluated \(v?["not_evaluated"]?.intValue ?? 0) 篮(不进分母)")
                .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
        }
    }

    @ViewBuilder
    private var tradableRow: some View {
        let tr = stratum["tradable"]
        VStack(alignment: .leading, spacing: 2) {
            Text("可交易收益").font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text("中位 \(nkPctText(tr?["median"]?.doubleValue)) · "
                 + "均值 \(nkPctText(tr?["mean"]?.doubleValue)) · "
                 + "胜率 \(nkRatioText(tr?["win_rate"]?.doubleValue))")
                .font(.system(size: 11).monospacedDigit()).foregroundStyle(NK.textSecondary)
            Text("成交 \(tr?["member_fills"]?.intValue ?? 0) 笔 · "
                 + "买不进 \(tr?["member_not_filled"]?.intValue ?? 0) 笔 · "
                 + "窗口未走完 \(tr?["member_unfinished"]?.intValue ?? 0) 笔(后两类不进均值)")
                .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - 画像段(偏好 / 能力两张账形状相同;**缺席 = 「没看」**)

private struct ProfileSegmentCard: View {
    let segment: ReviewSegment

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "画像",
                     emptyIcon: "person.crop.circle.badge.questionmark") {
            NKCard {
                if segment.items.isEmpty {
                    // ⚠ 「算过了但本期没有分组」与上面的「没跑过」讲的是两回事。
                    Text("算过了 · 本期无可展示的分组").font(.system(size: 12))
                        .foregroundStyle(NK.textSecondary)
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(segment.items.map(ProfileRow.init(raw:))) { row in
                            ProfileRowView(row: row)
                        }
                    }
                }
            }
        }
    }
}

/// 画像一行:**每行必带样本量 / 时间范围 / 置信度**;`low` 置信度**只写「样本不足,
/// 不给结论」,⛔ 不把数字摆出来当结论**(⑫ 验收条款)。
private struct ProfileRowView: View {
    let row: ProfileRow

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text("\(row.dimension) · \(row.bucket)")
                    .font(.system(size: 12, weight: .medium)).foregroundStyle(NK.textPrimary)
                Spacer()
                NKChip(text: "样本 \(row.sampleN)", tone: row.isLowConfidence ? .warn : .neutral)
            }
            Text("窗口 \(row.windowStart) ~ \(row.windowEnd)")
                .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
            if row.isLowConfidence {
                Text("样本不足,不给结论").font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(NK.amber)
            } else {
                ForEach(row.metricKeys, id: \.self) { k in
                    HStack {
                        Text(k).font(.system(size: 10.5).monospaced()).foregroundStyle(NK.textTertiary)
                        Spacer()
                        Text(row.raw[k]?.displayText ?? "—")
                            .font(.system(size: 10.5).monospaced()).foregroundStyle(NK.textSecondary)
                    }
                }
            }
        }
    }
}

// MARK: - 对账段(**缺席 = 「没有」,不是「没看」** —— 与画像段刻意判得不一样)

private struct ReconcileSegmentCard: View {
    let segment: ReviewSegment
    /// iOS 对账页要多说一句「上传在 macOS 端做」;累计页里的这一段不必重复。
    let showUploadHint: Bool

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "交割单对账", emptyIcon: "exclamationmark.icloud") {
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    if segment.found == true {
                        foundBody
                    } else {
                        // 🔴 这是**「没有」**:系统查过 `reviews` 表、确实没有这一行 ——
                        // 必需输入(券商交割单)只能由用户给,系统补不出没上传的那一份。
                        // ⛔ 别把它渲染成故障 / 「没取到」。
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "tray").foregroundStyle(NK.textTertiary)
                            Text(segment.note ?? "本周尚未上传交割单")
                                .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                    if showUploadHint {
                        Text("交割单上传请在 macOS 端做(拖入 .xlsx → 自动对账);"
                             + "iPhone 上只做只读查看。")
                            .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// 已有该周对账 → **只读**展示要点(违纪清单是这一页的主角)。
    /// ⛔ 客户端不重算任何判定(解析 / FIFO 闭合 / 三查 / 章程判定全在后端)。
    @ViewBuilder
    private var foundBody: some View {
        let result = segment.detail["result"]
        let violations = (result?["disciplineViolations"]?.arrayValue ?? [])
            .compactMap { $0.stringValue }
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text("第 \(segment.detail["week"]?.stringValue ?? segment.asOf) 周")
                    .font(.system(size: 12.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                if result?["forcedReview"]?.boolValue == true {
                    NKChip(text: "触发强制复盘", tone: .bad, filled: true)
                }
                Spacer()
                if let g = segment.detail["generatedAt"]?.stringValue, !g.isEmpty {
                    Text(g).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                }
            }
            Text("违纪清单 \(violations.count) 条").font(.system(size: 11, weight: .bold))
                .foregroundStyle(NK.textTertiary)
            if violations.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.seal.fill").foregroundStyle(NK.up)
                    Text("本周未发现违纪").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(Array(violations.enumerated()), id: \.offset) { _, v in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 10))
                            .foregroundStyle(NK.down).padding(.top, 2)
                        Text(v).font(.system(size: 12)).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if let m = segment.detail["material"]?.stringValue, !m.isEmpty {
                DisclosureGroup("复盘材料") {
                    Text(m).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
            }
        }
    }
}

// MARK: - 观察项段(**等证据的策略问题清单**,⛔ 不是待办、不给建议)

private struct ObservationSegmentCard: View {
    let segment: ReviewSegment

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "观察项 · 等证据的策略问题",
                     emptyIcon: "questionmark.circle") {
            if segment.items.isEmpty {
                NKCard {
                    Text("本期无登记的观察项").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(segment.items.map(ReviewObservation.init(raw:))) { o in
                    NKCard {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                NKChip(text: o.obsId)
                                Text(o.title).font(.system(size: 12.5, weight: .semibold))
                                    .foregroundStyle(NK.textPrimary)
                                Spacer()
                            }
                            if !o.question.isEmpty {
                                Text(o.question).font(.system(size: 11.5))
                                    .foregroundStyle(NK.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !o.evidenceNeeded.isEmpty {
                                Text("需要的证据:\(o.evidenceNeeded)")
                                    .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !o.status.isEmpty {
                                Text("状态:\(o.status)").font(.system(size: 10.5))
                                    .foregroundStyle(NK.amber)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
        }
    }
}

// MARK: - V2.2-④-A 选股时钟结案一行
//
// ⚠ **`tierAccuracy` 是四态原样**(verified/partial/unclear/falsified)加两个"没判"码,
// **⛔ 不是 0/1 的对错** —— 折成正确率是周度侧的事,这里一个数都不折。
// ⚠ **`regimeAtD0 == nil` = D0 当天没有行情状态行**(如实),⛔ 不猜、不显示成"正常"。

private struct SelectionClockRow: View {
    let clock: SelectionClock
    @State private var expanded = false

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    NKChip(text: "T\(clock.coveredTier)",
                           tone: clock.coveredTier == 1 ? .good : .warn, filled: true)
                    Text("篮子 #\(clock.basketId)").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(NK.textPrimary)
                    Spacer()
                    NKChip(text: clock.tierAccuracyLabel, tone: clock.tierAccuracyTone)
                }
                HStack(spacing: 6) {
                    NKChip(text: "D0 \(clock.d0Date)")
                    NKChip(text: "D1 \(clock.d1Date)")
                    if let e = clock.engineCode {
                        NKChip(text: "引擎 \(e)\(clock.engineVersion.map { " " + $0 } ?? "")")
                    }
                    Spacer()
                }
                // 「D0 无行情状态行」与「行情状态是 X」讲不同的话。
                if let r = clock.regimeAtD0 {
                    Text("D0 行情状态:\(r)").font(.system(size: 11))
                        .foregroundStyle(NK.textSecondary)
                } else {
                    Text("D0 当天无行情状态判定行(缺行 = 不知道,不猜)")
                        .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                }
                if let why = clock.untriggeredReason, !why.isEmpty {
                    Text("未触发:\(why)").font(.system(size: 11)).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                DisclosureGroup("展开 D1 九项验证(K8 §十四,只读冻结件)", isExpanded: $expanded) {
                    NKJSONTable(value: clock.mech)
                }
                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                Text("结案于 \(clock.closedAt) · 骨架 \(clock.skeletonVersion) · 验证条件集 \(clock.verificationRulesetVersion)")
                    .font(.system(size: 9.5)).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - 双时钟的周度归因段(读**周度落盘产物**,⛔ 在线不补算)

private struct ClockScoreboardCard: View {
    let segment: ReviewSegment

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "时钟归因", emptyIcon: "doc.badge.clock") {
            NKCard {
                DisclosureGroup("展开本期归因产物(只读)") {
                    NKJSONTable(value: segment.detail)
                }
                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
            }
        }
    }
}

// MARK: - V2.2-④-D 修改建议四分类(K8 §十七)
//
// 🔴 **只给建议,⛔ 零写回**(V2.1 裁定 #3 一字不变):系统攒证据、**用户拍板改包**。
// 唯一通道 = 带着移交件去策略台 → 新引擎版本 → 四道闸激活。
//
// 🔴🔴 **`klass == nil` + `klassStatus == "thresholds_undecided"` 是设计中的状态**:
// K8 §十七 只给了四个类别的定性描述,**没有给「多少样本算够」「差多少算失效」这两个数**。
// ⛔ **绝不许把它渲染成「暂无建议」或空白** —— 那会把「还没决定」讲成「没问题」;
// ⛔ 也不许渲染成「观察」——「还没决定」与「样本不足」是两件不同的事。
// 界面必须当面说:**缺的是哪两个数、由谁定、定完怎么生效**。

private struct IterationSegmentCard: View {
    let segment: ReviewSegment

    private var rows: [IterationSuggestion] { segment.items.map(IterationSuggestion.init(raw:)) }
    private var undecided: Bool { rows.contains(where: { $0.thresholdsUndecided }) }
    private var thresholds: NKJSON? { segment.detail["thresholds"] }

    var body: some View {
        SegmentShell(segment: segment, fallbackTitle: "修改建议 · 保留 / 观察 / 降权 / 淘汰",
                     emptyIcon: "slider.horizontal.3") {
            if undecided { undecidedBanner }
            if rows.isEmpty {
                NKCard {
                    Text("算过了 · 本期没有可分类的因素(窗口内没有结案样本)")
                        .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                }
            } else {
                ForEach(rows) { r in
                    NKCard {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                if r.thresholdsUndecided {
                                    // 🔴 **不是「暂无建议」**:统计量有,缺的是那两个数。
                                    NKChip(text: "分界线未定 · 待你拍板", tone: .warn, filled: true)
                                } else if let label = r.klassLabel {
                                    NKChip(text: label, tone: r.klassTone, filled: true)
                                }
                                Text(r.factor).font(.system(size: 12, weight: .semibold).monospaced())
                                    .foregroundStyle(NK.textPrimary)
                                Spacer()
                                Text("n=\(r.n)").font(.system(size: 11).monospacedDigit())
                                    .foregroundStyle(NK.textSecondary)
                            }
                            HStack(spacing: 6) {
                                if let e = r.engineCode {
                                    NKChip(text: "引擎 \(e)\(r.engineVersion.map { " " + $0 } ?? "")")
                                }
                                if let a = r.accuracy { NKChip(text: "正确率 \(nkRatioText(a))") }
                                if let d = r.delta {
                                    NKChip(text: String(format: "相对基线 %+.3f", d),
                                           tone: d > 0 ? .good : (d < 0 ? .bad : .neutral))
                                }
                                if let e = r.placeboEdge { NKChip(text: "安慰剂 \(e)") }
                                Spacer()
                            }
                            if !r.suggestion.isEmpty {
                                // 服务端把「缺哪两个数、该怎么定」整句写好了 —— **原样展示**。
                                Text(r.suggestion).font(.system(size: 11))
                                    .foregroundStyle(NK.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
            NKCard {
                VStack(alignment: .leading, spacing: 4) {
                    if let t = thresholds {
                        Text("本期分界线").font(.system(size: 11, weight: .bold))
                            .foregroundStyle(NK.textTertiary)
                        NKJSONTable(value: t)
                    }
                    if let d = segment.detail["disclaimer"]?.stringValue, !d.isEmpty {
                        Text(d).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("改包唯一通道是你带材料去策略台 —— 系统不会自己改选股包。")
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 🔴 分界线未定的整段说明。**这一段本身就是产品要说的话**,⛔ 不许省。
    private var undecidedBanner: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 12)).foregroundStyle(NK.amber)
                    Text("四分类的分界线还没定 —— 等你拍板")
                        .font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                    Spacer()
                }
                Text("统计量**已经算出来了**(下面每行的 n / 正确率 / 相对基线 / 安慰剂对照都是真的),缺的只是两个数:")
                    .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 3) {
                    numbered("min_n", "低于它一律判「观察:样本不足」——「多少样本算够」")
                    numbered("retire_min_n", "判「淘汰:持续失效」所需的最低样本量——「差多少算失效」")
                }
                Text("K8 §十七 只给了四个类别的定性描述,没有给这两个数。定好之后经四道闸写进骨架包 `config.iteration`,本段会自动开始给分类。**⛔ 系统不会替你选这两个数。**")
                    .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("⛔ 这**不是**「本期没有建议」,也**不是**「都判成观察」——是这两个数还没有人定。")
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func numbered(_ key: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(key).font(.system(size: 11, weight: .bold).monospaced())
                .foregroundStyle(NK.accent)
            Text(text).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - 小工具:比率 / 百分数的缺席安全格式化

/// 比例(0.0123)→ 百分数文本;`nil` → `—`(**⛔ 不显示 0.00%**:那是个实质性判断)。
func nkPctText(_ v: Double?) -> String {
    guard let v else { return "—" }
    return NKFmt.signedPct(v * 100)
}

/// 比率(0.4)→ 百分比文本(无符号);`nil` → `—`。
func nkRatioText(_ v: Double?) -> String {
    guard let v else { return "—" }
    return NKFmt.pct(v * 100)
}
