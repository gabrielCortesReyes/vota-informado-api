"""Vota Informado API.

- Datos parlamentarios: se consultan en vivo a los web services de la Cámara
  (opendata.camara.cl) y se entregan en JSON, con caché en memoria.
- En la base: usuarios, padrón de diputados/senadores y partidos (Cámara, Senado y Wikipedia),
  comentarios, calificaciones y encuestas.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.camara import client
from app.database import Base, SessionLocal, engine
from app.routes import admin, auth, catalogos, diputados, encuestas, legislativo, parlamentarios, sala, usuario
from app.security import current_user, limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # crea las tablas que falten
    db = SessionLocal()
    try:
        encuestas.asegurar_encuesta_por_defecto(db)
    finally:
        db.close()
    yield
    await client.cerrar()


app = FastAPI(
    title="Vota Informado API",
    version="3.0.0",
    description=(
        "Información de la Cámara de Diputadas y Diputados de Chile en JSON: proyectos de ley, "
        "votaciones, diputados, sesiones, asistencia y catálogos. Fuente: "
        "[opendata.camara.cl](https://opendata.camara.cl/). Además: cuentas de usuario, padrón de diputados y "
        "senadores con descripción de Wikipedia, comentarios, calificaciones y encuestas."
    ),
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (legislativo, diputados, sala, catalogos, parlamentarios, encuestas, auth, usuario, admin):
    app.include_router(r.router)


@app.get("/", tags=["General"])
async def root():
    return {"api": "Vota Informado", "version": app.version, "docs": "/docs", "fuente": "https://opendata.camara.cl/"}


@app.get("/health", tags=["General"])
async def health():
    return {"status": "ok"}


@app.post("/cache/clear", tags=["General"], summary="Vacía la caché de respuestas de la Cámara (requiere token)")
async def clear_cache(_=Depends(current_user)):
    return {"eliminadas": client.limpiar_cache()}
