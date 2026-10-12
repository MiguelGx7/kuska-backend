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
from apps.solicitudes.models import Solicitud
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


def formato_cantidad(valor):
    """Decimal legible para mensajes: 5.00 -> "5", 1.50 -> "1.5"."""
    texto = f"{valor:f}"
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


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
def _bloquear_alimentos_de(donacion_id):
    """
    Regla 6 (orden fijo de bloqueos): los alimentos se bloquean ANTES que la
    donación, en orden por id. Se llama antes de bloquear la donación.
    """
    return list(
        DonacionAlimento.objects.select_for_update().filter(donacion_id=donacion_id).order_by("id")
    )


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
        alimentos = _bloquear_alimentos_de(donacion_id)
        _bloquear_donacion_propia(donante, donacion_id)
        alimento = next((a for a in alimentos if a.pk == alimento_id), None)
        if alimento is None:
            raise NotFound("Ese alimento no pertenece a esta donación.")
        if len(alimentos) == 1:
            raise ValidationError(
                "La donación debe tener al menos un alimento. "
                "Si ya no quieres donar, elimina la donación."
            )
        _borrar_fotos_al_confirmar([alimento.foto.name])
        alimento.delete()


def eliminar_donacion(donante, donacion_id):
    with transaction.atomic():
        # El borrado en cascada también toca los alimentos: se bloquean antes.
        alimentos = _bloquear_alimentos_de(donacion_id)
        donacion = _bloquear_donacion_propia(donante, donacion_id)
        _borrar_fotos_al_confirmar([a.foto.name for a in alimentos])
        # Los alimentos se borran en cascada; las notificaciones quedan con donacion = NULL.
        donacion.delete()


# ---------------------------------------------------------------------
# Administrador: validar y rechazar (RN-13)
# ---------------------------------------------------------------------
def _bloquear_pendiente(donacion_id):
    try:
        # of=("self",): bloquea solo la donación, no la fila del donante que se
        # lee en la misma consulta (el usuario va antes en el orden de la regla 6).
        donacion = (
            Donacion.objects.select_for_update(of=("self",))
            .select_related("donante")
            .get(pk=donacion_id)
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
        alimentos = _bloquear_alimentos_de(donacion_id)
        donacion = _bloquear_pendiente(donacion_id)
        config = Configuracion.objects.get(pk=1)

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


def motivos_no_visible(alimentos, config=None):
    """
    RN-14 explicado: devuelve {alimento_id: motivo} solo para los alimentos
    que hoy NO se pueden pedir. Los alimentos deben venir con
    select_related("donacion__donante").
    """
    config = config or Configuracion.objects.get(pk=1)
    minima = fecha_minima_vencimiento(config)
    ids = [a.pk for a in alimentos]
    donaciones = {a.donacion_id for a in alimentos}
    novedades = Novedad.objects.filter(estado=Novedad.Estado.PENDIENTE).filter(
        Q(donacion_alimento_id__in=ids) | Q(donacion_id__in=donaciones, donacion_alimento__isnull=True)
    ).values_list("donacion_id", "donacion_alimento_id")
    con_novedad = {alimento for _, alimento in novedades if alimento}
    donaciones_con_novedad = {donacion for donacion, alimento in novedades if alimento is None}

    motivos = {}
    for a in alimentos:
        if not a.activo:
            motivo = "Este alimento fue retirado de la donación."
        elif a.cantidad_disponible <= 0:
            motivo = "Este alimento se agotó."
        elif a.fecha_vencimiento <= minima:
            motivo = "Este alimento está muy cerca de vencer y ya no se puede pedir."
        elif a.donacion.estado != Donacion.Estado.VALIDADA:
            motivo = "Esta donación ya no está disponible."
        elif a.donacion.donante.estado != Usuario.Estado.ACTIVO:
            motivo = "El donante no está disponible por ahora."
        elif a.pk in con_novedad or a.donacion_id in donaciones_con_novedad:
            motivo = "Este alimento está en revisión por una novedad del donante."
        else:
            continue
        motivos[a.pk] = motivo
    return motivos


# ---------------------------------------------------------------------
# Estado de la donación (RN-16)
# ---------------------------------------------------------------------
def recalcular_estado_donacion(donacion):
    """
    Solo usa condiciones permanentes. Un alimento "tiene disponibilidad" si
    está activo, con cantidad disponible y vencimiento posterior a
    hoy + margen. Las temporales (novedad Pendiente, donante bloqueado)
    no cambian el estado.
    - Validada: algún alimento tiene disponibilidad.
    - Agotada: ninguno, pero hay solicitudes Pendiente o Aprobada.
    - Finalizada: ninguno y sin solicitudes en curso. Es final.
    Pendiente y Rechazada no se tocan.
    """
    with transaction.atomic():
        donacion = Donacion.objects.select_for_update().get(pk=donacion.pk)
        if donacion.estado not in (Donacion.Estado.VALIDADA, Donacion.Estado.AGOTADA):
            return donacion

        config = Configuracion.objects.get(pk=1)
        con_disponibilidad = donacion.alimentos.filter(
            activo=True,
            cantidad_disponible__gt=0,
            fecha_vencimiento__gt=fecha_minima_vencimiento(config),
        ).exists()
        if con_disponibilidad:
            nuevo = Donacion.Estado.VALIDADA
        elif Solicitud.objects.filter(
            donacion=donacion, estado__in=Solicitud.ESTADOS_EN_CURSO
        ).exists():
            nuevo = Donacion.Estado.AGOTADA
        else:
            nuevo = Donacion.Estado.FINALIZADA

        if nuevo != donacion.estado:
            donacion.estado = nuevo
            donacion.save(update_fields=["estado", "fecha_actualizacion"])
    return donacion


# =====================================================================
# Novedades (RN-30 a RN-33), Paso 8
# Orden de bloqueos (regla 6): usuario -> novedad -> solicitud ->
# alimentos (por id) -> configuración -> donación.
# =====================================================================
MOTIVO_CANCELACION_NOVEDAD = "Los alimentos fueron retirados por una novedad del donante."


def _objeto_novedad(novedad):
    if novedad.donacion_alimento_id:
        return f"el alimento «{novedad.donacion_alimento.nombre}»"
    return "toda la donación"


def crear_novedad(donante, donacion_id, alimento_id, descripcion):
    """
    RN-30: solo el donante dueño, sobre una donación Validada o Agotada, sobre
    un alimento suyo o sobre toda la donación. Máximo una Pendiente por
    alimento y una por toda la donación. Notifica a los administradores.
    """
    with transaction.atomic():
        # Turno del donante: dos peticiones suyas no crean dos Pendiente iguales.
        Usuario.objects.select_for_update().get(pk=donante.pk)

        donacion = Donacion.objects.filter(pk=donacion_id, donante=donante).first()
        if donacion is None:
            raise NotFound("No encontramos esa donación.")
        if donacion.estado not in (Donacion.Estado.VALIDADA, Donacion.Estado.AGOTADA):
            mensaje = (
                "Mientras la donación está Pendiente puedes editarla directamente."
                if donacion.estado == Donacion.Estado.PENDIENTE
                else f"No se pueden reportar novedades sobre una donación {donacion.estado}."
            )
            raise ValidationError({"donacion": [mensaje]})

        alimento = None
        if alimento_id is not None:
            alimento = DonacionAlimento.objects.filter(pk=alimento_id, donacion=donacion).first()
            if alimento is None:
                raise ValidationError({"donacion_alimento": ["Ese alimento no pertenece a esta donación."]})
            if not alimento.activo:
                raise ValidationError({"donacion_alimento": ["Ese alimento ya fue retirado de la donación."]})

        repetida = Novedad.objects.filter(
            donacion=donacion, donacion_alimento=alimento, estado=Novedad.Estado.PENDIENTE
        ).exists()
        if repetida:
            objeto = "este alimento" if alimento else "toda la donación"
            raise ValidationError(
                {"donacion_alimento": [f"Ya hay una novedad pendiente sobre {objeto}."]}
            )

        novedad = Novedad.objects.create(
            donante=donante, donacion=donacion, donacion_alimento=alimento, descripcion=descripcion
        )
        notificar_administradores(
            Notificacion.Tipo.NOVEDAD_CREADA,
            "Nueva novedad por revisar",
            f"{donante.nombre_para_mostrar} reportó una novedad sobre {_objeto_novedad(novedad)} "
            f"de «{donacion.titulo}»: {descripcion}",
            donacion=donacion,
        )
    return novedad


def _bloquear_novedad_pendiente(novedad_id):
    try:
        novedad = (
            Novedad.objects.select_for_update(of=("self",))
            .select_related("donacion__donante", "donacion_alimento")
            .get(pk=novedad_id)
        )
    except Novedad.DoesNotExist:
        raise NotFound("No encontramos esa novedad.")
    if novedad.estado != Novedad.Estado.PENDIENTE:
        raise ValidationError(f"Esta novedad ya fue revisada (estado: {novedad.estado}).")
    return novedad


def _alimentos_afectados_ids(novedad):
    if novedad.donacion_alimento_id:
        return [novedad.donacion_alimento_id]
    return list(novedad.donacion.alimentos.order_by("id").values_list("id", flat=True))


def _bloquear_solicitudes_afectadas(novedad, alimentos_ids, estados=Solicitud.ESTADOS_EN_CURSO):
    """Solicitudes en esos estados con algún detalle NO cancelado de los alimentos afectados."""
    ids = (
        Solicitud.objects.filter(
            donacion_id=novedad.donacion_id,
            estado__in=estados,
            detalles__donacion_alimento_id__in=alimentos_ids,
            detalles__cancelado=False,
        )
        .values_list("id", flat=True)
        .distinct()
    )
    return list(
        Solicitud.objects.select_for_update(of=("self",))
        .select_related("beneficiario")
        .filter(pk__in=list(ids))
        .order_by("id")
    )


def _fecha_limite_tras_novedad(solicitud, config, fecha_revision):
    """
    RN-24 sin los detalles cancelados, con base en fecha_gestion. RN-31: si
    esa fecha ya pasó, la base es la fecha de revisión de la novedad.
    """
    from apps.solicitudes.services import calcular_fecha_limite  # evita importación circular

    limite = calcular_fecha_limite(solicitud, config, solicitud.fecha_gestion)
    if limite <= fecha_revision:
        limite = calcular_fecha_limite(solicitud, config, fecha_revision)
    return limite


def contar_solicitudes_afectadas(novedad):
    """Para que el administrador vea el impacto antes de decidir."""
    return (
        Solicitud.objects.filter(
            donacion_id=novedad.donacion_id,
            estado__in=Solicitud.ESTADOS_EN_CURSO,
            detalles__donacion_alimento_id__in=_alimentos_afectados_ids(novedad),
            detalles__cancelado=False,
        )
        .distinct()
        .count()
    )


def aceptar_novedad(admin, novedad_id, respuesta=None):
    """RN-32 (y RN-31 para la fecha límite)."""
    from apps.solicitudes.models import DetalleSolicitud
    from apps.solicitudes.services import texto_fecha  # import local: evita importación circular

    with transaction.atomic():
        novedad = _bloquear_novedad_pendiente(novedad_id)
        donacion = novedad.donacion
        ahora = timezone.now()
        alimentos_ids = _alimentos_afectados_ids(novedad)
        solicitudes = _bloquear_solicitudes_afectadas(novedad, alimentos_ids)

        # Alimentos afectados: se dan de baja (se bloquean en orden por id).
        alimentos = list(
            DonacionAlimento.objects.select_for_update().filter(pk__in=alimentos_ids).order_by("id")
        )
        for alimento in alimentos:
            alimento.activo = False
            alimento.cantidad_disponible = Decimal("0")
        DonacionAlimento.objects.bulk_update(alimentos, ["activo", "cantidad_disponible"])

        config = Configuracion.objects.get(pk=1)
        motivo = f"el donante reportó una novedad: {novedad.descripcion}"
        for solicitud in solicitudes:
            cancelados = solicitud.detalles.filter(
                donacion_alimento_id__in=alimentos_ids, cancelado=False
            ).select_related("donacion_alimento")
            nombres = ", ".join(d.donacion_alimento.nombre for d in cancelados)
            DetalleSolicitud.objects.filter(pk__in=[d.pk for d in cancelados]).update(cancelado=True)

            if not solicitud.detalles.filter(cancelado=False).exists():
                solicitud.estado = Solicitud.Estado.CANCELADA
                solicitud.motivo_cancelacion = MOTIVO_CANCELACION_NOVEDAD
                solicitud.save(update_fields=["estado", "motivo_cancelacion", "fecha_actualizacion"])
                crear_notificacion(
                    solicitud.beneficiario,
                    Notificacion.Tipo.SOLICITUD_CANCELADA,
                    "Tu solicitud fue cancelada",
                    f"Tu solicitud {solicitud.codigo_pedido} de «{donacion.titulo}» se canceló porque "
                    f"{motivo}",
                    solicitud=solicitud,
                )
                continue

            extra = ""
            if solicitud.estado == Solicitud.Estado.APROBADA:
                solicitud.fecha_limite_recogida = _fecha_limite_tras_novedad(solicitud, config, ahora)
                solicitud.save(update_fields=["fecha_limite_recogida", "fecha_actualizacion"])
                extra = f" Nueva fecha límite: {texto_fecha(solicitud.fecha_limite_recogida)}."
            crear_notificacion(
                solicitud.beneficiario,
                Notificacion.Tipo.ALIMENTO_CANCELADO,
                "Se retiraron alimentos de tu solicitud",
                f"De tu solicitud {solicitud.codigo_pedido} de «{donacion.titulo}» se retiró: {nombres}, "
                f"porque {motivo}.{extra}",
                solicitud=solicitud,
            )

        novedad.estado = Novedad.Estado.ACEPTADA
        novedad.respuesta_admin = respuesta or None
        novedad.revisada_por = admin
        novedad.fecha_revision = ahora
        novedad.save()

        recalcular_estado_donacion(donacion)

        detalle = f" Respuesta: {respuesta}" if respuesta else ""
        crear_notificacion(
            donacion.donante,
            Notificacion.Tipo.NOVEDAD_ACEPTADA,
            "Tu novedad fue aceptada",
            f"Se aceptó tu novedad sobre {_objeto_novedad(novedad)} de «{donacion.titulo}»: "
            f"se retiró de la donación y {len(solicitudes)} solicitud(es) fueron ajustadas.{detalle}",
            donacion=donacion,
        )
    return novedad


def rechazar_novedad(admin, novedad_id, respuesta):
    """RN-33 (y RN-31 para la fecha límite): lo afectado vuelve a mostrarse."""
    with transaction.atomic():
        novedad = _bloquear_novedad_pendiente(novedad_id)
        ahora = timezone.now()
        # RN-31: Aprobadas afectadas cuya fecha límite ya pasó mientras la novedad estaba Pendiente.
        aprobadas = _bloquear_solicitudes_afectadas(
            novedad, _alimentos_afectados_ids(novedad), estados=[Solicitud.Estado.APROBADA]
        )
        config = Configuracion.objects.get(pk=1)
        for solicitud in aprobadas:
            if solicitud.fecha_limite_recogida and solicitud.fecha_limite_recogida <= ahora:
                solicitud.fecha_limite_recogida = _fecha_limite_tras_novedad(solicitud, config, ahora)
                solicitud.save(update_fields=["fecha_limite_recogida", "fecha_actualizacion"])

        novedad.estado = Novedad.Estado.RECHAZADA
        novedad.respuesta_admin = respuesta
        novedad.revisada_por = admin
        novedad.fecha_revision = ahora
        novedad.save()

        crear_notificacion(
            novedad.donacion.donante,
            Notificacion.Tipo.NOVEDAD_RECHAZADA,
            "Tu novedad fue rechazada",
            f"Se rechazó tu novedad sobre {_objeto_novedad(novedad)} de «{novedad.donacion.titulo}». "
            f"Respuesta: {respuesta}. Los alimentos siguen disponibles para los beneficiarios.",
            donacion=novedad.donacion,
        )
    return novedad
