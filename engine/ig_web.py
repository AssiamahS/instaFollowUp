"""Instagram web through the already-running Dia browser (CDP :9223).

This is the only way to START a conversation (the official API can only reply) and
the only way to message someone after Instagram's 24h reply window closed.
Same recipe as the LinkedIn DM driver: navigate, execCommand insertText, verify.

Dia must be running with --remote-debugging-port=9223 (it is, for post4me/formfill).
NEVER launch a second Dia instance.
"""
from __future__ import annotations

import json
import time
import urllib.request

import websocket

CDP = "http://127.0.0.1:9223"
IG = "https://www.instagram.com"


class WebError(Exception):
    pass


class Tab:
    def __init__(self):
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json"))
        mine = [t for t in tabs if t["type"] == "page" and "instagram.com" in t.get("url", "")]
        if mine:
            t = mine[0]
        else:
            req = urllib.request.Request(f"{CDP}/json/new?{IG}/direct/inbox/", method="PUT")
            t = json.load(urllib.request.urlopen(req))
        self.id = t["id"]
        self.ws = websocket.create_connection(t["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
        self._n = 0
        self.call("Page.enable")
        # Dia keeps this tab in the background where the viewport collapses to 0x0 and every
        # bounding box is garbage; pin a real viewport so geometry-based reads work.
        self.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        self._n += 1
        self.ws.send(json.dumps({"id": self._n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._n:
                if "error" in m:
                    raise WebError(m["error"])
                return m.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise WebError(res.get("description"))
        return res.get("value")

    def goto(self, url, settle=4.0):
        self.call("Page.navigate", url=url)
        time.sleep(settle)
        for _ in range(20):
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.5)

    def url(self):
        return self.js("location.href")

    def click_text(self, text, roles=("button", "link")):
        expr = """(() => {
          const want = %s;
          const els = [...document.querySelectorAll('div[role=button],button,a[role=button],a,span[role=button]')];
          const hit = els.find(e => e.innerText && e.innerText.trim() === want && e.offsetParent !== null);
          if (!hit) return false;
          hit.click(); return true; })()""" % json.dumps(text)
        return bool(self.js(expr))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def logged_in(tab):
    return bool(tab.js("document.cookie.includes('ds_user_id=')"))


COMPOSER = "div[role=textbox][contenteditable=true], textarea[placeholder*='Message']"


def dismiss_prompts(tab):
    for label in ("Not Now", "Not now", "Cancel"):
        if tab.click_text(label):
            time.sleep(1)


def open_thread(tab, username):
    """Land in the DM thread with @username. Returns the thread URL (/direct/t/<id>/).

    Uses the inbox's New message dialog, which works whether or not I follow them
    (the profile page only shows a Message button for accounts I follow)."""
    username = username.lstrip("@").strip()
    tab.goto(f"{IG}/direct/inbox/", settle=5)
    if "/accounts/login" in tab.url():
        raise WebError("not logged in to instagram.com in Dia")
    dismiss_prompts(tab)
    clicked = tab.js("""(() => { const s = document.querySelector('svg[aria-label="New message"]'); if (!s) return false;
        const b = s.closest('div[role=button],button,a'); if (!b) return false; b.click(); return true; })()""")
    if not clicked:
        raise WebError("New message button not found in the inbox")
    for _ in range(20):
        time.sleep(0.5)
        if tab.js("!!document.querySelector('div[role=dialog] input[name=queryBox]')"):
            break
    typed = tab.js("""(() => { const i = document.querySelector('div[role=dialog] input[name=queryBox]'); if (!i) return false;
        i.focus(); document.execCommand('insertText', false, %s); return i.value; })()""" % json.dumps(username))
    if not typed:
        raise WebError("new-message search box not found")
    picked = False
    for _ in range(16):
        time.sleep(0.5)
        picked = tab.js("""(() => {
          const want = %s;
          for (const cb of document.querySelectorAll('div[role=dialog] input[name=IGDRecipientContactSearchResultCheckbox]')) {
            let e = cb;
            for (let d = 0; d < 7 && e; d++, e = e.parentElement) {
              if (e.querySelectorAll('input[name=IGDRecipientContactSearchResultCheckbox]').length !== 1) continue;
              const lines = (e.innerText || '').split('\\n').map(s => s.trim().toLowerCase());
              if (lines.includes(want)) { cb.click(); return true; }
            }
          }
          return false; })()""" % json.dumps(username.lower()))
        if picked:
            break
    if not picked:
        dismiss_prompts(tab)
        raise WebError(f"@{username} not in new-message search results")
    time.sleep(1)
    if not tab.click_text("Chat"):
        raise WebError("Chat button not found in new-message dialog")
    for _ in range(30):
        time.sleep(0.5)
        if "/direct/t/" in tab.url():
            break
    if "/direct/t/" not in tab.url():
        raise WebError(f"@{username}: dialog did not open a thread (url {tab.url()})")
    time.sleep(2.5)
    dismiss_prompts(tab)
    return tab.url()


def read_thread(tab, limit=30):
    """Best-effort transcript of the open thread: [{from: me|them, text}] oldest first.

    Instagram's DOM is obfuscated (no roles on bubbles). Bubbles are dir=auto spans;
    mine sit in the right half of the pane, theirs in the left."""
    return tab.js("""(() => {
      const comp = document.querySelector(%s);
      const cr = comp ? comp.getBoundingClientRect() : {left: 0, right: window.innerWidth};
      const els = [...document.querySelectorAll('span[dir=auto], div[dir=auto]')]
        .filter(e => e.offsetParent !== null && !(comp && comp.contains(e)) && !e.querySelector('[dir=auto]'));
      const out = [];
      for (const e of els) {
        const txt = (e.innerText || '').trim();
        if (!txt || txt.length > 2000) continue;
        const r = e.getBoundingClientRect();
        if (r.top < 90 || r.width === 0) continue;             // header / hidden
        if (r.left < cr.left - 10) continue;                    // inbox sidebar, not the thread
        const mine = (r.left + r.right) / 2 > (cr.left + cr.right) / 2;
        out.push({from: mine ? 'me' : 'them', text: txt.slice(0, 600), y: r.top});
      }
      return out.sort((a, b) => a.y - b.y).slice(-%d).map(({from, text}) => ({from, text}));
    })()""" % (json.dumps(COMPOSER), limit)) or []


def _key(tab, key, code, vk, modifiers=0, commands=None):
    extra = {"commands": commands} if commands else {}
    tab.call("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers, **extra)
    tab.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers)


def clear_composer(tab):
    tab.js("(() => { const c = document.querySelector(%s); if (c) c.focus(); })()" % json.dumps(COMPOSER))
    _key(tab, "a", "KeyA", 65, modifiers=4, commands=["selectAll"])
    _key(tab, "Backspace", "Backspace", 8)
    time.sleep(0.3)


def composer_text(tab):
    return (tab.js("(() => { const c = document.querySelector(%s); return c ? (c.innerText || c.value || '') : null; })()"
                   % json.dumps(COMPOSER)) or "").strip()


def type_message(tab, text):
    if not tab.js("!!document.querySelector(%s)" % json.dumps(COMPOSER)):
        raise WebError("composer not found")
    clear_composer(tab)
    tab.js("(() => { document.querySelector(%s).focus(); })()" % json.dumps(COMPOSER))
    tab.call("Input.insertText", text=text)       # Lexical editor: CDP insertText lands, .value= does not
    time.sleep(0.6)
    if composer_text(tab).replace("\n", " ")[:30] != text.strip().replace("\n", " ")[:30]:
        raise WebError(f"composer holds {composer_text(tab)[:40]!r}, expected {text[:40]!r}")


def send_message(tab, username, text):
    """Open @username's thread, type, send with Enter, verify the text shows up on my side.

    Returns the thread URL. Raises WebError if anything cannot be verified."""
    url = open_thread(tab, username)
    type_message(tab, text)
    _key(tab, "Enter", "Enter", 13)                # instagram web sends on Enter
    for _ in range(20):
        time.sleep(0.5)
        if not composer_text(tab):
            break
    if composer_text(tab):
        raise WebError("composer still holds the text; send did not go through")
    time.sleep(2)
    tail = read_thread(tab, 8)
    probe = text.strip().split("\n")[0][:40]
    if not any(m["from"] == "me" and probe in m["text"] for m in tail):
        raise WebError("sent text not visible on my side of the thread after send")
    return url


if __name__ == "__main__":
    import sys
    t = Tab()
    print("logged in:", logged_in(t))
    if len(sys.argv) > 1:
        print(open_thread(t, sys.argv[1]))
        for m in read_thread(t, 8):
            print(f"  {m['from']:4} {m['text'][:80]!r}")
        print("composer present:", t.js("!!document.querySelector(%s)" % json.dumps(COMPOSER)))
    t.close()
