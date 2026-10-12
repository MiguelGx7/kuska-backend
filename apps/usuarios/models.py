from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q


class Rol(models.Model):
    """Roles fijos. Siempre se buscan por nombre, nunca por id."""

    DONANTE = "Donante"
    BENEFICIARIO = "Beneficiario"
    ADMINISTRADOR = "Administrador"

    id = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=30, unique=True)

    class Meta:
        db_table = "rol"
        verbose_name = "rol"
        verbose_name_plural = "roles"

    def __str__(self):
        return self.nombre


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, correo, password, **extra_fields):
        if not correo:
            raise ValueError("El correo es obligatorio.")
        usuario = self.model(correo=self.normalize_email(correo), **extra_fields)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, correo, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(correo, password, **extra_fields)

    def create_superuser(self, correo, password=None, **extra_fields):
        """Crea un Administrador (RN-01: solo por consola)."""
        rol_model = self.model._meta.get_field("rol").related_model
        try:
            extra_fields["rol"] = rol_model.objects.get(nombre=rol_model.ADMINISTRADOR)
        except rol_model.DoesNotExist:
            raise ValueError(
                "No existe el rol Administrador. Ejecuta primero 'python manage.py migrate'."
            )
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True or extra_fields["is_superuser"] is not True:
            raise ValueError("El administrador debe tener is_staff=True e is_superuser=True.")
        return self._create_user(correo, password, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    class TipoDocumento(models.TextChoices):
        CC = "CC", "Cédula de ciudadanía"
        CE = "CE", "Cédula de extranjería"
        PAS = "PAS", "Pasaporte"
        PPT = "PPT", "Permiso por Protección Temporal"

    class Estado(models.TextChoices):
        ACTIVO = "Activo"
        BLOQUEADO = "Bloqueado"
        INACTIVO = "Inactivo"

    class TipoDonante(models.TextChoices):
        PERSONA = "Persona"
        NEGOCIO = "Negocio"

    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, related_name="usuarios")
    nombres = models.CharField(max_length=80)
    apellidos = models.CharField(max_length=80)
    tipo_documento = models.CharField(max_length=3, choices=TipoDocumento.choices)
    numero_documento = models.CharField(max_length=20, unique=True)
    correo = models.EmailField(max_length=120, unique=True)
    password = models.CharField("contraseña", max_length=255, db_column="contrasena")
    telefono = models.CharField(max_length=20)
    direccion = models.CharField(max_length=200, null=True, blank=True)
    estado = models.CharField(max_length=9, choices=Estado.choices, default=Estado.ACTIVO)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    tipo_donante = models.CharField(
        max_length=7, choices=TipoDonante.choices, null=True, blank=True
    )
    nombre_negocio = models.CharField(max_length=120, null=True, blank=True)
    nit = models.CharField(max_length=20, unique=True, null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    # Campo técnico que exige el admin de Django (last_login viene de
    # AbstractBaseUser e is_superuser de PermissionsMixin).
    is_staff = models.BooleanField(default=False)

    objects = UsuarioManager()

    USERNAME_FIELD = "correo"
    EMAIL_FIELD = "correo"
    # Lo que pide createsuperuser además del correo y la contraseña.
    REQUIRED_FIELDS = ["nombres", "apellidos", "tipo_documento", "numero_documento", "telefono"]

    class Meta:
        db_table = "usuario"
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        constraints = [
            models.CheckConstraint(
                name="chk_usuario_negocio",
                condition=(
                    Q(tipo_donante="Negocio", nombre_negocio__isnull=False, nit__isnull=False)
                    | (
                        (Q(tipo_donante__isnull=True) | Q(tipo_donante="Persona"))
                        & Q(nombre_negocio__isnull=True, nit__isnull=True)
                    )
                ),
                violation_error_message=(
                    "Si el donante es Negocio, el nombre del negocio y el NIT son obligatorios; "
                    "si no, deben quedar vacíos."
                ),
            ),
        ]

    def __str__(self):
        return f"{self.nombres} {self.apellidos} ({self.correo})"

    @property
    def is_active(self):
        """No es columna: se calcula desde estado. Los bloqueados los maneja el login (RN-03)."""
        return self.estado != self.Estado.INACTIVO

    def get_full_name(self):
        return f"{self.nombres} {self.apellidos}"

    def get_short_name(self):
        return self.nombres


class RecuperacionContrasena(models.Model):
    usuario = models.ForeignKey(
        Usuario, on_delete=models.CASCADE, related_name="recuperaciones"
    )
    codigo_hash = models.CharField(max_length=255)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_expiracion = models.DateTimeField()
    intentos = models.IntegerField(default=0)
    usado = models.BooleanField(default=False)

    class Meta:
        db_table = "recuperacion_contrasena"
        verbose_name = "recuperación de contraseña"
        verbose_name_plural = "recuperaciones de contraseña"
        constraints = [
            models.CheckConstraint(
                name="chk_recuperacion_intentos",
                condition=Q(intentos__gte=0, intentos__lte=5),
            ),
        ]
        indexes = [
            models.Index(fields=["usuario", "usado"], name="idx_recuperacion_usuario"),
        ]

    def __str__(self):
        return f"Recuperación de {self.usuario.correo} ({self.fecha_creacion:%Y-%m-%d %H:%M})"
