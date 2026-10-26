import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject var store: AppStore
    @State private var importing = false
    @State private var showSettings = false
    @State private var askID = false
    @State private var appID = ""

    var body: some View {
        NavigationStack {
            Group {
                if store.jobs.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "shippingbox")
                            .font(.system(size: 48))
                            .foregroundStyle(.secondary)
                        Text("No ports yet")
                            .font(.headline)
                        Text("Tap + to enter a Flathub app id (like org.kde.kcalc) or pick an .AppImage or .flatpak file. The app is recompiled from source for iOS.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding()
                } else {
                    List {
                        ForEach(store.jobs) { job in
                            NavigationLink(value: job.id) {
                                JobRow(job: job)
                            }
                        }
                        .onDelete { offsets in
                            store.jobs.remove(atOffsets: offsets)
                        }
                    }
                }
            }
            .navigationTitle("iNux")
            .navigationDestination(for: String.self) { id in
                JobDetail(jobID: id)
            }
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Button { askID = true } label: { Label("From Flathub id…", systemImage: "number") }
                        Button { importing = true } label: { Label("From file…", systemImage: "doc") }
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .alert("Flathub app id", isPresented: $askID) {
                TextField("org.kde.kcalc", text: $appID)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Build") {
                    store.submit(appID: appID)
                    appID = ""
                }
                Button("Cancel", role: .cancel) { appID = "" }
            } message: {
                Text("The id from the app's Flathub page, used to fetch its source and build recipe.")
            }
            .fileImporter(isPresented: $importing,
                          allowedContentTypes: [.data, .item],
                          allowsMultipleSelection: false) { result in
                switch result {
                case .success(let urls):
                    if let url = urls.first { store.submit(fileURL: url) }
                case .failure(let error):
                    store.banner = error.localizedDescription
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .alert("iNux", isPresented: Binding(
                get: { store.banner != nil },
                set: { if !$0 { store.banner = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(store.banner ?? "")
            }
            .onAppear { store.resumePending() }
        }
    }
}

struct JobRow: View {
    let job: PortJob

    private var icon: String {
        switch job.status {
        case "done": return "checkmark.circle.fill"
        case "failed": return "xmark.octagon.fill"
        default: return "hourglass"
        }
    }

    private var tint: Color {
        switch job.status {
        case "done": return .green
        case "failed": return .red
        default: return .orange
        }
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.name).font(.headline)
                Text(job.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }
}

struct JobDetail: View {
    @EnvironmentObject var store: AppStore
    let jobID: String

    var body: some View {
        if let job = store.jobs.first(where: { $0.id == jobID }) {
            List {
                Section("Status") {
                    Text(job.status.capitalized)
                    Text(job.detail).foregroundStyle(.secondary)
                }
                Section("Source") {
                    LabeledContent("Type", value: job.kind)
                    LabeledContent("Architecture", value: job.arch)
                }
                if !job.notes.isEmpty {
                    Section("Compatibility") {
                        ForEach(job.notes, id: \.self) { note in
                            Text(note)
                        }
                    }
                }
                if let file = job.ipaFile {
                    Section("Result") {
                        ShareLink(item: Paths.documents.appendingPathComponent(file)) {
                            Label("Share IPA", systemImage: "square.and.arrow.up")
                        }
                        Text("Unsigned. Sign and install it with your usual sideloading tool.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle(job.name)
        } else {
            Text("This job was removed.")
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Repository") {
                    TextField("Owner", text: $store.owner)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Name", text: $store.repo)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section {
                    SecureField("GitHub token", text: $store.token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Access")
                } footer: {
                    Text("Create a token with repo and workflow scopes (classic), or Contents and Actions read/write on this repository (fine-grained). It is stored in the Keychain.")
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
