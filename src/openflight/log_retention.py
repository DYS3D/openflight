"""Bounded retention for session logs and raw captures.

Session JSONL, raw radar logs, IWR6843 dumps, and camera captures grow without
limit on the Pi's SD card. Pruning removes whole sessions (a JSONL and its cloud
sidecars together) oldest-first, first by age and then until the directory fits
the size budget. Only files OpenFlight itself writes are ever considered.
"""

from __future__ import annotations

import logging
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
            info = path.stat()
        except FileNotFoundError:
            continue
        unit.files.append(path)
        unit.mtime = max(unit.mtime, info.st_mtime)
        unit.size += info.st_size
    return unit if unit.files else None


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
) -> List[Path]:
    """Delete expired or over-budget logs; returns the removed paths.

    A limit of 0 disables it. ``protect`` marks files (a session JSONL or a
    capture) that must be kept regardless, e.g. still queued for cloud upload.
    """
    log_dir = Path(log_dir)
    if not log_dir.is_dir() or (max_age_days <= 0 and max_total_mb <= 0):
        return []
    now = time.time() if now is None else now
    units = sorted(_collect_units(log_dir, protect), key=lambda unit: unit.mtime)
    removed: List[Path] = []
    remaining: List[_Unit] = []

    cutoff = now - max_age_days * 86_400
    for unit in units:
        if max_age_days > 0 and unit.mtime < cutoff and not unit.protected:
            removed.extend(_delete(unit))
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
            removed.extend(_delete(unit))
            total -= unit.size

    _remove_empty_camera_shot_dirs(log_dir)
    if removed:
        logger.info("[RETENTION] Removed %d old log file(s) from %s", len(removed), log_dir)
    return removed
