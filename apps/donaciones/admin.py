from django.contrib import admin

from .models import Donacion, DonacionAlimento, Novedad


class DonacionAlimentoInline(admin.TabularInline):
    model = DonacionAlimento
    extra = 0


@admin.register(Donacion)
class DonacionAdmin(admin.ModelAdmin):
    list_display = ("id", "titulo", "donante", "modalidad_entrega", "estado", "fecha_registro")
    list_filter = ("estado", "modalidad_entrega")
    search_fields = ("titulo", "donante__correo")
    raw_id_fields = ("donante", "revisada_por")
    readonly_fields = ("fecha_registro", "fecha_actualizacion")
    inlines = [DonacionAlimentoInline]


@admin.register(DonacionAlimento)
class DonacionAlimentoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "donacion",
        "categoria",
        "unidad_medida",
        "cantidad_total",
        "cantidad_disponible",
        "fecha_vencimiento",
        "activo",
    )
    list_filter = ("categoria", "unidad_medida", "activo")
    search_fields = ("nombre",)
    raw_id_fields = ("donacion",)


@admin.register(Novedad)
class NovedadAdmin(admin.ModelAdmin):
    list_display = ("id", "donacion", "donacion_alimento", "donante", "estado", "fecha_creacion")
    list_filter = ("estado",)
    raw_id_fields = ("donante", "donacion", "donacion_alimento", "revisada_por")
    readonly_fields = ("fecha_creacion",)
