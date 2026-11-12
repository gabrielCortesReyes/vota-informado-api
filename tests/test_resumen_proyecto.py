"""Resumen en lenguaje simple de un proyecto: lo escribe un administrador y lo lee cualquiera."""
from app.database import SessionLocal
from app.models import User
from app.security import limiter

RESUMEN = {
    "en_una_frase": "Define cuánto dinero puede gastar el Estado en 2027 y en qué.",
    "que_cambia": ["Fija el gasto de cada ministerio.", "Autoriza endeudamiento."],
    "a_quien_afecta": ["Todas las personas que usan servicios públicos."],
    "cifras": [{"etiqueta": "Gasto total", "valor": "$ 85 billones"}],
    "fuente_url": "https://www.dipres.gob.cl",
}


def test_resumen_proyecto(api):
    assert api.get("/proyectos/18702-05/resumen").status_code == 404
    limiter.reset()
    r = api.post("/auth/register", json={"fiscal_id": "15432198-5", "names": "Admin", "last_names": "Resumen",
                                         "email": "admin@resumen.cl", "password": "clave-segura-1"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert api.put("/admin/proyectos/18702-05/resumen", json=RESUMEN, headers=h).status_code == 403

    db = SessionLocal()
    db.query(User).filter_by(email="admin@resumen.cl").update({"is_admin": True})
    db.commit(); db.close()

    assert api.put("/admin/proyectos/18702-05/resumen", json={**RESUMEN, "que_cambia": []}, headers=h).status_code == 422
    assert api.put("/admin/proyectos/no-valido/resumen", json=RESUMEN, headers=h).status_code == 422
    r = api.put("/admin/proyectos/18702-05/resumen", json=RESUMEN, headers=h)
    assert r.status_code == 200, r.text
    r = api.put("/admin/proyectos/18702-05/resumen", json={**RESUMEN, "en_una_frase": "Otra frase más corta."}, headers=h)
    assert r.json()["en_una_frase"] == "Otra frase más corta."

    leido = api.get("/proyectos/18702-05/resumen").json()
    assert leido["boletin"] == "18702-05"
    assert leido["cifras"] == [{"etiqueta": "Gasto total", "valor": "$ 85 billones"}]
    assert leido["que_cambia"][1] == "Autoriza endeudamiento."

    assert api.delete("/admin/proyectos/18702-05/resumen", headers=h).status_code == 204
    assert api.get("/proyectos/18702-05/resumen").status_code == 404
    assert api.delete("/admin/proyectos/18702-05/resumen", headers=h).status_code == 404

    db = SessionLocal()
    from app.routes.usuario import borrar_usuario
    borrar_usuario(db, db.query(User).filter_by(email="admin@resumen.cl").one().id)
    db.commit(); db.close()
