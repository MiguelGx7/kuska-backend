from django.urls import path

from . import views

urlpatterns = [
    path("registro/", views.RegistroView.as_view(), name="auth-registro"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("perfil/", views.PerfilView.as_view(), name="auth-perfil"),
    path(
        "perfil/cambiar-contrasena/",
        views.CambiarContrasenaView.as_view(),
        name="auth-cambiar-contrasena",
    ),
    path(
        "recuperar/solicitar/",
        views.RecuperarSolicitarView.as_view(),
        name="auth-recuperar-solicitar",
    ),
    path(
        "recuperar/confirmar/",
        views.RecuperarConfirmarView.as_view(),
        name="auth-recuperar-confirmar",
    ),
]
