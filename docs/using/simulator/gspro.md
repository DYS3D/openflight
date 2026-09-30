# GSPro

OpenFlight streams shots into [GSPro](https://gsprogolf.com/) using the
**OpenConnect V1** API — the de-facto open standard for launch-monitor → sim
integrations.

See the connector architecture in [README.md](https://github.com/jewbetcha/openflight/blob/main/README.md). This page covers
requirements and setup specific to GSPro.

## Requirements

- **A GSPro license.** GSPro is paid software (subscription). The OpenConnect
  API is included with a standard license — there is no separate add-on.
- **An OpenAPI / OpenConnect license type.** GSPro ties each license to a
  launch-monitor type, and OpenFlight connects as a generic OpenConnect device.
  If your license is set to a specific monitor (Foresight, Uneekor, etc.), you
  must convert it to the **OpenAPI** type using GSPro's
  [license conversion tool](https://gsprogolf.com/convert.html). After
  converting, install the latest GSPro and **clear your GSPro Connect settings**
  so the OpenAPI option is selectable (see
  [How To Clear GSPro Connect Settings](https://gspro.gitbook.io/gspro-knowledge-base/troubleshooting-and-support/how-to-clear-gspro-connect-settings)).
- **The GSPro Connect window.** GSPro exposes the API through its built-in
  "GSPro Connect" / OpenAPI interface, which listens on **TCP port 921**.
- **Network reachability + an open port 921.** OpenFlight (the Raspberry Pi)
  and the PC running GSPro should be on the same LAN; you need the GSPro PC's IP
  address. **TCP port 921 must be reachable on the GSPro PC** — allow it as an
  inbound rule through the PC's firewall (Windows Defender Firewall by default).
  If OpenFlight and GSPro are on *different* networks, you'd have to forward 921
  to the GSPro PC on the router — but OpenConnect has **no authentication**
  (below), so never expose 921 to the public internet; keep both on the same
  trusted LAN.
- No account/credentials are sent by OpenFlight — OpenConnect V1 has no auth.

## Setup

1. **Convert your license to OpenAPI** (one-time). Open the
   [GSPro license conversion tool](https://gsprogolf.com/convert.html), enter
   your GSPro license key, and select the **OpenAPI / OpenConnect** option. Then
   install the latest GSPro and clear your GSPro Connect settings so the OpenAPI
   interface is selected
   ([how to clear Connect settings](https://gspro.gitbook.io/gspro-knowledge-base/troubleshooting-and-support/how-to-clear-gspro-connect-settings)).
   Skip this if your license is already the OpenAPI type.

2. **Find the GSPro PC's IP address and open port 921.**
   - Windows: `ipconfig` → IPv4 Address (e.g. `192.168.1.50`).
   - Allow inbound **TCP 921** through the GSPro PC's firewall. The GSPro
     installer can add this rule for you; if OpenFlight's connection is refused,
     add it manually in Windows Defender Firewall (Inbound Rules → New Rule →
     Port → TCP → 921 → Allow).

3. **Configure OpenFlight.** Copy the example config if you haven't already:
   ```bash
   cp config/sim.example.json config/sim.json
   ```
   Set the GSPro connector:
   ```jsonc
   {
     "connectors": [
       {
         "type": "gspro",
         "enabled": true,
         "host": "192.168.1.50",
         "port": 921,
         "device_id": "OpenFlight",
         "units": "Yards",
         "heartbeat_interval_s": 5
       }
     ]
   }
   ```
   Then enable simulator connectors at launch with `--sim` (off by default):
   ```bash
   scripts/start-kiosk.sh --sim
   ```

4. **Open GSPro and start a round**, then open the **GSPro Connect** window
   (the OpenAPI interface). It should report "Waiting for connection".

5. **Start OpenFlight.** The header GSPro pill should turn **green**
   (connected). GSPro Connect should show the device connected.

6. **Hit a shot.** It appears in GSPro within a few milliseconds, and the
   "Sent to GSPro" panel in OpenFlight shows the values sent with
   measured/estimated badges.

## What gets sent

| GSPro field | Source |
|---|---|
| `BallData.Speed` | measured ball speed (required — shot dropped if missing) |
| `BallData.VLA` / `HLA` | measured launch angles, else model fallback |
| `BallData.TotalSpin` | measured spin if high-confidence, else per-club model |
| `BallData.SpinAxis` | measured spin axis, else `0` |
| `BallData.BackSpin` / `SideSpin` | derived from total spin + axis |
| `BallData.CarryDistance` | OpenFlight's carry estimate |
| `ClubData.Speed` / `Path` | measured if available (`ContainsClubData` set accordingly) |

## OpenConnect field requirements

The [OpenConnect V1 spec](https://gsprogolf.com/GSProConnectV1.html) annotates
its example shot message inline. Verbatim, it marks:

| Field | Spec annotation |
|---|---|
| `DeviceID`, `ShotNumber`, `APIversion` | `//required` |
| `Units` | `//default yards` (no other value documented) |
| `BallData.Speed`, `SpinAxis`, `TotalSpin`, `HLA`, `VLA` | `//required` |
| `BallData.BackSpin`, `SideSpin` | `//only required if total spin is not sent` |
| `BallData.CarryDistance` | `//optional` |
| `ClubData.*` (all ten keys) | no annotation — listed as `0.0` with no required/optional marking |
| `ShotDataOptions.ContainsBallData`, `ContainsClubData` | `//required` |
| `ShotDataOptions.LaunchMonitorIsReady`, `LaunchMonitorBallDetected`, `IsHeartBeat` | `//not required` |

The spec never says whether a `ClubData` key may be left out of the JSON, or
whether GSPro reads `ClubData` when `ContainsClubData` is `false`. Its own
example sends every club key as `0.0` with `ContainsClubData: false`.

By default OpenFlight does the same: every `ClubData` key is sent, the ones it
does not measure (`AngleOfAttack`, `FaceToTarget`, `Lie`, `Loft`,
`SpeedAtImpact`, `VerticalFaceImpact`, `HorizontalFaceImpact`, `ClosureRate`,
and `Path` when it was not measured) as `0.0`, and `ContainsClubData` is
`true` only when club speed was measured.

Set `"omit_unsupported_fields": true` on a connector to instead leave
unmeasured `ClubData` keys out of the JSON entirely and set `ContainsClubData`
to `true` when club speed **or** a measured club path is present. `BallData`,
`ShotDataOptions`, and heartbeats are unchanged by this option: every
`BallData` key is required (estimated values are still sent, as documented
above) except `CarryDistance`, which OpenFlight sends deliberately.

Whether GSPro accepts a `ClubData` object with missing keys has not been
verified against a real GSPro install; the spec does not say. Leave the option
off unless you have tested it.

## Club selection

When you change clubs in GSPro, it sends a player update (code 201). OpenFlight
applies it: the current club used for shot tagging and the carry/spin model
follows GSPro. Putts (`PT`) are out of scope and ignored.

## Troubleshooting

- **Pill stays amber (connecting / reconnecting):** OpenFlight can't reach
  `host:port`. Check the IP, that GSPro Connect is open, and that **inbound TCP
  921 is allowed through the GSPro PC's firewall**. "Connecting" means it has
  never connected yet (often a wrong IP or a blocked port); "reconnecting" means
  it was connected and the link dropped. OpenFlight retries automatically with
  backoff (1→2→4→…→30s).
- **Can't select OpenAPI in GSPro Connect, or shots are rejected:** your license
  is likely set to another monitor type. Convert it to OpenAPI
  ([conversion tool](https://gsprogolf.com/convert.html)) and clear your Connect
  settings, then restart GSPro.
- **Pill red (error):** GSPro returned an error code; hover the pill for the
  message. The connection stays up.
- **Shots don't appear in GSPro:** confirm a round is active and the Connect
  window shows the device connected. Check `sim_send` entries in the session
  log to confirm OpenFlight is sending.

## References

- [GSPro OpenConnect V1 spec](https://gsprogolf.com/GSProConnectV1.html)
- [GSPro license conversion tool (convert to OpenAPI)](https://gsprogolf.com/convert.html)
- [GSPro Knowledge Base: clear GSPro Connect settings](https://gspro.gitbook.io/gspro-knowledge-base/troubleshooting-and-support/how-to-clear-gspro-connect-settings)
- [GSPro Knowledge Base](https://gspro.gitbook.io/gspro-knowledge-base)
