"""Administración: sincronizar el padrón, crear encuestas y moderar comentarios."""
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Comment, Party, Survey
from app.routes.encuestas import crear_encuesta
from app.schemas import PartyLogoIn, PartyOut, SurveyIn, SurveyOut
from app.security import admin_user
from app.asistencia import sincronizar_asistencia
from app.sync import sincronizar_todo

router = APIRouter(prefix="/admin", tags=["Administración"], dependencies=[Depends(admin_user)])


async def _sync_en_segundo_plano(wikipedia: bool) -> None:
    db = SessionLocal()
    try:
        await sincronizar_todo(db, wikipedia=wikipedia)
    finally:
        db.close()


@router.post("/sincronizar", status_code=202,
             summary="Carga partidos, diputados (Cámara), senadores (Senado) y descripciones (Wikipedia)")
async def sincronizar(background: BackgroundTasks, wikipedia: bool = True):
    background.add_task(_sync_en_segundo_plano, wikipedia)
    return {"message": "Sincronización iniciada; revisa los logs o GET /parlamentarios en unos minutos"}


async def _asistencia_en_segundo_plano(desde: str | None, recargar_dias: int) -> None:
    db = SessionLocal()
    try:
        await sincronizar_asistencia(db, desde=desde, recargar_dias=recargar_dias)
    finally:
        db.close()


@router.post("/asistencia/sincronizar", status_code=202,
             summary="Carga la asistencia a la Sala y las justificaciones (WSSala) desde una fecha")
async def sincronizar_asistencia_admin(background: BackgroundTasks, desde: date | None = None,
                                       recargar_dias: int = Query(30, ge=0, le=3650)):
    background.add_task(_asistencia_en_segundo_plano, desde.isoformat() if desde else None, recargar_dias)
    return {"message": "Carga de asistencia iniciada; revisa GET /asistencia/diputados en unos minutos"}


@router.post("/encuestas", response_model=SurveyOut, status_code=201)
def nueva_encuesta(body: SurveyIn, db: Session = Depends(get_db)):
    return crear_encuesta(db, body)


class SurveyState(BaseModel):
    active: bool


@router.patch("/encuestas/{survey_id}", response_model=SurveyOut, summary="Activar o cerrar una encuesta")
def estado_encuesta(survey_id: int, body: SurveyState, db: Session = Depends(get_db)):
    s = db.get(Survey, survey_id)
    if not s:
        raise HTTPException(status_code=404, detail="Encuesta no encontrada")
    s.active = body.active
    db.commit()
    db.refresh(s)
    return s


class Moderation(BaseModel):
    hidden: bool


@router.patch("/comentarios/{comment_id}", summary="Ocultar o mostrar un comentario")
def moderar(comment_id: int, body: Moderation, db: Session = Depends(get_db)):
    c = db.get(Comment, comment_id)
    if not c:
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    c.hidden = body.hidden
    db.commit()
    return {"id": c.id, "hidden": c.hidden}


@router.patch("/partidos/{party_id}/logo", response_model=PartyOut,
              summary="Fijar a mano el logo de un partido (si la búsqueda en Wikimedia no lo encontró o eligió mal)")
def logo_partido(party_id: int, body: PartyLogoIn, db: Session = Depends(get_db)):
    p = db.get(Party, party_id)
    if not p:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p
