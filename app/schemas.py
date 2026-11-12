from datetime import date, datetime
from typing import Annotated, Literal, Optional

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


DistrictVote = Literal["diputado", "otro", "no_voto", "no_responde"]
VOTO_DESC = ("A quién votó en la última elección de diputados: 'diputado' (uno de los electos, ver "
             "voted_parliamentarian_id), 'otro' (un candidato que no salió), 'no_voto' o 'no_responde'")


class RegisterSchema(BaseModel):
    fiscal_id: str = Field(description="RUT, con o sin puntos", examples=["12.345.678-5"])
    names: str = Field(min_length=1, max_length=100)
    last_names: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", examples=["sample@mail.com"])
    password: str = Field(min_length=8, max_length=72)
    phone_number: Optional[str] = Field(None, examples=["+56912345678"])
    commune_id: Optional[int] = Field(None, description="Numero de /catalogos/comunas")
    district_vote: Optional[DistrictVote] = Field(None, description=VOTO_DESC)
    voted_parliamentarian_id: Optional[int] = Field(None, description="Id (de /parlamentarios) del diputado que votó")


class UserPreferencesSchema(BaseModel):
    """Al guardar, solo se cambian los campos enviados."""
    commune_id: Optional[int] = None
    district_vote: Optional[DistrictVote] = Field(None, description=VOTO_DESC)
    voted_parliamentarian_id: Optional[int] = None

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
    logo_url: Optional[str] = Field(None, description="PNG del logo (Wikimedia Commons)")
    logo_license: Optional[str] = None
    logo_author: Optional[str] = None
    logo_source_url: Optional[str] = Field(None, description="Página del archivo en Commons (autor y licencia)")

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


class AsistenciaResumen(BaseModel):
    """Asistencia a sesiones de Sala (solo diputados). porcentaje = asistencias / sesiones × 100."""
    sesiones: int = 0
    asistencias: int = 0
    inasistencias: int = 0
    justificadas: int = Field(0, description="Inasistencias con una justificación registrada por la Cámara")
    sin_justificar: int = 0
    porcentaje: Optional[float] = Field(None, description="% de asistencia; null si aún no hay sesiones cargadas")
    desde: Optional[date] = Field(None, description="Fecha de la primera sesión considerada")
    hasta: Optional[date] = Field(None, description="Fecha de la última sesión considerada")


class ParliamentarianDetail(ParliamentarianOut):
    birth_date: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    wikipedia_url: Optional[str] = None
    licencia_descripcion: Optional[str] = None
    calificacion: RatingSummary = RatingSummary()
    comentarios: int = 0
    asistencia: Optional[AsistenciaResumen] = Field(None, description="Solo diputados; detalle en /parlamentarios/{id}/asistencia")


class InasistenciaOut(BaseModel):
    sesion_id: int
    numero: Optional[int] = None
    fecha: datetime
    tipo_sesion: Optional[str] = None
    justificada: bool
    justificacion: Optional[str] = Field(None, description="Nombre de la justificación según la Cámara; null si no la hay")
    justificacion_id: Optional[int] = None
    rebaja_asistencia: Optional[bool] = Field(None, description="Marca RebajaAsistencia de la Cámara para esa justificación")
    rebaja_quorum: Optional[bool] = Field(None, description="Marca RebajaQuorum de la Cámara para esa justificación")
    fuente_url: str = Field(description="Dato oficial: WSSala retornarSesionAsistencia de esa sesión")


class JustificacionConteo(BaseModel):
    justificacion: str
    cantidad: int


class AsistenciaDetalle(AsistenciaResumen):
    parlamentario_id: int
    actualizado: Optional[datetime] = None
    por_justificacion: list[JustificacionConteo] = []
    inasistencias_detalle: list[InasistenciaOut] = []
    fuente: str = "Cámara de Diputadas y Diputados, opendata.camara.cl (WSSala)"


class AsistenciaDiputado(AsistenciaResumen):
    parlamentario: ParliamentarianOut


# --- Comentarios y calificaciones ---------------------------------------------------

class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class CommentOut(BaseModel):
    id: int
    parliamentarian_id: int
    autor: str
    autor_id: int  # para bloquearlo: POST /usuarios/{autor_id}/bloquear
    es_mio: bool = False
    text: str
    created_at: datetime
    updated_at: Optional[datetime] = None


ReportReason = Literal["spam", "ofensivo", "acoso", "otro"]


class CommentReportIn(BaseModel):
    motivo: ReportReason
    detalle: Optional[str] = Field(None, max_length=500)


class BlockedUserOut(BaseModel):
    id: int
    nombre: str
    bloqueado_at: datetime


class DeleteAccountIn(BaseModel):
    password: str = Field(description="Contraseña actual, para confirmar")


class CommentReportOut(BaseModel):
    id: int
    comment_id: int
    comentario: str
    autor: str
    autor_id: int
    comentario_oculto: bool
    reportes_del_comentario: int
    motivo: ReportReason
    detalle: Optional[str] = None
    status: str
    created_at: datetime


class ReportDecisionIn(BaseModel):
    status: Literal["resuelto", "descartado"]
    ocultar_comentario: Optional[bool] = Field(None, description="true oculta el comentario; false lo vuelve a mostrar")


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


class PartyLogoIn(BaseModel):
    logo_url: Optional[str] = None
    logo_license: Optional[str] = None
    logo_author: Optional[str] = None
    logo_source_url: Optional[str] = None


class MiDistritoOut(BaseModel):
    commune_id: Optional[int] = None
    distrito: Optional[int] = None
    district_vote: Optional[DistrictVote] = None
    voted_parliamentarian_id: Optional[int] = None
    diputados: list[ParliamentarianOut] = []


# --- Patrimonio (InfoProbidad) ----------------------------------------------------------

class PatrimonioItem(BaseModel):
    titulo: str = Field(..., description="Comuna del inmueble, marca del vehículo, razón social, emisor o acreedor")
    detalle: Optional[str] = None
    monto: Optional[float] = Field(None, description="CLP: avalúo fiscal, valor libro, valor corriente o monto adeudado")
    porcentaje: Optional[float] = Field(None, description="% de propiedad, si se declara")
    fecha_adquisicion: Optional[date] = None


class PatrimonioTotales(BaseModel):
    inmuebles: float = 0
    vehiculos: float = 0
    sociedades: float = 0
    valores: float = 0
    otros_bienes: float = 0
    pasivos: float = 0
    activos: float = 0
    patrimonio_neto: float = Field(0, description="activos − pasivos, con los valores declarados (no comerciales)")


class PatrimonioOut(BaseModel):
    parliamentarian_id: int
    declaracion_id: int
    fecha: Optional[date] = None
    tipo: Optional[str] = None
    fuente_url: str
    licencia: str
    advertencia: str = ("Montos declarados por el propio diputado: avalúo fiscal de inmuebles y vehículos, valor libro "
                        "de sociedades y valor corriente de instrumentos. En bienes compartidos se suma solo su porcentaje. "
                        "El valor comercial suele ser mayor.")
    renta_bruta_mensual: Optional[float] = None
    totales: PatrimonioTotales
    inmuebles: list[PatrimonioItem] = []
    vehiculos: list[PatrimonioItem] = []
    sociedades: list[PatrimonioItem] = []
    valores: list[PatrimonioItem] = []
    otros_bienes: list[PatrimonioItem] = []
    pasivos: list[PatrimonioItem] = []
    datos: Optional[dict] = Field(None, description="JSON original de InfoProbidad (solo con ?incluir_fuente=true)")


class PatrimonioRankingItem(BaseModel):
    parlamentario: ParliamentarianOut
    declaracion_id: int
    fecha: Optional[date] = None
    totales: PatrimonioTotales


# --- Resumen simple de un proyecto ----------------------------------------------------

Punto = Annotated[str, Field(min_length=3, max_length=300)]


class CifraClave(BaseModel):
    etiqueta: str = Field(..., min_length=1, max_length=80, examples=["Gasto total"])
    valor: str = Field(..., min_length=1, max_length=80, examples=["$ 85 billones"])


class ResumenProyectoIn(BaseModel):
    en_una_frase: str = Field(..., min_length=10, max_length=300,
                              examples=["Define cuánto dinero puede gastar el Estado en 2027 y en qué."])
    que_cambia: list[Punto] = Field(..., min_length=1, max_length=6)
    a_quien_afecta: list[Punto] = Field(default_factory=list, max_length=6)
    cifras: list[CifraClave] = Field(default_factory=list, max_length=6)
    fuente_url: Optional[str] = Field(None, max_length=500)
    fuente_nombre: Optional[str] = Field(None, max_length=200)


class ResumenProyectoOut(ResumenProyectoIn):
    boletin: str
    actualizado_at: datetime

    model_config = {"from_attributes": True}
