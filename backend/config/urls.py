from urllib.parse import urlencode

from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from apps.dashboard.views import (
    OcupacionAPIView, OcupacionDashboardView, SolicitudView,
    SolicitudCrearView, SolicitudRecurrenteView, RecursoDetalleView,
)
from apps.accounts.views import LoginRateLimitView, CambiarPasswordView

urlpatterns = [
    # Redirige el login del admin a nuestra página personalizada
    # (urlencode evita inyectar parámetros extra vía ?next=)
    path("admin/login/", lambda req: redirect("/login/?" + urlencode({"next": req.GET.get("next", "/admin/")}))),
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
    path("solicitud/recurrente/", SolicitudRecurrenteView.as_view(), name="solicitud-recurrente"),
    path("dashboard/", OcupacionDashboardView.as_view(), name="dashboard"),
    path("recurso/<int:pk>/", RecursoDetalleView.as_view(), name="recurso-detalle"),
    path("login/", LoginRateLimitView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/login/"), name="logout"),
    path("password/cambiar/", CambiarPasswordView.as_view(), name="password-cambiar"),
    path("", OcupacionDashboardView.as_view(), name="home"),
]
