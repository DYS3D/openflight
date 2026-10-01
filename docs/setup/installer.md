---
icon: lucide/download
---

# One-Command Installer

`scripts/install.sh` sets up a Raspberry Pi 5 running Raspberry Pi OS Bookworm
(64-bit) without prompts beyond one confirmation. It is idempotent: re-running
it checks each step and changes only what is missing, so it is also the way to
repair a half-finished install.

```bash
git clone https://github.com/DYS3D/openflight.git
cd openflight
./scripts/install.sh
```

On a Pi with no checkout yet, the installer can clone one first:

```bash
curl -fsSL https://raw.githubusercontent.com/DYS3D/openflight/main/scripts/install.sh \
  | bash -s -- --yes
```

Everything the installer prints is also appended to `~/openflight-install.log`.
Reboot when it finishes, then run the [doctor](#check-the-install).

## What it does

| Step | Change |
| --- | --- |
| Platform check | Refuses anything but a Pi 5 on Bookworm unless `--force` is given; refuses to run as root (it uses `sudo` itself) |
| System packages | `apt-get install` of git, curl, python3-dev, python3-venv, build-essential, swig, liblgpio-dev, ffmpeg, i2c-tools, avahi-daemon and Chromium (Chromium only when a desktop is installed; plus `python3-picamera2 rpicam-apps` with `--with-camera`) |
| uv | Installs [uv](https://docs.astral.sh/uv/) if it is not already on the path |
| Python environment | `uv sync` (`--extra camera` with a system-site-packages venv under `--with-camera`) |
| Node.js and UI | If the installed Node.js is older than 22.12, installs the major in `.node-version` (the one CI builds with) from NodeSource, then `npm ci` and `npm run build` in `ui/` |
| UART | `raspi-config nonint do_serial_hw 0` (UART on), `do_serial_cons 1` (no serial console), `do_i2c 0`; adds `dtparam=uart0=on` (Pi 5) or `enable_uart=1` and `dtoverlay=disable-bt` (Pi 3/4) under an `# OpenFlight UART` block in `/boot/firmware/config.txt` |
| Groups | Adds you to `dialout`, `gpio`, `i2c` and `video` |
| udev rules | Installs `scripts/setup/99-openflight.rules` as `/etc/udev/rules.d/99-openflight.rules` |
| Service | Installs `openflight.service` for your user and checkout, writes `/etc/default/openflight`, enables the service |
| Kiosk | Desktop autologin and no screen blanking (skip with `--no-kiosk`). On an image without a desktop (Pi OS Lite: no lightdm, labwc or wayfire) the kiosk and Chromium are skipped with a warning and OpenFlight runs headless |
| Self-check | `self_test.py --software-only` (a failure here only warns; hardware waits for the reboot). Groups added a moment ago show as "takes effect after you log out and back in", not as a failure |

Before changing `config.txt` the installer saves a timestamped copy next to it,
for example `/boot/firmware/config.txt.openflight-20260930-141502.bak`. A
second run finds the `# OpenFlight UART` block and leaves the file alone, so
no new backup is made.

## Options

| Flag | Effect |
| --- | --- |
| `--yes` | Skip the confirmation (required when there is no terminal, e.g. piped installs) |
| `--with-iwr6843` | Add `--iwr6843` to the service arguments and print the firmware flash command |
| `--with-camera` | Install the camera packages, add `--camera-capture` to the service arguments, and enable the OV9281 in `config.txt` (`camera_auto_detect=0`, `dtoverlay=ov9281,<port>`, with a timestamped backup) |
| `--camera-port cam0\|cam1` | Which CAM/DISP connector the camera is on (default `cam0`). A Pi 5 treats an overlay without a port as CAM/DISP 1, so the port is always written; re-run with the other value to move it |
| `--with-updates` | Add `--update-check` to the service arguments ([Software updates](updates.md)) |
| `--no-kiosk` | Leave desktop autologin and screen blanking alone |
| `--server-args "..."` | Other server flags for the service, e.g. `"--radar-port /dev/ttyAMA0 --altitude-ft 850"` |
| `--dry-run` | Print every step and every change without making any (nothing is logged either) |
| `--force` | Continue on hardware or an OS other than a Pi 5 with Bookworm |
| `--dir`, `--repo`, `--branch` | Where a piped install clones from and to (default `DYS3D/openflight` `main` into `~/openflight`) |

An IWR6843 build normally moves the OPS243 to the GPIO header, because USB
cannot power both radars ([OPS243 UART migration](../build/ops243-uart.md)).
In that case pass its port too:

```bash
./scripts/install.sh --with-iwr6843 --server-args "--radar-port /dev/ttyAMA0"
```

The service reads its arguments from `/etc/default/openflight`
(`OPENFLIGHT_ARGS="..."`). A re-run without `--server-args`, `--with-iwr6843` or
`--with-camera` keeps your hand edits to that file. A re-run with them merges
the new arguments into the existing ones: an option given again replaces its
old value, every other earlier option (such as `--radar-port /dev/ttyAMA0`) is
kept, the result is printed, and a timestamped backup is kept. To drop an
argument, edit the file. After editing it, apply the change with
`sudo systemctl restart openflight`.

At boot the kiosk waits up to 60 s for the desktop's display to appear before
opening the browser (set `OPENFLIGHT_DISPLAY_WAIT_S` in the env file to change
it). If the display never appears, or the browser exits during start-up, the
journal shows `KIOSK NOT STARTED` and the server keeps running.

## Stable device names

The udev rules give each radar a name that does not depend on plug order, and
make them usable by the `dialout` group:

| Name | Device |
| --- | --- |
| `/dev/openflight-ops243` | OPS243-A over USB (STMicroelectronics CDC ACM, VID `0483`) |
| `/dev/openflight-iwr-cli` | IWR6843LEVM CP2105 (VID `10c4`, PID `ea70`) interface 00, the Enhanced UART that the OpenFlight firmware and the ROM bootloader use |
| `/dev/openflight-iwr-data` | The same CP2105's interface 01, the Standard UART |

OpenFlight tries these names first when it auto-detects the radars and falls
back to scanning `/dev/ttyACM*` and `/dev/ttyUSB*` when they are absent. An
OPS243 on the GPIO header is `/dev/ttyAMA0` and gets no udev name.

## Check the install

After the reboot:

```bash
./scripts/openflight-doctor.sh
```

The doctor prints PASS, FAIL or SKIP for each check and exits non-zero when
any check fails:

- serial/GPIO group membership and the udev rules file
- UART configuration in `config.txt` and `cmdline.txt` (a failure only when the
  OPS243 is on the GPIO UART)
- the `/dev/openflight-*` names for the connected radars
- the OPS243 is found and answers a firmware-version query
- the IWR6843 answers the OpenFlight CLI (required when the service runs with
  `--iwr6843`, or with `--with-iwr6843`)
- `openflight.service` is enabled and not failed
- at least 1 GB free

It reads the radar port and IWR6843 mode from `/etc/default/openflight`, so it
checks the setup the service will actually use. A running service is stopped
while the radars are queried (two programs cannot share a serial port) and
started again afterwards. `--software-only` skips the radar queries and
leaves the service running; `--ops-port PORT` overrides the OPS243 port.

The checks live in `scripts/hardware-test/self_test.py --doctor`, which reuses
the [hardware diagnostic](diagnostic.md). Run the diagnostic when the doctor
reports a radar failure and you need the full signal path, including the
sound trigger.

## Flash the IWR6843

```bash
./scripts/setup/flash-iwr6843.sh           # newest image in firmware/releases/
./scripts/setup/flash-iwr6843.sh --probe   # bootloader handshake only
./scripts/setup/flash-iwr6843.sh --dry-run # show the command
```

The helper uses `/dev/openflight-iwr-cli` (or finds the CP2105 interface 00
under `/dev/serial/by-id`), stops the OpenFlight service while it runs, and
starts [the guided flashing tool](../iwr6843/flashing.md), which asks you to
set the board switches and press RESET. `--port` and `--image` override the
defaults.

## The interactive setup script

`scripts/setup/setup.sh` is the older interactive setup. It still works and
walks through the one-time hardware steps the installer does not do: saving
the OPS243 [rolling-buffer mode](rolling-buffer.md) to flash (it needs a power
cycle), legacy K-LD7 naming, the Geekworm UPS, a desktop shortcut, and cloud
sync.
