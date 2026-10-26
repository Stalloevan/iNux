import Foundation
import SwiftUI

struct PortJob: Identifiable, Codable {
    var id: String
    var name: String
    var kind: String
    var arch: String
    var status: String      // uploading, building, downloading, done, failed
    var detail: String
    var createdAt: Date
    var ipaFile: String?
    var notes: [String]
}

@MainActor
final class AppStore: ObservableObject {
    @Published var jobs: [PortJob] = [] {
        didSet { saveJobs() }
    }
    @Published var owner: String {
        didSet { UserDefaults.standard.set(owner, forKey: "owner") }
    }
    @Published var repo: String {
        didSet { UserDefaults.standard.set(repo, forKey: "repo") }
    }
    @Published var token: String {
        didSet { Keychain.set(token, for: "github-token") }
    }
    @Published var banner: String?

    private var polling = Set<String>()

    init() {
        let defaults = UserDefaults.standard
        owner = defaults.string(forKey: "owner") ?? "Stalloevan"
        repo = defaults.string(forKey: "repo") ?? "iNux"
        token = Keychain.get("github-token")
        if let data = defaults.data(forKey: "jobs"),
           let saved = try? JSONDecoder().decode([PortJob].self, from: data) {
            jobs = saved
        }
    }

    private func saveJobs() {
        if let data = try? JSONEncoder().encode(jobs) {
            UserDefaults.standard.set(data, forKey: "jobs")
        }
    }

    private var client: GitHubClient {
        GitHubClient(owner: owner, repo: repo, token: token)
    }

    private func update(_ id: String, _ change: (inout PortJob) -> Void) {
        if let index = jobs.firstIndex(where: { $0.id == id }) {
            change(&jobs[index])
        }
    }

    private static func safeAssetName(_ name: String) -> String {
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        let mapped = name.map { allowed.contains($0) ? $0 : "_" }
        return String(mapped)
    }

    func submit(fileURL: URL) {
        guard !token.isEmpty else {
            banner = "Add a GitHub token in Settings first."
            return
        }
        let scoped = fileURL.startAccessingSecurityScopedResource()
        let report: SourceReport
        do {
            report = try SourceAnalyzer.analyze(url: fileURL)
        } catch {
            if scoped { fileURL.stopAccessingSecurityScopedResource() }
            banner = error.localizedDescription
            return
        }

        let id = String(UUID().uuidString.prefix(8)).lowercased()
        let name = fileURL.deletingPathExtension().lastPathComponent
        let assetName = Self.safeAssetName(fileURL.lastPathComponent)
        let job = PortJob(id: id, name: name, kind: report.kind.rawValue, arch: "arm64",
                          status: "uploading", detail: "Copying file", createdAt: Date(),
                          ipaFile: nil,
                          notes: ["iNux reads the app id from this \(report.kind.rawValue) and rebuilds it from source for iOS, rather than running the Linux binary inside."])
        jobs.insert(job, at: 0)

        let client = self.client
        let kind = report.kind == .flatpak ? "flatpak" : "appimage"
        Task {
            await self.run(id: id, name: name, kind: kind, source: fileURL, scoped: scoped,
                           assetName: assetName, client: client)
        }
    }

    /// Build straight from a Flathub app id (org.kde.kcalc), no upload.
    func submit(appID rawID: String) {
        let appID = rawID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else {
            banner = "Add a GitHub token in Settings first."
            return
        }
        guard appID.contains("."), !appID.contains(" ") else {
            banner = "Enter a Flathub app id like org.kde.kcalc."
            return
        }
        let id = String(UUID().uuidString.prefix(8)).lowercased()
        let name = appID.components(separatedBy: ".").last ?? appID
        let job = PortJob(id: id, name: name, kind: "source", arch: "arm64",
                          status: "building", detail: "Resolving \(appID)", createdAt: Date(),
                          ipaFile: nil, notes: ["Building \(appID) from source for iOS (arm64, native)."])
        jobs.insert(job, at: 0)
        let client = self.client
        Task {
            do {
                try await client.dispatchSourceBuild(jobID: id, appID: appID, assetName: nil)
                update(id) { $0.detail = "Compiling from source on GitHub Actions" }
            } catch {
                update(id) { $0.status = "failed"; $0.detail = error.localizedDescription }
                return
            }
            await poll(id: id, client: client)
        }
    }

    private func run(id: String, name: String, kind: String, source: URL, scoped: Bool,
                     assetName: String, client: GitHubClient) async {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("\(id)-\(assetName)")
        defer {
            if scoped { source.stopAccessingSecurityScopedResource() }
            try? FileManager.default.removeItem(at: tmp)
        }
        do {
            try await Task.detached {
                try FileManager.default.copyItem(at: source, to: tmp)
            }.value
            update(id) { $0.detail = "Creating upload slot" }
            let release = try await client.createRelease(tag: "src-\(id)", name: "Source \(id)")
            update(id) { $0.detail = "Uploading to GitHub" }
            try await client.upload(file: tmp, assetName: assetName, to: release)
            update(id) { $0.detail = "Starting build" }
            try await client.dispatchSourceBuild(jobID: id, appID: nil, assetName: assetName)
            update(id) { $0.status = "building"; $0.detail = "Resolving and compiling from source" }
        } catch {
            update(id) { $0.status = "failed"; $0.detail = error.localizedDescription }
            return
        }
        await poll(id: id, client: client)
    }

    func resumePending() {
        let client = self.client
        for job in jobs {
            if job.status == "uploading" {
                update(job.id) { $0.status = "failed"; $0.detail = "Interrupted before upload finished" }
            } else if job.status == "building" || job.status == "downloading" {
                Task { await self.poll(id: job.id, client: client) }
            }
        }
    }

    private func poll(id: String, client: GitHubClient) async {
        if polling.contains(id) { return }
        polling.insert(id)
        defer { polling.remove(id) }

        for _ in 0..<180 {
            do {
                if let rel = try await client.release(tag: "app-\(id)"),
                   let asset = rel.assets.first(where: { $0.name.hasSuffix(".ipa") }) {
                    update(id) { $0.status = "downloading"; $0.detail = "Downloading IPA" }
                    let dest = Paths.documents.appendingPathComponent(asset.name)
                    try await client.download(assetURL: asset.url, to: dest)
                    update(id) { $0.status = "done"; $0.ipaFile = asset.name; $0.detail = "IPA ready (unsigned)" }
                    return
                }
                if let run = try await client.run(containing: id) {
                    if run.status == "completed" && run.conclusion != "success" {
                        update(id) {
                            $0.status = "failed"
                            $0.detail = "Build \(run.conclusion ?? "failed"). Open the Actions tab of the repository for the log."
                        }
                        return
                    }
                    update(id) { $0.detail = "Build \(run.status.replacingOccurrences(of: "_", with: " "))" }
                }
            } catch {
                update(id) { $0.detail = "Retrying: \(error.localizedDescription)" }
            }
            try? await Task.sleep(nanoseconds: 20_000_000_000)
        }
        update(id) { $0.status = "failed"; $0.detail = "Timed out waiting for the build" }
    }
}
