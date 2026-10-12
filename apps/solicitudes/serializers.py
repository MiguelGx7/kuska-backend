from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.donaciones.serializers import AlimentoDisponibleSerializer, FotoUrlMixin

from .models import CarritoItem, DetalleSolicitud, Solicitud


# ---------------------------------------------------------------------
# Carrito
# ---------------------------------------------------------------------
class CarritoAgregarSerializer(serializers.Serializer):
    donacion_alimento = serializers.IntegerField(help_text="id del alimento (ver alimentos-disponibles/).")
    cantidad = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class CarritoCantidadSerializer(serializers.Serializer):
    cantidad = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class CarritoItemSerializer(serializers.ModelSerializer):
    alimento = AlimentoDisponibleSerializer(source="donacion_alimento", read_only=True)
    valido = serializers.BooleanField(read_only=True, default=True)
    motivos = serializers.ListField(
        child=serializers.CharField(), read_only=True, default=list,
        help_text="Por qué el ítem ya no se puede enviar (vacío si es válido).",
    )

    class Meta:
        model = CarritoItem
        fields = ("id", "cantidad", "fecha_agregado", "valido", "motivos", "alimento")
        read_only_fields = fields


class CarritoGrupoSerializer(serializers.Serializer):
    donacion = serializers.IntegerField(source="donacion.pk")
    titulo = serializers.CharField(source="donacion.titulo")
    modalidad_entrega = serializers.CharField(
        source="donacion.modalidad_entrega",
        help_text="Si es 'Ambas', al enviar el beneficiario elige Recoger o Domicilio.",
    )
    donante_nombre = serializers.CharField(source="donacion.donante.nombre_para_mostrar")
    items = CarritoItemSerializer(many=True)


class CarritoSerializer(serializers.Serializer):
    donaciones = CarritoGrupoSerializer(many=True)
    avisos = serializers.ListField(child=serializers.CharField())
    puede_enviar = serializers.BooleanField()


class EleccionDonacionSerializer(serializers.Serializer):
    donacion = serializers.IntegerField()
    modalidad_entrega = serializers.ChoiceField(
        choices=Solicitud.ModalidadEntrega.choices, required=False,
        help_text="Obligatoria si la donación es 'Ambas'.",
    )
    direccion_entrega = serializers.CharField(
        max_length=200, required=False, allow_blank=True,
        help_text="Para Domicilio. Si no se envía, se usa la dirección del perfil.",
    )


class EnviarCarritoSerializer(serializers.Serializer):
    donaciones = EleccionDonacionSerializer(many=True, required=False, default=list)

    def validate_donaciones(self, valor):
        ids = [e["donacion"] for e in valor]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Cada donación debe aparecer una sola vez.")
        return valor

    def elecciones(self):
        return {e["donacion"]: e for e in self.validated_data["donaciones"]}


# ---------------------------------------------------------------------
# Solicitudes del beneficiario
# ---------------------------------------------------------------------
class DetalleSolicitudSerializer(FotoUrlMixin, serializers.ModelSerializer):
    alimento = serializers.IntegerField(source="donacion_alimento_id", read_only=True)
    nombre = serializers.CharField(source="donacion_alimento.nombre", read_only=True)
    unidad_medida = serializers.CharField(source="donacion_alimento.unidad_medida", read_only=True)
    foto_url = serializers.SerializerMethodField()

    class Meta:
        model = DetalleSolicitud
        fields = ("id", "alimento", "nombre", "unidad_medida", "cantidad", "cancelado", "foto_url")
        read_only_fields = fields

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_foto_url(self, obj):
        return super().get_foto_url(obj.donacion_alimento)


class DonanteContactoSerializer(serializers.Serializer):
    nombre = serializers.CharField()
    telefono = serializers.CharField(allow_null=True)
    direccion_recogida = serializers.CharField(allow_null=True)


class MiSolicitudSerializer(serializers.ModelSerializer):
    donacion_titulo = serializers.CharField(source="donacion.titulo", read_only=True)
    donante = serializers.SerializerMethodField()
    detalles = DetalleSolicitudSerializer(many=True, read_only=True)

    class Meta:
        model = Solicitud
        fields = (
            "id", "codigo_pedido", "estado", "modalidad_entrega", "direccion_entrega",
            "motivo_rechazo", "motivo_cancelacion", "fecha_solicitud", "fecha_gestion",
            "fecha_limite_recogida", "donacion", "donacion_titulo", "donante", "detalles",
        )
        read_only_fields = fields

    @extend_schema_field(DonanteContactoSerializer)
    def get_donante(self, obj):
        """RN-29: en Pendiente solo el nombre; desde Aprobada, teléfono y dirección de recogida."""
        donante = obj.donacion.donante
        con_contacto = obj.estado in Solicitud.ESTADOS_CON_CONTACTO
        return {
            "nombre": donante.nombre_para_mostrar,
            "telefono": donante.telefono if con_contacto else None,
            "direccion_recogida": obj.donacion.direccion_recogida if con_contacto else None,
        }


class PedidoEnviadoSerializer(serializers.Serializer):
    codigo_pedido = serializers.CharField()
    solicitudes = MiSolicitudSerializer(many=True)
