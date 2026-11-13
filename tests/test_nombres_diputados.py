"""opendata entrega nombres de exdiputados para los Id del período 2026-2030 (app/nombres.py)."""
import asyncio
import json

from app.camara import client as camara
from app.database import SessionLocal
from app.models import Parliamentarian
from app.nombres import DATA, corregir_nombres
from app.sync import sincronizar_diputados
from conftest import NS, RESPUESTAS

ASCENCIO = f"""<DiputadosPeriodoColeccion {NS}>
  <DiputadoPeriodo><FechaInicio>2030-03-10T00:00:00</FechaInicio><FechaTermino xsi:nil="true"/>
    <Diputado><Id>1174</Id><Nombre>GABRIEL</Nombre><Nombre2/><ApellidoPaterno>ASCENCIO</ApellidoPaterno>
      <ApellidoMaterno>MANSILLA</ApellidoMaterno><Militancias/></Diputado></DiputadoPeriodo></DiputadosPeriodoColeccion>"""

VOTACION = f"""<Votacion {NS}><Id>91458</Id><Descripcion>Boletín N° 18702-05</Descripcion>
  <Votos><Voto><Diputado><Id>1174</Id><Nombre>GABRIEL</Nombre><ApellidoPaterno>ASCENCIO</ApellidoPaterno>
    <ApellidoMaterno>MANSILLA</ApellidoMaterno></Diputado><OpcionVoto Valor="0">En Contra</OpcionVoto></Voto>
  <Voto><Diputado><Id>1009</Id><Nombre>Ana</Nombre><ApellidoPaterno>Pérez</ApellidoPaterno></Diputado>
    <OpcionVoto Valor="1">Afirmativo</OpcionVoto></Voto></Votos></Votacion>"""


def test_mapa_completo():
    mapa = json.loads((DATA / "diputados_nombres.json").read_text(encoding="utf-8"))
    assert len(mapa) == 155
    assert mapa["1174"] == {"nombre": "Marco Antonio", "apellido_paterno": "Sulantay", "apellido_materno": "Olivares"}
    assert mapa["1166"]["apellido_paterno"] == "Romero"


def test_corregir_nombres_sin_id_conocido():
    data = {"Diputado": {"Id": 99999, "Nombre": "X"}, "Votos": [{"Diputado": {"Id": 1174, "Nombre": "GABRIEL"}}]}
    corregir_nombres(data)
    assert data["Diputado"]["Nombre"] == "X"
    assert data["Votos"][0]["Diputado"]["Nombre"] == "Marco Antonio"


def test_sync_diputado_ascencio_queda_sulantay(api, monkeypatch):
    monkeypatch.setitem(RESPUESTAS, "retornarDiputadosPeriodoActual", ASCENCIO)
    camara.limpiar_cache()
    db = SessionLocal()
    try:
        assert asyncio.run(sincronizar_diputados(db)) == 1
        p = db.query(Parliamentarian).filter_by(chamber="diputado", external_id=1174).one()
        assert (p.first_name, p.last_name, p.second_last_name) == ("Marco Antonio", "Sulantay", "Olivares")
    finally:  # no dejar el 1174 para las demás pruebas
        db.query(Parliamentarian).filter_by(chamber="diputado", external_id=1174).delete()
        db.commit(); db.close()
        camara.limpiar_cache()


def test_detalle_votacion_corrige_nombres(api, monkeypatch):
    monkeypatch.setitem(RESPUESTAS, "retornarVotacionDetalle", VOTACION)
    monkeypatch.setitem(RESPUESTAS, "retornarDiputado",
                        f"<Diputado {NS}><Id>1174</Id><Nombre>GABRIEL</Nombre><ApellidoPaterno>ASCENCIO</ApellidoPaterno></Diputado>")
    camara.limpiar_cache()
    try:
        votos = api.get("/votaciones/91458").json()["Votos"]
        dip = {v["Diputado"]["Id"]: v["Diputado"] for v in votos}
        assert (dip[1174]["Nombre"], dip[1174]["ApellidoPaterno"], dip[1174]["ApellidoMaterno"]) == \
            ("Marco Antonio", "Sulantay", "Olivares")
        assert dip[1009]["Nombre"] == "Ana"  # Id fuera del mapa: queda como viene
        assert api.get("/diputados/1174").json()["ApellidoPaterno"] == "Sulantay"  # la raíz es el diputado
    finally:
        camara.limpiar_cache()
