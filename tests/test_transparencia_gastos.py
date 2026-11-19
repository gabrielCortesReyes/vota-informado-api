"""(Corre después de test_participacion.) Gastos de transparencia: carga por CSV/JSON (solo admin),
identificación por Id o nombre, reemplazo al recargar, consulta por diputado y ranking."""
import pytest

from app.database import SessionLocal
from app.gastos import leer_csv
from app.models import Parliamentarian, User
from app.security import limiter
from test_ranking import _rut

CSV = """diputado_id;diputado;anno;mes;tipo;categoria;monto
1009;;2026;8;operacionales;TRASLACIÓN;3.494.271
1009;;2026;8;operacionales;Telefonía;$70.087
1009;;2026;8;operacionales;DIFUSIÓN;0
;Muñoz, Luis;2026;agosto;operacionales;TRASLACIÓN;1.000.000
1010;;2026;9;operacionales;TRASLACIÓN;500.000
9999;Nadie Conocido;2026;8;operacionales;TRASLACIÓN;1
"""


@pytest.fixture(scope="module")
def admin(api):
    limiter.reset()
    r = api.post("/auth/register", json={"fiscal_id": _rut(30_000_300), "names": "Admin", "last_names": "Gastos",
                                         "email": "admin@gastos.cl", "password": "clave-segura-1"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert api.post("/admin/gastos/csv", content=CSV, headers={**h, "content-type": "text/csv"}).status_code == 403
    db = SessionLocal()
    db.query(User).filter_by(email="admin@gastos.cl").update({"is_admin": True})
    db.commit(); db.close()
    return h


def _id(external_id):
    db = SessionLocal()
    try:
        return db.query(Parliamentarian).filter_by(chamber="diputado", external_id=external_id).one().id
    finally:
        db.close()


def test_leer_csv():
    filas = leer_csv(CSV)
    assert len(filas) == 6 and filas[0].monto == 3494271 and filas[1].monto == 70087
    assert filas[3].mes == 8 and filas[3].diputado == "Muñoz, Luis"
    with pytest.raises(ValueError, match="columnas"):
        leer_csv("a,b\n1,2\n")
    with pytest.raises(ValueError, match="Línea 2"):
        leer_csv("diputado_id,anno,mes,categoria,monto\n1009,2026,13,X,1\n")


def test_gastos(api, admin):
    h = {**admin, "content-type": "text/csv"}
    r = api.post("/admin/gastos/csv", content=CSV.encode(), headers=h, params={"fuente": "Solicitud AB001"})
    assert r.status_code == 200, r.text
    assert r.json() == {"filas": 5, "meses": 3, "sin_identificar": ["Nadie Conocido"]}

    ana = api.get(f"/parlamentarios/{_id(1009)}/gastos").json()
    assert ana["total"] == 3494271 + 70087 and (ana["desde"], ana["hasta"]) == ("2026-08", "2026-08")
    mes = ana["meses"][0]
    assert (mes["anno"], mes["mes"], mes["tipo"]) == (2026, 8, "operacionales")
    assert [c["categoria"] for c in mes["categorias"]] == ["TRASLACIÓN", "TELEFONÍA"]  # sin los $0
    assert ana["fuente_url"].endswith("gastosoperacionales.aspx?prmId=1009")

    luis = api.get(f"/parlamentarios/{_id(1010)}/gastos").json()
    assert luis["total"] == 1_500_000 and [m["mes"] for m in luis["meses"]] == [9, 8]
    assert api.get(f"/parlamentarios/{_id(1010)}/gastos", params={"desde": "2026-09"}).json()["total"] == 500_000

    # ranking de traslación
    rk = api.get("/gastos/diputados", params={"categoria": "traslación"}).json()
    assert isinstance(rk, list) and rk[0]["origen"] == "detalle"  # lista: el formato de la app publicada
    assert [(f["parlamentario"]["external_id"], f["total"], f["meses"]) for f in rk] == [(1009, 3494271, 1), (1010, 1500000, 2)]
    assert rk[1]["promedio_mensual"] == 750_000
    assert api.get("/gastos/diputados", params={"orden": "asc"}).json()[0]["parlamentario"]["external_id"] == 1010
    assert api.get("/gastos/periodos").json() == [{"periodo": "2026-09", "diputados": 1}, {"periodo": "2026-08", "diputados": 2}]
    assert api.get("/gastos/categorias").json()[0] == {"tipo": "operacionales", "categoria": "TRASLACIÓN", "monto": 4994271}

    # volver a cargar un mes lo reemplaza; JSON también sirve
    r = api.post("/admin/gastos", headers=admin, json={"fuente": "corrección", "gastos": [
        {"diputado_id": 1009, "anno": 2026, "mes": 8, "categoria": "Traslación", "monto": 100}]})
    assert r.json()["meses"] == 1
    assert api.get(f"/parlamentarios/{_id(1009)}/gastos").json()["total"] == 100

    assert api.delete("/admin/gastos", params={"periodo": "2026-09"}, headers=admin).json() == {"eliminadas": 1}
    assert api.get(f"/parlamentarios/{_id(1010)}/gastos").json()["total"] == 1_000_000
    assert api.post("/admin/gastos/csv", content=b"x;y\n1;2", headers=h).status_code == 422

    juan = api.get("/parlamentarios", params={"camara": "senador"}).json()[0]["id"]
    assert api.get(f"/parlamentarios/{juan}/gastos").status_code == 404


RESUMEN = {
    "dataset": "gastos", "datosAl": "2026-11-19T13:37:33Z", "metodologia": "https://congresovisible.cl/metodologia",
    "licencia": "CC BY 4.0", "licenciaUrl": "https://creativecommons.org/licenses/by/4.0/deed.es",
    "atribucion": "Congreso Visible (congresovisible.cl)", "fuente": "Cámara de Diputadas y Diputados (camara.cl)",
    "registros": [
        {"nombre": "Ana Pérez", "gasto_total_clp": 160000000, "meses_publicados": 8,
         "url_ficha": "https://congresovisible.cl/parlamentarios/ana-perez"},
        {"nombre": "Luis Muñoz", "gasto_total_clp": 120000000, "meses_publicados": 8},
        {"nombre": "Nadie Conocido", "gasto_total_clp": 1, "meses_publicados": 8},
    ],
}


def test_gastos_resumen(api, admin):
    r = api.post("/admin/gastos/resumen", json=RESUMEN, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json() == {"cargados": 2, "sin_identificar": ["Nadie Conocido"]}
    sin_atribucion = {k: v for k, v in RESUMEN.items() if k not in ("atribucion", "fuente")}
    assert api.post("/admin/gastos/resumen", json=sin_atribucion, headers=admin).status_code == 422

    ana = api.get(f"/parlamentarios/{_id(1009)}/gastos").json()["resumen"]
    assert (ana["total"], ana["meses"], ana["promedio_mensual"]) == (160000000, 8, 20000000)
    assert ana["atribucion"] == "Congreso Visible (congresovisible.cl)" and ana["licencia"] == "CC BY 4.0"
    assert "Dieta" in ana["incluye"] and ana["url_ficha"].endswith("ana-perez")

    # sin categoría ni período el ranking usa el total del período; con categoría, el detalle mensual
    rk = api.get("/gastos/diputados").json()
    assert all(f["origen"] == "resumen" and f["atribucion"].startswith("Congreso Visible") for f in rk)
    assert [(f["parlamentario"]["external_id"], f["total"]) for f in rk] == [(1009, 160000000), (1010, 120000000)]
    assert api.get("/gastos/diputados", params={"categoria": "TRASLACIÓN"}).json()[0]["origen"] == "detalle"

    # si casi nadie calza, no se carga nada
    mal = {**RESUMEN, "registros": [{"nombre": "X Y", "gasto_total_clp": 1, "meses_publicados": 1}]}
    assert api.post("/admin/gastos/resumen", json=mal, headers=admin).status_code == 422
    assert api.get("/gastos/diputados").json()[0]["origen"] == "resumen"
