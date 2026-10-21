import AppKit

/// Draws the menu bar icon: a syringe whose dose is the remaining usage.
/// Blue while the agents run; gray with a pause mark while one is in lockout.
enum SyringeIcon {
    private static var cache: [String: NSImage] = [:]

    static let running = NSColor.systemBlue
    static let waiting = NSColor.systemGray

    /// - Parameters:
    ///   - remaining: 0...1 share of the most-drained window that is left; nil = unknown.
    ///   - locked: an agent is waiting out a cooldown.
    static func image(remaining: Double?, locked: Bool) -> NSImage {
        let level = remaining.map { (max(0, min(1, $0)) * 20).rounded() / 20 } // 5% steps
        let key = "\(level ?? -1)-\(locked)"
        if let img = cache[key] { return img }

        let size = NSSize(width: 22, height: 16)
        let img = NSImage(size: size, flipped: false) { _ in
            (locked ? waiting : running).set()

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
                // Pause mark inside the barrel.
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
            return true
        }
        img.isTemplate = false
        img.accessibilityDescription = locked ? "Morphine: in lockout" : "Morphine: running"
        cache[key] = img
        return img
    }
}
