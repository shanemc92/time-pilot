"""End-to-end smoke test, run by .github/workflows/smoke.yml (not by unittest
discovery - it needs a running container).

Talks to the app image on 127.0.0.1:5170, backed by Postgres and seeded with
sample_data.py's demo user, and plays the ntfy server itself on 127.0.0.1:8099
so the dispatcher has somewhere to post. Covers what the unit tests can't:
the real image, the Postgres driver, login with CSRF, export/import, the
reminders API and the background dispatcher (one elected across two gunicorn
workers, each reminder sent exactly once, a weekday-filtered one rolled on).
"""
import json, re, sys, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import requests

BASE = "http://127.0.0.1:5170"
got = []


class Recv(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        got.append((self.path, body, self.headers.get("Title")))
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass


threading.Thread(target=HTTPServer(("127.0.0.1", 8099), Recv).serve_forever, daemon=True).start()
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# 1. health (proves app boot + Postgres connection + create_all)
for _ in range(60):
    try:
        if requests.get(BASE + "/healthz", timeout=3).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(2)
check("healthz (app up, Postgres reachable)", requests.get(BASE + "/healthz").status_code == 200)

# 2. login through the real form, CSRF included
s = requests.Session()
tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', s.get(BASE + "/login").text)
check("login page has a CSRF token", bool(tok))
r = s.post(BASE + "/login", data={"csrf_token": tok.group(1), "username": "demo", "password": "demo1234"}, allow_redirects=False)
check("login succeeds", r.status_code in (302, 303), r.status_code)
check("index served once logged in", s.get(BASE + "/").status_code == 200)

# 3. state read, and an export -> import round trip (encryption + sanitize + SQLAlchemy writes)
st = s.get(BASE + "/api/state"); check("GET /api/state", st.status_code == 200 and len(st.json()["tasks"]) > 0)
exp = s.get(BASE + "/api/export"); check("GET /api/export", exp.status_code == 200)
imp = s.post(BASE + "/api/import", json=exp.json()); check("POST /api/import round trip", imp.status_code == 200, imp.text[:200])
check("state intact after import", len(s.get(BASE + "/api/state").json()["tasks"]) == len(st.json()["tasks"]))

# 4. point ntfy at ourselves
state = s.get(BASE + "/api/state").json()
state["settings"].update({"ntfyUrl": "http://127.0.0.1:8099", "ntfyTopic": "ci-topic"})
check("PUT /api/state (settings)", s.put(BASE + "/api/state", json=state).status_code == 200)

# 5. two due reminders: a one-off, and a daily weekday-filtered one allowed today
now = int(time.time())
today = datetime.now(ZoneInfo("Europe/Dublin")).weekday()
a = s.post(BASE + "/api/reminders", json={"message": "ci one-off", "next_fire": now - 60, "recurring": False})
b = s.post(BASE + "/api/reminders", json={"message": "ci weekday", "next_fire": now - 60, "recurring": True,
                                          "interval_type": "days", "interval_value": 1, "days": [today], "tz": "Europe/Dublin"})
check("create reminders", a.status_code == 201 and b.status_code == 201, (a.text, b.text))
check("filtered reminder kept its first fire (today is allowed)", b.json()["next_fire"] == now - 60, b.text)
bad = s.post(BASE + "/api/reminders", json={"message": "x", "next_fire": now, "recurring": True,
                                            "interval_type": "days", "interval_value": 1, "days": [9]})
check("bad days rejected with 400", bad.status_code == 400)

# 6. the live dispatcher (Postgres advisory lock, 2 gunicorn workers) must send each exactly once
deadline = time.time() + 120
while time.time() < deadline and len(got) < 2:
    time.sleep(2)
check("dispatcher sent both reminders", len(got) == 2, got)
print("   received:", [(p, b) for p, b, _ in got])
time.sleep(70)   # > two dispatch cycles: a second worker double-sending would show up now
check("no duplicate sends (single dispatcher elected)", len(got) == 2, got)
rs = {x["message"]: x for x in s.get(BASE + "/api/reminders").json()}
check("one-off reminder removed after firing", "ci one-off" not in rs)
nf = rs.get("ci weekday", {}).get("next_fire", 0)
check("weekday reminder rolled to next allowed day (~7 days)", now + 6 * 86400 < nf < now + 8 * 86400 + 3600, nf - now)

print("\nFAILED:" if fails else "\nALL PASSED", fails or "")
sys.exit(1 if fails else 0)
