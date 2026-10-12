from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("donaciones", views.DonacionDonanteViewSet, basename="donaciones")
router.register("admin/donaciones", views.AdminDonacionViewSet, basename="admin-donaciones")

urlpatterns = [
    path(
        "alimentos-disponibles/",
        views.AlimentoDisponibleListView.as_view(),
        name="alimentos-disponibles",
    ),
    *router.urls,
]
