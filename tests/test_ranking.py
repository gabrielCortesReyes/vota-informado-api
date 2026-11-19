"""(Corre después de test_participacion.) Rankings de notas y comentarios, y límite de comentarios por día."""
from app.database import SessionLocal
from app.models import Comment, Parliamentarian, Rating, User
from app.routes import parlamentarios
from app.security import limiter


def _rut(n: int) -> str:
    s, m = 0, 2
    for d in reversed(str(n)):
        s += int(d) * m
        m = 2 if m == 7 else m + 1
    dv = 11 - s % 11
    return f"{n}-{'0' if dv == 11 else 'K' if dv == 10 else dv}"


def _usuarios(api, cantidad: int, prefijo: str, base: int) -> list[int]:
    ids = []
    for i in range(cantidad):
        limiter.reset()
        r = api.post("/auth/register", json={"fiscal_id": _rut(base + i),
                                             "names": "Persona", "last_names": prefijo,
                                             "email": f"{prefijo}{i}@ranking.cl", "password": "clave-segura-1"})
        assert r.status_code == 201, r.text
        ids.append(api.get("/user/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()["id"])
    return ids


def _parl(db, external_id, chamber="diputado"):
    return db.query(Parliamentarian).filter_by(chamber=chamber, external_id=external_id).one().id


def test_ranking_calificaciones(api):
    usuarios = _usuarios(api, 12, "nota", 30_000_100)
    db = SessionLocal()
    try:
        ana, luis, rosa = _parl(db, 1009), _parl(db, 1010), _parl(db, 1011)
        juan = _parl(db, 905, "senador")
        db.query(Rating).delete()
        # Ana: 12 notas, promedio 4,5 · Luis: 2 notas 5 · Rosa: 12 notas, promedio 2 · un senador (no entra)
        for i, u in enumerate(usuarios):
            db.add(Rating(user_id=u, parliamentarian_id=ana, score=5 if i % 2 else 4))
            db.add(Rating(user_id=u, parliamentarian_id=rosa, score=2))
        for u in usuarios[:2]:
            db.add(Rating(user_id=u, parliamentarian_id=luis, score=5))
        db.add(Rating(user_id=usuarios[0], parliamentarian_id=juan, score=5))
        db.commit()
    finally:
        db.close()

    # sin mínimo: entra todo diputado con al menos una nota, ordenado por promedio, desde la posición 1
    r = api.get("/ranking/calificaciones").json()
    assert [(i["posicion"], i["parlamentario"]["external_id"], i["promedio"], i["cantidad"]) for i in r["items"]] == [
        (1, 1010, 5.0, 2), (2, 1009, 4.5, 12), (3, 1011, 2.0, 12)]
    assert r["promedio_general"] == 3.38  # solo diputados
    # campos que ya leía la app publicada
    assert r["minimo_calificaciones"] == 1 and r["items"][0]["nota_ajustada"] == 5.0
    assert api.get("/ranking/calificaciones", params={"camara": "senador"}).json()["items"] == []
    assert len(api.get("/ranking/calificaciones", params={"camara": "diputado", "limit": 5}).json()["items"]) == 3
    asc = api.get("/ranking/calificaciones", params={"orden": "asc"}).json()["items"]
    assert [(i["posicion"], i["parlamentario"]["external_id"]) for i in asc] == [(1, 1011), (2, 1009), (3, 1010)]


def test_ranking_comentarios(api):
    usuarios = _usuarios(api, 3, "coment", 30_000_200)
    db = SessionLocal()
    try:
        ana, luis = _parl(db, 1009), _parl(db, 1010)
        juan = _parl(db, 905, "senador")
        db.query(Comment).delete()
        for u in usuarios:
            db.add(Comment(user_id=u, parliamentarian_id=luis, text="Bien"))
        for _ in range(5):  # una sola persona con muchos comentarios
            db.add(Comment(user_id=usuarios[0], parliamentarian_id=ana, text="Hola"))
        db.add(Comment(user_id=usuarios[1], parliamentarian_id=ana, text="Oculto", hidden=True))
        db.add(Comment(user_id=usuarios[1], parliamentarian_id=juan, text="Senador"))
        db.commit()
    finally:
        db.close()

    items = api.get("/ranking/comentarios").json()["items"]
    assert [(i["posicion"], i["parlamentario"]["external_id"], i["personas"], i["comentarios"]) for i in items] == [
        (1, 1010, 3, 3), (2, 1009, 1, 5)]
    # menos comentados: desde 1 comentario (sin los que tienen 0) y sin senadores
    menos = api.get("/ranking/comentarios", params={"orden": "asc"}).json()["items"]
    assert [(i["posicion"], i["parlamentario"]["external_id"]) for i in menos] == [(1, 1009), (2, 1010)]
    assert api.get("/ranking/comentarios", params={"desde": "2099-01-01"}).json()["items"] == []


def test_limite_comentarios_por_dia(api, monkeypatch):
    monkeypatch.setattr(parlamentarios, "COMENTARIOS_POR_DIA", 2)
    limiter.reset()
    r = api.post("/auth/register", json={"fiscal_id": _rut(29_876_543), "names": "Mucho", "last_names": "Texto",
                                         "email": "mucho@ranking.cl", "password": "clave-segura-1"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    db = SessionLocal()
    luis = _parl(db, 1010)
    db.close()
    for _ in range(2):
        assert api.post(f"/parlamentarios/{luis}/comentarios", json={"text": "Uno más"}, headers=h).status_code == 201
    r = api.post(f"/parlamentarios/{luis}/comentarios", json={"text": "Uno más"}, headers=h)
    assert r.status_code == 429 and "al día" in r.json()["detail"]
    limiter.reset()
