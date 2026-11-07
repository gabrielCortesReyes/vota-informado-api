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
    _agregar_columnas_nuevas()
    # parties.external_id pasó de entero a texto: la Cámara usa siglas como "PC" o "IND"
    if engine.dialect.name == "postgresql":
        columnas = {c["name"]: c for c in inspect(engine).get_columns("parties")}
        if "external_id" in columnas and columnas["external_id"]["type"].python_type is int:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE parties ALTER COLUMN external_id TYPE VARCHAR(20) USING external_id::text"))


def _agregar_columnas_nuevas() -> None:
    """create_all no agrega columnas a tablas que ya existen: se agregan aquí (siempre opcionales)."""
    insp = inspect(engine)
    tablas = set(insp.get_table_names())
    for tabla in Base.metadata.sorted_tables:
        if tabla.name not in tablas:
            continue
        existentes = {c["name"] for c in insp.get_columns(tabla.name)}
        for col in tabla.columns:
            if col.name in existentes or not col.nullable:
                continue
            tipo = col.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                si_falta = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""  # varias instancias a la vez
                conn.execute(text(f"ALTER TABLE {tabla.name} ADD COLUMN {si_falta}{col.name} {tipo}"))
