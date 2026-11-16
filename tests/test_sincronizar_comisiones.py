"""(Corre después de test_participacion, que carga el padrón.) Comisiones: integrantes con otro Id que las
votaciones, identificados por apellidos; presidente; sesiones; y comisiones de un diputado."""
import asyncio

import httpx

from app.camara import client as camara
from app.comisiones import sincronizar_comisiones
from app.database import SessionLocal
from app.models import Comision, Parliamentarian
from conftest import NS, camara_falsa


def _dip(id_, nombre, paterno, materno=""):
    return (f"<Diputado><Id>{id_}</Id><Nombre>{nombre}</Nombre><ApellidoPaterno>{paterno}</ApellidoPaterno>"
            f"<ApellidoMaterno>{materno}</ApellidoMaterno></Diputado>")


VIGENTES = f"""<ComisionesColeccion {NS}>
  <Comision><Id>4900</Id><Nombre>Agricultura</Nombre><Tipo Valor="1">Permanente</Tipo>
    <Presidente><Id>1489</Id><Nombre>ANA</Nombre><ApellidoPaterno>PÉREZ</ApellidoPaterno>
      <ApellidoMaterno>GÓMEZ</ApellidoMaterno></Presidente></Comision>
  <Comision><Id>4950</Id><Nombre>Investigadora Isapres</Nombre><Tipo Valor="3">Especial Investigadora</Tipo></Comision>
</ComisionesColeccion>"""

COMISION = {
    "4900": f"""<Comision {NS}><Id>4900</Id><Nombre>Agricultura, Silvicultura y Desarrollo Rural</Nombre><Alias>Agricultura</Alias>
      <Tipo Valor="1">Permanente</Tipo><FechaInicio>2026-03-11T00:00:00</FechaInicio><Correo>agricam@congreso.cl</Correo>
      <Telefono>+56 32 250 5289</Telefono><Numero>8</Numero><Integrantes>
        <DiputadoIntegrante><FechaInicio>2026-03-17T14:08:16</FechaInicio><FechaTermino>2030-03-10T23:59:59</FechaTermino>
          {_dip(1489, 'ANA', 'PÉREZ', 'GÓMEZ')}</DiputadoIntegrante>
        <DiputadoIntegrante><FechaInicio>2026-03-17T14:08:16</FechaInicio><FechaTermino>2030-03-10T23:59:59</FechaTermino>
          {_dip(1500, 'LUIS', 'MUÑOZ')}</DiputadoIntegrante>
        <DiputadoIntegrante><FechaInicio>2026-03-17T14:08:16</FechaInicio><FechaTermino>2026-04-01T00:00:00</FechaTermino>
          {_dip(1501, 'ROSA', 'VERA')}</DiputadoIntegrante>
        <DiputadoIntegrante><FechaInicio>2026-03-17T14:08:16</FechaInicio>{_dip(1999, 'NADIE', 'DESCONOCIDO', 'X')}</DiputadoIntegrante>
      </Integrantes></Comision>""",
    "4950": f"""<Comision {NS}><Id>4950</Id><Nombre>Investigadora Isapres</Nombre><Tipo Valor="3">Especial Investigadora</Tipo>
      <Integrantes><DiputadoIntegrante>{_dip(1500, 'Luis', 'Muñoz')}</DiputadoIntegrante></Integrantes></Comision>""",
}
SESIONES = f"""<Comision {NS}><Id>4900</Id><Sesiones>
  <SesionComision><Id>81685</Id><Numero>9</Numero><Tipo Valor="1">Ordinaria</Tipo><FechaInicio>2026-06-09T15:00:00</FechaInicio>
    <FechaTermino>2026-06-09T17:00:00</FechaTermino><Estado Valor="2">En Comité</Estado><Asistencia/></SesionComision>
  <SesionComision><Id>81935</Id><Numero>14</Numero><Tipo Valor="1">Ordinaria</Tipo><FechaInicio>2026-08-04T15:00:00</FechaInicio>
    <Estado Valor="2">En Comité</Estado><Asistencia/></SesionComision>
</Sesiones></Comision>"""


def _red(request: httpx.Request) -> httpx.Response:
    op = request.url.path.rsplit("/", 1)[-1]
    if op == "retornarComisionesVigentes":
        body = VIGENTES
    elif op == "retornarComision":
        body = COMISION[request.url.params["prmComisionId"]]
    elif op == "retornarSesionesXComisionYAnno":
        body = SESIONES if request.url.params["prmComisionId"] == "4900" else f"<Comision {NS}><Sesiones/></Comision>"
    else:
        return camara_falsa(request)
    return httpx.Response(200, content=body.encode())


def _id(external_id):
    db = SessionLocal()
    try:
        return db.query(Parliamentarian).filter_by(chamber="diputado", external_id=external_id).one().id
    finally:
        db.close()


def test_comisiones(api):
    anterior = camara._client
    camara._client = httpx.AsyncClient(transport=httpx.MockTransport(_red))
    camara.limpiar_cache()
    db = SessionLocal()
    try:
        r = asyncio.run(sincronizar_comisiones(db, anno=2026))
        assert r == {"comisiones": 2, "integrantes": 3, "sin_identificar": 1, "sesiones": 2, "errores": 0}
        # se puede volver a cargar sin duplicar
        camara.limpiar_cache()
        assert asyncio.run(sincronizar_comisiones(db, anno=2026))["integrantes"] == 3
        assert db.get(Comision, 4900).presidente_id == 1009  # 1489 en WSComision = 1009 en las votaciones
    finally:
        db.close()
        camara._client = anterior
        camara.limpiar_cache()

    lista = api.get("/comisiones", params={"anno": 2026}).json()
    agri = next(c for c in lista if c["id"] == 4900)
    assert agri["presidente"]["external_id"] == 1009 and agri["integrantes"] == 2 and agri["sesiones_anno"] == 2
    assert agri["fuente_url"].endswith("prmID=4900") and agri["correo"] == "agricam@congreso.cl"
    assert api.get("/comisiones", params={"tipo": "especial investigadora"}).json()[0]["id"] == 4950

    d = api.get("/comisiones/4900", params={"anno": 2026}).json()
    assert [m["parlamentario"]["external_id"] for m in d["miembros"]] == [1009, 1010]  # presidente primero
    assert d["miembros"][0]["es_presidente"] is True
    assert [s["id"] for s in d["sesiones"]] == [81935, 81685]  # Rosa Vera ya salió y no aparece
    assert api.get("/comisiones/1").status_code == 404

    luis = api.get(f"/parlamentarios/{_id(1010)}/comisiones").json()
    assert {c["id"] for c in luis} == {4900, 4950} and not any(c["es_presidente"] for c in luis)
    ana = api.get(f"/parlamentarios/{_id(1009)}/comisiones").json()
    assert ana == [{"id": 4900, "nombre": "Agricultura, Silvicultura y Desarrollo Rural", "alias": "Agricultura",
                    "tipo": "Permanente", "es_presidente": True, "desde": "2026-03-17T14:08:16"}]
    juan = api.get("/parlamentarios", params={"camara": "senador"}).json()[0]["id"]
    assert api.get(f"/parlamentarios/{juan}/comisiones").json() == []


def test_comisiones_respuesta_vacia(api):
    """Si la Cámara responde sin comisiones, no se borra ni se da de baja nada."""
    anterior = camara._client
    camara._client = httpx.AsyncClient(transport=httpx.MockTransport(camara_falsa))
    camara.limpiar_cache()
    db = SessionLocal()
    try:
        assert asyncio.run(sincronizar_comisiones(db))["comisiones"] == 0
        assert db.get(Comision, 4900).vigente is True
    finally:
        db.close()
        camara._client = anterior
        camara.limpiar_cache()
