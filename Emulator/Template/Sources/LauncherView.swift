import SwiftUI
import UIKit

struct LauncherView: View {
    @State private var manifest: PortManifest? = PayloadStore.loadManifest()

    var body: some View {
        NavigationStack {
            if let m = manifest {
                LauncherContent(manifest: m)
            } else {
                Text("manifest.json is missing from this app.")
                    .padding()
            }
        }
    }
}

private struct LauncherContent: View {
    let manifest: PortManifest
    @StateObject private var session: GuestSession
    @State private var showTerminal = false
    @State private var showLog = false

    init(manifest: PortManifest) {
        self.manifest = manifest
        _session = StateObject(wrappedValue: GuestSession(manifest: manifest))
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 14) {
                    if let data = PayloadStore.iconData(), let image = UIImage(data: data) {
                        Image(uiImage: image)
                            .resizable()
                            .interpolation(.high)
                            .frame(width: 64, height: 64)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(manifest.displayName).font(.title3).bold()
                        Text("\(manifest.kind) · \(manifest.arch) · emulated")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Button {
                    showTerminal = true
                } label: {
                    Label(session.hasRun ? "Show Output" : "Run", systemImage: "play.fill")
                }
                .disabled(manifest.entry == nil)
                if manifest.gui == true {
                    Text("This is a graphical app. Graphics support is not finished yet, so it will most likely stop with a \"cannot open display\" error.")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            }

            if let smoke = manifest.smokeTest, let status = smoke.status {
                Section("Build test (Linux)") {
                    LabeledContent("Exit status", value: "\(status)")
                    if let output = smoke.output, !output.isEmpty {
                        Text(output)
                            .font(.system(.caption2, design: .monospaced))
                            .lineLimit(12)
                    }
                }
            }

            Section("Compatibility") {
                ForEach(manifest.notes, id: \.self) { note in
                    Text(note).font(.footnote)
                }
                if let missing = manifest.missingLibraries, !missing.isEmpty {
                    Text("Missing: " + missing.keys.sorted().joined(separator: ", "))
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }

            Section("Details") {
                if let entry = manifest.entry {
                    LabeledContent("Program", value: entry)
                }
                LabeledContent("Files", value: "\(manifest.fileCount)")
                LabeledContent("Size", value: ByteCountFormatter.string(fromByteCount: manifest.totalBytes, countStyle: .file))
                Button("Engine log") { showLog = true }
                Button("Reset app files", role: .destructive) {
                    try? GuestFileSystem.reset()
                }
                .disabled(session.hasRun)
            }
        }
        .navigationTitle(manifest.displayName)
        .fullScreenCover(isPresented: $showTerminal) {
            TerminalContainer(session: session, title: manifest.displayName)
        }
        .sheet(isPresented: $showLog) {
            LogView()
        }
    }
}

private struct TerminalContainer: View {
    @ObservedObject var session: GuestSession
    let title: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                TerminalScreen(session: session)
            }
            .safeAreaInset(edge: .top) {
                statusBar
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Close") { dismiss() }
                }
            }
            .onAppear { session.start() }
        }
    }

    @ViewBuilder
    private var statusBar: some View {
        switch session.state {
        case .idle:
            EmptyView()
        case .preparing(let message):
            banner(message + "…", color: .blue)
        case .running:
            EmptyView()
        case .exited(let status):
            banner("Program exited with status \(status). Close the app fully to run it again.", color: status == 0 ? .green : .orange)
        case .failed(let message):
            banner(message, color: .red)
        }
    }

    private func banner(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(.white)
            .padding(8)
            .frame(maxWidth: .infinity)
            .background(color.opacity(0.85))
    }
}

private struct LogView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(text.isEmpty ? "The engine has not logged anything." : text)
                    .font(.system(.caption2, design: .monospaced))
                    .textSelection(.enabled)
                    .padding()
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle("Engine log")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .navigationBarLeading) {
                    ShareLink(item: GuestFileSystem.logURL)
                }
            }
            .onAppear {
                let data = (try? Data(contentsOf: GuestFileSystem.logURL)) ?? Data()
                let tail = data.suffix(200_000)
                text = String(decoding: tail, as: UTF8.self)
            }
        }
    }
}
