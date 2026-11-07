"""Perfil y preferencias del usuario autenticado."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserPreferences
from app.schemas import UserPreferencesSchema, UserSchema
from app.security import current_user

router = APIRouter(prefix="/user", tags=["Usuario"])


@router.get("/me", response_model=UserSchema)
async def me(user: User = Depends(current_user)):
    return user


@router.get("/me/preferences", response_model=UserPreferencesSchema)
async def get_preferences(user: User = Depends(current_user)):
    if not user.preferences:
        raise HTTPException(status_code=404, detail="El usuario aún no tiene preferencias")
    return user.preferences


@router.post("/me/preferences", response_model=UserPreferencesSchema)
async def save_preferences(body: UserPreferencesSchema, user: User = Depends(current_user),
                           db: Session = Depends(get_db)):
    prefs = user.preferences or UserPreferences(user_id=user.id)
    for key, value in body.model_dump().items():
        setattr(prefs, key, value)
    db.add(prefs)
    db.commit()
    db.refresh(prefs)
    return prefs
