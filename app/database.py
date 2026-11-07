from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crea las tablas que falten y aplica los cambios de esquema que create_all no hace."""
    from app import models  # noqa: F401  (registra los modelos en Base)

    Base.metadata.create_all(bind=engine)
    # parties.external_id pasó de entero a texto: la Cámara usa siglas como "PC" o "IND"
    if engine.dialect.name == "postgresql":
        columnas = {c["name"]: c for c in inspect(engine).get_columns("parties")}
        if "external_id" in columnas and columnas["external_id"]["type"].python_type is int:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE parties ALTER COLUMN external_id TYPE VARCHAR(20) USING external_id::text"))
