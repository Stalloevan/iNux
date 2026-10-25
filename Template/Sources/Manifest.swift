import Foundation

struct SmokeTest: Codable {
    var status: Int?
    var output: String?
    var seconds: Double?
}

struct PortManifest: Codable {
    var name: String
    var kind: String
    var arch: String
    var buildId: String?
    var entry: String?
    var args: [String]?
    var env: [String: String]?
    var gui: Bool?
    var desktopName: String?
    var desktopExec: String?
    var fileCount: Int
    var totalBytes: Int64
    var missingLibraries: [String: [String]]?
    var notes: [String]
    var hasIcon: Bool
    var smokeTest: SmokeTest?

    var displayName: String { desktopName ?? name }
}

enum PayloadStore {
    static var generatedURL: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("Generated", isDirectory: true)
    }

    static var bundledRootURL: URL? {
        generatedURL?.appendingPathComponent("rootfs", isDirectory: true)
    }

    static func loadManifest() -> PortManifest? {
        guard let dir = generatedURL else { return nil }
        let url = dir.appendingPathComponent("manifest.json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(PortManifest.self, from: data)
    }

    static func iconData() -> Data? {
        guard let dir = generatedURL else { return nil }
        return try? Data(contentsOf: dir.appendingPathComponent("icon.png"))
    }
}
