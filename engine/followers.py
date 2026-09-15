#!/usr/bin/env python3
"""Who follows me, who left, who is gone. Daily snapshot of my own followers + following
(instagram's list endpoints with the Dia session), diffed against yesterday.

  missing from followers  -> profile still exists  = unfollowed me
                          -> profile page gone     = deleted / deactivated
  new in followers        -> new follower
  following but not back  -> "doesn't follow you back" list

  python3 followers.py            snapshot + diff + text me a summary (launchd daily)
  python3 followers.py --status
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
from clock import now

SNAP = os.path.join(store.DIR, "followers.json")


def fetch_list(ig, me, kind):
    users, max_id = {}, None
    for _ in range(80):
        url = f"https://www.instagram.com/api/v1/friendships/{me}/{kind}/?count=200&search_surface=follow_list_page"
        if max_id:
            url += f"&max_id={max_id}"
        d = ig.get(url)
        for u in d.get("users", []):
            users[str(u["pk"])] = {"username": u["username"], "full_name": u.get("full_name") or "", "private": bool(u.get("is_private"))}
        max_id = d.get("next_max_id")
        if not max_id or not d.get("users"):
            break
        time.sleep(1.2)
    return users


def load_snap():
    try:
        return json.load(open(SNAP))
    except (OSError, ValueError):
        return {"followers": {}, "following": {}, "history": [], "gone": {}, "unfollowed": {}, "new": {}}


def account_gone(username):
    """True when the profile page says it isn't available (deleted or deactivated)."""
    import ig_web
    tab = ig_web.Tab()
    try:
        return ig_web.read_profile(tab, username) is None
    except ig_web.WebError:
        return False
    finally:
        tab.close()


def snapshot(check_gone=True):
    from ig_session import IG
    ig = IG()
    me = ig.h["Cookie"].split("ds_user_id=")[1].split(";")[0]
    snap = load_snap()
    followers = fetch_list(ig, me, "followers")
    following = fetch_list(ig, me, "following")
    old = snap.get("followers") or {}
    first = not old
    new = {pk: u for pk, u in followers.items() if pk not in old}
    missing = {pk: u for pk, u in old.items() if pk not in followers}
    unfollowed, gone = {}, {}
    for pk, u in list(missing.items())[:25]:
        if check_gone and account_gone(u["username"]):
            gone[pk] = u
        else:
            unfollowed[pk] = u
    ts = now()
    for pk, u in new.items():
        snap.setdefault("new", {})[pk] = {**u, "at": ts}
    for pk, u in unfollowed.items():
        snap.setdefault("unfollowed", {})[pk] = {**u, "at": ts}
    for pk, u in gone.items():
        snap.setdefault("gone", {})[pk] = {**u, "at": ts}
    snap["followers"], snap["following"] = followers, following
    snap["not_back"] = {pk: u for pk, u in following.items() if pk not in followers}
    snap["fans"] = {pk: u for pk, u in followers.items() if pk not in following}
    snap.setdefault("history", []).append({"at": ts, "followers": len(followers), "following": len(following),
                                           "new": len(new), "unfollowed": len(unfollowed), "gone": len(gone)})
    snap["history"] = snap["history"][-90:]
    os.makedirs(store.DIR, exist_ok=True)
    json.dump(snap, open(SNAP, "w"), indent=1)
    if first:
        return f"[IG] followers baseline: {len(followers)} followers, {len(following)} following, {len(snap['not_back'])} don't follow back."
    parts = [f"{len(followers)} followers ({len(new):+d} new" + (f", -{len(unfollowed)} unfollowed" if unfollowed else "") + (f", -{len(gone)} gone" if gone else "") + ")"]
    if new:
        parts.append("new: " + ", ".join("@" + u["username"] for u in list(new.values())[:8]))
    if unfollowed:
        parts.append("unfollowed: " + ", ".join("@" + u["username"] for u in list(unfollowed.values())[:8]))
    if gone:
        parts.append("deleted/deactivated: " + ", ".join("@" + u["username"] for u in list(gone.values())[:8]))
    return "[IG] " + " | ".join(parts)


def status():
    s = load_snap()
    h = s.get("history", [])
    return {"followers": len(s.get("followers", {})), "following": len(s.get("following", {})),
            "not_back": len(s.get("not_back", {})), "fans": len(s.get("fans", {})),
            "new": sorted(s.get("new", {}).values(), key=lambda u: -u["at"])[:50],
            "unfollowed": sorted(s.get("unfollowed", {}).values(), key=lambda u: -u["at"])[:50],
            "gone": sorted(s.get("gone", {}).values(), key=lambda u: -u["at"])[:50],
            "not_back_list": list(s.get("not_back", {}).values())[:200],
            "history": h[-30:], "last": h[-1]["at"] if h else None}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-text", action="store_true")
    a = ap.parse_args()
    if a.status:
        print(json.dumps(status(), indent=1)[:3000])
    else:
        msg = snapshot()
        print(msg)
        if not a.no_text:
            import bridge
            bridge.text_me(msg)
