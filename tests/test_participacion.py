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
    assert resultado["partidos"] == 2 and resultado["diputados"] == 3 and resultado["senadores"] == 1
    assert resultado["wikipedia"] == {"revisados": 4, "encontrados": 1}

    partidos = {p["alias"]: p for p in api.get("/partidos").json()}
    assert partidos["PX"]["diputados"] == 1 and partidos["PX"]["logo_url"] and partidos["PY"]["senadores"] == 1

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
        assert db.query(Parliamentarian).count() == 4
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


def test_distrito_voto_y_logos(api):
    db = SessionLocal()
    try:
        asyncio.run(sync.sincronizar_todo(db))
    finally:
        db.close()

    # logo del partido desde Wikidata (P154 vigente) + Commons, con licencia y autor
    px = {p["alias"]: p for p in api.get("/partidos").json()}["PX"]
    assert px["logo_url"] == "https://upload.wikimedia.org/256px-px.png"
    assert px["logo_license"] == "Public domain" and px["logo_author"] == "Partido X"
    assert px["logo_source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    ana = api.get("/parlamentarios", params={"q": "Ana"}).json()[0]
    assert ana["party"]["logo_url"] and ana["district"] == "10"

    # comunas con distrito y diputados por distrito
    comunas = {c["Nombre"]: c for c in api.get("/catalogos/comunas").json()}
    assert comunas["Ñuñoa"]["Distrito"] == 10
    d10 = api.get("/distritos/10/diputados").json()
    assert [d["last_name"] for d in d10] == ["Muñoz", "Pérez"]
    assert len(api.get("/parlamentarios", params={"distrito": 8}).json()) == 1
    luis = next(d for d in d10 if d["last_name"] == "Muñoz")
    rosa = api.get("/distritos/8/diputados").json()[0]

    # registro con el diputado votado; debe ser de su distrito
    limiter.reset()
    base = {"names": "Paula", "last_names": "Rey", "password": "clave-segura-1", "commune_id": 13120}
    r = api.post("/auth/register", json={**base, "fiscal_id": "5.126.663-3", "email": "paula@mail.com",
                                         "voted_parliamentarian_id": rosa["id"]})
    assert r.status_code == 422 and "distrito 10" in r.text
    r = api.post("/auth/register", json={**base, "fiscal_id": "5.126.663-3", "email": "paula@mail.com",
                                         "voted_parliamentarian_id": luis["id"]})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    prefs = api.get("/user/me/preferences", headers=h).json()
    assert prefs == {"commune_id": 13120, "district_vote": "diputado", "voted_parliamentarian_id": luis["id"]}

    mio = api.get("/user/me/distrito", headers=h).json()
    assert mio["distrito"] == 10 and len(mio["diputados"]) == 2

    # votos del distrito en una votación: mi diputado primero, el que no votó queda sin opción
    v = api.get("/user/me/distrito/votaciones/40000", headers=h).json()
    assert v["distrito"] == 10 and v["votacion"]["Resultado"]["Nombre"] == "Aprobado"
    assert v["votos"][0]["es_mi_diputado"] and v["votos"][0]["opcion"] is None
    assert v["votos"][1]["opcion"]["Nombre"] == "Afirmativo"
    assert api.get("/user/me/distrito/votaciones/ultima", headers=h).json()["votacion"]["Id"] == 40000
    publico = api.get("/votaciones/40000/distrito/8").json()
    assert publico["votos"][0]["opcion"]["Nombre"] == "En Contra" and not publico["votos"][0]["es_mi_diputado"]

    # perfil: cambiar solo el voto mantiene la comuna; 'otro' borra el diputado
    r = api.post("/user/me/preferences", headers=h, json={"district_vote": "otro"}).json()
    assert r == {"commune_id": 13120, "district_vote": "otro", "voted_parliamentarian_id": None}
    assert api.post("/user/me/preferences", headers=h, json={"district_vote": "diputado"}).status_code == 422
    api.post("/user/me/preferences", headers=h, json={"voted_parliamentarian_id": ana["id"]})
    # cambiarse a una comuna de otro distrito borra el voto
    r = api.post("/user/me/preferences", headers=h, json={"commune_id": 13101}).json()
    assert r["district_vote"] is None and r["voted_parliamentarian_id"] is None
