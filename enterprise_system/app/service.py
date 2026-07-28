"""Deterministic enterprise access operations behind HTTP and MCP boundaries."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    AccessRequestStatus,
    AccessRequestView,
    AccessVerificationResult,
    ApplicationView,
    CreateAccessRequestCommand,
    EmployeeView,
    EmploymentStatus,
    EnterpriseWriteResult,
    GrantApplicationAccessCommand,
    UserAccessView,
    VerifyApplicationAccessQuery,
    WriteResultStatus,
)
from enterprise_system.app.models import (
    AccessRequestRecord,
    ApplicationRecord,
    EmployeeRecord,
    ExternalIdempotencyRecord,
    UserAccessRecord,
)


class EnterpriseResourceNotFoundError(LookupError):
    """Raised when an authoritative enterprise resource is absent."""


class EnterpriseRuleViolationError(ValueError):
    """Raised when authoritative enterprise data forbids the operation."""


class EnterpriseIdempotencyConflictError(RuntimeError):
    """Raised when one external idempotency key is reused with new content."""


class EnterpriseAccessService:
    """Own enterprise domain rules without depending on BizOrch internals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def query_employee(self, employee_id: str) -> EmployeeView:
        record = self._session.get(EmployeeRecord, employee_id)
        if record is None:
            raise EnterpriseResourceNotFoundError(f"employee:{employee_id}")
        employment_status = EmploymentStatus(record.employment_status)
        expected_active = employment_status is EmploymentStatus.ACTIVE
        if record.active is not expected_active:
            raise EnterpriseRuleViolationError(
                "employee active flag conflicts with employment status"
            )
        return EmployeeView(
            employee_id=record.employee_id,
            display_name=record.display_name,
            department_code=record.department_code,
            manager_id=record.manager_id,
            active=expected_active,
            employment_status=employment_status.value,
            job_code=record.job_code,
            work_location_code=record.work_location_code,
            version=record.version,
            updated_at=record.updated_at,
        )

    def query_employee_manager(self, employee_id: str) -> EmployeeView:
        employee = self.query_employee(employee_id)
        if employee.manager_id is None:
            raise EnterpriseResourceNotFoundError(f"manager:{employee_id}")
        return self.query_employee(employee.manager_id)

    def query_application(self, application_code: str) -> ApplicationView:
        record = self._session.get(ApplicationRecord, application_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(
                f"application:{application_code}"
            )
        return ApplicationView(
            application_code=record.application_code,
            display_name=record.display_name,
            active=record.active,
            allowed_role_codes=tuple(record.allowed_role_codes),
        )

    def query_user_access(self, employee_id: str) -> tuple[UserAccessView, ...]:
        self.query_employee(employee_id)
        records = self._session.scalars(
            select(UserAccessRecord)
            .where(UserAccessRecord.employee_id == employee_id)
            .order_by(UserAccessRecord.application_code, UserAccessRecord.role_code)
        ).all()
        return tuple(self._access_view(record) for record in records)

    def query_access_request(self, request_id: str) -> AccessRequestView:
        record = self._session.get(AccessRequestRecord, request_id)
        if record is None:
            raise EnterpriseResourceNotFoundError(f"access_request:{request_id}")
        return self._request_view(record)

    def create_access_request(
        self,
        command: CreateAccessRequestCommand,
        *,
        idempotency_key: str,
    ) -> EnterpriseWriteResult:
        digest = self._command_digest(command)
        replay = self._find_replay(
            idempotency_key,
            operation_type="create_access_request",
            request_digest=digest,
        )
        if replay is not None:
            return replay

        self._require_grantable(
            command.employee_id, command.application_code, command.role_code
        )
        if self._session.get(AccessRequestRecord, command.request_id) is not None:
            raise EnterpriseRuleViolationError("access request id already exists")

        record = AccessRequestRecord(
            request_id=command.request_id,
            employee_id=command.employee_id,
            application_code=command.application_code,
            role_code=command.role_code,
            duration_days=command.duration_days,
            business_reason=command.business_reason,
            status=AccessRequestStatus.CREATED.value,
        )
        self._session.add(record)
        result = EnterpriseWriteResult(
            status=WriteResultStatus.CREATED,
            resource_id=command.request_id,
        )
        self._save_idempotency(
            idempotency_key,
            "create_access_request",
            digest,
            result,
        )
        self._session.flush()
        return result

    def grant_application_access(
        self,
        command: GrantApplicationAccessCommand,
        *,
        idempotency_key: str,
    ) -> EnterpriseWriteResult:
        digest = self._command_digest(command)
        replay = self._find_replay(
            idempotency_key,
            operation_type="grant_application_access",
            request_digest=digest,
        )
        if replay is not None:
            return replay

        self._require_grantable(
            command.employee_id, command.application_code, command.role_code
        )
        request_record = self._validate_matching_request(command)
        record = self._session.scalar(
            select(UserAccessRecord).where(
                UserAccessRecord.employee_id == command.employee_id,
                UserAccessRecord.application_code == command.application_code,
                UserAccessRecord.role_code == command.role_code,
            )
        )
        expires_at = datetime.now(UTC) + timedelta(days=command.duration_days)
        if record is not None and self._is_active(record):
            status = WriteResultStatus.ALREADY_PRESENT
        else:
            status = WriteResultStatus.GRANTED
            if record is None:
                record = UserAccessRecord(
                    access_id=str(uuid4()),
                    employee_id=command.employee_id,
                    application_code=command.application_code,
                    role_code=command.role_code,
                    expires_at=expires_at,
                    active=True,
                )
                self._session.add(record)
            else:
                record.expires_at = expires_at
                record.active = True

        if request_record is not None:
            request_record.status = AccessRequestStatus.GRANTED.value
        result = EnterpriseWriteResult(status=status, resource_id=record.access_id)
        self._save_idempotency(
            idempotency_key,
            "grant_application_access",
            digest,
            result,
        )
        self._session.flush()
        return result

    def verify_application_access(
        self,
        query: VerifyApplicationAccessQuery,
    ) -> AccessVerificationResult:
        record = self._session.scalar(
            select(UserAccessRecord).where(
                UserAccessRecord.employee_id == query.employee_id,
                UserAccessRecord.application_code == query.application_code,
                UserAccessRecord.role_code == query.role_code,
            )
        )
        if record is None or not self._is_active(record):
            return AccessVerificationResult(confirmed=False)
        return AccessVerificationResult(
            confirmed=True,
            access=self._access_view(record),
        )

    def _require_grantable(
        self,
        employee_id: str,
        application_code: str,
        role_code: str,
    ) -> None:
        employee = self.query_employee(employee_id)
        application = self.query_application(application_code)
        if not employee.active:
            raise EnterpriseRuleViolationError("employee is inactive")
        if not application.active:
            raise EnterpriseRuleViolationError("application is inactive")
        if role_code not in application.allowed_role_codes:
            raise EnterpriseRuleViolationError("role is not allowed by application")

    def _validate_matching_request(
        self,
        command: GrantApplicationAccessCommand,
    ) -> AccessRequestRecord | None:
        if command.access_request_id is None:
            return None
        record = self._session.get(AccessRequestRecord, command.access_request_id)
        if record is None:
            raise EnterpriseResourceNotFoundError(
                f"access_request:{command.access_request_id}"
            )
        expected = (
            record.employee_id,
            record.application_code,
            record.role_code,
            record.duration_days,
        )
        actual = (
            command.employee_id,
            command.application_code,
            command.role_code,
            command.duration_days,
        )
        if actual != expected:
            raise EnterpriseRuleViolationError(
                "grant content does not match access request"
            )
        return record

    def _find_replay(
        self,
        key: str,
        *,
        operation_type: str,
        request_digest: str,
    ) -> EnterpriseWriteResult | None:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("idempotency_key must not be blank")
        record = self._session.get(ExternalIdempotencyRecord, normalized_key)
        if record is None:
            return None
        if (
            record.operation_type != operation_type
            or record.request_digest != request_digest
        ):
            raise EnterpriseIdempotencyConflictError(normalized_key)
        replayed = EnterpriseWriteResult.model_validate(record.response_payload)
        return replayed.model_copy(
            update={"status": WriteResultStatus.REPLAYED, "replayed": True}
        )

    def _save_idempotency(
        self,
        key: str,
        operation_type: str,
        request_digest: str,
        result: EnterpriseWriteResult,
    ) -> None:
        self._session.add(
            ExternalIdempotencyRecord(
                key=key.strip(),
                operation_type=operation_type,
                request_digest=request_digest,
                response_payload=result.model_dump(mode="json"),
            )
        )

    @staticmethod
    def _command_digest(command: BaseModel) -> str:
        canonical = json.dumps(
            command.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_active(record: UserAccessRecord) -> bool:
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return record.active and expires_at > datetime.now(UTC)

    @classmethod
    def _access_view(cls, record: UserAccessRecord) -> UserAccessView:
        return UserAccessView(
            access_id=record.access_id,
            employee_id=record.employee_id,
            application_code=record.application_code,
            role_code=record.role_code,
            expires_at=record.expires_at,
            active=cls._is_active(record),
        )

    @staticmethod
    def _request_view(record: AccessRequestRecord) -> AccessRequestView:
        return AccessRequestView(
            request_id=record.request_id,
            employee_id=record.employee_id,
            application_code=record.application_code,
            role_code=record.role_code,
            duration_days=record.duration_days,
            business_reason=record.business_reason,
            status=AccessRequestStatus(record.status),
        )
