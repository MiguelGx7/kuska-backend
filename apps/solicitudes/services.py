"""
Lógica de negocio del carrito, las solicitudes y las entregas
(RN-20 a RN-28, RN-15, RN-31 parcial).

Orden de bloqueos (regla 6, para evitar bloqueos cruzados entre peticiones):
usuario -> solicitud -> alimentos (por id) -> configuración -> donación.
"""

from collections import OrderedDict
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import Length
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalogo.models import Configuracion
from apps.donaciones.models import DonacionAlimento, Novedad
from apps.donaciones.services import (
    formato_cantidad,
    motivos_no_visible,
    recalcular_estado_donacion,
)
from apps.notificaciones.models import Notificacion
from apps.notificaciones.services import crear_notificacion, notificar_administradores
from apps.usuarios.models import Usuario

from .models import CarritoItem, DetalleSolicitud, Entrega, Solicitud

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


# =====================================================================
# Paso 7: gestión de solicitudes y entregas (RN-24 a RN-28, RN-31)
# =====================================================================
MOTIVO_PENDIENTE_VENCIDA = "Los alimentos vencieron antes de ser aprobada"


def _texto_fecha(valor):
    return f"{timezone.localtime(valor):%d/%m/%Y a las %H:%M}"


def fin_del_dia(fecha):
    """Último instante de esa fecha en la hora de Bogotá."""
    return timezone.make_aware(datetime.combine(fecha, time.max))


def limite_por_vencimiento(solicitud, config):
    """
    Final del día (vencimiento más cercano de los alimentos no cancelados
    − margen). Después de ese momento la comida ya no se debe entregar.
    None si la solicitud no tiene detalles vigentes.
    """
    fechas = solicitud.detalles.filter(cancelado=False).values_list(
        "donacion_alimento__fecha_vencimiento", flat=True
    )
    if not fechas:
        return None
    return fin_del_dia(min(fechas) - timedelta(days=config.margen_vencimiento_dias))


def calcular_fecha_limite(solicitud, config, fecha_gestion):
    """RN-24: la menor entre fecha_gestion + plazo y el límite por vencimiento."""
    por_plazo = fecha_gestion + timedelta(days=config.plazo_recogida_dias)
    por_vencimiento = limite_por_vencimiento(solicitud, config)
    return min(por_plazo, por_vencimiento) if por_vencimiento else por_plazo


def novedades_pendientes(solicitud):
    """
    RN-31: novedades Pendiente sobre algún alimento no cancelado de la
    solicitud o sobre toda su donación.
    """
    alimentos = list(
        solicitud.detalles.filter(cancelado=False).values_list("donacion_alimento_id", flat=True)
    )
    return Novedad.objects.filter(estado=Novedad.Estado.PENDIENTE).filter(
        Q(donacion_alimento_id__in=alimentos)
        | Q(donacion_id=solicitud.donacion_id, donacion_alimento__isnull=True)
    )


def _mensaje_novedad(accion):
    return (
        f"No se puede {accion}: hay una novedad pendiente sobre alimentos de esta solicitud. "
        "Espera a que el administrador la resuelva."
    )


def _bloquear_solicitud(solicitud_id, **filtros):
    """Regla 6: solo la fila de la solicitud (no el donante ni el beneficiario que se leen con ella)."""
    try:
        return (
            Solicitud.objects.select_for_update(of=("self",))
            .select_related("donacion__donante", "beneficiario")
            .get(pk=solicitud_id, **filtros)
        )
    except Solicitud.DoesNotExist:
        raise NotFound("No encontramos esa solicitud.")


def _notificar_ambos(solicitud, tipo, titulo, mensaje_beneficiario, mensaje_donante):
    crear_notificacion(solicitud.beneficiario, tipo, titulo, mensaje_beneficiario, solicitud=solicitud)
    crear_notificacion(solicitud.donacion.donante, tipo, titulo, mensaje_donante, solicitud=solicitud)


# ---------------------------------------------------------------------
# Vencimientos (RN-27 y RN-28)
# ---------------------------------------------------------------------
def marcar_no_recogida_si_vencida(solicitud, ahora=None):
    """
    RN-27: Aprobada con fecha_limite_recogida vencida -> No recogida.
    Devuelve las cantidades de los detalles no cancelados, recalcula la
    donación y notifica a ambos. Por RN-31 no aplica si hay una novedad
    pendiente (no es culpa del beneficiario).
    Requiere la solicitud bloqueada. Devuelve True si cambió.
    """
    ahora = ahora or timezone.now()
    if (
        solicitud.estado != Solicitud.Estado.APROBADA
        or solicitud.fecha_limite_recogida is None
        or solicitud.fecha_limite_recogida >= ahora
        or novedades_pendientes(solicitud).exists()
    ):
        return False

    solicitud.estado = Solicitud.Estado.NO_RECOGIDA
    solicitud.save(update_fields=["estado", "fecha_actualizacion"])
    devolver_cantidades(solicitud)
    titulo = solicitud.donacion.titulo
    _notificar_ambos(
        solicitud,
        Notificacion.Tipo.SOLICITUD_NO_RECOGIDA,
        "Solicitud no recogida",
        f"Tu solicitud {solicitud.codigo_pedido} de «{titulo}» venció sin ser entregada "
        f"(fecha límite: {_texto_fecha(solicitud.fecha_limite_recogida)}).",
        f"La solicitud {solicitud.codigo_pedido} de {solicitud.beneficiario.nombre_para_mostrar} "
        f"sobre tu donación «{titulo}» venció sin ser entregada. "
        "Los alimentos volvieron a estar disponibles.",
    )
    return True


def cancelar_si_pendiente_vencida(solicitud, ahora=None):
    """
    RN-28: Pendiente cuyo vencimiento más cercano − margen ya pasó: ya no se
    puede aprobar. Pasa a Cancelada, devuelve cantidades, recalcula la
    donación y notifica a ambos. Sin sanción.
    Requiere la solicitud bloqueada. Devuelve True si cambió.
    """
    if solicitud.estado != Solicitud.Estado.PENDIENTE:
        return False
    limite = limite_por_vencimiento(solicitud, Configuracion.objects.get(pk=1))
    if limite is None or limite >= (ahora or timezone.now()):
        return False

    solicitud.estado = Solicitud.Estado.CANCELADA
    solicitud.motivo_cancelacion = MOTIVO_PENDIENTE_VENCIDA
    solicitud.save(update_fields=["estado", "motivo_cancelacion", "fecha_actualizacion"])
    devolver_cantidades(solicitud)
    titulo = solicitud.donacion.titulo
    _notificar_ambos(
        solicitud,
        Notificacion.Tipo.SOLICITUD_CANCELADA,
        "Solicitud cancelada por vencimiento",
        f"Tu solicitud {solicitud.codigo_pedido} de «{titulo}» se canceló: "
        "los alimentos vencieron antes de ser aprobada.",
        f"La solicitud {solicitud.codigo_pedido} de {solicitud.beneficiario.nombre_para_mostrar} "
        f"sobre tu donación «{titulo}» se canceló: los alimentos vencieron antes de ser aprobada.",
    )
    return True


def aplicar_vencimientos(solicitud_id):
    """
    Aplica RN-27 y RN-28 a una solicitud en su PROPIA transacción, para que
    el cambio quede guardado aunque la acción que sigue (aprobar, rechazar
    o entregar) termine en error.
    """
    with transaction.atomic():
        solicitud = _bloquear_solicitud(solicitud_id)
        return marcar_no_recogida_si_vencida(solicitud) or cancelar_si_pendiente_vencida(solicitud)


def _exigir_estado(solicitud, estado, accion):
    if solicitud.estado == estado:
        return
    motivo = f" Motivo: {solicitud.motivo_cancelacion}." if solicitud.motivo_cancelacion else ""
    raise ValidationError(
        f"No se puede {accion}: la solicitud está {solicitud.estado} y debe estar {estado}.{motivo}"
    )


def _texto_entrega(solicitud):
    if solicitud.modalidad_entrega == Solicitud.ModalidadEntrega.RECOGER:
        return f"Recoger en {solicitud.donacion.direccion_recogida}"
    return f"Domicilio a {solicitud.direccion_entrega}"


# ---------------------------------------------------------------------
# Aprobar (RN-24)
# ---------------------------------------------------------------------
def aprobar_solicitud(admin, solicitud_id):
    aplicar_vencimientos(solicitud_id)
    with transaction.atomic():
        solicitud = _bloquear_solicitud(solicitud_id)
        _exigir_estado(solicitud, Solicitud.Estado.PENDIENTE, "aprobar")
        if novedades_pendientes(solicitud).exists():
            raise ValidationError(_mensaje_novedad("aprobar"))

        ahora = timezone.now()
        limite = calcular_fecha_limite(solicitud, Configuracion.objects.get(pk=1), ahora)
        if limite <= ahora:
            raise ValidationError(
                "No se puede aprobar: por el vencimiento de los alimentos ya no queda tiempo para recogerlos."
            )

        solicitud.estado = Solicitud.Estado.APROBADA
        solicitud.gestionada_por = admin
        solicitud.fecha_gestion = ahora
        solicitud.fecha_limite_recogida = limite
        solicitud.save(update_fields=[
            "estado", "gestionada_por", "fecha_gestion", "fecha_limite_recogida", "fecha_actualizacion",
        ])

        donante = solicitud.donacion.donante
        titulo = solicitud.donacion.titulo
        entrega = _texto_entrega(solicitud)
        fecha = _texto_fecha(limite)
        crear_notificacion(
            solicitud.beneficiario,
            Notificacion.Tipo.SOLICITUD_APROBADA,
            "Tu solicitud fue aprobada",
            f"Tu solicitud {solicitud.codigo_pedido} de «{titulo}» fue aprobada. {entrega}. "
            f"Fecha límite: {fecha}. Donante: {donante.nombre_para_mostrar}, teléfono {donante.telefono}.",
            solicitud=solicitud,
        )
        crear_notificacion(
            donante,
            Notificacion.Tipo.SOLICITUD_APROBADA,
            "Se aprobó una solicitud de tu donación",
            f"Se aprobó la solicitud {solicitud.codigo_pedido} de "
            f"{solicitud.beneficiario.nombre_para_mostrar} sobre «{titulo}». {entrega}. "
            f"Fecha límite: {fecha}.",
            solicitud=solicitud,
        )
    return solicitud


# ---------------------------------------------------------------------
# Rechazar (RN-25)
# ---------------------------------------------------------------------
def rechazar_solicitud(admin, solicitud_id, motivo):
    aplicar_vencimientos(solicitud_id)
    with transaction.atomic():
        solicitud = _bloquear_solicitud(solicitud_id)
        _exigir_estado(solicitud, Solicitud.Estado.PENDIENTE, "rechazar")
        solicitud.estado = Solicitud.Estado.RECHAZADA
        solicitud.motivo_rechazo = motivo
        solicitud.gestionada_por = admin
        solicitud.fecha_gestion = timezone.now()
        solicitud.save(update_fields=[
            "estado", "motivo_rechazo", "gestionada_por", "fecha_gestion", "fecha_actualizacion",
        ])
        devolver_cantidades(solicitud)  # también recalcula la donación (RN-16)
        crear_notificacion(
            solicitud.beneficiario,
            Notificacion.Tipo.SOLICITUD_RECHAZADA,
            "Tu solicitud fue rechazada",
            f"Tu solicitud {solicitud.codigo_pedido} de «{solicitud.donacion.titulo}» fue rechazada. "
            f"Motivo: {motivo}",
            solicitud=solicitud,
        )
    return solicitud


# ---------------------------------------------------------------------
# Entregar (RN-26)
# ---------------------------------------------------------------------
def registrar_entrega(usuario, solicitud_id, observacion=None, solo_donante=False):
    """
    La registra el administrador o el donante dueño de la donación
    (solo_donante=True), sobre una solicitud Aprobada. Es total: cualquier
    diferencia va en la observación.
    """
    filtros = {"donacion__donante": usuario} if solo_donante else {}
    if not Solicitud.objects.filter(pk=solicitud_id, **filtros).exists():
        raise NotFound("No encontramos esa solicitud.")

    aplicar_vencimientos(solicitud_id)
    with transaction.atomic():
        solicitud = _bloquear_solicitud(solicitud_id, **filtros)
        _exigir_estado(solicitud, Solicitud.Estado.APROBADA, "registrar la entrega")
        if novedades_pendientes(solicitud).exists():
            raise ValidationError(_mensaje_novedad("registrar la entrega"))

        entrega = Entrega.objects.create(
            solicitud=solicitud, registrada_por=usuario, observacion=observacion or None
        )
        solicitud.estado = Solicitud.Estado.ENTREGADA
        solicitud.save(update_fields=["estado", "fecha_actualizacion"])
        recalcular_estado_donacion(solicitud.donacion)
        crear_notificacion(
            solicitud.beneficiario,
            Notificacion.Tipo.SOLICITUD_ENTREGADA,
            "Tu solicitud fue entregada",
            f"Se registró la entrega de tu solicitud {solicitud.codigo_pedido} de "
            f"«{solicitud.donacion.titulo}».",
            solicitud=solicitud,
        )
    return entrega


# ---------------------------------------------------------------------
# Donante: solicitudes recibidas (RN-15)
# ---------------------------------------------------------------------
def solicitudes_recibidas(donacion):
    """
    Por cada alimento de la donación: quién pidió, cuánto y en qué estado.
    Teléfono y dirección del beneficiario solo si la solicitud está
    Aprobada, Entregada o No recogida. La dirección es la de entrega si es
    Domicilio; si es Recoger, la del perfil del beneficiario.
    """
    detalles = (
        DetalleSolicitud.objects.filter(solicitud__donacion=donacion)
        .select_related("solicitud__beneficiario")
        .order_by("solicitud__fecha_solicitud", "id")
    )
    por_alimento = OrderedDict(
        (a.pk, {"alimento": a, "solicitudes": []}) for a in donacion.alimentos.order_by("id")
    )
    for detalle in detalles:
        solicitud = detalle.solicitud
        beneficiario = solicitud.beneficiario
        con_contacto = solicitud.estado in Solicitud.ESTADOS_CON_CONTACTO
        direccion = (
            solicitud.direccion_entrega
            if solicitud.modalidad_entrega == Solicitud.ModalidadEntrega.DOMICILIO
            else beneficiario.direccion
        )
        por_alimento[detalle.donacion_alimento_id]["solicitudes"].append({
            "solicitud": solicitud,
            "detalle": detalle,
            "beneficiario": {
                "nombre": beneficiario.nombre_para_mostrar,
                "telefono": beneficiario.telefono if con_contacto else None,
                "direccion": direccion if con_contacto else None,
            },
        })
    return list(por_alimento.values())
