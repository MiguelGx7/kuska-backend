"""
Lógica de negocio de usuarios y autenticación (RN-01 a RN-04b).

Las vistas y serializers solo validan formato y llaman a estas funciones.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.sanciones.models import Sancion
from config.correo import nombre_backend

from .models import RecuperacionContrasena, Rol, Usuario

logger = logging.getLogger(__name__)

ROLES_REGISTRO = (Rol.DONANTE, Rol.BENEFICIARIO)
CAMPOS_PERFIL = ("nombres", "apellidos", "telefono", "direccion")

MINUTOS_VIGENCIA_CODIGO = 15
MAX_INTENTOS_CODIGO = 5
MENSAJE_RECUPERACION = "Si el correo está registrado, te enviamos un código."
MENSAJE_CODIGO_INVALIDO = "El código no es válido o ya venció. Solicita uno nuevo si es necesario."


def normalizar_correo(correo):
    return correo.strip().lower()


def _validar_contrasena(contrasena, usuario, campo):
    """Aplica los validadores de Django y devuelve los errores en el campo indicado."""
    try:
        validate_password(contrasena, usuario)
    except DjangoValidationError as error:
        raise ValidationError({campo: list(error.messages)})


# ---------------------------------------------------------------------
# Registro (RN-01, RN-02)
# ---------------------------------------------------------------------
def _validar_datos_donante(nombre_rol, datos):
    tipo = datos["tipo_donante"]
    errores = {}

    if nombre_rol == Rol.DONANTE:
        if tipo is None:
            errores["tipo_donante"] = ["Como donante debes indicar si eres Persona o Negocio."]
        elif tipo == Usuario.TipoDonante.NEGOCIO:
            if not datos["nombre_negocio"]:
                errores["nombre_negocio"] = ["El nombre del negocio es obligatorio."]
            if not datos["nit"]:
                errores["nit"] = ["El NIT es obligatorio para un negocio."]
        elif datos["nombre_negocio"] or datos["nit"]:
            errores["tipo_donante"] = [
                "El nombre del negocio y el NIT solo aplican a donantes tipo Negocio."
            ]
    elif tipo or datos["nombre_negocio"] or datos["nit"]:
        errores["rol"] = [
            "Un beneficiario no lleva tipo de donante, nombre de negocio ni NIT."
        ]

    if errores:
        raise ValidationError(errores)


def registrar_usuario(datos):
    """Crea un Donante o Beneficiario. No inicia sesión."""
    datos = dict(datos)
    contrasena = datos.pop("contrasena")
    nombre_rol = datos.pop("rol")

    if nombre_rol not in ROLES_REGISTRO:
        raise ValidationError({"rol": ["Solo puedes registrarte como Donante o Beneficiario."]})

    datos["correo"] = normalizar_correo(datos["correo"])
    # Los opcionales vacíos se guardan como NULL.
    for campo in ("direccion", "tipo_donante", "nombre_negocio", "nit"):
        datos[campo] = datos.get(campo) or None

    _validar_datos_donante(nombre_rol, datos)

    errores = {}
    if Usuario.objects.filter(correo__iexact=datos["correo"]).exists():
        errores["correo"] = ["Ya existe una cuenta con este correo."]
    if Usuario.objects.filter(numero_documento=datos["numero_documento"]).exists():
        errores["numero_documento"] = ["Ya existe una cuenta con este número de documento."]
    if datos["nit"] and Usuario.objects.filter(nit=datos["nit"]).exists():
        errores["nit"] = ["Ya existe una cuenta con este NIT."]
    if errores:
        raise ValidationError(errores)

    usuario = Usuario(rol=Rol.objects.get(nombre=nombre_rol), **datos)
    _validar_contrasena(contrasena, usuario, "contrasena")
    usuario.set_password(contrasena)

    try:
        with transaction.atomic():
            usuario.save()
    except IntegrityError:
        # Dos registros simultáneos con el mismo correo, documento o NIT.
        raise ValidationError("Ya existe una cuenta con este correo, documento o NIT.")
    return usuario


# ---------------------------------------------------------------------
# Estado de la cuenta (RN-03 y permiso EsUsuarioActivo)
# ---------------------------------------------------------------------
def _bloqueo_vencido(usuario, ahora):
    return (
        usuario.estado == Usuario.Estado.BLOQUEADO
        and usuario.bloqueado_hasta is not None
        and usuario.bloqueado_hasta <= ahora
    )


def desbloquear_si_vencido(usuario):
    """
    Si el bloqueo temporal ya venció: pasa a Activo y su sanción temporal
    queda vigente = 0 (RN-03). Bloquea la fila para no chocar con otra
    petición que haga lo mismo al tiempo.
    """
    ahora = timezone.now()
    if not _bloqueo_vencido(usuario, ahora):
        return usuario

    with transaction.atomic():
        bloqueado = Usuario.objects.select_for_update().get(pk=usuario.pk)
        if _bloqueo_vencido(bloqueado, ahora):
            bloqueado.estado = Usuario.Estado.ACTIVO
            bloqueado.bloqueado_hasta = None
            bloqueado.save(update_fields=["estado", "bloqueado_hasta", "fecha_actualizacion"])
            Sancion.objects.filter(
                usuario=bloqueado,
                tipo=Sancion.Tipo.BLOQUEO_TEMPORAL,
                vigente=True,
                fecha_fin__lte=ahora,
            ).update(vigente=False)

    usuario.refresh_from_db(fields=["estado", "bloqueado_hasta", "fecha_actualizacion"])
    return usuario


def motivo_sin_acceso(usuario):
    """
    Devuelve por qué el usuario no puede usar el sistema, o None si está Activo.
    Antes levanta el bloqueo si ya venció.
    """
    desbloquear_si_vencido(usuario)

    if usuario.estado == Usuario.Estado.ACTIVO:
        return None
    if usuario.estado == Usuario.Estado.INACTIVO:
        return "Tu cuenta está inactiva. Comunícate con el administrador."
    if usuario.bloqueado_hasta is None:
        return "Tu cuenta está bloqueada de forma permanente."
    hasta = timezone.localtime(usuario.bloqueado_hasta)
    return f"Tu cuenta está bloqueada hasta el {hasta:%d/%m/%Y} a las {hasta:%H:%M}."


# ---------------------------------------------------------------------
# Login y tokens
# ---------------------------------------------------------------------
def iniciar_sesion(correo, contrasena):
    """Valida credenciales y estado (RN-03). Devuelve (usuario, refresh_token)."""
    usuario = (
        Usuario.objects.select_related("rol")
        .filter(correo__iexact=normalizar_correo(correo))
        .first()
    )
    if usuario is None:
        # Igual se calcula un hash para que el tiempo de respuesta no
        # revele si el correo existe.
        make_password(contrasena)
        raise AuthenticationFailed("Correo o contraseña incorrectos.")
    if not usuario.check_password(contrasena):
        raise AuthenticationFailed("Correo o contraseña incorrectos.")

    motivo = motivo_sin_acceso(usuario)
    if motivo:
        raise PermissionDenied(motivo)

    refresh = RefreshToken.for_user(usuario)
    update_last_login(None, usuario)
    return usuario, refresh


def validar_usuario_para_refresh(user_id):
    """Al renovar el token, el usuario debe seguir existiendo y estar Activo."""
    usuario = Usuario.objects.filter(pk=user_id).first()
    if usuario is None:
        raise AuthenticationFailed("La cuenta ya no existe.")
    motivo = motivo_sin_acceso(usuario)
    if motivo:
        raise AuthenticationFailed(motivo)
    return usuario


def invalidar_refresh_tokens(usuario):
    """Pone en la lista negra todos los refresh tokens vigentes del usuario."""
    pendientes = OutstandingToken.objects.filter(
        user=usuario,
        expires_at__gt=timezone.now(),
        blacklistedtoken__isnull=True,
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in pendientes],
        ignore_conflicts=True,
    )


# ---------------------------------------------------------------------
# Perfil (RN-04a)
# ---------------------------------------------------------------------
def actualizar_perfil(usuario, datos):
    """Solo se editan nombres, apellidos, teléfono y dirección."""
    for campo in CAMPOS_PERFIL:
        if campo in datos:
            setattr(usuario, campo, datos[campo])
    usuario.direccion = usuario.direccion or None
    usuario.save(update_fields=[*CAMPOS_PERFIL, "fecha_actualizacion"])
    return usuario


def cambiar_contrasena(usuario, contrasena_actual, contrasena_nueva):
    if not usuario.check_password(contrasena_actual):
        raise ValidationError({"contrasena_actual": ["La contraseña actual no es correcta."]})
    if contrasena_actual == contrasena_nueva:
        raise ValidationError(
            {"contrasena_nueva": ["La nueva contraseña debe ser diferente a la actual."]}
        )
    _validar_contrasena(contrasena_nueva, usuario, "contrasena_nueva")

    with transaction.atomic():
        usuario.set_password(contrasena_nueva)
        usuario.save(update_fields=["password", "fecha_actualizacion"])
        invalidar_refresh_tokens(usuario)


# ---------------------------------------------------------------------
# Recuperar contraseña (RN-04b)
# ---------------------------------------------------------------------
def _enviar_codigo(usuario, codigo):
    try:
        send_mail(
            subject="Kumora: código para recuperar tu contraseña",
            message=(
                f"Hola, {usuario.nombres}.\n\n"
                f"Tu código para recuperar la contraseña es: {codigo}\n\n"
                f"Vence en {MINUTOS_VIGENCIA_CODIGO} minutos. "
                "Si no lo pediste, ignora este correo.\n\n"
                "Kumora - Red de Alimentos Solidarios"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[usuario.correo],
        )
    except Exception:
        # No se le cuenta al cliente: la respuesta debe ser siempre la misma.
        # En el log va el id del usuario, no su correo ni el código.
        logger.exception("No se pudo enviar el código de recuperación al usuario %s.", usuario.pk)
        return
    logger.info(
        "Código de recuperación enviado al usuario %s (%s).",
        usuario.pk,
        nombre_backend(settings.EMAIL_BACKEND),
    )


def _enmascarar_correo(correo):
    """'dona@mail.com' -> 'do***@mail.com': sirve para revisar sin exponer el correo."""
    local, arroba, dominio = (correo or "").strip().partition("@")
    if not arroba:
        return "***"
    return f"{local[:2]}***@{dominio}"


def solicitar_recuperacion(correo):
    """Genera y envía un código si el correo existe. No revela si existe."""
    usuario = Usuario.objects.filter(correo__iexact=normalizar_correo(correo)).first()
    if usuario is None:
        # Al cliente se le responde igual (RN-04b); en el log, el correo enmascarado.
        logger.warning(
            "Recuperación pedida para un correo que no es de ningún usuario: %s",
            _enmascarar_correo(correo),
        )
        return

    codigo = f"{secrets.randbelow(10**6):06d}"
    with transaction.atomic():
        # Pedir un código nuevo invalida los anteriores.
        RecuperacionContrasena.objects.filter(usuario=usuario, usado=False).update(usado=True)
        RecuperacionContrasena.objects.create(
            usuario=usuario,
            codigo_hash=make_password(codigo),
            fecha_expiracion=timezone.now() + timedelta(minutes=MINUTOS_VIGENCIA_CODIGO),
        )
    _enviar_codigo(usuario, codigo)


def confirmar_recuperacion(correo, codigo, contrasena_nueva):
    """
    Cambia la contraseña si el código es correcto, no está vencido ni usado
    y no superó los intentos. No desbloquea la cuenta (el login aplica RN-03).

    Ante cualquier fallo responde el mismo mensaje, para no revelar si el
    correo existe ni cuántos intentos quedan. El límite de 5 intentos se
    aplica internamente.
    """
    # Primero las reglas de contraseña que no dependen del usuario: así una
    # contraseña débil da el mismo error exista o no el correo, y no gasta
    # un intento.
    _validar_contrasena(contrasena_nueva, None, "contrasena_nueva")

    usuario = Usuario.objects.filter(correo__iexact=normalizar_correo(correo)).first()
    if usuario is None:
        raise ValidationError({"codigo": [MENSAJE_CODIGO_INVALIDO]})

    # Los errores se lanzan fuera de la transacción para que el intento
    # fallido sí quede guardado.
    codigo_valido = False
    with transaction.atomic():
        # Regla 6: primero el usuario (luego se le cambia la contraseña), después su código.
        Usuario.objects.select_for_update().get(pk=usuario.pk)
        recuperacion = (
            RecuperacionContrasena.objects.select_for_update()
            .filter(usuario=usuario, usado=False)
            .order_by("-fecha_creacion")
            .first()
        )
        if (
            recuperacion is None
            or recuperacion.fecha_expiracion <= timezone.now()
            or recuperacion.intentos >= MAX_INTENTOS_CODIGO
        ):
            pass
        elif not check_password(codigo, recuperacion.codigo_hash):
            recuperacion.intentos += 1
            recuperacion.save(update_fields=["intentos"])
        else:
            codigo_valido = True
            # Con el código correcto ya se puede revisar la similitud con
            # los datos del usuario; si falla, el código no se gasta.
            _validar_contrasena(contrasena_nueva, usuario, "contrasena_nueva")
            recuperacion.usado = True
            recuperacion.save(update_fields=["usado"])
            usuario.set_password(contrasena_nueva)
            usuario.save(update_fields=["password", "fecha_actualizacion"])
            invalidar_refresh_tokens(usuario)

    if not codigo_valido:
        raise ValidationError({"codigo": [MENSAJE_CODIGO_INVALIDO]})


# ---------------------------------------------------------------------
# Administración de usuarios (RN-04, RN-04c, RN-75)
# ---------------------------------------------------------------------
def actualizar_contacto(usuario, datos):
    """El administrador corrige el correo y el teléfono (RN-04c)."""
    campos = []
    if "correo" in datos:
        correo = normalizar_correo(datos["correo"])
        if Usuario.objects.filter(correo__iexact=correo).exclude(pk=usuario.pk).exists():
            raise ValidationError({"correo": ["Ya existe una cuenta con este correo."]})
        usuario.correo = correo
        campos.append("correo")
    if "telefono" in datos:
        usuario.telefono = datos["telefono"]
        campos.append("telefono")

    if campos:
        try:
            with transaction.atomic():
                usuario.save(update_fields=[*campos, "fecha_actualizacion"])
        except IntegrityError:
            raise ValidationError({"correo": ["Ya existe una cuenta con este correo."]})
    return usuario


def _bloqueo_vigente(usuario, ahora):
    """
    Devuelve (hay_bloqueo, bloqueado_hasta) según las sanciones vigentes.
    De paso marca vigente = 0 los bloqueos temporales que ya vencieron.
    """
    Sancion.objects.filter(
        usuario=usuario,
        tipo=Sancion.Tipo.BLOQUEO_TEMPORAL,
        vigente=True,
        fecha_fin__lte=ahora,
    ).update(vigente=False)

    vigentes = Sancion.objects.filter(
        usuario=usuario,
        vigente=True,
        tipo__in=[Sancion.Tipo.BLOQUEO_TEMPORAL, Sancion.Tipo.BLOQUEO_PERMANENTE],
    )
    if vigentes.filter(tipo=Sancion.Tipo.BLOQUEO_PERMANENTE).exists():
        return True, None
    temporal = vigentes.order_by("-fecha_fin").first()
    if temporal:
        return True, temporal.fecha_fin
    return False, None


def cambiar_estado(admin, usuario_id, nuevo_estado):
    """
    Cambia un usuario entre Activo e Inactivo (RN-04). Bloqueado solo se pone
    con sanciones. No aplica a administradores ni al propio usuario.
    """
    if nuevo_estado not in (Usuario.Estado.ACTIVO, Usuario.Estado.INACTIVO):
        raise ValidationError({"estado": ["Solo se puede cambiar a Activo o Inactivo."]})

    with transaction.atomic():
        # Se bloquea la fila y se revisa el estado dentro de la transacción.
        # of=("self",): bloquea solo al usuario, no la fila compartida de su rol.
        usuario = (
            Usuario.objects.select_for_update(of=("self",)).select_related("rol").get(pk=usuario_id)
        )

        if usuario.pk == admin.pk:
            raise PermissionDenied("No puedes cambiar tu propio estado.")
        if usuario.rol.nombre == Rol.ADMINISTRADOR:
            raise PermissionDenied("No se puede cambiar el estado de un Administrador.")

        if nuevo_estado == Usuario.Estado.INACTIVO:
            if usuario.estado == Usuario.Estado.INACTIVO:
                raise ValidationError({"estado": ["El usuario ya está inactivo."]})
            # Import local: sanciones/services.py importa este módulo.
            from apps.sanciones.services import DEFINITIVO, aplicar_cascada

            # Se conserva bloqueado_hasta; al reactivar se recalcula con RN-75.
            usuario.estado = Usuario.Estado.INACTIVO
            usuario.save(update_fields=["estado", "fecha_actualizacion"])
            invalidar_refresh_tokens(usuario)
            # RN-71/RN-73: la inactivación (baja) cierra todo lo que está en curso.
            aplicar_cascada(usuario, DEFINITIVO, admin, inactivacion=True)
        else:
            if usuario.estado == Usuario.Estado.ACTIVO:
                raise ValidationError({"estado": ["El usuario ya está activo."]})
            if usuario.estado == Usuario.Estado.BLOQUEADO:
                raise ValidationError(
                    {"estado": ["Un usuario bloqueado solo se desbloquea cuando vence su sanción."]}
                )
            # RN-75: vuelve a Activo, o a Bloqueado si aún tiene un bloqueo vigente.
            bloqueado, hasta = _bloqueo_vigente(usuario, timezone.now())
            usuario.estado = Usuario.Estado.BLOQUEADO if bloqueado else Usuario.Estado.ACTIVO
            usuario.bloqueado_hasta = hasta
            usuario.save(update_fields=["estado", "bloqueado_hasta", "fecha_actualizacion"])

    return usuario
