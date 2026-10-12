"""
Configuración de Django para Kumora (backend).

Todos los valores sensibles o que cambian entre entornos se leen del
archivo .env (ver .env.example). Nunca se escriben credenciales aquí.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Carga las variables del .env en os.environ (también CLOUDINARY_URL,
# que la librería de Cloudinary lee directamente del entorno).
load_dotenv(BASE_DIR / ".env")


def env_list(nombre, por_defecto=""):
    """Convierte 'a,b,c' en ['a', 'b', 'c'] ignorando espacios y vacíos."""
    return [v.strip() for v in os.getenv(nombre, por_defecto).split(",") if v.strip()]


# ---------------------------------------------------------------------
# Básico
# ---------------------------------------------------------------------
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.getenv("DEBUG", "False").strip().lower() in ("true", "1", "yes")
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")


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
# DB_SSL_CA puede ser relativa (ej. "ca.pem"): se resuelve desde BASE_DIR.
DB_SSL_CA = str(BASE_DIR / os.environ["DB_SSL_CA"])

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
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# django-cloudinary-storage toma las credenciales de CLOUDINARY_URL.
CLOUDINARY_STORAGE = {"CLOUDINARY_URL": os.environ["CLOUDINARY_URL"]}


# ---------------------------------------------------------------------
# Correo (SMTP) para recuperar contraseña
# ---------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
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
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    # Sin throttling global: solo las vistas de login, registro y
    # recuperar contraseña usan el scope "auth" (ScopedRateThrottle).
    # Se limita por IP porque esas rutas son anónimas.
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
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Kumora API",
    "DESCRIPTION": "Red de Alimentos Solidarios: API REST del backend.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
