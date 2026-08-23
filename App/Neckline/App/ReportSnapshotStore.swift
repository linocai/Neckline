//
//  ReportSnapshotStore.swift
//  最近成功报告的本地只读快照。网络错误可回看；鉴权失败绝不拿旧报告遮掩。
//

import Foundation
import CryptoKit

struct ReportSnapshotStore {
    struct CachedSnapshot {
        let snapshot: SelectionSnapshot
        let savedAt: Date
    }
    private let root: URL
    private let namespace: String

    init(baseURL: URL, token: String, fileManager: FileManager = .default) {
        let material = baseURL.absoluteString.lowercased() + "\n" + token
        namespace = SHA256.hash(data: Data(material.utf8)).map { String(format: "%02x", $0) }.joined()
        let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fileManager.temporaryDirectory
        root = support.appendingPathComponent("Neckline/Reports/\(namespace)", isDirectory: true)
    }

    /// 切换服务或账号时删除派生快照，避免旧环境在新的身份下留下可读入口。
    static func clearAll(fileManager: FileManager = .default) {
        let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fileManager.temporaryDirectory
        let reports = support.appendingPathComponent("Neckline/Reports", isDirectory: true)
        try? fileManager.removeItem(at: reports)
    }

    func save(_ snapshot: SelectionSnapshot) throws {
        guard snapshot.state != nil else { return }
        let manager = FileManager.default
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        let filename = (snapshot.tradeDate.isEmpty ? "latest" : snapshot.tradeDate) + ".json"
        let destination = root.appendingPathComponent(filename)
        let temp = root.appendingPathComponent(UUID().uuidString + ".tmp")
        let data = try JSONEncoder().encode(snapshot)
        try data.write(to: temp, options: [.atomic])
        #if os(iOS)
        try? manager.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: temp.path)
        #endif
        _ = try? manager.replaceItemAt(destination, withItemAt: temp)
        if !manager.fileExists(atPath: destination.path) { try manager.moveItem(at: temp, to: destination) }
        trim(manager)
    }

    func latest() -> CachedSnapshot? {
        let manager = FileManager.default
        guard let files = try? manager.contentsOfDirectory(at: root, includingPropertiesForKeys: [.contentModificationDateKey]) else { return nil }
        let candidates = files.filter { $0.pathExtension == "json" }.sorted {
            let a = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let b = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return a > b
        }
        for file in candidates {
            if let data = try? Data(contentsOf: file), let snapshot = try? JSONDecoder().decode(SelectionSnapshot.self, from: data), snapshot.state != nil {
                let savedAt = (try? file.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                return CachedSnapshot(snapshot: snapshot, savedAt: savedAt)
            }
        }
        return nil
    }

    private func trim(_ manager: FileManager) {
        guard let files = try? manager.contentsOfDirectory(at: root, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        let old = files.filter { $0.pathExtension == "json" }.sorted {
            let a = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let b = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return a > b
        }.dropFirst(30)
        for file in old { try? manager.removeItem(at: file) }
    }
}
