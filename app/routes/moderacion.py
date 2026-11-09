"""Reportar comentarios y bloquear usuarios (contenido generado por usuarios, App Store 1.2)."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Comment, CommentReport, User, UserBlock
from app.schemas import BlockedUserOut, CommentReportIn
from app.security import current_user, get_active_user

router = APIRouter(tags=["Moderación"])

# Con estos reportes pendientes de distintos usuarios el comentario se oculta hasta que un admin lo revise
REPORTES_PARA_OCULTAR = 3


@router.post("/comentarios/{comment_id}/reportar", status_code=201,
             summary="Reportar un comentario (spam, ofensivo, acoso u otro); uno por usuario")
def reportar(comment_id: int, body: CommentReportIn, response: Response,
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Comment, comment_id)
    if not c or c.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    if c.user_id == user.id:
        raise HTTPException(status_code=422, detail="No puedes reportar tu propio comentario")
    if db.query(CommentReport).filter_by(comment_id=c.id, reporter_id=user.id).first():
        response.status_code = 200
        return {"comment_id": c.id, "reportado": True, "nuevo": False}

    detalle = (body.detalle or "").strip() or None
    db.add(CommentReport(comment_id=c.id, reporter_id=user.id, motivo=body.motivo, detalle=detalle))
    db.flush()
    pendientes = db.query(CommentReport).filter_by(comment_id=c.id, status="pendiente").count()
    if pendientes >= REPORTES_PARA_OCULTAR:
        c.hidden = True
    db.commit()
    return {"comment_id": c.id, "reportado": True, "nuevo": True}


@router.post("/usuarios/{user_id}/bloquear", status_code=204,
             summary="Bloquear a un usuario: dejas de ver sus comentarios")
def bloquear(user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(status_code=422, detail="No puedes bloquearte a ti mismo")
    if not get_active_user(db, user_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not db.query(UserBlock).filter_by(blocker_id=user.id, blocked_id=user_id).first():
        db.add(UserBlock(blocker_id=user.id, blocked_id=user_id))
        db.commit()
    return Response(status_code=204)


@router.delete("/usuarios/{user_id}/bloquear", status_code=204, summary="Desbloquear a un usuario")
def desbloquear(user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(UserBlock).filter_by(blocker_id=user.id, blocked_id=user_id).delete()
    db.commit()
    return Response(status_code=204)


@router.get("/user/me/bloqueados", response_model=list[BlockedUserOut], tags=["Usuario"],
            summary="Usuarios que bloqueé")
def mis_bloqueados(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (db.query(UserBlock).options(joinedload(UserBlock.blocked)).filter(UserBlock.blocker_id == user.id)
            .order_by(UserBlock.created_at.desc(), UserBlock.id.desc()).all())
    return [BlockedUserOut(id=b.blocked_id, nombre=b.blocked.display_name, bloqueado_at=b.created_at) for b in rows]
