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
//  🔴 **这是全 App 唯一放图的地方**(`Neckline 信息卡与对账.dc.html` 27 行原话:
//  图表券商能做好,这里只保留三张**回答特定问题**的小图 —— 价格在哪 / 跑赢大盘没有 /
//  在行业里领先还是掉队)。⛔ **不加指标、不加副图。**
//
//  ⚠ **V2.3.1 批 5**:整页按原型 37–284 重画(两列网格 / 卡内页边距 / 图注 / 快照四列)。
//  本文件**双端共用**:改动同时改到 iOS,iOS 侧实拍核对归批 7。
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
        #if os(macOS)
        // 原型 38–48 行:整页是**自己的一扇窗**(标题条 + 关闭),不是 `NavigationStack`
        // 的推入页 —— 故用与四个弹层同一套 `NKSheetShell` 壳,左侧位留空、右侧「关闭」。
        VStack(spacing: 0) {
            infoCardTitleBar
            ScrollView {
                content
                    .padding(.horizontal, NKSpace.pagePadWide)
                    .padding(.top, NKSpace.pagePad)
                    .padding(.bottom, NKSpace.pagePadBottom)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(NK.pageBg)
        #else
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(request.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.dismissInfoCard() }
                }
            }
        }
        #endif
    }

    #if os(macOS)
    /// 原型 39–47 行:`height:50px` 的标题条,中间「信息卡 · <名称>」,右端「关闭」。
    private var infoCardTitleBar: some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: 60)          // 左侧留白,保证标题真正居中
            Text("信息卡 · \(request.name)").font(NKFont.headline)
                .foregroundStyle(NK.textPrimary)
                .frame(maxWidth: .infinity)
            Button { model.dismissInfoCard() } label: {
                Text("关闭").font(NKFont.body).foregroundStyle(NK.accent)
                    .frame(width: 60, alignment: .trailing)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .frame(height: 50)
        .background(Color(hex: 0xF7F7FA))
        .overlay(alignment: .bottom) { Rectangle().fill(NK.hairline).frame(height: 0.5) }
    }
    #endif

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            header
            if model.infoCardLoading && model.infoCard == nil {
                NKCard {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("加载信息卡…").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                }
            } else if let err = model.infoCardError {
                NKCard {
                    NKEmptyState(title: "信息卡加载失败", subtitle: err, systemImage: "exclamationmark.triangle")
                }
            } else if let card = model.infoCard {
                basketCard(card)          // V2-⑬-N:所属篮子 / 本票角色 / 同篮对比
                tagsCard(card)            // V2-⑬-N-K7:成员标注件(参考、非指令)
                klineCard(card)
                // 原型 158–184 行:RS 线与行业分歧线**并排两列**(K 线通栏)。
                twoUp(rsLineCard(card), industryDivergenceCard(card))
                snapshotCard(card)
                twoUp(k4FlagsCard(card), newsCard(card))
                twoUp(topListCard(card), marketCard(card))
                auditEntry
            }
        }
    }

    /// 两列等宽网格(原型 `grid-template-columns:1fr 1fr; gap:16px`)。
    /// ⚠ iOS 402pt 宽放不下两列 → 手机上退回上下两块(⛔ 别在窄屏硬塞两列)。
    @ViewBuilder
    private func twoUp<A: View, B: View>(_ a: A, _ b: B) -> some View {
        #if os(macOS)
        // ⚠ 两列**等高**(原型是 CSS grid,同一行天生等高)。SwiftUI 里 `HStack` 按各自
        // 内容高 —— 一边有说明句一边没有时两张卡会差出一截;`Grid` 的一行才会拉平。
        Grid(horizontalSpacing: NKSpace.cardGap, verticalSpacing: 0) {
            GridRow {
                a.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                b.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        #else
        a
        b
        #endif
    }

    /// 原型 52–63 行:左「名称 26/700 + 代码 · 交易日」,右「末根收盘 + 当日涨跌」。
    ///
    /// 🔴 **两处刻意与原型不同,都是"契约支撑不了"**:
    /// ① 原型副标题里的「主板」—— `InfoCardOut` **没有 `board` 字段**,而
    ///    CLAUDE.md 明文「中文展示走客户端纯展示层换算,⛔ **不要客户端重推分类**」;
    /// ② 原型右上那个 30px 大数标的是「现价」—— 契约里同样没有这个字段。
    ///    这里改用**K 线序列末根的收盘**并**当面写清是什么**(前复权 · 该根日期),
    ///    ⛔ 不把它冒充成现价;K 线不可用时整块不画。
    private var header: some View {
        HStack(alignment: .bottom, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text(request.name).font(NKFont.title1).foregroundStyle(NK.textPrimary)
                    .tracking(-0.4)
                Text("\(request.code) · 交易日 \(model.calendar.displayString(request.tradeDate))")
                    .font(NKFont.callout.monospacedDigit()).foregroundStyle(NK.textSecondary)
            }
            Spacer(minLength: 8)
            if let card = model.infoCard, card.klineAvailable, let last = card.kline.last {
                VStack(alignment: .trailing, spacing: 3) {
                    Text(NKFmt.price(last.close))
                        .font(NKFont.heroNumber).tracking(-0.8)
                        .foregroundStyle(NK.textPrimary)
                    if let chg = lastBarChangePct(card.kline) {
                        Text(NKFmt.signedPct(chg))
                            .font(NKFont.body.monospacedDigit()).fontWeight(.semibold)
                            .foregroundStyle(chg >= 0 ? NK.up : NK.down)
                    }
                    Text("K 线末根收盘 · 前复权 · \(last.tradeDate)")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
            }
        }
    }

    /// 末根相对前一根的收盘涨跌(**两个数都来自本页正在画的那条序列**,不另取数据源)。
    private func lastBarChangePct(_ bars: [InfoCardKlineBar]) -> Double? {
        guard bars.count >= 2 else { return nil }
        let prev = bars[bars.count - 2].close
        guard prev > 0 else { return nil }
        return (bars[bars.count - 1].close / prev - 1) * 100
    }

    // MARK: - V2-⑬-N ①所属篮子与共同驱动 ②本票角色(含对拍分歧)③同篮成员对比

    @ViewBuilder
    private func basketCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("所属篮子").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    if card.basket.available && card.basket.isPrimary {
                        NKChip(text: "主归属", tone: .good)
                    }
                }
                .padding(.bottom, 11)

                if !card.basket.available {
                    // ⛔ 「不在任何篮子里」与「在篮子里但卡没生成」**两态分得开**,
                    // 不许显示成同一句话。
                    unavailableRow(card.basket.unavailableText ?? "篮子信息暂不可用")
                } else {
                    HStack(spacing: 8) {
                        Text(card.basket.name).font(NKFont.headline)
                            .foregroundStyle(NK.textPrimary)
                        if let t = card.basket.tier {
                            NKChip(text: "T\(t)", tone: t == 1 ? .good : .info, filled: true)
                        }
                        Spacer(minLength: 0)
                    }
                    if !card.basket.driver.isEmpty {
                        Text("共同驱动:\(card.basket.driver)")
                            .font(NKFont.callout).lineSpacing(4)
                            .foregroundStyle(NK.textPrimary.opacity(0.65))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 7)
                    }
                    if !card.basket.whyNow.isEmpty {
                        Text("为什么是现在:\(card.basket.whyNow)")
                            .font(NKFont.callout).lineSpacing(4)
                            .foregroundStyle(NK.textPrimary.opacity(0.65))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 5)
                    }
                    // **角色两说并存**:冲突时两个都显示,⛔ 不挑一个当正确答案。
                    HStack(spacing: 5) {
                        // ⚠ 两说并存这两枚也要换算(V2.3.1 §〇c 硬伤 2):原来直接插
                        // 服务端原值 → 卡上印 `LLM:leader` / `机械:core`。
                        if card.basket.roleConflict {
                            NKChip(text: "LLM:\(nkRoleLabelOrDash(card.basket.roleLlm))", tone: .warn)
                            NKChip(text: "机械:\(nkRoleLabelOrDash(card.basket.roleMech))", tone: .warn)
                            Text("两说并存").font(NKFont.caption).foregroundStyle(NK.amber)
                        } else {
                            NKChip(text: card.basket.roleDisplay)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.top, 9)
                    if !card.basket.roleReason.isEmpty {
                        Text(card.basket.roleReason).font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 4)
                    }
                    if !card.basket.peers.isEmpty { peerTable(card.basket.peers) }
                }
            }
        }
    }

    /// 原型 71–78 行的同篮对比表。
    ///
    /// 🔴 **两处刻意与原型不同**(都是契约支撑不了,§五 〇 同族):
    /// ① 原型有「今日」涨跌一列 —— `InfoCardBasketPeer` **没有当日涨跌**字段;
    /// ② 原型把**本票自己**也排进表里做对比 —— 契约里本票没有 `rsRank`
    ///    (`InfoCardBasket` 无此字段),硬排进去那一格只能空着。
    /// 故表头照实写「同篮其他成员」,三列 = 成员 / RS / 角色。
    private func peerTable(_ peers: [InfoCardBasketPeer]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.top, 13)
            Text("同篮其他成员").nkLabel().foregroundStyle(NK.textTertiary)
                .padding(.top, 12).padding(.bottom, 9)
            HStack(spacing: 0) {
                Text("成员").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("RS").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .frame(width: 66, alignment: .trailing)
                Text("角色").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .frame(width: 78, alignment: .trailing)
            }
            .padding(.bottom, 6)
            Rectangle().fill(NK.hairline).frame(height: 0.5)
            ForEach(peers) { p in
                HStack(spacing: 0) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(p.name).font(NKFont.callout)
                            .foregroundStyle(NK.textPrimary.opacity(0.75))
                        Text(p.tsCode).font(NKFont.caption.monospacedDigit())
                            .foregroundStyle(NK.textTertiary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Text(p.rsRank.map { "#\($0)" } ?? "—")
                        .font(NKFont.callout.monospacedDigit())
                        .foregroundStyle(NK.textPrimary.opacity(0.65))
                        .frame(width: 66, alignment: .trailing)
                    // 原型 76 行:对拍分歧那一行在**表格里**只写「两说」(琥珀 / 600)——
                    // 78pt 的一列放不下「LLM:龙头 / 机械:跟随」那一整串(实拍逮到:压成两行)。
                    // ⚠ **信息没有丢**:两说各是什么就在上方那张卡的两枚徽标里,
                    // 这里还挂了 tooltip;⛔ 但表格里绝不挑一个当正确答案。
                    Text(p.roleConflict ? "两说" : p.roleDisplay).font(NKFont.callout)
                        .fontWeight(p.roleConflict ? .semibold : .regular)
                        .foregroundStyle(p.roleConflict ? NK.amber : NK.textSecondary)
                        .lineLimit(1)
                        .help(p.roleDisplay)
                        .frame(width: 78, alignment: .trailing)
                }
                .padding(.vertical, 8)
                .overlay(alignment: .bottom) {
                    if p.id != peers.last?.id {
                        Rectangle().fill(NK.hairline.opacity(0.5)).frame(height: 0.5)
                    }
                }
            }
        }
    }

    /// ⑬-N-K7 标注件。`text` **已含「参考、非指令」后缀,不改写、不截断**;
    /// `tagsAbsent`(判不了的码)与「判过没命中」是两回事,⛔ 不合并成"没有标注"。
    @ViewBuilder
    private func tagsCard(_ card: InfoCard) -> some View {
        if !card.tags.isEmpty || !card.tagsAbsent.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 0) {
                    Text("成员标注件 · K7").nkLabel().foregroundStyle(NK.textTertiary)
                        .padding(.bottom, 12)
                    ForEach(Array(card.tags.enumerated()), id: \.offset) { idx, t in
                        if idx > 0 {
                            Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.vertical, 10)
                        }
                        HStack(alignment: .top, spacing: 9) {
                            NKChip(text: t.label, tone: t.axisTone)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(t.text).font(NKFont.callout).lineSpacing(3)
                                    .foregroundStyle(NK.textPrimary)
                                    .fixedSize(horizontal: false, vertical: true)
                                if !t.source.isEmpty {
                                    Text(t.source).font(NKFont.caption)
                                        .foregroundStyle(NK.textTertiary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                    }
                    if !card.tagsAbsent.isEmpty {
                        Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.top, 12)
                        // 🔴 **硬伤 2 的第九处**(V2.3.1 批 5 实拍逮到):`tagsAbsent` 是
                        // **服务端英文码**(`pullback_leader` / `warn_streak_top` /
                        // `warn_chase_zone`,源 `selection/member_tags.py`)—— 原来直接插进
                        // 中文正文里印上屏。⛔ **不在客户端补一张中文表**:人读文案的唯一源
                        // 是服务端 `member_tags.tag_label()`(命中项已经在发 `label` 了),
                        // 补表 = 给同一个标签造第二份中文。按批 3 clamp 的先例:
                        // **正文说人话、机器码降 `monoKey` 等宽灰字**,并把根治登记出去
                        // (服务端 `tags_absent` 应改发 `{code,label}`)。
                        // ⚠ 这一句原来还写成 `Text("…\(插值)…**不等于**…")` —— `Text(String)`
                        // **不解析 Markdown**,星号会原样上屏(§五 〇d 第 7 条)。
                        Text("判不了的标注 \(card.tagsAbsent.count) 项 —— 数据缺失,**不等于**没命中")
                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 11)
                        Text(card.tagsAbsent.joined(separator: " · "))
                            .font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 3)
                    }
                    NKReferenceNote().padding(.top, 9)
                }
            }
        }
    }

    // MARK: - ① K 线(60 日,蜡烛 + 量柱 + MA20/MA250)

    @ViewBuilder
    private func klineCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("K 线 · 60 日前复权").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    // 原型 130–133 行:图例是两段 12×2 的**线段**(不是圆点)。
                    legendLine(NK.accent, "MA20")
                    legendLine(NK.textTertiary.opacity(0.9), "MA250")
                }
                .padding(.bottom, 14)
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

    private func legendLine(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(color).frame(width: 12, height: 2)
            Text(label).font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
    }

    // MARK: - ② RS 线(相对大盘,60 日,起点归一 100)

    @ViewBuilder
    private func rsLineCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("RS 线 · 相对大盘").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    Text("基准 \(card.rsBenchmark) · 起点归一 100")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
                .padding(.bottom, 12)
                if !card.rsAvailable || card.rsLine.isEmpty {
                    unavailableRow(card.rsUnavailableReason ?? "无数据")
                } else {
                    IndexLineChartView(points: card.rsLine, color: NK.accent, height: 110)
                }
            }
        }
    }

    // MARK: - ③ 行业分歧线(个股/行业成员中位数合成,60 日)

    @ViewBuilder
    private func industryDivergenceCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("行业分歧线").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    if !card.industry.isEmpty {
                        Text(card.industry).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                }
                .padding(.bottom, 12)
                if !card.industryDivergenceAvailable || card.industryDivergenceLine.isEmpty {
                    unavailableRow(card.industryDivergenceUnavailableReason ?? "无数据")
                } else {
                    // 用 amber 与 RS 线的 accent 区分开(两图并排,颜色只为"两条线不撞色",
                    // 不承载额外语义)。
                    IndexLineChartView(points: card.industryDivergenceLine, color: NK.amber,
                                       height: 110)
                }
                Text(card.industryDivergenceNote).font(NKFont.caption).lineSpacing(3)
                    .foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)
            }
        }
    }

    // MARK: - ④ 快照七项

    @ViewBuilder
    private func snapshotCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                Text("快照 · 七项").nkLabel().foregroundStyle(NK.textTertiary)
                    .padding(.bottom, 14)
                // 原型 168 行:`repeat(4,1fr); gap:18px 20px`。iOS 窄屏退回两列。
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 20),
                                         count: snapshotColumns),
                          alignment: .leading, spacing: 18) {
                    metric("量比(5日)", card.snapshot.volRatio5.map { String(format: "%.2f", $0) } ?? "—")
                    metric("换手率", card.snapshot.turnoverRate.map { NKFmt.pct($0) } ?? "—")
                    metric("行业强度排名", card.snapshot.industryRank.map { "#\($0)" } ?? "未参与排名")
                    // v1.4-⑩-E:`nil` = 行业强度表当日无数据(「没看」),显示「数据未就绪」
                    // 而不是「0 天」(「0 天」是「看了,不是强度日」,两者不能混)。
                    metric("行业强度持续天数",
                           card.snapshot.industryPersistDays.map { "\($0) 天" } ?? "数据未就绪",
                           amber: card.snapshot.industryPersistDays == nil)
                    metric("年线位置", yearLineText(card.snapshot), compact: true)
                    metric("距 20 日高点",
                           card.snapshot.distFromHigh20dPct.map { NKFmt.signedPct($0 * 100) } ?? "—")
                    metric("连续涨停天数", "\(card.snapshot.consecLimitUpDays) 天")
                }
                Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.top, 14)
                // 🔴 温和带**只在命中时**才出现一枚琥珀徽标(原型 178 行原话);
                // 未命中时整枚不显示 —— 而这句话把"为什么这里什么都没有"说出口。
                if card.mildBand {
                    NKChip(text: "温和带(低方差核心带,≈0 期望、非正 alpha)", tone: .warn)
                        .padding(.top, 12)
                } else {
                    Text("「温和带(低方差核心带,≈0 期望、非正 alpha)」只在**命中时**才出现一枚琥珀徽标 —— 本次未命中,故整枚不显示。")
                        .font(NKFont.caption).lineSpacing(3)
                        .foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 12)
                }
                if card.snapshot.industryPersistDays == nil {
                    NKTintedNote(text: "「数据未就绪」= 行业强度表当日无数据(**没看**)。⛔ 不是「0 天」—— 0 天是「看了,今天不是强度日」,两者不能混。",
                                 tone: .warn)
                        .padding(.top, 14)
                }
            }
        }
    }

    private var snapshotColumns: Int {
        #if os(macOS)
        return 4
        #else
        return 2
        #endif
    }

    private func yearLineText(_ s: InfoCardSnapshot) -> String {
        guard let above = s.aboveMa250 else { return "未就绪(历史不足250日)" }
        let distText = s.distFromMa250Pct.map { NKFmt.signedPct($0 * 100) } ?? ""
        return (above ? "年线上方 " : "年线下方 ") + distText
    }

    /// 原型 169 行:`11px .55` 标题 + `18px/600 tabular` 数值(长文本那一格降到 15)。
    private func metric(_ label: String, _ value: String,
                        amber: Bool = false, compact: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value)
                .font(compact || amber
                      ? NKFont.headline.monospacedDigit()
                      : NKFont.metric.monospacedDigit())
                .fontWeight(.semibold)
                .foregroundStyle(amber ? NK.amber : NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - ⑤ 红黄牌

    @ViewBuilder
    private func k4FlagsCard(_ card: InfoCard) -> some View {
        if !card.k4Flags.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 0) {
                    Text("红黄牌").nkLabel().foregroundStyle(NK.textTertiary)
                        .padding(.bottom, 12)
                    ForEach(Array(card.k4Flags.enumerated()), id: \.offset) { idx, flag in
                        if idx > 0 {
                            Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.vertical, 11)
                        }
                        HStack(alignment: .top, spacing: 9) {
                            NKChip(text: flag.sectionLabel, tone: flag.sectionTone, filled: true)
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(alignment: .firstTextBaseline, spacing: 5) {
                                    Text(flag.label).font(NKFont.callout).fontWeight(.semibold)
                                        .foregroundStyle(NK.textPrimary)
                                    Text(nkK4LevelLabel(flag.level)).font(NKFont.caption)
                                        .foregroundStyle(NK.textTertiary)
                                    if flag.evidenceStrength == "constituent" {
                                        Text("参考").font(NKFont.caption)
                                            .foregroundStyle(NK.textTertiary)
                                    }
                                }
                                if !flag.evidence.isEmpty {
                                    Text(flag.evidence).font(NKFont.caption).lineSpacing(2)
                                        .foregroundStyle(NK.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
        }
    }

    /// 红黄牌的 `level` 是**服务端英文码**(`strong` / `normal`)。
    /// ⚠ 原来写的是 `flag.level == "strong" ? "强" : "普通"` —— 第三种取值会被**静默
    /// 讲成「普通」**(硬伤 2 那一族的同款:未识别值不许猜)。⛔ 未识别值原样透传。
    private func nkK4LevelLabel(_ raw: String) -> String {
        switch raw {
        case "strong": return "强"
        case "normal": return "普通"
        default: return raw
        }
    }

    // MARK: - ⑥ 消息面

    @ViewBuilder
    private func newsCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                Text("消息面").nkLabel().foregroundStyle(NK.textTertiary)
                    .padding(.bottom, 12)
                if !card.news.scanned {
                    unavailableRow(card.news.unavailableReason ?? "本次未扫描")
                } else if card.news.items.isEmpty {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle").font(.system(size: 12))
                        Text("已扫描,当前无命中 = 确认无消息").font(NKFont.callout)
                    }
                    .foregroundStyle(NK.up)
                } else {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(card.news.items) { item in
                            HStack(alignment: .top, spacing: 6) {
                                NKChip(text: item.categoryLabel, tone: .bad)
                                Text(item.summary).font(NKFont.callout)
                                    .foregroundStyle(NK.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Spacer(minLength: 0)
                            }
                        }
                    }
                }
                // 原型 200 行:三态各说各的话 —— ⛔ 前两者绝不能渲染成同一句。
                NKTintedNote(text: "三态各说各的话:**没扫**(本行)· **扫了、无命中**(会写成「已扫描,当前无命中 = 确认无消息」)· **扫了、有命中**(逐条列出)。⛔ 前两者绝不能渲染成同一句。",
                             tone: .warn)
                    .padding(.top, 12)
            }
        }
    }

    // MARK: - ⑦ 龙虎榜

    @ViewBuilder
    private func topListCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("龙虎榜").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    Text("近 5 日查到 \(card.topList.lookbackDaysCovered) 天,命中 \(card.topList.lookbackHitDays) 天")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                }
                .padding(.bottom, 12)
                if card.topList.onListToday {
                    HStack(spacing: 11) {
                        NKChip(text: "今日上榜", tone: .warn, filled: true)
                        if let net = card.topList.netAmount {
                            Text("净额 \(NKFmt.signedMoney(net))")
                                .font(NKFont.callout.monospacedDigit())
                                .foregroundStyle(NK.textPrimary.opacity(0.65))
                        }
                        if let rate = card.topList.netRate {
                            Text("净占比 \(NKFmt.signedPct(rate * 100))")
                                .font(NKFont.callout.monospacedDigit())
                                .foregroundStyle(NK.textPrimary.opacity(0.65))
                        }
                        Spacer(minLength: 0)
                    }
                } else {
                    // ⛔ 未上榜时**不留空**:服务端给的具体原因优先,没给才用兜底句。
                    Text(card.topList.reason ?? "今日未上榜").font(NKFont.callout)
                        .foregroundStyle(NK.textSecondary)
                }
            }
        }
    }

    // MARK: - ⑧ 市场语境

    @ViewBuilder
    private func marketCard(_ card: InfoCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text("市场语境").nkLabel().foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    Text(card.market.indexCode).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary)
                }
                .padding(.bottom, 12)
                if card.market.indexLine.isEmpty {
                    unavailableRow("无数据")
                } else {
                    IndexLineChartView(points: card.market.indexLine,
                                       color: NK.textSecondary, height: 74, showBaseline: false)
                }
                Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.top, 11)
                HStack(alignment: .top, spacing: 18) {
                    smallMetric("涨停家数", "\(card.market.limitUpCount)")
                    smallMetric("跌停家数", "\(card.market.limitDownCount)")
                    smallMetric("大盘 MA20", card.market.aboveMa20.map { $0 ? "上方" : "下方" } ?? "—")
                    Spacer(minLength: 0)
                }
                .padding(.top, 11)
            }
        }
    }

    /// 原型 216–218 行:`10.5px .55` 标题 + `15px/600 tabular` 数值(比快照那档小一号)。
    private func smallMetric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value).font(NKFont.headline.monospacedDigit())
                .foregroundStyle(NK.textPrimary)
        }
    }

    /// 原型 275 行:虚线描边按钮。⚠ **本版还没有这一页**(审计视图是既有的
    /// `NKAuditSection` 形态,挂在篮子卡里)—— 这里不画一个点不开的按钮冒充有,
    /// 改成一句说明:它说清「原始件在哪儿看」。
    private var auditEntry: some View {
        NKInlineNote(text: "快照原始件与各路数据源可用性:见篮子卡里的**审计视图**(本页各卡的「本次未取得」已逐路写明原因)。")
    }

    // MARK: - 共用:诚实缺省行(不画空图、不画 0 值线)

    private func unavailableRow(_ reason: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "info.circle").font(.system(size: 11)).foregroundStyle(NK.textTertiary)
            Text(reason).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
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

    /// 量柱带(原型 156 行:`display:flex; gap:1px; height:26px; align-items:flex-end`)。
    private var volumeBand: some View {
        let maxVol = max(points.map { $0.bar.vol }.max() ?? 1, 1)
        return HStack(alignment: .bottom, spacing: 1) {
            ForEach(points) { p in
                Rectangle()
                    .fill((p.bar.isUp ? NK.up : NK.down).opacity(0.30))
                    .frame(maxWidth: .infinity)
                    .frame(height: max(2, 26 * p.bar.vol / maxVol))
            }
        }
        .frame(height: 26, alignment: .bottom)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
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
                        .lineStyle(StrokeStyle(lineWidth: 1.8))
                        .interpolationMethod(.linear)
                }
                ForEach(points.filter { $0.bar.ma250 != nil }) { p in
                    LineMark(x: .value("日期", p.date), y: .value("MA250", p.bar.ma250 ?? 0))
                        // 原型 138 行 MA250 是 `rgba(60,60,67,.35)` 的**灰**线(⛔ 不是琥珀:
                        // 琥珀在本 App 里是"警示 / 降级"的语义色,年线不是警示)。
                        .foregroundStyle(NK.textTertiary.opacity(0.9))
                        .lineStyle(StrokeStyle(lineWidth: 1.5))
                        .interpolationMethod(.linear)
                }
            }
            .chartYScale(domain: .automatic(includesZero: false))
            .chartPlotStyle { $0.frame(height: 190) }
            // 原型 118–120 行:只有**三条水平浅灰网格线**,没有竖网格。
            // ⚠ 刻度标签**保留**(判「刻意不同 · 平台差异」):原型那张 SVG 是纯装饰位图,
            // 落地的图没有刻度就读不出价位在哪 —— 而"价格在哪"正是这张图要回答的问题。
            .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) { AxisValueLabel() } }
            .chartYAxis {
                AxisMarks(values: .automatic(desiredCount: 3)) {
                    AxisGridLine().foregroundStyle(NK.hairline)
                    AxisValueLabel()
                }
            }

            // 原型 156 行的量柱**根本不是一张图**:它是一行 `flex` 的 div,
            // `align-items:flex-end` + 每根 `height:xx%`。
            // 🔴 用第二个 `Chart` 画它在 26–34pt 的高度上**渲染成一条空白**(实拍两次逮到)
            // —— 换成与原型同构的一排 `Rectangle`,既画得出来也更贴原型。
            volumeBand
        }
    }
}

// MARK: - 指数化折线图(RS 线 / 行业分歧线 / 市场语境指数线共用,起点归一 100)

private struct IndexLineChartView: View {
    let points: [InfoCardIndexPoint]
    var color: Color = NK.accent
    var height: CGFloat = 130
    /// 原型 161 / 172 行有一条 `100`(或 `0`)的虚线基准;市场语境那张(209 行)**没有**。
    var showBaseline: Bool = true

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
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    .interpolationMethod(.linear)
            }
            if showBaseline {
                RuleMark(y: .value("基准 100", 100))
                    .foregroundStyle(NK.textTertiary.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 4]))
            }
        }
        // 🔴 **`includesZero: false` 缺一不可**(V2.3.1 批 5 实拍逮到):默认 y 轴带上 0,
        // 一条 100→118 的线会被压成一条**几乎水平的直线** —— 图还在,但它要回答的问题
        // (跑赢大盘没有 / 斜率有没有转陡)在图上完全看不出来。
        .chartYScale(domain: .automatic(includesZero: false))
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) { AxisValueLabel() } }
        .chartYAxis {
            AxisMarks(values: .automatic(desiredCount: 3)) {
                AxisGridLine().foregroundStyle(NK.hairline)
                AxisValueLabel()
            }
        }
        .frame(height: height)
    }
}
