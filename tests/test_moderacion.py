"""Reportar comentarios, bloquear usuarios y eliminar la cuenta (App Store 1.2 y 5.1.1(v))."""
import pytest

from app.database import SessionLocal
from app.models import Comment, CommentReport, Parliamentarian, Rating, User, UserBlock
from app.security import limiter
from conftest import login


def _registrar(api, rut, email):
    limiter.reset()
    r = api.post("/auth/register", json={"fiscal_id": rut, "names": "Usuario", "last_names": email[0].upper(),
                                         "email": email, "password": "clave-segura-1"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def escenario(api):
    """Un parlamentario propio y tres usuarios; al final se borra todo para no afectar a otras pruebas."""
    db = SessionLocal()
    p = Parliamentarian(chamber="diputado", external_id=990001, first_name="Prueba", last_name="Moderación")
    db.add(p)
    db.commit()
    pid = p.id
    db.close()
    usuarios = {n: _registrar(api, rut, f"{n}@mod.cl")
                for n, rut in (("ana", "9876543-3"), ("beto", "8765432-K"), ("caro", "6543210-2"))}
    yield pid, usuarios
    db = SessionLocal()
    ids = [u.id for u in db.query(User).filter(User.email.like("%@mod.cl"))]
    from app.routes.usuario import borrar_usuario
    for uid in ids:
        borrar_usuario(db, uid)
    db.query(Parliamentarian).filter_by(id=pid).delete()
    db.commit()
    db.close()


def test_reportar_y_bloquear(api, escenario):
    pid, u = escenario
    url = f"/parlamentarios/{pid}/comentarios"
    c = api.post(url, headers=u["ana"], json={"text": "Comentario ofensivo"}).json()
    api.post(url, headers=u["beto"], json={"text": "Comentario normal"})
    assert c["autor_id"] and len(api.get(url).json()) == 2

    reportar = f"/comentarios/{c['id']}/reportar"
    assert api.post(reportar, json={"motivo": "spam"}).status_code in (401, 403)
    assert api.post(reportar, headers=u["ana"], json={"motivo": "spam"}).status_code == 422  # el propio
    assert api.post(reportar, headers=u["beto"], json={"motivo": "insulto"}).status_code == 422  # motivo inválido
    assert api.post("/comentarios/99999/reportar", headers=u["beto"], json={"motivo": "spam"}).status_code == 404
    r = api.post(reportar, headers=u["beto"], json={"motivo": "ofensivo", "detalle": " insulta "})
    assert r.status_code == 201 and r.json()["nuevo"]
    r = api.post(reportar, headers=u["beto"], json={"motivo": "acoso"})  # una vez por usuario
    assert r.status_code == 200 and not r.json()["nuevo"]
    db = SessionLocal()
    rep = db.query(CommentReport).filter_by(comment_id=c["id"]).one()
    assert rep.motivo == "ofensivo" and rep.detalle == "insulta" and rep.status == "pendiente"
    db.close()

    # bloquear: caro deja de ver los comentarios de ana; los demás la siguen viendo
    ana_id = c["autor_id"]
    assert api.post(f"/usuarios/{ana_id}/bloquear", headers=u["ana"]).status_code == 422
    assert api.post("/usuarios/99999/bloquear", headers=u["caro"]).status_code == 404
    assert api.post(f"/usuarios/{ana_id}/bloquear", headers=u["caro"]).status_code == 204
    assert api.post(f"/usuarios/{ana_id}/bloquear", headers=u["caro"]).status_code == 204  # idempotente
    assert [x["text"] for x in api.get(url, headers=u["caro"]).json()] == ["Comentario normal"]
    assert len(api.get(url, headers=u["beto"]).json()) == 2 and len(api.get(url).json()) == 2
    bloqueados = api.get("/user/me/bloqueados", headers=u["caro"]).json()
    assert [(b["id"], b["nombre"]) for b in bloqueados] == [(ana_id, "Usuario A.")]
    assert api.delete(f"/usuarios/{ana_id}/bloquear", headers=u["caro"]).status_code == 204
    assert len(api.get(url, headers=u["caro"]).json()) == 2 and api.get("/user/me/bloqueados", headers=u["caro"]).json() == []

    # con 3 reportes de distintos usuarios el comentario se oculta solo
    api.post(reportar, headers=u["caro"], json={"motivo": "acoso"})
    assert len(api.get(url).json()) == 2
    otro = _registrar(api, "15555555-6", "dani@mod.cl")
    api.post(reportar, headers=otro, json={"motivo": "otro"})
    assert [x["text"] for x in api.get(url).json()] == ["Comentario normal"]


def test_admin_reportes(api, escenario):
    pid, u = escenario
    c = api.post(f"/parlamentarios/{pid}/comentarios", headers=u["ana"], json={"text": "Spam spam"}).json()
    api.post(f"/comentarios/{c['id']}/reportar", headers=u["beto"], json={"motivo": "spam"})
    api.post(f"/comentarios/{c['id']}/reportar", headers=u["caro"], json={"motivo": "otro", "detalle": "publicidad"})
    assert api.get("/admin/reportes", headers=u["beto"]).status_code == 403

    db = SessionLocal()
    db.query(User).filter_by(email="beto@mod.cl").update({"is_admin": True})
    db.commit()
    db.close()
    pendientes = [r for r in api.get("/admin/reportes", headers=u["beto"]).json() if r["comment_id"] == c["id"]]
    assert len(pendientes) == 2 and pendientes[0]["reportes_del_comentario"] == 2
    assert pendientes[0]["comentario"] == "Spam spam" and pendientes[0]["comentario_oculto"] is False

    r = api.patch(f"/admin/reportes/{pendientes[0]['id']}", headers=u["beto"],
                  json={"status": "resuelto", "ocultar_comentario": True}).json()
    assert r["status"] == "resuelto" and r["comentario_oculto"]
    assert not [r for r in api.get("/admin/reportes", headers=u["beto"]).json() if r["comment_id"] == c["id"]]
    resueltos = api.get("/admin/reportes", headers=u["beto"], params={"status": "resuelto"}).json()
    assert len([r for r in resueltos if r["comment_id"] == c["id"]]) == 2
    assert api.get(f"/parlamentarios/{pid}/comentarios").json() == []

    c2 = api.post(f"/parlamentarios/{pid}/comentarios", headers=u["caro"], json={"text": "Otro"}).json()
    assert api.delete(f"/admin/comentarios/{c2['id']}", headers=u["beto"]).status_code == 204
    assert api.get(f"/parlamentarios/{pid}/comentarios").json() == []


def test_eliminar_cuenta(api, escenario):
    pid, u = escenario
    c = api.post(f"/parlamentarios/{pid}/comentarios", headers=u["ana"], json={"text": "Me voy"}).json()
    api.put(f"/parlamentarios/{pid}/calificacion", headers=u["ana"], json={"score": 3})
    api.post("/user/me/preferences", headers=u["ana"], json={"district_vote": "no_voto"})
    api.post(f"/comentarios/{c['id']}/reportar", headers=u["beto"], json={"motivo": "spam"})
    api.post(f"/usuarios/{c['autor_id']}/bloquear", headers=u["beto"])
    beto_c = api.post(f"/parlamentarios/{pid}/comentarios", headers=u["beto"], json={"text": "Hola"}).json()
    api.post(f"/comentarios/{beto_c['id']}/reportar", headers=u["ana"], json={"motivo": "otro"})
    api.post(f"/usuarios/{beto_c['autor_id']}/bloquear", headers=u["ana"])
    limiter.reset()
    refresh = api.post("/auth/login", json={"username": "ana@mod.cl", "password": "clave-segura-1"}).json()["refresh_token"]

    assert api.request("DELETE", "/user/me", json={"password": "clave-segura-1"}).status_code in (401, 403)
    assert api.request("DELETE", "/user/me", headers=u["ana"], json={"password": "otra"}).status_code == 403
    assert api.request("DELETE", "/user/me", headers=u["ana"], json={}).status_code == 422
    assert api.request("DELETE", "/user/me", headers=u["ana"], json={"password": "clave-segura-1"}).status_code == 204

    # sus tokens dejan de servir, no puede entrar y sus datos se borraron
    assert api.get("/user/me", headers=u["ana"]).status_code == 404
    assert api.post("/auth/refresh-token", json={"refresh_token": refresh}).status_code == 404
    limiter.reset()
    assert api.post("/auth/login", json={"username": "ana@mod.cl", "password": "clave-segura-1"}).status_code == 401
    db = SessionLocal()
    try:
        ana = c["autor_id"]
        assert db.get(User, ana) is None
        assert db.query(Comment).filter_by(user_id=ana).count() == 0
        assert db.query(Rating).filter_by(user_id=ana).count() == 0
        assert db.query(CommentReport).filter((CommentReport.reporter_id == ana)
                                              | (CommentReport.comment_id == c["id"])).count() == 0
        assert db.query(UserBlock).filter((UserBlock.blocker_id == ana) | (UserBlock.blocked_id == ana)).count() == 0
    finally:
        db.close()
    assert [x["text"] for x in api.get(f"/parlamentarios/{pid}/comentarios").json()] == ["Hola"]
    assert api.get("/user/me/bloqueados", headers=u["beto"]).json() == []
    # el RUT y el correo quedan libres
    _registrar(api, "9876543-3", "ana@mod.cl")
