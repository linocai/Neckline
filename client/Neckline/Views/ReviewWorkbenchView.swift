//
//  ReviewWorkbenchView.swift
//  Neckline — 周复盘工作台(macOS 独有,§五 阶段4C.5):本块只做壳 + 文件拖入区占位。
//  对账逻辑(交割单解析 / 违纪清单 / 强制复盘)是阶段 4D 的事,后端 `neckline/review/`
//  当前仍是空包、无 `/review/*` 端点——本视图刻意不假装能对账,拖入文件只回显文件名 +
//  诚实的"待接入"提示,不发任何网络请求(没有端点可打)。
//
//  iOS 不做此板块(拖文件 + 阅读长材料是桌面场景,plan §五 阶段4D 明文)。
//

import SwiftUI
#if os(macOS)
import UniformTypeIdentifiers

struct ReviewWorkbenchView: View {
    @State private var droppedFileName: String? = nil
    @State private var isTargeted = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                header
                dropZone
                if let name = droppedFileName {
                    NKCard {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(name, systemImage: "doc.text").font(.system(size: 13, weight: .medium))
                            Text("对账引擎(交割单解析 / 违纪清单 / 实际 vs 报告 / 强制复盘)是阶段 4D 的施工内容,"
                                 + "后端 `neckline/review/` 尚未落地对应端点。本壳先占位,4D 接上后此处会展示"
                                 + "真实对账结果与复盘材料。")
                                .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
            }
            .padding(NKSpace.pagePad)
            .frame(maxWidth: 760)
        }
        .frame(maxWidth: .infinity)
        .background(NK.pageBg)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("周复盘工作台").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            Text("拖入券商交割单 xlsx,对照当周计划与纪律章程生成违纪清单(阶段 4D 接入)")
                .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
        }
    }

    private var dropZone: some View {
        RoundedRectangle(cornerRadius: NKRadius.card)
            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
            .foregroundStyle(isTargeted ? NK.accent : NK.hairline)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(isTargeted ? NK.accent.opacity(0.06) : NK.cardBg))
            .frame(height: 180)
            .overlay {
                VStack(spacing: 8) {
                    Image(systemName: "tray.and.arrow.down.fill").font(.system(size: 28))
                        .foregroundStyle(isTargeted ? NK.accent : NK.textTertiary)
                    Text("把交割单 .xlsx 拖到这里").font(.system(size: 13, weight: .medium)).foregroundStyle(NK.textSecondary)
                }
            }
            .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
                handleDrop(providers)
            }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        _ = provider.loadObject(ofClass: URL.self) { url, _ in
            guard let url else { return }
            Task { @MainActor in droppedFileName = url.lastPathComponent }
        }
        return true
    }
}
#endif
