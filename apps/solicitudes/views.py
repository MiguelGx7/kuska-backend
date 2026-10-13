from django.db.models import Prefetch
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.usuarios.permissions import PERMISOS_ADMIN, PERMISOS_BENEFICIARIO, PERMISOS_DONANTE

from . import services
from .filters import AdminSolicitudFilter
from .models import DetalleSolicitud, Entrega, Solicitud
from .serializers import (
    AdminSolicitudSerializer,
    CarritoAgregarSerializer,
    ConfirmarEntregaSerializer,
    RevisarReclamoSerializer,
    EntregarSerializer,
    EntregaSerializer,
    RechazarSolicitudSerializer,
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
        description="Revalida RN-20: alimento visible, tope por solicitud y cantidad disponible. No reserva.",
        request=CarritoCantidadSerializer,
        responses={200: CarritoItemSerializer},
    )
    def patch(self, request, pk):
        serializer = CarritoCantidadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = services.cambiar_cantidad(request.user, pk, serializer.validated_data["cantidad"])
        return Response(CarritoItemSerializer(item).data)

    @extend_schema(tags=TAG_CARRITO, summary="Quitar un ítem del carrito", description="Solo ítems del propio carrito; uno ajeno responde 404.", responses={204: None})
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
    list=extend_schema(summary="Mis solicitudes", description="Filtro: estado. Búsqueda por código de pedido. Antes de listar aplica los vencimientos (RN-50) a los registros que estén vencidos."),
    retrieve=extend_schema(summary="Ver una de mis solicitudes", description="Datos de contacto del donante solo desde Aprobada (RN-29). Una ajena responde 404."),
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
            Solicitud.objects.filter(beneficiario=self.request.user)
            .select_related("entrega")
            .order_by("-fecha_solicitud", "-id")
        )

    def list(self, request, *args, **kwargs):
        # RN-50: antes de listar, solo las suyas que de verdad estén vencidas.
        services.procesar_solicitudes_vencidas(Solicitud.objects.filter(beneficiario=request.user))
        services.asumir_entregas_vencidas(Entrega.objects.filter(solicitud__beneficiario=request.user))
        return super().list(request, *args, **kwargs)

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

    @extend_schema(
        summary="Confirmar si recibí el pedido (RN-26b)",
        description=(
            "Solo sobre una solicitud Entregada propia, una vez y dentro de las 48 horas siguientes a la "
            "entrega (ver entrega.confirmar_hasta). recibido=true la confirma; recibido=false la reclama "
            "(comentario obligatorio) y se avisa a los administradores y al donante. La solicitud sigue "
            "Entregada."
        ),
        request=ConfirmarEntregaSerializer,
        responses={200: MiSolicitudSerializer},
    )
    @action(detail=True, methods=["post"], url_path="confirmar-entrega")
    def confirmar_entrega(self, request, pk=None):
        solicitud = self.get_object()
        serializer = ConfirmarEntregaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirmar_entrega(
            request.user, solicitud.pk, serializer.validated_data["recibido"], serializer.validated_data.get("comentario")
        )
        return Response(MiSolicitudSerializer(self.get_queryset().get(pk=solicitud.pk)).data)


# ---------------------------------------------------------------------
# Administrador (Paso 7)
# ---------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(
        summary="Listar solicitudes",
        description=(
            "Filtros: estado, codigo_pedido, beneficiario (id) y donacion (id). "
            "Búsqueda por código, correo o documento del beneficiario."
            " Antes de listar aplica los vencimientos (RN-50) a los registros que estén vencidos."
        ),
        parameters=[
            OpenApiParameter(
                "ordering",
                str,
                description=(
                    "Campos: fecha_solicitud, estado, fecha_limite_recogida, entrega__fecha_entrega. "
                    "Con - delante, descendente (ej. -entrega__fecha_entrega: entregas más recientes "
                    "primero, junto con ?estado=Entregada). Por defecto: -fecha_solicitud."
                ),
            )
        ],
    ),
    retrieve=extend_schema(summary="Ver una solicitud", description="Con sus detalles, el beneficiario, quién la gestionó y la entrega si existe."),
)
@extend_schema(tags=["Admin - Solicitudes"])
class AdminSolicitudViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = PERMISOS_ADMIN
    serializer_class = AdminSolicitudSerializer
    filterset_class = AdminSolicitudFilter
    search_fields = ["codigo_pedido", "beneficiario__correo", "beneficiario__numero_documento"]
    ordering_fields = ["fecha_solicitud", "estado", "fecha_limite_recogida", "entrega__fecha_entrega"]

    def get_queryset(self):
        return _solicitudes_con_detalle(
            Solicitud.objects.select_related(
                "beneficiario", "gestionada_por", "entrega__registrada_por", "entrega__revisada_por"
            ).order_by("-fecha_solicitud", "-id")
        )

    def list(self, request, *args, **kwargs):
        # RN-50: solo las que de verdad están vencidas (una consulta las encuentra).
        services.procesar_solicitudes_vencidas(Solicitud.objects.all())
        services.asumir_entregas_vencidas()
        return super().list(request, *args, **kwargs)

    def _respuesta(self, solicitud_id):
        return Response(AdminSolicitudSerializer(self.get_queryset().get(pk=solicitud_id)).data)

    @extend_schema(
        summary="Aprobar solicitud (RN-24)",
        description=(
            "Antes aplica RN-27/RN-28 a esta solicitud. No aprueba si hay una novedad pendiente "
            "(RN-31). Calcula fecha_limite_recogida y notifica al beneficiario y al donante."
        ),
        request=None,
        responses={200: AdminSolicitudSerializer},
    )
    @action(detail=True, methods=["post"])
    def aprobar(self, request, pk=None):
        solicitud = self.get_object()
        services.aprobar_solicitud(request.user, solicitud.pk)
        return self._respuesta(solicitud.pk)

    @extend_schema(
        summary="Rechazar solicitud (RN-25)",
        description="Motivo obligatorio. Devuelve las cantidades y notifica al beneficiario.",
        request=RechazarSolicitudSerializer,
        responses={200: AdminSolicitudSerializer},
    )
    @action(detail=True, methods=["post"])
    def rechazar(self, request, pk=None):
        solicitud = self.get_object()
        serializer = RechazarSolicitudSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.rechazar_solicitud(request.user, solicitud.pk, serializer.validated_data["motivo_rechazo"])
        return self._respuesta(solicitud.pk)

    @extend_schema(
        summary="Registrar entrega (RN-26)",
        description="Solo solicitudes Aprobada y sin novedad pendiente. La entrega es total.",
        request=EntregarSerializer,
        responses={200: AdminSolicitudSerializer},
    )
    @action(detail=True, methods=["post"])
    def entregar(self, request, pk=None):
        solicitud = self.get_object()
        serializer = EntregarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.registrar_entrega(request.user, solicitud.pk, serializer.validated_data.get("observacion"))
        return self._respuesta(solicitud.pk)

    @extend_schema(
        summary="Revisar el reclamo de una entrega (RN-26b)",
        description=(
            "Solo si la confirmación está Reclamada. La respuesta es obligatoria; pasa a Revisada y se "
            "notifica al beneficiario y al donante. No sanciona: si corresponde, se sanciona aparte en "
            "admin/sanciones/ con la solicitud (RN-05, RN-07). La solicitud sigue Entregada."
        ),
        request=RevisarReclamoSerializer,
        responses={200: AdminSolicitudSerializer},
    )
    @action(detail=True, methods=["post"], url_path="revisar-reclamo")
    def revisar_reclamo(self, request, pk=None):
        solicitud = self.get_object()
        serializer = RevisarReclamoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.revisar_reclamo(request.user, solicitud.pk, serializer.validated_data["respuesta_admin"])
        return self._respuesta(solicitud.pk)


# ---------------------------------------------------------------------
# Donante: registrar entrega (RN-26)
# ---------------------------------------------------------------------
class EntregaDonanteView(APIView):
    permission_classes = PERMISOS_DONANTE

    @extend_schema(
        tags=["Donante - Donaciones"],
        summary="Registrar entrega de una solicitud de mi donación (RN-26)",
        description="Solo el donante dueño, solo solicitudes Aprobada. La entrega es total.",
        request=EntregarSerializer,
        responses={201: EntregaSerializer},
    )
    def post(self, request, pk):
        serializer = EntregarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entrega = services.registrar_entrega(
            request.user, pk, serializer.validated_data.get("observacion"), solo_donante=True
        )
        return Response(EntregaSerializer(entrega).data, status=status.HTTP_201_CREATED)
