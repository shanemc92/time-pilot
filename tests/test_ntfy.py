"""Run with:  python -m unittest discover -s tests -v
Covers the weekday filter on repeating reminders (ntfy.py). No database or
network needed - ntfy.send is stubbed where the dispatcher is exercised."""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ntfy  # noqa: E402

DUBLIN = ZoneInfo("Europe/Dublin")
UTC = ZoneInfo("UTC")
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]


def ts(y, m, d, hh=9, mm=0, tz=DUBLIN):
    return int(datetime(y, m, d, hh, mm, tzinfo=tz).timestamp())


def wd(t, tz=DUBLIN):
    return datetime.fromtimestamp(t, tz).weekday()


# 9 Oct 2026 is a Friday - see test_calendar_assumption.
FRI, SAT, SUN, MON, TUE = (ts(2026, 10, d) for d in (9, 10, 11, 12, 13))


class Calendar(unittest.TestCase):
    def test_calendar_assumption(self):
        self.assertEqual([wd(t) for t in (FRI, SAT, SUN, MON, TUE)], [4, 5, 6, 0, 1])


class Advance(unittest.TestCase):
    def test_no_filter_is_the_plain_interval(self):
        self.assertEqual(ntfy.advance_time(FRI, "days", 1), SAT)
        self.assertEqual(ntfy.advance_time(FRI, "days", 1, None, DUBLIN), SAT)
        self.assertEqual(ntfy.advance_time(FRI, "days", 1, [], DUBLIN), SAT)

    def test_daily_on_weekdays_skips_the_weekend(self):
        self.assertEqual(ntfy.advance_time(FRI, "days", 1, WEEKDAYS, DUBLIN), MON)
        self.assertEqual(ntfy.advance_time(MON, "days", 1, WEEKDAYS, DUBLIN), TUE)

    def test_daily_on_weekends_skips_the_week(self):
        self.assertEqual(ntfy.advance_time(SAT, "days", 1, WEEKENDS, DUBLIN), SUN)
        self.assertEqual(ntfy.advance_time(SUN, "days", 1, WEEKENDS, DUBLIN), SAT + 7 * 86400)

    def test_hourly_keeps_its_grid_across_the_gap(self):
        fri_9pm = ts(2026, 10, 9, 21)
        # 21:00 + 3h lands on Sat 00:00 (masked) ... and resumes Mon 00:00.
        self.assertEqual(ntfy.advance_time(fri_9pm, "hours", 3, WEEKDAYS, DUBLIN), ts(2026, 10, 12, 0))

    def test_every_other_day_is_masked_not_shifted(self):
        # Fri +2d = Sun (masked), +4d = Tue.
        self.assertEqual(ntfy.advance_time(FRI, "days", 2, WEEKDAYS, DUBLIN), TUE)

    def test_the_weekday_is_read_in_the_given_zone(self):
        # Fri 23:30 UTC is already Sat 00:30 in Dublin (BST).
        late = ts(2026, 10, 9, 23, 30, UTC)
        self.assertEqual(ntfy.first_allowed(late, "days", 1, WEEKDAYS, UTC), late)
        self.assertEqual(ntfy.first_allowed(late, "days", 1, WEEKDAYS, DUBLIN), ts(2026, 10, 12, 0, 30))   # Mon 00:30 Dublin

    def test_first_allowed_keeps_an_already_allowed_time(self):
        self.assertEqual(ntfy.first_allowed(MON, "days", 1, WEEKDAYS, DUBLIN), MON)
        self.assertEqual(ntfy.first_allowed(SAT, "days", 1, WEEKDAYS, DUBLIN), MON)

    def test_an_interval_that_never_hits_a_chosen_day_is_reported(self):
        # Every 7 days from a Monday can only ever be a Monday.
        self.assertIsNone(ntfy.first_allowed(MON, "days", 7, [1], DUBLIN))
        # ...and the dispatcher's advance_time degrades to the plain step rather than spinning.
        self.assertEqual(ntfy.advance_time(MON, "days", 7, [1], DUBLIN), MON + 7 * 86400)

    def test_zone_lookup(self):
        self.assertEqual(ntfy.zone("Europe/Dublin"), DUBLIN)
        for bad in (None, "", "Not/AZone", "../../etc/passwd", "Europe"):
            self.assertIsNone(ntfy.zone(bad), bad)


class Dispatch(unittest.TestCase):
    def run_dispatch(self, reminder, now):
        with mock.patch.object(ntfy, "send", return_value=(True, None)) as send:
            kept, sent = ntfy.dispatch_for_user({"ntfyTopic": "t"}, [reminder], now=now)
        return kept, sent, send

    def reminder(self, **kw):
        r = {"id": "r", "message": "m", "priority": 3, "tag": "alarm_clock", "next_fire": FRI,
             "recurring": True, "interval_type": "days", "interval_value": 1,
             "days": WEEKDAYS, "tz": "Europe/Dublin"}
        r.update(kw)
        return r

    def test_friday_reminder_rolls_to_monday(self):
        kept, sent, send = self.run_dispatch(self.reminder(), FRI + 60)
        self.assertEqual(sent, 1)
        self.assertEqual(kept[0]["next_fire"], MON)
        send.assert_called_once()

    def test_catching_up_after_downtime_fires_once_and_lands_on_an_allowed_day(self):
        # App was down Fri -> Wed morning: one notification, next one Wed 09:00.
        kept, sent, _ = self.run_dispatch(self.reminder(), ts(2026, 10, 14, 8))
        self.assertEqual(sent, 1)
        self.assertEqual(kept[0]["next_fire"], ts(2026, 10, 14, 9))

    def test_weekends_only(self):
        kept, _, _ = self.run_dispatch(self.reminder(next_fire=SAT, days=WEEKENDS), SAT + 60)
        self.assertEqual(kept[0]["next_fire"], SUN)

    def test_reminders_without_the_new_fields_still_work(self):
        r = self.reminder()
        del r["days"], r["tz"]
        kept, _, _ = self.run_dispatch(r, FRI + 60)
        self.assertEqual(kept[0]["next_fire"], SAT)


if __name__ == "__main__":
    unittest.main()
