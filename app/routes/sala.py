"""WSSala: sesiones de Sala y asistencia."""
from datetime import date

from fastapi import APIRouter, Query

from app.camara.client import consultar, consultar_o_404

WS = "WSSala"
router = APIRouter(prefix="/sesiones", tags=["Sesiones de Sala"])


@router.get("", summary="Sesiones de Sala de un año")
async def sesiones(anno: int | None = Query(None, ge=1990, le=2100, description="Año (por defecto, el actual)")):
    return await consultar(WS, "retornarSesionesXAnno", prmAnno=anno or date.today().year)


@router.get("/legislatura/{legislatura_id}", summary="Sesiones de una legislatura (ver /legislaturas)")
async def sesiones_por_legislatura(legislatura_id: int):
    return await consultar(WS, "retornarSesionesXLegislatura", prmLegislaturaId=legislatura_id)


@router.get("/{sesion_id}", summary="Detalle de una sesión: votaciones y asistencia de cada diputado")
async def sesion(sesion_id: int):
    return await consultar_o_404(WS, "retornarSesionAsistencia", "Sesión no encontrada", prmSesionId=sesion_id)
