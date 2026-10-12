from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.usuarios.permissions import PERMISOS_ADMIN

from . import services
from .models import Categoria
from .serializers import CategoriaPublicaSerializer, CategoriaSerializer, ConfiguracionSerializer


@extend_schema_view(
    list=extend_schema(summary="Listar categorías", description="Filtro: activa. Búsqueda por nombre."),
    retrieve=extend_schema(summary="Ver una categoría", description="Incluye las inactivas."),
    create=extend_schema(summary="Crear categoría", description="Nombre único (sin importar mayúsculas). Queda activa."),
    update=extend_schema(summary="Editar categoría", description="Inactivarla (activa=false) impide usarla en alimentos nuevos; los que ya la tienen no cambian (RN-17)."),
    partial_update=extend_schema(summary="Editar parte de una categoría", description="Igual que editar, enviando solo los campos que cambian (RN-17)."),
    destroy=extend_schema(
        summary="Eliminar categoría",
        description="Solo si ningún alimento la usa; si no, se debe inactivar (RN-17).",
    ),
)
@extend_schema(tags=["Admin - Categorías"])
class AdminCategoriaViewSet(viewsets.ModelViewSet):
    permission_classes = PERMISOS_ADMIN
    queryset = Categoria.objects.all()
    serializer_class = CategoriaSerializer
    filterset_fields = ["activa"]
    search_fields = ["nombre"]
    ordering_fields = ["nombre", "id"]

    def perform_create(self, serializer):
        serializer.instance = services.crear_categoria(serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.actualizar_categoria(
            serializer.instance, serializer.validated_data
        )

    def perform_destroy(self, instance):
        services.eliminar_categoria(instance)


@extend_schema(
    tags=["Catálogo"],
    summary="Categorías activas",
    description="Para elegir la categoría de un alimento o filtrar. Sin paginación.",
)
class CategoriaActivaListView(generics.ListAPIView):
    serializer_class = CategoriaPublicaSerializer
    queryset = Categoria.objects.filter(activa=True).order_by("nombre")
    pagination_class = None
    filter_backends = []


@extend_schema(tags=["Admin - Configuración"])
class AdminConfiguracionView(APIView):
    permission_classes = PERMISOS_ADMIN

    @extend_schema(summary="Ver la configuración", description="Fila única con los plazos y límites que usan las reglas de negocio.", responses={200: ConfiguracionSerializer})
    def get(self, request):
        return Response(ConfiguracionSerializer(services.obtener_configuracion()).data)

    @extend_schema(
        summary="Editar la configuración",
        description=(
            "Plazos y límites deben ser mayores que 0, el margen no puede ser negativo y el "
            "porcentaje va de 0 (sin incluir) a 100. Los cambios solo aplican hacia adelante."
        ),
        request=ConfiguracionSerializer,
        responses={200: ConfiguracionSerializer},
    )
    def patch(self, request):
        serializer = ConfiguracionSerializer(
            services.obtener_configuracion(), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        config = services.actualizar_configuracion(request.user, serializer.validated_data)
        return Response(ConfiguracionSerializer(config).data)
