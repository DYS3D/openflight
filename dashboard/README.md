# OpenFlight home dashboard

Session history, trends, club gapping and personal records for your OpenFlight,
running on your own PC or NAS. Nothing leaves your home network.

The dashboard copies session logs from the Pi every few minutes into its own
database (`data/openflight.sqlite3`), so your history survives even after the
Pi prunes old logs. Shots you delete on the unit are left out.

## 1. Turn on log sharing on the Pi

Add `--session-log-api` to `OPENFLIGHT_ARGS` in `/etc/default/openflight`, then
`sudo systemctl restart openflight`. Check it from any computer on your network:
`http://<pi-ip>:8080/api/session-logs` should list your sessions.

The Pi only serves `session_*.jsonl` files, read-only. If the Pi runs with
`--auth-token-file`, pass the same token to the dashboard (`--token` or
`OPENFLIGHT_TOKEN`).

## 2a. Run it on a NAS (Docker)

Copy this `dashboard/` folder to the NAS, set your Pi's IP address in
`docker-compose.yml`, then:

```bash
docker compose up -d --build
```

Synology (Container Manager), QNAP (Container Station), Unraid and TrueNAS can
all build from a compose file. Open `http://<nas-ip>:8090`.

## 2b. Run it on a PC

Install [uv](https://docs.astral.sh/uv/), then from this folder:

```bash
uv run openflight-dashboard --pi http://<pi-ip>:8080
```

Open `http://localhost:8090`. Leave it running (or start it at login) to keep
copying new sessions.

## Options

| Flag | Environment variable | Default |
|---|---|---|
| `--pi URL` | `OPENFLIGHT_PI_URL` | none: only imported logs are shown |
| `--token TOKEN` | `OPENFLIGHT_TOKEN` | none |
| `--data-dir DIR` | `OPENFLIGHT_DASHBOARD_DATA` | `./data` |
| `--port PORT` | `OPENFLIGHT_DASHBOARD_PORT` | `8090` |
| `--host HOST` | `OPENFLIGHT_DASHBOARD_HOST` | `0.0.0.0` (reachable from your network) |
| `--sync-minutes N` | `OPENFLIGHT_SYNC_MINUTES` | `5` |

Logs copied off the Pi by hand (USB stick, `scp`) can be loaded with:

```bash
uv run openflight-dashboard import ~/Downloads/session_*.jsonl
```

## Development

```bash
uv run --group dev pytest
```
