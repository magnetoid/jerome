import math
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from jerome.api.auth import LoginThrottle, client_ip
from jerome.api.deps import (
    SESSION_COOKIE,
    Principal,
    SessionDep,
    clear_session_cookie,
    current_principal,
    get_settings_dep,
    set_session_cookie,
)
from jerome.config import Settings
from jerome.core import users
from jerome.core.sessions import create_session, revoke_session
from jerome.orm import User

# Included without the router-level auth dependency: login needs none, and the other
# routes ask for the principal themselves.
router = APIRouter()

SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
PrincipalDep = Annotated[Principal, Depends(current_principal)]


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


class MeOut(BaseModel):
    id: int | None
    email: str | None
    name: str
    must_change_password: bool


def _me(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        must_change_password=user.must_change_password,
    )


@router.post("/auth/login", response_model=MeOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
):
    throttle: LoginThrottle = request.app.state.login_throttle
    email_key = f"email:{users.normalize_email(body.email)}"
    keys = [email_key]
    if (ip := client_ip(request, settings.trusted_proxies)) is not None:
        keys.append(f"ip:{ip}")
    if (wait := throttle.check(keys)) is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many failed logins; try again later",
            headers={"Retry-After": str(math.ceil(wait))},
        )
    user = users.authenticate(session, body.email, body.password)
    if user is None:
        throttle.fail(keys)
        raise HTTPException(status_code=401, detail="Wrong email or password")
    throttle.succeed([email_key])
    token = create_session(session, user, days=settings.session_days, now=datetime.now(UTC))
    set_session_cookie(response, token, settings)
    return _me(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, session: SessionDep, settings: SettingsDep, principal: PrincipalDep):
    if principal.channel == "web" and (token := request.cookies.get(SESSION_COOKIE)):
        revoke_session(session, token)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


@router.get("/auth/me", response_model=MeOut)
def me(session: SessionDep, principal: PrincipalDep):
    if principal.user_id is None:  # an API key
        return MeOut(id=None, email=None, name=principal.actor, must_change_password=False)
    user = session.get(User, principal.user_id)
    assert user is not None  # current_principal just resolved it
    return _me(user)


@router.post("/auth/password", status_code=204)
def change_password(
    body: PasswordIn, request: Request, session: SessionDep, principal: PrincipalDep
):
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="API keys have no password to change")
    user = session.get(User, principal.user_id)
    assert user is not None
    # The principal came from this cookie, so it names the session making the change: it
    # stays logged in and the person's other sessions end.
    current = request.cookies.get(SESSION_COOKIE)
    users.change_password(session, user, body.current, body.new, keep_token=current)
    return Response(status_code=204)
