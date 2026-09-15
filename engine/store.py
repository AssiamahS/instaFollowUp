"""State for the follow-up engine: one JSON file, one lock.

~/.instafollowup/state.json
  threads[key]      one entry per person (key = lowercase @username)
  outreach          queue of businesses/people to open a conversation with
  coaching_global   corrections that apply to every thread using a playbook
  settings          defaults + poll cursors
"""
from __future__ import annotations

import fcntl
import json
import os
import time

DIR = os.path.expanduser("~/.instafollowup")
STATE = os.path.join(DIR, "state.json")
LOCK = os.path.join(DIR, "state.lock")
TOKEN = os.path.join(DIR, "token")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYBOOKS = os.path.join(ROOT, "engine", "playbooks")

MODES = ("off", "approve", "auto")

DEFAULT = {
    "threads": {},
    "outreach": {"queue": [], "daily_cap": 8, "hours": [10, 20], "sent_today": {"date": "", "n": 0}},
    "coaching_global": {},
    "settings": {
        "new_inbound_mode": "approve",      # someone new DMs me -> draft + ask
        "outreach_mode": "approve",         # openers wait for "ig yes"; flip a thread to auto once you trust it
        "followup_days": {"dj_pitch": [2, 5, 12], "personal": [3, 8], "default": [3]},
        "imessage_rowid": 0,
        "seen_message_ids": [],
        "seen_comment_ids": [],
        "seeded": False,
    },
    "log": [],
}


def _lock():
    os.makedirs(DIR, exist_ok=True)
    fh = open(LOCK, "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def load():
    try:
        with open(STATE) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    for k, v in DEFAULT.items():
        st.setdefault(k, json.loads(json.dumps(v)))
    for k, v in DEFAULT["settings"].items():
        st["settings"].setdefault(k, v)
    return st


def save(st):
    st["log"] = st["log"][-400:]
    st["settings"]["seen_message_ids"] = st["settings"]["seen_message_ids"][-3000:]
    st["settings"]["seen_comment_ids"] = st["settings"]["seen_comment_ids"][-1000:]
    os.makedirs(DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, STATE)


class locked:
    """with locked() as st: ...   (saved on clean exit)"""

    def __enter__(self):
        self.fh = _lock()
        self.st = load()
        return self.st

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            save(self.st)
        self.fh.close()


def log(st, msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    st["log"].append(line)
    print(line, flush=True)


def token():
    try:
        return open(TOKEN).read().strip()
    except OSError:
        import secrets
        os.makedirs(DIR, exist_ok=True)
        t = secrets.token_hex(16)
        with open(TOKEN, "w") as fh:
            fh.write(t)
        os.chmod(TOKEN, 0o600)
        return t


def key_for(username):
    return username.lstrip("@").strip().lower()


def new_thread(username, **kw):
    t = {
        "key": key_for(username),
        "username": username.lstrip("@").strip(),
        "display": kw.get("display") or username.lstrip("@").strip(),
        "igsid": kw.get("igsid"),
        "conversation_id": kw.get("conversation_id"),
        "mode": kw.get("mode", "off"),
        "playbook": kw.get("playbook", "personal"),
        "goal": kw.get("goal", ""),
        "source": kw.get("source", "inbox"),
        "messages": [],
        "last_in_ts": 0,
        "last_out_ts": 0,
        "last_out_by": None,       # "me" | "bot"
        "followups_sent": 0,
        "pending": None,           # {"text","kind","created","prompt_rowid"}
        "coaching": [],
        "muted": False,
        "created": time.time(),
    }
    return t


def get_thread(st, username, create=True, **kw):
    k = key_for(username)
    t = st["threads"].get(k)
    if t is None and create:
        t = new_thread(username, **kw)
        st["threads"][k] = t
    return t


def add_message(t, mid, ts, sender, text, via):
    """sender = 'me' | 'them'. Returns False if already recorded."""
    if mid and any(m.get("id") == mid for m in t["messages"]):
        return False
    t["messages"].append({"id": mid, "ts": ts, "from": sender, "text": text, "via": via})
    t["messages"] = sorted(t["messages"], key=lambda m: m["ts"])[-80:]
    if sender == "them":
        t["last_in_ts"] = max(t["last_in_ts"], ts)
    else:
        t["last_out_ts"] = max(t["last_out_ts"], ts)
    return True


def playbook_text(name):
    path = os.path.join(PLAYBOOKS, f"{name}.md")
    try:
        return open(path).read()
    except OSError:
        return open(os.path.join(PLAYBOOKS, "default.md")).read()


def playbook_names():
    return sorted(f[:-3] for f in os.listdir(PLAYBOOKS) if f.endswith(".md"))
