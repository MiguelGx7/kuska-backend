"""
Sanciones y efectos de bloquear o inactivar a un usuario
(RN-05 a RN-08, RN-70 a RN-77).

Orden de bloqueos (regla 6): usuario -> sanción -> novedad -> solicitud ->
alimentos (por id) -> configuración -> donación.

La cascada (RN-70 a RN-73) y el impacto (RN-74) salen de la misma función,
`_alcance()`: el impacto cuenta lo que la cascada después cambia, así lo
que ve el administrador coincide con lo que pasa.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.donaciones.models import Donacion, DonacionAlimento
from apps.donaciones.services import alimentos_visibles, recalcular_estado_donacion
from apps.notificaciones.models import Notificacion
from apps.notificaciones.services import crear_notificacion
from apps.solicitudes.models import CarritoItem, DetalleSolicitud, Solicitud
from apps.solicitudes.services import devolver_cantidades
from apps.usuarios.models import Rol, Usuario
from apps.usuarios.services import desbloquear_si_vencido, invalidar_refresh_tokens

from .models import Sancion

logger = logging.getLogger(__name__)

DIAS_BLOQUEO_TEMPORAL = 7

# Efecto sobre lo que está en curso.
SIN_EFECTO = None          # Advertencia (RN-76)
TEMPORAL = "temporal"      # Bloqueo temporal (RN-70, RN-72)
DEFINITIVO = "definitivo"  # Bloqueo permanente o inactivación (RN-71, RN-73)

# Acciones que acepta el impacto (RN-74).
class Accion:
    ADVERTENCIA = "advertencia"
    BLOQUEO_TEMPORAL = "bloqueo_temporal"
    BLOQUEO_PERMANENTE = "bloqueo_permanente"
    INACTIVAR = "inactivar"

    CHOICES = [
        (ADVERTENCIA, "Advertencia"),
        (BLOQUEO_TEMPORAL, "Bloqueo temporal"),
        (BLOQUEO_PERMANENTE, "Bloqueo permanente"),
        (INACTIVAR, "Inactivar"),
    ]


TIPO_DE_ACCION = {
    Accion.ADVERTENCIA: Sancion.Tipo.ADVERTENCIA,
    Accion.BLOQUEO_TEMPORAL: Sancion.Tipo.BLOQUEO_TEMPORAL,
    Accion.BLOQUEO_PERMANENTE: Sancion.Tipo.BLOQUEO_PERMANENTE,
}
EFECTO_DE_TIPO = {
    Sancion.Tipo.ADVERTENCIA: SIN_EFECTO,
    Sancion.Tipo.BLOQUEO_TEMPORAL: TEMPORAL,
    Sancion.Tipo.BLOQUEO_PERMANENTE: DEFINITIVO,
}
ESCALA = [Sancion.Tipo.ADVERTENCIA, Sancion.Tipo.BLOQUEO_TEMPORAL, Sancion.Tipo.BLOQUEO_PERMANENTE]


# ---------------------------------------------------------------------
# Escala (RN-05)
# ---------------------------------------------------------------------
def sancion_sugerida(usuario):
    """
    El siguiente nivel después del más alto que el usuario ya recibió,
    contando TODAS sus sanciones históricas (vigentes o no). None si ya
    tiene un bloqueo permanente (no hay nivel más alto).
    """
    tipos = set(Sancion.objects.filter(usuario=usuario).values_list("tipo", flat=True))
    nivel = max((ESCALA.index(t) for t in tipos), default=-1)
    return ESCALA[nivel + 1] if nivel + 1 < len(ESCALA) else None


def historial(usuario):
    """Cuántas sanciones de cada tipo tiene el usuario (para mostrar junto a la sugerencia)."""
    conteo = {tipo: 0 for tipo in ESCALA}
    for tipo in Sancion.objects.filter(usuario=usuario).values_list("tipo", flat=True):
        conteo[tipo] += 1
    return conteo


# ---------------------------------------------------------------------
# Qué se afecta (RN-70 a RN-73): una sola fuente para el impacto y la cascada
# ---------------------------------------------------------------------
def efecto_real(usuario, efecto):
    """
    Un usuario Inactivo ya tuvo su cascada al inactivarlo (RN-71/RN-73): una
    sanción nueva solo queda registrada y no afecta nada más.
    """
    return SIN_EFECTO if usuario.estado == Usuario.Estado.INACTIVO else efecto


def _alcance(usuario, efecto):
    """
    Devuelve los querysets de lo que cambiaría (o se informa) según el rol
    del usuario y el efecto. Las claves son siempre las mismas.
    """
    vacio = {
        "solicitudes_canceladas": Solicitud.objects.none(),
        "items_carrito": CarritoItem.objects.none(),
        "donaciones_rechazadas": Donacion.objects.none(),
        "donaciones_finalizadas": Donacion.objects.none(),
        "alimentos_retirados": DonacionAlimento.objects.none(),
        "alimentos_ocultos": DonacionAlimento.objects.none(),
        "solicitudes_que_siguen": Solicitud.objects.none(),
        "donaciones_en_espera": Donacion.objects.none(),
    }
    if efecto is SIN_EFECTO:
        return vacio

    rol = usuario.rol.nombre
    if rol == Rol.BENEFICIARIO:
        if efecto == TEMPORAL:
            # RN-70: las Pendiente se cancelan; las Aprobada siguen.
            return {
                **vacio,
                "solicitudes_canceladas": Solicitud.objects.filter(
                    beneficiario=usuario, estado=Solicitud.Estado.PENDIENTE
                ),
                "items_carrito": CarritoItem.objects.filter(beneficiario=usuario),
                "solicitudes_que_siguen": Solicitud.objects.filter(
                    beneficiario=usuario, estado=Solicitud.Estado.APROBADA
                ),
            }
        # RN-71: Pendiente y Aprobada se cancelan.
        return {
            **vacio,
            "solicitudes_canceladas": Solicitud.objects.filter(
                beneficiario=usuario, estado__in=Solicitud.ESTADOS_EN_CURSO
            ),
            "items_carrito": CarritoItem.objects.filter(beneficiario=usuario),
        }

    if rol == Rol.DONANTE:
        en_curso = Solicitud.objects.filter(
            donacion__donante=usuario, estado__in=Solicitud.ESTADOS_EN_CURSO
        )
        pendientes = Donacion.objects.filter(donante=usuario, estado=Donacion.Estado.PENDIENTE)
        if efecto == TEMPORAL:
            # RN-72: nada cambia; sus alimentos dejan de mostrarse mientras dure.
            return {
                **vacio,
                "alimentos_ocultos": alimentos_visibles().filter(donacion__donante=usuario),
                "solicitudes_que_siguen": en_curso,
                "donaciones_en_espera": pendientes,
            }
        # RN-73.
        revisadas = Donacion.objects.filter(
            donante=usuario, estado__in=[Donacion.Estado.VALIDADA, Donacion.Estado.AGOTADA]
        )
        return {
            **vacio,
            "solicitudes_canceladas": en_curso,
            "donaciones_rechazadas": pendientes,
            "donaciones_finalizadas": revisadas,
            "alimentos_retirados": DonacionAlimento.objects.filter(donacion__in=revisadas, activo=True),
        }

    return vacio


def _resumen(conteos, rol, efecto):
    """Frases cortas para que el panel muestre qué se afectará (RN-74)."""
    if efecto is SIN_EFECTO:
        return ["No afecta nada en curso: solo queda registrada."]

    lineas = []
    n = conteos["solicitudes_canceladas"]
    if n:
        if rol == Rol.DONANTE:
            lineas.append(
                f"{n} solicitud(es) de sus donaciones se cancelarán y se avisará a cada beneficiario."
            )
        else:
            lineas.append(f"{n} solicitud(es) se cancelarán y sus cantidades volverán a estar disponibles.")
    if conteos["items_carrito"]:
        lineas.append(f"Se vaciará su carrito ({conteos['items_carrito']} alimento(s)).")
    if conteos["donaciones_rechazadas"]:
        lineas.append(f"{conteos['donaciones_rechazadas']} donación(es) pendiente(s) se rechazarán.")
    if conteos["donaciones_finalizadas"]:
        lineas.append(
            f"{conteos['donaciones_finalizadas']} donación(es) pasarán a Finalizada y "
            f"{conteos['alimentos_retirados']} alimento(s) se retirarán."
        )
    if conteos["alimentos_ocultos"]:
        lineas.append(
            f"{conteos['alimentos_ocultos']} alimento(s) dejarán de mostrarse mientras dure el bloqueo."
        )
    if conteos["solicitudes_que_siguen"]:
        quien = "el administrador registrará las entregas" if rol == Rol.DONANTE else "puede recogerlas"
        lineas.append(f"{conteos['solicitudes_que_siguen']} solicitud(es) en curso seguirán ({quien}).")
    if conteos["donaciones_en_espera"]:
        lineas.append(
            f"{conteos['donaciones_en_espera']} donación(es) pendiente(s) quedan en espera de revisión."
        )
    return lineas or ["No tiene nada en curso que se vea afectado."]


# ---------------------------------------------------------------------
# Cascada (RN-70 a RN-73)
# ---------------------------------------------------------------------
def _motivo(efecto, inactivacion, rol):
    quien = "del donante" if rol == Rol.DONANTE else "del beneficiario"
    if inactivacion:
        return f"Se canceló porque la cuenta {quien} fue desactivada."
    if efecto == TEMPORAL:
        return f"Se canceló por un bloqueo temporal de la cuenta {quien}."
    return f"Se canceló por un bloqueo permanente de la cuenta {quien}."


def aplicar_cascada(usuario, efecto, admin, inactivacion=False):
    """
    Aplica RN-70 a RN-73. Debe llamarse dentro de una transacción con la
    fila del usuario ya bloqueada. Bloquea en el orden de la regla 6:
    solicitudes -> alimentos (por id) -> donaciones (por id). Devuelve los
    conteos de lo que cambió.
    """
    rol = usuario.rol.nombre
    alcance = _alcance(usuario, efecto)
    if efecto is SIN_EFECTO:
        return _contar(alcance)

    solicitudes = list(
        alcance["solicitudes_canceladas"]
        .select_for_update(of=("self",))
        .select_related("donacion__donante", "beneficiario")
        .order_by("id")
    )
    motivo = _motivo(efecto, inactivacion, rol)

    if rol == Rol.BENEFICIARIO:
        # Se bloquean de una vez los alimentos y las donaciones que devolver_cantidades()
        # va a tocar, para respetar el orden aunque haya varias solicitudes.
        alimentos_ids = DetalleSolicitud.objects.filter(
            solicitud__in=solicitudes, cancelado=False
        ).values_list("donacion_alimento_id", flat=True)
        list(DonacionAlimento.objects.select_for_update().filter(pk__in=list(alimentos_ids)).order_by("id"))
        list(
            Donacion.objects.select_for_update()
            .filter(pk__in={s.donacion_id for s in solicitudes})
            .order_by("id")
        )

        for solicitud in solicitudes:
            solicitud.estado = Solicitud.Estado.CANCELADA
            solicitud.motivo_cancelacion = motivo
            solicitud.save(update_fields=["estado", "motivo_cancelacion", "fecha_actualizacion"])
            devolver_cantidades(solicitud)  # también recalcula la donación (RN-16)
            # RN-71 avisa al donante. RN-70 no lo pide: solo cancela Pendiente, de las
            # que el donante no recibió aviso al crearse (las ve solo con el nombre, RN-15).
            if efecto == DEFINITIVO:
                crear_notificacion(
                    solicitud.donacion.donante,
                    Notificacion.Tipo.SOLICITUD_CANCELADA,
                    "Se canceló una solicitud de tu donación",
                    f"La solicitud {solicitud.codigo_pedido} de "
                    f"{solicitud.beneficiario.nombre_para_mostrar} sobre «{solicitud.donacion.titulo}» "
                    "se canceló porque el beneficiario ya no puede recibir donaciones. "
                    "Los alimentos volvieron a estar disponibles.",
                    solicitud=solicitud,
                )
        carrito = alcance["items_carrito"].delete()[0]
        conteos = _contar(alcance, solicitudes=len(solicitudes))
        conteos["items_carrito"] = carrito
        return conteos

    if rol == Rol.DONANTE and efecto == DEFINITIVO:
        # Alimentos antes que donaciones (regla 6). Se bloquean los de todas sus
        # donaciones abiertas: si una Pendiente se valida justo ahora, validar_donacion()
        # espera a que esto termine o esto espera a que ella termine, y el estado
        # se revisa ya con la donación bloqueada.
        donaciones_ids = [
            *alcance["donaciones_rechazadas"].values_list("id", flat=True),
            *alcance["donaciones_finalizadas"].values_list("id", flat=True),
        ]
        alimentos = list(
            DonacionAlimento.objects.select_for_update().filter(donacion_id__in=donaciones_ids).order_by("id")
        )
        donaciones = list(Donacion.objects.select_for_update().filter(pk__in=donaciones_ids).order_by("id"))
        revisadas = {
            d.pk for d in donaciones if d.estado in (Donacion.Estado.VALIDADA, Donacion.Estado.AGOTADA)
        }
        alimentos = [a for a in alimentos if a.donacion_id in revisadas]
        conteos = _contar(alcance, solicitudes=len(solicitudes))
        conteos["donaciones_rechazadas"] = sum(d.estado == Donacion.Estado.PENDIENTE for d in donaciones)
        conteos["donaciones_finalizadas"] = len(revisadas)
        conteos["alimentos_retirados"] = sum(a.activo for a in alimentos)

        for solicitud in solicitudes:
            solicitud.estado = Solicitud.Estado.CANCELADA
            solicitud.motivo_cancelacion = motivo
            solicitud.save(update_fields=["estado", "motivo_cancelacion", "fecha_actualizacion"])
            crear_notificacion(
                solicitud.beneficiario,
                Notificacion.Tipo.SOLICITUD_CANCELADA,
                "Tu solicitud fue cancelada",
                f"Tu solicitud {solicitud.codigo_pedido} de «{solicitud.donacion.titulo}» se canceló "
                "porque el donante ya no puede entregar donaciones. Puedes pedir otros alimentos "
                "disponibles.",
                solicitud=solicitud,
            )

        # No se devuelven cantidades: los alimentos se retiran del todo.
        for alimento in alimentos:
            alimento.activo = False
            alimento.cantidad_disponible = 0
        DonacionAlimento.objects.bulk_update(alimentos, ["activo", "cantidad_disponible"])

        ahora = timezone.now()
        motivo_donacion = (
            "Rechazada porque la cuenta del donante fue desactivada."
            if inactivacion
            else "Rechazada por un bloqueo permanente de la cuenta del donante."
        )
        for donacion in donaciones:
            if donacion.estado == Donacion.Estado.PENDIENTE:
                donacion.estado = Donacion.Estado.RECHAZADA
                donacion.motivo_rechazo = motivo_donacion
                donacion.revisada_por = admin
                donacion.fecha_revision = ahora
                donacion.save()
            elif donacion.pk in revisadas:
                # Sin alimentos disponibles ni solicitudes en curso: queda Finalizada (RN-16).
                recalcular_estado_donacion(donacion)
        return conteos

    # RN-72 (donante con bloqueo temporal): no se cambia nada; sus alimentos se
    # ocultan solos porque RN-14 exige que el donante esté Activo.
    return _contar(alcance)


def _contar(alcance, solicitudes=None):
    conteos = {clave: qs.count() for clave, qs in alcance.items()}
    if solicitudes is not None:
        conteos["solicitudes_canceladas"] = solicitudes
    return conteos


# ---------------------------------------------------------------------
# Validaciones compartidas por el impacto y la creación
# ---------------------------------------------------------------------
def _exigir_no_administrador(usuario):
    # RN-04: un administrador no se sanciona ni se inactiva desde la API.
    if usuario.rol.nombre == Rol.ADMINISTRADOR:
        raise PermissionDenied("No se puede sancionar ni cambiar el estado de un Administrador.")


def _sanciones_vigentes(usuario, bloquear=False):
    """Bloqueos vigentes del usuario. Antes marca vigente = 0 los temporales vencidos."""
    ahora = timezone.now()
    Sancion.objects.filter(
        usuario=usuario, tipo=Sancion.Tipo.BLOQUEO_TEMPORAL, vigente=True, fecha_fin__lte=ahora
    ).update(vigente=False)
    vigentes = Sancion.objects.filter(
        usuario=usuario,
        vigente=True,
        tipo__in=[Sancion.Tipo.BLOQUEO_TEMPORAL, Sancion.Tipo.BLOQUEO_PERMANENTE],
    ).order_by("id")
    if bloquear:
        vigentes = vigentes.select_for_update()
    return list(vigentes)


def _validar_tipo(usuario, tipo, vigentes):
    """Casos límite de RN-05/RN-06 (ver docs/decisiones.md, Paso 9)."""
    if any(s.tipo == Sancion.Tipo.BLOQUEO_PERMANENTE for s in vigentes):
        raise ValidationError(
            {"tipo": ["El usuario ya tiene un bloqueo permanente, que es definitivo: no se puede sancionar más."]}
        )
    temporal = next((s for s in vigentes if s.tipo == Sancion.Tipo.BLOQUEO_TEMPORAL), None)
    if temporal and tipo == Sancion.Tipo.BLOQUEO_TEMPORAL:
        hasta = timezone.localtime(temporal.fecha_fin)
        raise ValidationError(
            {"tipo": [
                f"El usuario ya tiene un bloqueo temporal vigente hasta el {hasta:%d/%m/%Y a las %H:%M}. "
                "Los bloqueos temporales no se acumulan: si la falta es grave, aplica el bloqueo permanente."
            ]}
        )


def _usuario_o_404(usuario_id, bloquear=False):
    consulta = Usuario.objects.select_related("rol")
    if bloquear:
        # of=("self",): solo el usuario, no la fila compartida de su rol.
        consulta = consulta.select_for_update(of=("self",))
    usuario = consulta.filter(pk=usuario_id).first()
    if usuario is None:
        raise NotFound("No encontramos ese usuario.")
    return usuario


# ---------------------------------------------------------------------
# Impacto (RN-74): cuenta sin aplicar nada
# ---------------------------------------------------------------------
def calcular_impacto(usuario_id, accion):
    usuario = _usuario_o_404(usuario_id)
    _exigir_no_administrador(usuario)
    desbloquear_si_vencido(usuario)  # RN-03: no mostrar como bloqueado a quien ya venció

    aviso = None
    if accion == Accion.INACTIVAR:
        if usuario.estado == Usuario.Estado.INACTIVO:
            raise ValidationError({"accion": ["El usuario ya está inactivo."]})
        efecto = DEFINITIVO
    else:
        tipo = TIPO_DE_ACCION[accion]
        _validar_tipo(usuario, tipo, _sanciones_vigentes(usuario))
        efecto = efecto_real(usuario, EFECTO_DE_TIPO[tipo])
        if usuario.estado == Usuario.Estado.INACTIVO:
            aviso = (
                "El usuario está inactivo: la sanción solo queda registrada. "
                "Si se reactiva, entrará Bloqueado mientras el bloqueo siga vigente (RN-75)."
            )

    conteos = _contar(_alcance(usuario, efecto))
    return {
        "usuario": usuario,
        "accion": accion,
        "sancion_sugerida": sancion_sugerida(usuario),
        "historial": historial(usuario),
        "aviso": aviso,
        "conteos": conteos,
        "resumen": _resumen(conteos, usuario.rol.nombre, efecto),
    }


# ---------------------------------------------------------------------
# Crear una sanción (RN-05 a RN-08)
# ---------------------------------------------------------------------
def _validar_relacion(usuario, donacion_id, solicitud_id):
    """La donación o solicitud debe ser del usuario (como donante o como beneficiario)."""
    donacion = solicitud = None
    errores = {}
    if solicitud_id is not None:
        solicitud = Solicitud.objects.select_related("donacion").filter(pk=solicitud_id).first()
        if solicitud is None:
            errores["solicitud"] = ["No encontramos esa solicitud."]
        elif usuario.pk not in (solicitud.beneficiario_id, solicitud.donacion.donante_id):
            errores["solicitud"] = ["Esa solicitud no es de este usuario (ni como beneficiario ni como donante)."]
    if donacion_id is not None:
        donacion = Donacion.objects.filter(pk=donacion_id).first()
        if donacion is None:
            errores["donacion"] = ["No encontramos esa donación."]
        elif donacion.donante_id != usuario.pk and not Solicitud.objects.filter(
            donacion=donacion, beneficiario=usuario
        ).exists():
            errores["donacion"] = ["Esa donación no es de este usuario (ni como donante ni como beneficiario)."]
    if solicitud and donacion and solicitud.donacion_id != donacion.pk:
        errores["solicitud"] = ["La solicitud no pertenece a la donación indicada."]
    if errores:
        raise ValidationError(errores)

    repetidas = {}
    if solicitud and Sancion.objects.filter(usuario=usuario, solicitud=solicitud).exists():
        repetidas["solicitud"] = ["Este usuario ya fue sancionado por esta solicitud."]
    if donacion and Sancion.objects.filter(usuario=usuario, donacion=donacion).exists():
        repetidas["donacion"] = ["Este usuario ya fue sancionado por esta donación."]
    if repetidas:
        raise ValidationError(repetidas)
    return donacion, solicitud


def _mensaje_sancion(sancion, conteos):
    texto = {
        Sancion.Tipo.ADVERTENCIA: "Recibiste una advertencia.",
        Sancion.Tipo.BLOQUEO_TEMPORAL: (
            "Tu cuenta fue bloqueada hasta el "
            f"{timezone.localtime(sancion.fecha_fin):%d/%m/%Y a las %H:%M}."
            if sancion.fecha_fin
            else ""
        ),
        Sancion.Tipo.BLOQUEO_PERMANENTE: "Tu cuenta fue bloqueada de forma permanente.",
    }[sancion.tipo]
    partes = [texto, f"Motivo: {sancion.motivo}."]
    if conteos["solicitudes_canceladas"]:
        partes.append(f"Se cancelaron {conteos['solicitudes_canceladas']} solicitud(es) en curso.")
    if conteos["donaciones_rechazadas"] or conteos["donaciones_finalizadas"]:
        partes.append("Tus donaciones en curso se cerraron.")
    if sancion.tipo == Sancion.Tipo.ADVERTENCIA:
        partes.append("Si se repite, la siguiente sanción será un bloqueo temporal.")
    return " ".join(partes)


def crear_sancion(admin, usuario_id, tipo, motivo, donacion_id=None, solicitud_id=None):
    """
    Registra la sanción y aplica sus efectos (RN-06), la cascada (RN-70 a
    RN-73) y la notificación (RN-08). Devuelve (sancion, conteos).
    """
    usuario = _usuario_o_404(usuario_id)
    _exigir_no_administrador(usuario)
    desbloquear_si_vencido(usuario)  # RN-03: un bloqueo ya vencido se levanta antes

    with transaction.atomic():
        # Regla 6: primero el usuario (turno: dos sanciones a la vez no pasan juntas
        # las validaciones), luego sus sanciones vigentes.
        usuario = _usuario_o_404(usuario_id, bloquear=True)
        vigentes = _sanciones_vigentes(usuario, bloquear=True)
        _validar_tipo(usuario, tipo, vigentes)
        donacion, solicitud = _validar_relacion(usuario, donacion_id, solicitud_id)

        ahora = timezone.now()
        sancion = Sancion(
            usuario=usuario,
            tipo=tipo,
            motivo=motivo,
            donacion=donacion,
            solicitud=solicitud,
            creada_por=admin,
            fecha_inicio=ahora,
        )
        if tipo == Sancion.Tipo.BLOQUEO_TEMPORAL:
            sancion.fecha_fin = ahora + timedelta(days=DIAS_BLOQUEO_TEMPORAL)
        if tipo == Sancion.Tipo.BLOQUEO_PERMANENTE:
            # RN-06: el permanente reemplaza al temporal vigente.
            Sancion.objects.filter(pk__in=[s.pk for s in vigentes]).update(vigente=False)
        sancion.save()

        # La cascada va antes de cambiar el estado: así cuenta igual que el impacto
        # (por ejemplo, los alimentos que se ocultan solo son visibles mientras está Activo).
        conteos = aplicar_cascada(usuario, efecto_real(usuario, EFECTO_DE_TIPO[tipo]), admin)
        if tipo != Sancion.Tipo.ADVERTENCIA:
            if usuario.estado != Usuario.Estado.INACTIVO:
                # Un Inactivo sigue Inactivo; RN-75 lo pondrá Bloqueado si se reactiva.
                usuario.estado = Usuario.Estado.BLOQUEADO
                usuario.bloqueado_hasta = sancion.fecha_fin
                usuario.save(update_fields=["estado", "bloqueado_hasta", "fecha_actualizacion"])
            invalidar_refresh_tokens(usuario)

        crear_notificacion(
            usuario,
            Notificacion.Tipo.SANCION,
            f"Sanción: {sancion.tipo}",
            _mensaje_sancion(sancion, conteos),
            donacion=donacion,
            solicitud=solicitud,
        )
    sancion.efectos = conteos
    return sancion


# ---------------------------------------------------------------------
# Bloqueos temporales vencidos (RN-50 con RN-03 y RN-77)
# ---------------------------------------------------------------------
def levantar_bloqueos_vencidos():
    """
    - Bloqueado con bloqueado_hasta vencido: pasa a Activo y su temporal queda
      vigente = 0 (misma función que el login, RN-03). Sus alimentos vuelven a
      mostrarse solos (RN-77), porque RN-14 solo exige que el donante esté Activo.
    - Inactivo (o Activo) con un temporal vencido aún vigente: solo se marca
      vigente = 0; el estado no cambia.
    Cada usuario en su propia transacción. Devuelve los conteos.
    """
    conteos = {"desbloqueados": 0, "sanciones_cerradas": 0, "errores": 0}
    ahora = timezone.now()

    bloqueados = Usuario.objects.filter(
        estado=Usuario.Estado.BLOQUEADO, bloqueado_hasta__isnull=False, bloqueado_hasta__lte=ahora
    ).order_by("id")
    for usuario in bloqueados:
        try:
            desbloquear_si_vencido(usuario)
        except Exception:
            logger.exception("No se pudo levantar el bloqueo del usuario %s", usuario.pk)
            conteos["errores"] += 1
            continue
        if usuario.estado == Usuario.Estado.ACTIVO:
            conteos["desbloqueados"] += 1

    # Los Bloqueado se dejan a la parte de arriba: si allí falló uno, no se le
    # cierra la sanción dejándolo bloqueado sin motivo.
    pendientes = (
        Sancion.objects.filter(
            tipo=Sancion.Tipo.BLOQUEO_TEMPORAL, vigente=True, fecha_fin__lte=ahora
        )
        .exclude(usuario__estado=Usuario.Estado.BLOQUEADO)
        .values_list("usuario_id", flat=True)
        .distinct()
    )
    for usuario_id in sorted(set(pendientes)):
        try:
            with transaction.atomic():
                # Regla 6: usuario -> sanción.
                usuario = Usuario.objects.select_for_update().get(pk=usuario_id)
                if usuario.estado == Usuario.Estado.BLOQUEADO:
                    continue
                conteos["sanciones_cerradas"] += Sancion.objects.filter(
                    usuario=usuario,
                    tipo=Sancion.Tipo.BLOQUEO_TEMPORAL,
                    vigente=True,
                    fecha_fin__lte=ahora,
                ).update(vigente=False)
        except Exception:
            logger.exception("No se pudo cerrar la sanción vencida del usuario %s", usuario_id)
            conteos["errores"] += 1
    return conteos
