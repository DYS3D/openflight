"""GSPro OpenConnectV1 codec — ResolvedShot <-> wire bytes.

Wraps the OpenConnectV1 dataclasses in messages.py and the GSPro club map in
state.py behind the protocol-neutral Codec interface the transport expects.
Spec: https://gsprogolf.com/GSProConnectV1.html
"""

from typing import List, Optional, Tuple

from openflight.gspro.messages import (
    BallData,
    ClubData,
    ShotDataOptions,
    ShotPayload,
    build_heartbeat,
    parse_response,
    serialize_payload,
)
from openflight.gspro.state import gspro_code_to_club
from openflight.sim.types import (
    InboundEvent,
    PlayerUpdate,
    ResolvedShot,
    ShotAck,
    SimError,
)

# Logical fields GSPro actually transmits (drives the UI provenance badges).
_GSPRO_FIELDS = [
    "ball_speed",
    "vla",
    "hla",
    "total_spin",
    "spin_axis",
    "back_spin",
    "side_spin",
    "carry",
    "club_speed",
    "club_path",
]


class GSProCodec:
    """OpenConnect V1 wire format (used by GSPro and by OpenGolfSim's
    OpenConnect plugin, and by PAR-TEE). ``name`` is the connector/display
    target — "gspro" for GSPro, "opengolfsim" when this codec drives OGS over its
    OpenConnect plugin, "partee" when it drives the PAR-TEE app.

    ``units`` is passed through as the payload's ``Units`` label; values are
    always yards/mph, so the config loader only admits "Yards" (the spec's
    "default yards" — no other value is documented).

    ``omit_unsupported_fields`` (default off) changes only ``ClubData`` and
    ``ContainsClubData``: off sends every ClubData key, unmeasured ones as 0.0,
    and flags club data present only when club speed is; on sends only the
    measured club fields (speed and/or a measured path, per
    ``ResolvedShot.provenance``) and flags club data present when any of them
    is. ``BallData`` is untouched either way: every key there is spec-required
    except ``CarryDistance``, which OpenFlight sends on purpose (see
    ``gspro.messages`` for the spec's required/optional list).
    """

    def __init__(
        self,
        device_id: str = "OpenFlight",
        units: str = "Yards",
        name: str = "gspro",
        omit_unsupported_fields: bool = False,
    ):
        self.name = name
        self.device_id = device_id
        self.units = units
        self.omit_unsupported_fields = omit_unsupported_fields

    def _club_data(self, resolved: ResolvedShot) -> Tuple[ClubData, bool]:
        """(ClubData, ContainsClubData) for the shot, per ``omit_unsupported_fields``."""
        has_club_speed = resolved.club_speed_mph is not None
        if not self.omit_unsupported_fields:
            club = ClubData(
                Speed=round(resolved.club_speed_mph, 1) if has_club_speed else 0.0,
                Path=round(resolved.club_path_deg, 1),
            )
            return club, has_club_speed
        speed = round(resolved.club_speed_mph, 1) if has_club_speed else None
        path_measured = resolved.provenance.get("club_path") == "measured"
        path = round(resolved.club_path_deg, 1) if path_measured else None
        return ClubData.measured_only(speed, path), has_club_speed or path_measured

    def build_shot(self, resolved: ResolvedShot) -> bytes:
        club_data, contains_club_data = self._club_data(resolved)
        payload = ShotPayload(
            DeviceID=self.device_id,
            Units=self.units,
            ShotNumber=resolved.shot_number,
            APIversion="1",
            BallData=BallData(
                Speed=round(resolved.ball_speed_mph, 1),
                SpinAxis=round(resolved.spin_axis_deg, 1),
                TotalSpin=round(resolved.total_spin_rpm, 0),
                BackSpin=round(resolved.back_spin_rpm, 0),
                SideSpin=round(resolved.side_spin_rpm, 0),
                HLA=round(resolved.hla, 1),
                VLA=round(resolved.vla, 1),
                CarryDistance=round(resolved.carry_yards, 1),
            ),
            ClubData=club_data,
            ShotDataOptions=ShotDataOptions(
                ContainsBallData=True,
                ContainsClubData=contains_club_data,
                LaunchMonitorIsReady=True,
                LaunchMonitorBallDetected=True,
                IsHeartBeat=False,
            ),
        )
        return serialize_payload(payload)

    def parse_inbound(self, frame: bytes) -> List[InboundEvent]:
        resp = parse_response(frame)  # raises ValueError on malformed JSON
        if resp.Code == 201 and resp.Player:
            return [
                PlayerUpdate(
                    handed=str(resp.Player["Handed"]) if "Handed" in resp.Player else None,
                    club=gspro_code_to_club(str(resp.Player["Club"]))
                    if "Club" in resp.Player
                    else None,
                )
            ]
        if resp.Code >= 500:
            return [SimError(message=resp.Message)]
        if resp.Code == 200:
            return [ShotAck(ok=True, message=resp.Message)]
        return []

    def heartbeat_bytes(self) -> Optional[bytes]:
        return build_heartbeat(self.device_id, self.units, shot_number=0)

    def on_connect_bytes(self) -> Optional[bytes]:
        return None

    def fields_for_target(self) -> List[str]:
        return list(_GSPRO_FIELDS)
