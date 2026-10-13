"""Elección del backend de correo según el entorno (ver config/settings.py)."""

BREVO = "anymail.backends.brevo.EmailBackend"
SMTP = "django.core.mail.backends.smtp.EmailBackend"
CONSOLA = "django.core.mail.backends.console.EmailBackend"


def elegir_backend(brevo_api_key, email_host_user):
    """
    1. Con BREVO_API_KEY: Brevo por API HTTPS (django-anymail). Es lo de producción,
       porque Railway bloquea el SMTP saliente pero no HTTPS.
    2. Si no, con EMAIL_HOST_USER: SMTP (ej. Gmail con contraseña de aplicación).
    3. Si no: consola (el correo se ve en la terminal o en los logs).
    """
    if brevo_api_key and brevo_api_key.strip():
        return BREVO
    if email_host_user and email_host_user.strip():
        return SMTP
    return CONSOLA
