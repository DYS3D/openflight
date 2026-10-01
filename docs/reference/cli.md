---
icon: lucide/terminal
---

# CLI Flags

Every flag accepted by the server, grouped by subsystem.

`scripts/start-kiosk.sh` accepts most of these and forwards them. Use
`--dry-run` to print the exact command the script would run:

```bash
scripts/start-kiosk.sh --iwr6843 --dry-run
```

!!! note "Generated from the source"

    This table is derived from the argparse definitions in
    `src/openflight/server.py`. If a flag here disagrees with the code, the
    code is right — please open an issue.

## Server & web

Binding, ports, and debug output.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--mock`, `-m` | flag | Run in mock mode without radar |
| `--mock-swing-speed` | flag | Run swing speed training mode with simulated reps and no OPS radar |
| `--host` | default `0.0.0.0` | Host to bind to (default: 0.0.0.0) |
| `--web-port` | int; default `8080` | Web server port (default: 8080) |
| `--auth-required` | flag; default off | Require the device token from clients that are not on the Pi itself and only accept browsers from the Pi's own hostname/IP, `localhost`, or `--allowed-origin`. The kiosk (loopback) is always exempt. Off = today's open server |
| `--auth-token-file` | path; default `~/.config/openflight/token` | Device token file (created with mode 0600 on first `--auth-required` start or by the installer; `$OPENFLIGHT_AUTH_TOKEN` overrides it). Send it as `X-OpenFlight-Token`, `Authorization: Bearer`, `?token=`, or Socket.IO `auth: {token}` |
| `--allowed-origin` | repeatable | Extra browser origin/hostname accepted with `--auth-required` |
| `--request-rate-limit` | float/s; default `0` | Refuse HTTP requests from one non-loopback IP above this sustained rate (2 s burst window) with 429. 0 = no limit |
| `--max-request-bytes` | int; default `0` | Reject HTTP bodies larger than this with 413. 0 = unlimited |
| `--update-check` | flag; default off | Check GitHub for a newer OpenFlight and show an Update button on the touchscreen (kiosk only). See [Software updates](../setup/updates.md) |
| `--update-remote` | default `origin` | Git remote the update check fetches |
| `--update-branch` | default `main` | Branch to follow; the Pi must be checked out on it |
| `--update-check-hours` | float; default `6` | Hours between automatic update checks |
| `--level-warning-deg` | float; default `0` (off) | With `--inclinometer`: send a `level_status` event (UI banner) when enclosure pitch or roll exceeds this many degrees; clears below 0.8× |
| `--startup-status-file` | path | Write structured initialization progress for the optional kiosk splash |
| `--debug`, `-d` | flag | Enable verbose FFT/CFAR debug output |
| `--radar-log` | flag | Log raw radar data to console (Python logging) |
| `--show-raw` | flag | Show raw radar readings in console (signed values) |

## OPS243 radar & transport

Latency options (off by default; `docs/setup/hardware-validation.md` §9):

| Flag | Type / default | Description |
| --- | --- | --- |
| `--radar-profile` | `standard` \| `low-latency`; default `standard` | `low-latency` runs the OPS243 at 50 ksps with the pre-trigger span kept at the same duration (S#27), cutting ~55 ms before the dump starts. Costs post-impact window (spin will rarely report) and halves per-window frequency resolution |
| `--fast-dsp` | flag; default off | Pre-planned, multi-threaded FFT path in the rolling-buffer processor; numerically identical results, ~35% faster STFT stage |
| `--gated-postprocessing` | flag; default off | Run IWR6843 and camera enrichment inside a 400 ms budget each; a stage that overruns is skipped (`*_status="skipped_budget"`) so ball speed and carry are never delayed |

Every shot now logs `[LATENCY] trigger→ui … ms, →final … ms` and carries `latency_ms` in the UI payload (Debug panel).


Serial port, baud, and sample rate.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--port`, `-p` | — | Serial port for radar |
| `--ops-baud` | int | — |
| `--radar-auto-reconnect` | flag; default off | After a serial error on the OPS243 or IWR6843 (cable pulled, USB reset), close the port and re-run radar detection — the udev names `/dev/openflight-ops243` and `/dev/openflight-iwr-cli` first — with exponential back-off capped at 30 s, then re-apply the radar configuration. The UI status menu shows the radar as reconnecting meanwhile. Off by default: the capture loops keep retrying the dead port, as before. A read timeout never triggers a reconnect. |
| `--sample-rate` | int; default `30` | Radar sample rate in ksps (default: 30). Lower = longer buffer but lower max speed. 25=174mph/164ms, 27=187mph/152ms |

## Trigger & capture

How a capture is initiated and framed.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--trigger` | choices: `sound`, `speed`; default `sound` | Trigger strategy |
| `--sound-pre-trigger` | int; default `16` | Pre-trigger segments S#n, 0-32 (default: 16 = 50/50 split, each segment ~4.27ms at 30ksps) |

## OPS243 timing

Clock-sync and re-arm timing for the sound-trigger capture cycle
(`src/openflight/radar_timing.py`). Every default reproduces the shipped
behaviour; the two fast paths are opt-in. If a dump ever arrives without its
`]}` terminator, the active timing reverts to these defaults for the rest of
the session and a single warning is logged (`[TIMING] ... reverting radar
timing to the slow defaults`).

| Flag | Type / default | Description |
| --- | --- | --- |
| `--clock-sync-samples` | int; default `36` | `C?` reads per accepted shot when mapping the radar clock to host time |
| `--clock-sync-max-duration` | float; default `1.25` | Seconds to keep sampling an integer-only radar clock while waiting for a one-second rollover |
| `--rearm-drain-poll` | float; default `0.2` | Seconds between serial-drain polls before re-arming |
| `--rearm-after-pa` | float; default `0.1` | Seconds to wait after the first `PA` on re-arm |
| `--rearm-after-split` | float; default `0.1` | Seconds to wait after `S#n` on re-arm |
| `--rearm-after-activate` | float; default `0.15` | Seconds to wait after the second `PA` on re-arm |
| `--fast-clock-sync` | flag | Stop the per-shot clock sync early once a fractional-clock reply arrives with under 3 ms of read latency. Default off: always take every sample. |
| `--rearm-after-handoff` | flag | Hand an accepted capture to shot processing and the UI before re-arming the radar. Default off: re-arm first, then process. Rejected (silent) captures still re-arm immediately. |

## IWR6843 angle radar

The supported angle radar.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--iwr6843` | flag | Enable TI IWR6843 L3 capture and LCMF-v1 vertical launch angle |
| `--iwr6843-port` | — | TI serial port (auto-detect by default) |
| `--iwr6843-config` | default `config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` | TI RF config matching the flashed L3 firmware |
| `--iwr6843-cal` | default `config/iwr6843_calibration_reference.json` | TI complex array/range calibration JSON |
| `--iwr6843-trigger-pin` | int; default `17` | BCM GPIO receiving the shared sound-trigger edge (default: 17) |
| `--iwr6843-tee-m` | float; default `1.575` | Antenna-center to tee slant range in metres (default: 1.575) |
| `--iwr6843-net-m` | float; default `4.6` | Antenna-center to net range in metres (default: 4.6) |
| `--iwr6843-tilt-deg` | float | Override mount tilt from the TI calibration JSON |
| `--iwr6843-radar-height-m` | float | Override antenna-center height from the TI calibration JSON |
| `--iwr6843-ball-height-m` | float; default `0.04` | Ball-center height above the floor/mat (default: 0.040) |
| `--iwr6843-tx-order` | choices: `auto`, `normal`, `reversed`; default `auto` | TI TDM chirp order; auto reads the chirp masks from the cfg |
| `--iwr6843-estimator-process` | flag; default off | Run the LCMF launch-angle and club-path estimators in a long-lived worker process so their numpy loops cannot stall the OPS serial reader. Off = inline, as before. Any worker failure falls back to inline for the session |
| `--iwr6843-fast-angle-search` | flag; default off | Coarse-to-fine launch-angle search (2° sweep, then 0.25° within ±1°) instead of the exhaustive 0.5° sweep. Off = exhaustive sweep, as before |
| `--iwr6843-capture-timeout` | float; default `16.0` | Maximum seconds an OPS shot waits for its TI UART dump |
| `--iwr6843-output-dir` | — | Raw TI dump directory when --debug is enabled (default: <session-log-dir>/iwr6843) |
| `--iwr6843-azimuth-offset-deg` | float | Azimuth of the radar boresight relative to the target line, in degrees. Positive means boresight points right of the target line. Added to the measured club path; 0 reports club path relative to boresight. |
| `--iwr6843-horizontal-phase-reference-rad` | float | Static target-line phase measured by horizontal aim calibration. Subtracted from the TX2 horizontal proxy before angle conversion. |

## Inclinometer

LIS3DH enclosure tilt compensation.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--inclinometer` | flag | Enable LIS3DH enclosure pitch compensation for IWR6843 tilt |
| `--inclinometer-zero-offset` | float | Degrees added to raw LIS3DH pitch (default: 0) |

## Ballistics & spin

Opt-in accuracy models (see [Hardware validation](../setup/hardware-validation.md)):

| Flag | Type / default | Description |
| --- | --- | --- |
| `--spin-axis-model` | `legacy` \| `dplane`; default `legacy` | `legacy` = horizontal launch minus club path (today). `dplane` = D-plane geometry: face-to-path from the start-direction ratio, spin loft from launch angle and attack angle, tilt = atan(tan(face-to-path)/tan(spin loft)). Labelled estimated |
| `--show-normalized-carry` | flag; default off | Re-simulate each shot at sea level, 77 °F, 0% humidity and add `carry_normalized_yards` to the UI payload and session log |
| `--ball-marker` | `none` \| `dot` \| `rct`; default `none` | Tell the radar spin estimator the ball has a metallic dot or is a Titleist RCT ball: expects the once-per-revolution line, prefers it over a 2× line, and lets a passing result reach the trusted band |
| `--spin-octave-check` | flag; default off | Halve a spin pick that is ≈2× the club prior (or double one ≈0.5×) when a supporting spectral peak exists; tagged `octave_*` in `spin_method` |
| `--inclinometer-roll-compensation` | flag; default off | With `--inclinometer`: rotate IWR6843 launch angles and club path by the measured enclosure roll into a level frame |
| `--derived-metrics` | flag; default off | Add a `derived` block to UI shot payloads: smash, face angle, face-to-path, dynamic loft, spin loft, curve, side, apex, hang time, landing angle, descent speed, roll, total and a shot-shape label, each tagged measured/estimated. Display only; never logged |
| `--derived-metrics-strict` | flag; default off | With `--derived-metrics`: omit face angle, face-to-path, curve, side and shot shape when the horizontal launch is the neutral 0° estimate (`launch_angle_horizontal_source` = `estimated`) or there is no spin axis, and dynamic loft / spin loft when the vertical launch is the club-table estimate. Off keeps today's block. The UI simply hides missing tiles |
| `--interference-check` | flag; default off | Track the OPS243 noise floor from every buffer dump and emit `radar_health` (UI banner + status row) when it rises > 6 dB above its baseline for three samples |
| `--gspro-ready-signals` | flag; default off | GSPro heartbeats report `LaunchMonitorIsReady` from the real armed state (radar connected, no shot in flight) instead of a constant |
| `--camera-strobe-spin` | flag; default off | **Experimental, needs IR strobe hardware.** With `--camera-capture`: estimate spin from a marked ball in two strobed exposures and attach `camera_spin_*` fields. Never overwrites radar spin |


Carry model and spin handling.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--ballistics` | flag | Use the physics-based carry simulator (drag + Magnus, RK4). This is the default; shots without a vertical launch angle fall back to the legacy table estimator. |
| `--no-ballistics` | flag | Disable the physics simulator and use the legacy carry table for all shots. |
| `--altitude-ft` | float; default `0` | Site elevation in feet for the ballistic carry model |
| `--temperature-f` | float; default `59` | Air temperature in °F for the ballistic carry model |
| `--humidity` | float; default `0` | Relative humidity in percent for the ballistic carry model |
| `--calculated-spin` | flag | Replace radar-measured spin with the kinematic estimate (170*v*sin(LA)^1.2) when the launch angle was measured. The 24 GHz OPS return carries no usable spin line (see src/openflight/spin_estimate.py); the measured value is kept in spin_rpm_measured for offline scoring |
| `--speed-correction-without-angle-radar` | flag | Apply the ball-speed cosine correction (`src/openflight/speed_correction.py`) on OPS-only builds using the table-estimated launch angle and the `--kld7-ball-distance` / `--kld7-radar-height-inches` geometry. Default off: the correction only runs with `--kld7` or `--iwr6843`. |

## Swing speed

Club-only training mode.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--swing-speed` | flag | Run club-only swing speed training mode (no impact or ball required) |
| `--swing-speed-threshold` | float; default `30.0` | Outbound speed threshold that starts a swing speed rep (default: 30 mph) |
| `--swing-speed-max` | float; default `130.0` | Maximum plausible swing speed accepted from OPS reports; use 0 to disable (default: 130 mph) |
| `--swing-speed-min-readings` | int; default `3` | Minimum qualifying radar readings required to count a swing speed rep (default: 3) |
| `--swing-speed-single-peak` | float; default `60.0` | Peak speed that can count as a swing from one radar reading (default: 60 mph) |
| `--swing-speed-num-reports` | int; default `8` | Number of OPS speed candidates to report per sample cycle (default: 8) |
| `--swing-speed-end-ms` | float; default `1000.0` | Milliseconds below threshold before ending a swing speed rep (default: 1000) |
| `--swing-speed-cooldown-ms` | float; default `750.0` | Cooldown after a swing speed rep before accepting another (default: 750) |
| `--swing-speed-rejected-cooldown-ms` | float; default `100.0` | Cooldown after an ignored short motion before re-arming (default: 100) |

## Logging & session data

Where session logs go and what they capture.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--session-location`, `-l` | default `range` | Location identifier for session logs (e.g., 'range', 'course', 'home') |
| `--log-dir` | — | Directory for session logs (default: ~/openflight_sessions) |
| `--profiles-path` | path | Profile store (default: `OPENFLIGHT_PROFILES_PATH` or `~/.config/openflight/profiles.json`) |
| `--no-logging` | flag | Disable session logging |
| `--log-retention-days` | float; default `90` | At startup, delete session logs, raw radar logs, TI dumps, camera captures, and `~/openflight_logs` debug files older than this (0 = keep forever). Every file is listed in the log before it is removed; sessions and dumps still waiting for cloud upload are kept |
| `--log-max-mb` | float; default `8192` | At startup, trim the oldest of those files until the log directory fits this size (0 = no limit) |

## Simulators & power

Outbound connectors and battery status.

| Flag | Type / default | Description |
| --- | --- | --- |
| `--battery` | — | Show battery and external-power status using the selected provider |
| `--sim` | flag | Enable simulator connectors from config/sim.json (GSPro / OpenGolfSim / PAR-TEE). Off by default. |

## High-speed camera capture

Optional rolling-buffer capture and replay. See [camera setup](../camera/README.md).

| Flag | Type / default | Description |
| --- | --- | --- |
| `--camera-capture` | flag | Enable high-speed rolling-buffer capture and replay |
| `--camera-capture-width` | int; default `640` | Capture width |
| `--camera-capture-height` | int; default `400` | Capture height |
| `--camera-capture-fps` | float; default `300` | Capture frame rate |
| `--camera-capture-pre-ms` | float; default `150` | Milliseconds retained before the trigger |
| `--camera-capture-post-ms` | float; default `50` | Milliseconds retained after the trigger |
| `--camera-capture-exposure-us` | int; default `1000` | Exposure seed for startup calibration |
| `--camera-capture-gain` | float; default `4.0` | Analogue-gain seed for startup calibration |
| `--camera-capture-mount-height-m` | float; default `0.20955` | Camera optical-center height above the hitting surface |
| `--camera-capture-horizontal-offset-deg` | float; default `0` | Target-line correction added to horizontal launch angles |
| `--camera-capture-lateral-offset-m` | float; default `0` | Camera position relative to radar center; positive is target-right |
| `--camera-capture-roll-deg` | float; default `0` | Clockwise image-roll correction for preview and geometry |
| `--camera-capture-stream` | `raw` or `main-y`; default `raw` | Camera stream to persist |
| `--camera-capture-scaler-crop` | `X,Y,W,H` | Optional Picamera2 scaler crop |
| `--camera-capture-rotate-180` | flag | Rotate saved frames 180 degrees |
| `--camera-capture-mirror-horizontal` | flag | Mirror saved frames left-to-right after rotation |
| `--camera-archive-frames` / `--no-camera-archive-frames` | flag; default on | Write each matched clip to the camera output directory as `frames.npz` plus stills. Turning it off keeps nothing on disk, so shot replay is unavailable; requires `--camera-frames-in-memory` |
| `--camera-frames-in-memory` | flag | Hand the captured frame stack to the launch estimators directly and archive it in the background, instead of re-reading `frames.npz` from disk for every shot. Off by default |

## K-LD7 (deprecated)

Retained for existing builds only. See [Legacy (K-LD7)](../legacy/index.md).

| Flag | Type / default | Description |
| --- | --- | --- |
| `--kld7` | flag | [DEPRECATED] Enable K-LD7 vertical angle radar (launch angle) |
| `--kld7-port` | — | K-LD7 vertical serial port (auto-detect if not specified) |
| `--kld7-angle-offset` | float; default `1.5` | K-LD7 vertical boresight offset in degrees. Not user-measurable without a corner reflector; 1.5 is the calibrated default for the standard mount (default: 1.5) |
| `--kld7-mount-tilt` | float | K-LD7 vertical radar mount tilt in degrees. REQUIRED with --kld7 — measure it with a phone inclinometer against the radar face; there is no default because a wrong tilt silently corrupts the launch angle |
| `--kld7-ball-distance` | float; default `5.0` | Radar-to-tee distance in feet (default: 5.0) |
| `--net-distance` | float; default `10.0` | Ball-to-net/screen distance in feet (two_ray). For nets beyond the ~11ft FSK range wrap, far-flight frames are de-aliased and kept instead of dropped (default: 10.0; nets at/inside the wrap are unaffected). |
| `--kld7-radar-height-inches` | float; default `4.0` | K-LD7 radar height above the ball in inches, used by the ball-speed cosine correction geometry (default: 4.0) |
| `--kld7-vertical-raw` | flag | TEST MODE: show the raw vertical launch angle for every shot the estimator produces, bypassing all display guardrails (plausibility, soft-lane, estimator-agreement, confidence floor). Default off. |
| `--kld7-horizontal` | flag | [DEPRECATED] Enable K-LD7 horizontal angle radar (club path) |
| `--kld7-horizontal-port` | — | K-LD7 horizontal serial port |
| `--kld7-horizontal-offset` | float | K-LD7 horizontal angle offset in degrees (default: 0.0) |

## Wrapper-only flags

Handled by `scripts/start-kiosk.sh` itself rather than passed through.

| Flag | Description |
| --- | --- |
| `--dry-run` | Print the command that would run, then exit |
| `--startup-splash` | Show component progress while the kiosk starts |
| `--startup-splash-port` | Port used by the temporary splash server |
| `--port`, `--web-port` | Set the kiosk web port |
| `--radar-port`, `--ops-port` | Forward the OPS serial port as the server's `--port` |
| `--buffer-split` | Buffer split preset (`balanced`, `post-heavy`, `pre-heavy`) or raw segment count |

## Related

- [Running & modes](../using/running.md) — the common invocations
- [Configuration files](configuration.md) — settings that are not flags
- [Constants](constants.md) — values compiled in rather than passed
