#!/usr/bin/env python3
"""Finds who to talk to and learns what Sly says yes to.

Two lanes, same loop: discover public Instagram accounts in his markets -> enrich (followers,
category, bio, recent posts) -> score against his past yes/no decisions -> put the best in the
app / a daily text -> he taps LIKE or PASS -> LIKE lands in the outreach queue (which itself
only drafts and asks). Every decision updates the taste weights, so the ranking gets closer
to what he actually picks.

  business lane: venues + event people that would hire a DJ (bars, lounges, clubs, rooftops,
                 restaurants with nightlife, hookah, breweries, event spaces, promoters)
  people lane:   normal-sized public accounts (hundreds to a few thousand followers) posting
                 from the places he chooses (tags per market). He judges the photos himself;
                 nothing here rates anyone's looks, it only learns from his picks.

  python3 scout.py --daily                  all markets, both lanes, capped (launchd 9am)
  python3 scout.py --business --market philly
  python3 scout.py --people --market nj
  python3 scout.py --decide @user yes|no [note]
  python3 scout.py --status
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge
import store
from clock import now

MARKETS = {
    "philly": {"city": "Philadelphia", "queries": ["philadelphia rooftop bar", "philly lounge", "philly hookah lounge",
                                                   "philadelphia nightclub", "philly brewery events", "philadelphia event space",
                                                   "philly day party", "fishtown bar"],
               "business_tags": ["phillynightlife", "phillyevents", "phillybars", "phillyparty"],
               "people_tags": ["phillyfitness", "phillygym", "phillygirls", "phillysummer", "fitphilly"]},
    "nj": {"city": "New Jersey", "queries": ["jersey city rooftop bar", "hoboken bar", "newark lounge", "north jersey hookah lounge",
                                             "new jersey nightclub", "jersey shore bar", "new brunswick bar", "nj event venue"],
           "business_tags": ["njnightlife", "jerseycitynightlife", "hobokennightlife", "njevents"],
           "people_tags": ["njfitness", "jerseyfit", "jerseyshore", "njsummer", "hobokenfitness"]},
    "nyc": {"city": "New York City", "queries": ["brooklyn rooftop bar", "harlem lounge", "bronx hookah lounge", "queens bar",
                                                 "manhattan nightclub", "brooklyn day party", "nyc event space", "bushwick bar"],
            "business_tags": ["nycnightlife", "brooklynnightlife", "nycevents", "nycparty"],
            "people_tags": ["nycfitness", "nycfitgirls", "brooklynfitness", "nycgirls", "rockawaybeach"]},
    "charlotte": {"city": "Charlotte", "queries": ["charlotte rooftop bar", "charlotte lounge", "charlotte hookah lounge",
                                                   "charlotte nightclub", "charlotte brewery", "uptown charlotte bar", "charlotte event venue"],
                  "business_tags": ["charlottenightlife", "cltnightlife", "charlotteevents", "cltparty"],
                  "people_tags": ["charlottefitness", "cltfitness", "cltfit", "charlottegirls", "cltsummer"]},
    # no city: pure photo lane. Posters under swimwear tags, then the judge decides.
    "bikini": {"city": "anywhere", "queries": [], "business_tags": [],
               "people_tags": ["bikini", "bikinigirl", "bikinibody", "bikinilife", "swimwear", "swimsuit", "beachbabe",
                               "beachgirl", "poolside", "fitbikini", "bikinifitness", "bikinimodel", "summerbody", "beachbody"]},
}
VENUE_WORDS = ("bar", "lounge", "club", "rooftop", "restaurant", "hookah", "brewery", "venue", "event", "nightlife",
               "party", "promoter", "entertainment", "hall", "hotel", "grill", "tavern", "pub", "taproom", "cafe", "bistro",
               "kitchen", "speakeasy", "cigar", "sports bar", "beer garden", "pool", "yacht", "boat", "banquet")
VENUE_CATEGORIES = ("bar", "night club", "nightclub", "lounge", "restaurant", "pub", "brewery", "event", "hookah", "cocktail",
                    "wine bar", "sports bar", "dance", "party", "entertainment", "hotel", "venue", "food & beverage", "concert")
BANDS = {"business": (150, 60000), "people": (80, 12000)}
ENRICH_GAP = 6.0            # seconds between profile lookups; instagram 429s a burst
MAX_ENRICH = 20             # per run (each web read is a page load in Dia)
STOP = re.compile(r"[^a-z0-9#@']+")


# ---------- taste (what he says yes to) ----------

def tokens(c):
    toks = {f"lane:{c['lane']}", f"market:{c.get('market', '')}", f"cat:{(c.get('category') or '').lower()}",
            f"biz:{int(bool(c.get('is_business')))}"}
    f = c.get("followers") or 0
    toks.add("fol:" + ("xs" if f < 300 else "s" if f < 1500 else "m" if f < 6000 else "l" if f < 25000 else "xl"))
    text = " ".join([c.get("biography") or "", c.get("full_name") or ""] + [p.get("caption") or "" for p in c.get("recent") or []])
    for w in STOP.split(text.lower()):
        if len(w) > 2 and not w.isdigit():
            toks.add(("tag:" + w[1:]) if w.startswith("#") else ("w:" + w))
    for p in c.get("recent") or []:
        if p.get("location"):
            toks.add("loc:" + p["location"].lower())
    return toks


def score(taste, c):
    w = taste.get("weights", {})
    return round(sum(w.get(t, 0.0) for t in tokens(c)), 2)


def learn(taste, c, yes):
    w = taste.setdefault("weights", {})
    for t in tokens(c):
        w[t] = round(w.get(t, 0.0) * 0.98 + (1.0 if yes else -1.0), 3)
    taste["decisions"] = taste.get("decisions", 0) + 1


def taste_summary(taste, lane, n=12):
    w = taste.get("weights", {})
    items = [(k, v) for k, v in w.items() if not k.startswith(("lane:", "market:"))]
    likes = sorted(items, key=lambda kv: -kv[1])[:n]
    hates = sorted(items, key=lambda kv: kv[1])[:n]
    return {"decisions": taste.get("decisions", 0),
            "likes": [k for k, v in likes if v > 0.5], "passes": [k for k, v in hates if v < -0.5]}


# ---------- discovery ----------

VENUE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in VENUE_WORDS) + r")s?\b", re.I)


def looks_like_venue(p):
    """A place that books DJs: the category says so, or it's a business whose bio/name says so."""
    cat = (p.get("category") or "").lower()
    if any(k in cat for k in VENUE_CATEGORIES):
        return True
    text = (p.get("biography") or "") + " " + (p.get("full_name") or "")
    return bool(p.get("is_business") or p.get("category")) and bool(VENUE_RE.search(text))


def discover_business(ig, market, limit):
    """Candidate usernames with a reason, from keyword search (posts + tagged venues) and hashtags."""
    seen, out = {}, []

    def add(u, reason):
        u = (u or "").lower().strip()
        if u and u not in seen:
            seen[u] = reason
            out.append((u, reason))

    m = MARKETS[market]
    if not m["queries"]:
        return out
    for q in m["queries"]:
        try:
            d = ig.get(f"https://www.instagram.com/api/v1/fbsearch/web/top_serp/?query={q.replace(' ', '%20')}&search_surface=web_top_serp")
        except (urllib.error.HTTPError, OSError) as e:
            print("  search failed:", q, str(e)[:60]); continue
        venues = set()
        for s in (d.get("media_grid") or {}).get("sections", []):
            for med in (s.get("layout_content") or {}).get("medias", []):
                media = med.get("media") or {}
                u = (media.get("user") or {}).get("username")
                loc = (media.get("location") or {}).get("name")
                add(u, f"posted for '{q}'")
                if loc and loc.lower() not in (m["city"].lower(), "philadelphia, pennsylvania", "new york, new york", "charlotte, north carolina"):
                    venues.add(loc)
        for v in list(venues)[:6]:
            try:
                r = ig.topsearch(v)
            except (urllib.error.HTTPError, OSError):
                continue
            for u in r["users"][:2]:
                if not u.get("is_private"):
                    add(u["username"], f"tagged venue '{v}' in '{q}' posts")
            time.sleep(1.5)
        time.sleep(2)
        if len(out) >= limit * 3:
            break
    for tag in m["business_tags"]:
        try:
            for p in ig.hashtag_posters(tag):
                if not p.get("is_private"):
                    add(p["username"], f"posted under #{tag}: {p['caption'][:60]!r}")
        except (urllib.error.HTTPError, OSError) as e:
            print("  tag failed:", tag, str(e)[:60])
        time.sleep(2)
    return out


def discover_people(ig, market, limit):
    seen, out = {}, []
    for tag in MARKETS[market]["people_tags"] + store.load().get("scout", {}).get("extra_people_tags", {}).get(market, []):
        try:
            posters = ig.hashtag_posters(tag)
        except (urllib.error.HTTPError, OSError) as e:
            print("  tag failed:", tag, str(e)[:60]); continue
        for p in posters:
            u = p["username"].lower()
            if p.get("is_private") or u in seen:
                continue
            seen[u] = 1
            out.append((u, f"posted under #{tag}: {p['caption'][:60]!r}"))
        time.sleep(2)
        if len(out) >= limit * 3:
            break
    return out


class Enricher:
    """Profile lookups: JSON endpoint first; once instagram 429s it, the rendered page in Dia."""

    def __init__(self, ig):
        self.ig, self.tab, self.api_dead = ig, None, False

    def get(self, username):
        if not self.api_dead:
            try:
                p = self.ig.profile(username, raise_429=True)
                time.sleep(ENRICH_GAP)
                if p is not None:
                    return p
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.api_dead = True
                    print("  profile endpoint throttled (429); reading profiles through Dia instead")
                else:
                    return None
        import ig_web
        if self.tab is None:
            self.tab = ig_web.Tab()
        try:
            p = ig_web.read_profile(self.tab, username)
        except ig_web.WebError as e:
            print(f"  web profile @{username} failed: {str(e)[:80]}")
            return None
        time.sleep(1.5)
        return p

    def close(self):
        if self.tab:
            self.tab.close()


COMPANY_WORDS = ("club", "social", "society", "group", "page", "shop", "store", "brand", "agency", "salon", "studio", "company", "business", "service", "boutique",
                 "clothing", "photograph", "media", "magazine", "marketing", "real estate", "gym", "fitness center",
                 "spa", "clinic", "school", "church", "organization", "nonprofit", "community", "website", "product")
# people lane: words in the bio / name / category that mean "not who Sly is looking for"
BIO_DENY = ("mom", "mama", "mommy", "mother", "momlife", "mom life", "kids", "my son", "my daughter", "boy mom", "girl mom",
            "wife", "wifey", "married", "engaged", "fiance", "fiancé", "husband", "hubby", "taken",
            "yoga", "pilates", "podcast", "studio", "salon", "coach", "coaching", "trainer", "owner", "founder", "ceo",
            "booking", "bookings", "inquiries", "inquires", "collab", "brand ambassador", "realtor", "real estate",
            "author", "speaker", "consultant", "agency", "boutique", "shop now", "order now", "dm to order", "menu")

NAME_COMPANY_WORDS = ("yoga", "pilates", "podcast", "realestate", "realtor", "realty", "photos", "photography", "wellness", "massage", "salon", "studio",
                      "theapp", "collective", "shop", "boutique", "llc", "journal", "magazine", "agency", "official", "makers",
                      "designs", "beauty", "lashes", "nails", "hair", "fitness", "coach", "clinic", "events", "media", "music",
                      "records", "podcast", "church", "ministries", "foundation", "rentals", "homes", "properties")


def qualify(lane, p):
    """Returns (ok, why). People keep creator-type categories (digital creator, artist, model);
    only venue-like or company-like categories are dropped."""
    if not p:
        return False, "no profile"
    if p.get("is_private"):
        return False, "private"
    lo, hi = BANDS[lane]
    f = p.get("followers")
    if f is None:
        return False, "no follower count"
    if not (lo <= f <= hi):
        return False, f"{f} followers outside {lo}-{hi}"
    posts = p.get("posts")
    if posts is not None and posts < 6:
        return False, f"only {posts} posts"
    if posts is None and len(p.get("recent") or []) < 3:
        return False, "post count unknown and fewer than 3 recent photos"
    cat = (p.get("category") or "").lower()
    if lane == "business":
        return (True, "venue") if looks_like_venue(p) else (False, f"not a venue ({cat or 'no category'})")
    if any(k in cat for k in VENUE_CATEGORIES):
        return False, f"venue category {cat}"
    if any(k in cat for k in COMPANY_WORDS):
        return False, f"company category {cat}"
    bio = ((p.get("biography") or "") + " " + (p.get("full_name") or "") + " " + cat).lower()
    hit = next((k for k in BIO_DENY if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", bio)), None)
    if hit:
        return False, f"bio says '{hit}'"
    name = ((p.get("username") or "") + " " + (p.get("full_name") or "")).lower().replace("_", "").replace(".", "")
    hit = next((k for k in NAME_COMPANY_WORDS if k in name), None)
    if hit:
        return False, f"business name ({hit})"
    if p.get("is_verified"):
        return False, "verified"
    return True, cat or "person"


def run_lane(lane, market, limit=10):
    from ig_session import IG
    ig = IG()
    st = store.load()
    leads = st.setdefault("leads", {})
    taste = st.setdefault("taste", {}).setdefault(lane, {})
    known = set(leads) | set(st["threads"]) | {store.key_for(i["username"]) for i in st["outreach"]["queue"]}
    found = (discover_business if lane == "business" else discover_people)(ig, market, limit)
    fresh = [(u, r) for u, r in found if u not in known]
    print(f"{lane}/{market}: {len(found)} discovered, {len(fresh)} new, enriching up to {MAX_ENRICH}")
    added, throttled = 0, False
    en = Enricher(ig)
    for u, reason in fresh[:MAX_ENRICH]:
        p = en.get(u)
        throttled = en.api_dead
        ok, why = qualify(lane, p)
        if not ok:
            print(f"  - @{u}: {why}")
            continue
        c = {**p, "lane": lane, "market": market, "reason": reason, "found": now(), "status": "new", "decision": None}
        if lane == "people":
            try:
                keep, jwhy, v = judge.judge(c)
            except Exception as e:
                print(f"  ? @{u}: judge failed ({str(e)[:80]}), skipping")
                continue
            if not keep:
                print(f"  - @{u}: judge: {jwhy}")
                continue
            c["verdict"], c["judge"] = v, jwhy
            c["reason"] = f"{jwhy} · {reason}"
        c["score"] = score(taste, c) + (judge.bonus(c["verdict"]) if c.get("verdict") else 0)
        with store.locked() as st2:
            st2.setdefault("leads", {})[u] = c
        added += 1
        print(f"  + @{u} ({p.get('followers')} followers, {p.get('category') or 'no category'}) score {c['score']}")
        if added >= limit:
            break
    en.close()
    with store.locked() as st2:
        st2.setdefault("scout", {})["last_run"] = {"lane": lane, "market": market, "at": now(), "added": added,
                                                   "throttled": throttled, "calls": ig.calls}
    return added, throttled


def decide(username, yes, note=""):
    key = store.key_for(username)
    with store.locked() as st:
        c = st.get("leads", {}).get(key)
        if not c:
            raise KeyError(f"no lead @{username}")
        taste = st.setdefault("taste", {}).setdefault(c["lane"], {})
        learn(taste, c, yes)
        c["decision"], c["decided"] = ("like" if yes else "pass"), now()
        c["status"] = "liked" if yes else "passed"
        if note:
            c["note"] = note
        if yes:
            what = ", ".join(x for x in (c.get("category"), c.get("full_name")) if x)
            auto_note = f"{what} in {MARKETS.get(c['market'], {}).get('city', c['market'])}. bio: {c.get('biography', '')[:140]}"
            last = next((p.get("caption") for p in c.get("recent") or [] if p.get("caption")), "")
            if last:
                auto_note += f" recent post: {last[:120]!r}"
            already = any(store.key_for(i["username"]) == key and i["status"] in ("queued", "drafted", "sent")
                          for i in st["outreach"]["queue"])
            if not already:
                st["outreach"]["queue"].append({"username": c["username"], "note": (note + " " if note else "") + auto_note,
                                                "playbook": "dj_pitch" if c["lane"] == "business" else "personal",
                                                "added": now(), "status": "queued", "from_scout": True})
            c["status"] = "queued"
        # re-score what's still waiting so the app shows the best first
        for o in st["leads"].values():
            if o["status"] == "new" and o["lane"] == c["lane"]:
                o["score"] = score(taste, o) + (judge.bonus(o["verdict"]) if o.get("verdict") else 0)
        return c["status"]


def daily():
    text = []
    for lane, per in (("business", 4), ("people", 3)):
        total = 0
        for market in MARKETS:
            try:
                n, thr = run_lane(lane, market, limit=per)
            except Exception as e:
                print(f"{lane}/{market} crashed: {e}")
                continue
            total += n
            if thr:
                text.append(f"{lane}: instagram throttled, partial")
                break
        text.append(f"{total} new {lane} leads")
    st = store.load()
    waiting = sum(1 for c in st.get("leads", {}).values() if c["status"] == "new")
    return f"[IG] scout: {', '.join(text)}. {waiting} waiting for your yes/no in the app."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--business", action="store_true")
    ap.add_argument("--people", action="store_true")
    ap.add_argument("--market", default="philly", choices=list(MARKETS))
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--decide", nargs="+", metavar=("@user", "yes|no"))
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.daily:
        msg = daily()
        print(msg)
        try:
            import bridge
            bridge.text_me(msg)
        except Exception as e:
            print("text failed:", e)
    elif a.business or a.people:
        print(run_lane("business" if a.business else "people", a.market, a.limit))
    elif a.decide:
        print(decide(a.decide[0], a.decide[1].lower() in ("yes", "y", "like"), " ".join(a.decide[2:])))
    else:
        st = store.load()
        leads = st.get("leads", {})
        for lane in ("business", "people"):
            ls = [c for c in leads.values() if c["lane"] == lane]
            print(f"{lane}: {len(ls)} leads, {sum(1 for c in ls if c['status']=='new')} new, taste {taste_summary(st.get('taste', {}).get(lane, {}), lane)}")
            for c in sorted(ls, key=lambda c: -c.get("score", 0))[:8]:
                print(f"  {c['status']:7} {c.get('score', 0):5} @{c['username']:24} {c.get('followers')} {c.get('category') or ''} | {c['reason'][:50]}")
        print("last run:", st.get("scout", {}).get("last_run"))


if __name__ == "__main__":
    main()
