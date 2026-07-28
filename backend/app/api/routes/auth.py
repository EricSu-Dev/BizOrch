"""Password login, current identity and session logout endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.dependencies import (
    CurrentActor,
    get_auth_service,
    get_bearer_token,
    get_current_actor,
)
from app.auth.contracts import AvatarKey, AuthPrincipal, LoginResult
from app.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100)
    password: SecretStr


class UpdateProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=100,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    avatar_key: AvatarKey


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=8, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)


@router.post("/login", response_model=LoginResult)
def login(
    body: LoginBody,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> LoginResult:
    return auth.login(
        username=body.username,
        password=body.password.get_secret_value(),
    )


@router.get("/me", response_model=AuthPrincipal)
def current_user(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthPrincipal:
    return auth.get_profile(actor.user_id)


@router.patch("/me", response_model=AuthPrincipal)
def update_current_user(
    body: UpdateProfileBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthPrincipal:
    return auth.update_profile(
        employee_id=actor.user_id,
        username=body.username,
        avatar_key=body.avatar_key,
    )


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_current_password(
    body: ChangePasswordBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    token: Annotated[str, Depends(get_bearer_token)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    auth.change_password(
        employee_id=actor.user_id,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
        current_token=token,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/avatar", response_model=AuthPrincipal)
async def upload_current_avatar(
    file: Annotated[UploadFile, File()],
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthPrincipal:
    content_type = file.content_type
    try:
        content = await file.read(AuthService.MAX_AVATAR_BYTES + 1)
    finally:
        await file.close()
    return auth.update_avatar(
        employee_id=actor.user_id,
        content=content,
        content_type=content_type,
    )


@router.get("/me/avatar")
def current_avatar(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    public_url = auth.get_avatar_url(actor.user_id)
    if public_url:
        return RedirectResponse(
            url=public_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    content, content_type, version = auth.get_avatar(actor.user_id)
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{version}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/me/avatar", response_model=AuthPrincipal)
def delete_current_avatar(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthPrincipal:
    return auth.clear_avatar(actor.user_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: Annotated[str, Depends(get_bearer_token)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    auth.logout(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
