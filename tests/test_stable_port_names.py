"""Radar auto-detection prefers the udev stable names from scripts/install.sh."""

from unittest.mock import patch

from openflight.iwr6843 import driver
from openflight.ops243 import OPS243Radar


class _FakePort:
    def __init__(self, device, vid=None):
        self.device = device
        self.vid = vid
        self.description = None


def _fake_fs(links: dict[str, str]):
    """os.path.exists/realpath stand-ins for a /dev with the given symlinks."""

    def exists(path):
        return path in links

    def realpath(path):
        return links.get(path, path)

    return exists, realpath


class TestOps243:
    def test_stable_name_is_listed_first_and_replaces_its_target(self):
        exists, realpath = _fake_fs({OPS243Radar.STABLE_PORT: "/dev/ttyACM1"})
        comports = [_FakePort("/dev/ttyACM0", 0x0483), _FakePort("/dev/ttyACM1", 0x0483)]
        with (
            patch("openflight.ops243.serial.tools.list_ports.comports", return_value=comports),
            patch("openflight.ops243.os.path.exists", side_effect=exists),
            patch("openflight.ops243.os.path.realpath", side_effect=realpath),
        ):
            ports = OPS243Radar.find_radar_ports()

        assert ports == ["/dev/openflight-ops243", "/dev/ttyACM0"]

    def test_without_the_symlink_detection_is_unchanged(self):
        exists, realpath = _fake_fs({})
        comports = [_FakePort("/dev/ttyACM0", 0x0483)]
        with (
            patch("openflight.ops243.serial.tools.list_ports.comports", return_value=comports),
            patch("openflight.ops243.os.path.exists", side_effect=exists),
            patch("openflight.ops243.os.path.realpath", side_effect=realpath),
        ):
            assert OPS243Radar.find_radar_ports() == ["/dev/ttyACM0"]

    def test_connect_opens_the_stable_name(self):
        radar = OPS243Radar()
        with (
            patch.object(OPS243Radar, "find_radar_ports", return_value=["/dev/openflight-ops243"]),
            patch.object(OPS243Radar, "_open_serial"),
            patch.object(OPS243Radar, "_should_negotiate_baud", return_value=False),
            patch.object(OPS243Radar, "_log_transport"),
            patch.object(OPS243Radar, "_drain_serial"),
        ):
            radar.connect()
        assert radar.port == "/dev/openflight-ops243"


class TestIwr6843:
    def _candidates(self, links, globbed):
        exists, realpath = _fake_fs(links)

        def fake_glob(pattern):
            return globbed if pattern == "/dev/ttyUSB*" else []

        with (
            patch.object(driver.glob, "glob", side_effect=fake_glob),
            patch.object(driver.os.path, "exists", side_effect=exists),
            patch.object(driver.os.path, "realpath", side_effect=realpath),
        ):
            return driver._candidate_ports()  # pylint: disable=protected-access

    def test_stable_cli_name_is_probed_first(self):
        candidates = self._candidates(
            {driver.STABLE_CLI_PORT: "/dev/ttyUSB1"}, ["/dev/ttyUSB0", "/dev/ttyUSB1"]
        )
        assert candidates == ["/dev/openflight-iwr-cli", "/dev/ttyUSB0"]

    def test_without_the_symlink_globs_are_unchanged(self):
        assert self._candidates({}, ["/dev/ttyUSB1", "/dev/ttyUSB0"]) == [
            "/dev/ttyUSB0",
            "/dev/ttyUSB1",
        ]

    def test_detect_port_returns_the_stable_name_when_it_answers(self):
        class _Answering:
            def reset_input_buffer(self):
                pass

            def write(self, _data):
                pass

            def read(self, _n):
                return b"sensorStart"

            def close(self):
                pass

        opened = []

        def fake_open(port, _baud):
            opened.append(port)
            return _Answering()

        with (
            patch.object(driver, "_candidate_ports", return_value=["/dev/openflight-iwr-cli"]),
            patch.object(driver, "open_port", side_effect=fake_open),
        ):
            assert driver.IWR6843Radar.detect_port() == "/dev/openflight-iwr-cli"
        assert opened == ["/dev/openflight-iwr-cli"]
