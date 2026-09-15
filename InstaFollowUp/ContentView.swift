import SwiftUI

struct ContentView: View {
    @EnvironmentObject var api: API

    var body: some View {
        TabView {
            InboxView()
                .tabItem { Label("Inbox", systemImage: "bubble.left.and.bubble.right") }
                .badge(api.status?.pending ?? 0)
            LeadsView()
                .tabItem { Label("Scout", systemImage: "binoculars") }
            OutreachView()
                .tabItem { Label("Outreach", systemImage: "paperplane") }
            PlaybooksView()
                .tabItem { Label("Playbooks", systemImage: "text.book.closed") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .tint(.pink)
    }
}

struct ModeBadge: View {
    let mode: String
    var body: some View {
        Text(Mode(rawValue: mode)?.label ?? mode)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(color.opacity(0.2), in: Capsule())
            .foregroundStyle(color)
    }
    var color: Color {
        switch mode {
        case "auto": return .green
        case "approve": return .orange
        default: return .secondary
        }
    }
}

extension Date {
    var short: String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: self, relativeTo: Date())
    }
}
