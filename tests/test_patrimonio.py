"""(Corre después de test_participacion, que carga el padrón.) Patrimonio desde InfoProbidad, sin red."""
import asyncio
import json

import httpx

from app import patrimonio
from app.database import SessionLocal
from app.models import Parliamentarian, PatrimonioDeclaracion

LISTADO = [
    # Ana Pérez (Id Cámara 1009): dos declaraciones, gana la más reciente
    {"IdDeclaracion": 100, "Nombre": "ANA", "ApellidoPaterno": "PÉREZ", "Cargo": "DIPUTADO/DA",
     "FechaDeclaracion": "2025/04/08"},
    {"IdDeclaracion": 200, "Nombre": "ANA", "ApellidoPaterno": "PÉREZ", "Cargo": "DIPUTADO/DA",
     "FechaDeclaracion": "2026/09/04"},
    # homónima con otro cargo: no se usa
    {"IdDeclaracion": 300, "Nombre": "ANA", "ApellidoPaterno": "PEREZ", "Cargo": "CONCEJAL",
     "FechaDeclaracion": "2026/10/01"},
]

DECLARACION = {
    "Id_Declaracion": 200,
    "Fecha_de_la_Declaracion": "2026/09/04",
    "Tipo_Declaracion": {"nombre": "Cese Y Asunción Simultánea De Funciones"},
    "Datos_del_Declarante": {"nombre": "ANA", "Apellido_Paterno": "PÉREZ"},
    "Datos_Entidad_Por_La_Que_Declara": {"Cargo_Funcion": {"nombre": "DIPUTADO/DA"}, "Renta_Bruta": "5.300.000"},
    "Bienes_Inmuebles_Situados_En_Chile": [
        {"Comuna": {"nombre": "LO BARNECHEA"}, "Direccion": "RESERVADO", "Rol_Avaluo": "RESERVADO",
         "Avaluo_Fiscal": "614.576.536", "Porcentaje_Propiedad": "100", "Fecha_Adquisicion": "2018/09/06"},
        {"Comuna": {"nombre": "SANTIAGO"}, "Direccion": "AGUSTINAS 1185 OF 51", "Rol_Avaluo": "105-162",
         "Avaluo_Fiscal": 72443013, "Porcentaje_Propiedad": 100},
        {"Comuna": {"nombre": "LAS CONDES"}, "Direccion": "MALAGA 395", "Avaluo_Fiscal": 1000000000,
         "Porcentaje_Propiedad": "14.29"},
    ],
    "Vehiculos_Motorizados": {"Vehiculo": [{"Marca": "MERCEDES BENZ", "Modelo": "GLS 350D", "Anio_Fabricacion": 2018,
                                            "Avaluo_Fiscal": "$ 40.000.000"}]},
    "Derechos_Acciones_Chile": [{"Razon_Social": "ZINTEX EVENTOS LIMITADA", "Rut": "99.519.370-1",
                                 "Cantidad_Porcentaje": 85, "Valor_Libro": "150000000"}],
    "Instrumentos_Valor_TransableChile": [{"Tipo_Instrumento": "ACCION", "Emisor": "CLUB DE YATES ALGARROBO",
                                           "Cantidad": 1, "Valor_Corriente": "4.500.000"}],
    "Otros_Bienes": None,
    "Pasivos": [{"Tipo_Obligacion": "CREDITO HIPOTECARIO", "Acreedor": "BANCO SANTANDER", "Monto": "500.000.000"}],
}

PEDIDAS = []


def _red(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/Home/Listado":
        return httpx.Response(200, text=f"<script>var datos = {json.dumps(LISTADO)};</script>")
    if request.url.path == "/Declaracion/descargarDeclaracionJSon":
        did = int(dict(httpx.QueryParams(request.content.decode()))["ID"])
        PEDIDAS.append(did)
        return httpx.Response(200, json={**DECLARACION, "Id_Declaracion": did})
    return httpx.Response(404)


def test_normalizar_montos():
    assert patrimonio._numero("$ 614.576.536") == 614576536
    assert patrimonio._numero("13.4") == 13.4
    assert patrimonio._numero("1.234,5") == 1234.5
    assert patrimonio._numero("RESERVADO") is None


def test_patrimonio(api, monkeypatch):
    monkeypatch.setattr(patrimonio, "_cliente", lambda: httpx.AsyncClient(
        base_url=patrimonio.INFOPROBIDAD_URL, transport=httpx.MockTransport(_red)))
    db = SessionLocal()
    try:
        r = asyncio.run(patrimonio.sincronizar_patrimonio(db))
        ana = db.query(Parliamentarian).filter_by(chamber="diputado", external_id=1009).one().id
    finally:
        db.close()
    assert r["nuevas"] == 1 and r["errores"] == 0 and PEDIDAS == [200]

    d = api.get(f"/parlamentarios/{ana}/patrimonio").json()
    assert d["declaracion_id"] == 200 and d["fecha"] == "2026-09-04"
    assert d["tipo"] == "Cese Y Asunción Simultánea De Funciones"
    assert d["fuente_url"].endswith("Declaracion?ID=200") and "CC BY 4.0" in d["licencia"]
    assert d["renta_bruta_mensual"] == 5300000
    t = d["totales"]
    assert t["inmuebles"] == 614576536 + 72443013 + 142900000  # del bien compartido, solo su 14,29 %
    assert d["inmuebles"][2]["monto"] == 1000000000
    assert (t["vehiculos"], t["sociedades"], t["valores"], t["pasivos"]) == (40000000, 150000000, 4500000, 500000000)
    assert t["patrimonio_neto"] == t["activos"] - 500000000
    casa = d["inmuebles"][0]
    assert casa["titulo"] == "LO BARNECHEA" and casa["porcentaje"] == 100 and casa["fecha_adquisicion"] == "2018-09-06"
    assert d["vehiculos"][0]["titulo"] == "MERCEDES BENZ"
    assert d["sociedades"][0]["titulo"] == "ZINTEX EVENTOS LIMITADA" and d["sociedades"][0]["porcentaje"] == 85
    assert d["valores"][0]["titulo"] == "CLUB DE YATES ALGARROBO"
    assert d["pasivos"][0]["titulo"] == "BANCO SANTANDER"
    assert d["datos"] is None
    assert api.get(f"/parlamentarios/{ana}/patrimonio", params={"incluir_fuente": True}).json()["datos"]["Id_Declaracion"] == 200

    ranking = api.get("/patrimonio/diputados").json()
    assert [f["parlamentario"]["external_id"] for f in ranking] == [1009]

    # sin cambios: no se vuelve a descargar
    PEDIDAS.clear()
    db = SessionLocal()
    try:
        assert asyncio.run(patrimonio.sincronizar_patrimonio(db))["sin_cambios"] == 1
        assert db.query(PatrimonioDeclaracion).count() == 1
    finally:
        db.close()
    assert PEDIDAS == []

    otro = api.get("/parlamentarios").json()
    sin = next(p["id"] for p in otro if p["id"] != ana)
    assert api.get(f"/parlamentarios/{sin}/patrimonio").status_code == 404
