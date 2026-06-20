from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from apps.dashboard.views import OcupacionAPIView, OcupacionDashboardView, SolicitudView, SolicitudCrearView, RecursoDetalleView

urlpatterns = [
    # Redirige el login del admin a nuestra página personalizada
    path("admin/login/", lambda req: redirect(f"/login/?next={req.GET.get('next', '/admin/')}")),
    path("admin/", admin.site.urls),
    # API
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.calendar_engine.urls")),
    path("api/", include("apps.assignments.urls")),
    path("api/dashboard/ocupacion/", OcupacionAPIView.as_view(), name="dashboard-api"),
    # Auth session (login/logout para DRF browsable API)
    path("api-auth/", include("rest_framework.urls")),
    # Vistas UI
    path("solicitud/", SolicitudView.as_view(), name="solicitud"),
    path("solicitud/crear/", SolicitudCrearView.as_view(), name="solicitud-crear"),
    path("dashboard/", OcupacionDashboardView.as_view(), name="dashboard"),
    path("recurso/<int:pk>/", RecursoDetalleView.as_view(), name="recurso-detalle"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/login/"), name="logout"),
    path("", OcupacionDashboardView.as_view(), name="home"),
]
