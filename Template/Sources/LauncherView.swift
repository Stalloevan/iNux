import SwiftUI
import UIKit

struct LauncherView: View {
    @State private var manifest: PortManifest? = PayloadStore.loadManifest()
    @State private var outcome: LaunchOutcome?

    var body: some View {
        NavigationStack {
            if let m = manifest {
                List {
                    Section {
                        HStack(spacing: 14) {
                            if let data = PayloadStore.iconData(), let image = UIImage(data: data) {
                                Image(uiImage: image)
                                    .resizable()
                                    .frame(width: 64, height: 64)
                                    .clipShape(RoundedRectangle(cornerRadius: 14))
                            }
                            VStack(alignment: .leading) {
                                Text(m.desktopName ?? m.name).font(.title3).bold()
                                Text("\(m.kind) / \(m.arch)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Button("Launch") {
                            outcome = Runtime.launch(m)
                        }
                        if let o = outcome {
                            Text(o.message)
                                .font(.footnote)
                                .foregroundStyle(o.started ? Color.green : Color.orange)
                        }
                    }
                    Section("Compatibility") {
                        ForEach(m.notes, id: \.self) { note in
                            Text(note).font(.footnote)
                        }
                    }
                    Section("Payload") {
                        LabeledContent("Files", value: "\(m.fileCount)")
                        LabeledContent("Size", value: ByteCountFormatter.string(fromByteCount: m.totalBytes, countStyle: .file))
                        if let entry = m.entry {
                            LabeledContent("Entry", value: entry)
                        }
                        ForEach(PayloadStore.topLevelEntries(), id: \.self) { name in
                            Text(name).font(.system(.footnote, design: .monospaced))
                        }
                    }
                }
                .navigationTitle(m.desktopName ?? m.name)
            } else {
                Text("manifest.json is missing from this app.")
                    .padding()
            }
        }
    }
}
