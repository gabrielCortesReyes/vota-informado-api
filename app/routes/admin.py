"""Administración: sincronizar el padrón, crear encuestas y moderar comentarios."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Comment, Survey
from app.routes.encuestas import crear_encuesta
from app.schemas import SurveyIn, SurveyOut
from app.security import admin_user
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
