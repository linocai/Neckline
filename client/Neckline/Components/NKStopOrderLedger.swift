//
//  NKStopOrderLedger.swift
//  Neckline — 「已在券商挂 -5% 条件单」的**本机**记录(规范 §08 第 1 条)
//
//  🔴 **⛔ 绝不写服务端**:这一格记的是「**我在券商那边做了什么**」,不是系统能核实
//  的事实。把它写进服务端 = 让台账里出现一条**系统无法验证、也无法反驳**的记录 ——
//  周复盘对账时它会被当成事实参与归因,而它只是用户随手点的一个勾。
//
//  🔴 **它也不是纪律状态位**:勾没勾**不改变任何判定**(止损线、距止损、时间退出各自
//  照旧跑),⛔ 不据此灰化任何按钮、不据此改推送。它只是个便签。
//
//  ⚠ **V2.3 之前这是 `@State private var checkedLocally`,刷新即丢** —— 用户当面提的
//  第一个反人类点(盘中刷一次行情,早上勾的全没了)。现在按 `positionId` 落
//  `UserDefaults`,并**记下勾选时刻**:一个没有时间的勾选说明不了任何事。
//
//  ⚠ **本机记录、换机不同步** —— 这句话必须在界面上说出口(⛔ 不许让用户以为它跟着账号走)。
//

import Foundation

enum NKStopOrderLedger {
    /// `positionId`(字符串化)→ 勾选时刻(`timeIntervalSince1970`)。
    /// 未勾选 = **键不存在**(⛔ 不存 `false`:那样分不清"取消勾选"与"从没见过这笔仓")。
    private static let key = "nk.stopOrderChecked.v1"

    private static func load() -> [String: Double] {
        UserDefaults.standard.dictionary(forKey: key) as? [String: Double] ?? [:]
    }

    /// 勾选时刻;`nil` = 没勾。
    static func checkedAt(positionId: Int) -> Date? {
        guard let t = load()["\(positionId)"] else { return nil }
        return Date(timeIntervalSince1970: t)
    }

    static func isChecked(positionId: Int) -> Bool { checkedAt(positionId: positionId) != nil }

    static func setChecked(_ on: Bool, positionId: Int, at date: Date = Date()) {
        var d = load()
        if on {
            d["\(positionId)"] = date.timeIntervalSince1970
        } else {
            d.removeValue(forKey: "\(positionId)")
        }
        UserDefaults.standard.set(d, forKey: key)
    }

    /// 清仓后的清理钩子。⚠ **不自动跑** —— 平仓的仓位不再出现在列表里,残留的键
    /// 既不占地方也不会被读到;真要清由调用方显式调用(留着这个入口是为了让
    /// 「为什么不清理」这件事有个明确答案,而不是被当成漏写)。
    static func forget(positionId: Int) { setChecked(false, positionId: positionId) }

    /// 展示用:`8月10日 09:31 勾选`。
    static func checkedLabel(positionId: Int) -> String? {
        guard let d = checkedAt(positionId: positionId) else { return nil }
        return formatter.string(from: d) + " 勾选"
    }

    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "M月d日 HH:mm"
        return f
    }()
}
