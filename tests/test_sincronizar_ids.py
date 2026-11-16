"""(Corre después de test_participacion, que carga el padrón.) El 2026-11-16 retornarDiputadosPeriodoActual pasó a
usar otros Id (1487...) que las votaciones (803...), sin militancias ni distrito. La sincronización debe seguir
usando los Id de las votaciones y no perder partido ni distrito."""
import asyncio

import httpx

from app import nombres, sync
from app.camara import client as camara
from app.database import SessionLocal
from app.models import Parliamentarian
from conftest import NS, camara_falsa


def _periodo(*diputados):
    filas = "".join(
        f"""<DiputadoPeriodo><FechaInicio>2030-03-10T00:00:00</FechaInicio><FechaTermino xsi:nil="true"/>
          <Diputado><Id>{i}</Id><Nombre>{n}</Nombre><ApellidoPaterno>{p}</ApellidoPaterno>
          <ApellidoMaterno>{m}</ApellidoMaterno><Militancias/></Diputado></DiputadoPeriodo>"""
        for i, n, p, m in diputados)
    return f"<DiputadosPeriodoColeccion {NS}>{filas}</DiputadosPeriodoColeccion>"


def _sincronizar(cuerpo, monkeypatch):
    def red(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("retornarDiputadosPeriodoActual"):
            return httpx.Response(200, content=cuerpo.encode())
        return camara_falsa(request)

    # Id de las votaciones con sus nombres correctos (app/data/diputados_nombres.json)
    monkeypatch.setattr(nombres, "diputados_nombres", lambda: {
        "1009": {"nombre": "Ana", "apellido_paterno": "Pérez", "apellido_materno": ""},
        "1010": {"nombre": "Luis", "apellido_paterno": "Muñoz", "apellido_materno": ""},
        "1011": {"nombre": "Rosa", "apellido_paterno": "Vera", "apellido_materno": ""},
    })
    anterior = camara._client
    camara._client = httpx.AsyncClient(transport=httpx.MockTransport(red))
    camara.limpiar_cache()
    db = SessionLocal()
    try:
        return asyncio.run(sync.sincronizar_diputados(db))
    finally:
        db.close()
        camara._client = anterior
        camara.limpiar_cache()


def _diputados():
    db = SessionLocal()
    try:
        return {p.external_id: (p.current, p.district, p.party_id)
                for p in db.query(Parliamentarian).filter_by(chamber="diputado")}
    finally:
        db.close()


def test_ids_nuevos_se_traducen(api, monkeypatch):
    antes = _diputados()
    assert antes[1009][0] and antes[1009][1] == "10" and antes[1009][2] is not None

    n = _sincronizar(_periodo((1487, "ANA", "PÉREZ", ""), (1488, "LUIS", "MUÑOZ", ""), (1489, "ROSA", "VERA", "")),
                     monkeypatch)
    despues = _diputados()
    assert n == 3
    assert not {1487, 1488, 1489} & set(despues)            # no se crearon diputados con los Id nuevos
    assert despues[1009] == antes[1009]                      # sigue vigente, con su distrito y su partido
    assert despues[1011][:2] == (True, "8")


def test_respuesta_a_medias_no_da_de_baja(api, monkeypatch):
    _sincronizar(_periodo((1487, "ANA", "PÉREZ", "")), monkeypatch)  # 1 de 3: no se da de baja a nadie
    assert all(v[0] for k, v in _diputados().items() if k in (1009, 1010, 1011))
