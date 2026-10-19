#!/usr/bin/env bash
set -uo pipefail

### ---- EDIT THESE ---- ###
# These are the defaults for a FIRST run. They are then saved to SECRETS_ENV (root-only) and that
# file wins on every later run of the installed copy - see "Config persistence" below. To change
# a value afterwards, edit SECRETS_ENV, or run a freshly edited copy of this script by hand.
DOMAIN="timepilot-demo.example.com"
CF_ZONE_NAME="example.com"          # <-- the Cloudflare zone this domain lives in
CF_PROXIED=false                    # <-- true = orange cloud (proxied), false = grey cloud (DNS only)
LE_EMAIL="you@example.com"          # <-- Let's Encrypt notification email
CF_API_TOKEN=""                     # <-- Cloudflare token, Zone:DNS:Edit scope. Blank is fine on re-runs
                                    #     (loaded from SECRETS_ENV, or from an existing cloudflare.ini).
NTFY_TOPIC="your-unique-topic-here" # <-- pick something unguessable
DEMO_USERNAME="demo"                # <-- account sample_data.py resets daily
DEMO_PASSWORD="ChangeThisDemoPass"  # <-- initial password, only used until the first nightly reset generates a random one
TIMEPILOT_DISABLE_SIGNUP=false      # <-- disables the signup option so only the demo user works
TIMEPILOT_LOGIN_BANNER_TEMPLATE="Demo instance - today's password is %s - all data is wiped every 24 hours."
DNS_PROPAGATION_BUFFER=30           # <-- seconds to wait before the final HTTPS check, for external resolvers to pick up the new record
SSH_PORT=2222                       # <-- custom SSH port (update your SSH client and cloud firewall config to match before running!)
FAIL2BAN_MAXRETRY=3                 # <-- how many ssh failed logins before IP is blocked
FAIL2BAN_BANTIME=3600               # <-- seconds = 1 hour
FAIL2BAN_FINDTIME=600               # <-- window within which maxretry failures count

# --- Self-update / self-heal ---
SELF_UPDATE_URL="https://raw.githubusercontent.com/shanemc92/TimePilot/main/deploy/init-timepilot-demo.sh"
                                    # <-- the installed copy refreshes itself from here before doing
                                    #     any work (only a valid bash script is ever swapped in).
                                    #     Leave "" to disable.
HEAL_CRON="30 4 * * *"              # <-- when the installed copy runs --heal (cert/nginx/stack/DNS repair).
                                    #     Clear of the 00:00 nightly reset. Also runs once ~2 min after boot.
CERT_RENEW_DAYS=21                  # <-- renew + reload when the cert (on disk OR as actually served by nginx)
                                    #     has fewer than this many days left. LE certs last 90 days.
### --------------------- ###

# Bump when you change this script. Printed on every run so you can tell at a glance which version
# executed - the installed copy is otherwise a frozen snapshot and runs last month's logic forever.
SCRIPT_VERSION="2026.10.16"

INSTALL_PATH="/usr/local/sbin/redeploy-timepilot.sh"
SECRETS_ENV="/root/.secrets/timepilot-deploy.env"
LOCK_FILE="/run/timepilot-init.lock"
HEAL_LOG="/var/log/timepilot-heal.log"

# --heal = the unattended repair pass cron runs: skips the slow/risky work (apt upgrade, SSH and
# firewall changes, git pull, forced compose up) and only fixes what is actually broken.
MODE="full"
[[ "${1:-}" == "--heal" ]] && MODE="heal"

if [[ $EUID -ne 0 ]]; then echo "Run as root (sudo)."; exit 1; fi

SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
echo "==> init-timepilot-demo.sh version ${SCRIPT_VERSION} (${MODE})"

# One run at a time: cron heal vs a manual run vs the nightly reset's compose restarts.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then echo "Another init/heal run is in progress - exiting."; exit 0; fi

# --- Config persistence ---
# The installed copy is replaced by SELF_UPDATE_URL, which carries the example values above - so
# the real config cannot live in the script. First run (a hand-run copy, config block is
# authoritative): save the config to SECRETS_ENV. Later runs of the installed copy: load it back.
CONFIG_VARS=(DOMAIN CF_ZONE_NAME CF_PROXIED LE_EMAIL CF_API_TOKEN NTFY_TOPIC DEMO_USERNAME DEMO_PASSWORD
             TIMEPILOT_DISABLE_SIGNUP TIMEPILOT_LOGIN_BANNER_TEMPLATE DNS_PROPAGATION_BUFFER SSH_PORT
             FAIL2BAN_MAXRETRY FAIL2BAN_BANTIME FAIL2BAN_FINDTIME CERT_RENEW_DAYS)
mkdir -p /root/.secrets && chmod 700 /root/.secrets
if [[ "${SCRIPT_PATH}" == "${INSTALL_PATH}" && -r "${SECRETS_ENV}" ]]; then
    # shellcheck disable=SC1090
    . "${SECRETS_ENV}"
fi
# A box deployed by the old script has no SECRETS_ENV but does have certbot's cloudflare.ini.
if [[ -z "${CF_API_TOKEN}" && -r /root/.secrets/cloudflare.ini ]]; then
    CF_API_TOKEN=$(sed -n 's/^dns_cloudflare_api_token *= *//p' /root/.secrets/cloudflare.ini | head -1)
fi
if [[ -z "${CF_API_TOKEN}" ]]; then
    echo "No CF_API_TOKEN in the config block, ${SECRETS_ENV} or cloudflare.ini - set it and re-run."; exit 1
fi
if [[ "${DOMAIN}" == *example.com ]]; then
    echo "DOMAIN is still the example value - edit the config block (or ${SECRETS_ENV}) and re-run."; exit 1
fi
save_config() {
    local v
    ( umask 077
      printf '# Written by init-timepilot-demo.sh - root-only, sourced by %s on later runs.\n' "${INSTALL_PATH}"
      for v in "${CONFIG_VARS[@]}"; do printf '%s=%q\n' "$v" "${!v}"; done ) > "${SECRETS_ENV}.tmp" \
      && chmod 600 "${SECRETS_ENV}.tmp" && mv "${SECRETS_ENV}.tmp" "${SECRETS_ENV}"
}
# Only a hand-run copy may (re)write it; the installed copy just reads it.
[[ "${SCRIPT_PATH}" != "${INSTALL_PATH}" ]] && save_config

# --- Self-update ---
# Only the installed copy updates itself: a hand-run copy carries your edited config block, which an
# overwrite from upstream would throw away. Re-execs once (TP_SELF_UPDATED) before doing any work.
if [[ -n "${SELF_UPDATE_URL}" && -z "${TP_SELF_UPDATED:-}" && "${SCRIPT_PATH}" == "${INSTALL_PATH}" ]]; then
    _new="$(mktemp)"
    if curl -fsSL -m 30 "${SELF_UPDATE_URL}" -o "${_new}" && [[ -s "${_new}" ]]; then
        # A truncated download or an HTML error page must never overwrite a working script.
        if bash -n "${_new}" 2>/dev/null && head -1 "${_new}" | grep -q '^#!' \
           && grep -q '^SCRIPT_VERSION=' "${_new}"; then
            if ! cmp -s "${_new}" "${SCRIPT_PATH}"; then
                echo "==> newer version at ${SELF_UPDATE_URL} - updating and re-running"
                cat "${_new}" > "${SCRIPT_PATH}"; chmod 700 "${SCRIPT_PATH}"; rm -f "${_new}"
                exec 9>&-    # release the lock for the re-exec'd copy
                TP_SELF_UPDATED=1 exec "${SCRIPT_PATH}" "$@"
            fi
            echo "==> already running the latest version"
        else
            echo "WARN: downloaded self-update failed validation - continuing with the current version"
        fi
    else
        echo "WARN: could not fetch ${SELF_UPDATE_URL} - continuing with the current version"
    fi
    rm -f "${_new}"
fi

LOG_FILE="/var/log/timepilot-init.log"
( umask 077; touch "$LOG_FILE" )
exec > >(tee -a "$LOG_FILE") 2>&1

# Prevent apt/dpkg from blocking on interactive prompts during a headless run.
# Ubuntu 24.04's needrestart in particular will otherwise stall waiting for TTY input.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

notify() {
    curl -fsS -m 10 -H "Title: $1" -H "Tags: ${3:-}" -d "$2" "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null 2>&1 || true
}

# run_step "desc" check_fn action_fn
# Skips (silently, just logged) if check_fn already returns true.
# Notifies on actual action taken, and always notifies on failure.
run_step() {
    local desc="$1" check_fn="$2" action_fn="$3"
    if "$check_fn"; then
        echo "==> ${desc}: already done, skipping"
        return 0
    fi
    echo "==> ${desc}"
    if "$action_fn" >>"$LOG_FILE" 2>&1; then
        notify "TimePilot ${MODE}: OK" "${desc}" "white_check_mark"
    else
        local rc=$?
        local tail_output; tail_output=$(tail -n 20 "$LOG_FILE")
        notify "TimePilot ${MODE}: FAILED" "${desc} (exit ${rc})
---
${tail_output}" "rotating_light"
        echo "FAILED: ${desc} - see ${LOG_FILE}"
        exit "$rc"
    fi
}

# full_only run_step ... : skipped in --heal mode.
full_only() { [[ "${MODE}" == "full" ]] || return 0; "$@"; }

[[ "${MODE}" == "full" ]] && notify "TimePilot setup: started" "Running setup checks on $(hostname)."

DNS_CHANGED=false   # set when the A record is rewritten, so the final check knows to wait for resolvers

### 1. Updates ###
check_updates() { false; }   # always safe/cheap to re-run, no meaningful skip condition
step_updates() { apt update && apt -y upgrade; }
full_only run_step "apt update & upgrade" check_updates step_updates

### 2. Base deps ###
check_deps() { dpkg -s nginx certbot python3-certbot-dns-cloudflare git openssl iptables-persistent cron >/dev/null 2>&1 && systemctl is-active --quiet cron; }
step_deps() {
    echo iptables-persistent iptables-persistent/autosave_v4 boolean true | debconf-set-selections
    echo iptables-persistent iptables-persistent/autosave_v6 boolean true | debconf-set-selections
    # Ubuntu 24.04 minimal doesn't ship cron by default - needed for the nightly reset job.
    apt install -y ca-certificates curl gnupg nginx certbot python3-certbot-dns-cloudflare git openssl iptables-persistent cron
    systemctl enable --now cron
}
run_step "install base dependencies" check_deps step_deps

### 2b. SSH hardening: switch to SSH_PORT, disable root login + password auth. ###
### Old port 22 stays open in the firewall until the new port is CONFIRMED listening - ###
### only then is 22 closed. If verification fails, this step aborts and leaves 22 open ###
### rather than risk locking you out over a config problem. ###
check_ssh_hardening() {
    [[ -f /etc/ssh/sshd_config.d/99-hardening.conf ]] \
      && grep -q "^Port ${SSH_PORT}$" /etc/ssh/sshd_config.d/99-hardening.conf \
      && ss -tlnp 2>/dev/null | grep -q ":${SSH_PORT} " \
      && ! iptables -C INPUT -p tcp --dport 22 -j ACCEPT >/dev/null 2>&1
}
step_ssh_hardening() {
    # Ubuntu 24.04 often ships ssh.socket for socket-activation, which hardcodes ListenStream=22
    # independent of sshd_config's Port directive - restarting ssh.service alone won't rebind
    # the actual listening socket while this is active. Disable it in favor of plain ssh.service.
    if systemctl is-active --quiet ssh.socket 2>/dev/null; then
        systemctl disable --now ssh.socket
        systemctl enable ssh.service
    fi

    # Figure out what port was previously configured (default 22 on a first-ever run) so we
    # know exactly what to close later - not just literal 22 - if SSH_PORT has changed again.
    local old_port="22"
    if [[ -f /etc/ssh/sshd_config.d/99-hardening.conf ]]; then
        old_port=$(grep -oP '^Port \K[0-9]+' /etc/ssh/sshd_config.d/99-hardening.conf 2>/dev/null || echo "22")
    fi

    # sshd -t (and sshd itself) requires the privilege-separation dir. On a fresh boot where we've
    # just disabled ssh.socket, ssh.service may not have started yet to create it - make it ourselves.
    mkdir -p /run/sshd
    chmod 0755 /run/sshd

    # Open the new port first so sshd has somewhere safe to land before we touch its config
    iptables -C INPUT -p tcp --dport "${SSH_PORT}" -j ACCEPT >/dev/null 2>&1 \
        || iptables -I INPUT -p tcp --dport "${SSH_PORT}" -j ACCEPT

    cat > /etc/ssh/sshd_config.d/99-hardening.conf <<EOF
Port ${SSH_PORT}
PermitRootLogin no
PasswordAuthentication no
PermitEmptyPasswords no
MaxAuthTries 3
X11Forwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
LoginGraceTime 30
EOF

    if ! sshd -t; then
        echo "sshd config test failed - leaving old config/port in place"
        rm -f /etc/ssh/sshd_config.d/99-hardening.conf
        return 1
    fi

    systemctl restart ssh

    local listening=false
    for _ in $(seq 1 10); do
        if ss -tlnp 2>/dev/null | grep -q ":${SSH_PORT} "; then listening=true; break; fi
        sleep 1
    done
    if [[ "${listening}" != "true" ]]; then
        echo "sshd did not come up on port ${SSH_PORT} - aborting, old port ${old_port} stays open"
        return 1
    fi

    # New port confirmed working - safe to close whatever the old port was
    if [[ "${old_port}" != "${SSH_PORT}" ]]; then
        while iptables -C INPUT -p tcp --dport "${old_port}" -j ACCEPT >/dev/null 2>&1; do
            iptables -D INPUT -p tcp --dport "${old_port}" -j ACCEPT
        done
        # Oracle's default image also ships this exact variant for port 22 specifically
        while iptables -C INPUT -p tcp --dport "${old_port}" -m state --state NEW -j ACCEPT >/dev/null 2>&1; do
            iptables -D INPUT -p tcp --dport "${old_port}" -m state --state NEW -j ACCEPT
        done
    fi
    netfilter-persistent save
}
full_only run_step "harden SSH + switch to port ${SSH_PORT}" check_ssh_hardening step_ssh_hardening

### 2c. Firewall: only SSH_PORT and HTTPS (443) reachable; everything else dropped ###
check_firewall() {
    iptables -C INPUT -p tcp --dport "${SSH_PORT}" -j ACCEPT >/dev/null 2>&1 \
      && iptables -C INPUT -p tcp --dport 443 -j ACCEPT >/dev/null 2>&1 \
      && ! iptables -C INPUT -p tcp --dport 80 -j ACCEPT >/dev/null 2>&1 \
      && ! iptables -C INPUT -p tcp --dport 22 -j ACCEPT >/dev/null 2>&1
}
step_firewall() {
    iptables -C INPUT -p tcp --dport "${SSH_PORT}" -j ACCEPT >/dev/null 2>&1 || iptables -I INPUT -p tcp --dport "${SSH_PORT}" -j ACCEPT
    iptables -C INPUT -p tcp --dport 443 -j ACCEPT >/dev/null 2>&1 || iptables -I INPUT -p tcp --dport 443 -j ACCEPT
    # Remove port 80 and the old SSH port 22 if either is still open
    while iptables -C INPUT -p tcp --dport 80 -j ACCEPT >/dev/null 2>&1; do
        iptables -D INPUT -p tcp --dport 80 -j ACCEPT
    done
    while iptables -C INPUT -p tcp --dport 22 -j ACCEPT >/dev/null 2>&1; do
        iptables -D INPUT -p tcp --dport 22 -j ACCEPT
    done
    # Ensure a final catch-all reject exists so nothing else is reachable
    iptables -C INPUT -j REJECT --reject-with icmp-host-prohibited >/dev/null 2>&1 \
      || iptables -A INPUT -j REJECT --reject-with icmp-host-prohibited
    netfilter-persistent save
}
full_only run_step "lock down firewall to ${SSH_PORT}+443 only" check_firewall step_firewall

### 2d. Unattended security upgrades ###
check_unattended() { dpkg -s unattended-upgrades >/dev/null 2>&1 && [[ -f /etc/apt/apt.conf.d/20auto-upgrades ]]; }
step_unattended() {
    apt install -y unattended-upgrades
    cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
    systemctl enable --now unattended-upgrades
}
full_only run_step "enable unattended security upgrades" check_unattended step_unattended

### 2e. fail2ban - bans an IP for FAIL2BAN_BANTIME after FAIL2BAN_MAXRETRY failed SSH attempts ###
check_fail2ban() {
    dpkg -s fail2ban >/dev/null 2>&1 || return 1
    systemctl is-active --quiet fail2ban || return 1
    [[ -f /etc/fail2ban/jail.d/sshd.local ]] || return 1
    grep -q "^port = ${SSH_PORT}$" /etc/fail2ban/jail.d/sshd.local \
      && grep -q "^maxretry = ${FAIL2BAN_MAXRETRY}$" /etc/fail2ban/jail.d/sshd.local \
      && grep -q "^findtime = ${FAIL2BAN_FINDTIME}$" /etc/fail2ban/jail.d/sshd.local \
      && grep -q "^bantime = ${FAIL2BAN_BANTIME}$" /etc/fail2ban/jail.d/sshd.local
}
step_fail2ban() {
    apt install -y fail2ban
    cat > /etc/fail2ban/jail.d/sshd.local <<EOF
[sshd]
enabled = true
port = ${SSH_PORT}
maxretry = ${FAIL2BAN_MAXRETRY}
findtime = ${FAIL2BAN_FINDTIME}
bantime = ${FAIL2BAN_BANTIME}
EOF
    systemctl enable --now fail2ban
    systemctl restart fail2ban
}
full_only run_step "configure fail2ban for sshd" check_fail2ban step_fail2ban

### 3. Docker. Repo file also gets rewritten if the OS codename has moved on (e.g. an ###
### in-place release upgrade) rather than treating mere file existence as "done". ###
check_docker_repo() {
    [[ -f /etc/apt/sources.list.d/docker.list ]] \
      && grep -qF " $(. /etc/os-release && echo "$VERSION_CODENAME") " /etc/apt/sources.list.d/docker.list
}
step_docker_repo() {
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list
    apt update
}
run_step "add Docker repo" check_docker_repo step_docker_repo

check_docker_install() { command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; }
step_docker_install() { apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; }
run_step "install Docker" check_docker_install step_docker_install

# The daemon must be enabled + running or nothing below can come up after a reboot/crash.
check_docker_running() { systemctl is-enabled --quiet docker && systemctl is-active --quiet docker; }
step_docker_running() { systemctl enable --now docker; }
run_step "enable + start Docker" check_docker_running step_docker_running

### 4. Clone TimePilot (never wipe an existing checkout - it holds .env/secrets) ###
check_clone() { [[ -d /opt/TimePilot/.git ]]; }
step_clone() {
    cd /opt || return 1
    git clone https://github.com/shanemc92/TimePilot.git
    cd /opt/TimePilot || return 1
    cp .env.example .env
}
run_step "clone TimePilot repo" check_clone step_clone

### 4b. Always pull latest TimePilot code on rerun - .env is gitignored/untracked so this ###
### never touches secrets; git reset --hard discards any local drift (there shouldn't be any) ###
### and guarantees the working tree matches origin exactly, same as a fresh clone would give. ###
### (--heal skips this: the nightly reset already pulls, and a repair pass shouldn't change code.) ###
check_repo_update() { false; }   # always re-check for updates, cheap no-op if already current
step_repo_update() {
    cd /opt/TimePilot || return 1
    git fetch origin
    git reset --hard origin/HEAD
}
full_only run_step "update TimePilot repo to latest" check_repo_update step_repo_update

### 5. Generate secrets into .env - NEVER regenerate if already set. ###
### Rotating TIMEPILOT_MASTER_KEY after data exists makes all existing data unrecoverable. ###
check_secrets() {
    [[ -f /opt/TimePilot/.env ]] \
      && grep -q '^POSTGRES_PASSWORD=.\+' /opt/TimePilot/.env \
      && grep -q '^FLASK_SECRET_KEY=.\+' /opt/TimePilot/.env \
      && grep -q '^TIMEPILOT_MASTER_KEY=.\+' /opt/TimePilot/.env
}
step_secrets() {
    cd /opt/TimePilot || return 1
    local pg_pass flask_secret master_key
    pg_pass=$(openssl rand -hex 24)
    flask_secret=$(openssl rand -hex 32)
    master_key=$(python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pg_pass}|" .env
    sed -i "s|^FLASK_SECRET_KEY=.*|FLASK_SECRET_KEY=${flask_secret}|" .env
    sed -i "s|^TIMEPILOT_MASTER_KEY=.*|TIMEPILOT_MASTER_KEY=${master_key}|" .env
}
run_step "generate .env secrets" check_secrets step_secrets

### 5b. Demo-mode env vars - not secrets, safe to always keep in sync (unlike the guarded values above). ###
### Requires app support for these two vars in TimePilot's code (not upstream by default). ###
check_demo_env() {
    [[ -f /opt/TimePilot/.env ]] || return 1
    grep -q "^TIMEPILOT_DISABLE_SIGNUP=${TIMEPILOT_DISABLE_SIGNUP}$" /opt/TimePilot/.env || return 1
    # Compare only the template's fixed text (prefix/suffix around %s), not the whole line -
    # the password portion legitimately changes every night via daily-reset.sh and shouldn't
    # cause this to re-fire and stomp on that night's already-rotated password.
    local prefix="${TIMEPILOT_LOGIN_BANNER_TEMPLATE%%%s*}"
    local suffix="${TIMEPILOT_LOGIN_BANNER_TEMPLATE##*%s}"
    local current
    current=$(grep '^TIMEPILOT_LOGIN_BANNER=' /opt/TimePilot/.env | sed 's/^TIMEPILOT_LOGIN_BANNER=//')
    [[ -n "${current}" && "${current}" == "${prefix}"*"${suffix}" ]]
}
step_demo_env() {
    cd /opt/TimePilot || return 1
    local banner
    # Substitute the literal %s placeholder rather than using printf, so a template containing
    # other % characters (e.g. "50% off") isn't misinterpreted as a printf format spec.
    banner="${TIMEPILOT_LOGIN_BANNER_TEMPLATE//%s/${DEMO_PASSWORD}}"
    if grep -q '^TIMEPILOT_DISABLE_SIGNUP=' .env; then
        sed -i "s|^TIMEPILOT_DISABLE_SIGNUP=.*|TIMEPILOT_DISABLE_SIGNUP=${TIMEPILOT_DISABLE_SIGNUP}|" .env
    else
        echo "TIMEPILOT_DISABLE_SIGNUP=${TIMEPILOT_DISABLE_SIGNUP}" >> .env
    fi
    # Write the banner via python (not sed) - the banner is free text that may contain characters
    # like | that would collide with sed's delimiter, or & that sed treats specially.
    BANNER_VALUE="${banner}" python3 - <<'PY'
import os
path = ".env"
key = "TIMEPILOT_LOGIN_BANNER"
val = os.environ["BANNER_VALUE"]
lines = []
found = False
try:
    with open(path) as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    pass
for i, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[i] = f"{key}={val}"
        found = True
        break
if not found:
    lines.append(f"{key}={val}")
with open(path, "w") as f:
    f.write("\n".join(lines) + "\n")
PY
}
run_step "set demo-mode env vars" check_demo_env step_demo_env

### 6. Publish the app port to localhost via an override file (upstream compose.yml deliberately keeps it internal-only). ###
### Using an override, not editing docker-compose.yml directly, so this survives `git pull` / image updates. ###
### The `ports:` mapping alone isn't enough: docker-compose.yml's `internal` network has `internal: true`, ###
### which blocks Docker's NAT/port-publish machinery for any container solely on it. Fix: attach timepilot ###
### to a second, non-internal network too (postgres stays untouched, still internal-only). ###
### Also passes TIMEPILOT_DISABLE_SIGNUP/TIMEPILOT_LOGIN_BANNER through, since docker-compose.yml ###
### doesn't reference them upstream - .env alone wouldn't reach the container without this. ###
check_override() {
    [[ -f /opt/TimePilot/docker-compose.override.yml ]] \
      && grep -q "proxy" /opt/TimePilot/docker-compose.override.yml \
      && grep -q "TIMEPILOT_DISABLE_SIGNUP" /opt/TimePilot/docker-compose.override.yml
}
step_override() {
    cat > /opt/TimePilot/docker-compose.override.yml <<'EOF'
networks:
  proxy:
    internal: false

services:
  timepilot:
    ports:
      - "127.0.0.1:5170:5170"
    networks:
      - internal
      - proxy
    environment:
      TIMEPILOT_DISABLE_SIGNUP: ${TIMEPILOT_DISABLE_SIGNUP}
      TIMEPILOT_LOGIN_BANNER: ${TIMEPILOT_LOGIN_BANNER}
EOF
}
run_step "add port-publish + network override" check_override step_override

### 7. Bring up the stack - docker compose up -d is naturally idempotent, always safe to re-run ###
### (--heal skips the forced pull + up; the health check below recreates the container if it's down.) ###
check_compose_up() { false; }
step_compose_up() { cd /opt/TimePilot && docker compose pull && docker compose up -d; }
full_only run_step "docker compose up" check_compose_up step_compose_up

check_healthcheck() { curl -fsS http://127.0.0.1:5170/healthz >/dev/null 2>&1; }
step_healthcheck() {
    cd /opt/TimePilot || return 1
    # Heal mode never ran the full "up -d" above, so bring the stack up first if nothing is running
    # (e.g. after a host reboot or a manual "docker compose down").
    docker compose ps --status running --services 2>/dev/null | grep -Fxq timepilot || docker compose up -d >/dev/null 2>&1
    for _ in {1..30}; do
        if curl -fsS http://127.0.0.1:5170/healthz >/dev/null 2>&1; then return 0; fi
        # The app isn't safe against concurrent schema creation across gunicorn workers on a fresh
        # DB - a worker can lose a CREATE TABLE race and crash the container into a restart loop.
        # A plain "up -d" is a no-op against a restarting container, so force-recreate when it isn't
        # running. The proxy network already exists (full up -d ran above), so a scoped recreate
        # safely reconnects it; fall back to full up -d if the network somehow went away.
        state=$(docker inspect -f '{{.State.Status}}' timepilot-timepilot-1 2>/dev/null || echo missing)
        if [[ "$state" != "running" ]]; then
            docker compose up -d --force-recreate timepilot >/dev/null 2>&1 || docker compose up -d >/dev/null 2>&1
        fi
        sleep 5
    done
    return 1
}
run_step "wait for TimePilot healthz" check_healthcheck step_healthcheck

### 8. Cloudflare credentials ###
check_cf_creds() { [[ -f /root/.secrets/cloudflare.ini ]] && grep -qF "dns_cloudflare_api_token = ${CF_API_TOKEN}" /root/.secrets/cloudflare.ini; }
step_cf_creds() {
    mkdir -p /root/.secrets
    ( umask 077
      cat > /root/.secrets/cloudflare.ini <<EOF
dns_cloudflare_api_token = ${CF_API_TOKEN}
EOF
    )
    chmod 600 /root/.secrets/cloudflare.ini
}
run_step "write Cloudflare credentials" check_cf_creds step_cf_creds

### 9. Sync the DNS A record to this box's public IP via Cloudflare API - no manual DNS step needed ###
CF_API="https://api.cloudflare.com/client/v4"

cf_zone_id() {
    curl -fsS -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" \
        "${CF_API}/zones?name=${CF_ZONE_NAME}" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result'][0]['id'])" 2>/dev/null
}

# One GET returns both id and content (tab-separated) - avoids a second round trip to the
# same endpoint just to read the other field.
cf_record_lookup() {
    local zone_id="$1"
    curl -fsS -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" \
        "${CF_API}/zones/${zone_id}/dns_records?type=A&name=${DOMAIN}" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['result']; print(f\"{r[0]['id']}\t{r[0]['content']}\" if r else '\t')" 2>/dev/null
}

check_dns_record() {
    local public_ip zone_id record_content
    public_ip=$(curl -fsS -m 10 https://ifconfig.me) || return 1
    zone_id=$(cf_zone_id) || return 1
    [[ -n "${zone_id}" ]] || return 1
    IFS=$'\t' read -r _ record_content <<< "$(cf_record_lookup "${zone_id}")"
    [[ "${record_content}" == "${public_ip}" ]]
}

step_dns_record() {
    local public_ip zone_id record_id record_content payload
    public_ip=$(curl -fsS -m 10 https://ifconfig.me)
    zone_id=$(cf_zone_id)
    [[ -n "${zone_id}" ]] || { echo "Could not find Cloudflare zone ${CF_ZONE_NAME} - check the token's zone access"; return 1; }
    IFS=$'\t' read -r record_id record_content <<< "$(cf_record_lookup "${zone_id}")"
    payload="{\"type\":\"A\",\"name\":\"${DOMAIN}\",\"content\":\"${public_ip}\",\"ttl\":300,\"proxied\":${CF_PROXIED}}"

    if [[ -n "${record_id}" ]]; then
        curl -fsS -X PATCH -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" \
            "${CF_API}/zones/${zone_id}/dns_records/${record_id}" --data "${payload}" >/dev/null
    else
        curl -fsS -X POST -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" \
            "${CF_API}/zones/${zone_id}/dns_records" --data "${payload}" >/dev/null
    fi
    DNS_CHANGED=true
}
run_step "sync DNS A record via Cloudflare API" check_dns_record step_dns_record

### 10. Obtain / renew the cert. DNS-01 uses a TXT record, independent of the A record above, so no wait needed here. ###
### "Valid" means BOTH: the cert on disk has more than CERT_RENEW_DAYS left, AND (once nginx is serving it) ###
### the cert nginx is actually presenting does too. Checking only the file misses the classic failure: ###
### certbot renewed the file but nginx never reloaded, so visitors keep getting the expired one. ###
CERT_PEM="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
cert_secs() { echo $(( CERT_RENEW_DAYS * 86400 )); }
served_cert_ok() {
    echo | timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "${DOMAIN}" 2>/dev/null \
      | openssl x509 -noout -checkend "$(cert_secs)" >/dev/null 2>&1
}
check_cert_valid() {
    [[ -f "${CERT_PEM}" ]] || return 1
    openssl x509 -checkend "$(cert_secs)" -noout -in "${CERT_PEM}" >/dev/null 2>&1 || return 1
    # Only judge the served cert once nginx is up with our site enabled (not on a first run).
    if systemctl is-active --quiet nginx && [[ -L /etc/nginx/sites-enabled/timepilot ]]; then
        served_cert_ok || return 1
    fi
    return 0
}
step_cert() {
    if [[ -d "/etc/letsencrypt/renewal" && -f "/etc/letsencrypt/renewal/${DOMAIN}.conf" ]]; then
        # Existing lineage (including an already-expired one): renew in place. --force-renewal because
        # we only get here when the cert is inside our own, wider, renewal window or isn't being served.
        if ! openssl x509 -checkend "$(cert_secs)" -noout -in "${CERT_PEM}" >/dev/null 2>&1; then
            certbot renew --cert-name "${DOMAIN}" --force-renewal --non-interactive
        fi
    else
        certbot certonly \
          --dns-cloudflare \
          --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
          --dns-cloudflare-propagation-seconds "${DNS_PROPAGATION_BUFFER}" \
          -d "${DOMAIN}" \
          -m "${LE_EMAIL}" \
          --agree-tos --non-interactive
    fi
    # Whether or not certbot had to do anything, make nginx pick up what's on disk (fixes the
    # renewed-but-never-reloaded case). Restart if a reload isn't enough.
    if systemctl is-active --quiet nginx; then
        nginx -t && { systemctl reload nginx || systemctl restart nginx; }
    fi
}
run_step "obtain/renew Let's Encrypt cert" check_cert_valid step_cert

# certbot's own renewal timer is the first line of defence - make sure it exists and is running.
check_certbot_timer() { systemctl is-enabled --quiet certbot.timer 2>/dev/null && systemctl is-active --quiet certbot.timer; }
step_certbot_timer() { systemctl enable --now certbot.timer; }
run_step "enable certbot renewal timer" check_certbot_timer step_certbot_timer

### 11. Nginx reverse proxy config ###
check_nginx_config() {
    [[ -L /etc/nginx/sites-enabled/timepilot ]] && nginx -t >/dev/null 2>&1 \
      && systemctl is-enabled --quiet nginx && systemctl is-active --quiet nginx
}
step_nginx_config() {
    cat > /etc/nginx/sites-available/timepilot <<EOF
# Port 80 is intentionally not served - the firewall only allows ${SSH_PORT} and 443,
# so there's nothing to redirect from an http:// hit anyway.
server {
    listen 443 ssl;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5170;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    ln -sf /etc/nginx/sites-available/timepilot /etc/nginx/sites-enabled/timepilot
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl enable nginx
    systemctl reload nginx || systemctl restart nginx
}
run_step "configure nginx" check_nginx_config step_nginx_config

### 12. Renewal hook ###
check_renew_hook() { [[ -x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh ]]; }
step_renew_hook() {
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
    chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
}
run_step "install renewal hook" check_renew_hook step_renew_hook

### 13. Daily ephemeral reset - wipes the DB (fresh schema), reseeds demo data, cleans logs/temp files ###
check_daily_reset() {
    [[ -x /opt/TimePilot/daily-reset.sh ]] || return 1
    [[ -f /etc/cron.d/timepilot-demo-reset ]] || return 1
    # daily-reset.sh bakes in the banner as prefix + ${NEW_PASSWORD} + suffix, so match on the
    # fixed prefix text (before %s) rather than the full template string.
    local banner_prefix="${TIMEPILOT_LOGIN_BANNER_TEMPLATE%%%s*}"
    grep -qF "${banner_prefix}" /opt/TimePilot/daily-reset.sh || return 1
    grep -qF "\"${DEMO_USERNAME}\"" /opt/TimePilot/daily-reset.sh || return 1
}
step_daily_reset() {
    # Pre-compute the banner prefix/suffix around the %s placeholder so daily-reset.sh can rebuild
    # the banner each night with a fresh password, without depending on any init-script variable
    # existing at cron runtime (which it wouldn't - that would crash under set -u).
    local banner_prefix="${TIMEPILOT_LOGIN_BANNER_TEMPLATE%%%s*}"
    local banner_suffix="${TIMEPILOT_LOGIN_BANNER_TEMPLATE##*%s}"
    cat > /opt/TimePilot/daily-reset.sh <<EOF
#!/usr/bin/env bash
set -uo pipefail
cd /opt/TimePilot

# Pick up any repo changes since last reset (docker-compose.yml, sample_data.py, etc.), and
# pull the latest published image. Best-effort: a transient network hiccup shouldn't abort
# the whole nightly reset.
git fetch origin >/dev/null 2>&1 && git reset --hard origin/HEAD >/dev/null 2>&1
docker compose pull >/dev/null 2>&1 || true

# Generate a fresh demo password for today and splice it into the login banner.
# Prefix/suffix are baked in literally at generation time; python (not sed) writes .env so
# a | or & in the banner text can't break the substitution.
NEW_PASSWORD=\$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 12)
BANNER="${banner_prefix}\${NEW_PASSWORD}${banner_suffix}"
BANNER_VALUE="\${BANNER}" python3 - <<'PY'
import os
path = ".env"
key = "TIMEPILOT_LOGIN_BANNER"
val = os.environ["BANNER_VALUE"]
lines = []
found = False
try:
    with open(path) as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    pass
for i, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[i] = f"{key}={val}"
        found = True
        break
if not found:
    lines.append(f"{key}={val}")
with open(path, "w") as f:
    f.write("\n".join(lines) + "\n")
PY

# Full wipe: tear down and drop the postgres volume so the app recreates its schema from scratch,
# same as a brand-new install. (docker compose down -v only touches this project's own volumes.)
docker compose down -v
docker compose up -d

# Wait for the app to become healthy. The app isn't safe against concurrent schema creation
# across gunicorn workers on a freshly-wiped DB - one worker can lose a CREATE TABLE race and
# crash the container, which then enters a restart loop (restart: unless-stopped). A plain
# "up -d" is a no-op against an already-existing/restarting container, so we force-recreate
# when timepilot isn't in the running state. --force-recreate tears down the looping container
# and starts it clean; on an empty DB the winning worker usually creates the schema before the
# next crash, so a couple of forced recreates reliably converges.
for i in {1..30}; do
    curl -fsS http://127.0.0.1:5170/healthz >/dev/null 2>&1 && break
    state=\$(docker inspect -f '{{.State.Status}}' timepilot-timepilot-1 2>/dev/null || echo missing)
    if [[ "\$state" != "running" ]]; then
        docker compose up -d --force-recreate timepilot >/dev/null 2>&1 || docker compose up -d >/dev/null 2>&1
    fi
    sleep 5
done

docker compose exec -T timepilot python sample_data.py --username "${DEMO_USERNAME}" --password "\${NEW_PASSWORD}" --force

# Clean host-level logs and temp data
truncate -s 0 /var/log/timepilot-init.log /var/log/timepilot-demo-reset.log /var/log/timepilot-heal.log 2>/dev/null || true
find /tmp -mindepth 1 -exec rm -rf {} + 2>/dev/null || true
docker system prune -f >/dev/null 2>&1 || true
journalctl --vacuum-time=1d >/dev/null 2>&1 || true
EOF
    chmod 700 /opt/TimePilot/daily-reset.sh

    cat > /etc/cron.d/timepilot-demo-reset <<'EOF'
# Wipes the DB, reseeds demo data, and cleans logs/temp files daily so the box stays ephemeral.
0 0 * * * root /opt/TimePilot/daily-reset.sh >> /var/log/timepilot-demo-reset.log 2>&1
EOF
    chmod 600 /etc/cron.d/timepilot-demo-reset

    # Seed it once now too, so there's demo data immediately rather than waiting for midnight
    # (no wipe needed here - the DB was just created fresh moments ago by the earlier compose-up step)
    cd /opt/TimePilot && docker compose exec -T timepilot python sample_data.py --username "${DEMO_USERNAME}" --password "${DEMO_PASSWORD}" --force
}
run_step "schedule daily ephemeral reset" check_daily_reset step_daily_reset

### 14. Self-heal cron - runs the INSTALLED copy with --heal, which self-updates first and then repairs ###
### only what is broken: expired/unserved cert, stopped nginx/docker/containers, drifted DNS record, ###
### missing config files. Silent (no ntfy) when everything is fine; notifies when it fixes or fails. ###
### Skipped on the very first run, before the installed copy exists - it's registered by step 15. ###
check_heal_cron() {
    [[ -f /etc/cron.d/timepilot-heal ]] \
      && grep -qF "${INSTALL_PATH} --heal" /etc/cron.d/timepilot-heal \
      && grep -qF "${HEAL_CRON} root" /etc/cron.d/timepilot-heal
}
step_heal_cron() {
    cat > /etc/cron.d/timepilot-heal <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Self-update + repair pass (cert, nginx, docker, DNS). See init-timepilot-demo.sh --heal.
${HEAL_CRON} root ${INSTALL_PATH} --heal >> ${HEAL_LOG} 2>&1
# Also once shortly after every boot, in case the box was down across a renewal window.
@reboot root sleep 120 && ${INSTALL_PATH} --heal >> ${HEAL_LOG} 2>&1
EOF
    chmod 644 /etc/cron.d/timepilot-heal
}
check_logrotate() { [[ -f /etc/logrotate.d/timepilot ]] && grep -qF "${HEAL_LOG}" /etc/logrotate.d/timepilot; }
step_logrotate() {
    cat > /etc/logrotate.d/timepilot <<EOF
${LOG_FILE} ${HEAL_LOG} /var/log/timepilot-demo-reset.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notempty
    copytruncate
}
EOF
    chmod 644 /etc/logrotate.d/timepilot
}
if [[ -x "${INSTALL_PATH}" || "${SCRIPT_PATH}" == "${INSTALL_PATH}" ]]; then
    run_step "schedule self-heal cron" check_heal_cron step_heal_cron
fi
run_step "install logrotate config" check_logrotate step_logrotate

### 15. Final verification (always runs, cheap, no state to guard) ###
### Validates the certificate (no -k): the old check passed with an expired cert, which is exactly ###
### how an expiry went unnoticed. --resolve pins the request to this box's own nginx, so it tests ###
### what we serve rather than whatever DNS/Cloudflare currently points at. ###
[[ "${DNS_CHANGED}" == "true" || "${MODE}" == "full" ]] && sleep "${DNS_PROPAGATION_BUFFER}"
if curl -fsS -m 20 --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/healthz" >/dev/null 2>&1; then
    [[ "${MODE}" == "full" ]] && notify "TimePilot setup: COMPLETE" "https://${DOMAIN}/login is live." "tada"
    echo "Done. Visit https://${DOMAIN}/login"
else
    notify "TimePilot ${MODE}: FAILED" "All steps reported OK but https://${DOMAIN}/healthz is not responding with a valid certificate." "rotating_light"
    exit 1
fi

[[ "${MODE}" == "heal" ]] && exit 0

### 16. Install a token-free copy of this script at INSTALL_PATH, then shred the original. ###
### The real config (incl. CF_API_TOKEN/DEMO_PASSWORD) lives only in SECRETS_ENV (0600) and is loaded ###
### by the installed copy at runtime, so nothing sensitive stays on disk in a script. cloudflare.ini ###
### stays put - certbot's renewal timer needs it. Deferred via systemd-run so the delete happens in a ###
### separate process after this one has fully exited. Re-running from the installed copy skips this. ###
if [[ "${SCRIPT_PATH}" == "${INSTALL_PATH}" ]]; then
    [[ -f /etc/cron.d/timepilot-heal ]] || step_heal_cron
    notify "TimePilot setup: done" "Re-run complete from ${INSTALL_PATH}." "white_check_mark"
    exit 0
fi

if [[ -f "${SCRIPT_PATH}" ]]; then
    ( umask 077
      sed -e 's|^CF_API_TOKEN=.*|CF_API_TOKEN=""                     # loaded from /root/.secrets/timepilot-deploy.env at runtime|' \
          -e 's|^DEMO_PASSWORD=.*|DEMO_PASSWORD="ChangeThisDemoPass"  # real value loaded from /root/.secrets/timepilot-deploy.env|' \
          "${SCRIPT_PATH}" > "${INSTALL_PATH}" )
    chmod 700 "${INSTALL_PATH}"
    if grep -q '^CF_API_TOKEN=""' "${INSTALL_PATH}" && [[ -s "${SECRETS_ENV}" ]] && bash -n "${INSTALL_PATH}"; then
        echo "==> installed token-free copy at ${INSTALL_PATH}"
        step_heal_cron   # the installed copy exists now, so the heal cron can point at it
        notify "TimePilot setup: cleanup" "Installed ${INSTALL_PATH} + self-heal cron; deleting the original in 15s (secrets stay only in /root/.secrets)." "broom"
        systemd-run --on-active=15 --unit="timepilot-init-cleanup" --description="Delete TimePilot init script" \
            -- shred -u -- "${SCRIPT_PATH}"
    else
        # Sanitising/saving failed - never shred the original in that case.
        rm -f "${INSTALL_PATH}"
        echo "WARN: could not install a token-free copy - leaving ${SCRIPT_PATH} in place."
        notify "TimePilot setup: WARN" "Could not install a token-free copy; original script left on disk and no self-heal cron." "warning"
    fi
fi
exit 0
