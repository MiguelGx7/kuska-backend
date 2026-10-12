import re

from django.db.models import Prefetch
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.solicitudes import services as servicios_solicitudes
from apps.solicitudes.serializers import AlimentoConSolicitudesSerializer
from apps.usuarios.permissions import PERMISOS_ADMIN, PERMISOS_BENEFICIARIO, PERMISOS_DONANTE

from . import services
from .filters import AdminDonacionFilter, AlimentoDisponibleFilter
from .models import Donacion, DonacionAlimento
from .serializers import (
    AdminDonacionSerializer,
    AlimentoDisponibleSerializer,
    AlimentoNuevoSerializer,
    AlimentoSerializer,
    DonacionEditarSerializer,
    DonacionEntradaSerializer,
    DonacionMultipartSerializer,
    DonacionSerializer,
    RechazarDonacionSerializer,
)

PATRON_FOTO = re.compile(r"^foto_(\d+)$")


def _con_alimentos(queryset):
    alimentos = DonacionAlimento.objects.select_related("categoria").order_by("id")
    return queryset.prefetch_related(Prefetch("alimentos", queryset=alimentos))


def _fotos_de(request):
    """{indice: archivo} con los campos foto_0, foto_1... del multipart."""
    return {
        int(coincidencia.group(1)): archivo
        for clave, archivo in request.FILES.items()
        if (coincidencia := PATRON_FOTO.match(clave))
    }


# ---------------------------------------------------------------------
# Donante
# ---------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(summary="Mis donaciones", description="Filtro: estado."),
    retrieve=extend_schema(summary="Ver una de mis donaciones"),
    destroy=extend_schema(
        summary="Eliminar mi donación (solo Pendiente)",
        description="También borra las fotos en Cloudinary.",
    ),
)
@extend_schema(tags=["Donante - Donaciones"])
class DonacionDonanteViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = PERMISOS_DONANTE
    serializer_class = DonacionSerializer
    queryset = Donacion.objects.none()  # referencia para Swagger; ver get_queryset()
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filterset_fields = ["estado"]
    search_fields = ["titulo"]
    ordering_fields = ["fecha_registro", "estado"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        # Cada donante solo ve y toca sus donaciones (las demás dan 404).
        if getattr(self, "swagger_fake_view", False):
            return Donacion.objects.none()
        return _con_alimentos(
            Donacion.objects.filter(donante=self.request.user).order_by("-fecha_registro")
        )

    def _respuesta(self, donacion_id, codigo=status.HTTP_200_OK):
        donacion = self.get_queryset().get(pk=donacion_id)
        return Response(DonacionSerializer(donacion).data, status=codigo)

    @extend_schema(
        summary="Crear donación (multipart)",
        description=(
            "Campo 'datos': JSON con la donación y su lista 'alimentos' (al menos uno). "
            "Fotos: foto_0, foto_1... en el mismo orden de los alimentos, sin límite (aquí solo "
            "se muestran 4 campos; desde la app se envían foto_4, foto_5...). Si algo falla no se "
            "guarda nada ni queda ninguna foto en Cloudinary. Se notifica a los administradores."
        ),
        request={"multipart/form-data": DonacionMultipartSerializer},
        responses={201: DonacionSerializer},
    )
    def create(self, request):
        datos = DonacionEntradaSerializer.desde_texto(request.data.get("datos"))
        donacion = services.crear_donacion(request.user, datos, _fotos_de(request))
        return self._respuesta(donacion.pk, status.HTTP_201_CREATED)

    @extend_schema(
        summary="Editar mi donación (solo Pendiente)",
        description="Título, descripción, dirección de recogida y modalidad.",
        request=DonacionEditarSerializer,
        responses={200: DonacionSerializer},
    )
    def partial_update(self, request, pk=None):
        donacion = self.get_object()
        serializer = DonacionEditarSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.editar_donacion(request.user, donacion.pk, serializer.validated_data)
        return self._respuesta(donacion.pk)

    def perform_destroy(self, instance):
        services.eliminar_donacion(self.request.user, instance.pk)

    @extend_schema(
        summary="Agregar un alimento (solo Pendiente)",
        request={"multipart/form-data": AlimentoNuevoSerializer},
        responses={201: AlimentoSerializer},
    )
    @action(detail=True, methods=["post"], url_path="alimentos")
    def agregar_alimento(self, request, pk=None):
        donacion = self.get_object()
        serializer = AlimentoNuevoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = {k: v for k, v in serializer.validated_data.items() if k != "foto"}
        alimento = services.agregar_alimento(
            request.user, donacion.pk, datos, request.FILES.get("foto")
        )
        return Response(AlimentoSerializer(alimento).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Quitar un alimento (solo Pendiente)",
        description="No se puede quitar el último: en ese caso elimina la donación.",
        parameters=[OpenApiParameter("alimento_id", int, OpenApiParameter.PATH)],
        responses={204: None},
    )
    @action(detail=True, methods=["delete"], url_path=r"alimentos/(?P<alimento_id>\d+)")
    def quitar_alimento(self, request, pk=None, alimento_id=None):
        donacion = self.get_object()
        services.quitar_alimento(request.user, donacion.pk, int(alimento_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Solicitudes recibidas por alimento (RN-15)",
        description=(
            "Quién pidió, cuánto y en qué estado, por cada alimento. Teléfono y dirección del "
            "beneficiario solo si la solicitud está Aprobada, Entregada o No recogida. "
            "Con el id de la solicitud se registra la entrega en solicitudes/{id}/entregar/."
        ),
        responses={200: AlimentoConSolicitudesSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="solicitudes-recibidas", pagination_class=None)
    def solicitudes_recibidas(self, request, pk=None):
        donacion = self.get_object()
        datos = servicios_solicitudes.solicitudes_recibidas(donacion)
        return Response(AlimentoConSolicitudesSerializer(datos, many=True).data)


# ---------------------------------------------------------------------
# Administrador
# ---------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(
        summary="Listar donaciones",
        description="Filtros: estado y donante (id). Búsqueda por título o correo del donante.",
    ),
    retrieve=extend_schema(summary="Ver una donación con sus fotos"),
)
@extend_schema(tags=["Admin - Donaciones"])
class AdminDonacionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = PERMISOS_ADMIN
    serializer_class = AdminDonacionSerializer
    filterset_class = AdminDonacionFilter
    search_fields = ["titulo", "donante__correo"]
    ordering_fields = ["fecha_registro", "estado"]

    def get_queryset(self):
        return _con_alimentos(
            Donacion.objects.select_related("donante", "revisada_por").order_by("-fecha_registro")
        )

    def _respuesta(self, donacion_id):
        return Response(AdminDonacionSerializer(self.get_queryset().get(pk=donacion_id)).data)

    @extend_schema(
        summary="Validar donación (RN-13)",
        description=(
            "Revisa otra vez el vencimiento de cada alimento, calcula max_por_solicitud "
            "y notifica al donante."
        ),
        request=None,
        responses={200: AdminDonacionSerializer},
    )
    @action(detail=True, methods=["post"])
    def validar(self, request, pk=None):
        donacion = self.get_object()
        services.validar_donacion(request.user, donacion.pk)
        return self._respuesta(donacion.pk)

    @extend_schema(
        summary="Rechazar donación",
        description="Motivo obligatorio. Se notifica al donante; las fotos se conservan.",
        request=RechazarDonacionSerializer,
        responses={200: AdminDonacionSerializer},
    )
    @action(detail=True, methods=["post"])
    def rechazar(self, request, pk=None):
        donacion = self.get_object()
        serializer = RechazarDonacionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.rechazar_donacion(request.user, donacion.pk, serializer.validated_data["motivo_rechazo"])
        return self._respuesta(donacion.pk)


# ---------------------------------------------------------------------
# Beneficiario
# ---------------------------------------------------------------------
@extend_schema(
    tags=["Beneficiario - Alimentos"],
    summary="Alimentos disponibles para pedir (RN-14)",
    description=(
        "Filtros: categoria (id) y modalidad. Búsqueda (search) por nombre, descripción, "
        "título de la donación o categoría. Ordenados por vencimiento más cercano."
    ),
)
class AlimentoDisponibleListView(generics.ListAPIView):
    permission_classes = PERMISOS_BENEFICIARIO
    serializer_class = AlimentoDisponibleSerializer
    filterset_class = AlimentoDisponibleFilter
    search_fields = ["nombre", "descripcion", "donacion__titulo", "categoria__nombre"]
    ordering_fields = ["fecha_vencimiento", "nombre"]

    def get_queryset(self):
        return services.alimentos_visibles().order_by("fecha_vencimiento", "id")
