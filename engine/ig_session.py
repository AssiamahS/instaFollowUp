"""Instagram's own web endpoints with the Dia session cookie (read-only discovery).

Ported from post4me/scripts/competitors.py. Used by scout.py to find businesses and people:
topsearch (keyword -> accounts/places/hashtags), hashtag + location feeds (recent posters),
profile info (followers, category, bio) and a user's recent posts.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2

COOKIE_DB = os.path.expanduser("~/Library/Application Support/Dia/User Data/Default/Cookies")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
APP_ID = "936619743392459"


def dia_cookies(domain_like="%instagram.com"):
    pw = subprocess.check_output(["security", "find-generic-password", "-s", "Dia Safe Storage", "-w"]).strip()
    key = PBKDF2(pw, b"saltysalt", dkLen=16, count=1003)
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy(COOKIE_DB, tmp)
    con = sqlite3.connect(tmp)
    out = {}
    for host, name, blob in con.execute(
            "select host_key,name,encrypted_value from cookies where host_key like ?", (domain_like,)):
        if not blob.startswith(b"v10"):
            out[name] = blob.decode(errors="ignore")
            continue
        p = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(blob[3:])
        p = p[:-p[-1]]
        if len(p) > 32 and p[:32] == hashlib.sha256(host.encode()).digest():
            p = p[32:]
        out[name] = p.decode(errors="ignore")
    con.close()
    os.remove(tmp)
    return out


def _num(s):
    s = s.replace(",", "")
    mult = {"K": 1_000, "M": 1_000_000}.get(s[-1:], 1)
    return int(float(s.rstrip("KM")) * mult)


class IG:
    def __init__(self):
        ck = dia_cookies()
        if "sessionid" not in ck:
            raise RuntimeError("no instagram sessionid in Dia; log into instagram.com in Dia first")
        keep = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")
        self.h = {
            "x-ig-app-id": APP_ID, "User-Agent": UA, "Referer": "https://www.instagram.com/",
            "x-csrftoken": ck.get("csrftoken", ""), "x-requested-with": "XMLHttpRequest",
            "Cookie": "; ".join(f"{k}={v}" for k, v in ck.items() if k in keep),
        }
        self.calls = 0

    def get(self, url, retries=2):
        for i in range(retries + 1):
            try:
                self.calls += 1
                req = urllib.request.Request(url, headers=self.h)
                return json.load(urllib.request.urlopen(req, timeout=30))
            except urllib.error.HTTPError as e:
                if e.code in (429, 401) or i == retries:
                    raise
                time.sleep(2 + 2 * i)
        return {}

    def topsearch(self, query):
        """{users:[{username, full_name, pk, is_verified, is_private, follower_count?}], places:[...], hashtags:[...]}"""
        q = urllib.parse.quote(query)
        d = self.get(f"https://www.instagram.com/api/v1/web/search/topsearch/?context=blended&query={q}")
        users = [r["user"] for r in d.get("users", []) if r.get("user")]
        places = [p["place"] for p in d.get("places", []) if p.get("place")]
        tags = [h["hashtag"] for h in d.get("hashtags", []) if h.get("hashtag")]
        return {"users": users, "places": places, "hashtags": tags}

    def profile(self, handle, raise_429=False):
        """Full profile dict or None. With raise_429 the caller sees instagram's throttle."""
        for url in (f"https://i.instagram.com/api/v1/users/web_profile_info/?username={handle}",
                    f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}"):
            try:
                u = self.get(url, retries=0)["data"]["user"]
            except urllib.error.HTTPError as e:
                if e.code == 429 and raise_429:
                    raise
                time.sleep(1.5)
                continue
            except (KeyError, TypeError, OSError):
                time.sleep(1.5)
                continue
            posts = [e["node"] for e in (u.get("edge_owner_to_timeline_media") or {}).get("edges", [])]
            return {
                "id": u["id"], "username": u["username"], "full_name": u.get("full_name") or "",
                "biography": u.get("biography") or "", "category": u.get("category_name") or "",
                "is_business": bool(u.get("is_business_account")), "is_private": bool(u.get("is_private")),
                "is_verified": bool(u.get("is_verified")),
                "followers": (u.get("edge_followed_by") or {}).get("count"),
                "following": (u.get("edge_follow") or {}).get("count"),
                "posts": (u.get("edge_owner_to_timeline_media") or {}).get("count"),
                "pic": u.get("profile_pic_url_hd") or u.get("profile_pic_url"),
                "external_url": u.get("external_url") or "",
                "recent": [{
                    "shortcode": n.get("shortcode"), "thumb": n.get("thumbnail_src") or n.get("display_url"),
                    "caption": " ".join(e["node"].get("text", "") for e in (n.get("edge_media_to_caption") or {}).get("edges", []))[:300],
                    "taken_at": n.get("taken_at_timestamp"), "likes": (n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}).get("count"),
                    "location": (n.get("location") or {}).get("name"),
                } for n in posts[:9]],
            }
        return None

    def hashtag_posters(self, tag):
        """Usernames + captions of recent/top posts under #tag."""
        d = self.get(f"https://www.instagram.com/api/v1/tags/web_info/?tag_name={urllib.parse.quote(tag)}")
        return _posters(d.get("data") or d)

    def location_posters(self, location_id):
        d = self.get(f"https://www.instagram.com/api/v1/locations/web_info/?location_id={location_id}&show_nearby=false")
        return _posters((d.get("native_location_data") or d.get("data") or d))


def _posters(d):
    out = []
    for section_key in ("recent", "top", "ranked"):
        sec = d.get(section_key) or {}
        for s in sec.get("sections") or []:
            medias = ((s.get("layout_content") or {}).get("medias")) or []
            for m in medias:
                media = m.get("media") or {}
                u = media.get("user") or media.get("owner") or {}
                cap = (media.get("caption") or {}).get("text") or ""
                if u.get("username"):
                    out.append({"username": u["username"], "full_name": u.get("full_name") or "", "pk": u.get("pk") or u.get("id"),
                                "is_private": u.get("is_private"), "caption": cap[:300], "taken_at": media.get("taken_at"),
                                "location": (media.get("location") or {}).get("name"), "section": section_key})
    return out
