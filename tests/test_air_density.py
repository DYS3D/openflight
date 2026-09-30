"""Tests for site-dependent air density in the ballistic model."""

import math
import sys

import pytest

from openflight import server as server_module
from openflight.ballistics import (
    AIR_DENSITY_STD,
    LaunchConditions,
    air_density_kg_m3,
    simulate,
)


def _driver():
    return LaunchConditions(
        ball_speed_mph=160.0,
        launch_angle_v=12.0,
        launch_angle_h=0.0,
        spin_rpm=2600.0,
        spin_axis_deg=0.0,
        spin_source="measured",
    )


class TestAirDensity:
    def test_defaults_reproduce_isa_sea_level(self):
        assert math.isclose(air_density_kg_m3(), AIR_DENSITY_STD, rel_tol=1e-3)

    def test_denver_is_about_seventeen_percent_thinner(self):
        denver = air_density_kg_m3(altitude_m=1609.0, temperature_c=15.0)
        assert 0.80 < denver / AIR_DENSITY_STD < 0.86

    def test_hot_air_is_thinner_than_cold_air(self):
        assert air_density_kg_m3(temperature_c=35.0) < air_density_kg_m3(temperature_c=0.0)

    def test_humid_air_is_slightly_thinner_than_dry_air(self):
        dry = air_density_kg_m3(temperature_c=30.0, relative_humidity=0.0)
        humid = air_density_kg_m3(temperature_c=30.0, relative_humidity=1.0)
        assert humid < dry
        assert dry - humid < 0.03

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"altitude_m": 9000.0},
            {"altitude_m": -1000.0},
            {"temperature_c": 80.0},
            {"temperature_c": -60.0},
            {"relative_humidity": 1.5},
            {"relative_humidity": -0.1},
            {"altitude_m": float("nan")},
            {"temperature_c": float("inf")},
        ],
    )
    def test_implausible_inputs_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            air_density_kg_m3(**kwargs)


class TestCarryVersusDensity:
    def test_thin_air_carries_farther(self):
        sea_level = simulate(_driver()).carry_yards
        denver = simulate(_driver(), air_density=air_density_kg_m3(altitude_m=1609.0)).carry_yards
        assert denver > sea_level
        assert 0.03 < (denver - sea_level) / sea_level < 0.15

    def test_cold_air_carries_shorter(self):
        mild = simulate(_driver(), air_density=air_density_kg_m3(temperature_c=15.0))
        cold = simulate(_driver(), air_density=air_density_kg_m3(temperature_c=0.0))
        assert cold.carry_yards < mild.carry_yards


class TestServerFlags:
    def _run_main(self, monkeypatch, *extra):
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging", *extra])
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *_a, **_kw: None)
        monkeypatch.setattr(server_module, "air_density", AIR_DENSITY_STD)
        server_module.main()

    def test_defaults_keep_standard_density(self, monkeypatch):
        self._run_main(monkeypatch)
        assert server_module.air_density == AIR_DENSITY_STD

    def test_site_flags_set_density(self, monkeypatch):
        self._run_main(monkeypatch, "--altitude-ft", "5280", "--temperature-f", "85")
        expected = air_density_kg_m3(altitude_m=5280 * 0.3048, temperature_c=(85 - 32) * 5 / 9)
        assert math.isclose(server_module.air_density, expected, rel_tol=1e-9)

    def test_bad_site_flags_fail_at_the_cli(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, "--altitude-ft", "40000")
