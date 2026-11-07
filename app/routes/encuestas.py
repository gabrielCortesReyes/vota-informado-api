"""Encuestas configurables. Por defecto existe 'Evaluación de tu parlamentario':
¿Votaste por él/ella?, ¿Volverías a votar por él/ella?, ¿Apruebas su desempeño?"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Parliamentarian, Survey, SurveyAnswer, SurveyQuestion, User
from app.schemas import SurveyIn, SurveyOut, SurveyResponseIn
from app.security import current_user

router = APIRouter(prefix="/encuestas", tags=["Encuestas"])

SI = {"si", "sí", "true", "1", "yes"}
NO = {"no", "false", "0"}

ENCUESTA_POR_DEFECTO = SurveyIn(
    title="Evaluación de tu parlamentario",
    description="Cuéntanos tu relación con este diputado o senador.",
    per_parliamentarian=True,
    questions=[
        {"text": "¿Votaste por él/ella?", "kind": "si_no"},
        {"text": "¿Volverías a votar por él/ella?", "kind": "si_no"},
        {"text": "¿Apruebas su desempeño?", "kind": "si_no", "required": False},
    ],
)


def crear_encuesta(db: Session, data: SurveyIn) -> Survey:
    survey = Survey(title=data.title, description=data.description,
                    per_parliamentarian=data.per_parliamentarian, active=data.active)
    for i, q in enumerate(data.questions):
        if q.kind == "opcion" and not q.options:
            raise HTTPException(status_code=422, detail=f"La pregunta '{q.text}' necesita opciones")
        survey.questions.append(SurveyQuestion(position=i, text=q.text, kind=q.kind,
                                               options=q.options if q.kind == "opcion" else None,
                                               required=q.required))
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return survey


def asegurar_encuesta_por_defecto(db: Session) -> None:
    if db.query(Survey).count() == 0:
        crear_encuesta(db, ENCUESTA_POR_DEFECTO)


def _survey(db: Session, survey_id: int, solo_activas: bool = True) -> Survey:
    s = db.query(Survey).options(selectinload(Survey.questions)).filter(Survey.id == survey_id).first()
    if not s or (solo_activas and not s.active):
        raise HTTPException(status_code=404, detail="Encuesta no encontrada")
    return s


def _normalizar(q: SurveyQuestion, valor: str) -> str:
    v = valor.strip()
    if q.kind == "si_no":
        if v.lower() in SI:
            return "si"
        if v.lower() in NO:
            return "no"
        raise HTTPException(status_code=422, detail=f"'{q.text}': responde si o no")
    if q.kind == "escala":
        if v not in {"1", "2", "3", "4", "5"}:
            raise HTTPException(status_code=422, detail=f"'{q.text}': responde un número de 1 a 5")
        return v
    if q.kind == "opcion":
        if v not in (q.options or []):
            raise HTTPException(status_code=422, detail=f"'{q.text}': opciones válidas {q.options}")
        return v
    return v


def _check_parl(db: Session, survey: Survey, parl_id: Optional[int]) -> Optional[int]:
    if survey.per_parliamentarian:
        if not parl_id or not db.get(Parliamentarian, parl_id):
            raise HTTPException(status_code=422, detail="Indica un parliamentarian_id válido")
        return parl_id
    return None


@router.get("", response_model=list[SurveyOut], summary="Encuestas activas")
def listar(db: Session = Depends(get_db)):
    return db.query(Survey).options(selectinload(Survey.questions)).filter(Survey.active.is_(True)).order_by(Survey.id).all()


@router.get("/{survey_id}", response_model=SurveyOut)
def ver(survey_id: int, db: Session = Depends(get_db)):
    return _survey(db, survey_id)


@router.post("/{survey_id}/respuestas", summary="Responder (volver a responder reemplaza tus respuestas)")
def responder(survey_id: int, body: SurveyResponseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    survey = _survey(db, survey_id)
    parl_id = _check_parl(db, survey, body.parliamentarian_id)
    preguntas = {q.id: q for q in survey.questions}

    for a in body.answers:
        q = preguntas.get(a.question_id)
        if not q:
            raise HTTPException(status_code=422, detail=f"La pregunta {a.question_id} no es de esta encuesta")
        valor = _normalizar(q, a.value)
        prev = db.query(SurveyAnswer).filter_by(question_id=q.id, user_id=user.id, parliamentarian_id=parl_id).first()
        if prev:
            prev.value = valor
        else:
            db.add(SurveyAnswer(question_id=q.id, user_id=user.id, parliamentarian_id=parl_id, value=valor))
    db.flush()

    respondidas = {r.question_id for r in db.query(SurveyAnswer.question_id).filter(
        SurveyAnswer.user_id == user.id, SurveyAnswer.parliamentarian_id == parl_id,
        SurveyAnswer.question_id.in_(preguntas))}
    faltan = [q.text for q in survey.questions if q.required and q.id not in respondidas]
    if faltan:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Faltan respuestas obligatorias: {faltan}")
    db.commit()
    return {"message": "Respuestas guardadas", "survey_id": survey.id, "parliamentarian_id": parl_id}


@router.get("/{survey_id}/mis-respuestas")
def mis_respuestas(survey_id: int, parliamentarian_id: Optional[int] = None,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    survey = _survey(db, survey_id, solo_activas=False)
    ids = [q.id for q in survey.questions]
    rows = db.query(SurveyAnswer).filter(SurveyAnswer.user_id == user.id, SurveyAnswer.question_id.in_(ids),
                                         SurveyAnswer.parliamentarian_id == (parliamentarian_id if survey.per_parliamentarian else None))
    return {r.question_id: r.value for r in rows}


@router.get("/{survey_id}/resultados", summary="Resultados agregados (opcional: de un parlamentario)")
def resultados(survey_id: int, parliamentarian_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    survey = _survey(db, survey_id, solo_activas=False)
    salida = []
    for q in survey.questions:
        query = db.query(SurveyAnswer.value).filter(SurveyAnswer.question_id == q.id)
        if parliamentarian_id:
            query = query.filter(SurveyAnswer.parliamentarian_id == parliamentarian_id)
        valores = [v for (v,) in query]
        item = {"question_id": q.id, "text": q.text, "kind": q.kind, "respuestas": len(valores)}
        if q.kind == "si_no":
            si = valores.count("si")
            item.update(si=si, no=len(valores) - si,
                        porcentaje_si=round(100 * si / len(valores), 1) if valores else None)
        elif q.kind == "escala":
            nums = [int(v) for v in valores]
            item.update(promedio=round(sum(nums) / len(nums), 2) if nums else None,
                        distribucion={i: nums.count(i) for i in range(1, 6)})
        elif q.kind == "opcion":
            item["conteo"] = {o: valores.count(o) for o in (q.options or [])}
        salida.append(item)  # las respuestas de texto libre no se publican
    return {"survey_id": survey.id, "title": survey.title, "parliamentarian_id": parliamentarian_id, "preguntas": salida}
