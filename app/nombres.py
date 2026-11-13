"""Nombres correctos de los diputados del período 2026-2030.

Desde el 2026-11-13 el opendata de la Cámara devuelve nombres de exdiputados para los Id del
período 2026-2030 (p.ej. Id 1174 = "Gabriel Ascencio Mansilla" en vez de Marco Antonio
Sulantay Olivares) en todos sus métodos: diputados, votaciones, asistencia, autores. El partido
y los votos sí corresponden al Id. app/data/diputados_nombres.json se armó con la votación
90853 (2026-11-04) y se contrastó con el listado de camara.cl.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data"


@lru_cache
def diputados_nombres() -> dict[str, dict[str, str]]:
    """{Id de diputado en la Cámara: {nombre, apellido_paterno, apellido_materno}}"""
    return json.loads((DATA / "diputados_nombres.json").read_text(encoding="utf-8"))


def corregir_diputado(d: Any) -> Any:
    """Reemplaza Nombre/ApellidoPaterno/ApellidoMaterno de un diputado de la Cámara según su Id."""
    if not isinstance(d, dict) or d.get("Id") is None:
        return d
    correcto = diputados_nombres().get(str(d["Id"]))
    if correcto:
        d["Nombre"] = correcto["nombre"]
        d["Nombre2"] = None
        d["ApellidoPaterno"] = correcto["apellido_paterno"]
        d["ApellidoMaterno"] = correcto["apellido_materno"]
    return d


def corregir_nombres(data: Any, raiz_es_diputado: bool = False) -> Any:
    """Corrige cada objeto "Diputado" dentro de una respuesta de la Cámara (ya en JSON)."""
    if raiz_es_diputado:
        corregir_diputado(data)
    if isinstance(data, list):
        for x in data:
            corregir_nombres(x)
    elif isinstance(data, dict):
        for k, v in data.items():
            if k == "Diputado" and isinstance(v, dict):
                corregir_diputado(v)
            corregir_nombres(v)
    return data
