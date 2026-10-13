"""Elección del backend de correo según el entorno (ver config/settings.py)."""

BREVO = "anymail.backends.brevo.EmailBackend"
SMTP = "django.core.mail.backends.smtp.EmailBackend"
CONSOLA = "django.core.mail.backends.console.EmailBackend"

NOMBRES = {BREVO: "Brevo (API HTTPS)", SMTP: "SMTP", CONSOLA: "consola"}
PREFIJO_API_KEY = "xkeysib-"


def limpiar_clave(valor):
    """Quita espacios y comillas que se cuelan al pegar la clave en Railway."""
    return (valor or "").strip().strip("\"'").strip()


def elegir_backend(brevo_api_key, email_host_user):
    """
    1. Con BREVO_API_KEY: Brevo por API HTTPS (django-anymail). Es lo de producción,
       porque Railway bloquea el SMTP saliente pero no HTTPS.
    2. Si no, con EMAIL_HOST_USER: SMTP (ej. Gmail con contraseña de aplicación).
    3. Si no: consola (el correo se ve en la terminal o en los logs).
    """
    if limpiar_clave(brevo_api_key):
        return BREVO
    if email_host_user and email_host_user.strip():
        return SMTP
    return CONSOLA


def nombre_backend(backend):
    return NOMBRES.get(backend, backend)


def resumen_configuracion(backend, brevo_api_key, remitente, debug):
    """
    Lo que se registra al arrancar: [(nivel, mensaje)]. Nunca incluye la clave;
    de ella solo se dice si está y con qué empieza (antes del primer guion).
    """
    mensajes = [("INFO", f"Correo: se usa {nombre_backend(backend)}. Remitente: {remitente or '(sin definir)'}.")]
    if backend == BREVO:
        clave = limpiar_clave(brevo_api_key)
        if not clave.startswith(PREFIJO_API_KEY):
            inicio = clave.split("-", 1)[0][:10]
            mensajes.append((
                "WARNING",
                f"BREVO_API_KEY no parece una API key v3 (empieza por '{inicio}-', debería empezar por "
                f"'{PREFIJO_API_KEY}'). Si es una SMTP key (xsmtpsib-), la API de Brevo la rechaza.",
            ))
        if not remitente:
            mensajes.append(("WARNING", "Brevo necesita DEFAULT_FROM_EMAIL con un remitente verificado en Brevo."))
    if backend == CONSOLA and not debug:
        mensajes.append((
            "WARNING",
            "Producción sin BREVO_API_KEY ni EMAIL_HOST_USER: los correos no se envían, solo se escriben en el log.",
        ))
    return mensajes
