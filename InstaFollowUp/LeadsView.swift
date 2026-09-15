import SwiftUI

struct Lead: Codable, Identifiable, Hashable {
    struct Post: Codable, Hashable { var thumb: String?; var caption: String? }
    var username: String
    var full_name: String?
    var biography: String?
    var category: String?
    var followers: Int?
    var following: Int?
    var posts: Int?
    var pic: String?
    var external_url: String?
    var recent: [Post]?
    var lane: String
    var market: String
    var reason: String
    var status: String
    var score: Double?
    var note: String?
    var id: String { username }
}

struct TasteSummary: Codable { var decisions: Int; var likes: [String]; var passes: [String] }

struct LeadsPayload: Codable {
    var leads: [Lead]
    var taste: [String: TasteSummary]
    var last_run: LastRun?
    var markets: [String]
    struct LastRun: Codable { var lane: String; var market: String; var at: Double; var added: Int; var throttled: Bool }
}

extension API {
    func loadLeads() async -> LeadsPayload? {
        var out: LeadsPayload?
        await run {
            let data = try await self.raw("GET", "/leads")
            out = try JSONDecoder().decode(LeadsPayload.self, from: data)
        }
        return out
    }
    func decide(_ username: String, like: Bool, note: String) async {
        await run { _ = try await self.raw("POST", "/leads/\(username)", body: ["decision": like ? "like" : "pass", "note": note]) }
    }
    func scoutRun(lane: String? = nil, market: String? = nil) async {
        var body: [String: Any] = [:]
        if let lane, let market { body = ["lane": lane, "market": market, "limit": 8] }
        await run { _ = try await self.raw("POST", "/scout/run", body: body) }
    }
}

struct LeadsView: View {
    @EnvironmentObject var api: API
    @State private var payload: LeadsPayload?
    @State private var lane = "business"
    @State private var market = "all"
    @State private var note = ""
    @State private var showAll = false

    var queue: [Lead] {
        (payload?.leads ?? []).filter { $0.lane == lane && (market == "all" || $0.market == market) && (showAll || $0.status == "new") }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Lane", selection: $lane) {
                    Text("DJ leads").tag("business")
                    Text("People").tag("people")
                }
                .pickerStyle(.segmented).padding(.horizontal).padding(.top, 6)
                HStack {
                    Picker("Market", selection: $market) {
                        Text("All markets").tag("all")
                        ForEach(payload?.markets ?? ["philly", "nj", "nyc", "charlotte"], id: \.self) { Text($0.uppercased()).tag($0) }
                    }.pickerStyle(.menu)
                    Spacer()
                    Toggle("Show decided", isOn: $showAll).toggleStyle(.switch).font(.caption)
                }
                .padding(.horizontal)
                if let t = payload?.taste[lane], t.decisions > 0 {
                    Text("\(t.decisions) decisions · likes: \(t.likes.prefix(6).map { $0.split(separator: ":").last.map(String.init) ?? $0 }.joined(separator: ", "))")
                        .font(.caption2).foregroundStyle(.secondary).padding(.horizontal).lineLimit(2)
                }
                if let first = queue.first {
                    LeadCard(lead: first, note: $note) { like in
                        Task {
                            await api.decide(first.username, like: like, note: note)
                            note = ""
                            payload = await api.loadLeads()
                        }
                    }
                    Text("\(queue.count) waiting").font(.caption).foregroundStyle(.secondary).padding(.bottom, 6)
                } else {
                    ContentUnavailableView {
                        Label("Nothing to review", systemImage: "binoculars")
                    } description: {
                        Text(payload == nil ? "Pull to load." : "Run the scout to fill the queue. It texts you when leads land.")
                    } actions: {
                        Button("Scout all markets now") { Task { await api.scoutRun() } }.buttonStyle(.borderedProminent)
                        if market != "all" { Button("Scout \(market.uppercased()) \(lane)") { Task { await api.scoutRun(lane: lane, market: market) } } }
                    }
                }
            }
            .navigationTitle("Scout")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Scout all markets now") { Task { await api.scoutRun() } }
                        ForEach(payload?.markets ?? [], id: \.self) { m in
                            Button("Scout \(m.uppercased()) · \(lane)") { Task { await api.scoutRun(lane: lane, market: m) } }
                        }
                        NavigationLink("Followers") { FollowersView() }
                    } label: { Image(systemName: "ellipsis.circle") }
                }
            }
            .refreshable { payload = await api.loadLeads() }
            .task { payload = await api.loadLeads() }
        }
    }
}

struct LeadCard: View {
    let lead: Lead
    @Binding var note: String
    let decide: (Bool) -> Void
    private let cols = [GridItem(.flexible(), spacing: 2), GridItem(.flexible(), spacing: 2), GridItem(.flexible(), spacing: 2)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    AsyncImage(url: URL(string: lead.pic ?? "")) { img in img.resizable().scaledToFill() } placeholder: { Color.secondary.opacity(0.2) }
                        .frame(width: 64, height: 64).clipShape(Circle())
                    VStack(alignment: .leading, spacing: 2) {
                        Text(lead.full_name?.isEmpty == false ? lead.full_name! : "@\(lead.username)").font(.headline)
                        Link("@\(lead.username)", destination: URL(string: "https://www.instagram.com/\(lead.username)/")!).font(.subheadline)
                        HStack(spacing: 8) {
                            if let f = lead.followers { Text("\(f) followers") }
                            if let c = lead.category, !c.isEmpty { Text(c) }
                            Text(lead.market.uppercased())
                        }.font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if let s = lead.score, s != 0 { Text(String(format: "%+.1f", s)).font(.caption.monospacedDigit()).foregroundStyle(s > 0 ? .green : .red) }
                }
                if let b = lead.biography, !b.isEmpty { Text(b).font(.subheadline) }
                Text(lead.reason).font(.caption).foregroundStyle(.secondary)
                LazyVGrid(columns: cols, spacing: 2) {
                    ForEach(Array((lead.recent ?? []).prefix(9).enumerated()), id: \.offset) { _, p in
                        AsyncImage(url: URL(string: p.thumb ?? "")) { img in img.resizable().scaledToFill() } placeholder: { Color.secondary.opacity(0.15) }
                            .frame(height: 110).clipped()
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 10))
                if let cap = lead.recent?.first?.caption, !cap.isEmpty {
                    Text(cap).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }
                TextField(lead.lane == "business" ? "note for the pitch (optional)" : "note for the opener (optional)", text: $note, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(1...3)
                HStack(spacing: 12) {
                    Button { decide(false) } label: { Label("Pass", systemImage: "xmark").frame(maxWidth: .infinity) }
                        .buttonStyle(.bordered).tint(.red)
                    Button { decide(true) } label: { Label(lead.lane == "business" ? "Pitch" : "Like", systemImage: lead.lane == "business" ? "music.note" : "heart.fill").frame(maxWidth: .infinity) }
                        .buttonStyle(.borderedProminent).tint(lead.lane == "business" ? .pink : .green)
                }
                Text(lead.lane == "business" ? "Pitch = queued for a DJ opener that you approve before it sends." : "Like = queued for an opener you approve before it sends. Pass teaches the scout.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            .padding()
        }
    }
}
