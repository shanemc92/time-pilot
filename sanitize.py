"""Server-side validation of the user's saved state.

The browser escapes everything it renders, but the server is the last line
of defence for data that arrives through PUT /api/state and /api/import - a
hand-edited backup file, an old client, a script. This module normalises
every field the UI reads, so a crafted or corrupt document can neither
inject markup nor make the page throw (for example a number where the UI
expects a string, which would leave the board blank until the database is
edited by hand).

Design rules:
- Repair, don't reject. A bad value is replaced with a safe one (or the
  record is dropped when it can't stand on its own); the request as a whole
  still succeeds, so one stray field never loses the rest of a restore.
- Known fields are validated strictly. Unknown fields are kept only if they
  are plain scalars, so a newer client's extra settings survive a round trip
  through an older server.
- Long free text (task details, snippet bodies, scratchpads) is only
  type-checked. The request size cap already bounds it, and truncating it
  would silently destroy someone's notes.
- Never raises on bad input: everything returns a value.
"""
import re
import uuid

_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})?$")
_TAG = re.compile(r"^[A-Za-z0-9_+-]{1,64}$")
_TZ = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")   # IANA name; looked up (and ignored if unknown) at dispatch
_WORD = re.compile(r"^[a-z0-9_-]{1,24}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")

MAX_COLUMNS = 6            # matches COL_MAX in the client
MAX_CATEGORIES = 50
MAX_SHORT_LISTS = 100      # interrupts, quick tags
MAX_IGNORE = 500
MAX_PADS = 8               # matches SCR_MAX in the client


def _new_id():
    return uuid.uuid4().hex[:8]


def _str(v, default="", limit=None):
    if not isinstance(v, str):
        return default
    return v[:limit] if limit else v


def _int(v, default, lo, hi):
    if isinstance(v, bool):
        return default
    try:
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return n if lo <= n <= hi else default


def _hm(v, default=""):
    return v if isinstance(v, str) and _HM.match(v) else default


def _date(v, default=None):
    return v if isinstance(v, str) and _DATE.match(v) else default


def _key(v):
    return v if isinstance(v, str) and _KEY.match(v) else None


def _bool(v, default):
    return v if isinstance(v, bool) else default


def _keep_unknown(rec, known, out):
    """Carry over keys we don't know about, but only as harmless scalars."""
    for k, v in rec.items():
        if k in known or not isinstance(k, str) or len(k) > 64:
            continue
        if v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 2000):
            out[k] = v
    return out


def _dicts(seq):
    return [x for x in seq if isinstance(x, dict)] if isinstance(seq, list) else []


def _str_list(v, limit, item_limit, pattern=None, default=None):
    if not isinstance(v, list):
        return default
    out = []
    for x in v:
        if isinstance(x, str) and x.strip() and len(x) <= item_limit and (pattern is None or pattern.match(x)):
            out.append(x)
        if len(out) >= limit:
            break
    return out


# ---- settings -----------------------------------------------------------
_SETTINGS_KNOWN = {
    "icsUrl", "theme", "pinView", "font", "dayStart", "dayEnd", "viewStart",
    "viewEnd", "lunchStart", "lunchEnd", "rounding", "remind", "remindLead",
    "remindMeetings", "syncLastTaskEnd", "browserNotifications", "ntfyUrl",
    "ntfyTopic", "ntfyIcon", "ntfyTasks", "columns", "quickTags", "interrupts",
    "ignoreEvents", "categories",
}


def clean_settings(s):
    if not isinstance(s, dict):
        return {}
    out = {}
    # Free-form strings that end up in inputs, URLs or attributes.
    for k, limit in (("icsUrl", 2048), ("ntfyUrl", 2048), ("ntfyTopic", 200), ("ntfyIcon", 2048)):
        if k in s:
            out[k] = _str(s[k], "", limit)
    # Theme/font names are looked up against a list client-side; here we only
    # require a plain word so no version has to know the other's theme list.
    for k, default in (("theme", "dark"), ("font", "sans")):
        if k in s:
            out[k] = s[k] if isinstance(s[k], str) and _WORD.match(s[k]) else default
    if "pinView" in s:
        out["pinView"] = s["pinView"] if isinstance(s["pinView"], str) and _WORD.match(s["pinView"]) else ""
    for k, default in (("dayStart", "08:00"), ("dayEnd", "18:00")):
        if k in s:
            out[k] = _hm(s[k], default)
    for k in ("viewStart", "viewEnd", "lunchStart", "lunchEnd"):
        if k in s:
            out[k] = _hm(s[k], "")
    if "rounding" in s:
        out["rounding"] = _int(s["rounding"], 5, 1, 1440)
    if "remindLead" in s:
        out["remindLead"] = _int(s["remindLead"], 0, 0, 1440)
    for k, default in (("remind", True), ("remindMeetings", False), ("syncLastTaskEnd", True),
                       ("browserNotifications", False), ("ntfyTasks", False)):
        if k in s:
            out[k] = _bool(s[k], default)

    if "columns" in s and isinstance(s["columns"], list):
        cols, seen = [], set()
        for c in _dicts(s["columns"])[:MAX_COLUMNS]:
            k = _key(c.get("k")) or "col_" + _new_id()
            if k in seen:
                k = "col_" + _new_id()
            seen.add(k)
            label = _str(c.get("label"), "", 40).strip() or k
            col = {"k": k, "label": label}
            if c.get("done"):
                col["done"] = True
            cols.append(col)
        if cols:
            out["columns"] = cols
    if "categories" in s and isinstance(s["categories"], list):
        cats, seen = [], set()
        for c in _dicts(s["categories"])[:MAX_CATEGORIES]:
            k = _key(c.get("k")) or "c_" + _new_id()
            if k in seen:
                k = "c_" + _new_id()
            seen.add(k)
            color = c.get("color")
            cats.append({"k": k,
                         "label": _str(c.get("label"), "", 40).strip() or k,
                         "color": color if isinstance(color, str) and _COLOR.match(color) else "#888888"})
        if cats:
            out["categories"] = cats
    for k, limit, item_limit, pattern in (("quickTags", MAX_SHORT_LISTS, 64, _TAG),
                                          ("interrupts", MAX_SHORT_LISTS, 40, None),
                                          ("ignoreEvents", MAX_IGNORE, 200, None)):
        if k in s:
            lst = _str_list(s[k], limit, item_limit, pattern)
            if lst is not None:
                out[k] = lst
    return _keep_unknown(s, _SETTINGS_KNOWN, out)


# ---- record lists ---------------------------------------------------------
_TASK_KNOWN = {"id", "title", "category", "column", "est", "slot", "slotDay", "doneAt", "details"}


def clean_tasks(tasks):
    out = []
    for t in _dicts(tasks):
        title = _str(t.get("title"), "", 500).strip()
        if not title:
            continue
        rec = {
            "id": _key(t.get("id")) or _new_id(),
            "title": title,
            "category": _str(t.get("category"), "", 64),
            "column": _str(t.get("column"), "", 64),
            "est": _int(t.get("est"), 30, 1, 100000),
        }
        # Optional fields are only written when the input had them, so a clean
        # document passes through unchanged rather than gaining null keys.
        if "slot" in t:
            rec["slot"] = _hm(t["slot"], None)
        if "slotDay" in t:
            rec["slotDay"] = _date(t["slotDay"])
        if "doneAt" in t:
            rec["doneAt"] = _date(t["doneAt"])
        if isinstance(t.get("details"), str) and t["details"].strip():
            rec["details"] = t["details"]
        out.append(_keep_unknown(t, _TASK_KNOWN, rec))
    return out


_LOG_KNOWN = {"id", "date", "label", "taskId", "category", "start", "end", "minutes", "source"}


def clean_timelog(entries):
    out = []
    for e in _dicts(entries):
        date = _date(e.get("date"))
        if not date:
            continue
        rec = {
            "id": _key(e.get("id")) or _new_id(),
            "date": date,
            "label": _str(e.get("label"), "", 500),
            "category": _str(e.get("category"), "", 64),
            "start": _hm(e.get("start"), "00:00"),
            "end": _hm(e.get("end"), "00:00"),
            "minutes": _int(e.get("minutes"), 1, 0, 100000),
        }
        if "taskId" in e:
            rec["taskId"] = _key(e["taskId"])
        if e.get("source") == "cal":
            rec["source"] = "cal"
        out.append(_keep_unknown(e, _LOG_KNOWN, rec))
    return out


def clean_notes(sections):
    out = []
    for sec in _dicts(sections):
        items = []
        for it in _dicts(sec.get("items")):
            items.append({"id": _key(it.get("id")) or _new_id(), "text": _str(it.get("text"), "")})
        out.append({"id": _key(sec.get("id")) or _new_id(),
                    "title": _str(sec.get("title"), "", 200), "items": items})
    return out


def clean_library(items):
    """Snippets and clipboard entries share one shape."""
    out = []
    for it in _dicts(items):
        out.append({"id": _key(it.get("id")) or _new_id(),
                    "title": _str(it.get("title"), "", 500),
                    "category": _str(it.get("category"), "", 64),
                    "desc": _str(it.get("desc"), "", 2000),
                    "body": _str(it.get("body"), "")})
    return out


def clean_scratch(sc):
    if not isinstance(sc, dict):
        return {}
    pads = []
    for p in _dicts(sc.get("pads"))[:MAX_PADS]:
        pads.append({"id": _key(p.get("id")) or _new_id(),
                     "name": _str(p.get("name"), "", 100).strip() or "Note",
                     "text": _str(p.get("text"), "")})
    out = {"size": _int(sc.get("size"), 13, 8, 40),
           "wrap": _bool(sc.get("wrap"), True), "nums": _bool(sc.get("nums"), True)}
    if pads:
        out["pads"] = pads
        active = sc.get("active")
        out["active"] = active if any(p["id"] == active for p in pads) else pads[0]["id"]
    return out


def clean_cal_seen(seen):
    out = {}
    if not isinstance(seen, dict):
        return out
    for day, keys in seen.items():
        if isinstance(day, str) and _DATE.match(day) and isinstance(keys, list):
            out[day] = [k for k in keys if isinstance(k, str) and len(k) <= 500][:5000]
    return out


def clean_active_timer(t):
    if t is None or not isinstance(t, dict):
        return None
    start = t.get("start")
    if not (isinstance(start, str) and _ISO.match(start)):
        return None          # a timer with no valid start time can't be shown or logged
    return {"label": _str(t.get("label"), "", 500), "taskId": _key(t.get("taskId")),
            "category": _str(t.get("category"), "", 64) or None, "start": start}


def clean_reminder(r):
    """One stored reminder, or None. Same rules the /api/reminders endpoints
    enforce, applied to reminders arriving inside an imported backup."""
    if not isinstance(r, dict):
        return None
    msg = _str(r.get("message"), "").strip()
    if not msg or len(msg) > 500:
        return None
    if isinstance(r.get("next_fire"), bool):
        return None
    try:
        next_fire = int(r.get("next_fire"))
    except (TypeError, ValueError, OverflowError):
        return None
    priority = _int(r.get("priority"), 3, 1, 5)
    recurring = bool(r.get("recurring"))
    itype, ivalue = r.get("interval_type"), None
    if recurring:
        if itype not in ("hours", "days", "weeks", "months"):
            return None
        ivalue = _int(r.get("interval_value"), 0, 1, 100000)
        if ivalue < 1:
            return None
    else:
        itype = None
    days, tz = None, None
    if recurring and itype in ("hours", "days") and isinstance(r.get("days"), list):
        # 0=Mon .. 6=Sun. Anything unusable, or all seven, means "every day".
        picked = sorted({d for d in r["days"] if type(d) is int and 0 <= d <= 6})
        days = picked if 0 < len(picked) < 7 else None
    if days:
        tz = _str(r.get("tz"), "", 64)
        tz = tz if _TZ.match(tz) else None
    tag = _str(r.get("tag"), "").strip()[:60]
    return {"id": _key(r.get("id")) or uuid.uuid4().hex, "message": msg, "priority": priority,
            "tag": tag if _TAG.match(tag) else "alarm_clock", "next_fire": next_fire,
            "recurring": recurring, "interval_type": itype, "interval_value": ivalue,
            "days": days, "tz": tz,
            "created": _int(r.get("created"), 0, 0, 10 ** 11)}


def clean_reminders(rs):
    return [c for c in (clean_reminder(r) for r in rs) if c] if isinstance(rs, list) else []


_CLEANERS = {
    "settings": clean_settings, "tasks": clean_tasks, "timelog": clean_timelog,
    "notes": clean_notes, "snippets": clean_library, "pastes": clean_library,
    "scratch": clean_scratch, "calSeen": clean_cal_seen, "reminders": clean_reminders,
}


def clean_state_values(state):
    """Field-level pass over a state dict whose top-level keys and container
    types are already checked (app._clean_state). Returns a new dict."""
    out = {}
    for k, v in state.items():
        if k in _CLEANERS:
            out[k] = _CLEANERS[k](v)
        elif k == "activeTimer":
            out[k] = clean_active_timer(v)
        else:
            out[k] = v
    return out
