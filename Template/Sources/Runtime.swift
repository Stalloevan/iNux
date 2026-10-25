import Foundation

struct LaunchOutcome {
    var started: Bool
    var message: String
}

/// Extension point for an execution backend (an x86-64 emulator, a Linux
/// userland layer, a WebAssembly translation, and so on).
/// No backend is bundled yet, so launching reports what is missing.
protocol ExecutionBackend {
    var name: String { get }
    func canRun(_ manifest: PortManifest) -> Bool
    func launch(_ manifest: PortManifest, rootfs: URL) throws -> LaunchOutcome
}

enum Runtime {
    /// Add backends here as they are implemented.
    static var backends: [ExecutionBackend] = []

    static func launch(_ manifest: PortManifest) -> LaunchOutcome {
        guard let rootfs = PayloadStore.generatedURL?.appendingPathComponent("rootfs", isDirectory: true) else {
            return LaunchOutcome(started: false, message: "The payload is missing from this app bundle.")
        }
        for backend in backends where backend.canRun(manifest) {
            do {
                return try backend.launch(manifest, rootfs: rootfs)
            } catch {
                return LaunchOutcome(started: false, message: "\(backend.name) failed: \(error.localizedDescription)")
            }
        }
        var lines = ["No execution backend is bundled in this build, so the program was not started."]
        switch manifest.arch {
        case "x86_64", "x86":
            lines.append("This is an \(manifest.arch) program. It needs a CPU emulator, and iOS only allows that with JIT or a very slow interpreter.")
        case "aarch64":
            lines.append("This is an ARM64 Linux program. iOS refuses to run unsigned native code from the app bundle and has no Linux kernel interface.")
        default:
            lines.append("The program's CPU architecture could not be matched to a backend.")
        }
        return LaunchOutcome(started: false, message: lines.joined(separator: " "))
    }
}
