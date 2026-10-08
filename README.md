# TimePilot

A self-hosted task board, day planner, one-at-a-time timer, and timesheet
exporter for multiple users - Kanban board, calendar-aware day view, code
snippet & clipboard libraries, all behind your own login. Data is encrypted
at rest in PostgreSQL.

**3.0 - Glass.** A ground-up visual refresh: frosted-glass panels, a floating
icon rail, and a glowing LCD timer. The functionality is identical to 2.x -
same backend, same data, same features. See [Versions](#versions) if you'd
rather stay on the classic look.

See [CHANGELOG.md](CHANGELOG.md) for what's changed release to release.

![TimePilot demo](docs/demo.webp)

![TimePilot 3.0 screenshot](docs/screenshot.png)

![TimePilot 3.0, Dark theme](docs/screenshot-dark.png)

*3.0 with the Aurora theme (the default, top) and the true-black Dark theme.*

## Versions

| | Look | Where to get it |
|---|---|---|
| **3.x - Glass** (current) | Frosted glass, icon rail, ambient light | `main`, image `:latest` / `:3` |
| **2.x - Classic** | Flat dark UI, top tab bar | branch `release/2.x`, tag `v2.7.0`, image `:2` / `:2.7` |

The two share the same backend and database schema, so you can switch
between them at any time without migrating or exporting anything - your
tasks, notes and settings are the same either way. (One small catch: 2.x has
no Aurora theme, so an account set to it falls back to Dark when opened in
2.x.)

**Stay on the classic look:** pin the image in `docker-compose.yml`
(`image: ghcr.io/shanemc92/timepilot:2`) then `docker compose pull && docker
compose up -d`. From source: `git clone --branch release/2.x
https://github.com/shanemc92/timepilot.git`. Pinned to `:latest`? You'll get
3.0 on your next pull. Security fixes are backported to 2.x.

![TimePilot 2.x (Classic)](docs/screenshot-classic.png)

*2.x (Classic), for comparison.*

**Browser support (3.x):** the frosted effect uses `backdrop-filter` - Chrome
and Edge 76+, Safari 9+, Firefox 103+. On anything older the panels fall back
to plain translucent surfaces with no blur; everything still works.

## Features

- **Multi-user** - sign up, log in, each user's board/tasks/notes/snippets
  are private to them.
- **Kanban board** - Today / This Week / Next Week / Next Month / Done, with
  drag-and-drop and customisable categories. Done clears itself daily (your
  time history is kept).
- **Day planner** - a timeline merging your calendar with slotted tasks.
  Drag to reschedule; "Auto" drops a task into the first free gap that fits,
  skipping over a lunch break if you've set one. Overlapping meetings and/or
  tasks are shown side by side rather than hiding each other.
- **One-at-a-time timer** - a CASIO-style LCD clock. Starting anything stops
  and logs the previous task. Click it to edit the running task's name,
  category, or start time.
- **Timesheet export** - editable per-day entries that aggregate into a
  clean text list, plus a date-range bulk export to CSV.
- **Notes, Snippets & Clipboard** - free-form note sections (reorderable,
  click a note to copy it), a searchable code-snippet library, and a
  Markdown-aware clipboard library for reusable text/templates.
- **Configurable board columns** - rename the kanban columns in Settings and
  run up to 6 of them. Defaults are Today, High Priority, Medium Priority,
  Low Priority, Done. The first column is always what the Today tab plans
  from, whatever it's called, and one column can be marked "clears daily".
- **Split view** - pin any tab to the left half of the screen and hop
  between the others on the right (Board next to Scratch, Today next to
  Snippets, and so on). Clicking the pinned tab swaps the two sides. The
  pinned tab is marked with a dot on the icon rail. The pin is remembered;
  narrow screens fall back to one tab at a time.
- **Themes** - Aurora (default), a true-black Dark, light, HTB and Dracula (Settings ->
  Appearance), all built on the glass look. On phones the icon rail becomes a
  bottom dock.
- **Scratchpad** - a plain-text scratch tab for quick copy/paste, with up
  to 8 named pads open at once (double-click a pad tab to rename it): line
  numbers, adjustable text size, word wrap, live line/word/char counts,
  find & replace (with regex and capture groups), and one-click transforms
  (remove blank lines, dedupe, sort, trim trailing space, case). Autosaves
  with everything else.
- **Notifications (ntfy)** - schedule one-off or recurring push reminders to
  your phone via [ntfy](https://ntfy.sh) (ntfy.sh or your own server). Sent
  by the server, so they arrive whether or not the app is open. Task and
  meeting pop-ups can optionally push to ntfy too.
- **Encrypted at rest** - every user's data is AES-256-GCM encrypted before
  it touches the database (see "Security notes" below).
- **Backup/restore** - export your account's data as a single JSON file
  from Settings, import it back (or into a different install) any time.

## Architecture

```
Internet -> your reverse proxy (elsewhere) -> TimePilot (Flask/gunicorn) -> PostgreSQL
```

Two containers: `timepilot` (the app) and `postgres` (its database, on an
internal-only network - nothing but the app can reach it, not even the
host). TimePilot doesn't run its own reverse proxy or manage TLS certs -
`docker-compose.yml` publishes it on `127.0.0.1:5170`, the standard
"reverse proxy on the same host" pattern. Point Traefik, Nginx, Caddy, or
whatever you already run at that address and configure routing/TLS there,
exactly as you would for any other upstream service - nothing
TimePilot-specific needed on the proxy side.

## Quick start

```bash
git clone https://github.com/shanemc92/timepilot.git
cd timepilot
cp .env.example .env
```

Generate the three required secrets and put each into `.env`:

```bash
# Postgres password (hex, so it's always safe inside a connection URL)
openssl rand -hex 24

# Flask session secret
python3 -c "import secrets; print(secrets.token_hex(32))"

# Data encryption master key (needs the `cryptography` package -
# `pip install cryptography` first if you don't have Python handy, or run
# this one line inside the container after first boot: see note below)
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

Three ways to run it from here, all equally supported:

**1. Pull the published image** (easiest, no build):

```bash
docker compose pull
docker compose up -d
```

**2. Build your own image** instead of using the published one:

```bash
docker compose up --build -d
```

Uses the `Dockerfile` in this repo and locally tags the result with the
same name as the published image, so it's a drop-in swap either way.

**3. Build from source, no Docker for the app itself** - for development,
or if you'd rather not containerize the app (you'll still need a
PostgreSQL instance somewhere):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL=postgresql://user:pass@localhost:5432/timepilot
export FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export TIMEPILOT_MASTER_KEY=$(python3 crypto.py)
export SESSION_COOKIE_SECURE=false   # only for local http:// testing

flask --app app run --debug
```

`flask run --debug` auto-reloads on file changes, useful for working on
the code. No Postgres handy? `docker run -p 5432:5432 postgres:16-alpine`
gets you one without touching the rest of this setup.

---

Whichever option you used, first boot creates the database tables
automatically. Visit `https://<your-domain>/signup` (or
`http://localhost:5170/signup` / `:5000` for the source-checkout case) to
create an account.

No reverse proxy at all, just testing options 1 or 2 on your own machine
over plain `http://`? Add `docker-compose.local.yml`, which turns off the
HTTPS-only cookie flag (needed for a real deployment, wrong for raw local
testing - browsers won't send a `Secure` cookie over an insecure
connection, so login would silently never persist a session without this):

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml \
  up -d --pull always timepilot postgres
```

**Demo data:** seed a demo account with example tasks/history/notes/snippets
and a demo calendar (a few days of meetings, so the Today view and calendar
reminders aren't empty either) so there's something to look at immediately:

```bash
docker compose exec timepilot python sample_data.py
# -> user 'demo', password 'demo1234' - change the password after logging in
# --no-ics skips the demo calendar; --force resets an existing demo account
```

**Running a public demo instance:** [`deploy/init-timepilot-demo.sh`](deploy/init-timepilot-demo.sh)
automates standing up a whole demo box from a fresh Ubuntu server - SSH
hardening (custom port, key-only login, fail2ban), a firewall locked to
SSH+HTTPS only, unattended security upgrades, Docker install, cloning this
repo, generating `.env` secrets, syncing a Cloudflare DNS record and
obtaining a Let's Encrypt certificate, an nginx reverse proxy, ntfy
notifications for setup progress/failures, and a nightly cron job that
fully wipes the database and reseeds it via `sample_data.py` with a fresh
random password each time (spliced into `TIMEPILOT_LOGIN_BANNER`). It's
idempotent - re-run it any time to pick up config changes without
disturbing what's already set up. Edit the variables block at the top of
the script before running it; every value is commented inline.

## Calendar (optional)

TimePilot reads your calendar from an **iCalendar (ICS)** feed - it never
connects to your mail account. Two ways to provide it, both in Settings
once you're logged in:

1. **Published ICS URL** - in Outlook/Google Calendar, publish your
   calendar and paste the ICS link in. Meetings refresh automatically
   (cached 5 min).
2. **Upload an .ics file** - a fallback if you don't have (or lose) a URL.

Recurring meetings are expanded correctly, and times use the server's
local timezone. Settings also has independent controls for work hours
(bounds "Auto" task-slotting), the calendar display range (what the Today
timeline actually shows - widen this to see or manually place out-of-hours
items without changing what counts as normal hours), and an optional lunch
break that "Auto" slotting skips over.

## Notifications (optional)

TimePilot can push reminders to your phone via [ntfy](https://ntfy.sh).
Configure it in **⚙ Settings → Notifications & ntfy**:

1. **Server URL** - `https://ntfy.sh`, or your own self-hosted server.
2. **Topic** - any name you like, e.g. `timepilot-a7f3c2e9`. Subscribe to the
   same topic in the ntfy mobile/desktop app. **Anyone who knows a topic name
   can read and post to it on a public server**, so use something long and
   unguessable rather than `reminders`.
3. **Icon URL** (optional) - a `.png` shown on the notification.
4. **Send test notification** confirms it works before you save.

There are two separate kinds of reminder, and the difference matters:

| | Where | Delivered when app is closed? |
|---|---|---|
| **Scheduled reminders** | Notifications tab | **Yes** - sent by the server |
| **Task / meeting pop-ups** | fired by Settings → Reminders | No - needs a tab open |

The **Notifications** tab schedules one-off or recurring reminders (hours /
days / weeks / months) with an ntfy priority and emoji tag. A background
thread in the app dispatches them every 30 seconds, so they arrive with
nothing open - it replaces the cron job you'd otherwise wire up. Recurring
reminders that were missed while the app was down roll forward to their next
occurrence rather than firing once per occurrence missed.

Hourly and daily repeats also have a row of **M T W T F S S** buttons, so a
daily reminder can run on workdays only, or weekends only. The excluded days
are skipped but the schedule keeps its rhythm (every 3 hours on weekdays
resumes Monday on the same 3-hour grid). If the first fire you pick lands on a
day you've switched off, it moves to the next day that's on. Weekdays are
worked out in the timezone of the browser that saved the reminder, not the
server's, so a reminder at 00:30 doesn't slip to the wrong day when the server
runs in UTC. Weekly and monthly repeats don't show the buttons: they already
land on one fixed day.

The **"Push task & meeting reminders to ntfy"** toggle mirrors the existing
slotted-task and meeting pop-ups out to ntfy as they fire. Those are driven
by the browser, so they only happen while a tab is open somewhere - if you
want a reminder that reaches you regardless, schedule it on the Notifications
tab.

**Self-hosted ntfy on a private address?** See `TIMEPILOT_ALLOW_PRIVATE_NTFY`
in `.env.example` - private targets are opt-in, for the reason described
under "Security notes" below.

## Login page banner (optional)

Set `TIMEPILOT_LOGIN_BANNER` in `.env` to show a message above the login and
signup forms - blank/unset (the default) shows nothing. Useful for a public
demo instance:

```
TIMEPILOT_LOGIN_BANNER=Demo instance - all data is wiped every 24 hours.
```

Set `TIMEPILOT_DISABLE_SIGNUP=true` to close public registration entirely -
`/signup` 404s and the "Sign up" link disappears from the login page, so only
accounts that already exist (e.g. one seeded with `sample_data.py`) can log
in. The two combine naturally for a demo instance: a banner explaining the
data-wipe schedule, with signup disabled so there's exactly one shared demo
account rather than an open registration page.

## Security notes

**Encryption at rest.** Every user's tasks, notes, snippets, settings, and
uploaded calendar file are encrypted with AES-256-GCM using
`TIMEPILOT_MASTER_KEY` before being written to Postgres. A stolen database
backup or disk image is unreadable without that key.
**Back it up somewhere separate from your database backups - losing it
makes all existing data permanently unreadable, with no recovery path.**

**Authentication.** Passwords are hashed (never stored in plaintext).
Sessions use signed, `HttpOnly`, `SameSite=Lax` cookies; the `Secure` flag
is on by default, which requires HTTPS - that's what your reverse proxy
provides. Login and signup are rate-limited (10/min per IP) against brute
forcing. Login timing is deliberately constant whether or not the username
exists, to prevent enumerating valid accounts.

**Calendar URL fetch.** The server fetches whatever ICS URL a user enters
in Settings, so it refuses to fetch anything that resolves to a private,
loopback, or internal address - otherwise a malicious user could point it
at your internal network and use the app as a probe. The check runs both
up front (for a friendly error message) and again at connection time on
every request and redirect hop, which also narrows the DNS-rebinding
window to milliseconds.

**ntfy server URL.** Same reasoning as the calendar fetch: the server posts
to whatever ntfy URL a user configures, so by default it refuses private,
loopback, or internal addresses. Self-hosted ntfy servers usually *are* on
such an address, so this is the one place that guard can be relaxed - set
`TIMEPILOT_ALLOW_PRIVATE_NTFY=true` in `.env`. That's deliberately an
operator decision made once in the environment, rather than something any
registered user can switch on from Settings.

**Input validation.** The page escapes everything it renders, and the
server independently validates every field it stores (`sanitize.py`), on both
live saves and backup imports: times must be `HH:MM`, numbers must be numbers,
keys and ids must be plain identifiers, colours must be hex, and imported
reminders get the same checks as the reminders API. Bad values are repaired
rather than rejected, so one stray field can't lose the rest of a restore or
leave the UI unable to render. Free text (titles, notes, snippets) is kept
exactly as typed and escaped on output. Run the tests with
`python -m unittest discover -s tests`.

**Response headers.** Every response carries `Content-Security-Policy`
(self-only sources, no framing, no plugins), `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`.

**Security logging.** Logins (success and failure, with source IP),
signups, data exports/imports, and calendar uploads are logged to stdout -
`docker compose logs timepilot` is your audit trail. Watch for repeated
`login FAILED` lines.

**What's not included:** no "forgot password" flow (reset one directly in
the database if needed), no email verification, no 2FA.
The rate limiter's storage is in-memory by default (declared explicitly as
`memory://` - not just left unset - so `docker compose logs` won't show
flask-limiter's "no storage specified" warning), which is fine for a
single `timepilot` container but won't coordinate correctly across more
than one worker/replica. Set `TIMEPILOT_RATELIMIT_STORAGE_URI` to a Redis
URL if you need limits actually shared - see `.env.example`. There's no
automatic schema migration tool - a future release that changes the
database shape will need a manual step; check release notes before
updating if that concerns you.

## Updating

```bash
docker compose pull
docker compose up -d
```

This restarts only the `timepilot` container - Postgres keeps running, so
it's a few seconds of downtime, not a full stack restart. **Take a backup
first** (see below) if [CHANGELOG.md](CHANGELOG.md) mentions a database
change for the new version.

Built your own image instead (option 2 above)? Pull the latest source,
`docker compose up --build -d` again.

## Data & backups

**Per-account export/import.** Logged in, Settings → Backup → Export data
downloads a JSON file with everything in that account - tasks, notes,
snippets, clipboard items, scheduled reminders, and settings. Import restores
from a previously exported file, **replacing** everything currently in the
account (a confirmation step shows what's about to be replaced before it
happens). This is the fastest way to back up or move a single account's
data, and doesn't need shell access to the server.

**Whole-database backup.** Everything lives in the `postgres_data` Docker
volume.

```bash
docker compose exec postgres pg_dump -U timepilot timepilot > backup.sql
```

The dump contains encrypted blobs, not plaintext - `TIMEPILOT_MASTER_KEY`
is what makes it useful to restore, so keep both together (but not stored
in the same place, per "Security notes" above).

**Using a specific host directory instead of a Docker-managed volume:** on
a NAS/homelab setup where Docker's own storage lives on a small system
disk separate from your bulk storage, the default named volume
(`postgres_data:`) inherits that constraint - it's created wherever
Docker's data-root is, which may not be where you want gigabytes of
database growth to land. Point it at your bulk storage directly by
replacing the volume with a bind mount in `docker-compose.yml`:

```yaml
services:
  postgres:
    volumes:
      - /path/on/your/big/disk/postgres_data:/var/lib/postgresql/data
```

and remove `postgres_data:` from the `volumes:` block at the bottom (no
longer needed once nothing references it). The host directory needs to be
writable by whatever UID the Postgres image runs its server process as
(the official image handles ownership itself via `fixing permissions on
existing directory` on startup, but pre-existing directories with
unusual ownership/permissions from a previous attempt can still trip this
up - if you hit a permissions error after switching, `sudo rm -rf` the
directory once and let Postgres recreate it cleanly on next start, rather
than trying to fix ownership by hand).

## Troubleshooting

**Postgres keeps restarting / "dependency failed to start: ... is
unhealthy".** Almost always a missing or empty `POSTGRES_PASSWORD` -
Postgres's official image refuses to start without one, and crash-loops
every few seconds rather than failing once. Check `.env` actually has it
set (`docker compose config` shows the resolved values; a fresh
`docker compose up` will refuse to start at all with a clear message
if it's missing, rather than crash-looping). If you hit this *before*
that fix and already have a container that's been restart-looping, wipe
the half-initialized volume once you've fixed `.env`:

```bash
docker compose down -v   # also deletes all data - only safe if nothing's stored yet
docker compose up -d
```

**Any other unhealthy/crash-looping container**: check its actual logs,
not just the dependency error - `docker compose logs postgres` (or
`timepilot`) shows the real error message underneath. Two common ones:

- `initdb: error: could not create directory ".../pg_wal": No space left
  on device` - the disk **Docker is actually writing to** is full, which
  is not necessarily the disk you'd think to check. In order:
  1. `docker info --format '{{.DockerRootDir}}'` shows where Docker
     actually stores everything, then `df -h` *that specific path* - not
     wherever you'd normally check free space. It's common for this to be
     a small system partition (or a separate Docker Desktop VM disk)
     while the rest of the machine has plenty of room.
  2. Docker Desktop (Mac/Windows) specifically: Settings -> Resources ->
     Advanced -> Disk image size. This is a **fixed-size virtual disk
     inside a VM**, entirely separate from host free space - having
     terabytes free on the actual machine doesn't help if this number is
     maxed out.
  3. Less common but same error message: inode exhaustion.
     `df -i` on Docker's root dir - a filesystem can show gigabytes free
     while still being out of inodes, which `docker system df` won't
     show you (it only tracks Docker's own byte usage).

  Reclaim space with `docker builder prune` (build cache from repeated
  `--build` runs is usually the biggest offender) and `docker volume
  prune`, or increase the Docker Desktop VM's disk allocation. On a
  NAS/homelab host specifically, the more permanent fix is usually to stop
  storing Postgres's data wherever Docker's constrained data-root is at
  all - see "Using a specific host directory instead of a Docker-managed
  volume" above. Either way, `docker compose down -v` to clear the
  half-initialized volume from the failed attempt before retrying.
- Missing/empty secret - see the `POSTGRES_PASSWORD` entry above; the same
  applies to `FLASK_SECRET_KEY` and `TIMEPILOT_MASTER_KEY`.

**`timepilot` container restarts with "Control server error: ... No space
left on device: '/home/timepilot/.gunicorn'"** even after Postgres is
healthy. This looks like the same disk-space class of problem but isn't
one you fix with a volume mapping: gunicorn 25.1.0+ runs a "control
socket" for the `gunicornc` CLI by default, which this app never uses, and
tries to create it under the container's own home directory on every
boot. Already fixed in the Dockerfile (`--no-control-socket`) - if you're
still seeing it, you're running an image built before that flag was added;
rebuild (`docker compose up --build`) or pull a newer tag.

**Reverse proxy can't reach the app.** Confirm the container is actually
up and listening: `docker compose ps` and `curl http://127.0.0.1:5170/healthz`
on the host running TimePilot - that should return `{"ok": true}`. If it
doesn't, check `docker compose logs timepilot`. If it does, the problem is
on the proxy's side - confirm its upstream/backend config points at
`127.0.0.1:5170` (or wherever you changed the published port to in
`docker-compose.yml`) and that the proxy is running on the *same* host
(the default publish is bound to localhost only, deliberately - see the
comment in `docker-compose.yml` if you need it reachable from elsewhere).

**"service has neither an image nor a build context specified".** You
passed `docker-compose.local.yml` on its own, without the base
`docker-compose.yml` - it only adds partial config (environment overrides)
and needs `-f docker-compose.yml` listed first.

## Licence

MIT - see [LICENSE](LICENSE).
