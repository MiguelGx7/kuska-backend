from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Sancion(models.Model):
    """Escala: Advertencia -> Bloqueo temporal (7 días) -> Bloqueo permanente (RN-05)."""

    class Tipo(models.TextChoices):
        ADVERTENCIA = "Advertencia"
        BLOQUEO_TEMPORAL = "Bloqueo temporal"
        BLOQUEO_PERMANENTE = "Bloqueo permanente"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sanciones"
    )
    tipo = models.CharField(max_length=18, choices=Tipo.choices)
    motivo = models.CharField(max_length=255)
    donacion = models.ForeignKey(
        "donaciones.Donacion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sanciones",
    )
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sanciones",
    )
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column="creada_por",
        related_name="sanciones_creadas",
    )
    # default (y no auto_now_add) para poder calcular fecha_fin con el mismo valor.
    fecha_inicio = models.DateTimeField(default=timezone.now)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    vigente = models.BooleanField(default=True)

    class Meta:
        db_table = "sancion"
        verbose_name = "sanción"
        verbose_name_plural = "sanciones"
        constraints = [
            models.CheckConstraint(
                name="chk_sancion_fecha",
                condition=~Q(tipo="Bloqueo temporal") | Q(fecha_fin__isnull=False),
                violation_error_message="El bloqueo temporal necesita fecha de fin.",
            ),
        ]
        indexes = [
            models.Index(fields=["usuario", "vigente"], name="idx_sancion_usuario"),
        ]

    def __str__(self):
        return f"{self.tipo} a {self.usuario} ({self.fecha_inicio:%Y-%m-%d})"
