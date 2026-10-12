from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class Categoria(models.Model):
    id = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=60, unique=True)
    descripcion = models.CharField(max_length=255, null=True, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        db_table = "categoria"
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Configuracion(models.Model):
    """Una sola fila (id = 1). Límites que aplican a los beneficiarios."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    plazo_recogida_dias = models.IntegerField(default=3)
    margen_vencimiento_dias = models.IntegerField(default=1)
    max_alimentos_por_solicitud = models.IntegerField(default=10)
    porcentaje_max_por_alimento = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("30.00")
    )
    max_solicitudes_activas = models.IntegerField(default=2)
    actualizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="actualizada_por",
        related_name="+",
    )
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "configuracion"
        verbose_name = "configuración"
        verbose_name_plural = "configuración"
        constraints = [
            models.CheckConstraint(name="chk_config_unica", condition=Q(id=1)),
            models.CheckConstraint(
                name="chk_config_valores",
                condition=Q(
                    plazo_recogida_dias__gt=0,
                    margen_vencimiento_dias__gte=0,
                    max_alimentos_por_solicitud__gt=0,
                    max_solicitudes_activas__gt=0,
                    porcentaje_max_por_alimento__gt=0,
                    porcentaje_max_por_alimento__lte=100,
                ),
                violation_error_message=(
                    "Valores inválidos: los plazos y límites deben ser mayores que 0, el margen "
                    "no puede ser negativo y el porcentaje debe estar entre 0 y 100."
                ),
            ),
        ]

    def __str__(self):
        return "Configuración del sistema"
