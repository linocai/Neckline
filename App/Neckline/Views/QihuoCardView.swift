// 单张 Q 期货可展开信息卡片：严格对齐已确认 HTML 样图（收起态 4 格快照 + 展开态全部/消息/数据/持仓 + 底部基本信息二次折叠）。

import SwiftUI

struct QihuoProductCard: View {
    let product: QihuoProduct
    let isExpanded: Bool
    let selectedTab: QihuoCardTab
    let isBasicExpanded: Bool
    let onToggleCard: () -> Void
    let onSelectTab: (QihuoCardTab) -> Void
    let onToggleBasic: () -> Void

    @Environment(\.horizontalSizeClass) private var hSize

    private var isCompact: Bool {
        #if os(iOS)
        return hSize != .regular
        #else
        return false
        #endif
    }

    var body: some View {
        VStack(spacing: 0) {
            cardHeader
            if isExpanded {
                Divider().overlay(NKQihuo.hairline)
                expandedBody
            }
        }
        .background(NKQihuo.card)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(isExpanded ? NKQihuo.openBorder : NKQihuo.hairline, lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(isExpanded ? 0.06 : 0.03), radius: isExpanded ? 10 : 2, y: isExpanded ? 4 : 1)
    }

    // MARK: 收起态（可点击展开/收起）

    private var cardHeader: some View {
        Button(action: onToggleCard) {
            VStack(alignment: .leading, spacing: 11) {
                HStack(alignment: .center, spacing: 10) {
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 6) {
                            Text(product.name)
                                .font(.system(size: 17, weight: .semibold))
                                .foregroundStyle(NKQihuo.textPrimary)
                                .lineLimit(1)
                            if let contract = product.snapshot.mainContract {
                                pill(contract, fg: NKQihuo.accent, bg: NKQihuo.accentFill, mono: true)
                            }
                        }
                        HStack(spacing: 6) {
                            let nightLabel = product.nightSession == true ? "\(product.exchangeName) · 夜盘" : product.exchangeName
                            pill(nightLabel, fg: NKQihuo.textSecondary, bg: NKQihuo.tagFill)
                            if let chain = product.chainTag, !chain.isEmpty {
                                pill(chain, fg: NKQihuo.textSecondary, bg: NKQihuo.tagFill)
                            }
                        }
                    }

                    Spacer(minLength: 6)

                    HStack(spacing: 10) {
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(QihuoCopy.fmtNumber(product.snapshot.close))
                                .font(.system(size: 17, weight: .bold).monospacedDigit())
                                .foregroundStyle(QihuoCopy.signColor(product.snapshot.pctChg))
                                .lineLimit(1)
                            Text("\(QihuoCopy.fmtPct(product.snapshot.pctChg)) · 结 \(QihuoCopy.fmtNumber(product.snapshot.settle))")
                                .font(.system(size: 11.5, weight: .semibold).monospacedDigit())
                                .foregroundStyle(QihuoCopy.signColor(product.snapshot.pctChg))
                                .lineLimit(1)
                        }
                        Image(systemName: "chevron.down")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(isExpanded ? NKQihuo.accent : NKQihuo.textSecondary)
                            .rotationEffect(.degrees(isExpanded ? 180 : 0))
                            .frame(width: 24, height: 24)
                            .background(isExpanded ? NKQihuo.accentFill : NKQihuo.tagFill)
                            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    }
                }

                Divider().overlay(NKQihuo.hairline)

                kpiStrip
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var kpiStrip: some View {
        let cols = isCompact
            ? [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
            : [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10),
               GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
        return LazyVGrid(columns: cols, alignment: .leading, spacing: 10) {
            wsrKPI
            holdingKPI
            spotKPI
            newsKPI
        }
    }

    private var wsrKPI: some View {
        let wsr = product.snapshot.wsr
        let dayStr = QihuoCopy.shortDay(wsr?.date ?? product.snapshot.dataDate)
        let valText = wsr?.value != nil ? "\(QihuoCopy.fmtNumber(wsr?.value)) \(wsr?.unit ?? "吨")" : "—"
        let chgText = wsr?.changeText ?? QihuoCopy.fmtSignedDouble(wsr?.change)
        return VStack(alignment: .leading, spacing: 2) {
            Text("交易所仓单（\(dayStr)）")
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
                .lineLimit(1)
            HStack(spacing: 4) {
                Text(valText)
                    .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                    .foregroundStyle(NKQihuo.textPrimary)
                Text(chgText)
                    .font(.system(size: 11.5, weight: .semibold).monospacedDigit())
                    .foregroundStyle(QihuoCopy.signColor(wsr?.change))
            }
            .lineLimit(1)
            Text("官方仓单日报")
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textSecondary)
                .lineLimit(1)
        }
    }

    private var holdingKPI: some View {
        let hold = product.snapshot.holding
        let dayStr = QihuoCopy.shortDay(hold?.date ?? product.snapshot.dataDate)
        let netText = hold?.net != nil ? "\(QihuoCopy.fmtSignedInt(hold?.net)) 手" : "—"
        let subText: String = {
            guard let hold, let pct = hold.longRatioPct else { return "—" }
            return String(format: "多%.1f%% · 多%@ 空%@", pct, QihuoCopy.fmtShortW(hold.longChg), QihuoCopy.fmtShortW(hold.shortChg))
        }()
        return VStack(alignment: .leading, spacing: 2) {
            Text("前 20 净持仓（\(dayStr)）")
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
                .lineLimit(1)
            Text(netText)
                .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                .foregroundStyle(QihuoCopy.signColor(hold?.net))
                .lineLimit(1)
            Text(subText)
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(NKQihuo.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.85)
        }
    }

    private var spotKPI: some View {
        let spot = product.snapshot.spot
        let basis = product.snapshot.basis
        let valText: String = {
            if let spot, let dv = spot.displayValue ?? spot.value.map({ QihuoCopy.fmtNumber($0) }) {
                return "\(dv) \(spot.unit)"
            }
            return product.spotPending ? QihuoCopy.emptySpotTitle : "—"
        }()
        let subText: String = {
            if let basis, let bv = basis.displayValue ?? basis.value.map({ QihuoCopy.fmtNumber($0) }) {
                return "基差 \(bv)（\(QihuoCopy.shortDay(spot?.date))旬价）"
            }
            return product.spotPending ? QihuoCopy.emptySpotSub : "暂无数据"
        }()
        return VStack(alignment: .leading, spacing: 2) {
            Text("参考现货 / 基差")
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
                .lineLimit(1)
            Text(valText)
                .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                .foregroundStyle(spot == nil ? NKQihuo.textSecondary : NKQihuo.textPrimary)
                .lineLimit(1)
            Text(subText)
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(NKQihuo.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.85)
        }
    }

    private var newsKPI: some View {
        let ns = product.snapshot.newsSummary
        let latestDay = ns.latestPublishedAt.map { QihuoCopy.shortDay($0) } ?? "无"
        return VStack(alignment: .leading, spacing: 2) {
            Text("近 15 日筛选消息")
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
                .lineLimit(1)
            HStack(spacing: 5) {
                if let bull = ns.bullCount, let bear = ns.bearCount {
                    pill("\(bull) 利多", fg: NKQihuo.up, bg: NKQihuo.upFill)
                    pill("\(bear) 利空", fg: NKQihuo.down, bg: NKQihuo.downFill)
                } else {
                    Text("—")
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundStyle(NKQihuo.textSecondary)
                }
            }
            Text("最新 \(latestDay)")
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(NKQihuo.textSecondary)
                .lineLimit(1)
        }
    }

    // MARK: 展开态

    private var expandedBody: some View {
        VStack(alignment: .leading, spacing: 12) {
            subNavBar

            if selectedTab == .all || selectedTab == .news {
                newsSection
            }
            if selectedTab == .all || selectedTab == .data {
                dataSection
            }
            if selectedTab == .all || selectedTab == .holding {
                holdingSection
            }

            basicAccordion
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .background(NKQihuo.cardSubtle)
    }

    private var subNavBar: some View {
        ViewThatFits(in: .horizontal) {
            HStack {
                subSegmentedControl
                Spacer()
                Text("近 15 天客观记录 · 展开查看明细")
                    .font(.system(size: 11.5))
                    .foregroundStyle(NKQihuo.textTertiary)
            }
            VStack(alignment: .leading, spacing: 6) {
                subSegmentedControl
                Text("近 15 天客观记录 · 展开查看明细")
                    .font(.system(size: 11.5))
                    .foregroundStyle(NKQihuo.textTertiary)
            }
        }
    }

    private var subSegmentedControl: some View {
        let items: [(QihuoCardTab, String)] = [
            (.all, "全部"),
            (.news, "消息 (\(product.news.count))"),
            (.data, "数据 (\(product.dataRows.count)日)"),
            (.holding, "持仓 (\(product.holdRows.count)日)"),
        ]
        return HStack(spacing: 2) {
            ForEach(items, id: \.0) { tab, title in
                let active = selectedTab == tab
                Button {
                    onSelectTab(tab)
                } label: {
                    Text(title)
                        .font(.system(size: 12, weight: active ? .semibold : .medium))
                        .foregroundStyle(active ? NKQihuo.textPrimary : NKQihuo.textSecondary)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 4)
                        .background(active ? NKQihuo.card : Color.clear)
                        .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
                        .shadow(color: active ? Color.black.opacity(0.08) : .clear, radius: 1, y: 1)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(NKQihuo.tagFill)
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }

    // MARK: 1. 消息栏

    private var newsSection: some View {
        sectionBox(title: "消息栏 · 近 15 日", meta: "只收明确利多 / 利空 · 每品种每次 ≤3 条") {
            if product.status.news.state == "failed" {
                warnStrip(
                    title: "部分新闻源采集异常",
                    detail: product.status.news.text ?? product.status.news.message ?? "最近一次新闻采集未完成，已保留近 15 日既有记录。"
                )
            }
            if product.news.isEmpty {
                Text("近 15 日暂无明确利多/利空消息。")
                    .font(.system(size: 12))
                    .foregroundStyle(NKQihuo.textTertiary)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(product.news.enumerated()), id: \.element.id) { idx, item in
                        if idx > 0 { Divider().overlay(NKQihuo.hairline) }
                        newsRow(item)
                    }
                }
            }
        }
    }

    private func newsRow(_ item: QihuoNewsItem) -> some View {
        let isBull = item.direction == "利多"
        return VStack(alignment: .leading, spacing: 5) {
            QihuoFlowLayout(spacing: 6) {
                pill(item.direction, fg: isBull ? NKQihuo.up : NKQihuo.down, bg: isBull ? NKQihuo.upFill : NKQihuo.downFill)
                if !item.tier.isEmpty {
                    pill(item.tier, fg: NKQihuo.textSecondary, bg: NKQihuo.tagFill, bold: false)
                }
                if !item.category.isEmpty {
                    pill(item.category, fg: NKQihuo.textSecondary, bg: NKQihuo.tagFill, bold: false)
                }
                Text(item.publishedDisplay)
                    .font(.system(size: 11).monospacedDigit())
                    .foregroundStyle(NKQihuo.textTertiary)
                if let url = Self.safeExternalURL(item.url) {
                    Link("\(item.source) ↗", destination: url)
                        .font(.system(size: 11))
                        .foregroundStyle(NKQihuo.accent)
                } else if !item.source.isEmpty {
                    Text("· \(item.source)")
                        .font(.system(size: 11))
                        .foregroundStyle(NKQihuo.textTertiary)
                }
            }
            Text(item.title)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(NKQihuo.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text(item.summary)
                .font(.system(size: 12.5))
                .foregroundStyle(NKQihuo.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            if !item.figures.isEmpty {
                QihuoFlowLayout(spacing: 6) {
                    ForEach(item.figures) { fig in
                        figureChip(fig)
                    }
                }
                .padding(.top, 2)
            }
        }
    }

    private func figureChip(_ fig: QihuoFigure) -> some View {
        let chg = fig.change.map { "（\($0)）" } ?? ""
        let tail = [fig.source, fig.scope].filter { !$0.isEmpty }.joined(separator: " / ")
        let detail = tail.isEmpty ? "\(fig.value)\(fig.unit)\(chg)" : "\(fig.value)\(fig.unit)\(chg) · \(tail)"
        return (Text(fig.metric).fontWeight(.semibold).foregroundStyle(NKQihuo.textPrimary)
                + Text("：\(detail)").foregroundStyle(NKQihuo.textSecondary))
            .font(.system(size: 11))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(NKQihuo.cardSubtle)
            .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
    }

    // MARK: 2. 数据栏

    private var dataSection: some View {
        sectionBox(title: "数据栏 · 仓单与收盘 / 现货", meta: "近 \(product.dataRows.count) 个交易日") {
            if product.status.data.state == "failed" {
                warnStrip(
                    title: "数据栏采集异常",
                    detail: product.status.data.text ?? product.status.data.message ?? "最近一次数据采集未完成，保留既有记录。"
                )
            }
            spotStrip
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(spacing: 0) {
                    dataTableHeader
                    Divider().overlay(NKQihuo.hairline)
                    ForEach(Array(product.dataRows.enumerated()), id: \.element.id) { idx, row in
                        if idx > 0 { Divider().overlay(NKQihuo.hairline) }
                        dataTableRow(row)
                    }
                }
                .frame(minWidth: isCompact ? 420 : 560, alignment: .leading)
            }
            Text(QihuoCopy.dataFootnote)
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
                .padding(.top, 4)
        }
    }

    private var spotStrip: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let sp = product.snapshot.spot {
                let dv = sp.displayValue ?? QihuoCopy.fmtNumber(sp.value)
                HStack(spacing: 14) {
                    (Text("最新统计局旬价（\(sp.date ?? "—")）：").foregroundStyle(NKQihuo.textTertiary)
                     + Text("\(dv) \(sp.unit)").fontWeight(.semibold).foregroundStyle(NKQihuo.textPrimary))
                        .font(.system(size: 12).monospacedDigit())
                    if let bs = product.snapshot.basis {
                        let bv = bs.displayValue ?? QihuoCopy.fmtNumber(bs.value)
                        (Text("参考基差（\(bs.date ?? "—")）：").foregroundStyle(NKQihuo.textTertiary)
                         + Text("\(bv) \(bs.unit)").fontWeight(.semibold).foregroundStyle(NKQihuo.textPrimary))
                            .font(.system(size: 12).monospacedDigit())
                    }
                }
                let specParts = [sp.series, sp.sourceName].compactMap { $0 }.filter { !$0.isEmpty }
                if !specParts.isEmpty {
                    Text("口径：\(specParts.joined(separator: " · "))")
                        .font(.system(size: 11))
                        .foregroundStyle(NKQihuo.textTertiary)
                }
            } else {
                (Text("现货价与基差：").foregroundStyle(NKQihuo.textTertiary)
                 + Text(product.spotPending ? QihuoCopy.emptySpotBanner : QihuoCopy.missingSpotBanner).foregroundStyle(NKQihuo.textSecondary))
                    .font(.system(size: 12))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(NKQihuo.cardSubtle)
        .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }

    private var dataTableHeader: some View {
        HStack(spacing: 8) {
            Text("日期").frame(width: 88, alignment: .leading)
            Text("主力收盘").frame(maxWidth: .infinity, alignment: .trailing)
            Text("结算价").frame(maxWidth: .infinity, alignment: .trailing)
            Text("交易所仓单(吨)").frame(maxWidth: .infinity, alignment: .trailing)
            Text("仓单日变").frame(width: 74, alignment: .trailing)
        }
        .font(.system(size: 11, weight: .medium))
        .foregroundStyle(NKQihuo.textTertiary)
        .padding(.vertical, 6)
    }

    private func dataTableRow(_ row: QihuoDataRow) -> some View {
        let chgText = row.wsrChgText ?? QihuoCopy.fmtSignedDouble(row.wsrChg)
        return HStack(spacing: 8) {
            Text(row.date).foregroundStyle(NKQihuo.textPrimary).frame(width: 88, alignment: .leading)
            Text(QihuoCopy.fmtNumber(row.close)).foregroundStyle(NKQihuo.textPrimary).frame(maxWidth: .infinity, alignment: .trailing)
            Text(QihuoCopy.fmtNumber(row.settle)).foregroundStyle(NKQihuo.textSecondary).frame(maxWidth: .infinity, alignment: .trailing)
            Text(QihuoCopy.fmtNumber(row.wsr)).foregroundStyle(NKQihuo.textPrimary).frame(maxWidth: .infinity, alignment: .trailing)
            Text(chgText).foregroundStyle(QihuoCopy.signColor(row.wsrChg)).frame(width: 74, alignment: .trailing)
        }
        .font(.system(size: 12).monospacedDigit())
        .padding(.vertical, 6)
    }

    // MARK: 3. 持仓栏

    private var holdingSection: some View {
        sectionBox(title: "持仓栏 · 主力前 20 名会员", meta: "近 \(product.holdRows.count) 个交易日 · 单位：手") {
            if product.status.holding.state == "failed" {
                warnStrip(
                    title: "持仓栏采集异常",
                    detail: product.status.holding.text ?? product.status.holding.message ?? "最近一次持仓采集未完成，保留既有记录。"
                )
            }
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(spacing: 0) {
                    holdTableHeader
                    Divider().overlay(NKQihuo.hairline)
                    ForEach(Array(product.holdRows.enumerated()), id: \.element.id) { idx, row in
                        if idx > 0 { Divider().overlay(NKQihuo.hairline) }
                        holdTableRow(row)
                    }
                }
                .frame(minWidth: 640, alignment: .leading)
            }
            Text(QihuoCopy.holdingFootnote)
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
                .padding(.top, 4)
        }
    }

    private var holdTableHeader: some View {
        HStack(spacing: 8) {
            Text("日期").frame(width: 84, alignment: .leading)
            Text("合约").frame(width: 62, alignment: .trailing)
            Text("前20多单").frame(width: 72, alignment: .trailing)
            Text("多单日变").frame(width: 68, alignment: .trailing)
            Text("前20空单").frame(width: 72, alignment: .trailing)
            Text("空单日变").frame(width: 68, alignment: .trailing)
            Text("多单占比").frame(width: 96, alignment: .trailing)
            Text("净持仓").frame(width: 74, alignment: .trailing)
        }
        .font(.system(size: 11, weight: .medium))
        .foregroundStyle(NKQihuo.textTertiary)
        .padding(.vertical, 6)
    }

    private func holdTableRow(_ row: QihuoHoldRow) -> some View {
        let ratioPct: Double? = {
            if let pct = row.longRatioPct { return min(100, max(0, pct)) }
            if let ratio = row.longRatio { return min(100, max(0, ratio * 100)) }
            return nil
        }()
        return HStack(spacing: 8) {
            Text(row.date).foregroundStyle(NKQihuo.textPrimary).frame(width: 84, alignment: .leading)
            Text(row.contract ?? "—").foregroundStyle(NKQihuo.textSecondary).frame(width: 62, alignment: .trailing)
            Text(QihuoCopy.fmtInt(row.longTotal)).foregroundStyle(NKQihuo.textPrimary).frame(width: 72, alignment: .trailing)
            Text(QihuoCopy.fmtSignedInt(row.longChg)).foregroundStyle(QihuoCopy.signColor(row.longChg)).frame(width: 68, alignment: .trailing)
            Text(QihuoCopy.fmtInt(row.shortTotal)).foregroundStyle(NKQihuo.textPrimary).frame(width: 72, alignment: .trailing)
            Text(QihuoCopy.fmtSignedInt(row.shortChg)).foregroundStyle(QihuoCopy.signColor(row.shortChg)).frame(width: 68, alignment: .trailing)
            HStack(spacing: 5) {
                if let ratioPct {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(NKQihuo.downFill)
                            Capsule().fill(NKQihuo.up)
                                .frame(width: geo.size.width * CGFloat(ratioPct / 100.0))
                        }
                    }
                    .frame(width: 42, height: 5)
                    Text(String(format: "%.1f%%", ratioPct))
                        .foregroundStyle(NKQihuo.textPrimary)
                } else {
                    Text("—")
                        .foregroundStyle(NKQihuo.textTertiary)
                }
            }
            .frame(width: 96, alignment: .trailing)
            Text(QihuoCopy.fmtSignedInt(row.net))
                .fontWeight(.semibold)
                .foregroundStyle(QihuoCopy.signColor(row.net))
                .frame(width: 74, alignment: .trailing)
        }
        .font(.system(size: 12).monospacedDigit())
        .padding(.vertical, 6)
    }

    /// 仅放行带有效主机的 HTTP(S) 外部链接，拒绝脚本协议及指向带访问码 `/read/` 端点的链接。
    static func safeExternalURL(_ raw: String?) -> URL? {
        guard let raw else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              (scheme == "http" || scheme == "https"),
              let host = components.host, !host.isEmpty,
              components.user == nil,
              components.password == nil else {
            return nil
        }
        let lowerPath = components.path.lowercased()
        if lowerPath.contains("/read/") || lowerPath.hasSuffix("/read") {
            return nil
        }
        return components.url
    }

    // MARK: 4. 底部二次折叠：长期基本信息档案

    private var basicAccordion: some View {
        VStack(spacing: 0) {
            Button(action: onToggleBasic) {
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("基本信息档案")
                            .font(.system(size: 12.5, weight: .semibold))
                            .foregroundStyle(NKQihuo.textPrimary)
                        let updatedText = product.basic.updated.map { "更新于 \($0)" } ?? "长期档案"
                        Text("合约规格 · 产业链 · 供需驱动 · 成本 · 季节 · 日常指标（\(updatedText)）")
                            .font(.system(size: 11.5))
                            .foregroundStyle(NKQihuo.textTertiary)
                            .lineLimit(2)
                    }
                    Spacer()
                    Text(isBasicExpanded ? "收起 ▴" : "展开 ▾")
                        .font(.system(size: 11.5, weight: .medium))
                        .foregroundStyle(NKQihuo.textSecondary)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isBasicExpanded {
                Divider().overlay(NKQihuo.hairline)
                basicBody
            }
        }
        .background(NKQihuo.card)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(
                    NKQihuo.hairline,
                    style: isBasicExpanded ? StrokeStyle(lineWidth: 1) : StrokeStyle(lineWidth: 1, dash: [4, 3])
                )
        )
    }

    private var basicBody: some View {
        let ct = product.basic.contract
        let specCols = isCompact
            ? [GridItem(.flexible(), spacing: 8)]
            : [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)]

        return VStack(alignment: .leading, spacing: 12) {
            LazyVGrid(columns: specCols, spacing: 8) {
                specBox("交易单位", ct.tradingUnit ?? "—")
                specBox("每点盈亏", ct.pointValue ?? "—")
                specBox("交易时间", ct.tradingHours ?? "—")
                specBox("交割月份", ct.deliveryMonths ?? "—")
            }

            chainDiagram

            if !product.basic.related.isEmpty {
                QihuoFlowLayout(spacing: 6) {
                    ForEach(product.basic.related) { rel in
                        relatedChip(rel)
                    }
                }
            }

            let sp = product.basic.standardSpot
            if let source = sp.source, !source.isEmpty {
                specBox("标准现货口径（\(sp.series ?? source)）", sp.spec ?? "—")
            } else {
                specBox("标准现货口径", "当前留空（暂无免费日度自动源）。")
            }

            ForEach(product.basic.sections) { sec in
                VStack(alignment: .leading, spacing: 6) {
                    Divider().overlay(NKQihuo.hairline)
                    Text(sec.title)
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundStyle(NKQihuo.textPrimary)
                        .padding(.top, 4)
                    QihuoMarkdownBlock(markdown: sec.markdown)
                }
            }
        }
        .padding(14)
    }

    @ViewBuilder private var chainDiagram: some View {
        let up = chainColumn(title: "上游原料 / 供给", nodes: product.basic.chain.upstream)
        let mid = Text("\(product.name) (\(product.code))")
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(NKQihuo.accent)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: isCompact ? .infinity : nil)
            .background(NKQihuo.accentFill)
            .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
        let down = chainColumn(title: "下游去向 / 需求", nodes: product.basic.chain.downstream)

        if isCompact {
            VStack(spacing: 8) { up; mid; down }
        } else {
            HStack(alignment: .top, spacing: 10) {
                up.frame(maxWidth: .infinity, alignment: .leading)
                mid
                down.frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func chainColumn(title: String, nodes: [QihuoChainNode]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(NKQihuo.textTertiary)
            ForEach(nodes) { node in
                VStack(alignment: .leading, spacing: 1) {
                    Text(node.name)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(NKQihuo.textPrimary)
                    if let note = node.note, !note.isEmpty {
                        Text(note)
                            .font(.system(size: 11.5))
                            .foregroundStyle(NKQihuo.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 11)
        .padding(.vertical, 9)
        .background(NKQihuo.cardSubtle)
        .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }

    private func relatedChip(_ rel: QihuoRelated) -> some View {
        HStack(spacing: 4) {
            Text(rel.name).fontWeight(.semibold).foregroundStyle(NKQihuo.textPrimary)
            if let code = rel.code, !code.isEmpty {
                pill(code, fg: rel.enabled ? NKQihuo.accent : NKQihuo.textSecondary,
                     bg: rel.enabled ? NKQihuo.accentFill : NKQihuo.tagFill)
            }
            if let note = rel.note, !note.isEmpty {
                Text("：\(note)").foregroundStyle(NKQihuo.textSecondary)
            }
        }
        .font(.system(size: 11.5))
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(NKQihuo.cardSubtle)
        .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
    }

    private func specBox(_ key: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(key)
                .font(.system(size: 11))
                .foregroundStyle(NKQihuo.textTertiary)
            Text(value)
                .font(.system(size: 12))
                .foregroundStyle(NKQihuo.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(NKQihuo.cardSubtle)
        .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }

    // MARK: 共用容器与样式

    private func sectionBox<Content: View>(title: String, meta: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(NKQihuo.textPrimary)
                Spacer()
                Text(meta)
                    .font(.system(size: 11.5).monospacedDigit())
                    .foregroundStyle(NKQihuo.textTertiary)
            }
            content()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(NKQihuo.card)
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func warnStrip(title: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("⚠")
            (Text(title).fontWeight(.semibold) + Text("（\(detail)）"))
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 11.5))
        .foregroundStyle(NKQihuo.amberInk)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(NKQihuo.amberBackground)
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
    }

    private func pill(_ text: String, fg: Color, bg: Color, mono: Bool = false, bold: Bool = true) -> some View {
        Text(text)
            .font(mono ? .system(size: 11.5, weight: .semibold).monospacedDigit()
                       : .system(size: 11, weight: bold ? .semibold : .medium))
            .foregroundStyle(fg)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(bg)
            .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
    }
}

/// 轻量纯原生 Markdown 渲染（支持段落、无序列表与行内加粗，完美适配浅/深色）。
struct QihuoMarkdownBlock: View {
    let markdown: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                if line.hasPrefix("- ") || line.hasPrefix("* ") {
                    HStack(alignment: .top, spacing: 6) {
                        Text("•")
                            .font(.system(size: 12.5))
                            .foregroundStyle(NKQihuo.textTertiary)
                        inlineText(String(line.dropFirst(2)))
                    }
                    .padding(.leading, 4)
                } else {
                    inlineText(line)
                }
            }
        }
    }

    private var lines: [String] {
        markdown
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    private func inlineText(_ raw: String) -> some View {
        var attributed = (try? AttributedString(markdown: raw, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(raw)
        for run in attributed.runs {
            if let link = run.link, QihuoProductCard.safeExternalURL(link.absoluteString) == nil {
                attributed[run.range].link = nil
            }
        }
        return Text(attributed)
            .font(.system(size: 12.5))
            .foregroundStyle(NKQihuo.textSecondary)
            .lineSpacing(3)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// 自动换行的流式标签布局（供消息胶囊、结构化指标芯片与关联品种使用）。
struct QihuoFlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var lineHeight: CGFloat = 0
        var totalWidth: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(ProposedViewSize(width: maxWidth, height: nil))
            if x > 0 && x + size.width > maxWidth {
                y += lineHeight + spacing
                x = 0
                lineHeight = 0
            }
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
            totalWidth = max(totalWidth, min(x, maxWidth))
        }
        return CGSize(width: totalWidth, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var lineHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
            if x > bounds.minX && x + size.width > bounds.maxX {
                y += lineHeight + spacing
                x = bounds.minX
                lineHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: min(size.width, bounds.width), height: size.height))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}
