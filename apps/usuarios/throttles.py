from rest_framework.exceptions import Throttled
from rest_framework.throttling import SimpleRateThrottle


class AuthIPThrottle(SimpleRateThrottle):
    """
    Grupo "auth" (5/min): cuenta por IP y por endpoint, aunque la petición
    traiga token. Así registrar dos cuentas no gasta los intentos de login.
    """

    scope = "auth"

    def get_cache_key(self, request, view):
        ident = f"{view.__class__.__name__}:{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LimiteAuthMixin:
    """Para las vistas públicas de autenticación."""

    throttle_classes = [AuthIPThrottle]

    def throttled(self, request, wait):
        segundos = int(wait) if wait else None
        detalle = "Demasiados intentos. Espera un momento e inténtalo de nuevo."
        if segundos:
            detalle = f"Demasiados intentos. Inténtalo de nuevo en {segundos} segundos."
        raise Throttled(detail=detalle)
