from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.sanciones import services as servicios_sanciones
from apps.sanciones.serializers import ImpactoConsultaSerializer, ImpactoSerializer

from . import services
from .filters import UsuarioFilter
from .models import Usuario
from .permissions import PERMISOS_ADMIN
from .serializers import (
    AdminUsuarioEditarSerializer,
    AdminUsuarioSerializer,
    CambiarEstadoSerializer,
    CambiarContrasenaSerializer,
    LoginRespuestaSerializer,
    LoginSerializer,
    MensajeSerializer,
    PerfilSerializer,
    RecuperarConfirmarSerializer,
    RecuperarSolicitarSerializer,
    RefreshRespuestaSerializer,
    RefreshSerializer,
    RegistroSerializer,
    SolicitarCodigoRegistroSerializer,
    UsuarioBasicoSerializer,
    UsuarioSerializer,
)
from .throttles import LimiteAuthMixin

TAG = ["Autenticación"]


class VistaPublicaMixin(LimiteAuthMixin):
    """Sin login y sin leer el token (uno vencido no debe bloquear el login)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get_authenticate_header(self, request):
        # Sin esto DRF convierte el 401 de credenciales incorrectas en 403.
        return 'Bearer realm="api"'


def _respuesta_sesion(usuario, refresh, codigo=status.HTTP_200_OK):
    """Lo que devuelven el login y el registro: tokens y datos básicos (con el rol)."""
    return Response(
        {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "usuario": UsuarioBasicoSerializer(usuario).data,
        },
        status=codigo,
    )


class SolicitarCodigoRegistroView(VistaPublicaMixin, APIView):
    @extend_schema(
        tags=TAG,
        summary="Registro, paso 1: pedir código al correo (RN-04d)",
        description=(
            "Envía un código de 6 dígitos (vence en 15 minutos, 5 intentos) al correo con el que se "
            "creará la cuenta. Si el correo ya está registrado responde 400. Pedir otro invalida el "
            "anterior. No crea ningún usuario. Si el correo no se puede enviar responde 503."
        ),
        request=SolicitarCodigoRegistroSerializer,
        responses={200: MensajeSerializer},
    )
    def post(self, request):
        serializer = SolicitarCodigoRegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.solicitar_codigo_registro(serializer.validated_data["correo"])
        return Response({"mensaje": services.MENSAJE_CODIGO_REGISTRO})


class RegistroView(VistaPublicaMixin, APIView):
    @extend_schema(
        tags=TAG,
        summary="Registro, paso 2: crear Donante o Beneficiario con el código (RN-04d)",
        description=(
            "Valida todos los datos (RN-01, RN-02, contraseña) y después el código enviado al correo. "
            "Si el código no es válido, venció, ya se usó o se acabaron los intentos, responde un único "
            "mensaje y no crea nada. Si todo está bien crea la cuenta y devuelve lo mismo que el login."
        ),
        request=RegistroSerializer,
        responses={201: LoginRespuestaSerializer},
    )
    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = services.registrar_usuario(serializer.validated_data)
        return _respuesta_sesion(usuario, services.emitir_tokens(usuario), status.HTTP_201_CREATED)


class LoginView(VistaPublicaMixin, APIView):
    @extend_schema(
        tags=TAG,
        summary="Iniciar sesión",
        description=(
            "Devuelve access, refresh y los datos básicos del usuario, incluido su rol. "
            "Cada frontend decide qué roles acepta."
        ),
        request=LoginSerializer,
        responses={200: LoginRespuestaSerializer},
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario, refresh = services.iniciar_sesion(**serializer.validated_data)
        return _respuesta_sesion(usuario, refresh)


@extend_schema(
    tags=TAG,
    summary="Renovar el token",
    description="Rota el refresh (el anterior queda en la lista negra). Exige estado Activo.",
    responses={200: RefreshRespuestaSerializer},
)
class RefreshView(TokenRefreshView):
    serializer_class = RefreshSerializer


@extend_schema(tags=TAG)
class PerfilView(generics.RetrieveUpdateAPIView):
    serializer_class = PerfilSerializer
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_object(self):
        return self.request.user

    @extend_schema(summary="Ver mi perfil", description="Datos del usuario autenticado (nunca la contraseña).")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(summary="Editar mi perfil (nombres, apellidos, teléfono y dirección)", description="RN-04a: si se envía un campo no editable (correo, documento, rol...), responde 400 y no guarda nada.")
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @extend_schema(summary="Editar parte de mi perfil", description="Igual que editar, enviando solo los campos que cambian (RN-04a).")
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)


class CambiarContrasenaView(APIView):
    @extend_schema(
        tags=TAG,
        summary="Cambiar mi contraseña",
        description="Cierra la sesión en todos los dispositivos (invalida los refresh tokens).",
        request=CambiarContrasenaSerializer,
        responses={200: MensajeSerializer},
    )
    def post(self, request):
        serializer = CambiarContrasenaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.cambiar_contrasena(request.user, **serializer.validated_data)
        return Response({"mensaje": "Contraseña actualizada. Vuelve a iniciar sesión."})


class RecuperarSolicitarView(VistaPublicaMixin, APIView):
    @extend_schema(
        tags=TAG,
        summary="Recuperar contraseña: pedir código",
        description="La respuesta es siempre la misma, exista o no el correo.",
        request=RecuperarSolicitarSerializer,
        responses={200: MensajeSerializer},
    )
    def post(self, request):
        serializer = RecuperarSolicitarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.solicitar_recuperacion(serializer.validated_data["correo"])
        return Response({"mensaje": services.MENSAJE_RECUPERACION})


class RecuperarConfirmarView(VistaPublicaMixin, APIView):
    @extend_schema(
        tags=TAG,
        summary="Recuperar contraseña: confirmar código",
        description="Cambia la contraseña y cierra la sesión en todos los dispositivos.",
        request=RecuperarConfirmarSerializer,
        responses={200: MensajeSerializer},
    )
    def post(self, request):
        serializer = RecuperarConfirmarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirmar_recuperacion(**serializer.validated_data)
        return Response({"mensaje": "Contraseña actualizada. Ya puedes iniciar sesión."})


# ---------------------------------------------------------------------
# Administración de usuarios (panel web)
# ---------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(
        summary="Listar usuarios",
        description="Filtros: rol y estado. Búsqueda (search) por nombres, apellidos, correo o documento.",
    ),
    retrieve=extend_schema(summary="Ver un usuario", description="Incluye estado, bloqueado_hasta y último ingreso."),
    partial_update=extend_schema(
        summary="Corregir correo y teléfono (RN-04c)",
        description="Solo correo y teléfono; el correo debe seguir siendo único. Cualquier otro campo responde 400.",
        request=AdminUsuarioEditarSerializer,
        responses={200: AdminUsuarioSerializer},
    ),
)
@extend_schema(tags=["Admin - Usuarios"])
class AdminUsuarioViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = PERMISOS_ADMIN
    queryset = Usuario.objects.select_related("rol").order_by("-fecha_registro")
    filterset_class = UsuarioFilter
    search_fields = ["nombres", "apellidos", "correo", "numero_documento"]
    ordering_fields = ["fecha_registro", "nombres", "apellidos", "correo"]
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "partial_update":
            return AdminUsuarioEditarSerializer
        return AdminUsuarioSerializer

    def partial_update(self, request, *args, **kwargs):
        usuario = self.get_object()
        serializer = AdminUsuarioEditarSerializer(usuario, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        usuario = serializer.save()
        return Response(AdminUsuarioSerializer(usuario).data)

    @extend_schema(
        summary="Cambiar estado entre Activo e Inactivo (RN-04, RN-75)",
        description=(
            "No aplica a administradores ni a uno mismo. Inactivar cierra todas sus sesiones y "
            "todo lo que tiene en curso (RN-71/RN-73); consulta antes impacto/?accion=inactivar. "
            "Reactivar devuelve a Activo, o a Bloqueado si aún tiene un bloqueo vigente."
        ),
        request=CambiarEstadoSerializer,
        responses={200: AdminUsuarioSerializer},
    )
    @action(detail=True, methods=["post"], url_path="cambiar-estado")
    def cambiar_estado(self, request, pk=None):
        usuario = self.get_object()
        serializer = CambiarEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = services.cambiar_estado(request.user, usuario.pk, serializer.validated_data["estado"])
        return Response(AdminUsuarioSerializer(usuario).data)

    @extend_schema(
        summary="Impacto de una sanción o inactivación (RN-74, RN-05)",
        description=(
            "Muestra, SIN aplicar nada, lo que pasaría con la acción indicada: conteos y un resumen "
            "en texto (ej. \"3 solicitud(es) se cancelarán\"). Usa la misma lógica que la cascada "
            "real. Incluye la sanción sugerida según todo el historial del usuario."
        ),
        parameters=[ImpactoConsultaSerializer],
        responses={200: ImpactoSerializer},
    )
    @action(detail=True, methods=["get"])
    def impacto(self, request, pk=None):
        usuario = self.get_object()
        consulta = ImpactoConsultaSerializer(data=request.query_params)
        consulta.is_valid(raise_exception=True)
        resultado = servicios_sanciones.calcular_impacto(usuario.pk, consulta.validated_data["accion"])
        return Response(ImpactoSerializer(resultado).data)
