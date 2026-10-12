from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.usuarios.permissions import PERMISOS_BENEFICIARIO

from . import services
from .models import DetalleSolicitud, Solicitud
from .serializers import (
    CarritoAgregarSerializer,
    CarritoCantidadSerializer,
    CarritoItemSerializer,
    CarritoSerializer,
    EnviarCarritoSerializer,
    MiSolicitudSerializer,
    PedidoEnviadoSerializer,
)

TAG_CARRITO = ["Beneficiario - Carrito"]


def _solicitudes_con_detalle(queryset):
    detalles = DetalleSolicitud.objects.select_related("donacion_alimento").order_by("id")
    return queryset.select_related("donacion__donante").prefetch_related(
        Prefetch("detalles", queryset=detalles)
    )


class CarritoView(APIView):
    permission_classes = PERMISOS_BENEFICIARIO

    @extend_schema(
        tags=TAG_CARRITO,
        summary="Ver mi carrito (RN-21)",
        description=(
            "Agrupado por donación. Cada ítem dice si sigue siendo válido y por qué no. "
            "'avisos' explica si el carrito supera los límites; 'puede_enviar' resume todo."
        ),
        responses={200: CarritoSerializer},
    )
    def get(self, request):
        return Response(CarritoSerializer(services.ver_carrito(request.user)).data)

    @extend_schema(
        tags=TAG_CARRITO,
        summary="Agregar al carrito (RN-20)",
        description="Si el alimento ya está, actualiza la cantidad. El carrito no reserva.",
        request=CarritoAgregarSerializer,
        responses={201: CarritoItemSerializer, 200: CarritoItemSerializer},
    )
    def post(self, request):
        serializer = CarritoAgregarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        item, creado = services.agregar_al_carrito(
            request.user, datos["donacion_alimento"], datos["cantidad"]
        )
        codigo = status.HTTP_201_CREATED if creado else status.HTTP_200_OK
        return Response(CarritoItemSerializer(item).data, status=codigo)


class CarritoItemView(APIView):
    permission_classes = PERMISOS_BENEFICIARIO

    @extend_schema(
        tags=TAG_CARRITO,
        summary="Cambiar la cantidad de un ítem",
        request=CarritoCantidadSerializer,
        responses={200: CarritoItemSerializer},
    )
    def patch(self, request, pk):
        serializer = CarritoCantidadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = services.cambiar_cantidad(request.user, pk, serializer.validated_data["cantidad"])
        return Response(CarritoItemSerializer(item).data)

    @extend_schema(tags=TAG_CARRITO, summary="Quitar un ítem del carrito", responses={204: None})
    def delete(self, request, pk):
        services.quitar_del_carrito(request.user, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EnviarCarritoView(APIView):
    permission_classes = PERMISOS_BENEFICIARIO

    @extend_schema(
        tags=TAG_CARRITO,
        summary="Enviar el carrito (RN-22)",
        description=(
            "Crea una solicitud por donación con el mismo codigo_pedido y reserva las cantidades. "
            "En 'donaciones' va, por cada donación 'Ambas', la modalidad elegida; y la dirección si "
            "es Domicilio (por defecto la del perfil). Si algo falla responde 400 con los errores "
            "por ítem ('items'), por donación ('donaciones') o del carrito ('carrito'), y no crea nada."
        ),
        request=EnviarCarritoSerializer,
        responses={201: PedidoEnviadoSerializer},
    )
    def post(self, request):
        serializer = EnviarCarritoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        codigo, solicitudes = services.enviar_carrito(request.user, serializer.elecciones())
        solicitudes = _solicitudes_con_detalle(
            Solicitud.objects.filter(pk__in=[s.pk for s in solicitudes]).order_by("id")
        )
        datos = {"codigo_pedido": codigo, "solicitudes": solicitudes}
        return Response(PedidoEnviadoSerializer(datos).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(summary="Mis solicitudes", description="Filtro: estado. Búsqueda por código de pedido."),
    retrieve=extend_schema(summary="Ver una de mis solicitudes"),
)
@extend_schema(tags=["Beneficiario - Solicitudes"])
class MisSolicitudesViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = PERMISOS_BENEFICIARIO
    serializer_class = MiSolicitudSerializer
    queryset = Solicitud.objects.none()  # referencia para Swagger; ver get_queryset()
    filterset_fields = ["estado"]
    search_fields = ["codigo_pedido"]
    ordering_fields = ["fecha_solicitud", "estado"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Solicitud.objects.none()
        return _solicitudes_con_detalle(
            Solicitud.objects.filter(beneficiario=self.request.user).order_by("-fecha_solicitud", "-id")
        )

    @extend_schema(
        summary="Cancelar mi solicitud (RN-23)",
        description="Solo si está Pendiente. Devuelve las cantidades. Sin sanción.",
        request=None,
        responses={200: MiSolicitudSerializer},
    )
    @action(detail=True, methods=["post"])
    def cancelar(self, request, pk=None):
        solicitud = self.get_object()
        services.cancelar_solicitud(request.user, solicitud.pk)
        return Response(MiSolicitudSerializer(self.get_queryset().get(pk=solicitud.pk)).data)
