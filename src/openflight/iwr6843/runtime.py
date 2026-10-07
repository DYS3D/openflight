"""Runtime boundary joining TI capture to the frozen LCMF estimator."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.club import ClubPathResult, ClubWindowPolicy, estimate_club_path
from openflight.iwr6843.estimator_worker import EstimatorWorker, EstimatorWorkerError
from openflight.iwr6843.lcmf import (
    PRODUCTION_ANGLE_STEP_DEG,
    LCMFResult,
    PreparedLCMFCapture,
    estimate_lcmf_v1,
    prepare_lcmf_capture,
)
from openflight.iwr6843.monitor import IWR6843Capture, IWR6843CaptureMonitor
from openflight.iwr6843.readback import Readback
from openflight.iwr6843.recovery import (
    RecoveryCandidate,
    RecoveryPrior,
    find_recovery_candidates,
    select_recovery_candidate,
)

logger = logging.getLogger(__name__)

# The ball estimate's measured tdm_sign_used takes priority; this only
# resolves the TDM sign for the club-path fallback when it is unavailable.
# "auto" has no fixed sign of its own, so it defaults to positive, same as
# this module's own tdm_sign_policy default.
_TDM_SIGN_BY_POLICY = {"positive": 1, "negative": -1, "auto": 1}

# TrackMan holdout tracks centered almost exactly on OPS speed. A larger
# disagreement is unusual enough to justify a bounded alternate-track pass,
# but not enough by itself to choose an angle.
OPS_TRACK_SPEED_TOLERANCE_FRAC = 0.15
OPS_GUIDED_MAX_CANDIDATES = 3
OPS_GUIDED_MIN_LAUNCH_DEG = 2.0


# The worker decodes each capture itself: the prepared products are ~8x the raw
# dump, so shipping them per call would cost more than decoding once there.
_worker_prepared: tuple[bytes, PreparedLCMFCapture] | None = None


def _estimate_lcmf_in_worker(raw: bytes, calibration: Calibration, **kwargs) -> LCMFResult:
    """LCMF-v1 inside the estimator process, decoding each capture only once."""
    global _worker_prepared  # pylint: disable=global-statement
    if _worker_prepared is None or _worker_prepared[0] != raw:
        _worker_prepared = (raw, prepare_lcmf_capture(raw))
    return estimate_lcmf_v1(raw, calibration, prepared=_worker_prepared[1], **kwargs)


def _estimate_readback_in_worker(
    readback: Readback | None,
    calibration: Calibration,
    *,
    club: str | None,
    net_range_m: float | None,
    **kwargs,
) -> LCMFResult | None:
    """LCMF-v1 on a selective readback; None when its windows miss this club's track."""
    if readback is None or not readback.covers(club=club, net_range_m=net_range_m):
        return None
    return estimate_lcmf_v1(
        b"", calibration, club=club, net_range_m=net_range_m, prepared=readback.prepare(), **kwargs
    )


def _ops_candidate_rank(candidate: RecoveryCandidate) -> tuple[float, int, float]:
    """Rank truth-free range walks before the more expensive LCMF pass."""
    return (
        abs(candidate.speed_ratio - 1.0),
        -candidate.track.n_inliers,
        candidate.track.rms_bins,
    )


def _credible_ops_candidates(
    candidates: list[RecoveryCandidate],
) -> list[RecoveryCandidate]:
    """Return a small, deduplicated set of OPS-compatible range walks."""
    credible = [
        candidate
        for candidate in candidates
        if abs(candidate.speed_ratio - 1.0) <= OPS_TRACK_SPEED_TOLERANCE_FRAC
        and candidate.track.n_inliers >= 12
        and candidate.track.rms_bins <= 0.48
        and candidate.track.t_last - candidate.track.t_first >= 0.009
    ]
    credible.sort(key=_ops_candidate_rank)
    selected: list[RecoveryCandidate] = []
    seen: set[tuple[int, int]] = set()
    for candidate in credible:
        # RANSAC emits many nearly identical lines. Keep one representative
        # per approximately 1 mph / 1.5 ms speed-impact cell.
        key = (
            round(candidate.track.speed_mph),
            round(candidate.impact_s / 0.0015),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
        if len(selected) >= OPS_GUIDED_MAX_CANDIDATES:
            break
    return selected


def _recovery_result_rank(
    candidate: RecoveryCandidate,
    result: LCMFResult,
) -> tuple[bool, float, float, int, float]:
    """Combine OPS agreement with independent spatial-estimator evidence."""
    # A single channel can still be useful (shot 6 on 2026-08-14), but must
    # beat a corroborated candidate by a meaningful OPS-speed margin.
    single_channel_penalty = 0.03 if result.single_channel else 0.0
    spread = float(result.component_std_deg or 0.0)
    return (
        result.single_channel,
        abs(candidate.speed_ratio - 1.0) + single_channel_penalty + 0.01 * min(spread, 8.0),
        candidate.track.rms_bins,
        -result.n_frames,
        -candidate.track.n_inliers,
    )


class _ReadbackEstimate:
    """One capture's readback measurement, computed once and shared by its callers."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.measurement: LCMFResult | None = None


# Readback estimates remembered for captures the shot pipeline has not consumed yet.
_MAX_READBACK_ESTIMATES = 8


@dataclass(frozen=True)
class IWR6843ShotResult:
    """Capture transport result and optional angle measurement."""

    capture: IWR6843Capture | None
    measurement: LCMFResult | None
    club_path: ClubPathResult | None = None


@dataclass
class IWR6843Runtime:
    """Configured TI hardware and estimator state for the server."""

    capture_monitor: IWR6843CaptureMonitor
    calibration: Calibration
    net_range_m: float | None
    tx_order: str = "normal"
    capture_timeout_s: float = 12.0
    # The currently selected club, for the capture monitor's early ball search.
    club_provider: Callable[[], str | None] | None = None
    azimuth_offset_deg: float = 0.0
    horizontal_phase_reference_rad: float | None = None
    tdm_sign_policy: str = "positive"
    club_window_policy: ClubWindowPolicy = field(default_factory=ClubWindowPolicy)
    # The ball-derived impact anchor runs ~2 ms late: on the 2026-08-07
    # 55-shot session, the club track's tee-contact error minimized at -2 ms
    # (0.026 m vs 0.035 m uncorrected), independently matching the camera's
    # ball-departure timing. Applied to the club estimators only; the ball
    # pipeline keeps its own anchor.
    club_impact_correction_s: float = -0.002
    # Accepted ball tracks establish a truth-free rolling prior. A rejected
    # vertical solution may use that prior to recover impact timing for the
    # independent experimental club search, never to publish vertical launch.
    recovery_observations: list[tuple[float, float, float]] = field(default_factory=list)
    # Run LCMF and club path in one long-lived child process so their
    # GIL-holding loops cannot stall the OPS serial reader. Off by default
    # (inline, as before) until validated on the Pi; see --iwr6843-estimator-process.
    # After any worker failure they run inline for the rest of the session.
    estimator_process: bool = False
    estimator_timeout_s: float = 30.0
    # Launch-angle search grid. The exhaustive production sweep by default;
    # None selects the coarse-to-fine search (--iwr6843-fast-angle-search).
    angle_grid_step_deg: float | None = PRODUCTION_ANGLE_STEP_DEG
    _estimator_worker: EstimatorWorker | None = field(default=None, init=False, repr=False)
    _readback_worker: EstimatorWorker | None = field(default=None, init=False, repr=False)
    _readback_estimates: dict[int, _ReadbackEstimate] = field(
        default_factory=dict, init=False, repr=False
    )
    _estimator_worker_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if getattr(self.capture_monitor, "selective_readback", False):
            # pylint: disable-next=protected-access  # the runtime owns this monitor
            self.capture_monitor._readback_context = self._readback_context
            # The readback estimate runs while the full dump is still crossing
            # the UART. Inline, its GIL-holding loops starved the serial reader
            # and the dump lost bytes (7 of 12 shots, 2026-10-06), so it always
            # runs in its own process, started now so the first shot does not wait.
            self._readback_worker = EstimatorWorker()
            threading.Thread(target=self._warm_readback_worker, daemon=True).start()

    def _warm_readback_worker(self) -> None:
        worker = self._readback_worker
        if worker is None:
            return
        try:
            worker.call(
                _estimate_readback_in_worker,
                None,
                self.calibration,
                club=None,
                net_range_m=None,
                timeout_s=60.0,
            )
        except EstimatorWorkerError as error:
            logger.warning("[IWR6843] Readback estimator process did not start: %s", error)

    def _readback_context(self) -> dict:
        club = self.club_provider() if self.club_provider is not None else None
        return {"club": club, "net_range_m": self.net_range_m}

    def _worker(self) -> EstimatorWorker | None:
        with self._estimator_worker_lock:
            if self.estimator_process and self._estimator_worker is None:
                self._estimator_worker = EstimatorWorker()
            return self._estimator_worker

    def _abandon_worker(self, error: EstimatorWorkerError) -> None:
        logger.warning(
            "[IWR6843] Estimator worker failed (%s); running estimators inline from now on",
            error,
        )
        with self._estimator_worker_lock:
            worker, self._estimator_worker = self._estimator_worker, None
            self.estimator_process = False
        if worker is not None:
            worker.close()

    def _estimate_lcmf(
        self,
        raw: bytes,
        calibration: Calibration,
        *,
        prepared: PreparedLCMFCapture,
        **kwargs,
    ) -> LCMFResult:
        worker = self._worker()
        if worker is not None:
            try:
                return worker.call(
                    _estimate_lcmf_in_worker,
                    raw,
                    calibration,
                    timeout_s=self.estimator_timeout_s,
                    **kwargs,
                )
            except EstimatorWorkerError as error:
                self._abandon_worker(error)
        return estimate_lcmf_v1(raw, calibration, prepared=prepared, **kwargs)

    def _estimate_club_path(self, raw: bytes, calibration: Calibration, **kwargs) -> ClubPathResult:
        worker = self._worker()
        if worker is not None:
            try:
                return worker.call(
                    estimate_club_path,
                    raw,
                    calibration,
                    timeout_s=self.estimator_timeout_s,
                    **kwargs,
                )
            except EstimatorWorkerError as error:
                self._abandon_worker(error)
        return estimate_club_path(raw, calibration, **kwargs)

    def _remember_recovery_observation(
        self, measurement: LCMFResult, ball_speed_mph: float
    ) -> None:
        if (
            not getattr(measurement, "accepted", False)
            or getattr(measurement, "impact_t_s", None) is None
            or getattr(measurement, "track_speed_mph", None) is None
            or getattr(measurement, "track_span_s", None) is None
        ):
            return
        self.recovery_observations.append(
            (
                float(measurement.track_speed_mph) / ball_speed_mph,
                float(measurement.impact_t_s),
                float(measurement.track_span_s),
            )
        )
        del self.recovery_observations[:-50]

    def _recover_impact_time(
        self, raw: bytes, calibration: Calibration, ball_speed_mph: float
    ) -> float | None:
        if len(self.recovery_observations) < 3:
            return None
        ratios, impacts, spans = zip(*self.recovery_observations)
        try:
            prior = RecoveryPrior.fit(list(ratios), list(impacts), list(spans))
            candidates = find_recovery_candidates(
                raw,
                calibration,
                ball_speed_mph=ball_speed_mph,
                net_range_m=self.net_range_m,
            )
        except ValueError:
            # Older/raw-ADC firmware formats cannot run the snapshot recovery.
            # Preserve the original no-impact behavior rather than losing the shot.
            return None
        candidate = select_recovery_candidate(candidates, prior)
        return candidate.impact_s if candidate is not None else None

    def _ops_guided_measurement(  # pylint: disable=too-many-return-statements
        self,
        raw: bytes,
        calibration: Calibration,
        *,
        ball_speed_mph: float,
        club: str | None,
        baseline: LCMFResult,
        prepared: PreparedLCMFCapture,
    ) -> LCMFResult:
        """Replace a suspicious TI range walk with an OPS-compatible one."""
        speed = baseline.track_speed_mph
        if baseline.accepted and speed is None:
            return replace(baseline, status="accepted_track_speed_warning")
        speed_error = (
            abs(speed / ball_speed_mph - 1.0)
            if speed is not None and ball_speed_mph > 0.0
            else float("inf")
        )
        if baseline.accepted and speed_error <= OPS_TRACK_SPEED_TOLERANCE_FRAC:
            return baseline

        try:
            candidates = _credible_ops_candidates(
                find_recovery_candidates(
                    raw,
                    calibration,
                    ball_speed_mph=ball_speed_mph,
                    net_range_m=self.net_range_m,
                    prepared=prepared.vertical,
                )
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] OPS-guided track search failed: %s", error)
            if baseline.accepted:
                return replace(baseline, status="accepted_track_speed_warning")
            return baseline
        recoveries: list[tuple[RecoveryCandidate, LCMFResult]] = []
        for candidate in candidates:
            result = self._estimate_lcmf(
                raw,
                calibration,
                ball_speed_mph=ball_speed_mph,
                club=club,
                net_range_m=self.net_range_m,
                tx_order=self.tx_order,
                tdm_sign_policy=self.tdm_sign_policy,
                horizontal_phase_reference_rad=self.horizontal_phase_reference_rad,
                track_override=candidate.track,
                track_override_scope=candidate.scope,
                prepared=prepared,
                grid_step_deg=self.angle_grid_step_deg,
            )
            if (
                result.accepted
                and result.n_frames >= 4
                and result.angle_deg is not None
                and result.angle_deg >= OPS_GUIDED_MIN_LAUNCH_DEG
            ):
                recoveries.append((candidate, result))

        if recoveries:
            _candidate, selected = min(
                recoveries,
                key=lambda item: _recovery_result_rank(item[0], item[1]),
            )
            status = (
                "accepted_ops_guided_single_channel"
                if selected.single_channel
                else "accepted_ops_guided"
            )
            return replace(selected, status=status)
        if baseline.accepted:
            return replace(baseline, status="accepted_track_speed_warning")
        return baseline

    def _with_azimuth_offset(self, measurement):
        horizontal_deg = getattr(measurement, "horizontal_deg", None)
        if horizontal_deg is None:
            return measurement
        return replace(
            measurement,
            horizontal_deg=horizontal_deg + self.azimuth_offset_deg,
            horizontal_raw_deg=horizontal_deg,
        )

    def _shared_readback_measurement(
        self,
        capture: IWR6843Capture,
        calibration: Calibration,
        *,
        ball_speed_mph: float,
        club: str | None,
        on_ball_measurement: Callable[[IWR6843ShotResult], None] | None,
    ) -> LCMFResult | None:
        """Estimate a capture's readback once; the first caller also reports it.

        The early publisher and the shot pipeline both ask for the same capture,
        in either order. Whoever arrives second waits for the first's answer.
        """
        with self._estimator_worker_lock:
            estimate = self._readback_estimates.get(capture.sequence)
            first = estimate is None
            if estimate is None:
                estimate = self._readback_estimates[capture.sequence] = _ReadbackEstimate()
                for stale in sorted(self._readback_estimates)[:-_MAX_READBACK_ESTIMATES]:
                    del self._readback_estimates[stale]
        if not first:
            estimate.done.wait(self.estimator_timeout_s)
            return estimate.measurement
        try:
            estimate.measurement = self._readback_measurement(
                capture, calibration, ball_speed_mph=ball_speed_mph, club=club
            )
            if estimate.measurement is not None and on_ball_measurement is not None:
                on_ball_measurement(
                    IWR6843ShotResult(capture=capture, measurement=estimate.measurement)
                )
        finally:
            estimate.done.set()
        return estimate.measurement

    def publish_early_ball_measurement(
        self,
        *,
        impact_timestamp: float | None,
        ball_speed_mph: float,
        club: str | None,
        tilt_deg: float | None = None,
        on_ball_measurement: Callable[[IWR6843ShotResult], None],
    ) -> None:
        """Report a shot's readback measurement without waiting for the shot pipeline.

        The pipeline handles one shot at a time, so a shot hit while the previous
        one is still in its camera stage would otherwise wait for it. This only
        looks at the capture; ``process_shot`` still consumes it and reuses the answer.
        """
        capture = self.capture_monitor.capture_for_shot(
            impact_timestamp, timeout_s=self.capture_timeout_s, consume=False
        )
        if capture is None or capture.readback is None or not capture.valid:
            return
        calibration = self.calibration
        if tilt_deg is not None:
            calibration = replace(self.calibration, tilt_rad=math.radians(tilt_deg))
        self._shared_readback_measurement(
            capture,
            calibration,
            ball_speed_mph=ball_speed_mph,
            club=club,
            on_ball_measurement=on_ball_measurement,
        )

    def _readback_measurement(
        self,
        capture: IWR6843Capture,
        calibration: Calibration,
        *,
        ball_speed_mph: float,
        club: str | None,
    ) -> LCMFResult | None:
        """The ball measurement from a selective readback, when it is already final.

        None sends the shot to the full dump: the fetched windows do not hold the
        track this club selects, the estimate was rejected, or its speed disagrees
        with OPS and the recovery search (which reads the whole ring) must run.
        """
        worker = self._readback_worker
        if capture.readback is None or worker is None:
            return None
        try:
            measurement = worker.call(
                _estimate_readback_in_worker,
                capture.readback,
                calibration,
                timeout_s=self.estimator_timeout_s,
                ball_speed_mph=ball_speed_mph,
                club=club,
                net_range_m=self.net_range_m,
                tx_order=self.tx_order,
                tdm_sign_policy=self.tdm_sign_policy,
                horizontal_phase_reference_rad=self.horizontal_phase_reference_rad,
                grid_step_deg=self.angle_grid_step_deg,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] Readback estimate failed; using the full dump: %s", error)
            return None
        if measurement is None:
            return None
        speed = measurement.track_speed_mph
        if (
            not measurement.accepted
            or speed is None
            or ball_speed_mph <= 0.0
            or abs(speed / ball_speed_mph - 1.0) > OPS_TRACK_SPEED_TOLERANCE_FRAC
        ):
            return None
        return self._with_azimuth_offset(measurement)

    def process_shot(  # pylint: disable=too-many-arguments
        self,
        *,
        impact_timestamp: float | None,
        ball_speed_mph: float,
        club: str | None,
        club_speed_mph: float | None = None,
        tilt_deg: float | None = None,
        on_ball_measurement: Callable[[IWR6843ShotResult], None] | None = None,
    ) -> IWR6843ShotResult:
        """Match one OPS shot to TI data and run LCMF-v1.

        With selective readback the ball measurement can be ready seconds before
        the full dump; ``on_ball_measurement`` receives it then, and the returned
        result repeats it alongside the club path once the dump has arrived.
        """
        capture = self.capture_monitor.capture_for_shot(
            impact_timestamp,
            timeout_s=self.capture_timeout_s,
        )
        if capture is None or not capture.valid:
            return IWR6843ShotResult(capture=capture, measurement=None)
        shot_calibration = self.calibration
        if tilt_deg is not None:
            shot_calibration = replace(self.calibration, tilt_rad=math.radians(tilt_deg))
        measurement = None
        if getattr(capture, "readback", None) is not None:
            measurement = self._shared_readback_measurement(
                capture,
                shot_calibration,
                ball_speed_mph=ball_speed_mph,
                club=club,
                on_ball_measurement=on_ball_measurement,
            )
        if capture.raw is None:
            full = capture.full(self.capture_timeout_s)
            if full is None or full.raw is None or not full.valid:
                if measurement is not None:
                    return IWR6843ShotResult(capture=capture, measurement=measurement)
                return IWR6843ShotResult(capture=full, measurement=None)
            capture = full
        if measurement is None:
            prepared = prepare_lcmf_capture(capture.raw)
            measurement = self._estimate_lcmf(
                capture.raw,
                shot_calibration,
                ball_speed_mph=ball_speed_mph,
                club=club,
                net_range_m=self.net_range_m,
                tx_order=self.tx_order,
                tdm_sign_policy=self.tdm_sign_policy,
                horizontal_phase_reference_rad=self.horizontal_phase_reference_rad,
                prepared=prepared,
                grid_step_deg=self.angle_grid_step_deg,
            )
            if isinstance(measurement, LCMFResult):
                measurement = self._ops_guided_measurement(
                    capture.raw,
                    shot_calibration,
                    ball_speed_mph=ball_speed_mph,
                    club=club,
                    baseline=measurement,
                    prepared=prepared,
                )
            measurement = self._with_azimuth_offset(measurement)
        self._remember_recovery_observation(measurement, ball_speed_mph)
        club_path = None
        # No OPS club speed means no identity gate to distinguish the club
        # track from hands, body, or the ball itself, so an estimate here
        # would be an unverifiable guess -- worse than no estimate at all.
        if club_speed_mph:
            ball_sign = getattr(measurement, "tdm_sign_used", None)
            fallback = ball_sign not in (-1, 1)
            policy_sign = _TDM_SIGN_BY_POLICY.get(self.tdm_sign_policy, 1)
            impact_t_s = getattr(measurement, "impact_t_s", None)
            recovered_impact = False
            if impact_t_s is None:
                impact_t_s = self._recover_impact_time(
                    capture.raw,
                    shot_calibration,
                    ball_speed_mph,
                )
                recovered_impact = impact_t_s is not None
            if impact_t_s is not None:
                impact_t_s += self.club_impact_correction_s
            club_path = self._estimate_club_path(
                capture.raw,
                shot_calibration,
                ops_club_speed_mph=club_speed_mph,
                # Where impact sits in the ring, from the ball's own range
                # walk. The freeze is requested by a UART command, so the
                # trigger frame lands late by a variable 2-4 frames and the
                # slot cannot be assumed; None means the ball tracker gave
                # nothing to anchor to and club path declines.
                impact_t_s=impact_t_s,
                aim_offset_deg=self.azimuth_offset_deg,
                phase_reference_rad=self.horizontal_phase_reference_rad,
                tdm_sign=policy_sign if fallback else ball_sign,
                window_policy=self.club_window_policy,
            )
            if recovered_impact:
                club_path.status = f"{club_path.status}_recovered_impact"
            if fallback:
                # The ball measurement had no usable sign, so this is the
                # configured policy's guess, not a measured value. Recorded
                # in the status so a later replay can tell the two apart.
                club_path.status = f"{club_path.status}_tdm_sign_fallback"
        return IWR6843ShotResult(capture=capture, measurement=measurement, club_path=club_path)

    def stop(self) -> None:
        """Release TI hardware and the estimator process."""
        try:
            self.capture_monitor.stop()
        finally:
            with self._estimator_worker_lock:
                workers = (self._estimator_worker, self._readback_worker)
                self._estimator_worker = self._readback_worker = None
                self.estimator_process = False
            for worker in workers:
                if worker is not None:
                    worker.close()


__all__ = ["IWR6843Runtime", "IWR6843ShotResult"]
