from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import CodigoVerificacion, Rol, Usuario

CAMPOS_DATOS = (
    "rol",
    "nombres",
    "apellidos",
    "tipo_documento",
    "numero_documento",
    "telefono",
    "direccion",
)
CAMPOS_DONANTE = ("tipo_donante", "nombre_negocio", "nit")


class UsuarioCreationForm(BaseUserCreationForm):
    class Meta:
        model = Usuario
        fields = ("correo", *CAMPOS_DATOS, *CAMPOS_DONANTE)


class UsuarioChangeForm(UserChangeForm):
    class Meta:
        model = Usuario
        fields = "__all__"
        field_classes = {"correo": forms.EmailField}


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form = UsuarioChangeForm
    add_form = UsuarioCreationForm

    fieldsets = (
        (None, {"fields": ("correo", "password")}),
        ("Datos personales", {"fields": CAMPOS_DATOS}),
        ("Donante", {"fields": CAMPOS_DONANTE}),
        ("Estado", {"fields": ("estado", "bloqueado_hasta")}),
        (
            "Permisos del admin de Django",
            {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Fechas", {"fields": ("last_login", "fecha_registro", "fecha_actualizacion")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("correo", "password1", "password2"),
            },
        ),
        ("Datos personales", {"fields": CAMPOS_DATOS}),
        ("Donante", {"fields": CAMPOS_DONANTE}),
    )
    readonly_fields = ("last_login", "fecha_registro", "fecha_actualizacion")
    list_display = ("correo", "nombres", "apellidos", "rol", "estado", "is_staff")
    list_filter = ("rol", "estado", "tipo_donante", "is_staff")
    search_fields = ("correo", "nombres", "apellidos", "numero_documento", "nit")
    ordering = ("correo",)


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre")


@admin.register(CodigoVerificacion)
class CodigoVerificacionAdmin(admin.ModelAdmin):
    list_display = ("correo", "proposito", "usuario", "fecha_creacion", "fecha_expiracion", "intentos", "usado")
    list_filter = ("proposito", "usado")
    search_fields = ("correo", "usuario__correo")
    # El código nunca se muestra: solo existe su hash.
    exclude = ("codigo_hash",)
    readonly_fields = ("fecha_creacion",)
