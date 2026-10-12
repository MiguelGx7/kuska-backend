from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.usuarios.permissions import PERMISOS_ADMIN

from . import services
from .filters import AdminSancionFilter
from .models import Sancion
from .serializers import AdminSancionSerializer, CrearSancionSerializer, SancionCreadaSerializer


@extend_schema_view(
    list=extend_schema(
        summary="Listar sanciones",
        description="Filtros: usuario (id), tipo y vigente. Búsqueda (search) por motivo o correo.",
    ),
    retrieve=extend_schema(summary="Ver una sanción"),
    create=extend_schema(
        summary="Sancionar a un usuario (RN-05 a RN-08, RN-70 a RN-73)",
        description=(
            "Antes de crearla, consulta admin/usuarios/{id}/impacto/ para ver la sanción sugerida "
            "y lo que se afectará. Motivo obligatorio. No aplica a administradores ni dos veces por "
            "la misma solicitud o donación, y estas deben ser del usuario. Un bloqueo cierra sus "
            "sesiones y aplica los efectos en cascada; la respuesta trae en `efectos` lo que cambió."
        ),
        request=CrearSancionSerializer,
        responses={201: SancionCreadaSerializer},
    ),
)
@extend_schema(tags=["Admin - Sanciones"])
class AdminSancionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = PERMISOS_ADMIN
    serializer_class = AdminSancionSerializer
    filterset_class = AdminSancionFilter
    search_fields = ["motivo", "usuario__correo"]
    ordering_fields = ["fecha_inicio", "tipo"]
    queryset = Sancion.objects.select_related("usuario__rol", "creada_por").order_by("-fecha_inicio", "-id")

    def create(self, request, *args, **kwargs):
        serializer = CrearSancionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sancion = services.crear_sancion(
            request.user,
            serializer.validated_data["usuario"],
            serializer.validated_data["tipo"],
            serializer.validated_data["motivo"],
            serializer.validated_data.get("donacion"),
            serializer.validated_data.get("solicitud"),
        )
        return Response(SancionCreadaSerializer(sancion).data, status=status.HTTP_201_CREATED)
