"""Materias de los proyectos de ley como categorías: proyectos por materia, intereses del usuario
y en qué materias presenta mociones cada diputado (datos guardados por app/proyectos.py)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Materia, Parliamentarian, ProyectoAutor, ProyectoLey, ProyectoMateria, User, UserMateria
from app.proyectos import materias_con_proyectos, materias_de_diputado, proyectos_de_materias
from app.security import current_user

router = APIRouter(tags=["Materias"])


class MateriaOut(BaseModel):
    id: int
    nombre: str
    proyectos: int = 0


class MateriasDiputadoOut(BaseModel):
    parlamentario_id: int
    mociones: int = Field(description="Mociones de las que es autor (guardadas en la base)")
    con_materia: int = Field(description="De ellas, cuántas tienen materia asignada por la Cámara")
    materias: list[MateriaOut]


class MisMateriasIn(BaseModel):
    materia_ids: list[int] = Field(max_length=50)


def proyecto_json(p: ProyectoLey) -> dict:
    """Mismo formato que los listados de la Cámara (/proyectos/mociones), más materias y autores."""
    return {
        "Id": p.id, "NumeroBoletin": p.boletin, "Nombre": p.nombre,
        "FechaIngreso": p.fecha_ingreso.isoformat() if p.fecha_ingreso else None,
        "TipoIniciativa": {"Nombre": p.tipo} if p.tipo else None,
        "CamaraOrigen": {"Nombre": p.camara_origen} if p.camara_origen else None,
        "Materias": [{"Id": m.id, "Nombre": m.nombre} for m in p.materias],
        "Autores": [{"Orden": a.orden, "Diputado": {"Id": a.diputado_id}} for a in p.autores],
    }


def _con_relaciones(proyectos: list[ProyectoLey]) -> list[dict]:
    return [proyecto_json(p) for p in proyectos]


@router.get("/materias/categorias", response_model=list[MateriaOut],
            summary="Materias que tienen proyectos, con cuántos (de la más a la menos usada)")
def categorias(db: Session = Depends(get_db)):
    filas = materias_con_proyectos(db)
    if filas:
        return filas
    # aún sin proyectos cargados: el catálogo completo
    return [{"id": m.id, "nombre": m.nombre, "proyectos": 0} for m in db.query(Materia).order_by(Materia.nombre)]


@router.get("/materias/{materia_id}/proyectos", summary="Proyectos de ley de una materia, del más reciente al más antiguo")
def proyectos_materia(materia_id: int, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                      db: Session = Depends(get_db)):
    if not db.get(Materia, materia_id):
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return _con_relaciones(proyectos_de_materias(db, [materia_id], limit, offset))


@router.get("/parlamentarios/{parl_id}/materias", response_model=MateriasDiputadoOut, tags=["Parlamentarios"],
            summary="En qué materias presenta mociones un diputado")
def materias_parlamentario(parl_id: int, desde: date | None = Query(None, description="Solo mociones desde esta fecha"),
                           limite: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    if p.chamber != "diputado":  # los senadores no vienen como autores en WSLegislativo
        return {"parlamentario_id": p.id, "mociones": 0, "con_materia": 0, "materias": []}
    return {"parlamentario_id": p.id, **materias_de_diputado(db, p.external_id, desde, limite)}


@router.get("/parlamentarios/{parl_id}/mociones", tags=["Parlamentarios"],
            summary="Mociones de las que es autor un diputado (opcional: de una materia)")
def mociones_parlamentario(parl_id: int, materia: int | None = None, limit: int = Query(30, ge=1, le=100),
                           offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    q = (db.query(ProyectoLey).join(ProyectoAutor, ProyectoAutor.proyecto_id == ProyectoLey.id)
         .filter(ProyectoAutor.diputado_id == p.external_id, p.chamber == "diputado"))
    if materia:
        q = q.join(ProyectoMateria, ProyectoMateria.proyecto_id == ProyectoLey.id).filter(
            ProyectoMateria.materia_id == materia)
    q = q.options(selectinload(ProyectoLey.materias), selectinload(ProyectoLey.autores))
    return _con_relaciones(q.order_by(ProyectoLey.fecha_ingreso.desc()).offset(offset).limit(limit).all())


# --- Intereses del usuario --------------------------------------------------------------

def _mis_materias(db: Session, user: User) -> list[Materia]:
    return (db.query(Materia).join(UserMateria, UserMateria.materia_id == Materia.id)
            .filter(UserMateria.user_id == user.id).order_by(Materia.nombre).all())


@router.get("/user/me/materias", response_model=list[MateriaOut], tags=["Usuario"],
            summary="Materias que me interesan")
def mis_materias(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": m.id, "nombre": m.nombre} for m in _mis_materias(db, user)]


@router.put("/user/me/materias", response_model=list[MateriaOut], tags=["Usuario"],
            summary="Reemplazar las materias que me interesan")
def guardar_mis_materias(body: MisMateriasIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = set(body.materia_ids)
    existentes = {m for (m,) in db.query(Materia.id).filter(Materia.id.in_(ids))} if ids else set()
    if ids - existentes:
        raise HTTPException(status_code=422, detail=f"Materias que no existen: {sorted(ids - existentes)}")
    db.query(UserMateria).filter(UserMateria.user_id == user.id).delete(synchronize_session=False)
    db.add_all(UserMateria(user_id=user.id, materia_id=i) for i in ids)
    db.commit()
    return [{"id": m.id, "nombre": m.nombre} for m in _mis_materias(db, user)]


@router.get("/user/me/materias/proyectos", tags=["Usuario"],
            summary="Proyectos de ley de las materias que me interesan, del más reciente al más antiguo")
def proyectos_de_mis_materias(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                              user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = [m.id for m in _mis_materias(db, user)]
    return _con_relaciones(proyectos_de_materias(db, ids, limit, offset)) if ids else []
