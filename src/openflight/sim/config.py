"""Simulator-connector configuration: config/sim.json.

A single file lists every connector; the server streams to all that are
``enabled`` — but only when the sim feature is turned on at launch (``--sim``).

A connector's ``type`` is the *product*: gspro (OpenConnect V1 on 921),
opengolfsim (reached via its Developer API on 3111, which speaks OpenConnect),
or partee (the PAR-TEE phone app, which listens for OpenConnect on 921).
All ride the shared OpenConnect codec; they differ only in name + default port.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/sim.json")

KNOWN_TYPES: Tuple[str, ...] = ("gspro", "opengolfsim", "partee")

# OpenConnect V1 documents Units only as "default yards" and never says what a
# metric payload would carry, and OpenFlight measures in yards/mph without
# converting — so "Yards" is the only label that is honest on the wire.
SUPPORTED_UNITS: Tuple[str, ...] = ("Yards",)

# Per-type defaults applied when a field is absent from the file.
_DEFAULTS: Dict[str, dict] = {
    "gspro": {
        "port": 921,
        "units": "Yards",
        "device_id": "OpenFlight",
        "heartbeat_interval_s": 5.0,
        "omit_unsupported_fields": False,
    },
    "opengolfsim": {
        "port": 3111,
        "units": "Yards",
        "device_id": "OpenFlight",
        "heartbeat_interval_s": 5.0,
        "omit_unsupported_fields": False,
    },
    "partee": {
        "port": 921,
        "units": "Yards",
        "device_id": "OpenFlight",
        "heartbeat_interval_s": 5.0,
        "omit_unsupported_fields": False,
    },
}


@dataclass
class ConnectorConfig:
    """One resolved simulator endpoint.

    ``omit_unsupported_fields`` leaves unmeasured ClubData keys off the wire
    instead of sending 0.0 placeholders (see ``gspro.codec.GSProCodec``).
    """

    type: str
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    units: str = "Yards"
    device_id: str = "OpenFlight"
    heartbeat_interval_s: float = 5.0
    omit_unsupported_fields: bool = False


def _parse_bool(value: object) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
        raise ValueError(f"not a boolean: {value!r}")
    return bool(value)


def _with_defaults(connector_type: str, data: dict) -> ConnectorConfig:
    base = dict(_DEFAULTS[connector_type])
    base.update(data)
    return ConnectorConfig(
        type=connector_type,
        enabled=_parse_bool(base.get("enabled", False)),
        host=str(base.get("host", "127.0.0.1")),
        port=int(base["port"]),
        units=str(base.get("units", "Yards")),
        device_id=str(base.get("device_id", "OpenFlight")),
        heartbeat_interval_s=float(base.get("heartbeat_interval_s", 5.0)),
        omit_unsupported_fields=_parse_bool(base.get("omit_unsupported_fields", False)),
    )


def _check_units(cfg: ConnectorConfig, config_path: Path) -> None:
    if cfg.units in SUPPORTED_UNITS:
        return
    raise ValueError(
        f"unsupported units {cfg.units!r} for {cfg.type} connector in {config_path}: "
        "OpenFlight sends yards/mph and does not convert, and OpenConnect V1 documents "
        'only "Yards" (https://gsprogolf.com/GSProConnectV1.html); set "units": "Yards"'
    )


def load_sim_config(config_path: Path = DEFAULT_CONFIG_PATH) -> List[ConnectorConfig]:
    """Resolve the enabled connector configs from the file (only enabled ones).

    Gating the whole feature on/off is the caller's job (the ``--sim`` flag);
    this just reads which connectors the file enables.

    Sim is opt-in and the core shot pipeline doesn't depend on it, so an
    unreadable/syntactically-broken file degrades to "no connectors" with a
    warning rather than crashing startup, and a single malformed connector entry
    is skipped so it can't take the others down with it. An *unknown connector
    type* or *unsupported units* still raises — those are real misconfigurations
    worth surfacing loudly.
    """
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[sim] ignoring unreadable %s: %s", config_path, e)
        return []
    if not isinstance(data, dict):
        logger.warning(
            "[sim] ignoring %s: expected a JSON object, got %s",
            config_path,
            type(data).__name__,
        )
        return []
    cfgs: List[ConnectorConfig] = []
    for entry in data.get("connectors", []):
        if not isinstance(entry, dict):
            logger.warning(
                "[sim] skipping non-object connector entry in %s: %r", config_path, entry
            )
            continue
        ctype = entry.get("type")
        if ctype not in KNOWN_TYPES:
            raise ValueError(f"unknown simulator type in {config_path}: {ctype!r}")
        try:
            cfg = _with_defaults(ctype, entry)
        except (ValueError, TypeError, KeyError) as e:
            logger.warning("[sim] skipping malformed %s connector in %s: %s", ctype, config_path, e)
            continue
        _check_units(cfg, config_path)
        if cfg.enabled:
            cfgs.append(cfg)
    return cfgs
