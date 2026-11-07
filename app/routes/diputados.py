"""WSDiputado: diputadas y diputados."""
from fastapi import APIRouter

from app.camara.client import consultar, consultar_o_404

WS = "WSDiputado"
router = APIRouter(prefix="/diputados", tags=["Diputados"])


@router.get("", summary="Todos los diputados (histórico)")
async def diputados():
    return await consultar(WS, "retornarDiputados")


@router.get("/actuales", summary="Diputados del período actual, con militancias")
async def diputados_actuales():
    return await consultar(WS, "retornarDiputadosPeriodoActual")


@router.get("/periodo/{periodo_id}", summary="Diputados de un período legislativo (ver /periodos)")
async def diputados_por_periodo(periodo_id: int):
    return await consultar(WS, "retornarDiputadosXPeriodo", prmPeriodoID=periodo_id)


@router.get("/{diputado_id}", summary="Detalle de un diputado")
async def diputado(diputado_id: int):
    return await consultar_o_404(WS, "retornarDiputado", "Diputado no encontrado", prmDiputadoId=diputado_id)
