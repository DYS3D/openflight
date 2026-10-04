"""Club ids as the Pi logs them, in bag order, with display names."""

CLUBS: list[tuple[str, str]] = [
    ("driver", "Driver"),
    ("3-wood", "3 Wood"),
    ("5-wood", "5 Wood"),
    ("7-wood", "7 Wood"),
    ("9-wood", "9 Wood"),
    ("3-hybrid", "3 Hybrid"),
    ("4-hybrid", "4 Hybrid"),
    ("5-hybrid", "5 Hybrid"),
    ("7-hybrid", "7 Hybrid"),
    ("9-hybrid", "9 Hybrid"),
    ("2-iron", "2 Iron"),
    ("3-iron", "3 Iron"),
    ("4-iron", "4 Iron"),
    ("5-iron", "5 Iron"),
    ("6-iron", "6 Iron"),
    ("7-iron", "7 Iron"),
    ("8-iron", "8 Iron"),
    ("9-iron", "9 Iron"),
    ("pw", "Pitching Wedge"),
    ("gw", "Gap Wedge"),
    ("sw", "Sand Wedge"),
    ("lw", "Lob Wedge"),
]

_ORDER = {club_id: index for index, (club_id, _) in enumerate(CLUBS)}
_NAMES = dict(CLUBS)


def club_name(club_id: str) -> str:
    return _NAMES.get(club_id, club_id.replace("-", " ").title())


def club_sort_key(club_id: str) -> tuple[int, str]:
    return (_ORDER.get(club_id, len(CLUBS)), club_id)
