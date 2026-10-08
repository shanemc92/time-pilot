"""Run with:  python -m unittest discover -s tests -v
No database or environment needed - sanitize.py is pure."""
import datetime
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sanitize as S  # noqa: E402

PAYLOAD = '"><img src=x onerror=alert(1)>'


def sample_state():
    """The demo account from sample_data.py, so this test tracks it."""
    path = os.path.join(os.path.dirname(__file__), "..", "sample_data.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    body = src[src.index("def build():"):src.index("def main():")]
    ns = {"datetime": datetime, "time": time}
    exec(body, ns)  # noqa: S102 - our own file
    return ns["build"]()


class RoundTrip(unittest.TestCase):
    def test_clean_data_passes_through_unchanged(self):
        st = sample_state()
        st["activeTimer"] = {"label": "x", "taskId": "t2", "category": "general",
                             "start": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        self.assertEqual(S.clean_state_values(st), st)


class Injection(unittest.TestCase):
    def test_settings_fields_that_reach_attributes(self):
        out = S.clean_settings({
            "dayStart": PAYLOAD, "dayEnd": PAYLOAD, "viewStart": PAYLOAD, "lunchEnd": PAYLOAD,
            "rounding": PAYLOAD, "remindLead": PAYLOAD, "theme": PAYLOAD, "font": PAYLOAD,
            "pinView": PAYLOAD, "quickTags": [PAYLOAD, "alarm_clock"]})
        self.assertEqual(out["dayStart"], "08:00")
        self.assertEqual(out["dayEnd"], "18:00")
        self.assertEqual((out["viewStart"], out["lunchEnd"]), ("", ""))
        self.assertEqual((out["rounding"], out["remindLead"]), (5, 0))
        self.assertEqual((out["theme"], out["font"], out["pinView"]), ("dark", "sans", ""))
        self.assertEqual(out["quickTags"], ["alarm_clock"])

    def test_columns_and_categories(self):
        out = S.clean_settings({
            "columns": [{"k": PAYLOAD, "label": PAYLOAD, "done": True}],
            "categories": [{"k": PAYLOAD, "label": "ok", "color": "red;background:url(x)"}]})
        self.assertRegex(out["columns"][0]["k"], r"^col_[0-9a-f]{8}$")
        self.assertRegex(out["categories"][0]["k"], r"^c_[0-9a-f]{8}$")
        self.assertEqual(out["categories"][0]["color"], "#888888")

    def test_duplicate_keys_are_made_unique(self):
        out = S.clean_settings({"columns": [{"k": "a", "label": "A"}, {"k": "a", "label": "B"}]})
        self.assertEqual(len({c["k"] for c in out["columns"]}), 2)

    def test_task_fields(self):
        (t,) = S.clean_tasks([{"id": PAYLOAD, "title": "x", "est": PAYLOAD, "slot": PAYLOAD,
                               "slotDay": PAYLOAD, "doneAt": PAYLOAD, "column": "today"}])
        self.assertRegex(t["id"], r"^[0-9a-f]{8}$")
        self.assertEqual((t["est"], t["slot"], t["slotDay"], t["doneAt"]), (30, None, None, None))

    def test_timelog_fields(self):
        (e,) = S.clean_timelog([{"date": "2026-01-02", "start": PAYLOAD, "end": PAYLOAD,
                                 "minutes": PAYLOAD, "label": "x", "taskId": PAYLOAD}])
        self.assertEqual((e["start"], e["end"], e["minutes"], e["taskId"]), ("00:00", "00:00", 1, None))
        self.assertEqual(S.clean_timelog([{"date": PAYLOAD}]), [])

    def test_reminders_in_a_backup_get_the_same_checks_as_the_api(self):
        good = {"message": "m", "next_fire": 1, "priority": 3}
        self.assertEqual(S.clean_reminder(dict(good, priority=PAYLOAD))["priority"], 3)
        self.assertEqual(S.clean_reminder(dict(good, priority=99))["priority"], 3)
        self.assertEqual(S.clean_reminder(dict(good, tag=PAYLOAD))["tag"], "alarm_clock")
        self.assertIsNone(S.clean_reminder(dict(good, next_fire="soon")))
        self.assertIsNone(S.clean_reminder(dict(good, recurring=True, interval_type="fortnights")))
        self.assertIsNone(S.clean_reminder(dict(good, message="x" * 501)))

    def test_reminder_weekday_filter(self):
        rep = {"message": "m", "next_fire": 1, "recurring": True, "interval_type": "days", "interval_value": 1}
        clean = S.clean_reminder
        self.assertEqual(clean(dict(rep, days=[4, 0, 0, 3]))["days"], [0, 3, 4])
        self.assertEqual(clean(dict(rep, days=[0, 1, 2, 3, 4, 5, 6]))["days"], None)   # all seven = every day
        self.assertEqual(clean(dict(rep, days=[7, -1, "1", True, 1.0]))["days"], None)  # nothing usable
        self.assertEqual(clean(dict(rep, days=PAYLOAD))["days"], None)
        self.assertEqual(clean(dict(rep, days=[]))["days"], None)
        # Only hourly/daily repeats carry one, and a one-off never does.
        self.assertEqual(clean(dict(rep, interval_type="weeks", days=[1]))["days"], None)
        self.assertEqual(clean(dict(rep, interval_type="hours", days=[1]))["days"], [1])
        self.assertEqual(clean(dict(rep, recurring=False, days=[1]))["days"], None)
        # tz only travels with a filter, and only as a plain zone name.
        self.assertEqual(clean(dict(rep, days=[1], tz="Europe/Dublin"))["tz"], "Europe/Dublin")
        self.assertIsNone(clean(dict(rep, days=[1], tz=PAYLOAD))["tz"])
        self.assertIsNone(clean(dict(rep, days=[1], tz="../etc/passwd"))["tz"])
        self.assertIsNone(clean(dict(rep, tz="Europe/Dublin"))["tz"])

    def test_active_timer_needs_a_real_start(self):
        self.assertIsNone(S.clean_active_timer({"label": "x", "start": PAYLOAD}))
        self.assertIsNone(S.clean_active_timer("nope"))


class TypeConfusion(unittest.TestCase):
    """Values the UI calls string methods on must always be strings."""

    def test_wrong_types_are_repaired_or_dropped(self):
        self.assertEqual(S.clean_tasks([{"title": 5}, {"title": None}, "x", 7, None]), [])
        (t,) = S.clean_tasks([{"title": "ok", "category": 3, "column": ["a"], "details": {"a": 1}}])
        self.assertEqual((t["category"], t["column"]), ("", ""))
        self.assertNotIn("details", t)
        (sec,) = S.clean_notes([{"title": 1, "items": [{"text": 2}, "x"]}])
        self.assertEqual((sec["title"], sec["items"][0]["text"]), ("", ""))
        (sn,) = S.clean_library([{"title": 1, "body": 2, "category": 3, "desc": 4}])
        self.assertEqual((sn["title"], sn["body"], sn["category"], sn["desc"]), ("", "", "", ""))

    def test_scratch_active_pad_always_exists(self):
        out = S.clean_scratch({"pads": [{"id": "a", "name": "n", "text": "t"}], "active": "gone"})
        self.assertEqual(out["active"], "a")

    def test_unknown_keys_survive_only_as_scalars(self):
        out = S.clean_settings({"futureFlag": True, "futureList": [1, 2], "futureObj": {"a": 1}})
        self.assertIs(out["futureFlag"], True)
        self.assertNotIn("futureList", out)
        self.assertNotIn("futureObj", out)

    def test_never_raises_on_garbage(self):
        rnd = random.Random(1)
        atoms = [None, True, 0, -1, 3.5, 10 ** 30, "", "x", PAYLOAD, [], {}, [1], {"a": 1}, "99:99", "\u0000"]

        def junk(depth=0):
            r = rnd.random()
            if depth > 2 or r < 0.5:
                return rnd.choice(atoms)
            if r < 0.75:
                return [junk(depth + 1) for _ in range(rnd.randint(0, 3))]
            return {rnd.choice(["id", "k", "title", "label", "start", "est", "items", "pads", "date",
                                "message", "next_fire", "columns", "categories", "settings"]): junk(depth + 1)
                    for _ in range(rnd.randint(0, 4))}

        for _ in range(2000):
            state = {k: junk() for k in ("settings", "tasks", "timelog", "notes", "snippets", "pastes",
                                          "scratch", "calSeen", "reminders", "activeTimer")}
            for k in ("tasks", "timelog", "notes", "snippets", "pastes", "reminders"):
                if not isinstance(state[k], list):
                    state[k] = [state[k]]
            for k in ("settings", "scratch", "calSeen"):
                if not isinstance(state[k], dict):
                    state[k] = {}
            S.clean_state_values(state)


if __name__ == "__main__":
    unittest.main()
