"""Tests for src/openflight/gspro/messages.py."""
import json

import pytest

from openflight.gspro.messages import (
    BallData, ClubData, GSProResponse, ShotDataOptions, ShotPayload,
    parse_response, serialize_payload, build_heartbeat,
)


def test_serialize_minimum_shot():
    payload = ShotPayload(
        DeviceID="OpenFlight", Units="Yards", ShotNumber=1, APIversion="1",
        BallData=BallData(Speed=147.5, HLA=2.3, VLA=14.3, TotalSpin=2500.0,
                          SpinAxis=-3.0, BackSpin=2496.6, SideSpin=-130.8,
                          CarryDistance=240.0),
        ClubData=ClubData(Speed=110.0, Path=1.0),
        ShotDataOptions=ShotDataOptions(),
    )
    raw = serialize_payload(payload)
    obj = json.loads(raw)
    assert obj["DeviceID"] == "OpenFlight"
    assert obj["APIversion"] == "1"  # string, not int
    assert obj["BallData"]["Speed"] == 147.5
    assert obj["ShotDataOptions"]["ContainsBallData"] is True
    assert obj["ShotDataOptions"]["IsHeartBeat"] is False


def test_serialize_includes_all_required_keys():
    payload = ShotPayload(
        DeviceID="X", Units="Yards", ShotNumber=1, APIversion="1",
        BallData=BallData(), ClubData=ClubData(),
        ShotDataOptions=ShotDataOptions(),
    )
    obj = json.loads(serialize_payload(payload))
    for key in ("DeviceID", "Units", "ShotNumber", "APIversion",
                "BallData", "ClubData", "ShotDataOptions"):
        assert key in obj
    for key in ("Speed", "SpinAxis", "TotalSpin", "BackSpin", "SideSpin",
                "HLA", "VLA", "CarryDistance"):
        assert key in obj["BallData"]
    for key in ("Speed", "AngleOfAttack", "FaceToTarget", "Lie", "Loft",
                "Path", "SpeedAtImpact", "VerticalFaceImpact",
                "HorizontalFaceImpact", "ClosureRate"):
        assert key in obj["ClubData"]


def test_serialize_omits_none_keys_only():
    payload = ShotPayload(
        DeviceID="X", Units="Yards", ShotNumber=1, APIversion="1",
        BallData=BallData(),
        ClubData=ClubData(Speed=101.0, AngleOfAttack=None, Path=None, Loft=None),
        ShotDataOptions=ShotDataOptions(),
    )
    obj = json.loads(serialize_payload(payload))
    assert obj["ClubData"] == {
        "Speed": 101.0, "FaceToTarget": 0.0, "Lie": 0.0, "SpeedAtImpact": 0.0,
        "VerticalFaceImpact": 0.0, "HorizontalFaceImpact": 0.0, "ClosureRate": 0.0,
    }
    # Sibling objects with no None values are untouched.
    assert len(obj["BallData"]) == 8
    assert len(obj["ShotDataOptions"]) == 5


def test_measured_only_club_data_keeps_just_speed_and_path():
    club = ClubData.measured_only(speed=98.5, path=-1.2)
    assert club.Speed == 98.5 and club.Path == -1.2
    for name in ("AngleOfAttack", "FaceToTarget", "Lie", "Loft", "SpeedAtImpact",
                 "VerticalFaceImpact", "HorizontalFaceImpact", "ClosureRate"):
        assert getattr(club, name) is None
    assert ClubData.measured_only(speed=None, path=None) == ClubData(
        **{name: None for name in ClubData.__dataclass_fields__}
    )


def test_build_heartbeat():
    raw = build_heartbeat(device_id="OpenFlight", units="Yards", shot_number=42)
    obj = json.loads(raw)
    assert obj["DeviceID"] == "OpenFlight"
    assert obj["ShotNumber"] == 42
    assert obj["ShotDataOptions"]["IsHeartBeat"] is True
    assert obj["ShotDataOptions"]["ContainsBallData"] is False
    assert obj["ShotDataOptions"]["LaunchMonitorIsReady"] is True
    assert obj["BallData"]["Speed"] == 0.0


def test_parse_response_code_200():
    raw = b'{"Code": 200, "Message": "Shot received"}'
    resp = parse_response(raw)
    assert resp.Code == 200
    assert resp.Message == "Shot received"
    assert resp.Player is None


def test_parse_response_code_201_with_player():
    raw = b'{"Code": 201, "Message": "Player Info", "Player": {"Handed": "RH", "Club": "I7"}}'
    resp = parse_response(raw)
    assert resp.Code == 201
    assert resp.Player == {"Handed": "RH", "Club": "I7"}


def test_parse_response_code_5xx_error():
    raw = b'{"Code": 501, "Message": "Internal error"}'
    resp = parse_response(raw)
    assert resp.Code == 501
    assert resp.Player is None


def test_parse_response_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_response(b"not json")
