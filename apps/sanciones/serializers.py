from rest_framework import serializers

from apps.usuarios.models import Usuario

from .models import Sancion
from .services import Accion


class UsuarioSancionadoSerializer(serializers.ModelSerializer):
    rol = serializers.CharField(source="rol.nombre", read_only=True)
    nombre = serializers.CharField(source="nombre_para_mostrar", read_only=True)

    class Meta:
        model = Usuario
        fields = ("id", "nombre", "correo", "rol", "estado", "bloqueado_hasta")
        read_only_fields = fields


class ConteosSerializer(serializers.Serializer):
    """Lo que cambia (o se informa) al aplicar la acción (RN-70 a RN-74)."""

    solicitudes_canceladas = serializers.IntegerField()
    items_carrito = serializers.IntegerField(help_text="Alimentos que se quitan del carrito.")
    donaciones_rechazadas = serializers.IntegerField()
    donaciones_finalizadas = serializers.IntegerField()
    alimentos_retirados = serializers.IntegerField()
    alimentos_ocultos = serializers.IntegerField(
        help_text="Alimentos que dejan de mostrarse mientras dure un bloqueo temporal (RN-72)."
    )
    solicitudes_que_siguen = serializers.IntegerField(help_text="Solicitudes en curso que no se cancelan.")
    donaciones_en_espera = serializers.IntegerField(help_text="Donaciones Pendiente que esperan revisión.")


class AdminSancionSerializer(serializers.ModelSerializer):
    usuario = UsuarioSancionadoSerializer(read_only=True)
    creada_por = serializers.EmailField(source="creada_por.correo", read_only=True)

    class Meta:
        model = Sancion
        fields = (
            "id", "usuario", "tipo", "motivo", "donacion", "solicitud",
            "creada_por", "fecha_inicio", "fecha_fin", "vigente",
        )
        read_only_fields = fields


class SancionCreadaSerializer(AdminSancionSerializer):
    efectos = ConteosSerializer(read_only=True, help_text="Lo que se afectó al aplicar la sanción.")

    class Meta(AdminSancionSerializer.Meta):
        fields = (*AdminSancionSerializer.Meta.fields, "efectos")
        read_only_fields = fields


class CrearSancionSerializer(serializers.Serializer):
    usuario = serializers.IntegerField(help_text="id del usuario a sancionar.")
    tipo = serializers.ChoiceField(
        choices=Sancion.Tipo.choices,
        help_text="Consulta antes la sugerencia en admin/usuarios/{id}/impacto/ (RN-05).",
        error_messages={"invalid_choice": "El tipo debe ser Advertencia, Bloqueo temporal o Bloqueo permanente."},
    )
    motivo = serializers.CharField(
        max_length=255,
        error_messages={
            "required": "El motivo de la sanción es obligatorio.",
            "blank": "El motivo de la sanción es obligatorio.",
        },
    )
    donacion = serializers.IntegerField(required=False, allow_null=True, help_text="Donación relacionada (opcional).")
    solicitud = serializers.IntegerField(required=False, allow_null=True, help_text="Solicitud relacionada (opcional).")


class ImpactoConsultaSerializer(serializers.Serializer):
    accion = serializers.ChoiceField(
        choices=Accion.CHOICES,
        error_messages={
            "required": "Indica la acción: advertencia, bloqueo_temporal, bloqueo_permanente o inactivar.",
            "invalid_choice": "La acción debe ser advertencia, bloqueo_temporal, bloqueo_permanente o inactivar.",
        },
    )


class HistorialSerializer(serializers.Serializer):
    advertencias = serializers.IntegerField(source="Advertencia")
    bloqueos_temporales = serializers.IntegerField(source="Bloqueo temporal")
    bloqueos_permanentes = serializers.IntegerField(source="Bloqueo permanente")


class ImpactoSerializer(serializers.Serializer):
    usuario = UsuarioSancionadoSerializer()
    accion = serializers.CharField()
    sancion_sugerida = serializers.ChoiceField(
        choices=Sancion.Tipo.choices,
        allow_null=True,
        help_text="Siguiente nivel de la escala según todas sus sanciones (RN-05). Null si ya tiene un bloqueo permanente.",
    )
    historial = HistorialSerializer(help_text="Sanciones anteriores del usuario, vigentes o no.")
    aviso = serializers.CharField(allow_null=True)
    conteos = ConteosSerializer()
    resumen = serializers.ListField(child=serializers.CharField())
