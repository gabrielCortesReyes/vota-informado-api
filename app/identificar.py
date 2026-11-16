"""Encontrar al diputado del padrón a partir de su nombre.

Algunas fuentes no usan el mismo Id de la Cámara que las votaciones: WSComision identifica a cada
diputado con otro registro (René Alinco es 803 en votaciones y 1489 en comisiones), y los datos de
transparencia pueden venir solo con el nombre. Aquí se busca por apellidos, y si hay más de uno, por el
primer nombre.
"""
import re
import unicodedata

from sqlalchemy.orm import Session

from app.models import Parliamentarian


def norm(texto: str | None) -> str:
    texto = unicodedata.normalize("NFD", (texto or "").upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^A-Z ]+", " ", texto).strip()


def _tokens(texto: str | None) -> list[str]:
    return norm(texto).split()


class IndiceDiputados:
    """Índice de los diputados en ejercicio por apellidos (Parliamentarian.external_id = Id de la Cámara)."""

    def __init__(self, db: Session):
        self.diputados = (db.query(Parliamentarian)
                          .filter(Parliamentarian.chamber == "diputado", Parliamentarian.current.is_(True)).all())
        self.por_apellidos: dict[tuple[str, str], list[Parliamentarian]] = {}
        for p in self.diputados:
            self.por_apellidos.setdefault((norm(p.last_name), norm(p.second_last_name)), []).append(p)

    def buscar(self, nombre: str | None = None, paterno: str | None = None, materno: str | None = None) -> int | None:
        """Id de la Cámara del diputado, o None si no se encuentra o es ambiguo."""
        if paterno:
            primero = (_tokens(nombre) or [""])[0]
            candidatos = self.por_apellidos.get((norm(paterno), norm(materno)), [])
            if not candidatos:  # una de las dos fuentes no trae el apellido materno: se exige el primer nombre
                candidatos = [p for (pat, mat), ps in self.por_apellidos.items()
                              if pat == norm(paterno) and (not mat or not materno) for p in ps
                              if primero and (_tokens(p.first_name) or [""])[0] == primero]
            if len(candidatos) > 1 and primero:
                candidatos = [p for p in candidatos if (_tokens(p.first_name) or [""])[0] == primero]
            return candidatos[0].external_id if len(candidatos) == 1 else None
        return self._por_texto(nombre)

    def _por_texto(self, texto: str | None) -> int | None:
        """«Vergara, Diego», «Diego Vergara Rodríguez» o «VERGARA RODRIGUEZ DIEGO»."""
        palabras = set(_tokens(texto))
        if not palabras:
            return None
        coinciden = []
        for p in self.diputados:
            paterno = _tokens(p.last_name)
            if not paterno or not set(paterno) <= palabras:
                continue
            materno, nombres = set(_tokens(p.second_last_name)), set(_tokens(p.first_name))
            puntaje = 1 + (2 if materno and materno <= palabras else 0) + (1 if nombres & palabras else 0)
            coinciden.append((puntaje, p))
        if not coinciden:
            return None
        coinciden.sort(key=lambda x: -x[0])
        mejor = coinciden[0][0]
        empatados = [p for s, p in coinciden if s == mejor]
        return empatados[0].external_id if len(empatados) == 1 and mejor >= 2 else None
