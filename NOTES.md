- 2026-09-15 build: Instagram Messaging API (Composio) can READ every thread but only SEND within 24h of
  their last message (403 subcode 2534022 after). Cold DMs and late follow-ups go through instagram.com in
  Dia over CDP :9223 (ig_web.py). The "New message" dialog works whether or not I follow them; the profile
  page only shows a Message button for accounts I follow.
- ig_web: Dia keeps the tab in the background so innerWidth is 0 and every rect is garbage; pin a viewport
  with Emulation.setDeviceMetricsOverride before reading geometry. Composer is a Lexical div; CDP
  Input.insertText lands, .value= does not, Enter sends, there is no "Send" button in the DOM.
- Some conversation ids from LIST_ALL_CONVERSATIONS fail LIST_ALL_MESSAGES with code 100 subcode 33 and
  the Composio call takes ~50s to give up; record updated_time on failure or the pass never finishes.
- Mac clock is ~25h behind (sntp): ASC API 401s (mint the JWT with the offset), and anything compared to
  Instagram timestamps goes through engine/clock.py (HTTP Date header).
- /usr/bin/python3 is 3.9 and lacks websocket-client; launchd runs /opt/homebrew/bin/python3 under node
  (node holds Full Disk Access for chat.db + Messages automation).
- post4me's approval_texter shares the iMessage thread and treats any plain reply as reel feedback; it now
  skips messages starting with ig / scipio / sc / [IG].
