#!/usr/bin/env bash
#
# Flash the validated OpenFlight firmware onto an IWR6843LEVM from the Pi.
#
# Picks the newest image in firmware/releases/, finds the CP2105 Enhanced UART,
# stops the OpenFlight service so nothing else holds the port, runs the guided
# flashing tool (it prompts for the board's switch settings and RESET), and
# restarts the service afterwards.
#
# Usage:
#   scripts/setup/flash-iwr6843.sh                 # flash the newest release image
#   scripts/setup/flash-iwr6843.sh --probe         # bootloader handshake only
#   scripts/setup/flash-iwr6843.sh --port /dev/ttyUSB0 --image path/to/image.bin
#   scripts/setup/flash-iwr6843.sh --dry-run       # show what would run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT=""
IMAGE=""
PROBE=false
DRY_RUN=false

log() { printf '[OpenFlight] %s\n' "$1"; }
die() { printf '[OpenFlight] ERROR: %s\n' "$1" >&2; exit 1; }

latest_release_image() {
    local releases="$1"
    find "$releases" -maxdepth 1 -name '*.bin' -type f 2>/dev/null | sort | tail -n 1
}

# The CP2105's Enhanced interface (if00) is the one the ROM bootloader answers on.
detect_cp2105_port() {
    local by_id_dir="${1:-/dev/serial/by-id}" candidate
    if [ -e /dev/iwr6843 ]; then
        echo /dev/iwr6843
        return 0
    fi
    for candidate in "$by_id_dir"/*CP2105*-if00*; do
        [ -e "$candidate" ] || continue
        echo "$candidate"
        return 0
    done
    return 1
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --port) PORT="${2:?--port needs a value}"; shift 2 ;;
            --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
            --probe) PROBE=true; shift ;;
            --dry-run) DRY_RUN=true; shift ;;
            -h|--help) awk 'NR>2 && !/^#/{exit} NR>2{sub(/^# ?/,""); print}' "$0"; exit 0 ;;
            *) die "Unknown option: $1" ;;
        esac
    done
}

main() {
    parse_args "$@"
    if [ -z "$PORT" ]; then
        PORT="$(detect_cp2105_port)" || die "No IWR6843 CP2105 port found. Connect the EVM's USB, or pass --port."
    fi
    local cmd=(uv run python firmware/flash_iwr6843.py --port "$PORT")
    if [ "$PROBE" = true ]; then
        cmd+=(--probe)
    else
        [ -n "$IMAGE" ] || IMAGE="$(latest_release_image "$PROJECT_DIR/firmware/releases")"
        [ -n "$IMAGE" ] && [ -f "$IMAGE" ] || die "No firmware image found; pass --image."
        cmd+=("$IMAGE")
    fi

    local restart=false
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet openflight 2>/dev/null; then
        restart=true
    fi

    if [ "$DRY_RUN" = true ]; then
        [ "$restart" = true ] && log "[dry-run] sudo systemctl stop openflight"
        printf '[dry-run] (cd %q &&' "$PROJECT_DIR"
        printf ' %q' "${cmd[@]}"
        printf ')\n'
        [ "$restart" = true ] && log "[dry-run] sudo systemctl start openflight"
        return 0
    fi

    if [ "$restart" = true ]; then
        log "Stopping OpenFlight so the TI port is free..."
        sudo systemctl stop openflight
        trap 'log "Restarting OpenFlight..."; sudo systemctl start openflight' EXIT
    fi
    (cd "$PROJECT_DIR" && "${cmd[@]}")
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
