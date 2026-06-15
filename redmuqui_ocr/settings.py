"""
Configuración del servicio Django de procesamiento OCR para redmuqui.

Este servicio NO expone una API web "principal": su tarea central es
correr el comando `poll_s3`, que revisa periódicamente el bucket S3,
detecta archivos (imágenes o PDFs escaneados) que necesitan convertirse
en PDF editable (con OCR), y sube el resultado al backend de Spring Boot
usando el mismo endpoint que usaría el frontend de React.

Todas las variables sensibles se leen de variables de entorno (ver .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Carga las variables de BASE_DIR/.env al entorno (si el archivo existe).
# No sobreescribe variables que ya estén definidas en el entorno real
# (override=False), así que en systemd con EnvironmentFile=.env esto no
# causa conflicto.
load_dotenv(BASE_DIR / ".env", override=False)


def env_bool(nombre: str, default: bool = False) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return default
    return valor.strip().lower() in {"1", "true", "yes", "si", "sí"}


# --- Configuración básica de Django -----------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "clave-de-desarrollo-cambiame")

DEBUG = env_bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "corsheaders",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "processor",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "redmuqui_ocr.urls"

WSGI_APPLICATION = "redmuqui_ocr.wsgi.application"

# --- Base de datos local (solo para registrar qué archivos ya se procesaron) -

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("OCR_DB_NAME", "ocr_tracker.sqlite3"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "es"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "America/Lima")
USE_I18N = True
USE_TZ = True

# --- Configuración AWS S3 -----------------------------------------------------

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_S3_BUCKET = os.environ["AWS_S3_BUCKET"]

# Prefijo dentro del bucket donde el backend guarda los archivos de documentos.
# Coincide con S3StorageService: "documentos/{documentoId}/{uuid}-{nombre}"
S3_DOCUMENTOS_PREFIX = os.environ.get("S3_DOCUMENTOS_PREFIX", "documentos/")

# Si las credenciales no se ponen aquí, boto3 usará el rol de la instancia EC2
# (LabRole de AWS Academy) automáticamente. Solo defínelas si pruebas en local.
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN")

# --- Configuración del backend Spring Boot ------------------------------------

SPRING_BACKEND_URL = os.environ["SPRING_BACKEND_URL"].rstrip("/")

# Usuario "de servicio" creado en la base del backend (rol TECNICO o similar,
# con permiso DOCUMENTOS_UPDATE) para que Django pueda autenticarse vía JWT.
SPRING_SERVICE_EMAIL = os.environ["SPRING_SERVICE_EMAIL"]
SPRING_SERVICE_PASSWORD = os.environ["SPRING_SERVICE_PASSWORD"]

# Descripción que se guardará en el Archivo creado por Django.
OCR_DESCRIPCION_RESULTADO = os.environ.get(
    "OCR_DESCRIPCION_RESULTADO",
    "PDF generado automáticamente por OCR a partir del archivo original.",
)

# --- CORS (el frontend llama directamente a este servicio desde el navegador) ---

CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("OCR_CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
# Para desarrollo local rápido, si no se configura nada, permite localhost:3000.
if not CORS_ALLOWED_ORIGINS and env_bool("DJANGO_DEBUG", default=False):
    CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]

# --- Poller de respaldo -----------------------------------------------------

# Segundos entre cada revisión de respaldo del bucket (la vía "rápida" es la
# llamada directa del frontend a /api/procesar-archivo/; esto solo atrapa lo
# que esa llamada no haya alcanzado a notificar, p.ej. si el usuario cerró la
# pestaña antes de que terminara el fetch).
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

# Carpeta temporal donde se descargan y procesan los archivos.
WORKDIR = Path(os.environ.get("OCR_WORKDIR", BASE_DIR / "tmp"))
WORKDIR.mkdir(parents=True, exist_ok=True)

# Idiomas para tesseract (paquetes tesseract-ocr-spa / tesseract-ocr-eng).
OCR_LANGUAGES = os.environ.get("OCR_LANGUAGES", "spa+eng")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
}
