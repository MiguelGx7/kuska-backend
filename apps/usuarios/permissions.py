from rest_framework.permissions import BasePermission, IsAuthenticated

from . import services
from .models import Rol


class EsUsuarioActivo(BasePermission):
    """
    En cada petición revisa el estado del usuario: un bloqueo o una
    inactivación aplican de inmediato aunque el token siga vigente.
    Si el bloqueo temporal ya venció, lo levanta (RN-03).
    """

    message = "Tu cuenta no está activa."

    def has_permission(self, request, view):
        usuario = request.user
        if not usuario or not usuario.is_authenticated:
            return False
        motivo = services.motivo_sin_acceso(usuario)
        if motivo:
            self.message = motivo
            return False
        return True


class _TieneRol(BasePermission):
    rol = None

    def has_permission(self, request, view):
        usuario = request.user
        return bool(
            usuario and usuario.is_authenticated and usuario.rol.nombre == self.rol
        )


class EsDonante(_TieneRol):
    rol = Rol.DONANTE
    message = "Esta acción es solo para donantes."


class EsBeneficiario(_TieneRol):
    rol = Rol.BENEFICIARIO
    message = "Esta acción es solo para beneficiarios."


class EsAdministrador(_TieneRol):
    rol = Rol.ADMINISTRADOR
    message = "Esta acción es solo para administradores."


# Listas para permission_classes: reemplazan las de por defecto, así que
# repiten IsAuthenticated y EsUsuarioActivo.
PERMISOS_ADMIN = [IsAuthenticated, EsUsuarioActivo, EsAdministrador]
PERMISOS_DONANTE = [IsAuthenticated, EsUsuarioActivo, EsDonante]
PERMISOS_BENEFICIARIO = [IsAuthenticated, EsUsuarioActivo, EsBeneficiario]
