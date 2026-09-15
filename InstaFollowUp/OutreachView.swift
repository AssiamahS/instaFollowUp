import SwiftUI

struct OutreachView: View {
    @EnvironmentObject var api: API
    @State private var showAdd = false
    @State private var username = ""
    @State private var note = ""
    @State private var playbook = "dj_pitch"
    @State private var cap = 8
    @State private var from = 10
    @State private var to = 20

    var body: some View {
        NavigationStack {
            List {
                if let o = api.outreach {
                    Section {
                        LabeledContent("Sent today", value: "\(o.sent_today.n) / \(o.daily_cap)")
                        Stepper("Daily cap: \(cap)", value: $cap, in: 1...30)
                        HStack {
                            Text("Hours")
                            Spacer()
                            Picker("", selection: $from) { ForEach(0..<24, id: \.self) { Text("\($0):00").tag($0) } }.labelsHidden()
                            Text("to")
                            Picker("", selection: $to) { ForEach(1..<25, id: \.self) { Text("\($0):00").tag($0) } }.labelsHidden()
                        }
                        Button("Save limits") { Task { await api.outreachSettings(cap: cap, hours: [from, to]) } }
                    } header: { Text("Pace") } footer: {
                        Text("One new conversation per engine pass (every 2 minutes), only inside these hours, never past the cap. Keep it low; Instagram limits cold DMs.")
                    }
                    Section("Queue") {
                        let q = o.queue.sorted { $0.added > $1.added }
                        if q.isEmpty { Text("Nothing queued. Add a business or a person.").foregroundStyle(.secondary) }
                        ForEach(q) { item in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text("@\(item.username)").font(.headline)
                                    Text(item.playbook == "dj_pitch" ? "pitch" : "reach").font(.caption2).foregroundStyle(.secondary)
                                    Spacer()
                                    Text(item.status).font(.caption.weight(.semibold)).foregroundStyle(color(item.status))
                                }
                                if !item.note.isEmpty { Text(item.note).font(.subheadline).foregroundStyle(.secondary) }
                                if let e = item.error { Text(e).font(.caption).foregroundStyle(.red) }
                            }
                            .swipeActions {
                                if item.status == "queued" {
                                    Button(role: .destructive) { Task { await api.removeOutreach(item.username) } } label: { Label("Remove", systemImage: "trash") }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Outreach")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button { showAdd = true } label: { Image(systemName: "plus") } }
            }
            .refreshable { await api.loadOutreach() }
            .task {
                await api.loadOutreach()
                if let o = api.outreach { cap = o.daily_cap; from = o.hours.first ?? 10; to = o.hours.last ?? 20 }
            }
            .sheet(isPresented: $showAdd) { addSheet }
        }
    }

    func color(_ s: String) -> Color {
        switch s {
        case "sent": return .green
        case "failed", "needs_note": return .red
        case "queued": return .orange
        default: return .secondary
        }
    }

    var addSheet: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("@username", text: $username).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Picker("Playbook", selection: $playbook) {
                        Text("DJ pitch (business)").tag("dj_pitch")
                        Text("Personal").tag("personal")
                    }
                } footer: { Text("Personal openers need a note about who they are and why you're reaching out.") }
                Section("What the bot should know about them") {
                    TextField("rooftop bar in Jersey City, Friday DJ nights, tagged @xyz last week", text: $note, axis: .vertical).lineLimit(3...8)
                }
                Button("Queue it") {
                    Task {
                        await api.addOutreach(username: username.replacingOccurrences(of: "@", with: ""), note: note, playbook: playbook)
                        username = ""; note = ""; showAdd = false
                    }
                }.disabled(username.trimmingCharacters(in: .whitespaces).count < 2)
            }
            .navigationTitle("New outreach")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showAdd = false } } }
        }
    }
}
