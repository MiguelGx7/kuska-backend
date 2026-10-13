from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("admin/usuarios", views.AdminUsuarioViewSet, basename="admin-usuarios")

urlpatterns = [
    path(
        "auth/registro/solicitar-codigo/",
        views.SolicitarCodigoRegistroView.as_view(),
        name="auth-registro-solicitar-codigo",
    ),
    path("auth/registro/", views.RegistroView.as_view(), name="auth-registro"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/perfil/", views.PerfilView.as_view(), name="auth-perfil"),
    path(
        "auth/perfil/cambiar-contrasena/",
        views.CambiarContrasenaView.as_view(),
        name="auth-cambiar-contrasena",
    ),
    path(
        "auth/recuperar/solicitar/",
        views.RecuperarSolicitarView.as_view(),
        name="auth-recuperar-solicitar",
    ),
    path(
        "auth/recuperar/confirmar/",
        views.RecuperarConfirmarView.as_view(),
        name="auth-recuperar-confirmar",
    ),
    *router.urls,
]
