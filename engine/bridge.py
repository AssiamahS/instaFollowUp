#!/usr/bin/env python3
"""The follow-up engine. One pass per run (launchd every 2 min):

  1. read my iMessage thread for `ig ...` commands (yes / no <fix> / say / on / auto / off / pitch / reach)
  2. poll Instagram DMs (official API): new inbound -> text me, draft per thread mode, auto-send or wait
  3. follow-ups: threads where the other side went quiet -> nudge (via API inside 24h, else via Dia web)
  4. outreach: open new conversations from the queue (Dia web), rate-capped, inside working hours
  5. comments on my recent posts -> text me

  python3 bridge.py            one pass
  python3 bridge.py --seed     first run: record everything that exists today, alert nothing
  python3 bridge.py --dry-run  show what would happen, send nothing
  python3 bridge.py --status

Thread modes: off (only alert me) | approve (draft, text me, send on "ig yes") | auto (send, text me a copy).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain
import ig_api
import ig_web
import store
from clock import now

ME = "sly.assiamah@icloud.com"
CHAT_DB = os.path.expanduser("~/Library/Messages/chat.db")
CMD = re.compile(r"^\s*ig[:\s]+(?P<cmd>.+)$", re.I | re.S)
USER = re.compile(r"@?([A-Za-z0-9._]{2,40})")
DRY = False


# ---------- iMessage ----------

def applescript(script):
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True, timeout=60)


def text_me(text):
    if DRY:
        print("  [dry] would text:", text.replace("\n", " / ")[:160])
        return
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    applescript('tell application "Messages"\nset svc to 1st account whose service type = iMessage\n'
                f'send "{safe}" to buddy "{ME}" of svc\nend tell')


def _decode_body(blob):
    if not blob:
        return ""
    i = blob.find(b"NSString")
    if i < 0:
        return ""
    i = blob.find(b"+", i)
    if i < 0:
        return ""
    i += 1
    n = blob[i]
    if n == 0x81:
        n = int.from_bytes(blob[i + 1:i + 3], "little")
        i += 3
    else:
        i += 1
    return blob[i:i + n].decode("utf-8", "replace")


def my_thread_since(rowid):
    db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    rows = db.execute(
        """SELECT m.ROWID, m.text, m.attributedBody FROM message m
           JOIN chat_message_join j ON j.message_id = m.ROWID
           JOIN chat c ON c.ROWID = j.chat_id
           WHERE c.chat_identifier = ? AND m.ROWID > ? ORDER BY m.ROWID""", (ME, rowid)).fetchall()
    db.close()
    return [(r[0], (r[1] or _decode_body(r[2])).strip()) for r in rows]


def last_rowid():
    db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    r = db.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]
    db.close()
    return r


# ---------- sending ----------

_tab = None


def web_tab():
    global _tab
    if _tab is None:
        _tab = ig_web.Tab()
    return _tab


def deliver(st, t, text, kind):
    """Send `text` to thread t. API inside Instagram's 24h window, Dia web otherwise. Records it."""
    if DRY:
        store.log(st, f"  [dry] would send to @{t['username']} via {'api' if t.get('igsid') else 'web'}: {text[:80]!r}")
        return "dry"
    via, mid = None, None
    if t.get("igsid") and t["last_in_ts"] and now() - t["last_in_ts"] < 23 * 3600:
        try:
            mid = ig_api.send_text(t["igsid"], text)
            via = "bot-api"
        except ig_api.ApiError as e:
            store.log(st, f"  api send to @{t['username']} failed ({'window closed' if e.window_closed else str(e)[:120]}), trying web")
    if via is None:
        ig_web.send_message(web_tab(), t["username"], text)
        via, mid = "bot-web", f"web-{int(now())}"
    store.add_message(t, mid, now(), "me", text, via)
    t["last_out_by"] = "bot"
    t["pending"] = None
    if kind == "followup":
        t["followups_sent"] += 1
    else:
        t["followups_sent"] = 0
    store.log(st, f"  sent to @{t['username']} via {via}: {text[:80]!r}")
    return via


def make_draft(st, t, kind, days=0, n=1):
    coaching = list(st["coaching_global"].get(t["playbook"], [])) + list(t.get("coaching", []))
    d = brain.draft(t, kind, store.playbook_text(t["playbook"]), coaching, days=days, n=n)
    store.log(st, f"  draft for @{t['username']} ({kind}): skip={d['skip']} {d['text'][:80]!r} why={d['why'][:80]}")
    return d


def cmd_hint(t):
    u = t["username"]
    return f"ig yes @{u} | ig no @{u} <what to change> | ig say @{u} <text>"


def act_on_draft(st, t, d, kind, incoming=None):
    """Apply the thread's mode to a fresh draft."""
    head = f"[IG] @{t['username']}: {incoming}" if incoming else f"[IG] @{t['username']} ({kind})"
    if d["skip"]:
        t["pending"] = None
        text_me(f"{head}\nbot passed: {d['why'] or 'needs you'}\nig say @{t['username']} <text> to answer")
        return
    if t["mode"] == "auto":
        try:
            deliver(st, t, d["text"], kind)
            text_me(f"{head}\nsent: {d['text']}\n(ig no @{t['username']} <fix> corrects it going forward)")
        except Exception as e:
            t["pending"] = {"text": d["text"], "kind": kind, "created": now(), "why": d["why"]}
            text_me(f"{head}\nauto-send FAILED ({str(e)[:100]}), holding draft:\n{d['text']}\n{cmd_hint(t)}")
    else:
        t["pending"] = {"text": d["text"], "kind": kind, "created": now(), "why": d["why"]}
        text_me(f"{head}\ndraft: {d['text']}\n{cmd_hint(t)}")


# ---------- 1. commands from my phone ----------

def pick_thread(st, arg):
    m = USER.search(arg or "")
    if m:
        t = st["threads"].get(store.key_for(m.group(1)))
        if t:
            return t, arg[m.end():].strip()
    pend = [t for t in st["threads"].values() if t.get("pending")]
    pend.sort(key=lambda t: t["pending"]["created"])
    return (pend[0], (arg or "").strip()) if pend else (None, (arg or "").strip())


def handle_commands(st):
    cursor = st["settings"]["imessage_rowid"]
    if not cursor:
        st["settings"]["imessage_rowid"] = last_rowid()
        return
    for rowid, text in my_thread_since(cursor):
        st["settings"]["imessage_rowid"] = rowid
        m = CMD.match(text or "")
        if not m:
            continue
        cmd = m.group("cmd").strip()
        store.log(st, f"command: {cmd[:100]!r}")
        try:
            run_command(st, cmd)
        except Exception as e:
            store.log(st, f"  command failed: {e}")
            text_me(f"[IG] command failed: {str(e)[:160]}")


def run_command(st, cmd):
    word, _, rest = cmd.partition(" ")
    word = word.lower().strip(":")
    if word in ("yes", "y", "send", "go"):
        t, _ = pick_thread(st, rest)
        if not t or not t.get("pending"):
            return text_me("[IG] nothing pending" + (f" for @{t['username']}" if t else ""))
        deliver(st, t, t["pending"]["text"], t["pending"]["kind"])
        text_me(f"[IG] sent to @{t['username']}")
    elif word in ("no", "n", "nah", "skip", "fix"):
        t, fb = pick_thread(st, rest)
        if not t:
            return text_me("[IG] no thread matched")
        t["pending"] = None
        if fb:
            t["coaching"].append(fb)
            d = make_draft(st, t, "reply" if t["messages"] and t["messages"][-1]["from"] == "them" else "followup")
            act_on_draft(st, t, d, "reply")
        else:
            text_me(f"[IG] dropped the draft for @{t['username']}")
    elif word == "say":
        t, msg = pick_thread(st, rest)
        if not t or not msg:
            return text_me("[IG] usage: ig say @user <text>")
        deliver(st, t, msg, "reply")
        text_me(f"[IG] sent to @{t['username']}")
    elif word in ("on", "approve", "auto", "off", "mute", "unmute"):
        m = USER.search(rest)
        if not m:
            return text_me(f"[IG] usage: ig {word} @user")
        u = m.group(1)
        t = store.get_thread(st, u, create=True, mode="approve", playbook="personal")
        if word == "mute":
            t["muted"] = True
        elif word == "unmute":
            t["muted"] = False
        else:
            t["mode"] = {"on": "approve"}.get(word, word)
            t["muted"] = False
        note = rest[m.end():].strip()
        if note:
            t["goal"] = note
        text_me(f"[IG] @{u}: mode {t['mode']}{' (muted)' if t['muted'] else ''}, playbook {t['playbook']}")
    elif word in ("pitch", "reach"):
        m = USER.search(rest)
        if not m:
            return text_me(f"[IG] usage: ig {word} @user <note about them>")
        u, note = m.group(1), rest[m.end():].strip()
        st["outreach"]["queue"].append({"username": u, "note": note, "playbook": "dj_pitch" if word == "pitch" else "personal",
                                        "added": now(), "status": "queued"})
        text_me(f"[IG] queued @{u} ({'dj pitch' if word == 'pitch' else 'personal'}). {queue_summary(st)}")
    elif word == "playbook":
        m = USER.search(rest)
        name = rest[m.end():].strip().lower() if m else ""
        if not m or name not in store.playbook_names():
            return text_me(f"[IG] usage: ig playbook @user <{'|'.join(store.playbook_names())}>")
        store.get_thread(st, m.group(1))["playbook"] = name
        text_me(f"[IG] @{m.group(1)} now uses {name}")
    elif word in ("status", "st"):
        text_me(status_text(st))
    elif word == "help":
        text_me("[IG] ig yes|no <fix>|say @user <text> | ig on|auto|off|mute @user | ig pitch @biz <note> | ig reach @user <note> | ig playbook @user dj_pitch | ig status")
    else:
        text_me(f"[IG] unknown command {word!r}. ig help")


def queue_summary(st):
    q = st["outreach"]["queue"]
    return f"queue: {sum(1 for i in q if i['status'] == 'queued')} waiting, cap {st['outreach']['daily_cap']}/day"


def status_text(st):
    ts = st["threads"].values()
    on = [t for t in ts if t["mode"] != "off"]
    pend = [t for t in ts if t.get("pending")]
    return (f"[IG] {len(ts)} threads, {len(on)} bot-enabled ({sum(1 for t in on if t['mode']=='auto')} auto), "
            f"{len(pend)} waiting on you" + (": " + ", ".join("@" + t["username"] for t in pend[:6]) if pend else "") +
            f". {queue_summary(st)}")


# ---------- 2. inbound DMs ----------

def poll_dms(st, seed=False):
    seen = set(st["settings"]["seen_message_ids"])
    updated = st["settings"].setdefault("conv_updated", {})
    convs = ig_api.conversations(50)
    changed = [c for c in convs if updated.get(c["id"]) != c.get("updated_time")]
    store.log(st, f"dms: {len(convs)} conversations, {len(changed)} changed")
    for c in changed:
        try:
            msgs = ig_api.messages(c["id"], 12)
        except ig_api.ApiError as e:
            store.log(st, f"  messages({c['id'][:12]}) failed: {str(e)[:120]}")
            updated[c["id"]] = c.get("updated_time")   # don't hammer a broken id every pass; retry when it changes
            continue
        updated[c["id"]] = c.get("updated_time")
        igsid, username = ig_api.other_party(msgs)
        if not igsid:
            continue
        t = store.get_thread(st, username, create=True, igsid=igsid, conversation_id=c["id"],
                             mode="off" if seed else st["settings"]["new_inbound_mode"], playbook="personal")
        t["igsid"], t["conversation_id"] = igsid, c["id"]
        if username and username != t["username"]:
            t["username"], t["display"] = username, username
        fresh = []
        for m in reversed(msgs):                      # oldest first
            mid, text = m["id"], (m.get("message") or "").strip()
            sender = "me" if (m.get("from") or {}).get("id") == ig_api.ME_IGSID else "them"
            ts = ig_api.ts(m["created_time"])
            if mid in seen:
                continue
            seen.add(mid)
            st["settings"]["seen_message_ids"].append(mid)
            if sender == "me":
                # our own api/web sends are already recorded (same text, same hour) - skip the echo
                if any(x["from"] == "me" and x["text"] == text and abs(x["ts"] - ts) < 3600 for x in t["messages"]):
                    continue
                store.add_message(t, mid, ts, "me", text or "(attachment)", "api")
                t["last_out_by"] = "me"
                t["followups_sent"] = 0
                t["pending"] = None
            else:
                store.add_message(t, mid, ts, "them", text or "(attachment)", "api")
                if not seed:
                    fresh.append(text or "(attachment)")
        if fresh and not t.get("muted"):
            incoming = " / ".join(fresh)[:300]
            store.log(st, f"  new from @{t['username']} [{t['mode']}]: {incoming[:80]!r}")
            if t["mode"] == "off":
                text_me(f"[IG] @{t['username']}: {incoming}\n(bot off) ig on @{t['username']} = draft for me, ig auto @{t['username']} = let it run")
            else:
                d = make_draft(st, t, "reply")
                act_on_draft(st, t, d, "reply", incoming=incoming)
                if not d["skip"]:
                    ig_api.mark_seen(t["igsid"])


# ---------- 3. follow-ups ----------

def followups(st):
    sched = st["settings"]["followup_days"]
    for t in st["threads"].values():
        if t["mode"] == "off" or t.get("muted") or t.get("pending") or not t["last_out_by"]:
            continue
        if t["last_in_ts"] >= t["last_out_ts"]:
            continue                                  # ball is in our court, not theirs
        days = sched.get(t["playbook"], sched["default"])
        n = t["followups_sent"]
        if n >= len(days):
            continue
        quiet = (now() - t["last_out_ts"]) / 86400
        if quiet < days[n]:
            continue
        store.log(st, f"followup #{n + 1} for @{t['username']} (quiet {quiet:.1f}d)")
        d = make_draft(st, t, "followup", days=int(quiet), n=n + 1)
        if d["skip"]:
            t["followups_sent"] = len(days)           # brain says stop nudging this person
            store.log(st, f"  brain declined: {d['why']}")
            continue
        act_on_draft(st, t, d, "followup")


# ---------- 4. outreach ----------

def outreach(st):
    o = st["outreach"]
    today = time.strftime("%Y-%m-%d", time.gmtime(now()))
    if o["sent_today"].get("date") != today:
        o["sent_today"] = {"date": today, "n": 0}
    hour = time.localtime(time.time() + (now() - time.time())).tm_hour
    if not (o["hours"][0] <= hour < o["hours"][1]):
        return
    for item in o["queue"]:
        if item["status"] != "queued":
            continue
        if o["sent_today"]["n"] >= o["daily_cap"]:
            store.log(st, "outreach: daily cap reached")
            return
        u = item["username"]
        if store.key_for(u) in st["threads"] and st["threads"][store.key_for(u)]["messages"]:
            item["status"] = "exists"
            store.log(st, f"outreach: @{u} already has a thread, skipping")
            continue
        t = store.get_thread(st, u, create=True, mode=st["settings"]["outreach_mode"], playbook=item.get("playbook", "dj_pitch"),
                             goal=item.get("note", ""), source="outreach")
        t["playbook"], t["goal"], t["source"] = item.get("playbook", "dj_pitch"), item.get("note", ""), "outreach"
        d = make_draft(st, t, "opener")
        if d["skip"]:
            item["status"] = "needs_note"
            text_me(f"[IG] outreach @{u}: bot could not write an opener ({d['why']}). ig pitch @{u} <better note> to retry")
            continue
        try:
            deliver(st, t, d["text"], "opener")
            item["status"], item["sent"] = "sent", now()
            o["sent_today"]["n"] += 1
            text_me(f"[IG] opened @{u} ({t['playbook']}, {t['mode']}): {d['text']}")
        except Exception as e:
            item["status"], item["error"] = "failed", str(e)[:200]
            text_me(f"[IG] outreach @{u} FAILED: {str(e)[:140]}")
        return                                        # one per pass, keep it human-paced


# ---------- 5. comments ----------

def comments(st, seed=False):
    seen = set(st["settings"]["seen_comment_ids"])
    try:
        media = ig_api.recent_media(3)
    except ig_api.ApiError as e:
        store.log(st, f"comments: media failed {str(e)[:100]}")
        return
    for m in media:
        try:
            cs = ig_api.media_comments(m["id"])
        except ig_api.ApiError:
            continue
        for c in cs:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            st["settings"]["seen_comment_ids"].append(c["id"])
            if seed or c.get("username") == ig_api.ME_USERNAME:
                continue
            cap = (m.get("caption") or "").split("\n")[0][:40]
            text_me(f"[IG] comment from @{c.get('username')} on \"{cap}\": {c.get('text', '')[:200]}\n{m.get('permalink', '')}")


# ---------- main ----------

def run(seed=False):
    with store.locked() as st:
        if seed:
            st["settings"]["seeded"] = False
        seed = seed or not st["settings"]["seeded"]
        for step, fn in (("commands", lambda: handle_commands(st)), ("dms", lambda: poll_dms(st, seed)),
                         ("followups", lambda: followups(st)), ("outreach", lambda: outreach(st)),
                         ("comments", lambda: comments(st, seed))):
            if seed and step in ("followups", "outreach"):
                continue
            try:
                fn()
            except Exception as e:
                store.log(st, f"{step} crashed: {e}\n{traceback.format_exc()[-600:]}")
        if seed:
            st["settings"]["seeded"] = True
            store.log(st, f"seeded: {len(st['threads'])} threads recorded, all mode off")
    if _tab:
        _tab.close()


def main():
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    DRY = a.dry_run
    if a.status:
        st = store.load()
        print(status_text(st))
        for t in sorted(st["threads"].values(), key=lambda t: -max(t["last_in_ts"], t["last_out_ts"])):
            last = t["messages"][-1]["text"][:50] if t["messages"] else ""
            print(f"  @{t['username']:24} {t['mode']:7} {t['playbook']:9} fu={t['followups_sent']} {'PENDING ' if t.get('pending') else ''}{last!r}")
        return
    run(seed=a.seed)


if __name__ == "__main__":
    main()
