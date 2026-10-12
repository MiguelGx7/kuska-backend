"""
Creación de notificaciones internas (RN-40).

Los demás servicios llaman a estas funciones dentro de su propia
transacción: si la operación falla, la notificación tampoco se guarda.
"""

from apps.usuarios.models import Rol, Usuario

from .models import Notificacion

MAX_TITULO = Notificacion._meta.get_field("titulo").max_length
MAX_MENSAJE = Notificacion._meta.get_field("mensaje").max_length


def _recortar(texto, maximo):
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _nueva(usuario, tipo, titulo, mensaje, donacion=None, solicitud=None):
    return Notificacion(
        usuario=usuario,
        tipo=tipo,
        titulo=_recortar(titulo, MAX_TITULO),
        mensaje=_recortar(mensaje, MAX_MENSAJE),
        donacion=donacion,
        solicitud=solicitud,
    )


def crear_notificacion(usuario, tipo, titulo, mensaje, donacion=None, solicitud=None):
    """Crea una notificación para un usuario. `tipo` es un Notificacion.Tipo."""
    notificacion = _nueva(usuario, tipo, titulo, mensaje, donacion, solicitud)
    notificacion.save()
    return notificacion


def notificar_administradores(tipo, titulo, mensaje, donacion=None, solicitud=None):
    """Crea la misma notificación para cada administrador activo."""
    administradores = Usuario.objects.filter(
        rol__nombre=Rol.ADMINISTRADOR, estado=Usuario.Estado.ACTIVO
    )
    return Notificacion.objects.bulk_create(
        [_nueva(admin, tipo, titulo, mensaje, donacion, solicitud) for admin in administradores]
    )
