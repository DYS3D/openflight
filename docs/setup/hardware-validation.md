---
icon: lucide/clipboard-check
---

# Hardware Validation Checklist

Everything on this page was written and unit-tested without a device. Each
entry is a behaviour that ships **off by default** (the server runs exactly as
before) and a way to prove it works on your Pi before you turn it on. Work
through them one at a time, with the default configuration as your baseline.

Flags go in `/etc/default/openflight` (`OPENFLIGHT_ARGS="..."`, applied on
`sudo systemctl restart openflight`) or on the `scripts/start-kiosk.sh` command
line. Before starting, run `scripts/openflight-doctor.sh` and save a baseline
session of 10–20 shots with the defaults so every comparison below has
something to compare against.

Useful tools:

- `uv run python scripts/analysis/compare_sessions.py <session.jsonl> <trackman.csv>`
  prints per-club bias and MAE for ball speed, club speed, launch angle, spin
  and carry against a TrackMan/GC export.
- `uv run python scripts/analysis/replay_captures.py <session.jsonl>` replays
  the recorded I/Q captures through the processor offline.
- `journalctl -u openflight -f` shows the server log, including every
  `[TIMING]`, `[MONITOR]`, `[RETENTION]` and `[SERVER]` line referenced below.

## 1. OPS243 timing

The clock-sync and re-arm timing used to be constants. They are now
`--clock-sync-samples` (36), `--clock-sync-max-duration` (1.25),
`--rearm-drain-poll` (0.2), `--rearm-after-pa` (0.1), `--rearm-after-split`
(0.1) and `--rearm-after-activate` (0.15). The defaults are the old constants.

- [ ] **Baseline.** With no timing flags, hit 10 shots. Every shot must
      re-arm (the next sound trigger fires) and the session log's
      `ops_clock_sync` block must report the usual number of reads.
- [ ] **`--fast-clock-sync`.** Requires firmware that reports a fractional
      clock (`C?` returns a decimal). Enable it, hit 10 shots, and confirm:
      the log shows `fast_clock_sync_early_exit: true` on most shots; the
      `trigger_timestamp` offset agrees with the baseline session to within a
      few ms (compare `ops_clock_sync.best_offset_s` between sessions); with an
      IWR6843, launch-angle correlation still succeeds. If early exits never
      happen, the read latency on your link is above 3 ms and the flag does
      nothing — leave it off.
- [ ] **`--rearm-after-handoff`.** Enable it, hit 10 shots including two in
      quick succession (about 3 s apart). Confirm every shot re-arms, no shot
      is missed, the UI shows the shot sooner than the baseline, and the OPS
      accepts `PA` after sitting idle through processing. Watch for a
      `[TIMING]` warning: any truncated dump switches the radar back to the
      slow defaults for the rest of the session.
- [ ] **Shorter re-arm sleeps** (`--rearm-after-pa`, `--rearm-after-split`,
      `--rearm-after-activate`, `--rearm-drain-poll`). Lower one value at a
      time. After each change confirm the next dump's pre/post split still
      matches `S#n` and HOST_INT triggering still works. On the GPIO UART at
      low baud, a short `--rearm-drain-poll` can declare the drain finished
      between bytes of a straggling dump; if a capture fails to parse, restore
      the default.
- [ ] **`--clock-sync-samples` / `--clock-sync-max-duration`.** Fewer samples
      must still pass the sync quality check (no `clock sync quality` warning
      in the log). The duration cap only matters on integer-clock firmware.
- [ ] **Safe-mode fallback** (automatic). The only way to trigger it is a real
      truncated dump (a missing `"]}"` terminator, e.g. UART dropped bytes).
      When it happens, expect exactly one `[TIMING] ... reverting radar timing
      to the slow defaults` warning and normal captures afterwards.

## 2. Radar auto-reconnect (`--radar-auto-reconnect`)

- [ ] **Baseline.** Pull the OPS243 USB cable mid-session with the flag off.
      The old behaviour is a logged error once a second until you restart.
- [ ] **OPS243 reconnect.** Enable the flag, pull the USB cable, wait 10 s,
      plug it back in. Expect one `Radar serial link lost` error, the status
      menu showing the radar as reconnecting, then one `Radar reconnected`
      info line, and the next sound trigger producing a shot. If the radar
      comes back but never triggers, the rolling-buffer setup did not survive
      the re-plug — report it and keep the flag off.
- [ ] **Stable name.** With the installer's udev rules, `ls -l
      /dev/openflight-ops243` must point at the re-enumerated tty after the
      re-plug; the log should show the reconnect used that name.
- [ ] **IWR6843 reconnect.** Same test on the TI USB cable. The board must
      answer `send_config` after re-enumeration without pressing RESET; if the
      log keeps saying "press RESET" every 30 s, a re-plug alone does not
      recover this board.
- [ ] **UART wiring.** If the OPS243 is on `/dev/ttyAMA0`, simulate a fault by
      briefly disconnecting the RX wire; confirm baud renegotiation on reconnect.
- [ ] **Timeouts don't reconnect.** Cover the radar so nothing triggers for
      a minute; there must be no reconnect activity in the log.

## 3. GSPro payload

- [ ] **Baseline.** Send 10 shots to GSPro with the default `config/sim.json`.
      Every shot must be accepted (200) and show the same values as the
      kiosk.
- [ ] **`omit_unsupported_fields: true`** (per connector in `config/sim.json`).
      The OpenConnect V1 page (<https://gsprogolf.com/GSProConnectV1.html>)
      marks `DeviceID`, `ShotNumber`, `APIversion`, `BallData.Speed`,
      `SpinAxis`, `TotalSpin`, `HLA`, `VLA` and both `ShotDataOptions` flags
      as required, `CarryDistance` optional, and says nothing about the
      `ClubData` keys. With the option on, confirm GSPro accepts shots whose
      `ClubData` has only `Speed`/`Path` (and `{}` when neither is measured,
      with `ContainsClubData: false`). If GSPro returns 501 for any of these,
      turn it off and note which shape failed.
- [ ] **Club path only.** With an IWR6843 and no club speed, confirm GSPro
      honours `ContainsClubData: true` with only `Path` present.
- [ ] **Units.** `units` must be `Yards`; the server now refuses `Meters` at
      startup because the spec never defines it. Confirm the startup error
      message is clear if you try it.
- [ ] **Error badge clears.** Force a 5xx (e.g. stop GSPro's connector and
      send a shot, then restart it). After the next accepted shot the red
      badge must clear on its own.

## 4. Access control (`--auth-required`)

- [ ] **Default cross-site guard** (no flags). The kiosk, a phone opening
      `http://<pi>:8080`, and the TV display all work as before. From another
      site's page (e.g. a test page served on a laptop),
      `fetch('http://<pi>:8080/api/shutdown', {method: 'POST', mode: 'no-cors'})`
      must not shut the Pi down (the journal shows a 403). `--allow-cross-origin`
      restores the old behaviour.

- [ ] **Baseline.** With the flag off, a phone on the LAN can open
      `http://<pi>:8080/` and GSPro can connect, exactly as before.
- [ ] **Token.** The installer created `~/.config/openflight/token` (mode
      0600; it is deliberately not echoed into the install log). Read it with
      `cat ~/.config/openflight/token` or
      `uv run python -m openflight.access print-token`. Enable the flag and
      restart.
- [ ] **Kiosk exempt.** The touchscreen UI on the Pi keeps working with no
      prompt (loopback needs no token).
- [ ] **Phone/TV.** `http://<pi>:8080/?token=<token>` works from a phone
      (the UI remembers the token in that browser). Without it the page and
      its assets still load but the socket is rejected and every `/api`
      call returns 401, so the display stays empty. Bookmark the token URL
      on each device.
- [ ] **GSPro / OpenGolfSim.** Simulator connections are outbound from the Pi
      and are not affected. Confirm shots still arrive.
- [ ] **Origins.** Opening the UI by the Pi's hostname, `<hostname>.local`
      and its IP all work. Check the IP from a phone in particular
      (`http://192.168.x.y:8080`): Pi OS maps the hostname to 127.0.1.1, so
      the LAN address is matched against the request's own Host and the
      Pi's current routable address rather than DNS. Any other name needs `--allowed-origin`; a browser
      tab on another site cannot call the API (403). If you put a reverse
      proxy on the Pi, every client arrives from loopback and is exempt —
      don't combine the two.

## 5. Web server limits

Werkzeug stays (`allow_unsafe_werkzeug=True`): Flask-SocketIO refuses to
start it without the override when stdin is not a TTY, which is how systemd
runs the service, and the threading async mode has no other in-process
server. gunicorn/gevent were ruled out because the radar threads must live in
the request process. A test pins this behaviour.

- [ ] **`--request-rate-limit 20`** (per non-loopback IP, 2 s burst). The
      kiosk (loopback) is exempt. Refresh the display page rapidly from a
      phone; a 429 appears only under abuse, never during normal use. Watch a
      full session with the phone display open for spurious 429s in the log.
- [ ] **`--max-request-bytes 65536`.** Normal UI and API use must be
      unaffected; only oversized POST bodies get 413.

## 6. Ball-speed cosine correction on OPS-only builds

`--speed-correction-without-angle-radar` applies the cosine correction using
the table-estimated launch angle and the `--kld7-ball-distance` /
`--kld7-radar-height-inches` geometry.

- [ ] **Decide from data.** Record 20+ shots per club against a TrackMan or
      GC with the flag off, then the same with it on. Run
      `compare_sessions.py` on each session. Turn the flag on only if the
      ball-speed bias moves toward zero for every club without widening the
      spread. The committed comparison shows the OPS reading about 2.7 mph
      low; the correction is worth roughly that only when the geometry values
      match your mount.

## 7. Performance switches from the previous pass

- [ ] **`--iwr6843-estimator-process`.** Enable, hit 10 shots. Launch angle
      and club path must match a baseline session on the same captures
      (replay both with `scripts/iwr6843/replay.py`), the first shot may take
      a few seconds longer while the worker starts, and `sudo systemctl stop
      openflight` must exit cleanly with no orphaned `python` process.
- [ ] **`--iwr6843-fast-angle-search`.** Replay your recorded IWR dumps with
      `scripts/iwr6843/replay.py` (default 0.5° sweep) and with
      `--grid-step-deg 0` (coarse-to-fine); angles must agree within 0.25°
      with identical status. `tests/test_iwr6843_real_captures.py` does this automatically
      when `session_logs/session_20260725_140533_range.jsonl` and
      `session_logs/iwr/` are present.
- [ ] **`--camera-frames-in-memory`** (with `--camera-capture`). Shot display
      latency should drop; replay must still work once the background archive
      finishes; `--no-camera-archive-frames` removes replay entirely.
- [ ] **Session log ordering.** `shot_detected` now precedes
      `rolling_buffer_capture` in the JSONL (the `trigger_event` still comes
      first); confirm your own analysis scripts key on type and shot number,
      not position. Session lines are written by a background thread; a
      `systemctl stop` now flushes them via the SIGTERM handler.
- [ ] **`--radar-log`.** The `radar_raw_*.log` file is now only written with
      this flag. Confirm it appears when set and not otherwise.
- [ ] **Log retention** (`--log-retention-days 90 --log-max-mb 8192`, on by
      default). On first start with an old session directory, read the
      `[RETENTION]` lines: every file is listed with its reason before it is
      deleted; sessions and TI dumps still queued for cloud upload are kept.
      Set both to 0 to disable.

## 8. Accuracy models (off by default)

- [ ] **`--spin-axis-model dplane`.** Needs a club path (K-LD7 or IWR6843
      path) and a vertical launch angle. Hit deliberate fades and draws next
      to a TrackMan/GC: fade must read positive, and the size should sit
      within a few degrees of the reference where `legacy` reads roughly a
      third to a fifth of it. The `[SERVER] Spin axis (D-plane)` line lists
      every input. Watch GSPro: curved shots should now curve.
- [ ] **`--show-normalized-carry`.** Run with your real `--altitude-ft`,
      `--temperature-f`, `--humidity`. `carry_normalized_yards` should be
      shorter than carry at altitude and 1–2 yd longer than carry at the
      default 59 °F; compare with TrackMan's normalized carry.
- [ ] **`--ball-marker dot|rct`.** Put a ~6 mm foil dot on the ball (or use
      RCT balls). Hit ~10 shots each with driver, 7-iron and wedge next to a
      reference. In the session JSONL check `spin_method` contains
      `marker_dot`, and no trusted spin (≥0.7) lands at 2× or 0.5× the
      reference. Repeat with an unmarked ball and the same flag: nothing may
      become trusted. Do not combine with `--calculated-spin`.
- [ ] **`--spin-octave-check`.** Same shots; look for `+octave_halved` /
      `+octave_doubled` tags. Offline it changed 1 of 41 committed captures.
- [ ] **`--inclinometer-roll-compensation`.** Shim the right side of the
      enclosure (viewed from behind): the startup log `roll` must go
      positive and match a phone level. If it reads non-zero when a phone
      level shows the enclosure level, pass the negated reading as
      `--inclinometer-roll-zero-deg` and confirm it then reads ~0. Then hit
      shots rolled ~3° with and without the flag; horizontal launch should
      shift by about roll × vertical launch (radians).
- [ ] **`--level-warning-deg 1.5`.** Tilt past 1.5°: log `Enclosure NOT
      level`, UI banner "Unit is not level". Back under 1.2° clears it; it
      must not flap near 1.5°. Reconnect a client and confirm it gets the
      state.
- [ ] **UI extras (menu → Display).** Big number after shot, consistency
      colours (need ≥5 shots per club), voice callout (needs a voice on the
      Pi's Chromium: `sudo apt install speech-dispatcher espeak-ng`, then
      restart Chromium; the menu shows "No voices installed on this device"
      otherwise; Chromium stays silent until the screen is first tapped),
      normalized carry line. Stats →
      Flight / Dispersion / Gapping use the new `flight` payload; Practice
      is under menu → System; TV layout at `/display?layout=tv`.

## 9. Latency and self-check options (off by default)

- [ ] **Baseline `[LATENCY]` lines** (always on). Hit 10 shots; note trigger→ui
      and →ready. Expect capture ≈ post-trigger span + serial dump time; the
      dump dominates.
- [ ] **`--fast-dsp`.** Same shots; ball/club speed and spin must be
      numerically equivalent (to ~1e-9) to the baseline;
      `[LATENCY] →processed` a few ms lower.
- [ ] **`--radar-profile low-latency`.** Confirm the log shows the 50 ksps
      preset with S#20 and that captures arrive with ~51 ms trigger offset
      (~31 ms post-trigger) and 4096 samples. Compare ball speed and spin
      against the standard profile on 10 shots; spin may still be missing
      when the ball signal after impact is shorter than 20 ms.
- [ ] **`--gated-postprocessing`.** With the IWR6843, hit shots; the angle
      must still arrive within 400 ms or be marked `skipped_budget` with the
      next shot unaffected. Raise the budget (`--gated-iwr6843-budget-ms`,
      `--gated-camera-budget-ms`) or leave off if the IWR driver misbehaves
      when abandoned mid-dump. With the camera, a skipped clip must leave no
      `camera_capture` session entry and no replay for that shot.
- [ ] **`--derived-metrics`** + menu → Display → More metrics: Total, Roll,
      Apex, Hang time, Land angle, Curve, Side, Face-to-path, Spin loft, Shot
      shape appear; sanity-check smash and face-to-path against a reference.
- [ ] **`--derived-metrics-strict`** on an OPS-only rig (no IWR6843 or
      camera): Face angle, Face-to-path, Curve, Side and Shot shape must not
      appear, instead of a 0° face and "straight" on every shot.
- [ ] **`--spin-axis-model dplane`**: the Live spin-axis tile shows the
      estimated mark, and GSPro / simulator provenance reports the axis as
      estimated.
- [ ] **`--spin-octave-check --spin-octave-prior range`** in a paired session:
      compare spin against the reference with `optimal` and `range`. With
      `range`, driver picks near 5,000 rpm and wedge picks near 1,500 rpm must
      no longer be halved/doubled, and every corrected shot shows low
      quality. The per-club ranges in `clubs/physics.py` are first guesses;
      adjust them from this session.
- [ ] **`--interference-check`.** Run a range session with a Wi-Fi router or
      second radar nearby; watch `[RADAR-HEALTH]` lines and confirm the
      banner does not chatter. Needs three dumps to assert.
- [ ] **`--gspro-ready-signals`.** GSPro must keep the connection with
      IsReady=false beats and show ready again shortly after each shot.
- [ ] **Level tool** (menu → System → Level) with `--inclinometer
      --level-warning-deg`: bubble and numbers track a phone level.
- [ ] **Camera → Show ball zone**: box sits where the ball should be teed.
- [ ] **`--camera-strobe-spin`**: not testable without IR strobe hardware;
      leave off.

## 10. Software updates (`--update-check`)

See [Software updates](updates.md). Validate on the Pi before relying on it.

- [ ] **Baseline.** With the flag off, the menu has no Software row and
      `journalctl -u openflight | grep UPDATE` is empty: the Pi never contacts
      GitHub.
- [ ] **Check.** Enable the flag and restart. Within a minute the menu shows
      "Up to date · <commit>". Push a harmless commit to `main` (a docs edit),
      tap **Check now**, and confirm "Update available" with the commit subject
      and a dot on the menu button.
- [ ] **Phone is read-only.** Open `http://<pi>:8080/` on a phone: the row
      shows the same status with no buttons.
- [ ] **Shot guard.** Hit a shot and tap **Update now** within 15 s; it must be
      refused with "A shot is being processed".
- [ ] **Install.** Tap **Update now**. Watch the steps, then the restart. The
      screen must come back on its own within about two minutes showing the
      new commit, and "Updated to <commit>" under the Software row. Hit five
      shots to confirm the radar re-armed after the restart.
- [ ] **UI change.** Repeat with a commit that changes `ui/` so the build step
      runs; the kiosk must show the new UI after the restart.
- [ ] **Rollback.** Over SSH, make the next update fail on purpose: push a
      commit whose `ui/src` does not compile (on a test branch, then run with
      `--update-branch <test-branch>` after `git checkout <test-branch>`).
      Tap **Update now**: expect "Update failed", "previous version was
      restored", and the old UI and commit after the restart.
- [ ] **Local edits.** Edit any tracked file on the Pi; **Update now** must be
      refused and the file left alone. `git checkout -- <file>` afterwards.
- [ ] **Offline.** Unplug the network and tap **Check now**: "Can't check for
      updates" with a git error, and nothing else changes.
- [ ] **Stop mid-install.** Start an update that rebuilds the UI and run
      `sudo systemctl stop openflight` during "Building the interface". The
      journal must show "rolling it back before exiting", the stop must wait
      for it, and `git log -1` must still be the old commit with the old UI.
- [ ] **Moved after check.** Tap **Check now**, push another commit, then
      **Update now**: refused with "check for updates again".

## 11. Installer and stable device names

- [ ] `scripts/install.sh --dry-run` lists every step; the real run completes
      and `scripts/openflight-doctor.sh` passes after a reboot. On the first
      run the self-check reports new groups as "takes effect after you log
      out and back in" rather than FAIL.
- [ ] Re-run with only `--with-updates` after an install that used
      `--server-args "--radar-port /dev/ttyAMA0"`: `/etc/default/openflight`
      must still contain `--radar-port /dev/ttyAMA0`.
- [ ] Cold boot to the desktop: the kiosk opens without a manual restart.
      `journalctl -u openflight -b` shows `Desktop display ready after Ns` or
      nothing about the display, and never `KIOSK NOT STARTED`.
- [ ] `ls -l /dev/openflight-ops243 /dev/openflight-iwr-cli
      /dev/openflight-iwr-data` resolve to the right ttys (CP2105 interface
      00 is the CLI, 01 the data port).
- [ ] `scripts/setup/flash-iwr6843.sh --probe` reaches the ROM bootloader
      through `/dev/openflight-iwr-cli`.
