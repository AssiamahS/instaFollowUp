import Foundation

struct Message: Codable, Identifiable, Hashable {
    var id: String?
    var ts: Double
    var from: String
    var text: String
    var via: String?

    var isMe: Bool { from == "me" }
    var date: Date { Date(timeIntervalSince1970: ts) }
}

struct Pending: Codable, Hashable {
    var text: String
    var kind: String
    var created: Double
    var why: String?
}

struct DMThread: Codable, Identifiable, Hashable {
    var key: String
    var username: String
    var display: String
    var mode: String
    var playbook: String
    var goal: String
    var source: String
    var last_in_ts: Double
    var last_out_ts: Double
    var last_out_by: String?
    var followups_sent: Int
    var muted: Bool
    var coaching: [String]
    var last: Message?
    var pending: Pending?
    var count: Int
    var activity: Double
    var messages: [Message]?

    var id: String { key }
    var activityDate: Date { Date(timeIntervalSince1970: activity) }
    var waitingOnThem: Bool { last_out_ts > last_in_ts && last_out_by != nil }
}

struct OutreachItem: Codable, Identifiable, Hashable {
    var username: String
    var note: String
    var playbook: String
    var added: Double
    var status: String
    var error: String?
    var id: String { username + String(added) }
}

struct SentToday: Codable { var date: String; var n: Int }

struct Outreach: Codable {
    var queue: [OutreachItem]
    var daily_cap: Int
    var hours: [Int]
    var sent_today: SentToday
}

struct Status: Codable {
    var text: String
    var threads: Int
    var pending: Int
    var queue: Int
}

struct DraftResult: Codable {
    var ok: Bool
    var draft: Draft?
    var via: String?
}

struct Draft: Codable {
    var text: String
    var skip: Bool
    var why: String?
}

enum Mode: String, CaseIterable, Identifiable {
    case off, approve, auto
    var id: String { rawValue }
    var label: String {
        switch self {
        case .off: return "Off"
        case .approve: return "Ask me"
        case .auto: return "Auto"
        }
    }
    var blurb: String {
        switch self {
        case .off: return "Only alerts. The bot writes nothing."
        case .approve: return "Bot drafts, you tap Send."
        case .auto: return "Bot sends on its own and texts you a copy."
        }
    }
}
