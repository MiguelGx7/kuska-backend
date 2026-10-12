"""Lógica de negocio de categorías (RN-17) y configuración."""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from rest_framework.exceptions import ValidationError

from .models import Categoria, Configuracion

MENSAJE_NOMBRE_REPETIDO = "Ya existe una categoría con este nombre."


# ---------------------------------------------------------------------
# Categorías (RN-17)
# ---------------------------------------------------------------------
def _validar_nombre_unico(nombre, excluir_id=None):
    repetidas = Categoria.objects.filter(nombre__iexact=nombre)
    if excluir_id is not None:
        repetidas = repetidas.exclude(pk=excluir_id)
    if repetidas.exists():
        raise ValidationError({"nombre": [MENSAJE_NOMBRE_REPETIDO]})


def _guardar_categoria(categoria):
    try:
        with transaction.atomic():
            categoria.save()
    except IntegrityError:
        raise ValidationError({"nombre": [MENSAJE_NOMBRE_REPETIDO]})
    return categoria


def crear_categoria(datos):
    datos = {**datos, "nombre": datos["nombre"].strip()}
    _validar_nombre_unico(datos["nombre"])
    return _guardar_categoria(Categoria(**datos))


def actualizar_categoria(categoria, datos):
    if "nombre" in datos:
        datos = {**datos, "nombre": datos["nombre"].strip()}
        _validar_nombre_unico(datos["nombre"], excluir_id=categoria.pk)
    for campo, valor in datos.items():
        setattr(categoria, campo, valor)
    return _guardar_categoria(categoria)


def eliminar_categoria(categoria):
    """Solo se borra si ningún alimento la usa; si no, se debe inactivar."""
    mensaje = (
        "No se puede eliminar: hay alimentos con esta categoría. "
        "Puedes inactivarla para que no se use en alimentos nuevos."
    )
    if categoria.alimentos.exists():
        raise ValidationError(mensaje)
    try:
        categoria.delete()
    except ProtectedError:
        # Un alimento la tomó justo antes de borrarla.
        raise ValidationError(mensaje)


# ---------------------------------------------------------------------
# Configuración (una sola fila, id = 1)
# ---------------------------------------------------------------------
def obtener_configuracion():
    return Configuracion.objects.select_related("actualizada_por").get(pk=1)


def _validar_limites(config):
    """Los mismos límites del CHECK chk_config_valores."""
    errores = {}
    mayor_que_cero = {
        "plazo_recogida_dias": "El plazo de recogida debe ser de al menos 1 día.",
        "max_alimentos_por_solicitud": "Debe permitirse al menos 1 alimento por solicitud.",
        "max_solicitudes_activas": "Debe permitirse al menos 1 solicitud activa.",
    }
    for campo, mensaje in mayor_que_cero.items():
        if getattr(config, campo) <= 0:
            errores[campo] = [mensaje]
    if config.margen_vencimiento_dias < 0:
        errores["margen_vencimiento_dias"] = ["El margen de vencimiento no puede ser negativo."]
    porcentaje = config.porcentaje_max_por_alimento
    if porcentaje <= 0 or porcentaje > Decimal("100"):
        errores["porcentaje_max_por_alimento"] = [
            "El porcentaje debe ser mayor que 0 y máximo 100."
        ]
    if errores:
        raise ValidationError(errores)


def actualizar_configuracion(admin, datos):
    with transaction.atomic():
        config = Configuracion.objects.select_for_update().get(pk=1)
        for campo, valor in datos.items():
            setattr(config, campo, valor)
        _validar_limites(config)
        config.actualizada_por = admin
        config.save()
    return obtener_configuracion()
