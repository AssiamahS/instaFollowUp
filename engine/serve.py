#!/usr/bin/env python3
"""HTTP API for the iPhone app, on the Tailscale IP (port 8801, registered in the port list).

Auth: header X-Token must equal ~/.instafollowup/token (the app stores it in Settings).

GET  /status                       counts + queue summary
GET  /threads                      every thread, newest activity first (no messages)
GET  /threads/<user>               one thread with messages + pending draft
POST /threads/<user>               {mode?, playbook?, goal?, muted?, coach?}   coach = append a correction
POST /threads/<user>/approve       send the pending draft
POST /threads/<user>/reject        {feedback?}  drop the draft; with feedback: coach + redraft
POST /threads/<user>/say           {text}  send exactly this
POST /threads/<user>/draft         ask the brain for a fresh draft now
GET  /outreach                     queue + settings
POST /outreach                     {username, note, playbook?}  add to queue
POST /outreach/settings            {daily_cap?, hours?}
DELETE /outreach/<user>            drop a queued item
GET  /playbooks                    {name: text}
POST /playbooks/<name>             {text}
GET  /coaching                     global corrections per playbook
POST /coaching/<playbook>          {add} | {remove_index}
GET  /log                          last 200 engine log lines
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge
import store

PORT = 8801
TOKEN = store.token()
_busy = threading.Lock()


def thread_summary(t):
    last = t["messages"][-1] if t["messages"] else None
    return {k: t[k] for k in ("key", "username", "display", "mode", "playbook", "goal", "source",
                                "last_in_ts", "last_out_ts", "last_out_by", "followups_sent", "muted", "coaching")} | {
        "last": last, "pending": t.get("pending"), "count": len(t["messages"]),
        "activity": max(t["last_in_ts"], t["last_out_ts"], t.get("created", 0)),
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def auth(self):
        if self.headers.get("X-Token") != TOKEN:
            self.send(401, {"error": "bad token"})
            return False
        return True

    def do_GET(self):
        if not self.auth():
            return
        p = self.path.split("?")[0].strip("/").split("/")
        st = store.load()
        if p == ["status"]:
            return self.send(200, {"text": bridge.status_text(st), "threads": len(st["threads"]),
                                   "pending": sum(1 for t in st["threads"].values() if t.get("pending")),
                                   "queue": sum(1 for i in st["outreach"]["queue"] if i["status"] == "queued")})
        if p == ["threads"]:
            ts = sorted((thread_summary(t) for t in st["threads"].values()), key=lambda t: -t["activity"])
            return self.send(200, ts)
        if p[0] == "threads" and len(p) == 2:
            t = st["threads"].get(store.key_for(p[1]))
            return self.send(200, thread_summary(t) | {"messages": t["messages"]}) if t else self.send(404, {"error": "no thread"})
        if p == ["outreach"]:
            return self.send(200, st["outreach"])
        if p == ["playbooks"]:
            return self.send(200, {n: store.playbook_text(n) for n in store.playbook_names()})
        if p == ["coaching"]:
            return self.send(200, st["coaching_global"])
        if p == ["log"]:
            return self.send(200, st["log"][-200:])
        if p == ["leads"]:
            import scout
            leads = list(st.get("leads", {}).values())
            leads.sort(key=lambda c: (c["status"] != "new", -c.get("score", 0), -c.get("found", 0)))
            return self.send(200, {"leads": [{k: v for k, v in c.items() if k != "id"} for c in leads[:300]],
                                   "taste": {ln: scout.taste_summary(st.get("taste", {}).get(ln, {}), ln) for ln in ("business", "people")},
                                   "last_run": st.get("scout", {}).get("last_run"), "markets": list(scout.MARKETS)})
        if p == ["followers"]:
            import followers
            return self.send(200, followers.status())
        self.send(404, {"error": "unknown"})

    def do_DELETE(self):
        if not self.auth():
            return
        p = self.path.strip("/").split("/")
        if p[0] == "outreach" and len(p) == 2:
            with store.locked() as st:
                q = st["outreach"]["queue"]
                st["outreach"]["queue"] = [i for i in q if not (store.key_for(i["username"]) == store.key_for(p[1]) and i["status"] == "queued")]
            return self.send(200, {"ok": True})
        self.send(404, {"error": "unknown"})

    def do_POST(self):
        if not self.auth():
            return
        p = self.path.strip("/").split("/")
        b = self.body()
        try:
            self._deferred = None
            with _busy, store.locked() as st:
                out = self.route_post(st, p, b)
            if self._deferred:
                out = {"ok": True, "status": self._deferred()}
            return self.send(200, out)
        except KeyError as e:
            return self.send(404, {"error": str(e)})
        except Exception as e:
            return self.send(500, {"error": str(e)[:300]})

    def route_post(self, st, p, b):
        if p[0] == "threads" and len(p) >= 2:
            t = st["threads"].get(store.key_for(p[1]))
            if not t:
                if len(p) == 2:
                    t = store.get_thread(st, p[1], create=True, mode="approve", playbook=b.get("playbook", "personal"))
                else:
                    raise KeyError("no thread")
            action = p[2] if len(p) > 2 else None
            if action is None:
                for k in ("mode", "playbook", "goal", "muted", "display"):
                    if k in b:
                        t[k] = b[k]
                if t["mode"] not in store.MODES:
                    t["mode"] = "approve"
                if b.get("coach"):
                    t["coaching"].append(b["coach"].strip())
                return thread_summary(t)
            if action == "approve":
                if not t.get("pending"):
                    raise KeyError("nothing pending")
                via = bridge.deliver(st, t, t["pending"]["text"], t["pending"]["kind"])
                return {"ok": True, "via": via}
            if action == "reject":
                t["pending"] = None
                fb = (b.get("feedback") or "").strip()
                if fb:
                    t["coaching"].append(fb)
                    kind = "reply" if t["messages"] and t["messages"][-1]["from"] == "them" else "followup"
                    d = bridge.make_draft(st, t, kind)
                    if not d["skip"]:
                        t["pending"] = {"text": d["text"], "kind": kind, "created": bridge.now(), "why": d["why"]}
                    return {"ok": True, "draft": d}
                return {"ok": True}
            if action == "say":
                text = (b.get("text") or "").strip()
                if not text:
                    raise KeyError("text required")
                return {"ok": True, "via": bridge.deliver(st, t, text, "reply")}
            if action == "draft":
                kind = b.get("kind") or ("reply" if t["messages"] and t["messages"][-1]["from"] == "them" else
                                         ("opener" if not t["messages"] else "followup"))
                d = bridge.make_draft(st, t, kind)
                t["pending"] = None if d["skip"] else {"text": d["text"], "kind": kind, "created": bridge.now(), "why": d["why"]}
                return {"ok": True, "draft": d}
        if p == ["outreach"]:
            u = (b.get("username") or "").lstrip("@").strip()
            if not u:
                raise KeyError("username required")
            st["outreach"]["queue"].append({"username": u, "note": b.get("note", ""), "playbook": b.get("playbook", "dj_pitch"),
                                            "added": bridge.now(), "status": "queued"})
            return st["outreach"]
        if p == ["outreach", "settings"]:
            for k in ("daily_cap", "hours"):
                if k in b:
                    st["outreach"][k] = b[k]
            return st["outreach"]
        if p[0] == "playbooks" and len(p) == 2:
            name = p[1].lower()
            if not name.replace("_", "").isalnum():
                raise KeyError("bad name")
            with open(os.path.join(store.PLAYBOOKS, f"{name}.md"), "w") as fh:
                fh.write(b.get("text", ""))
            return {"ok": True}
        if p[0] == "coaching" and len(p) == 2:
            lst = st["coaching_global"].setdefault(p[1], [])
            if b.get("add"):
                lst.append(b["add"].strip())
            if "remove_index" in b and 0 <= b["remove_index"] < len(lst):
                lst.pop(b["remove_index"])
            return st["coaching_global"]
        if p == ["run"]:
            threading.Thread(target=bridge.run, daemon=True).start()
            return {"ok": True}
        if p[0] == "leads" and len(p) == 2:
            import scout
            decision = (b.get("decision") or "").lower()
            if decision not in ("like", "pass"):
                raise KeyError("decision must be like or pass")
            # scout.decide takes its own lock; release ours first
            store.save(st)
            self._deferred = lambda: scout.decide(p[1], decision == "like", (b.get("note") or "").strip())
            return {"ok": True}
        if p == ["scout", "run"]:
            here = os.path.dirname(os.path.abspath(__file__))
            args = [sys.executable, os.path.join(here, "scout.py")]
            if b.get("lane") in ("business", "people") and b.get("market"):
                args += ["--" + b["lane"], "--market", b["market"], "--limit", str(int(b.get("limit", 8)))]
            else:
                args.append("--daily")
            import subprocess
            subprocess.Popen(args, stdout=open("/tmp/instafollowup-scout.log", "a"), stderr=subprocess.STDOUT)
            return {"ok": True, "started": args[2:]}
        if p == ["followers", "run"]:
            here = os.path.dirname(os.path.abspath(__file__))
            import subprocess
            subprocess.Popen([sys.executable, os.path.join(here, "followers.py")], stdout=open("/tmp/instafollowup-followers.log", "a"), stderr=subprocess.STDOUT)
            return {"ok": True}
        raise KeyError("unknown")


if __name__ == "__main__":
    print(f"instafollowup api on :{PORT}, token {TOKEN[:6]}…", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
