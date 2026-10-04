"""--spin-profile and --club-speed-scale (both off by default)."""

import argparse
import json
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.ballistics import CLUB_TYPICAL_SPIN_RPM, resolve_launch
from openflight.clubs import ClubType
from openflight.clubs.personal import (
    add_personal_calibration_args,
    fallback_spin_rpm,
    load_spin_profile,
    set_spin_profile,
    validate_club_speed_scale,
)
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState


@pytest.fixture(autouse=True)
def _tour_spin():
    set_spin_profile({})
    yield
    set_spin_profile({})


def _shot(**kwargs) -> Shot:
    defaults = dict(
        ball_speed_mph=100.0,
        timestamp=datetime.now(),
        club=ClubType.IRON_8,
        launch_angle_vertical=20.0,
    )
    defaults.update(kwargs)
    return Shot(**defaults)


def _write(tmp_path, content) -> str:
    path = tmp_path / "spin.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


def test_default_fallback_is_the_tour_average():
    assert fallback_spin_rpm(ClubType.IRON_8) == CLUB_TYPICAL_SPIN_RPM[ClubType.IRON_8]


def test_profile_replaces_fallback_spin_for_carry_and_simulator(tmp_path):
    set_spin_profile(load_spin_profile(_write(tmp_path, {"8-iron": 4570})))

    shot = _shot(spin_rpm=9000, spin_confidence=0.1)

    assert resolve_launch(shot).spin_rpm == 4570
    assert resolve_shot(shot, PlayerState()).total_spin_rpm == 4570
    # Clubs the profile leaves out keep the tour average.
    assert fallback_spin_rpm(ClubType.DRIVER) == CLUB_TYPICAL_SPIN_RPM[ClubType.DRIVER]


def test_profile_does_not_override_trusted_spin(tmp_path):
    set_spin_profile(load_spin_profile(_write(tmp_path, {"8-iron": 4570})))

    shot = _shot(spin_rpm=6100, spin_confidence=0.9, spin_source="measured")

    assert resolve_launch(shot).spin_rpm == 6100


@pytest.mark.parametrize(
    "content",
    [{"8-irn": 4570}, {"8-iron": 100}, {"8-iron": "4570"}, {"8-iron": True}, [4570]],
)
def test_bad_profiles_are_rejected(tmp_path, content):
    with pytest.raises(ValueError):
        load_spin_profile(_write(tmp_path, content))


def test_missing_profile_file_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_spin_profile(tmp_path / "missing.json")


def test_club_speed_scale_range():
    assert validate_club_speed_scale(1.07) == 1.07
    with pytest.raises(ValueError):
        validate_club_speed_scale(1.5)


def test_club_speed_scale_off_by_default(monkeypatch):
    monkeypatch.setattr(server_module, "club_speed_scale", 1.0)
    shot = _shot(club_speed_mph=72.0)

    server_module._apply_club_speed_scale(shot)

    assert shot.club_speed_mph == 72.0


def test_club_speed_scale_updates_club_speed_and_smash(monkeypatch):
    monkeypatch.setattr(server_module, "club_speed_scale", 1.07)
    shot = _shot(ball_speed_mph=98.0, club_speed_mph=72.0)

    server_module._apply_club_speed_scale(shot)

    assert shot.club_speed_mph == pytest.approx(77.04)
    assert shot.smash_factor == pytest.approx(98.0 / 77.04)


def test_flags_default_off():
    parser = argparse.ArgumentParser()
    add_personal_calibration_args(parser)

    args = parser.parse_args([])

    assert args.spin_profile is None
    assert args.club_speed_scale == 1.0
