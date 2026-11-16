"""Gastos de los diputados (Transparencia Activa de la Cámara): carga y consultas.

La Cámara no los ofrece por API: se publican en camara.cl como una ficha por diputado y mes (gastos
operacionales, pasajes aéreos), detrás de una protección contra accesos automáticos. Se cargan aquí desde
un CSV o JSON, por ejemplo la base que entregue la Cámara ante una solicitud por Ley de Transparencia
(ver docs/solicitud_transparencia_gastos.md).

CSV (separado por coma o punto y coma, con encabezado):
    diputado_id,diputado,anno,mes,tipo,categoria,monto
    1262,"Vergara, Diego",2026,8,operacionales,TRASLACIÓN,3.494.271
- diputado_id es el prmId de camara.cl; si falta, se busca por el nombre.
- tipo: operacionales (por defecto) o pasajes_aereos.
- monto en pesos; se aceptan puntos de miles y el signo $.
Cargar un mes de un diputado reemplaza lo que había de ese mes y tipo (se puede volver a cargar).
"""
import csv
import io
import re
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.identificar import IndiceDiputados
from app.models import GastoParlamentario, Parliamentarian
from app.schemas import GastoIn

TIPOS = {"operacionales", "pasajes_aereos"}
_ALIAS_COLUMNAS = {
    "id": "diputado_id", "prmid": "diputado_id", "diputado_id": "diputado_id",
    "diputado": "diputado", "nombre": "diputado", "parlamentario": "diputado",
    "anno": "anno", "año": "anno", "ano": "anno", "year": "anno",
    "mes": "mes", "tipo": "tipo", "categoria": "categoria", "categoría": "categoria", "gasto": "categoria",
    "gastos": "categoria", "monto": "monto", "total": "monto",
}
_MESES = {m: i + 1 for i, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                           "septiembre", "octubre", "noviembre", "diciembre"))}


def fuente_url(diputado_id: int, tipo: str = "operacionales") -> str:
    pagina = "pasajesaereos" if tipo == "pasajes_aereos" else "gastosoperacionales"
    return f"https://www.camara.cl/diputados/detalle/{pagina}.aspx?prmId={diputado_id}"


def _monto(valor: str) -> int:
    limpio = re.sub(r"[^\d,-]", "", str(valor or ""))
    limpio = limpio.split(",")[0]  # decimales: fuera
    if not limpio or limpio == "-":
        return 0
    return int(limpio)


def _mes(valor: str) -> int:
    v = str(valor or "").strip().lower()
    return _MESES.get(v) or int(v)


def leer_csv(texto: str) -> list[GastoIn]:
    """Convierte el CSV en filas validadas. Lanza ValueError con la línea si algo no calza."""
    texto = texto.lstrip("﻿")
    muestra = texto[:2000]
    delimitador = ";" if muestra.count(";") > muestra.count(",") else ","
    lector = csv.DictReader(io.StringIO(texto), delimiter=delimitador)
    columnas = {c: _ALIAS_COLUMNAS.get((c or "").strip().lower()) for c in lector.fieldnames or []}
    if not {"anno", "mes", "categoria", "monto"} <= set(columnas.values()):
        raise ValueError("El CSV debe tener las columnas anno, mes, categoria y monto (y diputado_id o diputado)")
    filas = []
    for n, fila in enumerate(lector, start=2):
        d = {columnas[k]: (v or "").strip() for k, v in fila.items() if columnas.get(k)}
        if not any(d.values()):
            continue
        try:
            filas.append(GastoIn(
                diputado_id=int(d["diputado_id"]) if d.get("diputado_id") else None,
                diputado=d.get("diputado") or None, anno=int(d["anno"]), mes=_mes(d["mes"]),
                tipo=(d.get("tipo") or "operacionales").lower(), categoria=d["categoria"], monto=_monto(d["monto"])))
        except Exception as e:
            raise ValueError(f"Línea {n}: {e}") from None
    return filas


def importar(db: Session, filas: list[GastoIn], fuente: str | None = None) -> dict:
    """Guarda las filas; por cada diputado, mes y tipo reemplaza lo que había."""
    indice = None
    validos = {p.external_id for p in db.query(Parliamentarian.external_id).filter(Parliamentarian.chamber == "diputado")}
    grupos: dict[tuple, dict[str, int]] = defaultdict(dict)
    sin_identificar: list[str] = []
    for f in filas:
        dip = f.diputado_id if f.diputado_id in validos else None
        if dip is None and f.diputado:
            indice = indice or IndiceDiputados(db)
            dip = indice.buscar(nombre=f.diputado)
        if dip is None:
            etiqueta = f.diputado or str(f.diputado_id)
            if etiqueta not in sin_identificar:
                sin_identificar.append(etiqueta)
            continue
        categoria = re.sub(r"\s+", " ", f.categoria).strip().upper()
        clave = (dip, f.anno, f.mes, f.tipo)
        grupos[clave][categoria] = grupos[clave].get(categoria, 0) + f.monto

    ahora = datetime.now()
    for (dip, anno, mes, tipo), categorias in grupos.items():
        db.query(GastoParlamentario).filter_by(diputado_id=dip, anno=anno, mes=mes, tipo=tipo).delete()
        for categoria, monto in categorias.items():
            db.add(GastoParlamentario(diputado_id=dip, anno=anno, mes=mes, tipo=tipo, categoria=categoria,
                                      monto=monto, fuente=fuente, cargado_at=ahora))
    db.commit()
    return {"filas": sum(len(c) for c in grupos.values()), "meses": len(grupos), "sin_identificar": sin_identificar}
