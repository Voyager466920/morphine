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
            guard let self else { return }
            Task { @MainActor in self.now = Date() }
        })
        timers.append(Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.refresh(network: false) }
        })
        timers.append(Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.refresh(network: true) }
        })
    }

    // MARK: - Derived state

    var waiting: [Waiter] { snapshot?.waiting ?? [] }
    var isLocked: Bool { !waiting.isEmpty }
    var nextResume: Waiter? { waiting.min { $0.resumeAt < $1.resumeAt } }
    var threshold: Double { snapshot?.thresholdPct ?? 5 }

    /// Show every agent in the menu bar (true) or only the most urgent one.
    @Published var showAllAgents: Bool = UserDefaults.standard.object(forKey: "showAllAgents") as? Bool ?? true {
        didSet { UserDefaults.standard.set(showAllAgents, forKey: "showAllAgents") }
    }

    /// Providers worth showing: they have usage numbers, or an agent is waiting on them.
    var visibleProviders: [Provider] {
        (snapshot?.providers ?? []).filter { p in
            !p.windows.isEmpty || waiting.contains { $0.provider == p.id }
        }
    }

    var hiddenProviders: [Provider] {
        let shown = Set(visibleProviders.map(\.id))
        return (snapshot?.providers ?? []).filter { !shown.contains($0.id) }
    }

    /// What the menu bar shows for a provider: the SHORTEST window it has —
    /// the 5-hour window when present, otherwise the daily, otherwise the weekly.
    func remaining(of p: Provider) -> (pct: Double, estimated: Bool)? {
        let order = ["5h": 0, "1d": 1, "7d": 2]
        let known = p.windows.filter { $0.used(at: now) != nil }
        guard let w = known.min(by: { (order[$0.name] ?? 99) < (order[$1.name] ?? 99) }),
              let u = w.used(at: now) else { return nil }
        return (pct: max(0, 100 - u), estimated: w.estimated ?? false)
    }

    /// One syringe per agent: locked agents show a countdown, others what's left.
    var labelItems: [SyringeIcon.Item] {
        guard let snap = snapshot else { return [] }
        var items: [(SyringeIcon.Item, Double)] = []  // (item, urgency: lower = more urgent)
        var seen = Set<String>()
        for p in snap.providers {
            seen.insert(p.id)
            let letter = SyringeIcon.letter(for: p.id)
            if let w = waiting.filter({ $0.provider == p.id }).min(by: { $0.resumeAt < $1.resumeAt }) {
                let left = w.resumeAt - now.timeIntervalSince1970
                items.append((.init(provider: p.id, letter: letter, remaining: nil, locked: true, text: Fmt.duration(left)), -1))
            } else if let r = remaining(of: p) {
                let text = showPercent ? "\(r.estimated ? "~" : "")\(Int(r.pct.rounded()))%" : ""
                items.append((.init(provider: p.id, letter: letter, remaining: r.pct / 100, locked: false, text: text), r.pct))
            }
        }
        // Wrapper sessions with a custom --provider label.
        for w in waiting where !seen.contains(w.provider) {
            seen.insert(w.provider)
            let left = w.resumeAt - now.timeIntervalSince1970
            items.append((.init(provider: w.provider, letter: SyringeIcon.letter(for: w.provider), remaining: nil, locked: true,
                                text: Fmt.duration(left)), -1))
        }
        if !showAllAgents, let top = items.min(by: { $0.1 < $1.1 }) { return [top.0] }
        return items.map { $0.0 }
    }

    var labelImage: NSImage { SyringeIcon.label(labelItems) }

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

    var icon: NSImage {
        SyringeIcon.image(remaining: worst.map { (100 - $0.used) / 100 }, locked: isLocked)
    }

    // MARK: - Actions

    func refresh(network: Bool) {
        if network { SyringeIcon.reloadCustomIcons() }  // pick up newly added badge art
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

    /// GUI apps get a bare PATH; borrow the login shell's so `codex` etc. resolve.
    private func environmentPath() -> String {
        if let p = shellPath { return p }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var path = "/opt/homebrew/bin:/usr/local/bin:\(home)/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // Interactive login shell so ~/.zshrc (nvm, npm prefixes, …) is applied too;
        // markers keep any prompt/banner noise out of the result.
        proc.arguments = ["-ilc", "printf '__MORPHINE_PATH__%s__END__' \"$PATH\""]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        proc.standardInput = FileHandle.nullDevice
        let done = DispatchSemaphore(value: 0)
        proc.terminationHandler = { _ in done.signal() }
        if (try? proc.run()) != nil {
            // Never hang the app on a slow or interactive ~/.zshrc.
            if done.wait(timeout: .now() + 4) == .timedOut {
                proc.terminate()
                shellPath = path
                return path
            }
            let raw = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            if let a = raw.range(of: "__MORPHINE_PATH__"), let b = raw.range(of: "__END__", range: a.upperBound..<raw.endIndex) {
                let out = String(raw[a.upperBound..<b.lowerBound])
                if !out.isEmpty { path = out + ":" + path }
            }
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
        var envVars = ProcessInfo.processInfo.environment
        envVars["PATH"] = environmentPath()
        let env = envVars  // immutable copy for the background queue
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
