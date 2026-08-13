//
//  AuctionCardView.swift
//  Neckline — V2.3.3-⑤ **D1 集合竞价确认层**的界面(K8.md §二十)
//
//  它回答的唯一问题:**市场对昨天(D0)那份交易假设投出的第一次票是什么**。
//
//  🔴 **不是买入指令**(K8 §二十 逐字):确认 / 中性 / 否决只描述集合竞价对 D0 假设的
//  支持程度 —— ⛔ 界面上不许出现「可以买 / 建议买入 / 该进场了」这类措辞(§3.8 系统
//  只审计、不代下单)。
//
//  🔴 **三态由 `AppModel` 分好,这里只按状态画**(⛔ 视图层不猜):
//    · 没跑过(404)→ **整张卡不画**(⛔ 不画一张空卡,那是噪声);
//    · 读不出(500)→ 画一行「需要排查」,⛔ 不许显示成"还没生成"(那份报告是冻结件、
//      坏了不会自己好,当成"等一等"就是让用户白等);
//    · 跑过了 → 收起卡一行 + 点开看五块全文。**篮子为空也是跑过了**,那时
//      `basketsUnavailableReason` 会把原因说出口。
//
//  🔴 **服务端只发英文枚举码**,中文一律走 `nkAuction*Label`(`Models.swift`)——
//  ⛔ 见到码直连 `Text` 就停一下(V2.3.1 硬伤 2 连踩三次的那一类)。
//
//  🔴 **逐票行 iPhone 402pt 一行放不下**「名称 + 代码 + 等级 + 引擎 + 竞价涨幅 + 判定」
//  → **iOS 分两行 / macOS 一行**,照 `NKMemberCard` 收起行的既有先例。
//  ⚠ 这类挤压**编译不报错、单测也测不出**,只有实拍看得见。
//
//  ⚠ **小纸条的文案本体由服务端下发**(`manualNote`,K8 §二十 固定文案)——
//  ⛔ 客户端不许自己写那段字(同 `BASKET_CARD_DISCLAIMER` 既有体例)。
//

import SwiftUI

// MARK: - 收起态:选股屏顶部那一张卡

/// 竞价小报告的**入口卡**(选股屏顶部第一张)。一行:日期 + 三态计数 + 数据质量徽标
/// + chevron;点开是五块全文。
///
/// ⚠ **`nil` 时整张不画**:`@ViewBuilder` 的 `if let` 分支在没有报告时输出
/// `EmptyView`,选股屏顶部逐字节回到 V2.3.2 的样子。
struct AuctionSummaryCard: View {
    @Bindable var model: AppModel

    var body: some View {
        if let a = model.auction {
            card(a)
        } else if model.auctionCorrupt {
            corruptCard
        }
    }

    private func card(_ a: AuctionPayload) -> some View {
        Button { model.showAuctionSheet = true } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: "bell.badge").font(.system(size: 12))
                        .foregroundStyle(NK.accent)
                    Text("集合竞价确认").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    NKChip(text: a.dataStatus.dataQualityLabel, tone: dqTone(a.dataStatus.dataQuality))
                    Spacer(minLength: 6)
                    Text(headMeta(a)).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary).lineLimit(1)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(NK.textTertiary)
                }
                // 三态计数(**逐字用中文**,码在 `nkAuctionVerdictLabel` 里换)。
                HStack(spacing: 10) {
                    countPill(a.confirmCount, "确认", .good)
                    countPill(a.neutralCount, "中性", .neutral)
                    countPill(a.vetoCount, "否决", .bad)
                    if a.pendingCount > 0 { countPill(a.pendingCount, "待解释", .warn) }
                    Spacer(minLength: 0)
                }
                // 🔴 「命中 D0 失效位」是**机械事实**、走独立警报通道:不受 LLM 缺席、
                // 不受三道夹逼闸影响 —— 收起态就得看得见。
                if !a.hitInvalidationCodes.isEmpty {
                    Text("\(a.hitInvalidationCodes.count) 只已触发 D0 冻结的明确失效位")
                        .font(NKFont.caption).foregroundStyle(NK.down)
                }
                if a.baskets.isEmpty, let r = a.basketsUnavailableReason {
                    Text(r).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if a.llmStage != "ok" {
                    Text("LLM 段:\(a.llmStageLabel) · 机械层的数据报告与失效警报照常")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.vertical, NKSpace.cardPad).padding(.horizontal, NKSpace.cardPadH)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }

    /// 「验 D0 20260811」。**缺就不写**,⛔ 不拿占位符冒充。
    ///
    /// ⚠ **刻意不带 D1 那个日期**(实拍逮到):这张卡只会出现在「今天有竞价报告」时
    /// (客户端拉的就是 today),D1 = 今天是**恒真**的废话;而 402pt 上把两个八位日期
    /// 挤进这一行,后面那个会被截成 `验 D0 202…` —— 一个**看不出是哪天**的日期比不写
    /// 更糟。完整的 `D1 xxx · 验证 D0 xxx` 在弹层标题里。
    private func headMeta(_ a: AuctionPayload) -> String {
        a.d0Date.isEmpty ? "" : "验 D0 \(a.d0Date)"
    }

    private func countPill(_ n: Int, _ label: String, _ tone: NKAxisTone) -> some View {
        HStack(spacing: 4) {
            Text("\(n)").font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                .foregroundStyle(tone.color)
            Text(label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
        }
    }

    /// 500 `auction_corrupt`:**有行但读不出**。⛔ 不许写成「还没生成」——
    /// 那份报告是冻结件,坏了不会自己好,写成"等一等"就是让用户白等。
    private var corruptCard: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 13))
                .foregroundStyle(NK.down)
            VStack(alignment: .leading, spacing: 2) {
                Text("今天的竞价报告读不出来").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textPrimary)
                Text("报告在库里、但数据损坏,需要排查 —— ⛔ 不是「还没生成」,等下去也不会好。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, NKSpace.cardPad).padding(.horizontal, NKSpace.cardPadH)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }
}

/// 市场锚点徽标一屏最多画几枚。⚠ 这是**纯展示层的排版上限**,不是判据 ——
/// 超出的部分由下面那句「全部 N 只里的前 M 只」如实说出口(⛔ 不静默截断);
/// 服务端喂 LLM 的那份**不截断**(§五 ⑨-A 第 5 行)。
private let _anchorChipCap = 24

/// 把一串代码接成一行,超过 `cap` 就**说出口**(⛔ 不静默截断)。
/// ⚠ 返回 `String` 供**插值**用 —— 调用方仍写成一整条字面量,别用 `+` 拼
/// (`Text(String)` 不解析 Markdown,V2.3.1 那条坑)。
func nkJoinCapped(_ items: [String], cap: Int) -> String {
    guard items.count > cap else { return items.joined(separator: "、") }
    return items.prefix(cap).joined(separator: "、") + " 等 \(items.count) 个"
}

/// 数据质量三态 → 语义色(`ok` 不着色:齐全是常态,不是好消息)。
func nkAuctionDQTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "ok": return .neutral
    case "degraded": return .warn
    case "insufficient": return .bad
    default: return .neutral
    }
}

/// 分域质量 → 语义色(V2.4.0 P2.3)。
/// 🔴 `nil`(旧版本未细分)走 `.warn` —— ⛔ **绝不许走 `.neutral`**:那等于把
/// 「当时没记这一位」画成「一切正常」(施工图 §五 P2.3:⛔ 不得默认成正常)。
func nkAuctionDomainTone(_ raw: String?) -> NKAxisTone {
    guard let v = raw, !v.isEmpty else { return .warn }
    return nkAuctionDQTone(v)
}

/// 竞价结论 → 语义色。⚠ `pending_explanation` 走 `.warn`(**没解释**,不是中性)。
func nkAuctionVerdictTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "confirm": return .good
    case "veto": return .bad
    case "neutral": return .neutral
    case "pending_explanation": return .warn
    default: return .neutral
    }
}

private func dqTone(_ raw: String) -> NKAxisTone { nkAuctionDQTone(raw) }

/// 竞价涨跌幅一句。**`nil` = 算不出**,⛔ 不拿 0 冒充"平开"。
func nkAuctionGapText(_ v: Double?) -> String {
    guard let v else { return "算不出" }
    return NKFmt.signedPct(v * 100)
}

// MARK: - 弹层:竞价小报告五块

/// K8 §二十 的**五块全文**:数据状态 / 市场与主线概览 / 篮子与逐票结论 /
/// 异常与风险 / APP 人工观察小纸条。**块序与块名一字不动**(它们是审计锚)。
struct AuctionReportPage: View {
    @Bindable var model: AppModel
    let payload: AuctionPayload

    // 🔴 **V2.4.0 P3.5:打开后默认先给逐篮结论**(施工图 P3.5 逐字)——
    // `basketsBlock`(块 3)挪到最前;块 1「数据状态」+ 块 2「市场与主线概览」折进
    // 「市场背景」/「数据审计」两个具名折叠区,内容**一字不少**。
    // ⚠ **块名与块序是审计锚**(1 数据状态 / 2 市场与主线概览 / 3 篮子与逐票结论 /
    // 4 异常与风险 / 5 APP 人工观察小纸条)——各块内部的「N · 标题」编号原样保留,
    // 变的只是默认先展示第 3 块,⛔ 别把编号也删了。
    var body: some View {
        VStack(spacing: 0) {
            headerBar
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    basketsBlock                 // 块 3(默认可见)
                    risksBlock                   // 块 4(默认可见)
                    manualNoteBlock               // 块 5(默认可见,恒发恒显)
                    proxyNoteBlock                // 恒发恒显:不是买入指令 + 任务边界
                    marketBackgroundDisclosure    // 折叠:块 2 + 竞价强势股的代理样本说明
                    dataAuditDisclosure           // 折叠:块 1
                    if !payload.notes.isEmpty { notesBlock }
                }
                .padding(NKSpace.pagePad)
            }
        }
        .background(NK.pageBgIOS)
    }

    /// 「市场背景」= 指数竞价 · 主线概览 · 竞价强势股 · 代理样本说明 ·
    /// 「市场锚点只解释资金方向,不取得交易资格」(块 2 原样 + `proxyNoteBlock`
    /// 里与市场锚点相关的那句,⛔ 内容一字不少,只是从默认可见改成点开可见)。
    private var marketBackgroundDisclosure: some View {
        NKDisclosure(summary: "市场背景 · 指数竞价 / 主线概览 / 竞价强势股 / 代理样本说明") {
            marketBlock
            if !payload.proxySampleNote.isEmpty {
                // 🔴 **`proxySampleNote` 仍必须能被看到**(K8 要求的诚实披露,守门单测
                // 保留)——折叠可以、消失不行,这里就是它折叠后的落点。
                Text(payload.proxySampleNote)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 🔴 裁定 ①:**当天那一份**观察范围(多少只、来自哪几层)。
            // ⚠ 空串 = 老报告没有这个概念 —— ⛔ 不补一句「范围正常」,也不留白误导:
            //   这一版的报告一定有,老版一定没有,两者由这句话在不在直接分得开。
            if !payload.observationScopeNote.isEmpty {
                Text(payload.observationScopeNote)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 「数据审计」= 覆盖率 · 冻结时刻 · 缺失清单 · 跨源冲突恒空口径 · 全部逐票指标 ·
    /// 历史统计(块 1 原样;逐票指标 / 历史统计仍分别嵌在各篮结论卡自己的「逐票读数」
    /// 折叠里,⛔ 不强行搬家拆散篮子与它自己的数据)。
    private var dataAuditDisclosure: some View {
        NKDisclosure(summary: "数据审计 · 覆盖率 / 冻结时刻 / 缺失清单 / 跨源冲突口径") {
            dataStatusBlock
        }
    }

    private var headerBar: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("竞价小报告").font(NKFont.title2).tracking(-0.3)
                    .foregroundStyle(NK.textPrimary)
                Text(subtitle).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button("关闭") { model.showAuctionSheet = false }
                .buttonStyle(.plain)
                .font(NKFont.callout).foregroundStyle(NK.accent)
        }
        .padding(.horizontal, NKSpace.pagePad).padding(.vertical, 14)
        .background(NK.cardBg)
        .overlay(alignment: .bottom) { Rectangle().fill(NK.hairline).frame(height: 0.5) }
    }

    private var subtitle: String {
        var parts = ["D1 \(payload.tradeDate)"]
        if !payload.d0Date.isEmpty { parts.append("验证 D0 \(payload.d0Date)") }
        parts.append("9:26—9:29 冻结")
        return parts.joined(separator: " · ")
    }

    // —— 第 1 块:数据状态 ——

    private var dataStatusBlock: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "1 · 数据状态")
                HStack(spacing: 8) {
                    NKChip(text: payload.dataStatus.dataQualityLabel,
                           tone: nkAuctionDQTone(payload.dataStatus.dataQuality), filled: true)
                    Text("来源 \(payload.dataStatus.source)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    Text("覆盖 \(payload.dataStatus.coverageText)")
                        .font(NKFont.callout.monospacedDigit()).foregroundStyle(NK.textSecondary)
                    Spacer(minLength: 0)
                }
                Text("冻结时刻 \(payload.dataStatus.capturedAt.isEmpty ? "未记录" : payload.dataStatus.capturedAt)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                domainQualityRow
                if !payload.dataStatus.missingCodes.isEmpty {
                    // ⚠ **⛔ 不用 `+` 拼字符串**(V2.3.1 那条坑的体例):`Text(String)`
                    // 不解析 Markdown,而 `+` 的产物一定是 `String`。要拼就拼成**一整条
                    // 插值字面量**,这样它仍是 `LocalizedStringKey`。
                    Text("本次没抓到:\(nkJoinCapped(payload.dataStatus.missingCodes, cap: 12))")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // 🔴 V2.4.0 P2.1:「抓到了、但读数不能用」必须自己占一行 ——
                // ⛔ 别并进上面那条「没抓到」:一份上一交易日的缓存行情长得跟正常读数
                // 一模一样,沉默会让人以为那一格是好的。
                if !payload.dataStatus.invalidCodes.isEmpty {
                    Text("抓到了但**读数不合格**(本次不当今天的竞价结果用):\(nkJoinCapped(payload.dataStatus.invalidCodes, cap: 12))")
                        .font(NKFont.caption).foregroundStyle(NK.down)
                        .fixedSize(horizontal: false, vertical: true)
                }
                crossSourceRow
                qualityDetailRows
                Text("LLM 段:\(payload.llmStageLabel)")
                    .font(NKFont.caption)
                    .foregroundStyle(payload.llmStage == "ok" ? NK.textTertiary : NK.amber)
            }
        }
    }

    /// 🔴 **跨源冲突那一行必须随「本次到底核验了没有」变化**(V2.4.0 复审 🔴-2)。
    ///
    /// 老版本(V2.3.3)在这里说的是「这一项**结构性恒空**,不等于『已核对无冲突』」;
    /// P2 上线双源核验后把它换成了一句**无条件的正面断言**「两源已交叉核验」——
    /// 而**备源整体失败的早晨**(腾讯 404 / 限流)`conflictCodes` 照样为空、
    /// `sourceDegraded` 为 false、逐票核验区 `worthShowing == false` 什么都不画,
    /// 屏幕上唯一那句话却说核验过了。**Python 侧在五处说的是相反的话**
    /// (`quality.py::detect_conflict` 逐字:只有一源 → 没有第二个读数可以打架,
    /// ⛔ 不是"已核对")—— 守门当时停在了屏幕前一层。
    ///
    /// 🔴 判别式**不在客户端**:服务端 `crossVerified` 逐格落库,这里只数个数。
    /// 四种状态各说各的话,⛔ 不许任何一种借用另一种的措辞:
    ///   ① 老报告(没有逐票账)→ 当年那一栏结构性恒空,如实说;
    ///   ② 有冲突 → 点名 + ⛔ 不能高置信输出;
    ///   ③ 无冲突且**一格都没对拍成** → 说清「没得比」,⛔ 不说"已核验";
    ///   ④ 无冲突且对拍过 → 说**几格**对拍过、还有几格只有一个源。
    @ViewBuilder
    private var crossSourceRow: some View {
        let ds = payload.dataStatus
        let verified = ds.crossVerifiedCount
        let total = ds.quotedCodeCount
        // ⚠ **每个分支各自一条完整字面量**(⛔ 不用三元、⛔ 不用 `+` 拼):
        // `Text(String)` 不解析 Markdown,拼出来的 `**加粗**` 会把星号原样上屏。
        if !ds.conflictCodes.isEmpty {
            Text("跨源冲突:\(ds.conflictCodes.joined(separator: "、")) —— ⛔ 不能高置信输出。")
                .font(NKFont.caption).foregroundStyle(NK.down)
                .fixedSize(horizontal: false, vertical: true)
        } else if !ds.hasDualSourceLedger {
            Text("跨源冲突:这份报告生成于双源核验上线之前 —— 那时这一项**结构性恒空**,⛔ 不等于「已核对无冲突」。")
                .font(NKFont.caption).foregroundStyle(NK.amber)
                .fixedSize(horizontal: false, vertical: true)
        } else if verified == 0 {
            Text("跨源冲突:本次为空,但**一只都没完成两源对拍**(备源整体缺席,或备源读数不合格)—— 是「没得比」,⛔ 不是「已核对无冲突」。")
                .font(NKFont.caption).foregroundStyle(NK.amber)
                .fixedSize(horizontal: false, vertical: true)
        } else if verified == total {
            Text("跨源冲突:本次为空 —— \(verified)/\(total) 只**全部完成两源对拍**。")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text("跨源冲突:本次为空 —— 只有 \(verified)/\(total) 只完成了两源对拍;其余那些**本次只有一个源给出有效读数**,没有可对拍的第二个数。")
                .font(NKFont.caption).foregroundStyle(NK.amber)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 🔴 **关键域 / 上下文域分开画**(V2.4.0 P2.3,K8 §二十「数据质量分域」)。
    ///
    /// 🔴 老报告没有分域信息 → 两枚徽标都写「旧版本未细分」,并**当面说出**它不等于
    /// 正常(施工图 §五 P2.3 逐字:⛔ 不得默认成正常)。
    @ViewBuilder
    private var domainQualityRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text("关键域").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                NKChip(text: payload.dataStatus.criticalQualityLabel,
                       tone: nkAuctionDomainTone(payload.dataStatus.criticalDataQuality))
                Text("上下文域").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                NKChip(text: payload.dataStatus.contextQualityLabel,
                       tone: nkAuctionDomainTone(payload.dataStatus.contextDataQuality))
                Spacer(minLength: 0)
            }
            if payload.dataStatus.hasDomainSplit {
                Text("只有**关键域**(成员自身 + 它实际用到的市场基准与板块基准 + D0 冻结锚)会把结论夹成中性;上下文域缺失只降低置信度并在风险里披露。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("这份报告生成于分域上线之前,**没有**关键域 / 上下文域的记录 —— 这不等于「数据正常」,只是当时没记这一位。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 逐票双源核验里**值得说出口**的那几条(不合格 / 冲突 / 换过源)。全好的不占版面。
    @ViewBuilder
    private var qualityDetailRows: some View {
        let rows = payload.dataStatus.notableQualityDetails
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("逐票双源核验(只列有问题的)")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                ForEach(rows) { d in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(d.tsCode).font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textSecondary)
                            NKChip(text: d.freshnessLabel,
                                   tone: d.freshness == "fresh" ? .neutral : .warn)
                            if d.sourceDegraded { NKChip(text: "已换备源", tone: .warn) }
                            if let cf = d.conflictLabel { NKChip(text: cf, tone: .bad) }
                            Spacer(minLength: 0)
                        }
                        ForEach(d.checks) { c in
                            Text(c.summaryText)
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    // —— 第 2 块:市场与主线概览 ——

    private var marketBlock: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "2 · 市场与主线概览")
                if !payload.marketOverview.indexGaps.isEmpty {
                    NKWrapRow(spacing: 8, lineSpacing: 6) {
                        ForEach(payload.marketOverview.indexGaps) { g in
                            HStack(spacing: 5) {
                                Text(g.displayName).font(NKFont.callout)
                                    .foregroundStyle(NK.textSecondary)
                                Text(nkAuctionGapText(g.gapPct))
                                    .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                                    .foregroundStyle(gapColor(g.gapPct))
                            }
                        }
                    }
                }
                if let t = payload.marketOverview.text, !t.isEmpty {
                    Text(t).font(NKFont.body).lineSpacing(4).foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                } else if let r = payload.marketOverview.textUnavailableReason {
                    Text(r).font(NKFont.callout).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !payload.marketOverview.anchors.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("竞价强势股(市场锚点)").nkLabel().foregroundStyle(NK.textTertiary)
                        NKWrapRow(spacing: 5, lineSpacing: 5) {
                            ForEach(payload.marketOverview.anchors.prefix(_anchorChipCap)) { a in
                                NKChip(text: "\(a.displayName) \(nkAuctionGapText(a.gapPct))")
                            }
                        }
                        // ⚠ **截断必须说出口**(⛔ 静默 `prefix` = 用户以为就这么多):
                        // 与上面「本次没抓到 … 等 N 个」同一个体例。
                        if payload.marketOverview.anchors.count > _anchorChipCap {
                            Text("以上是全部 \(payload.marketOverview.anchors.count) 只里的前 \(_anchorChipCap) 只(按竞价涨幅降序)。")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let n = payload.marketOverview.anchorsNote, !n.isEmpty {
                            Text(n).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Text("市场锚点只解释资金方向,**不取得交易资格**。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    // —— 第 3 块:篮子与逐票结论 ——

    @ViewBuilder
    private var basketsBlock: some View {
        if payload.baskets.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    NKSectionHeader(title: "3 · 篮子与逐票结论")
                    // 🔴 「跑过了、D0 没有 T1/T2」与「竞价层没跑」是两件事 —— 后者根本
                    // 到不了这一屏(端点 404、卡都不画)。这里如实说前者。
                    Text(payload.basketsUnavailableReason ?? "本次没有篮子级结论(服务端未给原因)。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: NKSpace.rowGap) {
                NKSectionHeader(title: "3 · 篮子与逐票结论",
                                trailing: "\(payload.baskets.count) 篮")
                ForEach(payload.baskets) { b in AuctionVerdictCard(verdict: b) }
            }
        }
    }

    // —— 第 4 块:异常与风险 ——

    private var risksBlock: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "4 · 异常与风险")
                if payload.risks.isEmpty {
                    Text("机械层与模型本次都没有报出异常。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                } else {
                    ForEach(payload.risks) { r in
                        HStack(alignment: .top, spacing: 8) {
                            NKChip(text: r.kindLabel, tone: riskTone(r.kind))
                            Text(r.text).font(NKFont.callout).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
        }
    }

    private func riskTone(_ kind: String) -> NKAxisTone {
        switch kind {
        case "hit_invalidation", "source_conflict": return .bad
        case "data_missing", "gap_up_deviation", "anchor_stale", "evidence_conflict",
             "verdict_clamped", "llm_unavailable", "single_strong", "auction_volume_anomaly",
             "invalidation_undetermined":
            return .warn
        default: return .neutral
        }
    }

    // —— 第 5 块:APP 人工观察小纸条 ——

    @ViewBuilder
    private var manualNoteBlock: some View {
        if let note = payload.manualNote, !note.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "5 · APP 人工观察小纸条")
                    // ⚠ 文案本体是**服务端下发**的固定字符串(K8 §二十 逐字),
                    // ⛔ 客户端不许自己写 —— 这里原样透传。
                    Text(note).font(NKFont.body).lineSpacing(4).foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("它只负责提醒:不要求点击或录入,不进入系统评分和正式样本,不改变系统结论。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 🔴 **恒发恒显,不折叠**:「不是买入指令」+ 任务边界是每次打开都该看到的话。
    /// ⚠ `proxySampleNote`(代理样本那句)V2.4.0 起挪进 `marketBackgroundDisclosure`
    /// ——它是「竞价强势股」这份市场锚点数据自己的诚实披露,折叠可以、消失不行,
    /// 与市场背景块住在一起比单独留在主流程里更贴切它描述的对象。
    private var proxyNoteBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("竞价结论只说明竞价反映出的信息,不等于买入指令。")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            NKReferenceNote(text: "报告发出后本次任务结束 · 不持续观察 9:30 之后的价格、不改 D0 的等级与预案")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var notesBlock: some View {
        NKDisclosure(summary: "本次运行备注 \(payload.notes.count) 条", tone: .neutral) {
            ForEach(Array(payload.notes.enumerated()), id: \.offset) { _, n in
                Text("· \(n)").fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private func gapColor(_ v: Double?) -> Color {
    guard let v else { return NK.textTertiary }
    if v > 0 { return NK.up }
    if v < 0 { return NK.down }
    return NK.textSecondary
}

// MARK: - 一个篮子的结论卡

struct AuctionVerdictCard: View {
    let verdict: AuctionVerdict
    @State private var expanded: Bool = NKQA.expandDisclosures

    /// V2.4.0 P3.5:触发失效的那一篮整圈描红——`hitInvalidation` 是机械事实、走独立
    /// 警报通道(不受 LLM 缺席、不受三道夹逼闸影响),默认层就该一眼看见,不必点开
    /// 「命中失效位」那句才知道。
    private var hasHitInvalidation: Bool { !verdict.hitInvalidation.isEmpty }

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 9) {
                head
                if !verdict.reasons.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(Array(verdict.reasons.enumerated()), id: \.offset) { _, r in
                            Text("· \(r)").font(NKFont.body).lineSpacing(3)
                                .foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                // 🔴 被夹逼过必须说出口(⛔ 不许静默把模型的话换掉)。
                if let ct = verdict.clampText {
                    Text(ct).font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                domainQualityBlock
                if !verdict.hitInvalidation.isEmpty {
                    Text("🔴 命中 D0 冻结的明确失效位:\(verdict.hitInvalidation.joined(separator: "、"))")
                        .font(NKFont.callout).foregroundStyle(NK.down)
                        .fixedSize(horizontal: false, vertical: true)
                }
                membersBlock
                footNote
            }
        }
        // 🔴 触发失效的那一篮整圈描红(设计参考:`1px rgba(229,68,59,.35)` ≈
        // `NK.down.opacity(0.35)`)——叠在 `NKCard` 自己的 hairline 描边之上。
        .overlay(
            RoundedRectangle(cornerRadius: NKRadius.card)
                .stroke(hasHitInvalidation ? NK.down.opacity(0.35) : Color.clear, lineWidth: 1)
        )
    }

    /// 篮子头行:名 + 等级 + 引擎 + 结论徽标 + 数据质量。
    /// ⚠ **iOS 分两行**(402pt 一行塞不下),macOS 一行。
    private var head: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 8) {
                Text(verdict.name.isEmpty ? verdict.basketKey : verdict.name)
                    .font(NKFont.headline).foregroundStyle(NK.textPrimary).lineLimit(1)
                Text("T\(verdict.coveredTier)").font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textTertiary)
                if let e = verdict.engineText {
                    Text(e).font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
                }
                #if os(macOS)
                Spacer(minLength: 6)
                badges
                #else
                Spacer(minLength: 0)
                #endif
            }
            #if os(iOS)
            HStack(spacing: 5) { badges; Spacer(minLength: 0) }
            #endif
        }
    }

    private var badges: some View {
        HStack(spacing: 5) {
            NKChip(text: verdict.verdictLabel,
                   tone: nkAuctionVerdictTone(verdict.verdict), filled: true)
            // 🔴 V2.4.0 P2.3:这一枚自此是**关键域**质量(⛔ 不再是"整体")。
            // 老行没有分域列 → 「旧版本未细分」+ 琥珀色,⛔ 不画成绿的。
            NKChip(text: verdict.criticalQualityLabel,
                   tone: nkAuctionDomainTone(verdict.criticalDataQuality))
            if verdict.manualNoteAttached { NKChip(text: "有小纸条", tone: .warn) }
        }
    }

    /// 🔴 两域各一行 + 各自缺了什么(V2.4.0 P2.3)。
    /// **上下文域降级⛔ 不改变结论** —— 这句话必须写在界面上,否则用户会以为
    /// "有缺失就该转中性"(那正是本版拆域前的旧行为)。
    @ViewBuilder
    private var domainQualityBlock: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text("关键域").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                NKChip(text: verdict.criticalQualityLabel,
                       tone: nkAuctionDomainTone(verdict.criticalDataQuality))
                Text("上下文域").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                NKChip(text: verdict.contextQualityLabel,
                       tone: nkAuctionDomainTone(verdict.contextDataQuality))
                Spacer(minLength: 0)
            }
            if !verdict.criticalMissingCodes.isEmpty {
                Text("关键域没有可用读数:\(nkJoinCapped(verdict.criticalMissingCodes, cap: 10)) —— 这一篮的确认 / 否决会被夹成中性。")
                    .font(NKFont.caption).foregroundStyle(NK.down)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !verdict.contextMissingCodes.isEmpty {
                Text("上下文域缺:\(nkJoinCapped(verdict.contextMissingCodes, cap: 10)) —— 只降低置信度,**不改变结论**。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !verdict.hasDomainSplit {
                Text("这一篮生成于分域上线之前,**没有**关键域 / 上下文域的记录 —— 不等于「数据正常」。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var membersBlock: some View {
        if !verdict.members.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                Button {
                    withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        Text("逐票读数 \(verdict.members.count)").nkLabel()
                            .foregroundStyle(NK.textTertiary)
                        Image(systemName: expanded ? "chevron.up" : "chevron.down")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(NK.textTertiary)
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if expanded {
                    VStack(alignment: .leading, spacing: 8) {
                        // 🔴 `sectorPeerMin` 与逐票历史都住在**篮级** json 里,只能从这里
                        // 传下去(⛔ 客户端不许自己写那个裁定值 / 自己判够不够)。
                        ForEach(verdict.members) { m in
                            AuctionMemberRowView(member: m,
                                                 sectorPeerMin: verdict.sectorPeerMin,
                                                 history: verdict.historyFor(m.tsCode))
                        }
                    }
                    .padding(.top, 8)
                }
            }
        }
    }

    /// 自身历史样本天数 + 🔴 **「历史样本不足」标记**(用户裁定 P3-69,2026-08-12)。
    ///
    /// 🔴 **⛔ 客户端不许有任何自己拍的天数门槛**(V2.3.3 复审 🔴-2a):原先写过
    /// `days <= 5 ? "样本很少…" : ""` —— 那个 5 是自己拍的。现在够不够由**服务端按用户
    /// 裁定的 `n ≥ 15`** 判好后随契约下发(`historySampleSufficient`),客户端只显示。
    /// ⚠ 老行没有那个键 → `nil` → **什么都不说**(⛔ 不许默认成"够"或"不够")。
    ///
    /// 🔴 **回看窗口必须一起显示**(复审 🔴-2b):这个天数被服务端的取数窗口封顶,
    /// 只报天数会让读者把「窗口内可得」读成「全史可得」。那句话是**服务端下发**的
    /// 文案单一源(`history_lookback_note`),⛔ 客户端不许自己写。
    @ViewBuilder
    private var footNote: some View {
        VStack(alignment: .leading, spacing: 3) {
            if let days = verdict.historyDaysAvailable {
                HStack(spacing: 6) {
                    // 🔴 篮级那个数是**逐票最小值**(定向复审 🔴-1 之后),⛔ 不许写成
                    // 「本篮有 N 天」——那会被读成"每只票都有这么多天"。
                    Text("自身历史竞价样本:篮内每只票至少 \(days) 天")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    if verdict.historySampleSufficient == false {
                        NKChip(text: "历史样本不足", tone: .warn)
                    }
                    Spacer(minLength: 0)
                }
                // 🔴 **点名是哪几只不够**(定向复审 🔴-1):只报一个篮级总数,人和模型
                // 都看不出后门在哪。逐票明细在「逐票读数」展开里。
                if !verdict.historyInsufficientCodes.isEmpty {
                    Text("样本不足的成员:"
                         + verdict.historyInsufficientCodes.joined(separator: "、")
                         + "(这几只只看原始值,不作历史比较;其余成员不受影响)")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let note = verdict.historyInsufficientNote {
                    Text(note).font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let note = verdict.historyLookbackNote {
                    Text(note).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            // 🔴 **板块对照股的披露与历史那一段没有从属关系**(定向复审 🔵-8):
            // 原先它嵌在 `if let days = …` 里 —— 历史那个键一缺(老行 / 该段读不出),
            // 这句诚实披露也一起消失。两件事,两个独立块。
            if let note = verdict.sectorPeerPoolNote {
                Text(note).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - 逐票一行(🔴 iOS 两行 / macOS 一行)

/// 🔴 **402pt 放不下**「名称 + 代码 + 角色 + 竞价涨幅 + 量比 + 判定」一行 ——
/// iPhone 上会把名称挤成两行、把徽标压成竖排单字(V2.3 成员卡踩过)。
/// 故 **iOS 分两行**:首行 名称 · 代码 · 角色 · 竞价涨幅;次行 量比 / 相对强弱 / 判定。
/// macOS 详情栏 ≥700pt,一行放得下。
struct AuctionMemberRowView: View {
    let member: AuctionMemberRow
    /// 🔴 板块对照股下限(**服务端裁定值**,篮级 json 下发)。⛔ 客户端不许硬编。
    var sectorPeerMin: Int? = nil
    /// 🔴 这一只票的自身历史样本(逐票,定向复审 🔴-1 / 🔵-3)。`nil` = 该段读不出
    /// → **什么都不说**(⛔ 不许默认成"够"或"不够")。
    var history: AuctionMemberHistory? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(member.displayName).font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textPrimary).lineLimit(1)
                Text(member.tsCode).font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(NK.textTertiary).lineLimit(1)
                if let r = member.roleLabel, !r.isEmpty { NKChip(text: r) }
                Text(nkAuctionGapText(member.gapPct))
                    .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                    .foregroundStyle(gapColor(member.gapPct))
                #if os(macOS)
                Spacer(minLength: 6)
                metrics
                statusBadges
                #else
                Spacer(minLength: 0)
                #endif
            }
            #if os(iOS)
            HStack(spacing: 6) {
                metrics
                statusBadges
                Spacer(minLength: 0)
            }
            #endif
            // 🔴 **相对板块 / 相对市场各占一行**(用户裁定 P3-70:两条独立路径,
            // 禁止同源同值)。⚠ 402pt 上塞不进上面那一行 —— 每条都要带「减的是哪一支 /
            // 哪一组」,横着排必然被挤成竖排单字(V2.3 成员卡踩过)。
            // ⚠ **双端同一段**(改共用件只改一个平台 = V2.3.1 批 7 那个坑)。
            relStrengthBlock
            // 🔴 `nil` = **没判**,与 `false`「没问题」讲成两句不同的话。
            if member.anchorStale {
                Text("冻结锚今日失效(疑似除权除息)—— 本票的失效位与高开偏离**没判**,不是「无异常」。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let note = member.undeterminedNote {
                // 🔴 锚没失效、但判据本身缺(卡上无冻结价位 / 开盘价未发布 / 有篮无卡)——
                // 这一格是**空的**,必须说出口(V2.3.3 复审 🔴-1:原先它被折成
                // `false`「没问题」,用户与 LLM 都看不出来)。
                Text(note).font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 🔴 V2.4.0 P2.1/P2.2:这条读数的**来源与校验**。全好时返回 `nil`(不占版面);
            // 只要过期 / 换过源 / 两源打架,就必须当面说出来 —— 一份上一交易日的缓存
            // 行情看起来跟正常读数一模一样,沉默就等于替它背书。
            if let pv = member.quoteProvenanceNote {
                Text(pv).font(NKFont.caption).foregroundStyle(NK.down)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let n = member.volumeNote, !n.isEmpty {
                Text(n).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // 🔴 **「只展示原始值」那半句需求的落点**(定向复审 🔵-3):裁定 P3-69 原文是
            // 「`n < 15`,标记『历史样本不足』,**只展示原始值**」—— 改前界面只有天数 +
            // 三句话,原始值下发了却一个都不画 = 那半句需求没实现。
            // ⚠ 这一段住在**收起的**「逐票读数」里,不会把首屏推下去。
            if let h = history {
                Text(h.noteText)
                    .font(NKFont.caption.monospacedDigit())
                    .foregroundStyle(h.sampleSufficient ? NK.textSecondary : NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// 竞价量能 —— **算不出就写「算不出」**,⛔ 不拿 0 顶。
    private var metrics: some View {
        HStack(spacing: 8) {
            Text("量/前5日均 \(pctOrDash(member.volVsPrev5Frac))")
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
        }
    }

    /// 🔴 **相对板块 / 相对市场两条独立读数**(用户裁定 P3-70,2026-08-12)。
    ///
    /// 每条都自带「减的是哪一支 / 哪一组」;**取不到时画第三态** —— 琥珀色 +
    /// 「未取得 + 为什么 +(不是「持平」)」,⛔ **绝不许渲染成 0 或一片空白**
    /// (「没有」≠「持平」是本版的红线)。
    /// ⚠ 两条整句都由 `Models.swift` 的计算属性拼成**一个 `String`** 再进 `Text` ——
    /// `Text(String)` 不解析 Markdown,故那两句里一个 `*` 都不许有。
    private var relStrengthBlock: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(member.relToSectorText(sectorPeerMin: sectorPeerMin))
                .font(NKFont.caption.monospacedDigit())
                .foregroundStyle(member.relToSectorMissing ? NK.amber : NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text(member.relToIndexText)
                .font(NKFont.caption.monospacedDigit())
                .foregroundStyle(member.relToIndexMissing ? NK.amber : NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 🔴 **三态各有各的画法**:`true` 画红/琥珀徽标 · `false` 不画(常态) ·
    /// `nil` 画一枚「有项没判」——⛔ **不许与 `false` 一样什么都不画**,那就是把
    /// 「一个字都没核对」渲染成「过」(V2.3.3 复审 🔴-1)。
    /// ⚠ 402pt 上这一行已经很挤 → 第三态只占**一枚**徽标,哪一项、为什么写在下面
    /// 那句 `undeterminedNote` 里(⛔ 不加两枚,会把徽标压成竖排单字)。
    private var statusBadges: some View {
        HStack(spacing: 4) {
            if member.hitInvalidation == true { NKChip(text: "触发失效位", tone: .bad, filled: true) }
            if member.gapUpDeviation == true { NKChip(text: "高开偏离", tone: .warn) }
            if member.hasUndeterminedInvalidation { NKChip(text: "有项没判", tone: .warn) }
            NKChip(text: member.statusText,
                   tone: member.dataQuality == "insufficient" ? .warn : .neutral)
        }
    }

    private func pctOrDash(_ v: Double?) -> String {
        guard let v else { return "算不出" }
        return NKFmt.ratioPct(v)
    }
}
