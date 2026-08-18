from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.auth import verify_password
from kioskarr.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

# auto_error=False: a missing Authorization header should fall through to the
# session-cookie check below, not immediately 401 — OPDS clients use Basic auth,
# the SPA uses the session cookie, and either should work.
_basic_auth = HTTPBasic(auto_error=False)


class AuthStatus(BaseModel):
    auth_required: bool
    authenticated: bool


class LoginPayload(BaseModel):
    username: str
    password: str


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, db: Session = Depends(get_db)) -> AuthStatus:
    app_settings = get_app_settings(db)
    auth_required = bool(app_settings.admin_password_hash)
    return AuthStatus(
        auth_required=auth_required,
        authenticated=bool(request.session.get("authenticated")) if auth_required else True,
    )


@router.post("/login", response_model=AuthStatus)
def login(payload: LoginPayload, request: Request, db: Session = Depends(get_db)) -> AuthStatus:
    app_settings = get_app_settings(db)
    if payload.username != app_settings.admin_username or not verify_password(
        payload.password, app_settings.admin_password_hash
    ):
        raise HTTPException(401, "Invalid username or password")
    request.session["authenticated"] = True
    return AuthStatus(auth_required=True, authenticated=True)


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


def require_auth(request: Request, db: Session = Depends(get_db)) -> None:
    app_settings = get_app_settings(db)
    if not app_settings.admin_password_hash:
        return  # auth disabled — no password has ever been set
    if not request.session.get("authenticated"):
        raise HTTPException(401, "Authentication required")


def require_auth_or_basic(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic_auth),
    db: Session = Depends(get_db),
) -> None:
    """Like require_auth, but also accepts HTTP Basic — for routes consumed by
    non-browser clients (OPDS readers) that can't do the session-cookie login flow."""
    app_settings = get_app_settings(db)
    if not app_settings.admin_password_hash:
        return  # auth disabled — no password has ever been set
    if request.session.get("authenticated"):
        return
    if (
        credentials is not None
        and credentials.username == app_settings.admin_username
        and verify_password(credentials.password, app_settings.admin_password_hash)
    ):
        return
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="Kioskarr"'})
