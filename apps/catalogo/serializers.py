from rest_framework import serializers

from apps.usuarios.serializers import RechazarNoEditablesMixin

from .models import Categoria, Configuracion


class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = ("id", "nombre", "descripcion", "activa")
        read_only_fields = ("id",)
        # La unicidad del nombre la valida el servicio (sin importar mayúsculas).
        extra_kwargs = {"nombre": {"validators": []}, "descripcion": {"allow_blank": True}}


class CategoriaPublicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = ("id", "nombre", "descripcion")
        read_only_fields = fields


class ConfiguracionSerializer(RechazarNoEditablesMixin, serializers.ModelSerializer):
    actualizada_por = serializers.SerializerMethodField()

    class Meta:
        model = Configuracion
        fields = (
            "id",
            "plazo_recogida_dias",
            "margen_vencimiento_dias",
            "max_alimentos_por_solicitud",
            "porcentaje_max_por_alimento",
            "max_solicitudes_activas",
            "actualizada_por",
            "fecha_actualizacion",
        )
        read_only_fields = ("id", "actualizada_por", "fecha_actualizacion")

    def get_actualizada_por(self, obj) -> str | None:
        return obj.actualizada_por.correo if obj.actualizada_por else None
