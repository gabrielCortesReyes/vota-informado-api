"""Tablas propias de la app.

Los datos legislativos (proyectos, votaciones, sesiones) se leen en vivo de la Cámara.
En la base se guardan: usuarios, un padrón de parlamentarios y partidos (para poder
comentarlos, calificarlos y asociarles encuestas) y la participación de los usuarios.
"""
from sqlalchemy import (JSON, TIMESTAMP, Boolean, CheckConstraint, Column, Date, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import relationship

from app.database import Base



# --- Usuarios -------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fiscal_id = Column(String, nullable=False, unique=True)  # RUT normalizado: 12345678-5
    names = Column(String, nullable=False)
    last_names = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    nationality = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    photo_url = Column(String(200), nullable=True)
    date_of_birth = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
    modified_at = Column(TIMESTAMP, nullable=True, onupdate=func.current_timestamp())
    deleted_at = Column(TIMESTAMP, nullable=True)

    credentials = relationship("UserCredentials", back_populates="user", uselist=False)
    preferences = relationship("UserPreferences", back_populates="user", uselist=False)

    @property
    def display_name(self) -> str:
        """Nombre público en comentarios: 'Gabriel C.'"""
        first = (self.names or "").split()[0] if self.names else ""
        initial = (self.last_names or " ")[0]
        return f"{first} {initial}.".strip()


class UserCredentials(Base):
    __tablename__ = "user_credentials"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    username = Column(String, nullable=False, unique=True)
    hashed_password = Column(String, nullable=False)
    failed_attempts = Column(Integer, default=0)
    last_failed_attempt = Column(TIMESTAMP, nullable=True)
    has_change_password = Column(Boolean, default=False)  # True = debe cambiar su clave
    password_last_updated_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
    modified_at = Column(TIMESTAMP, nullable=True, onupdate=func.current_timestamp())
    deleted_at = Column(TIMESTAMP, nullable=True)

    user = relationship("User", back_populates="credentials")


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    commune_id = Column(Integer, nullable=True)  # "Numero" de /catalogos/comunas
    # A quién votó en la última elección de diputados de su distrito
    district_vote = Column(String(20), nullable=True)  # "diputado" | "otro" | "no_voto" | "no_responde"
    voted_parliamentarian_id = Column(Integer, ForeignKey("parliamentarians.id"), nullable=True)

    voted_parliamentarian = relationship("Parliamentarian")

    user = relationship("User", back_populates="preferences")


# --- Padrón de parlamentarios -----------------------------------------------------

class Party(Base):
    __tablename__ = "parties"

    id = Column(Integer, primary_key=True)
    external_id = Column(String(20), nullable=True, unique=True)  # Id del partido en la Cámara (sigla, p. ej. "PC")
    name = Column(String(150), nullable=False, unique=True)
    alias = Column(String(30), nullable=True)
    # Logo desde Wikidata (P154) / Wikimedia Commons, con su licencia y autor
    logo_url = Column(String(500), nullable=True)
    logo_license = Column(String(100), nullable=True)
    logo_author = Column(String(300), nullable=True)
    logo_source_url = Column(String(500), nullable=True)
    logo_checked_at = Column(TIMESTAMP, nullable=True)

    parliamentarians = relationship("Parliamentarian", back_populates="party")


class Parliamentarian(Base):
    __tablename__ = "parliamentarians"
    __table_args__ = (UniqueConstraint("chamber", "external_id"),)

    id = Column(Integer, primary_key=True)
    chamber = Column(String(10), nullable=False)          # "diputado" | "senador"
    external_id = Column(Integer, nullable=False)          # Id Cámara (diputados) o PARLID Senado
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    second_last_name = Column(String(100), nullable=True)
    gender = Column(String(20), nullable=True)
    birth_date = Column(String(30), nullable=True)
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=True)
    region = Column(String(150), nullable=True)
    district = Column(String(150), nullable=True)          # distrito o circunscripción
    email = Column(String(150), nullable=True)
    phone = Column(String(50), nullable=True)
    current = Column(Boolean, nullable=False, default=True)  # en ejercicio
    description = Column(Text, nullable=True)              # resumen de Wikipedia
    wikipedia_url = Column(String(300), nullable=True)
    photo_url = Column(String(500), nullable=True)
    wikipedia_checked_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
    updated_at = Column(TIMESTAMP, nullable=True, onupdate=func.current_timestamp())

    party = relationship("Party", back_populates="parliamentarians")

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name, self.second_last_name) if p)


# --- Asistencia a la Sala (WSSala) -------------------------------------------------

class SalaSession(Base):
    """Sesión de Sala de la Cámara (Id = Id de la Cámara)."""
    __tablename__ = "sala_sessions"

    id = Column(Integer, primary_key=True, autoincrement=False)
    numero = Column(Integer, nullable=True)
    fecha_inicio = Column(TIMESTAMP, nullable=False, index=True)
    fecha_termino = Column(TIMESTAMP, nullable=True)
    tipo = Column(String(50), nullable=True)      # Ordinaria | Especial | Congreso Pleno
    estado = Column(String(50), nullable=True)    # Celebrada | Citada
    asistencia_cargada = Column(Boolean, nullable=False, default=False)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    asistencias = relationship("SalaAttendance", back_populates="session", cascade="all, delete-orphan")


class SalaAttendance(Base):
    """Asistencia de un diputado a una sesión, con la justificación si no asistió."""
    __tablename__ = "sala_attendance"
    __table_args__ = (UniqueConstraint("session_id", "diputado_id"),
                      Index("ix_sala_attendance_diputado", "diputado_id", "asistio"))

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sala_sessions.id", ondelete="CASCADE"), nullable=False)
    diputado_id = Column(Integer, nullable=False)  # Id de la Cámara = Parliamentarian.external_id
    asistio = Column(Boolean, nullable=False)
    tipo_asistencia = Column(String(50), nullable=True)   # "Asiste" | "No Asiste"
    justificacion_id = Column(Integer, nullable=True)
    justificacion = Column(String(200), nullable=True)    # p. ej. "Licencia médica (Art. 42)"
    rebaja_asistencia = Column(Boolean, nullable=True)
    rebaja_quorum = Column(Boolean, nullable=True)

    session = relationship("SalaSession", back_populates="asistencias")


# --- Patrimonio (InfoProbidad) ------------------------------------------------------

class PatrimonioDeclaracion(Base):
    """Declaración de intereses y patrimonio (Id = Id de InfoProbidad), con el JSON original y un resumen."""
    __tablename__ = "patrimonio_declaraciones"

    id = Column(Integer, primary_key=True, autoincrement=False)
    parliamentarian_id = Column(Integer, ForeignKey("parliamentarians.id", ondelete="CASCADE"), nullable=False, index=True)
    fecha = Column(Date, nullable=True)          # Fecha_de_la_Declaracion
    tipo = Column(String(200), nullable=True)    # p. ej. "Actualización Periódica (Marzo)"
    datos = Column(JSON, nullable=False)         # JSON completo de InfoProbidad
    resumen = Column(JSON, nullable=False)       # app.patrimonio.normalizar(datos)
    cargada_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)


# --- Proyectos de ley y sus materias (WSLegislativo) ----------------------------------

class Materia(Base):
    """Materia legislativa de la Cámara (Id = Id de la Cámara): funciona como categoría de los proyectos."""
    __tablename__ = "materias"

    id = Column(Integer, primary_key=True, autoincrement=False)
    nombre = Column(String(300), nullable=False)


class ProyectoLey(Base):
    """Moción o mensaje (Id = Id de la Cámara), con sus materias y autores tomados del detalle."""
    __tablename__ = "proyectos_ley"

    id = Column(Integer, primary_key=True, autoincrement=False)
    boletin = Column(String(20), nullable=False, unique=True)
    nombre = Column(Text, nullable=False)
    fecha_ingreso = Column(TIMESTAMP, nullable=True, index=True)
    tipo = Column(String(30), nullable=True)            # "Moción" | "Mensaje"
    camara_origen = Column(String(50), nullable=True)
    detalle_cargado_at = Column(TIMESTAMP, nullable=True)  # None = falta pedir materias y autores

    materias = relationship("Materia", secondary="proyecto_materias", order_by="Materia.nombre")
    autores = relationship("ProyectoAutor", cascade="all, delete-orphan", order_by="ProyectoAutor.orden")


class ProyectoMateria(Base):
    __tablename__ = "proyecto_materias"

    proyecto_id = Column(Integer, ForeignKey("proyectos_ley.id", ondelete="CASCADE"), primary_key=True)
    materia_id = Column(Integer, ForeignKey("materias.id", ondelete="CASCADE"), primary_key=True, index=True)


class ProyectoAutor(Base):
    """Diputado autor de una moción (diputado_id = Id de la Cámara = Parliamentarian.external_id)."""
    __tablename__ = "proyecto_autores"

    proyecto_id = Column(Integer, ForeignKey("proyectos_ley.id", ondelete="CASCADE"), primary_key=True)
    diputado_id = Column(Integer, primary_key=True, index=True)
    orden = Column(Integer, nullable=True)


class ProyectoResumen(Base):
    """Explicación en lenguaje simple de un proyecto, escrita a mano por un administrador (sin IA)."""
    __tablename__ = "proyecto_resumenes"

    boletin = Column(String(20), primary_key=True)
    en_una_frase = Column(Text, nullable=False)
    que_cambia = Column(JSON, nullable=False)             # lista de textos
    a_quien_afecta = Column(JSON, nullable=False)         # lista de textos
    cifras = Column(JSON, nullable=False)                 # [{"etiqueta", "valor"}]
    fuente_url = Column(Text, nullable=True)
    fuente_nombre = Column(String(200), nullable=True)
    actualizado_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                            onupdate=func.current_timestamp(), nullable=False)


class UserMateria(Base):
    """Materias que le interesan a un usuario."""
    __tablename__ = "user_materias"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    materia_id = Column(Integer, ForeignKey("materias.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)


# --- Participación ------------------------------------------------------------------

class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    parliamentarian_id = Column(Integer, ForeignKey("parliamentarians.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    hidden = Column(Boolean, nullable=False, default=False)  # moderado por un admin
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
    updated_at = Column(TIMESTAMP, nullable=True, onupdate=func.current_timestamp())
    deleted_at = Column(TIMESTAMP, nullable=True)

    user = relationship("User")
    parliamentarian = relationship("Parliamentarian")


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "parliamentarian_id"),
        CheckConstraint("score BETWEEN 1 AND 5", name="ck_rating_score"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    parliamentarian_id = Column(Integer, ForeignKey("parliamentarians.id"), nullable=False, index=True)
    score = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
    updated_at = Column(TIMESTAMP, nullable=True, onupdate=func.current_timestamp())


class Survey(Base):
    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    per_parliamentarian = Column(Boolean, nullable=False, default=True)  # se responde por cada parlamentario
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)

    questions = relationship("SurveyQuestion", back_populates="survey", order_by="SurveyQuestion.position",
                             cascade="all, delete-orphan")


class SurveyQuestion(Base):
    __tablename__ = "survey_questions"

    id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    text = Column(String(300), nullable=False)
    kind = Column(String(20), nullable=False)  # si_no | escala | opcion | texto
    options = Column(JSON, nullable=True)      # solo para "opcion"
    required = Column(Boolean, nullable=False, default=True)

    survey = relationship("Survey", back_populates="questions")


class SurveyAnswer(Base):
    __tablename__ = "survey_answers"
    __table_args__ = (UniqueConstraint("question_id", "user_id", "parliamentarian_id"),)

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("survey_questions.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    parliamentarian_id = Column(Integer, ForeignKey("parliamentarians.id"), nullable=True, index=True)
    value = Column(String(1000), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
    updated_at = Column(TIMESTAMP, nullable=True, onupdate=func.current_timestamp())


# --- Moderación (App Store 1.2: reportar contenido y bloquear usuarios) ---------------

class CommentReport(Base):
    """Denuncia de un comentario; una por usuario y comentario."""
    __tablename__ = "comment_reports"
    __table_args__ = (UniqueConstraint("comment_id", "reporter_id"),)

    id = Column(Integer, primary_key=True)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False, index=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    motivo = Column(String(20), nullable=False)  # spam | ofensivo | acoso | otro
    detalle = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="pendiente")  # pendiente | resuelto | descartado
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)

    comment = relationship("Comment")
    reporter = relationship("User")


class UserBlock(Base):
    """Un usuario deja de ver los comentarios de otro."""
    __tablename__ = "user_blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id"),)

    id = Column(Integer, primary_key=True)
    blocker_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    blocked_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)

    blocked = relationship("User", foreign_keys=[blocked_id])
