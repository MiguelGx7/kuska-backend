from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("admin/sanciones", views.AdminSancionViewSet, basename="admin-sanciones")

urlpatterns = router.urls
