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
        </Diputado><Distrito><Numero>10</Numero></Distrito></DiputadoPeriodo>
      <DiputadoPeriodo><FechaInicio>2022-03-11T00:00:00</FechaInicio><FechaTermino xsi:nil="true"/>
        <Diputado><Id>1010</Id><Nombre>Luis</Nombre><ApellidoPaterno>Muñoz</ApellidoPaterno><Militancias/></Diputado>
        <Distrito><Numero>10</Numero></Distrito></DiputadoPeriodo>
      <DiputadoPeriodo><FechaInicio>2022-03-11T00:00:00</FechaInicio><FechaTermino xsi:nil="true"/>
        <Diputado><Id>1011</Id><Nombre>Rosa</Nombre><ApellidoPaterno>Vera</ApellidoPaterno><Militancias/></Diputado>
        <Distrito><Numero>8</Numero></Distrito></DiputadoPeriodo></DiputadosPeriodoColeccion>""",
    "retornarDistritos": f"""<DistritosColeccion {NS}>
      <Distrito><Numero>10</Numero><Comunas><Comuna><Numero>13120</Numero><Nombre>Ñuñoa</Nombre></Comuna></Comunas></Distrito>
      <Distrito><Numero>8</Numero><Comunas><Comuna><Numero>13101</Numero><Nombre>Maipú</Nombre></Comuna>
        <Comuna><Numero>13102</Numero><Nombre>Cerrillos</Nombre></Comuna></Comunas></Distrito></DistritosColeccion>""",
    "retornarVotacionDetalle": f"""<Votacion {NS}><Id>40000</Id><Descripcion>Boletín N° 1-07</Descripcion>
      <Fecha>2026-11-04T13:30:14</Fecha><TotalSi>1</TotalSi><TotalNo>1</TotalNo><Resultado Valor="1">Aprobado</Resultado>
      <Votos><Voto><Diputado><Id>1009</Id></Diputado><OpcionVoto Valor="1">Afirmativo</OpcionVoto></Voto>
        <Voto><Diputado><Id>1011</Id></Diputado><OpcionVoto Valor="0">En Contra</OpcionVoto></Voto></Votos></Votacion>""",
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
    "Partido X (Chile)": {"title": "Partido X (Chile)", "type": "standard", "extract": "Partido político chileno."},
    "Partido X de Venezuela": {"title": "Partido X de Venezuela", "type": "standard", "extract": "Partido venezolano."},
    "Juan Rojas (futbolista)": {"title": "Juan Rojas (futbolista)", "type": "standard",
                                "extract": "Juan Rojas es un futbolista argentino."},
}
def _claim(valor, **extra):
    return {"mainsnak": {"datavalue": {"value": valor}}, "rank": "normal", **extra}


WIKIDATA = {
    "Q1": {"claims": {"P17": [_claim({"id": "Q298"})],
                      "P154": [_claim("Logo PX viejo.svg", qualifiers={"P582": []}), _claim("Logo PX 2020.svg")]}},
    "Q2": {"claims": {"P17": [_claim({"id": "Q717"})], "P154": [_claim("Logo venezolano.svg")]}},
}
LLAMADAS = []


def red_falsa(request: httpx.Request) -> httpx.Response:
    """Responde por Cámara, Senado y Wikipedia."""
    host, path = request.url.host, request.url.path
    if "senado" in host:
        return httpx.Response(200, content=SENADORES_XML.encode())
    if "wikidata" in host:
        ids = request.url.params["ids"].split("|")
        return httpx.Response(200, json={"entities": {q: WIKIDATA[q] for q in ids if q in WIKIDATA}})
    if "commons" in host:
        assert request.url.params["titles"] == "File:Logo PX 2020.svg"
        return httpx.Response(200, json={"query": {"pages": {"1": {"title": "File:Logo PX 2020.svg", "imageinfo": [{
            "url": "https://upload.wikimedia.org/px.svg", "thumburl": "https://upload.wikimedia.org/256px-px.png",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Logo_PX_2020.svg",
            "extmetadata": {"LicenseShortName": {"value": "Public domain"},
                            "Artist": {"value": "<a href='x'>Partido X</a>"}}}]}}}})
    if "wikipedia" in host:
        if request.url.params.get("prop") == "pageprops":
            qids = {"Partido X (Chile)": "Q1", "Partido X de Venezuela": "Q2"}
            pages = {str(i): {"title": t, "pageprops": {"wikibase_item": qids[t]}}
                     for i, t in enumerate(request.url.params["titles"].split("|")) if t in qids}
            return httpx.Response(200, json={"query": {"pages": pages}})
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
