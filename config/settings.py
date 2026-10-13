"""
Configuración de Django para Kumora (backend).

Todos los valores sensibles o que cambian entre entornos se leen del
archivo .env (ver .env.example). Nunca se escriben credenciales aquí.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from config.certificado import ruta_certificado
from config.correo import elegir_backend, limpiar_clave

BASE_DIR = Path(__file__).resolve().parent.parent

# Carga las variables del .env en os.environ (también CLOUDINARY_URL,
# que la librería de Cloudinary lee directamente del entorno).
load_dotenv(BASE_DIR / ".env")


def env_list(nombre, por_defecto=""):
    """Convierte 'a,b,c' en ['a', 'b', 'c'] ignorando espacios y vacíos."""
    return [v.strip() for v in os.getenv(nombre, por_defecto).split(",") if v.strip()]


def env_bool(nombre, por_defecto="False"):
    return os.getenv(nombre, por_defecto).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------
# Básico
# ---------------------------------------------------------------------
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = env_bool("DEBUG")
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
# Orígenes con HTTPS desde donde se envían formularios con cookie CSRF
# (el admin de Django en el dominio de Railway). Ej.: https://kumora.up.railway.app
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
# Swagger (api/docs/) siempre con DEBUG; en producción solo si SWAGGER_PUBLICO=True.
SWAGGER_PUBLICO = DEBUG or env_bool("SWAGGER_PUBLICO")


# ---------------------------------------------------------------------
# Aplicaciones
# ---------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceros
    "cloudinary_storage",
    "cloudinary",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    # Apps de Kumora
    "apps.usuarios",
    "apps.catalogo",
    "apps.donaciones",
    "apps.solicitudes",
    "apps.sanciones",
    "apps.notificaciones",
    "apps.dashboard",
]

# Modelo de usuario personalizado (debe existir antes de la primera migración).
AUTH_USER_MODEL = "usuarios.Usuario"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# En producción WhiteNoise sirve los estáticos (admin y Swagger) sin un servidor
# aparte; va justo después de SecurityMiddleware. En local los sirve runserver.
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ---------------------------------------------------------------------
# Base de datos: MySQL en Aiven (con SSL)
# ---------------------------------------------------------------------
# Local: DB_SSL_CA es la ruta al ca.pem (relativa a BASE_DIR o absoluta).
# Railway: DB_SSL_CA_BASE64 (recomendado) o DB_SSL_CA_CONTENIDO traen el certificado
# y se escribe en un archivo temporal. Prioridad y detalles en config/certificado.py.
DB_SSL_CA = ruta_certificado(
    BASE_DIR,
    ruta=os.getenv("DB_SSL_CA"),
    contenido=os.getenv("DB_SSL_CA_CONTENIDO"),
    en_base64=os.getenv("DB_SSL_CA_BASE64"),
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ["DB_HOST"],
        "PORT": os.environ["DB_PORT"],
        "OPTIONS": {
            "ssl": {"ca": DB_SSL_CA},
            "charset": "utf8mb4",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------
# Contraseñas
# ---------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------
# Idioma y zona horaria
# ---------------------------------------------------------------------
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------
# Archivos: estáticos locales, fotos (media) en Cloudinary
# ---------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
    # En producción, WhiteNoise sirve versiones comprimidas y con nombre único
    # (caché larga). Requiere collectstatic, que corre al arrancar en Railway.
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# django-cloudinary-storage toma las credenciales de CLOUDINARY_URL.
CLOUDINARY_STORAGE = {"CLOUDINARY_URL": os.environ["CLOUDINARY_URL"]}


# ---------------------------------------------------------------------
# Correo para recuperar contraseña
# ---------------------------------------------------------------------
# Se elige el backend según lo que haya en el entorno:
# 1. BREVO_API_KEY → Brevo por su API HTTPS (django-anymail). Es lo de producción:
#    Railway bloquea el SMTP saliente, pero no las peticiones HTTPS.
# 2. EMAIL_HOST_USER → SMTP (por ejemplo Gmail con contraseña de aplicación).
# 3. Ninguno → consola: el correo se muestra en la terminal (o en los logs).
# Sin espacios ni comillas alrededor (se cuelan al pegarla en Railway).
BREVO_API_KEY = limpiar_clave(os.getenv("BREVO_API_KEY"))
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
# Si el servidor de correo no responde (ej. SMTP bloqueado por el hosting), se
# corta a los 10 s en lugar de dejar la petición colgada; el error queda en el log.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))

EMAIL_BACKEND = elegir_backend(BREVO_API_KEY, EMAIL_HOST_USER)
# Credencial de Brevo y el mismo límite de espera que SMTP para su petición HTTPS.
ANYMAIL = {"BREVO_API_KEY": BREVO_API_KEY, "REQUESTS_TIMEOUT": EMAIL_TIMEOUT}

# Remitente. Con Brevo debe ser una dirección (o dominio) verificada en Brevo,
# si no, Brevo rechaza el envío. Formato: Kumora <correo@dominio.com>
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)


# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")


# ---------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Toda ruta exige login y estado Activo, salvo las públicas de auth.
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        "apps.usuarios.permissions.EsUsuarioActivo",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    # Sin throttling global: solo login, registro y recuperar contraseña
    # usan el grupo "auth", por IP (apps/usuarios/throttles.py).
    "DEFAULT_THROTTLE_RATES": {
        "auth": "5/min",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Kumora API",
    "DESCRIPTION": "Red de Alimentos Solidarios: API REST del backend.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Esquemas separados de entrada y salida (los campos de solo lectura
    # no aparecen como obligatorios en las peticiones).
    "COMPONENT_SPLIT_REQUEST": True,
    # Swagger recuerda el token entre recargas de la página.
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True},
    # Errores en español y ejemplos para todos los endpoints (ver config/openapi.py).
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",  # el que trae por defecto
        "config.openapi.completar_esquema",
    ],
    # Nombres claros cuando dos campos "estado" tienen opciones distintas.
    "ENUM_NAME_OVERRIDES": {
        "EstadoUsuarioEnum": "apps.usuarios.models.Usuario.Estado",
        "EstadoCambiableEnum": ["Activo", "Inactivo"],
        "EstadoDonacionEnum": "apps.donaciones.models.Donacion.Estado",
        "EstadoNovedadEnum": "apps.donaciones.models.Novedad.Estado",
        "EstadoSolicitudEnum": "apps.solicitudes.models.Solicitud.Estado",
        "ModalidadDonacionEnum": "apps.donaciones.models.Donacion.ModalidadEntrega",
        "ModalidadSolicitudEnum": "apps.solicitudes.models.Solicitud.ModalidadEntrega",
        "TipoSancionEnum": "apps.sanciones.models.Sancion.Tipo",
        "TipoNotificacionEnum": "apps.notificaciones.models.Notificacion.Tipo",
    },
}


# ---------------------------------------------------------------------
# Producción (DEBUG=False): HTTPS y cookies seguras (check --deploy)
# ---------------------------------------------------------------------
# Nada de este bloque aplica en desarrollo local (DEBUG=True).
if not DEBUG:
    # Railway termina el HTTPS en su proxy y le pasa la petición a gunicorn por
    # HTTP con X-Forwarded-Proto: https. Con esto Django sabe que fue segura.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS: el navegador recordará usar solo HTTPS. Empieza bajo (1 hora) y se
    # sube (ej. 31536000 = 1 año) cuando el dominio esté estable; es difícil de revertir.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "3600"))
    # Sin incluir subdominios ni pedir "preload": en un dominio *.up.railway.app
    # no controlamos los subdominios, y la lista de preload de los navegadores es
    # casi irreversible. Por eso se silencian esas dos advertencias a propósito.
    SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

    # Logs a la consola, que es lo que muestra Railway. Todo pasa por el logger raíz
    # (un solo handler, sin duplicados):
    # - apps de Kumora ("apps.*"): desde INFO (backend de correo elegido, códigos enviados...);
    # - anymail (Brevo) y Django: desde WARNING (incluye los 500 de django.request);
    # - el resto: desde WARNING.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"simple": {"format": "%(levelname)s %(name)s: %(message)s"}},
        "handlers": {"consola": {"class": "logging.StreamHandler", "formatter": "simple"}},
        "root": {"handlers": ["consola"], "level": "WARNING"},
        "loggers": {
            "apps": {"level": "INFO"},
            "anymail": {"level": "WARNING"},
            # Sin handlers propios: así no se repite por la consola de Django ni intenta mail_admins.
            "django": {"handlers": [], "level": "WARNING"},
        },
    }
