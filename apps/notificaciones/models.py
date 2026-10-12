from django.conf import settings
from django.db import models


class Notificacion(models.Model):
    class Tipo(models.TextChoices):
        """Valores fijos de RN-40."""

        NUEVA_DONACION = "nueva_donacion", "Nueva donación"
        DONACION_VALIDADA = "donacion_validada", "Donación validada"
        DONACION_RECHAZADA = "donacion_rechazada", "Donación rechazada"
        NUEVA_SOLICITUD = "nueva_solicitud", "Nueva solicitud"
        SOLICITUD_APROBADA = "solicitud_aprobada", "Solicitud aprobada"
        SOLICITUD_RECHAZADA = "solicitud_rechazada", "Solicitud rechazada"
        SOLICITUD_CANCELADA = "solicitud_cancelada", "Solicitud cancelada"
        ALIMENTO_CANCELADO = "alimento_cancelado", "Alimento cancelado"
        SOLICITUD_ENTREGADA = "solicitud_entregada", "Solicitud entregada"
        SOLICITUD_NO_RECOGIDA = "solicitud_no_recogida", "Solicitud no recogida"
        NOVEDAD_CREADA = "novedad_creada", "Novedad creada"
        NOVEDAD_ACEPTADA = "novedad_aceptada", "Novedad aceptada"
        NOVEDAD_RECHAZADA = "novedad_rechazada", "Novedad rechazada"
        SANCION = "sancion", "Sanción"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notificaciones"
    )
    tipo = models.CharField(max_length=40, choices=Tipo.choices)
    titulo = models.CharField(max_length=120)
    mensaje = models.CharField(max_length=500)
    # SET_NULL: la notificación es historial y se conserva aunque se
    # elimine una donación Pendiente (RN-12).
    donacion = models.ForeignKey(
        "donaciones.Donacion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notificaciones",
    )
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notificaciones",
    )
    leida = models.BooleanField(default=False)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notificacion"
        verbose_name = "notificación"
        verbose_name_plural = "notificaciones"
        ordering = ["-fecha"]
        indexes = [
            models.Index(fields=["usuario", "leida"], name="idx_notif_usuario"),
        ]

    def __str__(self):
        return f"{self.titulo} -> {self.usuario}"
