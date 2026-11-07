"""Perfil y preferencias del usuario autenticado: comuna, distrito y a quién votó."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.distritos import diputados_del_distrito, distrito_de_comuna, ultima_votacion_id, votos_del_distrito
from app.models import Parliamentarian, User, UserPreferences
from app.schemas import MiDistritoOut, UserPreferencesSchema, UserSchema
from app.security import current_user

router = APIRouter(prefix="/user", tags=["Usuario"])


async def _distrito_seguro(commune_id: Optional[int]) -> Optional[int]:
    """Distrito de la comuna; None si no se sabe o la Cámara no responde."""
    try:
        return await distrito_de_comuna(commune_id)
    except HTTPException:
        return None


async def aplicar_preferencias(db: Session, prefs: UserPreferences, cambios: dict) -> None:
    """Aplica los campos enviados y valida que el diputado votado sea de su distrito."""
    toca_voto = "district_vote" in cambios or "voted_parliamentarian_id" in cambios
    if cambios.get("voted_parliamentarian_id") and not cambios.get("district_vote"):
        cambios["district_vote"] = "diputado"
    for key, value in cambios.items():
        setattr(prefs, key, value)
    if prefs.district_vote != "diputado":
        prefs.voted_parliamentarian_id = None
        return

    if not prefs.voted_parliamentarian_id:
        raise HTTPException(status_code=422, detail="Indica cuál diputado votaste (voted_parliamentarian_id)")
    dip = db.get(Parliamentarian, prefs.voted_parliamentarian_id)
    if not dip or dip.chamber != "diputado":
        raise HTTPException(status_code=422, detail="El diputado elegido no existe")
    distrito = await _distrito_seguro(prefs.commune_id)
    if distrito and dip.district and dip.district != str(distrito):
        if toca_voto:
            raise HTTPException(status_code=422, detail=f"{dip.full_name} no es diputado del distrito {distrito}")
        prefs.district_vote, prefs.voted_parliamentarian_id = None, None  # cambió de comuna: se borra


@router.get("/me", response_model=UserSchema)
async def me(user: User = Depends(current_user)):
    return user


@router.get("/me/preferences", response_model=UserPreferencesSchema)
async def get_preferences(user: User = Depends(current_user)):
    if not user.preferences:
        raise HTTPException(status_code=404, detail="El usuario aún no tiene preferencias")
    return user.preferences


@router.post("/me/preferences", response_model=UserPreferencesSchema,
             summary="Guardar comuna y a quién votó (solo cambian los campos enviados)")
async def save_preferences(body: UserPreferencesSchema, user: User = Depends(current_user),
                           db: Session = Depends(get_db)):
    prefs = user.preferences or UserPreferences(user_id=user.id)
    await aplicar_preferencias(db, prefs, body.model_dump(exclude_unset=True))
    db.add(prefs)
    db.commit()
    db.refresh(prefs)
    return prefs


@router.get("/me/distrito", response_model=MiDistritoOut,
            summary="Distrito del usuario (según su comuna), sus diputados y a quién votó")
async def mi_distrito(user: User = Depends(current_user), db: Session = Depends(get_db)):
    prefs = user.preferences
    distrito = await distrito_de_comuna(prefs.commune_id if prefs else None)
    return MiDistritoOut(
        commune_id=prefs.commune_id if prefs else None, distrito=distrito,
        district_vote=prefs.district_vote if prefs else None,
        voted_parliamentarian_id=prefs.voted_parliamentarian_id if prefs else None,
        diputados=diputados_del_distrito(db, distrito) if distrito else [],
    )


async def _votos_mi_distrito(db: Session, user: User, votacion_id: int) -> dict:
    prefs = user.preferences
    distrito = await distrito_de_comuna(prefs.commune_id if prefs else None)
    if not distrito:
        raise HTTPException(status_code=404, detail="Elige tu comuna en el perfil para ver tu distrito")
    return await votos_del_distrito(db, votacion_id, distrito, prefs.voted_parliamentarian_id)


@router.get("/me/distrito/votaciones/ultima", tags=["Votaciones"],
            summary="Cómo votaron los diputados de mi distrito en la última votación de Sala")
async def ultima_votacion_mi_distrito(user: User = Depends(current_user), db: Session = Depends(get_db)):
    votacion_id = await ultima_votacion_id()
    if not votacion_id:
        raise HTTPException(status_code=404, detail="No hay votaciones recientes")
    return await _votos_mi_distrito(db, user, votacion_id)


@router.get("/me/distrito/votaciones/{votacion_id}", tags=["Votaciones"],
            summary="Cómo votaron los diputados de mi distrito en una votación (el mío aparece primero)")
async def votacion_mi_distrito(votacion_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return await _votos_mi_distrito(db, user, votacion_id)
