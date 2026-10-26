import Foundation

struct GHError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Talks to the iNux repository: uploads the source file as a release asset,
/// triggers the port workflow, and downloads the finished IPA.
struct GitHubClient {
    let owner: String
    let repo: String
    let token: String

    struct Asset: Decodable {
        let id: Int
        let name: String
        let url: String
    }

    struct Release: Decodable {
        let id: Int
        let upload_url: String
        let tag_name: String
        let assets: [Asset]
    }

    struct Run: Decodable {
        let status: String
        let conclusion: String?
        let display_title: String?
    }

    struct Runs: Decodable {
        let workflow_runs: [Run]
    }

    private func request(_ url: URL, method: String = "GET",
                         accept: String = "application/vnd.github+json") -> URLRequest {
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.setValue(accept, forHTTPHeaderField: "Accept")
        r.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        return r
    }

    private func api(_ path: String) -> URL {
        URL(string: "https://api.github.com/repos/\(owner)/\(repo)\(path)")!
    }

    private func check(_ data: Data, _ response: URLResponse, ok: Set<Int>) throws {
        guard let http = response as? HTTPURLResponse else {
            throw GHError(message: "No response from GitHub")
        }
        if ok.contains(http.statusCode) { return }
        var message = ""
        if let obj = try? JSONSerialization.jsonObject(with: data),
           let dict = obj as? [String: Any],
           let m = dict["message"] as? String {
            message = m
        }
        if http.statusCode == 401 || http.statusCode == 403 || http.statusCode == 404 {
            message += " (check the token scopes and the repository name in Settings)"
        }
        throw GHError(message: "GitHub error \(http.statusCode): \(message)")
    }

    func createRelease(tag: String, name: String) async throws -> Release {
        var r = request(api("/releases"), method: "POST")
        let body: [String: Any] = [
            "tag_name": tag,
            "name": name,
            "prerelease": true,
            "body": "Temporary source upload for an iNux port. Deleted automatically after the build."
        ]
        r.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: r)
        try check(data, resp, ok: [201])
        return try JSONDecoder().decode(Release.self, from: data)
    }

    func upload(file: URL, assetName: String, to release: Release) async throws {
        let base = String(release.upload_url.split(separator: "{")[0])
        let encoded = assetName.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? assetName
        guard let url = URL(string: "\(base)?name=\(encoded)") else {
            throw GHError(message: "Bad upload URL")
        }
        var r = request(url, method: "POST")
        r.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        r.timeoutInterval = 3600
        let (data, resp) = try await URLSession.shared.upload(for: r, fromFile: file)
        try check(data, resp, ok: [201])
    }

    /// Starts the source-build workflow. Pass either a Flathub app id or the
    /// name of a source file already uploaded to release src-<jobID>.
    func dispatchSourceBuild(jobID: String, appID: String?, assetName: String?) async throws {
        var r = request(api("/actions/workflows/source-build.yml/dispatches"), method: "POST")
        var inputs: [String: String] = ["job_id": jobID]
        if let appID = appID, !appID.isEmpty {
            inputs["app_id"] = appID
        }
        if let assetName = assetName, !assetName.isEmpty {
            inputs["asset_name"] = assetName
        }
        let body: [String: Any] = ["ref": "main", "inputs": inputs]
        r.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: r)
        try check(data, resp, ok: [204])
    }

    func release(tag: String) async throws -> Release? {
        let r = request(api("/releases/tags/\(tag)"))
        let (data, resp) = try await URLSession.shared.data(for: r)
        if let http = resp as? HTTPURLResponse, http.statusCode == 404 { return nil }
        try check(data, resp, ok: [200])
        return try JSONDecoder().decode(Release.self, from: data)
    }

    func run(containing needle: String) async throws -> Run? {
        let r = request(api("/actions/runs?event=workflow_dispatch&per_page=20"))
        let (data, resp) = try await URLSession.shared.data(for: r)
        try check(data, resp, ok: [200])
        let runs = try JSONDecoder().decode(Runs.self, from: data)
        return runs.workflow_runs.first(where: { ($0.display_title ?? "").contains(needle) })
    }

    func download(assetURL: String, to destination: URL) async throws {
        guard let url = URL(string: assetURL) else { throw GHError(message: "Bad asset URL") }
        let r = request(url, accept: "application/octet-stream")
        let (tmp, resp) = try await URLSession.shared.download(for: r)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
            throw GHError(message: "Could not download the IPA")
        }
        try? FileManager.default.removeItem(at: destination)
        try FileManager.default.moveItem(at: tmp, to: destination)
    }
}
