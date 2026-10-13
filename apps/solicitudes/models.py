from django.conf import settings
from django.db import models
from django.db.models import Q


class CarritoItem(models.Model):
    """No reserva cantidad; se reserva al enviar el carrito (RN-20, RN-22)."""

    beneficiario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="carrito"
    )
    donacion_alimento = models.ForeignKey(
        "donaciones.DonacionAlimento", on_delete=models.CASCADE, related_name="en_carritos"
    )
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_agregado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "carrito_item"
        verbose_name = "ítem del carrito"
        verbose_name_plural = "ítems del carrito"
        constraints = [
            models.UniqueConstraint(
                fields=["beneficiario", "donacion_alimento"],
                name="uq_carrito",
                violation_error_message="Este alimento ya está en el carrito.",
            ),
            models.CheckConstraint(
                name="chk_carrito_cantidad",
                condition=Q(cantidad__gt=0),
                violation_error_message="La cantidad debe ser mayor que 0.",
            ),
        ]

    def __str__(self):
        return f"{self.donacion_alimento} x {self.cantidad}"


class Solicitud(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "Pendiente"
        APROBADA = "Aprobada"
        RECHAZADA = "Rechazada"
        ENTREGADA = "Entregada"
        NO_RECOGIDA = "No recogida"
        CANCELADA = "Cancelada"

    class ModalidadEntrega(models.TextChoices):
        RECOGER = "Recoger"
        DOMICILIO = "Domicilio"

    # Solicitudes "activas" o "en curso" (RN-16, RN-20, RN-22).
    ESTADOS_EN_CURSO = (Estado.PENDIENTE, Estado.APROBADA)
    # Desde estos estados el beneficiario ve teléfono y dirección del donante (RN-29).
    ESTADOS_CON_CONTACTO = (Estado.APROBADA, Estado.ENTREGADA, Estado.NO_RECOGIDA)

    # Formato KUM-AAAAMMDD-NNNN; agrupa las solicitudes de un mismo carrito.
    codigo_pedido = models.CharField(max_length=20)
    beneficiario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="solicitudes"
    )
    donacion = models.ForeignKey(
        "donaciones.Donacion", on_delete=models.PROTECT, related_name="solicitudes"
    )
    estado = models.CharField(max_length=11, choices=Estado.choices, default=Estado.PENDIENTE)
    modalidad_entrega = models.CharField(max_length=9, choices=ModalidadEntrega.choices)
    direccion_entrega = models.CharField(max_length=200, null=True, blank=True)
    motivo_rechazo = models.CharField(max_length=255, null=True, blank=True)
    motivo_cancelacion = models.CharField(max_length=255, null=True, blank=True)
    gestionada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="gestionada_por",
        related_name="solicitudes_gestionadas",
    )
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_gestion = models.DateTimeField(null=True, blank=True)
    fecha_limite_recogida = models.DateTimeField(null=True, blank=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solicitud"
        verbose_name = "solicitud"
        verbose_name_plural = "solicitudes"
        constraints = [
            models.UniqueConstraint(
                fields=["codigo_pedido", "donacion"], name="uq_solicitud_pedido"
            ),
            models.CheckConstraint(
                name="chk_solicitud_direccion",
                condition=Q(modalidad_entrega="Recoger") | Q(direccion_entrega__isnull=False),
                violation_error_message="La dirección de entrega es obligatoria para domicilio.",
            ),
            models.CheckConstraint(
                name="chk_solicitud_rechazo",
                condition=~Q(estado="Rechazada") | Q(motivo_rechazo__isnull=False),
                violation_error_message="El motivo de rechazo es obligatorio.",
            ),
        ]
        indexes = [
            models.Index(fields=["codigo_pedido"], name="idx_solicitud_pedido"),
            # En el script es idx_solicitud_beneficiario_estado (33 caracteres);
            # Django limita los nombres de índice a 30.
            models.Index(fields=["beneficiario", "estado"], name="idx_solicitud_benef_estado"),
        ]

    def __str__(self):
        return f"{self.codigo_pedido} - donación #{self.donacion_id} ({self.estado})"


class DetalleSolicitud(models.Model):
    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="detalles")
    donacion_alimento = models.ForeignKey(
        "donaciones.DonacionAlimento", on_delete=models.PROTECT, related_name="detalles_solicitud"
    )
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    # True: el alimento se quitó por una novedad aceptada (RN-32).
    cancelado = models.BooleanField(default=False)

    class Meta:
        db_table = "detalle_solicitud"
        verbose_name = "detalle de solicitud"
        verbose_name_plural = "detalles de solicitud"
        constraints = [
            models.UniqueConstraint(
                fields=["solicitud", "donacion_alimento"], name="uq_detalle"
            ),
            models.CheckConstraint(
                name="chk_detalle_cantidad",
                condition=Q(cantidad__gt=0),
                violation_error_message="La cantidad debe ser mayor que 0.",
            ),
        ]

    def __str__(self):
        return f"{self.donacion_alimento} x {self.cantidad}"


class Entrega(models.Model):
    """
    Siempre total: no hay entregas parciales (RN-26). El beneficiario confirma si
    la recibió (RN-26b); la solicitud sigue Entregada pase lo que pase.
    """

    # Plazo del beneficiario para responder, desde fecha_entrega (como DIAS_BLOQUEO_TEMPORAL:
    # constante del sistema, no un valor de configuracion).
    HORAS_PARA_CONFIRMAR = 48

    class EstadoConfirmacion(models.TextChoices):
        PENDIENTE = "Pendiente", "Pendiente"
        CONFIRMADA = "Confirmada", "Confirmada"
        ASUMIDA = "Asumida", "Asumida"
        RECLAMADA = "Reclamada", "Reclamada"
        REVISADA = "Revisada", "Revisada"

    solicitud = models.OneToOneField(
        Solicitud, on_delete=models.PROTECT, related_name="entrega"
    )
    registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column="registrada_por",
        related_name="entregas_registradas",
    )
    fecha_entrega = models.DateTimeField(auto_now_add=True)
    observacion = models.CharField(max_length=255, null=True, blank=True)
    # Confirmación del beneficiario (RN-26b).
    estado_confirmacion = models.CharField(
        max_length=10, choices=EstadoConfirmacion.choices, default=EstadoConfirmacion.PENDIENTE
    )
    fecha_confirmacion = models.DateTimeField(null=True, blank=True)
    comentario_beneficiario = models.CharField(max_length=255, null=True, blank=True)
    respuesta_admin = models.CharField(max_length=255, null=True, blank=True)
    revisada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="revisada_por",
        related_name="reclamos_revisados",
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "entrega"
        verbose_name = "entrega"
        verbose_name_plural = "entregas"
        constraints = [
            # Toda respuesta (o la suposición) queda con su fecha.
            models.CheckConstraint(
                name="chk_entrega_fecha_confirmacion",
                condition=Q(estado_confirmacion="Pendiente") | Q(fecha_confirmacion__isnull=False),
                violation_error_message="La confirmación necesita su fecha.",
            ),
            # Un reclamo (y su revisión) siempre tiene el comentario del beneficiario.
            models.CheckConstraint(
                name="chk_entrega_reclamo",
                condition=~Q(estado_confirmacion__in=["Reclamada", "Revisada"])
                | Q(comentario_beneficiario__isnull=False),
                violation_error_message="El reclamo necesita el comentario del beneficiario.",
            ),
            # Revisada: respuesta, quién y cuándo.
            models.CheckConstraint(
                name="chk_entrega_revision",
                condition=~Q(estado_confirmacion="Revisada")
                | Q(respuesta_admin__isnull=False, revisada_por__isnull=False, fecha_revision__isnull=False),
                violation_error_message="La revisión necesita respuesta, administrador y fecha.",
            ),
        ]
        indexes = [
            # Pendientes vencidas (RN-50) y reclamos por revisar.
            models.Index(fields=["estado_confirmacion", "fecha_entrega"], name="idx_entrega_confirmacion"),
        ]

    @property
    def confirmar_hasta(self):
        """Hasta cuándo puede responder el beneficiario."""
        from datetime import timedelta

        return self.fecha_entrega + timedelta(hours=self.HORAS_PARA_CONFIRMAR)

    def __str__(self):
        return f"Entrega de {self.solicitud}"
