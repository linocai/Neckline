//
//  BreathingLedgerView.swift
//  Neckline — 呼吸试验仓 T 台账(§五 v1.2-E.4):底仓 / T 仓分离记账的客户端界面。
//  展示 T 逐笔列表 + 底仓摊薄成本(baseCostAdj)+「先手」距离(edgeToPrice)——均为
//  服务端派生下发,客户端不重算(§五 v1.2-G.3)。
//
//  ⚠ `edgeToPrice` 口径是**相对成本**(浮盈率直觉):`(price−baseCostAdj)/baseCostAdj`。
//  文案按「先手成本比现价低/高 X%」写,不按「距现价」写——那是 `Position.
//  distToStopPct` 的分母口径(相对现价),两个字段回答不同的问题,不能混着写文案。
//

import SwiftUI

struct BreathingLedgerView: View {
    @Bindable var model: AppModel
    let positionId: Int
    let code: String
    let name: String

    var body: some View {
        NavigationStack {
            Form {
                summarySection
                tradesSection
                addTradeSection
            }
            .formStyle(.grouped)
            .navigationTitle("呼吸 T 台账")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.dismissModal() }
                }
            }
            .task { await model.loadBreathingLedger(positionId: positionId) }
        }
    }

    private var summarySection: some View {
        Section {
            LabeledContent("标的") { Text("\(name) · \(code)") }
            if let base = model.breathingLedger.baseCostAdj {
                LabeledContent("先手成本") { Text("¥\(NKFmt.price(base))") }
                if let edge = model.breathingLedger.edgeToPrice {
                    LabeledContent("与现价比较") {
                        Text(edgeText(edge)).foregroundStyle(edge >= 0 ? NK.up : NK.down)
                    }
                } else {
                    LabeledContent("与现价比较") { Text("—(无实时价)").foregroundStyle(NK.textTertiary) }
                }
            } else {
                Text("暂无 T 记录,先手成本 = 原始买入价").font(.system(size: 12)).foregroundStyle(NK.textTertiary)
            }
        } header: {
            Text("先手成本优势(读时派生,不代表可交易现价)")
        }
    }

    /// `edge >= 0` ⇒ 现价高于先手成本(先手成本比现价低 = 浮盈);`edge < 0` ⇒ 反之。
    private func edgeText(_ edge: Double) -> String {
        let pct = abs(edge) * 100
        return edge >= 0
            ? "先手成本比现价低 \(String(format: "%.2f", pct))%"
            : "先手成本比现价高 \(String(format: "%.2f", pct))%"
    }

    private var tradesSection: some View {
        Section {
            if model.breathingLoading {
                HStack { ProgressView().controlSize(.small); Text("加载中…").font(.system(size: 12.5)) }
            } else if model.breathingLedger.items.isEmpty {
                Text("暂无 T 记录").font(.system(size: 12.5)).foregroundStyle(NK.textTertiary)
            } else {
                ForEach(model.breathingLedger.items) { t in
                    BreathingTradeRow(trade: t) {
                        Task { await model.deleteBreathingTrade(id: t.id, positionId: positionId) }
                    }
                }
            }
        } header: {
            Text("T 逐笔(\(model.breathingLedger.items.count) 笔)")
        }
    }

    private var addTradeSection: some View {
        Section {
            TextField("买价", text: $model.breathingTradeForm.buyPrice)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            TextField("卖价", text: $model.breathingTradeForm.sellPrice)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            TextField("数量,股", text: $model.breathingTradeForm.qty)
                #if os(iOS)
                .keyboardType(.numberPad)
                #endif
            TextField("费用(必填,如实录入)", text: $model.breathingTradeForm.fees)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            TextField("备注(可选)", text: $model.breathingTradeForm.note)
            Button("记一笔 T") {
                Task { await model.submitBreathingTrade(positionId: positionId) }
            }
            .disabled(!model.breathingTradeForm.isValid)
        } header: {
            Text("新增 T")
        } footer: {
            Text("方向不影响盈亏公式(先买后卖 / 先卖后买同式);费用由你如实填写,系统不按费率估算,不替你代入 0。")
        }
    }
}

private struct BreathingTradeRow: View {
    let trade: BreathingTrade
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text("\(trade.tDate) · 买¥\(NKFmt.price(trade.buyPrice)) 卖¥\(NKFmt.price(trade.sellPrice)) × \(trade.qty)")
                    .font(.system(size: 12))
                Spacer()
                Text(NKFmt.signedMoney(trade.tPnl))
                    .font(.system(size: 12.5, weight: .semibold).monospacedDigit())
                    .foregroundStyle(trade.tPnl >= 0 ? NK.up : NK.down)
            }
            HStack {
                Text("费用 ¥\(NKFmt.price(trade.fees))\(trade.note.isEmpty ? "" : " · \(trade.note)")")
                    .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                Spacer()
                Button("删除", role: .destructive, action: onDelete)
                    .font(.system(size: 11))
            }
        }
        .padding(.vertical, 2)
    }
}
