"""Qué se votó en cada votación y su resultado real.

La Cámara describe lo que se vota en el campo `Articulo` de `retornarVotacionesXProyectoLey`, como texto
libre con saltos de línea. En el Presupuesto, por ejemplo:

    del diputado Rivas José Antonio - Votación Separada
    Partida 18 MINISTERIO DE VIVIENDA Y URBANISMO
    Capítulo 28 Serviu Región del Biobío
    ...

`que_se_vota()` lo separa en campos (autor, partida, capítulo, programa, indicaciones, texto) y arma un
título legible. El detalle de una votación (`retornarVotacionDetalle`) no trae ese campo: se toma de las
votaciones del proyecto.

Además, `retornarVotacionesXProyectoLey` marca como «Unánime» (Valor 2) las votaciones rechazadas; el
resultado correcto viene en `retornarVotacionesXAnno` y en el detalle.
"""
import logging
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.camara.client import consultar

logger = logging.getLogger(__name__)

WS = "WSLegislativo"
_BOLETIN = re.compile(r"(\d{1,6}-\d{2})")
_NIVELES = {  # nombre en el texto → clave
    "partida": "partida", "part": "partida",
    "capítulo": "capitulo", "capitulo": "capitulo", "cap": "capitulo",
    "programa": "programa", "prog": "programa",
    "subtítulo": "subtitulo", "subtitulo": "subtitulo", "sub": "subtitulo",
    "item": "item", "ítem": "item",
    "asignación": "asignacion", "asignacion": "asignacion", "asig": "asignacion",
}
_ORDEN = ("partida", "capitulo", "programa", "subtitulo", "item", "asignacion")
_ETIQUETA = {"partida": "Partida", "capitulo": "Capítulo", "programa": "Programa", "subtitulo": "Subtítulo",
             "item": "Ítem", "asignacion": "Asignación"}
_NIVEL_LARGO = re.compile(r"^(partida|cap[ií]tulo|programa|subt[ií]tulo|[ií]tem|asignaci[oó]n)\s+(\d+)\s*(.*)$",
                          re.IGNORECASE)
_NIVEL_CORTO = re.compile(r"\b(Part|Cap|Prog|Sub|Item|Asig)\.?\s+(\d+)\b", re.IGNORECASE)
_AUTOR = re.compile(r"^\s*(de(?:l| la| los| las)\s+(?:diputad[oa]s?|senador[ae]?s?|Ejecutivo|Presidente|Presidenta)\b.*?)"
                    r"\s*(?:-\s*Votaci[oó]n Separada\s*)?$", re.IGNORECASE)
_SEPARADA = re.compile(r"votaci[oó]n separada", re.IGNORECASE)
_INDICACIONES = re.compile(r"^Indicaci[oó]n(?:es)?\s*:?\s*(.+)$", re.IGNORECASE)
_INADMISIBLE = re.compile(r"^Partida\s+(\d+)\s+Indicaci[oó]n(?:es)?\s+(.+?)\s+-\s+(.+)$", re.IGNORECASE)
_PALABRAS_MENORES = {"de", "del", "la", "las", "el", "los", "y", "e", "a", "al", "en", "para", "por", "con", "o", "u"}


def boletin_de(descripcion: Any) -> str | None:
    m = _BOLETIN.search(str(descripcion or ""))
    return m.group(1) if m else None


def _nombre_legible(texto: str) -> str:
    """'TESORO PÚBLICO' → 'Tesoro Público'; deja tal cual lo que ya viene en mayúsculas y minúsculas."""
    texto = re.sub(r"\s+", " ", texto).strip(" .,:-")
    if not texto or texto != texto.upper():
        return texto
    palabras = texto.lower().split(" ")
    return " ".join(p if i and p in _PALABRAS_MENORES else p[:1].upper() + p[1:] for i, p in enumerate(palabras))


def _limpiar(texto: str) -> str:
    texto = texto.replace("“", '"').replace("”", '"').replace('""', '"')
    return re.sub(r"[ \t]+", " ", texto).strip()


def que_se_vota(articulo: Any) -> dict | None:
    """Separa el campo Articulo de la Cámara en partes. None si no viene."""
    if not articulo or not str(articulo).strip():
        return None
    original = str(articulo)
    out: dict[str, Any] = {
        "titulo": None, "autor": None, "votacion_separada": bool(_SEPARADA.search(original)),
        "admisibilidad": False, "nota": None, "indicaciones": None, "detalle": None, "texto": None,
        "niveles": [], "original": original.strip(),
    }
    niveles: dict[str, dict] = {}
    resto: list[str] = []

    for linea in (_limpiar(x) for x in original.splitlines()):
        if not linea:
            continue
        if m := _INADMISIBLE.match(linea):  # «Partida 50 Indicación 56 - En votación la reclamación ...»
            niveles["partida"] = {"numero": m.group(1), "nombre": None}
            out["indicaciones"] = m.group(2).strip()
            out["admisibilidad"] = "admisib" in m.group(3).lower()
            out["detalle"] = m.group(3).strip()
            continue
        if re.fullmatch(r"\(.*\)", linea):  # «(Ley Orgánica Constitucional)»
            out["nota"] = linea.strip("()")
            continue
        if m := _AUTOR.match(linea):
            if not out["autor"]:
                out["autor"] = re.sub(r"\s+", " ", m.group(1)).strip()
            continue
        if _SEPARADA.fullmatch(linea.strip(" .")):
            continue
        if m := _NIVEL_LARGO.match(linea):
            clave = _NIVELES.get(m.group(1).lower())
            nombre = _SEPARADA.sub("", m.group(3)).strip()
            if clave:
                niveles[clave] = {"numero": m.group(2), "nombre": _nombre_legible(nombre) or None}
                continue
        cortos = _NIVEL_CORTO.findall(linea)
        if len(cortos) >= 2 and re.fullmatch(r"[\sA-Za-zÍí.\d-]+", linea):  # «Part 23 - Cap 01 - Prog 01 - Sub 22»
            for etiqueta, numero in cortos:
                niveles[_NIVELES[etiqueta.lower()]] = {"numero": numero, "nombre": None}
            continue
        if m := _INDICACIONES.match(linea):
            out["indicaciones"] = m.group(1).strip(" .")
            continue
        texto = _SEPARADA.sub("", linea).strip()
        if texto:
            resto.append(texto)

    out["niveles"] = [{"nivel": k, "etiqueta": _ETIQUETA[k], **niveles[k]} for k in _ORDEN if k in niveles]
    if resto:
        texto = "\n".join(resto).strip().strip('"').strip()
        # una línea corta que no es el texto de una indicación (p. ej. «la glosa 16, con excepción…»)
        if len(resto) == 1 and len(texto) <= 160 and not out["detalle"]:
            out["detalle"] = texto
        else:
            out["texto"] = texto
    out["titulo"] = _titulo(out)
    return out


def _titulo(q: dict) -> str:
    partes = []
    if q["admisibilidad"]:
        partes.append("Reclamo de inadmisibilidad")
    elif q["votacion_separada"]:
        partes.append("Votación separada")
    elif q["texto"]:
        partes.append("Indicación")
    niveles = q["niveles"]
    con_nombre = [n for n in niveles if n["nombre"]]
    if con_nombre:
        # la partida (qué ministerio) y lo más específico que se vota
        elegidos = [con_nombre[0]] + con_nombre[-2:] if len(con_nombre) > 2 else con_nombre
        vistos, ruta = set(), []
        for n in elegidos:
            if n["nivel"] not in vistos and n["nombre"].lower() not in vistos:
                vistos.update((n["nivel"], n["nombre"].lower()))
                ruta.append(n["nombre"])
        partes.append(" › ".join(ruta))
    elif niveles:
        partes.append(" · ".join(f"{n['etiqueta']} {n['numero']}" for n in niveles))
    if q["indicaciones"]:
        partes.append(f"Indicación {q['indicaciones']}" if " " not in q["indicaciones"] and "," not in q["indicaciones"]
                      else f"Indicaciones {q['indicaciones']}")
    if not niveles and not q["indicaciones"]:
        extra = q["detalle"] or q["texto"] or q["original"]
        partes.append(re.sub(r"\s+", " ", extra)[:160])
    elif q["detalle"] and not q["admisibilidad"]:
        partes.append(q["detalle"][:80])
    return " · ".join(p for p in partes if p)


def completar_partidas(lista: list[dict]) -> None:
    """Pone nombre a las partidas que solo vienen con número («Partida 50 Indicación 56») si otra
    votación del mismo proyecto lo trae («Partida 50 TESORO PÚBLICO»), y rehace el título."""
    nombres: dict[tuple[str, str], str] = {}
    for q in lista:
        for n in q["niveles"]:
            if n["nombre"] and n["nivel"] == "partida":
                nombres.setdefault((n["nivel"], n["numero"].lstrip("0")), n["nombre"])
    for q in lista:
        cambio = False
        for n in q["niveles"]:
            if not n["nombre"] and (nombre := nombres.get((n["nivel"], n["numero"].lstrip("0")))):
                n["nombre"] = nombre
                cambio = True
        if cambio:
            q["titulo"] = _titulo(q)


# --- Resultado -------------------------------------------------------------------------------

def _anno(v: dict) -> int | None:
    try:
        return datetime.fromisoformat(str(v.get("Fecha"))).year
    except (TypeError, ValueError):
        return None


async def _resultados_del_anno(anno: int) -> dict[int, Any]:
    try:
        lista = await consultar(WS, "retornarVotacionesXAnno", prmAnno=anno)
    except HTTPException as e:
        logger.warning(f"No se pudieron leer las votaciones de {anno} para corregir resultados: {e.detail}")
        return {}
    return {x.get("Id"): x.get("Resultado") for x in lista or [] if isinstance(x, dict) and x.get("Resultado")}


def _es_unanime_sospechoso(v: dict) -> bool:
    r = v.get("Resultado")
    valor = r.get("Valor") if isinstance(r, dict) else None
    return valor == 2 and ((v.get("TotalNo") or 0) > 0 or (v.get("TotalAbstencion") or 0) > 0)


async def corregir_resultados(votaciones: list[dict]) -> None:
    """Reemplaza el Resultado de las votaciones del proyecto por el de retornarVotacionesXAnno."""
    annos = {_anno(v) for v in votaciones if isinstance(v, dict)} - {None}
    correctos: dict[int, Any] = {}
    for anno in sorted(annos):
        correctos.update(await _resultados_del_anno(anno))
    for v in votaciones:
        if not isinstance(v, dict):
            continue
        bueno = correctos.get(v.get("Id"))
        if bueno and bueno != v.get("Resultado"):
            v["Resultado"] = bueno
            v["ResultadoCorregido"] = True
        elif _es_unanime_sospechoso(v):  # sin el dato del año: mejor sin resultado que uno falso
            v["Resultado"] = None
            v["ResultadoCorregido"] = True


def _lista(x: Any) -> list:
    return x if isinstance(x, list) else ([x] if isinstance(x, dict) else [])


def tramitacion_url(proyecto: dict) -> str | None:
    pid, boletin = proyecto.get("Id"), proyecto.get("NumeroBoletin")
    if not pid or not boletin:
        return None
    return f"https://www.camara.cl/legislacion/ProyectosDeLey/tramitacion.aspx?prmID={pid}&prmBOLETIN={boletin}"


async def enriquecer_proyecto(proyecto: Any) -> Any:
    """Agrega QueSeVota a cada votación, corrige el resultado y la URL de tramitación (en el mismo objeto)."""
    if not isinstance(proyecto, dict) or proyecto.get("_enriquecido"):
        return proyecto
    votaciones = _lista(proyecto.get("Votaciones"))
    proyecto["Votaciones"] = votaciones
    for v in votaciones:
        if isinstance(v, dict):
            v["QueSeVota"] = que_se_vota(v.get("Articulo"))
    completar_partidas([v["QueSeVota"] for v in votaciones if isinstance(v, dict) and v.get("QueSeVota")])
    await corregir_resultados(votaciones)
    proyecto["TramitacionUrl"] = tramitacion_url(proyecto)
    proyecto["_enriquecido"] = True  # está en la caché: no repetir
    return proyecto


_CAMPOS_DEL_PROYECTO = ("Articulo", "TipoVotacionProyectoLey", "TramiteConstitucional", "TramiteReglamentario",
                        "QueSeVota")


async def enriquecer_votacion(detalle: Any) -> Any:
    """Completa el detalle de una votación con lo que solo viene en las votaciones del proyecto."""
    if not isinstance(detalle, dict) or detalle.get("_enriquecido"):
        return detalle
    boletin = boletin_de(detalle.get("Descripcion"))
    if boletin:
        try:
            proyecto = await consultar(WS, "retornarVotacionesXProyectoLey", prmNumeroBoletin=boletin)
            await enriquecer_proyecto(proyecto)
            propia = next((v for v in _lista((proyecto or {}).get("Votaciones"))
                           if isinstance(v, dict) and v.get("Id") == detalle.get("Id")), None)
            if propia:
                for campo in _CAMPOS_DEL_PROYECTO:
                    if propia.get(campo) is not None and detalle.get(campo) is None:
                        detalle[campo] = propia[campo]
            detalle["TramitacionUrl"] = (proyecto or {}).get("TramitacionUrl")
        except HTTPException as e:
            logger.warning(f"No se pudo completar la votación {detalle.get('Id')} con el proyecto {boletin}: {e.detail}")
    detalle.setdefault("QueSeVota", None)
    detalle["_enriquecido"] = True
    return detalle


def sin_marcas(data: Any) -> Any:
    """Copia superficial sin la marca interna _enriquecido."""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k != "_enriquecido"}
    return data
