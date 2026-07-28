"""Short-transaction adapters for approval and proposal execution checks."""

from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionProposal
from app.actions.repository import ActionProposalRepository
from app.approval.repository import ApprovalRepository


class PersistentApprovalValidator:
    """Validate approval in a fresh authoritative database transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def require_approved(self, approval_id: str, proposal: ActionProposal) -> object:
        with self._session_factory() as session:
            return ApprovalRepository(session).require_approved(approval_id, proposal)


class PersistentProposalVersionValidator:
    """Validate latest proposal version without reusing a stale ORM session."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def require_current(self, proposal: ActionProposal) -> None:
        with self._session_factory() as session:
            ActionProposalRepository(session).require_current(proposal)

