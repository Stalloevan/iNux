import Foundation

/// The guest's root directory. The copy inside the app bundle is read-only,
/// so on first launch it is copied (APFS clones files, so this is cheap) to
/// Application Support, where the program can write.
enum GuestFileSystem {
    static var baseURL: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return support.appendingPathComponent("Guest", isDirectory: true)
    }

    static var rootURL: URL { baseURL.appendingPathComponent("root", isDirectory: true) }
    static var markerURL: URL { baseURL.appendingPathComponent("build-id") }

    static var logURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("blink.log")
    }

    /// Directories the engine expects to exist (it mounts /dev and /proc).
    static let requiredDirectories = ["dev", "proc", "SystemRoot", "tmp", "var/tmp", "root",
                                      "home/user", "run/user/501"]

    static func isReady(buildId: String) -> Bool {
        guard let existing = try? String(contentsOf: markerURL, encoding: .utf8) else { return false }
        return existing == buildId && FileManager.default.fileExists(atPath: rootURL.path)
    }

    /// Runs off the main thread. `progress` gets short status lines.
    static func prepare(buildId: String, progress: (String) -> Void) throws {
        let fm = FileManager.default
        if isReady(buildId: buildId) {
            try ensureDirectories()
            return
        }
        guard let source = PayloadStore.bundledRootURL, fm.fileExists(atPath: source.path) else {
            throw GuestError("The app's files are missing from the bundle.")
        }
        progress("Copying app files")
        try fm.createDirectory(at: baseURL, withIntermediateDirectories: true)
        if fm.fileExists(atPath: rootURL.path) {
            try fm.removeItem(at: rootURL)
        }
        try fm.copyItem(at: source, to: rootURL)
        progress("Setting permissions")
        markExecutables()
        try ensureDirectories()
        try buildId.write(to: markerURL, atomically: true, encoding: .utf8)
    }

    static func ensureDirectories() throws {
        let fm = FileManager.default
        for dir in requiredDirectories {
            try fm.createDirectory(at: rootURL.appendingPathComponent(dir, isDirectory: true),
                                   withIntermediateDirectories: true)
        }
    }

    /// Install and re-signing tools can drop the executable bit. Restore it on
    /// every ELF file and script, since the engine checks it before running.
    private static func markExecutables() {
        let fm = FileManager.default
        guard let walker = fm.enumerator(at: rootURL, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey]) else { return }
        for case let url as URL in walker {
            guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true else { continue }
            guard let handle = try? FileHandle(forReadingFrom: url) else { continue }
            let head = (try? handle.read(upToCount: 4)) ?? Data()
            try? handle.close()
            let bytes = [UInt8](head)
            let elf = bytes.count == 4 && bytes[0] == 0x7f && bytes[1] == 0x45 && bytes[2] == 0x4c && bytes[3] == 0x46
            let script = bytes.count >= 2 && bytes[0] == 0x23 && bytes[1] == 0x21
            if elf || script {
                chmod(url.path, 0o755)
            }
        }
    }

    static func reset() throws {
        let fm = FileManager.default
        if fm.fileExists(atPath: baseURL.path) {
            try fm.removeItem(at: baseURL)
        }
    }
}

struct GuestError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
