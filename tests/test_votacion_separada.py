"""Qué se votó (campo Articulo de la Cámara) y resultado corregido. Ejemplos reales del boletín 18702-05."""
import httpx

from app.camara import client as camara
from app.votaciones import que_se_vota
from conftest import NS, camara_falsa

SEPARADA = ("Votación Separada \n\nPartida 50 TESORO PÚBLICO\n\nCapítulo 01 Fisco\n\nPrograma 10 Fondo para "
            "Diagnósticos y Tratamientos de Alto Costo\n\nSubtítulo 24 TRANSFERENCIAS CORRIENTES\n\nItem 02 Al Gobierno "
            "Central\n\nIndicaciones: 55.1 y 55.2")
CON_AUTOR = (" del diputado Rivas José Antonio - Votación Separada \n\nPartida 18 MINISTERIO DE VIVIENDA Y URBANISMO\n\n"
             "Capítulo 28 Serviu Región del Biobío\n\nPrograma 01 Serviu Región del Biobío\n\nSubtítulo 33 TRANSFERENCIAS "
             "DE CAPITAL\n\nItem 01 Al Sector Privado Votación separada ")
INADMISIBLE = "Partida 50 Indicación 56 - En votación la reclamación contra la declaración de inadmisibilidad"
INDICACION = ("(Ley Orgánica Constitucional)\n\n del diputado Manouchehri Daniel\n\nPart 23 - Cap 01 - Prog 01 - Sub 22\n\n "
              "\"Agrégase la siguiente glosa, nueva, asociada a los subtítulos 21 y 22:\n  \n  \"\"Con cargo a estos recursos "
              "se financiarán funciones de análisis contable.\"\"\"")


def test_votacion_separada():
    q = que_se_vota(SEPARADA)
    assert q["votacion_separada"] is True and q["autor"] is None
    assert [(n["nivel"], n["numero"]) for n in q["niveles"]] == [
        ("partida", "50"), ("capitulo", "01"), ("programa", "10"), ("subtitulo", "24"), ("item", "02")]
    assert q["niveles"][0]["nombre"] == "Tesoro Público"  # sin MAYÚSCULAS
    assert q["niveles"][1]["nombre"] == "Fisco"
    assert q["indicaciones"] == "55.1 y 55.2"
    assert q["titulo"] == ("Votación separada · Tesoro Público › Transferencias Corrientes › Al Gobierno Central"
                           " · Indicaciones 55.1 y 55.2")


def test_autor_y_texto():
    q = que_se_vota(CON_AUTOR)
    assert q["autor"] == "del diputado Rivas José Antonio" and q["votacion_separada"]
    assert q["niveles"][-1] == {"nivel": "item", "etiqueta": "Ítem", "numero": "01", "nombre": "Al Sector Privado"}
    assert q["titulo"].startswith("Votación separada · Ministerio de Vivienda y Urbanismo ›")

    q = que_se_vota(INADMISIBLE)
    assert q["admisibilidad"] is True and q["indicaciones"] == "56"
    assert q["titulo"] == "Reclamo de inadmisibilidad · Partida 50 · Indicación 56"

    q = que_se_vota(INDICACION)
    assert q["nota"] == "Ley Orgánica Constitucional" and q["autor"] == "del diputado Manouchehri Daniel"
    assert [n["numero"] for n in q["niveles"]] == ["23", "01", "01", "22"]
    assert q["texto"].startswith("Agrégase la siguiente glosa") and "análisis contable" in q["texto"]
    assert q["titulo"].startswith("Indicación · Partida 23")

    assert que_se_vota("Único")["titulo"] == "Único"
    assert que_se_vota(None) is None and que_se_vota("  ") is None


def _vpl(id_, articulo, resultado, si, no):
    return (f"<VotacionProyectoLey><Id>{id_}</Id><Descripcion>Boletín N°18702-05</Descripcion>"
            f"<Fecha>2026-11-13T05:15:17</Fecha><TotalSi>{si}</TotalSi><TotalNo>{no}</TotalNo>"
            f"<TotalAbstencion>0</TotalAbstencion><Resultado Valor=\"{resultado[0]}\">{resultado[1]}</Resultado>"
            f"<TipoVotacionProyectoLey Valor=\"3\">Particular</TipoVotacionProyectoLey>"
            f"<Articulo>{articulo}</Articulo><TramiteConstitucional Id=\"1\">Primer Trámite</TramiteConstitucional>"
            f"</VotacionProyectoLey>")


PROYECTO = (f"<ProyectoLey {NS}><Id>19391</Id><NumeroBoletin>18702-05</NumeroBoletin><Nombre>Presupuestos 2027</Nombre>"
            f"<Votaciones>{_vpl(91458, INADMISIBLE, (2, 'Unánime'), 55, 58)}{_vpl(91457, SEPARADA, (1, 'Aprobado'), 71, 45)}"
            f"{_vpl(91456, 'Artículo 1', (2, 'Unánime'), 90, 3)}</Votaciones></ProyectoLey>")
ANNO = (f"<VotacionesColeccion {NS}><Votacion><Id>91458</Id><Resultado Valor=\"0\">Rechazado</Resultado></Votacion>"
        f"<Votacion><Id>91457</Id><Resultado Valor=\"1\">Aprobado</Resultado></Votacion></VotacionesColeccion>")
DETALLE = (f"<Votacion {NS}><Id>91458</Id><Descripcion>Boletín N° 18702-05</Descripcion><Fecha>2026-11-13T05:15:17</Fecha>"
           f"<TotalSi>55</TotalSi><TotalNo>58</TotalNo><Resultado Valor=\"0\">Rechazado</Resultado>"
           f"<Votos><Voto><Diputado><Id>1009</Id></Diputado><OpcionVoto Valor=\"1\">Afirmativo</OpcionVoto></Voto>"
           f"<Voto><Diputado><Id>1010</Id></Diputado><OpcionVoto Valor=\"0\">En Contra</OpcionVoto></Voto></Votos></Votacion>")


def _red(request: httpx.Request) -> httpx.Response:
    op = request.url.path.rsplit("/", 1)[-1]
    if op == "retornarVotacionesXProyectoLey":
        body = PROYECTO
    elif op == "retornarVotacionesXAnno":
        body = ANNO
    elif op == "retornarVotacionDetalle" and request.url.params["prmVotacionId"] == "91458":
        body = DETALLE
    else:
        return camara_falsa(request)
    return httpx.Response(200, content=body.encode())


def test_endpoints_votaciones(api):
    anterior = camara._client
    camara._client = httpx.AsyncClient(transport=httpx.MockTransport(_red))
    camara.limpiar_cache()
    try:
        p = api.get("/proyectos/18702-05/votaciones").json()
        assert "_enriquecido" not in p
        assert p["TramitacionUrl"].endswith("prmID=19391&prmBOLETIN=18702-05")
        v = {x["Id"]: x for x in p["Votaciones"]}
        # la Cámara marca «Unánime» lo rechazado: se toma el resultado del año
        assert v[91458]["Resultado"] == {"Valor": 0, "Nombre": "Rechazado"} and v[91458]["ResultadoCorregido"] is True
        assert v[91457]["Resultado"]["Nombre"] == "Aprobado" and "ResultadoCorregido" not in v[91457]
        # sin el dato del año y con votos en contra: sin resultado antes que uno falso
        assert v[91456]["Resultado"] is None
        # la partida 50 toma su nombre de otra votación del proyecto
        assert v[91458]["QueSeVota"]["titulo"] == "Reclamo de inadmisibilidad · Tesoro Público · Indicación 56"

        d = api.get("/votaciones/91458").json()
        assert "_enriquecido" not in d and len(d["Votos"]) == 2
        assert d["Articulo"] == INADMISIBLE and d["TipoVotacionProyectoLey"]["Nombre"] == "Particular"
        assert d["TramiteConstitucional"]["Nombre"] == "Primer Trámite"
        assert d["QueSeVota"]["admisibilidad"] is True and d["TramitacionUrl"]
        assert api.get("/votaciones/91458").json() == d  # desde la caché, igual
    finally:
        camara._client = anterior
        camara.limpiar_cache()
