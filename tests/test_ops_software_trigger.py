"""--ops-software-trigger: relay the BCM17 sound edge to the OPS243 as S!."""

import sys

import pytest

from openflight import server
from openflight.iwr6843.monitor import IWR6843CaptureMonitor


class FakeRadar:
    def __init__(self, waiting=True):
        self.waiting = waiting
        self.sent = 0

    def send_software_trigger(self):
        if not self.waiting:
            return False
        self.sent += 1
        return True


class FakeCaptureMonitor:
    def __init__(self):
        self.observers = []

    def add_trigger_observer(self, observer):
        self.observers.append(observer)

    def edge(self, timestamp=1.0):
        for observer in self.observers:
            observer(timestamp)


@pytest.mark.parametrize(
    "argv",
    [
        ["--ops-software-trigger"],
        ["--ops-software-trigger", "--iwr6843", "--trigger", "speed"],
    ],
)
def test_flag_requires_iwr6843_and_sound_trigger(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["openflight-server", *argv])

    with pytest.raises(SystemExit, match="2"):
        server.main()


def test_flag_is_off_by_default():
    assert server.ops_software_trigger_enabled is False


def test_attached_relay_sends_s_bang_on_each_edge():
    capture_monitor = FakeCaptureMonitor()
    radar = FakeRadar()

    server._attach_ops_software_trigger(capture_monitor, radar)
    capture_monitor.edge()

    assert radar.sent == 1


def test_relay_failure_never_reaches_the_gpio_callback():
    class BrokenRadar:
        def send_software_trigger(self):
            raise OSError("port gone")

    capture_monitor = FakeCaptureMonitor()
    server._attach_ops_software_trigger(capture_monitor, BrokenRadar())

    capture_monitor.edge()  # must not raise


def test_iwr_monitor_notifies_added_observers():
    monitor = IWR6843CaptureMonitor.__new__(IWR6843CaptureMonitor)
    monitor._trigger_observers = []
    seen = []

    monitor.add_trigger_observer(seen.append)
    for observer in monitor._trigger_observers:
        observer(2.5)

    assert seen == [2.5]
