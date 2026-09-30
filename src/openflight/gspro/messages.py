"""OpenConnectV1 JSON schema (https://gsprogolf.com/GSProConnectV1.html).

The spec annotates its example shot message field by field. Verbatim, it marks:

- required: ``DeviceID``, ``ShotNumber``, ``APIversion``; ``BallData.Speed``,
  ``SpinAxis``, ``TotalSpin``, ``HLA``, ``VLA``; ``ShotDataOptions.ContainsBallData``,
  ``ContainsClubData``.
- ``BallData.BackSpin`` / ``SideSpin``: "only required if total spin is not sent".
- optional: ``BallData.CarryDistance``.
- "not required": ``ShotDataOptions.LaunchMonitorIsReady``,
  ``LaunchMonitorBallDetected``, ``IsHeartBeat``.
- ``Units``: "default yards" — no other value is documented, and the spec never
  states which units the numeric fields are in.
- Every ``ClubData`` key is listed with the value 0.0 and *no* required/optional
  annotation; ``ContainsClubData`` is the only stated signal for club data.

OpenFlight always sends every key with 0.0 placeholders (the spec's own
example). ``serialize_payload`` drops keys set to ``None`` so a codec can
instead omit the ``ClubData`` fields it did not measure.
"""

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Optional


@dataclass
class BallData:
    Speed: float = 0.0
    SpinAxis: float = 0.0
    TotalSpin: float = 0.0
    BackSpin: float = 0.0
    SideSpin: float = 0.0
    HLA: float = 0.0
    VLA: float = 0.0
    CarryDistance: float = 0.0


@dataclass
class ClubData:
    """Club fields; OpenFlight measures only ``Speed`` and ``Path``.

    ``None`` omits the key from the wire. Defaults stay 0.0 so the legacy
    payload (every key present) is unchanged unless a codec opts out.
    """

    Speed: Optional[float] = 0.0
    AngleOfAttack: Optional[float] = 0.0
    FaceToTarget: Optional[float] = 0.0
    Lie: Optional[float] = 0.0
    Loft: Optional[float] = 0.0
    Path: Optional[float] = 0.0
    SpeedAtImpact: Optional[float] = 0.0
    VerticalFaceImpact: Optional[float] = 0.0
    HorizontalFaceImpact: Optional[float] = 0.0
    ClosureRate: Optional[float] = 0.0

    @classmethod
    def measured_only(cls, speed: Optional[float], path: Optional[float]) -> "ClubData":
        """ClubData carrying only the measured fields; everything else is omitted."""
        values = {f.name: None for f in fields(cls)}
        values["Speed"] = speed
        values["Path"] = path
        return cls(**values)


@dataclass
class ShotDataOptions:
    ContainsBallData: bool = True
    ContainsClubData: bool = False
    LaunchMonitorIsReady: bool = True
    LaunchMonitorBallDetected: bool = True
    IsHeartBeat: bool = False


@dataclass
class ShotPayload:
    DeviceID: str
    Units: str
    ShotNumber: int
    APIversion: str  # string "1", not int (per spec)
    BallData: BallData = field(default_factory=BallData)
    ClubData: ClubData = field(default_factory=ClubData)
    ShotDataOptions: ShotDataOptions = field(default_factory=ShotDataOptions)


@dataclass
class GSProResponse:
    Code: int
    Message: str = ""
    Player: Optional[dict] = None


def _drop_none(obj):
    if isinstance(obj, dict):
        return {k: _drop_none(v) for k, v in obj.items() if v is not None}
    return obj


def serialize_payload(payload: ShotPayload) -> bytes:
    """Encode a payload; keys whose value is ``None`` are left off the wire."""
    return json.dumps(_drop_none(asdict(payload)), separators=(",", ":")).encode("utf-8")


def build_heartbeat(device_id: str, units: str, shot_number: int) -> bytes:
    payload = ShotPayload(
        DeviceID=device_id,
        Units=units,
        ShotNumber=shot_number,
        APIversion="1",
        ShotDataOptions=ShotDataOptions(
            ContainsBallData=False,
            ContainsClubData=False,
            LaunchMonitorIsReady=True,
            LaunchMonitorBallDetected=False,
            IsHeartBeat=True,
        ),
    )
    return serialize_payload(payload)


def parse_response(raw: bytes) -> GSProResponse:
    """Parse a GSPro reply. Raises ValueError on malformed JSON."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Malformed GSPro response: {e}") from e
    return GSProResponse(
        Code=int(obj.get("Code", 0)),
        Message=str(obj.get("Message", "")),
        Player=obj.get("Player"),
    )
