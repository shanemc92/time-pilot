# Changelog

Every entry is tagged `FEATURE` or `BUGFIX`. Sections and entries are in
chronological order (oldest first).

## Architecture: single-user desktop app → multi-user Docker server

- **FEATURE:** Replaced local per-file JSON storage with PostgreSQL, one
  encrypted row per user per data domain (settings, tasks, history, notes,
  snippets, clipboard, runtime).
- **FEATURE:** Added AES-256-GCM encryption at rest for all stored user
  data, keyed by a single `TIMEPILOT_MASTER_KEY`.
- **FEATURE:** Added multi-user accounts: signup/login/logout, password
  hashing, CSRF protection, rate limiting on auth endpoints.
- **FEATURE:** Removed the desktop widget entirely (pywebview wrapper,
  Windows shortcut installer, portable .exe builder) in favor of
  server-only mode.
- **FEATURE:** Verified with crypto round-trip tests, a full
  signup → use → logout → re-login flow, cross-user data isolation tests,
  encryption-at-rest confirmed via raw `pg_dump` inspection, and full
  browser (Playwright) tests.

## Docker packaging & CI

- **FEATURE:** Wrote `Dockerfile`, `docker-compose.yml` (app + Postgres).
- **FEATURE:** Set up a GitHub Actions workflow to build and publish the
  image to GHCR automatically on push/tag, plus a weekly scheduled rebuild
  to pick up base-image OS security patches.
- **FEATURE:** Added Dependabot for dependency/Docker-base-image/GitHub
  Actions updates.
- **BUGFIX:** Dockerfile `apt-get` build step was failing
  (network-dependent); removed it entirely after verifying
  `psycopg2-binary` and `cryptography` both ship prebuilt wheels for
  amd64+arm64, so no compiler was ever actually needed.
- **BUGFIX:** Postgres crash-loop on missing secrets - added fail-fast
  `${VAR:?message}` syntax so `docker compose up` refuses to start with a
  clear error instead of Postgres crash-looping silently.
- **FEATURE:** Diagnosed a genuine "No space left on device" Postgres
  startup failure as a real host/Docker-storage disk issue (not an app
  bug); added detailed troubleshooting docs (Docker Desktop VM disk cap,
  `DockerRootDir` mismatch, inode exhaustion).
- **BUGFIX:** gunicorn control-socket trying to write to a read-only path
  on startup (added `--no-control-socket`).
- **FEATURE:** Split local-testing config into `docker-compose.local.yml`
  (direct port + HTTP cookies for testing without a proxy).
- **FEATURE:** Added `docker-compose.proxy.yml` for attaching to an
  external Traefik's network - later removed (see below) in favor of a
  simpler model.
- **FEATURE:** Published the image at `ghcr.io/shanemc92/timepilot:latest`;
  updated compose file and docs to reference it directly, uncommented by
  default.
- **FEATURE:** Removed all Traefik-specific machinery (labels, external
  network attachment, `docker-compose.proxy.yml`, `DOMAIN`/`TRAEFIK_*` env
  vars) - app now just publishes on `127.0.0.1:5170`, the standard "any
  reverse proxy on the same host" pattern, so Traefik/Nginx/Caddy/etc. all
  work identically with zero app-specific config.

## Security hardening (initial pass)

- **FEATURE:** CSRF: JSON-only endpoints (state, export/import) safely
  exempted with documented reasoning; file-upload endpoints (calendar
  upload) kept protected since raw-byte bodies can be forged by a
  cross-site form.
- **BUGFIX:** Fixed a login timing side-channel: username enumeration was
  possible because password hashing only ran for existing usernames; now
  always hashes something so existing vs. non-existing accounts take equal
  time.
- **FEATURE:** Added an SSRF guard on the calendar ICS URL fetch: rejects
  URLs that resolve to private/loopback/link-local/reserved addresses,
  since the server (not the browser) does the fetching.
- **FEATURE:** Re-audited and pinned all dependencies to exact versions
  via a pip-compile lockfile.
- **BUGFIX:** Caught and fixed one real pre-release CVE (cryptography
  buffer overflow) before it shipped.
- **FEATURE:** Disabled caching on the state endpoint
  (`Cache-Control: no-store`) to rule out any stale-data-after-save class
  of bug.

## Backup / export / import

- **FEATURE:** Added native per-account export/import: single JSON file
  via Settings, full-replace semantics with a confirmation step,
  cross-user isolation verified.
- **FEATURE:** Added desktop-widget-compatible zip export/import
  (optionally AES-256 password-protected via pyzipper) for migrating data
  to/from the old single-user app's file format.
- **FEATURE:** Later removed the desktop-widget zip feature entirely
  (backend routes, frontend UI, pyzipper dependency) now that the old app
  is retired - native JSON export/import is the only supported path going
  forward.

## Feature: browser notifications

- **FEATURE:** Added an optional Settings toggle to fire real OS-level
  browser notifications (Notification API) alongside the existing in-app
  reminder modal, independent of the existing reminders toggle.

## Feature: calendar/Today page improvements

- **FEATURE:** Split "work hours" (bounds Auto-slotting) from a new,
  separate "calendar display range" (what the Today timeline actually
  shows/lets you drag into) - lets out-of-hours items be seen and placed
  manually without changing what Auto treats as normal hours.
- **FEATURE:** Added an optional lunch-break setting: Auto-slotting skips
  over it, and it renders as a distinct hatched block on the timeline.
- **FEATURE:** Added Outlook/Google-Calendar-style overlap handling:
  overlapping meetings and/or tasks now split side by side (cluster +
  greedy column layout) instead of one hiding the other.
- **BUGFIX:** Bulk timesheet export was re-syncing the calendar for every
  day in the range, risking overwriting a manual correction (e.g. a
  meeting that ran over/under); now purely reads whatever's already logged
  per day, no recalendar-sync side effects.

## Bug fixes (Today page & mobile)

- **BUGFIX:** Fixed the last hour label on the Today timeline rendering
  past the container's bottom edge into the panel below.
- **BUGFIX:** Fixed a mobile-only bug where typing a time into an
  unslotted task's input got silently wiped - caused by the on-screen
  keyboard triggering a `resize` event that forced a full re-render
  mid-edit; now skipped while a form field has focus.
- **BUGFIX:** Fixed the Export entries table's label column overlapping
  the category column on narrow/mobile screens (added a table min-width so
  it scrolls instead of squeezing to zero).

## Bug fix: calendar not loading at all

- **BUGFIX:** Root cause #1 (backend): the calendar route read
  `icsUrl`/`ignoreEvents` directly off the wrong (wrapped) settings shape,
  so it always saw an empty URL regardless of what was actually saved.
- **BUGFIX:** Root cause #2 (frontend, still broken after fixing #1):
  Settings' Save button only queued a debounced save (~600ms), so checking
  the Today tab right after saving could beat the actual write to the
  database; Save now explicitly waits for the write to complete before
  closing.

## Misc fixes

- **BUGFIX:** Investigated the login/signup page logo; confirmed the app's
  own logo (not a placeholder/clipboard icon) was already correctly in
  place, centered.
- **BUGFIX:** Converted a custom timer font (Cursed Timer) from TTF to
  WOFF, verified every digit glyph has identical advance width (the actual
  fix for timer digits shifting/jittering as numbers changed).
- **BUGFIX:** The new font was initially added alongside the old
  `digital.woff` instead of replacing it - corrected so only the intended
  file remains.

## Documentation

- **FEATURE:** Split docs into a maintainer README and a self-contained
  user-facing DEPLOY.md, then later consolidated back into a single
  simplified README.md (removing the maintainer-only publish/patch-workflow
  content) covering three equally-supported ways to run it: pull the
  published image, build your own image, or build from source.
- **FEATURE:** Removed a short-lived separate "minimal GHCR build bundle"
  zip in favor of just the one full repo zip.

## Security hardening (from independent review)

- **FEATURE:** Added security response headers: CSP, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- **FEATURE:** Added audit logging: logins (success/failure + source IP),
  signups, data exports/imports, calendar uploads - written to stdout.
- **FEATURE:** Tightened CSRF handling on `/api/state` and `/api/import`:
  now requires `Content-Type: application/json` instead of force-parsing
  any body.
- **FEATURE:** Calendar ICS fetch SSRF guard now re-validates on every
  redirect hop, closing a DNS-rebinding gap.
- **FEATURE:** `/healthz` no longer leaks internal error details to
  unauthenticated clients.
- **FEATURE:** Raised minimum password length to 12 characters for new
  signups (existing accounts unaffected).
- **FEATURE:** Added a scheduled pip-audit GitHub Actions workflow.
- **FEATURE:** Reduced default gunicorn workers 4 → 2, added memory/CPU
  limits in `docker-compose.yml`.
- **BUGFIX:** Caught a stranded/dead code path in the state-save handler
  that would have silently broken partial saves - fixed before shipping.

## GitHub / deployment

- **BUGFIX:** Image-publish workflow built the tag from the repo's actual
  casing (`TimePilot`), which GHCR rejects (must be lowercase) - hardcoded
  to lowercase.
- **FEATURE:** Added a demo GIF to the README.

## Notes tab

- **FEATURE:** Capped at 4 columns to match Snippets/Clipboard (previously
  uncapped, only looked capped by coincidence).
- **FEATURE:** Section panels now stretch to match the tallest one in
  their row.
- **FEATURE:** Added move-left/move-right buttons on sections.
- **BUGFIX:** Long unbroken text (e.g. long URLs) was overflowing the note
  box - now wraps.
- **FEATURE:** Replaced per-item delete with an edit popup (edit or
  delete); clicking a note's text now copies it to clipboard.

## Notifications (ntfy) - new feature

- **FEATURE:** New Notifications tab: schedule one-off or recurring push
  reminders via ntfy, delivered by a background dispatcher so they arrive
  even with the app closed.
- **FEATURE:** Settings: ntfy server URL, topic, optional icon URL.
- **FEATURE:** Toggle to push task/meeting pop-ups to ntfy too (needs a
  tab open, unlike scheduled reminders).
- **FEATURE:** Toggle to enable reminders for calendar meetings, not just
  tasks.
- **FEATURE:** Settings reorganized into collapsible sections, Save/Cancel
  pinned to the bottom.
- **FEATURE:** SSRF protection on the ntfy URL; private/internal addresses
  are opt-in via `TIMEPILOT_ALLOW_PRIVATE_NTFY`.
- **FEATURE:** Multi-worker safety: a Postgres advisory lock ensures only
  one worker dispatches reminders.
- **BUGFIX:** If the lock-holding worker died, the others gave up
  permanently instead of retrying - reminders would've silently stopped
  until a full restart. Fixed to retry and take over.
- **BUGFIX:** Notification titles showed a stray "?" between the emoji and
  text (e.g. "? Task due: ...") - header sanitizing was substituting "?"
  per dropped character instead of removing it.
- **FEATURE:** Paste a single emoji into the Tag field and it
  auto-converts to the matching ntfy shortcode; multiple pasted emoji keep
  only the first; unrecognized emoji are rejected with a message.
- **FEATURE:** Added a customizable quick-pick row of up to 10 tag
  buttons, configurable in Settings.
- **FEATURE:** Restyled the recurring toggle as a switch card.

## Login page

- **FEATURE:** Added `TIMEPILOT_LOGIN_BANNER` env var: optional message
  above login/signup (blank by default).
- **FEATURE:** Added `TIMEPILOT_DISABLE_SIGNUP` env var: fully disables
  public registration (`/signup` 404s, link hidden) while existing
  accounts still log in.

## Timer / timesheet

- **FEATURE:** Correcting a running timer's start time now also adjusts
  the previous logged entry's end time to match (task or meeting,
  whichever was last) - closes the gap instead of leaving unlogged time.
  On by default, with a Settings toggle.
- **FEATURE:** Added a live preview in the edit-timer popup showing
  exactly what will be adjusted, or why nothing will be.
- **BUGFIX:** The "last entry" logic picked whichever timelog entry had
  the latest end time by clock value, with no check that it had actually
  happened - a future calendar meeting would incorrectly win over a task
  that already finished. Fixed to only consider entries already ended by
  the current time.
- **BUGFIX:** Live preview said "extend" even when the adjustment was a
  shortening - changed to "adjust".

## Demo data (sample_data.py)

- **FEATURE:** Expanded seeded data: more tasks across all columns, an
  extra day of timesheet history, more notes/snippets/paste templates, two
  demo scheduled reminders.
- **FEATURE:** Added a generated demo `.ics` calendar (recurring standup,
  one-off meetings, an all-day event, a "Private Appointment" entry to
  demonstrate the ignore filter) - seeded through the same storage path as
  a real Settings → Calendar upload.
- **FEATURE:** Added `--no-ics` flag to skip the demo calendar.

## Repo / docs

- **FEATURE:** Added `deploy/init-timepilot-demo.sh`: an idempotent
  server-setup script for a public demo instance - installs and hardens
  SSH (custom port, key-only, fail2ban), locks the firewall down to
  SSH+443 only, enables unattended security upgrades, installs Docker,
  clones the repo, generates and preserves `.env` secrets, syncs a
  Cloudflare DNS record and obtains a Let's Encrypt cert via DNS-01,
  configures nginx, sends ntfy progress/failure notifications for each
  step, and schedules a nightly cron job that fully wipes the database and
  reseeds it with `sample_data.py` under a freshly generated password
  spliced into the login banner.
- **FEATURE:** Added this changelog (`CHANGELOG.md`).
- **BUGFIX:** Without `--preload`, each gunicorn worker imports `app.py`
  independently in its own forked process, and `create_app()` runs
  `db.create_all()` as a side effect of that import - so on a freshly
  wiped/empty database, N workers booting together raced the same schema
  creation. SQLAlchemy's `create_all()` inspects for existing tables, then
  issues a plain `CREATE TABLE` (no `IF NOT EXISTS`) for whatever's
  missing; two workers landing on that inspect step together both see
  "missing" and both try to create it, and the loser crashes on "relation
  already exists" and gets respawned by gunicorn - typically converging
  after one or two respawns, but a genuine race, not a deliberate retry
  loop. Reproduced directly (7 of 8 concurrent imports against an empty DB
  crashed) and fixed with a one-shot `entrypoint.sh` that creates the
  schema once, in a single process, before gunicorn ever forks a worker -
  verified the same 8-way concurrent import against an already-initialized
  DB with zero failures.

## Mobile & login page polish

- **BUGFIX:** The Today timeline's row heights are derived from
  `window.innerHeight`, recomputed on a `resize` listener. Mobile browsers
  also fire `resize` purely from their own address bar collapsing/expanding
  as you scroll (`innerHeight` shifts, `innerWidth` doesn't) - reacting to
  that was subtly rescaling the whole timeline mid-scroll. Now only a width
  change (a real resize or device rotation) triggers a re-render.
- **BUGFIX:** WTForms' `SubmitField` renders as `<input type="submit">`,
  not a `<button>` - the login/signup page's CSS only themed `button`, so
  the submit control silently fell back to the browser's default unstyled
  grey button despite the rest of the page (and the stylesheet's intent)
  being themed. Widened the CSS selector to cover both.
- **BUGFIX:** flask-limiter warned on every startup about using in-memory
  rate-limit storage with none explicitly specified. The omission wasn't
  actually a bug - in-memory is a reasonable default for this app's threat
  model and worker count - so declared it explicitly
  (`storage_uri="memory://"`) to silence the warning and make the choice
  visible in code, and added `TIMEPILOT_RATELIMIT_STORAGE_URI` so anyone
  running more workers/replicas who wants limits actually shared can point
  it at Redis instead. Confirmed rate limiting still fires (429s / log
  lines) unchanged after the change - only the storage declaration moved
  from implicit to explicit.
- **BUGFIX:** Meetings only get imported into the timelog when the Export
  tab has loaded that day (`importCalToLog`) - if that hadn't happened yet,
  a meeting sitting between a stopped task and "now" simply wasn't in
  `S.timelog`, so backdating a new timer's start could reach straight past
  it and adjust an *older* task instead, silently pulling its end into the
  middle of an unlogged meeting. Fixed by importing today's calendar (same
  path, same ignore-list, same dedup against manually-deleted entries) the
  moment the edit-timer popup opens, so the check always sees the real
  last thing that happened. Also replaced the silent auto-apply with a
  live preview of exactly which entry (task or meeting) is about to be
  adjusted and to what, behind a checkbox defaulted to checked - so a
  wrong pick is visible and skippable per-correction, not just per-import.
  Reproduced the exact reported scenario (task stopped, unimported meeting
  in between, new task backdated past it) and confirmed the meeting is now
  the one adjusted, not the earlier task.
- **FEATURE:** The first calendar check of the day (previously whichever
  tab happened to trigger it - often Export) meant waiting on a real
  network round-trip to the ICS URL if the server's 5-minute raw-bytes
  cache had gone cold. Now warmed in the background right after login/page
  load instead, off the critical path of any particular tab - by the time
  Export (or the edit-timer popup's own calendar check) runs, the fetch is
  already done and today's meetings are already imported, so both find
  nothing left to wait on.

## Scratchpad

- **FEATURE:** Added a **Scratch** tab - a single plain-text scratchpad for
  temporary copy/paste, replacing the habit of opening Notepad++ for it.
  Text size +/-, word wrap and line numbers toggle (numbers are measured
  per rendered row, so they stay aligned with wrapped lines), a live
  Ln/Col, lines, words, chars and selection status bar, find & replace with
  match-case and regex options (capture groups work in the replacement,
  search wraps around, live match count), and transforms that apply to the
  selection or the whole document: remove blank lines, trim trailing
  space, remove duplicate lines, sort A-Z / Z-A, reverse, UPPER, lower.
  Plus copy all, download as .txt, and clear.
- **FEATURE:** Edits are written through `execCommand("insertText")` where
  available so replacements and transforms land on the textarea's own undo
  stack - Ctrl+Z walks back through them, not just typing.
- **FEATURE:** Content and view options persist in a new encrypted
  `scratch` data domain (`{text, size, wrap, nums}`), autosaving on the
  same debounce as the rest of the app and included in backup export and
  import.

## Split view

- **FEATURE:** Added a **Split** button: pins the current tab to the left
  half of the screen while the tab bar keeps switching the right half, so
  Board or Today can stay visible next to Scratch, Snippets or Clipboard.
  Clicking the pinned tab swaps the two panes; the button unpins. The
  pinned tab is stored in settings (`pinView`) so it survives a reload.
- **FEATURE:** Implemented as a flex row on `<main>` - the view sections
  were already its direct children, so nothing is moved in the DOM and each
  view keeps its own state (notably the scratchpad's undo stack and every
  pane's scroll position). Both panes re-render on a tab change so an edit
  made on one side shows up on the other.
- **FEATURE:** Below 900px the pinned pane is dropped and only the active
  tab shows, since half a phone screen isn't usable for either view.

## Scratchpad tabs

- **FEATURE:** The scratchpad holds up to 8 named pads instead of one, with
  a tab strip above the toolbar: `+` adds, `x` closes (with a confirm if
  the pad has content, and the last one can't be closed), double-click a
  tab to rename it inline. Download names the file after the active pad.
- **FEATURE:** The stored shape became
  `{pads:[{id,name,text}], active, size, wrap, nums}`. The old single-pad
  `{text:...}` is migrated into the first pad on load, so an existing
  scratchpad carries over untouched.
- **KNOWN LIMIT:** All pads share the one textarea, so the browser's undo
  history resets when you switch pads - Ctrl+Z won't reach back past a pad
  switch.

## Configurable board columns

- **CHANGE:** The board's five hardcoded columns became editable in Settings,
  up to 6 of them. New defaults: Today, High Priority, Medium Priority, Low
  Priority, Done - replacing Today / This Week / Next Week / Next Month /
  Done.
- **FEATURE:** Columns are stored as `settings.columns`
  (`[{k, label, done}]`). Position 0 is the column the Today tab plans from
  and the only one that can hold time slots, whatever it's named - the old
  hardcoded `"today"` and `"done"` key checks are gone, replaced by
  `firstColK()` and `doneColK()`.
- **FEATURE:** The destructive daily clear-out is now an explicit per-column
  "clears daily" tick rather than an implied property of a column called
  Done. At most one column can hold it, and with none ticked nothing is ever
  auto-deleted.
- **FEATURE:** Renaming a column leaves its key alone, so tasks don't move.
  Deleting a column moves its tasks to the first column (confirmed first,
  with the count), and `reconcileColumns()` rehomes any task pointing at a
  column that no longer exists, clearing slots and `doneAt` that no longer
  apply.
- **MIGRATION:** Accounts with no `settings.columns` get the new defaults,
  with existing tasks mapped across by position - This Week becomes High
  Priority, Next Week becomes Medium, Next Month becomes Low. Nothing is
  orphaned and nothing is dumped into Today.
- **FIX:** Saving Settings now re-renders through `renderAll()` instead of
  only the active view, so a board pinned in split view picks up column
  changes immediately.


## Task details

- **FEATURE:** Tasks have an optional multi-line **Details** field in the
  add/edit modal, below the existing Title, for notes, next steps or a
  running log on long-lived tasks. It isn't shown on the board; a 📝 on
  the card marks tasks that have details, and clicking it opens the task.
  Stored as `details` on the task only when non-blank, so existing tasks and
  exports are unchanged.
- **FEATURE:** Clicking the backdrop no longer closes the task add/edit
  modal, so a stray click can't discard typed details. Cancel, Save or
  Delete close it; other modals still close on a backdrop click.

## Daily slot reset

- **FEATURE:** Time slots on the Today tab now only last for the day they
  were set. A slotted task that isn't finished goes back to unslotted the
  next day instead of reappearing at the same time. Tasks record the day
  they were slotted (`slotDay`); the check runs on load and at midnight.
  Slots set before this change are treated as set today, so they clear
  from tomorrow rather than being wiped on upgrade.

## 2.7.0 - Security hardening

- **BUGFIX:** Values from saved state were put into the page without HTML
  escaping in many places: board column names and category keys, the task
  estimate and slot shown on cards, the Today lists and the reminder popup,
  the time and duration fields in the Export table, the reminder priority,
  and the work-hours, display-range, lunch, rounding and reminder-lead fields
  in Settings. A crafted backup file imported through Settings (or a
  hand-edited saved state) could therefore run script in the account
  holder's session, since the CSP allows inline script. All of these now go
  through `esc()`.
- **FEATURE:** The server now validates every stored field, on live saves
  and on backup import (`sanitize.py`). Times must be `HH:MM`, numbers must be
  numbers, keys and ids must be plain identifiers, colours must be hex, and
  types are enforced (so a number where the UI expects text can no longer
  leave the page blank). Bad values are repaired instead of rejected, free
  text is stored exactly as typed, and unknown scalar settings from a newer
  client survive the round trip.
- **BUGFIX:** Reminders inside an imported backup skipped the checks the
  reminders API applies (priority range, interval type, message length). They
  now go through the same rules.
- **FEATURE:** Unit tests for the validator (`python -m unittest discover -s
  tests`), including a round trip of the demo account and a garbage-input
  fuzz. `tests/` is excluded from the Docker image.
- **FEATURE:** The image workflow also publishes a floating major tag
  (`:2`) alongside the exact version tags.

## 3.0.0 - Glass skin

Includes everything in 2.7.0.

- **FEATURE:** Full visual redesign. Frosted-glass panels, columns, modals
  and timeline over soft ambient light and grain; gradient buttons, glowing
  toggles and focus rings; a glowing LCD timer, with the header bar lighting
  up green while a timer runs.
- **FEATURE:** New layout. The top tab row is now a floating icon rail on the
  left, with hover labels, and Split / Settings / Log out sit as icon buttons
  in the timer header. A dot on the rail marks the pinned tab in split view.
  On phones the rail becomes a bottom dock.
- **FEATURE:** All four existing themes (dark, light, HTB, Dracula) re-tuned
  for the glass look. The login and signup pages restyled to match.
- **FEATURE:** New **Aurora** theme (Settings -> Appearance): a deep
  teal-black palette with a mint accent and teal and violet ambient light.
- **FEATURE:** Only the styles, nav markup and theme list changed in the page
  (`static/index.html`, `templates/base_auth.html`); the app logic, database
  schema and export format are unchanged, so 2.x and 3.x can be swapped
  freely against the same data (an account set to Aurora falls back to Dark
  in 2.x). The film-grain texture is a static file (`static/grain.svg`)
  because the CSP blocks `data:` images. Blur is limited to the rail, header
  and modal, since blurring every panel is costly on large libraries.
- **FEATURE:** README gains a Versions section explaining how to stay on the
  classic look (branch `release/2.x`, tag `v2.7.0`, image `:2`). The
  screenshot is replaced, an Aurora screenshot is added, and the demo is now
  a full-colour animated WebP (`docs/demo.webp`, replacing the GIF, whose
  256-colour palette washed out the colours). The classic screenshot is kept
  as `docs/screenshot-classic.png`.
- **FEATURE:** The image workflow also publishes a floating `:3` tag.
- **FEATURE:** Aurora is now the default theme for new accounts (and the login page). Dark is redone as a true-black, monochrome "ink" theme with a silver accent and almost no ambient light, so it no longer resembles the tinted themes. Existing accounts keep their saved theme. Screenshots and demo are re-recorded with Aurora; `docs/screenshot-aurora.png` is replaced by `docs/screenshot-dark.png`.

## Weekday picker for repeating reminders

- **FEATURE:** Hourly and daily repeating reminders get an M T W T F S S row
  of toggle buttons (Notifications tab), so a daily reminder can run on
  workdays only, or weekends only. Excluded days are skipped without
  shifting the schedule; a first fire that lands on an excluded day moves to
  the next allowed one. The list shows it as e.g. "every 1 day · weekdays".
- **FEATURE:** Weekdays are evaluated in the saving browser's timezone,
  stored with the reminder as `tz`, rather than the server's - a container
  running in UTC would otherwise put early-morning reminders on the wrong
  day. Reminders saved before this change have no `days`/`tz` and behave
  exactly as before; the export format gains the two optional fields.
