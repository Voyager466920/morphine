import AppKit

/// Menu bar art. Each agent gets a syringe "injecting" into a lettered badge
/// (C = Claude, O = Codex/OpenAI, G = Gemini, X = Grok/xAI, A = Antigravity),
/// followed by how much of its usage window is LEFT. Blue = running,
/// gray with a pause mark = waiting out a cooldown (countdown instead of %).
enum SyringeIcon {
    static let running = NSColor.systemBlue
    static let waiting = NSColor.systemGray

    struct Item: Equatable {
        var provider: String
        var letter: String
        var remaining: Double?   // 0...1, nil = unknown
        var locked: Bool
        var text: String         // "97%", "~40%", "1:12:04", or "" to hide
    }

    /// Optional user-supplied badge art: ~/.morphine/icons/<provider>.png (any square image).
    /// Drawn inside the badge circle instead of the letter. Morphine ships no logos.
    private static var customIcons: [String: NSImage?] = [:]

    static func customIcon(for provider: String) -> NSImage? {
        if let cached = customIcons[provider] { return cached }
        let home = ProcessInfo.processInfo.environment["MORPHINE_HOME"]
            ?? (FileManager.default.homeDirectoryForCurrentUser.path + "/.morphine")
        var img: NSImage?
        for ext in ["png", "jpg", "jpeg", "pdf", "icns"] {
            if let i = NSImage(contentsOfFile: "\(home)/icons/\(provider).\(ext)") { img = i; break }
        }
        customIcons[provider] = .some(img)
        return img
    }

    static func reloadCustomIcons() { customIcons.removeAll() }

    static func letter(for provider: String) -> String {
        switch provider {
        case "claude": return "C"
        case "codex": return "O"
        case "gemini": return "G"
        case "grok": return "X"
        case "antigravity": return "A"
        default: return String(provider.prefix(1)).uppercased()
        }
    }

    // MARK: - Single syringe (panel header, fallback)

    static func image(remaining: Double?, locked: Bool) -> NSImage {
        let img = NSImage(size: NSSize(width: 22, height: 16), flipped: false) { _ in
            (locked ? waiting : running).set()
            drawSyringe(remaining: remaining, locked: locked)
            return true
        }
        img.isTemplate = false
        return img
    }

    // MARK: - Menu bar label

    private static let height: CGFloat = 18
    private static let scale: CGFloat = 0.8
    private static let badgeRadius: CGFloat = 4.9
    private static let gap: CGFloat = 7

    private static func textAttrs(_ color: NSColor) -> [NSAttributedString.Key: Any] {
        [.font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold), .foregroundColor: color]
    }

    /// Side of the box a user-supplied logo fills (nearly the full bar height).
    private static let logoSide: CGFloat = 14

    private static func badgeWidth(_ item: Item) -> CGFloat {
        customIcon(for: item.provider) != nil ? logoSide : badgeRadius * 2
    }

    /// Logo silhouette recoloured to `color` (blue running / gray waiting), so it
    /// reads on light and dark menu bars alike.
    private static func tinted(_ art: NSImage, _ color: NSColor, side: CGFloat) -> NSImage {
        NSImage(size: NSSize(width: side, height: side), flipped: false) { r in
            art.draw(in: r, from: .zero, operation: .sourceOver, fraction: 1)
            color.set()
            r.fill(using: .sourceAtop)
            return true
        }
    }

    private static func itemWidth(_ item: Item) -> CGFloat {
        let art: CGFloat = 21.8 * scale - 0.6 + badgeWidth(item)
        guard !item.text.isEmpty else { return art }
        return art + 3 + ceil((item.text as NSString).size(withAttributes: textAttrs(.black)).width)
    }

    static func label(_ items: [Item]) -> NSImage {
        guard !items.isEmpty else { return image(remaining: nil, locked: false) }
        let width = items.map(itemWidth).reduce(0, +) + gap * CGFloat(items.count - 1)
        let img = NSImage(size: NSSize(width: ceil(width), height: height), flipped: false) { _ in
            var x: CGFloat = 0
            for item in items {
                let color = item.locked ? waiting : running
                color.set()

                // Syringe, scaled into the 18pt-tall bar and vertically centred.
                NSGraphicsContext.saveGraphicsState()
                let t = NSAffineTransform()
                t.translateX(by: x, yBy: (height - 16 * scale) / 2)
                t.scale(by: scale)
                t.concat()
                drawSyringe(remaining: item.remaining, locked: item.locked)
                NSGraphicsContext.restoreGraphicsState()

                // Badge: the needle tip (x + 21.8 * scale) pokes just inside it.
                let tip = x + 21.8 * scale - 0.6
                let badgeRight: CGFloat
                if let art = customIcon(for: item.provider) {
                    // User-supplied logo, filling its box, tinted to the state colour.
                    let box = NSRect(x: tip, y: (height - logoSide) / 2, width: logoSide, height: logoSide)
                    tinted(art, color, side: logoSide).draw(in: box)
                    badgeRight = box.maxX
                } else {
                    let cx = tip + badgeRadius
                    let badge = NSRect(x: cx - badgeRadius, y: height / 2 - badgeRadius,
                                       width: badgeRadius * 2, height: badgeRadius * 2)
                    NSBezierPath(ovalIn: badge).fill()
                    let letterAttrs: [NSAttributedString.Key: Any] = [
                        .font: NSFont.systemFont(ofSize: 7, weight: .heavy), .foregroundColor: NSColor.white,
                    ]
                    let ls = (item.letter as NSString).size(withAttributes: letterAttrs)
                    (item.letter as NSString).draw(at: NSPoint(x: cx - ls.width / 2, y: height / 2 - ls.height / 2 + 0.2),
                                                   withAttributes: letterAttrs)
                    badgeRight = badge.maxX
                }

                var next = badgeRight
                if !item.text.isEmpty {
                    let attrs = textAttrs(color)
                    let ts = (item.text as NSString).size(withAttributes: attrs)
                    (item.text as NSString).draw(at: NSPoint(x: next + 3, y: (height - ts.height) / 2), withAttributes: attrs)
                    next += 3 + ceil(ts.width)
                }
                x = next + gap
            }
            return true
        }
        img.isTemplate = false
        img.accessibilityDescription = items.map { "\($0.letter) \($0.text)" }.joined(separator: ", ")
        return img
    }

    // MARK: - Shared drawing (22 × 16 unit space, current colour already set)

    private static func drawSyringe(remaining: Double?, locked: Bool) {
        let level = remaining.map { max(0, min(1, $0)) }

        // Plunger: thumb rest, rod, finger flange.
        NSBezierPath(roundedRect: NSRect(x: 0.5, y: 3.6, width: 1.3, height: 8.8), xRadius: 0.5, yRadius: 0.5).fill()
        NSBezierPath(rect: NSRect(x: 1.8, y: 7.35, width: 3.4, height: 1.3)).fill()
        NSBezierPath(roundedRect: NSRect(x: 5.0, y: 2.6, width: 1.2, height: 10.8), xRadius: 0.5, yRadius: 0.5).fill()

        // Barrel.
        let barrel = NSRect(x: 6.2, y: 4.5, width: 10.8, height: 7.0)
        let outline = NSBezierPath(roundedRect: barrel, xRadius: 1.4, yRadius: 1.4)
        outline.lineWidth = 1.2
        outline.stroke()

        // Hub + needle.
        NSBezierPath(rect: NSRect(x: 17.0, y: 6.7, width: 1.6, height: 2.6)).fill()
        let needle = NSBezierPath()
        needle.move(to: NSPoint(x: 18.6, y: 8))
        needle.line(to: NSPoint(x: 21.8, y: 8))
        needle.lineWidth = 0.9
        needle.stroke()

        let inner = barrel.insetBy(dx: 1.25, dy: 1.25)
        if locked {
            NSBezierPath(rect: NSRect(x: inner.midX - 2.0, y: inner.minY + 0.4, width: 1.3, height: inner.height - 0.8)).fill()
            NSBezierPath(rect: NSRect(x: inner.midX + 0.7, y: inner.minY + 0.4, width: 1.3, height: inner.height - 0.8)).fill()
        } else if let level = level {
            // Dose sits at the needle end; the stopper rides on top of it.
            let w = inner.width * CGFloat(level)
            if w > 0.3 {
                NSBezierPath(rect: NSRect(x: inner.maxX - w, y: inner.minY, width: w, height: inner.height)).fill()
            }
            let stopperX = max(inner.minX, inner.maxX - w - 1.0)
            NSBezierPath(rect: NSRect(x: stopperX, y: inner.minY - 0.2, width: 0.9, height: inner.height + 0.4)).fill()
        } else {
            NSBezierPath(rect: NSRect(x: inner.midX - 2.5, y: inner.midY - 0.5, width: 5, height: 1.0)).fill()
        }
    }
}
