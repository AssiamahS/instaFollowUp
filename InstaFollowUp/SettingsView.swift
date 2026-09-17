import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var api: API
    @State private var showLog = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac engine") {
                    TextField("http://saints-macbook-air.tail40af16.ts.net:8801", text: $api.baseURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    SecureField("token (~/.instafollowup/token on the Mac)", text: $api.token)
                    Button("Test connection") { Task { await api.refresh() } }
                    if let s = api.status {
                        Text(s.text.replacingOccurrences(of: "[IG] ", with: "")).font(.footnote).foregroundStyle(.secondary)
                    } else if let e = api.error {
                        Text(e).font(.footnote).foregroundStyle(.red)
                    }
                }
                Section("Text commands (iMessage to yourself)") {
                    ForEach(["ig yes @user", "ig no @user <what to change>", "ig say @user <text>", "ig on / auto / off @user",
                             "ig pitch @business <note>", "ig reach @user <note>", "ig status"], id: \.self) {
                        Text($0).font(.system(.footnote, design: .monospaced))
                    }
                }
                Section {
                    Button("Run an engine pass now") { Task { await api.runPass() } }
                    Button("Engine log") { Task { await api.loadLog(); showLog = true } }
                }
            }
            .navigationTitle("Settings")
            .sheet(isPresented: $showLog) {
                NavigationStack {
                    List(api.log.reversed(), id: \.self) { Text($0).font(.system(.caption2, design: .monospaced)) }
                        .navigationTitle("Engine log")
                        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { showLog = false } } }
                }
            }
        }
    }
}
