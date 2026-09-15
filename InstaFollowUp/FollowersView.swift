import SwiftUI

struct FollowerUser: Codable, Identifiable, Hashable {
    var username: String
    var full_name: String
    var `private`: Bool?
    var at: Double?
    var id: String { username }
}

struct FollowersStatus: Codable {
    struct Point: Codable, Hashable { var at: Double; var followers: Int; var following: Int; var new: Int; var unfollowed: Int; var gone: Int }
    var followers: Int
    var following: Int
    var not_back: Int
    var fans: Int
    var new: [FollowerUser]
    var unfollowed: [FollowerUser]
    var gone: [FollowerUser]
    var not_back_list: [FollowerUser]
    var history: [Point]
    var last: Double?
}

extension API {
    func raw(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: baseURL.trimmingCharacters(in: .whitespaces) + path) else { throw Failure(message: "bad base URL") }
        var req = URLRequest(url: url, timeoutInterval: 90)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "X-Token")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code >= 300 {
            let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["error"] ?? "HTTP \(code)"
            throw Failure(message: msg)
        }
        return data
    }
    func loadFollowers() async -> FollowersStatus? {
        var out: FollowersStatus?
        await run { out = try JSONDecoder().decode(FollowersStatus.self, from: try await self.raw("GET", "/followers")) }
        return out
    }
    func followersRun() async { await run { _ = try await self.raw("POST", "/followers/run") } }
}

struct FollowersView: View {
    @EnvironmentObject var api: API
    @State private var s: FollowersStatus?
    @State private var section = "new"

    var body: some View {
        List {
            if let s {
                Section {
                    HStack {
                        stat("Followers", s.followers); stat("Following", s.following); stat("Not back", s.not_back); stat("Fans", s.fans)
                    }
                    if let last = s.last { Text("checked \(Date(timeIntervalSince1970: last).short)").font(.caption).foregroundStyle(.secondary) }
                }
                Picker("", selection: $section) {
                    Text("New").tag("new"); Text("Unfollowed").tag("unfollowed"); Text("Gone").tag("gone"); Text("Not back").tag("not_back")
                }.pickerStyle(.segmented).listRowBackground(Color.clear)
                Section(footer: Text(footer)) {
                    let rows = section == "new" ? s.new : section == "unfollowed" ? s.unfollowed : section == "gone" ? s.gone : s.not_back_list
                    if rows.isEmpty { Text("Nobody yet").foregroundStyle(.secondary) }
                    ForEach(rows) { u in
                        HStack {
                            VStack(alignment: .leading) {
                                Text("@\(u.username)").font(.body)
                                if !u.full_name.isEmpty { Text(u.full_name).font(.caption).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            if let at = u.at { Text(Date(timeIntervalSince1970: at).short).font(.caption2).foregroundStyle(.secondary) }
                            Link(destination: URL(string: "https://www.instagram.com/\(u.username)/")!) { Image(systemName: "arrow.up.right.square") }
                        }
                    }
                }
            } else {
                ContentUnavailableView("No snapshot yet", systemImage: "person.2", description: Text("Run a check. The first one is the baseline; changes show from the second on."))
            }
        }
        .navigationTitle("Followers")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Check now") { Task { await api.followersRun() } } } }
        .refreshable { s = await api.loadFollowers() }
        .task { s = await api.loadFollowers() }
    }

    var footer: String {
        switch section {
        case "gone": return "Accounts that were following you and whose profile page no longer exists (deleted or deactivated)."
        case "unfollowed": return "Still on Instagram, no longer following you."
        case "not_back": return "You follow them, they don't follow you."
        default: return "Started following you since the last check."
        }
    }

    func stat(_ label: String, _ n: Int) -> some View {
        VStack { Text("\(n)").font(.title3.bold()); Text(label).font(.caption2).foregroundStyle(.secondary) }.frame(maxWidth: .infinity)
    }
}
