import Foundation

struct PortBinary: Codable {
    var path: String
    var arch: String
}

struct PortManifest: Codable {
    var name: String
    var kind: String
    var arch: String
    var entry: String?
    var desktopName: String?
    var desktopExec: String?
    var fileCount: Int
    var totalBytes: Int64
    var binaries: [PortBinary]
    var notes: [String]
    var hasIcon: Bool
}

enum PayloadStore {
    static var generatedURL: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("Generated", isDirectory: true)
    }

    static func loadManifest() -> PortManifest? {
        guard let dir = generatedURL else { return nil }
        let url = dir.appendingPathComponent("manifest.json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(PortManifest.self, from: data)
    }

    static func topLevelEntries() -> [String] {
        guard let dir = generatedURL?.appendingPathComponent("rootfs", isDirectory: true),
              let names = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else { return [] }
        return names.sorted()
    }

    static func iconData() -> Data? {
        guard let dir = generatedURL else { return nil }
        return try? Data(contentsOf: dir.appendingPathComponent("icon.png"))
    }
}
