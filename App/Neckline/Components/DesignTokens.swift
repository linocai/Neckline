//
//  DesignTokens.swift
//  Neckline — A 股短线量化决策系统
//
//  设计令牌搬自 LinoN(整块复用,§五 阶段4C)。命名空间由 LN 改 NK 避免与 LinoN
//  代码库混淆(两者是完全独立的 App,无编译单元共享)。
//  涨跌色:绿涨红跌(国际惯例 / 用户明确选择,§3.5,勿改回 A 股本地红涨绿跌)。
//
//  ⚠ **V2.3 前端视觉升级(2026-08-10)**:字阶收成**八档 + 两档数字档**、间距圆角
//  分 macOS / iOS 两档、macOS 画布底色改深一档。落地口径 = `archive/Neckline视觉升级/
//  Neckline 视觉规范.dc.html` §02–§04(⚠ 该原型目录 2026-08-12 已由根目录移进 `archive/`)。
//  🔴 **色令牌一个没加、一个没改** —— 本次视觉升级动的是**字阶 / 间距 / 圆角 /
//  组件**,配色沿用原样;唯一变化是品牌标识改用既有的 `NK.alertGrad`(不是新色)。
//

import SwiftUI

// MARK: - Colors

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8)  & 0xff) / 255,
            blue:  Double( hex        & 0xff) / 255,
            opacity: alpha
        )
    }
}

enum NK {
    // 语义色
    static let up      = Color(hex: 0x0FA968)   // 涨 / 满额 / 盈利 / 过
    static let down    = Color(hex: 0xE5443B)   // 跌 / 破线 / 退潮刹车 / 休息 / 否决
    static let amber   = Color(hex: 0xE8910A)   // 半额 / 警示 / 待办 / 降级
    static let accent  = Color(hex: 0x0B6BCB)   // 交互蓝 / 主按钮 / 选中

    // 文本
    static let textPrimary   = Color(hex: 0x1D1D1F)
    static let textSecondary = Color(hex: 0x3C3C43, alpha: 0.55)   // rgba(60,60,67,.55)
    static let textTertiary  = Color(hex: 0x3C3C43, alpha: 0.40)
    static let hairline      = Color(hex: 0x3C3C43, alpha: 0.10)   // 分隔线 / 卡边

    // 背景
    static let cardBg     = Color.white
    /// **macOS 画布**。⚠ V2.3 由 `#FBFBFD` 改深一档 `#F6F6F8`:原值与白卡几乎无差,
    /// 卡片浮不起来(规范 §02「两处要注意」)。浮起靠 **白卡 + hairline 描边**,
    /// ⛔ **不加阴影** —— 阴影不参与动画是全局三禁之一,而且桌面密度下也不需要。
    ///
    /// ⚠ **这是三栏布局里「详情栏」那一栏的底色**(macOS 原型 247 行),
    /// 列表栏另有 `listBg`(下)—— ⛔ 别把两栏又调成同一个值。
    static let pageBg     = Color(hex: 0xF6F6F8)
    /// **macOS 列表栏底色**(V2.3.1 新增,全版**唯一**新增色令牌)。
    ///
    /// 🔴 **加它的理由**:原型两栏底色是**分开的** —— 列表栏 `#FCFCFD`(macOS 原型 81 行)
    /// 比详情栏 `#F6F6F8`(247 行)亮一档,靠这一档明暗差 + `.5px` 竖分隔把「选什么」
    /// 与「看什么」分成两个面。V2.3.0 两栏都吃 `pageBg`,**糊成一片**,列表栏里的白色
    /// 选中行反而比底色还暗一点点,选中态几乎看不出来。
    /// ⚠ V2.3 立的「色令牌一个没加、一个没改」在 V2.3.1 §② 收窄为:**只为这一处加一枚**,
    /// ⛔ 其余色差(卡描边 `.12` vs 令牌 `.10` 等)一律就近对齐既有令牌,不再新增。
    static let listBg     = Color(hex: 0xFCFCFD)
    /// iOS 画布**沿用不变**(手机上原本就分得开)。
    static let pageBgIOS  = Color(hex: 0xF3F4F7)
    static let fieldBg    = Color(hex: 0xF7F8FA)
    static let chipNeutral = Color(hex: 0x3C3C43, alpha: 0.05)
    /// 披露折叠展开区底色(规范 §05 `NKDisclosure`)。
    static let disclosureBg = Color(hex: 0xFAFAFB)

    /// **品牌渐变**(App 图标 / `NKLogo` / 字标)。
    ///
    /// ⚠ **V2.3 起 = `alertGrad` 同一条红橙**(用户选定)。**已知代价、用户拍板接受**:
    /// 它同时还是「退潮刹车 / 高危提醒」的既有语义色,故那两条红橙横幅的**独占性会弱
    /// 一点**。⛔ 别为了"消歧"把品牌色改回蓝绿 —— 那是被推翻的选择。
    static let brand = LinearGradient(
        colors: [Color(hex: 0xE5443B), Color(hex: 0xE8910A)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    /// 退潮刹车 / 高危提醒渐变(= 品牌色同一条,见上)。
    static let alertGrad = LinearGradient(
        colors: [Color(hex: 0xE5443B), Color(hex: 0xE8910A)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
}

// MARK: - Radius(规范 §04;macOS / iOS 分档 —— 桌面密度下 18 太圆)

enum NKRadius {
    /// 数据卡:**macOS 12 / iOS 18**。用 `NKRadius.card` 自动按平台取值。
    static let card: CGFloat = {
        #if os(macOS)
        return 12
        #else
        return 18
        #endif
    }()
    /// 卡内子块(嵌套在数据卡里的分组:关卡片 / 读数块 / 参考件块)。
    /// ⚠ **六关宫格的格子也是这一档**(原型 `border-radius:9px`,macOS 原型 291 行)——
    /// ⛔ 不是 `badge`(那是 4,§〇d 结转第 4 条点名过这处)。
    static let inner: CGFloat  = 9
    /// 按钮 / 折叠区。
    static let control: CGFloat = 8
    /// **成员卡**(`memCard()`,macOS 原型 1770 行 `border-radius:10px`)。
    /// ⚠ 比数据卡(12)小一档、比卡内子块(9)大一档 —— 它是"卡里的卡",刻意介于两者之间。
    static let memberCard: CGFloat = 10
    /// **「硬」角标**那一枚微型标记(macOS 原型 296 行 `border-radius:3px`)。
    /// ⚠ **⛔ 不是 `badge`(4)**:它只有 9px 字 + `padding:1px 4px`,4 的圆角在这个尺寸上
    /// 已经把方角吃掉一半(§〇d 结转第 4 条:批 0 把 `badge` 由 5 收到 4 时**没有**覆盖它)。
    static let hardTag: CGFloat = 3
    /// 方徽标(全 App 徽标 = `NKChip` / 六关格子 / Tier 角标)。
    /// ⚠ V2.3 起徽标**默认不再是胶囊**,胶囊只留给仍然语义为"标签"的那几处。
    /// ⚠ **V2.3.1 由 5 收到 4**:规范 §04 给的是区间「徽标(方)**4–5**」,而六份原型里
    /// 每一枚徽标的 inline style 都是 `border-radius:4px`(macOS 原型 253–258 / 367–376 …)
    /// —— 按 §五 〇a「对不上时以原型为准」取下沿。
    static let badge: CGFloat  = 4
    static let hero: CGFloat   = 20
    static let field: CGFloat  = 12
    static let glassBar: CGFloat = 26  // 底部玻璃标签栏(iOS,不变)
    static let sheet: CGFloat  = 28    // bottom sheet 顶圆角(不变)
    static let pill: CGFloat   = 999
}

// MARK: - Spacing(规范 §04;**分层密度** = 用户裁定「概览宽松、明细紧凑」)
//
// 🔴 **两档不是配色偏好,是信息层级**:列表栏 / 概览卡走**宽松档**(`pagePad` /
// `cardPad` / `cardGap`);成员读数、六关格子、键值行走**紧凑档**(`denseRow` 行高
// ≤ 22、`denseGap`)。⛔ 别为了"统一"把明细也放宽 —— 明细放宽 = 一屏看不完 = 又要滚。

enum NKSpace {
    /// 详情区页边距:macOS 22 / iOS 16(规范写 22 / 26 两档,取下沿常用值)。
    static let pagePad: CGFloat = {
        #if os(macOS)
        return 22
        #else
        return 16
        #endif
    }()
    /// 详情区页边距·宽档(macOS 大标题区 / 首屏概览)。
    static let pagePadWide: CGFloat = 26
    /// **macOS 详情栏底部留白**(原型每一屏都是 `padding:22px 26px 40px`,
    /// macOS 原型 250 / 645 / 709 / 828 行四处逐字相同)—— 最后一张卡不贴着窗口底沿。
    static let pagePadBottom: CGFloat = 40
    /// 列表栏页边距(macOS 三区布局的中栏)。
    /// **横 10 = 原型行容器 `padding:0 10px`**(macOS 原型 86 行);标题区另有
    /// 一档 16(`padding:18px 16px 10px`,82 行)—— **两套页边距是原型的原样**,
    /// 由标题区自己再补 6,⛔ 别把整栏统一成一个值。
    static let listPadH: CGFloat = 10
    static let listPadV: CGFloat = 16
    /// 列表栏标题区在 `listPadH` 之上补的那一档(10 + 6 = 16)。
    static let listHeaderExtraH: CGFloat = 6
    /// 列表栏上下:顶 18(原型 82 行)/ 底 20(原型 237 行末块 `padding:0 10px 20px`)。
    static let listPadTop: CGFloat = 18
    static let listPadBottom: CGFloat = 20
    /// 卡内边距·**纵向**:macOS 16 / iOS 18。
    /// ⚠ 横向另有一档 `cardPadH`(原型 `padding:16px 18px`,两个数**刻意不等**)。
    static let cardPad: CGFloat = {
        #if os(macOS)
        return 16
        #else
        return 18
        #endif
    }()
    /// 卡内边距·**横向** = 18(macOS 原型 264 行起每张数据卡都是 `padding:16px 18px`)。
    static let cardPadH: CGFloat = 18
    /// 卡与卡之间。
    static let cardGap: CGFloat = 16
    /// 卡内块与块之间。
    static let blockGap: CGFloat = 12
    /// 列表行之间(靠选中态分隔,不靠留白)。
    static let rowGap: CGFloat = 2
    /// 紧凑档:明细行之间。
    static let denseGap: CGFloat = 4
    /// 紧凑档行高上界(成员读数 / 六关格子 / 键值行)。
    static let denseRowHeight: CGFloat = 22

    /// ⚠ **旧名保留**(V2.3 前 455 处调用点的迁移期兼容;新代码用上面的语义名)。
    static let gap: CGFloat = 12
}

// MARK: - Typography(规范 §03:**八档 + 两档数字档**)
//
// 🔴 **视图里 ⛔ 不再写裸 `.system(size:)`** —— 一律走本枚举。字阶散着写就会一路
// 漂回十七个字号(V2.3 之前的实况:22 个字号档、455 个调用点)。
//
// 🔴 **9.5px 那一档整个删掉**:原来大量披露文案挂在 9.5,既看不清也压不住信息量。
// 折叠(`NKDisclosure`)之后它们统一走 `caption` 11 —— **折叠本身已经完成了降级,
// 不需要再靠缩字号**。⛔ 别为了塞下更多字把它加回来。
//
// 数字档(`heroNumber` / `metric`)已内建 `.monospacedDigit()`;其余档位里凡是要显示
// 数字的,调用点自己补 `.monospacedDigit()`(对应 HTML `tabular-nums`)。

enum NKFont {
    // —— 两档数字档 ——
    /// 32 / 600 / 数字。首屏那个最大的数(总分 74.2 / 现价 / 占总仓)。
    static let heroNumber = Font.system(size: 32, weight: .semibold).monospacedDigit()
    /// 20 / 600 / 数字。次级读数(百分比 38.2% / 敞口金额 / 五维贡献值)。
    static let metric     = Font.system(size: 20, weight: .semibold).monospacedDigit()

    // —— 八档文本档 ——
    /// 26 / 700。页面大标题(篮子名 / 个股名在详情栏顶部)。
    static let title1   = Font.system(size: 26, weight: .bold)
    /// 22 / 700。板块标题(「选股」「持仓」「复盘」)。
    static let title2   = Font.system(size: 22, weight: .bold)
    /// 17 / 600。段标题(「③ 今日篮子 2」「④ 成员 4」)。
    static let title3   = Font.system(size: 17, weight: .semibold)
    /// 15 / 600。卡标题 / 小节名(「情绪与市场语境」「纪律位置」)。
    static let headline = Font.system(size: 15, weight: .semibold)
    /// 13 / 400。正文(驱动叙述 / LLM 理由 / 说明句)。
    static let body     = Font.system(size: 13)
    /// 12 / 400。次要行(代码 · 买入价 × 数量 / 日期 / 副标题)。
    static let callout  = Font.system(size: 12)
    /// 11 / 400。披露文案 / 脚注 / 「参考、非指令」那一族。
    static let caption  = Font.system(size: 11)
    /// 10.5 / 700 / tracking +.5。全大写小标签(「六关判定」「EOD 硬数据」)。
    /// ⚠ tracking 不能烘进 `Font`,调用点须补 `.tracking(NKFont.labelTracking)`;
    /// 用 `.nkLabel()` 修饰符一次给全(见下)。
    static let label    = Font.system(size: 10.5, weight: .bold)
    static let labelTracking: CGFloat = 0.5
    /// 10.5 / 600 / 无 tracking。**徽标专用**(`NKChip`,全 App 唯一徽标实现)。
    /// ⚠ **不是第九档字阶** —— 字号与 `label` 是**同一档 10.5**(规范 §03 表里那一档),
    /// 只是字重降到 600、不加 tracking:原型每一枚徽标都是 `font-size:10.5px;
    /// font-weight:600`(macOS 原型 253–258 / 367–376),徽标是**贴在内容旁边的短标**,
    /// 700 + tracking 会把它抢成小标题。⛔ 别拿它当通用字阶用,徽标之外一律走八档。
    static let badge    = Font.system(size: 10.5, weight: .semibold)

    // —— 等宽档(审计视图 / 原始读数键名,**不算在八档里**:它答的是"这是机器标识符") ——
    /// 10 / 等宽。原始读数**键名**(`dist_to_ma20` 这类服务端语义标识符)。
    static let monoKey   = Font.system(size: 10).monospaced()
    /// 12 / 600 / 等宽数字。原始读数**数值**。
    static let monoValue = Font.system(size: 12, weight: .semibold).monospacedDigit()
}

extension View {
    /// `label` 档一次给全(字号 + 字重 + tracking)。
    func nkLabel() -> some View {
        self.font(NKFont.label).tracking(NKFont.labelTracking)
    }
}

// MARK: - Materials
// Liquid Glass 克制使用:仅栏/浮层/锁屏。数据卡用不透明 cardBg。
//   底部 TabBar / macOS 统一工具栏: .ultraThinMaterial + 描边 + 内高光
//   锁屏通知:                      .regularMaterial(深色壁纸上)


// MARK: - 固定文案(单一源)

/// 需要「全客户端只出现一次」的产品级固定文案。
///
/// 🔴 **V2.4.0 P0.2 —— 唯一保留的盘中小提示**。产品决定(审计规格 P0 定位逐字):
/// 用户自行观察盘中分时;系统保留 D0 冻结预案与必要的持仓纪律提示,**不再据普通
/// 盘中波动出「证伪」判决,不再据代理关注池出全局刹车**。
///
/// **展示规则(⛔ 一条都不许放宽)**:
///   · 落点 = 今日篮子页面 **D0 预案区域之下**(= T1/T2 篮子列表之后),**有且仅有一次**;
///     🔴 选「下方」不是随意 —— 放上方会把首屏第一个 T1/T2 篮子挤下去。
///   · 形态 = 普通辅助文字(`NKFont.caption` + `NK.textTertiary`):⛔ 无红黄底色、
///     ⛔ 无警告图标、⛔ 无强提醒卡、⛔ 无事件数量、⛔ 无「运行正常」、⛔ 无倒计时、
///     ⛔ 不可点击、⛔ 不展开审计字段、**⛔ 不依赖实时行情、不新增任何网络请求或轮询**。
///   · ⛔ **不得扩成七种变形中的任何一种**:盘中风险分数 / 正常-观察-恶化-恢复状态机 /
///     红黄绿交通灯 / 关注池涨跌统计 / VWAP-量比-低开事件摘要 / 另一种名称的「大盘退潮」/
///     另一种名称的「建议暂停开仓」。
///
/// ⚠ 它是**产品边界说明**,不是免责话术,也不暗示系统仍在后台判定。
/// ⚠ 守门单测按**字面量全客户端出现次数恰为 1** 锁死;⛔ 别复制第二份、⛔ 别拼接
/// (`Text("a" + "b")` 拼出来是 `String` 而非字面量,见 CLAUDE.md 那条 Markdown 坑)。
enum NKCopy {
    static let intradaySelfObserve =
        "盘中请自行结合分时判断;系统保留 D0 预案,不作盘中证伪或全局刹车。"
}
