"""Comisiones de la Cámara: integrantes, presidente y sesiones (cargadas por app/comisiones.py)."""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.comisiones import fuente_url
from app.database import get_db
from app.models import Comision, ComisionIntegrante, ComisionSesion, Parliamentarian
from app.schemas import ComisionDeParlamentario, ComisionDetalle, ComisionIntegranteOut, ComisionOut, ComisionSesionOut

router = APIRouter(tags=["Comisiones"])


def _diputados(db: Session, ids: set[int]) -> dict[int, Parliamentarian]:
    if not ids:
        return {}
    rows = (db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
            .filter(Parliamentarian.chamber == "diputado", Parliamentarian.external_id.in_(ids)).all())
    return {p.external_id: p for p in rows}


def _rango(anno: int) -> tuple[datetime, datetime]:
    return datetime(anno, 1, 1), datetime(anno + 1, 1, 1)


def _out(c: Comision, presidentes: dict, integrantes: dict, sesiones: dict, ultimas: dict) -> dict:
    return dict(id=c.id, nombre=c.nombre, alias=c.alias, tipo=c.tipo, fecha_inicio=c.fecha_inicio,
                fecha_termino=c.fecha_termino, correo=c.correo, telefono=c.telefono,
                presidente=presidentes.get(c.presidente_id), integrantes=integrantes.get(c.id, 0),
                sesiones_anno=sesiones.get(c.id, 0), ultima_sesion=ultimas.get(c.id), fuente_url=fuente_url(c.id))


@router.get("/comisiones", response_model=list[ComisionOut], summary="Comisiones vigentes con su presidente")
def comisiones(tipo: Optional[str] = Query(None, description="Permanente, Especial Investigadora..."),
               anno: Optional[int] = Query(None, ge=2000, le=2100, description="Año para contar sesiones (por defecto, el actual)"),
               db: Session = Depends(get_db)):
    q = db.query(Comision).filter(Comision.vigente.is_(True))
    if tipo:
        q = q.filter(Comision.tipo.ilike(tipo))
    filas = q.order_by(Comision.tipo, Comision.nombre).all()
    desde, hasta = _rango(anno or date.today().year)
    integrantes = dict(db.query(ComisionIntegrante.comision_id, func.count()).group_by(ComisionIntegrante.comision_id).all())
    sesiones = dict(db.query(ComisionSesion.comision_id, func.count())
                    .filter(ComisionSesion.fecha_inicio >= desde, ComisionSesion.fecha_inicio < hasta)
                    .group_by(ComisionSesion.comision_id).all())
    ultimas = dict(db.query(ComisionSesion.comision_id, func.max(ComisionSesion.fecha_inicio))
                   .filter(ComisionSesion.fecha_inicio <= datetime.now())
                   .group_by(ComisionSesion.comision_id).all())
    presidentes = _diputados(db, {c.presidente_id for c in filas if c.presidente_id})
    return [_out(c, presidentes, integrantes, sesiones, ultimas) for c in filas]


@router.get("/comisiones/{comision_id}", response_model=ComisionDetalle,
            summary="Comisión: integrantes (con el presidente primero) y sesiones del año")
def comision(comision_id: int, anno: Optional[int] = Query(None, ge=2000, le=2100), db: Session = Depends(get_db)):
    c = db.get(Comision, comision_id)
    if not c:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")
    desde, hasta = _rango(anno or date.today().year)
    sesiones = (db.query(ComisionSesion).filter(ComisionSesion.comision_id == c.id, ComisionSesion.fecha_inicio >= desde,
                                                ComisionSesion.fecha_inicio < hasta)
                .order_by(ComisionSesion.fecha_inicio.desc()).all())
    ids = {i.diputado_id for i in c.integrantes} | ({c.presidente_id} if c.presidente_id else set())
    dip = _diputados(db, ids)
    miembros = [ComisionIntegranteOut(parlamentario=dip[i.diputado_id], es_presidente=i.diputado_id == c.presidente_id,
                                      desde=i.fecha_inicio)
                for i in c.integrantes if i.diputado_id in dip]
    miembros.sort(key=lambda m: (not m.es_presidente, m.parlamentario.last_name, m.parlamentario.first_name))
    pasadas = [s.fecha_inicio for s in sesiones if s.fecha_inicio and s.fecha_inicio <= datetime.now()]
    base = _out(c, dip, {c.id: len(miembros)}, {c.id: len(sesiones)}, {c.id: max(pasadas) if pasadas else None})
    return ComisionDetalle(**base, miembros=miembros, sesiones=[ComisionSesionOut.model_validate(s) for s in sesiones])


@router.get("/parlamentarios/{parl_id}/comisiones", response_model=list[ComisionDeParlamentario],
            summary="Comisiones que integra un diputado (y si las preside)")
def comisiones_parlamentario(parl_id: int, db: Session = Depends(get_db)):
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    if p.chamber != "diputado":
        return []
    filas = (db.query(ComisionIntegrante, Comision).join(Comision, Comision.id == ComisionIntegrante.comision_id)
             .filter(ComisionIntegrante.diputado_id == p.external_id, Comision.vigente.is_(True)).all())
    out = [ComisionDeParlamentario(id=c.id, nombre=c.nombre, alias=c.alias, tipo=c.tipo,
                                   es_presidente=c.presidente_id == p.external_id, desde=i.fecha_inicio)
           for i, c in filas]
    out.sort(key=lambda x: (not x.es_presidente, x.tipo or "", x.nombre))
    return out
