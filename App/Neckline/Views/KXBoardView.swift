// KX V4 盘后情绪候选看板。
// 严格对照已确认 HTML 样图「KX 看板」标签页：
// 顶栏（盘后日期/整理时间/容量/刷新）→ 异常横幅 → 近5日情绪条 + DeepSeek 情绪周期判断 →
// 情绪派系快捷过滤 + 搜索 → 三个候选区（龙头候选区 / 高情绪候选区 / 淘汰候选区）；
// macOS 双栏（左列表右详情），iOS 纵向列表 + 推入详情。盘中彻底静默，无自动轮询。

import SwiftUI

struct KXBoardView: View {
    @Bindable var kx: KXBoardModel
    var openSettings: () -> Void
    @State private var marketNoteExpanded = false

    var body: some View {
        #if os(macOS)
        content
            .background(NKKX.page)
            .task { await kx.loadIfNeeded() }
        #else
        NavigationStack {
            content
                .background(NKKX.page)
                .navigationTitle(KXCopy.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            Task { await kx.refresh() }
                        } label: {
                            if kx.isRefreshing {
                                ProgressView()
                            } else {
                                Image(systemName: "arrow.clockwise")
                            }
                        }
                        .accessibilityLabel("刷新")
                    }
                }
        }
        .sheet(isPresented: Binding(
            get: { kx.presentedDetailItem != nil },
            set: { if !$0 { kx.closeDetail() } }
        )) {
            if let detailItem = kx.presentedDetailItem {
                NavigationStack {
                    KXStockDetailView(item: detailItem, onClose: { kx.closeDetail() })
                }
                .presentationDragIndicator(.visible)
            }
        }
        .task { await kx.loadIfNeeded() }
        #endif
    }

    @ViewBuilder
    private var content: some View {
        switch kx.state {
        case .loading:
            MorningLoadingView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(NKKX.page)
        case .error(let error):
            MorningStatusView(
                message: KXCopy.errorMessage(for: error),
                retry: { Task { await kx.refresh() } },
                openSettings: openSettings
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(NKKX.page)
        case .ready(let board):
            #if os(macOS)
            macLayout(board)
            #else
            phoneLayout(board)
            #endif
        }
    }

    // MARK: macOS 双栏布局

    #if os(macOS)
    private func macLayout(_ board: KXBoard) -> some View {
        VStack(spacing: 0) {
            headerBar(board)
            let banners = effectiveBanners(board)
            if !banners.isEmpty {
                alertBanner(banners)
            }
            sentimentCard(board)
            filterBar(board)
            Rectangle().fill(NKKX.border).frame(height: 1)

            HSplitView {
                ScrollView {
                    candidateZones(board, isMac: true)
                        .padding(14)
                }
                .background(NKKX.page)
                .frame(minWidth: 390, idealWidth: 450, maxWidth: 540)

                Group {
                    if let selected = kx.selectedItem {
                        KXStockDetailView(item: selected)
                            .id(selected.code)
                    } else {
                        VStack(spacing: 8) {
                            Text(kx.hasActiveFilter ? KXCopy.emptyFilterTitle : KXCopy.selectLeftPrompt)
                                .font(.system(size: 13))
                                .foregroundStyle(NKKX.textSecondary)
                            if kx.hasActiveFilter {
                                Button(KXCopy.clearFilter) { kx.resetFilters() }
                                    .buttonStyle(.bordered)
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(NKKX.surface)
                    }
                }
                .frame(minWidth: 420, maxWidth: .infinity)
            }
        }
    }
    #endif

    // MARK: iOS 纵向布局

    #if os(iOS)
    private func phoneLayout(_ board: KXBoard) -> some View {
        ScrollView {
            VStack(spacing: 0) {
                headerBar(board)
                let banners = effectiveBanners(board)
                if !banners.isEmpty {
                    alertBanner(banners)
                }
                sentimentCard(board)
                filterBar(board)
                candidateZones(board, isMac: false)
                    .padding(.horizontal, 14)
                    .padding(.top, 12)
                    .padding(.bottom, 24)
            }
        }
        .background(NKKX.page)
        .refreshable {
            await kx.refresh()
        }
    }
    #endif

    // MARK: 1. 顶栏（盘后日期、20:00 整理时间、容量计数）

    private func headerBar(_ board: KXBoard) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center, spacing: 8) {
                #if os(macOS)
                Text("KX 盘后看板")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(NKKX.textPrimary)
                Text("盘后 \(board.asOf)")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(NKKX.textSecondary)
                #else
                Text("盘后 \(board.asOf)")
                    .font(.system(size: 14, weight: .semibold, design: .monospaced))
                    .foregroundStyle(NKKX.textPrimary)
                #endif
                Spacer(minLength: 6)
                capacityBadge(board.capacity)
            }
            Text("整理于 \(KXCopy.generatedShortTime(board.generatedAt)) · 盘中静默快照")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(NKKX.textTertiary)
                .lineLimit(1)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(NKKX.surface)
        .overlay(alignment: .bottom) {
            Rectangle().fill(NKKX.border).frame(height: 1)
        }
    }

    private func capacityBadge(_ cap: KXCapacity) -> some View {
        HStack(spacing: 4) {
            Text("候选")
                .foregroundStyle(NKKX.textSecondary)
            Text("\(cap.used)")
                .fontWeight(.semibold)
                .foregroundStyle(NKKX.textPrimary)
            Text("/ \(cap.max)")
                .foregroundStyle(NKKX.textSecondary)
            if cap.nextFree > 0 {
                Text("· 拟空 \(cap.nextFree) 坑")
                    .foregroundStyle(NKKX.amberInk)
            } else {
                Text("· 空 0 坑")
                    .foregroundStyle(NKKX.textTertiary)
            }
        }
        .font(.system(size: 11.5, design: .monospaced))
        .lineLimit(1)
        .fixedSize()
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(NKKX.surfaceSubtle)
        .clipShape(Capsule())
    }

    // MARK: 2. 异常 / 未更新横幅

    private func effectiveBanners(_ board: KXBoard) -> [String] {
        var list: [String] = []
        if kx.refreshFailed {
            list.append(KXCopy.refreshFailedNotice)
        }
        for msg in board.alertMessages where !list.contains(msg) {
            list.append(msg)
        }
        return list
    }

    private func alertBanner(_ messages: [String]) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(Array(messages.enumerated()), id: \.offset) { _, text in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(NKKX.amber)
                        .padding(.top, 1)
                    Text(text)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(NKKX.amberInk)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 9)
        .background(NKKX.amberBackground)
        .overlay(alignment: .bottom) {
            Rectangle().fill(NKKX.amber.opacity(0.25)).frame(height: 1)
        }
    }

    // MARK: 3. 情绪分析区（程序事实 + DeepSeek 情绪周期判断分开展示）

    private func sentimentCard(_ board: KXBoard) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if let today = board.latestSentimentDay {
                sentimentFactsStrip(today: today, days: board.sentiment.days)
            }
            if let judgment = board.sentiment.judgment {
                deepseekSentimentBlock(judgment)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(NKKX.surface)
        .overlay(alignment: .bottom) {
            Rectangle().fill(NKKX.border).frame(height: 1)
        }
    }

    private func sentimentFactsStrip(today: KXSentimentDay, days: [KXSentimentDay]) -> some View {
        let highestNames = today.highest.map(\.name).joined(separator: "、")
        let contText: String = {
            if let cont = today.yCont, let total = today.yZtN {
                return "\(cont)/\(total) 续板"
            }
            return "—"
        }()

        return VStack(alignment: .leading, spacing: 10) {
            #if os(macOS)
            HStack(alignment: .top, spacing: 14) {
                sentiCell(value: today.zt.map(String.init) ?? "—", unit: nil, color: NKKX.up, label: "主板涨停", sub: nil)
                sentiCell(
                    value: today.zb.map(String.init) ?? "—",
                    unit: today.zbRate.map { String(format: "%.1f%%", $0) },
                    color: NKKX.amber,
                    label: "炸板 · 炸板率",
                    sub: nil
                )
                sentiCell(value: today.dt.map(String.init) ?? "—", unit: nil, color: NKKX.down, label: "主板跌停", sub: nil)
                sentiCell(
                    value: today.maxLbc.map(String.init) ?? "—",
                    unit: today.maxLbc == nil ? nil : "板",
                    color: NKKX.textPrimary,
                    label: "最高连板",
                    sub: highestNames.isEmpty ? nil : highestNames
                )
                sentiCell(
                    value: KXCopy.fmtPct1(today.yCloseMed),
                    unit: nil,
                    color: KXCopy.toneColor(today.yCloseMed),
                    label: "昨涨停今收中位（\(contText)）",
                    sub: "今开溢价 \(KXCopy.fmtPct(today.yOpenMean)) · 均收 \(KXCopy.fmtPct(today.yCloseMean))"
                )
                Spacer(minLength: 8)
                fiveDayMiniStrip(days)
            }
            #else
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    sentiCell(value: today.zt.map(String.init) ?? "—", unit: nil, color: NKKX.up, label: "涨停", sub: nil)
                    sentiCell(
                        value: today.zb.map(String.init) ?? "—",
                        unit: today.zbRate.map { String(format: "%.0f%%", $0) },
                        color: NKKX.amber,
                        label: "炸板·率",
                        sub: nil
                    )
                    sentiCell(value: today.dt.map(String.init) ?? "—", unit: nil, color: NKKX.down, label: "跌停", sub: nil)
                    sentiCell(
                        value: today.maxLbc.map(String.init) ?? "—",
                        unit: today.maxLbc == nil ? nil : "板",
                        color: NKKX.textPrimary,
                        label: "最高标",
                        sub: highestNames.isEmpty ? nil : highestNames
                    )
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("昨涨停今收中位")
                        .font(.system(size: 11.5))
                        .foregroundStyle(NKKX.textSecondary)
                    Text(KXCopy.fmtPct1(today.yCloseMed))
                        .font(.system(size: 14, weight: .semibold, design: .monospaced))
                        .foregroundStyle(KXCopy.toneColor(today.yCloseMed))
                    Text("(\(contText))")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(NKKX.textTertiary)
                    Spacer()
                    Text("开 \(KXCopy.fmtPct1(today.yOpenMean)) · 均 \(KXCopy.fmtPct1(today.yCloseMean))")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(NKKX.textSecondary)
                }
                fiveDayMiniStrip(days)
            }
            #endif
        }
    }

    private func sentiCell(value: String, unit: String?, color: Color, label: String, sub: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value)
                    .font(.system(size: 19, weight: .semibold, design: .monospaced))
                    .foregroundStyle(color)
                if let unit {
                    Text(unit)
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundStyle(NKKX.textSecondary)
                }
            }
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(NKKX.textTertiary)
                .lineLimit(1)
            if let sub, !sub.isEmpty {
                Text(sub)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(NKKX.textSecondary)
                    .lineLimit(1)
            }
        }
        #if os(iOS)
        .frame(maxWidth: .infinity, alignment: .leading)
        #endif
    }

    private func fiveDayMiniStrip(_ days: [KXSentimentDay]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("近 5 日温度（涨停 / 跌停）")
                .font(.system(size: 10.5))
                .foregroundStyle(NKKX.textTertiary)
            HStack(spacing: 5) {
                ForEach(Array(days.enumerated()), id: \.element.id) { idx, day in
                    let isLatest = idx == days.count - 1
                    VStack(spacing: 1) {
                        Text(KXCopy.monthDay(day.date))
                            .font(.system(size: 9.5, design: .monospaced))
                            .foregroundStyle(NKKX.textTertiary)
                        HStack(spacing: 1) {
                            Text(day.zt.map(String.init) ?? "—")
                                .foregroundStyle(NKKX.up)
                            Text("/")
                                .foregroundStyle(NKKX.textTertiary)
                            Text(day.dt.map(String.init) ?? "—")
                                .foregroundStyle(NKKX.down)
                        }
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    }
                    .frame(minWidth: 48)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
                    .padding(.horizontal, 4)
                    .background(isLatest ? NKKX.surface : NKKX.surfaceSubtle)
                    .overlay {
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(isLatest ? NKKX.accent : NKKX.border, lineWidth: isLatest ? 1.2 : 1)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
        }
    }

    private func deepseekSentimentBlock(_ judgment: KXSentimentJudgment) -> some View {
        Button {
            marketNoteExpanded.toggle()
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .center, spacing: 6) {
                    Text("DeepSeek 情绪周期判断")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(NKKX.accent)
                    if !judgment.stage.isEmpty {
                        Text(judgment.stage)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(NKKX.textPrimary)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(NKKX.surface)
                            .clipShape(Capsule())
                    }
                    Spacer()
                    Text(marketNoteExpanded ? "收起" : "展开")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(NKKX.accent)
                }

                if !judgment.marketNote.isEmpty {
                    Text(judgment.marketNote)
                        .font(.system(size: 12.5))
                        .foregroundStyle(NKKX.textSecondary)
                        .lineLimit(marketNoteExpanded ? nil : 2)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(NKKX.deepseekFill)
            .clipShape(RoundedRectangle(cornerRadius: 9))
        }
        .buttonStyle(.plain)
    }

    // MARK: 4. 筛选栏（派系快捷过滤 + 搜索）

    private func filterBar(_ board: KXBoard) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 12))
                        .foregroundStyle(NKKX.textTertiary)
                    TextField(KXCopy.searchPlaceholder, text: $kx.searchText)
                        .textFieldStyle(.plain)
                        .font(.system(size: 12.5))
                        .foregroundStyle(NKKX.textPrimary)
                    if !kx.searchText.isEmpty {
                        Button {
                            kx.searchText = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 12))
                                .foregroundStyle(NKKX.textTertiary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("清空搜索")
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(NKKX.surfaceSubtle)
                .overlay {
                    RoundedRectangle(cornerRadius: 8).stroke(NKKX.border, lineWidth: 1)
                }
                .clipShape(RoundedRectangle(cornerRadius: 8))

                if kx.hasActiveFilter {
                    Text("\(kx.filteredItems.count)/\(board.items.count)")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(NKKX.accent)
                }
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    factionFilterButton(title: KXCopy.allFaction, count: board.items.count, value: KXCopy.allFaction)
                    ForEach(board.factions) { faction in
                        factionFilterButton(title: faction.name, count: faction.count, value: faction.name)
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 9)
        .background(NKKX.surface)
    }

    private func factionFilterButton(title: String, count: Int, value: String) -> some View {
        let active = kx.selectedFaction == value
        return Button {
            kx.selectedFaction = value
        } label: {
            HStack(spacing: 4) {
                Text(title)
                if value != KXCopy.allFaction {
                    Text("\(count)")
                        .font(.system(size: 10.5, design: .monospaced))
                        .opacity(0.8)
                }
            }
            .font(.system(size: 12, weight: active ? .semibold : .regular))
            .foregroundStyle(active ? NKKX.accent : NKKX.textSecondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(active ? NKKX.accentFill : NKKX.surfaceSubtle)
            .overlay {
                Capsule().stroke(active ? NKKX.accent.opacity(0.45) : NKKX.border, lineWidth: 1)
            }
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    // MARK: 5. 三个候选区（龙头 / 高情绪 / 淘汰）

    private func candidateZones(_ board: KXBoard, isMac: Bool) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            if board.items.isEmpty {
                emptyBlock(title: KXCopy.emptyBoardTitle, showClear: false)
            } else if kx.filteredItems.isEmpty {
                emptyBlock(title: KXCopy.emptyFilterTitle, showClear: true)
            } else {
                ForEach(KXZone.allCases, id: \.self) { zone in
                    let items = kx.filteredItems(in: zone)
                    if !items.isEmpty || !kx.hasActiveFilter {
                        zoneSection(zone: zone, items: items, isMac: isMac)
                    }
                }
            }

            Text(KXCopy.footerNote)
                .font(.system(size: 11))
                .foregroundStyle(NKKX.textTertiary)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.top, 4)
        }
    }

    private func emptyBlock(title: String, showClear: Bool) -> some View {
        VStack(spacing: 10) {
            Text(title)
                .font(.system(size: 13))
                .foregroundStyle(NKKX.textSecondary)
            if showClear {
                Button(KXCopy.clearFilter) { kx.resetFilters() }
                    .buttonStyle(.bordered)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 36)
        .background(NKKX.surface)
        .overlay {
            RoundedRectangle(cornerRadius: 12).stroke(NKKX.border, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func zoneSection(zone: KXZone, items: [KXBoardItem], isMac: Bool) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Circle()
                    .fill(KXCopy.zoneDotColor(zone))
                    .frame(width: 7, height: 7)
                Text(KXCopy.zoneTitle(zone))
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(NKKX.textPrimary)
                Text(KXCopy.zoneHint(zone))
                    .font(.system(size: 11))
                    .foregroundStyle(NKKX.textTertiary)
                    .lineLimit(1)
                Spacer()
                Text("\(items.count) 只")
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(NKKX.textSecondary)
            }
            .padding(.horizontal, 2)

            if items.isEmpty {
                Text("当前无标的")
                    .font(.system(size: 12))
                    .foregroundStyle(NKKX.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(NKKX.surface)
                    .overlay {
                        RoundedRectangle(cornerRadius: 11).stroke(NKKX.border, lineWidth: 1)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 11))
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { idx, item in
                        Group {
                            if isMac {
                                Button {
                                    kx.select(item.code)
                                } label: {
                                    stockRowContent(item, selected: kx.selectedItem?.code == item.code)
                                }
                                .buttonStyle(.plain)
                            } else {
                                Button {
                                    kx.openDetail(item.code)
                                } label: {
                                    stockRowContent(item, selected: false)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .overlay(alignment: .bottom) {
                            if idx < items.count - 1 {
                                Rectangle().fill(NKKX.border).frame(height: 1)
                            }
                        }
                    }
                }
                .background(NKKX.surface)
                .overlay {
                    RoundedRectangle(cornerRadius: 11).stroke(NKKX.border, lineWidth: 1)
                }
                .clipShape(RoundedRectangle(cornerRadius: 11))
            }
        }
    }

    private func stockRowContent(_ item: KXBoardItem, selected: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            // 第一行：名称 + 代码 | 涨跌幅 + 换手（绝不折行）
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(item.name)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(NKKX.textPrimary)
                        .lineLimit(1)
                    Text(item.code)
                        .font(.system(size: 11.5, design: .monospaced))
                        .foregroundStyle(NKKX.textTertiary)
                }
                Spacer(minLength: 6)
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(KXCopy.fmtPct(item.facts.pct))
                        .font(.system(size: 14, weight: .semibold, design: .monospaced))
                        .foregroundStyle(KXCopy.toneColor(item.facts.pct))
                    Text("换 \(KXCopy.fmtTurnover(item.facts.tr, decimals: 1))")
                        .font(.system(size: 11.5, design: .monospaced))
                        .foregroundStyle(NKKX.textSecondary)
                }
            }

            // 第二行：独立于分区的节奏状态胶囊 + 变动徽章 | 收盘板型
            HStack(alignment: .center, spacing: 6) {
                HStack(spacing: 5) {
                    KXRhythmPill(rhythm: item.judgment.rhythm)
                    KXChangeBadgePill(badge: item.changeBadge)
                }
                Spacer(minLength: 6)
                Text(item.facts.boardStatus)
                    .font(.system(size: 11.5))
                    .foregroundStyle(NKKX.textSecondary)
                    .lineLimit(1)
            }

            // 第三行：主/次情绪派系标签 | 今日龙虎榜净买或淘汰区停留提示
            HStack(alignment: .center, spacing: 6) {
                HStack(spacing: 4) {
                    ForEach(Array(item.judgment.factions.prefix(3).enumerated()), id: \.offset) { _, tag in
                        KXFactionPillView(text: tag.name, primary: tag.primary)
                    }
                }
                Spacer(minLength: 6)
                if item.zone == .elim {
                    Text(item.cycleTag)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(item.nextRemove ? NKKX.amberInk : NKKX.textTertiary)
                        .lineLimit(1)
                } else if let lhb = item.facts.lhb, item.facts.lhbIsToday {
                    Text("榜 \(KXCopy.fmtWanSigned(lhb.netW))")
                        .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(KXCopy.toneColor(lhb.netW))
                } else {
                    Text("今日未上榜")
                        .font(.system(size: 11))
                        .foregroundStyle(NKKX.textTertiary)
                }
            }

            // 第四行：二波核心事实（5日线、回撤、量/峰）
            HStack(spacing: 12) {
                Text("5日线 \(KXCopy.fmtPct1(item.facts.ma5Diff))")
                    .foregroundStyle(NKKX.textSecondary)
                Text("回撤 \(KXCopy.fmtPct1(item.facts.ddPeak))")
                    .foregroundStyle(NKKX.textSecondary)
                Text("量/峰 \(KXCopy.fmtRatioPct(item.facts.volRatioPeak))")
                    .foregroundStyle(NKKX.textSecondary)
                Spacer(minLength: 0)
            }
            .font(.system(size: 11, design: .monospaced))
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(selected ? NKKX.accentFill.opacity(0.75) : Color.clear)
        .contentShape(Rectangle())
    }
}
