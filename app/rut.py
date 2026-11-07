"""Validación de RUT chileno (módulo 11)."""
import re


def normalizar_rut(rut: str) -> str | None:
    """'12.345.678-5' → '12345678-5'. Retorna None si el RUT no es válido."""
    limpio = re.sub(r"[.\-\s]", "", rut or "").upper()
    if not re.fullmatch(r"\d{7,8}[\dK]", limpio):
        return None
    cuerpo, dv = limpio[:-1], limpio[-1]
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    esperado = 11 - suma % 11
    esperado = "0" if esperado == 11 else "K" if esperado == 10 else str(esperado)
    return f"{int(cuerpo)}-{dv}" if dv == esperado else None
