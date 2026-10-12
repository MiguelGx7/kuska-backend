from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/admin/", views.DashboardAdminView.as_view(), name="dashboard-admin"),
    path("dashboard/donante/", views.DashboardDonanteView.as_view(), name="dashboard-donante"),
    path("dashboard/beneficiario/", views.DashboardBeneficiarioView.as_view(), name="dashboard-beneficiario"),
]
