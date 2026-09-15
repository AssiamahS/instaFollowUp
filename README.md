# instaFollowUp

Instagram DMs that keep going when I don't. A Mac-side engine watches @sl.ysl.yy, writes the next
message in my voice, and either sends it or texts me first. The iPhone app is the steering wheel.

Two ways to touch Instagram, both already logged in on this Mac:

| path | what it can do | used for |
|---|---|---|
| Instagram Messaging API (Composio, business account) | read every thread, send inside 24h of their last message, comments on my posts | polling, replies, alerts |
| instagram.com in Dia over CDP :9223 | start a conversation with anyone, message after the 24h window | outreach, follow-ups |

## Layout

- `engine/bridge.py` one pass: iMessage commands, poll DMs, follow-ups, outreach, comments. launchd every 2 min.
- `engine/serve.py` JSON API for the app on :8801 (token in `~/.instafollowup/token`).
- `engine/brain.py` `claude -p` drafting with a playbook + standing corrections + the thread.
- `engine/ig_api.py` Composio wrapper. `engine/ig_web.py` Dia driver. `engine/store.py` state.
- `engine/playbooks/*.md` how the bot talks: `dj_pitch` (businesses), `personal`, `default`.
- `InstaFollowUp/` SwiftUI app (iOS 26, XcodeGen, cloud-signed in CI, TestFlight).

## Thread modes

- **off** — alerts only. Every existing thread starts here.
- **approve** — bot drafts, texts me, sends when I say `ig yes`. New people who DM me land here.
- **auto** — bot sends and texts me a copy. Threads the engine opened itself (outreach) start here.

Corrections stick: `ig no @user say less, don't mention rates` rewrites the draft AND is obeyed in
every later message to that person. Playbook-wide corrections live in the app under Playbooks.

## Texting it (iMessage to yourself)

```
ig yes @user              send the draft            ig on @user      draft for me
ig no @user <fix>         redraft with that fix     ig auto @user    let it run
ig say @user <text>       send exactly this         ig off @user     alerts only
ig pitch @venue <note>    queue a DJ pitch          ig mute @user    no alerts
ig reach @user <note>     queue a personal opener   ig status
```

## Install (Mac)

```
cp engine/launchd/*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sly.instafollowup-bridge.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sly.instafollowup-api.plist
python3 engine/bridge.py --seed      # first run: record what exists, alert nothing
python3 engine/bridge.py --status
```

Needs: Dia running with `--remote-debugging-port=9223`, Composio CLI logged in with the
`instagram_andron-depa` connection, `claude` on PATH, Full Disk Access for node (chat.db).
