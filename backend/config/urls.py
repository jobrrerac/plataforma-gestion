from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from apps.dashboard.views import (
    OcupacionAPIView, OcupacionDashboardView, SolicitudView,
    SolicitudCrearView, SolicitudRecurrenteView, RecursoDetalleView,
    LiberacionSolicitarView, CesionSolicitarView, RegistrarView, SolicitarView, GestionarView, DashboardProyectoView,
)
from apps.accounts.views import LoginRateLimitView, CambiarPasswordView, salud, listo
from apps.calendar_engine.views_novedades import NovedadesView, NovedadesRevisarView
from apps.legalizacion.views import LegalizarDiaView
from apps.legalizacion.views_aprobacion import AprobarHorasView
from apps.seguimiento.views import BloqueantesView, FeedbackView

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
    path("liberacion/", LiberacionSolicitarView.as_view(), name="liberacion-solicitar"),
    path("cesion/", CesionSolicitarView.as_view(), name="cesion-solicitar"),
    path("dashboard/", OcupacionDashboardView.as_view(), name="dashboard"),
    # El de ocupacion responde "quien esta libre"; este responde "como va
    # este proyecto". Son dos preguntas distintas y no caben en el mismo mapa.
    path("dashboard/proyecto/", DashboardProyectoView.as_view(), name="dashboard-proyecto"),
    # Los dos concentradores. La barra superior llego a once pestanas y dejo de
    # caber; se agrupan por accion —lo que reporto sobre mi, lo que reviso de
    # otros— en vez de listarlo todo en una fila que hay que leer entera.
    path("registrar/", RegistrarView.as_view(), name="registrar"),
    path("solicitar/", SolicitarView.as_view(), name="solicitar"),
    path("gestionar/", GestionarView.as_view(), name="gestionar"),
    # Novedades: el panel propio es para cualquier usuario autenticado; la cola
    # de revisión, solo para Admin.
    path("novedades/", NovedadesView.as_view(), name="novedades"),
    path("novedades/revisar/", NovedadesRevisarView.as_view(), name="novedades-revisar"),
    # Legalizacion de horas: cada quien registra su propio dia.
    path("horas/", LegalizarDiaView.as_view(), name="horas"),
    path("horas/aprobar/", AprobarHorasView.as_view(), name="horas-aprobar"),
    path("recurso/<int:pk>/", RecursoDetalleView.as_view(), name="recurso-detalle"),
    # Seguimiento: lo que frena a la gente y lo que se observa de ella. Las dos
    # pantallas son para cualquier usuario autenticado —el bloqueante lo reporta
    # quien lo sufre—; el alcance de lo que se ve lo decide el servicio.
    # Dos modos de la misma vista: reportar lo tuyo, y revisar lo del equipo.
    # Separadas porque son dos tareas distintas con dos publicos distintos.
    path("bloqueantes/", BloqueantesView.as_view(modo="registrar"), name="bloqueantes"),
    path("bloqueantes/equipo/", BloqueantesView.as_view(modo="gestionar"),
         name="bloqueantes-equipo"),
    path("feedback/", FeedbackView.as_view(modo="registrar"), name="feedback"),
    path("feedback/equipo/", FeedbackView.as_view(modo="gestionar"),
         name="feedback-equipo"),
    # Sondas de la plataforma. Van sin autenticar y sin redirección a HTTPS.
    path("healthz/", salud, name="healthz"),
    path("readyz/", listo, name="readyz"),
    path("login/", LoginRateLimitView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/login/"), name="logout"),
    path("password/cambiar/", CambiarPasswordView.as_view(), name="password-cambiar"),
    path("", OcupacionDashboardView.as_view(), name="home"),
]

# SSO con Entra ID. Las rutas solo existen si está configurado: sin client_id no
# hay a dónde redirigir, y dejarlas montadas daría un error 500 a quien las
# alcance por accidente. El login local funciona con o sin esto.
if getattr(settings, "OIDC_HABILITADO", False) and getattr(settings, "OIDC_RP_CLIENT_ID", ""):
    urlpatterns += [
        path("oidc/", include("mozilla_django_oidc.urls")),
    ]
