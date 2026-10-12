import json
from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Donacion, DonacionAlimento

EJEMPLO_DATOS = {
    "titulo": "Mercado de la semana",
    "descripcion": "Productos en buen estado",
    "direccion_recogida": "Calle 10 # 5-20, Bogotá",
    "modalidad_entrega": "Ambas",
    "alimentos": [
        {"categoria": 1, "nombre": "Arroz", "descripcion": "Bolsas de 1 kg",
         "unidad_medida": "kg", "cantidad_total": "5", "fecha_vencimiento": "2027-01-31"},
        {"categoria": 2, "nombre": "Leche", "unidad_medida": "unidad",
         "cantidad_total": "12", "fecha_vencimiento": "2026-12-15"},
    ],
}


# ---------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------
class AlimentoEntradaSerializer(serializers.Serializer):
    categoria = serializers.IntegerField(help_text="id de una categoría activa (ver /api/categorias/).")
    nombre = serializers.CharField(max_length=100)
    descripcion = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    unidad_medida = serializers.ChoiceField(choices=DonacionAlimento.UnidadMedida.choices)
    cantidad_total = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
        help_text="Entera para 'unidad' y 'paquete'; hasta 2 decimales para kg, g, L y ml.",
    )
    fecha_vencimiento = serializers.DateField()


class DonacionEntradaSerializer(serializers.Serializer):
    titulo = serializers.CharField(max_length=120)
    descripcion = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    direccion_recogida = serializers.CharField(
        max_length=200, required=False, allow_blank=True, allow_null=True,
        help_text="Obligatoria si la modalidad es Recoger o Ambas.",
    )
    modalidad_entrega = serializers.ChoiceField(choices=Donacion.ModalidadEntrega.choices)
    alimentos = AlimentoEntradaSerializer(
        many=True,
        allow_empty=False,
        error_messages={"empty": "La donación debe tener al menos un alimento."},
    )

    @classmethod
    def desde_texto(cls, texto):
        """El campo 'datos' del multipart llega como texto JSON."""
        if not texto:
            raise serializers.ValidationError(
                {"datos": ["Envía los datos de la donación en el campo 'datos' (JSON)."]}
            )
        try:
            datos = json.loads(texto)
        except (TypeError, ValueError):
            raise serializers.ValidationError({"datos": ["El campo 'datos' no es un JSON válido."]})
        serializer = cls(data=datos)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class DonacionMultipartSerializer(serializers.Serializer):
    """Solo para documentar en Swagger la petición multipart de crear donación."""

    datos = serializers.CharField(
        help_text="JSON con la donación y su lista 'alimentos'. Ejemplo: "
        + json.dumps(EJEMPLO_DATOS, ensure_ascii=False)
    )
    foto_0 = serializers.ImageField(help_text="Foto del alimento 0 (jpg, png o webp; máx. 5 MB).")
    foto_1 = serializers.ImageField(required=False, help_text="Foto del alimento 1, si existe.")
    foto_2 = serializers.ImageField(required=False, help_text="Foto del alimento 2, si existe.")
    foto_3 = serializers.ImageField(required=False, help_text="Foto del alimento 3. No hay límite: el backend acepta foto_4, foto_5...")


class AlimentoNuevoSerializer(AlimentoEntradaSerializer):
    """Agregar un alimento a una donación Pendiente (multipart)."""

    # Opcional aquí para que el servicio dé el mensaje de RN-11a.
    foto = serializers.FileField(required=False, help_text="Obligatoria: jpg, png o webp; máx. 5 MB.")


class DonacionEditarSerializer(serializers.Serializer):
    titulo = serializers.CharField(max_length=120, required=False)
    descripcion = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    direccion_recogida = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    modalidad_entrega = serializers.ChoiceField(choices=Donacion.ModalidadEntrega.choices, required=False)

    def validate(self, attrs):
        no_editables = sorted(set(self.initial_data) - set(self.fields))
        if no_editables:
            raise serializers.ValidationError(
                {campo: ["Este dato no se puede editar aquí. Los alimentos se agregan o quitan "
                         "en donaciones/{id}/alimentos/."] for campo in no_editables}
            )
        return attrs


class RechazarDonacionSerializer(serializers.Serializer):
    motivo_rechazo = serializers.CharField(
        max_length=255,
        error_messages={
            "required": "Debes indicar el motivo del rechazo.",
            "blank": "Debes indicar el motivo del rechazo.",
        },
    )


# ---------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------
class FotoUrlMixin:
    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_foto_url(self, obj):
        # La base guarda la ruta (kumora/alimentos/...); aquí se arma la URL completa.
        return obj.foto.url if obj.foto else None


class AlimentoSerializer(FotoUrlMixin, serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(source="categoria.nombre", read_only=True)
    foto_url = serializers.SerializerMethodField()

    class Meta:
        model = DonacionAlimento
        fields = (
            "id", "categoria", "categoria_nombre", "nombre", "descripcion", "unidad_medida",
            "cantidad_total", "cantidad_disponible", "max_por_solicitud",
            "fecha_vencimiento", "foto_url", "activo",
        )
        read_only_fields = fields


class DonacionSerializer(serializers.ModelSerializer):
    alimentos = AlimentoSerializer(many=True, read_only=True)

    class Meta:
        model = Donacion
        fields = (
            "id", "titulo", "descripcion", "direccion_recogida", "modalidad_entrega", "estado",
            "motivo_rechazo", "fecha_revision", "fecha_registro", "fecha_actualizacion", "alimentos",
        )
        read_only_fields = fields


class DonanteResumenSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nombre = serializers.CharField(source="nombre_para_mostrar")
    correo = serializers.EmailField()
    telefono = serializers.CharField()
    tipo_donante = serializers.CharField(allow_null=True)
    estado = serializers.CharField()


class AdminDonacionSerializer(DonacionSerializer):
    donante = DonanteResumenSerializer(read_only=True)
    revisada_por = serializers.EmailField(source="revisada_por.correo", read_only=True, allow_null=True)

    class Meta(DonacionSerializer.Meta):
        fields = (*DonacionSerializer.Meta.fields, "donante", "revisada_por")
        read_only_fields = fields


class AlimentoDisponibleSerializer(FotoUrlMixin, serializers.ModelSerializer):
    """Lo que ve el beneficiario: sin teléfono ni dirección del donante (RN-29)."""

    categoria_nombre = serializers.CharField(source="categoria.nombre", read_only=True)
    foto_url = serializers.SerializerMethodField()
    donacion_titulo = serializers.CharField(source="donacion.titulo", read_only=True)
    modalidad_entrega = serializers.CharField(source="donacion.modalidad_entrega", read_only=True)
    donante_nombre = serializers.CharField(source="donacion.donante.nombre_para_mostrar", read_only=True)

    class Meta:
        model = DonacionAlimento
        fields = (
            "id", "nombre", "descripcion", "categoria", "categoria_nombre", "unidad_medida",
            "cantidad_disponible", "max_por_solicitud", "fecha_vencimiento", "foto_url",
            "donacion", "donacion_titulo", "modalidad_entrega", "donante_nombre",
        )
        read_only_fields = fields
