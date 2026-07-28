"""Explicit, idempotent demo-user initialization for local demonstrations."""

from app.auth.contracts import AuthPrincipal, RoleName
from app.auth.service import AuthService


def seed_demo_users(
    auth: AuthService,
    *,
    employee_password: str,
    manager_password: str,
    operator_password: str,
    hr_password: str,
) -> tuple[AuthPrincipal, ...]:
    employee = auth.ensure_user(
        employee_id="EMP-1001",
        username="employee",
        password=employee_password,
        roles=frozenset({RoleName.EMPLOYEE}),
    )
    manager = auth.ensure_user(
        employee_id="EMP-MANAGER",
        username="manager",
        password=manager_password,
        roles=frozenset({RoleName.EMPLOYEE, RoleName.APPROVER}),
    )
    operator = auth.ensure_user(
        employee_id="EMP-KNOWLEDGE-OPERATOR",
        username="operator",
        password=operator_password,
        roles=frozenset({RoleName.OPERATOR}),
    )
    hr = auth.ensure_user(
        employee_id="EMP-HR-OPERATOR",
        username="hr",
        password=hr_password,
        roles=frozenset({RoleName.HR}),
    )
    budget_owner = auth.ensure_user(
        employee_id="EMP-BUDGET-OWNER",
        username="budget.owner",
        password=manager_password,
        roles=frozenset({RoleName.APPROVER}),
    )
    procurement_owner = auth.ensure_user(
        employee_id="EMP-PROCUREMENT-OWNER",
        username="procurement.owner",
        password=manager_password,
        roles=frozenset({RoleName.APPROVER}),
    )
    return employee, manager, operator, hr, budget_owner, procurement_owner
