from django.contrib import admin

from .models import CarritoItem, DetalleSolicitud, Entrega, Solicitud


@admin.register(CarritoItem)
class CarritoItemAdmin(admin.ModelAdmin):
    list_display = ("beneficiario", "donacion_alimento", "cantidad", "fecha_agregado")
    raw_id_fields = ("beneficiario", "donacion_alimento")


class DetalleSolicitudInline(admin.TabularInline):
    model = DetalleSolicitud
    extra = 0
    raw_id_fields = ("donacion_alimento",)


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_pedido",
        "beneficiario",
        "donacion",
        "estado",
        "modalidad_entrega",
        "fecha_solicitud",
        "fecha_limite_recogida",
    )
    list_filter = ("estado", "modalidad_entrega")
    search_fields = ("codigo_pedido", "beneficiario__correo")
    raw_id_fields = ("beneficiario", "donacion", "gestionada_por")
    readonly_fields = ("fecha_solicitud", "fecha_actualizacion")
    inlines = [DetalleSolicitudInline]


@admin.register(Entrega)
class EntregaAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "registrada_por", "fecha_entrega")
    raw_id_fields = ("solicitud", "registrada_por")
    readonly_fields = ("fecha_entrega",)
