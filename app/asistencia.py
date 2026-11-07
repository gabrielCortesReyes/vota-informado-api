"""Asistencia de los diputados a las sesiones de Sala, con la justificación de cada inasistencia.

Fuente oficial: web service WSSala de la Cámara (opendata.camara.cl):
- `retornarSesionesXAnno`: sesiones de Sala de un año (Id, número, fecha, tipo y estado).
- `retornarSesionAsistencia`: por cada diputado, Asiste / No Asiste y, si no asistió,
  la justificación (p. ej. "Licencia médica (Art. 42)") con sus marcas RebajaAsistencia y RebajaQuorum.

Leer ~100 sesiones en cada consulta sería lento, así que se guardan en la base
(`sala_sessions` y `sala_attendance`) y se actualizan en la sincronización diaria.
Uso:  python -m app.cli asistencia [AAAA-MM-DD]   o   POST /admin/asistencia/sincronizar
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.camara.client import consultar
from app.config import ASISTENCIA_RECARGAR_DIAS, CAMARA_BASE_URL, PERIODO_INICIO
from app.models import SalaAttendance, SalaSession

logger = logging.getLogger(__name__)

CELEBRADA = 1  # Estado de la sesión
ASISTE = 1     # TipoAsistencia


def fuente_url(sesion_id: int) -> str:
    return f"{CAMARA_BASE_URL}/WSSala.asmx/retornarSesionAsistencia?prmSesionId={sesion_id}"


def _fecha(valor) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor)[:19]) if valor else None
    except ValueError:
        return None


def _tipo(valor) -> tuple[int | None, str | None]:
    """<Estado Valor="1">Celebrada</Estado> llega como {"Valor": 1, "Nombre": "Celebrada"}."""
    if isinstance(valor, dict):
        v = valor.get("Valor")
        return (v if isinstance(v, int) else None), valor.get("Nombre")
    return None, (str(valor) if valor is not None else None)


def _bool(valor) -> bool | None:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() == "true"
    return None


def _fila(sesion_id: int, item: dict) -> SalaAttendance | None:
    diputado = item.get("Diputado") or {}
    if not isinstance(diputado, dict) or not isinstance(diputado.get("Id"), int):
        return None
    valor, nombre = _tipo(item.get("TipoAsistencia"))
    asistio = valor == ASISTE if valor is not None else (nombre or "").strip().lower() == "asiste"
    j = item.get("Justificacion") if not asistio else None
    j = j if isinstance(j, dict) else {}
    return SalaAttendance(
        session_id=sesion_id, diputado_id=diputado["Id"], asistio=asistio, tipo_asistencia=nombre,
        justificacion_id=j.get("Valor") if isinstance(j.get("Valor"), int) else None,
        justificacion=(str(j["Nombre"]).strip()[:200] if j.get("Nombre") else None),
        rebaja_asistencia=_bool(j.get("RebajaAsistencia")), rebaja_quorum=_bool(j.get("RebajaQuorum")),
    )


async def sincronizar_asistencia(db: Session, desde: str | None = None,
                                 recargar_dias: int = ASISTENCIA_RECARGAR_DIAS) -> dict:
    """Guarda las sesiones celebradas desde `desde` (inicio del período) y la asistencia de cada una.

    Las sesiones ya cargadas no se vuelven a pedir, salvo las de los últimos `recargar_dias`
    (la Cámara suele registrar las justificaciones días después de la sesión).
    """
    inicio = date.fromisoformat(desde or PERIODO_INICIO)
    hoy = date.today()
    limite_recarga = hoy - timedelta(days=recargar_dias)
    resultado = {"sesiones": 0, "cargadas": 0, "sin_datos": 0, "errores": 0}
    for anno in range(inicio.year, hoy.year + 1):
        sesiones = await consultar("WSSala", "retornarSesionesXAnno", prmAnno=anno) or []
        if isinstance(sesiones, dict):  # un solo elemento
            sesiones = [sesiones]
        for s in sesiones:
            if not isinstance(s, dict) or not isinstance(s.get("Id"), int):
                continue
            fecha = _fecha(s.get("FechaInicio"))
            estado_valor, estado = _tipo(s.get("Estado"))
            if fecha is None or not (inicio <= fecha.date() <= hoy) or estado_valor != CELEBRADA:
                continue
            resultado["sesiones"] += 1
            ses = db.get(SalaSession, s["Id"])
            if ses is None:
                ses = SalaSession(id=s["Id"], asistencia_cargada=False)
                db.add(ses)
            ses.numero = s.get("Numero") if isinstance(s.get("Numero"), int) else None
            ses.fecha_inicio, ses.fecha_termino = fecha, _fecha(s.get("FechaTermino"))
            ses.tipo, ses.estado = _tipo(s.get("Tipo"))[1], estado
            if ses.asistencia_cargada and fecha.date() < limite_recarga:
                db.commit()
                continue
            try:
                detalle = await consultar("WSSala", "retornarSesionAsistencia", prmSesionId=s["Id"])
            except Exception as e:  # una sesión que falla no detiene las demás
                logger.warning(f"No se pudo leer la asistencia de la sesión {s['Id']}: {e}")
                db.commit()
                resultado["errores"] += 1
                continue
            listado = (detalle or {}).get("ListadoAsistencia") if isinstance(detalle, dict) else None
            if isinstance(listado, dict):
                listado = [listado]
            filas = {}
            for item in listado or []:
                fila = _fila(ses.id, item) if isinstance(item, dict) else None
                if fila:
                    filas[fila.diputado_id] = fila
            if not filas:  # la Cámara aún no publica la asistencia: se reintenta mañana
                db.commit()
                resultado["sin_datos"] += 1
                continue
            db.query(SalaAttendance).filter(SalaAttendance.session_id == ses.id).delete()
            db.add_all(filas.values())
            ses.asistencia_cargada = True
            db.commit()
            resultado["cargadas"] += 1
    logger.info(f"Asistencia: {resultado}")
    return resultado


# --- Consultas ------------------------------------------------------------------------

def _rango(query, desde: date | None, hasta: date | None):
    if desde:
        query = query.filter(SalaSession.fecha_inicio >= datetime.combine(desde, datetime.min.time()))
    if hasta:
        query = query.filter(SalaSession.fecha_inicio < datetime.combine(hasta + timedelta(days=1), datetime.min.time()))
    return query


def _resumen(sesiones: int, asistencias: int, justificadas: int, primera, ultima) -> dict:
    inasistencias = sesiones - asistencias
    return {
        "sesiones": sesiones, "asistencias": asistencias, "inasistencias": inasistencias,
        "justificadas": justificadas, "sin_justificar": inasistencias - justificadas,
        "porcentaje": round(100 * asistencias / sesiones, 1) if sesiones else None,
        "desde": primera.date() if primera else None, "hasta": ultima.date() if ultima else None,
    }


def _agregados():
    return (func.count(SalaAttendance.id),
            func.sum(case((SalaAttendance.asistio.is_(True), 1), else_=0)),
            func.sum(case(((SalaAttendance.asistio.is_(False)) & SalaAttendance.justificacion.isnot(None), 1), else_=0)),
            func.min(SalaSession.fecha_inicio), func.max(SalaSession.fecha_inicio))


def resumen_diputado(db: Session, diputado_id: int, desde: date | None = None, hasta: date | None = None) -> dict:
    q = db.query(*_agregados()).join(SalaSession).filter(SalaAttendance.diputado_id == diputado_id)
    n, si, just, primera, ultima = _rango(q, desde, hasta).one()
    return _resumen(n or 0, si or 0, just or 0, primera, ultima)


def resumen_todos(db: Session, desde: date | None = None, hasta: date | None = None) -> dict[int, dict]:
    q = db.query(SalaAttendance.diputado_id, *_agregados()).join(SalaSession).group_by(SalaAttendance.diputado_id)
    return {dip: _resumen(n or 0, si or 0, just or 0, primera, ultima)
            for dip, n, si, just, primera, ultima in _rango(q, desde, hasta).all()}


def inasistencias_diputado(db: Session, diputado_id: int, desde: date | None = None,
                           hasta: date | None = None) -> list[dict]:
    q = (db.query(SalaAttendance, SalaSession).join(SalaSession)
         .filter(SalaAttendance.diputado_id == diputado_id, SalaAttendance.asistio.is_(False)))
    filas = _rango(q, desde, hasta).order_by(SalaSession.fecha_inicio.desc()).all()
    return [{
        "sesion_id": s.id, "numero": s.numero, "fecha": s.fecha_inicio, "tipo_sesion": s.tipo,
        "justificada": a.justificacion is not None, "justificacion": a.justificacion,
        "justificacion_id": a.justificacion_id, "rebaja_asistencia": a.rebaja_asistencia,
        "rebaja_quorum": a.rebaja_quorum, "fuente_url": fuente_url(s.id),
    } for a, s in filas]


def ultima_actualizacion(db: Session) -> datetime | None:
    return db.query(func.max(SalaSession.updated_at)).filter(SalaSession.asistencia_cargada.is_(True)).scalar()
