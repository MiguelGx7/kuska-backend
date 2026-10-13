import re

from django.db.models import Prefetch
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.solicitudes import services as servicios_solicitudes
from apps.solicitudes.models import Entrega, Solicitud
from apps.solicitudes.serializers import AlimentoConSolicitudesSerializer
from apps.usuarios.permissions import PERMISOS_ADMIN, PERMISOS_BENEFICIARIO, PERMISOS_DONANTE

from . import services
from .filters import AdminDonacionFilter, AdminNovedadFilter, AlimentoDisponibleFilter
from .models import Donacion, DonacionAlimento, Novedad
from .serializers import (
    AceptarNovedadSerializer,
    AdminDonacionSerializer,
    AdminNovedadSerializer,
    AlimentoDisponibleSerializer,
    NovedadCrearSerializer,
    NovedadSerializer,
    RechazarNovedadSerializer,
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


def _poner_al_dia(solicitudes, donaciones):
    """
    RN-50 antes de listar: aplica RN-27/RN-28 y RN-16 solo a los registros de
    estos querysets que de verdad estén vencidos o desactualizados. Primero las
    solicitudes, porque devolver cantidades puede cambiar la donación.
    """
    servicios_solicitudes.procesar_solicitudes_vencidas(solicitudes)
    services.procesar_donaciones_desactualizadas(donaciones)


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
    list=extend_schema(summary="Mis donaciones", description="Filtro: estado. Antes de listar aplica los vencimientos (RN-50) a los registros que estén vencidos."),
    retrieve=extend_schema(summary="Ver una de mis donaciones", description="Con sus alimentos y fotos. Una donación ajena responde 404."),
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

    def list(self, request, *args, **kwargs):
        _poner_al_dia(
            Solicitud.objects.filter(donacion__donante=request.user),
            Donacion.objects.filter(donante=request.user),
        )
        return super().list(request, *args, **kwargs)

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
        description="Multipart con los campos del alimento y su foto (RN-11a). Valida categoría activa, vencimiento y enteros (RN-10a, RN-11, RN-17).",
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
            "Con el id de la solicitud se registra la entrega en solicitudes/{id}/entregar/. Antes de listar aplica los vencimientos (RN-50) a los registros que estén vencidos."
        ),
        responses={200: AlimentoConSolicitudesSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="solicitudes-recibidas", pagination_class=None)
    def solicitudes_recibidas(self, request, pk=None):
        donacion = self.get_object()
        _poner_al_dia(Solicitud.objects.filter(donacion=donacion), Donacion.objects.filter(pk=donacion.pk))
        servicios_solicitudes.asumir_entregas_vencidas(Entrega.objects.filter(solicitud__donacion=donacion))
        datos = servicios_solicitudes.solicitudes_recibidas(donacion)
        return Response(AlimentoConSolicitudesSerializer(datos, many=True).data)


# ---------------------------------------------------------------------
# Administrador
# ---------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(
        summary="Listar donaciones",
        description="Filtros: estado y donante (id). Búsqueda por título, o por correo, nombres, apellidos o nombre del negocio del donante. Antes de listar aplica los vencimientos (RN-50) a los registros que estén vencidos.",
        parameters=[
            OpenApiParameter(
                "search",
                str,
                description=(
                    "Busca por título de la donación, o por correo, nombres, apellidos o nombre del "
                    "negocio del donante. Varias palabras: cada una debe aparecer en alguno de esos "
                    "campos (ej. \"Ana Rojas\")."
                ),
            )
        ],
    ),
    retrieve=extend_schema(summary="Ver una donación con sus fotos", description="El administrador usa las fotos para validar que es comida en buen estado (RN-11a, RN-13)."),
)
@extend_schema(tags=["Admin - Donaciones"])
class AdminDonacionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = PERMISOS_ADMIN
    serializer_class = AdminDonacionSerializer
    filterset_class = AdminDonacionFilter
    search_fields = [
        "titulo",
        "donante__correo",
        "donante__nombres",
        "donante__apellidos",
        "donante__nombre_negocio",
    ]
    ordering_fields = ["fecha_registro", "estado"]

    def get_queryset(self):
        return _con_alimentos(
            Donacion.objects.select_related("donante", "revisada_por").order_by("-fecha_registro")
        )

    def list(self, request, *args, **kwargs):
        _poner_al_dia(Solicitud.objects.all(), Donacion.objects.all())
        return super().list(request, *args, **kwargs)

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


# ---------------------------------------------------------------------
# Novedades (Paso 8)
# ---------------------------------------------------------------------
def _novedades():
    return Novedad.objects.select_related(
        "donacion", "donacion_alimento", "donante", "revisada_por"
    ).order_by("-fecha_creacion", "-id")


@extend_schema_view(
    list=extend_schema(summary="Mis novedades", description="Filtros: estado y donacion (id)."),
    retrieve=extend_schema(summary="Ver una de mis novedades", description="Incluye su estado y la respuesta del administrador. Una ajena responde 404."),
)
@extend_schema(tags=["Donante - Novedades"])
class NovedadDonanteViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    permission_classes = PERMISOS_DONANTE
    serializer_class = NovedadSerializer
    queryset = Novedad.objects.none()  # referencia para Swagger; ver get_queryset()
    filterset_fields = ["estado", "donacion"]
    ordering_fields = ["fecha_creacion", "estado"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Novedad.objects.none()
        return _novedades().filter(donante=self.request.user)

    @extend_schema(
        summary="Reportar una novedad (RN-30)",
        description=(
            "Sobre tu donación Validada o Agotada: un alimento (donacion_alimento) o toda la donación "
            "(sin donacion_alimento). Máximo una Pendiente por alimento y una por toda la donación. "
            "Mientras está Pendiente, lo afectado deja de mostrarse para pedir."
        ),
        request=NovedadCrearSerializer,
        responses={201: NovedadSerializer},
    )
    def create(self, request):
        serializer = NovedadCrearSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        novedad = services.crear_novedad(
            request.user, datos["donacion"], datos.get("donacion_alimento"), datos["descripcion"]
        )
        return Response(NovedadSerializer(_novedades().get(pk=novedad.pk)).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(summary="Listar novedades", description="Filtros: estado, donacion (id) y donante (id)."),
    retrieve=extend_schema(
        summary="Ver una novedad",
        description="Incluye cuántas solicitudes en curso se verían afectadas si se acepta.",
    ),
)
@extend_schema(tags=["Admin - Novedades"])
class AdminNovedadViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = PERMISOS_ADMIN
    serializer_class = AdminNovedadSerializer
    filterset_class = AdminNovedadFilter
    search_fields = ["descripcion", "donacion__titulo", "donante__correo"]
    ordering_fields = ["fecha_creacion", "estado"]

    def get_queryset(self):
        return _novedades()

    def _respuesta(self, novedad_id):
        return Response(AdminNovedadSerializer(self.get_queryset().get(pk=novedad_id)).data)

    @extend_schema(
        summary="Aceptar novedad (RN-32)",
        description=(
            "Retira los alimentos afectados, cancela sus detalles en las solicitudes Pendiente o "
            "Aprobada (o la solicitud completa si no le queda nada), recalcula fechas límite y la "
            "donación, y notifica a los beneficiarios afectados y al donante."
        ),
        request=AceptarNovedadSerializer,
        responses={200: AdminNovedadSerializer},
    )
    @action(detail=True, methods=["post"])
    def aceptar(self, request, pk=None):
        novedad = self.get_object()
        serializer = AceptarNovedadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.aceptar_novedad(request.user, novedad.pk, serializer.validated_data.get("respuesta_admin"))
        return self._respuesta(novedad.pk)

    @extend_schema(
        summary="Rechazar novedad (RN-33)",
        description="Respuesta obligatoria. Lo afectado vuelve a mostrarse y se notifica al donante.",
        request=RechazarNovedadSerializer,
        responses={200: AdminNovedadSerializer},
    )
    @action(detail=True, methods=["post"])
    def rechazar(self, request, pk=None):
        novedad = self.get_object()
        serializer = RechazarNovedadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.rechazar_novedad(request.user, novedad.pk, serializer.validated_data["respuesta_admin"])
        return self._respuesta(novedad.pk)
