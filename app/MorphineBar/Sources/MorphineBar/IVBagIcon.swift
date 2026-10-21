import AppKit

/// Draws the menu bar icon: an IV drip bag whose fluid level is the remaining
/// usage. In lockout the bag shows a padlock instead (the PCA pump's lockout).
enum IVBagIcon {
    private static var cache: [String: NSImage] = [:]

    /// - Parameters:
    ///   - remaining: 0...1 share of the most-drained window that is left; nil = unknown.
    ///   - locked: an agent is waiting out a cooldown.
    static func image(remaining: Double?, locked: Bool) -> NSImage {
        let level = remaining.map { (max(0, min(1, $0)) * 20).rounded() / 20 } // 5% steps
        let key = "\(level ?? -1)-\(locked)"
        if let img = cache[key] { return img }

        let size = NSSize(width: 18, height: 18)
        let img = NSImage(size: size, flipped: false) { _ in
            NSColor.black.set()
            let line: CGFloat = 1.3

            // Hanger tab at the top.
            let hanger = NSBezierPath(roundedRect: NSRect(x: 7, y: 15.2, width: 4, height: 2.3),
                                      xRadius: 1.1, yRadius: 1.1)
            hanger.lineWidth = 1.1
            hanger.stroke()

            // Bag body.
            let body = NSRect(x: 3.6, y: 4.6, width: 10.8, height: 10.6)
            let bag = NSBezierPath(roundedRect: body, xRadius: 2.8, yRadius: 2.8)
            bag.lineWidth = line
            bag.stroke()

            // Outlet neck + drip line.
            NSBezierPath(rect: NSRect(x: 8.0, y: 2.6, width: 2.0, height: 2.0)).fill()
            let drip = NSBezierPath()
            drip.move(to: NSPoint(x: 9, y: 2.6))
            drip.line(to: NSPoint(x: 9, y: 0.3))
            drip.lineWidth = 1.0
            drip.stroke()

            if locked {
                // Padlock inside the bag.
                let shackle = NSBezierPath()
                shackle.appendArc(withCenter: NSPoint(x: 9, y: 10.6), radius: 1.9,
                                  startAngle: 0, endAngle: 180)
                shackle.move(to: NSPoint(x: 7.1, y: 10.6))
                shackle.line(to: NSPoint(x: 7.1, y: 9.4))
                shackle.move(to: NSPoint(x: 10.9, y: 10.6))
                shackle.line(to: NSPoint(x: 10.9, y: 9.4))
                shackle.lineWidth = 1.1
                shackle.stroke()
                NSBezierPath(roundedRect: NSRect(x: 6.1, y: 6.2, width: 5.8, height: 3.9),
                             xRadius: 0.8, yRadius: 0.8).fill()
            } else if let level = level {
                // Fluid level, clipped to the inside of the bag.
                let inner = body.insetBy(dx: line + 0.4, dy: line + 0.4)
                NSGraphicsContext.saveGraphicsState()
                NSBezierPath(roundedRect: inner, xRadius: 1.6, yRadius: 1.6).addClip()
                let h = inner.height * CGFloat(level)
                NSBezierPath(rect: NSRect(x: inner.minX, y: inner.minY, width: inner.width, height: h)).fill()
                NSGraphicsContext.restoreGraphicsState()
            } else {
                // Unknown: a question-free neutral dash.
                NSBezierPath(rect: NSRect(x: 6.5, y: 9.4, width: 5, height: 1.2)).fill()
            }
            return true
        }
        img.isTemplate = true
        img.accessibilityDescription = locked ? "Morphine: in lockout" : "Morphine"
        cache[key] = img
        return img
    }
}
