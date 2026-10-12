import django_filters

from .models import Solicitud


class AdminSolicitudFilter(django_filters.FilterSet):
    codigo_pedido = django_filters.CharFilter(lookup_expr="iexact", help_text="Ej.: KUM-20261012-0001")
    beneficiario = django_filters.NumberFilter(field_name="beneficiario_id", help_text="id del beneficiario")
    donacion = django_filters.NumberFilter(field_name="donacion_id", help_text="id de la donación")

    class Meta:
        model = Solicitud
        fields = ["estado", "codigo_pedido", "beneficiario", "donacion"]
