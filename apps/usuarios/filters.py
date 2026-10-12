import django_filters

from .models import Rol, Usuario


class UsuarioFilter(django_filters.FilterSet):
    rol = django_filters.ChoiceFilter(
        field_name="rol__nombre",
        choices=[(r, r) for r in (Rol.DONANTE, Rol.BENEFICIARIO, Rol.ADMINISTRADOR)],
    )
    estado = django_filters.ChoiceFilter(choices=Usuario.Estado.choices)

    class Meta:
        model = Usuario
        fields = ["rol", "estado"]
