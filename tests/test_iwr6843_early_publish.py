"""With selective readback the radar launch reaches the UI before the full dump."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.launch_monitor import ClubType, Shot


def _measurement(angle_deg: float = 17.42):
    return SimpleNamespace(
        accepted=True,
        angle_deg=angle_deg,
        status="accepted",
        n_snapshots=20,
        n_frames=6,
        component_std_deg=1.1,
        to_dict=lambda: {"estimator": "lcmf_v1", "launch_angle_deg": angle_deg},
    )


def _shot() -> Shot:
    return Shot(
        ball_speed_mph=100.0,
        club_speed_mph=80.0,
        timestamp=datetime.now(),
        impact_timestamp=100.0,
        club=ClubType.IRON_9,
    )


@pytest.fixture(name="emitted")
def _emitted(monkeypatch):
    events = []
    session = SimpleNamespace(stats={"shots_detected": 2}, log_iwr6843_capture=lambda **_kw: None)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: session)
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, payload: events.append((event, payload))
    )
    return events


def _runtime(seen: dict):
    capture = SimpleNamespace(
        trigger_timestamp=100.01,
        path=Path("/tmp/test.l3dump"),
        raw=b"raw",
        dump_duration_s=7.5,
        error=None,
        valid=True,
        sequence=1,
    )
    result = SimpleNamespace(capture=capture, measurement=_measurement())

    def process_shot(**kwargs):
        seen.update(kwargs)
        callback = kwargs.get("on_ball_measurement")
        if callback is not None:
            callback(result)
        return result

    return SimpleNamespace(process_shot=process_shot)


def test_launch_is_published_as_a_pending_update_before_the_shot_is_final(monkeypatch, emitted):
    shot = _shot()
    seen: dict = {}
    monkeypatch.setattr(server_module, "iwr6843_runtime", _runtime(seen))
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"selective_readback": True})

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    event, payload = emitted[0]
    assert event == "shot_update"
    assert payload["pending"] == {"iwr6843": True}
    assert payload["shot"]["launch_angle_vertical"] == pytest.approx(17.4, abs=0.05)
    assert payload["shot"]["estimated_carry_yards"] > 0
    assert shot.launch_angle_vertical == pytest.approx(17.42)
    assert shot.ball_speed_mph == 100.0, "the preview is a copy; the shot is finalized once"


def test_no_early_update_without_selective_readback(monkeypatch, emitted):
    seen: dict = {}
    monkeypatch.setattr(server_module, "iwr6843_runtime", _runtime(seen))
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"enabled": True})

    server_module._process_iwr6843_angle(_shot())  # pylint: disable=protected-access

    assert "on_ball_measurement" not in seen
    assert [event for event, _payload in emitted] == ["trigger_diagnostic_update"]


def test_a_failing_early_publish_does_not_lose_the_shot(monkeypatch, emitted):
    shot = _shot()
    monkeypatch.setattr(server_module, "iwr6843_runtime", _runtime({}))
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"selective_readback": True})
    monkeypatch.setattr(
        server_module, "_compute_shot_flight", lambda _shot: (_ for _ in ()).throw(ValueError("x"))
    )

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical == pytest.approx(17.42)
    assert [event for event, _payload in emitted] == ["trigger_diagnostic_update"]


def test_launch_is_published_ahead_of_the_enrichment_queue(monkeypatch, emitted):
    shot = _shot()
    result = SimpleNamespace(capture=None, measurement=_measurement())
    asked = {}

    def publish_early_ball_measurement(**kwargs):
        asked.update(kwargs)
        kwargs["on_ball_measurement"](result)

    runtime = SimpleNamespace(publish_early_ball_measurement=publish_early_ball_measurement)
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)

    server_module._publish_iwr6843_launch_ahead_of_queue(shot)  # pylint: disable=protected-access

    assert asked["impact_timestamp"] == 100.0 and asked["club"] == ClubType.IRON_9.value
    assert [event for event, _payload in emitted] == ["shot_update"]
    assert emitted[0][1]["shot"]["launch_angle_vertical"] == pytest.approx(17.4, abs=0.05)
    assert shot.launch_angle_vertical is None, "the queued shot is left for its own pipeline"


def test_a_failing_ahead_of_queue_publish_is_contained(monkeypatch, emitted):
    def boom(**_kwargs):
        raise RuntimeError("radar gone")

    monkeypatch.setattr(
        server_module, "iwr6843_runtime", SimpleNamespace(publish_early_ball_measurement=boom)
    )

    server_module._publish_iwr6843_launch_ahead_of_queue(_shot())  # pylint: disable=protected-access

    assert not emitted
