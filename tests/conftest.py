"""Configuración común de pruebas: sin red, se simulan Cámara, Senado y Wikipedia."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["APP_SECRET_KEY"] = "test"

import httpx
import pytest
from fastapi.testclient import TestClient

from app.camara import client as camara
from app import sync
from app.camara.xml import xml_to_json
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import User, UserCredentials
from app.security import hash_password, limiter

NS = 'xmlns="http://opendata.camara.cl/camaradiputados/v1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'

RESPUESTAS = {
    "retornarDiputadosPeriodoActual": f"""<DiputadosPeriodoColeccion {NS}>
      <DiputadoPeriodo><FechaInicio>2022-03-11T00:00:00</FechaInicio><FechaTermino xsi:nil="true"/>
        <Diputado><Id>1009</Id><Nombre>Ana</Nombre><ApellidoPaterno>Pérez</ApellidoPaterno>
          <Sexo Valor="0">Femenino</Sexo>
          <Militancias><Militancia><FechaInicio>2020-01-01T00:00:00</FechaInicio>
            <Partido><Id>12</Id><Nombre>Partido X</Nombre><Alias>PX</Alias></Partido></Militancia></Militancias>
        </Diputado></DiputadoPeriodo></DiputadosPeriodoColeccion>""",
    "retornarDiputado": f"""<Diputado {NS}><Id>1009</Id><Nombre>Ana</Nombre><RUT>12345678</RUT><Militancias xsi:nil="true"/></Diputado>""",
    "retornarProyectoLey": f"""<ProyectoLey {NS}><Id>1</Id><NumeroBoletin>11144-07</NumeroBoletin><Nombre>Proyecto</Nombre>
      <Admisible>true</Admisible><Autores><ParlamentarioAutor><Orden>1</Orden><Diputado><Id>1009</Id></Diputado></ParlamentarioAutor></Autores>
      <Votaciones><VotacionProyectoLey><Articulo>Único</Articulo></VotacionProyectoLey></Votaciones><Materias/></ProyectoLey>""",
    "retornarVotacionesXAnno": f"""<VotacionesColeccion {NS}><Votacion><Id>40000</Id><TotalSi>100</TotalSi>
      <Votos><Voto><Diputado><Id>1009</Id></Diputado><OpcionVoto Valor="1">Afirmativo</OpcionVoto></Voto></Votos></Votacion></VotacionesColeccion>""",
    "retornarSesionAsistencia": f"""<SesionSala {NS}><Id>4000</Id><Numero>10</Numero>
      <ListadoAsistencia><Asistencia><TipoAsistencia Valor="1">Asiste</TipoAsistencia><Diputado><Id>1009</Id></Diputado></Asistencia></ListadoAsistencia>
      </SesionSala>""",
    "retornarComunas": f"""<ComunasColeccion {NS}><Comuna><Numero>13120</Numero><Nombre>Ñuñoa</Nombre></Comuna></ComunasColeccion>""",
    "retornarLegislaturaActual": f"""<Legislatura {NS}><Id>55</Id><Numero>373</Numero></Legislatura>""",
    "retornarPartidosPoliticos": f"""<PartidosColeccion {NS}><Partido><Id>12</Id><Nombre>Partido X</Nombre><Alias>PX</Alias></Partido>
      <Partido><Id>13</Id><Nombre>Partido Y</Nombre><Alias>PY</Alias></Partido></PartidosColeccion>""",
}

SENADORES_XML = """<?xml version="1.0" encoding="UTF-8"?><senadores>
  <senador><PARLID>905</PARLID><PARLAPELLIDOPATERNO>Rojas</PARLAPELLIDOPATERNO><PARLAPELLIDOMATERNO>Soto</PARLAPELLIDOMATERNO>
    <PARLNOMBRE>Juan</PARLNOMBRE><REGION>Región de Valparaíso</REGION><CIRCUNSCRIPCION>6</CIRCUNSCRIPCION>
    <PARTIDO>Partido Y</PARTIDO><FONO>123</FONO><EMAIL>jrojas@senado.cl</EMAIL></senador>
</senadores>"""

WIKI = {
    "Ana Pérez": {"title": "Ana Pérez", "type": "standard",
                  "extract": "Ana Pérez es una abogada y política chilena, diputada desde 2022.",
                  "content_urls": {"desktop": {"page": "https://es.wikipedia.org/wiki/Ana_P%C3%A9rez"}},
                  "thumbnail": {"source": "https://upload.wikimedia.org/ana.jpg"}},
    "Juan Rojas (futbolista)": {"title": "Juan Rojas (futbolista)", "type": "standard",
                                "extract": "Juan Rojas es un futbolista argentino."},
}
LLAMADAS = []


def red_falsa(request: httpx.Request) -> httpx.Response:
    """Responde por Cámara, Senado y Wikipedia."""
    host, path = request.url.host, request.url.path
    if "senado" in host:
        return httpx.Response(200, content=SENADORES_XML.encode())
    if "wikipedia" in host:
        if path.endswith("api.php"):
            q = request.url.params["srsearch"]
            titulos = [t for t in WIKI if t.split(" ")[0] in q]
            return httpx.Response(200, json={"query": {"search": [{"title": t} for t in titulos]}})
        titulo = path.rsplit("/", 1)[-1].replace("_", " ")
        return httpx.Response(200, json=WIKI[titulo]) if titulo in WIKI else httpx.Response(404)
    return camara_falsa(request)


def camara_falsa(request: httpx.Request) -> httpx.Response:
    op = request.url.path.rsplit("/", 1)[-1]
    LLAMADAS.append((op, dict(request.url.params)))
    if op == "retornarProyectoLey" and request.url.params.get("prmNumeroBoletin") == "0-0":
        return httpx.Response(200, content=f"<ProyectoLey {NS}/>")
    body = RESPUESTAS.get(op, f"<Coleccion {NS}/>")
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/xml"})


@pytest.fixture(scope="session")
def api():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    u = User(fiscal_id="11111111-1", names="Gabriel", last_names="Cortés", enabled=True)
    db.add(u); db.flush()
    db.add(UserCredentials(user_id=u.id, username="g@mail.com", hashed_password=hash_password("123456")))
    db.commit(); db.close()
    with TestClient(app) as c:
        camara._client = httpx.AsyncClient(transport=httpx.MockTransport(camara_falsa))
        sync._cliente = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(red_falsa), **kw)
        camara.limpiar_cache()
        yield c
    os.remove("test.db")




def login(api, username="g@mail.com", password="123456") -> dict:
    limiter.reset()  # el límite de 5 logins/minuto se prueba aparte
    r = api.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
