from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from . import services
from .filters import NotificacionFilter
from .models import Notificacion
from .serializers import LeerTodasSerializer, NotificacionSerializer


class NotificacionPagination(PageNumberPagination):
    """La paginación normal más el total de no leídas, para el contador de la app."""

    def get_paginated_response(self, data):
        respuesta = super().get_paginated_response(data)
        respuesta.data["no_leidas"] = services.contar_no_leidas(self.request.user)
        return respuesta

    def get_paginated_response_schema(self, schema):
        esquema = super().get_paginated_response_schema(schema)
        esquema["properties"]["no_leidas"] = {
            "type": "integer",
            "description": "Total de no leídas del usuario (sin importar filtros ni página).",
            "example": 3,
        }
        esquema["required"] = [*esquema.get("required", []), "no_leidas"]
        return esquema


@extend_schema_view(
    list=extend_schema(
        summary="Mis notificaciones (RN-41)",
        description=(
            "Solo las del usuario autenticado, de la más nueva a la más vieja. Filtros: leida y tipo. "
            "Incluye no_leidas (total del usuario) para el contador."
        ),
    ),
)
@extend_schema(tags=["Notificaciones"])
class NotificacionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Para cualquier usuario Activo (Donante, Beneficiario o Administrador)."""

    serializer_class = NotificacionSerializer
    queryset = Notificacion.objects.none()  # referencia para Swagger; ver get_queryset()
    filterset_class = NotificacionFilter
    pagination_class = NotificacionPagination
    lookup_value_regex = r"\d+"
    ordering_fields = None
    search_fields = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notificacion.objects.none()
        return services.notificaciones_de(self.request.user)

    @extend_schema(
        summary="Marcar una como leída",
        description="Si la notificación es de otro usuario, responde 404.",
        request=None,
        responses={200: NotificacionSerializer},
    )
    @action(detail=True, methods=["post"])
    def leer(self, request, pk=None):
        notificacion = services.marcar_leida(request.user, pk)
        if notificacion is None:
            raise NotFound("No encontramos esa notificación.")
        return Response(NotificacionSerializer(notificacion).data)

    @extend_schema(
        summary="Marcar todas como leídas",
        description="Devuelve cuántas cambiaron.",
        request=None,
        responses={200: LeerTodasSerializer},
    )
    @action(detail=False, methods=["post"], url_path="leer-todas")
    def leer_todas(self, request):
        marcadas = services.marcar_todas_leidas(request.user)
        return Response({"marcadas": marcadas, "no_leidas": services.contar_no_leidas(request.user)})
