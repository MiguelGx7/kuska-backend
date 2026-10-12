from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("notificaciones", views.NotificacionViewSet, basename="notificaciones")

urlpatterns = router.urls
