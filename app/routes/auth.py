"""Login, renovación de tokens y contraseñas."""
import logging
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.config import WPP_ENDPOINT, WPP_TOKEN
from app.database import get_db
from app.models import User, UserCredentials, UserPreferences
from app.routes.usuario import aplicar_preferencias
from app.rut import normalizar_rut
from app.schemas import (ChangePasswordSchema, LoginSchema, RefreshTokenSchema, RegisterSchema,
                         RestorePasswordSchema, TokenResponse)
from app.security import (authenticate, current_user, decode, get_active_user, get_credentials,
                          hash_password, limiter, token_pair)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/register", response_model=TokenResponse, status_code=201, summary="Crear una cuenta")
@limiter.limit("5/minute")
async def register(body: RegisterSchema, request: Request, db: Session = Depends(get_db)):
    rut = normalizar_rut(body.fiscal_id)
    if not rut:
        raise HTTPException(status_code=422, detail="RUT inválido")
    email = body.email.strip().lower()
    if db.query(User).filter(User.fiscal_id == rut).first():
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese RUT")
    if get_credentials(db, email):
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese correo")

    user = User(fiscal_id=rut, names=body.names.strip(), last_names=body.last_names.strip(), email=email,
                phone_number=body.phone_number, enabled=True, nationality="Chilena")
    db.add(user)
    db.flush()
    db.add(UserCredentials(user_id=user.id, username=email, hashed_password=hash_password(body.password)))
    cambios = body.model_dump(include={"commune_id", "district_vote", "voted_parliamentarian_id"}, exclude_none=True)
    if cambios:
        prefs = UserPreferences(user_id=user.id)
        await aplicar_preferencias(db, prefs, cambios)  # 422 si el diputado no es de su distrito
        db.add(prefs)
    db.commit()
    db.refresh(user)
    return token_pair(user, False, get_remote_address(request))


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(body: LoginSchema, request: Request, db: Session = Depends(get_db)):
    cred = authenticate(db, body.username.strip().lower(), body.password)
    if not cred:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    user = get_active_user(db, cred.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado o deshabilitado")
    return token_pair(user, cred.has_change_password, get_remote_address(request))


@router.post("/refresh-token", response_model=TokenResponse)
async def refresh_token(body: RefreshTokenSchema, request: Request, db: Session = Depends(get_db)):
    payload = decode(body.refresh_token, "refresh")
    user = get_active_user(db, int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado o deshabilitado")
    must_change = bool(user.credentials and user.credentials.has_change_password)
    return token_pair(user, must_change, get_remote_address(request))


@router.post("/change-password", response_model=TokenResponse)
async def change_password(body: ChangePasswordSchema, request: Request,
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    cred = user.credentials
    if not cred:
        raise HTTPException(status_code=404, detail="Credenciales no encontradas")
    cred.hashed_password = hash_password(body.new_password)
    cred.has_change_password = False
    cred.password_last_updated_at = datetime.now(timezone.utc)
    db.commit()
    return token_pair(user, False, get_remote_address(request))


@router.post("/restore-password")
@limiter.limit("3/minute")
async def restore_password(body: RestorePasswordSchema, request: Request, db: Session = Depends(get_db)):
    """Envía una clave temporal por WhatsApp. Requiere WPP_ENDPOINT y WPP_TOKEN."""
    if not WPP_ENDPOINT:
        raise HTTPException(status_code=503, detail="El envío de clave temporal no está configurado (WPP_ENDPOINT)")
    cred = get_credentials(db, body.username.strip().lower())
    user = get_active_user(db, cred.user_id) if cred else None
    if not user or not user.phone_number:
        raise HTTPException(status_code=404, detail="Usuario sin teléfono registrado")

    temporal = f"{secrets.randbelow(900000) + 100000}"
    try:
        # Ajustar el formato del mensaje al proveedor de WhatsApp real
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(WPP_ENDPOINT, headers={"Authorization": f"Bearer {WPP_TOKEN}"}, json={
                "to": user.phone_number,
                "params": [{"value": f"{user.names} {user.last_names}"}, {"value": body.username}, {"value": temporal}],
            })
            r.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"Error enviando clave temporal: {e}")
        raise HTTPException(status_code=502, detail="No se pudo enviar la clave temporal")

    cred.hashed_password = hash_password(temporal)  # solo si el envío funcionó
    cred.has_change_password = True
    db.commit()
    return {"message": "Clave temporal enviada"}
