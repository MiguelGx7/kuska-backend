from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from . import services
from .serializers import (
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


class RegistroView(VistaPublicaMixin, APIView):
    @extend_schema(
        tags=TAG,
        summary="Registrar Donante o Beneficiario",
        description="No inicia sesión: después del registro el usuario hace login.",
        request=RegistroSerializer,
        responses={201: UsuarioSerializer},
    )
    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = services.registrar_usuario(serializer.validated_data)
        return Response(UsuarioSerializer(usuario).data, status=status.HTTP_201_CREATED)


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
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "usuario": UsuarioBasicoSerializer(usuario).data,
            }
        )


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

    @extend_schema(summary="Ver mi perfil")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(summary="Editar mi perfil (nombres, apellidos, teléfono y dirección)")
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @extend_schema(summary="Editar parte de mi perfil")
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
