from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.usuarios.urls")),
    path("api/", include("apps.catalogo.urls")),
    path("api/", include("apps.donaciones.urls")),
    path("api/", include("apps.solicitudes.urls")),
    path("api/", include("apps.sanciones.urls")),
    path("api/", include("apps.notificaciones.urls")),
    path("api/", include("apps.dashboard.urls")),
]

# Swagger: siempre con DEBUG; en producción solo si SWAGGER_PUBLICO=True
# (mientras se desarrollan los frontends).
if settings.SWAGGER_PUBLICO:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    ]
