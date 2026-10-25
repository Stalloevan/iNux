import Foundation

enum SourceKind: String, Codable {
    case appImage
    case flatpak
}

struct SourceReport {
    var kind: SourceKind
    var arch: String
    var sizeBytes: Int64
    var notes: [String]
}

struct AnalyzeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Quick, on-device look at the file before it is sent to CI.
/// The real extraction happens in Tools/port/port.py on the build runner.
enum SourceAnalyzer {
    static func archName(_ machine: UInt16) -> String {
        switch machine {
        case 0x03: return "x86"
        case 0x28: return "arm"
        case 0x3E: return "x86_64"
        case 0xB7: return "aarch64"
        case 0xF3: return "riscv"
        default: return "unknown"
        }
    }

    static func analyze(url: URL) throws -> SourceReport {
        let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
        let size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let head = [UInt8](try handle.read(upToCount: 64) ?? Data())
        let ext = url.pathExtension.lowercased()
        var notes: [String] = []

        let isELF = head.count >= 20 && head[0] == 0x7f && head[1] == 0x45 && head[2] == 0x4c && head[3] == 0x46
        if isELF {
            let machine = UInt16(head[18]) | (UInt16(head[19]) << 8)
            let arch = archName(machine)
            let hasAIMagic = head.count > 10 && head[8] == 0x41 && head[9] == 0x49
            if hasAIMagic {
                notes.append("AppImage type \(head[10])")
            } else if ext == "appimage" {
                notes.append("ELF file with .AppImage extension but no AppImage magic; will try anyway")
            } else {
                throw AnalyzeError(message: "This is a plain Linux executable, not an AppImage or Flatpak.")
            }
            if head.count > 10 && head[10] == 1 {
                notes.append("Type 1 AppImages (ISO9660) are not supported; only squashfs (type 2).")
            }
            notes.append(contentsOf: archNotes(arch))
            return SourceReport(kind: .appImage, arch: arch, sizeBytes: size, notes: notes)
        }

        if ext == "flatpak" {
            notes.append("Flatpak bundle. Architecture is read during the build.")
            notes.append("Bundles usually do not include their runtime (for example org.freedesktop.Platform), so many apps will be incomplete.")
            return SourceReport(kind: .flatpak, arch: "detected in build", sizeBytes: size, notes: notes)
        }

        throw AnalyzeError(message: "Pick a .AppImage or .flatpak file.")
    }

    private static func archNotes(_ arch: String) -> [String] {
        switch arch {
        case "x86_64", "x86":
            return ["x86 binary: iOS cannot run it natively. It needs an emulation backend."]
        case "aarch64":
            return ["ARM64 Linux binary: it uses Linux system calls and libc, which iOS does not provide."]
        default:
            return ["Unrecognised CPU architecture."]
        }
    }
}
