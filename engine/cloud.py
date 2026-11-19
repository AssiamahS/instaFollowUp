#!/usr/bin/env python3
"""Copy the follower snapshot to Cloudflare D1 so it can be read away from this Mac
(claude.ai's Cloudflare connector queries the same database).

Config lives outside the repo in ~/.instafollowup/d1.json:

  {"account_id": "<cloudflare account id>",
   "database_id": "<d1 database id>",
   "token": "<api token with D1 Edit on that account>"}

No config file = nothing is pushed and nothing breaks. Every statement is an upsert, so a push
can be repeated any number of times.

  python3 cloud.py            push the snapshot already on disk (no Instagram calls)
  python3 cloud.py --check    verify the config and the tables, push nothing

Tables (created on first push):
  lists    one row per list (followers, following): when, how many, users as a JSON array
  events   one row per account per kind (new, unfollowed, gone) with the time it was noticed
  history  one row per run: counts
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

CONFIG = os.path.join(store.DIR, "d1.json")
SNAP = os.path.join(store.DIR, "followers.json")
API = "https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS lists (kind TEXT PRIMARY KEY, at REAL NOT NULL, count INTEGER NOT NULL, users TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events (pk TEXT NOT NULL, kind TEXT NOT NULL, username TEXT NOT NULL, "
    "full_name TEXT NOT NULL DEFAULT '', at REAL NOT NULL, unverified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (pk, kind))",
    "CREATE TABLE IF NOT EXISTS history (at REAL PRIMARY KEY, followers INTEGER NOT NULL, following INTEGER NOT NULL, "
    "new INTEGER NOT NULL, unfollowed INTEGER NOT NULL, gone INTEGER NOT NULL)",
]

UPSERT_LIST = (
    "INSERT INTO lists (kind, at, count, users) VALUES (?1, ?2, ?3, ?4) "
    "ON CONFLICT(kind) DO UPDATE SET at = excluded.at, count = excluded.count, users = excluded.users"
)

UPSERT_EVENTS = (
    "INSERT INTO events (pk, kind, username, full_name, at, unverified) "
    "SELECT json_extract(value, '$.pk'), json_extract(value, '$.kind'), json_extract(value, '$.username'), "
    "coalesce(json_extract(value, '$.full_name'), ''), json_extract(value, '$.at'), "
    "coalesce(json_extract(value, '$.unverified'), 0) "
    "FROM json_each(?1) WHERE true "
    "ON CONFLICT(pk, kind) DO UPDATE SET username = excluded.username, full_name = excluded.full_name, "
    "at = excluded.at, unverified = excluded.unverified"
)

UPSERT_HISTORY = (
    "INSERT INTO history (at, followers, following, new, unfollowed, gone) "
    "SELECT json_extract(value, '$.at'), json_extract(value, '$.followers'), json_extract(value, '$.following'), "
    "coalesce(json_extract(value, '$.new'), 0), coalesce(json_extract(value, '$.unfollowed'), 0), "
    "coalesce(json_extract(value, '$.gone'), 0) "
    "FROM json_each(?1) WHERE true "
    "ON CONFLICT(at) DO UPDATE SET followers = excluded.followers, following = excluded.following, "
    "new = excluded.new, unfollowed = excluded.unfollowed, gone = excluded.gone"
)

EVENT_CHUNK = 500      # rows per statement; keeps each JSON parameter far below D1's 2 MB value limit


class CloudError(Exception):
    pass


def load_config():
    """The config dict, or None when the file is absent. Raises CloudError when it is present but unusable."""
    try:
        cfg = json.load(open(CONFIG))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise CloudError(f"{CONFIG} is unreadable: {e}")
    missing = [k for k in ("account_id", "database_id", "token") if not str(cfg.get(k) or "").strip()]
    if missing:
        raise CloudError(f"{CONFIG} is missing: {', '.join(missing)}")
    return {k: str(cfg[k]).strip() for k in ("account_id", "database_id", "token")}


def query(cfg, sql, params=()):
    body = json.dumps({"sql": sql, "params": [str(p) for p in params]}).encode()
    req = urllib.request.Request(
        API.format(account_id=cfg["account_id"], database_id=cfg["database_id"]),
        data=body, method="POST",
        headers={"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise CloudError(f"D1 HTTP {e.code}: {detail}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise CloudError(f"D1 request failed: {e}")
    if not d.get("success"):
        raise CloudError(f"D1 error: {json.dumps(d.get('errors'))[:300]}")
    return d.get("result") or []


def _users(d):
    return [{"pk": pk, "username": u.get("username", ""), "full_name": u.get("full_name") or "",
             "private": bool(u.get("private"))} for pk, u in (d or {}).items()]


def _events(snap):
    rows = []
    for kind in ("new", "unfollowed", "gone"):
        for pk, u in (snap.get(kind) or {}).items():
            if u.get("at") is None or not u.get("username"):
                continue
            rows.append({"pk": pk, "kind": kind, "username": u["username"], "full_name": u.get("full_name") or "",
                         "at": u["at"], "unverified": 1 if u.get("unverified") else 0})
    return rows


def push(snap, at=None):
    """Upsert the whole snapshot. Returns a one-line summary, or None when no config file exists."""
    cfg = load_config()
    if cfg is None:
        return None
    history = [h for h in (snap.get("history") or []) if h.get("at") is not None]
    if at is None:
        at = history[-1]["at"] if history else 0
    for sql in SCHEMA:
        query(cfg, sql)
    for kind in ("followers", "following"):
        users = _users(snap.get(kind))
        query(cfg, UPSERT_LIST, (kind, at, len(users), json.dumps(users, ensure_ascii=False)))
    events = _events(snap)
    for i in range(0, len(events), EVENT_CHUNK):
        query(cfg, UPSERT_EVENTS, (json.dumps(events[i:i + EVENT_CHUNK], ensure_ascii=False),))
    if history:
        query(cfg, UPSERT_HISTORY, (json.dumps(history),))
    return (f"d1: {len(snap.get('followers') or {})} followers, {len(snap.get('following') or {})} following, "
            f"{len(events)} events, {len(history)} runs")


def check():
    cfg = load_config()
    if cfg is None:
        return f"no config at {CONFIG}; nothing is pushed"
    for sql in SCHEMA:
        query(cfg, sql)
    rows = query(cfg, "SELECT kind, count, at FROM lists ORDER BY kind")
    found = (rows[0].get("results") if rows else []) or []
    if not found:
        return "config ok, tables ready, nothing pushed yet"
    return "config ok; " + ", ".join(f"{r['kind']}: {r['count']}" for r in found)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    try:
        if a.check:
            print(check())
        else:
            try:
                snap = json.load(open(SNAP))
            except (OSError, ValueError) as e:
                raise CloudError(f"no snapshot to push ({SNAP}): {e}")
            print(push(snap) or f"no config at {CONFIG}; nothing pushed")
    except CloudError as e:
        print(f"cloud: {e}", file=sys.stderr)
        sys.exit(1)
