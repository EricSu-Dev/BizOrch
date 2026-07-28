"""Run one durable BizOrch evaluation worker process.

The default worker processes only CONTRACT_ONLY runs and never calls external
model, embedding, MCP, approval, workflow or enterprise-write capabilities.
LIVE_READ_ONLY runs require an operator to start a separate worker explicitly
with --live after the run itself has been confirmed in the evaluation center.
"""

import argparse
import json
import os
import signal
import socket
from pathlib import Path
from threading import Event

from app.core.config import Settings
from app.evaluation.composition import (
    build_contract_evaluation_worker,
    build_live_read_only_evaluation_worker,
)
from app.evaluation.catalog import resolve_evaluation_repository_root
from app.persistence.database import build_engine, build_session_factory


def run_loop(
    worker: object,
    *,
    poll_interval_seconds: float,
    stop_event: Event,
    once: bool,
) -> None:
    """Poll one queue sequentially; MySQL remains the recovery authority."""
    run_once = getattr(worker, "run_once")
    while not stop_event.is_set():
        run_id = run_once()
        if run_id is not None:
            print(json.dumps({"run_id": run_id}, ensure_ascii=False), flush=True)
        if once:
            return
        if run_id is None:
            stop_event.wait(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one claimed evaluation run and exit",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="process only explicitly confirmed LIVE_READ_ONLY runs",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=2.0,
        help="idle polling interval; must be positive",
    )
    args = parser.parse_args()
    if args.poll_interval_seconds <= 0:
        parser.error("--poll-interval-seconds must be positive")

    settings = Settings()
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")

    engine = build_engine(settings.database_url, echo=settings.debug)
    sessions = build_session_factory(engine)
    worker_id = f"evaluation-{socket.gethostname()}-{os.getpid()}"[:100]
    repository_root = resolve_evaluation_repository_root(Path(__file__))
    worker = (
        build_live_read_only_evaluation_worker(
            sessions,
            settings,
            repository_root=repository_root,
            worker_id=worker_id,
        )
        if args.live
        else build_contract_evaluation_worker(
            sessions,
            repository_root=repository_root,
            worker_id=worker_id,
        )
    )
    stop_event = Event()

    def request_stop(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_loop(
            worker,
            poll_interval_seconds=args.poll_interval_seconds,
            stop_event=stop_event,
            once=args.once,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
