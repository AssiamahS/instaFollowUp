import SwiftUI

@main
struct InstaFollowUpApp: App {
    @StateObject private var api = API()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(api)
                .preferredColorScheme(.dark)
        }
    }
}
