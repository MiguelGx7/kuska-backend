import django_filters

from .models import Notificacion


class NotificacionFilter(django_filters.FilterSet):
    class Meta:
        model = Notificacion
        fields = ["leida", "tipo"]
