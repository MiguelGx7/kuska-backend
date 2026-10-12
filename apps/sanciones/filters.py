import django_filters

from .models import Sancion


class AdminSancionFilter(django_filters.FilterSet):
    usuario = django_filters.NumberFilter(field_name="usuario_id", help_text="id del usuario sancionado")

    class Meta:
        model = Sancion
        fields = ["usuario", "tipo", "vigente"]
