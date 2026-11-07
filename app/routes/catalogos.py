"""WSComun: catálogos (comunas, distritos, regiones, partidos, tipos de votación, etc.)."""
import re

from fastapi import APIRouter, HTTPException

from app.camara.client import consultar
from app.distritos import comunas_con_distrito

WS = "WSComun"
OPERACIONES = [
    "retornarComunas", "retornarDistritos", "retornarMinisterios", "retornarPartidosPoliticos",
    "retornarProvincias", "retornarRegiones", "retornarTiposAsistencia", "retornarTiposCamaraOrigen",
    "retornarTiposEstado", "retornarTiposEstadoAcuerdosResoluciones", "retornarTiposEstadoSesionComision",
    "retornarTiposEstadoSesionSala", "retornarTiposIniciativaProyectoLey", "retornarTiposJustificacionesInasistencia",
    "retornarTiposLegislatura", "retornarTiposOpcionVoto", "retornarTiposQuorumVotacion",
    "retornarTiposResultadoVotacion", "retornarTiposSesionComision", "retornarTiposSesionSala", "retornarTiposSexo",
    "retornarTiposTitularAsistencia", "retornarTiposVotacion", "retornarTiposVotacionProyectoLey",
]


def _slug(operacion: str) -> str:
    """retornarTiposOpcionVoto → tipos-opcion-voto"""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", operacion.removeprefix("retornar")).lower()


CATALOGOS = {_slug(op): op for op in OPERACIONES}

router = APIRouter(prefix="/catalogos", tags=["Catálogos"])


@router.get("", summary="Lista de catálogos disponibles")
async def listar_catalogos():
    return sorted(CATALOGOS)


@router.get("/{nombre}", summary="Contenido de un catálogo (p.ej. comunas, distritos, partidos-politicos)",
            description="`comunas` incluye además el campo `Distrito` (sacado de `distritos`).")
async def catalogo(nombre: str):
    if nombre == "comunas":
        return await comunas_con_distrito()
    if nombre not in CATALOGOS:
        raise HTTPException(status_code=404, detail=f"Catálogo desconocido. Disponibles: {', '.join(sorted(CATALOGOS))}")
    return await consultar(WS, CATALOGOS[nombre])
