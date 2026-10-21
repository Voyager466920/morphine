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
            Image(nsImage: store.icon).renderingMode(.template)
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
                    Text(L.t("resumes \(Fmt.clock(w.resumeAt))", "\(Fmt.clock(w.resumeAt))에 재개")
                         + (w.reason.map { " · \($0)" } ?? "")
                         + (w.cwd.map { " · \(URL(fileURLWithPath: $0).lastPathComponent)" } ?? ""))
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

    // MARK: providers

    private var providersSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(store.snapshot?.providers ?? []) { p in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(p.name).font(.callout.weight(.medium))
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
            }
        }
    }

    private func windowRow(_ w: UsageWindow) -> some View {
        let used = w.used(at: store.now)
        return HStack(spacing: 8) {
            Text(w.name).font(.caption.monospacedDigit()).frame(width: 22, alignment: .leading)
            ProgressView(value: (used ?? 0) / 100).tint(color(for: used))
            Text(used.map { "\(w.estimated == true ? "~" : "")\(Int($0.rounded()))%" } ?? "?")
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
        if u >= 85 { return .orange }
        return .green
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
            Toggle(L.t("Keep Mac awake while waiting", "대기 중 Mac 잠자기 방지"),
                   isOn: Binding(get: { store.snapshot?.keepAwake ?? true }, set: { store.setKeepAwake($0) }))
            Toggle(L.t("Show % in menu bar", "메뉴바에 % 표시"), isOn: $store.showPercent)
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

    private func fmtPct(_ v: Double) -> String {
        v == v.rounded() ? "\(Int(v))%" : String(format: "%.1f%%", v)
    }

    private func displayName(_ id: String) -> String {
        store.snapshot?.providers.first { $0.id == id }?.name ?? id
    }
}
