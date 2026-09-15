import SwiftUI

struct InboxView: View {
    @EnvironmentObject var api: API
    @State private var filter: Filter = .all
    @State private var search = ""

    enum Filter: String, CaseIterable { case all = "All", waiting = "Needs you", bot = "Bot on", outreach = "Outreach" }

    var shown: [DMThread] {
        api.threads.filter { t in
            switch filter {
            case .all: return true
            case .waiting: return t.pending != nil
            case .bot: return t.mode != "off"
            case .outreach: return t.source == "outreach"
            }
        }.filter { search.isEmpty || $0.username.localizedCaseInsensitiveContains(search) || $0.display.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        NavigationStack {
            List {
                if let s = api.status {
                    Text(s.text.replacingOccurrences(of: "[IG] ", with: ""))
                        .font(.footnote).foregroundStyle(.secondary)
                        .listRowBackground(Color.clear)
                }
                ForEach(shown) { t in
                    NavigationLink(value: t.key) { ThreadRow(t: t) }
                }
            }
            .navigationTitle("Inbox")
            .navigationDestination(for: String.self) { key in ThreadView(key: key) }
            .searchable(text: $search, prompt: "@username")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Picker("Filter", selection: $filter) {
                        ForEach(Filter.allCases, id: \.self) { Text($0.rawValue) }
                    }.pickerStyle(.menu)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await api.runPass(); try? await Task.sleep(for: .seconds(3)); await api.refresh() } } label: {
                        Image(systemName: "arrow.triangle.2.circlepath")
                    }
                }
            }
            .refreshable { await api.refresh() }
            .overlay {
                if api.threads.isEmpty {
                    ContentUnavailableView(api.error == nil ? "No threads yet" : "Can't reach the Mac",
                                           systemImage: api.error == nil ? "tray" : "wifi.slash",
                                           description: Text(api.error ?? "Pull to refresh once the engine has seeded."))
                }
            }
        }
        .task { await api.refresh() }
    }
}

struct ThreadRow: View {
    let t: DMThread
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(t.display).font(.headline).lineLimit(1)
                if t.display.lowercased() != t.username.lowercased() {
                    Text("@\(t.username)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Text(t.activityDate.short).font(.caption2).foregroundStyle(.secondary)
            }
            HStack(spacing: 6) {
                ModeBadge(mode: t.mode)
                if t.pending != nil {
                    Label("draft waiting", systemImage: "exclamationmark.circle.fill")
                        .font(.caption2).foregroundStyle(.orange)
                }
                if t.source == "outreach" {
                    Text(t.playbook == "dj_pitch" ? "pitch" : "reach").font(.caption2).foregroundStyle(.secondary)
                }
                if t.followups_sent > 0 {
                    Text("nudged \(t.followups_sent)x").font(.caption2).foregroundStyle(.secondary)
                }
            }
            if let m = t.last {
                Text((m.isMe ? "You: " : "") + m.text)
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .padding(.vertical, 2)
    }
}
