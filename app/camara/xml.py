"""Convierte el XML de los servicios de la Cámara a JSON (dict/list) de forma genérica."""
import re
import xml.etree.ElementTree as ET
from typing import Any

XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_INT = re.compile(r"^-?(0|[1-9]\d{0,17})$")


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _value(text: str | None) -> Any:
    if text is None:
        return None
    text = text.strip()
    if text == "":
        return None
    if _INT.match(text):
        return int(text)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def _is_collection(tag: str, children: list[ET.Element]) -> bool:
    """Un elemento es lista si todos sus hijos tienen el mismo nombre y
    se repiten, o si su nombre suena a colección (Votos, ListadoAsistencia, ...)."""
    if not children:
        return False
    names = {_local(c.tag) for c in children}
    if len(names) != 1:
        return False
    child = names.pop()
    if child == tag:
        return True
    return len(children) > 1 or tag.endswith(("s", "Coleccion")) or tag.startswith("Listado")


def element_to_json(el: ET.Element) -> Any:
    if el.get(XSI_NIL) == "true":
        return None
    tag = _local(el.tag)
    children = list(el)
    attrs = {_local(k): _value(v) for k, v in el.attrib.items() if not k.startswith("{")}

    if not children:
        text = _value(el.text)
        if attrs:  # p.ej. <Sexo Valor="1">Masculino</Sexo>
            return {**attrs, "Nombre": text}
        if text is None and tag.endswith(("s", "Coleccion")):
            return []
        return text

    if _is_collection(tag, children):
        return [element_to_json(c) for c in children]

    result: dict[str, Any] = dict(attrs)
    repetidas: set[str] = set()
    for c in children:
        key = _local(c.tag)
        value = element_to_json(c)
        if key in result:  # clave repetida en un objeto mixto → lista
            if key not in repetidas:
                result[key] = [result[key]]
                repetidas.add(key)
            result[key].append(value)
        else:
            result[key] = value
    return result


def xml_to_json(content: bytes) -> Any:
    return element_to_json(ET.fromstring(content))
