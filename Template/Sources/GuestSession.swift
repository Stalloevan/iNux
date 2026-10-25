import Foundation

/// Runs the ported program inside the Blink engine and moves bytes between
/// its terminal and the on-screen terminal view.
@MainActor
final class GuestSession: ObservableObject {
    enum State: Equatable {
        case idle
        case preparing(String)
        case running
        case exited(Int)
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var startedAt: Date?

    /// Set by the terminal view; receives guest output on the main thread.
    var output: (([UInt8]) -> Void)?

    private let manifest: PortManifest
    private var readFD: Int32 = -1
    private var writeFD: Int32 = -1
    private var isPTY = false
    private var reader: DispatchSourceRead?
    private var pending: [UInt8] = []
    private var cols = 80
    private var rows = 24

    init(manifest: PortManifest) {
        self.manifest = manifest
    }

    var hasRun: Bool {
        switch state {
        case .idle, .preparing: return false
        default: return true
        }
    }

    func resize(cols: Int, rows: Int) {
        self.cols = cols
        self.rows = rows
        if isPTY && readFD >= 0 {
            inux_resize_terminal(readFD, Int32(cols), Int32(rows))
        }
    }

    func start() {
        guard case .idle = state else { return }
        guard let entry = manifest.entry else {
            state = .failed("This port has no program to run.")
            return
        }
        if inux_engine_running() != 0 || inux_engine_exit_status() != -1 {
            state = .failed("A program already ran in this session. Close the app completely and open it again.")
            return
        }
        state = .preparing("Preparing files")
        let buildId = manifest.buildId ?? "\(manifest.name)-\(manifest.totalBytes)"
        Task.detached(priority: .userInitiated) { [weak self] in
            do {
                try GuestFileSystem.prepare(buildId: buildId) { message in
                    Task { @MainActor in self?.state = .preparing(message) }
                }
                await self?.launch(entry: entry)
            } catch {
                await self?.fail(error.localizedDescription)
            }
        }
    }

    private func fail(_ message: String) {
        state = .failed(message)
    }

    private func launch(entry: String) {
        var r: Int32 = -1
        var w: Int32 = -1
        let mode = inux_attach_terminal(Int32(cols), Int32(rows), &r, &w)
        if mode < 0 {
            state = .failed("Could not create a terminal for the program.")
            return
        }
        isPTY = mode == 1
        readFD = r
        writeFD = w
        startReading()

        let root = GuestFileSystem.rootURL.path
        // The host working directory becomes the guest's.
        let home = GuestFileSystem.rootURL.appendingPathComponent("home/user").path
        _ = FileManager.default.changeCurrentDirectoryPath(home)
        setenv("BLINK_LOG_FILENAME", GuestFileSystem.logURL.path, 1)

        var env = manifest.env ?? [:]
        env["COLUMNS"] = String(cols)
        env["LINES"] = String(rows)
        let envStrings = env.keys.sorted().map { "\($0)=\(env[$0]!)" }
        let argStrings = [entry] + (manifest.args ?? [])

        let argv = Self.cStringArray(argStrings)
        let envp = Self.cStringArray(envStrings)
        defer {
            Self.freeCStringArray(argv, count: argStrings.count)
            Self.freeCStringArray(envp, count: envStrings.count)
        }

        let context = Unmanaged.passRetained(self).toOpaque()
        let callback: inux_exit_callback = { status, ctx in
            guard let ctx = ctx else { return }
            let session = Unmanaged<GuestSession>.fromOpaque(ctx).takeRetainedValue()
            Task { @MainActor in
                session.guestExited(status: Int(status))
            }
        }
        let rc = inux_engine_start(root, Int32(argStrings.count), argv, envp, callback, context)
        if rc != 0 {
            Unmanaged<GuestSession>.fromOpaque(context).release()
            state = .failed("The engine did not start (error \(rc)).")
            return
        }
        startedAt = Date()
        state = .running
    }

    private func guestExited(status: Int) {
        // Let the last output drain before reporting.
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            self?.state = .exited(status)
        }
    }

    private func startReading() {
        let fd = readFD
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: DispatchQueue.global(qos: .userInitiated))
        source.setEventHandler { [weak self] in
            var buffer = [UInt8](repeating: 0, count: 65536)
            let n = read(fd, &buffer, buffer.count)
            if n <= 0 { return }
            let chunk = Array(buffer[0..<n])
            // Main-queue blocks run in order, which terminal output needs.
            DispatchQueue.main.async {
                self?.deliver(chunk)
            }
        }
        source.resume()
        reader = source
    }

    private func deliver(_ bytes: [UInt8]) {
        if let output = output {
            if !pending.isEmpty {
                output(pending)
                pending.removeAll()
            }
            output(bytes)
        } else {
            pending.append(contentsOf: bytes)
        }
    }

    func attachOutput(_ handler: @escaping ([UInt8]) -> Void) {
        output = handler
        if !pending.isEmpty {
            handler(pending)
            pending.removeAll()
        }
    }

    /// Keystrokes from the terminal view.
    func send(_ bytes: [UInt8]) {
        guard writeFD >= 0 else { return }
        var data = bytes
        if !isPTY {
            // Pipes have no line discipline: translate Return and echo locally.
            data = bytes.map { $0 == 13 ? 10 : $0 }
            deliver(data.flatMap { $0 == 10 ? [13, 10] : [$0] })
        }
        let fd = writeFD
        let payload = data
        DispatchQueue.global(qos: .userInitiated).async {
            payload.withUnsafeBytes { raw in
                var offset = 0
                while offset < raw.count {
                    let n = write(fd, raw.baseAddress! + offset, raw.count - offset)
                    if n <= 0 { break }
                    offset += n
                }
            }
        }
    }

    private static func cStringArray(_ strings: [String]) -> UnsafeMutablePointer<UnsafePointer<CChar>?> {
        let array = UnsafeMutablePointer<UnsafePointer<CChar>?>.allocate(capacity: strings.count + 1)
        for (i, s) in strings.enumerated() {
            array[i] = UnsafePointer(strdup(s))
        }
        array[strings.count] = nil
        return array
    }

    private static func freeCStringArray(_ array: UnsafeMutablePointer<UnsafePointer<CChar>?>, count: Int) {
        for i in 0..<count {
            free(UnsafeMutableRawPointer(mutating: array[i]))
        }
        array.deallocate()
    }
}
