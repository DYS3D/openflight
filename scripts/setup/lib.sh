# Shared helpers for the OpenFlight setup scripts: scripts/install.sh,
# scripts/setup/setup.sh, scripts/setup/flash-iwr6843.sh and
# scripts/openflight-doctor.sh. Source it; it only defines functions.
# shellcheck shell=bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { printf '%b[OpenFlight]%b %s\n' "$GREEN" "$NC" "$1"; }
info()  { printf '%b[OpenFlight]%b %s\n' "$BLUE" "$NC" "$1"; }
warn()  { printf '%b[OpenFlight]%b %s\n' "$YELLOW" "$NC" "$1" >&2; }
error() { printf '%b[OpenFlight]%b %s\n' "$RED" "$NC" "$1" >&2; }
die()   { error "ERROR: $1"; exit 1; }

# Run a command, or only print it when DRY_RUN=true.
run() {
    if [ "${DRY_RUN:-false}" = true ]; then
        printf '[dry-run]'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

# systemd and non-login shells omit the directories astral's installer uses.
of_ensure_uv_path() {
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

of_install_uv() {
    of_ensure_uv_path
    if command -v uv >/dev/null 2>&1; then
        log "uv $(uv --version | awk '{print $2}') found"
        return 0
    fi
    log "Installing uv (Python package manager)..."
    if [ "${DRY_RUN:-false}" = true ]; then
        printf '[dry-run] curl -LsSf https://astral.sh/uv/install.sh | sh\n'
        return 0
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
}

# Print a unit from scripts/setup rewritten for $USER and the checkout at
# $PROJECT_DIR. Every /home/coleman path in the templates is under the checkout.
of_render_unit() {
    sed -e "s|^User=.*|User=$USER|" \
        -e "s|/home/coleman/openflight|$PROJECT_DIR|g" \
        "$1"
}

# Stop a running openflight.service until this shell exits, so the caller can
# open the radar ports without another process reading them. Returns 1 when
# the service was not running.
of_pause_service() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl is-active --quiet openflight 2>/dev/null || return 1
    log "Stopping OpenFlight so the radar ports are free (restarted on exit)..."
    sudo systemctl stop openflight
    trap 'log "Restarting OpenFlight..."; sudo systemctl start openflight' EXIT
}
