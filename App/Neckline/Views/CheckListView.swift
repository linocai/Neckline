//
//  CheckListView.swift
//  Neckline — 次日核对表（选股板块第二视图）。
//
//  🔴 **这张表只有两段:「已触发放弃」与「待开盘后观察」。⛔ 没有「成立」段。**
//  这不是「本版先不做」,是 9:29 那一拍**结构上判不出成立**(裁定 10 / K9 §七):
//  K9 §6.3 四个形态的「成立」分支**全部含有「前 30 分钟」这一合取项**,而 9:29 时
//  前 30 分钟还没发生;四个「放弃」分支则全是单条破位判定,竞价价就能触发。
//  **三分支的终值由 D1 10:00 的一次性结算读数产出**,它落在**成绩**板块,
//  ⛔ 不进这一屏(裁定 10)。
//
//  ⚠ 段名与那一行脚注**全部由服务端下发**(`CHECKLIST_SEGMENT_LABEL` /
//  `CHECKLIST_FOOTNOTE` 是唯一源)—— ⛔ 客户端不另抄一份中文,也不许省略脚注:
//  它是这张表**没有「成立」段**的解释。
//
//  ⚠ **404 = 那天没跑过那一拍**(还没到 9:26 / 那天根本没有清单)——
//  ⛔ 不弹成错误,⛔ 也不画一张空表(空表看起来像「今天一只都没触发」)。
//

import SwiftUI

private func nkQuoteQualityLabel(_ raw: String) -> String {
    switch raw {
    case "fresh": return "正常"
    case "degraded": return "单路数据可用"
    case "insufficient": return "资料不足"
    case "conflict": return "两路数据不一致"
    default: return "需留意"
    }
}

struct CheckListView: View {
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if model.checklistLoading && model.checklist == nil {
                ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40)
            } else if let list = model.checklist {
                header(list)
                #if os(macOS)
                if model.hasCompletedSettlement() { settlementReadyCard }
                #endif
                segments(list)
                gaps(list)
                footnote(list)
            } else if let why = model.checklistMissing {
                // 🔴 **合法空态**:那天没跑过那一拍。⛔ 不是故障。
                NKEmptyState(title: "今天还没有竞价核对表", subtitle: why,
                             systemImage: "clock")
                notRunNote
            } else {
                NKEmptyState(title: "本次没取到核对表",
                             subtitle: "网络或鉴权没通 —— ⚠ 这与「那天没跑过那一拍」不是一回事。",
                             systemImage: "wifi.exclamationmark")
            }
        }
    }

    #if os(macOS)
    private var settlementReadyCard: some View {
        NKCard {
            HStack(alignment: .center, spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(NK.up)
                VStack(alignment: .leading, spacing: 3) {
                    Text("10:00 结算已完成")
                        .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    Text("这里保留 9:26 的早间快照；成立、放弃和观察的最终结果已更新到成绩里。")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
                Spacer(minLength: 8)
                Button("查看三分支终值") { model.openSettlementResults() }
                    .buttonStyle(.borderedProminent)
            }
        }
    }
    #endif

    private func header(_ list: Checklist) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("次日核对表").font(NKFont.title3).foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 8)
                    NKChip(text: "已触发放弃 \(list.rejectedCount)", tone: .bad)
                    NKChip(text: "待开盘后观察 \(list.pendingCount)", tone: .neutral)
                }
                // 🔴 两个日期分开写:核的是 **D0 的清单**,读数取的是 **D1 的竞价**。
                HStack(spacing: 10) {
                    if !list.d0Date.isEmpty { Text("核 \(NKFmt.reportDate(list.d0Date)) 的清单") }
                    if !list.tradeDate.isEmpty { Text("竞价日 \(NKFmt.reportDate(list.tradeDate))") }
                    if !list.capturedAt.isEmpty { Text("冻结于 \(NKFmt.timestamp(list.capturedAt))") }
                }
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                if !list.dataQuality.isEmpty {
                    NKChip(text: "报价质量 \(nkQuoteQualityLabel(list.dataQuality))",
                           tone: list.dataQuality == "fresh" ? .good : .warn)
                }
            }
        }
    }

    /// **两段**。⚠ 服务端 `segments` 里就只有这两个 —— 这里照序渲染,
    /// ⛔ 不补第三段、⛔ 不按客户端自己的顺序重排。
    private func segments(_ list: Checklist) -> some View {
        ForEach(list.segments) { seg in
            VStack(alignment: .leading, spacing: 8) {
                NKGroupHeader("\(seg.displayLabel) · \(seg.rows.count) 只")
                if seg.rows.isEmpty {
                    Text(seg.verdict == .rejected
                         ? "今天一只都没触发放弃。"
                         : "这一段是空的 —— 说明清单上的票都已经在竞价里定案了。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .padding(.horizontal, 2)
                } else {
                    ForEach(seg.rows) { row in ChecklistRowView(row: row) }
                }
            }
        }
    }

    /// 🔴 **两栏缺口必须逐只说出来**,⛔ 不许折叠成一句「部分数据缺失」:
    /// 「没抓到价」与「没有冻结预案」要人做的事完全不同。
    @ViewBuilder
    private func gaps(_ list: Checklist) -> some View {
        if !list.noQuoteCodes.isEmpty || !list.noPlaybookCodes.isEmpty || !list.notes.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "这一拍没覆盖到的")
                    if !list.noPlaybookCodes.isEmpty {
                        gapRow("没有冻结预案 —— 明早核对不了这几只",
                               list.noPlaybookCodes, .warn)
                    }
                    if !list.noQuoteCodes.isEmpty {
                        gapRow("这次没抓到价 —— 它们仍留在「待开盘后观察」段",
                               list.noQuoteCodes, .neutral)
                    }
                    ForEach(Array(list.notes.enumerated()), id: \.offset) { _, n in
                        Text(nkMarkdown("· \(n)")).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func gapRow(_ title: String, _ codes: [String], _ tone: NKAxisTone) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(NKFont.callout).foregroundStyle(tone.color)
            Text(codes.joined(separator: " · "))
                .font(NKFont.monoKey).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 🔴 服务端下发的那一行:「成立由 10:00 结算,9:30–10:00 由我自己判定。」
    /// ⛔ 不许改写、不许省略。
    @ViewBuilder
    private func footnote(_ list: Checklist) -> some View {
        if !list.footnote.isEmpty {
            NKNoteBlock(text: LocalizedStringKey(list.footnote))
        }
        Text("这张表只显示已经能确认放弃的项目；其余情况需开盘后继续观察。"
             + "9:29 的判断不构成「成立」结论，最终结果会在 10:00 的结算中确认。")
            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var notRunNote: some View {
        Text("竞价核对表只在交易日 9:26–9:29 生成，错过时点后不会补做，"
             + "避免把更晚的价格当成当时数据。")
            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - 一行

struct ChecklistRowView: View {
    let row: ChecklistRow
    @State private var expanded = NKQA.expandDisclosures

    var body: some View {
        NKCard(padding: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(row.displayName).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    Text(row.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    NKChip(text: nkPatternLabel(row.pattern), tone: .neutral)
                    // 段名走服务端下发的 `segment`(⛔ 不在客户端另抄一份中文)。
                    NKChip(text: row.segment.isEmpty ? (row.verdict?.label ?? "") : row.segment,
                           tone: row.verdict?.tone ?? .neutral, filled: row.verdict == .rejected)
                }
                HStack(spacing: 8) {
                    Text("预案 v\(row.playbookVersion)")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                    if row.quoteState.isEmpty {
                        // ⛔ 「没抓到价」不许静默 —— 它意味着这一只这一拍根本没判过。
                        Text("这一拍没抓到价").font(NKFont.caption).foregroundStyle(NK.amber)
                    } else {
                        Text("报价 \(nkQuoteQualityLabel(row.quoteState))")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                }
                if !row.readingRows.isEmpty {
                    Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                        HStack(spacing: 4) {
                            Image(systemName: expanded ? "chevron.down" : "chevron.right")
                                .font(.system(size: 9))
                            Text("这一拍读到的量 · \(row.readingRows.count) 项").font(NKFont.caption)
                        }
                        .foregroundStyle(NK.textTertiary)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if expanded {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(row.readingRows, id: \.label) { item in
                                HStack {
                                    Text(item.label).font(NKFont.monoKey)
                                        .foregroundStyle(NK.textTertiary)
                                    Spacer(minLength: 8)
                                    Text(NKFmt.price(item.value)).font(NKFont.monoValue)
                                        .foregroundStyle(NK.textSecondary)
                                }
                            }
                        }
                        // ⚠ 9:26 那一拍**刻意读不到**开盘价 / 高开幅度 / 前 30 分钟最高价 ——
                        // 那时开盘还没发生,给它们一个值就是编数。缺席即缺席,⛔ 不补 0。
                        Text("这里只展示当时已经取得的数据；开盘后才有的读数不会提前出现。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

// MARK: - macOS 列表栏的核对表轨(两段折成两组行)

#if os(macOS)
struct CheckListRail: View {
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            if let list = model.checklist {
                ForEach(list.segments) { seg in
                    NKGroupHeader("\(seg.displayLabel) · \(seg.rows.count)")
                        .padding(.horizontal, 2).padding(.top, 6)
                    ForEach(seg.rows) { row in
                        HStack(spacing: 8) {
                            Circle().fill((row.verdict?.tone ?? .neutral).color)
                                .frame(width: 6, height: 6)
                            Text(row.displayName).font(NKFont.body)
                                .foregroundStyle(NK.textPrimary)
                            Spacer(minLength: 4)
                            Text(nkPatternLabel(row.pattern)).font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                        }
                        .padding(.horizontal, 10).padding(.vertical, 6)
                    }
                }
            } else {
                Text(model.checklistMissing ?? "本次没取到核对表")
                    .font(NKFont.body).foregroundStyle(NK.textSecondary)
                    .padding(.horizontal, NKSpace.listHeaderExtraH)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
#endif
