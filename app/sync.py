"""Carga el padrón de parlamentarios y partidos en la base.

Fuentes:
- Cámara (WSDiputado / WSComun): diputados en ejercicio y partidos.
- Senado (tramitacion.senado.cl/wspublico/senadores_vigentes.php): senadores en ejercicio.
- Wikipedia en español: descripción, foto y enlace de cada parlamentario.

Uso:  python -m app.cli sincronizar [--sin-wikipedia]   o   POST /admin/sincronizar
"""
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.camara.client import consultar
from app.config import SENADO_SENADORES_URL, WIKIPEDIA_API, WIKIPEDIA_USER_AGENT
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


async def sincronizar_diputados(db: Session) -> int:
    periodo = await consultar("WSDiputado", "retornarDiputadosPeriodoActual") or []
    vistos = set()
    for item in periodo:
        d = (item or {}).get("Diputado") or {}
        if not d.get("Id"):
            continue
        mil = _militancia_actual(d.get("Militancias"))
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


async def sincronizar_todo(db: Session, wikipedia: bool = True) -> dict:
    resultado = {}
    for nombre, tarea in (("partidos", sincronizar_partidos), ("diputados", sincronizar_diputados),
                          ("senadores", sincronizar_senadores)):
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
    logger.info(f"Sincronización: {resultado}")
    return resultado
