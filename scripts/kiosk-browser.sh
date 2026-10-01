# Sourced by start-kiosk.sh. Launches the kiosk browser (the pinned Electron
# shell, else a system Chromium) in its own process group and stops exactly
# that group on shutdown.
#
# Chromium-based browsers fork a zygote, GPU, network and renderer processes
# that outlive a signal to the launcher PID. The previous cleanup swept them
# up by pattern-matching the Electron binary path, which caught *every*
# Electron on the machine: a crash-looping openflight.service ran it every 5 s and
# killed the desktop session's kiosk each time (Chromium then died with
# "GPU process isn't usable. Goodbye."). Owning a process group makes "ours"
# exact and leaves anyone else's browser alone.
#
# Expects from the caller: PROJECT_DIR, log(), warn().
# Sets: BROWSER_PID, BROWSER_PGID, BROWSER_LAUNCHED.

launch_kiosk_browser() {
    local url="$1"
    local display_wait_s="${2:-${OPENFLIGHT_DISPLAY_WAIT_S:-60}}"
    local electron_bin="$PROJECT_DIR/ui/node_modules/.bin/electron"

    if ! wait_for_kiosk_display "$display_wait_s"; then
        return 1
    fi
    log "Launching kiosk shell (Electron)..."
    if [ -x "$electron_bin" ]; then
        _launch_kiosk_process env DISPLAY="$DISPLAY" OPENFLIGHT_URL="$url" "$electron_bin" "$PROJECT_DIR/ui"
    elif command -v chromium-browser &> /dev/null; then
        warn "Electron kiosk shell not installed (run 'npm install' in ui/); falling back to chromium-browser"
        _launch_kiosk_process env DISPLAY="$DISPLAY" chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --password-store=basic "$url"
    elif command -v chromium &> /dev/null; then
        warn "Electron kiosk shell not installed (run 'npm install' in ui/); falling back to chromium"
        _launch_kiosk_process env DISPLAY="$DISPLAY" chromium --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --password-store=basic "$url"
    else
        warn "No Electron kiosk shell and no fallback browser found. Open $url manually."
        return 1
    fi
    _confirm_kiosk_running
}

# At boot the service reaches graphical.target before the autologin desktop
# has created its X / Wayland socket, and a browser started then exits
# immediately. Wait up to $1 seconds (0 checks once) for the session and
# export what the browser needs to reach it.
wait_for_kiosk_display() {
    local wait_s="$1"
    export DISPLAY="${DISPLAY:-:0}"
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
        export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    fi
    local x11_dir="${OPENFLIGHT_X11_SOCKET_DIR:-/tmp/.X11-unix}"
    local display_number="${DISPLAY#*:}"
    display_number="${display_number%%.*}"
    local x_socket="$x11_dir/X$display_number"
    local wayland_socket="${XDG_RUNTIME_DIR:-/nonexistent}/${WAYLAND_DISPLAY:-wayland-0}"
    local waited=0
    while true; do
        if [ -S "$x_socket" ] || [ -S "$wayland_socket" ]; then
            if [ "$waited" -gt 0 ]; then
                log "Desktop display ready after ${waited}s"
            fi
            return 0
        fi
        if [ "$waited" -ge "$wait_s" ]; then
            break
        fi
        if [ "$waited" -eq 0 ]; then
            log "Waiting up to ${wait_s}s for the desktop display ($x_socket or $wayland_socket)..."
        fi
        sleep 1
        waited=$((waited + 1))
    done
    if [ "$wait_s" -eq 0 ]; then
        log "Desktop display not ready yet; skipping the early kiosk window"
        return 1
    fi
    warn "KIOSK NOT STARTED: no desktop display after ${wait_s}s (looked for $x_socket and $wayland_socket)."
    warn "Check that the Pi boots to the desktop with autologin (raspi-config: System > Boot / Auto Login)."
    return 1
}

# Report a browser that dies during start-up instead of leaving a blank screen.
_confirm_kiosk_running() {
    local _ status
    for _ in {1..8}; do
        if ! kill -0 "$BROWSER_PID" 2>/dev/null; then
            status=0
            wait "$BROWSER_PID" 2>/dev/null || status=$?
            warn "KIOSK NOT STARTED: the kiosk shell exited during start-up (exit $status). The server keeps running."
            BROWSER_PID=""
            BROWSER_PGID=""
            BROWSER_LAUNCHED=false
            return 1
        fi
        sleep 0.25
    done
    return 0
}

stop_kiosk_browser() {
    if [ -z "$BROWSER_PID" ]; then
        return 0
    fi

    if kill -0 "$BROWSER_PID" 2>/dev/null; then
        log "Closing kiosk shell..."
        _signal_kiosk_browser TERM
        # Chromium shuts its helper processes down in order after SIGTERM;
        # give it a few seconds before forcing.
        local _
        for _ in {1..20}; do
            if ! kill -0 "$BROWSER_PID" 2>/dev/null; then
                break
            fi
            sleep 0.25
        done
        if kill -0 "$BROWSER_PID" 2>/dev/null; then
            warn "Kiosk shell did not exit after SIGTERM; forcing"
            _signal_kiosk_browser KILL
        fi
    fi
    wait "$BROWSER_PID" 2>/dev/null || true

    # Any helper that outlived the main process is still in our group and in
    # nobody else's, so a final group kill is safe and leaves no orphans.
    if [ -n "$BROWSER_PGID" ]; then
        kill -KILL -- "-$BROWSER_PGID" 2>/dev/null || true
    fi

    BROWSER_PID=""
    BROWSER_PGID=""
    BROWSER_LAUNCHED=false
}

# Start "$@" in the background inside a fresh session so the whole browser
# tree shares one process group that nothing else on the machine belongs to.
_launch_kiosk_process() {
    local have_setsid=false
    if command -v setsid >/dev/null 2>&1; then
        have_setsid=true
        setsid "$@" &
    else
        warn "setsid unavailable; browser helper processes may outlive shutdown"
        "$@" &
    fi
    BROWSER_PID=$!
    BROWSER_LAUNCHED=true
    BROWSER_PGID=""
    if [ "$have_setsid" = true ]; then
        _await_kiosk_process_group
    fi
}

# Record the browser's process group once setsid has detached it. Between
# fork and setsid() the child still shares this script's group, so reading
# too early would make a later group kill take the launcher down as well.
_await_kiosk_process_group() {
    local own_pgid pgid _
    own_pgid="$(_kiosk_process_group $$)"
    for _ in {1..50}; do
        pgid="$(_kiosk_process_group "$BROWSER_PID")"
        if [ -z "$pgid" ]; then
            # Already exited (crashed on launch); nothing to own.
            return 0
        fi
        if [ "$pgid" != "$own_pgid" ]; then
            BROWSER_PGID="$pgid"
            return 0
        fi
        sleep 0.02
    done
    warn "Kiosk shell did not detach into its own process group; shutdown will signal its PID only"
}

_kiosk_process_group() {
    ps -o pgid= -p "$1" 2>/dev/null | tr -d '[:space:]'
}

_signal_kiosk_browser() {
    local sig="$1"
    if [ -n "$BROWSER_PGID" ]; then
        kill "-$sig" -- "-$BROWSER_PGID" 2>/dev/null || true
    else
        kill "-$sig" "$BROWSER_PID" 2>/dev/null || true
    fi
}
