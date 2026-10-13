import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def registrar_configuracion_correo():
    """
    Deja en el log qué backend de correo quedó elegido (sin la clave). Se llama desde
    ready(), cuando el logging ya está configurado: en settings.py se perdería.
    """
    from django.conf import settings

    from config.correo import resumen_configuracion

    for nivel, mensaje in resumen_configuracion(
        settings.EMAIL_BACKEND,
        getattr(settings, "BREVO_API_KEY", ""),
        settings.DEFAULT_FROM_EMAIL,
        settings.DEBUG,
    ):
        logger.log(logging.getLevelName(nivel), mensaje)


class UsuariosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.usuarios"

    def ready(self):
        registrar_configuracion_correo()
