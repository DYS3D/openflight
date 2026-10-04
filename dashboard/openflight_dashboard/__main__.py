"""Run the OpenFlight dashboard, or ``import FILE...`` to load logs copied by hand."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

from .app import create_app
from .skytrak import SkyTrakFormatError, import_export
from .store import Store
from .sync import PiSource, SyncLoop


def port_in_use(port: int) -> bool:
    """Whether something already accepts connections on this port.

    Flask's server sets SO_REUSEADDR, which on Windows lets a second copy bind
    the same port; the browser then keeps reaching the old copy. Checking for a
    listener first makes a second start fail loudly instead.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openflight-dashboard", description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("OPENFLIGHT_DASHBOARD_DATA", "data"),
        help="Where the dashboard keeps its database (default: ./data)",
    )
    parser.add_argument(
        "--pi",
        default=os.environ.get("OPENFLIGHT_PI_URL"),
        help="Pi address, e.g. http://openflight.local:8080 (omit to only show imported logs)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPENFLIGHT_TOKEN"),
        help="Access token, if the Pi runs with --auth-token-file",
    )
    parser.add_argument("--host", default=os.environ.get("OPENFLIGHT_DASHBOARD_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("OPENFLIGHT_DASHBOARD_PORT", "8090"))
    )
    parser.add_argument(
        "--sync-minutes",
        type=float,
        default=float(os.environ.get("OPENFLIGHT_SYNC_MINUTES", "5")),
        help="How often to copy new shots from the Pi (default: 5)",
    )

    parser.add_argument(
        "--offload",
        action="store_true",
        default=os.environ.get("OPENFLIGHT_OFFLOAD", "") not in ("", "0", "false"),
        help=(
            "Move each finished session off the Pi: copy its log, raw radar data and "
            "captures here, verify them, then delete them from the Pi. Needs the Pi "
            "to run with --session-log-offload."
        ),
    )
    parser.add_argument(
        "--raw-dir",
        default=os.environ.get("OPENFLIGHT_RAW_DIR"),
        help="Where offloaded sessions are kept (default: <data-dir>/raw); may be a NAS share",
    )
    commands = parser.add_subparsers(dest="command")
    hider = commands.add_parser(
        "hide",
        help="Take sessions off the dashboard (their raw files stay in the raw folder)",
    )
    hider.add_argument(
        "sessions", nargs="*", help="Session ids, e.g. session_20261004_085833_range"
    )
    hider.add_argument("--all", action="store_true", help="Hide every session")
    hider.add_argument(
        "--keep-latest", action="store_true", help="Hide every session except the newest"
    )
    importer = commands.add_parser("import", help="Load session_*.jsonl files copied by hand")
    importer.add_argument("files", nargs="+", type=Path)
    skytrak = commands.add_parser(
        "import-skytrak",
        help="Load SkyTrak shots-history CSV exports (files or a folder of them)",
    )
    skytrak.add_argument("paths", nargs="+", type=Path)
    skytrak.add_argument(
        "--golfer", required=True, help="Whose shots these are, e.g. Justin (matches Pi profiles)"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    store = Store(Path(args.data_dir) / "openflight.sqlite3")

    if args.command == "hide":
        visible = store.session_ids()
        if args.all:
            chosen = visible
        elif args.keep_latest:
            chosen = visible[1:]
        else:
            chosen = args.sessions
        store.hide(chosen)
        print(
            f"Hid {len(chosen)} session(s); {len(visible) - len(set(chosen) & set(visible))} left"
        )
        return

    if args.command == "import-skytrak":
        files = [
            file
            for path in args.paths
            for file in (sorted(path.glob("*.csv")) if path.is_dir() else [path])
        ]
        for file in files:
            try:
                session_id, shots = import_export(
                    store, file.read_text(encoding="utf-8-sig"), args.golfer
                )
            except SkyTrakFormatError as exc:
                print(f"{file.name}: skipped ({exc})")
                continue
            print(f"{file.name}: {shots} shots -> {session_id}")
        return

    if args.command == "import":
        for path in args.files:
            stat = path.stat()
            shots = store.ingest(
                path.name, path.read_text(encoding="utf-8"), stat.st_size, stat.st_mtime
            )
            print(f"{path.name}: {shots} shots")
        return

    if port_in_use(args.port):
        sys.exit(
            f"Port {args.port} is already in use: the dashboard is probably already "
            "running. Stop it first (see README: Updating)."
        )

    sync = None
    if args.pi:
        raw_dir = None
        if args.offload:
            raw_dir = Path(args.raw_dir) if args.raw_dir else Path(args.data_dir) / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            print(f"Moving finished sessions off the Pi into {raw_dir}")
        sync = SyncLoop(store, PiSource(args.pi, args.token), args.sync_minutes * 60, raw_dir)
        sync.start()
    app = create_app(store, sync, args.pi)
    print(f"OpenFlight dashboard on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
