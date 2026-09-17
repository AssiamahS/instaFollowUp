"""Photo judge for the people lane: the same Gemini vision verdict the swiper uses (Tinder/Bumble),
applied to a public profile's pic + recent thumbnails. Key = Mac keychain `gemini`, model from
~/swiper/bumble/config.json (falls back to gemini-3.1-flash-lite). Rules are Sly's swiper rules.

judge(lead) -> (keep: bool, why: str, verdict: dict)
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

SWIPER_CFG = os.path.expanduser("~/swiper/bumble/config.json")
DEFAULT_VISION = {"gemini_model": "gemini-3.1-flash-lite", "reject_bodies": ["plus"], "min_body_conf": 0.5,
                  "min_quality": 5, "swimwear_auto_like": True, "curves_auto_like": 7,
                  "like_bodies": ["slim", "athletic"], "like_min_quality": 7, "min_feminine": 6}

PROMPT = (
    "You are rating a public Instagram profile for a personal filter. Look at ALL images (profile picture first, then "
    "recent posts of the same account; ignore text overlays) and return ONLY a JSON object, no prose:\n"
    '{"body":"slim|athletic|average|curvy|plus","body_confidence":0-1,"full_body_visible":true|false,'
    '"swimwear":true|false,"curves":0-10,"fit":0-10,"photo_quality":0-10,"grainy":true|false,"group_photo":true|false,'
    '"is_woman":true|false,"feminine":0-10,"is_person":true|false,"notes":"short"}\n'
    "is_person: false if the account is a business, brand, venue, meme page or the photos show no consistent person. "
    "body: overall body size of the account owner using the clearest full-body photo (plus = visibly heavy/plus-size). "
    "fit: 10 = visibly athletic/toned/gym-fit, 0 = not at all. curves: how pronounced hips/glutes/hourglass figure are. "
    "photo_quality: 10 = sharp, well lit; 0 = blurry, grainy, dark. grainy = true if most photos are low quality. "
    "group_photo = true if you cannot tell which person is the account owner. "
    "is_woman: is the account owner a woman (false for men, boys, or if you cannot tell). feminine: 0 = reads as a man/boy, 10 = unmistakably a woman."
)


def vision_cfg():
    try:
        v = json.load(open(SWIPER_CFG)).get("vision") or {}
    except (OSError, ValueError):
        v = {}
    return {**DEFAULT_VISION, **v}


def keychain(service):
    try:
        return subprocess.check_output(["security", "find-generic-password", "-s", service, "-w"], text=True).strip()
    except subprocess.CalledProcessError:
        return ""


def fetch_image(url, limit=2_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit)


def gemini(parts, model, key):
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 0, "maxOutputTokens": 800, "responseMimeType": "application/json"}}).encode()
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                                 data=body, headers={"Content-Type": "application/json"}, method="POST")
    d = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 + attempt * 10)
                continue
            raise RuntimeError(f"gemini http {e.code}: {e.read()[:160]!r}")
    if "error" in d:
        raise RuntimeError("gemini: " + d["error"].get("message", "")[:160])
    text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError("gemini: no json")
    return json.loads(m.group(0))


def verdict_for(lead, max_photos=6):
    key = keychain("gemini")
    if not key:
        raise RuntimeError("no gemini key in keychain")
    urls = [u for u in [lead.get("pic")] + [p.get("thumb") for p in lead.get("recent") or []] if u][:max_photos]
    parts = [{"text": PROMPT + "\nProfile text: " + " | ".join(x for x in (lead.get("full_name"), lead.get("category"), lead.get("biography")) if x)[:400]}]
    got = 0
    for u in urls:
        try:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(fetch_image(u)).decode()}})
            got += 1
        except Exception:
            continue
    if not got:
        raise RuntimeError("no photos could be downloaded")
    v = gemini(parts, vision_cfg()["gemini_model"], key)
    v["photos_seen"] = got
    return v


def _num(x, default=None):
    return float(x) if isinstance(x, (int, float)) else default


def decide(v, cfg=None):
    """Sly's swiper rules. Returns (keep, why)."""
    V = cfg or vision_cfg()
    if v.get("is_person") is False:
        return False, "not a person (business/page)"
    fem = _num(v.get("feminine"))
    if v.get("is_woman") is False or (fem is not None and fem < V["min_feminine"]):
        return False, f"not a woman (feminine {fem})"
    if v.get("group_photo") is True and _num(v.get("body_confidence"), 1) < V["min_body_conf"]:
        return False, "group photos, can't tell who"
    q = _num(v.get("photo_quality"))
    if v.get("grainy") is True or (q is not None and q < V["min_quality"]):
        return False, f"grainy / quality {q}"
    body = str(v.get("body", "")).lower()
    conf = _num(v.get("body_confidence"), 1)
    if body in [b.lower() for b in V["reject_bodies"]] and conf >= V["min_body_conf"]:
        return False, f"body {body}"
    fit, curves = _num(v.get("fit"), 0), _num(v.get("curves"), 0)
    if v.get("swimwear") is True and V["swimwear_auto_like"]:
        return True, f"swimwear, {body}, fit {fit:.0f}"
    if curves >= V["curves_auto_like"]:
        return True, f"curves {curves:.0f}, {body}"
    if body in [b.lower() for b in V["like_bodies"]] or fit >= 6:
        return True, f"{body}, fit {fit:.0f}"
    if body == "curvy":
        return True, f"curvy, curves {curves:.0f}"
    return False, f"{body}, fit {fit:.0f}, curves {curves:.0f}: not the type"


def bonus(v):
    """Extra ranking points so the fittest / most his type come up first."""
    b = 0.0
    b += _num(v.get("fit"), 0) * 0.6
    b += _num(v.get("curves"), 0) * 0.4
    b += 3 if v.get("swimwear") else 0
    b += 2 if str(v.get("body", "")).lower() in ("athletic", "slim") else 0
    b += _num(v.get("photo_quality"), 5) * 0.2
    return round(b, 1)


def judge(lead):
    v = verdict_for(lead)
    keep, why = decide(v)
    return keep, why, v


if __name__ == "__main__":
    import sys
    import store
    st = store.load()
    for u in sys.argv[1:]:
        c = st["leads"][store.key_for(u)]
        keep, why, v = judge(c)
        print(u, "KEEP" if keep else "DROP", why, {k: v.get(k) for k in ("body", "fit", "curves", "swimwear", "is_woman", "feminine", "photo_quality", "is_person")})
