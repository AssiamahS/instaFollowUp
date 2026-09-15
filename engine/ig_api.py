"""Instagram Messaging API (official, business account) through the Composio CLI.

The Composio connection instagram_andron-depa holds the token for @sl.ysl.yy.
Reading threads works for every conversation. Sending works only inside Instagram's
window: within 24h of the other person's last message. Outside it, ig_web sends.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

COMPOSIO = os.path.expanduser("~/.local/bin/composio")
ACCOUNT = "instagram_andron-depa"
IG_USER_ID = "28554014597536594"    # media/insights id
ME_IGSID = "17841400553770202"       # how the messaging API names me inside threads
ME_USERNAME = "sl.ysl.yy"


class ApiError(Exception):
    def __init__(self, tool, payload):
        self.tool, self.payload = tool, payload
        super().__init__(f"{tool}: {json.dumps(payload)[:300]}")

    @property
    def window_closed(self):
        s = json.dumps(self.payload)
        return "2534022" in s or "outside of allowed window" in s.lower()


def execute(tool, data, timeout=90):
    env = dict(os.environ, CI="false")   # CI=true makes composio print every id as <REDACTED>
    p = subprocess.run([COMPOSIO, "execute", tool, "--account", ACCOUNT, "-d", json.dumps(data)],
                       capture_output=True, text=True, timeout=timeout, env=env)
    out = p.stdout.strip()
    try:
        body = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        raise ApiError(tool, {"stdout": out[-400:], "stderr": p.stderr[-400:]})
    if not body.get("successful"):
        raise ApiError(tool, body.get("error") or body)
    return body.get("data") or {}


def conversations(limit=50):
    """[{id, updated_time}] newest first."""
    d = execute("INSTAGRAM_LIST_ALL_CONVERSATIONS", {"ig_user_id": IG_USER_ID, "limit": limit})
    return d.get("data") or []


def messages(conversation_id, limit=20):
    """Newest first: [{id, created_time, from{id,username}, to{data[{id,username}]}, message}]"""
    d = execute("INSTAGRAM_LIST_ALL_MESSAGES",
                {"conversation_id": conversation_id, "limit": limit,
                 "fields": "id,created_time,from,to,message"})
    return d.get("data") or []


def send_text(recipient_igsid, text):
    d = execute("INSTAGRAM_SEND_TEXT_MESSAGE",
                {"ig_user_id": IG_USER_ID, "recipient_id": recipient_igsid, "text": text})
    return d.get("message_id") or d.get("id") or json.dumps(d)[:80]


def mark_seen(recipient_igsid):
    try:
        execute("INSTAGRAM_MARK_SEEN", {"ig_user_id": IG_USER_ID, "recipient_id": recipient_igsid})
    except ApiError:
        pass


def recent_media(n=3):
    d = execute("INSTAGRAM_GET_IG_USER_MEDIA",
                {"ig_user_id": IG_USER_ID, "fields": "id,caption,permalink,timestamp", "limit": n})
    return (d.get("data") or [])[:n]


def media_comments(media_id):
    d = execute("INSTAGRAM_GET_IG_MEDIA_COMMENTS",
                {"ig_media_id": media_id, "fields": "id,text,username,timestamp"})
    return d.get("data") or []


def ts(iso):
    """'2026-09-16T14:08:04+0000' -> epoch"""
    return int(time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))) - time.timezone


def other_party(msgs):
    """(igsid, username) of the non-me participant in a message list."""
    for m in msgs:
        f = m.get("from") or {}
        if f.get("id") and f["id"] != ME_IGSID:
            return f["id"], f.get("username") or f["id"]
        for t in (m.get("to") or {}).get("data") or []:
            if t.get("id") and t["id"] != ME_IGSID:
                return t["id"], t.get("username") or t["id"]
    return None, None


if __name__ == "__main__":
    import sys
    convs = conversations(5)
    print(len(convs), "conversations")
    for c in convs[:3]:
        ms = messages(c["id"], 3)
        print(other_party(ms), [(m["from"].get("username"), (m.get("message") or "")[:40]) for m in ms])
    if len(sys.argv) > 2 and sys.argv[1] == "--send":
        print(send_text(sys.argv[2], " ".join(sys.argv[3:])))
