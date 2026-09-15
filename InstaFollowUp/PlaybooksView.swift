import SwiftUI

struct PlaybooksView: View {
    @EnvironmentObject var api: API
    @State private var selected = "dj_pitch"
    @State private var text = ""
    @State private var newRule = ""

    var body: some View {
        NavigationStack {
            Form {
                Picker("Playbook", selection: $selected) {
                    ForEach(api.playbooks.keys.sorted(), id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                .onChange(of: selected) { text = api.playbooks[selected] ?? "" }

                Section {
                    TextEditor(text: $text).frame(minHeight: 260).font(.system(.footnote, design: .monospaced))
                    Button("Save playbook") { Task { await api.savePlaybook(selected, text); await api.loadPlaybooks() } }
                        .disabled(text == api.playbooks[selected])
                } header: { Text("How the bot talks and what it may promise") } footer: {
                    Text("dj_pitch: fill in Rates and Available so the bot can answer those instead of passing them to you.")
                }

                Section("Standing corrections for \(selected)") {
                    ForEach(Array((api.coaching[selected] ?? []).enumerated()), id: \.offset) { i, rule in
                        Text(rule).font(.footnote)
                            .swipeActions { Button(role: .destructive) { Task { await api.uncoach(selected, index: i) } } label: { Label("Remove", systemImage: "trash") } }
                    }
                    HStack {
                        TextField("e.g. never open with 'yo' to a business", text: $newRule)
                        Button("Add") { Task { await api.coach(selected, add: newRule); newRule = "" } }
                            .disabled(newRule.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
            .navigationTitle("Playbooks")
            .task { await api.loadPlaybooks(); text = api.playbooks[selected] ?? "" }
            .refreshable { await api.loadPlaybooks(); text = api.playbooks[selected] ?? "" }
        }
    }
}
