import AppKit
import SwiftUI

struct PanelView: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if !store.waiting.isEmpty { lockoutSection }
            Divider()
            providersSection
            Divider()
            controls
            if let err = store.lastError {
                Text(err).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .frame(width: 340)
    }

    // MARK: header

    private var header: some View {
        HStack(alignment: .center, spacing: 8) {
            Image(nsImage: store.icon).renderingMode(.original)
            VStack(alignment: .leading, spacing: 1) {
                Text("Morphine").font(.headline)
                Text(statusLine).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text("v\(store.snapshot?.version ?? "–")").font(.caption2).foregroundStyle(.tertiary)
        }
    }

    private var statusLine: String {
        if store.isLocked {
            return L.t("\(store.waiting.count) agent(s) sedated — waiting for the window to refill",
                       "\(store.waiting.count)개 에이전트 진정 중 — 사용량 충전 대기")
        }
        return L.t("Awake · locks out below \(fmtPct(store.threshold)) left",
                   "작동 중 · 남은 양 \(fmtPct(store.threshold)) 미만이면 대기")
    }

    // MARK: lockout

    private var lockoutSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(L.t("Lockout", "대기 중 (lockout)"), systemImage: "lock.fill")
                .font(.subheadline.weight(.semibold))
            ForEach(store.waiting) { w in
                let total = max(1, w.resumeAt - w.startedAt)
                let left = max(0, w.resumeAt - store.now.timeIntervalSince1970)
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(displayName(w.provider)).font(.callout.weight(.medium))
                        Spacer()
                        Text(Fmt.duration(left)).font(.callout.monospacedDigit())
                    }
                    ProgressView(value: min(1, (total - left) / total))
                    Text(lockoutDetail(w))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Button {
                store.resumeNow()
            } label: {
                Label(L.t("Wake now", "지금 바로 깨우기"), systemImage: "bolt.fill")
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.12)))
    }

    private func lockoutDetail(_ w: Waiter) -> String {
        var parts: [String] = [L.t("refill ~\(Fmt.clock(w.resumeAt))", "충전 예상 \(Fmt.clock(w.resumeAt))")]
        if let n = w.nextCheckAt {
            parts.append(L.t("next check \(Fmt.clock(n))", "다음 확인 \(Fmt.clock(n))"))
        }
        if let r = w.reason { parts.append(r) }
        if let c = w.cwd { parts.append(URL(fileURLWithPath: c).lastPathComponent) }
        return parts.joined(separator: " · ")
    }

    // MARK: providers

    private var providersSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(store.visibleProviders) { p in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        badge(p.id)
                        Text(p.name).font(.callout.weight(.medium))
                        if p.economyOn == true {
                            Text(L.t("saving", "절약 중")).font(.caption2)
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(Capsule().fill(Color.green.opacity(0.2)))
                        }
                        if p.preemptive == false {
                            Text(L.t("wrapper", "래퍼")).font(.caption2)
                                .padding(.horizontal, 4).background(Capsule().stroke(.secondary))
                        }
                        Spacer()
                        if let src = p.source, !src.isEmpty, !p.windows.isEmpty {
                            Text(src).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    if p.windows.isEmpty {
                        Text(p.note ?? p.error ?? "–").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    ForEach(p.windows) { w in windowRow(w) }
                    if !p.windows.isEmpty, let note = p.note {
                        Text(note).font(.caption2).foregroundStyle(.tertiary).lineLimit(2)
                    }
                }
            }
            if store.snapshot == nil {
                Text(L.t("Loading…", "불러오는 중…")).font(.caption).foregroundStyle(.secondary)
            } else if store.visibleProviders.isEmpty {
                Text(L.t("No agent usage yet — use Claude Code or Codex once.",
                         "아직 사용량이 없어요 — Claude Code나 Codex를 한 번 사용해 보세요."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !store.hiddenProviders.isEmpty {
                Text(L.t("Not connected: ", "연결 안 됨: ") + store.hiddenProviders.map(\.name).joined(separator: ", "))
                    .font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    private func windowRow(_ w: UsageWindow) -> some View {
        let used = w.used(at: store.now)
        return HStack(spacing: 8) {
            Text(w.name).font(.caption.monospacedDigit()).frame(width: 22, alignment: .leading)
            ProgressView(value: max(0, 100 - (used ?? 100)) / 100).tint(color(for: used))
            Text(used.map { "\(w.estimated == true ? "~" : "")\(Int(max(0, 100 - $0).rounded()))%" } ?? "?")
                .font(.caption.monospacedDigit()).frame(width: 40, alignment: .trailing)
            Text(resetText(w)).font(.caption2).foregroundStyle(.secondary).frame(width: 70, alignment: .trailing)
        }
    }

    private func resetText(_ w: UsageWindow) -> String {
        guard let r = w.resetsAt, r > store.now.timeIntervalSince1970 else { return "" }
        return Fmt.clock(r)
    }

    private func color(for used: Double?) -> Color {
        guard let u = used else { return .gray }
        if 100 - u < store.threshold { return .red }
        if 100 - u < 15 { return .orange }
        return store.runningColor
    }

    /// Same lettered badge the menu bar uses (C Claude · O Codex · G Gemini · X Grok).
    private func badge(_ id: String) -> some View {
        let locked = store.waiting.contains { $0.provider == id }
        return Group {
            if let art = SyringeIcon.customIcon(for: id) {
                Image(nsImage: art).resizable().renderingMode(.template).interpolation(.high)
                    .foregroundStyle(locked ? Color.gray : store.runningColor)
                    .frame(width: 15, height: 15)
            } else {
                Text(SyringeIcon.letter(for: id))
                    .font(.system(size: 9, weight: .heavy))
                    .foregroundStyle(.white)
                    .frame(width: 15, height: 15)
                    .background(Circle().fill(locked ? Color.gray : store.runningColor))
            }
        }
    }

    // MARK: controls

    private var controls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(L.t("Sedate below", "대기 시작 기준 (남은 양)"))
                Spacer()
                Picker("", selection: Binding(get: { store.threshold }, set: { store.setThreshold($0) })) {
                    ForEach([1.0, 2.0, 3.0, 5.0, 10.0], id: \.self) { Text(fmtPct($0)).tag($0) }
                }
                .labelsHidden()
                .frame(width: 80)
            }
            HStack {
                Text(L.t("Economy mode below", "절약 모드 시작 (남은 양)"))
                Spacer()
                Picker("", selection: Binding(get: { store.economyBelow }, set: { store.setEconomy($0) })) {
                    Text(L.t("Off", "끄기")).tag(0.0)
                    ForEach([20.0, 30.0, 40.0, 50.0, 60.0, 70.0], id: \.self) { Text(fmtPct($0)).tag($0) }
                }
                .labelsHidden()
                .frame(width: 80)
            }
            HStack {
                Text(L.t("Menu bar colour", "메뉴바 색"))
                Spacer()
                if store.runColor == .custom {
                    ColorPicker("", selection: $store.customRunColor, supportsOpacity: false)
                        .labelsHidden()
                }
                Picker("", selection: $store.runColor) {
                    ForEach(RunColor.allCases) { c in
                        Label {
                            Text(c.title)
                        } icon: {
                            Image(nsImage: swatch(c))
                        }
                        .tag(c)
                    }
                }
                .labelsHidden()
                .frame(width: 170)
            }
            Toggle(L.t("Keep Mac awake while waiting", "대기 중 Mac 잠자기 방지"),
                   isOn: Binding(get: { store.snapshot?.keepAwake ?? true }, set: { store.setKeepAwake($0) }))
            Toggle(L.t("Show remaining % in menu bar", "메뉴바에 남은 % 표시"), isOn: $store.showPercent)
            Toggle(L.t("Show every agent in menu bar", "메뉴바에 모든 도구 표시 (끄면 가장 급한 것만)"),
                   isOn: $store.showAllAgents)
            Toggle(L.t("Launch at login", "로그인 시 실행"),
                   isOn: Binding(get: { store.launchAtLogin }, set: { store.launchAtLogin = $0 }))
            HStack {
                Button(L.t("Refresh", "새로고침")) { store.refresh(network: true) }
                Button(L.t("Settings file…", "설정 파일…")) { store.openConfig() }
                Spacer()
                Button(L.t("Quit", "종료")) { NSApplication.shared.terminate(nil) }
            }
            .controlSize(.small)
        }
        .toggleStyle(.switch)
        .controlSize(.small)
    }

    /// Small colour dot for the picker menu.
    private func swatch(_ c: RunColor) -> NSImage {
        let img = NSImage(size: NSSize(width: 12, height: 12), flipped: false) { r in
            c.nsColor(dark: SyringeIcon.isDark).set()
            NSBezierPath(ovalIn: r.insetBy(dx: 1, dy: 1)).fill()
            return true
        }
        img.isTemplate = false
        return img
    }

    private func fmtPct(_ v: Double) -> String {
        v == v.rounded() ? "\(Int(v))%" : String(format: "%.1f%%", v)
    }

    private func displayName(_ id: String) -> String {
        store.snapshot?.providers.first { $0.id == id }?.name ?? id
    }
}
