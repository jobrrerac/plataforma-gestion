from django.contrib import admin
from django.urls import path, include
from apps.dashboard.views import OcupacionAPIView, OcupacionDashboardView, SolicitudView, SolicitudCrearView

urlpatterns = [
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
    path("", OcupacionDashboardView.as_view(), name="home"),
]
