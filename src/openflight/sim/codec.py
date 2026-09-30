"""SimConnector: pairs a codec with a TCP transport, and the codec registry.

A connector is the unit the server fans shots out to. Adding a new simulator
means writing a codec and registering it here — nothing else in the server or
transport changes.
"""

import logging
from typing import Callable, List, Optional, Tuple

from openflight.sim.config import ConnectorConfig
from openflight.sim.transport import DEFAULT_BACKOFF, Codec, TcpSimClient
from openflight.sim.types import (
    ConnectionState,
    InboundEvent,
    ResolvedShot,
    ShotAck,
    SimError,
    StatusEvent,
)

logger = logging.getLogger(__name__)


class SimConnector:
    """One simulator endpoint: codec + transport + per-target callback routing.

    Callbacks are invoked as ``on_status(target, StatusEvent)`` and
    ``on_inbound(target, InboundEvent)`` so the server can multiplex several
    connectors through a single pair of handlers.

    A ``SimError`` leaves the socket up, so the transport emits no status for
    it; the server shows it as an "error" badge that nothing would otherwise
    clear. The connector therefore re-emits its real connection state through
    ``on_status`` on the first accepted shot after an error.
    """

    def __init__(
        self,
        codec: Codec,
        host: str,
        port: int,
        heartbeat_interval_s: float = 5.0,
        on_status: Optional[Callable[[str, StatusEvent], None]] = None,
        on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
        backoff_seconds=DEFAULT_BACKOFF,
    ):
        self.codec = codec
        self.name = codec.name
        self.host = host
        self.port = port
        self._on_status_user = on_status
        self._on_inbound_user = on_inbound
        self._sim_error_pending = False
        self._client = TcpSimClient(
            host=host,
            port=port,
            codec=codec,
            heartbeat_interval_s=heartbeat_interval_s,
            name=codec.name,
            on_inbound=self._handle_inbound,
            on_status=self._handle_status,
            backoff_seconds=backoff_seconds,
        )

    def _handle_status(self, event: StatusEvent) -> None:
        if event.state is not ConnectionState.CONNECTED:
            # Any real transition already replaces the error badge.
            self._sim_error_pending = False
        if self._on_status_user is not None:
            self._on_status_user(self.name, event)

    def _handle_inbound(self, event: InboundEvent) -> None:
        if self._on_inbound_user is not None:
            self._on_inbound_user(self.name, event)
        if isinstance(event, SimError):
            self._sim_error_pending = True
        elif isinstance(event, ShotAck) and event.ok and self._sim_error_pending:
            self._sim_error_pending = False
            self._handle_status(
                StatusEvent(
                    state=self._client.state,
                    target=self.name,
                    host=self.host,
                    port=self.port,
                    message="shot accepted after error",
                )
            )

    def start(self) -> None:
        self._client.start()

    def stop(self) -> None:
        self._client.stop()

    def is_connected(self) -> bool:
        return self._client.is_connected()

    @property
    def state(self):
        return self._client.state

    def send_shot(self, resolved: ResolvedShot) -> None:
        """Serialize and send a resolved shot. Raises OSError if the socket fails."""
        self._client.send_raw(self.codec.build_shot(resolved))


ReadyState = Callable[[], Tuple[bool, bool]]


def _codec_for(cfg: "ConnectorConfig", ready_state: Optional[ReadyState] = None) -> Codec:
    """Instantiate the codec for a connector type. Import is local to avoid an
    import cycle (the codec imports sim.types/resolver).

    The OpenConnect V1 codec is shared: GSPro uses it on 921; OpenGolfSim uses it
    on its Developer API (3111, which speaks OpenConnect), named "opengolfsim" so
    the UI/logs/config say OpenGolfSim rather than GSPro; PAR-TEE listens for it
    on the phone's Wi-Fi address (921), named "partee" for the same reason.
    """
    if cfg.type not in ("gspro", "opengolfsim", "partee"):
        raise ValueError(f"unknown simulator connector type: {cfg.type!r}")
    from openflight.gspro.codec import GSProCodec  # pylint: disable=import-outside-toplevel

    return GSProCodec(
        device_id=cfg.device_id,
        units=cfg.units,
        name=cfg.type,
        omit_unsupported_fields=cfg.omit_unsupported_fields,
        ready_state=ready_state,
    )


def build_connector(
    cfg: "ConnectorConfig",
    on_status: Optional[Callable[[str, StatusEvent], None]] = None,
    on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
    backoff_seconds=DEFAULT_BACKOFF,
    ready_state: Optional[ReadyState] = None,
) -> SimConnector:
    """Build a single connector from a resolved ConnectorConfig.

    ``ready_state`` (default None) makes heartbeats report live
    ``LaunchMonitorIsReady`` / ``LaunchMonitorBallDetected`` flags; see
    ``GSProCodec``.
    """
    codec = _codec_for(cfg, ready_state=ready_state)
    return SimConnector(
        codec=codec,
        host=cfg.host,
        port=cfg.port,
        heartbeat_interval_s=cfg.heartbeat_interval_s,
        on_status=on_status,
        on_inbound=on_inbound,
        backoff_seconds=backoff_seconds,
    )


def build_connectors(
    cfgs: List["ConnectorConfig"],
    on_status: Optional[Callable[[str, StatusEvent], None]] = None,
    on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
    backoff_seconds=DEFAULT_BACKOFF,
    ready_state: Optional[ReadyState] = None,
) -> List[SimConnector]:
    """Build every connector in a resolved config list."""
    return [
        build_connector(
            cfg,
            on_status=on_status,
            on_inbound=on_inbound,
            backoff_seconds=backoff_seconds,
            ready_state=ready_state,
        )
        for cfg in cfgs
    ]
