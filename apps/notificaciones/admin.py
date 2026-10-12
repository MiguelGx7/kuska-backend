from django.contrib import admin

from .models import Notificacion


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ("titulo", "usuario", "tipo", "leida", "fecha")
    list_filter = ("tipo", "leida")
    search_fields = ("titulo", "usuario__correo")
    raw_id_fields = ("usuario", "donacion", "solicitud")
    readonly_fields = ("fecha",)
