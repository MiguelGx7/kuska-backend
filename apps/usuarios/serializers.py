from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings

from . import services
from .models import Rol, Usuario

CAMPO_CONTRASENA = {"write_only": True, "trim_whitespace": False, "style": {"input_type": "password"}}


class UsuarioSerializer(serializers.ModelSerializer):
    """Datos del usuario para mostrar (nunca incluye la contraseña)."""

    rol = serializers.SlugRelatedField(slug_field="nombre", read_only=True)

    class Meta:
        model = Usuario
        fields = (
            "id",
            "correo",
            "rol",
            "nombres",
            "apellidos",
            "tipo_documento",
            "numero_documento",
            "telefono",
            "direccion",
            "estado",
            "tipo_donante",
            "nombre_negocio",
            "nit",
            "fecha_registro",
        )
        read_only_fields = fields


class UsuarioBasicoSerializer(serializers.ModelSerializer):
    rol = serializers.SlugRelatedField(slug_field="nombre", read_only=True)

    class Meta:
        model = Usuario
        fields = ("id", "correo", "rol", "nombres", "apellidos", "estado", "tipo_donante", "nombre_negocio")
        read_only_fields = fields


# ---------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------
class DatosRegistroSerializer(serializers.Serializer):
    """Datos de la cuenta (sin código). También lo usa cargar_demo."""

    correo = serializers.EmailField(max_length=120)
    contrasena = serializers.CharField(max_length=128, **CAMPO_CONTRASENA)
    rol = serializers.ChoiceField(
        choices=services.ROLES_REGISTRO,
        error_messages={"invalid_choice": "Solo puedes registrarte como Donante o Beneficiario."},
    )
    nombres = serializers.CharField(max_length=80)
    apellidos = serializers.CharField(max_length=80)
    tipo_documento = serializers.ChoiceField(choices=Usuario.TipoDocumento.choices)
    numero_documento = serializers.CharField(max_length=20)
    telefono = serializers.CharField(max_length=20)
    direccion = serializers.CharField(
        max_length=200, required=False, allow_blank=True, allow_null=True
    )
    tipo_donante = serializers.ChoiceField(
        choices=Usuario.TipoDonante.choices,
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Obligatorio para Donante. Vacío para Beneficiario.",
    )
    nombre_negocio = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Solo si tipo_donante es Negocio.",
    )
    nit = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Solo si tipo_donante es Negocio.",
    )


class RegistroSerializer(DatosRegistroSerializer):
    """Registro público (RN-04d): los datos más el código que llegó al correo."""

    codigo = serializers.RegexField(
        r"^\d{6}$",
        help_text="Código de 6 dígitos enviado con auth/registro/solicitar-codigo/.",
        error_messages={
            "invalid": "El código debe tener 6 dígitos.",
            "required": "Escribe el código que te enviamos al correo.",
        },
    )


class SolicitarCodigoRegistroSerializer(serializers.Serializer):
    correo = serializers.EmailField(
        max_length=120,
        error_messages={"invalid": "Escribe un correo válido.", "required": "El correo es obligatorio."},
    )


# ---------------------------------------------------------------------
# Login y refresh
# ---------------------------------------------------------------------
class LoginSerializer(serializers.Serializer):
    correo = serializers.EmailField()
    contrasena = serializers.CharField(**CAMPO_CONTRASENA)


class LoginRespuestaSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    usuario = UsuarioBasicoSerializer()


class RefreshSerializer(TokenRefreshSerializer):
    """Igual al de simplejwt (rotación + lista negra), pero exige estado Activo."""

    refresh = serializers.CharField()
    access = serializers.CharField(read_only=True)

    def validate(self, attrs):
        token = self.token_class(attrs["refresh"])
        services.validar_usuario_para_refresh(token.payload.get(api_settings.USER_ID_CLAIM))
        return super().validate(attrs)


class RefreshRespuestaSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


# ---------------------------------------------------------------------
# Perfil
# ---------------------------------------------------------------------
class RechazarNoEditablesMixin:
    """Si llega un dato de solo lectura, responde 400 en vez de ignorarlo."""

    mensaje_no_editable = "Este dato no se puede editar."

    def validate(self, attrs):
        no_editables = sorted(set(self.initial_data) & set(self.Meta.read_only_fields))
        if no_editables:
            raise serializers.ValidationError(
                {campo: [self.mensaje_no_editable] for campo in no_editables}
            )
        return super().validate(attrs)


class PerfilSerializer(RechazarNoEditablesMixin, UsuarioSerializer):
    """Solo se editan nombres, apellidos, teléfono y dirección (RN-04a)."""

    mensaje_no_editable = "Este dato no se puede editar desde el perfil."

    class Meta(UsuarioSerializer.Meta):
        read_only_fields = tuple(
            f for f in UsuarioSerializer.Meta.fields if f not in services.CAMPOS_PERFIL
        )
        extra_kwargs = {"direccion": {"allow_blank": True}}

    def update(self, instance, validated_data):
        return services.actualizar_perfil(instance, validated_data)


class CambiarContrasenaSerializer(serializers.Serializer):
    contrasena_actual = serializers.CharField(**CAMPO_CONTRASENA)
    contrasena_nueva = serializers.CharField(max_length=128, **CAMPO_CONTRASENA)


# ---------------------------------------------------------------------
# Recuperar contraseña
# ---------------------------------------------------------------------
class RecuperarSolicitarSerializer(serializers.Serializer):
    correo = serializers.EmailField()


class RecuperarConfirmarSerializer(serializers.Serializer):
    correo = serializers.EmailField()
    codigo = serializers.RegexField(
        r"^\d{6}$", error_messages={"invalid": "El código debe tener 6 dígitos."}
    )
    contrasena_nueva = serializers.CharField(max_length=128, **CAMPO_CONTRASENA)


class MensajeSerializer(serializers.Serializer):
    mensaje = serializers.CharField()


# ---------------------------------------------------------------------
# Administración de usuarios
# ---------------------------------------------------------------------
class AdminUsuarioSerializer(UsuarioSerializer):
    class Meta(UsuarioSerializer.Meta):
        fields = (*UsuarioSerializer.Meta.fields, "bloqueado_hasta", "last_login", "fecha_actualizacion")
        read_only_fields = fields


class AdminUsuarioEditarSerializer(RechazarNoEditablesMixin, AdminUsuarioSerializer):
    """El administrador solo corrige correo y teléfono (RN-04c)."""

    mensaje_no_editable = "El administrador solo puede corregir el correo y el teléfono."

    class Meta(AdminUsuarioSerializer.Meta):
        read_only_fields = tuple(
            f for f in AdminUsuarioSerializer.Meta.fields if f not in ("correo", "telefono")
        )
        # La unicidad la valida el servicio, con mensaje en español.
        extra_kwargs = {"correo": {"validators": []}}

    def update(self, instance, validated_data):
        return services.actualizar_contacto(instance, validated_data)


class CambiarEstadoSerializer(serializers.Serializer):
    estado = serializers.ChoiceField(
        choices=[Usuario.Estado.ACTIVO, Usuario.Estado.INACTIVO],
        error_messages={"invalid_choice": "Solo se puede cambiar a Activo o Inactivo."},
    )
