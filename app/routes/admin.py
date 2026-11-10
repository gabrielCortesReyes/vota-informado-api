"""Administración: sincronizar el padrón, crear encuestas y moderar comentarios y reportes."""
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal, get_db
from app.models import Comment, CommentReport, Parliamentarian, Party, Survey
from app.routes.encuestas import crear_encuesta
from app.schemas import CommentReportOut, PartyLogoIn, PartyOut, ReportDecisionIn, SurveyIn, SurveyOut
from app.security import admin_user
from app.asistencia import sincronizar_asistencia
from app.patrimonio import cargar_declaracion, sincronizar_patrimonio
from app.routes.patrimonio import patrimonio_out
from app.schemas import PatrimonioOut
from app.sync import sincronizar_todo
from app.proyectos import sincronizar_proyectos

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


async def _proyectos_en_segundo_plano(desde_anno: int | None, limite: int) -> None:
    db = SessionLocal()
    try:
        await sincronizar_proyectos(db, desde_anno=desde_anno, limite=limite)
    finally:
        db.close()


@router.post("/proyectos/sincronizar", status_code=202,
             summary="Carga mociones y mensajes y pide el detalle (materias y autores) de los que falten")
async def sincronizar_proyectos_admin(background: BackgroundTasks, desde_anno: int | None = Query(None, ge=1990, le=2100),
                                      limite: int = Query(500, ge=1, le=5000)):
    background.add_task(_proyectos_en_segundo_plano, desde_anno, limite)
    return {"message": "Carga de proyectos iniciada; revisa GET /materias/categorias en unos minutos"}


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


async def _patrimonio_en_segundo_plano(forzar: bool) -> None:
    db = SessionLocal()
    try:
        await sincronizar_patrimonio(db, forzar=forzar)
    finally:
        db.close()


@router.post("/patrimonio/sincronizar", status_code=202,
             summary="Busca en InfoProbidad la declaración más reciente de cada diputado y carga las nuevas")
async def sincronizar_patrimonio_admin(background: BackgroundTasks, forzar: bool = False):
    background.add_task(_patrimonio_en_segundo_plano, forzar)
    return {"message": "Carga de patrimonio iniciada; revisa GET /patrimonio/diputados en unos minutos"}


@router.post("/patrimonio/{parl_id}", response_model=PatrimonioOut,
             summary="Carga a mano una declaración de InfoProbidad (el ID de la URL Declaracion?ID=...)")
async def cargar_patrimonio(parl_id: int, declaracion_id: int = Query(..., ge=1), db: Session = Depends(get_db)):
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    try:
        d = await cargar_declaracion(db, p, declaracion_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo leer la declaración en InfoProbidad: {e}")
    return patrimonio_out(d)


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


@router.delete("/comentarios/{comment_id}", status_code=204, summary="Eliminar un comentario (moderación)")
def eliminar_comentario(comment_id: int, db: Session = Depends(get_db)):
    c = db.get(Comment, comment_id)
    if not c:
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    c.deleted_at = c.deleted_at or datetime.now(timezone.utc)
    db.query(CommentReport).filter_by(comment_id=c.id, status="pendiente").update({"status": "resuelto"})
    db.commit()
    return Response(status_code=204)


def _reporte_out(r: CommentReport, conteo: dict[int, int]) -> CommentReportOut:
    c = r.comment
    return CommentReportOut(id=r.id, comment_id=c.id, comentario=c.text, autor=c.user.display_name,
                            autor_id=c.user_id, comentario_oculto=bool(c.hidden or c.deleted_at),
                            reportes_del_comentario=conteo.get(c.id, 0), motivo=r.motivo, detalle=r.detalle,
                            status=r.status, created_at=r.created_at)


@router.get("/reportes", response_model=list[CommentReportOut],
            summary="Comentarios reportados (los más nuevos primero)")
def reportes(status: Optional[Literal["pendiente", "resuelto", "descartado"]] = "pendiente",
             limit: int = Query(100, le=500), offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(CommentReport).options(joinedload(CommentReport.comment).joinedload(Comment.user))
    if status:
        query = query.filter(CommentReport.status == status)
    rows = query.order_by(CommentReport.created_at.desc(), CommentReport.id.desc()).offset(offset).limit(limit).all()
    ids = {r.comment_id for r in rows}
    conteo = dict(db.query(CommentReport.comment_id, func.count()).filter(CommentReport.comment_id.in_(ids))
                  .group_by(CommentReport.comment_id).all()) if ids else {}
    return [_reporte_out(r, conteo) for r in rows]


@router.patch("/reportes/{report_id}", response_model=CommentReportOut,
              summary="Resolver o descartar un reporte; se aplica a todos los reportes pendientes del comentario")
def decidir_reporte(report_id: int, body: ReportDecisionIn, db: Session = Depends(get_db)):
    r = db.get(CommentReport, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    db.query(CommentReport).filter_by(comment_id=r.comment_id, status="pendiente").update({"status": body.status})
    r.status = body.status
    if body.ocultar_comentario is not None:
        r.comment.hidden = body.ocultar_comentario
    db.commit()
    db.refresh(r)
    total = db.query(CommentReport).filter_by(comment_id=r.comment_id).count()
    return _reporte_out(r, {r.comment_id: total})


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
