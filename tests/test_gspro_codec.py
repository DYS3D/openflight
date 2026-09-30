"""Tests for gspro.codec — OpenConnectV1 wire serialization + inbound parsing."""
import json

from openflight.clubs import ClubType
from openflight.gspro.codec import GSProCodec
from openflight.sim.types import PlayerUpdate, ResolvedShot, ShotAck, SimError


def _resolved(**kw) -> ResolvedShot:
    base = dict(
        shot_number=1, ball_speed_mph=140.0, vla=12.0, hla=1.5,
        total_spin_rpm=2500.0, spin_axis_deg=-3.0, back_spin_rpm=2496.6,
        side_spin_rpm=-130.8, carry_yards=255.0, club_path_deg=0.5,
        club=ClubType.DRIVER, club_speed_mph=110.0,
        provenance={},
    )
    base.update(kw)
    return ResolvedShot(**base)


def _build(codec, resolved) -> dict:
    return json.loads(codec.build_shot(resolved).decode("utf-8"))


# Byte-exact payloads captured from the codec before omit_unsupported_fields
# existed. The default configuration must keep producing exactly these.
_LEGACY_FULL = (
    b'{"DeviceID":"OpenFlight","Units":"Yards","ShotNumber":1,"APIversion":"1",'
    b'"BallData":{"Speed":140.0,"SpinAxis":-3.0,"TotalSpin":2500.0,"BackSpin":2497.0,'
    b'"SideSpin":-131.0,"HLA":1.5,"VLA":12.0,"CarryDistance":255.0},'
    b'"ClubData":{"Speed":110.0,"AngleOfAttack":0.0,"FaceToTarget":0.0,"Lie":0.0,'
    b'"Loft":0.0,"Path":0.5,"SpeedAtImpact":0.0,"VerticalFaceImpact":0.0,'
    b'"HorizontalFaceImpact":0.0,"ClosureRate":0.0},'
    b'"ShotDataOptions":{"ContainsBallData":true,"ContainsClubData":true,'
    b'"LaunchMonitorIsReady":true,"LaunchMonitorBallDetected":true,"IsHeartBeat":false}}'
)
_LEGACY_NO_CLUB_SPEED = _LEGACY_FULL.replace(
    b'"ClubData":{"Speed":110.0,', b'"ClubData":{"Speed":0.0,'
).replace(b'"ContainsClubData":true', b'"ContainsClubData":false')
_LEGACY_HEARTBEAT = (
    b'{"DeviceID":"OpenFlight","Units":"Yards","ShotNumber":0,"APIversion":"1",'
    b'"BallData":{"Speed":0.0,"SpinAxis":0.0,"TotalSpin":0.0,"BackSpin":0.0,"SideSpin":0.0,'
    b'"HLA":0.0,"VLA":0.0,"CarryDistance":0.0},'
    b'"ClubData":{"Speed":0.0,"AngleOfAttack":0.0,"FaceToTarget":0.0,"Lie":0.0,"Loft":0.0,'
    b'"Path":0.0,"SpeedAtImpact":0.0,"VerticalFaceImpact":0.0,"HorizontalFaceImpact":0.0,'
    b'"ClosureRate":0.0},'
    b'"ShotDataOptions":{"ContainsBallData":false,"ContainsClubData":false,'
    b'"LaunchMonitorIsReady":true,"LaunchMonitorBallDetected":false,"IsHeartBeat":true}}'
)


def test_default_codec_payload_is_byte_identical_to_legacy():
    codec = GSProCodec()
    assert codec.omit_unsupported_fields is False
    assert codec.build_shot(_resolved()) == _LEGACY_FULL
    assert codec.build_shot(_resolved(club_speed_mph=None)) == _LEGACY_NO_CLUB_SPEED
    assert codec.heartbeat_bytes() == _LEGACY_HEARTBEAT


def test_default_codec_ignores_measured_path_for_contains_club_data():
    # Legacy rule: ContainsClubData follows club speed alone; a measured path
    # without club speed is still sent as a value but not flagged.
    p = _build(GSProCodec(), _resolved(club_speed_mph=None, provenance={"club_path": "measured"}))
    assert p["ClubData"]["Path"] == 0.5
    assert p["ShotDataOptions"]["ContainsClubData"] is False


def test_omit_flag_sends_only_measured_club_fields():
    codec = GSProCodec(omit_unsupported_fields=True)
    p = _build(codec, _resolved(provenance={"club_path": "measured"}))
    assert p["ClubData"] == {"Speed": 110.0, "Path": 0.5}
    assert p["ShotDataOptions"]["ContainsClubData"] is True


def test_omit_flag_drops_estimated_path():
    p = _build(GSProCodec(omit_unsupported_fields=True), _resolved(provenance={"club_path": "estimated"}))
    assert p["ClubData"] == {"Speed": 110.0}
    assert p["ShotDataOptions"]["ContainsClubData"] is True


def test_omit_flag_measured_path_alone_flags_club_data():
    p = _build(
        GSProCodec(omit_unsupported_fields=True),
        _resolved(club_speed_mph=None, provenance={"club_path": "measured"}),
    )
    assert p["ClubData"] == {"Path": 0.5}
    assert p["ShotDataOptions"]["ContainsClubData"] is True


def test_omit_flag_no_club_measurements_sends_empty_club_data():
    p = _build(GSProCodec(omit_unsupported_fields=True), _resolved(club_speed_mph=None))
    assert p["ClubData"] == {}
    assert p["ShotDataOptions"]["ContainsClubData"] is False


def test_omit_flag_leaves_ball_data_options_and_heartbeat_unchanged():
    codec = GSProCodec(omit_unsupported_fields=True)
    p = _build(codec, _resolved(club_speed_mph=None))
    legacy = json.loads(_LEGACY_NO_CLUB_SPEED)
    assert p["BallData"] == legacy["BallData"]
    assert p["ShotDataOptions"] == legacy["ShotDataOptions"]
    assert {k: v for k, v in p.items() if k not in ("BallData", "ClubData", "ShotDataOptions")} == {
        "DeviceID": "OpenFlight", "Units": "Yards", "ShotNumber": 1, "APIversion": "1",
    }
    assert codec.heartbeat_bytes() == _LEGACY_HEARTBEAT


def test_build_shot_full_payload():
    p = _build(GSProCodec(), _resolved())
    assert p["DeviceID"] == "OpenFlight"
    assert p["Units"] == "Yards"
    assert p["ShotNumber"] == 1
    assert p["APIversion"] == "1" and isinstance(p["APIversion"], str)
    assert p["BallData"]["Speed"] == 140.0
    assert p["BallData"]["VLA"] == 12.0
    assert p["BallData"]["HLA"] == 1.5
    assert p["BallData"]["TotalSpin"] == 2500.0
    assert p["BallData"]["SpinAxis"] == -3.0
    assert p["BallData"]["CarryDistance"] == 255.0
    assert p["ClubData"]["Speed"] == 110.0
    assert p["ClubData"]["Path"] == 0.5
    assert p["ShotDataOptions"]["ContainsClubData"] is True


def test_build_shot_no_club_speed_drops_club_flag():
    p = _build(GSProCodec(), _resolved(club_speed_mph=None))
    assert p["ClubData"]["Speed"] == 0.0
    assert p["ShotDataOptions"]["ContainsClubData"] is False


def test_build_shot_options_flags():
    opts = _build(GSProCodec(), _resolved())["ShotDataOptions"]
    assert opts["ContainsBallData"] is True
    assert opts["LaunchMonitorIsReady"] is True
    assert opts["LaunchMonitorBallDetected"] is True
    assert opts["IsHeartBeat"] is False


def test_device_id_and_units_configurable():
    # The codec only labels the payload; sim.config is what restricts units to "Yards".
    p = _build(GSProCodec(device_id="Bay7", units="Meters"), _resolved())
    assert p["DeviceID"] == "Bay7"
    assert p["Units"] == "Meters"


def test_heartbeat_bytes_shape():
    obj = json.loads(GSProCodec().heartbeat_bytes().decode("utf-8"))
    assert obj["ShotDataOptions"]["IsHeartBeat"] is True
    assert obj["ShotDataOptions"]["LaunchMonitorIsReady"] is True
    assert obj["ShotDataOptions"]["LaunchMonitorBallDetected"] is False


def test_on_connect_bytes_none():
    assert GSProCodec().on_connect_bytes() is None


def test_parse_player_update_code_201():
    raw = json.dumps({"Code": 201, "Player": {"Handed": "LH", "Club": "I7"}}).encode()
    events = GSProCodec().parse_inbound(raw)
    assert len(events) == 1
    evt = events[0]
    assert isinstance(evt, PlayerUpdate)
    assert evt.handed == "LH"
    assert evt.club is ClubType.IRON_7


def test_parse_shot_ack_code_200():
    events = GSProCodec().parse_inbound(json.dumps({"Code": 200, "Message": "OK"}).encode())
    assert isinstance(events[0], ShotAck)
    assert events[0].ok is True


def test_parse_error_code_5xx():
    events = GSProCodec().parse_inbound(json.dumps({"Code": 501, "Message": "bad"}).encode())
    assert isinstance(events[0], SimError)
    assert events[0].message == "bad"


def test_parse_unknown_code_yields_nothing():
    assert GSProCodec().parse_inbound(json.dumps({"Code": 0}).encode()) == []


def test_fields_for_target_lists_logical_fields():
    fields = GSProCodec().fields_for_target()
    assert "ball_speed" in fields and "carry" in fields and "club_path" in fields


def test_name_defaults_to_gspro_and_is_configurable():
    assert GSProCodec().name == "gspro"
    # OGS reaches this same OpenConnect codec under its own target name.
    assert GSProCodec(name="opengolfsim").name == "opengolfsim"
