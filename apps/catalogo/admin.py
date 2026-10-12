from django.contrib import admin

from .models import Categoria, Configuracion


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "descripcion", "activa")
    list_filter = ("activa",)
    search_fields = ("nombre",)


@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = (
        "plazo_recogida_dias",
        "margen_vencimiento_dias",
        "max_alimentos_por_solicitud",
        "porcentaje_max_por_alimento",
        "max_solicitudes_activas",
        "fecha_actualizacion",
    )
    readonly_fields = ("id", "actualizada_por", "fecha_actualizacion")

    # Es una sola fila (id = 1): no se crea otra ni se borra.
    def has_add_permission(self, request):
        return not Configuracion.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
