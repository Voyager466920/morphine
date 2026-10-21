import AppKit
import SwiftUI

/// Selectable colour for the running state. Stored in UserDefaults ("runColor";
/// a custom colour is kept as "#RRGGBB" in "runColorCustom").
enum RunColor: String, CaseIterable, Identifiable {
    case system      // macOS system blue
    case contrast    // blue that brightens on dark menu bars, deepens on light ones
    case sky         // light sky blue (for dark backgrounds)
    case purple
    case green
    case orange
    case label       // the menu bar's own text colour (white/black)
    case custom

    var id: String { rawValue }

    static var current: RunColor {
        RunColor(rawValue: UserDefaults.standard.string(forKey: "runColor") ?? "") ?? .system
    }

    var title: String {
        switch self {
        case .system: return L.t("Blue", "파란색")
        case .contrast: return L.t("Blue (auto contrast)", "파란색 (배경에 맞춤)")
        case .sky: return L.t("Light blue", "연한 하늘색")
        case .purple: return L.t("Purple", "보라색")
        case .green: return L.t("Green", "초록색")
        case .orange: return L.t("Orange", "주황색")
        case .label: return L.t("White / black (like the menu bar)", "흰색/검정 (메뉴바 글자색)")
        case .custom: return L.t("Custom…", "직접 선택…")
        }
    }

    func nsColor(dark: Bool) -> NSColor {
        switch self {
        case .system: return .systemBlue
        case .contrast:
            return dark ? NSColor(srgbRed: 0.42, green: 0.74, blue: 1.0, alpha: 1)
                        : NSColor(srgbRed: 0.0, green: 0.40, blue: 0.92, alpha: 1)
        case .sky: return NSColor(srgbRed: 0.55, green: 0.80, blue: 1.0, alpha: 1)
        case .purple: return .systemPurple
        case .green: return .systemGreen
        case .orange: return .systemOrange
        case .label: return dark ? .white : .black
        case .custom: return RunColor.customColor
        }
    }

    static var customColor: NSColor {
        get {
            let hex = UserDefaults.standard.string(forKey: "runColorCustom") ?? "#0A84FF"
            return NSColor(hex: hex) ?? .systemBlue
        }
        set { UserDefaults.standard.set(newValue.hexString, forKey: "runColorCustom") }
    }
}

extension NSColor {
    convenience init?(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }

    var hexString: String {
        guard let c = usingColorSpace(.sRGB) else { return "#0A84FF" }
        return String(format: "#%02X%02X%02X", Int(round(c.redComponent * 255)),
                      Int(round(c.greenComponent * 255)), Int(round(c.blueComponent * 255)))
    }
}
