"""(Corre después de test_participacion, que carga el padrón.) Asistencia a la Sala: carga desde WSSala, % por diputado, inasistencias y justificaciones."""
import asyncio
from datetime import date, timedelta

import httpx

from app import asistencia
from app.camara import client as camara
from app.database import SessionLocal
from app.models import Parliamentarian, SalaAttendance, SalaSession
from conftest import NS, camara_falsa

HOY = date.today()
RECIENTE = (HOY - timedelta(days=3)).isoformat()

SESIONES = f"""<SesionesColeccion {NS}>
  <Sesion><Id>4736</Id><Numero>105</Numero><FechaInicio>2026-01-05T17:00:00</FechaInicio>
    <Tipo Valor="1">Ordinaria</Tipo><Estado Valor="1">Celebrada</Estado></Sesion>
  <Sesion><Id>4800</Id><Numero>40</Numero><FechaInicio>2026-06-02T10:00:00</FechaInicio>
    <FechaTermino>2026-06-02T13:00:00</FechaTermino><Tipo Valor="1">Ordinaria</Tipo><Estado Valor="1">Celebrada</Estado></Sesion>
  <Sesion><Id>4801</Id><Numero>41</Numero><FechaInicio>2026-06-03T10:00:00</FechaInicio>
    <Tipo Valor="2">Especial</Tipo><Estado Valor="1">Celebrada</Estado></Sesion>
  <Sesion><Id>4802</Id><Numero>42</Numero><FechaInicio>{RECIENTE}T10:00:00</FechaInicio>
    <Tipo Valor="1">Ordinaria</Tipo><Estado Valor="1">Celebrada</Estado></Sesion>
  <Sesion><Id>4803</Id><Numero>43</Numero><FechaInicio>{(HOY + timedelta(days=2)).isoformat()}T10:00:00</FechaInicio>
    <Tipo Valor="1">Ordinaria</Tipo><Estado Valor="0">Citada</Estado></Sesion>
</SesionesColeccion>"""


def _asiste(dip):
    return f'<Asistencia><TipoAsistencia Valor="1">Asiste</TipoAsistencia><Diputado><Id>{dip}</Id></Diputado></Asistencia>'


def _no_asiste(dip, justificacion=""):
    return (f'<Asistencia><TipoAsistencia Valor="0">No Asiste</TipoAsistencia>{justificacion}'
            f'<Diputado><Id>{dip}</Id></Diputado></Asistencia>')


LICENCIA = ('<Justificacion Valor="19"><Nombre>Licencia médica (Art. 42)</Nombre>'
            '<RebajaAsistencia>true</RebajaAsistencia><RebajaQuorum>false</RebajaQuorum></Justificacion>')

ASISTENCIA = {
    4800: _asiste(1009) + _asiste(1010),
    4801: _no_asiste(1009, LICENCIA) + _asiste(1010),
    4802: _no_asiste(1009) + _asiste(1010),  # aún sin justificación
}
PEDIDAS = []


def _red(request: httpx.Request) -> httpx.Response:
    op = request.url.path.rsplit("/", 1)[-1]
    if op == "retornarSesionesXAnno":
        body = SESIONES if request.url.params["prmAnno"] == "2026" else f"<SesionesColeccion {NS}/>"
    elif op == "retornarSesionAsistencia":
        sid = int(request.url.params["prmSesionId"])
        PEDIDAS.append(sid)
        body = f"<SesionSala {NS}><Id>{sid}</Id><ListadoAsistencia>{ASISTENCIA.get(sid, '')}</ListadoAsistencia></SesionSala>"
    elif op == "retornarVotacionDetalle" and request.url.params["prmVotacionId"] == "50000":
        body = f"<Votacion {NS}><Id>50000</Id><Fecha>2026-06-03T11:30:00</Fecha><Votos/></Votacion>"
    else:
        return camara_falsa(request)
    return httpx.Response(200, content=body.encode())


def _sincronizar(**kw):
    camara.limpiar_cache()
    db = SessionLocal()
    try:
        return asyncio.run(asistencia.sincronizar_asistencia(db, desde="2026-03-11", **kw))
    finally:
        db.close()


def _id(api, external_id):
    db = SessionLocal()
    try:
        return db.query(Parliamentarian).filter_by(chamber="diputado", external_id=external_id).one().id
    finally:
        db.close()


def test_asistencia(api):
    anterior = camara._client
    camara._client = httpx.AsyncClient(transport=httpx.MockTransport(_red))
    try:
        r = _sincronizar()
        # la de enero (período anterior) y la citada no se cuentan
        assert r == {"sesiones": 3, "cargadas": 3, "sin_datos": 0, "errores": 0}

        ana = _id(api, 1009)
        d = api.get(f"/parlamentarios/{ana}/asistencia").json()
        assert (d["sesiones"], d["asistencias"], d["inasistencias"]) == (3, 1, 2)
        assert (d["justificadas"], d["sin_justificar"], d["porcentaje"]) == (1, 1, 33.3)
        ultima, licencia = d["inasistencias_detalle"]  # de la más reciente a la más antigua
        assert ultima["sesion_id"] == 4802 and ultima["justificada"] is False and ultima["justificacion"] is None
        assert licencia["justificacion"] == "Licencia médica (Art. 42)" and licencia["rebaja_asistencia"] is True
        assert licencia["tipo_sesion"] == "Especial" and "prmSesionId=4801" in licencia["fuente_url"]
        assert {c["justificacion"] for c in d["por_justificacion"]} == {"Licencia médica (Art. 42)", "Sin justificación"}

        # filtro por fecha
        d = api.get(f"/parlamentarios/{ana}/asistencia", params={"desde": "2026-06-03", "hasta": "2026-06-03"}).json()
        assert (d["sesiones"], d["porcentaje"]) == (1, 0.0)

        # la justificación en el momento de una votación (sesión 4801, del mismo día)
        v = api.get("/votaciones/50000/asistencia").json()
        assert v["sesion"]["id"] == 4801
        por_dip = {f["diputado_id"]: f for f in v["asistencia"]}
        assert por_dip[1009] == {"diputado_id": 1009, "asistio": False, "justificacion": "Licencia médica (Art. 42)"}
        assert por_dip[1010]["asistio"] is True
        v = api.get("/votaciones/40000/asistencia").json()  # sin sesión cargada ese día
        assert v["sesion"] is None and v["asistencia"] == []

        # la ficha trae el resumen
        assert api.get(f"/parlamentarios/{ana}").json()["asistencia"]["porcentaje"] == 33.3

        ranking = api.get("/asistencia/diputados").json()
        assert [f["parlamentario"]["external_id"] for f in ranking] == [1010, 1009]
        assert ranking[0]["porcentaje"] == 100.0
        assert api.get("/asistencia/diputados", params={"orden": "asc"}).json()[0]["parlamentario"]["external_id"] == 1009

        # la Cámara justifica después: solo se vuelven a pedir las sesiones recientes
        ASISTENCIA[4802] = _no_asiste(1009, LICENCIA) + _asiste(1010)
        PEDIDAS.clear()
        r = _sincronizar(recargar_dias=30)
        assert PEDIDAS == [4802] and r["cargadas"] == 1
        d = api.get(f"/parlamentarios/{ana}/asistencia").json()
        assert (d["justificadas"], d["sin_justificar"]) == (2, 0)
        db = SessionLocal()
        try:
            assert db.query(SalaAttendance).count() == 6 and db.query(SalaSession).count() == 3
        finally:
            db.close()

        # senadores: no hay asistencia de la Cámara
        juan = api.get("/parlamentarios", params={"camara": "senador"}).json()[0]["id"]
        assert api.get(f"/parlamentarios/{juan}/asistencia").status_code == 404
    finally:
        camara._client = anterior
        camara.limpiar_cache()
