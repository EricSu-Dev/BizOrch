"""Stable authentication and identity contracts."""

from enum import Enum

from pydantic import BaseModel, ConfigDict


class RoleName(str, Enum):
    EMPLOYEE = "employee"
    APPROVER = "approver"
    OPERATOR = "operator"
    HR = "hr"
    ADMIN = "admin"


class AvatarKey(str, Enum):
    PERSON = "person"
    OPERATIONS = "operations"
    APPROVAL = "approval"
    SHIELD = "shield"
    MAINTENANCE = "maintenance"
    ROBOT = "robot"


class AuthPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: str
    username: str
    roles: frozenset[RoleName]
    avatar_key: AvatarKey | None = None
    has_custom_avatar: bool = False
    avatar_version: str | None = None
    avatar_url: str | None = None


class LoginResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthPrincipal
