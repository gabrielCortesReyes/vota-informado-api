"""Padrón de diputados y senadores (guardado en la base), con comentarios y calificaciones."""
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.distritos import diputados_del_distrito
from app.models import Comment, Parliamentarian, Party, Rating, User
from app.schemas import (CommentIn, CommentOut, ParliamentarianDetail, ParliamentarianOut, PartyWithCount,
                         RatingIn, RatingOut, RatingSummary)
from app.security import current_user, decode, get_active_user

router = APIRouter(tags=["Parlamentarios"])
optional_bearer = HTTPBearer(auto_error=False)


def _get(db: Session, parl_id: int) -> Parliamentarian:
    p = db.get(Parliamentarian, parl_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlamentario no encontrado")
    return p


def _rating_summary(db: Session, parl_id: int) -> RatingSummary:
    rows = db.query(Rating.score, func.count()).filter(Rating.parliamentarian_id == parl_id).group_by(Rating.score).all()
    dist = {i: 0 for i in range(1, 6)}
    dist.update({score: n for score, n in rows})
    total = sum(dist.values())
    promedio = round(sum(s * n for s, n in dist.items()) / total, 2) if total else None
    return RatingSummary(promedio=promedio, cantidad=total, distribucion=dist)


def _optional_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer),
                   db: Session = Depends(get_db)) -> Optional[User]:
    if not creds:
        return None
    return get_active_user(db, int(decode(creds.credentials, "access")["sub"]))


# --- Padrón -------------------------------------------------------------------------

@router.get("/partidos", response_model=list[PartyWithCount], summary="Partidos con su número de parlamentarios en ejercicio")
def partidos(db: Session = Depends(get_db)):
    conteo = dict()
    for party_id, chamber, n in (db.query(Parliamentarian.party_id, Parliamentarian.chamber, func.count())
                                 .filter(Parliamentarian.current.is_(True))
                                 .group_by(Parliamentarian.party_id, Parliamentarian.chamber)):
        conteo[(party_id, chamber)] = n
    return [
        PartyWithCount.model_validate(p).model_copy(update={
            "diputados": conteo.get((p.id, "diputado"), 0), "senadores": conteo.get((p.id, "senador"), 0)})
        for p in db.query(Party).order_by(Party.name)
    ]


@router.get("/distritos/{distrito}/diputados", response_model=list[ParliamentarianOut],
            summary="Diputados en ejercicio de un distrito (1 a 28)")
def diputados_distrito(distrito: int = Path(..., ge=1, le=60), db: Session = Depends(get_db)):
    return diputados_del_distrito(db, distrito)


@router.get("/parlamentarios", response_model=list[ParliamentarianOut], summary="Buscar diputados y senadores")
def listar(
    camara: Optional[Literal["diputado", "senador"]] = None,
    partido_id: Optional[int] = None,
    distrito: Optional[int] = Query(None, description="Número de distrito (diputados) o circunscripción (senadores)"),
    q: Optional[str] = Query(None, description="Texto en nombre o apellidos"),
    solo_vigentes: bool = True,
    limit: int = Query(200, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(Parliamentarian).options(joinedload(Parliamentarian.party))
    if camara:
        query = query.filter(Parliamentarian.chamber == camara)
    if partido_id:
        query = query.filter(Parliamentarian.party_id == partido_id)
    if distrito:
        query = query.filter(Parliamentarian.district == str(distrito))
    if solo_vigentes:
        query = query.filter(Parliamentarian.current.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Parliamentarian.first_name.ilike(like), Parliamentarian.last_name.ilike(like),
                                 Parliamentarian.second_last_name.ilike(like)))
    return query.order_by(Parliamentarian.last_name, Parliamentarian.first_name).offset(offset).limit(limit).all()


@router.get("/parlamentarios/{parl_id}", response_model=ParliamentarianDetail,
            summary="Ficha: datos, descripción de Wikipedia, calificación promedio y número de comentarios")
def detalle(parl_id: int, db: Session = Depends(get_db)):
    p = _get(db, parl_id)
    data = ParliamentarianDetail.model_validate(p)
    data.calificacion = _rating_summary(db, parl_id)
    data.comentarios = db.query(Comment).filter(Comment.parliamentarian_id == parl_id, Comment.deleted_at.is_(None),
                                                Comment.hidden.is_(False)).count()
    if p.description:
        data.licencia_descripcion = "Texto de Wikipedia, licencia CC BY-SA 4.0"
    return data


# --- Comentarios ------------------------------------------------------------------------

def _comment_out(c: Comment, viewer: Optional[User]) -> CommentOut:
    return CommentOut(id=c.id, parliamentarian_id=c.parliamentarian_id, autor=c.user.display_name,
                      es_mio=bool(viewer and viewer.id == c.user_id), text=c.text,
                      created_at=c.created_at, updated_at=c.updated_at)


@router.get("/parlamentarios/{parl_id}/comentarios", response_model=list[CommentOut], tags=["Comentarios"])
def ver_comentarios(parl_id: int, limit: int = Query(50, le=200), offset: int = 0,
                    db: Session = Depends(get_db), viewer: Optional[User] = Depends(_optional_user)):
    _get(db, parl_id)
    rows = (db.query(Comment).options(joinedload(Comment.user))
            .filter(Comment.parliamentarian_id == parl_id, Comment.deleted_at.is_(None), Comment.hidden.is_(False))
            .order_by(Comment.created_at.desc(), Comment.id.desc()).offset(offset).limit(limit).all())
    return [_comment_out(c, viewer) for c in rows]


@router.post("/parlamentarios/{parl_id}/comentarios", response_model=CommentOut, status_code=201, tags=["Comentarios"])
def comentar(parl_id: int, body: CommentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _get(db, parl_id)
    c = Comment(user_id=user.id, parliamentarian_id=parl_id, text=body.text.strip())
    db.add(c)
    db.commit()
    db.refresh(c)
    return _comment_out(c, user)


def _own_comment(db: Session, comment_id: int, user: User) -> Comment:
    c = db.get(Comment, comment_id)
    if not c or c.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    if c.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Solo puedes modificar tus comentarios")
    return c


@router.put("/comentarios/{comment_id}", response_model=CommentOut, tags=["Comentarios"])
def editar_comentario(comment_id: int, body: CommentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _own_comment(db, comment_id, user)
    c.text = body.text.strip()
    db.commit()
    db.refresh(c)
    return _comment_out(c, user)


@router.delete("/comentarios/{comment_id}", status_code=204, tags=["Comentarios"])
def borrar_comentario(comment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _own_comment(db, comment_id, user)
    c.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)


# --- Calificaciones -----------------------------------------------------------------------

@router.put("/parlamentarios/{parl_id}/calificacion", response_model=RatingOut, tags=["Calificaciones"],
            summary="Calificar de 1 a 5 (una por usuario; volver a enviar la reemplaza)")
def calificar(parl_id: int, body: RatingIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _get(db, parl_id)
    r = db.query(Rating).filter_by(user_id=user.id, parliamentarian_id=parl_id).first()
    if r:
        r.score = body.score
    else:
        db.add(Rating(user_id=user.id, parliamentarian_id=parl_id, score=body.score))
    db.commit()
    return RatingOut(parliamentarian_id=parl_id, score=body.score, resumen=_rating_summary(db, parl_id))


@router.get("/parlamentarios/{parl_id}/calificacion", response_model=RatingSummary, tags=["Calificaciones"])
def ver_calificacion(parl_id: int, db: Session = Depends(get_db)):
    _get(db, parl_id)
    return _rating_summary(db, parl_id)


@router.get("/parlamentarios/{parl_id}/calificacion/mia", tags=["Calificaciones"])
def mi_calificacion(parl_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.query(Rating).filter_by(user_id=user.id, parliamentarian_id=parl_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Aún no calificas a este parlamentario")
    return {"parliamentarian_id": parl_id, "score": r.score}
