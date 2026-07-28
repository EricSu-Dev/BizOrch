from sqlalchemy import func, select, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.base import Base
from app.tickets.contracts import TicketStatus
from app.tickets.models import TicketEvent
from app.tickets.repository import TicketActorMismatchError
from app.tickets.service import TicketProjectionService
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


def build_services(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tickets.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    return sessions, TicketProjectionService(sessions)


def create_waiting_workflow(sessions) -> str:
    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        run = workflows.create("generic_test")
        workflows.transition(
            run.id,
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="PROCESSING_STARTED",
        )
        workflows.transition(
            run.id,
            expected_version=1,
            target=WorkflowState.WAITING_USER,
            event_type="USER_INPUT_REQUIRED",
        )
        return run.id


def test_sync_is_idempotent_and_recovers_later_events(tmp_path) -> None:
    sessions, projection = build_services(tmp_path)
    run_id = create_waiting_workflow(sessions)

    first = projection.sync(
        run_id,
        requester_id="EMP-1001",
        title="Generic service request",
    )
    repeated = projection.sync(run_id)

    assert repeated == first
    initial = projection.get_for_requester(first.ticket_id, requester_id="EMP-1001")
    assert initial.status is TicketStatus.NEEDS_INPUT
    assert [event.sequence for event in initial.events] == [0, 1, 2]

    with sessions.begin() as session:
        WorkflowRepository(session).transition(
            run_id,
            expected_version=2,
            target=WorkflowState.RUNNING,
            event_type="USER_INPUT_RECEIVED",
        )

    projection.sync(run_id)
    recovered = projection.get_for_requester(
        first.ticket_id,
        requester_id="EMP-1001",
    )
    assert recovered.status is TicketStatus.IN_PROGRESS
    assert [event.sequence for event in recovered.events] == [0, 1, 2, 3]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(TicketEvent)) == 4


def test_ticket_queries_are_scoped_to_requester(tmp_path) -> None:
    sessions, projection = build_services(tmp_path)
    run_id = create_waiting_workflow(sessions)
    link = projection.sync(
        run_id,
        requester_id="EMP-1001",
        title="Generic service request",
    )

    own = projection.list_for_requester("EMP-1001")
    assert [ticket.ticket_id for ticket in own] == [link.ticket_id]
    assert projection.list_for_requester("EMP-9999") == ()

    try:
        projection.get_for_requester(link.ticket_id, requester_id="EMP-9999")
    except TicketActorMismatchError:
        pass
    else:
        raise AssertionError("another requester must not read the ticket")


def test_subject_reference_can_follow_a_corrected_scenario_resource(tmp_path) -> None:
    sessions, projection = build_services(tmp_path)
    run_id = create_waiting_workflow(sessions)
    link = projection.sync(
        run_id,
        requester_id="EMP-1001",
        title="Equipment maintenance request",
        subject_reference="PRESS",
    )

    first = projection.get_for_requester(link.ticket_id, requester_id="EMP-1001")
    assert first.subject_reference == "PRESS"

    projection.sync(run_id, subject_reference="PRESS-001")
    corrected = projection.get_for_requester(
        link.ticket_id,
        requester_id="EMP-1001",
    )
    assert corrected.subject_reference == "PRESS-001"
