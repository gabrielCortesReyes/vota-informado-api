"""Gastos de los diputados (Transparencia Activa): por diputado y ranking. Se cargan con /admin/gastos."""
from collections import defaultdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.gastos import fuente_url
from app.models import GastoParlamentario, Parliamentarian
from app.schemas import GastoCategoria, GastoMes, GastoRankingItem, GastosParlamentarioOut

router = APIRouter(tags=["Gastos"])

Tipo = Optional[Literal["operacionales", "pasajes_aereos"]]
PERIODO = r"^\d{4}-(0[1-9]|1[0-2])$"


def _filtrar(q, tipo: Tipo, desde: Optional[str], hasta: Optional[str], categoria: Optional[str] = None):
    if tipo:
        q = q.filter(GastoParlamentario.tipo == tipo)
    if categoria:
        # se guardan en MAYÚSCULAS (app/gastos.py); upper() en Python también cubre las tildes
        q = q.filter(GastoParlamentario.categoria.like(f"%{categoria.strip().upper()}%"))
    if desde:
        a, m = map(int, desde.split("-"))
        q = q.filter(or_(GastoParlamentario.anno > a, and_(GastoParlamentario.anno == a, GastoParlamentario.mes >= m)))
    if hasta:
        a, m = map(int, hasta.split("-"))
        q = q.filter(or_(GastoParlamentario.anno < a, and_(GastoParlamentario.anno == a, GastoParlamentario.mes <= m)))
    return q


@router.get("/parlamentarios/{parl_id}/gastos", response_model=GastosParlamentarioOut,
            summary="Gastos de un diputado por mes y categoría (gastos operacionales y pasajes aéreos)")
def gastos_parlamentario(parl_id: int, tipo: Tipo = None,
                         desde: Optional[str] = Query(None, pattern=PERIODO, description="AAAA-MM"),
                         hasta: Optional[str] = Query(None, pattern=PERIODO, description="AAAA-MM"),
                         db: Session = Depends(get_db)):
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    if p.chamber != "diputado":
        raise HTTPException(status_code=404, detail="Los gastos solo están disponibles para diputados")
    filas = _filtrar(db.query(GastoParlamentario).filter(GastoParlamentario.diputado_id == p.external_id),
                     tipo, desde, hasta).all()
    meses: dict[tuple, list] = defaultdict(list)
    por_categoria: dict[str, int] = defaultdict(int)
    for f in filas:
        meses[(f.anno, f.mes, f.tipo)].append(f)
        por_categoria[f.categoria] += f.monto
    lista = [GastoMes(anno=a, mes=m, tipo=t, total=sum(x.monto for x in fs),
                      categorias=[GastoCategoria(categoria=x.categoria, monto=x.monto)
                                  for x in sorted(fs, key=lambda x: -x.monto) if x.monto])
             for (a, m, t), fs in sorted(meses.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True)]
    periodos = sorted({(a, m) for a, m, _ in meses})
    return GastosParlamentarioOut(
        parlamentario_id=p.id, total=sum(por_categoria.values()), meses=lista,
        por_categoria=[GastoCategoria(categoria=c, monto=v) for c, v in sorted(por_categoria.items(), key=lambda kv: -kv[1]) if v],
        desde=f"{periodos[0][0]}-{periodos[0][1]:02d}" if periodos else None,
        hasta=f"{periodos[-1][0]}-{periodos[-1][1]:02d}" if periodos else None,
        actualizado=max((f.cargado_at for f in filas), default=None), fuente_url=fuente_url(p.external_id, tipo or "operacionales"))


@router.get("/gastos/categorias", summary="Categorías de gasto cargadas, con el total de todos los diputados")
def categorias(tipo: Tipo = None, desde: Optional[str] = Query(None, pattern=PERIODO),
               hasta: Optional[str] = Query(None, pattern=PERIODO), db: Session = Depends(get_db)):
    q = _filtrar(db.query(GastoParlamentario.tipo, GastoParlamentario.categoria, func.sum(GastoParlamentario.monto)),
                 tipo, desde, hasta)
    filas = q.group_by(GastoParlamentario.tipo, GastoParlamentario.categoria).all()
    return [{"tipo": t, "categoria": c, "monto": int(m or 0)} for t, c, m in sorted(filas, key=lambda x: -(x[2] or 0))]


@router.get("/gastos/periodos", summary="Meses con gastos cargados (el más reciente primero)")
def periodos(tipo: Tipo = None, db: Session = Depends(get_db)):
    q = _filtrar(db.query(GastoParlamentario.anno, GastoParlamentario.mes, func.count(func.distinct(GastoParlamentario.diputado_id))),
                 tipo, None, None)
    filas = q.group_by(GastoParlamentario.anno, GastoParlamentario.mes).all()
    return [{"periodo": f"{a}-{m:02d}", "diputados": n} for a, m, n in sorted(filas, reverse=True)]


@router.get("/gastos/diputados", response_model=list[GastoRankingItem],
            summary="Ranking de diputados por gasto (total del período; opcionalmente de una categoría, p. ej. traslación)")
def ranking(tipo: Tipo = None, categoria: Optional[str] = Query(None, examples=["TRASLACIÓN"]),
            desde: Optional[str] = Query(None, pattern=PERIODO), hasta: Optional[str] = Query(None, pattern=PERIODO),
            orden: Literal["desc", "asc"] = "desc", partido_id: Optional[int] = None, distrito: Optional[int] = None,
            limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db)):
    q = _filtrar(db.query(GastoParlamentario.diputado_id, func.sum(GastoParlamentario.monto),
                          func.count(func.distinct(GastoParlamentario.anno * 100 + GastoParlamentario.mes))),
                 tipo, desde, hasta, categoria)
    totales = {d: (int(t or 0), n) for d, t, n in q.group_by(GastoParlamentario.diputado_id).all()}
    pq = (db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
          .filter(Parliamentarian.chamber == "diputado", Parliamentarian.current.is_(True)))
    if partido_id:
        pq = pq.filter(Parliamentarian.party_id == partido_id)
    if distrito:
        pq = pq.filter(Parliamentarian.district == str(distrito))
    filas = [(totales[p.external_id], p) for p in pq.all() if p.external_id in totales]
    filas.sort(key=lambda f: (f[0][0] if orden == "asc" else -f[0][0], f[1].last_name or ""))
    return [GastoRankingItem(posicion=i + 1, parlamentario=p, total=t, meses=n, promedio_mensual=t // n if n else 0)
            for i, ((t, n), p) in enumerate(filas[:limit])]
