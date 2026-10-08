FROM python:3.14-slim

WORKDIR /app

# No apt-get / compiler needed: psycopg2-binary and cryptography (the only
# two dependencies that could need one) both ship prebuilt manylinux wheels
# for linux/amd64 and linux/arm64 at the versions pinned in requirements.txt
# - verified against PyPI's file listing for both platforms. Keeping this
# image apt-free also means the build has one less network call (Debian's
# mirrors) that can fail transiently.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

# Runs as a non-root user
RUN useradd -m -u 1000 timepilot && chown -R timepilot:timepilot /app
USER timepilot

EXPOSE 5170

# --no-control-socket: gunicorn >=25.1.0 runs a control socket for the
# gunicornc CLI by default, writing to $HOME/.gunicorn/gunicorn.ctl. This
# app never uses gunicornc, so there's no reason for that write to happen
# at all - and on constrained/slow container storage it can fail outright
# ("Control server error: ... No space left on device") and crash-loop the
# whole app for a feature nothing here calls.
# -w 2: for a small self-hosted user base 4 workers just multiplies the
# per-worker ICS cache and in-memory rate-limiter state (each worker counts
# independently, so N workers ~ N x the intended limit). Override the
# command in docker-compose.yml if you need more throughput.
#
# entrypoint.sh creates the DB schema once, in a single process, before any
# of this runs - see the comments in that file for why that matters.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5170", "--no-control-socket", "--access-logfile", "-", "app:app"]
