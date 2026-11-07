"""Configuración leída desde variables de entorno (o un archivo .env)."""
import logging
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _database_url() -> str:
    """DATABASE_URL > variables PGSQL_* > SQLite local."""
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    host = os.getenv("PGSQL_HOSTNAME")
    if host:
        user = os.getenv("PGSQL_USERNAME", "postgres")
        password = quote_plus(os.getenv("PGSQL_PASSWORD", ""))
        name = os.getenv("PGSQL_DBNAME", "vota_informado")
        port = os.getenv("PGSQL_PORT", "5432")
        if host.startswith("/"):  # socket de Cloud SQL (/cloudsql/proyecto:region:instancia)
            return f"postgresql://{user}:{password}@/{name}?host={host}"
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    return "sqlite:///./vota_informado.db"


DATABASE_URL = _database_url()

SECRET_KEY = os.getenv("APP_SECRET_KEY", "cambia-esta-clave")
ALGORITHM = os.getenv("APP_ALGORITHM", "HS256")
ACCESS_TOKEN_MINUTES = int(os.getenv("APP_ACCESS_TOKEN_EXPIRE", "60"))
REFRESH_TOKEN_DAYS = int(os.getenv("APP_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
if SECRET_KEY == "cambia-esta-clave":
    logger.warning("⚠️ APP_SECRET_KEY no está configurada: no usar así en producción")

CAMARA_BASE_URL = os.getenv("CAMARA_BASE_URL", "https://opendata.camara.cl/camaradiputados/WServices")
CAMARA_TIMEOUT = float(os.getenv("CAMARA_TIMEOUT_SECONDS", "30"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "3600"))  # 1 hora
# Asistencia a la Sala: se cuenta desde el inicio del período legislativo vigente (LVII: 11-03-2026)
PERIODO_INICIO = os.getenv("PERIODO_INICIO", "2026-03-11")
# Las sesiones de los últimos N días se vuelven a leer: la Cámara agrega justificaciones después
ASISTENCIA_RECARGAR_DIAS = int(os.getenv("ASISTENCIA_RECARGAR_DIAS", "30"))

WPP_ENDPOINT = os.getenv("WPP_ENDPOINT")
WPP_TOKEN = os.getenv("WPP_TOKEN")

SENADO_SENADORES_URL = os.getenv("SENADO_SENADORES_URL", "https://tramitacion.senado.cl/wspublico/senadores_vigentes.php")
WIKIPEDIA_API = os.getenv("WIKIPEDIA_API", "https://es.wikipedia.org")
# Wikimedia pide un User-Agent que identifique la app y un contacto (URL o correo del proyecto)
WIKIPEDIA_USER_AGENT = os.getenv("WIKIPEDIA_USER_AGENT", "VotaInformado/3.0 (https://opendata.camara.cl/)")
WIKIDATA_API = os.getenv("WIKIDATA_API", "https://www.wikidata.org/w/api.php")
COMMONS_API = os.getenv("COMMONS_API", "https://commons.wikimedia.org/w/api.php")
