"""Asistencia de los diputados a la Sala y justificación de cada inasistencia (datos oficiales WSSala)."""
from collections import Counter
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app import asistencia as a
from app.camara.client import consultar_o_404
from app.database import get_db
from app.models import Parliamentarian
from app.schemas import AsistenciaDetalle, AsistenciaDiputado

router = APIRouter(tags=["Asistencia"])

DESDE = Query(None, description="Desde esta fecha (por defecto, todo el período cargado)")
HASTA = Query(None, description="Hasta esta fecha, inclusive")


@router.get("/parlamentarios/{parl_id}/asistencia", response_model=AsistenciaDetalle,
            summary="% de asistencia a la Sala, sesiones a las que no asistió y la justificación de cada una")
def asistencia_parlamentario(parl_id: int, desde: Optional[date] = DESDE, hasta: Optional[date] = HASTA,
                             db: Session = Depends(get_db)):
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    if p.chamber != "diputado":
        raise HTTPException(status_code=404, detail="La asistencia solo está disponible para diputados")
    inasistencias = a.inasistencias_diputado(db, p.external_id, desde, hasta)
    conteo = Counter(i["justificacion"] or "Sin justificación" for i in inasistencias)
    return AsistenciaDetalle(
        parlamentario_id=p.id, **a.resumen_diputado(db, p.external_id, desde, hasta),
        actualizado=a.ultima_actualizacion(db),
        por_justificacion=[{"justificacion": j, "cantidad": n} for j, n in conteo.most_common()],
        inasistencias_detalle=inasistencias,
    )


@router.get("/asistencia/diputados", response_model=list[AsistenciaDiputado],
            summary="Asistencia de todos los diputados en ejercicio (ranking)")
def asistencia_diputados(desde: Optional[date] = DESDE, hasta: Optional[date] = HASTA,
                         orden: Literal["asc", "desc"] = Query("desc", description="Por % de asistencia"),
                         partido_id: Optional[int] = None, distrito: Optional[int] = None,
                         db: Session = Depends(get_db)):
    q = (db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
         .filter(Parliamentarian.chamber == "diputado", Parliamentarian.current.is_(True)))
    if partido_id:
        q = q.filter(Parliamentarian.party_id == partido_id)
    if distrito:
        q = q.filter(Parliamentarian.district == str(distrito))
    resumenes = a.resumen_todos(db, desde, hasta)
    filas = [AsistenciaDiputado(parlamentario=p, **resumenes[p.external_id])
             for p in q.all() if p.external_id in resumenes]
    filas.sort(key=lambda f: (f.porcentaje or 0, -f.sin_justificar), reverse=orden == "desc")
    return filas


@router.get("/votaciones/{votacion_id}/asistencia",
            summary="Asistencia a la sesión en que se votó: quién no asistió y su justificación")
async def asistencia_votacion(votacion_id: int, db: Session = Depends(get_db)):
    detalle = await consultar_o_404("WSLegislativo", "retornarVotacionDetalle", "Votación no encontrada",
                                    prmVotacionId=votacion_id)
    try:
        fecha = datetime.fromisoformat(str(detalle.get("Fecha")))
    except (TypeError, ValueError):
        fecha = None
    sesion = a.sesion_de_votacion(db, fecha) if fecha else None
    filas = a.asistencia_de_sesion(db, sesion.id) if sesion else []
    return {
        "votacion_id": votacion_id,
        "sesion": {"id": sesion.id, "numero": sesion.numero, "fecha": sesion.fecha_inicio,
                   "tipo": sesion.tipo, "fuente_url": a.fuente_url(sesion.id)} if sesion else None,
        "asistencia": [{"diputado_id": f.diputado_id, "asistio": f.asistio, "justificacion": f.justificacion}
                       for f in filas],
    }
