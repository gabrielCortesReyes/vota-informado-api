"""Cliente único para los web services de la Cámara de Diputadas y Diputados de Chile.

Todas las rutas de datos pasan por `consultar()`: arma la URL HTTP GET del servicio
(.asmx/operacion?parametros), guarda la respuesta en caché y la devuelve como JSON.
"""
import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.camara.xml import xml_to_json
from app.config import CACHE_TTL, CAMARA_BASE_URL, CAMARA_TIMEOUT
from app.nombres import corregir_nombres

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, Any]] = {}
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=CAMARA_TIMEOUT, follow_redirects=True)
    return _client


async def cerrar() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def limpiar_cache() -> int:
    n = len(_cache)
    _cache.clear()
    return n


async def consultar(servicio: str, operacion: str, **params: Any) -> Any:
    """Llama a `servicio.asmx/operacion` (p.ej. WSLegislativo, retornarProyectoLey)."""
    params = {k: str(v) for k, v in params.items() if v is not None}
    url = f"{CAMARA_BASE_URL}/{servicio}.asmx/{operacion}"
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]

    try:
        response = await _http().get(url, params=params)
    except httpx.HTTPError as e:
        logger.error(f"Cámara no responde ({key}): {e}")
        raise HTTPException(status_code=502, detail="No se pudo conectar con la API de la Cámara")

    if response.status_code != 200:
        logger.error(f"Cámara respondió {response.status_code} ({key}): {response.text[:300]}")
        detail = "Parámetros inválidos para la API de la Cámara" if response.status_code in (400, 500) \
            else f"La API de la Cámara respondió {response.status_code}"
        raise HTTPException(status_code=502, detail=detail)

    try:
        data = xml_to_json(response.content)
    except Exception as e:
        logger.error(f"XML inválido de la Cámara ({key}): {e}")
        raise HTTPException(status_code=502, detail="Respuesta inválida de la API de la Cámara")

    # opendata entrega nombres de exdiputados para el período 2026-2030 (ver app/nombres.py)
    data = corregir_nombres(data, raiz_es_diputado=operacion == "retornarDiputado")

    _cache[key] = (time.monotonic(), data)
    return data


async def consultar_o_404(servicio: str, operacion: str, mensaje: str, **params: Any) -> Any:
    data = await consultar(servicio, operacion, **params)
    if data in (None, [], {}):
        raise HTTPException(status_code=404, detail=mensaje)
    return data
