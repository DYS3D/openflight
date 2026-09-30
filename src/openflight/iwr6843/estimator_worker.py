"""One long-lived child process for the IWR6843 numpy estimators.

LCMF and club-path estimation spend seconds in Python-level loops that hold
the GIL. Running them in a separate process keeps the server's serial reader
threads responsive while a shot is being measured.
"""

from __future__ import annotations

import logging
import multiprocessing
import signal
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

_READY = "ready"


class EstimatorWorkerError(RuntimeError):
    """The worker could not deliver a result; the estimator itself did not fail."""


def _serve(conn) -> None:
    """Child main loop: run each ``(func, args, kwargs)`` request in order."""
    # The parent owns shutdown; a terminal Ctrl-C must not traceback here.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    conn.send(_READY)
    while True:
        try:
            request = conn.recv()
        except EOFError:
            return
        if request is None:
            return
        func, args, kwargs = request
        try:
            reply = ("ok", func(*args, **kwargs))
        except Exception as error:  # pylint: disable=broad-exception-caught
            reply = ("error", error)
        try:
            conn.send(reply)
        except Exception as error:  # pylint: disable=broad-exception-caught
            conn.send(("error", EstimatorWorkerError(f"unpicklable worker reply: {error!r}")))


class EstimatorWorker:
    """Run picklable module-level functions in a single spawned process.

    The process starts on the first call. A call that cannot complete --
    startup failure, a crash, an unpicklable request or a timeout -- kills
    the process and raises ``EstimatorWorkerError``; the next call starts a
    fresh one. An exception raised by the function itself is re-raised
    unchanged and leaves the process running.
    """

    def __init__(self, *, start_method: str = "spawn", startup_timeout_s: float = 60.0):
        self._context = multiprocessing.get_context(start_method)
        self._startup_timeout_s = startup_timeout_s
        self._lock = threading.Lock()
        self._process = None
        self._conn = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        """Child process id while it is running."""
        process = self._process
        return process.pid if process is not None and process.is_alive() else None

    def call(self, func: Callable[..., Any], /, *args, timeout_s: float, **kwargs) -> Any:
        """Return ``func(*args, **kwargs)`` computed in the worker process."""
        with self._lock:
            conn = self._ensure_started()
            try:
                conn.send((func, args, kwargs))
                if not conn.poll(timeout_s):
                    raise EstimatorWorkerError(f"no result within {timeout_s:.1f}s")
                status, payload = conn.recv()
            except EstimatorWorkerError:
                self._kill()
                raise
            except Exception as error:  # pylint: disable=broad-exception-caught
                self._kill()
                raise EstimatorWorkerError(f"worker transport failed: {error!r}") from error
        if status == "error":
            raise payload
        return payload

    def _ensure_started(self):
        if self._closed:
            raise EstimatorWorkerError("estimator worker is closed")
        if self._process is not None:
            return self._conn
        parent_conn, child_conn = self._context.Pipe()
        process = self._context.Process(
            target=_serve,
            args=(child_conn,),
            name="iwr6843-estimator",
            daemon=True,
        )
        try:
            process.start()
        except Exception as error:  # pylint: disable=broad-exception-caught
            parent_conn.close()
            raise EstimatorWorkerError(f"could not start worker: {error!r}") from error
        finally:
            child_conn.close()
        self._process, self._conn = process, parent_conn
        try:
            ready = parent_conn.poll(self._startup_timeout_s) and parent_conn.recv() == _READY
        except (EOFError, OSError):
            ready = False
        if not ready:
            self._kill()
            raise EstimatorWorkerError(
                f"worker did not become ready within {self._startup_timeout_s:.1f}s"
            )
        logger.info("[IWR6843] Estimator worker started (pid %d)", process.pid)
        return parent_conn

    def _kill(self) -> None:
        process, conn = self._process, self._conn
        self._process, self._conn = None, None
        if process is not None:
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        if conn is not None:
            conn.close()

    def close(self, timeout_s: float = 2.0) -> None:
        """Stop the worker; an in-flight call fails with ``EstimatorWorkerError``."""
        self._closed = True
        # Only ask politely when no call owns the pipe; Connection.send is not
        # thread-safe, and a busy worker would not read the request anyway.
        if self._lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            try:
                process, conn = self._process, self._conn
                if process is not None:
                    try:
                        conn.send(None)
                        process.join(timeout=timeout_s)
                    except (OSError, ValueError):
                        pass
                self._kill()
            finally:
                self._lock.release()
        else:
            self._kill()


__all__ = ["EstimatorWorker", "EstimatorWorkerError"]
