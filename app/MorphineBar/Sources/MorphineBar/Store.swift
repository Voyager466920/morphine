import AppKit
import Foundation
import ServiceManagement
import SwiftUI

/// Polls the `morphine` CLI and exposes what the menu bar needs.
@MainActor
final class Store: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var lastError: String?
    @Published var now = Date()
    @Published var busy = false
    @Published var showPercent: Bool = UserDefaults.standard.object(forKey: "showPercent") as? Bool ?? true {
        didSet { UserDefaults.standard.set(showPercent, forKey: "showPercent") }
    }
    /// `defaults write dev.morphine.bar morphinePath /path/to/morphine` to override discovery.
    var morphinePathOverride: String { UserDefaults.standard.string(forKey: "morphinePath") ?? "" }

    private var timers: [Timer] = []
    private var shellPath: String?

    init() {
        refresh(network: false)
        refresh(network: true)
        timers.append(Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.now = Date() }
        })
        timers.append(Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh(network: false) }
        })
        timers.append(Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh(network: true) }
        })
    }

    // MARK: - Derived state

    var waiting: [Waiter] { snapshot?.waiting ?? [] }
    var isLocked: Bool { !waiting.isEmpty }
    var nextResume: Waiter? { waiting.min { $0.resumeAt < $1.resumeAt } }
    var threshold: Double { snapshot?.thresholdPct ?? 2 }

    /// The most-drained watched, non-estimated window across providers.
    var worst: (provider: Provider, window: UsageWindow, used: Double)? {
        guard let snap = snapshot else { return nil }
        let watched = Set(snap.windows ?? ["5h", "7d", "1d"])
        var best: (Provider, UsageWindow, Double)?
        for p in snap.providers {
            for w in p.windows where watched.contains(w.name) && !(w.estimated ?? false) {
                guard let u = w.used(at: now) else { continue }
                if best == nil || u > best!.2 { best = (p, w, u) }
            }
        }
        return best.map { (provider: $0.0, window: $0.1, used: $0.2) }
    }

    var menuTitle: String {
        if let w = nextResume { return Fmt.duration(w.resumeAt - now.timeIntervalSince1970) }
        if showPercent, let worst = worst { return "\(Int(worst.used.rounded()))%" }
        return ""
    }

    var icon: NSImage {
        SyringeIcon.image(remaining: worst.map { (100 - $0.used) / 100 }, locked: isLocked)
    }

    // MARK: - Actions

    func refresh(network: Bool) {
        var args = ["status", "--json"]
        if !network { args.append("--cached") }
        Task {
            let result = await run(args)
            switch result {
            case .success(let out):
                do {
                    let dec = JSONDecoder()
                    dec.keyDecodingStrategy = .convertFromSnakeCase
                    snapshot = try dec.decode(Snapshot.self, from: Data(out.utf8))
                    lastError = nil
                } catch {
                    lastError = "Could not read morphine output: \(error.localizedDescription)"
                }
            case .failure(let err):
                lastError = err.message
            }
        }
    }

    func resumeNow() {
        Task { _ = await run(["resume"]); refresh(network: false) }
    }

    func setThreshold(_ pct: Double) {
        Task { _ = await run(["config", "set", "threshold_pct", String(pct)]); refresh(network: false) }
    }

    func setKeepAwake(_ on: Bool) {
        Task { _ = await run(["config", "set", "keep_awake", on ? "true" : "false"]); refresh(network: false) }
    }

    func openConfig() {
        Task {
            if case .success(let path) = await run(["config", "path"]) {
                let p = path.trimmingCharacters(in: .whitespacesAndNewlines)
                if !FileManager.default.fileExists(atPath: p) {
                    _ = await run(["config", "set", "threshold_pct", String(threshold)])
                }
                NSWorkspace.shared.open(URL(fileURLWithPath: p))
            }
        }
    }

    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do {
                if newValue { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                lastError = L.t("Launch at login needs the bundled Morphine.app: ",
                                "로그인 시 실행은 Morphine.app 번들에서만 됩니다: ") + error.localizedDescription
            }
            objectWillChange.send()
        }
    }

    // MARK: - Running the CLI

    struct CLIError: Error { let message: String }

    private func morphineExecutable() -> String? {
        let fm = FileManager.default
        if !morphinePathOverride.isEmpty, fm.isExecutableFile(atPath: morphinePathOverride) {
            return morphinePathOverride
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("morphine/bin/morphine").path,
           fm.isExecutableFile(atPath: bundled) {
            return bundled
        }
        let home = fm.homeDirectoryForCurrentUser.path
        for p in ["/opt/homebrew/bin/morphine", "/usr/local/bin/morphine", "\(home)/.local/bin/morphine"]
        where fm.isExecutableFile(atPath: p) {
            return p
        }
        for dir in (environmentPath()).split(separator: ":") {
            let p = "\(dir)/morphine"
            if fm.isExecutableFile(atPath: p) { return p }
        }
        return nil
    }

    /// GUI apps get a bare PATH; borrow the login shell's so `codex`, `agy`, etc. resolve.
    private func environmentPath() -> String {
        if let p = shellPath { return p }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var path = "/opt/homebrew/bin:/usr/local/bin:\(home)/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        proc.arguments = ["-lc", "printf %s \"$PATH\""]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        if (try? proc.run()) != nil {
            proc.waitUntilExit()
            let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            if !out.isEmpty { path = out + ":" + path }
        }
        shellPath = path
        return path
    }

    private func run(_ args: [String]) async -> Result<String, CLIError> {
        guard let exe = morphineExecutable() else {
            return .failure(CLIError(message: L.t(
                "`morphine` CLI not found. Install it (see README) or set its path in Settings.",
                "`morphine` CLI를 찾을 수 없어요. README대로 설치하거나 설정에서 경로를 지정하세요.")))
        }
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = environmentPath()
        return await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .utility).async {
                let proc = Process()
                proc.executableURL = URL(fileURLWithPath: exe)
                proc.arguments = args
                proc.environment = env
                let out = Pipe(), err = Pipe()
                proc.standardOutput = out
                proc.standardError = err
                do {
                    try proc.run()
                } catch {
                    cont.resume(returning: .failure(CLIError(message: error.localizedDescription)))
                    return
                }
                let data = out.fileHandleForReading.readDataToEndOfFile()
                let edata = err.fileHandleForReading.readDataToEndOfFile()
                proc.waitUntilExit()
                if proc.terminationStatus == 0 {
                    cont.resume(returning: .success(String(data: data, encoding: .utf8) ?? ""))
                } else {
                    let msg = String(data: edata, encoding: .utf8) ?? "exit \(proc.terminationStatus)"
                    cont.resume(returning: .failure(CLIError(message: msg)))
                }
            }
        }
    }
}
