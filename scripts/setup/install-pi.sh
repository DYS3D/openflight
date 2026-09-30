#!/usr/bin/env bash
#
# One-command OpenFlight installer for Raspberry Pi OS (Bookworm or newer).
#
# Installs system packages, Node.js, uv, Python and UI dependencies; adds the
# user to the hardware groups; installs udev rules; optionally configures the
# GPIO UART for an OPS243-A on the 40-pin header; installs the boot-time kiosk
# service; then runs the post-install self-test.
#
# From a fresh Pi:
#   curl -fsSL https://raw.githubusercontent.com/open-flight/openflight/main/scripts/setup/install-pi.sh | bash
# From a clone:
#   ./scripts/setup/install-pi.sh [options]
#
# Options:
#   --dir DIR            Checkout location when not run from a clone (default ~/openflight)
#   --repo URL           Git repository to clone (default upstream)
#   --branch NAME        Branch to clone or update (default main)
#   --uart               OPS243-A is wired to the GPIO UART (/dev/ttyAMA0)
#   --lan                Serve the read-only display to other devices on the LAN
#   --altitude-ft N      Site elevation for the ballistic carry model
#   --temperature-f N    Typical air temperature for the ballistic carry model
#   --server-args "..."  Extra arguments for the OpenFlight server
#   --no-service         Do not install the boot-time kiosk service
#   --no-kiosk           Do not enable desktop autologin / disable screen blanking
#   --skip-self-test     Do not run the self-test at the end
#   --dry-run            Print what would change without changing anything
#   -h, --help           Show this help
#
# Safe to re-run: every step is idempotent.

set -euo pipefail

DEFAULT_REPO="https://github.com/open-flight/openflight.git"
UDEV_RULES_DEST="/etc/udev/rules.d/99-openflight.rules"
ENV_FILE="/etc/default/openflight"
SERVICE_DEST="/etc/systemd/system/openflight.service"
UART_MARKER="# OpenFlight UART"
MIN_NODE_MAJOR=22

INSTALL_DIR="${HOME}/openflight"
REPO_URL="$DEFAULT_REPO"
BRANCH="main"
USE_UART=false
LAN=false
ALTITUDE_FT=""
TEMPERATURE_F=""
EXTRA_SERVER_ARGS=""
INSTALL_SERVICE=true
CONFIGURE_KIOSK=true
RUN_SELF_TEST=true
DRY_RUN=false
REBOOT_NEEDED=false

log() { printf '\033[0;32m[OpenFlight]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[OpenFlight]\033[0m %s\n' "$1" >&2; }
die() { printf '\033[0;31m[OpenFlight] ERROR:\033[0m %s\n' "$1" >&2; exit 1; }

# Run a command, or print it under --dry-run.
run() {
    if [ "$DRY_RUN" = true ]; then
        printf '[dry-run]'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

# Write stdin to a root-owned file, or show it under --dry-run.
write_root_file() {
    local dest="$1" content
    content="$(cat)"
    if [ "$DRY_RUN" = true ]; then
        printf '[dry-run] write %s:\n%s\n' "$dest" "$content"
        return 0
    fi
    printf '%s\n' "$content" | sudo tee "$dest" >/dev/null
}

usage() {
    awk 'NR>2 && !/^#/{exit} NR>2{sub(/^# ?/,""); print}' "$0"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dir) INSTALL_DIR="${2:?--dir needs a value}"; shift 2 ;;
            --repo) REPO_URL="${2:?--repo needs a value}"; shift 2 ;;
            --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
            --uart) USE_UART=true; shift ;;
            --lan) LAN=true; shift ;;
            --altitude-ft) ALTITUDE_FT="${2:?--altitude-ft needs a value}"; shift 2 ;;
            --temperature-f) TEMPERATURE_F="${2:?--temperature-f needs a value}"; shift 2 ;;
            --server-args) EXTRA_SERVER_ARGS="${2?--server-args needs a value}"; shift 2 ;;
            --no-service) INSTALL_SERVICE=false; shift ;;
            --no-kiosk) CONFIGURE_KIOSK=false; shift ;;
            --skip-self-test) RUN_SELF_TEST=false; shift ;;
            --dry-run) DRY_RUN=true; shift ;;
            -h|--help) usage; exit 0 ;;
            *) die "Unknown option: $1 (try --help)" ;;
        esac
    done
    if [ -n "$ALTITUDE_FT" ] && ! [[ "$ALTITUDE_FT" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
        die "--altitude-ft must be a number"
    fi
    if [ -n "$TEMPERATURE_F" ] && ! [[ "$TEMPERATURE_F" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
        die "--temperature-f must be a number"
    fi
}

# Server arguments the kiosk service passes to start-kiosk.sh.
server_args() {
    local args=()
    if [ "$USE_UART" = true ]; then args+=(--radar-port /dev/ttyAMA0); fi
    if [ "$LAN" = true ]; then args+=(--host 0.0.0.0); fi
    if [ -n "$ALTITUDE_FT" ]; then args+=(--altitude-ft "$ALTITUDE_FT"); fi
    if [ -n "$TEMPERATURE_F" ]; then args+=(--temperature-f "$TEMPERATURE_F"); fi
    printf '%s' "${args[*]:-}"
    if [ -n "$EXTRA_SERVER_ARGS" ]; then
        [ "${#args[@]}" -gt 0 ] && printf ' '
        printf '%s' "$EXTRA_SERVER_ARGS"
    fi
}

render_env_file() {
    printf '# Written by scripts/setup/install-pi.sh; read by openflight.service.\n'
    printf 'OPENFLIGHT_ARGS="%s"\n' "$(server_args)"
}

# Idempotently enable the 40-pin header UART (UART0 -> /dev/ttyAMA0).
update_uart_boot_config() {
    local config="$1" lines=()
    if grep -qF "$UART_MARKER" "$config" 2>/dev/null; then
        return 0
    fi
    grep -qE '^enable_uart=1' "$config" 2>/dev/null || lines+=("enable_uart=1")
    grep -qE '^dtparam=uart0=on' "$config" 2>/dev/null || lines+=("dtparam=uart0=on")
    printf '\n[all]\n%s\n' "$UART_MARKER" >>"$config"
    if [ "${#lines[@]}" -gt 0 ]; then
        printf '%s\n' "${lines[@]}" >>"$config"
    fi
}

# Remove a serial console from the kernel command line. A console on the radar
# UART sends boot output into the OPS243 RxD pin, where it parses as commands.
strip_serial_console() {
    local cmdline="$1" updated
    updated="$(tr ' ' '\n' <"$cmdline" | grep -vE '^console=(serial[0-9]|ttyAMA[0-9]+|ttyS[0-9]+)(,.*)?$' | grep -v '^$' | paste -sd' ' -)"
    printf '%s\n' "$updated" >"$cmdline"
}

render_service() {
    local project_dir="$1" user="$2" template="$3"
    sed -e "s|^User=.*|User=${user}|" -e "s|/home/coleman/openflight|${project_dir}|g" "$template"
}

detect_platform() {
    [ "$(uname -s)" = Linux ] || die "This installer is for Raspberry Pi OS (Linux)."
    if grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null \
        || grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        log "Detected $(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo 'Raspberry Pi')"
    else
        warn "This does not look like a Raspberry Pi; hardware steps may not apply."
    fi
    if [ "$(id -u)" -eq 0 ]; then
        die "Run as your normal user (not root); the installer uses sudo when needed."
    fi
}

boot_file() {
    local name="$1"
    if [ -f "/boot/firmware/$name" ]; then echo "/boot/firmware/$name"; else echo "/boot/$name"; fi
}

install_system_packages() {
    log "Installing system packages..."
    local chromium=chromium
    if ! apt-cache show chromium >/dev/null 2>&1; then chromium="chromium-browser"; fi
    run sudo apt-get update
    run sudo apt-get install -y git curl ca-certificates python3 python3-venv python3-dev \
        build-essential swig liblgpio-dev ffmpeg avahi-daemon i2c-tools "$chromium"
}

install_node() {
    local major=0
    if command -v node >/dev/null 2>&1; then
        major="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"
    fi
    if [ "$major" -ge "$MIN_NODE_MAJOR" ]; then
        log "Node.js $(node -v) found"
        return 0
    fi
    log "Installing Node.js ${MIN_NODE_MAJOR}.x from NodeSource..."
    if [ "$DRY_RUN" = true ]; then
        run bash -c "curl -fsSL https://deb.nodesource.com/setup_${MIN_NODE_MAJOR}.x | sudo -E bash -"
    else
        curl -fsSL "https://deb.nodesource.com/setup_${MIN_NODE_MAJOR}.x" | sudo -E bash -
    fi
    run sudo apt-get install -y nodejs
}

install_uv() {
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        log "uv $(uv --version | awk '{print $2}') found"
        return 0
    fi
    log "Installing uv..."
    if [ "$DRY_RUN" = true ]; then
        run bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
}

# Use the clone this script lives in, or clone/update INSTALL_DIR.
resolve_project_dir() {
    local candidate=""
    # Piped installs (curl | bash) have no script file, so always clone.
    if [ -f "${BASH_SOURCE[0]:-}" ]; then
        candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    fi
    if [ -n "$candidate" ] && grep -q '^name = "openflight"' "$candidate/pyproject.toml" 2>/dev/null; then
        PROJECT_DIR="$candidate"
        log "Using existing checkout at $PROJECT_DIR"
        return 0
    fi
    PROJECT_DIR="$INSTALL_DIR"
    if [ -d "$PROJECT_DIR/.git" ]; then
        log "Updating $PROJECT_DIR ($BRANCH)..."
        run git -C "$PROJECT_DIR" fetch origin "$BRANCH"
        run git -C "$PROJECT_DIR" checkout "$BRANCH"
        run git -C "$PROJECT_DIR" pull --ff-only origin "$BRANCH"
    else
        log "Cloning $REPO_URL ($BRANCH) into $PROJECT_DIR..."
        run git clone --branch "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
    fi
}

install_project_dependencies() {
    log "Installing Python dependencies..."
    run bash -c "cd '$PROJECT_DIR' && uv sync"
    log "Building the UI..."
    run bash -c "cd '$PROJECT_DIR/ui' && npm ci && npm run build"
}

# Hardware groups that exist on this system but the user is not yet in.
missing_groups() {
    local current group
    current=" $(id -nG "$1") "
    for group in dialout gpio i2c video; do
        getent group "$group" >/dev/null 2>&1 || continue
        [[ "$current" == *" $group "* ]] || printf '%s\n' "$group"
    done
}

configure_groups() {
    local missing
    missing="$(missing_groups "$USER" | paste -sd, -)"
    if [ -z "$missing" ]; then
        log "User $USER already has serial/GPIO/I2C access"
        return 0
    fi
    log "Adding $USER to $missing..."
    run sudo usermod -aG "$missing" "$USER"
    REBOOT_NEEDED=true
}

install_udev_rules() {
    log "Installing udev rules for the radars..."
    write_root_file "$UDEV_RULES_DEST" <"$PROJECT_DIR/scripts/setup/99-openflight.rules"
    run sudo udevadm control --reload-rules
    run sudo udevadm trigger --subsystem-match=tty
}

configure_uart() {
    [ "$USE_UART" = true ] || return 0
    local config cmdline
    config="$(boot_file config.txt)"
    cmdline="$(boot_file cmdline.txt)"
    log "Enabling the GPIO UART in $config and removing the serial console..."
    if [ "$DRY_RUN" = true ]; then
        printf '[dry-run] update %s and %s\n' "$config" "$cmdline"
    else
        local tmp_config tmp_cmdline
        tmp_config="$(mktemp)"; tmp_cmdline="$(mktemp)"
        cp "$config" "$tmp_config"
        cp "$cmdline" "$tmp_cmdline"
        update_uart_boot_config "$tmp_config"
        strip_serial_console "$tmp_cmdline"
        if ! sudo cmp -s "$tmp_config" "$config" || ! sudo cmp -s "$tmp_cmdline" "$cmdline"; then
            sudo cp "$config" "$config.openflight.bak"
            sudo cp "$cmdline" "$cmdline.openflight.bak"
            sudo cp "$tmp_config" "$config"
            sudo cp "$tmp_cmdline" "$cmdline"
            REBOOT_NEEDED=true
        fi
        rm -f "$tmp_config" "$tmp_cmdline"
    fi
    run sudo systemctl disable --now serial-getty@ttyAMA0.service
}

install_service() {
    [ "$INSTALL_SERVICE" = true ] || return 0
    log "Installing the boot-time kiosk service..."
    render_env_file | write_root_file "$ENV_FILE"
    render_service "$PROJECT_DIR" "$USER" "$PROJECT_DIR/scripts/setup/openflight.service" \
        | write_root_file "$SERVICE_DEST"
    run sudo systemctl daemon-reload
    run sudo systemctl enable openflight.service
}

configure_kiosk() {
    [ "$CONFIGURE_KIOSK" = true ] || return 0
    if ! command -v raspi-config >/dev/null 2>&1; then
        warn "raspi-config not found; enable desktop autologin manually for kiosk mode."
        return 0
    fi
    log "Enabling desktop autologin and disabling screen blanking..."
    run sudo raspi-config nonint do_boot_behaviour B4
    run sudo raspi-config nonint do_blanking 1
    run sudo raspi-config nonint do_i2c 0
}

run_self_test() {
    [ "$RUN_SELF_TEST" = true ] || return 0
    log "Running the post-install self-test (hardware checks may fail until you reboot)..."
    local args=(--no-interactive)
    [ "$USE_UART" = true ] && args+=(--ops-port /dev/ttyAMA0)
    run bash -c "cd '$PROJECT_DIR' && uv run python scripts/hardware-test/self_test.py ${args[*]}" \
        || warn "Self-test reported problems; re-run it after rebooting."
}

main() {
    parse_args "$@"
    detect_platform
    install_system_packages
    install_node
    install_uv
    resolve_project_dir
    install_project_dependencies
    configure_groups
    install_udev_rules
    configure_uart
    install_service
    configure_kiosk
    run_self_test

    echo
    log "Install complete."
    if [ "$REBOOT_NEEDED" = true ]; then
        log "Reboot to apply group and UART changes: sudo reboot"
    fi
    log "After rebooting, verify the hardware with:"
    echo "    cd $PROJECT_DIR && uv run python scripts/hardware-test/self_test.py"
    log "To flash the IWR6843 firmware (optional angle radar):"
    echo "    $PROJECT_DIR/scripts/setup/flash-iwr6843.sh"
}

if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
