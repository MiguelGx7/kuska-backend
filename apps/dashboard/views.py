from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.usuarios.permissions import PERMISOS_ADMIN, PERMISOS_DONANTE

from . import services
from .serializers import DashboardAdminSerializer, DashboardDonanteSerializer


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
