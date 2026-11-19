"""Rankings de participación ciudadana de los diputados: mejor y peor nota, y más y menos comentados.

- Nota: aparecen todos los diputados con al menos una calificación, ordenados por su promedio
  (a igual promedio, primero quien tiene más calificaciones).
- Comentarios: aparecen todos los diputados con al menos un comentario visible (ni borrado ni oculto por
  moderación), ordenados por personas distintas que comentaron y luego por número de comentarios, para
  que una sola cuenta no infle el ranking.
Sin límite de filas: entran todos los diputados en ejercicio que cumplan eso.
"""
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Comment, Parliamentarian, Rating
from app.schemas import RankingComentariosItem, RankingComentariosOut, RankingNotaItem, RankingNotaOut

router = APIRouter(prefix="/ranking", tags=["Ranking"])

Camara = Optional[Literal["diputado", "senador"]]  # compatibilidad: con "senador" la lista sale vacía


def _diputados(db: Session, partido_id: Optional[int], distrito: Optional[int]) -> dict[int, Parliamentarian]:
    q = (db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
         .filter(Parliamentarian.chamber == "diputado", Parliamentarian.current.is_(True)))
    if partido_id:
        q = q.filter(Parliamentarian.party_id == partido_id)
    if distrito:
        q = q.filter(Parliamentarian.district == str(distrito))
    return {p.id: p for p in q.all()}


def _nombre(p: Parliamentarian) -> tuple:
    return (p.last_name or "", p.second_last_name or "", p.first_name or "")


@router.get("/calificaciones", response_model=RankingNotaOut,
            summary="Diputados con al menos una calificación, por promedio (desc: mejor evaluados; asc: peor)")
def ranking_calificaciones(orden: Literal["desc", "asc"] = "desc", partido_id: Optional[int] = None,
                           distrito: Optional[int] = None, camara: Camara = None, db: Session = Depends(get_db)):
    if camara == "senador":  # el ranking es solo de diputados (la app publicada aún puede pedir senadores)
        return RankingNotaOut(items=[])
    dip = _diputados(db, partido_id, distrito)
    filas = (db.query(Rating.parliamentarian_id, func.count(Rating.id), func.sum(Rating.score))
             .group_by(Rating.parliamentarian_id).all())
    items = [(round(s / n, 2), n, dip[pid]) for pid, n, s in filas if pid in dip]
    total = sum(n for _, n, _ in items)
    promedio_general = round(sum(p * n for p, n, _ in items) / total, 2) if total else None
    signo = -1 if orden == "desc" else 1
    items.sort(key=lambda x: (signo * x[0], -x[1], _nombre(x[2])))
    return RankingNotaOut(promedio_general=promedio_general, items=[
        RankingNotaItem(posicion=i + 1, parlamentario=p, promedio=prom, cantidad=n, nota_ajustada=prom)
        for i, (prom, n, p) in enumerate(items)])


@router.get("/comentarios", response_model=RankingComentariosOut,
            summary="Diputados con al menos un comentario (desc: más comentados; asc: menos comentados)")
def ranking_comentarios(orden: Literal["desc", "asc"] = "desc", partido_id: Optional[int] = None,
                        distrito: Optional[int] = None,
                        desde: Optional[date] = Query(None, description="Solo comentarios desde esta fecha"),
                        camara: Camara = None, db: Session = Depends(get_db)):
    if camara == "senador":
        return RankingComentariosOut(items=[])
    dip = _diputados(db, partido_id, distrito)
    q = (db.query(Comment.parliamentarian_id, func.count(func.distinct(Comment.user_id)), func.count(Comment.id))
         .filter(Comment.deleted_at.is_(None), Comment.hidden.is_(False)))
    if desde:
        q = q.filter(Comment.created_at >= datetime.combine(desde, datetime.min.time()))
    filas = [((personas, n), dip[pid]) for pid, personas, n in q.group_by(Comment.parliamentarian_id).all()
             if pid in dip and n > 0]
    signo = -1 if orden == "desc" else 1
    filas.sort(key=lambda f: (signo * f[0][0], signo * f[0][1], _nombre(f[1])))
    return RankingComentariosOut(items=[
        RankingComentariosItem(posicion=i + 1, parlamentario=p, personas=personas, comentarios=n)
        for i, ((personas, n), p) in enumerate(filas)])
