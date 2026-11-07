from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginSchema(BaseModel):
    username: str = Field(examples=["sample@mail.com"])
    password: str = Field(examples=["123456"])


class RefreshTokenSchema(BaseModel):
    refresh_token: str


class ChangePasswordSchema(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)


class RestorePasswordSchema(BaseModel):
    username: str = Field(examples=["sample@mail.com"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterSchema(BaseModel):
    fiscal_id: str = Field(description="RUT, con o sin puntos", examples=["12.345.678-5"])
    names: str = Field(min_length=1, max_length=100)
    last_names: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", examples=["sample@mail.com"])
    password: str = Field(min_length=8, max_length=72)
    phone_number: Optional[str] = Field(None, examples=["+56912345678"])
    commune_id: Optional[int] = Field(None, description="Numero de /catalogos/comunas")


class UserPreferencesSchema(BaseModel):
    commune_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class UserSchema(BaseModel):
    id: int
    is_admin: bool = False
    fiscal_id: str
    names: str
    last_names: str
    email: Optional[str] = None
    phone_number: Optional[str] = None
    nationality: Optional[str] = None
    photo_url: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    preferences: Optional[UserPreferencesSchema] = None

    model_config = ConfigDict(from_attributes=True)


# --- Parlamentarios ------------------------------------------------------------

class PartyOut(BaseModel):
    id: int
    name: str
    alias: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PartyWithCount(PartyOut):
    diputados: int = 0
    senadores: int = 0


class RatingSummary(BaseModel):
    promedio: Optional[float] = None
    cantidad: int = 0
    distribucion: dict[int, int] = {}


class ParliamentarianOut(BaseModel):
    id: int
    chamber: str
    external_id: int
    full_name: str
    first_name: str
    last_name: str
    second_last_name: Optional[str] = None
    gender: Optional[str] = None
    party: Optional[PartyOut] = None
    region: Optional[str] = None
    district: Optional[str] = None
    current: bool
    photo_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ParliamentarianDetail(ParliamentarianOut):
    birth_date: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    wikipedia_url: Optional[str] = None
    licencia_descripcion: Optional[str] = None
    calificacion: RatingSummary = RatingSummary()
    comentarios: int = 0


# --- Comentarios y calificaciones ---------------------------------------------------

class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class CommentOut(BaseModel):
    id: int
    parliamentarian_id: int
    autor: str
    es_mio: bool = False
    text: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class RatingIn(BaseModel):
    score: int = Field(ge=1, le=5)


class RatingOut(BaseModel):
    parliamentarian_id: int
    score: int
    resumen: RatingSummary


# --- Encuestas ---------------------------------------------------------------------

QuestionKind = Literal["si_no", "escala", "opcion", "texto"]


class QuestionIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    kind: QuestionKind
    options: Optional[list[str]] = None
    required: bool = True


class SurveyIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    per_parliamentarian: bool = True
    active: bool = True
    questions: list[QuestionIn] = Field(min_length=1)


class QuestionOut(QuestionIn):
    id: int
    position: int

    model_config = ConfigDict(from_attributes=True)


class SurveyOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    per_parliamentarian: bool
    active: bool
    questions: list[QuestionOut]

    model_config = ConfigDict(from_attributes=True)


class AnswerIn(BaseModel):
    question_id: int
    value: str = Field(min_length=1, max_length=1000, description="si/no, 1-5, una opción o texto")


class SurveyResponseIn(BaseModel):
    parliamentarian_id: Optional[int] = None
    answers: list[AnswerIn] = Field(min_length=1)
