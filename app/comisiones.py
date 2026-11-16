"""Comisiones de la Cámara (WSComision): integrantes, presidente y sesiones.

WSComision identifica a los diputados con otro Id que las votaciones (ver app/identificar.py): se
guardan con el Id de las votaciones (Parliamentarian.external_id), buscando a cada uno por sus apellidos.
La Cámara no publica la asistencia ni los temas de las sesiones de comisión.
"""
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.camara.client import consultar
from app.identificar import IndiceDiputados
from app.models import Comision, ComisionIntegrante, ComisionSesion

logger = logging.getLogger(__name__)

WS = "WSComision"


def _lista(valor: Any) -> list:
    return valor if isinstance(valor, list) else ([valor] if isinstance(valor, dict) else [])


def _fecha(valor: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor)) if valor else None
    except ValueError:
        return None


def _nombre(valor: Any) -> str | None:
    return valor.get("Nombre") if isinstance(valor, dict) else (str(valor) if valor else None)


def _diputado(indice: IndiceDiputados, d: Any) -> int | None:
    if not isinstance(d, dict):
        return None
    return indice.buscar(nombre=d.get("Nombre"), paterno=d.get("ApellidoPaterno"), materno=d.get("ApellidoMaterno"))


def fuente_url(comision_id: int) -> str:
    return f"https://www.camara.cl/legislacion/comisiones/integrantes.aspx?prmID={comision_id}"


async def sincronizar_comisiones(db: Session, anno: int | None = None) -> dict:
    """Carga las comisiones vigentes con sus integrantes y las sesiones del año."""
    anno = anno or date.today().year
    vigentes = _lista(await consultar(WS, "retornarComisionesVigentes"))
    if not vigentes:  # la Cámara respondió vacío: no se toca lo que ya hay
        logger.warning("WSComision no devolvió comisiones vigentes; no se actualiza nada")
        return {"comisiones": 0, "integrantes": 0, "sin_identificar": 0, "sesiones": 0}

    indice = IndiceDiputados(db)
    vistas: set[int] = set()
    resultado = {"comisiones": 0, "integrantes": 0, "sin_identificar": 0, "sesiones": 0, "errores": 0}
    for item in vigentes:
        cid = item.get("Id") if isinstance(item, dict) else None
        if not cid:
            continue
        try:
            detalle = await consultar(WS, "retornarComision", prmComisionId=cid)
        except Exception as e:
            logger.error(f"No se pudo leer la comisión {cid}: {e}")
            resultado["errores"] += 1
            continue
        detalle = detalle if isinstance(detalle, dict) else {}
        c = db.get(Comision, cid) or Comision(id=cid)
        c.nombre = detalle.get("Nombre") or item.get("Nombre") or f"Comisión {cid}"
        c.alias = detalle.get("Alias") or item.get("Alias")
        c.tipo = _nombre(detalle.get("Tipo") or item.get("Tipo"))
        c.numero = detalle.get("Numero") if isinstance(detalle.get("Numero"), int) else None
        c.fecha_inicio = _fecha(detalle.get("FechaInicio") or item.get("FechaInicio"))
        c.fecha_termino = _fecha(detalle.get("FechaTermino") or item.get("FechaTermino"))
        c.correo = detalle.get("Correo") or item.get("Correo")
        tel = detalle.get("Telefono") or item.get("Telefono")
        c.telefono = str(tel) if tel else None
        c.presidente_id = _diputado(indice, item.get("Presidente") or detalle.get("Presidente"))
        c.vigente = True
        db.add(c)
        vistas.add(cid)

        integrantes: dict[int, tuple] = {}
        for di in _lista(detalle.get("Integrantes")):
            dip = _diputado(indice, di.get("Diputado") if isinstance(di, dict) else None)
            if dip is None:
                resultado["sin_identificar"] += 1
                d = (di or {}).get("Diputado") or {}
                logger.warning(f"Comisión {cid}: no se identificó a {d.get('Nombre')} {d.get('ApellidoPaterno')} "
                               f"{d.get('ApellidoMaterno')}")
                continue
            termino = _fecha(di.get("FechaTermino"))
            if termino and termino < datetime.now():  # ya salió de la comisión
                continue
            integrantes[dip] = (_fecha(di.get("FechaInicio")), termino)
        # se actualizan en su lugar: reemplazar la lista insertaría antes de borrar y chocaría con la clave única
        actuales = {i.diputado_id: i for i in c.integrantes}
        for dip, fila in actuales.items():
            if dip not in integrantes:
                c.integrantes.remove(fila)
        for dip, (inicio, termino) in integrantes.items():
            fila = actuales.get(dip)
            if fila is None:
                c.integrantes.append(ComisionIntegrante(diputado_id=dip, fecha_inicio=inicio, fecha_termino=termino))
            else:
                fila.fecha_inicio, fila.fecha_termino = inicio, termino
        resultado["integrantes"] += len(integrantes)
        resultado["comisiones"] += 1

        try:
            sesiones = await consultar(WS, "retornarSesionesXComisionYAnno", prmComisionId=cid, prmAnno=anno)
            for s in _lista((sesiones or {}).get("Sesiones") if isinstance(sesiones, dict) else None):
                sid = s.get("Id")
                if not sid:
                    continue
                fila = db.get(ComisionSesion, sid) or ComisionSesion(id=sid)
                fila.comision_id = cid
                fila.numero = s.get("Numero") if isinstance(s.get("Numero"), int) else None
                fila.tipo = _nombre(s.get("Tipo"))
                fila.estado = _nombre(s.get("Estado"))
                fila.fecha_inicio = _fecha(s.get("FechaInicio"))
                fila.fecha_termino = _fecha(s.get("FechaTermino"))
                db.add(fila)
                resultado["sesiones"] += 1
        except Exception as e:
            logger.error(f"No se pudieron leer las sesiones de la comisión {cid}: {e}")
            resultado["errores"] += 1
        db.flush()

    if vistas:
        db.query(Comision).filter(Comision.id.not_in(vistas)).update({"vigente": False}, synchronize_session=False)
    db.commit()
    logger.info(f"Comisiones: {resultado}")
    return resultado
