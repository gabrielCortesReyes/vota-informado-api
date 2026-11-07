"""Relación comuna → distrito y votos de los diputados de un distrito.

La Cámara entrega el catálogo de comunas sin distrito; el de distritos (WSComun
`retornarDistritos`) trae las comunas de cada uno, así que de ahí se arma el mapa.
"""
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.camara.client import consultar
from app.models import Parliamentarian


def _lista(valor: Any) -> list:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


async def mapa_comuna_distrito() -> dict[int, int]:
    """{numero de comuna: numero de distrito}"""
    mapa: dict[int, int] = {}
    for d in _lista(await consultar("WSComun", "retornarDistritos")):
        if not isinstance(d, dict) or d.get("Numero") is None:
            continue
        for c in _lista(d.get("Comunas")):
            if isinstance(c, dict) and c.get("Numero") is not None:
                mapa[int(c["Numero"])] = int(d["Numero"])
    return mapa


async def distrito_de_comuna(commune_id: Optional[int]) -> Optional[int]:
    if not commune_id:
        return None
    return (await mapa_comuna_distrito()).get(int(commune_id))


async def comunas_con_distrito() -> list[dict]:
    """El catálogo de comunas con el campo "Distrito" agregado."""
    mapa = await mapa_comuna_distrito()
    return [{**c, "Distrito": mapa.get(c.get("Numero"))} if isinstance(c, dict) else c
            for c in _lista(await consultar("WSComun", "retornarComunas"))]


def diputados_del_distrito(db: Session, distrito: int) -> list[Parliamentarian]:
    return (db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
            .filter(Parliamentarian.chamber == "diputado", Parliamentarian.current.is_(True),
                    Parliamentarian.district == str(distrito))
            .order_by(Parliamentarian.last_name, Parliamentarian.first_name).all())


def _resumen_votacion(v: dict) -> dict:
    return {k: v.get(k) for k in ("Id", "Descripcion", "Fecha", "TotalSi", "TotalNo", "TotalAbstencion",
                                  "TotalDispensado", "Quorum", "Resultado", "Tipo")}


async def votos_del_distrito(db: Session, votacion_id: int, distrito: int,
                             mi_diputado_id: Optional[int] = None) -> dict:
    detalle = await consultar("WSLegislativo", "retornarVotacionDetalle", prmVotacionId=votacion_id)
    if not isinstance(detalle, dict) or not detalle:
        raise HTTPException(status_code=404, detail="Votación no encontrada")
    opciones = {}
    for voto in _lista(detalle.get("Votos")):
        dip = (voto or {}).get("Diputado") or {}
        if dip.get("Id") is not None:
            opciones[int(dip["Id"])] = voto.get("OpcionVoto")
    from app.schemas import ParliamentarianOut  # evita import circular
    votos = [{
        "diputado": ParliamentarianOut.model_validate(p).model_dump(),
        "opcion": opciones.get(p.external_id),  # None: no figura en la votación
        "es_mi_diputado": p.id == mi_diputado_id,
    } for p in diputados_del_distrito(db, distrito)]
    votos.sort(key=lambda x: not x["es_mi_diputado"])  # el elegido primero
    return {"votacion": _resumen_votacion(detalle), "distrito": distrito, "votos": votos}


async def ultima_votacion_id() -> Optional[int]:
    from datetime import date
    anno = date.today().year
    for a in (anno, anno - 1):
        votaciones = [v for v in _lista(await consultar("WSLegislativo", "retornarVotacionesXAnno", prmAnno=a))
                      if isinstance(v, dict) and v.get("Id")]
        if votaciones:
            return int(max(votaciones, key=lambda v: (str(v.get("Fecha") or ""), v["Id"]))["Id"])
    return None
