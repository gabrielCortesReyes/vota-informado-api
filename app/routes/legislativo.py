"""WSLegislativo: proyectos de ley, votaciones, legislaturas, períodos, materias y trámites."""
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.camara.client import consultar, consultar_o_404
from app.database import get_db
from app.distritos import votos_del_distrito

WS = "WSLegislativo"
router = APIRouter(tags=["Legislativo"])


def _anno(anno: int | None) -> int:
    return anno or date.today().year


AnnoQuery = Query(None, ge=1990, le=2100, description="Año (por defecto, el actual)")


# --- Proyectos de ley -------------------------------------------------------

@router.get("/proyectos/mensajes", summary="Mensajes (proyectos del Ejecutivo) ingresados en un año")
async def mensajes(anno: int | None = AnnoQuery):
    return await consultar(WS, "retornarMensajesXAnno", prmAnno=_anno(anno))


@router.get("/proyectos/mociones", summary="Mociones (proyectos de parlamentarios) ingresadas en un año")
async def mociones(anno: int | None = AnnoQuery):
    return await consultar(WS, "retornarMocionesXAnno", prmAnno=_anno(anno))


@router.get("/proyectos/ley/{numero_ley}", summary="Proyectos asociados a un número de ley")
async def proyectos_por_numero_ley(numero_ley: str = Path(..., examples=["21000"])):
    return await consultar_o_404(WS, "retornarProyectosLeyxNumeroLey", "Ley no encontrada", prmNumeroLey=numero_ley)


@router.get("/proyectos/{boletin}", summary="Detalle de un proyecto de ley por número de boletín")
async def proyecto(boletin: str = Path(..., examples=["11144-07"])):
    return await consultar_o_404(WS, "retornarProyectoLey", "Proyecto no encontrado", prmNumeroBoletin=boletin)


@router.get("/proyectos/{boletin}/votaciones", summary="Proyecto de ley con sus votaciones")
async def votaciones_proyecto(boletin: str = Path(..., examples=["11144-07"])):
    return await consultar_o_404(WS, "retornarVotacionesXProyectoLey", "Proyecto no encontrado", prmNumeroBoletin=boletin)


@router.get("/proyectos/{boletin}/documento", summary="Documento de ingreso del proyecto de ley")
async def documento_proyecto(boletin: str = Path(..., examples=["11144-07"])):
    return await consultar_o_404(WS, "retornarDocumentoIngresoProyectoLey", "Documento no encontrado", prmNumeroBoletin=boletin)


# --- Votaciones -------------------------------------------------------------

@router.get("/votaciones", summary="Votaciones de Sala de un año")
async def votaciones(anno: int | None = AnnoQuery):
    return await consultar(WS, "retornarVotacionesXAnno", prmAnno=_anno(anno))


@router.get("/votaciones/{votacion_id}", summary="Detalle de una votación (voto de cada diputado)")
async def votacion(votacion_id: int):
    return await consultar_o_404(WS, "retornarVotacionDetalle", "Votación no encontrada", prmVotacionId=votacion_id)


@router.get("/votaciones/{votacion_id}/distrito/{distrito}",
            summary="Cómo votaron los diputados de un distrito en una votación")
async def votacion_distrito(votacion_id: int, distrito: int = Path(..., ge=1, le=60), db: Session = Depends(get_db)):
    return await votos_del_distrito(db, votacion_id, distrito)


# --- Legislaturas y períodos --------------------------------------------------

@router.get("/legislaturas", summary="Todas las legislaturas")
async def legislaturas():
    return await consultar(WS, "retornarLegislaturas")


@router.get("/legislaturas/actual", summary="Legislatura actual")
async def legislatura_actual():
    return await consultar(WS, "retornarLegislaturaActual")


@router.get("/periodos", summary="Períodos legislativos")
async def periodos():
    return await consultar(WS, "retornarPeriodosLegislativos")


@router.get("/periodos/actual", summary="Período legislativo actual")
async def periodo_actual():
    return await consultar(WS, "retornarPeriodoLegislativoActual")


# --- Catálogos legislativos ----------------------------------------------------

@router.get("/materias", summary="Materias legislativas")
async def materias():
    return await consultar(WS, "retornarMaterias")


@router.get("/tramites/constitucionales", summary="Tipos de trámite constitucional")
async def tramites_constitucionales():
    return await consultar(WS, "retornarTramitesConstitucionales")


@router.get("/tramites/reglamentarios", summary="Tipos de trámite reglamentario")
async def tramites_reglamentarios():
    return await consultar(WS, "retornarTramitesReglamentarios")
