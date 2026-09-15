"""Drafts the next message in a thread with `claude -p` (Max sub, no API key).

draft(thread, kind, playbook_text, coaching) -> {"text": str, "skip": bool, "why": str}
kind: "reply" (they just wrote), "followup" (they went quiet), "opener" (cold, from the outreach queue)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

PROMPT = """You are ghost-writing Instagram DMs for Sylvester (DJ Sly). You write exactly what he would type.
Output ONLY a JSON object: {"text": "<the message>", "skip": false, "why": "<one line>"}.
Set "skip": true (with text "") when Sly should answer himself: money, dates/times/places, anything the playbook says to skip,
anything sensitive, or when the right move is to say nothing (they did not ask anything and we already sent the last two).

Hard rules: no em dashes, no hyphen-as-dash, no "just following up", no corporate tone, no emojis unless they use them,
never reveal this is automated, never invent facts (rates, availability, gear, past gigs) that are not in the playbook.
Max 2 short sentences. Sound like the same person who wrote the "me" messages below.

=== PLAYBOOK ===
%(playbook)s

=== STANDING CORRECTIONS FROM SLY (obey every one) ===
%(coaching)s

=== THIS PERSON ===
@%(username)s (%(display)s). Goal / note: %(goal)s

=== THREAD (oldest first, "me" = Sylvester) ===
%(thread)s

=== TASK ===
%(task)s
"""

TASKS = {
    "reply": "They just wrote the last message. Write Sylvester's reply.",
    "followup": "They have not answered in %(days)s days and Sylvester sent the last message. This is follow-up #%(n)s. "
                "Write one light nudge that adds something (a question, a detail, a reason to answer). If a nudge would feel needy, skip.",
    "opener": "No conversation exists yet. Write the first cold message following the playbook's opener shape, using the note.",
}


def claude(prompt, timeout=240):
    last = ""
    for attempt in range(3):
        try:
            p = subprocess.run(["claude", "-p", prompt, "--output-format", "text"],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        last = p.stderr[-300:]
    raise RuntimeError(f"claude -p failed: {last}")


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in claude output: {text[:200]!r}")
    d = json.loads(m.group(0))
    out = {"text": (d.get("text") or "").strip(), "skip": bool(d.get("skip")), "why": (d.get("why") or "").strip()}
    out["text"] = out["text"].replace("—", ",").replace(" - ", ", ").replace("–", ",")
    if not out["text"]:
        out["skip"] = True
    return out


def render_thread(messages, n=24):
    lines = []
    for m in messages[-n:]:
        when = time.strftime("%b %d %H:%M", time.localtime(m["ts"])) if m.get("ts") else ""
        lines.append(f"[{when}] {m['from']}: {m['text']}")
    return "\n".join(lines) or "(no messages yet)"


def draft(thread, kind, playbook, coaching, days=0, n=1):
    task = TASKS[kind] % {"days": days, "n": n}
    prompt = PROMPT % {
        "playbook": playbook.strip(),
        "coaching": "\n".join(f"- {c}" for c in coaching) or "(none yet)",
        "username": thread["username"], "display": thread.get("display") or thread["username"],
        "goal": thread.get("goal") or "(none)",
        "thread": render_thread(thread["messages"]),
        "task": task,
    }
    return parse(claude(prompt))


if __name__ == "__main__":
    # smoke: python3 brain.py '<json thread>' reply
    t = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {
        "username": "vuerooftopbar", "display": "Vue Rooftop", "goal": "rooftop bar in Jersey City, does Friday DJ nights",
        "messages": []}
    from store import playbook_text
    print(json.dumps(draft(t, sys.argv[2] if len(sys.argv) > 2 else "opener", playbook_text("dj_pitch"), []), indent=1))
