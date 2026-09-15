import Foundation
import SwiftUI

@MainActor
final class API: ObservableObject {
    @AppStorage("baseURL") var baseURL: String = "http://100.97.199.99:8801"
    @AppStorage("token") var token: String = ""

    @Published var threads: [DMThread] = []
    @Published var status: Status?
    @Published var outreach: Outreach?
    @Published var playbooks: [String: String] = [:]
    @Published var coaching: [String: [String]] = [:]
    @Published var log: [String] = []
    @Published var error: String?
    @Published var busy = false

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    private func request(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: baseURL.trimmingCharacters(in: .whitespaces) + path) else {
            throw Failure(message: "bad base URL")
        }
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

    private func get<T: Decodable>(_ path: String, as: T.Type) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await request("GET", path))
    }

    @discardableResult
    private func post<T: Decodable>(_ path: String, _ body: [String: Any], as: T.Type) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await request("POST", path, body: body))
    }

    func run(_ work: () async throws -> Void) async {
        busy = true
        defer { busy = false }
        do { try await work(); error = nil } catch { self.error = error.localizedDescription }
    }

    func refresh() async {
        await run {
            async let s = get("/status", as: Status.self)
            async let t = get("/threads", as: [DMThread].self)
            status = try await s
            threads = try await t
        }
    }

    func load(thread key: String) async -> DMThread? {
        var out: DMThread?
        await run { out = try await get("/threads/\(key)", as: DMThread.self) }
        return out
    }

    func update(_ key: String, _ fields: [String: Any]) async -> DMThread? {
        var out: DMThread?
        await run { out = try await post("/threads/\(key)", fields, as: DMThread.self) }
        return out
    }

    func approve(_ key: String) async { await run { try await post("/threads/\(key)/approve", [:], as: DraftResult.self) } }
    func reject(_ key: String, feedback: String) async {
        await run { try await post("/threads/\(key)/reject", ["feedback": feedback], as: DraftResult.self) }
    }
    func say(_ key: String, _ text: String) async { await run { try await post("/threads/\(key)/say", ["text": text], as: DraftResult.self) } }
    func draft(_ key: String) async { await run { try await post("/threads/\(key)/draft", [:], as: DraftResult.self) } }

    func loadOutreach() async { await run { outreach = try await get("/outreach", as: Outreach.self) } }
    func addOutreach(username: String, note: String, playbook: String) async {
        await run { outreach = try await post("/outreach", ["username": username, "note": note, "playbook": playbook], as: Outreach.self) }
    }
    func outreachSettings(cap: Int, hours: [Int]) async {
        await run { outreach = try await post("/outreach/settings", ["daily_cap": cap, "hours": hours], as: Outreach.self) }
    }
    func removeOutreach(_ username: String) async {
        await run { _ = try await request("DELETE", "/outreach/\(username)"); outreach = try await get("/outreach", as: Outreach.self) }
    }

    func loadPlaybooks() async {
        await run {
            playbooks = try await get("/playbooks", as: [String: String].self)
            coaching = try await get("/coaching", as: [String: [String]].self)
        }
    }
    func savePlaybook(_ name: String, _ text: String) async {
        await run { try await post("/playbooks/\(name)", ["text": text], as: [String: Bool].self) }
    }
    func coach(_ playbook: String, add: String) async {
        await run { coaching = try await post("/coaching/\(playbook)", ["add": add], as: [String: [String]].self) }
    }
    func uncoach(_ playbook: String, index: Int) async {
        await run { coaching = try await post("/coaching/\(playbook)", ["remove_index": index], as: [String: [String]].self) }
    }
    func loadLog() async { await run { log = try await get("/log", as: [String].self) } }
    func runPass() async { await run { try await post("/run", [:], as: [String: Bool].self) } }
}
