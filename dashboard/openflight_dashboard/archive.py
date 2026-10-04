"""Move one finished session (log, raw radar data, captures) from the Pi to disk.

Order matters: everything is downloaded into a temporary folder and checked
against the Pi's manifest, the folder is renamed into place, and only then is
the Pi asked to delete its copy. The Pi re-checks the checksum and sizes before
deleting, so a partial copy can never cost data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath

from .logs import session_id_for
from .store import Store


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts:
        raise ValueError(f"refusing capture path {relative!r}")
    return Path(*parts)


def archive_session(store: Store, source, raw_dir: Path, name: str) -> Path:
    """Copy, verify, then delete from the Pi. Returns the session's folder."""
    session_id = session_id_for(name)
    final = raw_dir / session_id
    manifest = source.manifest(name)
    sizes = {entry["path"]: int(entry["size"]) for entry in manifest["files"]}

    if not final.exists():
        staging = raw_dir / f".{session_id}.partial"
        shutil.rmtree(staging, ignore_errors=True)
        source.download_log(name, staging / name)
        if _sha256(staging / name) != manifest["sha256"]:
            raise ValueError(f"{name} changed while it was being copied")
        for relative, size in sizes.items():
            dest = staging / "captures" / _safe_relative(relative)
            source.download_capture(name, relative, dest)
            if dest.stat().st_size != size:
                raise ValueError(f"{relative} was cut short while copying")
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        staging.rename(final)

    log_copy = final / name
    stat = log_copy.stat()
    store.ingest(name, log_copy.read_text(encoding="utf-8"), stat.st_size, stat.st_mtime)
    source.delete(name, manifest["sha256"], sizes)
    store.mark_archived(name, str(final))
    return final
