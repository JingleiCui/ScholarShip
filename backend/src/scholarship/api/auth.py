from dataclasses import dataclass
import hashlib
import secrets
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
import sqlalchemy as sa

from scholarship.config import settings
from scholarship.infrastructure.database import engine
from scholarship.infrastructure.schema import tables

router = APIRouter(prefix="/auth")
hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_hex(32))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Identity:
    user_id: UUID
    username: str
    display_name: str
    role: str
    csrf_hash: str

    def public(self):
        return {
            "id": str(self.user_id),
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
        }


def require_origin(request: Request):
    if request.headers.get("origin") not in settings().allowed_origins:
        raise HTTPException(403, "origin_not_allowed")


def identity(request: Request):
    token = request.cookies.get("ss_session", "")
    with engine("auth").connect() as conn:
        row = (
            conn.execute(sa.text("SELECT * FROM scholar.session_lookup(:token)"), {"token": digest(token)})
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(401, "authentication_required")
    return Identity(**row)


def mutation_identity(request: Request, user: Identity = Depends(identity)):
    require_origin(request)
    if not secrets.compare_digest(digest(request.headers.get("x-csrf-token", "")), user.csrf_hash):
        raise HTTPException(403, "csrf_required")
    return user


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login", dependencies=[Depends(require_origin)])
def login(body: Login, response: Response):
    with engine("auth").begin() as conn:
        row = (
            conn.execute(sa.select(tables["users"]).where(tables["users"].c.username == body.username))
            .mappings()
            .first()
        )
        try:
            valid = hasher.verify(row["password_hash"] if row else DUMMY_HASH, body.password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid or row is None or row["disabled_at"] is not None:
            raise HTTPException(401, "invalid_credentials")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        conn.execute(
            sa.text("SELECT scholar.session_create(:uid,:token,:csrf)"),
            {"uid": row["id"], "token": digest(token), "csrf": digest(csrf)},
        )
    response.set_cookie(
        "ss_session", token, httponly=True, secure=settings().cookie_secure, samesite="lax", max_age=28800, path="/"
    )
    response.set_cookie(
        "ss_csrf", csrf, httponly=False, secure=settings().cookie_secure, samesite="lax", max_age=28800, path="/"
    )
    return {k: str(row[k]) for k in ("id", "username", "display_name", "role")}


@router.get("/me")
def me(user: Identity = Depends(identity)):
    return user.public()


@router.post("/logout")
def logout(request: Request, response: Response, user: Identity = Depends(mutation_identity)):
    with engine("auth").begin() as conn:
        conn.execute(sa.text("SELECT scholar.session_revoke(:token)"), {"token": digest(request.cookies["ss_session"])})
    response.delete_cookie("ss_session", path="/")
    response.delete_cookie("ss_csrf", path="/")
    return {"signed_out": True}
