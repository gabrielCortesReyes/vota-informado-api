"""Datos de la Cámara (simulados) y usuarios.  Ejecutar: pytest -q"""
import httpx

from app.camara import client as camara
from app.camara.xml import xml_to_json
from conftest import LLAMADAS, RESPUESTAS


def test_xml_a_json():
    data = xml_to_json(RESPUESTAS["retornarDiputadosPeriodoActual"].encode())
    assert isinstance(data, list) and data[0]["FechaTermino"] is None
    dip = data[0]["Diputado"]
    assert dip["Id"] == 1009 and dip["Sexo"] == {"Valor": 0, "Nombre": "Femenino"}
    assert dip["Militancias"][0]["Partido"]["Alias"] == "PX"


def test_datos_camara(api):
    r = api.get("/diputados/actuales"); assert r.status_code == 200 and r.json()[0]["Diputado"]["Id"] == 1009
    r = api.get("/diputados/1009"); assert r.json()["Militancias"] is None
    r = api.get("/proyectos/11144-07"); p = r.json()
    assert p["Admisible"] is True and p["Autores"][0]["Diputado"]["Id"] == 1009 and p["Materias"] == []
    assert isinstance(p["Votaciones"], list)
    assert api.get("/proyectos/0-0").status_code == 404
    v = api.get("/votaciones", params={"anno": 2024}).json()
    assert v[0]["Votos"][0]["OpcionVoto"]["Nombre"] == "Afirmativo"
    assert ("retornarVotacionesXAnno", {"prmAnno": "2024"}) in LLAMADAS
    s = api.get("/sesiones/4000").json(); assert s["ListadoAsistencia"][0]["TipoAsistencia"]["Valor"] == 1
    assert api.get("/catalogos/comunas").json()[0]["Nombre"] == "Ñuñoa"
    assert "tipos-opcion-voto" in api.get("/catalogos").json()
    assert api.get("/catalogos/no-existe").status_code == 404
    assert api.get("/legislaturas/actual").json()["Numero"] == 373


def test_cache(api):
    camara.limpiar_cache(); LLAMADAS.clear()
    api.get("/catalogos/comunas"); api.get("/catalogos/comunas")
    assert len(LLAMADAS) == 1


def test_camara_caida(api, monkeypatch):
    async def falla(*a, **k): raise httpx.ConnectError("sin red")
    monkeypatch.setattr(camara._client, "get", falla)
    camara.limpiar_cache()
    assert api.get("/catalogos/regiones").status_code == 502


def test_usuarios(api):
    assert api.post("/auth/login", json={"username": "g@mail.com", "password": "mala"}).status_code == 401
    t = api.post("/auth/login", json={"username": "g@mail.com", "password": "123456"}).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert api.get("/user/me", headers=h).json()["names"] == "Gabriel"
    assert api.get("/user/me", headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401
    assert api.get("/user/me/preferences", headers=h).status_code == 404
    r = api.post("/user/me/preferences", headers=h, json={"commune_id": 13120})
    assert r.json()["commune_id"] == 13120
    assert api.get("/user/me/preferences", headers=h).json()["commune_id"] == 13120
    assert api.post("/auth/refresh-token", json={"refresh_token": t["refresh_token"]}).status_code == 200
    assert api.post("/auth/change-password", headers=h, json={"new_password": "nueva1234"}).status_code == 200
    assert api.post("/auth/login", json={"username": "g@mail.com", "password": "nueva1234"}).status_code == 200
    assert api.post("/auth/restore-password", json={"username": "g@mail.com"}).status_code == 503
    assert api.post("/cache/clear").status_code in (401, 403)
    assert api.post("/cache/clear", headers=h).status_code == 200
