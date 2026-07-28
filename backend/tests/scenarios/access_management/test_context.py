from datetime import UTC, datetime, timedelta

from app.integrations.enterprise_ops import (
    EnterpriseOpsApplication,
    EnterpriseOpsEmployee,
    EnterpriseOpsUserAccess,
)
from app.scenarios.access_management.context import AccessRequestContextResolver
from app.scenarios.access_management.contracts import AccessRequestDraft


class FakeReadOnlyClient:
    def __init__(self) -> None:
        self.application_queries = 0

    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee:
        return EnterpriseOpsEmployee(
            employee_id=employee_id,
            display_name="Lin Employee",
            department_code="SALES-EAST",
            manager_id="EMP-MANAGER",
            active=True,
        )

    def query_application(self, application_code: str) -> EnterpriseOpsApplication:
        self.application_queries += 1
        return EnterpriseOpsApplication(
            application_code=application_code,
            display_name="CRM",
            active=True,
            allowed_role_codes=("read_only",),
        )

    def query_user_access(self, employee_id: str):
        expires_at = datetime.now(UTC) + timedelta(days=10)
        return (
            EnterpriseOpsUserAccess(
                access_id="access-1",
                employee_id=employee_id,
                application_code="CRM",
                role_code="read_only",
                expires_at=expires_at,
                active=True,
            ),
            EnterpriseOpsUserAccess(
                access_id="access-2",
                employee_id=employee_id,
                application_code="ERP",
                role_code="admin",
                expires_at=expires_at,
                active=True,
            ),
        )


def test_context_is_derived_from_read_only_enterprise_facts() -> None:
    client = FakeReadOnlyClient()
    resolver = AccessRequestContextResolver(client)

    context = resolver.resolve(
        AccessRequestDraft(
            employee_id="EMP-1001",
            application_code="CRM",
        )
    )

    assert context.manager_id == "EMP-MANAGER"
    assert context.existing_role_codes == frozenset({"read_only"})
    assert client.application_queries == 1


def test_context_does_not_query_missing_identifiers() -> None:
    client = FakeReadOnlyClient()
    resolver = AccessRequestContextResolver(client)

    context = resolver.resolve(AccessRequestDraft())

    assert context.manager_id is None
    assert context.existing_role_codes == frozenset()
    assert client.application_queries == 0
