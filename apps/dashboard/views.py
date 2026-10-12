from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.usuarios.permissions import PERMISOS_ADMIN, PERMISOS_BENEFICIARIO, PERMISOS_DONANTE

from . import services
from .serializers import DashboardAdminSerializer, DashboardBeneficiarioSerializer, DashboardDonanteSerializer


class DashboardAdminView(APIView):
    permission_classes = PERMISOS_ADMIN

    @extend_schema(
        tags=["Dashboard"],
        summary="Dashboard del administrador (RN-60)",
        description=(
            "Totales y series de los últimos 12 meses (hora de Bogotá, meses vacíos en 0) y por "
            "categoría. Las series cuentan registros; no suman cantidades. Antes aplica los "
            "vencimientos pendientes (RN-50)."
        ),
        responses={200: DashboardAdminSerializer},
    )
    def get(self, request):
        return Response(DashboardAdminSerializer(services.dashboard_admin()).data)


class DashboardDonanteView(APIView):
    permission_classes = PERMISOS_DONANTE

    @extend_schema(
        tags=["Dashboard"],
        summary="Dashboard del donante (RN-61)",
        description=(
            "Solo sus datos: donaciones por estado, entregas, alimentos y cantidades entregadas por "
            "unidad de medida, personas ayudadas y series por mes y por categoría. Antes aplica los "
            "vencimientos pendientes de sus solicitudes y donaciones (RN-50)."
        ),
        responses={200: DashboardDonanteSerializer},
    )
    def get(self, request):
        return Response(DashboardDonanteSerializer(services.dashboard_donante(request.user)).data)


class DashboardBeneficiarioView(APIView):
    permission_classes = PERMISOS_BENEFICIARIO

    @extend_schema(
        tags=["Dashboard"],
        summary="Inicio del beneficiario (RN-62)",
        description=(
            "Solo sus datos: solicitudes Pendiente y Aprobada, cupos disponibles para enviar, su próxima "
            "fecha límite de recogida (con código de pedido y donación) y notificaciones no leídas. "
            "Antes aplica los vencimientos de sus solicitudes en curso (RN-50)."
        ),
        responses={200: DashboardBeneficiarioSerializer},
    )
    def get(self, request):
        return Response(DashboardBeneficiarioSerializer(services.dashboard_beneficiario(request.user)).data)
