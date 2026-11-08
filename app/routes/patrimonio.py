"""Patrimonio declarado de los diputados (InfoProbidad, CC BY 4.0)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Parliamentarian, PatrimonioDeclaracion
from app.patrimonio import LICENCIA, declaracion_url, ultima_declaracion
from app.schemas import ParliamentarianOut, PatrimonioOut, PatrimonioRankingItem

router = APIRouter(tags=["Patrimonio"])


def patrimonio_out(d: PatrimonioDeclaracion, incluir_fuente: bool = False) -> PatrimonioOut:
    r = d.resumen or {}
    return PatrimonioOut(
        parliamentarian_id=d.parliamentarian_id, declaracion_id=d.id, fecha=d.fecha, tipo=d.tipo,
        fuente_url=declaracion_url(d.id), licencia=LICENCIA, renta_bruta_mensual=r.get("renta_bruta_mensual"),
        totales=r.get("totales") or {}, **{g: r.get(g) or [] for g in
                                          ("inmuebles", "vehiculos", "sociedades", "valores", "otros_bienes", "pasivos")},
        datos=d.datos if incluir_fuente else None,
    )


@router.get("/parlamentarios/{parl_id}/patrimonio", response_model=PatrimonioOut,
            summary="Patrimonio de la declaración más reciente: inmuebles, vehículos, sociedades, valores y pasivos")
def patrimonio(parl_id: int, incluir_fuente: bool = Query(False, description="Agrega el JSON original de InfoProbidad"),
               db: Session = Depends(get_db)):
    if not db.get(Parliamentarian, parl_id):
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    d = ultima_declaracion(db, parl_id)
    if not d:
        raise HTTPException(status_code=404, detail="Aún no hay una declaración de patrimonio cargada")
    return patrimonio_out(d, incluir_fuente)


@router.get("/patrimonio/diputados", response_model=list[PatrimonioRankingItem],
            summary="Diputados ordenados por patrimonio neto declarado (u otro total)")
def ranking(orden: Literal["desc", "asc"] = "desc",
            por: Literal["patrimonio_neto", "activos", "pasivos", "inmuebles", "sociedades", "valores"] = "patrimonio_neto",
            limit: int = Query(200, le=500), db: Session = Depends(get_db)):
    ultimas = {}
    for d in db.query(PatrimonioDeclaracion).order_by(PatrimonioDeclaracion.fecha.asc().nullsfirst(),
                                                      PatrimonioDeclaracion.id.asc()):
        ultimas[d.parliamentarian_id] = d  # queda la más reciente
    parl = {p.id: p for p in db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
            .filter(Parliamentarian.id.in_(ultimas), Parliamentarian.current.is_(True))}
    filas = [PatrimonioRankingItem(parlamentario=ParliamentarianOut.model_validate(parl[pid]), declaracion_id=d.id,
                                   fecha=d.fecha, totales=(d.resumen or {}).get("totales") or {})
             for pid, d in ultimas.items() if pid in parl]
    filas.sort(key=lambda f: getattr(f.totales, por), reverse=orden == "desc")
    return filas[:limit]
