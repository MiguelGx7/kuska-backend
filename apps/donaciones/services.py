"""
Lógica de negocio de donaciones (RN-10 a RN-14, RN-11a, RN-17).

Fotos: se valida todo (datos y fotos) antes de subir nada a Cloudinary.
Si algo falla después de subir, se borran las fotos ya subidas. Al
eliminar, las fotos se borran solo cuando la base confirma el cambio
(transaction.on_commit).
"""

import logging
import uuid
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from PIL import Image
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalogo.models import Categoria, Configuracion
from apps.notificaciones.models import Notificacion
from apps.notificaciones.services import crear_notificacion, notificar_administradores
from apps.usuarios.models import Usuario

from .models import Donacion, DonacionAlimento, Novedad

logger = logging.getLogger(__name__)

MAX_BYTES_FOTO = 5 * 1024 * 1024
FORMATOS_FOTO = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
UNIDADES_ENTERAS = DonacionAlimento.UNIDADES_ENTERAS


# ---------------------------------------------------------------------
# Validaciones
# ---------------------------------------------------------------------
def fecha_minima_vencimiento(config, hoy=None):
    """El vencimiento debe ser POSTERIOR a esta fecha (RN-11). Hoy es la fecha de Bogotá."""
    return (hoy or timezone.localdate()) + timedelta(days=config.margen_vencimiento_dias)


def _cumple_vencimiento(fecha_vencimiento, config, hoy=None):
    return fecha_vencimiento > fecha_minima_vencimiento(config, hoy)


def validar_foto(archivo):
    """
    RN-11a: obligatoria, máximo 5 MB, y que Pillow confirme que de verdad es
    JPG, PNG o WEBP (no basta la extensión). Devuelve (extension, error).
    """
    if not archivo:
        return None, "La foto del alimento es obligatoria."
    if archivo.size > MAX_BYTES_FOTO:
        return None, "La foto no puede pesar más de 5 MB."
    try:
        with Image.open(archivo) as imagen:
            formato = imagen.format
            imagen.verify()
    except Exception:
        return None, "El archivo no es una imagen válida."
    finally:
        archivo.seek(0)
    if formato not in FORMATOS_FOTO:
        return None, "La foto debe ser JPG, PNG o WEBP."
    return FORMATOS_FOTO[formato], None


def _errores_alimento(datos, categorias, config, hoy):
    """Valida un alimento (RN-10a, RN-11, RN-17). Devuelve un dict de errores."""
    errores = {}
    if datos["categoria"] not in categorias:
        errores["categoria"] = ["La categoría no existe o está inactiva."]
    cantidad = datos["cantidad_total"]
    if datos["unidad_medida"] in UNIDADES_ENTERAS and cantidad != cantidad.to_integral_value():
        errores["cantidad_total"] = [
            "Para 'unidad' y 'paquete' la cantidad debe ser un número entero."
        ]
    if not _cumple_vencimiento(datos["fecha_vencimiento"], config, hoy):
        minima = fecha_minima_vencimiento(config, hoy)
        errores["fecha_vencimiento"] = [
            f"La fecha de vencimiento debe ser posterior al {minima:%d/%m/%Y}."
        ]
    return errores


def _validar_direccion(modalidad, direccion):
    """chk_donacion_direccion: Recoger o Ambas exigen dirección de recogida."""
    if modalidad != Donacion.ModalidadEntrega.DOMICILIO and not direccion:
        raise ValidationError(
            {"direccion_recogida": [
                "La dirección de recogida es obligatoria si la modalidad es Recoger o Ambas."
            ]}
        )


def _categorias_activas(ids):
    return {c.pk: c for c in Categoria.objects.filter(pk__in=set(ids), activa=True)}


def _limpiar_opcionales(datos, campos):
    for campo in campos:
        if campo in datos:
            datos[campo] = datos[campo] or None
    return datos


# ---------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------
def _subir_foto(alimento, archivo, extension, subidas):
    """Sube la foto a Cloudinary y anota su ruta por si hay que deshacer."""
    alimento.foto.save(f"{uuid.uuid4().hex}.{extension}", archivo, save=False)
    subidas.append(alimento.foto.name)


def _borrar_fotos(nombres):
    for nombre in nombres:
        try:
            default_storage.delete(nombre)
        except Exception:
            logger.exception("No se pudo borrar la foto %s", nombre)


def _borrar_fotos_al_confirmar(nombres):
    nombres = [n for n in nombres if n]
    transaction.on_commit(lambda: _borrar_fotos(nombres))


# ---------------------------------------------------------------------
# Donante: crear (RN-10)
# ---------------------------------------------------------------------
def crear_donacion(donante, datos, fotos):
    """
    `datos`: donación con su lista `alimentos`. `fotos`: {indice: archivo},
    en el mismo orden de los alimentos (foto_0, foto_1...).
    """
    datos = _limpiar_opcionales(dict(datos), ("descripcion", "direccion_recogida"))
    alimentos = [
        _limpiar_opcionales(dict(a), ("descripcion",)) for a in datos.pop("alimentos")
    ]
    config = Configuracion.objects.get(pk=1)
    hoy = timezone.localdate()

    # 1. Validar todo antes de subir cualquier foto.
    errores = {}
    try:
        _validar_direccion(datos["modalidad_entrega"], datos["direccion_recogida"])
    except ValidationError as error:
        errores.update(error.detail)

    categorias = _categorias_activas(a["categoria"] for a in alimentos)
    errores_alimentos = [_errores_alimento(a, categorias, config, hoy) for a in alimentos]
    if any(errores_alimentos):
        errores["alimentos"] = errores_alimentos

    extensiones = {}
    for indice in range(len(alimentos)):
        extension, error = validar_foto(fotos.get(indice))
        if error:
            errores[f"foto_{indice}"] = [error]
        extensiones[indice] = extension
    sobrantes = sorted(i for i in fotos if i >= len(alimentos))
    if sobrantes:
        errores["fotos"] = [
            f"Llegaron más fotos que alimentos (foto_{sobrantes[0]} no tiene alimento)."
        ]
    if errores:
        raise ValidationError(errores)

    # 2. Guardar todo en una transacción; si algo falla, borrar lo subido.
    subidas = []
    try:
        with transaction.atomic():
            donacion = Donacion.objects.create(donante=donante, **datos)
            for indice, datos_alimento in enumerate(alimentos):
                alimento = DonacionAlimento(
                    donacion=donacion,
                    categoria=categorias[datos_alimento.pop("categoria")],
                    cantidad_disponible=datos_alimento["cantidad_total"],
                    **datos_alimento,
                )
                _subir_foto(alimento, fotos[indice], extensiones[indice], subidas)
                alimento.save()

            notificar_administradores(
                Notificacion.Tipo.NUEVA_DONACION,
                "Nueva donación por revisar",
                f"{donante.nombre_para_mostrar} registró la donación «{donacion.titulo}» "
                f"con {len(alimentos)} alimento(s).",
                donacion=donacion,
            )
    except Exception:
        _borrar_fotos(subidas)
        raise
    return donacion


# ---------------------------------------------------------------------
# Donante: cambios mientras está Pendiente (RN-12)
# ---------------------------------------------------------------------
def _bloquear_donacion_propia(donante, donacion_id):
    """Bloquea la fila y revisa que siga Pendiente."""
    try:
        donacion = Donacion.objects.select_for_update().get(pk=donacion_id, donante=donante)
    except Donacion.DoesNotExist:
        raise NotFound("No encontramos esa donación.")

    if donacion.estado == Donacion.Estado.RECHAZADA:
        raise ValidationError(
            "Esta donación fue rechazada: no se puede editar ni eliminar. "
            "Si quieres donar de nuevo, crea una donación nueva."
        )
    if donacion.estado != Donacion.Estado.PENDIENTE:
        raise ValidationError(
            "Esta donación ya fue revisada: no se puede editar ni eliminar. "
            "Si algo cambió, reporta una novedad."
        )
    return donacion


def editar_donacion(donante, donacion_id, datos):
    datos = _limpiar_opcionales(dict(datos), ("descripcion", "direccion_recogida"))
    with transaction.atomic():
        donacion = _bloquear_donacion_propia(donante, donacion_id)
        for campo, valor in datos.items():
            setattr(donacion, campo, valor)
        _validar_direccion(donacion.modalidad_entrega, donacion.direccion_recogida)
        donacion.save()
    return donacion


def agregar_alimento(donante, donacion_id, datos, foto):
    datos = _limpiar_opcionales(dict(datos), ("descripcion",))
    config = Configuracion.objects.get(pk=1)
    categorias = _categorias_activas([datos["categoria"]])

    errores = _errores_alimento(datos, categorias, config, timezone.localdate())
    extension, error_foto = validar_foto(foto)
    if error_foto:
        errores["foto"] = [error_foto]
    if errores:
        raise ValidationError(errores)

    subidas = []
    try:
        with transaction.atomic():
            donacion = _bloquear_donacion_propia(donante, donacion_id)
            alimento = DonacionAlimento(
                donacion=donacion,
                categoria=categorias[datos.pop("categoria")],
                cantidad_disponible=datos["cantidad_total"],
                **datos,
            )
            _subir_foto(alimento, foto, extension, subidas)
            alimento.save()
    except Exception:
        _borrar_fotos(subidas)
        raise
    return alimento


def quitar_alimento(donante, donacion_id, alimento_id):
    with transaction.atomic():
        donacion = _bloquear_donacion_propia(donante, donacion_id)
        alimentos = DonacionAlimento.objects.select_for_update().filter(donacion=donacion)
        alimento = alimentos.filter(pk=alimento_id).first()
        if alimento is None:
            raise NotFound("Ese alimento no pertenece a esta donación.")
        if alimentos.count() == 1:
            raise ValidationError(
                "La donación debe tener al menos un alimento. "
                "Si ya no quieres donar, elimina la donación."
            )
        _borrar_fotos_al_confirmar([alimento.foto.name])
        alimento.delete()


def eliminar_donacion(donante, donacion_id):
    with transaction.atomic():
        donacion = _bloquear_donacion_propia(donante, donacion_id)
        fotos = list(donacion.alimentos.values_list("foto", flat=True))
        _borrar_fotos_al_confirmar(fotos)
        # Los alimentos se borran en cascada; las notificaciones quedan con donacion = NULL.
        donacion.delete()


# ---------------------------------------------------------------------
# Administrador: validar y rechazar (RN-13)
# ---------------------------------------------------------------------
def _bloquear_pendiente(donacion_id):
    try:
        donacion = (
            Donacion.objects.select_for_update().select_related("donante").get(pk=donacion_id)
        )
    except Donacion.DoesNotExist:
        raise NotFound("No encontramos esa donación.")
    if donacion.estado != Donacion.Estado.PENDIENTE:
        raise ValidationError(
            f"Esta donación ya fue revisada (estado: {donacion.estado}). Solo se revisan las pendientes."
        )
    return donacion


def calcular_max_por_solicitud(alimento, porcentaje):
    """
    cantidad_total × porcentaje / 100, redondeado hacia abajo:
    - unidad y paquete: a entero, mínimo 1.
    - kg, g, L y ml: a 2 decimales, mínimo 0,01.
    """
    valor = alimento.cantidad_total * porcentaje / Decimal("100")
    if alimento.unidad_medida in UNIDADES_ENTERAS:
        return max(valor.quantize(Decimal("1"), rounding=ROUND_FLOOR), Decimal("1"))
    return max(valor.quantize(Decimal("0.01"), rounding=ROUND_FLOOR), Decimal("0.01"))


def validar_donacion(admin, donacion_id):
    with transaction.atomic():
        donacion = _bloquear_pendiente(donacion_id)
        config = Configuracion.objects.get(pk=1)
        alimentos = list(DonacionAlimento.objects.select_for_update().filter(donacion=donacion))

        # RN-11 otra vez: si alguno ya no cumple, no se puede validar.
        vencidos = [a for a in alimentos if not _cumple_vencimiento(a.fecha_vencimiento, config)]
        if vencidos:
            detalle = ", ".join(f"{a.nombre} (vence el {a.fecha_vencimiento:%d/%m/%Y})" for a in vencidos)
            raise ValidationError(
                "No se puede validar: estos alimentos ya no cumplen la fecha de vencimiento "
                f"mínima: {detalle}. Rechaza la donación indicando el motivo."
            )

        for alimento in alimentos:
            alimento.max_por_solicitud = calcular_max_por_solicitud(
                alimento, config.porcentaje_max_por_alimento
            )
        DonacionAlimento.objects.bulk_update(alimentos, ["max_por_solicitud"])

        donacion.estado = Donacion.Estado.VALIDADA
        donacion.revisada_por = admin
        donacion.fecha_revision = timezone.now()
        donacion.save()

        crear_notificacion(
            donacion.donante,
            Notificacion.Tipo.DONACION_VALIDADA,
            "Tu donación fue validada",
            f"Tu donación «{donacion.titulo}» fue validada y ya está disponible para los beneficiarios.",
            donacion=donacion,
        )
    return donacion


def rechazar_donacion(admin, donacion_id, motivo):
    """Las fotos se conservan como evidencia para una posible sanción."""
    with transaction.atomic():
        donacion = _bloquear_pendiente(donacion_id)
        donacion.estado = Donacion.Estado.RECHAZADA
        donacion.motivo_rechazo = motivo
        donacion.revisada_por = admin
        donacion.fecha_revision = timezone.now()
        donacion.save()

        crear_notificacion(
            donacion.donante,
            Notificacion.Tipo.DONACION_RECHAZADA,
            "Tu donación fue rechazada",
            f"Tu donación «{donacion.titulo}» fue rechazada. Motivo: {motivo}",
            donacion=donacion,
        )
    return donacion


# ---------------------------------------------------------------------
# Beneficiario: alimentos visibles para pedir (RN-14)
# ---------------------------------------------------------------------
def alimentos_visibles():
    """
    Visible si: donante Activo, donación Validada, alimento activo, con
    cantidad disponible, sin novedad Pendiente (sobre él o sobre toda la
    donación) y con vencimiento posterior a hoy + margen.
    """
    config = Configuracion.objects.get(pk=1)
    novedad_pendiente = Novedad.objects.filter(estado=Novedad.Estado.PENDIENTE).filter(
        Q(donacion_alimento=OuterRef("pk"))
        | Q(donacion=OuterRef("donacion"), donacion_alimento__isnull=True)
    )
    return (
        DonacionAlimento.objects.filter(
            donacion__donante__estado=Usuario.Estado.ACTIVO,
            donacion__estado=Donacion.Estado.VALIDADA,
            activo=True,
            cantidad_disponible__gt=0,
            fecha_vencimiento__gt=fecha_minima_vencimiento(config),
        )
        .filter(~Exists(novedad_pendiente))
        .select_related("categoria", "donacion", "donacion__donante")
    )
