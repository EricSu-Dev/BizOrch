"""Read-only enterprise context resolution for access-request policy."""

from typing import Protocol

from app.integrations.enterprise_ops import (
    EnterpriseOpsApplication,
    EnterpriseOpsEmployee,
    EnterpriseOpsUserAccess,
)
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)


class AccessContextClientPort(Protocol):
    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee: ...

    def query_application(
        self, application_code: str
    ) -> EnterpriseOpsApplication: ...

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]: ...


class AccessRequestContextResolver:
    """Build trusted policy context only from read-only enterprise tools."""

    def __init__(self, client: AccessContextClientPort) -> None:
        self._client = client

    def resolve(self, draft: AccessRequestDraft) -> AccessRequestContext:
        manager_id: str | None = None
        existing_role_codes: frozenset[str] = frozenset()

        if draft.employee_id:
            employee = self._client.query_employee(draft.employee_id)
            manager_id = employee.manager_id
            accesses = self._client.query_user_access(draft.employee_id)
            if draft.application_code:
                existing_role_codes = frozenset(
                    access.role_code
                    for access in accesses
                    if access.active
                    and access.application_code == draft.application_code
                )

        if draft.application_code:
            self._client.query_application(draft.application_code)

        return AccessRequestContext(
            existing_role_codes=existing_role_codes,
            manager_id=manager_id,
        )
