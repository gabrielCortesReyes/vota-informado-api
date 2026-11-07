"""Tablas propias de la app.

Los datos legislativos (proyectos, votaciones, sesiones) se leen en vivo de la Cámara.
En la base se guardan: usuarios, un padrón de parlamentarios y partidos (para poder
comentarlos, calificarlos y asociarles encuestas) y la participación de los usuarios.
"""
from sqlalchemy import (JSON, TIMESTAMP, Boolean, CheckConstraint, Column, ForeignKey, Integer, String, Text,
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

    user = relationship("User", back_populates="preferences")


# --- Padrón de parlamentarios -----------------------------------------------------

class Party(Base):
    __tablename__ = "parties"

    id = Column(Integer, primary_key=True)
    external_id = Column(Integer, nullable=True, unique=True)  # Id del partido en la Cámara
    name = Column(String(150), nullable=False, unique=True)
    alias = Column(String(30), nullable=True)

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
