//
//  BasketModels.swift
//  Neckline — 客户端展示层数据模型 · V2 篮子族(⑭-B 契约,⑮ 客户端落地)+ V2.2-③ 六道关口展示层
//
//  ⚠ **V2.4.0 P3.7 纯机械拆分**:本文件与同目录另外五份 `*Models.swift` 是原
//  `Networking/Models.swift`(5633 行)**逐字**切出来的,⛔ 一个声明没改、没加、没删
//  (切点全在顶层 `// MARK:` 之前的空行上;拆分脚本对拼回来的全文做过逐字节比对)。
//  🔴 **守门单测不再按 `Models.swift` 这个文件名读客户端 DTO** —— 一律走
//  `tests/client_sources.py::networking_swift_text()`(把本目录全部 `.swift` 拼起来)。
//  ⛔ 新增 DTO 文件必须放在本目录下,否则那些「某字段已退役」的**缺席断言**会静默变成
//  真(读不到的文件里当然搜不到),看起来还全绿 —— 这是拆分引入的唯一新风险面,
//  `tests/client_sources.py` 里的哨兵断言就是为它立的。
//
//  ⚠ 加 / 移动 `.swift` 与新增一样,**必须 `xcodegen generate`**(pbxproj 是显式文件引用)。
//

import Foundation

// ══════════════════════════════════════════════════════════════════════════
// MARK: - V2 篮子族(⑭-B 契约,⑮ 客户端落地)
// ══════════════════════════════════════════════════════════════════════════
//
// **两类 DTO,决定解码怎么写**(CLAUDE.md「落库快照两类论」/ 对拍表 §四.1):
//   · **A 类:每次响应重新拼装**(`Basket` / `Tier` / `BasketVerification` /
//     `DroppedBasket` / `PositionPlan` / `Profile` / `Pack` / `EvalWeekly`)——服务端用
//     pydantic 默认值重构,新字段旧数据也会补全。
//   · **B 类:写入当时冻住的历史快照**(`BasketCard` ← `basket_cards.card_json`;
//     `BasketReview.mech` ← `basket_review_daily.mech_json`)——服务端升级**永远不会**
//     给老快照补新键。⛔ 用合成 `Codable` 的后果:装了新 App 的用户翻几周前的老卡 →
//     **整张卡解不出**。
// 本文件对**两类一律手写 `init(from:)` + `decodeIfPresent`**:B 类是硬要求;A 类这么写
// 是白拿的保险(Swift 合成 Decodable 对非 Optional 属性**不会**因为声明了默认值就容忍
// 缺键 —— 默认值只影响 memberwise init,这条坑本文件其它类型早已踩过)。

/// 篮子卡上的一条证据(`evidence[]`,⑤ 两段式流水的检索产出)。**来源与日期必带**,
/// 缺了就如实留空,⛔ 不替 LLM 补一个看起来像样的出处。
struct BasketEvidence: Codable, Equatable, Identifiable {
    var claim: String = ""
    var source: String = ""
    var date: String = ""
    var url: String = ""

    var id: String { "\(claim)|\(source)|\(date)" }

    enum CodingKeys: String, CodingKey { case claim, source, date, url }

    init(claim: String = "", source: String = "", date: String = "", url: String = "") {
        self.claim = claim; self.source = source; self.date = date; self.url = url
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        claim = try c.decodeIfPresent(String.self, forKey: .claim) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""
        url = try c.decodeIfPresent(String.self, forKey: .url) ?? ""
    }
}

// ⚠ V2.3.3-①:`struct BasketScripts`(次日强 / 平 / 弱三剧本)**已删除** —— 卡 #6 换成
// 「预期上涨路径」一段话(`BasketCard.upsidePath`,K8.md §十 第 8 项)。服务端两键
// `scripts` / `scriptsUnavailableReason` 同版停发;老 v3 卡里那两键不再进契约面。

/// 口径指纹(章程 / 包 / 引擎 / 验证条件集四个版本号 + 两个纪律比例)。
/// ⑨ 的按包归因靠它分层,**不是装饰字段**。
struct BasketFingerprint: Codable, Equatable {
    var stopPct: Double? = nil
    var takeProfitRetrace: Double? = nil
    /// V2.3.2-⑤(K8.md §十九):对外退出语义 —— 「−5% 触发的是什么」。
    /// `lossWarningAction == "review"` = **亏损警戒 + 由你完成离场决策**,系统永不代下单。
    /// ⚠ **老卡上为 `nil` 是正常的**:`card_json` 是冻结快照(`INSERT OR IGNORE` 永不覆盖),
    /// 新键不回填历史卡 —— ⛔ 别渲染成「配置丢了」。`stopPct` 本版**保留不删**(两步淘汰)。
    var lossWarningPct: Double? = nil
    var lossWarningAction: String? = nil
    var charterVersion: String? = nil
    var packVersion: String? = nil
    /// 服务端契约是 int(`neckline/db.py` 三处 `engine_api_version INTEGER`,`selection/
    /// pack.py`/`aggregate.py` 的 pydantic 字段同为 `int`)。2026-08-05 定向快修:此前
    /// 误写成 `String?`,生产恒发数字 `1` → `typeMismatch` 直接拖炸**整份**报告解码
    /// (Mac 实证,iPhone 同代码同炸)。
    var engineApiVersion: Int? = nil
    var verificationRulesetVersion: String? = nil

    enum CodingKeys: String, CodingKey {
        case stopPct, takeProfitRetrace, lossWarningPct, lossWarningAction
        case charterVersion, packVersion
        case engineApiVersion, verificationRulesetVersion
    }

    init(stopPct: Double? = nil, takeProfitRetrace: Double? = nil,
         lossWarningPct: Double? = nil, lossWarningAction: String? = nil,
         charterVersion: String? = nil,
         packVersion: String? = nil, engineApiVersion: Int? = nil,
         verificationRulesetVersion: String? = nil) {
        self.stopPct = stopPct; self.takeProfitRetrace = takeProfitRetrace
        self.lossWarningPct = lossWarningPct; self.lossWarningAction = lossWarningAction
        self.charterVersion = charterVersion; self.packVersion = packVersion
        self.engineApiVersion = engineApiVersion
        self.verificationRulesetVersion = verificationRulesetVersion
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        stopPct = try c.decodeIfPresent(Double.self, forKey: .stopPct)
        takeProfitRetrace = try c.decodeIfPresent(Double.self, forKey: .takeProfitRetrace)
        lossWarningPct = try c.decodeIfPresent(Double.self, forKey: .lossWarningPct)
        lossWarningAction = try c.decodeIfPresent(String.self, forKey: .lossWarningAction)
        charterVersion = try c.decodeIfPresent(String.self, forKey: .charterVersion)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        // B 类冻结快照字段(`basket_cards.card_json` 写入当时冻住,`INSERT OR IGNORE`
        // 永不覆盖)——容错双态解码:数字优先(现役契约),字符串数字也认(防万一某张
        // 历史卡是字符串形态),都没有 / 都解不出才 nil,⛔ 不许因这一个字段整份报告炸。
        if let n = try? c.decode(Int.self, forKey: .engineApiVersion) {
            engineApiVersion = n
        } else if let s = try? c.decode(String.self, forKey: .engineApiVersion) {
            engineApiVersion = Int(s)
        } else {
            engineApiVersion = nil
        }
        verificationRulesetVersion = try c.decodeIfPresent(String.self,
                                                           forKey: .verificationRulesetVersion)
    }
}

/// 价格区间参考件(`entryZone` = `{low, high, why}`;`exitReference` = `{low, high}`)。
/// ⛔ **`exitReference` 不是止盈线**(§2.8-C 语义红线)——文案里不许这么写:回落止盈
/// 才是纪律,离场参考只是来源篮子卡上的一个参考位。
struct BasketPriceBand: Codable, Equatable {
    var low: Double? = nil
    var high: Double? = nil
    var why: String? = nil

    enum CodingKeys: String, CodingKey { case low, high, why }

    init(low: Double? = nil, high: Double? = nil, why: String? = nil) {
        self.low = low; self.high = high; self.why = why
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        low = try c.decodeIfPresent(Double.self, forKey: .low)
        high = try c.decodeIfPresent(Double.self, forKey: .high)
        why = try c.decodeIfPresent(String.self, forKey: .why)
    }

    /// `nil` = 夹逼闸拒收(值为 null 且原因非空)——⛔ 展示时不许把 nil 画成 0 或空白。
    var rangeText: String? {
        guard let lo = low, let hi = high else { return nil }
        return "¥\(String(format: "%.2f", lo)) ~ ¥\(String(format: "%.2f", hi))"
    }

    /// 🔴 **V2.4.0 P3.4:等分格里那一版**(`30.60–31.95`)—— 去掉两个 `¥`、分隔符换成
    /// 短破折号。**理由是实拍逮到的**:首选成员块三格等分,393pt 上每格约 110pt,而
    /// `¥30.60 ~ ¥31.95` 在 15/600 下约 120pt → **被截成 `¥30.60 ~ ¥31…`**
    /// ——一个看不出上沿是多少的区间比不写更糟(同 `CLAUDE.md` 竞价卡日期那条)。
    /// ⚠ 只给**格位**用;句子里、明细行里仍用 `rangeText`(那里带 `¥` 更清楚、也放得下)。
    var compactRangeText: String? {
        guard let lo = low, let hi = high else { return nil }
        return "\(String(format: "%.2f", lo))–\(String(format: "%.2f", hi))"
    }
}

/// ⑦-K7 成员标注件一条(与 `InfoCardOut.tags` 同源同形,`selection/member_tags.py`
/// 唯一实现)。`text` **已含「参考、非指令」后缀,客户端不许改写、不许截断**。
/// **四不硬约束**:不进排序 / 不进哨兵 / 不改去留 / 不加分。
struct BasketMemberTag: Codable, Equatable, Identifiable {
    var code: String = ""
    var label: String = ""
    var tone: String = "neutral"   // neutral | warn
    var text: String = ""
    var source: String = ""

    var id: String { code }
    var axisTone: NKAxisTone { tone == "warn" ? .warn : .neutral }

    enum CodingKeys: String, CodingKey { case code, label, tone, text, source }

    init(code: String = "", label: String = "", tone: String = "neutral",
         text: String = "", source: String = "") {
        self.code = code; self.label = label; self.tone = tone
        self.text = text; self.source = source
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decodeIfPresent(String.self, forKey: .code) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        tone = try c.decodeIfPresent(String.self, forKey: .tone) ?? "neutral"
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
    }
}

/// 篮子卡上的一名成员(**B 类冻结快照**)。
///
/// **两条展示纪律写死在类型注释里**:
///  1. `roleLlm` / `roleMech` 是**两说并存**的对拍结果 —— `roleConflict=true` 时
///     **两个都显示**,⛔ 不许挑一个当"正确答案"。
///  2. 三个参考件各带 `*Clamp` + `*UnavailableReason`:夹逼闸拒收时值是 `nil` 且原因
///     非空,⛔ 不许把 `nil` 显示成 `0` 或空白了事。
struct BasketMember: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    var roleLlm: String? = nil
    var roleMech: String? = nil
    var roleConflict: Bool = false
    var reason: String = ""
    var isPrimary: Bool = false
    var industry: String? = nil
    var industryLift: Double? = nil
    var liftReason: String? = nil
    var primaryReason: String? = nil
    var rsRank: Int? = nil
    var k4Tag: String? = nil
    // —— V2.2-③-C 位置关(裁定 #11:机械层只出读数,判定交 LLM,只降级不除名)——
    // `positionVerdict` ∈ ok(合适)/ weak(勉强,降一档)/ unfit(不合适,退出正式
    // 候选但**仍在 ③b 列名**,⛔ 非硬否决)。`positionMetrics` = 当次喂给模型的
    // 落地起跳读数原样(⛔ 缺项就是真的没取到,不是 0)。**老卡缺这三键是常态**——
    // 纯新增,`landingState` 那个作废的四态枚举从未上过产,零删键。
    var positionVerdict: String? = nil
    var positionReason: String? = nil
    var positionMetrics: NKJSON? = nil
    // —— V2.2-③-C2 核心关(裁定 #12:同构但独立于位置关,问的是"是不是那一群的
    // 龙头")—— `coreMetrics` 一定带分母(`industry_member_count`),⛔ 缺项不是 0。
    var coreVerdict: String? = nil
    var coreReason: String? = nil
    var coreMetrics: NKJSON? = nil

    /// 🔴 **V2.4.0 P1.3:比较域五字段**(与读数同住 `coreMetrics`,服务端唯一实现
    /// `selection/core_metrics.py::resolve_comparison_domain`)。
    /// **「跟谁比」与「读数按什么算」是两件事**:读数六项**恒按行业算**,比较域可能是
    /// 驱动域 —— ⛔ 别把它们读成同一群(展示上因此单独一行,不混进读数宫格)。
    /// ⚠ **`peerCodes` 刻意不上屏**:驱动域就是那张成员清单,行业域动辄几十上百只,
    /// 铺在卡上没人读得完;完整名单在服务端审计记录里(`gate_evaluations`)。
    var comparisonDomainLabel: String? {
        guard let obj = coreMetrics?.objectValue else { return nil }
        // ⚠ 三态:driver / industry / **null = 没有可用的比较域**(⛔ 不是"没记录")
        let raw = obj["comparison_domain"]?.stringValue
        let hasKey = obj["comparison_domain"] != nil
        guard hasKey else { return nil }                   // 老卡没有这五个字段
        let name: String
        switch raw {
        case "driver": name = "同一主要驱动的候选成员域"
        case "theme": name = "同一题材 / 方向"
        case "industry": name = "同行业"
        case nil, .some(""): name = "没有可用的比较域"
        default: name = raw ?? ""                          // 未识别码原样透传,⛔ 不瞎翻译
        }
        var bits = [name]
        if let key = obj["comparison_domain_key"]?.stringValue, !key.isEmpty {
            bits.append(key)
        }
        if let n = obj["peer_count"]?.intValue { bits.append("同域 \(n) 只") }
        return bits.joined(separator: " · ")
    }
    /// 机械面板原样透传(⑦ `MemberMech.to_dict()`,自由结构)。
    var mech: NKJSON = .object([:])
    var entryZone: BasketPriceBand? = nil
    var entryZoneClamp: String = ""
    var entryZoneUnavailableReason: String? = nil
    var maxChase: Double? = nil
    var maxChaseClamp: String = ""
    var maxChaseUnavailableReason: String? = nil
    var exitReference: BasketPriceBand? = nil
    var exitReferenceClamp: String = ""
    var exitReferenceUnavailableReason: String? = nil
    var tags: [BasketMemberTag] = []
    /// **判不了的标注码** —— 与「判过没命中」是两回事,⛔ 不许合并成"没有标注"。
    var tagsAbsent: [String] = []

    var id: String { tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, roleLlm, roleMech, roleConflict, reason, isPrimary
        case industry, industryLift, liftReason, primaryReason, rsRank, k4Tag
        case positionVerdict, positionReason, positionMetrics
        case coreVerdict, coreReason, coreMetrics
        case mech
        case entryZone, entryZoneClamp, entryZoneUnavailableReason
        case maxChase, maxChaseClamp, maxChaseUnavailableReason
        case exitReference, exitReferenceClamp, exitReferenceUnavailableReason
        case tags, tagsAbsent
    }

    init(tsCode: String = "", name: String = "", roleLlm: String? = nil, roleMech: String? = nil,
         roleConflict: Bool = false, reason: String = "", isPrimary: Bool = false,
         industry: String? = nil, industryLift: Double? = nil, liftReason: String? = nil,
         primaryReason: String? = nil, rsRank: Int? = nil, k4Tag: String? = nil,
         positionVerdict: String? = nil, positionReason: String? = nil,
         positionMetrics: NKJSON? = nil, coreVerdict: String? = nil, coreReason: String? = nil,
         coreMetrics: NKJSON? = nil,
         mech: NKJSON = .object([:]), entryZone: BasketPriceBand? = nil,
         entryZoneClamp: String = "", entryZoneUnavailableReason: String? = nil,
         maxChase: Double? = nil, maxChaseClamp: String = "",
         maxChaseUnavailableReason: String? = nil, exitReference: BasketPriceBand? = nil,
         exitReferenceClamp: String = "", exitReferenceUnavailableReason: String? = nil,
         tags: [BasketMemberTag] = [], tagsAbsent: [String] = []) {
        self.tsCode = tsCode; self.name = name; self.roleLlm = roleLlm; self.roleMech = roleMech
        self.roleConflict = roleConflict; self.reason = reason; self.isPrimary = isPrimary
        self.industry = industry; self.industryLift = industryLift; self.liftReason = liftReason
        self.primaryReason = primaryReason; self.rsRank = rsRank; self.k4Tag = k4Tag
        self.positionVerdict = positionVerdict; self.positionReason = positionReason
        self.positionMetrics = positionMetrics
        self.coreVerdict = coreVerdict; self.coreReason = coreReason; self.coreMetrics = coreMetrics
        self.mech = mech; self.entryZone = entryZone; self.entryZoneClamp = entryZoneClamp
        self.entryZoneUnavailableReason = entryZoneUnavailableReason
        self.maxChase = maxChase; self.maxChaseClamp = maxChaseClamp
        self.maxChaseUnavailableReason = maxChaseUnavailableReason
        self.exitReference = exitReference; self.exitReferenceClamp = exitReferenceClamp
        self.exitReferenceUnavailableReason = exitReferenceUnavailableReason
        self.tags = tags; self.tagsAbsent = tagsAbsent
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        roleLlm = try c.decodeIfPresent(String.self, forKey: .roleLlm)
        roleMech = try c.decodeIfPresent(String.self, forKey: .roleMech)
        roleConflict = try c.decodeIfPresent(Bool.self, forKey: .roleConflict) ?? false
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        isPrimary = try c.decodeIfPresent(Bool.self, forKey: .isPrimary) ?? false
        industry = try c.decodeIfPresent(String.self, forKey: .industry)
        industryLift = try c.decodeIfPresent(Double.self, forKey: .industryLift)
        liftReason = try c.decodeIfPresent(String.self, forKey: .liftReason)
        primaryReason = try c.decodeIfPresent(String.self, forKey: .primaryReason)
        rsRank = try c.decodeIfPresent(Int.self, forKey: .rsRank)
        k4Tag = try c.decodeIfPresent(String.self, forKey: .k4Tag)
        positionVerdict = try c.decodeIfPresent(String.self, forKey: .positionVerdict)
        positionReason = try c.decodeIfPresent(String.self, forKey: .positionReason)
        positionMetrics = try c.decodeIfPresent(NKJSON.self, forKey: .positionMetrics)
        coreVerdict = try c.decodeIfPresent(String.self, forKey: .coreVerdict)
        coreReason = try c.decodeIfPresent(String.self, forKey: .coreReason)
        coreMetrics = try c.decodeIfPresent(NKJSON.self, forKey: .coreMetrics)
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
        entryZone = try c.decodeIfPresent(BasketPriceBand.self, forKey: .entryZone)
        entryZoneClamp = try c.decodeIfPresent(String.self, forKey: .entryZoneClamp) ?? ""
        entryZoneUnavailableReason = try c.decodeIfPresent(String.self,
                                                           forKey: .entryZoneUnavailableReason)
        maxChase = try c.decodeIfPresent(Double.self, forKey: .maxChase)
        maxChaseClamp = try c.decodeIfPresent(String.self, forKey: .maxChaseClamp) ?? ""
        maxChaseUnavailableReason = try c.decodeIfPresent(String.self,
                                                          forKey: .maxChaseUnavailableReason)
        exitReference = try c.decodeIfPresent(BasketPriceBand.self, forKey: .exitReference)
        exitReferenceClamp = try c.decodeIfPresent(String.self, forKey: .exitReferenceClamp) ?? ""
        exitReferenceUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .exitReferenceUnavailableReason)
        tags = try c.decodeIfPresent([BasketMemberTag].self, forKey: .tags) ?? []
        tagsAbsent = try c.decodeIfPresent([String].self, forKey: .tagsAbsent) ?? []
    }

    /// 角色两说的展示串(唯一实现 `nkRoleDisplay`,V2.3.1 §〇c 硬伤 2 收口)。
    var roleDisplay: String {
        nkRoleDisplay(roleLlm: roleLlm, roleMech: roleMech, roleConflict: roleConflict)
    }

    // —— V2.2-③-C/③-C2 位置关 / 核心关判定的**纯展示层换算**(沿 `nkBoardLabel` 先例:
    // 服务端只发英文码 ok/weak/unfit,中文与着色在客户端算,⛔ 不要服务端另建映射)。
    // `verdict == nil` 时 label 也是 `nil`(老卡缺这三键是常态)——调用方据此整行不显示,
    // ⛔ 不许显示成"未判定"这种看起来像结论的占位。

    var positionVerdictLabel: String? { positionVerdict.map(nkVerdictLabel) }
    var positionVerdictTone: NKAxisTone { positionVerdict.map(nkVerdictTone) ?? .neutral }
    var coreVerdictLabel: String? { coreVerdict.map(nkVerdictLabel) }
    var coreVerdictTone: NKAxisTone { coreVerdict.map(nkVerdictTone) ?? .neutral }
}

/// 一张 D0 冻结的篮子卡(**B 类冻结快照**,蓝图 4.6 十一项)。
///
/// `narrative` 是 LLM 叙述,**原文整段呈现**、不得拆解塞回枚举卡片(§2.7);
/// `degraded=true` = **人话半份缺席、结构化半份照出**(不是"这张卡不可信")。
/// `disclaimer` 是固定文案单一源,**客户端原样透传不改写**。
struct BasketCard: Codable, Equatable {
    var specVersion: String? = nil
    var version: Int? = nil
    var basketKey: String = ""
    var tradeDate: String = ""
    var nextTradeDate: String? = nil
    var name: String = ""
    var driver: String = ""
    var driverKind: String = ""
    // V2.2-③-E 引擎归属三键(裁定 #9:单篮子单引擎,成员继承篮子引擎;老卡缺键 =
    // 「当时没有引擎归属概念」,⛔ 不是 engine 为空)。⚠ 成员上没有这两键(引擎标在
    // 篮子上),要显示成员引擎从这里(或壳 `Basket.engineCode` 等)取。
    var engineCode: String? = nil
    var engineVersion: String? = nil
    var skeletonVersion: String? = nil
    var evidence: [BasketEvidence] = []
    /// ⑤ 两段式流水的单侧故障披露:`ok` | `search_unavailable` | `partial`。
    /// ⛔ 不是 `ok` 时必须显式标注"取证不完整",不许静默当完整证据展示。
    var evidenceStatus: String = ""
    var whyNow: String = ""
    var members: [BasketMember] = []
    var roleConflicts: [String] = []
    var tier: Int? = nil
    var rankInTier: Int? = nil
    var rankMech: Int? = nil
    var mechScore: Double? = nil
    /// 五维分项 + 权重。**键是维度名**(与现役包权重键逐字对应),原样透传、⛔ 不改名。
    var tierBreakdown: NKJSON = .object([:])
    var tierReason: String? = nil
    var tierNote: String? = nil
    /// V2.3.3-①(K8.md §十 第 8 项):卡 #6「预期上涨路径」——**一段话,不分支**。
    /// 开盘那一刻怎么办由次日 9:26 的集合竞价确认层负责,不是这张卡的事。
    var upsidePath: String? = nil
    var upsidePathUnavailableReason: String? = nil
    /// 喂 ⑧ 哨兵的结构化 spec(机器半份),原样透传。
    var verificationSpec: NKJSON = .object([:])
    var verificationText: String? = nil
    var invalidationSpec: NKJSON = .object([:])
    var invalidationText: String? = nil
    var risks: [String] = []
    var disclaimer: String = ""
    var fingerprint: BasketFingerprint = BasketFingerprint()
    var disciplineLabels: [String] = []
    var narrative: String = ""
    var llmStage: String = ""
    var degraded: Bool = false
    var notes: [String] = []

    enum CodingKeys: String, CodingKey {
        case specVersion, version, basketKey, tradeDate, nextTradeDate, name
        case driver, driverKind, engineCode, engineVersion, skeletonVersion
        case evidence, evidenceStatus, whyNow, members, roleConflicts
        case tier, rankInTier, rankMech, mechScore, tierBreakdown, tierReason, tierNote
        case upsidePath, upsidePathUnavailableReason
        case verificationSpec, verificationText, invalidationSpec, invalidationText
        case risks, disclaimer, fingerprint, disciplineLabels, narrative, llmStage, degraded, notes
    }

    init(specVersion: String? = nil, version: Int? = nil, basketKey: String = "",
         tradeDate: String = "", nextTradeDate: String? = nil, name: String = "",
         driver: String = "", driverKind: String = "", engineCode: String? = nil,
         engineVersion: String? = nil, skeletonVersion: String? = nil,
         evidence: [BasketEvidence] = [],
         evidenceStatus: String = "", whyNow: String = "", members: [BasketMember] = [],
         roleConflicts: [String] = [], tier: Int? = nil, rankInTier: Int? = nil,
         rankMech: Int? = nil, mechScore: Double? = nil, tierBreakdown: NKJSON = .object([:]),
         tierReason: String? = nil, tierNote: String? = nil, upsidePath: String? = nil,
         upsidePathUnavailableReason: String? = nil, verificationSpec: NKJSON = .object([:]),
         verificationText: String? = nil, invalidationSpec: NKJSON = .object([:]),
         invalidationText: String? = nil, risks: [String] = [], disclaimer: String = "",
         fingerprint: BasketFingerprint = BasketFingerprint(), disciplineLabels: [String] = [],
         narrative: String = "", llmStage: String = "", degraded: Bool = false,
         notes: [String] = []) {
        self.specVersion = specVersion; self.version = version; self.basketKey = basketKey
        self.tradeDate = tradeDate; self.nextTradeDate = nextTradeDate; self.name = name
        self.driver = driver; self.driverKind = driverKind
        self.engineCode = engineCode; self.engineVersion = engineVersion
        self.skeletonVersion = skeletonVersion; self.evidence = evidence
        self.evidenceStatus = evidenceStatus; self.whyNow = whyNow; self.members = members
        self.roleConflicts = roleConflicts; self.tier = tier; self.rankInTier = rankInTier
        self.rankMech = rankMech; self.mechScore = mechScore; self.tierBreakdown = tierBreakdown
        self.tierReason = tierReason; self.tierNote = tierNote; self.upsidePath = upsidePath
        self.upsidePathUnavailableReason = upsidePathUnavailableReason
        self.verificationSpec = verificationSpec; self.verificationText = verificationText
        self.invalidationSpec = invalidationSpec; self.invalidationText = invalidationText
        self.risks = risks; self.disclaimer = disclaimer; self.fingerprint = fingerprint
        self.disciplineLabels = disciplineLabels; self.narrative = narrative
        self.llmStage = llmStage; self.degraded = degraded; self.notes = notes
    }

    /// **B 类冻结快照 → 全字段 `decodeIfPresent`**(⛔ 一个 `try c.decode` 都不许有):
    /// `card_json` 是写入当时冻住的,服务端升级永远不会给老卡补新键;硬解码任何一个键,
    /// 装了新 App 的用户翻几周前的老卡就是**整张卡解不出**。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        specVersion = try c.decodeIfPresent(String.self, forKey: .specVersion)
        version = try c.decodeIfPresent(Int.self, forKey: .version)
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        nextTradeDate = try c.decodeIfPresent(String.self, forKey: .nextTradeDate)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        driver = try c.decodeIfPresent(String.self, forKey: .driver) ?? ""
        driverKind = try c.decodeIfPresent(String.self, forKey: .driverKind) ?? ""
        engineCode = try c.decodeIfPresent(String.self, forKey: .engineCode)
        engineVersion = try c.decodeIfPresent(String.self, forKey: .engineVersion)
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion)
        evidence = try c.decodeIfPresent([BasketEvidence].self, forKey: .evidence) ?? []
        evidenceStatus = try c.decodeIfPresent(String.self, forKey: .evidenceStatus) ?? ""
        whyNow = try c.decodeIfPresent(String.self, forKey: .whyNow) ?? ""
        members = try c.decodeIfPresent([BasketMember].self, forKey: .members) ?? []
        roleConflicts = try c.decodeIfPresent([String].self, forKey: .roleConflicts) ?? []
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        rankInTier = try c.decodeIfPresent(Int.self, forKey: .rankInTier)
        rankMech = try c.decodeIfPresent(Int.self, forKey: .rankMech)
        mechScore = try c.decodeIfPresent(Double.self, forKey: .mechScore)
        tierBreakdown = try c.decodeIfPresent(NKJSON.self, forKey: .tierBreakdown) ?? .object([:])
        tierReason = try c.decodeIfPresent(String.self, forKey: .tierReason)
        tierNote = try c.decodeIfPresent(String.self, forKey: .tierNote)
        upsidePath = try c.decodeIfPresent(String.self, forKey: .upsidePath)
        upsidePathUnavailableReason = try c.decodeIfPresent(String.self,
                                                            forKey: .upsidePathUnavailableReason)
        verificationSpec = try c.decodeIfPresent(NKJSON.self, forKey: .verificationSpec) ?? .object([:])
        verificationText = try c.decodeIfPresent(String.self, forKey: .verificationText)
        invalidationSpec = try c.decodeIfPresent(NKJSON.self, forKey: .invalidationSpec) ?? .object([:])
        invalidationText = try c.decodeIfPresent(String.self, forKey: .invalidationText)
        risks = try c.decodeIfPresent([String].self, forKey: .risks) ?? []
        disclaimer = try c.decodeIfPresent(String.self, forKey: .disclaimer) ?? ""
        fingerprint = try c.decodeIfPresent(BasketFingerprint.self,
                                            forKey: .fingerprint) ?? BasketFingerprint()
        disciplineLabels = try c.decodeIfPresent([String].self, forKey: .disciplineLabels) ?? []
        narrative = try c.decodeIfPresent(String.self, forKey: .narrative) ?? ""
        llmStage = try c.decodeIfPresent(String.self, forKey: .llmStage) ?? ""
        degraded = try c.decodeIfPresent(Bool.self, forKey: .degraded) ?? false
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
    }

    /// 取证完整性展示(⛔ 不是 `ok` 就必须说出来,不许静默当完整证据展示)。
    var evidenceIncompleteNote: String? {
        switch evidenceStatus {
        case "", "ok": return nil
        case "search_unavailable": return "取证不完整 · 检索侧不可用,以下证据未经联网核实"
        case "partial": return "取证不完整 · 仅部分证据经检索核实"
        default: return "取证不完整(\(evidenceStatus))"
        }
    }
}

/// 百分制打分卡里的**一维贡献**(V2.1-④,**纯展示层**)。
///
/// `contribPercent = 归一化权重 × 该维得分 × 100`,五维合计 ≈ `scorePercent`
/// (各项独立舍入,末位可能差零点几)。**唯一换算实现在服务端**
/// `neckline/report/score_display.py` —— ⛔ 客户端不重算、不另建中文标签表
/// (`label` 由服务端给);本类型只负责把已经算好的数**格式化**成一位小数。
/// ⚠ 契约里 `contribPercent` 是 **4 位小数**(精度住契约),展示层各自 `:.1f`
/// (位数住展示)—— ⛔ 别在解码时先舍入,那会把"五维合计 ≈ 总分"的自洽性吃掉。
///
/// 🔴 **`neutralFilled == true` 是一句必须说出口的话**:那一维今天**没算出来**、
/// 按中性分 0.5 计入 —— 它撑起来的那部分分数**不是「这一维表现好」**。
/// ⛔ 不许渲染成与其它维度无差别的一根条(§3.8「没有」与「没看」必须分得开)。
///
/// `dimScore` / `weight` 为 `nil` = 该维在冻结留痕里就缺这个数,**⛔ 不是 0**。
struct ScoreContribution: Codable, Equatable, Identifiable {
    var dim: String = ""
    var label: String = ""
    var dimScore: Double? = nil
    var weight: Double? = nil
    var contribPercent: Double? = nil
    var neutralFilled: Bool = false

    var id: String { dim }

    enum CodingKeys: String, CodingKey {
        case dim, label, dimScore, weight, contribPercent, neutralFilled
    }

    init(dim: String = "", label: String = "", dimScore: Double? = nil, weight: Double? = nil,
         contribPercent: Double? = nil, neutralFilled: Bool = false) {
        self.dim = dim; self.label = label; self.dimScore = dimScore; self.weight = weight
        self.contribPercent = contribPercent; self.neutralFilled = neutralFilled
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dim = try c.decodeIfPresent(String.self, forKey: .dim) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        dimScore = try c.decodeIfPresent(Double.self, forKey: .dimScore)
        weight = try c.decodeIfPresent(Double.self, forKey: .weight)
        contribPercent = try c.decodeIfPresent(Double.self, forKey: .contribPercent)
        neutralFilled = try c.decodeIfPresent(Bool.self, forKey: .neutralFilled) ?? false
    }

    /// 展示名:服务端给了中文 `label` 就用它,没给(未登记的新维度)原样显示 `dim`
    /// —— ⛔ 不在客户端另建一份中文映射(那就是第二个会漂的语义来源)。
    var displayLabel: String { label.isEmpty ? dim : label }
}

/// 一篮的 Tier 定档留痕(`tier_history` 一行,**A 类**)。
/// **Tier = 注意力优先级,不是收益预测**(§2.8-C 红线):`rankInTier` 排第一 ≠ 最会涨。
///
/// `tier` 取值域:**新数据 ∈ {1, 2}**(V2.1-② T3 全链退役,写侧收窄);
/// 历史留痕行仍可能是 `3` —— ⛔ 客户端别把 3 当非法值,那是 V2 时代的真实数据。
struct Tier: Codable, Equatable {
    var basketId: Int = 0
    var tradeDate: String = ""
    var tier: Int? = nil
    var mechScore: Double? = nil
    var mechBreakdown: NKJSON = .object([:])
    var rankInTier: Int? = nil
    /// LLM 微调**之前**的机械序;与 `llmRankDelta`(微调位移)两个都留着,定档才谈得上可复现。
    var rankMech: Int? = nil
    var llmRankDelta: Int = 0
    var llmReason: String? = nil
    var packVersion: String? = nil
    /// **V2.1-④ 新增两个只读键**:`mechScore` 的百分制等价换算 + 五维拆解,
    /// 由服务端 `report/score_display.py` 从**同一份已冻结的 `mechBreakdown`** 算出。
    /// ⛔ 它不是第二个分数、不进任何判定路径。
    /// `scorePercent == nil` = 这一篮取不到分(没有 breakdown),**⛔ 不是 0 分**。
    var scorePercent: Double? = nil
    var scoreContributions: [ScoreContribution] = []

    enum CodingKeys: String, CodingKey {
        case basketId, tradeDate, tier, mechScore, mechBreakdown
        case rankInTier, rankMech, llmRankDelta, llmReason, packVersion
        case scorePercent, scoreContributions
    }

    init(basketId: Int = 0, tradeDate: String = "", tier: Int? = nil, mechScore: Double? = nil,
         mechBreakdown: NKJSON = .object([:]), rankInTier: Int? = nil, rankMech: Int? = nil,
         llmRankDelta: Int = 0, llmReason: String? = nil, packVersion: String? = nil,
         scorePercent: Double? = nil, scoreContributions: [ScoreContribution] = []) {
        self.basketId = basketId; self.tradeDate = tradeDate; self.tier = tier
        self.mechScore = mechScore; self.mechBreakdown = mechBreakdown
        self.rankInTier = rankInTier; self.rankMech = rankMech; self.llmRankDelta = llmRankDelta
        self.llmReason = llmReason; self.packVersion = packVersion
        self.scorePercent = scorePercent; self.scoreContributions = scoreContributions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        mechScore = try c.decodeIfPresent(Double.self, forKey: .mechScore)
        mechBreakdown = try c.decodeIfPresent(NKJSON.self, forKey: .mechBreakdown) ?? .object([:])
        rankInTier = try c.decodeIfPresent(Int.self, forKey: .rankInTier)
        rankMech = try c.decodeIfPresent(Int.self, forKey: .rankMech)
        llmRankDelta = try c.decodeIfPresent(Int.self, forKey: .llmRankDelta) ?? 0
        llmReason = try c.decodeIfPresent(String.self, forKey: .llmReason)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        scorePercent = try c.decodeIfPresent(Double.self, forKey: .scorePercent)
        scoreContributions = try c.decodeIfPresent([ScoreContribution].self,
                                                   forKey: .scoreContributions) ?? []
    }
}

/// 一个篮子的壳(**A 类**)。`card == nil` + `cardUnavailableReason == "card_not_ready"`
/// = **篮子在、卡没生成**(⑦ 事务 1 与事务 2 分开,合法中间态)。
/// ⛔ 不许把它显示成「篮子不存在」——那是另一回事(`basket_not_found`)。
struct Basket: Codable, Equatable, Identifiable {
    var basketId: Int = 0
    var basketKey: String = ""
    var name: String = ""
    var tradeDate: String = ""
    var tier: Int? = nil
    var memberCodes: [String] = []
    // V2.2-③-E 引擎归属三键(裁定 #9:单篮子单引擎,成员继承篮子引擎)。⚠ **与
    // `scorePercent` 不同构**:live(`GET /baskets`)与报告快照两条路**都**从
    // `baskets` 表行直填(服务端 `_shape_basket`/`load_today_baskets` 同一姿势),
    // ⛔ 不是"live 路径刻意留空"那种非对称 —— 这里不需要 `?? tierHistory` 式合并。
    // `card.engineCode` 等是同一次落库写入在冻结卡里的另一份拷贝,读法见下方
    // `engineVersionDisplay`(壳优先,壳缺席时兜底读卡)。
    var engineCode: String? = nil
    var engineVersion: String? = nil
    var skeletonVersion: String? = nil
    var card: BasketCard? = nil
    var cardVersion: Int? = nil
    var cardUnavailableReason: String? = nil
    var tierHistory: Tier? = nil
    /// 🔴 **这两键只在「报告快照」这条路上有值**(V2.1-④,**B 类:随
    /// `reports.basket_daily_json` 冻住**);**live 路径(`GET /baskets`)刻意留空**,
    /// 那条路上同一个数住 `tierHistory.scorePercent`(分数是定档留痕的属性)。
    /// ⛔ 读的时候别只读一处 —— 唯一正确读法是下面的 `scoreDisplayPercent`
    /// (= `scorePercent ?? tierHistory?.scorePercent`,服务端 `BasketOut` docstring 定死)。
    var scorePercent: Double? = nil
    var scoreContributions: [ScoreContribution] = []

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, basketKey, name, tradeDate, tier, memberCodes
        case engineCode, engineVersion, skeletonVersion
        case card, cardVersion, cardUnavailableReason, tierHistory
        case scorePercent, scoreContributions
    }

    init(basketId: Int = 0, basketKey: String = "", name: String = "", tradeDate: String = "",
         tier: Int? = nil, memberCodes: [String] = [], engineCode: String? = nil,
         engineVersion: String? = nil, skeletonVersion: String? = nil, card: BasketCard? = nil,
         cardVersion: Int? = nil, cardUnavailableReason: String? = nil, tierHistory: Tier? = nil,
         scorePercent: Double? = nil, scoreContributions: [ScoreContribution] = []) {
        self.basketId = basketId; self.basketKey = basketKey; self.name = name
        self.tradeDate = tradeDate; self.tier = tier; self.memberCodes = memberCodes
        self.engineCode = engineCode; self.engineVersion = engineVersion
        self.skeletonVersion = skeletonVersion
        self.card = card; self.cardVersion = cardVersion
        self.cardUnavailableReason = cardUnavailableReason; self.tierHistory = tierHistory
        self.scorePercent = scorePercent; self.scoreContributions = scoreContributions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        memberCodes = try c.decodeIfPresent([String].self, forKey: .memberCodes) ?? []
        engineCode = try c.decodeIfPresent(String.self, forKey: .engineCode)
        engineVersion = try c.decodeIfPresent(String.self, forKey: .engineVersion)
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion)
        card = try c.decodeIfPresent(BasketCard.self, forKey: .card)
        cardVersion = try c.decodeIfPresent(Int.self, forKey: .cardVersion)
        cardUnavailableReason = try c.decodeIfPresent(String.self, forKey: .cardUnavailableReason)
        tierHistory = try c.decodeIfPresent(Tier.self, forKey: .tierHistory)
        scorePercent = try c.decodeIfPresent(Double.self, forKey: .scorePercent)
        scoreContributions = try c.decodeIfPresent([ScoreContribution].self,
                                                   forKey: .scoreContributions) ?? []
    }

    // —— V2.1-④ 百分制打分卡的**唯一读法**(两条路各填一处,展示层收口在这里)——

    /// 本篮的百分分数。`nil` = **本篮无打分可显示**(老报告快照没有这两个键 /
    /// 这一篮没有定档留痕)—— 🔴 **⛔ 绝不是 0 分**,展示处如实写「本报告版本无打分」。
    var scoreDisplayPercent: Double? { scorePercent ?? tierHistory?.scorePercent }

    /// 与 `scoreDisplayPercent` **同源**的五维拆解:分数从哪条路来,拆解就从哪条路取
    /// (⛔ 不许一处取分、另一处取拆解 —— 那会在同一张卡上拼出两份数据的混合体)。
    var scoreDisplayContributions: [ScoreContribution] {
        scorePercent != nil ? scoreContributions : (tierHistory?.scoreContributions ?? [])
    }

    // —— V2.2-③-E 引擎徽标的**唯一读法**(壳优先,壳缺席〔极旧数据〕时兜底读卡;
    // 两条路正常情况下逐位相同,见上方字段注释,这里的 `??` 只是防御性兜底,
    // ⛔ 不是"两条路刻意不同步"那种语义)——

    var engineCodeDisplay: String? { engineCode ?? card?.engineCode }
    var engineVersionDisplay: String? { engineVersion ?? card?.engineVersion }
    var skeletonVersionDisplay: String? { skeletonVersion ?? card?.skeletonVersion }

    /// V2.2-③ 六关灯条的**唯一读法**(两条路各自带一份**同一次写入**的拷贝):
    /// 报告快照路只有 `card.tierBreakdown`(⚠ `BasketView` 不发 `tierHistory`),
    /// live 路(`GET /baskets/{id}`)两处都有 —— 故卡优先、留痕兜底。
    /// ⛔ 这不是"两份可能不同步的数据":`tier.py` 把同一个 breakdown dict 同时写进
    /// `tier_history.mech_breakdown` 与卡的 `tier_breakdown`。
    var gates: BasketGates {
        let g = BasketGates(tierBreakdown: card?.tierBreakdown)
        return g.available ? g : BasketGates(tierBreakdown: tierHistory?.mechBreakdown)
    }

    /// 「落地起跳位置态」的篮子级摘要(裁定 #11:位置关是**成员级**判定,篮子级只能
    /// 说"最差的那只是什么")。nil = 这张卡没有任何成员带位置判定(老卡常态)。
    /// ⛔ 别把 nil 显示成「位置合适」。
    var worstPositionVerdict: String? { nkWorstVerdict(card?.members.compactMap(\.positionVerdict)) }

    /// 核心关同款(裁定 #12)。
    var worstCoreVerdict: String? { nkWorstVerdict(card?.members.compactMap(\.coreVerdict)) }

    /// 卡未就绪时的诚实文案。⛔ **不是**「篮子不存在」。
    var cardUnavailableText: String? {
        guard card == nil else { return nil }
        switch cardUnavailableReason {
        case "card_not_ready", .none: return "本篮的卡还没生成"
        // B1(2026-08-04):「有卡行但读不出」是**数据事故**,不是等待中 ——
        // ⛔ 不许合进上一条,那会让用户以为再等等就有了(卡是冻结件,不会自己好)。
        case "card_corrupt": return "本篮卡数据损坏,已记录待排查"
        case .some(let r): return "本篮的卡暂不可用(\(r))"
        }
    }
}

/// ③b 未定档篮子一行(⑥-b-C;**V2.2-③ 起「名 / 分 / 卡在哪一关、差多少 / 原因码」**)。
/// 🔴 **每个 `reason` 码指向不同的市场 / 系统结论,⛔ 不许合并成一句「未入选」**——
/// 逐码含义见 `nkDroppedReasonLabel`(唯一源 = 服务端
/// `report/basket_daily.py::DROPPED_REASON_LABEL`,这里是它的中文展示层镜像)。
/// `gate`/`gateDetail` 是 **V2.2 新增可选键**:老快照缺它们 = 该版本还没有关口概念
/// (⛔ 不是「没卡在任何关」)。
/// **没有 `basketId`** —— 它没进 `baskets` 表,给一个 id 会让用户以为点得进去。
struct DroppedBasket: Codable, Equatable, Identifiable {
    var name: String = ""
    var mechScore: Double? = nil
    var reason: String = ""
    /// 卡在哪一关(`market|driver|sector|core|position|evidence`);nil = 老快照 / 与关口无关。
    var gate: String? = nil
    /// 差多少(服务端的机器原因码串,数值内嵌)。**原样展示**,⛔ 不改写、不翻译。
    var gateDetail: String? = nil

    // ⚠ id 必须把 `gate` 也算进去:同一篮同一 reason 但卡在不同关是可能的(如
    // 证据关降级出局时 `gate` 取第一道降级关),不带进 id 会让 ForEach 撞 key。
    var id: String { "\(name)|\(reason)|\(gate ?? "")" }

    enum CodingKeys: String, CodingKey { case name, mechScore, reason, gate, gateDetail }

    init(name: String = "", mechScore: Double? = nil, reason: String = "",
         gate: String? = nil, gateDetail: String? = nil) {
        self.name = name; self.mechScore = mechScore; self.reason = reason
        self.gate = gate; self.gateDetail = gateDetail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        mechScore = try c.decodeIfPresent(Double.self, forKey: .mechScore)
        reason = try c.decodeIfPresent(String.self, forKey: .reason)  ?? ""
        gate = try c.decodeIfPresent(String.self, forKey: .gate)
        gateDetail = try c.decodeIfPresent(String.self, forKey: .gateDetail)
    }

    var reasonLabel: String { nkDroppedReasonLabel(reason) }
    var reasonTone: NKAxisTone { nkDroppedReasonTone(reason) }

    /// V2.3 信息层级:把「主句 · 补充」拆成两行(⛔ **不改字面**,只是换行位置)。
    /// 主句着 `reasonTone`、补充走次级色 —— 一列九种原因挤成同色同字号的一坨,
    /// 「系统缺席」那两码就淹在里面了,而它们恰恰是最该被看见的。
    var reasonHeadline: String {
        reasonLabel.components(separatedBy: " · ").first ?? reasonLabel
    }
    /// `nil` = 这个码没有补充句(如未识别码原样透传)。
    var reasonDetail: String? {
        let parts = reasonLabel.components(separatedBy: " · ")
        guard parts.count > 1 else { return nil }
        return parts.dropFirst().joined(separator: " · ")
    }
    /// 「卡在哪一关」的人读名;nil = 老快照没有这个键(⛔ 不写「无」)。
    var gateLabel: String? { gate.map(nkGateLabel) }
    /// 该关是机械关(硬否决)还是证据关(只降级)—— nil = 不知道是哪一关。
    var gateKind: NKGateKind? { gate.map(nkGateKind) }
}

/// ③b 的**另一类行**:V2.3.2-②-B 的**股票级 OUT**(K8 §六 候选三态 T1/T2/OUT 之一)。
///
/// ⚠ **与 `DroppedBasket` 刻意不合并、界面分两段渲染**:那一类是**篮子级**的
/// 「档位已满 · 未定档」(`capacity_overflow` —— K8 §八 的 OUT 适用状态里**没有**
/// "位置满",它不是 OUT);这一类才是 OUT。⛔ 别为了"少一张表"合起来。
///
/// `outReason` 与 `DroppedBasket.reason` **共用同一套原因码**(`nkDroppedReasonLabel`),
/// ⛔ 不另起第二套词表。**没有 basketId** —— 它没进 `baskets` 表,给一个 id 会让人
/// 以为点得进去。
struct OutCandidate: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    /// `leader|core|elastic`(LLM 主张);nil = 没给。中文换算走 `nkRoleLabel`。
    var role: String? = nil
    var engineCode: String? = nil
    var engineVersion: String? = nil
    /// 出局关口(`market|driver|sector|core|position|evidence`);nil = 非关口原因
    /// (如引擎缺席)。
    var outGate: String? = nil
    var outReason: String = ""
    /// 差多少 / 模型理由(服务端原因码串,数值内嵌)。**原样展示**,⛔ 不改写。
    var outDetail: String? = nil
    /// 出局时所在的篮子标识。**⛔ 不是 basketId**(点不进去),只用于消歧。
    var basketKey: String? = nil

    // 🔴 同一只票可能在同一天的**多个** OUT 篮里出现(篮子间成员可重叠,服务端
    // `out_candidates` 的主键就含 `basket_key`)。⚠ 只拿 `tsCode|outReason|outGate`
    // 当 id **挡不住碰撞**:同码同关同因、只是篮子不同的两行会撞 ForEach 主键
    // (2026-08-11 复审逮到)。⛔ 别把 basketKey 从这里拿掉。
    var id: String { "\(tsCode)|\(outReason)|\(outGate ?? "")|\(basketKey ?? "")" }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, role, engineCode, engineVersion, outGate, outReason, outDetail
        case basketKey
    }

    init(tsCode: String = "", name: String = "", role: String? = nil,
         engineCode: String? = nil, engineVersion: String? = nil, outGate: String? = nil,
         outReason: String = "", outDetail: String? = nil, basketKey: String? = nil) {
        self.tsCode = tsCode; self.name = name; self.role = role
        self.engineCode = engineCode; self.engineVersion = engineVersion
        self.outGate = outGate; self.outReason = outReason; self.outDetail = outDetail
        self.basketKey = basketKey
    }

    // 🔴 手写 `init(from:)` + 全字段 `decodeIfPresent`(V2-⑮ 起的硬要求):合成
    // `Decodable` 对**非 Optional 属性「有默认值也不容忍缺键」** —— 老快照缺一个键
    // 就整份报告解不出来。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        role = try c.decodeIfPresent(String.self, forKey: .role)
        engineCode = try c.decodeIfPresent(String.self, forKey: .engineCode)
        engineVersion = try c.decodeIfPresent(String.self, forKey: .engineVersion)
        outGate = try c.decodeIfPresent(String.self, forKey: .outGate)
        outReason = try c.decodeIfPresent(String.self, forKey: .outReason) ?? ""
        outDetail = try c.decodeIfPresent(String.self, forKey: .outDetail)
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey)
    }

    var reasonLabel: String { nkDroppedReasonLabel(outReason) }
    var reasonTone: NKAxisTone { nkDroppedReasonTone(outReason) }
    var reasonHeadline: String {
        reasonLabel.components(separatedBy: " · ").first ?? reasonLabel
    }
    /// 「卡在哪一关」的人读名;nil = 非关口原因(⛔ 不写「无」)。
    var gateLabel: String? { outGate.map(nkGateLabel) }
    /// 展示串:有名字就「名称(代码)」,没有就裸代码。
    var display: String { name.isEmpty ? tsCode : "\(name)(\(tsCode))" }
    /// 引擎三件套的一行展示;nil = 没登记(⛔ 不写「无」)。
    var engineLabel: String? {
        let bits = [engineCode, engineVersion].compactMap { $0 }.filter { !$0.isEmpty }
        return bits.isEmpty ? nil : bits.joined(separator: " · ")
    }
}

/// ③b 原因码的展示层换算(**⛔ 不许合并**,每个码指向不同的结论)。
/// 🔴 **唯一源是服务端 `report/basket_daily.py::DROPPED_REASON_LABEL`**;这里是
/// 中文短句镜像(界面要一行放得下,服务端那份带括号补充给 markdown 报告用)。
/// ⚠ **码字符串一经落库不改**(⑨ 按原因码归因,改码 = 历史归因断线)——
/// 新增码只加 case,⛔ 不改既有码的字面。未识别码原样透传,不静默瞎翻译。
func nkDroppedReasonLabel(_ raw: String) -> String {
    switch raw {
    case "capacity_overflow": return "档位已满 · 今天机会多到装不下"
    // ⚠ V2.2 门槛制**之前**的历史码:老快照回放仍会出现,⛔ 别当非法值。
    case "below_quality_line": return "未过质量线 · 今天没什么好货(V2.2 前的历史码)"
    // —— V2.2-③ 六道关口新增七码(服务端已登记;⛔ 一个都不许并成「未入选」)——
    case "evidence_degraded_out": return "证据关降级出局 · 逻辑没被证据撑住"
    case "mech_gate_rejected": return "机械关硬否决 · 市场关 / 板块关不过"
    // 🔴 裁定 #11:位置关判定交 LLM,`unfit` **不是硬否决** —— 票就在这张表里、
    // 写明是哪只成员与模型的理由。
    case "position_unfit": return "位置关判定不合适 · 落地起跳位置不对(非硬否决)"
    // 🔴 裁定 #12:核心关同款交 LLM。「不是龙头」≠「这票不行」。
    case "core_unfit": return "核心关判定不是龙头 · 同行业里不占核心地位(非硬否决)"
    // 🔴 V2.3.2-①:市场关 / 板块关里**未经用户确认的阈值**已退出机械硬否决、降为
    // 证据输入,判定交 LLM。⚠ 与上面的 `mech_gate_rejected` **别读成一回事**:
    // 那个是「客观量没过一道确认过的硬门」,这两个是「模型看了读数觉得环境不适配」。
    case "market_unfit": return "市场关判定不适配 · 大盘环境与该引擎不合(非硬否决)"
    case "sector_unfit": return "板块关判定不适配 · 板块状态撑不住这个篮子(非硬否决)"
    // 🔴 V2.4.0 P1.4 新增的**成员级** OUT 码:只摘掉这一只、**篮子还在**(K8 §八)。
    // ⚠ 与上面两条**篮子级**的 `core_unfit` / `position_unfit` 别读成一回事:
    // 那两条是「整篮走了、成员被连带列出」,这两条是「只有这一只被摘掉」。
    case "member_core_unfit": return "核心关判定该成员不适合 · 只摘掉这一只,篮子仍在"
    case "member_position_unfit": return "位置关判定位置不合适 · 只摘掉这一只,篮子仍在"
    case "members_all_removed": return "成员级关口判定后成员全部出篮 · 篮子因此整体 OUT"
    // 🔴 下面两码是**系统缺席**,不是市场结论 —— ⛔ 别读成「今天没好票」。
    case "no_active_engine": return "无运行中的引擎线 · 系统缺席(不是市场结论)"
    case "engine_unresolved": return "引擎归属解析失败 · 无引擎可容纳"
    default: return raw
    }
}

/// ③b 原因码的着色。**三类分开**:关口过了只是位置满(good)/ 市场或证据判它不行
/// (warn)/ **系统自己缺席**(bad —— 那不是市场结论,是我们没跑起来,该最刺眼)。
func nkDroppedReasonTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "capacity_overflow": return .good
    case "no_active_engine", "engine_unresolved": return .bad
    default: return .warn
    }
}

// MARK: - V2.2-③ 六道关口(展示层:码 → 中文 + 机械关 / 证据关二分)
//
// 🔴 **③-A 二分是产品语义,不是配色偏好**(用户裁定 #6 + #11 + #12):
//   · **机械关**(市场 / 板块)读的是已预计算的**客观量** → **硬否决**,过不去就没了;
//   · **证据关**(驱动 / 核心 / 位置 / 证据)由 LLM 组织证据 → **只降级**
//     (T1→T2→退出正式候选,**仍在报告 ③b 列名**)。
// ⛔ 界面上两者必须能分辨:后果完全不同,混成一种灯就是把「否决」与「扣分」讲成一回事。
// 唯一源 = 服务端 `selection/gates.py`(`MECH_GATES` / `EVIDENCE_GATES` / `GATE_LABELS`)。

enum NKGateKind: Equatable {
    /// 机械关:硬否决(市场 / 板块)。
    case mechanical
    /// 证据关:只降级、不除名(驱动 / 核心 / 位置 / 证据)。
    case evidence
    /// 未识别的关口码(新服务端加了关而这版客户端还不认识)——⛔ 不猜成任何一类。
    case unknown

    var label: String {
        switch self {
        case .mechanical: return "机械关 · 硬否决"
        case .evidence:   return "证据关 · 只降级"
        case .unknown:    return "未知关口"
        }
    }
}

/// 「卡在 X 关」后面那句括注的**正确**写法(V2.3.2-① 之后必须走这里,⛔ 不许直接
/// 印 `nkGateKind(gate).label`)。
///
/// 🔴 **只看关别会说反**:V2.3.2-① 起市场关 / 板块关是**半机械半证据**的 —— 同一道关
/// 既可能因 `source=audited` 的硬门 reject(原因码 `mech_gate_rejected`),也可能因
/// LLM 三值判 `unfit`(原因码 `market_unfit` / `sector_unfit`)。后者印成
/// 「机械关 · 硬否决」是**把"只降级"讲成了"硬除名"**,正好说反 —— 实拍逮到过。
///
/// ⛔ **修的是这句文案,不是 `nkGateKind` 的关级二分** —— 那个二分**不能动**:
/// 把 market/sector 挪进证据关会反过来把四项 audited 的硬否决讲成只降级
/// (服务端 `MECH_GATES`/`EVIDENCE_GATES` 是关级的,守门单测两侧对拍)。
func nkGateEnforcementNote(gate: String?, reason: String) -> String? {
    guard let g = gate, !g.isEmpty else { return nil }
    if reason == "market_unfit" || reason == "sector_unfit" {
        return "按证据判 · 只降级"
    }
    return nkGateKind(g).label
}

/// 六关码的固定展示顺序(= 服务端 `GATE_ORDER`)。⛔ 别按字典序排 —— 那会把
/// 「市场 → 驱动 → 板块 → 核心 → 位置 → 证据」这条管线顺序打乱。
let nkGateOrder: [String] = ["market", "driver", "sector", "core", "position", "evidence"]

func nkGateLabel(_ raw: String) -> String {
    switch raw {
    case "market": return "市场关"
    case "driver": return "驱动关"
    case "sector": return "板块关"
    case "core": return "核心关"
    case "position": return "位置关"
    case "evidence": return "证据关"
    default: return raw
    }
}

func nkGateKind(_ raw: String) -> NKGateKind {
    switch raw {
    case "market", "sector": return .mechanical
    case "driver", "core", "position", "evidence": return .evidence
    default: return .unknown
    }
}

/// 关口判定三值的展示层换算(服务端 `VERDICT_PASS/DEGRADE/REJECT`)。
func nkGateVerdictLabel(_ raw: String) -> String {
    switch raw {
    case "pass": return "过"
    case "degrade": return "降级"
    case "reject": return "否决"
    default: return raw
    }
}

func nkGateVerdictTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "pass": return .good
    case "degrade": return .warn
    case "reject": return .bad
    default: return .neutral
    }
}

/// 一篮的六关灯条读数(**纯展示投影**,从已冻结的 `card.tierBreakdown["gates"]` 读出)。
///
/// 🔴 **数据来源刻意是冻结卡而不是现连**:这一节由 `selection/tier.py::_gate_breakdown`
/// 在定档当时写进 `mech_breakdown_json.gates`,并随篮子卡冻住 —— 回看三天前的报告
/// 该看到**当时**判的六关,不是今天重判一遍的结果。⛔ 别改成调 `gate_evaluations`。
///
/// **`available == false` 的三种成因经本类型收口后不可区分**(老卡没有这一节 /
/// 这一篮没有关口汇总 / 汇总读不出)—— 三者给用户的动作相同(等下一份报告),
/// 但 ⛔ **绝不许渲染成「六关都过了」**:那是把「没看」讲成「没有问题」。
struct BasketGates: Equatable {
    var available: Bool = false
    /// `gate 码 → verdict 码`(每关取**最严**的那一条,服务端已归并)。
    var verdicts: [String: String] = [:]
    /// 🔴 **V2.4.0 P1.5+(`basket_card_v5`)新增**:`gate 码 → 这一关判得出来吗`。
    /// `false` = **判不出**(数据缺失 / 模型漏答)—— 服务端把它记成 `pass + available=false`,
    /// 老形状(v4 及更早)里它长得**跟「过」一模一样**,格子上是把「没看」讲成了「没问题」。
    /// ⚠ 老卡没有这个键 → 该关取 `nil` = **不知道判没判得出**,按老形状只画 verdict
    /// (⛔ 不许猜成 `true`:那等于替老卡编一个"判得出"的结论)。
    var gateAvailable: [String: Bool] = [:]
    var engineCode: String? = nil
    var engineVersion: String? = nil
    /// 证据关累计降了几档(服务端 `evidence_degrades`)。
    var evidenceDegrades: Int? = nil
    /// 哪些证据关判了降级。
    var degradedGates: [String] = []
    /// **该篮不得进 T1**(⚠ 与「被否决」不是一回事:多半是某一关**判不出**)。
    var blocksT1: Bool = false
    /// 位置关 / 核心关**有成员被移除**(V2.4.0 P1.4:成员级 OUT —— 只摘掉那一只,
    /// 篮子仍在;⛔ 不再是"整篮退出正式候选")。
    var positionUnfit: Bool = false
    var coreUnfit: Bool = false

    /// 从 `card.tierBreakdown` 里读那一节。⛔ 不重算任何判据,只做投影。
    init(tierBreakdown: NKJSON?) {
        guard let node = tierBreakdown?["gates"], let obj = node.objectValue else { return }
        available = obj["available"]?.boolValue ?? false
        guard available else { return }
        engineCode = obj["engine_code"]?.stringValue
        engineVersion = obj["engine_version"]?.stringValue
        evidenceDegrades = obj["evidence_degrades"]?.intValue
        degradedGates = (obj["degraded_gates"]?.arrayValue ?? []).compactMap(\.stringValue)
        blocksT1 = obj["blocks_t1"]?.boolValue ?? false
        positionUnfit = obj["position_unfit"]?.boolValue ?? false
        coreUnfit = obj["core_unfit"]?.boolValue ?? false
        for (k, v) in (obj["verdicts"]?.objectValue ?? [:]) {
            if let s = v.stringValue { verdicts[k] = s }
        }
        for (k, v) in (obj["gate_available"]?.objectValue ?? [:]) {
            if let b = v.boolValue { gateAvailable[k] = b }
        }
    }

    /// 灯条一格。`verdict == nil` = **这一关这份快照里没有记录**,⛔ 不是「过了」;
    /// `available == false` = **这一关判不出**(V2.4.0 起服务端逐关下发)——
    /// 🔴 它在服务端的 verdict 也是 `pass`,⛔ 绝不许照着 verdict 画成「过」。
    struct Light: Identifiable, Equatable {
        let gate: String
        let verdict: String?
        let available: Bool?
        var id: String { gate }
        var label: String { nkGateLabel(gate) }
        var kind: NKGateKind { nkGateKind(gate) }
        /// 「判不出」优先于 verdict:那一格的 `pass` 只是"没拦",不是"过了"。
        var isUnknown: Bool { available == false }
        var verdictLabel: String {
            if isUnknown { return "判不出" }
            return verdict.map(nkGateVerdictLabel) ?? "未记录"
        }
        var tone: NKAxisTone {
            if isUnknown { return .neutral }
            return verdict.map(nkGateVerdictTone) ?? .neutral
        }
    }

    /// 六格,**恒定六格、恒定顺序**(缺记录的那格如实标「未记录」,⛔ 不隐藏 ——
    /// 隐藏会让「这一关没判」看起来像「这一关不存在」)。
    var lights: [Light] {
        nkGateOrder.map { Light(gate: $0, verdict: verdicts[$0], available: gateAvailable[$0]) }
    }

    /// 「卡在哪一关」= 按管线顺序第一道**非 pass 或判不出**的关;全过 → nil。
    /// ⚠ V2.4.0:判不出的关也算"卡住" —— 它挡 T1,把它当"过了"会让这一行说谎。
    var blockedGate: String? {
        nkGateOrder.first { (verdicts[$0] ?? "pass") != "pass" || gateAvailable[$0] == false }
    }
}

/// ⑧ 的「当前状态」三路读法(**A 类**)。三个位分别回答不同问题,⛔ 不许合并:
/// `state` 四态 / `provisional`(盘中暂态、未收盘定论)/ `notEvaluated`(**今天还没判过**,
/// 不是「判了是 unclear」)。
///
/// ⚠ **篮子 `falsified` ≠ 持仓该走**(CLAUDE.md 坑条):它说的是「这个驱动假设不成立了」,
/// **不指向任何持仓动作**、不进推送。展示处不得写成卖出暗示。
struct BasketVerification: Codable, Equatable {
    var basketId: Int = 0
    var tradeDate: String = ""
    var state: String = ""      // verified | partial | unclear | falsified
    var label: String = ""
    var source: String? = nil
    var observedAt: String? = nil
    var provisional: Bool = false
    var notEvaluated: Bool = false
    var evidence: NKJSON? = nil
    var rows: [NKJSON] = []

    enum CodingKeys: String, CodingKey {
        case basketId, tradeDate, state, label, source, observedAt
        case provisional, notEvaluated, evidence, rows
    }

    init(basketId: Int = 0, tradeDate: String = "", state: String = "", label: String = "",
         source: String? = nil, observedAt: String? = nil, provisional: Bool = false,
         notEvaluated: Bool = false, evidence: NKJSON? = nil, rows: [NKJSON] = []) {
        self.basketId = basketId; self.tradeDate = tradeDate; self.state = state
        self.label = label; self.source = source; self.observedAt = observedAt
        self.provisional = provisional; self.notEvaluated = notEvaluated
        self.evidence = evidence; self.rows = rows
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source)
        observedAt = try c.decodeIfPresent(String.self, forKey: .observedAt)
        provisional = try c.decodeIfPresent(Bool.self, forKey: .provisional) ?? false
        notEvaluated = try c.decodeIfPresent(Bool.self, forKey: .notEvaluated) ?? false
        evidence = try c.decodeIfPresent(NKJSON.self, forKey: .evidence)
        rows = try c.decodeIfPresent([NKJSON].self, forKey: .rows) ?? []
    }

    /// 角标文案。**「今天还没判过」与「判了是 unclear」讲不同的话**。
    var badgeText: String {
        if notEvaluated { return "今日尚未判定" }
        let base = label.isEmpty ? nkVerificationStateLabel(state) : label
        return provisional ? "\(base) · 盘中暂态" : base
    }

    var badgeTone: NKAxisTone {
        if notEvaluated { return .neutral }
        switch state {
        case "verified": return .good
        case "partial": return .warn
        case "falsified": return .bad
        default: return .neutral
        }
    }
}

/// 验证四态的展示层换算(服务端 `label` 优先;这里只做兜底,未识别原样透传)。
func nkVerificationStateLabel(_ raw: String) -> String {
    switch raw {
    case "verified": return "已验证"
    case "partial": return "部分验证"
    case "unclear": return "未明"
    case "falsified": return "驱动假设已证伪"
    default: return raw.isEmpty ? "未判定" : raw
    }
}

/// ⑨ 的一篮盘后复盘(`mech` 是 **B 类冻结快照**,`llmText` 是参考件)。
/// `depth`:`full`(T1/T2 详复盘)| `brief`(T3 简评)。
/// `llmText == nil` + `llmSkipReason` 非空 = **未生成**(预算耗尽 / 降级),
/// ⛔ 不拿空串冒充「生成了但没内容」。
struct BasketReview: Codable, Equatable, Identifiable {
    var basketId: Int = 0
    var basketKey: String = ""
    var name: String = ""
    var tier: Int? = nil
    var d0: String = ""
    var reviewDate: String = ""
    var depth: String = ""
    var mech: NKJSON = .object([:])
    var llmText: String? = nil
    var llmSkipReason: String? = nil
    var degraded: Bool = false
    var verification: NKJSON? = nil

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, basketKey, name, tier, d0, reviewDate, depth
        case mech, llmText, llmSkipReason, degraded, verification
    }

    init(basketId: Int = 0, basketKey: String = "", name: String = "", tier: Int? = nil,
         d0: String = "", reviewDate: String = "", depth: String = "",
         mech: NKJSON = .object([:]), llmText: String? = nil, llmSkipReason: String? = nil,
         degraded: Bool = false, verification: NKJSON? = nil) {
        self.basketId = basketId; self.basketKey = basketKey; self.name = name; self.tier = tier
        self.d0 = d0; self.reviewDate = reviewDate; self.depth = depth; self.mech = mech
        self.llmText = llmText; self.llmSkipReason = llmSkipReason; self.degraded = degraded
        self.verification = verification
    }

    /// **B 类冻结快照**(`mech` ← `basket_review_daily.mech_json`)→ 全字段 `decodeIfPresent`。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        d0 = try c.decodeIfPresent(String.self, forKey: .d0) ?? ""
        reviewDate = try c.decodeIfPresent(String.self, forKey: .reviewDate) ?? ""
        depth = try c.decodeIfPresent(String.self, forKey: .depth) ?? ""
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
        llmText = try c.decodeIfPresent(String.self, forKey: .llmText)
        llmSkipReason = try c.decodeIfPresent(String.self, forKey: .llmSkipReason)
        degraded = try c.decodeIfPresent(Bool.self, forKey: .degraded) ?? false
        verification = try c.decodeIfPresent(NKJSON.self, forKey: .verification)
    }

    var depthLabel: String {
        switch depth {
        case "full": return "详复盘"
        case "brief": return "简评"
        default: return depth
        }
    }
}

/// 报告里的篮子日报三段(③ 今日篮子 / ③b 未定档 / ④ 昨日复盘)。
///
/// **每段各自带 `*Available` + `*UnavailableReason`**,两种「空」在界面上**必须讲不同的话**:
///  · 空数组 + `available == true` = **今天真没有**(合法输出);
///  · `available == false` = **本次没取到**。
struct BasketDaily: Codable, Equatable {
    var tradeDate: String = ""
    var baskets: [Basket] = []
    var basketsAvailable: Bool = false
    var basketsUnavailableReason: String? = nil
    var droppedBaskets: [DroppedBasket] = []
    var droppedBasketsAvailable: Bool = false
    var droppedBasketsUnavailableReason: String? = nil
    /// V2.3.2-②-B:③b 的第二类行(**股票级 OUT**)。三件套照 `droppedBaskets*` 体例
    /// —— 空数组**只有在 `available == true` 时**才等于"今天没有 OUT"。
    var outCandidates: [OutCandidate] = []
    var outCandidatesAvailable: Bool = false
    var outCandidatesUnavailableReason: String? = nil
    var reviews: [BasketReview] = []
    var reviewsAvailable: Bool = false
    var reviewsUnavailableReason: String? = nil
    var reviewD0: String? = nil
    var packVersion: String? = nil
    /// 🔴 V2.4.0 P2.5:「正式空结果」与「系统缺席」严格分开(K8 §十)。
    /// · `basketsAvailable == true` + 空数组 = **今天没有形成正式篮子**(合法输出);
    /// · `basketsAvailable == false` + 这四位 = **选股解释未完成** ——
    ///   ⛔ 界面绝不许把它说成「今天没有机会」。
    /// 🔴 `unexplainedSeed*` **不是第四种候选状态**:⛔ 不当 T2 / OUT 画、⛔ 不给它
    /// 任何"可以买"的语气 —— 它只说明"机械层当时看到了这些方向,但没人解释过"。
    var selectionStage: String? = nil
    var selectionUnavailableReason: String? = nil
    var unexplainedSeedCount: Int? = nil
    var unexplainedSeedSummary: String? = nil
    var notes: [String] = []

    enum CodingKeys: String, CodingKey {
        case tradeDate, baskets, basketsAvailable, basketsUnavailableReason
        case droppedBaskets, droppedBasketsAvailable, droppedBasketsUnavailableReason
        case outCandidates, outCandidatesAvailable, outCandidatesUnavailableReason
        case reviews, reviewsAvailable, reviewsUnavailableReason
        case reviewD0, packVersion, notes
        case selectionStage, selectionUnavailableReason
        case unexplainedSeedCount, unexplainedSeedSummary
    }

    init(tradeDate: String = "", baskets: [Basket] = [], basketsAvailable: Bool = false,
         basketsUnavailableReason: String? = nil, droppedBaskets: [DroppedBasket] = [],
         droppedBasketsAvailable: Bool = false, droppedBasketsUnavailableReason: String? = nil,
         outCandidates: [OutCandidate] = [], outCandidatesAvailable: Bool = false,
         outCandidatesUnavailableReason: String? = nil,
         reviews: [BasketReview] = [], reviewsAvailable: Bool = false,
         reviewsUnavailableReason: String? = nil, reviewD0: String? = nil,
         packVersion: String? = nil, notes: [String] = []) {
        self.tradeDate = tradeDate; self.baskets = baskets
        self.basketsAvailable = basketsAvailable
        self.basketsUnavailableReason = basketsUnavailableReason
        self.droppedBaskets = droppedBaskets
        self.droppedBasketsAvailable = droppedBasketsAvailable
        self.droppedBasketsUnavailableReason = droppedBasketsUnavailableReason
        self.outCandidates = outCandidates
        self.outCandidatesAvailable = outCandidatesAvailable
        self.outCandidatesUnavailableReason = outCandidatesUnavailableReason
        self.reviews = reviews; self.reviewsAvailable = reviewsAvailable
        self.reviewsUnavailableReason = reviewsUnavailableReason
        self.reviewD0 = reviewD0; self.packVersion = packVersion; self.notes = notes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        baskets = try c.decodeIfPresent([Basket].self, forKey: .baskets) ?? []
        basketsAvailable = try c.decodeIfPresent(Bool.self, forKey: .basketsAvailable) ?? false
        basketsUnavailableReason = try c.decodeIfPresent(String.self,
                                                         forKey: .basketsUnavailableReason)
        droppedBaskets = try c.decodeIfPresent([DroppedBasket].self, forKey: .droppedBaskets) ?? []
        droppedBasketsAvailable = try c.decodeIfPresent(Bool.self,
                                                        forKey: .droppedBasketsAvailable) ?? false
        droppedBasketsUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .droppedBasketsUnavailableReason)
        outCandidates = try c.decodeIfPresent([OutCandidate].self, forKey: .outCandidates) ?? []
        outCandidatesAvailable = try c.decodeIfPresent(
            Bool.self, forKey: .outCandidatesAvailable) ?? false
        outCandidatesUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .outCandidatesUnavailableReason)
        reviews = try c.decodeIfPresent([BasketReview].self, forKey: .reviews) ?? []
        reviewsAvailable = try c.decodeIfPresent(Bool.self, forKey: .reviewsAvailable) ?? false
        reviewsUnavailableReason = try c.decodeIfPresent(String.self,
                                                         forKey: .reviewsUnavailableReason)
        reviewD0 = try c.decodeIfPresent(String.self, forKey: .reviewD0)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        selectionStage = try c.decodeIfPresent(String.self, forKey: .selectionStage)
        selectionUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .selectionUnavailableReason)
        unexplainedSeedCount = try c.decodeIfPresent(Int.self, forKey: .unexplainedSeedCount)
        unexplainedSeedSummary = try c.decodeIfPresent(
            String.self, forKey: .unexplainedSeedSummary)
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
    }

    /// 🔴 **选股解释未完成**(V2.4.0 P2.5,K8 §十「系统缺席」)—— 界面据此把 ③ 节
    /// 画成「这一段没有跑成」,⛔ 绝不许画成「今天没有机会」。
    /// 判据 = 服务端已经判好的 `selectionUnavailableReason` 非空,⛔ 客户端不再推一遍。
    var selectionUnexplained: Bool {
        (selectionUnavailableReason?.isEmpty == false) && !basketsAvailable
    }

    /// 系统缺席时展示层的那一句原因。
    /// ⚠ **⛔ 不复读服务端那句散文**:标题已经写着「选股解释未完成」,再把整句原文
    /// 塞进副标题会印成「选股解释未完成 / 原因:选股解释未完成(原因:no_provider)…」
    /// (实拍逮到)。这里改用**结构化的原因码**重述一遍,信息一个字没少。
    /// ⚠ 原因码可能带后缀(`call_failed:<原因>`)→ **未识别值原样透传**。
    var selectionUnavailableDetail: String? {
        guard selectionUnexplained, let code = selectionUnavailableReason, !code.isEmpty else {
            return nil
        }
        return "原因码 \(code) —— 本次没有生成正式 T1/T2 与 OUT;"
            + "这不是「今天没有机会」,是这一段没有跑成。"
    }

    /// 系统缺席时那一句「机械层当时看到了什么」。**没记就返回 `nil`**(⛔ 不拿 0 冒充)。
    var unexplainedSeedText: String? {
        guard selectionUnexplained, let n = unexplainedSeedCount else { return nil }
        let tail = (unexplainedSeedSummary?.isEmpty == false) ? "(\(unexplainedSeedSummary!))" : ""
        return "机械层当时看到 \(n) 个候选方向\(tail);它们没有被解释过,"
            + "⛔ 不是候选、不构成任何买入依据。"
    }

    /// 某一档的篮子。**空档位如实显示「今日 T1 为空」,⛔ 不隐藏**(E1)。
    /// `tier == nil`(极旧快照 / 数据缺口)的篮子**不进任何档**,⛔ 别拿假档位塞进去。
    func baskets(tier: Int) -> [Basket] { baskets.filter { $0.tier == tier } }

    /// **现役档位**(V2.1-② 起引擎两档化;写侧收紧的单一源在服务端
    /// `selection/tier.py::TIERS`,客户端这份是展示层的镜像)。
    static let liveTiers: [Int] = [1, 2]

    /// ③ 节要渲染的档位 = **现役两档 ∪ 本份快照实际出现的档位**(V2.1-② 移交 ⑦ 的硬约束,
    /// 与服务端 `render._render_today_baskets` 同构)。
    ///
    /// 🔴 **⛔ 不许写死 `[1, 2]`**:`basketDaily` 是**冻结快照**,回放一份 V2 时代的老报告
    /// 时里面就有 tier=3 的篮子 —— 写死两档会让它们在客户端**静默消失**(那是"把历史删了",
    /// 不是"退役新档"),等于把服务端刚立起来的读侧宽容在展示层拆掉。
    /// 🔴 **⛔ 也不许写死 `[1, 2, 3]`**:新报告会凭空多出一个恒空的 T3 分组并说
    /// 「今日 T3 为空(算过了,今天没有达到该档标准的篮子)」—— 而真相是 T3 已取消,
    /// **那正是把系统缺席讲成实质性市场结论**(§2 诚实披露红线)。
    /// 并集两头都对:现役档保证「今日 T2 为空」这句诚实披露不消失,实际档保证老报告照出。
    var displayTiers: [Int] {
        Array(Set(Self.liveTiers).union(baskets.compactMap { $0.tier })).sorted()
    }
}
