import SwiftUI

struct ThreadView: View {
    @EnvironmentObject var api: API
    let key: String
    @State private var t: DMThread?
    @State private var editing = ""
    @State private var coachNote = ""
    @State private var sayText = ""
    @State private var goal = ""
    @State private var showCoach = false

    var body: some View {
        Group {
            if let t {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            ForEach(t.messages ?? []) { m in Bubble(m: m) }
                            if let p = t.pending { draftCard(p) }
                            Color.clear.frame(height: 1).id("bottom")
                        }
                        .padding()
                    }
                    .onAppear { proxy.scrollTo("bottom") }
                    .onChange(of: t.messages?.count ?? 0) { proxy.scrollTo("bottom") }
                }
                .safeAreaInset(edge: .bottom) { composer }
                .navigationTitle(t.display)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) { controls(t) }
                }
            } else {
                ProgressView().task { await reload() }
            }
        }
        .alert("Error", isPresented: Binding(get: { api.error != nil }, set: { _ in api.error = nil })) {
            Button("OK") {}
        } message: { Text(api.error ?? "") }
        .sheet(isPresented: $showCoach) { coachSheet }
    }

    func reload() async {
        if let fresh = await api.load(thread: key) {
            t = fresh
            goal = fresh.goal
        }
    }

    func controls(_ t: DMThread) -> some View {
        Menu {
            Picker("Bot", selection: Binding(get: { t.mode }, set: { new in Task { await set(["mode": new]) } })) {
                ForEach(Mode.allCases) { m in Text("\(m.label): \(m.blurb)").tag(m.rawValue) }
            }
            Picker("Playbook", selection: Binding(get: { t.playbook }, set: { new in Task { await set(["playbook": new]) } })) {
                ForEach(["personal", "dj_pitch", "default"], id: \.self) { Text($0).tag($0) }
            }
            Button(t.muted ? "Unmute alerts" : "Mute alerts") { Task { await set(["muted": !t.muted]) } }
            Button("Write a draft now") { Task { await api.draft(key); await reload() } }
            Button("Coach this thread…") { showCoach = true }
        } label: { ModeBadge(mode: t.mode) }
    }

    func set(_ fields: [String: Any]) async {
        if let fresh = await api.update(key, fields) {
            var keep = fresh
            keep.messages = t?.messages
            t = keep
        }
    }

    func draftCard(_ p: Pending) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(p.kind == "followup" ? "Follow-up draft" : (p.kind == "opener" ? "Opener draft" : "Reply draft"), systemImage: "sparkles")
                    .font(.caption.weight(.semibold)).foregroundStyle(.orange)
                Spacer()
                Text(Date(timeIntervalSince1970: p.created).short).font(.caption2).foregroundStyle(.secondary)
            }
            TextField("Draft", text: Binding(get: { editing.isEmpty ? p.text : editing }, set: { editing = $0 }), axis: .vertical)
                .lineLimit(2...8)
                .font(.body)
            if let why = p.why, !why.isEmpty {
                Text(why).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button {
                    Task {
                        let text = editing.isEmpty ? p.text : editing
                        if text == p.text { await api.approve(key) } else { await api.say(key, text) }
                        editing = ""
                        await reload()
                    }
                } label: { Label("Send", systemImage: "paperplane.fill") }
                .buttonStyle(.borderedProminent)
                Button {
                    Task { await api.reject(key, feedback: ""); editing = ""; await reload() }
                } label: { Label("Skip", systemImage: "xmark") }
                .buttonStyle(.bordered)
                Spacer()
                Button("Fix…") { showCoach = true }.buttonStyle(.bordered)
            }
        }
        .padding()
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.orange.opacity(0.4)))
    }

    var composer: some View {
        HStack(spacing: 8) {
            TextField("Say it yourself…", text: $sayText, axis: .vertical).lineLimit(1...4)
                .textFieldStyle(.roundedBorder)
            Button {
                let text = sayText.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return }
                Task { await api.say(key, text); sayText = ""; await reload() }
            } label: { Image(systemName: "arrow.up.circle.fill").font(.title) }
            .disabled(api.busy || sayText.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }

    var coachSheet: some View {
        NavigationStack {
            Form {
                Section("Tell the bot how to talk to this person") {
                    TextField("e.g. don't bring up DJing, keep it about the gym", text: $coachNote, axis: .vertical).lineLimit(2...5)
                    Button("Save and redraft") {
                        Task {
                            let note = coachNote.trimmingCharacters(in: .whitespacesAndNewlines)
                            if t?.pending != nil { await api.reject(key, feedback: note) } else if !note.isEmpty { await api.update(key, ["coach": note]); await api.draft(key) }
                            coachNote = ""; editing = ""; showCoach = false
                            await reload()
                        }
                    }.disabled(coachNote.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                Section("Goal / note for this thread") {
                    TextField("what this conversation is for", text: $goal, axis: .vertical).lineLimit(1...4)
                    Button("Save goal") { Task { await set(["goal": goal]); showCoach = false } }
                }
                if let c = t?.coaching, !c.isEmpty {
                    Section("Standing corrections") { ForEach(c, id: \.self) { Text($0).font(.footnote) } }
                }
            }
            .navigationTitle("Coach @\(t?.username ?? "")")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { showCoach = false } } }
        }
        .presentationDetents([.medium, .large])
    }
}

struct Bubble: View {
    let m: Message
    var body: some View {
        HStack {
            if m.isMe { Spacer(minLength: 50) }
            VStack(alignment: m.isMe ? .trailing : .leading, spacing: 2) {
                Text(m.text)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(m.isMe ? Color.pink.opacity(0.85) : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                    .foregroundStyle(m.isMe ? .white : .primary)
                HStack(spacing: 4) {
                    Text(m.date.short)
                    if m.isMe, (m.via ?? "").hasPrefix("bot") { Text("bot").fontWeight(.semibold) }
                }
                .font(.caption2).foregroundStyle(.secondary)
            }
            if !m.isMe { Spacer(minLength: 50) }
        }
    }
}
