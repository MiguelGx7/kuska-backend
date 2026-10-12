from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("admin/categorias", views.AdminCategoriaViewSet, basename="admin-categorias")

urlpatterns = [
    path("categorias/", views.CategoriaActivaListView.as_view(), name="categorias-activas"),
    path(
        "admin/configuracion/",
        views.AdminConfiguracionView.as_view(),
        name="admin-configuracion",
    ),
    *router.urls,
]
