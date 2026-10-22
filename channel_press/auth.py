from __future__ import annotations

import json
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .constants import ROLE_RANK
from .db import session_dependency
from .models import (
    AuditEvent,
    Channel,
    ChannelOwnership,
    ChannelPermission,
    LoginAttempt,
    LoginSession,
    RightsGrant,
    User,
)
from .security import (
    csrf_valid,
    hash_password,
    needs_rehash,
    new_csrf_token,
    new_session_token,
    session_expiry,
    token_digest,
    verify_password,
)
from .timeutil import utcnow

SESSION_COOKIE = "cp_session"


def audit(
    db: Session,
    actor: User | None,
    action: str,
    object_type: str = "",
    object_id: str | int = "",
    detail: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            detail_json=json.dumps(detail or {}, ensure_ascii=False, default=str),
        )
    )


def user_count(db: Session) -> int:
    return int(db.scalar(select(func.count(User.id))) or 0)


def create_first_owner(db: Session, username: str, password: str, display_name: str) -> User:
    if user_count(db) > 0:
        raise ValueError("An administrator already exists.")
    username = username.strip().lower()
    if not username or len(username) > 64:
        raise ValueError("Username is required (max 64 characters).")
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name.strip() or username,
        role="owner",
    )
    db.add(user)
    db.flush()
    audit(db, user, "user.create_owner", "user", user.id)
    return user


def authenticate(db: Session, username: str, password: str, ip: str = "") -> User:
    username = username.strip().lower()
    window_start = utcnow() - timedelta(minutes=settings.login_window_minutes)
    failures = db.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= window_start,
        )
    )
    if int(failures or 0) >= settings.login_max_attempts:
        raise PermissionError("Too many failed attempts. Try again later.")

    user = db.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
    ok = bool(user and verify_password(user.password_hash, password))
    db.add(LoginAttempt(username=username, success=ok, ip_address=ip[:64]))
    if not ok or user is None:
        db.flush()
        raise PermissionError("Invalid username or password.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    audit(db, user, "auth.login", "user", user.id)
    db.flush()
    return user


def create_login_session(db: Session, user: User, ip: str = "", agent: str = "") -> tuple[str, LoginSession]:
    raw = new_session_token()
    login = LoginSession(
        user_id=user.id,
        token_hash=token_digest(raw),
        csrf_token=new_csrf_token(),
        expires_at=session_expiry(settings.session_hours),
        ip_address=ip[:64],
        user_agent=agent[:255],
    )
    db.add(login)
    db.flush()
    return raw, login


def resolve_session(db: Session, raw_token: str | None) -> LoginSession | None:
    if not raw_token:
        return None
    login = db.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == token_digest(raw_token),
            LoginSession.revoked_at.is_(None),
        )
    )
    if login is None:
        return None
    if login.expires_at <= utcnow():
        login.revoked_at = utcnow()
        db.flush()
        return None
    return login


def revoke_session(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    login = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token)))
    if login:
        login.revoked_at = utcnow()
        db.flush()


def get_session_user(request: Request, db: Session) -> User | None:
    cached = getattr(request.state, "user", "unset")
    if cached != "unset":
        return cached
    raw = request.cookies.get(SESSION_COOKIE)
    login = resolve_session(db, raw)
    user = db.get(User, login.user_id) if login else None
    if user and user.is_active:
        request.state.user = user
        request.state.login_session = login
        return user
    request.state.user = None
    return None


def current_user(request: Request, db: Session = Depends(session_dependency)) -> User:
    user = get_session_user(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def optional_user(request: Request, db: Session = Depends(session_dependency)) -> User | None:
    return get_session_user(request, db)


def require_role(minimum: str):
    def dependency(request: Request, db: Session = Depends(session_dependency)) -> User:
        user = get_session_user(request, db)
        if not user:
            raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail="Insufficient permission.")
        return user

    return dependency


async def require_csrf(request: Request, db: Session = Depends(session_dependency)) -> None:
    user = get_session_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    login = getattr(request.state, "login_session", None)
    provided = request.headers.get("x-csrf-token")
    if not provided:
        form = await request.form()
        provided = str(form.get("csrf_token") or "")
    if login is None or not csrf_valid(login.csrf_token, provided):
        raise HTTPException(status_code=403, detail="CSRF validation failed. Reload and retry.")


def channel_role(db: Session, user: User, channel: Channel) -> str | None:
    if user.role == "owner":
        return "owner"
    ownership = db.scalar(select(ChannelOwnership).where(ChannelOwnership.channel_id == channel.id))
    if ownership and ownership.owner_user_id == user.id:
        return "owner"
    perm = db.scalar(
        select(ChannelPermission).where(
            ChannelPermission.channel_id == channel.id, ChannelPermission.user_id == user.id
        )
    )
    return perm.role if perm else None


def can_access_channel(db: Session, user: User, channel: Channel, minimum: str = "viewer") -> bool:
    role = channel_role(db, user, channel)
    if role is None:
        return False
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[minimum]


def require_channel_access(channel_id: int, minimum: str = "viewer"):
    def dependency(request: Request, db: Session = Depends(session_dependency)) -> User:
        user = get_session_user(request, db)
        if not user:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        channel = db.get(Channel, channel_id)
        if not channel or not can_access_channel(db, user, channel, minimum):
            raise HTTPException(status_code=403, detail="Channel access denied.")
        return user

    return dependency


def active_rights(db: Session, channel_id: int) -> RightsGrant | None:
    return db.scalar(
        select(RightsGrant)
        .where(RightsGrant.channel_id == channel_id, RightsGrant.revoked_at.is_(None))
        .order_by(RightsGrant.accepted_at.desc())
    )


def require_active_rights(db: Session, channel_id: int) -> RightsGrant:
    grant = active_rights(db, channel_id)
    if grant is None:
        raise PermissionError(
            "AI processing is blocked: no active source-rights declaration for this channel."
        )
    return grant


def request_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For by default; this is a local single-user app.
    return request.client.host if request.client else ""


def set_session_cookie(response, raw_token: str) -> None:  # type: ignore[no-untyped-def]
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_hours * 3600,
        path="/",
    )


def clear_session_cookie(response) -> None:  # type: ignore[no-untyped-def]
    response.delete_cookie(SESSION_COOKIE, path="/")
