# Sourced by start-kiosk.sh. Build a missing UI, but do not fail startup when
# Electron cannot be installed — Chromium remains the kiosk fallback.

_ensure_kiosk_ui_build() {
    # shellcheck source=require-node.sh
    source "$SCRIPT_DIR/require-node.sh"
    if ! openflight_node_meets_min; then
        openflight_node_install_hint
        show_startup_failure \
            "server" \
            "Node.js is too old to build the UI" \
            "OpenFlight needs Node.js ${OPENFLIGHT_MIN_NODE} or newer (found $(openflight_node_version 2>/dev/null || echo none)). Upgrade Node, then relaunch."
    fi
    if ! (cd "$PROJECT_DIR/ui" && npm install && npm run build); then
        show_startup_failure \
            "server" \
            "CopperStrike interface build failed" \
            "Check the terminal log or network connection, then relaunch CopperStrike."
    fi
}

# A git pull updates ui/src but leaves the old ui/dist in place, so the kiosk
# would keep serving the previous interface. Rebuild when any source is newer
# than the bundle; keep the old bundle if the rebuild cannot run or fails.
_rebuild_stale_kiosk_ui() {
    local ui_dir="$PROJECT_DIR/ui"
    local bundle="$ui_dir/dist/index.html"
    [ -f "$bundle" ] || return 0
    [ -d "$ui_dir/node_modules" ] || return 0
    if [ -z "$(find "$ui_dir/src" "$ui_dir/index.html" "$ui_dir/package.json" -newer "$bundle" -print -quit 2>/dev/null)" ]; then
        return 0
    fi
    # shellcheck source=require-node.sh
    source "$SCRIPT_DIR/require-node.sh"
    if ! openflight_node_meets_min; then
        warn "UI sources changed but Node.js is too old to rebuild; using existing UI bundle"
        return 0
    fi
    warn "UI sources changed since the last build. Rebuilding..."
    if ! (cd "$ui_dir" && npm run build); then
        warn "UI rebuild failed; using existing UI bundle"
    fi
}

_try_install_electron_shell() {
    warn "Electron kiosk shell missing. Attempting install..."
    # shellcheck source=require-node.sh
    source "$SCRIPT_DIR/require-node.sh"
    if ! openflight_node_meets_min; then
        warn "Node.js is too old to install Electron (need ${OPENFLIGHT_MIN_NODE}+, found $(openflight_node_version 2>/dev/null || echo none)); falling back to Chromium."
        return 0
    fi
    if ! (cd "$PROJECT_DIR/ui" && npm install); then
        warn "Could not install Electron; falling back to Chromium if available."
        return 0
    fi
    if [ ! -x "$PROJECT_DIR/ui/node_modules/.bin/electron" ]; then
        warn "Electron is still missing after npm install; falling back to Chromium if available."
    fi
}

ensure_kiosk_ui() {
    PROJECT_DIR="${PROJECT_DIR//$'\r'/}"
    SCRIPT_DIR="${SCRIPT_DIR//$'\r'/}"
    local dist_dir="$PROJECT_DIR/ui/dist"
    local electron_bin="$PROJECT_DIR/ui/node_modules/.bin/electron"

    if [ ! -d "$dist_dir" ]; then
        warn "UI not built. Building now..."
        _ensure_kiosk_ui_build
        return
    fi

    _rebuild_stale_kiosk_ui

    if [ -x "$electron_bin" ]; then
        return 0
    fi

    if [ ! -d "$PROJECT_DIR/ui/node_modules" ] && [ -f "$dist_dir/index.html" ]; then
        warn "UI dependencies not installed; using existing UI bundle"
        return 0
    fi

    _try_install_electron_shell
}
