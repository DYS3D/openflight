#!/usr/bin/env bash
#
# OpenFlight doctor: a PASS/FAIL checklist for an installed Pi. Exits non-zero
# when any check fails.
#
# Checks serial permissions, udev rules and /dev/openflight-* names, the GPIO
# UART configuration, that the OPS243 is detected and answers a query, that
# the IWR6843 answers the OpenFlight CLI (required when the service runs with
# --iwr6843), the openflight service, and free disk space. The checks live in
# scripts/hardware-test/self_test.py --doctor.
#
# A running openflight service is stopped for the radar checks (they cannot
# share a serial port with it) and restarted afterwards.
#
# Usage:
#   scripts/openflight-doctor.sh                    # full checklist
#   scripts/openflight-doctor.sh --software-only    # leave the radars and service alone
#   scripts/openflight-doctor.sh --with-iwr6843     # require the IWR6843
#   scripts/openflight-doctor.sh --ops-port /dev/ttyAMA0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=setup/lib.sh
source "$SCRIPT_DIR/setup/lib.sh"

main() {
    local args=(--doctor --no-interactive) software_only=false
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --software-only) software_only=true; args+=("$1"); shift ;;
            --with-iwr6843) args+=(--expect-iwr6843); shift ;;
            --ops-port) args+=("$1" "${2:?--ops-port needs a value}"); shift 2 ;;
            -h|--help) awk 'NR>2 && !/^#/{exit} NR>2{sub(/^# ?/,""); print}' "$0"; exit 0 ;;
            *) die "Unknown option: $1 (try --help)" ;;
        esac
    done

    if [ "$software_only" = false ] && of_pause_service; then
        args+=(--service-was-active)
    fi
    of_ensure_uv_path
    command -v uv >/dev/null 2>&1 || die "uv not found; run scripts/install.sh first."
    uv --directory "$PROJECT_DIR" run python scripts/hardware-test/self_test.py "${args[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
