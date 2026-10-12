from django.contrib import admin

from .models import Sancion


@admin.register(Sancion)
class SancionAdmin(admin.ModelAdmin):
    list_display = ("usuario", "tipo", "vigente", "fecha_inicio", "fecha_fin", "creada_por")
    list_filter = ("tipo", "vigente")
    search_fields = ("usuario__correo", "motivo")
    raw_id_fields = ("usuario", "donacion", "solicitud", "creada_por")
