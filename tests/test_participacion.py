"""Registro, padrón (Cámara + Senado + Wikipedia), comentarios, calificaciones y encuestas."""
import asyncio

from app import sync
from app.database import SessionLocal
from app.models import Parliamentarian
from app.rut import normalizar_rut
from app.security import limiter
from conftest import login


def registrar(api, rut, email, password="clave-segura-1"):
    limiter.reset()
    r = api.post("/auth/register", json={"fiscal_id": rut, "names": "María José", "last_names": "Soto Díaz",
                                         "email": email, "password": password})
    return r


def test_rut():
    assert normalizar_rut("12.345.678-5") == "12345678-5"
    assert normalizar_rut("12345678-0") is None
    assert normalizar_rut("7.654.321-6") == "7654321-6"
    assert normalizar_rut("10.000.013-K") == "10000013-K"
    assert normalizar_rut("abc") is None


def test_registro(api):
    r = registrar(api, "12.345.678-5", "Maria@Mail.com")
    assert r.status_code == 201 and "access_token" in r.json()
    assert registrar(api, "12345678-5", "otra@mail.com").status_code == 409       # RUT repetido
    assert registrar(api, "7654321-6", "maria@mail.com").status_code == 409        # correo repetido
    assert registrar(api, "12345678-0", "x@mail.com").status_code == 422          # RUT inválido
    assert api.post("/auth/register", json={"fiscal_id": "7654321-6", "names": "A", "last_names": "B",
                                            "email": "corta@mail.com", "password": "123"}).status_code == 422
    h = login(api, "maria@mail.com", "clave-segura-1")  # login no distingue mayúsculas en el correo
    me = api.get("/user/me", headers=h).json()
    assert me["fiscal_id"] == "12345678-5" and me["is_admin"] is False


def test_sincronizacion(api):
    db = SessionLocal()
    try:
        resultado = asyncio.run(sync.sincronizar_todo(db))
    finally:
        db.close()
    assert resultado["partidos"] == 2 and resultado["diputados"] == 1 and resultado["senadores"] == 1
    assert resultado["wikipedia"] == {"revisados": 2, "encontrados": 1}

    partidos = {p["alias"]: p for p in api.get("/partidos").json()}
    assert partidos["PX"]["diputados"] == 1 and partidos["PY"]["senadores"] == 1

    todos = api.get("/parlamentarios").json()
    assert {p["chamber"] for p in todos} == {"diputado", "senador"}
    ana = api.get("/parlamentarios", params={"q": "pérez"}).json()[0]
    ficha = api.get(f"/parlamentarios/{ana['id']}").json()
    assert ficha["party"]["alias"] == "PX" and "diputada" in ficha["description"]
    assert ficha["wikipedia_url"].startswith("https://es.wikipedia.org") and ficha["licencia_descripcion"]
    juan = api.get("/parlamentarios", params={"camara": "senador"}).json()[0]
    ficha = api.get(f"/parlamentarios/{juan['id']}").json()
    assert ficha["district"] == "6" and ficha["party"]["name"] == "Partido Y"
    assert ficha["description"] is None  # el artículo del futbolista se descartó

    # sincronizar de nuevo no duplica
    db = SessionLocal()
    try:
        asyncio.run(sync.sincronizar_todo(db, wikipedia=False))
        assert db.query(Parliamentarian).count() == 2
    finally:
        db.close()


def _ana(api):
    return api.get("/parlamentarios", params={"q": "Ana"}).json()[0]["id"]


def test_comentarios_y_calificaciones(api):
    pid = _ana(api)
    h1 = login(api, "maria@mail.com", "clave-segura-1")
    registrar(api, "7654321-6", "pedro@mail.com")
    h2 = login(api, "pedro@mail.com", "clave-segura-1")

    assert api.post(f"/parlamentarios/{pid}/comentarios", json={"text": "hola"}).status_code in (401, 403)
    c = api.post(f"/parlamentarios/{pid}/comentarios", headers=h1, json={"text": "Buen trabajo en la comisión"}).json()
    assert c["autor"] == "María S." and c["es_mio"]
    api.post(f"/parlamentarios/{pid}/comentarios", headers=h2, json={"text": "No estoy de acuerdo"})
    lista = api.get(f"/parlamentarios/{pid}/comentarios", headers=h2).json()
    assert len(lista) == 2 and lista[0]["es_mio"] and not lista[1]["es_mio"]
    assert api.put(f"/comentarios/{c['id']}", headers=h2, json={"text": "hackeado"}).status_code == 403
    assert api.put(f"/comentarios/{c['id']}", headers=h1, json={"text": "Editado"}).json()["text"] == "Editado"
    assert api.delete(f"/comentarios/{c['id']}", headers=h1).status_code == 204
    assert len(api.get(f"/parlamentarios/{pid}/comentarios").json()) == 1
    assert api.post("/parlamentarios/9999/comentarios", headers=h1, json={"text": "x"}).status_code == 404

    assert api.put(f"/parlamentarios/{pid}/calificacion", headers=h1, json={"score": 6}).status_code == 422
    api.put(f"/parlamentarios/{pid}/calificacion", headers=h1, json={"score": 2})
    api.put(f"/parlamentarios/{pid}/calificacion", headers=h1, json={"score": 5})  # reemplaza
    r = api.put(f"/parlamentarios/{pid}/calificacion", headers=h2, json={"score": 4}).json()
    assert r["resumen"]["cantidad"] == 2 and r["resumen"]["promedio"] == 4.5
    assert api.get(f"/parlamentarios/{pid}/calificacion/mia", headers=h1).json()["score"] == 5
    ficha = api.get(f"/parlamentarios/{pid}").json()
    assert ficha["calificacion"]["promedio"] == 4.5 and ficha["comentarios"] == 1


def test_encuestas(api):
    pid = _ana(api)
    h = login(api, "maria@mail.com", "clave-segura-1")
    encuesta = api.get("/encuestas").json()[0]
    assert encuesta["title"] == "Evaluación de tu parlamentario"
    q = {x["text"]: x["id"] for x in encuesta["questions"]}
    url = f"/encuestas/{encuesta['id']}/respuestas"

    assert api.post(url, headers=h, json={"answers": [{"question_id": q["¿Votaste por él/ella?"], "value": "si"}]}).status_code == 422  # falta parlamentario
    faltan = api.post(url, headers=h, json={"parliamentarian_id": pid,
                                            "answers": [{"question_id": q["¿Votaste por él/ella?"], "value": "sí"}]})
    assert faltan.status_code == 422 and "Volverías" in faltan.text
    assert api.post(url, headers=h, json={"parliamentarian_id": pid,
                                          "answers": [{"question_id": q["¿Votaste por él/ella?"], "value": "quizás"}]}).status_code == 422
    ok = api.post(url, headers=h, json={"parliamentarian_id": pid, "answers": [
        {"question_id": q["¿Votaste por él/ella?"], "value": "Sí"},
        {"question_id": q["¿Volverías a votar por él/ella?"], "value": "no"}]})
    assert ok.status_code == 200, ok.text
    mias = api.get(f"/encuestas/{encuesta['id']}/mis-respuestas", headers=h, params={"parliamentarian_id": pid}).json()
    assert mias[str(q["¿Votaste por él/ella?"])] == "si"

    h2 = login(api, "pedro@mail.com", "clave-segura-1")
    api.post(url, headers=h2, json={"parliamentarian_id": pid, "answers": [
        {"question_id": q["¿Votaste por él/ella?"], "value": "si"},
        {"question_id": q["¿Volverías a votar por él/ella?"], "value": "si"}]})
    res = api.get(f"/encuestas/{encuesta['id']}/resultados", params={"parliamentarian_id": pid}).json()
    votaste, volverias = res["preguntas"][0], res["preguntas"][1]
    assert votaste["si"] == 2 and volverias["porcentaje_si"] == 50.0


def test_admin(api):
    h = login(api, "maria@mail.com", "clave-segura-1")
    nueva = {"title": "Reforma de pensiones", "per_parliamentarian": False,
             "questions": [{"text": "¿Qué opinas?", "kind": "opcion", "options": ["A favor", "En contra"]},
                           {"text": "Del 1 al 5, ¿qué tan informado estás?", "kind": "escala"}]}
    assert api.post("/admin/encuestas", headers=h, json=nueva).status_code == 403

    from app.cli import main as cli
    cli(["admin", "maria@mail.com"])
    s = api.post("/admin/encuestas", headers=h, json=nueva).json()
    ids = [x["id"] for x in s["questions"]]
    r = api.post(f"/encuestas/{s['id']}/respuestas", headers=h, json={"answers": [
        {"question_id": ids[0], "value": "A favor"}, {"question_id": ids[1], "value": "4"}]})
    assert r.status_code == 200, r.text
    res = api.get(f"/encuestas/{s['id']}/resultados").json()["preguntas"]
    assert res[0]["conteo"]["A favor"] == 1 and res[1]["promedio"] == 4.0

    assert api.patch(f"/admin/encuestas/{s['id']}", headers=h, json={"active": False}).json()["active"] is False
    assert api.get(f"/encuestas/{s['id']}").status_code == 404

    pid = _ana(api)
    c = api.get(f"/parlamentarios/{pid}/comentarios").json()[0]
    assert api.patch(f"/admin/comentarios/{c['id']}", headers=h, json={"hidden": True}).status_code == 200
    assert api.get(f"/parlamentarios/{pid}/comentarios").json() == []
    assert api.post("/admin/sincronizar", headers=h, params={"wikipedia": False}).status_code == 202


def test_limite_de_intentos(api):
    limiter.reset()
    codes = [api.post("/auth/login", json={"username": "z@z.cl", "password": "x"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    limiter.reset()
