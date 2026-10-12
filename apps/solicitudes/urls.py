from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("mis-solicitudes", views.MisSolicitudesViewSet, basename="mis-solicitudes")
router.register("admin/solicitudes", views.AdminSolicitudViewSet, basename="admin-solicitudes")

urlpatterns = [
    path("carrito/", views.CarritoView.as_view(), name="carrito"),
    path("carrito/enviar/", views.EnviarCarritoView.as_view(), name="carrito-enviar"),
    path("carrito/<int:pk>/", views.CarritoItemView.as_view(), name="carrito-item"),
    path("solicitudes/<int:pk>/entregar/", views.EntregaDonanteView.as_view(), name="solicitud-entregar"),
    *router.urls,
]
