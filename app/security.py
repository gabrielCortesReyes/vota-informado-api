"""Contraseñas (bcrypt) y tokens JWT de los usuarios de la app."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.config import ACCESS_TOKEN_MINUTES, ALGORITHM, REFRESH_TOKEN_DAYS, SECRET_KEY
from app.database import get_db
from app.models import User, UserCredentials

limiter = Limiter(key_func=get_remote_address)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def get_credentials(db: Session, username: str) -> Optional[UserCredentials]:
    return db.query(UserCredentials).filter(
        UserCredentials.username == username, UserCredentials.deleted_at.is_(None)
    ).first()


def get_active_user(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(
        User.id == user_id, User.enabled.is_(True), User.deleted_at.is_(None)
    ).first()


def authenticate(db: Session, username: str, password: str) -> Optional[UserCredentials]:
    cred = get_credentials(db, username)
    if not cred:
        return None
    if pwd_context.verify(password, cred.hashed_password):
        cred.failed_attempts = 0
        db.commit()
        return cred
    cred.failed_attempts = (cred.failed_attempts or 0) + 1
    cred.last_failed_attempt = datetime.now(timezone.utc)
    db.commit()
    return None


def _encode(data: dict, expires: timedelta) -> str:
    data["exp"] = datetime.now(timezone.utc) + expires
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def token_pair(user: User, must_change_password: bool, ip: Optional[str] = None) -> dict:
    access = {
        "sub": str(user.id), "user_id": user.id, "type": "access",
        "names": user.names, "last_names": user.last_names, "email": user.email,
        "fiscal_id": user.fiscal_id, "photo_url": user.photo_url,
        "need_change_password": bool(must_change_password),
    }
    refresh = {"sub": str(user.id), "type": "refresh", "ip": ip}
    return {
        "access_token": _encode(access, timedelta(minutes=ACCESS_TOKEN_MINUTES)),
        "refresh_token": _encode(refresh, timedelta(days=REFRESH_TOKEN_DAYS)),
        "token_type": "bearer",
    }


def decode(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido")
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise HTTPException(status_code=401, detail=f"Se requiere un {expected_type} token")
    return payload


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)) -> User:
    payload = decode(creds.credentials, "access")
    user = get_active_user(db, int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado o deshabilitado")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Requiere rol de administrador")
    return user
