"""
Estadísticas del dashboard (RN-60 administrador, RN-61 donante).

Sin modelos propios: todo sale de agregaciones en la base (Count, Sum,
annotate). Las series cuentan registros; nunca suman cantidades de
unidades distintas. Los meses se agrupan en hora de Bogotá.
"""

from datetime import date, datetime, time

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.catalogo.models import Categoria
from apps.donaciones.models import Donacion, DonacionAlimento
from apps.donaciones.services import procesar_donaciones_desactualizadas
from apps.solicitudes.models import DetalleSolicitud, Entrega, Solicitud
from apps.solicitudes.services import procesar_solicitudes_vencidas

MESES_SERIE = 12
# "Donaciones recibidas" (RN-60): de Validada en adelante. Pendiente aún no se
# revisa y Rechazada nunca se recibió.
ESTADOS_RECIBIDA = (Donacion.Estado.VALIDADA, Donacion.Estado.AGOTADA, Donacion.Estado.FINALIZADA)


# ---------------------------------------------------------------------
# Meses (hora de Bogotá)
# ---------------------------------------------------------------------
def _meses():
    """Primer día de cada uno de los últimos 12 meses (el actual incluido), del más viejo al más nuevo."""
    hoy = timezone.localdate()
    indice = hoy.year * 12 + hoy.month - 1
    return [date(i // 12, i % 12 + 1, 1) for i in range(indice - MESES_SERIE + 1, indice + 1)]


def _inicio(meses):
    """Desde qué momento cuentan las series: 00:00 (Bogotá) del primer mes."""
    return timezone.make_aware(datetime.combine(meses[0], time.min))


def _por_mes(queryset, campo_fecha, inicio):
    """
    {primer día del mes: total} en UNA consulta: GROUP BY mes, con el mes
    calculado en la zona de Bogotá (TruncMonth con tzinfo).
    """
    filas = (
        queryset.filter(**{f"{campo_fecha}__gte": inicio})
        .annotate(mes=TruncMonth(campo_fecha, tzinfo=timezone.get_current_timezone()))
        .values("mes")
        .annotate(total=Count("id", distinct=True))
        .order_by("mes")
    )
    return {timezone.localtime(f["mes"]).date(): f["total"] for f in filas}


def _serie_mensual(meses, **series):
    """Une las series y rellena con 0 los meses sin datos (la gráfica no queda con huecos)."""
    return [
        {"mes": f"{mes:%Y-%m}", **{nombre: valores.get(mes, 0) for nombre, valores in series.items()}}
        for mes in meses
    ]


# ---------------------------------------------------------------------
# Consultas compartidas
# ---------------------------------------------------------------------
def _detalles_entregados(**filtros):
    """Alimentos entregados: detalles no cancelados de solicitudes Entregada (RN-26: entrega total)."""
    return DetalleSolicitud.objects.filter(
        cancelado=False, solicitud__estado=Solicitud.Estado.ENTREGADA, **filtros
    )


def _por_categoria(filtro_donacion):
    """
    Por categoría, en UNA consulta: cuántos alimentos se donaron (en donaciones
    recibidas) y cuántos se entregaron. Solo las categorías con datos.
    """
    donados = Q(alimentos__donacion__estado__in=ESTADOS_RECIBIDA, **{
        f"alimentos__donacion__{k}": v for k, v in filtro_donacion.items()
    })
    entregados = Q(
        alimentos__detalles_solicitud__cancelado=False,
        alimentos__detalles_solicitud__solicitud__estado=Solicitud.Estado.ENTREGADA,
        **{f"alimentos__donacion__{k}": v for k, v in filtro_donacion.items()},
    )
    filas = (
        Categoria.objects.annotate(
            alimentos_donados=Count("alimentos", filter=donados, distinct=True),
            alimentos_entregados=Count("alimentos__detalles_solicitud", filter=entregados, distinct=True),
        )
        .filter(alimentos_donados__gt=0)
        .order_by("-alimentos_donados", "nombre")
        .values("id", "nombre", "alimentos_donados", "alimentos_entregados")
    )
    return [{"categoria_id": f.pop("id"), "categoria": f.pop("nombre"), **f} for f in filas]


# ---------------------------------------------------------------------
# Administrador (RN-60)
# ---------------------------------------------------------------------
def dashboard_admin():
    # RN-50: primero los vencimientos pendientes, para que los números estén al día.
    procesar_solicitudes_vencidas(Solicitud.objects.all())
    procesar_donaciones_desactualizadas(Donacion.objects.all())

    recibidas = Donacion.objects.filter(estado__in=ESTADOS_RECIBIDA)
    totales = Solicitud.objects.aggregate(
        solicitudes_entregadas=Count("id", filter=Q(estado=Solicitud.Estado.ENTREGADA)),
        solicitudes_pendientes=Count("id", filter=Q(estado=Solicitud.Estado.PENDIENTE)),
        personas_beneficiadas=Count(
            "beneficiario", filter=Q(estado=Solicitud.Estado.ENTREGADA), distinct=True
        ),
    )

    meses = _meses()
    inicio = _inicio(meses)
    return {
        "donaciones_recibidas": recibidas.count(),
        **totales,
        "por_mes": _serie_mensual(
            meses,
            donaciones=_por_mes(recibidas, "fecha_registro", inicio),
            solicitudes=_por_mes(Solicitud.objects.all(), "fecha_solicitud", inicio),
            entregas=_por_mes(Entrega.objects.all(), "fecha_entrega", inicio),
            alimentos_donados=_por_mes(
                DonacionAlimento.objects.filter(donacion__estado__in=ESTADOS_RECIBIDA),
                "donacion__fecha_registro",
                inicio,
            ),
            alimentos_entregados=_por_mes(_detalles_entregados(), "solicitud__entrega__fecha_entrega", inicio),
        ),
        "por_categoria": _por_categoria({}),
    }


# ---------------------------------------------------------------------
# Donante (RN-61): solo sus datos
# ---------------------------------------------------------------------
def dashboard_donante(donante):
    procesar_solicitudes_vencidas(Solicitud.objects.filter(donacion__donante=donante))
    procesar_donaciones_desactualizadas(Donacion.objects.filter(donante=donante))

    por_estado = dict(
        Donacion.objects.filter(donante=donante)
        .values("estado")
        .annotate(total=Count("id"))
        .order_by()
        .values_list("estado", "total")
    )
    entregas = Solicitud.objects.filter(
        donacion__donante=donante, estado=Solicitud.Estado.ENTREGADA
    ).aggregate(
        entregas=Count("id"),
        personas_ayudadas=Count("beneficiario", distinct=True),
    )
    detalles = _detalles_entregados(solicitud__donacion__donante=donante)
    # Cantidades agrupadas por unidad: nunca se suman kg con unidades o litros.
    por_unidad = {
        f["donacion_alimento__unidad_medida"]: f
        for f in detalles.values("donacion_alimento__unidad_medida")
        .annotate(alimentos=Count("id"), cantidad=Sum("cantidad"))
        .order_by()
    }

    meses = _meses()
    inicio = _inicio(meses)
    return {
        "donaciones_por_estado": {estado: por_estado.get(estado, 0) for estado in Donacion.Estado.values},
        "entregas": entregas["entregas"],
        "alimentos_entregados": sum(f["alimentos"] for f in por_unidad.values()),
        "cantidades_entregadas": [
            {
                "unidad_medida": unidad,
                "alimentos": por_unidad[unidad]["alimentos"],
                "cantidad": por_unidad[unidad]["cantidad"],
            }
            for unidad in DonacionAlimento.UnidadMedida.values
            if unidad in por_unidad
        ],
        "personas_ayudadas": entregas["personas_ayudadas"],
        "por_mes": _serie_mensual(
            meses,
            donaciones=_por_mes(
                Donacion.objects.filter(donante=donante, estado__in=ESTADOS_RECIBIDA), "fecha_registro", inicio
            ),
            entregas=_por_mes(Entrega.objects.filter(solicitud__donacion__donante=donante), "fecha_entrega", inicio),
            alimentos_donados=_por_mes(
                DonacionAlimento.objects.filter(donacion__donante=donante, donacion__estado__in=ESTADOS_RECIBIDA),
                "donacion__fecha_registro",
                inicio,
            ),
            alimentos_entregados=_por_mes(detalles, "solicitud__entrega__fecha_entrega", inicio),
        ),
        "por_categoria": _por_categoria({"donante": donante}),
    }
