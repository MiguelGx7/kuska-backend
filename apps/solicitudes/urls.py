from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("mis-solicitudes", views.MisSolicitudesViewSet, basename="mis-solicitudes")

urlpatterns = [
    path("carrito/", views.CarritoView.as_view(), name="carrito"),
    path("carrito/enviar/", views.EnviarCarritoView.as_view(), name="carrito-enviar"),
    path("carrito/<int:pk>/", views.CarritoItemView.as_view(), name="carrito-item"),
    *router.urls,
]
