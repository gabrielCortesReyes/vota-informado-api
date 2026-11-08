"""Patrimonio declarado de los diputados, desde InfoProbidad (CPLT + Contraloría, licencia CC BY 4.0).

Fuentes:
- Listado de declaraciones: https://www.infoprobidad.cl/Home/Listado trae embebido `var datos = [...]`,
  un arreglo con una fila por declaración (incluye `IdDeclaracion`).
- Cada declaración en JSON: POST https://www.infoprobidad.cl/Declaracion/descargarDeclaracionJSon con ID=<id>
  (secciones como Bienes_Inmuebles_Situados_En_Chile, Vehiculos_Motorizados, Pasivos...).

El JSON se guarda completo (`datos`) y además se calcula un resumen normalizado (`resumen`).
Los nombres de los campos dentro de cada sección no están documentados, así que la normalización
busca las claves por palabras (avalúo, valor, monto, comuna...) en vez de depender de nombres exactos.

Uso:  python -m app.cli patrimonio   o   POST /admin/patrimonio/sincronizar
      POST /admin/patrimonio/{parlamentario_id}?declaracion_id=N  (fijar una declaración a mano)
"""
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import INFOPROBIDAD_URL
from app.models import Parliamentarian, PatrimonioDeclaracion

logger = logging.getLogger(__name__)

LICENCIA = "InfoProbidad (Consejo para la Transparencia y Contraloría), licencia CC BY 4.0"
REGEX_DATOS = re.compile(r"var datos =\s*(null|\[\s*\]|\[.+?}\]);", re.S)

# Secciones del JSON de InfoProbidad → grupo del resumen
SECCIONES = {
    "inmuebles": ["Bienes_Inmuebles_Situados_En_Chile", "Bienes_Inmuebles_Situados_En_Extranjero"],
    "vehiculos": ["Vehiculos_Motorizados", "Naves_Artefactos_Navales", "Aeronaves"],
    "sociedades": ["Derechos_Acciones_Chile", "Derechos_Acciones_Extranjero"],
    "valores": ["Instrumentos_Valor_TransableChile", "Instrumentos_Valor_TransableExtranjero",
                "Deposito_Plazo_Adicional", "Ahorro_Previsional_Voluntario_Adicional",
                "Cuenta_de_Ahorro_Adicional", "Seguro_Adicional"],
    "otros_bienes": ["Otros_Bienes_Muebles", "Otros_Bienes", "Derecho_Aprovechamiento_De_Aguas", "Concesiones"],
    "pasivos": ["Pasivos", "Deudas_Pension_Alimentos"],
}
ACTIVOS = ("inmuebles", "vehiculos", "sociedades", "valores", "otros_bienes")

# Palabras (sin tildes, minúsculas) con que se buscan los campos de cada ítem, en orden de preferencia
MONTO = {
    "inmuebles": ["avaluo", "valor", "monto"],
    "vehiculos": ["avaluo", "valor", "tasacion", "monto"],
    "sociedades": ["valor_libro", "valorlibro", "valor", "monto"],
    "valores": ["valor_corriente", "valor", "monto", "saldo"],
    "otros_bienes": ["valor", "monto", "avaluo"],
    "pasivos": ["monto", "deuda", "saldo", "valor"],
}
TITULO = {
    "inmuebles": ["comuna", "direccion"],
    "vehiculos": ["marca", "modelo", "tipo"],
    "sociedades": ["razon_social", "razonsocial", "nombre", "sociedad"],
    "valores": ["emisor", "entidad", "institucion", "nombre", "tipo", "titulo"],
    "otros_bienes": ["descripcion", "nombre", "tipo"],
    "pasivos": ["acreedor", "institucion", "entidad", "tipo"],
}
DETALLE = {
    "inmuebles": ["direccion", "rol"],
    "vehiculos": ["modelo", "ano", "anio", "tipo"],
    "sociedades": ["giro", "titulo", "rut"],
    "valores": ["titulo", "tipo", "instrumento"],
    "otros_bienes": ["tipo", "descripcion"],
    "pasivos": ["tipo"],
}
NO_MONTO = ("porcentaje", "cantidad", "fecha", "rut", "rol", "anio", "numero", "cuotas")


# --- utilidades ---------------------------------------------------------------------------

def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def _clave(k: str) -> str:
    return _norm(k).replace(" ", "_")


def _texto(valor) -> str | None:
    """Los catálogos llegan como {"nombre": "..."}; los demás como texto o número."""
    if isinstance(valor, dict):
        for k in ("nombre", "Nombre", "descripcion", "valor"):
            if valor.get(k) not in (None, ""):
                return _texto(valor[k])
        return None
    if valor is None or isinstance(valor, (list, bool)):
        return None
    t = str(valor).strip()
    return t or None


def _numero(valor) -> float | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    t = _texto(valor)
    if not t:
        return None
    t = re.sub(r"[^\d,.\-]", "", t)
    if not re.search(r"\d", t):
        return None
    if "," in t and "." in t:          # 1.234.567,89
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):  # 1.234.567
        t = t.replace(".", "")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _buscar(item: dict, palabras: list[str], numerico: bool = False, excluir=()):
    claves = [(k, _clave(k)) for k in item]
    for palabra in palabras:
        for k, kn in claves:
            if palabra in kn and not any(x in kn for x in excluir):
                v = _numero(item[k]) if numerico else _texto(item[k])
                if v is not None:
                    return v
    return None


def _items(seccion) -> list[dict]:
    """Una sección puede ser lista, un solo objeto o un objeto con la lista adentro."""
    if seccion is None:
        return []
    if isinstance(seccion, list):
        return [i for i in seccion if isinstance(i, dict)]
    if isinstance(seccion, dict):
        for v in seccion.values():
            if isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
                return v
        return [seccion] if any(v not in (None, "", [], {}) for v in seccion.values()) else []
    return []


def _fecha(valor) -> date | None:
    t = _texto(valor)
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t[:10], fmt).date()
        except ValueError:
            continue
    return None


def _buscar_rec(datos, palabras: list[str], numerico: bool = False):
    """Busca una clave en todo el árbol (p. ej. la renta, que viene dentro de la entidad)."""
    if isinstance(datos, dict):
        v = _buscar(datos, palabras, numerico)
        if v is not None:
            return v
        for hijo in datos.values():
            if isinstance(hijo, (dict, list)):
                v = _buscar_rec(hijo, palabras, numerico)
                if v is not None:
                    return v
    elif isinstance(datos, list):
        for hijo in datos:
            v = _buscar_rec(hijo, palabras, numerico)
            if v is not None:
                return v
    return None


# --- normalización ------------------------------------------------------------------------

def _item(grupo: str, crudo: dict) -> dict:
    titulo = _buscar(crudo, TITULO[grupo]) or "Sin descripción"
    detalle = [d for d in (_buscar(crudo, [p]) for p in DETALLE[grupo]) if d and d != titulo]
    return {
        "titulo": titulo,
        "detalle": " · ".join(dict.fromkeys(detalle)) or None,
        "monto": _buscar(crudo, MONTO[grupo], numerico=True, excluir=NO_MONTO),
        "porcentaje": _buscar(crudo, ["porcentaje"], numerico=True),
        "fecha_adquisicion": _fecha(_buscar(crudo, ["fecha_adq", "fecha"])),
    }


def normalizar(datos: dict) -> dict:
    """Resumen de la declaración: ítems por grupo, totales y patrimonio neto estimado."""
    grupos = {}
    for grupo, secciones in SECCIONES.items():
        items = []
        for s in secciones:
            items += [_item(grupo, i) for i in _items(datos.get(s))]
        grupos[grupo] = items
    totales = {g: round(sum(i["monto"] or 0 for i in items)) for g, items in grupos.items()}
    activos = sum(totales[g] for g in ACTIVOS)
    for items in grupos.values():
        for i in items:
            if i["fecha_adquisicion"]:
                i["fecha_adquisicion"] = i["fecha_adquisicion"].isoformat()
    return {
        "renta_bruta_mensual": _buscar_rec(datos, ["renta"], numerico=True),
        **grupos,
        "totales": {**totales, "activos": activos, "patrimonio_neto": activos - totales["pasivos"]},
    }


# --- InfoProbidad -------------------------------------------------------------------------

def _cliente() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=60,
                             headers={"User-Agent": "VotaInformado/3.0 (datos abiertos InfoProbidad)"})


def declaracion_url(declaracion_id: int) -> str:
    return f"{INFOPROBIDAD_URL}/Declaracion/Declaracion?ID={declaracion_id}"


async def descargar_listado(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(f"{INFOPROBIDAD_URL}/Home/Listado")
    r.raise_for_status()
    m = REGEX_DATOS.search(r.text)
    if not m or m.group(1) == "null":
        raise RuntimeError("InfoProbidad devolvió el listado vacío (suele pasar unos días; reintentar)")
    return json.loads(m.group(1))


async def descargar_declaracion(client: httpx.AsyncClient, declaracion_id: int) -> dict:
    r = await client.post(f"{INFOPROBIDAD_URL}/Declaracion/descargarDeclaracionJSon", data={"ID": declaracion_id})
    r.raise_for_status()
    datos = r.json()
    if not isinstance(datos, dict) or not datos.get("Datos_del_Declarante"):
        raise ValueError(f"La declaración {declaracion_id} no trae datos del declarante")
    return datos


def _texto_fila(fila: dict) -> str:
    return _norm(" ".join(_texto(v) or "" for v in fila.values()))


def _fecha_fila(fila: dict) -> date | None:
    for k, v in fila.items():
        if "fecha" in _clave(k) and "asun" not in _clave(k):
            f = _fecha(v)
            if f:
                return f
    return None


def _id_fila(fila: dict) -> int | None:
    v = fila.get("IdDeclaracion")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def buscar_declaraciones(filas: list[dict], p: Parliamentarian) -> list[dict]:
    """Filas del listado que corresponden a un diputado: nombre y apellidos completos y cargo de diputado."""
    nombres = [_norm(x) for x in (p.first_name, p.last_name, p.second_last_name) if x]
    tokens = [t for n in nombres for t in n.split()]
    encontradas = []
    for fila in filas:
        texto = _texto_fila(fila)
        if "diputad" in texto and all(re.search(rf"\b{re.escape(t)}\b", texto) for t in tokens):
            encontradas.append(fila)
    return encontradas


def mas_reciente(filas: list[dict]) -> dict | None:
    """La más reciente por fecha; a igual fecha, el Id mayor (las rectificaciones tienen Id nuevo)."""
    validas = [f for f in filas if _id_fila(f)]
    if not validas:
        return None
    return max(validas, key=lambda f: (_fecha_fila(f) or date.min, _id_fila(f)))


def _tipo_y_fecha(datos: dict) -> tuple[str | None, date | None]:
    return _texto(datos.get("Tipo_Declaracion")), _fecha(datos.get("Fecha_de_la_Declaracion"))


def guardar(db: Session, p: Parliamentarian, declaracion_id: int, datos: dict) -> PatrimonioDeclaracion:
    tipo, fecha = _tipo_y_fecha(datos)
    d = db.get(PatrimonioDeclaracion, declaracion_id) or PatrimonioDeclaracion(id=declaracion_id)
    d.parliamentarian_id, d.tipo, d.fecha = p.id, tipo, fecha
    d.datos, d.resumen, d.cargada_at = datos, normalizar(datos), datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(d)
    db.commit()
    return d


async def cargar_declaracion(db: Session, p: Parliamentarian, declaracion_id: int) -> PatrimonioDeclaracion:
    async with _cliente() as client:
        return guardar(db, p, declaracion_id, await descargar_declaracion(client, declaracion_id))


async def sincronizar_patrimonio(db: Session, forzar: bool = False) -> dict:
    """Busca la declaración más reciente de cada diputado en ejercicio y descarga las que falten."""
    resultado = {"diputados": 0, "nuevas": 0, "sin_cambios": 0, "no_encontrados": 0, "errores": 0}
    async with _cliente() as client:
        filas = await descargar_listado(client)
        diputados = db.query(Parliamentarian).filter(Parliamentarian.chamber == "diputado",
                                                     Parliamentarian.current.is_(True)).all()
        for p in diputados:
            resultado["diputados"] += 1
            fila = mas_reciente(buscar_declaraciones(filas, p))
            if not fila:
                resultado["no_encontrados"] += 1
                logger.info(f"Patrimonio: sin declaración para {p.full_name}")
                continue
            did = _id_fila(fila)
            if not forzar and db.get(PatrimonioDeclaracion, did):
                resultado["sin_cambios"] += 1
                continue
            try:
                guardar(db, p, did, await descargar_declaracion(client, did))
                resultado["nuevas"] += 1
            except Exception as e:  # una declaración que falla no detiene las demás
                db.rollback()
                logger.warning(f"Patrimonio: no se pudo cargar la declaración {did} de {p.full_name}: {e}")
                resultado["errores"] += 1
    logger.info(f"Patrimonio: {resultado}")
    return resultado


def ultima_declaracion(db: Session, parl_id: int) -> PatrimonioDeclaracion | None:
    return (db.query(PatrimonioDeclaracion).filter(PatrimonioDeclaracion.parliamentarian_id == parl_id)
            .order_by(PatrimonioDeclaracion.fecha.desc().nullslast(), PatrimonioDeclaracion.id.desc()).first())
