---
icon: lucide/refresh-cw
---

# Software Updates

OpenFlight can check GitHub for a newer version and offer an **Update**
button on the touchscreen. This is **off by default**. When it is off, the Pi
never contacts GitHub on its own and nothing on this page applies.

## Turn it on

Either re-run the installer with `--with-updates`:

```bash
cd ~/openflight
./scripts/install.sh --with-updates
sudo systemctl restart openflight
```

Re-running the installer with any service flag rewrites
`/etc/default/openflight` (the old file is kept as a timestamped backup), so
pass every flag you use, for example `--with-iwr6843 --with-updates`.

Or add `--update-check` to `OPENFLIGHT_ARGS` in `/etc/default/openflight` and
run `sudo systemctl restart openflight`.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--update-check` | off | Check for updates and show the Update button |
| `--update-remote` | `origin` | Git remote to update from |
| `--update-branch` | `main` | Branch to follow; the Pi must be checked out on it |
| `--update-check-hours` | `6` | Hours between automatic checks (the first runs 30 s after start) |

## Using it

Open the menu (the logo button, bottom left). When an update is waiting, a
dot appears on that button. Under **System**, the **Software** row shows one of:

- **Up to date · abc1234**: tap **Check now** to check immediately.
- **Update available**: tap **Update** to see what changed, then
  **Update now**.
- **Can't check for updates**: the reason is shown underneath (no network,
  local edits, wrong branch).

After **Update now**, OpenFlight:

1. Stops the radar and other hardware.
2. Fast-forwards the checkout to the new commit (`git merge --ff-only`).
3. Runs `uv sync` (with `--extra camera` when the camera is enabled).
4. Runs `npm ci` in `ui/`, but only if `ui/package.json` or its lockfile changed.
5. Builds the interface into `ui/dist.update` and swaps it in only when the
   build succeeds, so a failed build never leaves the kiosk without a UI.
6. Imports the new server as a smoke check.
7. Restarts. `openflight.service` restarts the server on its own, and the
   screen comes back in about a minute. Phones and TVs showing the display
   reload by themselves.

If any step fails, the previous commit, `uv.lock`, Python packages, UI packages
and UI build are put back, and OpenFlight restarts on the old version. The menu
then shows "Last update failed; the previous version was restored".

## Safety rules

- **Only the touchscreen can check or install.** Phones, tablets and TVs on the
  network see the status but get no buttons, and the server refuses their
  requests, even with `--auth-required` off.
- **Never during a shot.** An update is refused for 15 s after a shot is
  captured or processed.
- **Fast-forward only.** If the Pi has local commits or edited files, or is on
  another branch, the update is refused and nothing is changed. Update over SSH
  instead. The one exception is `ui/package-lock.json`: npm sometimes rewrites
  it on its own, so the updater restores it before merging.
- **Nothing runs until you tap.** Checking only runs `git fetch`.

## Over SSH

The same updater works from a terminal, which is handy when the service is
stopped or the update button reports a problem:

```bash
cd ~/openflight
uv run python -m openflight.updater check          # what's new
sudo systemctl stop openflight
uv run python -m openflight.updater apply          # add --camera with the camera extra
sudo systemctl start openflight
```

Without the updater, the manual path still works:

```bash
cd ~/openflight
git pull
./scripts/install.sh --yes
sudo systemctl restart openflight
```

## When something goes wrong

- Every attempt writes the full command output to
  `~/openflight_logs/update_YYYYMMDD_HHMMSS.log`.
- The outcome of the last attempt is in
  `~/.local/state/openflight/last_update.json`.
- `journalctl -u openflight | grep UPDATE` shows each step as it ran.
- "Rollback was incomplete" means a package reinstall failed (usually no
  network). The checkout is already back on the old commit; run
  `./scripts/install.sh --yes` once the network is back.
- If OpenFlight was started from the desktop rather than by the service, it
  exits after the update and you relaunch it yourself.
