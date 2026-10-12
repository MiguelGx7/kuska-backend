"""
Lógica de negocio del carrito y las solicitudes (RN-20 a RN-23).

Orden de bloqueos (para evitar bloqueos cruzados entre peticiones):
usuario -> solicitud -> alimentos (por id) -> configuración -> donación.
"""

from collections import OrderedDict

from django.db import IntegrityError, transaction
from django.db.models.functions import Length
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalogo.models import Configuracion
from apps.donaciones.models import DonacionAlimento
from apps.donaciones.services import (
    formato_cantidad,
    motivos_no_visible,
    recalcular_estado_donacion,
)
from apps.notificaciones.models import Notificacion
from apps.notificaciones.services import notificar_administradores
from apps.usuarios.models import Usuario

from .models import CarritoItem, DetalleSolicitud, Solicitud

MENSAJE_SOLICITUD_EN_CURSO = (
    "Ya tienes una solicitud en curso de esta donación. Cuando termine podrás pedir de nuevo."
)


# ---------------------------------------------------------------------
# Validaciones compartidas
# ---------------------------------------------------------------------
def errores_cantidad(alimento, cantidad):
    """RN-10a y RN-20: entera para unidad/paquete, ≤ max_por_solicitud y ≤ disponible."""
    errores = []
    unidad = alimento.unidad_medida
    if unidad in DonacionAlimento.UNIDADES_ENTERAS and cantidad != cantidad.to_integral_value():
        errores.append("Para 'unidad' y 'paquete' la cantidad debe ser un número entero.")
    if alimento.max_por_solicitud is not None and cantidad > alimento.max_por_solicitud:
        errores.append(
            f"Puedes pedir máximo {formato_cantidad(alimento.max_por_solicitud)} {unidad} de este alimento."
        )
    if cantidad > alimento.cantidad_disponible:
        errores.append(f"Solo quedan {formato_cantidad(alimento.cantidad_disponible)} {unidad} disponibles.")
    return errores


def _donaciones_en_curso(beneficiario):
    """Ids de las donaciones donde el beneficiario tiene una solicitud Pendiente o Aprobada."""
    return set(
        Solicitud.objects.filter(
            beneficiario=beneficiario, estado__in=Solicitud.ESTADOS_EN_CURSO
        ).values_list("donacion_id", flat=True)
    )


def _alimento_con_relaciones(alimento_id):
    alimento = (
        DonacionAlimento.objects.select_related("categoria", "donacion__donante")
        .filter(pk=alimento_id)
        .first()
    )
    if alimento is None:
        raise NotFound("No encontramos ese alimento.")
    return alimento


# ---------------------------------------------------------------------
# Carrito (RN-20, RN-21)
# ---------------------------------------------------------------------
def agregar_al_carrito(beneficiario, alimento_id, cantidad):
    """Agrega el alimento o, si ya está, actualiza su cantidad. No reserva."""
    alimento = _alimento_con_relaciones(alimento_id)

    motivo = motivos_no_visible([alimento]).get(alimento.pk)
    if motivo:
        raise ValidationError({"donacion_alimento": [motivo]})
    if alimento.donacion_id in _donaciones_en_curso(beneficiario):
        raise ValidationError({"donacion_alimento": [MENSAJE_SOLICITUD_EN_CURSO]})
    errores = errores_cantidad(alimento, cantidad)
    if errores:
        raise ValidationError({"cantidad": errores})

    try:
        with transaction.atomic():
            item, creado = CarritoItem.objects.update_or_create(
                beneficiario=beneficiario,
                donacion_alimento=alimento,
                defaults={"cantidad": cantidad},
            )
    except IntegrityError:
        # Dos peticiones simultáneas agregando el mismo alimento.
        raise ValidationError({"donacion_alimento": ["Este alimento ya está en el carrito."]})
    return item, creado


def _item_propio(beneficiario, item_id):
    item = CarritoItem.objects.filter(pk=item_id, beneficiario=beneficiario).first()
    if item is None:
        raise NotFound("Ese ítem no está en tu carrito.")
    return item


def cambiar_cantidad(beneficiario, item_id, cantidad):
    item = _item_propio(beneficiario, item_id)
    item, _ = agregar_al_carrito(beneficiario, item.donacion_alimento_id, cantidad)
    return item


def quitar_del_carrito(beneficiario, item_id):
    _item_propio(beneficiario, item_id).delete()


def _items_del_carrito(beneficiario):
    return list(
        CarritoItem.objects.filter(beneficiario=beneficiario)
        .select_related("donacion_alimento__categoria", "donacion_alimento__donacion__donante")
        .order_by("donacion_alimento__donacion_id", "donacion_alimento_id")
    )


def _agrupar_por_donacion(items):
    grupos = OrderedDict()
    for item in items:
        donacion = item.donacion_alimento.donacion
        grupos.setdefault(donacion.pk, {"donacion": donacion, "items": []})["items"].append(item)
    return list(grupos.values())


def _avisos_de_limites(grupos, beneficiario, config):
    """RN-21: cuántas solicitudes generaría el carrito y cuántos alimentos por solicitud."""
    avisos = []
    activas = Solicitud.objects.filter(
        beneficiario=beneficiario, estado__in=Solicitud.ESTADOS_EN_CURSO
    ).count()
    libres = max(config.max_solicitudes_activas - activas, 0)
    if len(grupos) > libres:
        avisos.append(
            f"Tu carrito tiene alimentos de {len(grupos)} donaciones y cada una genera una solicitud. "
            f"Puedes tener máximo {config.max_solicitudes_activas} solicitudes en curso y ya tienes "
            f"{activas}: quita los alimentos de {len(grupos) - libres} donación(es) para poder enviarlo."
        )
    for grupo in grupos:
        distintos = len(grupo["items"])
        if distintos > config.max_alimentos_por_solicitud:
            avisos.append(
                f"De la donación «{grupo['donacion'].titulo}» tienes {distintos} alimentos distintos; "
                f"el máximo por solicitud es {config.max_alimentos_por_solicitud}."
            )
    return avisos


def ver_carrito(beneficiario):
    """
    RN-21: marca cada ítem que ya no es válido con sus motivos y avisa si el
    carrito supera los límites. Devuelve {"donaciones", "avisos", "puede_enviar"}.
    """
    config = Configuracion.objects.get(pk=1)
    items = _items_del_carrito(beneficiario)
    alimentos = [item.donacion_alimento for item in items]
    no_visibles = motivos_no_visible(alimentos, config)
    en_curso = _donaciones_en_curso(beneficiario)

    for item in items:
        alimento = item.donacion_alimento
        if alimento.pk in no_visibles:
            item.motivos = [no_visibles[alimento.pk]]
        elif alimento.donacion_id in en_curso:
            item.motivos = [MENSAJE_SOLICITUD_EN_CURSO]
        else:
            item.motivos = errores_cantidad(alimento, item.cantidad)
        item.valido = not item.motivos

    grupos = _agrupar_por_donacion(items)
    avisos = _avisos_de_limites(grupos, beneficiario, config)
    return {
        "donaciones": grupos,
        "avisos": avisos,
        "puede_enviar": bool(items) and all(i.valido for i in items) and not avisos,
    }


# ---------------------------------------------------------------------
# Enviar el carrito (RN-22)
# ---------------------------------------------------------------------
def _generar_codigo_pedido():
    """
    KUM-AAAAMMDD-NNNN, consecutivo del día (Bogotá). Bloquear la fila de
    configuración funciona como un turno: dos envíos simultáneos no pueden
    calcular el mismo número.
    """
    Configuracion.objects.select_for_update().get(pk=1)
    prefijo = f"KUM-{timezone.localdate():%Y%m%d}-"
    ultimo = (
        Solicitud.objects.filter(codigo_pedido__startswith=prefijo)
        .order_by(Length("codigo_pedido").desc(), "-codigo_pedido")
        .values_list("codigo_pedido", flat=True)
        .first()
    )
    consecutivo = int(ultimo[len(prefijo):]) + 1 if ultimo else 1
    return f"{prefijo}{consecutivo:04d}"


def _modalidad_y_direccion(donacion, eleccion, beneficiario):
    """Devuelve (modalidad, direccion, errores) para la solicitud de esta donación."""
    elegida = eleccion.get("modalidad_entrega")
    if donacion.modalidad_entrega == donacion.ModalidadEntrega.AMBAS:
        if not elegida:
            return None, None, [
                f"Elige cómo quieres recibir los alimentos de «{donacion.titulo}»: Recoger o Domicilio."
            ]
        modalidad = elegida
    else:
        modalidad = donacion.modalidad_entrega
        if elegida and elegida != modalidad:
            return None, None, [f"La donación «{donacion.titulo}» solo permite {modalidad}."]

    if modalidad == Solicitud.ModalidadEntrega.RECOGER:
        return modalidad, None, []
    direccion = (eleccion.get("direccion_entrega") or beneficiario.direccion or "").strip()
    if not direccion:
        return None, None, [
            f"Indica la dirección de entrega para «{donacion.titulo}» "
            "(no tienes una dirección en tu perfil)."
        ]
    return modalidad, direccion, []


def enviar_carrito(beneficiario, elecciones):
    """
    `elecciones`: {donacion_id: {"modalidad_entrega", "direccion_entrega"}}.
    Crea una solicitud por donación con el mismo codigo_pedido, reserva las
    cantidades y vacía el carrito. Si algo falla, no crea nada.
    """
    with transaction.atomic():
        # Turno por beneficiario: dos envíos suyos al tiempo no pasan juntos el límite.
        Usuario.objects.select_for_update().get(pk=beneficiario.pk)

        items = _items_del_carrito(beneficiario)
        if not items:
            raise ValidationError({"carrito": ["Tu carrito está vacío."]})

        # Bloquea los alimentos en orden por id y usa sus valores actuales.
        bloqueados = {
            a.pk: a
            for a in DonacionAlimento.objects.select_for_update(of=("self",))
            .select_related("donacion__donante")
            .filter(pk__in=[i.donacion_alimento_id for i in items])
            .order_by("id")
        }
        for item in items:
            item.donacion_alimento = bloqueados[item.donacion_alimento_id]

        config = Configuracion.objects.get(pk=1)
        no_visibles = motivos_no_visible(list(bloqueados.values()), config)
        en_curso = _donaciones_en_curso(beneficiario)
        grupos = _agrupar_por_donacion(items)

        errores_items, errores_donaciones, errores_carrito = {}, {}, []
        for item in items:
            alimento = item.donacion_alimento
            if alimento.pk in no_visibles:
                motivos = [no_visibles[alimento.pk]]
            elif alimento.donacion_id in en_curso:
                motivos = [MENSAJE_SOLICITUD_EN_CURSO]
            else:
                motivos = errores_cantidad(alimento, item.cantidad)
            if motivos:
                errores_items[str(item.pk)] = motivos

        sobrantes = set(elecciones) - {g["donacion"].pk for g in grupos}
        for donacion_id in sorted(sobrantes):
            errores_donaciones[str(donacion_id)] = ["Tu carrito no tiene alimentos de esta donación."]

        envios = []
        for grupo in grupos:
            donacion = grupo["donacion"]
            modalidad, direccion, errores = _modalidad_y_direccion(
                donacion, elecciones.get(donacion.pk, {}), beneficiario
            )
            if len(grupo["items"]) > config.max_alimentos_por_solicitud:
                errores.append(
                    f"Máximo {config.max_alimentos_por_solicitud} alimentos distintos por solicitud; "
                    f"de esta donación tienes {len(grupo['items'])}."
                )
            if errores:
                errores_donaciones[str(donacion.pk)] = errores
            envios.append((grupo, modalidad, direccion))

        activas = Solicitud.objects.filter(
            beneficiario=beneficiario, estado__in=Solicitud.ESTADOS_EN_CURSO
        ).count()
        if activas + len(grupos) > config.max_solicitudes_activas:
            errores_carrito.append(
                f"Este envío crearía {len(grupos)} solicitud(es) y ya tienes {activas} en curso; "
                f"el máximo es {config.max_solicitudes_activas}."
            )

        if errores_items or errores_donaciones or errores_carrito:
            errores = {}
            if errores_carrito:
                errores["carrito"] = errores_carrito
            if errores_items:
                errores["items"] = errores_items
            if errores_donaciones:
                errores["donaciones"] = errores_donaciones
            raise ValidationError(errores)

        # Todo es válido: crear, reservar, recalcular, vaciar y notificar.
        codigo = _generar_codigo_pedido()
        solicitudes = []
        for grupo, modalidad, direccion in envios:
            solicitud = Solicitud.objects.create(
                codigo_pedido=codigo,
                beneficiario=beneficiario,
                donacion=grupo["donacion"],
                modalidad_entrega=modalidad,
                direccion_entrega=direccion,
            )
            DetalleSolicitud.objects.bulk_create(
                DetalleSolicitud(
                    solicitud=solicitud, donacion_alimento=item.donacion_alimento, cantidad=item.cantidad
                )
                for item in grupo["items"]
            )
            for item in grupo["items"]:
                alimento = item.donacion_alimento
                alimento.cantidad_disponible -= item.cantidad
                alimento.save(update_fields=["cantidad_disponible"])
            solicitudes.append(solicitud)

        for grupo in grupos:
            recalcular_estado_donacion(grupo["donacion"])

        CarritoItem.objects.filter(pk__in=[i.pk for i in items]).delete()

        # Una notificación por pedido (no una por solicitud).
        notificar_administradores(
            Notificacion.Tipo.NUEVA_SOLICITUD,
            "Nuevo pedido por revisar",
            f"{beneficiario.nombre_para_mostrar} envió el pedido {codigo} con "
            f"{len(solicitudes)} solicitud(es) y {len(items)} alimento(s).",
        )
    return codigo, solicitudes


# ---------------------------------------------------------------------
# Cancelar (RN-23)
# ---------------------------------------------------------------------
def devolver_cantidades(solicitud):
    """
    Devuelve a cantidad_disponible lo de los detalles no cancelados y
    recalcula la donación (RN-16). Debe llamarse con la solicitud ya
    bloqueada, dentro de una transacción.
    """
    detalles = list(solicitud.detalles.filter(cancelado=False))
    alimentos = {
        a.pk: a
        for a in DonacionAlimento.objects.select_for_update()
        .filter(pk__in=[d.donacion_alimento_id for d in detalles])
        .order_by("id")
    }
    for detalle in detalles:
        alimento = alimentos[detalle.donacion_alimento_id]
        alimento.cantidad_disponible = min(
            alimento.cantidad_disponible + detalle.cantidad, alimento.cantidad_total
        )
        alimento.save(update_fields=["cantidad_disponible"])
    recalcular_estado_donacion(solicitud.donacion)


def cancelar_solicitud(beneficiario, solicitud_id):
    """El beneficiario solo cancela solicitudes Pendiente. Sin sanción."""
    with transaction.atomic():
        try:
            solicitud = Solicitud.objects.select_for_update().get(
                pk=solicitud_id, beneficiario=beneficiario
            )
        except Solicitud.DoesNotExist:
            raise NotFound("No encontramos esa solicitud.")
        if solicitud.estado != Solicitud.Estado.PENDIENTE:
            raise ValidationError(
                f"Solo puedes cancelar solicitudes pendientes; esta está {solicitud.estado}."
            )
        solicitud.estado = Solicitud.Estado.CANCELADA
        solicitud.motivo_cancelacion = "Cancelada por el beneficiario."
        solicitud.save(update_fields=["estado", "motivo_cancelacion", "fecha_actualizacion"])
        devolver_cantidades(solicitud)
    return solicitud
