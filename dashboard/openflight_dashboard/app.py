"""Flask app: JSON API for the dashboard pages plus the static front end."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from . import stats
from .store import Store
from .sync import SyncLoop

STATIC_DIR = Path(__file__).parent / "static"


def create_app(store: Store, sync: SyncLoop | None, pi_url: str | None) -> Flask:
    app = Flask(__name__, static_folder=None)

    def profile() -> str | None:
        return request.args.get("profile") or None

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(STATIC_DIR, name)

    @app.get("/api/summary")
    def summary():
        return jsonify(stats.summary(store) | {"pi_url": pi_url, "sync_enabled": sync is not None})

    @app.post("/api/sync")
    def sync_now():
        if sync is None:
            abort(409, "No Pi configured")
        return jsonify(sync.trigger())

    @app.get("/api/profiles")
    def profiles():
        return jsonify(stats.profiles(store))

    @app.get("/api/sessions")
    def sessions():
        return jsonify(stats.sessions(store, profile()))

    @app.get("/api/sessions/<session_id>")
    def session(session_id: str):
        result = stats.session_shots(store, session_id, profile())
        if result is None:
            abort(404)
        return jsonify(result)

    @app.get("/api/trends")
    def trends():
        return jsonify(stats.trends(store, profile()))

    @app.get("/api/gapping")
    def gapping():
        return jsonify(stats.gapping(store, profile(), request.args.get("since") or None))

    @app.get("/api/records")
    def records():
        return jsonify(stats.records(store, profile()))

    return app
