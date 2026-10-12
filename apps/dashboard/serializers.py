"""Solo describen la respuesta (para Swagger); los datos los arma services.py."""

from rest_framework import serializers

from apps.donaciones.models import DonacionAlimento


class MesAdminSerializer(serializers.Serializer):
    mes = serializers.CharField(help_text="AAAA-MM, en hora de Bogotá.")
    donaciones = serializers.IntegerField(help_text="Donaciones recibidas (Validada en adelante) registradas ese mes.")
    solicitudes = serializers.IntegerField(help_text="Solicitudes enviadas ese mes (cualquier estado).")
    entregas = serializers.IntegerField()
    alimentos_donados = serializers.IntegerField(help_text="Alimentos de las donaciones recibidas ese mes.")
    alimentos_entregados = serializers.IntegerField(help_text="Alimentos entregados ese mes (se cuentan, no se suman cantidades).")


class MesDonanteSerializer(serializers.Serializer):
    mes = serializers.CharField(help_text="AAAA-MM, en hora de Bogotá.")
    donaciones = serializers.IntegerField(help_text="Sus donaciones recibidas (Validada en adelante) registradas ese mes.")
    entregas = serializers.IntegerField()
    alimentos_donados = serializers.IntegerField()
    alimentos_entregados = serializers.IntegerField()


class CategoriaSerieSerializer(serializers.Serializer):
    categoria_id = serializers.IntegerField()
    categoria = serializers.CharField()
    alimentos_donados = serializers.IntegerField()
    alimentos_entregados = serializers.IntegerField()


class DashboardAdminSerializer(serializers.Serializer):
    donaciones_recibidas = serializers.IntegerField(help_text="Donaciones Validada, Agotada o Finalizada.")
    solicitudes_entregadas = serializers.IntegerField()
    solicitudes_pendientes = serializers.IntegerField()
    personas_beneficiadas = serializers.IntegerField(help_text="Beneficiarios distintos con al menos una entrega.")
    por_mes = MesAdminSerializer(many=True, help_text="Últimos 12 meses, con los meses sin datos en 0.")
    por_categoria = CategoriaSerieSerializer(many=True, help_text="Histórico, solo categorías con alimentos donados.")


class DonacionesPorEstadoSerializer(serializers.Serializer):
    Pendiente = serializers.IntegerField()
    Validada = serializers.IntegerField()
    Rechazada = serializers.IntegerField()
    Agotada = serializers.IntegerField()
    Finalizada = serializers.IntegerField()


class CantidadPorUnidadSerializer(serializers.Serializer):
    unidad_medida = serializers.ChoiceField(choices=DonacionAlimento.UnidadMedida.choices)
    alimentos = serializers.IntegerField(help_text="Alimentos entregados con esta unidad.")
    cantidad = serializers.DecimalField(max_digits=14, decimal_places=2, help_text="Cantidad total entregada en esta unidad.")


class DashboardDonanteSerializer(serializers.Serializer):
    donaciones_por_estado = DonacionesPorEstadoSerializer()
    entregas = serializers.IntegerField(help_text="Solicitudes de sus donaciones que fueron entregadas.")
    alimentos_entregados = serializers.IntegerField()
    cantidades_entregadas = CantidadPorUnidadSerializer(
        many=True, help_text="Agrupadas por unidad de medida: no se suman unidades distintas."
    )
    personas_ayudadas = serializers.IntegerField(help_text="Beneficiarios distintos que recibieron sus alimentos.")
    por_mes = MesDonanteSerializer(many=True, help_text="Últimos 12 meses, con los meses sin datos en 0.")
    por_categoria = CategoriaSerieSerializer(many=True, help_text="Histórico, solo categorías con alimentos donados.")


class ProximaRecogidaSerializer(serializers.Serializer):
    solicitud = serializers.IntegerField()
    codigo_pedido = serializers.CharField()
    fecha_limite_recogida = serializers.DateTimeField()
    modalidad_entrega = serializers.CharField()
    donacion = serializers.IntegerField()
    donacion_titulo = serializers.CharField()


class DashboardBeneficiarioSerializer(serializers.Serializer):
    solicitudes_pendientes = serializers.IntegerField()
    solicitudes_aprobadas = serializers.IntegerField()
    max_solicitudes_activas = serializers.IntegerField(help_text="Límite de la configuración.")
    cupos_disponibles = serializers.IntegerField(
        help_text="max_solicitudes_activas − (Pendiente + Aprobada), mínimo 0: cuántas solicitudes más puede enviar."
    )
    proxima_recogida = ProximaRecogidaSerializer(
        allow_null=True,
        help_text="La Aprobada con la fecha límite más cercana que aún no vence; null si no tiene.",
    )
    notificaciones_no_leidas = serializers.IntegerField()
