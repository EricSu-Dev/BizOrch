"""Run the single durable knowledge index worker process."""

import argparse
from datetime import timedelta
import json
import os
import signal
import socket
from threading import Event

from app.core.config import Settings
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.index_worker import KnowledgeIndexWorker
from app.knowledge.vector_store import ChromaKnowledgeVectorStore
from app.persistence.database import build_engine, build_session_factory


def run_loop(
    worker: KnowledgeIndexWorker,
    *,
    poll_interval_seconds: float,
    stop_event: Event,
    once: bool,
) -> None:
    """Poll sequentially; durable MySQL state remains the recovery source."""
    while not stop_event.is_set():
        result = worker.run_once()
        if result is not None:
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        if once:
            return
        if result is None:
            stop_event.wait(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one ready task and exit",
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")

    engine = build_engine(settings.database_url, echo=settings.debug)
    sessions = build_session_factory(engine)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"[:100]
    worker = KnowledgeIndexWorker(
        sessions,
        DashScopeEmbeddings(
            settings.dashscope_api_key.get_secret_value()
            if settings.dashscope_api_key
            else None
        ),
        ChromaKnowledgeVectorStore(settings.chroma_path),
        worker_id=worker_id,
        lease_duration=timedelta(
            seconds=settings.knowledge_index_lease_seconds
        ),
        retry_base_delay=timedelta(
            seconds=settings.knowledge_index_retry_base_seconds
        ),
    )
    stop_event = Event()

    def request_stop(signum, frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_loop(
            worker,
            poll_interval_seconds=settings.knowledge_index_poll_interval_seconds,
            stop_event=stop_event,
            once=args.once,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
