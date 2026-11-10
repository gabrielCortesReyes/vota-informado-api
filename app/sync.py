"""Carga el padrón de parlamentarios y partidos en la base.

Fuentes:
- Cámara (WSDiputado / WSComun): diputados en ejercicio y partidos.
- Senado (tramitacion.senado.cl/wspublico/senadores_vigentes.php): senadores en ejercicio.
- Wikipedia en español: descripción, foto y enlace de cada parlamentario.

- Cámara (WSSala): asistencia a la Sala y justificaciones (ver app/asistencia.py).

Uso:  python -m app.cli sincronizar [--sin-wikipedia]   o   POST /admin/sincronizar
"""
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.asistencia import sincronizar_asistencia
from app.patrimonio import sincronizar_patrimonio
from app.proyectos import sincronizar_proyectos
from app.camara.client import consultar
from app.config import COMMONS_API, SENADO_SENADORES_URL, WIKIDATA_API, WIKIPEDIA_API, WIKIPEDIA_USER_AGENT
from app.distritos import DISTRITOS_VIGENTES, diputados_distrito
from app.models import Parliamentarian, Party

logger = logging.getLogger(__name__)


def _cliente(**kwargs) -> httpx.AsyncClient:
    """Punto único para crear clientes HTTP (las pruebas lo reemplazan)."""
    return httpx.AsyncClient(follow_redirects=True, **kwargs)


def _norm(texto: str | None) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto).strip().lower()


def _party(db: Session, name: str | None, alias: str | None = None, external_id=None) -> Party | None:
    if not name:
        return None
    # La Cámara identifica los partidos con siglas ("PC", "IND"); el XML puede traerlas como número
    external_id = (str(external_id).strip() or None) if external_id is not None else None
    party = None
    if external_id is not None:
        party = db.query(Party).filter(Party.external_id == external_id).first()
    if party is None:  # buscar por nombre o alias (el Senado solo entrega texto)
        clave = _norm(name)
        for p in db.query(Party).all():
            if clave in (_norm(p.name), _norm(p.alias)):
                party = p
                break
    if party is None:
        party = Party(name=name.strip(), alias=alias, external_id=external_id)
        db.add(party)
        db.flush()
    else:
        party.alias = party.alias or alias
        party.external_id = party.external_id or external_id
    return party


def _upsert(db: Session, chamber: str, external_id: int, **campos) -> Parliamentarian:
    p = db.query(Parliamentarian).filter_by(chamber=chamber, external_id=external_id).first()
    if p is None:
        p = Parliamentarian(chamber=chamber, external_id=external_id)
        db.add(p)
    for k, v in campos.items():
        if v is not None or k in ("party",):
            setattr(p, k, v)
    p.current = True
    return p


async def sincronizar_partidos(db: Session) -> int:
    partidos = await consultar("WSComun", "retornarPartidosPoliticos") or []
    for item in partidos:
        if isinstance(item, dict) and item.get("Nombre"):
            _party(db, item["Nombre"], item.get("Alias"), item.get("Id"))
    db.commit()
    return len(partidos)


def _militancia_actual(militancias) -> dict | None:
    if not isinstance(militancias, list) or not militancias:
        return None
    vigentes = [m for m in militancias if isinstance(m, dict) and not m.get("FechaTermino")]
    candidatas = vigentes or militancias
    return max(candidatas, key=lambda m: str(m.get("FechaInicio") or ""))


def _distrito_diputado(item: dict, diputado_id: int) -> int | None:
    """Distrito del WS si viene y es uno de los 28 vigentes; si no, el de app/data."""
    ws = item.get("Distrito") if isinstance(item.get("Distrito"), dict) else {}
    try:
        numero = int(ws.get("Numero"))
    except (TypeError, ValueError):
        numero = None
    if numero and 1 <= numero <= DISTRITOS_VIGENTES:
        return numero
    return diputados_distrito().get(str(diputado_id))


async def sincronizar_diputados(db: Session) -> int:
    periodo = await consultar("WSDiputado", "retornarDiputadosPeriodoActual") or []
    vistos = set()
    for item in periodo:
        d = (item or {}).get("Diputado") or {}
        if not d.get("Id"):
            continue
        mil = _militancia_actual(d.get("Militancias"))
        distrito = _distrito_diputado(item, int(d["Id"]))
        partido = (mil or {}).get("Partido") or {}
        sexo = d.get("Sexo")
        _upsert(
            db, "diputado", int(d["Id"]),
            first_name=" ".join(x for x in (d.get("Nombre"), d.get("Nombre2")) if x) or "",
            last_name=d.get("ApellidoPaterno") or "",
            second_last_name=d.get("ApellidoMaterno"),
            gender=sexo.get("Nombre") if isinstance(sexo, dict) else sexo,
            birth_date=str(d["FechaNacimiento"])[:10] if d.get("FechaNacimiento") else None,
            party=_party(db, partido.get("Nombre"), partido.get("Alias"), partido.get("Id")),
            district=str(distrito) if distrito else None,
        )
        vistos.add(int(d["Id"]))
    _marcar_no_vigentes(db, "diputado", vistos)
    db.commit()
    return len(vistos)


def _texto(el: ET.Element, *nombres: str) -> str | None:
    """Busca un hijo por nombre sin importar mayúsculas (el XML del Senado usa MAYÚSCULAS)."""
    buscados = {n.lower() for n in nombres}
    for hijo in el:
        tag = hijo.tag.split("}", 1)[-1].lower()
        if tag in buscados and hijo.text and hijo.text.strip():
            return hijo.text.strip()
    return None


async def sincronizar_senadores(db: Session) -> int:
    async with _cliente(timeout=30) as client:
        r = await client.get(SENADO_SENADORES_URL)
        r.raise_for_status()
    root = ET.fromstring(r.content)
    vistos = set()
    for s in root.iter():
        if s.tag.split("}", 1)[-1].lower() != "senador":
            continue
        parlid = _texto(s, "PARLID", "ID")
        if not parlid or not parlid.isdigit():
            continue
        _upsert(
            db, "senador", int(parlid),
            first_name=_texto(s, "PARLNOMBRE", "NOMBRE") or "",
            last_name=_texto(s, "PARLAPELLIDOPATERNO", "APELLIDOPATERNO") or "",
            second_last_name=_texto(s, "PARLAPELLIDOMATERNO", "APELLIDOMATERNO"),
            region=_texto(s, "REGION"),
            district=_texto(s, "CIRCUNSCRIPCION"),
            email=_texto(s, "EMAIL", "CORREO"),
            phone=_texto(s, "FONO", "TELEFONO"),
            gender=_texto(s, "SEXO"),
            party=_party(db, _texto(s, "PARTIDO")),
        )
        vistos.add(int(parlid))
    if vistos:  # si el formato cambió y no se leyó nada, no se marcan todos como no vigentes
        _marcar_no_vigentes(db, "senador", vistos)
    db.commit()
    return len(vistos)


def _marcar_no_vigentes(db: Session, chamber: str, vistos: set[int]) -> None:
    for p in db.query(Parliamentarian).filter(Parliamentarian.chamber == chamber, Parliamentarian.current.is_(True)):
        if p.external_id not in vistos:
            p.current = False


# --- Wikipedia -------------------------------------------------------------------

PALABRAS_CARGO = ("diputad", "senador", "politic", "parlamentari", "abogad")


def _es_la_persona(p: Parliamentarian, titulo: str, extracto: str) -> bool:
    """Evita asignar el artículo de otra persona: el título debe contener nombre y apellido,
    y el texto debe hablar de un político chileno."""
    t, e = _norm(titulo), _norm(extracto)
    nombre = _norm(p.first_name).split(" ")[0] if p.first_name else ""
    return (nombre in t and _norm(p.last_name) in t and "chilen" in e
            and any(k in e for k in PALABRAS_CARGO))


async def _buscar_wikipedia(client: httpx.AsyncClient, p: Parliamentarian) -> dict | None:
    cargo = "diputado" if p.chamber == "diputado" else "senador"
    consulta = f"{p.first_name.split(' ')[0]} {p.last_name} {p.second_last_name or ''} {cargo} Chile"
    r = await client.get(f"{WIKIPEDIA_API}/w/api.php", params={
        "action": "query", "list": "search", "srsearch": consulta, "srlimit": 5, "format": "json",
    })
    r.raise_for_status()
    for hit in r.json().get("query", {}).get("search", []):
        titulo = hit["title"]
        s = await client.get(f"{WIKIPEDIA_API}/api/rest_v1/page/summary/{titulo.replace(' ', '_')}")
        if s.status_code != 200:
            continue
        data = s.json()
        if data.get("type") == "disambiguation":
            continue
        if _es_la_persona(p, data.get("title", titulo), data.get("extract", "")):
            return data
    return None


async def sincronizar_wikipedia(db: Session, solo_faltantes: bool = True, limite: int | None = None) -> dict:
    q = db.query(Parliamentarian).filter(Parliamentarian.current.is_(True))
    if solo_faltantes:
        q = q.filter(Parliamentarian.wikipedia_url.is_(None))
    pendientes = q.order_by(Parliamentarian.id).limit(limite).all() if limite else q.order_by(Parliamentarian.id).all()
    encontrados = 0
    async with _cliente(timeout=20, headers={"User-Agent": WIKIPEDIA_USER_AGENT}) as client:
        for p in pendientes:
            try:
                data = await _buscar_wikipedia(client, p)
            except httpx.HTTPError as e:
                logger.warning(f"Wikipedia falló para {p.full_name}: {e}")
                continue
            p.wikipedia_checked_at = datetime.now(timezone.utc)
            if data:
                p.description = data.get("extract")
                p.wikipedia_url = (data.get("content_urls") or {}).get("desktop", {}).get("page")
                p.photo_url = (data.get("thumbnail") or {}).get("source") or p.photo_url
                encontrados += 1
            db.commit()
    return {"revisados": len(pendientes), "encontrados": encontrados}


# --- Logos de partidos (Wikipedia → Wikidata P154 → Wikimedia Commons) -------------

CHILE = "Q298"
_VACIAS = {"partido", "de", "del", "la", "el", "los", "las", "y", "chile", "por"}


def _palabras(texto: str) -> set[str]:
    texto = re.sub(r"\(.*?\)", " ", _norm(texto))
    return {w for w in re.findall(r"[a-z0-9]+", texto) if w not in _VACIAS}


def _mismo_nombre(partido: Party, titulo: str) -> bool:
    t = _palabras(titulo)
    return any(n and t and (n <= t or t <= n) for n in (_palabras(partido.name), _palabras(partido.alias or "")))


def _logo_vigente(claims: list) -> str | None:
    """De los valores de P154 (logo): el preferido, o el que no tiene fecha de término, o el último."""
    vals = [c for c in claims if c.get("rank") != "deprecated" and
            ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")]
    if not vals:
        return None
    sin_fin = [c for c in vals if "P582" not in (c.get("qualifiers") or {})]
    elegido = next((c for c in vals if c.get("rank") == "preferred"), None) or (sin_fin or vals)[-1]
    return elegido["mainsnak"]["datavalue"]["value"]


def _sin_html(texto: str | None) -> str | None:
    if not texto:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", texto)).strip()[:300] or None


async def _buscar_logo(client: httpx.AsyncClient, partido: Party) -> dict | None:
    if _norm(partido.name).startswith("independiente"):
        return None
    r = await client.get(f"{WIKIPEDIA_API}/w/api.php", params={
        "action": "query", "list": "search", "srsearch": f"{partido.name} partido político Chile",
        "srlimit": 5, "format": "json"})
    r.raise_for_status()
    titulos = [h["title"] for h in r.json().get("query", {}).get("search", []) if _mismo_nombre(partido, h["title"])]
    if not titulos:
        return None
    r = await client.get(f"{WIKIPEDIA_API}/w/api.php", params={
        "action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1,
        "titles": "|".join(titulos), "format": "json"})
    r.raise_for_status()
    por_titulo = {p.get("title"): (p.get("pageprops") or {}).get("wikibase_item")
                  for p in (r.json().get("query", {}).get("pages") or {}).values()}
    qids = [por_titulo[t] for t in titulos if por_titulo.get(t)]
    if not qids:
        return None
    r = await client.get(WIKIDATA_API, params={"action": "wbgetentities", "ids": "|".join(qids),
                                               "props": "claims", "format": "json"})
    r.raise_for_status()
    entidades = r.json().get("entities", {})
    for qid in qids:
        claims = (entidades.get(qid) or {}).get("claims", {})
        paises = {((c.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id")
                  for c in claims.get("P17", [])}
        archivo = _logo_vigente(claims.get("P154", []))
        if CHILE not in paises or not archivo:
            continue
        r = await client.get(COMMONS_API, params={
            "action": "query", "titles": f"File:{archivo}", "prop": "imageinfo",
            "iiprop": "url|extmetadata", "iiurlwidth": 256, "format": "json"})
        r.raise_for_status()
        for page in (r.json().get("query", {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [None])[0]
            if not info:
                continue
            meta = info.get("extmetadata") or {}
            return {"logo_url": info.get("thumburl") or info.get("url"),
                    "logo_source_url": info.get("descriptionurl"),
                    "logo_license": _sin_html((meta.get("LicenseShortName") or {}).get("value")),
                    "logo_author": _sin_html((meta.get("Artist") or {}).get("value"))}
    return None


async def sincronizar_logos(db: Session, solo_faltantes: bool = True) -> dict:
    q = db.query(Party)
    if solo_faltantes:
        q = q.filter(Party.logo_url.is_(None))
    pendientes = q.order_by(Party.id).all()
    encontrados = 0
    async with _cliente(timeout=20, headers={"User-Agent": WIKIPEDIA_USER_AGENT}) as client:
        for partido in pendientes:
            try:
                logo = await _buscar_logo(client, partido)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                logger.warning(f"No se pudo buscar el logo de {partido.name}: {e}")
                continue
            partido.logo_checked_at = datetime.now(timezone.utc)
            if logo and logo["logo_url"]:
                for k, v in logo.items():
                    setattr(partido, k, v)
                encontrados += 1
            db.commit()
    return {"revisados": len(pendientes), "encontrados": encontrados}


async def sincronizar_todo(db: Session, wikipedia: bool = True) -> dict:
    resultado = {}
    for nombre, tarea in (("partidos", sincronizar_partidos), ("diputados", sincronizar_diputados),
                          ("senadores", sincronizar_senadores), ("asistencia", sincronizar_asistencia),
                          ("patrimonio", sincronizar_patrimonio), ("proyectos", sincronizar_proyectos)):
        try:
            resultado[nombre] = await tarea(db)
        except Exception as e:  # una fuente caída no detiene a las demás
            db.rollback()
            logger.error(f"Error sincronizando {nombre}: {e}")
            resultado[nombre] = f"error: {e}"
    if wikipedia:
        try:
            resultado["wikipedia"] = await sincronizar_wikipedia(db)
        except Exception as e:
            db.rollback()
            resultado["wikipedia"] = f"error: {e}"
        try:
            resultado["logos"] = await sincronizar_logos(db)
        except Exception as e:
            db.rollback()
            resultado["logos"] = f"error: {e}"
    logger.info(f"Sincronización: {resultado}")
    return resultado
