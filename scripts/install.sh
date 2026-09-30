#!/usr/bin/env bash
#
# OpenFlight installer for a Raspberry Pi 5 running Raspberry Pi OS Bookworm.
#
# Installs system packages, uv and the Python environment, Node.js 22 and the
# UI build; enables the GPIO UART with the serial console off; adds you to the
# hardware groups; installs udev rules that name the radars
# /dev/openflight-ops243, /dev/openflight-iwr-cli and /dev/openflight-iwr-data;
# installs the openflight systemd service and, unless --no-kiosk, desktop
# autologin for the touchscreen kiosk. Safe to re-run: every step checks
# before it changes anything. Output is also logged to ~/openflight-install.log.
#
# From a clone:
#   ./scripts/install.sh [options]
# From a fresh Pi (clones into --dir, then runs the clone's installer):
#   curl -fsSL https://raw.githubusercontent.com/DYS3D/openflight/improvements/scripts/install.sh | bash -s -- --yes --repo https://github.com/DYS3D/openflight.git --branch improvements
#
# Options:
#   --yes                Do not ask for confirmation
#   --with-iwr6843       Also run the IWR6843 angle radar (adds --iwr6843 to the service)
#   --with-camera        Install camera packages (adds --camera-capture to the service)
#   --no-kiosk           Leave desktop autologin and screen blanking alone
#   --server-args "..."  Extra server flags for the service, e.g. "--radar-port /dev/ttyAMA0"
#   --dry-run            Print every step and change without making any
#   --force              Skip the Raspberry Pi 5 + Bookworm check
#   --dir DIR            Checkout for piped installs (default ~/openflight)
#   --repo URL           Repository for piped installs (default upstream)
#   --branch NAME        Branch for piped installs (default main)
#   -h, --help           Show this help

set -euo pipefail

DEFAULT_REPO="https://github.com/open-flight/openflight.git"

# Piped installs have no checkout yet: clone one and run its installer.
bootstrap_from_clone() {
    local dir="$HOME/openflight" repo="$DEFAULT_REPO" branch="main" dry_run=false
    local args=("$@")
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dir) dir="${2:?--dir needs a value}"; shift 2 ;;
            --repo) repo="${2:?--repo needs a value}"; shift 2 ;;
            --branch) branch="${2:?--branch needs a value}"; shift 2 ;;
            --dry-run) dry_run=true; shift ;;
            *) shift ;;
        esac
    done
    if [ "$dry_run" = true ]; then
        printf '[dry-run] git clone --branch %q %q %q, then run its scripts/install.sh\n' \
            "$branch" "$repo" "$dir"
        return 0
    fi
    if ! command -v git >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y git
    fi
    if [ -d "$dir/.git" ]; then
        git -C "$dir" fetch origin "$branch"
        git -C "$dir" checkout "$branch"
        git -C "$dir" pull --ff-only origin "$branch"
    else
        git clone --branch "$branch" "$repo" "$dir"
    fi
    exec bash "$dir/scripts/install.sh" "${args[@]}"
}

SCRIPT_DIR=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -z "$SCRIPT_DIR" ] || [ ! -f "$SCRIPT_DIR/setup/lib.sh" ]; then
    bootstrap_from_clone "$@"
    exit 0
fi

PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=setup/lib.sh
source "$SCRIPT_DIR/setup/lib.sh"
# shellcheck source=require-node.sh
source "$SCRIPT_DIR/require-node.sh"

UDEV_RULES_DEST="/etc/udev/rules.d/99-openflight.rules"
ENV_FILE="/etc/default/openflight"
SERVICE_DEST="/etc/systemd/system/openflight.service"
UART_MARKER="# OpenFlight UART"
HARDWARE_GROUPS=(dialout gpio i2c video)
# Overridable so tests can point the boot-file edits at a temp directory.
BOOT_DIR="${OPENFLIGHT_BOOT_DIR:-/boot/firmware}"
LOG_FILE="${OPENFLIGHT_INSTALL_LOG:-$HOME/openflight-install.log}"
TOTAL_STEPS=10

ASSUME_YES=false
WITH_IWR6843=false
WITH_CAMERA=false
CONFIGURE_KIOSK=true
EXTRA_SERVER_ARGS=""
DRY_RUN=false
FORCE=false
REBOOT_NEEDED=false
STEP=0

usage() {
    awk 'NR>2 && !/^#/{exit} NR>2{sub(/^# ?/,""); print}' "$0"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --yes|-y) ASSUME_YES=true; shift ;;
            --with-iwr6843) WITH_IWR6843=true; shift ;;
            --with-camera) WITH_CAMERA=true; shift ;;
            --no-kiosk) CONFIGURE_KIOSK=false; shift ;;
            --server-args) EXTRA_SERVER_ARGS="${2?--server-args needs a value}"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --force) FORCE=true; shift ;;
            --dir|--repo|--branch) shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) die "Unknown option: $1 (try --help)" ;;
        esac
    done
    # The value lands inside double quotes in a systemd EnvironmentFile.
    case "$EXTRA_SERVER_ARGS" in
        *[\"\\\$\`]*) die "--server-args must not contain quotes, \\, \$ or backticks" ;;
    esac
}

step() {
    STEP=$((STEP + 1))
    printf '\n%b==> [%d/%d] %s%b\n' "$GREEN" "$STEP" "$TOTAL_STEPS" "$1" "$NC"
}

skip_step() {
    step "$1"
    log "Skipped ($2)"
}

# Pi 5 + Bookworm is the only tested platform. Prints each mismatch.
platform_problems() {
    local model_file="${1:-/proc/device-tree/model}" os_release="${2:-/etc/os-release}"
    local model codename
    model="$(tr -d '\0' 2>/dev/null <"$model_file" || true)"
    codename="$(sed -n 's/^VERSION_CODENAME=//p' "$os_release" 2>/dev/null | tr -d '"')"
    [[ "$model" == *"Raspberry Pi 5"* ]] || echo "board is '${model:-unknown}', not a Raspberry Pi 5"
    [ "$codename" = bookworm ] || echo "OS is '${codename:-unknown}', not Raspberry Pi OS Bookworm"
}

check_platform() {
    [ "$(id -u)" -ne 0 ] || die "Run as your normal user (not root); the installer uses sudo when needed."
    local problems
    problems="$(platform_problems)"
    if [ -z "$problems" ]; then
        log "Raspberry Pi 5 on Bookworm"
    elif [ "$FORCE" = true ]; then
        while IFS= read -r line; do warn "$line (continuing: --force)"; done <<<"$problems"
    else
        while IFS= read -r line; do error "$line"; done <<<"$problems"
        die "OpenFlight is tested on a Raspberry Pi 5 with Bookworm; pass --force to install anyway."
    fi
}

confirm_install() {
    if [ "$ASSUME_YES" = true ] || [ "$DRY_RUN" = true ]; then
        return 0
    fi
    local answer
    if ! { printf '[OpenFlight] Install OpenFlight into %s? [y/N] ' "$PROJECT_DIR" >/dev/tty \
        && read -r answer </dev/tty; } 2>/dev/null; then
        die "No terminal to confirm on; re-run with --yes."
    fi
    [[ "$answer" =~ ^[Yy]$ ]] || die "Cancelled."
}

# Write stdin to a root-owned file when its content differs. Returns 1 when
# the file was already up to date.
install_root_file() {
    local dest="$1" content
    content="$(cat)"
    if [ -f "$dest" ] && [ "$(cat "$dest")" = "$content" ]; then
        log "$dest is up to date"
        return 1
    fi
    if [ "$DRY_RUN" = true ]; then
        printf '[dry-run] write %s:\n%s\n' "$dest" "$content"
        return 0
    fi
    printf '%s\n' "$content" | sudo tee "$dest" >/dev/null
    log "Wrote $dest"
}

backup_path() {
    printf '%s.openflight-%s.bak' "$1" "$(date +%Y%m%d-%H%M%S)"
}

install_system_packages() {
    step "System packages"
    run sudo apt-get update
    local chromium=chromium packages
    if ! apt-cache show chromium >/dev/null 2>&1; then chromium="chromium-browser"; fi
    packages=(git curl ca-certificates python3 python3-venv python3-dev build-essential
        swig liblgpio-dev ffmpeg i2c-tools avahi-daemon "$chromium")
    if [ "$WITH_CAMERA" = true ]; then
        packages+=(python3-picamera2 rpicam-apps)
    fi
    run sudo apt-get install -y "${packages[@]}"
}

install_uv_step() {
    step "uv (Python package manager)"
    of_install_uv
}

install_python_env() {
    step "Python environment (uv sync)"
    if [ "$WITH_CAMERA" = true ]; then
        # Picamera2 comes from apt, so the venv must see system site-packages
        # (same as start-kiosk.sh --camera-capture).
        export UV_PYTHON=/usr/bin/python3
        if [ ! -x "$PROJECT_DIR/.venv/bin/python" ] \
            || ! "$PROJECT_DIR/.venv/bin/python" -c 'import picamera2' >/dev/null 2>&1; then
            run uv --directory "$PROJECT_DIR" venv --clear --system-site-packages --python /usr/bin/python3
        fi
        run uv --directory "$PROJECT_DIR" sync --extra camera
    else
        run uv --directory "$PROJECT_DIR" sync
    fi
}

install_node_and_ui() {
    step "Node.js ${OPENFLIGHT_MIN_NODE%%.*} and UI build"
    if openflight_node_meets_min; then
        log "Node.js $(openflight_node_version) found"
    else
        log "Installing Node.js 22.x from NodeSource..."
        if [ "$DRY_RUN" = true ]; then
            printf '[dry-run] curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -\n'
        else
            curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
        fi
        run sudo apt-get install -y nodejs
    fi
    run npm --prefix "$PROJECT_DIR/ui" ci
    run npm --prefix "$PROJECT_DIR/ui" run build
}

boot_file() {
    local name="$1"
    if [ -f "$BOOT_DIR/$name" ] || [ ! -f "/boot/$name" ]; then
        echo "$BOOT_DIR/$name"
    else
        echo "/boot/$name"
    fi
}

# Raspberry Pi generation from the device-tree model (0 when unknown).
pi_generation() {
    local model_file="${1:-/proc/device-tree/model}" model
    model="$(tr -d '\0' 2>/dev/null <"$model_file" || true)"
    if [[ "$model" =~ Raspberry\ Pi\ ([0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo 0
    fi
}

# Idempotently put the 40-pin header UART on /dev/ttyAMA0. A Pi 5 needs UART0
# switched on; a Pi 3/4 must move Bluetooth off the PL011 (disable-bt).
update_uart_boot_config() {
    local config="$1" generation="${2:-5}" lines=()
    if grep -qF "$UART_MARKER" "$config" 2>/dev/null; then
        return 0
    fi
    grep -qE '^enable_uart=1' "$config" 2>/dev/null || lines+=("enable_uart=1")
    if [ "$generation" -ge 5 ]; then
        grep -qE '^dtparam=uart0=on' "$config" 2>/dev/null || lines+=("dtparam=uart0=on")
    else
        grep -qE '^dtoverlay=disable-bt' "$config" 2>/dev/null || lines+=("dtoverlay=disable-bt")
    fi
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

# Apply an edit function to a boot file through a temp copy, keeping a
# timestamped backup when (and only when) the file changes.
edit_boot_file() {
    local file="$1" tmp backup
    shift
    if [ ! -f "$file" ]; then
        warn "$file not found; skipping"
        return 0
    fi
    tmp="$(mktemp)"
    cp "$file" "$tmp"
    "$@" "$tmp"
    if cmp -s "$tmp" "$file"; then
        log "$file already configured"
        rm -f "$tmp"
        return 0
    fi
    backup="$(backup_path "$file")"
    if [ "$DRY_RUN" = true ]; then
        printf '[dry-run] update %s (backup %s):\n' "$file" "$backup"
        diff -u "$file" "$tmp" || true
        rm -f "$tmp"
        return 0
    fi
    sudo cp "$file" "$backup"
    sudo cp "$tmp" "$file"
    rm -f "$tmp"
    log "Updated $file (backup: $backup)"
    REBOOT_NEEDED=true
}

configure_uart() {
    step "GPIO UART on, serial console off, I2C on"
    local generation
    generation="$(pi_generation)"
    [ "$generation" -ne 0 ] || generation=5
    if command -v raspi-config >/dev/null 2>&1; then
        run sudo raspi-config nonint do_serial_hw 0
        run sudo raspi-config nonint do_serial_cons 1
        run sudo raspi-config nonint do_i2c 0
    else
        warn "raspi-config not found; editing the kernel command line directly."
        edit_boot_file "$(boot_file cmdline.txt)" strip_serial_console
    fi
    edit_boot_file "$(boot_file config.txt)" update_uart_boot_config_for "$generation"
    run sudo systemctl disable --now serial-getty@ttyAMA0.service
}

# edit_boot_file passes the file last; update_uart_boot_config wants it first.
update_uart_boot_config_for() {
    update_uart_boot_config "$2" "$1"
}

# Hardware groups that exist on this system but the user is not yet in.
missing_groups() {
    local current group
    current=" $(id -nG "$1") "
    for group in "${HARDWARE_GROUPS[@]}"; do
        getent group "$group" >/dev/null 2>&1 || continue
        [[ "$current" == *" $group "* ]] || printf '%s\n' "$group"
    done
}

configure_groups() {
    step "Hardware groups (${HARDWARE_GROUPS[*]})"
    local missing
    missing="$(missing_groups "$USER" | paste -sd, -)"
    if [ -z "$missing" ]; then
        log "$USER is already in every hardware group"
        return 0
    fi
    run sudo usermod -aG "$missing" "$USER"
    REBOOT_NEEDED=true
}

install_udev_rules() {
    step "udev rules (/dev/openflight-*)"
    if install_root_file "$UDEV_RULES_DEST" <"$SCRIPT_DIR/setup/99-openflight.rules"; then
        run sudo udevadm control --reload-rules
        run sudo udevadm trigger --subsystem-match=tty
    fi
}

server_args() {
    local args=()
    if [ -n "$EXTRA_SERVER_ARGS" ]; then args+=("$EXTRA_SERVER_ARGS"); fi
    if [ "$WITH_IWR6843" = true ]; then args+=(--iwr6843); fi
    if [ "$WITH_CAMERA" = true ]; then args+=(--camera-capture); fi
    printf '%s' "${args[*]:-}"
}

render_env_file() {
    printf '# Written by scripts/install.sh; read by openflight.service.\n'
    printf 'OPENFLIGHT_ARGS="%s"\n' "$(server_args)"
}

install_env_file() {
    # A re-run without server flags keeps any hand edits to the env file.
    if [ -f "$ENV_FILE" ] && [ -z "$(server_args)" ]; then
        log "Keeping $ENV_FILE"
        return 0
    fi
    if [ -f "$ENV_FILE" ] && [ "$(cat "$ENV_FILE")" != "$(render_env_file)" ]; then
        run sudo cp "$ENV_FILE" "$(backup_path "$ENV_FILE")"
    fi
    render_env_file | install_root_file "$ENV_FILE" || true
}

install_service() {
    step "openflight systemd service"
    if [ "$WITH_IWR6843" = true ] && [[ " $EXTRA_SERVER_ARGS " != *" --radar-port "* ]]; then
        # USB cannot power both radars, so an IWR6843 build normally moves the
        # OPS243 to the GPIO UART (docs/build/ops243-uart.md).
        warn "With the IWR6843 the OPS243 is usually on the GPIO UART; if so, add --server-args \"--radar-port /dev/ttyAMA0\"."
    fi
    install_env_file
    of_render_unit "$SCRIPT_DIR/setup/openflight.service" | install_root_file "$SERVICE_DEST" || true
    run sudo systemctl daemon-reload
    run sudo systemctl enable openflight.service
}

configure_kiosk() {
    if [ "$CONFIGURE_KIOSK" = false ]; then
        skip_step "Kiosk autostart" "--no-kiosk"
        return 0
    fi
    step "Kiosk autostart (desktop autologin, no screen blanking)"
    if ! command -v raspi-config >/dev/null 2>&1; then
        warn "raspi-config not found; enable desktop autologin manually for kiosk mode."
        return 0
    fi
    run sudo raspi-config nonint do_boot_behaviour B4
    run sudo raspi-config nonint do_blanking 1
}

software_check() {
    step "Software self-check"
    run uv --directory "$PROJECT_DIR" run python scripts/hardware-test/self_test.py --software-only \
        || warn "Self-check reported problems; run scripts/openflight-doctor.sh after rebooting."
}

main() {
    parse_args "$@"
    if [ "$DRY_RUN" = false ]; then
        exec > >(tee -a "$LOG_FILE") 2>&1
        log "Logging to $LOG_FILE ($(date))"
    else
        log "Dry run: nothing will be changed."
    fi
    check_platform
    confirm_install

    install_system_packages
    install_uv_step
    install_python_env
    install_node_and_ui
    configure_uart
    configure_groups
    install_udev_rules
    install_service
    configure_kiosk
    software_check

    echo
    log "Install complete."
    if [ "$REBOOT_NEEDED" = true ]; then
        log "Reboot to apply group and UART changes: sudo reboot"
    fi
    log "After rebooting, check the hardware with: $PROJECT_DIR/scripts/openflight-doctor.sh"
    if [ "$WITH_IWR6843" = true ]; then
        log "Flash the IWR6843 firmware (once per board): $PROJECT_DIR/scripts/setup/flash-iwr6843.sh"
    fi
}

if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
