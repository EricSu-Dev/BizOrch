from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.knowledge.contracts import (
    KnowledgeDocumentEventType,
    KnowledgeIndexJobStatus,
)
from app.knowledge.models import KnowledgeIndexJob
from app.knowledge.repository import KnowledgeRepository
from app.persistence.base import Base
from tests.knowledge.fakes import FakeEmbeddings, InMemoryVectorStore
from tests.knowledge.test_service import ingest
from app.knowledge.service import KnowledgeService


def build_repository(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'repository.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    service = KnowledgeService(sessions, FakeEmbeddings(), InMemoryVectorStore())
    document = ingest(service, source_uri="policy://repository")
    return sessions, document.document_id


def test_repository_persists_index_job_and_safe_document_event(tmp_path) -> None:
    sessions, document_id = build_repository(tmp_path)
    active_key = "a" * 64
    now = datetime.now(UTC)

    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        job = KnowledgeIndexJob(
            id=str(uuid4()),
            document_id=document_id,
            index_version="te4-d1024-c800-o120-v1",
            active_key=active_key,
            status=KnowledgeIndexJobStatus.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            available_at=now,
        )
        repository.add_index_job(job)
        event = repository.add_document_event(
            document_id=document_id,
            event_type=KnowledgeDocumentEventType.CREATED,
            actor_id="EMP-OPERATOR",
            actor_roles=frozenset({"operator"}),
            safe_payload={"version_label": "v1"},
        )

        assert repository.get_index_job(job.id).id == job.id
        assert repository.find_active_index_job(active_key=active_key).id == job.id
        assert repository.list_document_events(document_id) == [event]
        assert event.actor_roles == ["operator"]
        assert event.safe_payload == {"version_label": "v1"}


def test_active_key_uniquely_protects_unfinished_index_job(tmp_path) -> None:
    sessions, document_id = build_repository(tmp_path)
    active_key = "b" * 64
    now = datetime.now(UTC)

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            repository = KnowledgeRepository(session)
            for _ in range(2):
                repository.add_index_job(
                    KnowledgeIndexJob(
                        id=str(uuid4()),
                        document_id=document_id,
                        index_version="te4-d1024-c800-o120-v1",
                        active_key=active_key,
                        status=KnowledgeIndexJobStatus.PENDING.value,
                        attempt_count=0,
                        max_attempts=3,
                        available_at=now,
                    )
                )


def test_completed_jobs_can_release_active_key_for_future_rebuilds(tmp_path) -> None:
    sessions, document_id = build_repository(tmp_path)
    now = datetime.now(UTC)

    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        for _ in range(2):
            repository.add_index_job(
                KnowledgeIndexJob(
                    id=str(uuid4()),
                    document_id=document_id,
                    index_version="te4-d1024-c800-o120-v1",
                    active_key=None,
                    status=KnowledgeIndexJobStatus.SUCCEEDED.value,
                    attempt_count=1,
                    max_attempts=3,
                    available_at=now,
                    started_at=now,
                    finished_at=now,
                )
            )
