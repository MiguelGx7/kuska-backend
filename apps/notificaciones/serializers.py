from rest_framework import serializers

from .models import Notificacion


class NotificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notificacion
        fields = ("id", "tipo", "titulo", "mensaje", "donacion", "solicitud", "leida", "fecha")
        read_only_fields = fields


class LeerTodasSerializer(serializers.Serializer):
    marcadas = serializers.IntegerField(help_text="Cuántas notificaciones pasaron a leídas.")
    no_leidas = serializers.IntegerField()
