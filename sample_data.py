#!/usr/bin/env python3
"""Seed a demo user with example data so you can try TimePilot out of the box.

    python sample_data.py                              # user 'demo' / 'demo1234'
    python sample_data.py --username bob --password mypassword123
    python sample_data.py --force                       # reset the user's data if they exist
    python sample_data.py --no-ics                      # skip the demo .ics calendar

Run it with the same environment as the app (DATABASE_URL, FLASK_SECRET_KEY,
TIMEPILOT_MASTER_KEY), against the same database - e.g. inside the container:

    docker compose exec timepilot python sample_data.py

Dates are generated relative to today, so the board, Export tab, and calendar
always look populated no matter when you run it. The demo calendar is seeded
via the same "uploaded .ics file" storage a real Settings > Calendar upload
uses, so it behaves exactly like one - including being skipped in favour of
a real icsUrl if you set one afterwards.
"""
import argparse
import datetime
import sys
import time

import icalendar

from app import app, load_state, save_state, JSON_DOMAINS, set_user_ics_bytes
from extensions import db
from models import User


def build_ics(today):
    """A small demo calendar, stored as this user's "uploaded file" fallback
    (see Settings > Calendar) - so the Today view and meeting reminders have
    something to show without needing a real ICS URL. Covers the cases the
    app's calendar handling actually branches on: a daily recurring meeting,
    a plain one-off, an all-day event, and a title matching the default
    ignoreEvents filter (to demonstrate that it's actually excluded).
    """
    cal = icalendar.Calendar()
    cal.add("prodid", "-//TimePilot Demo//timepilot//")
    cal.add("version", "2.0")

    def event(uid, summary, start, end=None, all_day=False, rrule=None, location=None):
        ev = icalendar.Event()
        ev.add("summary", summary)
        ev.add("uid", f"{uid}@timepilot-demo")
        ev.add("dtstamp", datetime.datetime.now())
        if all_day:
            ev.add("dtstart", start)                       # date, not datetime -> all-day
            ev.add("dtend", end or (start + datetime.timedelta(days=1)))
        else:
            ev.add("dtstart", start)
            ev.add("dtend", end)
        if location:
            ev.add("location", location)
        if rrule:
            ev.add("rrule", rrule)
        cal.add_component(ev)

    def at(day, h, m=0):
        return datetime.datetime.combine(day, datetime.time(h, m))

    yesterday = today - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)

    # Daily recurring standup, going back a couple of weeks so past days'
    # exported timesheets line up with a calendar entry too.
    event("standup", "Team standup", at(today - datetime.timedelta(days=14), 9, 0), at(today - datetime.timedelta(days=14), 9, 15),
          rrule={"freq": "daily", "count": 60})
    event("sprint-planning", "Sprint planning", at(yesterday, 10, 0), at(yesterday, 11, 0), location="Meeting Room 2")
    event("client-demo", "Client demo", at(today, 15, 0), at(today, 16, 0), location="Zoom")
    # Matches the default Settings > Calendar "ignore" list - shows up in the
    # raw ICS but should NOT appear in the Today view.
    event("private-appt", "Private Appointment", at(today, 12, 30), at(today, 13, 0))
    event("one-on-one", "1:1 with manager", at(tomorrow, 11, 0), at(tomorrow, 11, 30))
    event("offsite", "Team offsite", tomorrow + datetime.timedelta(days=1), all_day=True)

    return cal.to_ical()


def build():
    today = datetime.date.today()
    y = today - datetime.timedelta(days=1)
    d2 = today - datetime.timedelta(days=2)
    d3 = today - datetime.timedelta(days=3)

    return {
        "settings": {
            "icsUrl": "",
            "theme": "aurora",
            "font": "sans",
            "dayStart": "09:00",
            "dayEnd": "17:00",
            "rounding": 15,
            "remind": True,
            "remindLead": 5,
            "remindMeetings": False,
            "syncLastTaskEnd": True,
            "ntfyUrl": "https://ntfy.sh",
            "ntfyTopic": "",
            "ntfyIcon": "",
            "ntfyTasks": False,
            "quickTags": ["alarm_clock", "mailbox_with_mail", "warning", "rotating_light",
                          "calendar", "computer", "bulb", "white_check_mark",
                          "closed_lock_with_key", "wastebasket"],
            "interrupts": ["Message", "Email", "Call", "Meeting"],
            "ignoreEvents": ["Private Appointment"],
            "categories": [
                {"k": "general", "label": "General", "color": "#5b9bd5"},
                {"k": "meetings", "label": "Meetings", "color": "#57b76a"},
                {"k": "admin", "label": "Admin", "color": "#a17ae0"},
            ],
        },
        "tasks": [
            {"id": "t1", "title": "Reply to project emails", "category": "admin", "column": "today", "est": 30, "slot": "09:30"},
            {"id": "t2", "title": "Draft weekly status update", "category": "general", "column": "today", "est": 45, "slot": None},
            {"id": "t3", "title": "Review pull requests", "category": "general", "column": "today", "est": 60, "slot": "14:00"},
            {"id": "t4", "title": "Prepare slides for team demo", "category": "meetings", "column": "week", "est": 90, "slot": None},
            {"id": "t5", "title": "Update project documentation", "category": "admin", "column": "week", "est": 60, "slot": None},
            {"id": "t9", "title": "Follow up with client on proposal", "category": "admin", "column": "week", "est": 20, "slot": None},
            {"id": "t6", "title": "Plan next sprint", "category": "meetings", "column": "nextweek", "est": 120, "slot": None},
            {"id": "t10", "title": "Refactor auth module", "category": "general", "column": "nextweek", "est": 180, "slot": None},
            {"id": "t7", "title": "Quarterly goals review", "category": "general", "column": "nextmonth", "est": 60, "slot": None},
            {"id": "t11", "title": "Renew SSL certificates", "category": "admin", "column": "nextmonth", "est": 30, "slot": None},
            {"id": "t8", "title": "Set up local dev environment", "category": "admin", "column": "done", "est": 45, "slot": None, "doneAt": today.isoformat()},
            {"id": "t12", "title": "Fix login page styling", "category": "general", "column": "done", "est": 30, "slot": None, "doneAt": y.isoformat()},
        ],
        "timelog": [
            {"id": "e0", "date": d3.isoformat(), "label": "Team standup", "taskId": None, "source": "cal", "category": "meetings", "start": "09:00", "end": "09:15", "minutes": 15},
            {"id": "e0b", "date": d3.isoformat(), "label": "Onboarding new starter", "taskId": None, "category": "admin", "start": "09:20", "end": "10:15", "minutes": 55},
            {"id": "e0c", "date": d3.isoformat(), "label": "Call - vendor renewal", "taskId": None, "category": "admin", "start": "10:20", "end": "10:40", "minutes": 20},
            {"id": "e0d", "date": d3.isoformat(), "label": "Feature development", "taskId": None, "category": "general", "start": "11:00", "end": "12:30", "minutes": 90},
            {"id": "e0e", "date": d3.isoformat(), "label": "Feature development", "taskId": None, "category": "general", "start": "13:15", "end": "16:00", "minutes": 165},
            {"id": "e1", "date": d2.isoformat(), "label": "Team standup", "taskId": None, "source": "cal", "category": "meetings", "start": "09:00", "end": "09:15", "minutes": 15},
            {"id": "e2", "date": d2.isoformat(), "label": "Feature development", "taskId": None, "category": "general", "start": "09:20", "end": "11:30", "minutes": 130},
            {"id": "e3", "date": d2.isoformat(), "label": "Email - client query", "taskId": None, "category": "admin", "start": "11:30", "end": "11:50", "minutes": 20},
            {"id": "e4", "date": d2.isoformat(), "label": "Code review", "taskId": None, "category": "general", "start": "13:00", "end": "14:30", "minutes": 90},
            {"id": "e4b", "date": d2.isoformat(), "label": "Message - deploy window", "taskId": None, "category": "admin", "start": "14:30", "end": "14:40", "minutes": 10},
            {"id": "e5", "date": y.isoformat(), "label": "Team standup", "taskId": None, "source": "cal", "category": "meetings", "start": "09:00", "end": "09:15", "minutes": 15},
            {"id": "e6", "date": y.isoformat(), "label": "Sprint planning", "taskId": None, "source": "cal", "category": "meetings", "start": "10:00", "end": "11:00", "minutes": 60},
            {"id": "e7", "date": y.isoformat(), "label": "Documentation updates", "taskId": None, "category": "admin", "start": "11:15", "end": "12:30", "minutes": 75},
            {"id": "e8", "date": y.isoformat(), "label": "Bug fixing", "taskId": None, "category": "general", "start": "13:30", "end": "16:00", "minutes": 150},
            {"id": "e8b", "date": y.isoformat(), "label": "Fix login page styling", "taskId": "t12", "category": "general", "start": "16:00", "end": "16:30", "minutes": 30},
            {"id": "e9", "date": today.isoformat(), "label": "Team standup", "taskId": None, "source": "cal", "category": "meetings", "start": "09:00", "end": "09:15", "minutes": 15},
            {"id": "e10", "date": today.isoformat(), "label": "Set up local dev environment", "taskId": "t8", "category": "admin", "start": "09:15", "end": "10:00", "minutes": 45},
            {"id": "e11", "date": today.isoformat(), "label": "Reply to project emails", "taskId": "t1", "category": "admin", "start": "10:00", "end": "10:25", "minutes": 25},
        ],
        "notes": [
            {"id": "n1", "title": "To follow up", "items": [
                {"id": "i1", "text": "Chase sign-off on the design doc"},
                {"id": "i2", "text": "Book meeting room for Thursday demo"},
            ]},
            {"id": "n2", "title": "Questions", "items": [
                {"id": "i3", "text": "Which environment should the release go to first?"},
                {"id": "i4", "text": "Confirm deadline for the quarterly report"},
            ]},
            {"id": "n3", "title": "Ideas", "items": [
                {"id": "i5", "text": "Automate the weekly status email"},
                {"id": "i6", "text": "Add a dark-mode toggle to the client portal"},
            ]},
            {"id": "n4", "title": "Links", "items": [
                {"id": "i7", "text": "https://github.com/shanemc92/TimePilot/issues?q=is%3Aopen+is%3Aissue+label%3Aneeds-triage"},
                {"id": "i8", "text": "Shared drive: /Projects/2026/Q3-planning/budget-and-headcount-v3-FINAL.xlsx"},
            ]},
        ],
        "snippets": [
            {"id": "s1", "title": "Git - undo last commit", "category": "Git", "desc": "Keep the changes staged", "body": "git reset --soft HEAD~1"},
            {"id": "s2", "title": "Find large files", "category": "Shell", "desc": "Top 10 by size in current dir", "body": "du -ah . | sort -rh | head -n 10"},
            {"id": "s3", "title": "Python venv", "category": "Python", "desc": "Create and activate", "body": "python -m venv .venv\nsource .venv/bin/activate   # Windows: .venv\\Scripts\\activate"},
            {"id": "s4", "title": "Pretty-print JSON", "category": "Shell", "desc": "", "body": "cat file.json | python -m json.tool"},
            {"id": "s5", "title": "Docker - clean up", "category": "Docker", "desc": "Remove stopped containers, dangling images, unused networks", "body": "docker system prune -f"},
            {"id": "s6", "title": "SQL - table sizes", "category": "SQL", "desc": "Postgres, largest tables first", "body": "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))\nFROM pg_catalog.pg_statio_user_tables\nORDER BY pg_total_relation_size(relid) DESC\nLIMIT 10;"},
        ],
        "pastes": [
            {"id": "p1", "title": "Meeting follow-up", "category": "Email", "desc": "Standard reply after a call", "body": "Hi {name},\n\nThanks for the call today. To summarise what we agreed:\n\n- {point 1}\n- {point 2}\n\nI'll follow up on the above and circle back by {date}.\n\nBest regards"},
            {"id": "p2", "title": "Status update template", "category": "Reports", "desc": "Weekly format", "body": "## Weekly Update\n\n**Done this week**\n- \n\n**In progress**\n- \n\n**Blockers**\n- \n\n**Next week**\n- "},
            {"id": "p3", "title": "Out of office", "category": "Email", "desc": "", "body": "I'm currently out of office and will respond on my return on {date}. For anything urgent, please contact {colleague}."},
            {"id": "p4", "title": "Incident update", "category": "Reports", "desc": "Posted to the status page / incident channel", "body": "**Update - {time}**\n\nWe're continuing to investigate {issue}. Impact: {impact}. Next update within {interval}."},
        ],
        "reminders": [
            {"id": "r1", "message": "Stand-up prep - check overnight alerts", "priority": 3, "tag": "alarm_clock",
             "next_fire": int(time.time()) + 3600, "recurring": True, "interval_type": "days", "interval_value": 1,
             "days": None, "tz": None, "created": int(time.time())},
            {"id": "r2", "message": "Submit weekly timesheet", "priority": 3, "tag": "white_check_mark",
             "next_fire": int(time.time()) + 6 * 3600, "recurring": False,
             "interval_type": None, "interval_value": None, "days": None, "tz": None, "created": int(time.time())},
        ],
        "activeTimer": None,
        "calSeen": {},
    }


def main():
    ap = argparse.ArgumentParser(description="Seed a demo user with example data.")
    ap.add_argument("--username", default="demo")
    ap.add_argument("--password", default="demo1234")
    ap.add_argument("--force", action="store_true", help="reset the user's data if the account already exists")
    ap.add_argument("--no-ics", action="store_true", help="skip seeding the demo .ics calendar file")
    args = ap.parse_args()

    with app.app_context():
        user = User.query.filter_by(username=args.username.lower()).first()
        if user and not args.force:
            print(f"User '{args.username}' already exists. Re-run with --force to reset their data.")
            return 1
        if not user:
            user = User(username=args.username.lower())
            user.set_password(args.password)
            db.session.add(user)
            db.session.commit()
            print(f"Created user '{args.username}'.")
        else:
            print(f"Resetting data for existing user '{args.username}'.")

        save_state(user.id, build())

        if not args.no_ics:
            # Same storage path as a real Settings > Calendar upload, and the
            # same encrypted-at-rest column - so this is exactly what "an
            # ICS URL is empty or unreachable" falls back to (see app.py's
            # /api/calendar), not a special demo-only code path.
            set_user_ics_bytes(user.id, build_ics(datetime.date.today()))
            print("Seeded a demo .ics calendar (Settings > Calendar shows it as an uploaded file).")

        print(f"\nLog in at /login\n  username: {args.username}\n  password: {args.password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
