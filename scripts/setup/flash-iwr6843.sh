#!/usr/bin/env bash
#
# Flash the validated OpenFlight firmware onto an IWR6843LEVM from the Pi.
#
# Picks the newest image in firmware/releases/, uses /dev/openflight-iwr-cli
# (the CP2105 Enhanced UART, named by scripts/install.sh's udev rules) or finds
# that interface under /dev/serial/by-id, stops the OpenFlight service so
# nothing else holds the port, runs firmware/flash_iwr6843.py (it prompts for
# the board's switch settings and RESET), and restarts the service afterwards.
#
# Usage:
#   scripts/setup/flash-iwr6843.sh                 # flash the newest release image
#   scripts/setup/flash-iwr6843.sh --probe         # bootloader handshake only
#   scripts/setup/flash-iwr6843.sh --port /dev/ttyUSB0 --image path/to/image.bin
#   scripts/setup/flash-iwr6843.sh --dry-run       # show what would run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

STABLE_CLI_PORT="/dev/openflight-iwr-cli"
PORT=""
IMAGE=""
PROBE=false
DRY_RUN=false

latest_release_image() {
    local releases="$1"
    find "$releases" -maxdepth 1 -name '*.bin' -type f 2>/dev/null | sort | tail -n 1
}

# The CP2105's Enhanced interface (if00) is the one the ROM bootloader answers on.
detect_cp2105_port() {
    local by_id_dir="${1:-/dev/serial/by-id}" stable="${2:-$STABLE_CLI_PORT}" candidate
    if [ -e "$stable" ]; then
        echo "$stable"
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
    local cmd=(uv --directory "$PROJECT_DIR" run python firmware/flash_iwr6843.py --port "$PORT")
    if [ "$PROBE" = true ]; then
        cmd+=(--probe)
    else
        [ -n "$IMAGE" ] || IMAGE="$(latest_release_image "$PROJECT_DIR/firmware/releases")"
        [ -n "$IMAGE" ] && [ -f "$IMAGE" ] || die "No firmware image found; pass --image."
        cmd+=("$IMAGE")
    fi

    if [ "$DRY_RUN" = true ]; then
        if systemctl is-active --quiet openflight 2>/dev/null; then
            run sudo systemctl stop openflight
        fi
        run "${cmd[@]}"
        return 0
    fi
    of_ensure_uv_path
    of_pause_service || true
    "${cmd[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
