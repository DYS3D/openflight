"""Run the OpenFlight dashboard, or ``import FILE...`` to load logs copied by hand."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .app import create_app
from .store import Store
from .sync import PiSource, SyncLoop


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

    commands = parser.add_subparsers(dest="command")
    importer = commands.add_parser("import", help="Load session_*.jsonl files copied by hand")
    importer.add_argument("files", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    store = Store(Path(args.data_dir) / "openflight.sqlite3")

    if args.command == "import":
        for path in args.files:
            stat = path.stat()
            shots = store.ingest(
                path.name, path.read_text(encoding="utf-8"), stat.st_size, stat.st_mtime
            )
            print(f"{path.name}: {shots} shots")
        return

    sync = None
    if args.pi:
        sync = SyncLoop(store, PiSource(args.pi, args.token), args.sync_minutes * 60)
        sync.start()
    app = create_app(store, sync, args.pi)
    print(f"OpenFlight dashboard on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
