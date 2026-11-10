"""(Corre después de test_participacion, que carga el padrón.) Materias de proyectos: carga, categorías,
intereses del usuario y materias por diputado."""
import asyncio

import httpx

from app import proyectos
from app.camara import client as camara
from app.database import SessionLocal
from app.models import Parliamentarian, ProyectoLey
from app.models import UserMateria
from app.security import limiter
from conftest import NS, camara_falsa

MOCIONES = f"""<ProyectosLeyColeccion {NS}>
  <ProyectoLey><Id>501</Id><NumeroBoletin>18001-04</NumeroBoletin><Nombre>Mejora la educación</Nombre>
    <FechaIngreso>2026-04-01T00:00:00</FechaIngreso><TipoIniciativa Valor="2">Moción</TipoIniciativa>
    <CamaraOrigen Valor="1">Cámara de Diputados</CamaraOrigen></ProyectoLey>
  <ProyectoLey><Id>502</Id><NumeroBoletin>18002-11</NumeroBoletin><Nombre>Salud y escuelas</Nombre>
    <FechaIngreso>2026-05-01T00:00:00</FechaIngreso><TipoIniciativa Valor="2">Moción</TipoIniciativa></ProyectoLey>
  <ProyectoLey><Id>503</Id><NumeroBoletin>18003-07</NumeroBoletin><Nombre>Sin materia</Nombre>
    <FechaIngreso>2026-06-01T00:00:00</FechaIngreso><TipoIniciativa Valor="2">Moción</TipoIniciativa></ProyectoLey>
</ProyectosLeyColeccion>"""

MATERIAS = f"""<MateriasColeccion {NS}><Materia><Id>10</Id><Nombre>EDUCACION</Nombre></Materia>
  <Materia><Id>20</Id><Nombre>SALUD</Nombre></Materia><Materia><Id>30</Id><Nombre>PESCA</Nombre></Materia></MateriasColeccion>"""


def _autor(dip, orden=1):
    return f"<ParlamentarioAutor><Orden>{orden}</Orden><Diputado><Id>{dip}</Id></Diputado></ParlamentarioAutor>"


DETALLES = {
    "18001-04": f"<Autores>{_autor(1009)}</Autores><Materias><Materia><Id>10</Id><Nombre>EDUCACION</Nombre></Materia></Materias>",
    "18002-11": (f"<Autores>{_autor(1009)}{_autor(1010, 2)}</Autores><Materias><Materia><Id>10</Id><Nombre>EDUCACION</Nombre>"
                 "</Materia><Materia><Id>20</Id><Nombre>SALUD</Nombre></Materia></Materias>"),
    "18003-07": f"<Autores>{_autor(1010)}</Autores><Materias/>",
}


def _red(request: httpx.Request) -> httpx.Response:
    op = request.url.path.rsplit("/", 1)[-1]
    if op == "retornarMocionesXAnno":
        body = MOCIONES if request.url.params["prmAnno"] == "2026" else f"<ProyectosLeyColeccion {NS}/>"
    elif op == "retornarMaterias":
        body = MATERIAS
    elif op == "retornarProyectoLey":
        b = request.url.params["prmNumeroBoletin"]
        body = f"<ProyectoLey {NS}><Id>0</Id><NumeroBoletin>{b}</NumeroBoletin>{DETALLES[b]}</ProyectoLey>"
    else:
        return camara_falsa(request)
    return httpx.Response(200, content=body.encode())


def _sincronizar(**kw):
    anterior = camara._client
    camara._client = httpx.AsyncClient(transport=httpx.MockTransport(_red))
    camara.limpiar_cache()
    db = SessionLocal()
    try:
        return asyncio.run(proyectos.sincronizar_proyectos(db, desde_anno=2026, **kw))
    finally:
        db.close()
        camara._client = anterior
        camara.limpiar_cache()


def test_materias(api):
    r = _sincronizar(limite=2)  # de a poco: quedan pendientes para la próxima corrida
    assert r["proyectos"] == 3 and r["detalles"] == 2 and r["pendientes"] == 1 and r["materias"] == 3
    r = _sincronizar()
    assert r["detalles"] == 1 and r["pendientes"] == 0
    db = SessionLocal()
    assert db.get(ProyectoLey, 502).tipo == "Moción" and len(db.get(ProyectoLey, 502).autores) == 2
    ana = db.query(Parliamentarian).filter_by(chamber="diputado", external_id=1009).one().id
    luis = db.query(Parliamentarian).filter_by(chamber="diputado", external_id=1010).one().id
    db.close()

    cats = api.get("/materias/categorias").json()
    assert cats == [{"id": 10, "nombre": "EDUCACION", "proyectos": 2}, {"id": 20, "nombre": "SALUD", "proyectos": 1}]
    l = api.get("/materias/10/proyectos").json()
    assert [p["NumeroBoletin"] for p in l] == ["18002-11", "18001-04"]
    assert l[0]["Materias"] == [{"Id": 10, "Nombre": "EDUCACION"}, {"Id": 20, "Nombre": "SALUD"}]
    assert l[0]["TipoIniciativa"] == {"Nombre": "Moción"}
    assert api.get("/materias/999/proyectos").status_code == 404

    m = api.get(f"/parlamentarios/{ana}/materias").json()
    assert m["mociones"] == 2 and m["con_materia"] == 2
    assert m["materias"] == [{"id": 10, "nombre": "EDUCACION", "proyectos": 2}, {"id": 20, "nombre": "SALUD", "proyectos": 1}]
    m = api.get(f"/parlamentarios/{luis}/materias").json()
    assert m["mociones"] == 2 and m["con_materia"] == 1 and [x["id"] for x in m["materias"]] == [10, 20]
    assert api.get(f"/parlamentarios/{luis}/materias", params={"desde": "2026-06-01"}).json()["mociones"] == 1
    assert [p["Id"] for p in api.get(f"/parlamentarios/{luis}/mociones").json()] == [503, 502]
    assert [p["Id"] for p in api.get(f"/parlamentarios/{luis}/mociones", params={"materia": 20}).json()] == [502]

    limiter.reset()
    r = api.post("/auth/register", json={"fiscal_id": "22222222-2", "names": "Materias", "last_names": "Prueba",
                                         "email": "materias@mail.com", "password": "clave-segura-1"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert api.get("/user/me/materias", headers=h).json() == []
    assert api.get("/user/me/materias/proyectos", headers=h).json() == []
    assert api.put("/user/me/materias", headers=h, json={"materia_ids": [20, 999]}).status_code == 422
    r = api.put("/user/me/materias", headers=h, json={"materia_ids": [20, 30]})
    assert [x["nombre"] for x in r.json()] == ["PESCA", "SALUD"]
    assert [p["Id"] for p in api.get("/user/me/materias/proyectos", headers=h).json()] == [502]
    assert api.put("/user/me/materias", headers=h, json={"materia_ids": [10]}).json()[0]["id"] == 10
    # al eliminar la cuenta se borran sus materias
    assert api.request("DELETE", "/user/me", headers=h, json={"password": "clave-segura-1"}).status_code == 204
    db = SessionLocal()
    assert db.query(UserMateria).count() == 0
    db.close()
    assert api.get("/user/me/materias").status_code in (401, 403)
