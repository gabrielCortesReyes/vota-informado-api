"""Proyectos de ley con sus materias y autores, guardados en la base.

La Cámara no permite buscar proyectos por materia: los listados por año (mociones y mensajes)
no traen materias ni autores, solo el detalle de cada proyecto (retornarProyectoLey). Por eso
la sincronización guarda el listado y va pidiendo los detalles que faltan, unos cientos por corrida.

Con eso se puede: listar las materias como categorías, ver los proyectos de una materia,
mostrarle a cada usuario los proyectos de las materias que eligió y ver en qué materias
presenta mociones cada diputado.
"""
import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.camara.client import consultar
from app.config import PROYECTOS_DESDE_ANNO, PROYECTOS_DETALLES_POR_CORRIDA
from app.models import Materia, ProyectoAutor, ProyectoLey, ProyectoMateria

logger = logging.getLogger(__name__)
WS = "WSLegislativo"
CONCURRENCIA = 5
# Los proyectos recientes se vuelven a leer: la Cámara les asigna materias días después del ingreso
RECARGAR_RECIENTES_DIAS = 90
RECARGAR_CADA_DIAS = 7


def _lista(valor) -> list:
    if isinstance(valor, list):
        return valor
    return [valor] if isinstance(valor, dict) else []


def _nombre(valor) -> str | None:
    if isinstance(valor, dict):
        valor = valor.get("Nombre")
    return str(valor).strip() if valor not in (None, "") else None


def _fecha(valor) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor)) if valor else None
    except ValueError:
        return None


def _materia(db: Session, item: dict, cache: dict[int, Materia]) -> Materia | None:
    mid, nombre = item.get("Id"), _nombre(item.get("Nombre") or item.get("Descripcion"))
    if not isinstance(mid, int) or not nombre:
        return None
    m = cache.get(mid) or db.get(Materia, mid)
    if m is None:
        m = Materia(id=mid, nombre=nombre[:300])
        db.add(m)
    elif m.nombre != nombre[:300]:
        m.nombre = nombre[:300]
    cache[mid] = m
    return m


async def sincronizar_materias(db: Session) -> int:
    cache: dict[int, Materia] = {}
    for item in _lista(await consultar(WS, "retornarMaterias")):
        if isinstance(item, dict):
            _materia(db, item, cache)
    db.commit()
    return len(cache)


def _upsert_listado(db: Session, item: dict) -> ProyectoLey | None:
    pid, boletin = item.get("Id"), item.get("NumeroBoletin")
    if not isinstance(pid, int) or not boletin:
        return None
    p = db.get(ProyectoLey, pid)
    if p is None:
        p = db.query(ProyectoLey).filter(ProyectoLey.boletin == str(boletin)).first()
        if p is not None and p.id != pid:  # mismo boletín con otro Id: se queda el más nuevo
            db.delete(p)
            db.flush()
            p = None
    if p is None:
        p = ProyectoLey(id=pid, boletin=str(boletin))
        db.add(p)
    p.nombre = _nombre(item.get("Nombre")) or p.nombre or str(boletin)
    p.fecha_ingreso = _fecha(item.get("FechaIngreso")) or p.fecha_ingreso
    p.tipo = _nombre(item.get("TipoIniciativa")) or p.tipo
    p.camara_origen = _nombre(item.get("CamaraOrigen")) or p.camara_origen
    return p


def aplicar_detalle(db: Session, p: ProyectoLey, detalle: dict, cache: dict[int, Materia] | None = None) -> None:
    """Guarda las materias y los diputados autores que trae retornarProyectoLey."""
    cache = {} if cache is None else cache
    _upsert_listado(db, {**detalle, "Id": p.id, "NumeroBoletin": p.boletin})
    materias = {}
    for item in _lista(detalle.get("Materias")):
        m = _materia(db, item, cache) if isinstance(item, dict) else None
        if m:
            materias[m.id] = m
    p.materias = list(materias.values())
    autores = {}
    for item in _lista(detalle.get("Autores")):
        dip = item.get("Diputado") if isinstance(item, dict) else None
        if isinstance(dip, dict) and isinstance(dip.get("Id"), int) and dip["Id"] not in autores:
            orden = item.get("Orden")
            autores[dip["Id"]] = ProyectoAutor(diputado_id=dip["Id"], orden=orden if isinstance(orden, int) else None)
    p.autores = list(autores.values())
    p.detalle_cargado_at = datetime.now()


def _pendientes(db: Session, limite: int) -> list[ProyectoLey]:
    ahora = datetime.now()
    sin_detalle = (db.query(ProyectoLey).filter(ProyectoLey.detalle_cargado_at.is_(None))
                   .order_by(ProyectoLey.fecha_ingreso.desc()).limit(limite).all())
    if len(sin_detalle) >= limite:
        return sin_detalle
    recientes = (db.query(ProyectoLey)
                 .filter(ProyectoLey.fecha_ingreso >= ahora - timedelta(days=RECARGAR_RECIENTES_DIAS),
                         ProyectoLey.detalle_cargado_at < ahora - timedelta(days=RECARGAR_CADA_DIAS))
                 .order_by(ProyectoLey.detalle_cargado_at).limit(limite - len(sin_detalle)).all())
    return sin_detalle + recientes


async def sincronizar_proyectos(db: Session, desde_anno: int | None = None,
                                limite: int = PROYECTOS_DETALLES_POR_CORRIDA) -> dict:
    """Guarda mociones y mensajes desde `desde_anno` y pide hasta `limite` detalles (materias y autores)."""
    resultado = {"materias": 0, "proyectos": 0, "detalles": 0, "errores": 0, "pendientes": 0}
    try:
        resultado["materias"] = await sincronizar_materias(db)
    except Exception as e:  # el catálogo no es indispensable: las materias llegan también en cada detalle
        db.rollback()
        logger.warning(f"No se pudo leer el catálogo de materias: {e}")

    for anno in range(desde_anno or PROYECTOS_DESDE_ANNO, date.today().year + 1):
        for operacion in ("retornarMocionesXAnno", "retornarMensajesXAnno"):
            for item in _lista(await consultar(WS, operacion, prmAnno=anno)):
                if isinstance(item, dict) and _upsert_listado(db, item):
                    resultado["proyectos"] += 1
            db.commit()

    sem = asyncio.Semaphore(CONCURRENCIA)

    async def pedir(boletin: str):
        async with sem:
            try:
                return await consultar(WS, "retornarProyectoLey", prmNumeroBoletin=boletin)
            except Exception as e:
                logger.warning(f"No se pudo leer el proyecto {boletin}: {e}")
                return e

    cache: dict[int, Materia] = {}
    # Proyectos votados este año pero ingresados antes de desde_anno: también se guardan, para
    # mostrar las materias de cada votación.
    resultado["votados"] = 0
    try:
        votados = {boletin_de(v.get("Descripcion")) for v in await _votaciones_del_anno(date.today().year)}
        votados.discard(None)
        existentes = {b for (b,) in db.query(ProyectoLey.boletin).filter(ProyectoLey.boletin.in_(votados))} if votados else set()
        faltantes = sorted(votados - existentes)[: max(limite // 5, 1)]
        for detalle in await asyncio.gather(*(pedir(b) for b in faltantes)):
            if isinstance(detalle, dict) and isinstance(detalle.get("Id"), int):
                p = _upsert_listado(db, detalle)
                if p:
                    db.flush()
                    aplicar_detalle(db, p, detalle, cache)
                    resultado["votados"] += 1
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"No se pudieron cargar los proyectos votados: {e}")

    pendientes = _pendientes(db, limite)
    for i in range(0, len(pendientes), 50):  # de a 50 para guardar el avance
        lote = pendientes[i:i + 50]
        detalles = await asyncio.gather(*(pedir(p.boletin) for p in lote))
        for p, detalle in zip(lote, detalles):
            if isinstance(detalle, dict) and detalle:
                aplicar_detalle(db, p, detalle, cache)
                resultado["detalles"] += 1
            else:
                resultado["errores"] += 1
        db.commit()
    resultado["pendientes"] = db.query(ProyectoLey).filter(ProyectoLey.detalle_cargado_at.is_(None)).count()
    logger.info(f"Proyectos: {resultado}")
    return resultado


_BOLETIN = re.compile(r"(\d{3,5}-\d{1,2})")


def boletin_de(texto) -> str | None:
    """«Boletín N° 18282-07» → «18282-07»."""
    m = _BOLETIN.search(str(texto or ""))
    return m.group(1) if m else None


async def _votaciones_del_anno(anno: int) -> list[dict]:
    return [v for v in _lista(await consultar(WS, "retornarVotacionesXAnno", prmAnno=anno)) if isinstance(v, dict)]


# --- Consultas ------------------------------------------------------------------------

def materias_con_proyectos(db: Session) -> list[dict]:
    """Materias que tienen al menos un proyecto guardado, de la más a la menos usada."""
    filas = (db.query(Materia.id, Materia.nombre, func.count(ProyectoMateria.proyecto_id))
             .join(ProyectoMateria, ProyectoMateria.materia_id == Materia.id)
             .group_by(Materia.id, Materia.nombre)
             .order_by(func.count(ProyectoMateria.proyecto_id).desc(), Materia.nombre).all())
    return [{"id": i, "nombre": n, "proyectos": c} for i, n, c in filas]


def proyectos_de_materias(db: Session, materia_ids: list[int], limit: int = 30, offset: int = 0):
    sub = db.query(ProyectoMateria.proyecto_id).filter(ProyectoMateria.materia_id.in_(materia_ids))
    return (db.query(ProyectoLey).filter(ProyectoLey.id.in_(sub))
            .options(selectinload(ProyectoLey.materias), selectinload(ProyectoLey.autores))
            .order_by(ProyectoLey.fecha_ingreso.desc(), ProyectoLey.id.desc()).offset(offset).limit(limit).all())


def materias_de_diputado(db: Session, diputado_id: int, desde: date | None = None, limite: int = 10) -> dict:
    """En qué materias presenta mociones un diputado (diputado_id = Id de la Cámara)."""
    mis = db.query(ProyectoAutor.proyecto_id).filter(ProyectoAutor.diputado_id == diputado_id)
    if desde:
        mis = mis.join(ProyectoLey, ProyectoLey.id == ProyectoAutor.proyecto_id).filter(
            ProyectoLey.fecha_ingreso >= datetime.combine(desde, datetime.min.time()))
    ids = [r[0] for r in mis]
    filas = []
    if ids:
        filas = (db.query(Materia.id, Materia.nombre, func.count(ProyectoMateria.proyecto_id))
                 .join(ProyectoMateria, ProyectoMateria.materia_id == Materia.id)
                 .filter(ProyectoMateria.proyecto_id.in_(ids))
                 .group_by(Materia.id, Materia.nombre)
                 .order_by(func.count(ProyectoMateria.proyecto_id).desc(), Materia.nombre).limit(limite).all())
    con_materia = (db.query(func.count(func.distinct(ProyectoMateria.proyecto_id)))
                   .filter(ProyectoMateria.proyecto_id.in_(ids)).scalar() if ids else 0)
    return {"mociones": len(ids), "con_materia": con_materia,
            "materias": [{"id": i, "nombre": n, "proyectos": c} for i, n, c in filas]}


async def materias_de_votaciones(db: Session, anno: int) -> dict[int, list[dict]]:
    """Materias del proyecto de cada votación de Sala del año (por el boletín de su descripción)."""
    por_votacion = {v["Id"]: boletin_de(v.get("Descripcion")) for v in await _votaciones_del_anno(anno)
                    if isinstance(v.get("Id"), int)}
    boletines = {b for b in por_votacion.values() if b}
    if not boletines:
        return {}
    proyectos = (db.query(ProyectoLey).filter(ProyectoLey.boletin.in_(boletines))
                 .options(selectinload(ProyectoLey.materias)).all())
    materias = {p.boletin: [{"id": m.id, "nombre": m.nombre} for m in p.materias] for p in proyectos}
    return {vid: materias[b] for vid, b in por_votacion.items() if b in materias and materias[b]}
