"""
backend/auth.py — Minimal JWT auth for the multi-user backend

Password hashing via passlib/bcrypt, tokens via PyJWT. Deliberately small:
this is enough to isolate one user's portfolio/watchlist from another's,
not a full identity system (no email verification, password reset flows,
OAuth, etc. — add those before exposing this beyond trusted internal use).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from .db import User, get_db

SECRET_KEY = os.getenv("BACKEND_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "BACKEND_SECRET_KEY is not set. Generate one (e.g. `openssl rand -hex 32`) "
        "and put it in .env — refusing to start with a default/blank secret."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24h

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Using the `bcrypt` library directly rather than passlib: passlib's bcrypt
# backend-version detection has a long-standing incompatibility with
# bcrypt>=4.1 (raises on init instead of hashing), which is exactly the
# kind of silently-fragile dependency this rework is trying to get away
# from. bcrypt truncates at 72 bytes by spec; we enforce that explicitly
# instead of letting a mismatched shim raise a confusing error.
_MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    pw_bytes = plain.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.checkpw(pw_bytes, hashed.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user
