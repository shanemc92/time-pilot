"""ntfy integration: URL validation, sending, and the reminder dispatcher.

Two distinct paths use this module:

  * Scheduled reminders (the Notifications tab) are fired by the background
    dispatcher below, so they arrive whether or not anyone has the app open.
    That's the whole point of a reminder - it replaces the cron job in the
    standalone scheduler this replicates.

  * Slotted-task / meeting reminders are pushed by the browser when its
    pop-up fires (see checkReminders in index.html), because they're tied to
    that pop-up. They need a tab open somewhere.
"""

import calendar as _calendar
import ipaddress
import logging
import os
import socket
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

logger = logging.getLogger("timepilot")

# ntfy servers are very often self-hosted on the same LAN as TimePilot, which
# is exactly what the calendar fetch's SSRF guard exists to forbid. Rather
# than silently punching a hole in that guard, private targets are opt-in:
# the operator states the intent once via env, instead of any registered user
# being able to aim the server at internal addresses through Settings.
ALLOW_PRIVATE = os.environ.get("TIMEPILOT_ALLOW_PRIVATE_NTFY", "").lower() in ("1", "true", "yes")

DISPATCH_INTERVAL = 30      # seconds between due-reminder scans
_MAX_DAY_SKIPS = 500        # > the 168 hourly steps in a week, so any satisfiable filter resolves
_ADVISORY_LOCK_KEY = 0x7C1D_9E27   # arbitrary, app-specific


def check_ntfy_url(url):
    """(ok, reason). Mirrors _check_external_url in app.py, but honours
    ALLOW_PRIVATE - see the note above."""
    if not url:
        return False, "no ntfy server URL set"
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "not a valid URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"unsupported URL scheme {parsed.scheme!r} - use http or https"
    if not parsed.hostname:
        return False, "URL has no hostname"
    if ALLOW_PRIVATE:
        return True, None
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        return False, f"couldn't resolve hostname {parsed.hostname!r} ({e})"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False, (f"{parsed.hostname!r} resolves to a private/internal address ({ip}). "
                           "If that's your own ntfy server, set TIMEPILOT_ALLOW_PRIVATE_NTFY=true "
                           "in .env and restart.")
    return True, None


def send(settings, message, title="TimePilot", tags="alarm_clock", priority=3):
    """Post one notification using this user's ntfy settings. (ok, reason)."""
    base = (settings.get("ntfyUrl") or "").strip().rstrip("/")
    topic = (settings.get("ntfyTopic") or "").strip()
    if not topic:
        return False, "no ntfy topic set"
    ok, reason = check_ntfy_url(base)
    if not ok:
        return False, reason

    headers = {
        "Title": _header_safe(title),
        "Priority": str(priority),
        "Tags": _header_safe(tags),
    }
    icon = (settings.get("ntfyIcon") or "").strip()
    if icon:
        icon_ok, _ = check_ntfy_url(icon)
        # A bad icon URL shouldn't cost you the notification itself.
        if icon_ok:
            headers["Icon"] = _header_safe(icon)
    try:
        r = requests.post(f"{base}/{quote(topic, safe='')}",
                          data=message.encode("utf-8"), headers=headers, timeout=10)
        r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)


def _header_safe(v):
    """HTTP headers are latin-1 and single-line. ntfy reads Title/Tags from
    headers, so anything with a newline (or an emoji in a task name) would
    otherwise raise inside requests and lose the notification entirely.

    'ignore' drops non-ASCII chars outright - 'replace' (the previous
    behaviour) substitutes each one with a literal '?', which is why a
    leading emoji like the "⏰" in "⏰ Task due: ..." showed up as "? Task
    due: ..." in the notification title. ntfy also renders the Tags header
    as its own emoji, so the title didn't need one anyway.
    """
    v = str(v).replace("\n", " ").replace("\r", " ").encode("ascii", "ignore").decode("ascii")
    return " ".join(v.split())[:250]   # collapse the double space the dropped emoji leaves behind


def zone(name):
    """The ZoneInfo for an IANA name, or None (-> the server's local zone)."""
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _step(ts, interval_type, interval_value):
    """One interval after ts, ignoring any weekday filter."""
    dt = datetime.fromtimestamp(ts)
    if interval_type == "hours":
        return ts + interval_value * 3600
    if interval_type == "days":
        return ts + interval_value * 86400
    if interval_type == "weeks":
        return ts + interval_value * 7 * 86400
    if interval_type == "months":
        month = dt.month - 1 + interval_value
        year = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, _calendar.monthrange(year, month)[1])
        # replace() keeps the wall-clock time, so a monthly reminder stays at
        # the same hour across a DST boundary rather than drifting by an hour.
        return int(dt.replace(year=year, month=month, day=day).timestamp())
    return ts + 86400


def first_allowed(ts, interval_type, interval_value, days, tz=None):
    """ts itself if it falls on one of `days` (0=Mon .. 6=Sun, read in `tz`),
    else the first later occurrence of the schedule that does. The schedule
    keeps its own rhythm and the excluded days are just masked out of it, so
    "every 3 hours, weekdays" resumes Monday on the same 3-hour grid.

    None if the interval never lands on a selected day (e.g. every 7 days
    from a Monday, Tuesdays only). Callers reject that when a reminder is
    saved; the cap just keeps the dispatcher from ever spinning on it.
    """
    if not days:
        return ts
    for _ in range(_MAX_DAY_SKIPS):
        if datetime.fromtimestamp(ts, tz).weekday() in days:
            return ts
        ts = _step(ts, interval_type, interval_value)
    return None


def advance_time(ts, interval_type, interval_value, days=None, tz=None):
    """Next fire timestamp after advancing by one interval, skipping any
    occurrence that lands on a day outside `days`."""
    nxt = _step(ts, interval_type, interval_value)
    allowed = first_allowed(nxt, interval_type, interval_value, days, tz)
    return nxt if allowed is None else allowed


def dispatch_for_user(settings, reminders, now=None):
    """Fire everything due. Returns (kept_reminders, sent_count).

    Pure-ish and side-effecting only via send(), so it's directly testable.
    """
    now = int(time.time()) if now is None else now
    kept, sent = [], 0
    for r in reminders:
        if int(r.get("next_fire", 0)) > now:
            kept.append(r)
            continue
        ok, reason = send(settings, r.get("message", ""),
                          title="TimePilot Reminder",
                          tags=r.get("tag") or "alarm_clock",
                          priority=int(r.get("priority", 3)))
        if ok:
            sent += 1
        else:
            logger.warning("ntfy send failed for reminder %s: %s", r.get("id"), reason)
        if r.get("recurring") and r.get("interval_type") and r.get("interval_value"):
            step = (r["interval_type"], int(r["interval_value"]), r.get("days"), zone(r.get("tz")))
            nxt = advance_time(int(r["next_fire"]), *step)
            # Catch up past any occurrences missed while the app was down,
            # rather than firing one notification per missed occurrence.
            while nxt <= now:
                nxt = advance_time(nxt, *step)
            r["next_fire"] = nxt
            kept.append(r)
        # one-time reminders are simply not kept
    return kept, sent


def _try_advisory_lock(db):
    """Only one gunicorn worker should dispatch, or every reminder arrives N
    times. A Postgres session-level advisory lock held on a dedicated
    connection is the cheapest way to elect one, and releases by itself if
    that worker dies. Non-Postgres (a dev SQLite run) is single-process
    anyway, so it just proceeds.

    Returns (got_lock, conn) - conn is None on SQLite, or the connection that
    must stay open to keep holding the lock.
    """
    from sqlalchemy import text
    if not db.engine.url.get_backend_name().startswith("postgres"):
        return True, None
    conn = db.engine.connect()
    try:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}).scalar()
    except Exception:
        conn.close()
        raise
    if not got:
        conn.close()
        return False, None
    return True, conn   # keep the connection open - closing it drops the lock


def start_dispatcher(app, db, load_settings, load_reminders, save_reminders, list_user_ids):
    """Spawn the background dispatch thread. Callbacks are passed in so this
    module doesn't import app.py back (circular)."""

    def loop():
        # Stagger startup so workers don't all race for the lock at the same
        # instant on boot.
        time.sleep(2 + (os.getpid() % 5))
        holder, conn = False, None
        announced = False
        while True:
            try:
                # Standby workers keep retrying rather than exiting: if the
                # worker holding the lock is recycled or killed, Postgres drops
                # its lock and one of the others has to pick the work up -
                # otherwise reminders would silently stop until a full restart.
                if not holder:
                    with app.app_context():
                        holder, conn = _try_advisory_lock(db)
                    if not holder:
                        time.sleep(DISPATCH_INTERVAL)
                        continue
                    if not announced:
                        logger.info("ntfy dispatcher: active in pid %s (every %ss)", os.getpid(), DISPATCH_INTERVAL)
                        announced = True
                elif conn is not None and conn.closed:
                    # Lost the connection, so we've lost the lock with it.
                    holder, conn, announced = False, None, False
                    continue

                with app.app_context():
                    now = int(time.time())
                    for uid in list_user_ids():
                        settings = load_settings(uid)
                        if not (settings.get("ntfyTopic") or "").strip():
                            continue
                        rs = load_reminders(uid)
                        if not any(int(r.get("next_fire", 0)) <= now for r in rs):
                            continue   # nothing due - skip the write entirely
                        kept, sent = dispatch_for_user(settings, rs)
                        save_reminders(uid, kept)
                        if sent:
                            logger.info("ntfy dispatcher: sent %d reminder(s) for user %s", sent, uid)
            except Exception as e:
                # Never let one bad iteration kill the thread - it won't come back.
                logger.error("ntfy dispatcher iteration failed: %s", e)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                holder, conn, announced = False, None, False
            time.sleep(DISPATCH_INTERVAL)

    t = threading.Thread(target=loop, name="ntfy-dispatcher", daemon=True)
    t.start()
    return t
