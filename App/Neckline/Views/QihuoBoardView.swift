// Q 期货看板主视图：顶栏状态条、全部展开/收起、5 张可点击下拉展开的信息卡片与停用品种留存说明。
// macOS 居中阅读最大宽 980pt；iOS 纵向全宽阅读支持下拉刷新。

import SwiftUI

struct QihuoBoardView: View {
    @Bindable var qihuo: QihuoBoardModel
    var openSettings: () -> Void

    var body: some View {
        Group {
            switch qihuo.state {
            case .loading:
                MorningLoadingView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(NKQihuo.page)
            case .error(let error):
                MorningStatusView(
                    message: QihuoCopy.message(for: error),
                    retry: { Task { await qihuo.refresh() } },
                    openSettings: openSettings
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(NKQihuo.page)
            case .ready(let board):
                boardScroll(board)
            }
        }
        .background(NKQihuo.page)
    }

    private func boardScroll(_ board: QihuoBoard) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                headerBar(board)

                if board.stale || qihuo.refreshFailed {
                    staleBanner(board)
                }

                VStack(spacing: 12) {
                    ForEach(board.products) { product in
                        QihuoProductCard(
                            product: product,
                            isExpanded: qihuo.isExpanded(product.slug),
                            selectedTab: qihuo.tab(for: product.slug),
                            isBasicExpanded: qihuo.isBasicExpanded(product.slug),
                            onToggleCard: {
                                withAnimation(.easeInOut(duration: 0.18)) {
                                    qihuo.toggleCard(product.slug)
                                }
                            },
                            onSelectTab: { tab in
                                qihuo.setTab(tab, for: product.slug)
                            },
                            onToggleBasic: {
                                withAnimation(.easeInOut(duration: 0.18)) {
                                    qihuo.toggleBasic(product.slug)
                                }
                            }
                        )
                    }
                }

                if !board.pool.paused.isEmpty {
                    pausedFooter(board.pool)
                }
            }
            .frame(maxWidth: 980, alignment: .leading)
            .padding(.horizontal, horizontalPadding)
            .padding(.top, 14)
            .padding(.bottom, 32)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(NKQihuo.page)
        .refreshable {
            await qihuo.refresh()
        }
    }

    private var horizontalPadding: CGFloat {
        #if os(iOS)
        return 14
        #else
        return 22
        #endif
    }

    private var headerTitleSize: CGFloat {
        #if os(iOS)
        return 20
        #else
        return 22
        #endif
    }

    private func headerBar(_ board: QihuoBoard) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                Text(QihuoCopy.boardTitle)
                    .font(.system(size: headerTitleSize, weight: .semibold))
                    .foregroundStyle(NKQihuo.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                Spacer(minLength: 6)
                HStack(spacing: 6) {
                    ghostButton(QihuoCopy.expandAll) {
                        withAnimation(.easeInOut(duration: 0.18)) { qihuo.expandAll() }
                    }
                    ghostButton(QihuoCopy.collapseAll) {
                        withAnimation(.easeInOut(duration: 0.18)) { qihuo.collapseAll() }
                    }
                    #if os(iOS)
                    Button {
                        Task { await qihuo.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 12.5, weight: .semibold))
                            .foregroundStyle(NKQihuo.textPrimary)
                            .frame(width: 28, height: 28)
                            .background(NKQihuo.card)
                            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
                            .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("刷新")
                    #endif
                }
            }

            QihuoFlowLayout(spacing: 8) {
                metaPill(dot: NKQihuo.down, text: "启用 \(board.pool.activeCount) / \(board.pool.totalStoredCount) 品种 · 化工全夜盘")
                metaPill(dot: nil, text: "数据日 \(board.asOfDate ?? "—") · 晚间 1 次更新 · 不推送", mono: true)
                metaPill(dot: NKQihuo.accent, text: "同步供 Neckline MCP 与只读网页")
            }
        }
    }

    private func staleBanner(_ board: QihuoBoard) -> some View {
        let text: String = {
            if qihuo.refreshFailed {
                return "本次刷新未能连上服务器，当前继续展示已缓存快照（\(board.asOfDate ?? "—")）。"
            }
            return board.staleReason ?? "当前展示缓存快照，数据可能尚未更新。"
        }()
        return HStack(alignment: .top, spacing: 7) {
            Text("⚠")
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 12))
        .foregroundStyle(NKQihuo.amberInk)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(NKQihuo.amberBackground)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }

    private func pausedFooter(_ pool: QihuoPool) -> some View {
        let names = pool.paused.map(\.name).joined(separator: "、")
        return (Text("数据库已留存其余 \(pool.pausedCount) 个停用品种（未删除）：").fontWeight(.semibold).foregroundStyle(NKQihuo.textPrimary)
                + Text("\(names)。历史数据与基本信息完整保存在后台，需要时切换启用名单即可恢复采集与看板显示。").foregroundStyle(NKQihuo.textSecondary))
            .font(.system(size: 12))
            .lineSpacing(3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(NKQihuo.card)
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func metaPill(dot: Color?, text: String, mono: Bool = false) -> some View {
        HStack(spacing: 5) {
            if let dot {
                Circle().fill(dot).frame(width: 6, height: 6)
            }
            Text(text)
                .font(mono ? .system(size: 11.5).monospacedDigit() : .system(size: 11.5))
                .foregroundStyle(NKQihuo.textSecondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(NKQihuo.card)
        .overlay(Capsule().stroke(NKQihuo.hairline, lineWidth: 1))
        .clipShape(Capsule())
    }

    private func ghostButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(NKQihuo.textPrimary)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .padding(.horizontal, 9)
                .padding(.vertical, 5)
                .background(NKQihuo.card)
                .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(NKQihuo.hairline, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}
