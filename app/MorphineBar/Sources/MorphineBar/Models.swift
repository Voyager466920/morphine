import Foundation

/// Mirrors `morphine status --json` (decoded with .convertFromSnakeCase).
struct Snapshot: Decodable {
    var version: String
    var generatedAt: Double
    var thresholdPct: Double?
    var windows: [String]?
    var keepAwake: Bool?
    var providers: [Provider]
    var waiting: [Waiter]
}

struct Provider: Decodable, Identifiable {
    var id: String
    var name: String
    var ok: Bool
    var error: String?
    var note: String?
    var source: String?
    var preemptive: Bool?
    var windows: [UsageWindow]
}

struct UsageWindow: Decodable, Identifiable {
    var name: String
    var usedPct: Double?
    var resetsAt: Double?
    var estimated: Bool?
    var usedNow: Double?
    var remaining: Double?
    var id: String { name }

    /// Used % at `date`, treating a passed reset time as a fresh window.
    func used(at date: Date) -> Double? {
        if let r = resetsAt, r <= date.timeIntervalSince1970 { return 0 }
        return usedNow ?? usedPct
    }
}

struct Waiter: Decodable, Identifiable {
    var id: String
    var provider: String
    var startedAt: Double
    var resumeAt: Double
    var reason: String?
    var cwd: String?
    var window: String?
}

enum Fmt {
    static func duration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        if h >= 24 { return "\(h / 24)d \(h % 24)h" }
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, sec) }
        return String(format: "%d:%02d", m, sec)
    }

    static func clock(_ epoch: Double) -> String {
        let d = Date(timeIntervalSince1970: epoch)
        let f = DateFormatter()
        f.dateFormat = Calendar.current.isDateInToday(d) ? "HH:mm" : "M/d HH:mm"
        return f.string(from: d)
    }
}

/// Tiny two-language string table (Korean when the system prefers it).
enum L {
    static let ko = Locale.preferredLanguages.first?.hasPrefix("ko") ?? false

    static func t(_ en: String, _ kr: String) -> String { ko ? kr : en }
}
