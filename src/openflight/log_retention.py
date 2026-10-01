"""Bounded retention for session logs and raw captures.

Session JSONL, raw radar logs, IWR6843 dumps, and camera captures grow without
limit on the Pi's SD card. Pruning removes whole sessions (a JSONL and its cloud
sidecars together) oldest-first, first by age and then until the directory fits
the size budget. Only files OpenFlight itself writes are ever considered.
"""

from __future__ import annotations

import logging
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)

SIDECAR_SUFFIXES = (".pushed", ".parked", ".state")
# Per-shot captures, relative to the log dir. Only these exact names are
# pruned so a --log-dir pointed somewhere broad (e.g. the home directory) can
# never reach unrelated files.
IWR6843_DUMP_GLOB = "iwr6843/iwr6843_*.l3dump"
CAMERA_SHOT_GLOB = "*/camera/camera_*"
CAMERA_SHOT_MARKER = "frames.npz"


@dataclass
class _Unit:
    """Files deleted together: a session JSONL plus its sidecars, or one capture."""

    files: List[Path] = field(default_factory=list)
    mtime: float = 0.0
    size: int = 0
    protected: bool = False


def _stat_unit(paths: Iterable[Path], protected: bool = False) -> Optional[_Unit]:
    unit = _Unit(protected=protected)
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        unit.files.append(path)
        unit.mtime = max(unit.mtime, info.st_mtime)
        unit.size += info.st_size
    return unit if unit.files else None


# Files the --debug toggle and the updater write into ~/openflight_logs.
DEBUG_LOG_GLOBS = ("debug_*.jsonl", "radar_raw_*.log", "update_*.log")


def _collect_debug_units(log_dir: Path, protect: Callable[[Path], bool]) -> List[_Unit]:
    units: List[_Unit] = []
    for pattern in DEBUG_LOG_GLOBS:
        for path in log_dir.glob(pattern):
            if path.is_file() and not path.is_symlink():
                unit = _stat_unit([path], protected=protect(path))
                if unit:
                    units.append(unit)
    return units


def _collect_units(log_dir: Path, protect: Callable[[Path], bool]) -> List[_Unit]:
    units: List[_Unit] = []
    for session in log_dir.glob("session_*.jsonl"):
        sidecars = [session.with_name(session.name + suffix) for suffix in SIDECAR_SUFFIXES]
        unit = _stat_unit([session, *sidecars], protected=protect(session))
        if unit:
            units.append(unit)
    for raw in log_dir.glob("radar_raw_*.log"):
        unit = _stat_unit([raw])
        if unit:
            units.append(unit)
    for dump in log_dir.glob(IWR6843_DUMP_GLOB):
        if dump.is_file() and not dump.is_symlink():
            unit = _stat_unit([dump], protected=protect(dump))
            if unit:
                units.append(unit)
    for shot_dir in log_dir.glob(CAMERA_SHOT_GLOB):
        if shot_dir.is_dir() and (shot_dir / CAMERA_SHOT_MARKER).is_file():
            files = [p for p in shot_dir.iterdir() if p.is_file() and not p.is_symlink()]
            unit = _stat_unit(files, protected=protect(shot_dir))
            if unit:
                units.append(unit)
    return units


def _delete(unit: _Unit) -> List[Path]:
    removed = []
    for path in unit.files:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.warning("[RETENTION] Could not delete %s: %s", path, error)
    return removed


def _remove_empty_camera_shot_dirs(log_dir: Path) -> None:
    for shot_dir in log_dir.glob(CAMERA_SHOT_GLOB):
        if shot_dir.is_dir():
            try:
                shot_dir.rmdir()
            except OSError:
                pass  # not empty


def prune_logs(
    log_dir: Path,
    *,
    max_age_days: float,
    max_total_mb: float,
    protect: Callable[[Path], bool] = lambda _path: False,
    now: Optional[float] = None,
    kind: str = "session",
) -> List[Path]:
    """Delete expired or over-budget logs; returns the removed paths.

    A limit of 0 disables it. ``protect`` marks files (a session JSONL or a
    capture) that must be kept regardless, e.g. still queued for cloud upload.
    ``kind`` is "session" for ~/openflight_sessions or "debug" for the
    --debug toggle's ~/openflight_logs.
    """
    log_dir = Path(log_dir)
    if not log_dir.is_dir() or (max_age_days <= 0 and max_total_mb <= 0):
        return []
    now = time.time() if now is None else now
    collect = _collect_debug_units if kind == "debug" else _collect_units
    units = sorted(collect(log_dir, protect), key=lambda unit: unit.mtime)
    remaining: List[_Unit] = []
    doomed: List[tuple[_Unit, str]] = []

    cutoff = now - max_age_days * 86_400
    for unit in units:
        if max_age_days > 0 and unit.mtime < cutoff and not unit.protected:
            doomed.append((unit, f"older than {max_age_days:g} days"))
        else:
            remaining.append(unit)

    if max_total_mb > 0:
        budget = int(max_total_mb * 1024 * 1024)
        total = sum(unit.size for unit in remaining)
        for unit in remaining:
            if total <= budget:
                break
            if unit.protected:
                continue
            doomed.append((unit, f"over the {max_total_mb:g} MB budget"))
            total -= unit.size

    if not doomed:
        return []

    # Never delete silently: name every file first so the terminal log shows
    # exactly what went and why before the first unlink.
    total_bytes = sum(unit.size for unit, _ in doomed)
    logger.warning(
        "[RETENTION] Deleting %d file(s), %.1f MB, from %s:",
        sum(len(unit.files) for unit, _ in doomed),
        total_bytes / (1024 * 1024),
        log_dir,
    )
    for unit, reason in doomed:
        for path in unit.files:
            logger.warning("[RETENTION]   %s (%s)", path.relative_to(log_dir), reason)

    removed: List[Path] = []
    for unit, _ in doomed:
        removed.extend(_delete(unit))

    if kind == "session":
        _remove_empty_camera_shot_dirs(log_dir)
    logger.info("[RETENTION] Removed %d old log file(s) from %s", len(removed), log_dir)
    return removed
