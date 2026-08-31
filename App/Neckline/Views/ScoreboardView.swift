import SwiftUI

/// K9-v3 成绩页：每张卡代表一个不可变成绩包，绝不跨包拼总分。
struct ScoreboardView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(macOS)
        NKSplitLayout { list } detail: { detail }
        #else
        NavigationStack { ScrollView { detail.padding(NKSpace.pagePad) }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .background(NK.pageBgIOS).navigationTitle("成绩")
            .refreshable { await model.refreshScoreboard() } }
        #endif
    }

    private var list: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            Text("成绩").font(NKFont.title2).foregroundStyle(NK.textPrimary)
            packageSection("进行中成绩包", model.activeScorePackages)
            packageSection("已结算历史", model.settledScorePackages)
        }
    }

    @ViewBuilder private func packageSection(_ title: String, _ packages: [ScoreboardPackage]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(NKFont.headline).foregroundStyle(NK.textPrimary)
            if packages.isEmpty {
                Text("暂无成绩包").font(NKFont.callout).foregroundStyle(NK.textTertiary)
            }
            ForEach(packages) { package in
                Button { model.openScorePackage(batchId: package.batchId) } label: {
                    packageCard(package)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func packageCard(_ p: ScoreboardPackage) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(NKFmt.reportDate(p.selectionDate)).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                Spacer()
                NKChip(text: p.coverageState.label, tone: coverageTone(p.coverageState))
            }
            Text(ScoreboardText.cardSubtitle(p))
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            HStack(spacing: 5) {
                NKChip(text: "K9-v3", tone: .info)
                NKChip(text: ScoreboardText.planRevision(p.revision), tone: .neutral)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(NKSpace.cardPad)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }

    @ViewBuilder private var detail: some View {
        if model.selectedScorePackageLoading {
            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 60)
        } else if let error = model.selectedScorePackageError {
            NKEmptyState(title: "成绩包未加载", subtitle: error, systemImage: "exclamationmark.triangle")
        } else if let package = model.selectedScorePackage {
            ScorePackageDetailView(package: package, model: model)
        } else {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                packageSection("进行中成绩包", model.activeScorePackages)
                packageSection("已结算历史", model.settledScorePackages)
                if model.activeScorePackages.isEmpty && model.settledScorePackages.isEmpty {
                    NKEmptyState(title: "尚无 K9-v3 成绩包", subtitle: "参数未配置或尚未生成 Day 1 时，这里不会用旧成绩填充。", systemImage: "chart.bar")
                }
            }
        }
    }

    private func coverageTone(_ state: K9CoverageState) -> NKAxisTone {
        switch state { case .complete: return .good; case .partial: return .warn; case .pending: return .neutral; case .unavailable: return .bad }
    }
}

struct ScorePackageDetailView: View {
    let package: ScoreboardPackageDetail
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "K9-v3 成绩包")
                    Text(ScoreboardText.selectionDate(package.selectionDate))
                        .font(NKFont.title3).foregroundStyle(NK.textPrimary)
                    Text(ScoreboardText.stageDates(package))
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    NKWrapRow(spacing: 5, lineSpacing: 5) {
                        NKChip(text: package.strategyVersion, tone: .info)
                        NKChip(text: package.paramsPackageVersion, tone: .neutral)
                        NKChip(text: package.packVersion, tone: .neutral)
                        NKChip(text: package.labelContractVersion, tone: .neutral)
                        NKChip(text: package.coverageState.label, tone: .neutral)
                    }
                }
            }
            ForEach(package.candidates) { candidate in FrozenCandidateDetailView(candidate: candidate, model: model) }
        }
    }
}

/// 成绩包卡片的动态文案集中在这里，避免 Swift 字符串漏写 `\\(...)` 后把表达式原样展示。
enum ScoreboardText {
    static func cardSubtitle(_ package: ScoreboardPackage) -> String {
        "信号交易日 \(NKFmt.reportDate(package.signalTradeDate)) · \(package.candidateCount) 只"
    }

    static func planRevision(_ revision: Int) -> String {
        "预案第 \(revision) 版"
    }

    static func selectionDate(_ raw: String) -> String {
        "选股日 \(NKFmt.reportDate(raw))"
    }

    static func stageDates(_ package: ScoreboardPackageDetail) -> String {
        "信号交易日 \(NKFmt.reportDate(package.signalTradeDate)) · D1 \(NKFmt.reportDate(package.d1TradeDate)) · D2 \(NKFmt.reportDate(package.d2TradeDate))"
    }
}
