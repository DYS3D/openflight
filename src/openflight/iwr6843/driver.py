"""IWR6843 serial driver — the host side of the L3-dump firmware contract.

Firmware v3+ speaks a SINGLE UART (the CP2105 Enhanced interface) at
1,041,667 baud for both CLI commands and the binary dump; the dump is framed
by its "ILD1" magic plus the header-declared length, so CLI echo and payload
can share the pipe. Hardware-validated 2026-07-13 at 100% of wire rate.

Gotchas baked in (each cost a debugging session):
- DTR/RTS must be held low on open (TI EVMs tie them to reset/boot mode).
- One serial handle only — two handles on one tty steal each other's bytes.
- The CP2105 can stall a stream for seconds (cp210x -110 control timeouts)
  and resume; the reader waits out gaps up to ``stall_tolerance_s``.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from collections.abc import Callable

import serial

from openflight.iwr6843.dump import HEADER, MAGIC, parse_header, payload_nbytes
from openflight.iwr6843.readback import SUMMARY_MAGIC, WINDOWS_MAGIC, summary_reply_nbytes

BAUD = 1_041_667
_PORT_GLOBS = ("/dev/ttyUSB*", "/dev/tty.SLAB_USBtoUART*")
# udev symlink to the CP2105 Enhanced interface (scripts/setup/99-openflight.rules).
STABLE_CLI_PORT = "/dev/openflight-iwr-cli"


class DumpRestartError(RuntimeError):
    """The dump was sent but the firmware could not restart capture afterwards."""


class ReadbackError(RuntimeError):
    """A selective-readback command was refused or its reply never completed."""


logger = logging.getLogger(__name__)


def open_port(port: str, baud: int = BAUD, timeout: float = 0.3) -> serial.Serial:
    """DTR/RTS-safe serial open."""
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def _candidate_ports() -> list[str]:
    """Serial ports to probe, the udev stable name first when it exists."""
    candidates: list[str] = []
    for pattern in _PORT_GLOBS:
        candidates.extend(sorted(glob.glob(pattern)))
    if os.path.exists(STABLE_CLI_PORT):
        target = os.path.realpath(STABLE_CLI_PORT)
        candidates = [STABLE_CLI_PORT] + [c for c in candidates if os.path.realpath(c) != target]
    return candidates


class IWR6843Radar:
    """CLI + dump transport for the custom L3-dump firmware."""

    def __init__(self, port: str | None = None, baud: int = BAUD):
        if port is None:
            port = self.detect_port(baud)
            if port is None:
                raise RuntimeError("no IWR6843 CLI found — board on, flashed, single-port fw?")
        self.port = port
        self.ser = open_port(port, baud)
        self.last_dump_reader_stall_s = 0.0

    @staticmethod
    def detect_port(baud: int = BAUD) -> str | None:
        """First serial port whose CLI answers `help` with our commands."""
        candidates = _candidate_ports()
        for cand in candidates:
            try:
                ser = open_port(cand, baud)
            except (OSError, serial.SerialException):
                continue
            try:
                ser.reset_input_buffer()
                ser.write(b"help\n")
                resp = b""
                deadline = time.time() + 1.5
                while time.time() < deadline and b"sensorStart" not in resp:
                    resp += ser.read(512)
            finally:
                ser.close()
            if b"sensorStart" in resp:
                return cand
        return None

    def cmd(self, line: str, window: float = 1.5) -> str:
        """Send one CLI line; collect the response until Done/Error/timeout."""
        self.ser.reset_input_buffer()
        self.ser.write((line + "\n").encode())
        resp = b""
        deadline = time.time() + window
        while time.time() < deadline:
            resp += self.ser.read(512)
            if b"Done" in resp or b"Error" in resp:
                break
        return resp.decode(errors="replace")

    def drain_stale_output(
        self,
        *,
        max_wait_s: float = 10.0,
        initial_quiet_s: float = 0.25,
        stream_quiet_s: float = 4.25,
    ) -> int:
        """Drain an abandoned binary dump before sending configuration commands.

        If a host process exits during ``l3dump``, the firmware can still be
        writing the old payload through the CP2105. Commands sent into that
        stream are not safe to associate with their responses. Once bytes are
        observed, tolerate the bridge's known multi-second stalls before
        declaring the stream quiet.
        """
        drained = 0
        saw_data = False
        start = time.monotonic()
        last_data = start
        while time.monotonic() - start < max_wait_s:
            waiting = self.ser.in_waiting
            if waiting:
                chunk = self.ser.read(min(waiting, 4096))
                if chunk:
                    drained += len(chunk)
                    saw_data = True
                    last_data = time.monotonic()
                    continue
            quiet_s = stream_quiet_s if saw_data else initial_quiet_s
            if time.monotonic() - last_data >= quiet_s:
                break
            time.sleep(0.01)
        if drained:
            logger.warning(
                "[IWR6843] Drained %d stale UART bytes before configuration",
                drained,
            )
        return drained

    @staticmethod
    def _require_done(command: str, response: str) -> None:
        if "Error" in response:
            raise RuntimeError(f"config rejected: {command!r}: {response.strip()}")
        if "Done" not in response:
            raise RuntimeError(
                f"IWR6843 did not acknowledge {command!r}; "
                "the firmware may be wedged (press RESET and retry)"
            )

    def send_config(self, cfg_path: str) -> None:
        """Stop and flush old state, then stream the cfg; raise on Error.

        The firmware's geometry guard rejects a cfg whose loops/samples don't
        match the flashed build — that surfaces here as RuntimeError.
        """
        self.drain_stale_output()
        self._require_done("sensorStop", self.cmd("sensorStop", 3.0))
        self._require_done("flushCfg", self.cmd("flushCfg", 1.5))
        with open(cfg_path, encoding="utf-8") as cfg:
            for rawline in cfg:
                line = rawline.strip()
                if not line or line.startswith("%"):
                    continue
                # The driver owns the lifecycle commands so every config gets
                # the required stop/flush ordering without sending duplicates.
                if line in {"sensorStop", "flushCfg"}:
                    continue
                window = 6.0 if line.startswith("sensorStart") else 1.5
                resp = self.cmd(line, window)
                self._require_done(line, resp)
        deadline = time.monotonic() + 6.0
        health = ""
        while time.monotonic() < deadline:
            health = self.stats()
            self._require_done("stats", health)
            if "active=1" in health:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"IWR6843 did not enter active capture mode: {health.strip()}")

    def read_dump(self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0) -> bytes:
        """Fire `l3dump` and return one complete dump (best effort on stalls).

        Syncs on the ILD1 magic past the CLI echo and sizes the read from the
        dump's own header, so any firmware geometry works.
        """
        self.ser.reset_input_buffer()
        self.ser.write(b"l3dump\n")
        buf = bytearray()
        expected: int | None = None
        start = time.time()
        last = start
        # Longest gap between two reads while data was flowing. The CP2105 drops
        # bytes when the reader is starved, so a short dump logs this as evidence.
        self.last_dump_reader_stall_s = 0.0
        polled = time.monotonic()
        while time.time() - start < timeout_s:
            waiting = self.ser.in_waiting
            now = time.monotonic()
            if waiting and buf:
                self.last_dump_reader_stall_s = max(self.last_dump_reader_stall_s, now - polled)
            polled = now
            chunk = self.ser.read(waiting if waiting else 1)
            if chunk:
                buf.extend(chunk)
                last = time.time()
            elif buf and time.time() - last > stall_tolerance_s:
                break
            if expected is None:
                idx = buf.find(MAGIC)
                if idx >= 0 and len(buf) - idx >= HEADER.size:
                    del buf[:idx]
                    try:
                        metadata = parse_header(buf)
                        expected = metadata["header_nbytes"] + payload_nbytes(metadata, buf)
                    except ValueError:
                        expected = None
            elif len(buf) >= expected:
                break
        if expected is None or len(buf) < expected:
            # The firmware may still be streaming this dump; a command sent
            # into that stream (the next l3dump) would be lost or misread.
            self.drain_stale_output()
            return bytes(buf)

        payload = bytes(buf[:expected])
        if len(payload) == expected:
            # The binary payload can finish just before the CLI handler returns.
            # Wait for its trailing Done before another command can be consumed
            # by the firmware while it is still completing dump/restart work.
            elapsed = time.time() - start
            trailer = self._wait_for_dump_cli_ready(
                buf[expected:], timeout_s=min(1.0, max(0.0, timeout_s - elapsed))
            )
            if b"Error" in trailer:
                raise DumpRestartError(
                    f"IWR6843 dump completed but firmware restart failed: "
                    f"{trailer.decode(errors='replace').strip()}"
                )
        return payload

    def freeze(self) -> None:
        """Stop capture at a frame boundary and hold the ring for readback."""
        self._require_done("l3freeze", self.cmd("l3freeze", 3.0))

    def resume(self) -> None:
        """Restart capture after a selective readback."""
        response = self.cmd("l3resume", 6.0)
        if "Done" not in response or "Error" in response:
            raise DumpRestartError(f"IWR6843 did not resume capture: {response.strip()}")

    def read_summary(self, scope: int) -> bytes:
        """Reply to `l3sum <scope>` on a frozen ring."""
        return self._read_reply(f"l3sum {scope}", SUMMARY_MAGIC, summary_reply_nbytes)

    def read_windows(self, request: str, nbytes: int) -> bytes:
        """Reply to `l3bins <request>` on a frozen ring, `nbytes` long."""
        return self._read_reply(f"l3bins {request}", WINDOWS_MAGIC, lambda _partial: nbytes)

    def _read_reply(
        self,
        command: str,
        magic: bytes,
        reply_nbytes: Callable[[bytes], int | None],
        timeout_s: float = 6.0,
    ) -> bytes:
        """Send a readback command and return its binary reply, past the CLI echo."""
        self.ser.reset_input_buffer()
        self.ser.write((command + "\n").encode())
        buf = bytearray()
        synced = False
        expected: int | None = None
        deadline = time.monotonic() + timeout_s
        while expected is None or len(buf) < expected:
            if time.monotonic() >= deadline:
                self.drain_stale_output()
                raise ReadbackError(
                    f"IWR6843 {command.split()[0]} reply stalled at {len(buf)} bytes"
                )
            waiting = self.ser.in_waiting
            buf.extend(self.ser.read(waiting if waiting else 1))
            if not synced:
                start = buf.find(magic)
                if start < 0:
                    if b"Error" in buf:
                        raise ReadbackError(
                            f"IWR6843 rejected {command.split()[0]}: "
                            f"{buf.decode(errors='replace').strip()}"
                        )
                    continue
                del buf[:start]
                synced = True
            if expected is None:
                expected = reply_nbytes(bytes(buf[:256]))
        self._wait_for_dump_cli_ready(buf[expected:], timeout_s=1.0)
        return bytes(buf[:expected])

    def _wait_for_dump_cli_ready(self, initial: bytes, *, timeout_s: float) -> bytes:
        """Consume the dump handler's trailing response before reusing the CLI."""
        response = bytearray(initial)
        deadline = time.monotonic() + timeout_s
        while b"Done" not in response and b"Error" not in response:
            if time.monotonic() >= deadline:
                break
            waiting = self.ser.in_waiting
            chunk = self.ser.read(waiting if waiting else 1)
            if chunk:
                response.extend(chunk)
        return bytes(response)

    def stats(self) -> str:
        """Firmware health line (frames/wraps/active/calib/rf_faults)."""
        return self.cmd("stats", 2.0)

    def stop_sensor(self) -> None:
        """Stop capture and verify the firmware returned to its idle CLI state."""
        self._require_done("sensorStop", self.cmd("sensorStop", 3.0))
        health = self.stats()
        self._require_done("stats", health)
        if "active=0" not in health:
            raise RuntimeError(f"IWR6843 remained active after sensorStop: {health.strip()}")

    def close(self) -> None:
        """Release the serial port."""
        self.ser.close()

    def __enter__(self) -> "IWR6843Radar":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
