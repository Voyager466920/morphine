import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Menu bar only — no Dock icon, even when run outside the .app bundle.
        NSApp.setActivationPolicy(.accessory)
    }
}

@main
struct MorphineBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var store = Store()

    var body: some Scene {
        MenuBarExtra {
            PanelView().environmentObject(store)
        } label: {
            MenuLabel(store: store)
        }
        .menuBarExtraStyle(.window)
    }
}

struct MenuLabel: View {
    @ObservedObject var store: Store

    var body: some View {
        HStack(spacing: 3) {
            Image(nsImage: store.icon).renderingMode(.template)
            let title = store.menuTitle
            if !title.isEmpty {
                Text(title).monospacedDigit()
            }
        }
    }
}
