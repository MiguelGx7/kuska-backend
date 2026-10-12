import django_filters

from .models import Donacion, DonacionAlimento, Novedad


class AdminNovedadFilter(django_filters.FilterSet):
    donacion = django_filters.NumberFilter(field_name="donacion_id", help_text="id de la donación")
    donante = django_filters.NumberFilter(field_name="donante_id", help_text="id del donante")

    class Meta:
        model = Novedad
        fields = ["estado", "donacion", "donante"]


class AdminDonacionFilter(django_filters.FilterSet):
    donante = django_filters.NumberFilter(field_name="donante_id", help_text="id del donante")

    class Meta:
        model = Donacion
        fields = ["estado", "donante"]


class AlimentoDisponibleFilter(django_filters.FilterSet):
    categoria = django_filters.NumberFilter(field_name="categoria_id", help_text="id de la categoría")
    modalidad = django_filters.ChoiceFilter(
        choices=Donacion.ModalidadEntrega.choices,
        method="filtrar_modalidad",
        help_text=(
            "Cómo quiere recibir el beneficiario. Recoger o Domicilio incluyen también las "
            "donaciones 'Ambas'; Ambas muestra solo las que permiten las dos."
        ),
    )

    class Meta:
        model = DonacionAlimento
        fields = ["categoria", "modalidad"]

    def filtrar_modalidad(self, queryset, name, value):
        if value == Donacion.ModalidadEntrega.AMBAS:
            return queryset.filter(donacion__modalidad_entrega=value)
        return queryset.filter(
            donacion__modalidad_entrega__in=[value, Donacion.ModalidadEntrega.AMBAS]
        )
