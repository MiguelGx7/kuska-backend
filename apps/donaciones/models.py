from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Floor


class Donacion(models.Model):
    class ModalidadEntrega(models.TextChoices):
        RECOGER = "Recoger"
        DOMICILIO = "Domicilio"
        AMBAS = "Ambas"

    class Estado(models.TextChoices):
        PENDIENTE = "Pendiente"
        VALIDADA = "Validada"
        RECHAZADA = "Rechazada"
        AGOTADA = "Agotada"
        FINALIZADA = "Finalizada"

    donante = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="donaciones"
    )
    titulo = models.CharField(max_length=120)
    descripcion = models.TextField(null=True, blank=True)
    direccion_recogida = models.CharField(max_length=200, null=True, blank=True)
    modalidad_entrega = models.CharField(max_length=9, choices=ModalidadEntrega.choices)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    motivo_rechazo = models.CharField(max_length=255, null=True, blank=True)
    revisada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="revisada_por",
        related_name="donaciones_revisadas",
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "donacion"
        verbose_name = "donación"
        verbose_name_plural = "donaciones"
        constraints = [
            models.CheckConstraint(
                name="chk_donacion_direccion",
                condition=Q(modalidad_entrega="Domicilio") | Q(direccion_recogida__isnull=False),
                violation_error_message=(
                    "La dirección de recogida es obligatoria si la modalidad es Recoger o Ambas."
                ),
            ),
            models.CheckConstraint(
                name="chk_donacion_rechazo",
                condition=~Q(estado="Rechazada") | Q(motivo_rechazo__isnull=False),
                violation_error_message="El motivo de rechazo es obligatorio.",
            ),
        ]
        indexes = [
            models.Index(fields=["estado"], name="idx_donacion_estado"),
        ]

    def __str__(self):
        return f"#{self.pk} {self.titulo} ({self.estado})"


class DonacionAlimento(models.Model):
    class UnidadMedida(models.TextChoices):
        KG = "kg"
        G = "g"
        L = "L"
        ML = "ml"
        UNIDAD = "unidad"
        PAQUETE = "paquete"

    # Unidades que solo admiten cantidades enteras (RN-10a).
    UNIDADES_ENTERAS = (UnidadMedida.UNIDAD, UnidadMedida.PAQUETE)

    donacion = models.ForeignKey(Donacion, on_delete=models.CASCADE, related_name="alimentos")
    categoria = models.ForeignKey(
        "catalogo.Categoria", on_delete=models.PROTECT, related_name="alimentos"
    )
    nombre = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=255, null=True, blank=True)
    unidad_medida = models.CharField(max_length=7, choices=UnidadMedida.choices)
    cantidad_total = models.DecimalField(max_digits=10, decimal_places=2)
    cantidad_disponible = models.DecimalField(max_digits=10, decimal_places=2)
    max_por_solicitud = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fecha_vencimiento = models.DateField()
    # En MySQL se guarda la ruta de Cloudinary (kumora/alimentos/...).
    foto = models.ImageField(
        db_column="url_foto", max_length=500, upload_to="kumora/alimentos/"
    )
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "donacion_alimento"
        verbose_name = "alimento de donación"
        verbose_name_plural = "alimentos de donación"
        constraints = [
            models.CheckConstraint(
                name="chk_alimento_total",
                condition=Q(cantidad_total__gt=0),
                violation_error_message="La cantidad total debe ser mayor que 0.",
            ),
            models.CheckConstraint(
                name="chk_alimento_disponible",
                condition=Q(cantidad_disponible__gte=0, cantidad_disponible__lte=F("cantidad_total")),
                violation_error_message=(
                    "La cantidad disponible debe estar entre 0 y la cantidad total."
                ),
            ),
            models.CheckConstraint(
                name="chk_alimento_entero",
                condition=(
                    ~Q(unidad_medida__in=["unidad", "paquete"])
                    | Q(
                        cantidad_total=Floor("cantidad_total"),
                        cantidad_disponible=Floor("cantidad_disponible"),
                    )
                ),
                violation_error_message=(
                    "Para 'unidad' y 'paquete' las cantidades deben ser números enteros."
                ),
            ),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.cantidad_total} {self.unidad_medida})"


class Novedad(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "Pendiente"
        ACEPTADA = "Aceptada"
        RECHAZADA = "Rechazada"

    donante = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="novedades"
    )
    donacion = models.ForeignKey(Donacion, on_delete=models.PROTECT, related_name="novedades")
    # NULL = la novedad afecta toda la donación.
    donacion_alimento = models.ForeignKey(
        DonacionAlimento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="novedades",
    )
    descripcion = models.TextField()
    estado = models.CharField(max_length=9, choices=Estado.choices, default=Estado.PENDIENTE)
    respuesta_admin = models.CharField(max_length=255, null=True, blank=True)
    revisada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="revisada_por",
        related_name="novedades_revisadas",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_revision = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "novedad"
        verbose_name = "novedad"
        verbose_name_plural = "novedades"
        indexes = [
            models.Index(fields=["estado"], name="idx_novedad_estado"),
        ]

    def __str__(self):
        return f"Novedad #{self.pk} de la donación #{self.donacion_id} ({self.estado})"
