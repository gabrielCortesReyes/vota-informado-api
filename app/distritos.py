"""Relación comuna → distrito y votos de los diputados de un distrito.

Se usan los 28 distritos vigentes (Ley 20.840). El WSComun `retornarDistritos` de la Cámara
trae todavía los 60 distritos antiguos y `retornarDiputadosPeriodoActual` no trae distrito,
así que ambos mapas vienen de app/data (generados desde Wikipedia "Distritos electorales de
Chile" y "LVII periodo legislativo del Congreso Nacional de Chile").
"""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.camara.client import consultar
from app.models import Parliamentarian

DATA = Path(__file__).parent / "data"
DISTRITOS_VIGENTES = 28


def _lista(valor: Any) -> list:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def normalizar(nombre: Any) -> str:
    """Mayúsculas, sin tildes (Ñ→N) y sin signos: "O'Higgins" y 'O"HIGGINS' quedan iguales."""
    s = unicodedata.normalize("NFD", str(nombre or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", s).split())


@lru_cache
def comunas_distrito() -> dict[str, int]:
    """{nombre normalizado de comuna: distrito 1-28}"""
    return json.loads((DATA / "comunas_distrito.json").read_text(encoding="utf-8"))


@lru_cache
def diputados_distrito() -> dict[str, int]:
    """{Id de diputado en la Cámara: distrito 1-28} del período 2026-2030"""
    return json.loads((DATA / "diputados_distrito.json").read_text(encoding="utf-8"))


async def mapa_comuna_distrito() -> dict[int, int]:
    """{numero de comuna (retornarComunas): numero de distrito}"""
    nombres = comunas_distrito()
    mapa: dict[int, int] = {}
    for c in _lista(await consultar("WSComun", "retornarComunas")):
        if isinstance(c, dict) and c.get("Numero") is not None:
            distrito = nombres.get(normalizar(c.get("Nombre")))
            if distrito:
                mapa[int(c["Numero"])] = distrito
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
